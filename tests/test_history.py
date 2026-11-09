import pytest

from little_mcp.agent import Event
from little_mcp.history import ChatHistory


@pytest.fixture
def history(tmp_path):
    return ChatHistory(tmp_path / "chats")


def test_a_chat_is_saved_in_order_and_survives_a_new_object(history):
    chat = history.new_id()
    history.save(chat, Event("user", "Quanti libri di D’Annunzio?"))
    history.save(chat, Event("tool_call", tool="tiny_sql_read", args={"sql": "SELECT * FROM BOOKS"}, call_id="1"))
    history.save(chat, Event("answer", "Due."))
    records = ChatHistory(history.directory).load(chat)
    assert [r["kind"] for r in records] == ["user", "tool_call", "answer"]
    assert records[0]["text"] == "Quanti libri di D’Annunzio?"
    assert records[1]["args"] == {"sql": "SELECT * FROM BOOKS"}
    assert records[0]["time"]


def test_reasoning_is_not_saved(history):
    chat = history.new_id()
    history.save(chat, Event("reasoning", "hmm"))
    assert history.load(chat) == []


def test_the_list_has_titles_and_the_latest_chat_first(history):
    import os

    old, new = "old-chat", "new-chat"
    history.save(old, Event("user", "first question"))
    history.save(new, Event("user", "x" * 100 + "\nsecond line"))
    os.utime(history.directory / "old-chat.jsonl", (1, 1))
    chats = history.chats()
    assert [c.id for c in chats] == [new, old]
    assert chats[1].title == "first question"
    assert len(chats[0].title) == 61 and chats[0].title.endswith("…")


def test_no_chats_yet(history):
    assert history.chats() == []
    assert history.load("nothing") == []


def test_a_line_cut_by_a_crash_is_skipped(history):
    history.save("c", Event("user", "kept"))
    with (history.directory / "c.jsonl").open("a") as file:
        file.write('{"kind": "answ')
    assert [r["text"] for r in history.load("c")] == ["kept"]


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "", "a.b"])
def test_a_chat_id_cannot_leave_the_folder(history, bad):
    with pytest.raises(ValueError):
        history.load(bad)
