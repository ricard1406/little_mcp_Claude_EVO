"""The command line (Rust: main.rs, without the browser server).

    python -m little_mcp.tinysql --data=data/tinysql --sqlfile=script.sql   run a script
    python -m little_mcp.tinysql --data=data/tinysql                        type statements

Exit code: 0 on success, 1 if a script statement failed, 2 for a usage error
or a missing file or data directory.
"""

import argparse
import sys
from pathlib import Path

from .output import render_text
from .session import Session
from .util import text_of


def _print(text: str) -> None:
    sys.stdout.buffer.write(text.encode())
    sys.stdout.buffer.flush()


def run_script(session: Session, data: bytes) -> int:
    exit_code = 0
    for result in session.execute_script(text_of(data)):
        _print(render_text(result))
        if result.failed:
            exit_code = 1
    return exit_code


def run_interactive(session: Session) -> int:
    while True:
        _print("tinysql> ")
        line = sys.stdin.buffer.readline()
        if not line:
            _print("\n")
            return 0
        for result in session.execute_script(text_of(line)):
            _print(render_text(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m little_mcp.tinysql", description="myTinySQL")
    parser.add_argument("--sqlfile", help="run the statements in FILE ('-' for standard input)")
    parser.add_argument("--data", default="data/tinysql", help="directory with SYSTEM_TABLE and the *_DATA files")
    args = parser.parse_args(argv)

    if not Path(args.data).is_dir():
        print(f"data directory '{args.data}' not found (use --data=DIR)", file=sys.stderr)
        return 2

    session = Session(args.data)
    if args.sqlfile is None and sys.stdin.isatty():
        return run_interactive(session)
    if args.sqlfile is None or args.sqlfile == "-":
        return run_script(session, sys.stdin.buffer.read())
    try:
        data = Path(args.sqlfile).read_bytes()
    except OSError:
        print(f"cannot open '{args.sqlfile}'", file=sys.stderr)
        return 2
    return run_script(session, data)


if __name__ == "__main__":
    sys.exit(main())
