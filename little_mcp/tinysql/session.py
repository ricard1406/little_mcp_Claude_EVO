"""Running statements, and the results they produce (Rust: session.rs)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import CATALOG_TABLE, Catalog, Column, TableSchema
from .parser import parse_statement, split_statements
from .statement import (
    ColumnFormatStmt,
    ColumnItem,
    Comment,
    Condition,
    CreateTableStmt,
    DeleteStmt,
    DescribeStmt,
    InsertStmt,
    Literal,
    Operator,
    SelectStmt,
    Star,
    Statement,
    UpdateStmt,
)
from .table_file import TableFile
from .util import SqlError, ascii_upper, byte_len, matches_like, parse_integer, same_name, trim

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


def _matches(field_value: str, condition: Condition) -> bool:
    """Applies a WHERE test to one value.

    LIKE matches a pattern, ignoring case. The other operators compare numbers
    as numbers when both sides are integers, otherwise as case-sensitive text.
    """
    if condition.op is Operator.LIKE:
        return matches_like(field_value, condition.value)

    left: int | str | None = parse_integer(field_value)
    right: int | str | None = parse_integer(condition.value)
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


class Session:
    """Runs statements against the tables in one data directory.

    COLUMN ... FORMAT settings last for the whole session. Every statement
    reloads the catalogue, so changes to the files are seen at once.
    """

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.display_widths: dict[str, int] = {}  # upper-case column name to display width

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

        where_index = _column_index(schema, stmt.condition.column) if stmt.condition else None

        result = ResultSet(headers, widths)
        for row in TableFile(schema, catalog.data_file(schema)).read_rows():
            if stmt.condition and not _matches(row[where_index], stmt.condition):
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
            new_values.append((index, assignment.value))
        where_index = _column_index(schema, stmt.condition.column)

        updated = TableFile(schema, catalog.data_file(schema)).update_rows(
            lambda row: _matches(row[where_index], stmt.condition), new_values
        )
        return StatementResult(table_name=schema.name, message=f"{_plural(updated, 'row')} updated in {schema.name}")

    def _run_delete(self, stmt: DeleteStmt) -> StatementResult:
        catalog = Catalog.load(self.data_dir)
        schema = catalog.table(stmt.table)
        if same_name(schema.name, CATALOG_TABLE):
            raise SqlError("SYSTEM_TABLE cannot be changed with DELETE: edit the file instead")
        where_index = _column_index(schema, stmt.condition.column)

        removed = TableFile(schema, catalog.data_file(schema)).remove_rows(
            lambda row: _matches(row[where_index], stmt.condition)
        )
        return StatementResult(table_name=schema.name, message=f"{_plural(removed, 'row')} deleted from {schema.name}")

    def _run_create_table(self, stmt: CreateTableStmt) -> StatementResult:
        """Runs a CREATE TABLE: adds one SYSTEM_TABLE line per column and an empty data file."""
        catalog = Catalog.load(self.data_dir)

        # The new table's definition; names are kept in upper case, like the existing tables.
        _check_name(stmt.table, "table")
        schema = TableSchema(ascii_upper(stmt.table))
        if any(same_name(t.name, schema.name) for t in catalog.tables):
            raise SqlError(f"table {schema.name} already exists")
        for definition in stmt.columns:
            _check_name(definition.name, "column")
            name = ascii_upper(definition.name)
            if schema.index_of(name) is not None:
                raise SqlError(f"column {name} appears twice")
            if name == "ID" and schema.columns:
                raise SqlError("the ID column must be the first one: rows are numbered by it")
            # Python's integers never overflow; Rust's saturating_add stops at the largest usize,
            # and that number appears in the error message, so the versions must agree on it.
            end = min(schema.record_length + definition.width, _USIZE_MAX)
            schema.columns.append(Column(name, definition.kind, schema.record_length, end - 1))
            schema.record_length = end
        if schema.record_length > MAX_RECORD_LENGTH:
            raise SqlError(
                f"a row of table {schema.name} would be {schema.record_length} characters long: "
                f"the limit is {MAX_RECORD_LENGTH}"
            )

        # One SYSTEM_TABLE line per column, numbered after the last line.
        catalog_schema = catalog.table(CATALOG_TABLE)
        catalog_file = TableFile(catalog_schema, catalog.data_file(catalog_schema))
        first_id = catalog_file.next_id()
        lines = [
            [
                str(first_id + number - 1),
                str(number),
                schema.name,
                column.name,
                column.kind,
                str(column.start),
                str(column.to),
                str(column.width),
                str(schema.record_length),
            ]
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
