"""Share this computer's tools with other computers, from inside the app.

This is what `MCP_HOST=0.0.0.0 python -m little_mcp.server --transport http`
does in a second terminal, run by the app itself in a background thread: one
command, one terminal. The chat of this computer keeps its own server, as before.
Both use the same data, and every change holds a file lock (see store.py).
"""

import asyncio
import errno
import logging
import socket
import threading
import time
from pathlib import Path

log = logging.getLogger("little_mcp.share")

EVERYWHERE = "0.0.0.0"  # listen on every network of this computer, not only on itself


def port_free(port: int) -> bool:
    """True if nothing else listens on the port, such as a Little MCP server in another terminal."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn does
        try:
            probe.bind((EVERYWHERE, port))
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                return False
            raise
    return True


def this_computer_addresses() -> list[str]:
    """The addresses another computer can use to reach this one: its IP, and its name on the home network.

    The IP is found by asking the system which address it would use to reach another network.
    A UDP "connect" only picks a route: it sends nothing.
    """
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))
            ip = probe.getsockname()[0]
            if not ip.startswith("127."):
                addresses.append(ip)
        except OSError:  # no network at all
            pass
    addresses.append(f"{socket.gethostname()}.local")
    return addresses


def firewall_on() -> bool:
    """True if Ubuntu's firewall (ufw) is enabled. Its settings file can be read without sudo."""
    try:
        return "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text()
    except OSError:
        return False


class Sharing:
    """The HTTP server in a background thread: start() and stop() as often as the settings change."""

    def __init__(self):
        self._server = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.password = ""
        self._failure = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, port: int, password: str) -> str:
        """Start sharing; return "" if it works, otherwise what went wrong, in plain words."""
        self.stop()
        if not port_free(port):
            return (f"Port {port} is already used by another program, maybe a Little MCP server started "
                    "in a terminal. Stop it there (Ctrl+C), then Save again.")
        from .server import http_server  # imported here: only a computer that shares needs it

        self._server = http_server(EVERYWHERE, port, password)
        self._failure = ""
        self._thread = threading.Thread(target=self._run, name="little-mcp-share", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            self.stop()
            return self._failure or "The server did not start in 10 seconds."
        self.port, self.password = port, password
        log.info("sharing on: port %s, password %s", port, "on" if password else "OFF")
        return ""

    def _run(self) -> None:
        # uvicorn listens to Ctrl+C only in the main thread, so here it leaves Ctrl+C to the app.
        try:
            asyncio.run(self._server.serve())
        except BaseException as error:  # uvicorn ends with SystemExit when it cannot start
            self._failure = f"The server stopped: {type(error).__name__} {error}".strip()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)
            log.info("sharing off")
        self._server = self._thread = None
        self.port, self.password = 0, ""
