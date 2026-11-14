# myTinySQL: Rust vs Python

The same small program, in a third language: the Rust version
([github.com/ricard1406/myTinySql-rust](https://github.com/ricard1406/myTinySql-rust)) and this Python port,
in `little_mcp/tinysql/`. These notes compare the two, idea by idea. They continue
[`docs/cpp-vs-rust.md`](https://github.com/ricard1406/myTinySql-rust/blob/main/docs/cpp-vs-rust.md) in the Rust project.

The port keeps the design: the same modules with the same jobs, the same function names, the same error
messages and the same files on disk. Only the language changes.

## How we know they behave the same

| Check | Result |
| --- | --- |
| The 8 test scripts of the C++ and Rust versions, with their `.expected` files | Identical, byte for byte, except one line changed on purpose (section 8) |
| The Rust unit tests | Ported to Python: all pass |
| Six scripts run in a row by both programs (`create`, `insert`, `update`, `delete`, `apostrophes`, `errors`) | Identical output (330 lines) and identical data files |
| Tables written by Python, then read and changed by the Rust program, then read by Python again | Works in both directions, with `’`, `è` and `d'Asti` in the values |

## File by file

| Rust | Python | Lines | Notes |
| --- | --- | --- | --- |
| `util.rs` | `util.py` | 147 → 135 | `SqlError` becomes an exception; the unit tests moved to `tests/` |
| `statement.rs` | `statement.py` | 156 → 169 | One dataclass per variant |
| `parser.rs` | `parser.py` | 562 → 477 | Same tokenizer and recursive descent |
| `catalog.rs` | `catalog.py` | 177 → 117 | |
| `table_file.rs` | `table_file.py` | 234 → 170 | Lines are `bytearray`, see section 3 |
| `session.rs` | `session.py` | 476 → 509 | `ResultSet` keeps only what the console needs |
| `output.rs` | `output.py` | 169 → 37 | No JSON: there is no browser server |
| `server.rs` | (none) | 287 → 0 | The engine lives inside the MCP server, so it needs no port of its own |
| `main.rs` | `__main__.py` | 210 → 86 | Without the server options |
| (none) | `dates.py` | 0 → 68 | Only in Python, see section 8 |
| (none) | `database.py` | 0 → 194 | Only in Python, see section 7 |

## 1. `enum` and `match` → dataclasses and `match`

In Rust, a statement is an `enum`, and the compiler refuses a `match` that forgets a variant:

```rust
match statement {
    Statement::Select(stmt) => self.run_select(&stmt),
    Statement::Insert(stmt) => self.run_insert(&stmt),
    // leave one out, and it does not compile
}
```

Python has had `match` since 3.10, and each variant is a dataclass:

```python
match statement:
    case SelectStmt():
        return self._run_select(statement)
    case InsertStmt():
        return self._run_insert(statement)
raise TypeError(f"not a statement: {statement!r}")
```

It reads almost the same, but nothing checks it before the program runs. Forget a `case` and you find out from
the last line, at run time, the first time someone uses that statement. A type checker such as `mypy` can
bring back some of what the Rust compiler does.

## 2. `Result` and `?` → exceptions

Rust writes every way of failing into the function's type, and `?` passes the error on, visibly, at each call:

```rust
let catalog = Catalog::load(&self.data_dir)?;
let schema = catalog.table(&stmt.table)?;
```

In Python the same two lines look like nothing can go wrong:

```python
catalog = Catalog.load(self.data_dir)
schema = catalog.table(stmt.table)
```

The `SqlError` flies up by itself until `Session.execute()` catches it. It's shorter, and you can no longer
see, by reading a function, where it might stop. `Parser.fail()` shows the difference well: Rust callers write
`return self.fail(...)`, while in Python `fail` simply raises, and is marked `NoReturn`.

## 3. Bytes and text: the one real trap

This is where a naive port would go wrong. myTinySQL's widths are **bytes**. In Python, `len("D’Annunzio")` is 10
characters, but the file needs 12 bytes, because `’` takes 3. Rust's `str.len()` counts bytes by itself; Python's
`len()` doesn't, so the port has a `byte_len()` and does all its file work on `bytes`:

```python
def _place(record: bytearray, column: Column, value: str) -> None:
    record[column.start : column.to + 1] = value.encode().ljust(column.width)
```

Rust has the opposite problem: a `&str` may never be cut in the middle of a character, so its parser has to reason
about byte positions. The Python parser works on characters and never meets that problem. So each version
counts in the unit that suits it, and they meet at the file.

Case needs the same care. `to_ascii_uppercase` touches only `a-z`, while Python's `"ß".upper()` is `"SS"`. So
the port has `ascii_upper()`, built on `bytes.upper()`, which is ASCII-only.

## 4. Integers that never overflow

The golden tests found this one. Rust's integers have a fixed size, so `parse_integer` fails on a huge number, and a
huge column width stops at the largest `usize` thanks to `saturating_add`. Python's integers grow as needed, so the
port has to put those limits back by hand, or `errors` and `create` print different results:

```python
return value if _I64_MIN <= value <= _I64_MAX else None
```

The first version of the port missed the second limit. One test failed by a single digit
(`...551619` instead of `...551615`), and that is what the golden tests are for.

## 5. Ownership and lifetimes → nothing to write

```rust
pub struct TableFile<'a> {
    schema: &'a TableSchema,
    path: PathBuf,
}
```

The `'a` tells the compiler that the schema must outlive the `TableFile`. The Python version has no such thing:

```python
class TableFile:
    def __init__(self, schema: TableSchema, path: Path):
```

The garbage collector keeps the schema alive for as long as anything points to it. There is less to write and
less to learn, but also no compiler checking who may change what. In Python, two objects can share a list
and change it without anyone noticing. That is why the statements here are `frozen=True` dataclasses.

## 6. Closures and `impl Fn`

Very close in both. Rust passes `impl Fn(&Row) -> bool`, resolved at compile time. Python passes a `lambda`:

```python
TableFile(schema, data_file).remove_rows(lambda row: _matches(row[where_index], stmt.condition))
```

The price is speed: every call is looked up at run time. For a table of a few hundred rows, nobody will notice.

## 7. What exists only in Python: `database.py`

The Rust program is a separate server handling one request at a time, and people write its SQL. Here the engine
lives inside the MCP server and an **AI writes the SQL**, which needs three things that Rust's `server.rs` did
differently or not at all:

- **A file lock** (`fcntl.flock`) instead of one thread, so that two servers on the same folder take turns.
- **A policy.** Your own parser reads every statement, and `isinstance(statement, allowed)` decides whether it may run. A
  `DELETE` hidden after a line break or a `;` in a "read" script is refused, because the decision is made on
  what the parser understood, and before anything runs.
- **All-or-nothing scripts,** by saving every file and putting them back after a failure. It's the simplest possible
  transaction. It reads the whole database for each write, and it wouldn't survive a power cut half-way: for
  that, real databases keep a journal. A good next step for this project.

Two small changes come from the same reason. `_check_fits` refuses a value containing a line break, because here a
statement can reach the engine without going through the line-by-line splitter. And `store.py` builds statements
directly as objects, so a value is never parsed at all: this is what a parameterized query does in other databases.

## 8. When the dialect grows: `DATE` and `ALTER TABLE`

Up to section 7, the three versions speak exactly the same SQL. The Python version is the first to add to it:
a `DATE` type and `ALTER TABLE ... ADD` / `MODIFY`. Three things are worth seeing in how that was done.

**The files stay readable by the older programs.** SYSTEM_TABLE records a type code for each column, `S1` or
`N1`, and C++ and Rust read it as text without checking it. So a `DATE` column is recorded as `D1`, and the older
programs simply show it as text. `ALTER TABLE` writes the same fixed-width files, only with new positions. A
table that Python has altered can still be read and changed by the Rust program (tested with `BOOKS`, after a
`MODIFY` and an `ADD`). What the older programs cannot do is check a date: they would accept `2026-02-30`.

**A changed spec means a changed test.** The golden files are the specification all three versions share. One
error message had to change: an unknown type used to say "use CHAR(<width>) or NUMBER(<width>)", and that is no
longer true. So that one line of `create.expected` now differs from the C++ and Rust copies, and the golden
README says so. If those versions get `DATE` too, they take the same line.

**A date is stored as text that sorts.** `YYYY-MM-DD` sorts as text in the same order as in time, so the engine's
existing comparisons work on dates with no new code. The only new rules are at the edges: a value must be a real
day, `TODAY` is resolved in a chosen time zone (with `zoneinfo`, from Python's standard library; Rust's standard
library has no time zones), and an empty date passes no comparison, like `NULL` in SQL.

`ALTER TABLE` shows the price of fixed-width rows: there is no free space, so changing one column's width moves
every column after it, in every row. The engine prepares both files in memory, checks everything, and only then
replaces them. The window between the two replacements is the next thing a journal would close.

## 9. Decimal numbers: when text order is wrong

The C++ and Rust versions compare two values as numbers only when both are whole numbers. Anything else is
compared as text, character by character. For whole numbers that is enough. For prices it is not:

```text
> SELECT DAY FROM STOCKS WHERE ASML > 1000          (the Rust program)
2026-11-13        ASML = 1338.80   right
2026-11-05        ASML = 999.50    wrong: as text, "9" comes after "1"
```

The Python version adds `parse_number()`, which also reads a decimal written with a point, and compares with
`Decimal`, Python's exact decimal type. A `float` would not do for money: `0.1 + 0.2` is `0.30000000000000004`
as a float, and exactly `0.3` as a `Decimal`.

Values in a `NUMBER` column are now checked when they go in, as dates are, so `1.338,80` (the Italian way of
writing 1338.80) is refused with a hint instead of being stored as text that never compares correctly. The C++
and Rust versions accept any text in a `NUMBER` column. All 8 golden scripts still pass with the check, so none
of them relied on that.
