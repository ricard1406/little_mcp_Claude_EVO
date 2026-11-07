from little_mcp.agent import Event
from little_mcp.cli import add_to_chat


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
