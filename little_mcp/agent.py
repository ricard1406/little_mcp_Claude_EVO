"""The Little MCP agent: an LLM that uses the tools of an MCP server.

The agent does not know any tool in advance. It connects to the MCP server,
asks it for its tools (tools/list) and hands them to the LLM. Add a tool to
server.py and the agent picks it up on the next start.
"""

import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.checkpoint.memory import InMemorySaver
from mcp.types import CallToolResult, TextContent

from . import config

DEFAULT_OLLAMA_MODEL = "qwen3:4b"
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are Little MCP, a helpful assistant for a small business.

You have tools for the current date and time, the weather, exact arithmetic, \
searching the user's local documents, the warehouse inventory (tables FRUITS \
and VEGGIE), and a small SQL database called myTinySQL. Use a tool whenever the answer depends on live data, the user's \
documents or the inventory, and do arithmetic with the calculate tool instead of \
in your head. You do not know today's date unless you call get_datetime.

When a question is about the user's documents, base the answer only on what \
search_documents returns, and say so if the documents do not contain it.

Before changing stock, make sure you have the exact item name (use find_item).

When the user wants to keep a list or a table of their own (books, customers...), \
use myTinySQL: tiny_sql_read to look at it and tiny_sql_write to create tables and \
store rows. Store every item the user gave you, exactly as written, and send all \
the statements in a single tiny_sql_write call. Write dates as YYYY-MM-DD, and \
numbers with a point for decimals (1338.80); prices go in NUMBER columns. The user is asked to approve each \
change. If the user declines a change, do not try it again.

Answer in the user's language, briefly and clearly."""


# --- LLM ----------------------------------------------------------------------


def build_llm(provider: str, model: str | None = None, think: bool = False, effort: str = "medium"):
    """Return a LangChain chat model for "ollama" (local) or "anthropic" (Claude)."""
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise RuntimeError("Claude support is not installed: pip install -e '.[claude]'") from None
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY in your .env file to use Claude.")
        return ChatAnthropic(
            model=model or DEFAULT_CLAUDE_MODEL,
            max_tokens=16000,
            output_config={"effort": effort},
            # Claude always reasons adaptively; "summarized" makes the summary visible in --think mode.
            thinking={"type": "adaptive", "display": "summarized"} if think else None,
            # If a safety classifier declines a request, the API retries it on a suitable fallback model.
            betas=["server-side-fallback-2026-07-01"],
            model_kwargs={"fallbacks": "default"},
        )

    from langchain_ollama import ChatOllama

    # reasoning=True returns a thinking model's reasoning separately from the answer. Don't use
    # reasoning=False to save time: qwen3:4b reasons anyway, and the reasoning lands in the answer.
    # The tools take about 1800 tokens of the model's memory, and thinking takes thousands more:
    # num_ctx gives it room, and num_predict stops an answer that never ends.
    return ChatOllama(model=model or DEFAULT_OLLAMA_MODEL, temperature=0.1, reasoning=True if think else None,
                      num_ctx=8192, num_predict=6000)


# --- Middleware ---------------------------------------------------------------


class KeepRecentMessages(AgentMiddleware):
    """Send only the recent part of a long conversation to the model.

    Small local models have small context windows. The full history stays in
    memory; only what the model sees is shortened. The cut always starts at a
    user message, so a tool call is never separated from its result.
    """

    def __init__(self, max_messages: int = 30):
        super().__init__()
        self.max_messages = max_messages

    def _trim(self, messages: list) -> list:
        if len(messages) <= self.max_messages:
            return messages
        start = len(messages) - self.max_messages
        human = [i for i, m in enumerate(messages) if m.type == "human"]
        later = [i for i in human if i >= start]
        cut = later[0] if later else (human[-1] if human else 0)
        return messages[cut:]

    def wrap_model_call(self, request, handler):
        return handler(request.override(messages=self._trim(request.messages)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(messages=self._trim(request.messages)))


Confirm = Callable[[str, dict, str], Awaitable[bool]]
"""Asked before a tool changes data: (tool name, arguments, conversation id) -> allowed?"""


DECLINED = "The user declined this change. Do not retry it."

NO_ANSWER = ("The model ran out of room before it could answer. Try a shorter request, "
             "a bigger model, or Claude (--provider anthropic).")


def _thread_id(request) -> str:
    """Which conversation a tool call belongs to: the UI needs it to ask in the right place."""
    run_config = getattr(request.runtime, "config", None) or {}
    return run_config.get("configurable", {}).get("thread_id", "default")


def _confirm_writes(write_tools: set[str], confirm: Confirm, declined_message: str):
    """MCP tool interceptor: ask before running any tool that changes data."""

    async def interceptor(request, handler):
        if request.name in write_tools and not await confirm(request.name, request.args, _thread_id(request)):
            return CallToolResult(content=[TextContent(type="text", text=declined_message)], isError=True)
        return await handler(request)

    return interceptor


# --- Events the UI shows --------------------------------------------------------


@dataclass
class Event:
    kind: str  # "user" | "reasoning" | "tool_call" | "tool_result" | "answer" | "error" | "confirm"
    text: str = ""
    tool: str = ""
    args: dict | None = None
    is_error: bool = False
    call_id: str = ""  # links a tool_result to its tool_call (tools can run in parallel)


_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)


def _reasoning(msg: AIMessage) -> str:
    parts = [b.get("thinking", "") for b in msg.content if isinstance(b, dict) and b.get("type") == "thinking"] \
        if isinstance(msg.content, list) else []
    parts.append(msg.additional_kwargs.get("reasoning_content", ""))
    if isinstance(msg.content, str):
        parts += re.findall(r"<think>(.*?)</think>", msg.content, re.DOTALL)
    return "\n".join(p.strip() for p in parts if p and p.strip())


def _answer(msg: AIMessage) -> str:
    if msg.response_metadata.get("stop_reason") == "refusal":
        return "Claude declined to answer this request."
    return _THINK_TAGS.sub("", str(msg.text)).strip()


def _tool_text(msg: ToolMessage) -> str:
    if isinstance(msg.content, str):
        return msg.content
    return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in msg.content)


# Settings the server may need from the shell. Everything else (including the
# Anthropic key) stays with the agent; the server also reads .env itself.
_SERVER_ENV_PREFIXES = ("LITTLE_MCP_", "OPENWEATHER_", "DB_", "SQLITE_", "TINYSQL_", "EMBED_", "OLLAMA_", "MCP_",
                        "HTTP_TIMEOUT", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
                        "no_proxy")


def _server_env() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    env = get_default_environment()
    env.update({k: v for k, v in os.environ.items() if k.startswith(_SERVER_ENV_PREFIXES)})
    return env


def _remembered(records: list[dict]) -> list:
    """What the model is told about an old conversation: the questions and the answers.

    Tool calls are left out: they would take up the memory of a small model, and
    a provider refuses a tool call that is separated from its result.
    """
    messages = []
    for record in records:
        if record.get("kind") == "user":
            messages.append(HumanMessage(record["text"]))
        elif record.get("kind") == "answer" and record["text"]:
            messages.append(AIMessage(record["text"]))
    return messages


# --- Agent ----------------------------------------------------------------------


class LittleAgent:
    """Use as `async with LittleAgent(llm) as agent:`; it holds one MCP session open."""

    def __init__(self, llm, server_url: str | None = None, confirm: Confirm | None = None,
                 trim_history: bool = False, declined_message: str = DECLINED):
        self.llm = llm
        self.server_url = server_url
        self.confirm = confirm
        self.trim_history = trim_history
        self.declined_message = declined_message
        self.tools = []
        self._stack = AsyncExitStack()
        self._graph = None
        self._threads: set[str] = set()  # conversations already in the model's memory

    def _connection(self) -> dict:
        if self.server_url:
            return {"transport": "streamable_http", "url": self.server_url}
        # Start our own server as a child process and talk to it over stdin/stdout.
        return {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "little_mcp.server"],
            "cwd": str(config.PROJECT_ROOT),
            "env": _server_env(),
        }

    async def __aenter__(self):
        client = MultiServerMCPClient({"little": self._connection()})
        session = await self._stack.enter_async_context(client.session("little"))

        write_tools: set[str] = set()
        interceptors = [_confirm_writes(write_tools, self.confirm, self.declined_message)] if self.confirm else []
        self.tools = await load_mcp_tools(session, tool_interceptors=interceptors, server_name="little")
        # MCP tool annotations tell us which tools change data.
        write_tools.update(t.name for t in self.tools if not (t.metadata or {}).get("readOnlyHint", False))

        middleware = [ModelCallLimitMiddleware(run_limit=15, exit_behavior="end")]
        if self.trim_history:
            middleware.append(KeepRecentMessages())
        self._graph = create_agent(
            self.llm, self.tools, system_prompt=SYSTEM_PROMPT,
            middleware=middleware, checkpointer=InMemorySaver(),
        )
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def stream(self, message: str, thread_id: str = "default",
                     earlier: list[dict] | None = None) -> AsyncIterator[Event]:
        """Run one user turn and yield what happens: reasoning, tool calls, results, answer.

        `earlier` holds the saved records of a conversation from a previous run.
        The agent's memory does not survive a restart, so they are put back the
        first time this conversation is used.
        """
        config_ = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        messages = [HumanMessage(message)]
        if thread_id not in self._threads:
            self._threads.add(thread_id)
            messages[:0] = _remembered(earlier or [])
        async for update in self._graph.astream(
            {"messages": messages}, config_, stream_mode="updates"
        ):
            for node_output in update.values():
                for msg in (node_output or {}).get("messages", []):
                    if isinstance(msg, AIMessage):
                        if reasoning := _reasoning(msg):
                            yield Event("reasoning", reasoning)
                        for call in msg.tool_calls:
                            yield Event("tool_call", tool=call["name"], args=call["args"], call_id=call.get("id") or "")
                        if not msg.tool_calls:
                            yield Event("answer", _answer(msg) or NO_ANSWER)
                    elif isinstance(msg, ToolMessage):
                        yield Event("tool_result", _tool_text(msg), tool=msg.name or "",
                                    is_error=msg.status == "error", call_id=msg.tool_call_id)

    async def forget(self, thread_id: str) -> None:
        """Clear a conversation from the model's memory, after a turn that was stopped or failed.

        Such a turn can leave a tool call without its result, which a provider refuses. The
        next message rebuilds the conversation from the saved chat instead.
        """
        await self._graph.checkpointer.adelete_thread(thread_id)
        self._threads.discard(thread_id)

    async def chat(self, message: str, thread_id: str = "default") -> str:
        answer = ""
        async for event in self.stream(message, thread_id):
            if event.kind == "answer":
                answer = event.text
        return answer
