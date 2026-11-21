"""Sharing the tools from inside the app: a real HTTP server on a free port, real connections."""

import socket

import httpx
import pytest

from little_mcp import settings, sharing


@pytest.fixture
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def shared(free_port):
    share = sharing.Sharing()
    assert share.start(free_port, "s3cret") == ""
    yield share, f"http://127.0.0.1:{free_port}/mcp"
    share.stop()


async def test_the_right_password_gets_the_tools(shared):
    _, url = shared
    ok, text = await settings.check_server(url, "s3cret")
    assert ok and "10 tools" in text


async def test_a_wrong_password_is_refused_in_plain_words(shared):
    _, url = shared
    ok, text = await settings.check_server(url, "wrong")
    assert not ok and "refused the password" in text


def test_another_computer_is_not_turned_away_by_its_address(shared, free_port):
    # Another computer writes this one's IP in the Host header, not "localhost": that must not be refused.
    response = httpx.post(f"http://127.0.0.1:{free_port}/mcp", headers={
        "Host": f"192.168.1.20:{free_port}", "Authorization": "Bearer s3cret",
        "Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    assert response.status_code == 200


async def test_sharing_can_stop_and_start_again(shared, free_port):
    share, url = shared
    share.stop()
    assert not share.running
    assert (await settings.check_server(url, "s3cret"))[0] is False  # nothing answers now
    assert share.start(free_port, "other") == ""  # the MCP library allows one start per session manager
    assert (await settings.check_server(url, "other"))[0]


def test_a_busy_port_is_explained(shared, free_port):
    second = sharing.Sharing()
    assert "already used" in second.start(free_port, "x")
    assert not second.running


async def test_nothing_answering_is_explained():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # free, and nobody listens once the socket is closed
    ok, text = await settings.check_server(f"http://127.0.0.1:{port}/mcp", "")
    assert not ok and "Nothing answered" in text


@pytest.mark.parametrize("typed, url", [
    ("192.168.1.20", "http://192.168.1.20:8000/mcp"),
    ("ubuntu-TEST.local", "http://ubuntu-TEST.local:8000/mcp"),
    ("  192.168.1.20:9000 ", "http://192.168.1.20:9000/mcp"),
    ("http://192.168.1.20:8000/mcp", "http://192.168.1.20:8000/mcp"),
    ("", ""),
])
def test_what_the_user_types_becomes_a_full_address(typed, url):
    assert settings.server_url(typed) == url


def test_this_computer_can_be_found_by_name():
    assert settings and sharing.this_computer_addresses()[-1].endswith(".local")


def test_the_network_mode_defaults_to_this_computer_only(monkeypatch):
    monkeypatch.delenv("LITTLE_MCP_NETWORK", raising=False)
    assert settings.network_mode() == "local"
    monkeypatch.setenv("LITTLE_MCP_NETWORK", "share")
    assert settings.network_mode() == "share"
    monkeypatch.setenv("LITTLE_MCP_NETWORK", "cloud")
    assert settings.network_mode() == "local"
