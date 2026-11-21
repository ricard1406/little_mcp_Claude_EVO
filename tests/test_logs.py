"""The log: a quiet console, a detailed file in columns, rotated at start."""

import io
import logging
import re

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from little_mcp import logs, server

LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d  (INFO |WARN |ERROR|DEBUG)  (\S+)\s+ (.*)$")


@pytest.fixture
def logged(tmp_path):
    """logs.setup() into a temporary file and a fake console; the test's own logging comes back afterwards."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    console = io.StringIO()

    def start(verbose=False):
        logs.setup(verbose=verbose, path=tmp_path / "little_mcp.log", console_stream=console)
        return tmp_path / "little_mcp.log", console

    yield start
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved[0]:
        root.addHandler(handler)
    root.setLevel(saved[1])


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_one_line_per_event_in_columns(logged):
    path, _ = logged()
    logging.getLogger("little_mcp.tools").info("find_item by claude-code from 192.168.1.20, 3 ms: {}")
    logging.getLogger("little_mcp.share").warning("refused a wrong or missing password, from 192.168.1.20")
    first, second = (LINE.match(line) for line in lines(path))
    assert first.groups() == ("INFO ", "tools", "find_item by claude-code from 192.168.1.20, 3 ms: {}")
    assert second.group(1, 2) == ("WARN ", "share")


def test_the_console_stays_quiet(logged):
    path, console = logged()
    logging.getLogger("little_mcp.app").info("page ready")                       # routine: file only
    logging.getLogger("little_mcp.chat").error("chat x: the answer failed")      # the page shows it: file only
    logging.getLogger("little_mcp.tools").warning("find_item FAILED")            # often routine: file only
    logging.getLogger("uvicorn.access").info('"POST /mcp HTTP/1.1" 200')         # technical: nowhere
    logging.getLogger("little_mcp.share").warning("sharing: port already used")  # a real problem: shown
    assert console.getvalue() == "sharing: port already used\n"
    assert len(lines(path)) == 4  # all but the HTTP request


def test_verbose_shows_everything(logged):
    path, console = logged(verbose=True)
    logging.getLogger("uvicorn.access").info('"POST /mcp HTTP/1.1" 200')
    logging.getLogger("little_mcp.chat").error("chat x: the answer failed")
    assert "POST /mcp" in console.getvalue() and "the answer failed" in console.getvalue()
    assert any("http" == LINE.match(line).group(2) for line in lines(path))


def test_rotation_keeps_three_old_files(tmp_path):
    path = tmp_path / "little_mcp.log"
    for generation in range(5):
        path.write_text(f"generation {generation}\n" * 10)
        logs.rotate(path, max_bytes=50, keep=3)
    assert not path.exists()  # the app starts a fresh one
    olds = {n: (tmp_path / f"little_mcp.log.{n}").read_text().split()[1] for n in (1, 2, 3)}
    assert olds == {1: "4", 2: "3", 3: "2"}  # newest first; the two oldest are gone
    assert not (tmp_path / "little_mcp.log.4").exists()


def test_a_small_log_is_not_rotated(tmp_path):
    path = tmp_path / "little_mcp.log"
    path.write_text("short\n")
    logs.rotate(path, max_bytes=1000)
    assert path.read_text() == "short\n"


@pytest.fixture
def isolated(monkeypatch, tmp_path, inventory):
    monkeypatch.setattr(server, "_inventory", inventory)


async def test_every_tool_call_is_logged_with_its_caller(logged, isolated):
    path, _ = logged()
    async with create_connected_server_and_client_session(server.mcp) as session:
        await session.call_tool("find_item", {"item": "apples"})
        await session.call_tool("calculate", {"expression": "2 +"})  # a failing call
    found = [LINE.match(line).groups() for line in lines(path) if LINE.match(line).group(2) == "tools"]
    assert found[0][0] == "INFO " and found[0][2].startswith("find_item by mcp on this computer, ")
    assert found[0][2].endswith('ms: {"item": "apples"}')
    assert found[1][0] == "WARN " and "calculate" in found[1][2] and "FAILED" in found[1][2]


def test_a_known_error_takes_one_line_an_unknown_one_its_traceback(logged):
    import anthropic
    import httpx

    from little_mcp.cli import log_failed_answer

    path, console = logged()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    refused = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=request),
                                            body=None)
    log_failed_answer("chat-1", refused)
    try:
        raise ValueError("something nobody expected")
    except ValueError as unexpected:
        log_failed_answer("chat-2", unexpected)
    text = path.read_text()
    known, unknown = text.split("\n", 1)
    assert "refused the API key" in known and "AuthenticationError: invalid x-api-key" in known
    assert "Traceback" not in known.split("chat-2")[0]
    assert "chat-2" in unknown and "Traceback (most recent call last)" in unknown
    assert console.getvalue() == ""  # the page shows both: the console stays quiet


def test_the_anthropic_library_requests_stay_out(logged):
    path, _ = logged()
    logging.getLogger("httpx2").info('HTTP Request: POST https://api.anthropic.com/v1/messages "200 OK"')
    assert path.read_text() == ""
