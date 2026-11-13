"""Reading, appending, editing and removing the rows of one table's data file (Rust: table_file.rs).

Everything here works on bytes, because a column's position and width are
counted in bytes: an accented letter takes more than one.
"""

import os
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path

from .catalog import Column, TableSchema
from .util import SqlError, byte_len, is_blank, parse_integer, text_of, trim_right

Row = list[str]
"""One row of a table: one value per column, trailing spaces removed."""


class _Change(Enum):
    """What TableFile._rewrite does with one line."""

    KEEP = auto()
    REMOVE = auto()
    UPDATE = auto()


def _check_fits(column: Column, value: str) -> None:
    """Checks that a value can be stored in its column."""
    if "\n" in value or "\r" in value:
        # Not in the Rust version, where a script is cut into lines first, so a value can never
        # hold a line end. Here values can also come from Python: a line end would split the row in two.
        raise SqlError(f"a value cannot contain a line break (column {column.name})")
    if byte_len(value) > column.width:
        raise SqlError(f"value '{value}' is too long for column {column.name} (max {column.width} characters)")


def _place(record: bytearray, column: Column, value: str) -> None:
    """Writes a value into its column's bytes of a row, padded with spaces. Check it with _check_fits first."""
    record[column.start : column.to + 1] = value.encode().ljust(column.width)


def replace_file(path: Path, content: bytes) -> None:
    """Replaces a file's content through a temporary copy, so a crash never leaves it half-written."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(content)
    except OSError:
        raise SqlError(f"cannot write {temporary}") from None
    try:
        os.replace(temporary, path)
    except OSError as e:
        raise SqlError(f"cannot replace {path}: {e}") from None


def _lacks_final_newline(path: Path) -> bool:
    """Tells whether a file exists, is not empty, and does not end with a line feed."""
    try:
        with path.open("rb") as file:
            file.seek(-1, os.SEEK_END)
            return file.read(1) != b"\n"
    except OSError:  # no file, or seeking before the start of an empty one
        return False


class TableFile:
    """Reads and changes the rows of one table's data file.

    Each line of the file is one row; each column sits at the fixed positions
    given by the schema. Changes that rewrite the file go through a temporary
    file, so a crash half-way never leaves a half-written table.
    """

    def __init__(self, schema: TableSchema, path: Path):
        self.schema = schema
        self.path = path

    def _read_lines(self) -> list[bytearray]:
        """Reads the non-blank lines of the file; fails if the file does not exist."""
        try:
            content = self.path.read_bytes()
        except OSError:
            raise SqlError(f"data file {self.path.name} not found") from None
        return [bytearray(line) for line in content.split(b"\n") if not is_blank(line)]

    def read_rows(self) -> list[Row]:
        """Reads every row of the file, each split into one value per column."""
        return [self._split_line(line) for line in self._read_lines()]

    def _split_line(self, line: bytearray) -> Row:
        """Cuts one line into its column values. A line shorter than the row gives empty values at the end."""
        return [trim_right(text_of(bytes(line[c.start : c.to + 1]))) for c in self.schema.columns]

    def remove_rows(self, should_remove: Callable[[Row], bool]) -> int:
        """Removes the rows for which should_remove returns True, and tells how many there were."""
        return self._rewrite(lambda _line, row: _Change.REMOVE if should_remove(row) else _Change.KEEP)

    def update_rows(self, should_update: Callable[[Row], bool], new_values: list[tuple[int, str]]) -> int:
        """Changes some columns in the rows for which should_update returns True, and tells how many there were.

        new_values holds pairs of (column index, new value). Every value is
        checked before the file is touched, so a value too long changes nothing.
        """
        columns = self.schema.columns
        for index, value in new_values:
            _check_fits(columns[index], value)

        def decide(line: bytearray, row: Row) -> _Change:
            if not should_update(row):
                return _Change.KEEP
            line.extend(b" " * (self.schema.record_length - len(line)))
            for index, value in new_values:
                _place(line, columns[index], value)
            return _Change.UPDATE

        return self._rewrite(decide)

    def _rewrite(self, decide: Callable[[bytearray, Row], _Change]) -> int:
        """Rewrites the file through a temporary copy, line by line.

        Returns how many lines were removed or edited; when 0, the file is left untouched.
        """
        kept = bytearray()
        changed = 0
        for line in self._read_lines():
            change = decide(line, self._split_line(line))
            if change is not _Change.KEEP:
                changed += 1
            if change is not _Change.REMOVE:
                kept += line + b"\n"

        if changed == 0:
            return 0  # nothing to rewrite
        replace_file(self.path, bytes(kept))
        return changed

    def next_id(self) -> int:
        """The highest numeric ID in the file plus one, or 1 for an empty or missing file."""
        if not self.path.exists():
            return 1
        ids = (parse_integer(row[0]) for row in self.read_rows() if row)
        return max([0, *(n for n in ids if n is not None)]) + 1

    def append(self, values: Row) -> None:
        """Adds one row at the end of the file: one value per column, ID included."""
        self.append_rows([values])

    def encode_rows(self, rows: list[Row]) -> bytes:
        """Lays rows out as the lines of a data file. Every value is checked, and nothing is written."""
        columns = self.schema.columns
        records = bytearray()
        for values in rows:
            if len(values) != len(columns):
                raise SqlError(f"table {self.schema.name} has {len(columns)} columns, got {len(values)} values")
            record = bytearray(b" " * self.schema.record_length)
            for column, value in zip(columns, values):
                _check_fits(column, value)
                _place(record, column, value)
            records += record + b"\n"
        return bytes(records)

    def append_rows(self, rows: list[Row]) -> None:
        """Adds several rows in one write. Every row is checked first, so a bad row adds nothing."""
        records = self.encode_rows(rows)
        # Make sure the new row starts on its own line, even if the file lacks a final newline.
        text = (b"\n" if _lacks_final_newline(self.path) else b"") + records
        try:
            with self.path.open("ab") as out:
                out.write(text)
        except OSError:
            raise SqlError(f"cannot write {self.path}") from None
