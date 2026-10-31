"""Talk to the MCP server over the real protocol, in memory."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from little_mcp import server


@pytest.fixture(autouse=True)
def isolated(monkeypatch, inventory, doc_index):
    monkeypatch.setattr(server, "_inventory", inventory)
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
                              "list_inventory", "find_item", "adjust_stock", "set_stock"}
        writes = {name for name, t in tools.items() if not t.annotations.readOnlyHint}
        assert writes == {"adjust_stock", "set_stock"}
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
