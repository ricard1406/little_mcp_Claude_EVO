from little_mcp.agent import Event
from little_mcp.cli import add_to_chat, describe_change, parse_args, replay


def test_parallel_results_land_under_their_own_tool_box():
    shown, boxes = [], {}
    for event in [
        Event("tool_call", tool="find_item", args={"item": "apple"}, call_id="a"),
        Event("tool_call", tool="calculate", args={"expression": "4 * 6"}, call_id="b"),
        Event("tool_result", '{"item": "APPLES", "quantity": 26}', tool="find_item", call_id="a"),
        Event("tool_result", "24", tool="calculate", call_id="b"),
        Event("answer", "Done."),
    ]:
        add_to_chat(shown, boxes, event, think=False)
    assert [m.get("metadata", {}).get("title") for m in shown] == ["🛠️ find_item", "🛠️ calculate", None]
    assert "APPLES" in shown[0]["content"] and "24" not in shown[0]["content"]
    assert shown[1]["content"].endswith("↳ 24")


def test_reasoning_is_shown_only_in_think_mode():
    shown = []
    add_to_chat(shown, {}, Event("reasoning", "hmm"), think=False)
    assert shown == []
    add_to_chat(shown, {}, Event("reasoning", "hmm"), think=True)
    assert shown[0]["metadata"]["title"] == "💭 Reasoning"


def test_the_web_ui_is_the_default():
    assert parse_args([]).mode == "graph"
    assert parse_args(["text"]).mode == "text"


def test_a_saved_chat_comes_back_with_its_tool_boxes():
    shown = replay([
        {"time": "t", "kind": "user", "text": "Oranges?", "tool": "", "args": None, "is_error": False, "call_id": ""},
        {"time": "t", "kind": "tool_call", "text": "", "tool": "find_item", "args": {"item": "orange"}, "is_error": False, "call_id": "a"},
        {"time": "t", "kind": "tool_result", "text": "3", "tool": "find_item", "args": None, "is_error": False, "call_id": "a"},
        {"time": "t", "kind": "answer", "text": "Yes, 3.", "tool": "", "args": None, "is_error": False, "call_id": ""},
    ])
    assert [m["role"] for m in shown] == ["user", "assistant", "assistant"]
    assert shown[1]["metadata"]["title"] == "🛠️ find_item" and shown[1]["content"].endswith("↳ 3")


def test_the_user_approves_the_whole_change_never_a_shortened_one():
    script = "\n".join(f"INSERT INTO T VALUES ('{i}')" for i in range(500))
    assert describe_change({"sql": script}) == script
    assert '"item": "APPLES"' in describe_change({"table": "FRUITS", "item": "APPLES", "delta": 5})
