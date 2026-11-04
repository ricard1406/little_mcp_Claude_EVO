"""End-to-end: the agent starts the real MCP server over stdio and uses its tools.

A scripted fake LLM stands in for Ollama/Claude, so the test needs no model.
"""

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from little_mcp.agent import KeepRecentMessages, LittleAgent


class ScriptedModel(BaseChatModel):
    replies: list
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def call(name, args, id_="call_1"):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": id_, "type": "tool_call"}])


@pytest.fixture(autouse=True)
def isolated_server(monkeypatch, tmp_path):
    # The server runs as a child process; it inherits these settings.
    monkeypatch.setenv("LITTLE_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LITTLE_MCP_INDEX_DIR", str(tmp_path / "index"))


async def test_agent_discovers_tools_and_uses_them():
    llm = ScriptedModel(replies=[call("find_item", {"item": "orange"}), AIMessage("Yes, 3 oranges.")])
    async with LittleAgent(llm) as agent:
        assert "find_item" in {t.name for t in agent.tools}
        events = [e async for e in agent.stream("Do we have oranges?")]
    kinds = [e.kind for e in events]
    assert kinds == ["tool_call", "tool_result", "answer"]
    assert '"quantity": 3' in events[1].text
    assert events[-1].text == "Yes, 3 oranges."


async def test_writes_need_confirmation():
    asked = []

    async def decline(tool, args):
        asked.append(tool)
        return False

    llm = ScriptedModel(replies=[
        call("adjust_stock", {"table": "FRUITS", "item": "APPLES", "delta": 5}),
        AIMessage("OK, I left the stock unchanged."),
        call("find_item", {"item": "apples"}, "call_2"),
        AIMessage("2 apples."),
    ])
    async with LittleAgent(llm, confirm=decline) as agent:
        events = [e async for e in agent.stream("Add 5 apples")]
        assert asked == ["adjust_stock"]
        result = next(e for e in events if e.kind == "tool_result")
        assert result.is_error and "declined" in result.text
        # Read-only tools run without asking, and the stock really is unchanged.
        events = [e async for e in agent.stream("How many apples?")]
        assert asked == ["adjust_stock"]
        assert '"quantity": 2' in next(e for e in events if e.kind == "tool_result").text


async def test_conversation_memory_is_per_thread():
    llm = ScriptedModel(replies=[AIMessage("Hi Ada"), AIMessage("Hi Bob"), AIMessage("Your name is Ada")], seen=[])
    async with LittleAgent(llm) as agent:
        await agent.chat("I'm Ada", thread_id="a")
        await agent.chat("I'm Bob", thread_id="b")
        await agent.chat("What's my name?", thread_id="a")
    last_input = [m.content for m in llm.seen[-1] if m.type == "human"]
    assert last_input == ["I'm Ada", "What's my name?"]


def test_trimming_keeps_tool_calls_with_their_results():
    msgs = [HumanMessage("q1"), call("calculate", {"expression": "1+1"}),
            ToolMessage("2", tool_call_id="call_1"), AIMessage("2"), HumanMessage("q2"), AIMessage("a2")]
    trimmed = KeepRecentMessages(max_messages=4)._trim(msgs)
    assert [m.content for m in trimmed] == ["q2", "a2"]
    # A single turn longer than the limit is kept whole.
    assert KeepRecentMessages(max_messages=2)._trim(msgs[:4]) == msgs[:4]


def test_server_gets_only_the_settings_it_needs(monkeypatch):
    from little_mcp.agent import _server_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("PS1", "${debian_chroot:+($debian_chroot)}$ ")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "weather-key")
    env = _server_env()
    assert "ANTHROPIC_API_KEY" not in env and "PS1" not in env
    assert env["OPENWEATHER_API_KEY"] == "weather-key"
    assert "PATH" in env
