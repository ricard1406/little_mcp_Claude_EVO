"""Command-line entry point: chat in a web page (the default) or in the terminal.

    python -m little_mcp                       # web UI on http://127.0.0.1:7860, local Ollama
    python -m little_mcp text                  # chat in the terminal
    python -m little_mcp --provider anthropic  # use Claude
    python -m little_mcp --think               # show reasoning and tool calls
"""

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict

from . import __version__
from .agent import DEFAULT_CLAUDE_MODEL, DEFAULT_OLLAMA_MODEL, Event, LittleAgent, build_llm
from .history import ChatHistory
from .tinysql.database import only_inserts

EXAMPLES = [
    "What time is it in Tokyo, and what's the weather there?",
    "Is Dianne in our list of candidates?",
    "Do we have oranges in the warehouse?",
    "We just received 4 boxes of 6 apples each. Update the stock.",
    "What is (1250 - 380) * 1.22?",
    "Create a table of books and store these: Pinocchio, Carlo Collodi 1883; Cuore, Edmondo De Amicis 1886",
    "Find Dianne Bridgewater in our candidates. If she's there, write a short invitation "
    "for an interview at our main office: in two days if the weather in her city is good, "
    "otherwise next Monday.",
]

CONFIRM_TIMEOUT = 600  # seconds: with nobody at the page, a change is declined

# When to ask before a tool changes data: the --ask option, and the choice in the web page.
ASK_CHOICES = {
    "always": "Always",
    "except-inserts": "Not for new rows",
    "never": "Never",
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="little_mcp", description="Little MCP agent",
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("mode", nargs="?", choices=["graph", "text"], default="graph",
                   help="graph: web UI (default)\ntext: chat in the terminal")
    p.add_argument("--provider", choices=["ollama", "anthropic"], default="ollama",
                   help="ollama: local model (default)\nanthropic: Claude (needs ANTHROPIC_API_KEY in .env)")
    p.add_argument("--model", help=f"model name (defaults: {DEFAULT_OLLAMA_MODEL} / {DEFAULT_CLAUDE_MODEL})")
    p.add_argument("--think", action="store_true", help="show the model's reasoning and every tool call")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="medium",
                   help="Claude only: how much effort to spend per answer (default: medium)")
    p.add_argument("--server-url", help="connect to an MCP server already running over HTTP,\n"
                                        "e.g. http://127.0.0.1:8000/mcp (default: start one automatically)")
    p.add_argument("--ask", choices=list(ASK_CHOICES), default="always",
                   help="when to ask before changing data:\n"
                        "always: before every change (default)\n"
                        "except-inserts: not for an INSERT, which only adds rows\n"
                        "never: change the data without asking\n"
                        "(the web UI starts with this choice, and you can change it there)")
    p.add_argument("--allow-writes", action="store_true", help="the same as --ask never")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)
    if args.allow_writes:
        args.ask = "never"
    return args


def _short(value, limit=300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


# --- Terminal chat --------------------------------------------------------------


def describe_change(args: dict) -> str:
    """The whole of what a tool is about to do, never shortened: this is what the user approves."""
    if set(args) == {"sql"}:
        return args["sql"]
    return json.dumps(args, ensure_ascii=False, indent=2)


def needs_question(ask: str, tool: str, args: dict) -> bool:
    """Tells whether a change must wait for the user's OK, given the "ask" setting.

    With "except-inserts", only a tiny_sql_write script made of INSERTs alone runs
    without a question. Creating a table still asks, because a table cannot be
    dropped yet; so do UPDATE, DELETE and the stock tools, which change existing data.
    """
    if ask == "never":
        return False
    if ask == "except-inserts" and tool == "tiny_sql_write":
        return not only_inserts(str(args.get("sql", "")))
    return True


async def _ask_in_terminal(tool: str, args: dict, thread_id: str) -> bool:
    answer = await asyncio.to_thread(input, f"\n  ⚠️  Allow {tool}?\n{describe_change(args)}\n[y/N] ")
    return answer.strip().lower() in ("y", "yes", "s", "si", "sì")


async def run_text(args, llm) -> None:
    async def confirm(tool: str, args_: dict, thread_id: str) -> bool:
        return not needs_question(args.ask, tool, args_) or await _ask_in_terminal(tool, args_, thread_id)

    history = ChatHistory()
    chat_id = history.new_id()
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
            history.save(chat_id, Event("user", question))
            try:
                async for event in agent.stream(question, chat_id):
                    history.save(chat_id, event)
                    _print_event(event, args.think)
            except Exception as e:
                history.save(chat_id, Event("error", f"{type(e).__name__}: {e}"))
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
    if event.kind == "user":
        shown.append({"role": "user", "content": event.text})
    elif event.kind == "answer":
        shown.append({"role": "assistant", "content": event.text})
    elif event.kind == "error":
        shown.append({"role": "assistant", "content": f"❌ {event.text}"})
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


def replay(records: list[dict]) -> list[dict]:
    """Turn a saved chat back into Gradio chat messages."""
    shown: list[dict] = []
    boxes: dict[str, int] = {}
    for record in records:
        add_to_chat(shown, boxes, Event(**{k: v for k, v in record.items() if k != "time"}), think=False)
    return shown


def run_graph(args, llm) -> None:
    import gradio as gr

    history = ChatHistory()
    agent_holder: dict = {}
    lock = asyncio.Lock()
    queues: dict[str, asyncio.Queue] = {}  # chat id -> what its running turn wants to show
    answers: dict[str, asyncio.Future] = {}  # chat id -> the Allow / Decline click it waits for
    turns: dict[str, asyncio.Task] = {}  # chat id -> its running turn, for the Stop button
    asking: dict[str, str] = {}  # chat id -> the "Ask before changes" choice when its turn started
    one_question: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # for tools running in parallel

    async def ask_in_browser(tool: str, args_: dict, chat_id: str) -> bool:
        """Show the Allow / Decline panel, and wait for a click. The agent is paused meanwhile."""
        if not needs_question(asking.get(chat_id, args.ask), tool, args_):
            return True
        async with one_question[chat_id]:
            answers[chat_id] = asyncio.get_running_loop().create_future()
            await queues[chat_id].put(Event("confirm", tool=tool, args=args_))
            try:
                return await asyncio.wait_for(answers[chat_id], CONFIRM_TIMEOUT)
            except asyncio.TimeoutError:
                return False
            finally:
                del answers[chat_id]

    async def get_agent() -> LittleAgent:
        # Created inside Gradio's event loop, which is where the MCP session must live.
        async with lock:
            if "agent" not in agent_holder:
                agent = LittleAgent(llm, args.server_url, ask_in_browser, trim_history=args.provider == "ollama")
                agent_holder["agent"] = await agent.__aenter__()
        return agent_holder["agent"]

    def past_chats(selected: str | None = None):
        return gr.update(choices=[(c.label, c.id) for c in history.chats()], value=selected)

    with gr.Blocks(title="Little MCP EVO") as demo:
        chat_id = gr.State("")  # the conversation this browser tab is looking at

        gr.Markdown("## Little MCP EVO\nWeather, time, arithmetic, your documents, the warehouse and your own tables.")

        # A row, not a sidebar: on a narrow window a row wraps, while a sidebar would cover the chat.
        with gr.Row():
            with gr.Column(scale=1, min_width=220):
                new_chat = gr.Button("➕ New chat", variant="primary")
                past = gr.Radio(label="Chat history", choices=[], interactive=True, elem_id="chat-history")
                ask_when = gr.Radio(label="Ask before changes", value=args.ask, interactive=True,
                                    choices=[(label, value) for value, label in ASK_CHOICES.items()],
                                    info="\"Not for new rows\" lets an INSERT run without the question.")

            with gr.Column(scale=4, min_width=420):
                chatbot = gr.Chatbot(height="55vh", label="Conversation", show_label=False)

                with gr.Group(visible=False) as confirm_panel:
                    change = gr.Textbox(label="", interactive=False, lines=2, max_lines=25)
                    with gr.Row():
                        allow = gr.Button("✅ Allow", variant="primary")
                        decline = gr.Button("🚫 Decline", variant="stop")

                status = gr.Markdown("")

                with gr.Row():
                    message = gr.Textbox(placeholder="Write a message...", show_label=False, scale=8, lines=2,
                                         max_lines=12, submit_btn=False)
                    with gr.Column(scale=1, min_width=100):
                        send = gr.Button("Send", variant="primary")
                        stop = gr.Button("⏹ Stop", variant="stop", interactive=False)
                gr.Examples(examples=[[e] for e in EXAMPLES], inputs=[message], label="Try")

        def busy(is_busy: bool) -> dict:
            """While a turn runs, nothing may start a second one or switch to another chat."""
            enabled = gr.update(interactive=not is_busy)
            return {message: enabled, send: enabled, new_chat: enabled, past: enabled,
                    stop: gr.update(interactive=is_busy)}

        async def respond(text: str, current: str, ask_choice: str):
            text = text.strip()
            if not text:
                yield {message: gr.update()}
                return
            current = current or history.new_id()
            asking[current] = ask_choice
            earlier = history.load(current)
            shown = replay(earlier)
            boxes: dict[str, int] = {}
            first = Event("user", text)
            history.save(current, first)
            add_to_chat(shown, boxes, first, args.think)
            yield {**busy(True), message: gr.update(value="", interactive=False), chatbot: shown, chat_id: current,
                   past: past_chats(current) | {"interactive": False}}

            queue: asyncio.Queue = asyncio.Queue()
            queues[current] = queue
            finished_cleanly = False

            async def run_turn() -> None:
                # A task of its own, so the page can be updated while the agent works or waits for a click.
                nonlocal finished_cleanly
                try:
                    agent = await get_agent()
                    async for event in agent.stream(text, current, earlier):
                        queue.put_nowait(event)
                    finished_cleanly = True
                except asyncio.CancelledError:
                    queue.put_nowait(Event("error", "Stopped."))
                except Exception as e:
                    queue.put_nowait(Event("error", f"{type(e).__name__}: {e}"))
                finally:
                    queue.put_nowait(None)

            turns[current] = asyncio.create_task(run_turn())
            started = time.monotonic()
            waiting_for_click = False
            next_event = asyncio.ensure_future(queue.get())
            try:
                while True:
                    # Say something every second: a slow model must not look like a frozen page.
                    await asyncio.wait({next_event}, timeout=1)
                    if not next_event.done():
                        seconds = int(time.monotonic() - started)
                        yield {status: "⚠️ Waiting for your answer: **Allow** or **Decline**." if waiting_for_click
                               else f"⏳ Working... {seconds} s. A local model can take minutes on a small computer."}
                        continue
                    event = next_event.result()
                    if event is None:
                        break
                    next_event = asyncio.ensure_future(queue.get())
                    if event.kind == "confirm":
                        waiting_for_click = True
                        yield {confirm_panel: gr.update(visible=True),
                               change: gr.update(label=f"⚠️ Allow {event.tool}?", value=describe_change(event.args))}
                        continue
                    waiting_for_click = False
                    history.save(current, event)
                    add_to_chat(shown, boxes, event, args.think)
                    yield {chatbot: shown, confirm_panel: gr.update(visible=False)}
            finally:
                next_event.cancel()
                turns.pop(current).cancel()
                queues.pop(current, None)
            if not finished_cleanly and "agent" in agent_holder:
                await agent_holder["agent"].forget(current)
            yield {**busy(False), confirm_panel: gr.update(visible=False), status: "",
                   past: past_chats(current) | {"interactive": True}}

        async def click_allow(current: str):
            if current in answers and not answers[current].done():
                answers[current].set_result(True)
            return gr.update(visible=False)

        async def click_decline(current: str):
            if current in answers and not answers[current].done():
                answers[current].set_result(False)
            return gr.update(visible=False)

        async def click_stop(current: str):
            if current in turns:
                turns[current].cancel()

        def open_chat(selected: str):
            return selected, replay(history.load(selected))

        def start_new_chat():
            return "", [], past_chats()

        gr.on([message.submit, send.click], respond, [message, chat_id, ask_when],
              [message, send, stop, new_chat, past, chatbot, chat_id, confirm_panel, change, status],
              concurrency_limit=4)
        stop.click(click_stop, [chat_id], None, concurrency_limit=None)
        allow.click(click_allow, [chat_id], [confirm_panel], concurrency_limit=None)
        decline.click(click_decline, [chat_id], [confirm_panel], concurrency_limit=None)
        past.input(open_chat, [past], [chat_id, chatbot])
        new_chat.click(start_new_chat, None, [chat_id, chatbot, past])
        demo.load(past_chats, None, [past])

    demo.launch(share=False, css="#chat-history { max-height: 60vh; overflow-y: auto; }")


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
