"""Chat history: every conversation is kept in its own file.

One file per chat, in data/chats/, with one JSON object per line (JSON Lines).
A new line is added as each thing happens, so a crash loses nothing, and the
files are plain text you can read, search or delete by hand.

myTinySQL is not used here on purpose: its rows have a fixed width and cannot
hold line breaks, and a chat message is long text with line breaks.
"""

import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import config

# Reasoning is not saved: it can be long, and it is only shown with --think.
SAVED_KINDS = ("user", "tool_call", "tool_result", "answer", "error")

TITLE_LENGTH = 60
_VALID_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass
class Chat:
    """One saved conversation, as listed in the web UI."""

    id: str
    title: str
    updated: datetime

    @property
    def label(self) -> str:
        return f"{self.updated:%d %b %H:%M} · {self.title}"


class ChatHistory:
    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory or config.CHATS_DIR)

    def new_id(self) -> str:
        return f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{secrets.token_hex(2)}"

    def _path(self, chat_id: str) -> Path:
        # The id comes from the browser and ends up in a file name.
        if not _VALID_ID.fullmatch(chat_id):
            raise ValueError(f"Invalid chat id: {chat_id!r}")
        return self.directory / f"{chat_id}.jsonl"

    def save(self, chat_id: str, event) -> None:
        """Add one event (see agent.Event) to a chat."""
        if event.kind not in SAVED_KINDS:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)  # chats are private
        record = {"time": datetime.now().isoformat(timespec="seconds"), **asdict(event)}
        with self._path(chat_id).open("a", encoding="utf-8", errors="replace") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load(self, chat_id: str) -> list[dict]:
        """Everything saved for one chat, oldest first; an empty list if there is no such chat."""
        try:
            lines = self._path(chat_id).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:  # a line cut short by a crash
                continue
        return records

    def chats(self) -> list[Chat]:
        """Every saved chat, most recent first."""
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.glob("*.jsonl"):
            first_message = next((r["text"] for r in self.load(path.stem) if r.get("kind") == "user"), "")
            title = " ".join(first_message.split())
            if len(title) > TITLE_LENGTH:
                title = title[:TITLE_LENGTH] + "…"
            found.append(Chat(path.stem, title or "(empty)", datetime.fromtimestamp(path.stat().st_mtime)))
        return sorted(found, key=lambda chat: chat.updated, reverse=True)
