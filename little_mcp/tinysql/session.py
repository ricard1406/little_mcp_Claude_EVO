"""Running statements, and the results they produce (Rust: session.rs)."""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .catalog import CATALOG_TABLE, Catalog, Column, TableSchema
from .dates import DATE_KIND, check_date
from .parser import parse_statement, split_statements
from .statement import (
    AddColumnStmt,
    ColumnDefinition,
    ColumnFormatStmt,
    ColumnItem,
    Comment,
    Condition,
    CreateTableStmt,
    DeleteStmt,
    DescribeStmt,
    InsertStmt,
    Literal,
    ModifyColumnStmt,
    Operator,
    SelectStmt,
    Star,
    Statement,
    UpdateStmt,
)
from .table_file import TableFile, replace_file
from .util import SqlError, ascii_upper, byte_len, check_number, matches_like, parse_number, same_name, trim

MAX_NAME_LENGTH = 20
"""Longest table or column name: the width of the TABLE and FIELD columns of SYSTEM_TABLE."""

MAX_RECORD_LENGTH = 99999
"""Longest row: SYSTEM_TABLE keeps column positions in 5 characters."""

_USIZE_MAX = 2**64 - 1

_VALID_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


@dataclass
class ResultSet:
    """The rows produced by a SELECT or DESCRIBE."""

    headers: list[str]
    widths: list[int]  # display width of each column in console output
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class StatementResult:
    """What running one statement produced: a table, a message, or an error."""

    sql: str = ""
    table_name: str = ""
    table: ResultSet | None = None
    message: str = ""  # e.g. "1 row inserted ...", or the error text
    failed: bool = False
    silent: bool = False  # True for COLUMN and comments: nothing to show


def _matches(field_value: str, condition: Condition, typed: bool = False) -> bool:
    """Applies a WHERE test to one value.

    LIKE matches a pattern, ignoring case. The other operators compare numbers
    as numbers when both sides are numbers (whole, or decimal like 1338.80),
    otherwise as case-sensitive text. Dates are text in the form YYYY-MM-DD,
    which sorts in the same order as time.

    In a DATE or NUMBER column (`typed`), an empty value means "no value": like
    NULL in SQL, it never passes a comparison, except `= ''`, which is how you
    find it. Otherwise an empty price would count as smaller than every price.
    """
    if condition.op is Operator.LIKE:
        return matches_like(field_value, condition.value)
    if typed and trim(field_value) == "" and trim(condition.value) != "":
        return False

    left = parse_number(field_value)
    right = parse_number(condition.value)
    if left is None or right is None:
        left, right = trim(field_value), trim(condition.value)

    match condition.op:
        case Operator.EQUAL:
            return left == right
        case Operator.NOT_EQUAL:
            return left != right
        case Operator.LESS:
            return left < right
        case Operator.LESS_OR_EQUAL:
            return left <= right
        case Operator.GREATER:
            return left > right
        case Operator.GREATER_OR_EQUAL:
            return left >= right


def _fitted(headers: list[str], rows: list[list[str]]) -> ResultSet:
    """Builds a result whose column widths fit the headers and the values (used by DESCRIBE)."""
    widths = [max([byte_len(h)] + [byte_len(row[i]) for row in rows]) for i, h in enumerate(headers)]
    return ResultSet(headers, widths, rows)


def _plural(n: int, word: str) -> str:
    """Writes a count with a noun in singular or plural, e.g. "1 row" or "3 rows"."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def _column_index(schema: TableSchema, column: str) -> int:
    """The position of a column; fails if the table does not have it."""
    index = schema.index_of(column)
    if index is None:
        raise SqlError(f"unknown column '{column}' in table {schema.name}")
    return index


def _check_name(name: str, what: str) -> None:
    """Checks a new table or column name; `what` is "table" or "column", for the error message."""
    if len(name) > MAX_NAME_LENGTH or not _VALID_NAME.fullmatch(name):
        raise SqlError(
            f"invalid {what} name '{name}': use up to {MAX_NAME_LENGTH} letters, digits, _ and -, starting with a letter"
        )


NUMBER_KIND = "N1"


def _is_date(column: Column) -> bool:
    return column.kind == DATE_KIND


def _is_typed(column: Column) -> bool:
    """DATE and NUMBER columns check their values (in the Python version; C++ and Rust accept any text)."""
    return column.kind in (DATE_KIND, NUMBER_KIND)


def _layout(name: str, definitions: list[ColumnDefinition]) -> TableSchema:
    """Places columns one after the other, from byte 0: the positions SYSTEM_TABLE records."""
    schema = TableSchema(name)
    for definition in definitions:
        # Python's integers never overflow; Rust's saturating_add stops at the largest usize,
        # and that number appears in the error message, so the versions must agree on it.
        end = min(schema.record_length + definition.width, _USIZE_MAX)
        schema.columns.append(Column(definition.name, definition.kind, schema.record_length, end - 1))
        schema.record_length = end
    if schema.record_length > MAX_RECORD_LENGTH:
        raise SqlError(
            f"a row of table {schema.name} would be {schema.record_length} characters long: "
            f"the limit is {MAX_RECORD_LENGTH}"
        )
    return schema


def _definitions(schema: TableSchema) -> list[ColumnDefinition]:
    return [ColumnDefinition(c.name, c.kind, c.width) for c in schema.columns]


def _catalog_line(number: int, line_id: int, schema: TableSchema, column: Column) -> list[str]:
    """One SYSTEM_TABLE line: ID, PROGR, TABLE, FIELD, TYPE, FROM, TO, LEN, RECLEN."""
    return [str(line_id), str(number), schema.name, column.name, column.kind,
            str(column.start), str(column.to), str(column.width), str(schema.record_length)]


class Session:
    """Runs statements against the tables in one data directory.

    COLUMN ... FORMAT settings last for the whole session. Every statement
    reloads the catalogue, so changes to the files are seen at once.

    `timezone` (such as "Europe/Rome") decides which day TODAY is; empty means
    this computer's zone. `clock` gives the current moment, and tests replace it.
    """

    def __init__(self, data_dir: Path | str, timezone: str = "", clock=None):
        self.data_dir = Path(data_dir)
        self.display_widths: dict[str, int] = {}  # upper-case column name to display width
        self.timezone = timezone
        self.clock = clock or (lambda: None)  # None: check_date asks the system clock

    def _value(self, value: str, column: Column) -> str:
        """A value checked for its column's type, as it will be stored (TODAY becomes a date)."""
        if column.kind == DATE_KIND:
            return check_date(value, column.name, self.timezone, self.clock())
        if column.kind == NUMBER_KIND:
            return check_number(value, column.name)
        return value

    def _where(self, schema: TableSchema, condition: Condition) -> tuple[int, Condition, bool]:
        """The WHERE column's index, the condition ready to test, and whether the column is a DATE.

        On a DATE column the value must be a date (or TODAY), and on a NUMBER column
        a number, so a mistake such as 13/11/2026 or 1.338,80 is an error instead of
        a search that silently finds the wrong rows.
        """
        index = _column_index(schema, condition.column)
        column = schema.columns[index]
        if _is_typed(column) and condition.op is not Operator.LIKE:
            condition = replace(condition, value=self._value(condition.value, column))
        return index, condition, _is_typed(column)

    def execute(self, sql: str) -> StatementResult:
        """Parses and runs one statement. Errors never escape: a failure gives a result with `failed` set."""
        try:
            result = self.run(parse_statement(sql))
        except SqlError as error:
            result = StatementResult(failed=True, message=str(error))
        result.sql = trim(sql)
        return result

    def execute_script(self, script: str) -> list[StatementResult]:
        """Runs every statement of a script, in order; a failed statement does not stop the ones after it."""
        return [self.execute(sql) for sql in split_statements(script)]

    def run(self, statement: Statement) -> StatementResult:
        """Calls the _run_...() method for the kind of statement. Raises SqlError if the statement fails.

        Python code can call this directly with a statement it built itself.
        Nothing is ever parsed then, so a value cannot be mistaken for SQL: this
        is how store.py does what a parameterized query does elsewhere.
        """
        match statement:
            case SelectStmt():
                return self._run_select(statement)
            case InsertStmt():
                return self._run_insert(statement)
            case UpdateStmt():
                return self._run_update(statement)
            case DeleteStmt():
                return self._run_delete(statement)
            case CreateTableStmt():
                return self._run_create_table(statement)
            case AddColumnStmt():
                return self._run_add_column(statement)
            case ModifyColumnStmt():
                return self._run_modify_column(statement)
            case ColumnFormatStmt():
                return self._run_column_format(statement)
            case DescribeStmt():
                return self._run_describe(statement)
            case Comment():
                return StatementResult(silent=True)
        raise TypeError(f"not a statement: {statement!r}")

    def _run_select(self, stmt: SelectStmt) -> StatementResult:
        catalog = Catalog.load(self.data_dir)
        schema = catalog.table(stmt.table)

        # What each output column shows: a table column (by index) or, with index None, a literal text.
        headers: list[str] = []
        widths: list[int] = []
        sources: list[int | None] = []

        def add_column(index: int) -> None:
            column = schema.columns[index]
            # COLUMN ... FORMAT wins; otherwise the column is wide enough for its values and its name.
            custom = self.display_widths.get(ascii_upper(column.name))
            headers.append(column.name)
            widths.append(custom if custom is not None else max(column.width, byte_len(column.name)))
            sources.append(index)

        for item in stmt.items:
            match item:
                case Star():
                    for index in range(len(schema.columns)):
                        add_column(index)
                case ColumnItem(name):
                    add_column(_column_index(schema, name))
                case Literal(text):
                    headers.append(text)
                    widths.append(byte_len(text))
                    sources.append(None)

        if stmt.condition:
            where_index, condition, is_date = self._where(schema, stmt.condition)

        result = ResultSet(headers, widths)
        for row in TableFile(schema, catalog.data_file(schema)).read_rows():
            if stmt.condition and not _matches(row[where_index], condition, is_date):
                continue
            result.rows.append([row[s] if s is not None else h for s, h in zip(sources, headers)])

        return StatementResult(table_name=schema.name, message=_plural(len(result.rows), "row"), table=result)

    def _run_insert(self, stmt: InsertStmt) -> StatementResult:
        """Runs an INSERT, assigning the next ID when it is left out."""
        catalog = Catalog.load(self.data_dir)
        schema = catalog.table(stmt.table)
        data_file = catalog.data_file(schema)
        file = TableFile(schema, data_file)

        values = list(stmt.values)
        columns = len(schema.columns)
        if schema.has_auto_id() and len(values) + 1 == columns:
            values.insert(0, str(file.next_id()))
        elif len(values) != columns:
            hint = f" (give {columns - 1} and ID is assigned)" if schema.has_auto_id() else ""
            raise SqlError(f"table {schema.name} has {columns} columns{hint}: got {len(values)} values")
        elif schema.has_auto_id():
            rows = file.read_rows() if data_file.exists() else []
            if any(row[0] == values[0] for row in rows):
                raise SqlError(f"table {schema.name} already has a row with ID {values[0]}")
        values = [self._value(v, c) for v, c in zip(values, schema.columns)]
        file.append(values)

        message = f"1 row inserted into {schema.name}"
        if schema.has_auto_id():
            message += f" with ID {values[0]}"
        return StatementResult(table_name=schema.name, message=message)

    def _run_update(self, stmt: UpdateStmt) -> StatementResult:
        catalog = Catalog.load(self.data_dir)
        schema = catalog.table(stmt.table)
        if same_name(schema.name, CATALOG_TABLE):
            raise SqlError("SYSTEM_TABLE cannot be changed with UPDATE: edit the file instead")

        new_values: list[tuple[int, str]] = []
        for assignment in stmt.assignments:
            index = _column_index(schema, assignment.column)
            if index == 0 and schema.has_auto_id():
                raise SqlError("the ID column cannot be changed: rows are found by their ID")
            column = schema.columns[index]
            new_values.append((index, self._value(assignment.value, column)))
        where_index, condition, is_date = self._where(schema, stmt.condition)

        updated = TableFile(schema, catalog.data_file(schema)).update_rows(
            lambda row: _matches(row[where_index], condition, is_date), new_values
        )
        return StatementResult(table_name=schema.name, message=f"{_plural(updated, 'row')} updated in {schema.name}")

    def _run_delete(self, stmt: DeleteStmt) -> StatementResult:
        catalog = Catalog.load(self.data_dir)
        schema = catalog.table(stmt.table)
        if same_name(schema.name, CATALOG_TABLE):
            raise SqlError("SYSTEM_TABLE cannot be changed with DELETE: edit the file instead")
        where_index, condition, is_date = self._where(schema, stmt.condition)

        removed = TableFile(schema, catalog.data_file(schema)).remove_rows(
            lambda row: _matches(row[where_index], condition, is_date)
        )
        return StatementResult(table_name=schema.name, message=f"{_plural(removed, 'row')} deleted from {schema.name}")

    def _run_create_table(self, stmt: CreateTableStmt) -> StatementResult:
        """Runs a CREATE TABLE: adds one SYSTEM_TABLE line per column and an empty data file."""
        catalog = Catalog.load(self.data_dir)

        # The new table's definition; names are kept in upper case, like the existing tables.
        _check_name(stmt.table, "table")
        name = ascii_upper(stmt.table)
        if any(same_name(t.name, name) for t in catalog.tables):
            raise SqlError(f"table {name} already exists")
        definitions: list[ColumnDefinition] = []
        for definition in stmt.columns:
            definitions.append(self._new_column(definition, definitions))
        schema = _layout(name, definitions)

        # One SYSTEM_TABLE line per column, numbered after the last line.
        catalog_schema = catalog.table(CATALOG_TABLE)
        catalog_file = TableFile(catalog_schema, catalog.data_file(catalog_schema))
        first_id = catalog_file.next_id()
        lines = [
            _catalog_line(number, first_id + number - 1, schema, column)
            for number, column in enumerate(schema.columns, start=1)
        ]

        # First the empty data file, then the catalogue: if the catalogue cannot be written, the
        # data file is removed again and nothing has changed. Mode "x" fails if the file exists.
        data_file = catalog.data_file(schema)
        try:
            data_file.open("xb").close()
        except FileExistsError:
            raise SqlError(f"data file {data_file.name} already exists: remove it, or choose another name") from None
        except OSError:
            raise SqlError(f"cannot create {data_file}") from None
        try:
            catalog_file.append_rows(lines)
        except SqlError:
            data_file.unlink(missing_ok=True)
            raise

        return StatementResult(
            table_name=schema.name,
            message=(
                f"table {schema.name} created with {_plural(len(schema.columns), 'column')} "
                f"(row length {schema.record_length})"
            ),
        )

    @staticmethod
    def _new_column(definition: ColumnDefinition, existing: list[ColumnDefinition]) -> ColumnDefinition:
        """Checks a column for CREATE TABLE or ALTER TABLE ADD; its name is kept in upper case."""
        _check_name(definition.name, "column")
        name = ascii_upper(definition.name)
        if any(d.name == name for d in existing):
            raise SqlError(f"column {name} appears twice")
        if name == "ID" and existing:
            raise SqlError("the ID column must be the first one: rows are numbered by it")
        return replace(definition, name=name)

    # --- ALTER TABLE (Python only) ----------------------------------------------------------
    #
    # A fixed-width file has no room to grow: every column sits at fixed bytes. So adding a
    # column or changing a width means writing every row again with the new layout, and
    # writing the new positions in SYSTEM_TABLE. Both files are prepared and checked first,
    # so an error changes nothing. Then each is replaced through a temporary file. A crash
    # between the two replacements could still leave them out of step: a journal, as real
    # databases keep, is what would close that gap.

    def _altered_table(self, stmt: AddColumnStmt | ModifyColumnStmt) -> tuple[Catalog, TableSchema]:
        catalog = Catalog.load(self.data_dir)
        schema = catalog.table(stmt.table)
        if same_name(schema.name, CATALOG_TABLE):
            raise SqlError("SYSTEM_TABLE cannot be changed with ALTER TABLE")
        return catalog, schema

    def _rebuild(self, catalog: Catalog, new: TableSchema, rows: list[list[str]]) -> None:
        """Writes the table's rows with the new layout, and its new positions in SYSTEM_TABLE."""
        data = TableFile(new, catalog.data_file(new)).encode_rows(rows)

        catalog_schema = catalog.table(CATALOG_TABLE)
        catalog_file = TableFile(catalog_schema, catalog.data_file(catalog_schema))
        table_at, field_at = catalog_schema.index_of("TABLE"), catalog_schema.index_of("FIELD")
        lines = catalog_file.read_rows()
        next_id = catalog_file.next_id()
        for number, column in enumerate(new.columns, start=1):
            mine = [i for i, line in enumerate(lines)
                    if same_name(line[table_at], new.name) and same_name(line[field_at], column.name)]
            if mine:  # an existing column keeps its line, its ID and its place in the file
                line_id = lines[mine[0]][0]
                lines[mine[0]] = _catalog_line(number, int(line_id), new, column)
            else:  # a new column: a new line at the end, so it is read as the last column
                lines.append(_catalog_line(number, next_id, new, column))
                next_id += 1
        catalog_text = catalog_file.encode_rows(lines)

        replace_file(catalog.data_file(catalog_schema), catalog_text)
        replace_file(catalog.data_file(new), data)

    def _run_add_column(self, stmt: AddColumnStmt) -> StatementResult:
        """ALTER TABLE ... ADD: a new column at the end of every row, empty in the existing rows."""
        catalog, schema = self._altered_table(stmt)
        definitions = _definitions(schema)
        definitions.append(self._new_column(stmt.column, definitions))
        new = _layout(schema.name, definitions)
        rows = [row + [""] for row in TableFile(schema, catalog.data_file(schema)).read_rows()]
        self._rebuild(catalog, new, rows)
        column = new.columns[-1]
        return StatementResult(
            table_name=schema.name,
            message=f"column {column.name} added to {schema.name} (row length {new.record_length})",
        )

    def _run_modify_column(self, stmt: ModifyColumnStmt) -> StatementResult:
        """ALTER TABLE ... MODIFY: a new width or type. Fails if an existing value would not fit."""
        catalog, schema = self._altered_table(stmt)
        index = _column_index(schema, stmt.column.name)
        old_column = schema.columns[index]
        definitions = _definitions(schema)
        definitions[index] = replace(stmt.column, name=old_column.name)
        new = _layout(schema.name, definitions)
        column = new.columns[index]

        rows = TableFile(schema, catalog.data_file(schema)).read_rows()
        for row in rows:
            where = f" (row with ID {row[0]})" if schema.has_auto_id() else ""
            try:
                if column.kind != old_column.kind:  # e.g. CHAR to NUMBER: every value must be a number
                    row[index] = self._value(row[index], column)
                if byte_len(row[index]) > column.width:
                    raise SqlError(
                        f"value '{row[index]}' is too long for column {column.name} "
                        f"(max {column.width} characters)"
                    )
            except SqlError as error:
                raise SqlError(f"cannot change column {column.name}: {error}{where}") from None
        self._rebuild(catalog, new, rows)

        kinds = {"S1": "CHAR", "N1": "NUMBER", DATE_KIND: "DATE"}
        kind = kinds.get(column.kind, column.kind)
        shape = kind if _is_date(column) else f"{kind}({column.width})"
        return StatementResult(
            table_name=schema.name,
            message=f"column {column.name} of {schema.name} is now {shape} (row length {new.record_length})",
        )

    def _run_column_format(self, stmt: ColumnFormatStmt) -> StatementResult:
        """Remembers a column's display width for the rest of the session."""
        self.display_widths[ascii_upper(stmt.column)] = stmt.width
        return StatementResult(silent=True)

    def _run_describe(self, stmt: DescribeStmt) -> StatementResult:
        """Lists the tables, or one table's columns."""
        catalog = Catalog.load(self.data_dir)

        if stmt.table is None:
            rows = [[t.name, str(len(t.columns)), str(t.record_length)] for t in catalog.tables]
            return StatementResult(
                message=_plural(len(rows), "table"), table=_fitted(["TABLE", "COLUMNS", "ROW_LENGTH"], rows)
            )

        schema = catalog.table(stmt.table)
        rows = [[c.name, c.kind, str(c.start), str(c.to), str(c.width)] for c in schema.columns]
        return StatementResult(
            table_name=schema.name,
            message=_plural(len(rows), "column"),
            table=_fitted(["COLUMN", "TYPE", "FROM", "TO", "WIDTH"], rows),
        )
