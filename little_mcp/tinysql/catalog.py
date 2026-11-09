"""The table definitions read from SYSTEM_TABLE: tables, their columns and positions (Rust: catalog.rs)."""

from dataclasses import dataclass, field
from pathlib import Path

from .util import SqlError, is_blank, parse_integer, same_name, text_of, trim

CATALOG_TABLE = "SYSTEM_TABLE"
"""The catalogue describes every table, itself included, and is its own data file."""


@dataclass
class Column:
    """One column of a table: the bytes it occupies in every fixed-width row."""

    name: str
    kind: str  # N1 or S1: recorded in SYSTEM_TABLE, not enforced
    start: int  # first byte, counted from 0 ("from" is a keyword in Python)
    to: int  # last byte, inclusive

    @property
    def width(self) -> int:
        return self.to - self.start + 1


@dataclass
class TableSchema:
    """The definition of one table: its name, its columns, and the length of a row."""

    name: str
    columns: list[Column] = field(default_factory=list)
    record_length: int = 0

    def index_of(self, column_name: str) -> int | None:
        """Finds a column by name (case-insensitive): its position in `columns`, or None."""
        return next((i for i, c in enumerate(self.columns) if same_name(c.name, column_name)), None)

    def has_auto_id(self) -> bool:
        """True when the first column is called ID: INSERT then fills it in."""
        return bool(self.columns) and same_name(self.columns[0].name, "ID")


# The byte positions of the fields in a SYSTEM_TABLE line: (first, last), inclusive.
_TABLE = (15, 34)
_NAME = (35, 54)
_TYPE = (55, 59)
_FROM = (60, 64)
_TO = (65, 69)
_RECORD_LENGTH = (75, 79)


def _cut(line: bytes, position: tuple[int, int]) -> str:
    """Cuts one field out of a SYSTEM_TABLE line, without surrounding spaces; empty if the line is too short."""
    return trim(text_of(line[position[0] : position[1] + 1]))


def _number(line: bytes, position: tuple[int, int], line_number: int) -> int:
    """Reads one numeric field of a SYSTEM_TABLE line; fails if it is not a non-negative integer."""
    value = parse_integer(_cut(line, position))
    if value is None or value < 0:
        raise SqlError(
            f"{CATALOG_TABLE} line {line_number}: expected a number in characters {position[0]}-{position[1]}"
        )
    return value


class Catalog:
    """All table definitions, read from the SYSTEM_TABLE file (one 80-character line per column)."""

    def __init__(self, data_dir: Path, tables: list[TableSchema]):
        self.data_dir = data_dir
        self.tables = tables

    @classmethod
    def load(cls, data_dir: Path) -> "Catalog":
        """Reads SYSTEM_TABLE from a data directory. Fails if it cannot be opened or a line is malformed."""
        path = data_dir / CATALOG_TABLE
        try:
            content = path.read_bytes()
        except OSError:
            raise SqlError(f"cannot open the catalogue {path}") from None

        tables: list[TableSchema] = []
        for line_number, line in enumerate(content.split(b"\n"), start=1):
            if is_blank(line):
                continue
            table_name = _cut(line, _TABLE)
            column = Column(
                name=_cut(line, _NAME),
                kind=_cut(line, _TYPE),
                start=_number(line, _FROM, line_number),
                to=_number(line, _TO, line_number),
            )
            if not table_name or not column.name or column.to < column.start:
                raise SqlError(f"{CATALOG_TABLE} line {line_number}: invalid column definition")
            record_length = max(_number(line, _RECORD_LENGTH, line_number), column.to + 1)

            table = next((t for t in tables if same_name(t.name, table_name)), None)
            if table is None:
                table = TableSchema(table_name)
                tables.append(table)
            table.columns.append(column)
            table.record_length = max(table.record_length, record_length)
        return cls(data_dir, tables)

    def table(self, name: str) -> TableSchema:
        """Finds a table by name (case-insensitive); fails if there is no such table."""
        for table in self.tables:
            if same_name(table.name, name):
                return table
        raise SqlError(f"unknown table '{name}'")

    def data_file(self, table: TableSchema) -> Path:
        """The file holding a table's rows: SYSTEM_TABLE for the catalogue, <name>_DATA for every other table."""
        if same_name(table.name, CATALOG_TABLE):
            return self.data_dir / CATALOG_TABLE
        return self.data_dir / f"{table.name}_DATA"
