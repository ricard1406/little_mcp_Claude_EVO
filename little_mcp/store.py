"""The warehouse inventory database.

The LLM does not need SQL for the inventory. It calls small tools (list, find,
adjust, set) on a whitelist of tables, and this module turns them into fixed
operations whose values are never read as SQL. That blocks SQL injection.

Backends:
  tinysql (default) myTinySQL, the small database in little_mcp/tinysql/, created on first use
  sqlite  a demo database in data/mystore.db, created on first use
  mariadb the MYSTORE database from the original project
"""

import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Literal

from . import config
from .tinysql import Database, SqlError
from .tinysql.catalog import Catalog
from .tinysql.statement import (
    Assignment,
    ColumnDefinition,
    ColumnItem,
    Condition,
    CreateTableStmt,
    InsertStmt,
    Operator,
    SelectStmt,
    UpdateStmt,
)

Table = Literal["FRUITS", "VEGGIE"]
TABLES: tuple[str, ...] = ("FRUITS", "VEGGIE")

DEMO_DATA = {
    "FRUITS": [("APPLES", 2), ("ORANGE", 3), ("APRICOT", 5), ("GRAPES", 4), ("BANANA", 1)],
    "VEGGIE": [("CARROT", 7), ("POTATO", 12), ("TOMATO", 6), ("ONION", 9)],
}


class InventoryError(ValueError):
    """A problem the model can fix by changing its tool arguments."""


def _check_table(table: str) -> str:
    table = table.strip().upper()
    if table not in TABLES:
        raise InventoryError(f"Unknown table {table!r}. Use one of: {', '.join(TABLES)}.")
    return table


def _norm_item(item: str) -> str:
    item = item.strip().upper()
    if not item:
        raise InventoryError("The item name is empty.")
    return item


class _TinySqlBackend:
    """Stock in myTinySQL. Statements are built as objects, so no value is ever parsed as SQL."""

    def __init__(self, directory: Path):
        self.db = Database(directory)
        self.session = self.db.session
        with self.db.locked():
            existing = {t.name for t in Catalog.load(self.db.directory).tables}
            for table, rows in DEMO_DATA.items():
                if table not in existing:
                    columns = [ColumnDefinition("ITEM", "S1", 50), ColumnDefinition("QUANTITY", "N1", 10)]
                    self.session.run(CreateTableStmt(table, columns))
                    for item, quantity in rows:
                        self.session.run(InsertStmt(table, [item, str(quantity)]))

    def locked(self):
        return self.db.locked()

    def _select(self, table: str, condition: Condition | None = None) -> list[tuple[str, int]]:
        statement = SelectStmt([ColumnItem("ITEM"), ColumnItem("QUANTITY")], table, condition)
        return [(item, int(quantity)) for item, quantity in self.session.run(statement).table.rows]

    def all_items(self, table: str) -> list[tuple[str, int]]:
        return self._select(table)

    def matching(self, table: str, pattern: str) -> list[tuple[str, int]]:
        return self._select(table, Condition("ITEM", Operator.LIKE, pattern))

    def exact(self, table: str, item: str) -> list[tuple[str, int]]:
        return self._select(table, Condition("ITEM", Operator.EQUAL, item))

    def update(self, table: str, item: str, quantity: int) -> None:
        condition = Condition("ITEM", Operator.EQUAL, item)
        self.session.run(UpdateStmt(table, [Assignment("QUANTITY", str(quantity))], condition))


class _SqlBackend:
    """Stock in SQLite or MariaDB, with parameterized queries."""

    def __init__(self, kind: str, sqlite_path: Path):
        self.kind = kind
        self.sqlite_path = sqlite_path
        if kind == "sqlite":
            self._seed_sqlite()

    def locked(self):
        return nullcontext()

    def _connect(self):
        if self.kind == "sqlite":
            return sqlite3.connect(self.sqlite_path)
        try:
            import mariadb
        except ImportError:
            raise RuntimeError(
                "DB_BACKEND=mariadb needs the MariaDB connector: pip install -e '.[mariadb]'"
            ) from None
        return mariadb.connect(**config.MARIADB)

    def _seed_sqlite(self) -> None:
        if self.sqlite_path.exists():
            return
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.sqlite_path)) as conn, conn:
            for table, rows in DEMO_DATA.items():
                conn.execute(f"CREATE TABLE {table} (ITEM VARCHAR(50) PRIMARY KEY, QUANTITY INT NOT NULL)")
                conn.executemany(f"INSERT INTO {table} VALUES (?, ?)", rows)

    def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with closing(self._connect()) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()

    def all_items(self, table: str) -> list[tuple[str, int]]:
        return self._query(f"SELECT ITEM, QUANTITY FROM {table}")

    def matching(self, table: str, pattern: str) -> list[tuple[str, int]]:
        return self._query(f"SELECT ITEM, QUANTITY FROM {table} WHERE UPPER(ITEM) LIKE ?", (pattern,))

    def exact(self, table: str, item: str) -> list[tuple[str, int]]:
        return self._query(f"SELECT ITEM, QUANTITY FROM {table} WHERE ITEM = ?", (item,))

    def update(self, table: str, item: str, quantity: int) -> None:
        with closing(self._connect()) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET QUANTITY = ? WHERE ITEM = ?", (quantity, item))
            conn.commit()


class Inventory:
    def __init__(self, backend: str | None = None, sqlite_path: Path | None = None, tinysql_dir: Path | None = None):
        self.backend = (backend or config.DB_BACKEND).lower()
        if self.backend == "tinysql":
            self._db = _TinySqlBackend(Path(tinysql_dir or config.TINYSQL_DIR))
        elif self.backend in ("sqlite", "mariadb"):
            self._db = _SqlBackend(self.backend, Path(sqlite_path or config.SQLITE_PATH))
        else:
            raise ValueError(f"DB_BACKEND must be 'tinysql', 'sqlite' or 'mariadb', not {self.backend!r}.")

    # -- read tools ----------------------------------------------------------

    def list_items(self, table: str) -> list[dict]:
        table = _check_table(table)
        with self._db.locked():
            rows = self._db.all_items(table)
        return [{"item": item, "quantity": qty} for item, qty in sorted(rows)]

    def find_item(self, item: str) -> list[dict]:
        """Search every table for items whose name contains `item`."""
        pattern = f"%{_norm_item(item)}%"
        found = []
        with self._db.locked():
            for table in TABLES:
                found += [{"table": table, "item": i, "quantity": q} for i, q in self._db.matching(table, pattern)]
        return found

    # -- write tools ---------------------------------------------------------

    def adjust_quantity(self, table: str, item: str, delta: int) -> dict:
        """Add (positive delta) or remove (negative delta) stock for one item."""
        table, item = _check_table(table), _norm_item(item)
        with self._db.locked():  # the read and the write are one step: nobody can change the stock in between
            current = self._current(table, item)
            new = current + delta
            if new < 0:
                raise InventoryError(f"Only {current} {item} in stock, cannot remove {-delta}.")
            return self._write(table, item, new, before=current)

    def set_quantity(self, table: str, item: str, quantity: int) -> dict:
        table, item = _check_table(table), _norm_item(item)
        if quantity < 0:
            raise InventoryError("Quantity cannot be negative.")
        with self._db.locked():
            return self._write(table, item, quantity, before=self._current(table, item))

    def _current(self, table: str, item: str) -> int:
        rows = self._db.exact(table, item)
        if not rows:
            raise InventoryError(f"{item} is not in {table}. Use find_item to check the name.")
        return rows[0][1]

    def _write(self, table: str, item: str, quantity: int, before: int) -> dict:
        try:
            self._db.update(table, item, quantity)
        except SqlError as e:  # for example a number too long for its column
            raise InventoryError(str(e)) from None
        return {"table": table, "item": item, "before": before, "after": quantity}
