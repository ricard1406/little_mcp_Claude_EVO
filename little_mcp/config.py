"""Settings, read once from the environment and the project's .env file.

All paths are resolved from the project root, so the app works no matter
which folder you start it from.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = Path(os.getenv("LITTLE_MCP_DATA_DIR", PROJECT_ROOT / "data"))
INDEX_DIR = Path(os.getenv("LITTLE_MCP_INDEX_DIR", PROJECT_ROOT / ".index"))

# Network calls never wait longer than this (seconds).
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# Your time zone, such as Europe/Rome: the day TODAY means in myTinySQL, and the local
# time of get_datetime. Empty: this computer's zone. It matters when the server runs on
# a machine set to another zone than yours (a cloud server is often set to UTC).
TIMEZONE = os.getenv("LITTLE_MCP_TIMEZONE", "").strip()

# Saved conversations: one file per chat (see history.py).
CHATS_DIR = Path(os.getenv("LITTLE_MCP_CHATS_DIR", DATA_DIR / "chats"))

DB_BACKEND = os.getenv("DB_BACKEND", "tinysql").lower()
TINYSQL_DIR = Path(os.getenv("TINYSQL_DIR", DATA_DIR / "tinysql"))
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", DATA_DIR / "mystore.db"))
MARIADB = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "database": os.getenv("DB_NAME", "MYSTORE"),
    "user": os.getenv("DB_USER", ""),
    "password": os.getenv("DB_PASSWORD", ""),
}

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
