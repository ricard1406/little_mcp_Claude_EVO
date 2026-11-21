"""The log: a quiet console for users, a detailed file for whoever wants to know what happened.

    data/logs/little_mcp.log

One line per event, in columns, so it reads well and is easy to search:

    2026-11-21 20:31:05  INFO   tools     tiny_sql_write by claude-code from 192.168.192.128, 34 ms: {"sql": "INSERT..."}
    2026-11-21 20:32:10  WARN   share     refused a wrong or missing password, from 192.168.192.128

time, level, area (app, chat, settings, share, tools, docs, http), and what happened.

The app and the MCP server it starts are two processes, and both write to the
same file. Lines are appended (the system keeps each short write whole), and the
file is rotated only when the app starts: little_mcp.log -> .1 -> .2 -> .3, so a
running process never loses its file. Passwords, keys and the text of the chats
are never written here: the chats have their own files in data/chats.
"""

import logging
import sys
from pathlib import Path

from . import config

LOG_DIR = config.DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "little_mcp.log"
MAX_BYTES = 2_000_000  # rotated at start when bigger: about 15,000 lines
KEEP = 3  # old files kept: .1, .2, .3

AREAS = {  # logger name -> the short area shown in the file
    "little_mcp.app": "app", "little_mcp.chat": "chat", "little_mcp.settings": "settings",
    "little_mcp.share": "share", "little_mcp.tools": "tools", "little_mcp.docs": "docs",
    "little_mcp.rag": "docs", "little_mcp": "server", "uvicorn.access": "http", "uvicorn.error": "http",
}
LEVELS = {"WARNING": "WARN", "CRITICAL": "ERROR"}


class _Columns(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.area = AREAS.get(record.name, record.name.split(".")[0])
        record.lvl = LEVELS.get(record.levelname, record.levelname)
        return super().format(record)


class _NotInThePage(logging.Filter):
    """Keeps off the console what the page already shows: failed answers and failed tool calls.

    A failed tool call is often routine (the model fixes its arguments and tries again).
    Both stay in the file, with all their details.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name not in ("little_mcp.chat", "little_mcp.tools")


FILE_FORMAT = _Columns("%(asctime)s  %(lvl)-5s  %(area)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def rotate(path: Path = LOG_FILE, max_bytes: int = MAX_BYTES, keep: int = KEEP) -> None:
    """At start only: if the log is big, shift little_mcp.log -> .1 -> .2 ..., dropping the oldest."""
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    for n in range(keep, 0, -1):
        older = path.with_name(f"{path.name}.{n}")
        newer = path.with_name(f"{path.name}.{n - 1}") if n > 1 else path
        if newer.exists():
            newer.replace(older)


def setup(console_level: int = logging.WARNING, verbose: bool = False, path: Path = LOG_FILE,
          console_stream=None) -> None:
    """Send the log to the file (everything) and to the console (only console_level and up).

    verbose: also the technical details, such as every HTTP request (to the file and the console).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):  # setup() may run twice, as in the tests
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    to_file = logging.FileHandler(path, encoding="utf-8")  # mode "a": appends
    to_file.setFormatter(FILE_FORMAT)
    root.addHandler(to_file)

    to_console = logging.StreamHandler(console_stream or sys.stderr)
    to_console.setLevel(logging.DEBUG if verbose else console_level)
    to_console.setFormatter(logging.Formatter("%(message)s"))
    if not verbose:
        to_console.addFilter(_NotInThePage())
    root.addHandler(to_console)

    # Other libraries talk a lot. Their warnings stay; their routine stays out, unless verbose.
    quiet = logging.DEBUG if verbose else logging.WARNING
    for name in ("httpx", "httpx2", "httpcore", "mcp", "chromadb", "gradio", "urllib3", "asyncio", "anthropic",
                 "langchain", "langgraph", "uvicorn.access"):
        logging.getLogger(name).setLevel(quiet)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO if verbose else logging.WARNING)
