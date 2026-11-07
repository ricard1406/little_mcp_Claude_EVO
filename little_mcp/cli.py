"""Command-line entry point: chat in the terminal or in a Gradio web page.

    python -m little_mcp                       # terminal chat, local Ollama
    python -m little_mcp graph                 # web UI on http://127.0.0.1:7860
    python -m little_mcp --provider anthropic  # use Claude
    python -m little_mcp --think               # show reasoning and tool calls
"""

import argparse
import asyncio
import json
import sys
import uuid

from . import __version__
from .agent import DEFAULT_CLAUDE_MODEL, DEFAULT_OLLAMA_MODEL, LittleAgent, build_llm

EXAMPLES = [
    "What time is it in Tokyo, and what's the weather there?",
    "Is Dianne in our list of candidates?",
    "Do we have oranges in the warehouse?",
    "We just received 4 boxes of 6 apples each. Update the stock.",
    "What is (1250 - 380) * 1.22?",
    "Find Dianne Bridgewater in our candidates. If she's there, write a short invitation "
    "for an interview at our main office: in two days if the weather in her city is good, "
    "otherwise next Monday.",
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="little_mcp", description="Little MCP agent",
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("mode", nargs="?", choices=["text", "graph"], default="text",
                   help="text: chat in the terminal (default)\ngraph: Gradio web UI")
    p.add_argument("--provider", choices=["ollama", "anthropic"], default="ollama",
                   help="ollama: local model (default)\nanthropic: Claude (needs ANTHROPIC_API_KEY in .env)")
    p.add_argument("--model", help=f"model name (defaults: {DEFAULT_OLLAMA_MODEL} / {DEFAULT_CLAUDE_MODEL})")
    p.add_argument("--think", action="store_true", help="show the model's reasoning and every tool call")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="medium",
                   help="Claude only: how much effort to spend per answer (default: medium)")
    p.add_argument("--server-url", help="connect to an MCP server already running over HTTP,\n"
                                        "e.g. http://127.0.0.1:8000/mcp (default: start one automatically)")
    p.add_argument("--allow-writes", action="store_true",
                   help="change the inventory without asking first\n"
                        "(in graph mode the inventory is read-only without this flag)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _short(value, limit=300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


# --- Terminal chat --------------------------------------------------------------


async def _ask_in_terminal(tool: str, args: dict) -> bool:
    answer = await asyncio.to_thread(input, f"\n  ⚠️  Allow {tool}({_short(args)})? [y/N] ")
    return answer.strip().lower() in ("y", "yes", "s", "si", "sì")


async def _always_yes(tool: str, args: dict) -> bool:
    return True


async def run_text(args, llm) -> None:
    confirm = _always_yes if args.allow_writes else _ask_in_terminal
    async with LittleAgent(llm, args.server_url, confirm, trim_history=args.provider == "ollama") as agent:
        _banner(args, agent)
        print("Try:\n" + "\n".join(f"  - {e}" for e in EXAMPLES[:4]))
        print("Type 'quit' to exit.\n")
        while True:
            try:
                question = (await asyncio.to_thread(input, "You: ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in ("quit", "exit", "bye"):
                break
            if not question:
                continue
            try:
                async for event in agent.stream(question):
                    _print_event(event, args.think)
            except Exception as e:
                print(f"\n❌ {type(e).__name__}: {e}\n")
        print("Goodbye!")


def _print_event(event, think: bool) -> None:
    if event.kind == "answer":
        print(f"\nAssistant: {event.text}\n" + "-" * 60)
    elif not think:
        return
    elif event.kind == "reasoning":
        print(f"\n  💭 {_short(event.text, 600)}")
    elif event.kind == "tool_call":
        print(f"\n  🛠️  {event.tool}({_short(event.args)})")
    elif event.kind == "tool_result":
        mark = "❌" if event.is_error else "↳"
        print(f"  {mark} {event.tool}: {_short(event.text)}")


def _banner(args, agent) -> None:
    model = args.model or (DEFAULT_CLAUDE_MODEL if args.provider == "anthropic" else DEFAULT_OLLAMA_MODEL)
    print("=" * 60)
    print(f"  Little MCP EVO v{__version__}")
    print(f"  Provider : {args.provider}   Model: {model}")
    print(f"  Server   : {args.server_url or 'started automatically (stdio)'}")
    print(f"  Tools    : {', '.join(t.name for t in agent.tools)}")
    print("=" * 60)


# --- Web UI -------------------------------------------------------------------------


def add_to_chat(shown: list[dict], boxes: dict[str, int], event, think: bool) -> None:
    """Add one agent event to the Gradio chat messages.

    Each tool call gets a collapsible box. `boxes` maps a call id to its box,
    so when several tools run at once, each result lands under its own call.
    """
    if event.kind == "answer":
        shown.append({"role": "assistant", "content": event.text})
    elif event.kind == "tool_call":
        boxes[event.call_id] = len(shown)
        shown.append({"role": "assistant", "content": f"`{_short(event.args)}`",
                      "metadata": {"title": f"🛠️ {event.tool}"}})
    elif event.kind == "tool_result":
        i = boxes.get(event.call_id, len(shown) - 1)
        if i >= 0:
            shown[i]["content"] += f"\n\n{'❌' if event.is_error else '↳'} {_short(event.text, 800)}"
    elif event.kind == "reasoning" and think:
        shown.append({"role": "assistant", "content": event.text, "metadata": {"title": "💭 Reasoning"}})


def run_graph(args, llm) -> None:
    try:
        import gradio as gr
    except ImportError:
        sys.exit("The web UI is not installed: pip install -e '.[ui]'")

    async def no_writes(tool: str, args_: dict) -> bool:
        return False

    confirm = _always_yes if args.allow_writes else no_writes
    agent_holder: dict = {}
    lock = asyncio.Lock()

    async def get_agent() -> LittleAgent:
        # Created inside Gradio's event loop, which is where the MCP session must live.
        async with lock:
            if "agent" not in agent_holder:
                agent = LittleAgent(
                    llm, args.server_url, confirm, trim_history=args.provider == "ollama",
                    declined_message="Changing stock is disabled in this web session (started without "
                                     "--allow-writes). Tell the user; do not retry.",
                )
                agent_holder["agent"] = await agent.__aenter__()
        return agent_holder["agent"]

    async def respond(message: str, history, request: gr.Request):
        agent = await get_agent()
        shown: list[dict] = []
        boxes: dict[str, int] = {}
        try:
            # One conversation per browser tab.
            async for event in agent.stream(message, thread_id=request.session_hash or "web"):
                add_to_chat(shown, boxes, event, args.think)
                yield shown
        except Exception as e:
            shown.append({"role": "assistant", "content": f"❌ {type(e).__name__}: {e}"})
            yield shown

    writes = "allowed" if args.allow_writes else "read-only (start with --allow-writes to let the agent change stock)"
    demo = gr.ChatInterface(
        fn=respond,
        title="Little MCP EVO",
        description=f"Weather, time, arithmetic, your documents and the warehouse. Inventory: {writes}.",
        examples=EXAMPLES,
    )
    demo.launch(share=False)


def main(argv=None) -> None:
    args = parse_args(argv)
    try:
        llm = build_llm(args.provider, args.model, args.think, args.effort)
    except RuntimeError as e:
        sys.exit(f"❌ {e}")
    if args.mode == "graph":
        run_graph(args, llm)
    else:
        asyncio.run(run_text(args, llm))


if __name__ == "__main__":
    main()
