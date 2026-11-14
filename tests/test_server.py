"""Talk to the MCP server over the real protocol, in memory."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from little_mcp import server
from little_mcp.tinysql import Database


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, inventory, doc_index):
    monkeypatch.setattr(server, "_inventory", inventory)
    monkeypatch.setattr(server, "_database", Database(tmp_path / "user-tables"))
    monkeypatch.setattr(server, "_docs", doc_index)


def connect():
    return create_connected_server_and_client_session(server.mcp)


def data(result):
    if result.structuredContent is not None:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


async def test_tools_are_discoverable():
    async with connect() as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert set(tools) == {"get_datetime", "get_weather", "calculate", "search_documents",
                              "list_inventory", "find_item", "adjust_stock", "set_stock",
                              "tiny_sql_read", "tiny_sql_write"}
        writes = {name for name, t in tools.items() if not t.annotations.readOnlyHint}
        assert writes == {"adjust_stock", "set_stock", "tiny_sql_write"}
        assert tools["list_inventory"].inputSchema["properties"]["table"]["enum"] == ["FRUITS", "VEGGIE"]


async def test_calculate():
    async with connect() as session:
        result = await session.call_tool("calculate", {"expression": "15 * 3 + 7"})
        assert not result.isError and result.structuredContent == {"result": 52}


async def test_errors_come_back_as_tool_errors():
    async with connect() as session:
        result = await session.call_tool("calculate", {"expression": "2 +"})
        assert result.isError and "Not a valid arithmetic expression" in result.content[0].text
        result = await session.call_tool("calculate", {"expression": "ADD, 5, 3"})
        assert result.isError and "Only numbers" in result.content[0].text


async def test_stock_round_trip():
    async with connect() as session:
        result = await session.call_tool("adjust_stock", {"table": "FRUITS", "item": "apples", "delta": 2})
        assert data(result)["after"] == 4
        found = await session.call_tool("find_item", {"item": "apples"})
        assert data(found)[0]["quantity"] == 4


async def test_unknown_table_is_rejected_by_the_schema():
    async with connect() as session:
        result = await session.call_tool("list_inventory", {"table": "mysql.user"})
        assert result.isError


async def test_search_documents():
    async with connect() as session:
        result = await session.call_tool("search_documents", {"query": "Dianne", "max_results": 1})
        assert data(result)[0]["source"] == "candidates.txt"


async def test_weather_without_key_explains_setup(monkeypatch):
    async with connect() as session:
        monkeypatch.setattr(server.config, "OPENWEATHER_API_KEY", "")
        result = await session.call_tool("get_weather", {"city": "Paris"})
        assert result.isError and "OPENWEATHER_API_KEY" in result.content[0].text


async def test_local_datetime_needs_no_network():
    async with connect() as session:
        result = await session.call_tool("get_datetime", {})
        assert not result.isError and "day_of_week" in result.content[0].text


async def test_document_list_resource():
    async with connect() as session:
        resource = await session.read_resource("docs://files")
        assert resource.contents[0].text == "candidates.txt"


BOOKS = [
    ("Operette morali", "Giacomo Leopardi", "1835"),
    ("I Promessi Sposi", "Alessandro Manzoni", "1840-1842"),
    ("Confessioni di un italiano", "Ippolito Nievo", "1867"),
    ("Giacinta", "Luigi Capuana", "1879"),
    ("I Malavoglia", "Giovanni Verga", "1881"),
    ("Malombra", "Antonio Fogazzaro", "1881"),
    ("Pinocchio", "Carlo Collodi", "1883"),
    ("Il ventre di Napoli", "Matilde Serao", "1884"),
    ("La vergine delle rocce", "Gabriele D’Annunzio", "1885"),
    ("Cuore", "Edmondo De Amicis", "1886"),
    ("Il Piacere", "Gabriele D’Annunzio", "1889"),
    ("Mastro-don Gesualdo", "Giovanni Verga", "1889"),
]


async def test_the_books_of_the_project_brief_can_be_stored():
    script = "CREATE TABLE BOOKS (ID NUMBER(10), TITLE CHAR(100), AUTHOR CHAR(60), YEAR CHAR(20))\n" + "\n".join(
        f"INSERT INTO BOOKS VALUES ('{title}', '{author}', '{year}')" for title, author, year in BOOKS
    )
    async with connect() as session:
        written = await session.call_tool("tiny_sql_write", {"sql": script})
        assert not written.isError, written.content[0].text
        rows = data(await session.call_tool("tiny_sql_read", {"sql": "SELECT TITLE,AUTHOR,YEAR FROM BOOKS"}))
        assert [tuple(r) for r in rows["results"][0]["rows"]] == BOOKS
        found = data(await session.call_tool("tiny_sql_read", {"sql": "SELECT TITLE FROM BOOKS WHERE AUTHOR LIKE '%annunzio%'"}))
        assert found["results"][0]["rows"] == [["La vergine delle rocce"], ["Il Piacere"]]


async def test_the_read_tool_cannot_change_anything():
    async with connect() as session:
        await session.call_tool("tiny_sql_write", {"sql": "CREATE TABLE T (ID NUMBER(10), N CHAR(5))"})
        result = await session.call_tool("tiny_sql_read", {"sql": "SELECT * FROM T\nDELETE FROM T WHERE ID = 1"})
        assert result.isError and "tiny_sql_write" in result.content[0].text


async def test_a_failed_write_tells_the_model_what_to_fix():
    async with connect() as session:
        result = await session.call_tool("tiny_sql_write", {"sql": "CREATE TABLE T (ID NUMBER(10), N CHAR(2))\nINSERT INTO T VALUES ('too long')"})
        assert result.isError and "too long" in result.content[0].text and "Nothing was changed" in result.content[0].text
        described = data(await session.call_tool("tiny_sql_read", {"sql": "DESCRIBE"}))
        assert [r[0] for r in described["results"][0]["rows"]] == ["SYSTEM_TABLE"]


async def test_a_column_too_small_can_be_widened_with_the_tools():
    """The fresh-machine test: YEAR NUMBER(4) refuses '1840-1842' until the model widens it."""
    async with connect() as session:
        await session.call_tool("tiny_sql_write", {"sql": "CREATE TABLE BOOKS (ID NUMBER(10), TITLE CHAR(100), "
                                                          "AUTHOR CHAR(100), YEAR NUMBER(4))"})
        insert = "INSERT INTO BOOKS VALUES ('I Promessi Sposi', 'Alessandro Manzoni', '1840-1842')"
        refused = await session.call_tool("tiny_sql_write", {"sql": insert})
        assert refused.isError and "not a number (column YEAR)" in refused.content[0].text
        fixed = await session.call_tool("tiny_sql_write", {"sql": "ALTER TABLE BOOKS MODIFY YEAR CHAR(20)\n" + insert})
        assert not fixed.isError, fixed.content[0].text
        rows = data(await session.call_tool("tiny_sql_read", {"sql": "SELECT YEAR FROM BOOKS"}))
        assert rows["results"][0]["rows"] == [["1840-1842"]]


async def test_the_tools_describe_dates_and_alter():
    async with connect() as session:
        tools = {t.name: t.description for t in (await session.list_tools()).tools}
        assert "ALTER TABLE <table> MODIFY" in tools["tiny_sql_write"] and "TODAY" in tools["tiny_sql_write"]
        assert "WHERE DUE < TODAY" in tools["tiny_sql_read"]


async def test_local_time_follows_the_configured_zone(monkeypatch):
    monkeypatch.setattr(server.config, "TIMEZONE", "Asia/Tokyo")
    async with connect() as session:
        result = data(await session.call_tool("get_datetime", {}))
        assert result["timezone"] == "Asia/Tokyo"


def test_a_normal_disconnect_is_not_logged_as_an_error(caplog):
    """The library logs a closed stream at the end of a session as an error; real errors still show."""
    import logging

    import anyio

    library = logging.getLogger("mcp.server.streamable_http")
    with caplog.at_level(logging.ERROR, logger="mcp.server.streamable_http"):
        for error in (anyio.ClosedResourceError(), ValueError("a real problem")):
            try:
                raise error
            except Exception:
                library.exception("Error in standalone SSE writer")
        try:
            raise anyio.ClosedResourceError()
        except Exception:
            library.exception("Error in standalone SSE response")
    shown = [(r.getMessage(), r.exc_info[0].__name__) for r in caplog.records]
    assert shown == [("Error in standalone SSE writer", "ValueError"),
                     ("Error in standalone SSE response", "ClosedResourceError")]


def test_the_shutdown_message_is_hidden_only_while_stopping(caplog):
    import logging
    from types import SimpleNamespace

    from little_mcp.server import _QuietShutdown

    server = SimpleNamespace(should_exit=False)
    uvicorn_log = logging.getLogger("uvicorn.error.test")
    quiet = _QuietShutdown(server)
    uvicorn_log.addFilter(quiet)
    try:
        with caplog.at_level(logging.ERROR, logger="uvicorn.error.test"):
            uvicorn_log.error("ASGI callable returned without completing response.")  # running: a real problem
            server.should_exit = True
            uvicorn_log.error("ASGI callable returned without completing response.")  # stopping: expected
            uvicorn_log.error("Something else went wrong.")
    finally:
        uvicorn_log.removeFilter(quiet)
    assert [r.getMessage() for r in caplog.records] == [
        "ASGI callable returned without completing response.", "Something else went wrong."]
