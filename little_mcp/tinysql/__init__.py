"""myTinySQL: a tiny SQL interpreter over fixed-width text files.

This is a port of the Rust version (https://github.com/ricard1406/myTinySql-rust),
itself a port of the C++ one (https://github.com/ricard1406/myTinySql). It keeps
the same statements, the same error messages and the same files on disk, and
each Rust module has a Python module with the same job, so the versions can be
read side by side. docs/rust-vs-python.md compares them.

| Rust            | Python          | Job                                       |
| --------------- | --------------- | ----------------------------------------- |
| util.rs         | util.py         | SqlError and string helpers               |
| statement.rs    | statement.py    | The parsed form of every statement        |
| parser.rs       | parser.py       | Tokenizer, parser, and splitting a script |
| catalog.rs      | catalog.py      | Reading SYSTEM_TABLE                      |
| table_file.rs   | table_file.py   | Reading and changing one data file        |
| session.rs      | session.py      | Running statements                        |
| output.rs       | output.py       | Console text                              |
| main.rs         | __main__.py     | The command line                          |
| (none)          | database.py     | Lock and policy for the MCP server        |
| (none)          | dates.py        | The DATE type and TODAY                   |

Every input becomes SQL text. split_statements() cuts it into statements,
parse_statement() turns each one into a Statement, and Session.run() runs it.
"""

from .database import Database
from .session import Session, StatementResult
from .util import SqlError

__all__ = ["Database", "Session", "SqlError", "StatementResult"]
