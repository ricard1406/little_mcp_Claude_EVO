"""The warehouse inventory database.

The LLM never writes SQL. It calls small tools (list, find, adjust, set),
and this module turns them into fixed, parameterized queries against a
whitelist of tables. That blocks SQL injection and stops a confused or
prompt-injected model from touching anything else in the database.

Backends:
  sqlite  (default) a demo database in data/mystore.db, created on first use
  mariadb the MYSTORE database from the original project
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from . import config

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


class Inventory:
    def __init__(self, backend: str | None = None, sqlite_path: Path | None = None):
        self.backend = (backend or config.DB_BACKEND).lower()
        self.sqlite_path = Path(sqlite_path or config.SQLITE_PATH)
        if self.backend == "sqlite":
            self._seed_sqlite()
        elif self.backend != "mariadb":
            raise ValueError(f"DB_BACKEND must be 'sqlite' or 'mariadb', not {self.backend!r}.")

    # -- connections ---------------------------------------------------------

    def _connect(self):
        if self.backend == "sqlite":
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

    # -- read tools ----------------------------------------------------------

    def list_items(self, table: str) -> list[dict]:
        table = _check_table(table)
        rows = self._query(f"SELECT ITEM, QUANTITY FROM {table} ORDER BY ITEM")
        return [{"item": item, "quantity": qty} for item, qty in rows]

    def find_item(self, item: str) -> list[dict]:
        """Search every table for items whose name contains `item`."""
        pattern = f"%{_norm_item(item)}%"
        found = []
        for table in TABLES:
            rows = self._query(f"SELECT ITEM, QUANTITY FROM {table} WHERE UPPER(ITEM) LIKE ?", (pattern,))
            found += [{"table": table, "item": i, "quantity": q} for i, q in rows]
        return found

    # -- write tools ---------------------------------------------------------

    def adjust_quantity(self, table: str, item: str, delta: int) -> dict:
        """Add (positive delta) or remove (negative delta) stock for one item."""
        table, item = _check_table(table), _norm_item(item)
        current = self._current(table, item)
        new = current + delta
        if new < 0:
            raise InventoryError(f"Only {current} {item} in stock, cannot remove {-delta}.")
        return self._write(table, item, new, before=current)

    def set_quantity(self, table: str, item: str, quantity: int) -> dict:
        table, item = _check_table(table), _norm_item(item)
        if quantity < 0:
            raise InventoryError("Quantity cannot be negative.")
        return self._write(table, item, quantity, before=self._current(table, item))

    def _current(self, table: str, item: str) -> int:
        rows = self._query(f"SELECT QUANTITY FROM {table} WHERE ITEM = ?", (item,))
        if not rows:
            raise InventoryError(f"{item} is not in {table}. Use find_item to check the name.")
        return rows[0][0]

    def _write(self, table: str, item: str, quantity: int, before: int) -> dict:
        with closing(self._connect()) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET QUANTITY = ? WHERE ITEM = ?", (quantity, item))
            conn.commit()
        return {"table": table, "item": item, "before": before, "after": quantity}
