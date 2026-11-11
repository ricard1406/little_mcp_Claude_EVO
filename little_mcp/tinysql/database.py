"""What the MCP server needs around the engine. Python only: there is no Rust file for this.

The Rust version is a separate program, and its web server runs one request at
a time. Here the engine lives inside the MCP server, and an AI writes the SQL,
so this module adds three things:

  * a new database creates itself (SYSTEM_TABLE describes itself in 9 lines);
  * a file lock, so two servers on the same files take turns;
  * a policy: your parser reads the statement, and only the kinds of statement
    a tool allows are run. A write script is all-or-nothing.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows has no fcntl: a single server still works, without the lock
    fcntl = None

from .catalog import CATALOG_TABLE
from .parser import parse_statement, split_statements
from .session import Session, StatementResult
from .statement import CreateTableStmt, DeleteStmt, DescribeStmt, InsertStmt, SelectStmt, UpdateStmt
from .util import SqlError, same_name

READ_STATEMENTS = (SelectStmt, DescribeStmt)
WRITE_STATEMENTS = (CreateTableStmt, InsertStmt, UpdateStmt, DeleteStmt)

MAX_ROWS = 200
"""A SELECT returns at most this many rows to the model, so one question cannot fill its memory."""

_LOCK_FILE = ".lock"

# SYSTEM_TABLE's description of itself: name, type, first byte, last byte.
_CATALOG_COLUMNS = [
    ("ID", "N1", 0, 9),
    ("PROGR", "N1", 10, 14),
    ("TABLE", "S1", 15, 34),
    ("FIELD", "S1", 35, 54),
    ("TYPE", "S1", 55, 59),
    ("FROM", "N1", 60, 64),
    ("TO", "N1", 65, 69),
    ("LEN", "N1", 70, 74),
    ("RECLEN", "N1", 75, 79),
]


def empty_catalog() -> bytes:
    """The contents of SYSTEM_TABLE for a database with no tables yet."""
    lines = []
    for number, (name, kind, start, to) in enumerate(_CATALOG_COLUMNS, start=1):
        lines.append(
            f"{number:<10}{number:<5}{CATALOG_TABLE:<20}{name:<20}{kind:<5}{start:<5}{to:<5}{to - start + 1:<5}{80:<5}\n"
        )
    return "".join(lines).encode()


def only_inserts(script: str) -> bool:
    """Tells whether a script only adds rows: every statement in it is an INSERT.

    A client can use it to ask the user less often: a new row changes no existing
    data, and a DELETE takes it out again. The parser decides, as everywhere else,
    so a DELETE hidden after a `;` or a line break makes the answer False.
    """
    try:
        statements = [parse_statement(sql) for sql in split_statements(script)]
    except SqlError:
        return False
    return bool(statements) and all(isinstance(statement, InsertStmt) for statement in statements)


class Database:
    """One myTinySQL database (a folder of files), safe to share between tools and servers."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = Session(self.directory)
        with self.locked():
            catalog = self.directory / CATALOG_TABLE
            if not catalog.exists():
                catalog.write_bytes(empty_catalog())

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Holds the database for one reader or writer at a time. Do not nest it: it would wait for itself."""
        with (self.directory / _LOCK_FILE).open("a") as lock:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file is closed
            yield

    def read(self, script: str) -> dict:
        """Runs SELECT and DESCRIBE statements only."""
        with self.locked():
            results = self._run(script, READ_STATEMENTS, "read-only: use tiny_sql_write to change data")
        return {"results": [_as_dict(r) for r in results]}

    def write(self, script: str) -> dict:
        """Runs CREATE TABLE, INSERT, UPDATE and DELETE statements: either all of them, or none."""
        with self.locked():
            saved = self._snapshot()
            try:
                results = self._run(script, WRITE_STATEMENTS, "it changes nothing: use tiny_sql_read to read data")
            except SqlError as error:
                self._restore(saved)
                raise SqlError(f"{error} Nothing was changed.") from None
        # Short on purpose: repeating every statement would fill a small model's memory.
        return {"done": len(results), "messages": [r.message for r in results]}

    def _run(self, script: str, allowed: tuple[type, ...], hint: str) -> list[StatementResult]:
        """Parses and checks every statement, and only then runs them, stopping at the first failure."""
        lines = split_statements(script)
        if not lines:
            raise SqlError("There is no statement to run.")

        def fail(number: int, sql: str, why: str) -> SqlError:
            return SqlError(
                f"Statement {number} of {len(lines)} ({sql}): {why}. "
                "Remember: one whole statement per line."
            )

        statements = []
        for number, sql in enumerate(lines, start=1):
            try:
                statement = parse_statement(sql)
            except SqlError as error:
                raise fail(number, sql, str(error)) from None
            if not isinstance(statement, allowed):
                raise fail(number, sql, f"this tool does not run {type(statement).__name__[:-4].upper()}, {hint}")
            if isinstance(statement, WRITE_STATEMENTS) and same_name(statement.table, CATALOG_TABLE):
                raise fail(number, sql, f"{CATALOG_TABLE} is managed by the database and cannot be changed")
            statements.append(statement)

        results = []
        for number, (sql, statement) in enumerate(zip(lines, statements), start=1):
            try:
                result = self.session.run(statement)
            except SqlError as error:
                raise fail(number, sql, str(error)) from None
            result.sql = sql
            results.append(result)
        return results

    # A transaction the simple way: remember every file, and put them back after a failure. It
    # reads the whole database on each write, which is fine for a tiny one. It does not protect
    # against a power cut in the middle of a script: that needs a journal, like real databases have.

    def _files(self) -> list[Path]:
        return [p for p in self.directory.iterdir() if p.is_file() and p.name != _LOCK_FILE]

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self._files()}

    def _restore(self, saved: dict[Path, bytes]) -> None:
        for path in self._files():
            if path not in saved:
                os.unlink(path)
        for path, content in saved.items():
            if not path.exists() or path.read_bytes() != content:
                path.write_bytes(content)


def _as_dict(result: StatementResult) -> dict:
    """One SELECT or DESCRIBE result, in the compact form the model reads."""
    answer = {"sql": result.sql, "message": result.message}
    if result.table is not None:
        answer["columns"] = result.table.headers
        answer["rows"] = result.table.rows[:MAX_ROWS]
        if len(result.table.rows) > MAX_ROWS:
            answer["note"] = f"Only the first {MAX_ROWS} rows are shown: use WHERE to narrow the search."
    return answer
