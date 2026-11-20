"""The Little MCP server: a real Model Context Protocol server.

Any MCP client can use it: the Little MCP agent in this project, Claude
Desktop, Claude Code, the MCP Inspector, and so on. Clients discover the
tools by asking the server (tools/list), so nothing is hardcoded on their
side.

Run it:
    python -m little_mcp.server                     # stdio (clients start it for you)
    python -m little_mcp.server --transport http    # http://127.0.0.1:8000/mcp
"""

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import anyio
import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import calculator, config
from .rag import DocumentIndex
from .store import Inventory, Table
from .tinysql import Database

# In stdio mode stdout carries the protocol, so logs must go to stderr.
# Short "[server]" lines, since they appear in the middle of the chat.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="  [server] %(message)s")
for noisy in ("mcp", "httpx", "chromadb"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("little_mcp")


class _QuietSessionEnd(logging.Filter):
    """Hides one false alarm of the MCP library (mcp 1.30) in HTTP mode.

    When a client ends its session normally, the server closes the session's
    message stream. If the helper that feeds the client's event stream is still
    waiting on it, it gets ClosedResourceError, and the library logs that as
    "Error in standalone SSE writer" with a full traceback. Nothing went wrong.
    It depends on network timing: seen between two computers, never on one.

    Only that message with that cause is hidden; any other error still shows.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        return not (record.getMessage() == "Error in standalone SSE writer"
                    and isinstance(error, anyio.ClosedResourceError))


logging.getLogger("mcp.server.streamable_http").addFilter(_QuietSessionEnd())

mcp = FastMCP(
    "little-mcp",
    instructions=(
        "Tools for a small business assistant: current time and weather for a city, "
        "arithmetic, search over the user's local documents, a warehouse inventory "
        "with the tables FRUITS and VEGGIE, and a small SQL database (myTinySQL) "
        "where the user can keep tables of their own."
    ),
    host=config.MCP_HOST,
    port=config.MCP_PORT,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True)
READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

_docs = DocumentIndex()
_inventory: Inventory | None = None
_database: Database | None = None


def inventory() -> Inventory:
    # Created on first use, so a misconfigured database only breaks the inventory tools.
    global _inventory
    if _inventory is None:
        _inventory = Inventory()
    return _inventory


def database() -> Database:
    global _database
    if _database is None:
        _database = Database(config.TINYSQL_DIR, config.TIMEZONE)
    return _database


# --- Time and weather ---------------------------------------------------------


def _geocode(city: str):
    from geopy.geocoders import Nominatim

    location = Nominatim(user_agent="little-mcp-evo").geocode(city, timeout=config.HTTP_TIMEOUT, language="en")
    if location is None:
        raise ValueError(f"City {city!r} not found.")
    return location


@mcp.tool(annotations=READ_ONLY_WEB)
def get_datetime(city: str = "") -> dict:
    """Current date, time and day of the week.

    Args:
        city: A city such as "Tokyo" or "Paris, France". Leave empty for the user's own time.
    """
    if not city.strip():
        # The user's zone from .env (LITTLE_MCP_TIMEZONE), or this computer's.
        now = datetime.now(ZoneInfo(config.TIMEZONE)) if config.TIMEZONE else datetime.now().astimezone()
        return {"city": "local", "timezone": config.TIMEZONE or str(now.tzinfo),
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"), "day_of_week": now.strftime("%A")}

    from timezonefinder import TimezoneFinder

    location = _geocode(city)
    tz_name = TimezoneFinder().timezone_at(lat=location.latitude, lng=location.longitude)
    if tz_name is None:
        raise ValueError(f"Could not determine the time zone of {city!r}.")
    now = datetime.now(ZoneInfo(tz_name))
    return {
        "city": location.address.split(",")[0],
        "timezone": tz_name,
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "day_of_week": now.strftime("%A"),
    }


@mcp.tool(annotations=READ_ONLY_WEB)
def get_weather(city: str) -> dict:
    """Current weather for a city (metric units).

    Args:
        city: A city such as "London" or "London, UK".
    """
    if not config.OPENWEATHER_API_KEY:
        raise ValueError("The weather tool is not configured: set OPENWEATHER_API_KEY in .env.")
    response = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"q": city, "appid": config.OPENWEATHER_API_KEY, "units": "metric"},
        timeout=config.HTTP_TIMEOUT,
    )
    if response.status_code == 404:
        raise ValueError(f"City {city!r} not found.")
    response.raise_for_status()
    data = response.json()
    # A short summary is easier for small local models than the full API reply.
    return {
        "city": f"{data['name']}, {data['sys'].get('country', '')}".strip(", "),
        "description": data["weather"][0]["description"],
        "temperature_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "humidity_pct": data["main"]["humidity"],
        "wind_m_s": data.get("wind", {}).get("speed"),
    }


# --- Calculator ---------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def calculate(expression: str) -> float | int:
    """Evaluate an arithmetic expression exactly, e.g. "15 * 3 + 7" or "(120 - 15) / 4".

    Supports + - * / // % ** and parentheses.
    """
    return calculator.evaluate(expression)


# --- Documents ----------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def search_documents(query: str, max_results: int = 4) -> list[dict]:
    """Search the user's local documents (PDF, txt, md in the data folder).

    Returns the most relevant passages with their file name and page. Use it
    for any question about the user's own documents, e.g. the list of candidates.

    Args:
        query: What to look for, in natural language.
        max_results: How many passages to return (1-10).
    """
    try:
        return _docs.search(query, k=max(1, min(max_results, 10)))
    except Exception as e:
        log.exception("Document search failed")
        raise ValueError(
            f"Document search failed ({e}). It needs Ollama running with the "
            f"'{config.EMBED_MODEL}' model: ollama pull {config.EMBED_MODEL}"
        ) from e


@mcp.resource("docs://files", mime_type="text/plain")
def list_document_files() -> str:
    """The documents available to search_documents."""
    files = _docs.files()
    return "\n".join(str(p.relative_to(_docs.data_dir)) for p in files) or "(no documents)"


# --- Warehouse inventory ------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def list_inventory(table: Table) -> list[dict]:
    """List every item and its quantity in one warehouse table (FRUITS or VEGGIE)."""
    return inventory().list_items(table)


@mcp.tool(annotations=READ_ONLY)
def find_item(item: str) -> list[dict]:
    """Find an item in the warehouse by (part of) its name, in every table.

    Use it to answer questions like "do we have oranges?". An empty list means
    the item is not stocked at all.
    """
    return inventory().find_item(item)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def adjust_stock(table: Table, item: str, delta: int) -> dict:
    """Add stock (positive delta) or remove stock (negative delta) for one item.

    Example: "we sold 2 apples" -> adjust_stock("FRUITS", "APPLES", -2).
    Use find_item first if you are unsure of the exact item name.
    """
    return inventory().adjust_quantity(table, item, delta)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
def set_stock(table: Table, item: str, quantity: int) -> dict:
    """Overwrite the quantity of one item, e.g. after a stock count.

    Prefer adjust_stock when the user describes a change ("add 3", "sold 2").
    """
    return inventory().set_quantity(table, item, quantity)


# --- myTinySQL ----------------------------------------------------------------
#
# Here the model writes real SQL, in myTinySQL's small dialect. Your own parser
# reads every statement, and each tool runs only the kinds it allows. There are
# two tools so that MCP clients can see which one changes data, and ask first.


@mcp.tool(annotations=READ_ONLY)
def tiny_sql_read(sql: str) -> dict:
    """Read the user's own tables in the myTinySQL database. Only SELECT and DESCRIBE.

    Grammar (one statement per line):
      DESCRIBE                     list the tables
      DESCRIBE <table>             list the columns of a table (TYPE: S1 CHAR, N1 NUMBER, D1 DATE)
      SELECT * FROM <table>
      SELECT <col>,<col> FROM <table> WHERE <col> <op> 'value'

    <op> is = != < <= > >= or LIKE (LIKE ignores case; % is any text, _ one letter).
    A number compares as a number (decimals too: WHERE PRICE > 1300.5). A DATE compares as a date: write 'YYYY-MM-DD'
    or TODAY, e.g. WHERE DUE < TODAY. An empty date matches only = ''.
    WHERE takes one condition. There is no AND, OR, ORDER BY, COUNT or JOIN:
    read the rows and work out the rest yourself.

    Args:
        sql: One or more statements, one per line.
    """
    return database().read(sql)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
def tiny_sql_write(sql: str) -> dict:
    """Create tables and change rows in the user's myTinySQL database.

    Grammar (one whole statement per line, never split a statement over lines):
      CREATE TABLE <table> (ID NUMBER(10), <col> CHAR(<width>), <col> NUMBER(<width>), <col> DATE)
      ALTER TABLE <table> ADD <col> CHAR(<width>)        a new column, empty in the existing rows
      ALTER TABLE <table> MODIFY <col> CHAR(<width>)     a new width (or type) for a column
      INSERT INTO <table> VALUES ('text', 'text')
      UPDATE <table> SET <col> = 'value' WHERE <col> <op> 'value'
      DELETE FROM <table> WHERE <col> <op> 'value'

    Rules:
    - A first column called ID is numbered automatically: leave it out of INSERT.
    - Names: up to 20 letters, digits, _ or -, starting with a letter.
    - The types are CHAR, NUMBER and DATE. Use NUMBER for any number, whole or
      decimal (prices, amounts), DATE for a single day, and CHAR for anything else
      (a period like '1840-1842').
    - A NUMBER is written with a point for decimals and no thousands separators:
      convert the Italian 1.338,80 to 1338.80. NUMBER(<width>) counts every
      character, so 1338.80 needs NUMBER(7) or more. There is no NUMBER(10,2).
      Show numbers and dates to the user in their own format if they prefer.
    - A DATE is written 'YYYY-MM-DD' (convert 13/11/2026 to '2026-11-13'), or TODAY
      for the user's today. '' means no date.
    - Widths are in bytes and a longer value is refused, so be generous, for
      example CHAR(100) for a title. An accented letter takes 2 bytes or more.
    - Put text in single quotes. Write an apostrophe at the start or end of a
      word twice, as in 'Ca'' Foscari'.
    - One row per INSERT. UPDATE and DELETE always need a WHERE with one condition.
    - If a value is too long, or a type is wrong, change the column with ALTER TABLE
      MODIFY, then send the INSERT again. A table cannot be dropped yet.

    The whole script is all-or-nothing: if one statement fails, nothing is
    changed. Fix the problem and send the whole script again.

    Args:
        sql: The statements, one per line.
    """
    return database().write(sql)


# --- Entry point --------------------------------------------------------------


class _QuietShutdown(logging.Filter):
    """Hides uvicorn's "ASGI callable returned without completing response" while the server stops.

    A client such as Claude Code keeps a connection open to listen for the
    server's messages. Ctrl+C cuts it, and uvicorn reports the unfinished answer
    as an error. While stopping, that is expected; at any other time it could
    be a real bug, so then it still shows.
    """

    def __init__(self, server):
        super().__init__()
        self.server = server

    def filter(self, record: logging.LogRecord) -> bool:
        return not (self.server.should_exit
                    and record.getMessage() == "ASGI callable returned without completing response.")


def serve_http() -> None:
    """What mcp.run(transport="streamable-http") does, with a clean Ctrl+C.

    uvicorn handles Ctrl+C itself: it stops gracefully, then raises Ctrl+C
    again so the program ends. Nothing caught it, so Python printed a traceback.
    Starting uvicorn here also lets the filter above know when it is stopping.
    """
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host=config.MCP_HOST, port=config.MCP_PORT,
                                          log_level=mcp.settings.log_level.lower()))
    logging.getLogger("uvicorn.error").addFilter(_QuietShutdown(server))
    log.info("Little MCP server on http://%s:%s/mcp (Ctrl+C to stop)", config.MCP_HOST, config.MCP_PORT)
    try:
        anyio.run(server.serve)
    except KeyboardInterrupt:
        pass
    log.info("Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Little MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio (default): a client starts the server. http: run it standalone.")
    args = parser.parse_args()
    if args.transport == "http":
        serve_http()
        return
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:  # Ctrl+C in the terminal chat reaches this child process too
        pass


if __name__ == "__main__":
    main()
