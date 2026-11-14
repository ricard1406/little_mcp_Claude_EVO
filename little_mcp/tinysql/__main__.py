"""The command line, and the SQL console (Rust: main.rs, without the browser server).

    python -m little_mcp.tinysql                          the console, on your tables (data/tinysql)
    python -m little_mcp.tinysql --sqlfile=script.sql     run a script
    python -m little_mcp.tinysql --data=tests/tinysql_golden/data

In the console the arrow keys bring back earlier statements. It is safe to use
while the chat is running: each statement waits for its turn, as the tools do.
There is no Allow question here, because you type the SQL yourself.

Exit code: 0 on success, 1 if a script statement failed, 2 for a usage error
or a missing file or data directory.
"""

import argparse
import sys
from pathlib import Path

from .database import Database
from .output import render_text
from .util import text_of


def _print(text: str) -> None:
    sys.stdout.buffer.write(text.encode())
    sys.stdout.buffer.flush()


def run_script(db: Database, data: bytes) -> int:
    with db.locked():
        results = db.session.execute_script(text_of(data))
    for result in results:
        _print(render_text(result))
    return 1 if any(result.failed for result in results) else 0


def run_console(db: Database) -> int:
    try:
        import readline  # noqa: F401  (just importing it gives input() line editing and history)
    except ImportError:  # Windows
        pass
    print(f"myTinySQL console on {db.directory}")
    print("Try DESCRIBE, or SELECT * FROM FRUITS. Statements end at the end of the line. Ctrl+D or EXIT to quit.\n")
    while True:
        try:
            line = input("tinysql> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.strip().upper().rstrip(";") in ("EXIT", "QUIT"):
            return 0
        with db.locked():
            results = db.session.execute_script(line)
        for result in results:
            _print(render_text(result))


def main(argv: list[str] | None = None) -> int:
    from .. import config  # the project's settings: where your tables are, and your time zone

    parser = argparse.ArgumentParser(prog="python -m little_mcp.tinysql", description="myTinySQL")
    parser.add_argument("--sqlfile", help="run the statements in FILE ('-' for standard input)")
    parser.add_argument("--data", default=str(config.TINYSQL_DIR),
                        help=f"directory with SYSTEM_TABLE and the *_DATA files (default: {config.TINYSQL_DIR})")
    args = parser.parse_args(argv)

    if not (Path(args.data) / "SYSTEM_TABLE").is_file():
        print(f"no database in '{args.data}' (use --data=DIR; the chat creates data/tinysql on first use)",
              file=sys.stderr)
        return 2

    db = Database(args.data, config.TIMEZONE)
    if args.sqlfile is None and sys.stdin.isatty():
        return run_console(db)
    if args.sqlfile is None or args.sqlfile == "-":
        return run_script(db, sys.stdin.buffer.read())
    try:
        data = Path(args.sqlfile).read_bytes()
    except OSError:
        print(f"cannot open '{args.sqlfile}'", file=sys.stderr)
        return 2
    return run_script(db, data)


if __name__ == "__main__":
    sys.exit(main())
