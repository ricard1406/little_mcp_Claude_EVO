"""Command-line entry point: chat in a web page (the default) or in the terminal.

    python -m little_mcp                       # web UI on http://127.0.0.1:7860, local Ollama
    python -m little_mcp text                  # chat in the terminal
    python -m little_mcp --provider anthropic  # use Claude
    python -m little_mcp --think               # show reasoning and tool calls
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict

from . import config, logs, settings
from .version import describe as version
from .agent import DEFAULT_CLAUDE_MODEL, DEFAULT_OLLAMA_MODEL, Event, LittleAgent, build_llm
from .history import ChatHistory, usage_totals
from .sharing import Sharing, firewall_on, this_computer_addresses
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

app_log = logging.getLogger("little_mcp.app")
chat_log = logging.getLogger("little_mcp.chat")
settings_log = logging.getLogger("little_mcp.settings")

CONFIRM_TIMEOUT = 600  # seconds: with nobody at the page, a change is declined

# When to ask before a tool changes data: the --ask option, and the choice in the web page.
ASK_CHOICES = {
    "always": "Always",
    "except-inserts": "Not for new rows",
    "never": "Never",
}


def _from_env(name: str, choices, default: str) -> str:
    """A default saved by the ⚙️ Settings tab in .env, if it is a valid choice."""
    value = os.getenv(name, "").strip().lower()
    return value if value in choices else default


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="little_mcp", description="Little MCP agent",
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("mode", nargs="?", choices=["graph", "text"], default="graph",
                   help="graph: web UI (default)\ntext: chat in the terminal")
    p.add_argument("--provider", choices=["ollama", "anthropic"],
                   help="ollama: local model\nanthropic: Claude (needs ANTHROPIC_API_KEY in .env)\n"
                        "(default: the choice saved in the ⚙️ Settings tab; with none, Claude if\n"
                        "there is a key, the local model if it is downloaded, otherwise Claude)")
    p.add_argument("--model", help=f"model name (defaults: {DEFAULT_OLLAMA_MODEL} / {DEFAULT_CLAUDE_MODEL})")
    p.add_argument("--think", action="store_true", help="show the model's reasoning and every tool call")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="medium",
                   help="Claude only: how much effort to spend per answer (default: medium)")
    p.add_argument("--server-url", help="connect to an MCP server already running over HTTP,\n"
                                        "e.g. http://127.0.0.1:8000/mcp (default: start one automatically)")
    p.add_argument("--ask", choices=list(ASK_CHOICES), default=_from_env(settings.ASK, ASK_CHOICES, "always"),
                   help="when to ask before changing data:\n"
                        "always: before every change (default)\n"
                        "except-inserts: not for an INSERT, which only adds rows\n"
                        "never: change the data without asking\n"
                        "(default: the choice saved in the ⚙️ Settings tab, otherwise always)")
    p.add_argument("--allow-writes", action="store_true", help="the same as --ask never")
    p.add_argument("--verbose", action="store_true",
                   help="also write the technical details, such as every HTTP request,\n"
                        f"to the console and to the log ({logs.LOG_FILE.relative_to(config.PROJECT_ROOT)})")
    p.add_argument("--open-browser", action="store_true",
                   help="open the page in the browser when it is ready (the desktop icon uses it)")
    p.add_argument("--version", action="version", version=f"Little MCP {version()}")
    args = p.parse_args(argv)
    if args.allow_writes:
        args.ask = "never"
    return args


def _short(value, limit=300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def plain_words(error: Exception) -> str | None:
    """Plain words for the problems a user can fix, or None for an error the app doesn't know."""
    try:
        import anthropic
    except ImportError:  # Claude support not installed: nothing of Anthropic's can fail
        return None
    if isinstance(error, anthropic.AuthenticationError):
        return "Claude refused the API key. Open ⚙️ Settings, paste the key again and press 🔑 Test the key."
    if isinstance(error, anthropic.PermissionDeniedError):
        return "This Claude API key is not allowed to use the API. Check it on console.anthropic.com → API Keys."
    if isinstance(error, anthropic.BadRequestError) and "credit balance" in str(error).lower():
        return "Your Anthropic credit is used up. Add some on console.anthropic.com → Billing, then ask again."
    return None


def explain_error(error: Exception) -> str:
    """What the chat shows for a failed answer."""
    return plain_words(error) or f"{type(error).__name__}: {error}"


def log_failed_answer(chat_id: str, error: Exception) -> None:
    """A known problem in one line, with the original error (it holds Anthropic's request_id).

    An unknown one with its whole traceback: that is when someone will need to read it.
    """
    known = plain_words(error)
    if known:
        chat_log.error("chat %s: the answer failed: %s (%s: %s)", chat_id, known, type(error).__name__, error)
    else:
        chat_log.error("chat %s: the answer failed: %s: %s", chat_id, type(error).__name__, error, exc_info=error)


def describe_usage(turn: dict | None, chat: dict) -> str:
    """One line about tokens, e.g. "this answer: 2,431 in, 312 out · this chat: 10,234 in, 1,020 out"."""
    def counts(usage: dict) -> str:
        text = f"{usage['input']:,} in, {usage['output']:,} out"
        if usage.get("cached"):
            text += f" ({usage['cached']:,} of the input from the cache)"
        return text

    parts = [f"this answer: {counts(turn)}"] if turn else []
    if chat["input"] or chat["output"]:
        parts.append(f"this chat: {counts(chat)}")
    return "Tokens · " + " · ".join(parts) if parts else ""


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


async def _input(prompt: str) -> str:
    """input() without blocking the event loop, and without blocking Ctrl+C.

    asyncio.to_thread would also work, but Python waits for its threads before
    exiting, so Ctrl+C at the prompt did nothing until Enter was pressed. A daemon
    thread is not waited for.
    """
    loop = asyncio.get_running_loop()
    answer: asyncio.Future = loop.create_future()

    def deliver(setter, value) -> None:
        if not answer.done():  # after Ctrl+C nobody is waiting any more
            setter(value)

    def read() -> None:
        try:
            line = input(prompt)
        except BaseException as error:  # EOFError (Ctrl+D) goes back to the caller
            loop.call_soon_threadsafe(deliver, answer.set_exception, error)
        else:
            loop.call_soon_threadsafe(deliver, answer.set_result, line)

    threading.Thread(target=read, daemon=True).start()
    return await answer


async def _ask_in_terminal(tool: str, args: dict, thread_id: str) -> bool:
    answer = await _input(f"\n  ⚠️  Allow {tool}?\n{describe_change(args)}\n[y/N] ")
    return answer.strip().lower() in ("y", "yes", "s", "si", "sì")


async def run_text(args, llm) -> None:
    async def confirm(tool: str, args_: dict, thread_id: str) -> bool:
        return not needs_question(args.ask, tool, args_) or await _ask_in_terminal(tool, args_, thread_id)

    history = ChatHistory()
    chat_id = history.new_id()
    chat_usage = {"input": 0, "output": 0, "cached": 0}
    async with LittleAgent(llm, args.server_url, confirm, trim_history=args.provider == "ollama") as agent:
        _banner(args, agent)
        print("Try:\n" + "\n".join(f"  - {e}" for e in EXAMPLES[:4]))
        print("Type 'quit' to exit.\n")
        while True:
            try:
                question = (await _input("You: ")).strip()
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
                    if event.kind == "usage":
                        for key in chat_usage:
                            chat_usage[key] += event.args.get(key, 0)
                        print(f"  {describe_usage(event.args, chat_usage)}")
                    _print_event(event, args.think)
            except Exception as e:
                history.save(chat_id, Event("error", explain_error(e)))
                print(f"\n❌ {explain_error(e)}\n")
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
    print(f"  Little MCP {version()}")
    print(f"  Provider : {args.provider}   Model: {model}")
    print(f"  Server   : {args.server_url or 'started automatically (stdio)'}")
    print(f"  Tools    : {', '.join(t.name for t in agent.tools)}")
    print("=" * 60)


# --- Web UI -------------------------------------------------------------------------


def add_to_chat(shown: list[dict], boxes: dict[str, int], event, think: bool, show_tools: bool = True) -> None:
    """Add one agent event to the Gradio chat messages.

    Each tool call gets a collapsible box. `boxes` maps a call id to its box,
    so when several tools run at once, each result lands under its own call.
    With show_tools off, calls and results are left out (they stay in the saved chat).
    """
    if event.kind in ("tool_call", "tool_result") and not show_tools:
        return
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


def replay(records: list[dict], show_tools: bool = True) -> list[dict]:
    """Turn a saved chat back into Gradio chat messages."""
    shown: list[dict] = []
    boxes: dict[str, int] = {}
    for record in records:
        add_to_chat(shown, boxes, Event(**{k: v for k, v in record.items() if k != "time"}), think=False,
                    show_tools=show_tools)
    return shown


NO_MODEL = "No AI model is set up yet. Open the ⚙️ Settings tab, choose one and press Save."


def welcome_text(problem: str) -> str:
    return ("### 👋 Welcome!\nBefore the first chat, choose the AI model that will answer you.\n\n"
            f"⚠️ {problem}")


def run_graph(args, llm) -> None:
    # Gradio sends usage statistics to huggingface.co by default. Off: this app tells its users
    # where their data goes (DISCLAIMER.md), and that was a place nobody chose. Set before the
    # import, so it also covers the statistics Gradio sends when the page starts.
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    import gradio as gr

    quit_requested = threading.Event()  # set by the ⏻ Quit button

    # What the ⚙️ Settings tab can change while the app runs. A turn reads it when it starts.
    live = {"llm": llm, "provider": args.provider, "ask": args.ask, "show_tools": settings.show_tools(),
            # --server-url on the command line means "use the tools of another computer", for this run.
            "network": "remote" if args.server_url else settings.network_mode(),
            "server_url": args.server_url or settings.get(settings.SERVER_URL),
            "password": settings.get(settings.PASSWORD), "share_failure": ""}

    # "Share this computer's tools": the HTTP server runs inside the app, no second terminal.
    share = Sharing()
    if live["network"] == "share":
        live["share_failure"] = share.start(config.MCP_PORT, live["password"])
        print(f"⚠️  Sharing did not start: {live['share_failure']}" if live["share_failure"] else
              f"Sharing this computer's tools on port {config.MCP_PORT}, password "
              f"{'on' if live['password'] else 'OFF'}. Details in ⚙️ Settings → Network.")

    def server_for_chat() -> str | None:
        """None: the chat starts its own server, as always. An address: it uses another computer's."""
        return live["server_url"] if live["network"] == "remote" else None

    def share_status() -> str:
        """What the Network section says about sharing: where to connect, and how."""
        if live["network"] != "share":
            return ""
        if not share.running:
            return f"🔴 **Not sharing.** {live['share_failure']}"
        addresses = this_computer_addresses()
        header = f' --header "Authorization: Bearer {share.password}"' if share.password else ""
        lines = ["🟢 **Sharing.** Other computers can use this computer's tools at:", ""]
        lines += [f"- `http://{a}:{share.port}/mcp`" for a in addresses]
        lines += ["", "Password: **on**." if share.password else
                  "⚠️ **No password:** any device on your network can use every tool, "
                  "including the ones that change your data."]
        lines += ["", f"**On the other computer**, in Little MCP: ⚙️ Settings → Network → "
                      f"*{settings.NETWORK_MODES['remote']}*, address `{addresses[0]}`"
                      + (", and the same password." if share.password else "."),
                  "", "In Claude Code:", "```bash",
                  f"claude mcp add --transport http little-mcp http://{addresses[0]}:{share.port}/mcp{header}", "```"]
        if firewall_on():
            lines += ["", f"🧱 The firewall (ufw) is on and blocks other computers. Let one in, with its "
                          f"IP address in place of OTHER_IP:", "```bash",
                      f"sudo ufw allow from OTHER_IP to any port {share.port} proto tcp", "```"]
        return "\n".join(lines)

    def model_for(provider: str) -> str | None:
        """--model was chosen for the provider given on the command line, not for the other one."""
        return args.model if provider == args.provider else None

    def problem_now() -> str:
        provider = live["provider"]
        problem = settings.setup_problem(provider, model_for("ollama") or DEFAULT_OLLAMA_MODEL)
        if not problem and live["llm"] is None:
            problem = NO_MODEL
        return problem

    history = ChatHistory()
    host: dict = {}  # the task that owns the MCP session: "task", "ready", "stop", "loop"
    lock = asyncio.Lock()
    queues: dict[str, asyncio.Queue] = {}  # chat id -> what its running turn wants to show
    answers: dict[str, asyncio.Future] = {}  # chat id -> the Allow / Decline click it waits for
    turns: dict[str, asyncio.Task] = {}  # chat id -> its running turn, for the Stop button
    asking: dict[str, str] = {}  # chat id -> the "Ask before changes" choice when its turn started
    one_question: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # for tools running in parallel

    async def ask_in_browser(tool: str, args_: dict, chat_id: str) -> bool:
        """Show the Allow / Decline panel, and wait for a click. The agent is paused meanwhile."""
        if not needs_question(asking.get(chat_id, live["ask"]), tool, args_):
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

    # The MCP session is opened and closed by one task that lives as long as the app.
    # anyio, which the MCP library uses, requires a session to be closed by the same task
    # that opened it. Opened inside the first chat turn, it could only be closed by
    # Python's clean-up at exit, from another task: that printed pages of errors on Ctrl+C.

    async def keep_agent(ready: asyncio.Future, stop: asyncio.Event) -> None:
        try:
            async with LittleAgent(live["llm"], server_for_chat(), ask_in_browser,
                                   trim_history=live["provider"] == "ollama") as agent:
                ready.set_result(agent)
                await stop.wait()
        except Exception as error:  # e.g. the server did not start: the next message tries again
            if not ready.done():
                ready.set_exception(error)

    async def get_agent() -> LittleAgent:
        async with lock:
            if "task" not in host or host["task"].done():
                loop = asyncio.get_running_loop()  # Gradio's event loop, where the session must live
                ready, stop = loop.create_future(), asyncio.Event()
                host.update(loop=loop, ready=ready, stop=stop, task=loop.create_task(keep_agent(ready, stop)))
            ready = host["ready"]
        # shield: a Stop click while the server starts must not cancel the start itself.
        return await asyncio.shield(ready)

    async def close_agent() -> None:
        for turn in list(turns.values()):
            turn.cancel()
        if "task" in host and not host["task"].done():
            host["stop"].set()
            await asyncio.wait_for(host["task"], 15)

    def past_chats(selected: str | None = None):
        return gr.update(choices=[(c.label, c.id) for c in history.chats()], value=selected)

    with gr.Blocks(title="Little MCP EVO", analytics_enabled=False) as demo:
        chat_id = gr.State("")  # the conversation this browser tab is looking at

        with gr.Row():
            with gr.Column():
                gr.Markdown("## Little MCP EVO\nWeather, time, arithmetic, your documents, the warehouse and "
                            "your own tables.")
                # Small, under the title: a screenshot of the page says which code it came from.
                gr.Markdown(f"Little MCP {version()}", elem_id="usage")
            # Started from the desktop icon, the app has no terminal and no Ctrl+C: this is its off switch.
            quit_button = gr.Button("⏻ Quit", size="sm", scale=0, min_width=90)
        with gr.Row(visible=False) as quit_confirm:
            gr.Markdown("**Stop Little MCP?** The chats and your data stay saved. "
                        "Other computers that use this one's tools lose them until you start it again.")
            quit_yes = gr.Button("⏻ Yes, stop", variant="stop", size="sm", scale=0, min_width=120)
            quit_no = gr.Button("Cancel", size="sm", scale=0, min_width=90)
        stopped = gr.Markdown("", visible=False)

        with gr.Tabs(selected="chat") as tabs:
            with gr.Tab("💬 Chat", id="chat"):
                # A row, not a sidebar: on a narrow window a row wraps, while a sidebar would cover the chat.
                with gr.Row():
                    with gr.Column(scale=1, min_width=220):
                        new_chat = gr.Button("➕ New chat", variant="primary")
                        past = gr.Radio(label="Chat history", choices=[], interactive=True, elem_id="chat-history")

                    with gr.Column(scale=4, min_width=420):
                        chatbot = gr.Chatbot(height="55vh", label="Conversation", show_label=False)

                        with gr.Group(visible=False) as confirm_panel:
                            change = gr.Textbox(label="", interactive=False, lines=2, max_lines=25)
                            with gr.Row():
                                allow = gr.Button("✅ Allow", variant="primary")
                                decline = gr.Button("🚫 Decline", variant="stop")

                        status = gr.Markdown("")
                        usage_line = gr.Markdown("", elem_id="usage")

                        with gr.Row():
                            message = gr.Textbox(placeholder="Write a message...", show_label=False, scale=8,
                                                 lines=2, max_lines=12, submit_btn=False)
                            with gr.Column(scale=1, min_width=100):
                                send = gr.Button("Send", variant="primary")
                                stop = gr.Button("⏹ Stop", variant="stop", interactive=False)
                        gr.Examples(examples=[[e] for e in EXAMPLES], inputs=[message], label="Try")

            with gr.Tab("⚙️ Settings", id="settings"):
                # The keys are never sent to the browser: the fields start empty, the placeholder
                # shows the last 4 characters, and an empty field on Save keeps the saved key.
                #
                # The key fields are plain text fields, hidden with CSS (the "secret" class), not
                # type="password": a browser sees a password field emptied after Save as a sign-in,
                # and offers to save the key in its password manager.
                no_autofill = gr.InputHTMLAttributes(autocomplete="off", spellcheck=False,
                                                     autocorrect="off", autocapitalize="off")
                with gr.Column(elem_id="settings"):
                    welcome = gr.Markdown("", visible=False)

                    gr.Markdown("### AI model")
                    provider = gr.Radio(choices=[(label, value) for value, label in settings.PROVIDERS.items()],
                                        value=args.provider, show_label=False)
                    claude_key = gr.Textbox(label="Claude API key", elem_classes=["secret"],
                                            html_attributes=no_autofill,
                                            info="From console.anthropic.com → API Keys. "
                                                 "Leave it empty to keep the saved key.")
                    with gr.Row():
                        test_key = gr.Button("🔑 Test the key", size="sm", scale=0)
                        key_result = gr.Markdown("")

                    gr.Markdown("### Ask before changing data")
                    ask_when = gr.Radio(value=args.ask, show_label=False,
                                        choices=[(label, value) for value, label in ASK_CHOICES.items()],
                                        info="\"Not for new rows\" lets an INSERT run without the question. "
                                             "\"Never\" lets the AI change and delete your data without asking.")

                    gr.Markdown("### Show the tools in the chat")
                    show_tools = gr.Radio(choices=[("Yes", "yes"), ("No", "no")], show_label=False,
                                          value="yes" if live["show_tools"] else "no",
                                          info="Yes: each tool the AI uses appears in a box, with what it sent "
                                               "and what came back (🛠️ get_datetime...). No: only the answers. "
                                               "The question before a change always appears.")

                    gr.Markdown("### Network")
                    network = gr.Radio(choices=[(label, value) for value, label in settings.NETWORK_MODES.items()],
                                       value=live["network"], show_label=False,
                                       info="Share: this computer runs the tools and keeps the data, and other "
                                            "computers use them. The chat on this computer works in every case.")
                    with gr.Group(visible=live["network"] == "remote") as remote_box:
                        server_address = gr.Textbox(label="Address of the other computer", value=live["server_url"],
                                                    placeholder="For example 192.168.1.20 or ubuntu-TEST.local",
                                                    html_attributes=no_autofill)
                    # The network password is shown, unlike the API keys: you have to read it here
                    # and type it on the other computer. An API key costs money; this one guards a
                    # home network, and the page itself is reachable only from this computer.
                    with gr.Column(visible=live["network"] != "local") as password_row:
                        net_password = gr.Textbox(label="Network password", html_attributes=no_autofill,
                                                  info="The same on both computers. Empty: no password.")
                        make_password = gr.Button("🎲 Create a password", size="sm", visible=live["network"] == "share")
                    test_server = gr.Button("🔌 Test the connection", size="sm",
                                            visible=live["network"] == "remote")
                    server_result = gr.Markdown("")
                    share_info = gr.Markdown("")

                    gr.Markdown("### Weather (optional)")
                    weather_key = gr.Textbox(label="OpenWeather API key", elem_classes=["secret"],
                                             html_attributes=no_autofill,
                                             info="Free at openweathermap.org/api. A new key can take 2 hours "
                                                  "to work. Leave it empty to keep the saved key.")

                    # Pinned to the bottom of the window (#save-bar in the CSS): the tab is longer
                    # than a screen, and a first test showed that a Save button out of sight gets
                    # forgotten. The form then looks done, but nothing was saved.
                    with gr.Column(elem_id="save-bar"):
                        save = gr.Button("💾 Save", variant="primary")
                        save_result = gr.Markdown("")
                    gr.Markdown("Settings are saved in the `.env` file of the project. "
                                "An option on the command line, such as `--provider`, wins over them.",
                                elem_id="usage")

        def busy(is_busy: bool) -> dict:
            """While a turn runs, nothing may start a second one or switch to another chat."""
            enabled = gr.update(interactive=not is_busy)
            return {message: enabled, send: enabled, new_chat: enabled, past: enabled,
                    stop: gr.update(interactive=is_busy)}

        async def respond(text: str, current: str):
            text = text.strip()
            if not text:
                yield {message: gr.update()}
                return
            current = current or history.new_id()
            asking[current] = live["ask"]
            earlier = history.load(current)
            shown = replay(earlier, live["show_tools"])
            boxes: dict[str, int] = {}
            first = Event("user", text)
            history.save(current, first)
            add_to_chat(shown, boxes, first, args.think, live["show_tools"])
            yield {**busy(True), message: gr.update(value="", interactive=False), chatbot: shown, chat_id: current,
                   past: past_chats(current) | {"interactive": False}}

            queue: asyncio.Queue = asyncio.Queue()
            queues[current] = queue
            finished_cleanly = False
            outcome = "answered"  # or "stopped" (⏹ Stop), or "failed"

            async def run_turn() -> None:
                # A task of its own, so the page can be updated while the agent works or waits for a click.
                nonlocal finished_cleanly, outcome
                try:
                    if live["llm"] is None:
                        queue.put_nowait(Event("error", NO_MODEL))
                        finished_cleanly = True
                        return
                    agent = await get_agent()
                    async for event in agent.stream(text, current, earlier):
                        queue.put_nowait(event)
                    finished_cleanly = True
                except asyncio.CancelledError:
                    outcome = "stopped"
                    queue.put_nowait(Event("error", "Stopped."))
                except Exception as e:
                    outcome = "failed"
                    log_failed_answer(current, e)
                    queue.put_nowait(Event("error", explain_error(e)))
                finally:
                    queue.put_nowait(None)

            turns[current] = asyncio.create_task(run_turn())
            started = time.monotonic()
            waiting_for_click = False
            turn_usage = None  # this answer's tokens, from the agent's "usage" event
            tool_calls = 0
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
                    if event.kind == "usage":
                        turn_usage = event.args
                    tool_calls += event.kind == "tool_call"
                    add_to_chat(shown, boxes, event, args.think, live["show_tools"])
                    yield {chatbot: shown, confirm_panel: gr.update(visible=False)}
            finally:
                next_event.cancel()
                turns.pop(current).cancel()
                queues.pop(current, None)
            # One line per answer: how long, how many tools, how many tokens. Not the text: the chat
            # has its own file in data/chats, and the log should be safe to show when asking for help.
            tokens = (f", {turn_usage.get('input', 0):,} tokens in ({turn_usage.get('cached', 0):,} cached), "
                      f"{turn_usage.get('output', 0):,} out") if turn_usage else ""
            chat_log.info("chat %s: %s in %.1f s, %d tool call%s%s, %s", current,
                          outcome, time.monotonic() - started, tool_calls,
                          "" if tool_calls == 1 else "s", tokens, live["provider"])
            if not finished_cleanly and "ready" in host and host["ready"].done() \
                    and not host["ready"].exception():
                await host["ready"].result().forget(current)
            yield {**busy(False), confirm_panel: gr.update(visible=False), status: "",
                   usage_line: describe_usage(turn_usage, usage_totals(history.load(current))),
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
            records = history.load(selected)
            return selected, replay(records, live["show_tools"]), describe_usage(None, usage_totals(records))

        def start_new_chat():
            return "", [], past_chats(), ""

        # --- ⚙️ Settings ---------------------------------------------------------

        def key_fields() -> dict:
            """Empty key fields, with the saved keys shown masked as placeholders."""
            def field(name: str, example: str):
                saved = settings.mask(settings.get(name))
                return gr.update(value="", placeholder=f"Saved: {saved}" if saved else example)
            return {claude_key: field(settings.CLAUDE_KEY, "Paste your key: sk-ant-..."),
                    weather_key: field(settings.WEATHER_KEY, "Paste your key")}

        def network_fields(mode: str) -> dict:
            """Show only what the chosen network mode needs."""
            return {remote_box: gr.update(visible=mode == "remote"), password_row: gr.update(visible=mode != "local"),
                    make_password: gr.update(visible=mode == "share"), test_server: gr.update(visible=mode == "remote"),
                    server_result: ""}

        def on_page_load():
            """Fill the settings, and open them first if the chat cannot work yet."""
            problem = problem_now()
            return {past: past_chats(), provider: live["provider"], ask_when: live["ask"],
                    show_tools: "yes" if live["show_tools"] else "no", **key_fields(),
                    network: live["network"], server_address: live["server_url"], net_password: live["password"],
                    share_info: share_status(), **network_fields(live["network"]),
                    welcome: gr.update(value=welcome_text(problem) if problem else "", visible=bool(problem)),
                    tabs: gr.Tabs(selected="settings" if problem else "chat")}

        async def test_the_key(typed: str):
            key = typed.strip() or settings.get(settings.CLAUDE_KEY)
            yield "⏳ Asking Anthropic..."
            _, verdict = await asyncio.to_thread(settings.check_claude_key, key)
            yield verdict if typed.strip() or not key else f"{verdict} (I tested the saved key.)"

        async def test_the_server(typed_address: str, typed_password: str):
            yield "⏳ Connecting..."
            _, verdict = await settings.check_server(settings.server_url(typed_address), typed_password.strip())
            yield verdict

        async def save_settings(chosen: str, typed_claude: str, typed_weather: str, ask_choice: str,
                                tools_choice: str, mode: str, typed_address: str, typed_password: str, current: str):
            if turns:
                return {save_result: "⏳ An answer is still being written. Wait for it, or press ⏹ Stop, "
                                     "then Save again."}
            url = settings.server_url(typed_address)
            if mode == "remote" and not url:
                return {save_result: "❌ Write the address of the other computer, then Save again."}
            password = typed_password.strip()
            values = {settings.PROVIDER: chosen, settings.ASK: ask_choice, settings.SHOW_TOOLS: tools_choice,
                      settings.NETWORK: mode, settings.SERVER_URL: url or live["server_url"],
                      settings.PASSWORD: password}
            if typed_claude.strip():
                values[settings.CLAUDE_KEY] = typed_claude
            if typed_weather.strip():
                values[settings.WEATHER_KEY] = typed_weather
            try:
                settings.save(values)
            except (ValueError, OSError) as error:
                settings_log.warning("could not save the settings: %s", error)
                return {save_result: f"❌ Could not save: {error}"}
            # Which settings, never the secrets themselves.
            settings_log.info("saved: model %s, ask %s, show tools %s, network %s%s%s%s, network password %s",
                              chosen, ask_choice, tools_choice, mode, f" ({url})" if mode == "remote" else "",
                              ", new Claude key" if typed_claude.strip() else "",
                              ", new weather key" if typed_weather.strip() else "", "on" if password else "off")

            live.update(ask=ask_choice, show_tools=tools_choice == "yes")
            config.MCP_PASSWORD = password  # what the chat sends to another computer's server

            # Sharing: start it, start it again with a new password, or stop it.
            if mode == "share" and (not share.running or share.password != password):
                live["share_failure"] = await asyncio.to_thread(share.start, config.MCP_PORT, password)
            elif mode != "share" and share.running:
                await asyncio.to_thread(share.stop)

            # A new model, a new key or another server means a new connection: the MCP server is
            # a child process that read the keys when it started. The chats stay; the next message
            # reconnects.
            new_key = settings.CLAUDE_KEY in values or settings.WEATHER_KEY in values
            new_server = mode != live["network"] or (mode == "remote" and (url, password) !=
                                                     (live["server_url"], live["password"]))
            live.update(network=mode, server_url=url or live["server_url"], password=password)
            if chosen != live["provider"] or new_key or new_server or live["llm"] is None:
                try:
                    new_llm = build_llm(chosen, model_for(chosen), args.think, args.effort)
                except RuntimeError:
                    new_llm = None
                await close_agent()
                live.update(llm=new_llm, provider=chosen)

            problem = problem_now()
            if not problem and mode == "share" and not share.running:
                problem = f"Sharing did not start. {live['share_failure']}"
            # The chat on screen is drawn again, so a new "show the tools" choice applies to it too.
            redrawn = {chatbot: replay(history.load(current), live["show_tools"]) if current else []}
            if problem:
                # Stay here: the welcome at the top says what is still missing.
                return {save_result: f"⚠️ Saved, but something doesn't work yet: {problem}", **key_fields(),
                        welcome: gr.update(value=welcome_text(problem), visible=True), share_info: share_status(),
                        server_address: url or live["server_url"], **redrawn}
            # All set: back to the chat, with a notice that fades by itself.
            notice = f"Settings saved. The next message uses {settings.PROVIDERS[chosen].split(' (')[0]}"
            notice += {"share": f", and other computers can use this one's tools (port {config.MCP_PORT}).",
                       "remote": f", with the tools of {url}.", "local": "."}[mode]
            gr.Info(notice)
            return {save_result: "", **key_fields(), welcome: gr.update(value="", visible=False),
                    tabs: gr.Tabs(selected="chat"), share_info: share_status(),
                    server_address: url or live["server_url"], **redrawn}

        gr.on([message.submit, send.click], respond, [message, chat_id],
              [message, send, stop, new_chat, past, chatbot, chat_id, confirm_panel, change, status, usage_line],
              concurrency_limit=4)
        stop.click(click_stop, [chat_id], None, concurrency_limit=None)
        allow.click(click_allow, [chat_id], [confirm_panel], concurrency_limit=None)
        decline.click(click_decline, [chat_id], [confirm_panel], concurrency_limit=None)
        past.input(open_chat, [past], [chat_id, chatbot, usage_line])
        new_chat.click(start_new_chat, None, [chat_id, chatbot, past, usage_line])
        test_key.click(test_the_key, [claude_key], [key_result])
        # Then back to the top of the page: the chat starts there, and so does the welcome
        # that says what is still missing.
        network.change(network_fields, [network], [remote_box, password_row, make_password, test_server, server_result])
        make_password.click(settings.new_password, None, [net_password])
        test_server.click(test_the_server, [server_address, net_password], [server_result])
        save.click(save_settings, [provider, claude_key, weather_key, ask_when, show_tools, network, server_address,
                                   net_password, chat_id],
                   [save_result, claude_key, weather_key, welcome, tabs, chatbot, share_info, server_address]
                   ).then(None, js="() => window.scrollTo({top: 0, behavior: 'smooth'})")
        def stop_app():
            # The same as Ctrl+C in the terminal: the main thread below closes the MCP session,
            # stops sharing and closes the page. Half a second later, so this answer reaches the page.
            # Not by sending ourselves Ctrl+C (SIGINT): a program started in the background, as the
            # desktop icon does, ignores it, and the app would go on running unseen.
            threading.Timer(0.5, quit_requested.set).start()
            return {quit_confirm: gr.update(visible=False), tabs: gr.update(visible=False),
                    stopped: gr.update(visible=True, value="### ⏻ Little MCP has stopped.\nYou can close this "
                                       "tab. To start it again, click the Little MCP icon, or run "
                                       "`python -m little_mcp`.")}

        quit_button.click(lambda: gr.update(visible=True), None, [quit_confirm])
        quit_no.click(lambda: gr.update(visible=False), None, [quit_confirm])
        quit_yes.click(stop_app, None, [quit_confirm, tabs, stopped])
        demo.load(on_page_load, None, [past, provider, ask_when, show_tools, claude_key, weather_key, welcome, tabs,
                                       network, server_address, net_password, share_info, remote_box, password_row,
                                       make_password, test_server, server_result])

    # server_name stays 127.0.0.1 (Gradio's default) on purpose: the Settings tab shows and
    # changes your keys, so this page must never be reachable from another computer.
    # quiet: no "Running on local URL" and, above all, no "set share=True" hint, which for this app
    # would mean putting the Settings page, keys included, on the internet. Our own lines follow.
    demo.launch(share=False, server_name="127.0.0.1", prevent_thread_lock=True, inbrowser=args.open_browser,
                quiet=True,
                css="#chat-history { max-height: 60vh; overflow-y: auto; } #usage { opacity: 0.7; font-size: 0.9em; }"
                    " #settings { max-width: 760px; }"
                    # Gradio's outer box has overflow: hidden, which turns "sticky" off for everything
                    # inside it. "clip" cuts off what sticks out just the same, but leaves sticky working.
                    " .gradio-container { overflow: clip !important; }"
                    " #save-bar { position: sticky; bottom: 0; z-index: 10; padding: 10px 0;"
                    " background: var(--body-background-fill); border-top: 1px solid var(--border-color-primary); }"
                    # Dots instead of letters in the key fields. Chrome, Edge and Safari support it;
                    # a browser that doesn't shows the letters, on your own screen only.
                    " .secret input, .secret textarea { -webkit-text-security: disc; }")
    page = f"http://127.0.0.1:{demo.server_port}"
    print(f"Little MCP is running: open {page}")
    print("Stop it with ⏻ Quit in the page, or Ctrl+C here.")
    print(f"What happens is written to {logs.LOG_FILE}")
    app_log.info("page ready at %s", page)
    why = "⏻ Quit in the page"
    try:
        # The web server runs in its own thread; this one waits for ⏻ Quit or Ctrl+C.
        while not quit_requested.wait(0.5):
            pass
    except KeyboardInterrupt:
        why = "Ctrl+C"
        print("\nClosing...")
    finally:
        app_log.info("stopping (%s)", why)
        loop = host.get("loop")
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(close_agent(), loop).result(timeout=20)
            except Exception as error:
                print(f"The MCP server did not close cleanly ({type(error).__name__}).")
        share.stop()
        demo.close()
        app_log.info("stopped")


def main(argv=None) -> None:
    args = parse_args(argv)
    logs.rotate()
    logs.setup(verbose=args.verbose)
    # First, so whoever reads the screen (or a screenshot of it) knows exactly which code runs.
    print(f"Little MCP {version()}")
    if args.provider is None:  # chosen here, not in parse_args: it may ask Ollama what is downloaded
        args.provider = settings.default_provider(args.model or DEFAULT_OLLAMA_MODEL)
    try:
        llm = build_llm(args.provider, args.model, args.think, args.effort)
    except RuntimeError as e:
        if args.mode != "graph":
            sys.exit(f"❌ {e}")
        # The web page can fix this itself: it opens on the ⚙️ Settings tab and says what is missing.
        # (Not the error text: "Set ANTHROPIC_API_KEY in your .env file" would send the user to .env.)
        print("⚠️  The AI model isn't set up yet. Open the page: it starts on ⚙️ Settings, "
              "where you can choose the model and paste your key.")
        llm = None
    app_log.info("started: Little MCP %s, %s, model %s, network %s%s", version(),
                 "web page" if args.mode == "graph" else "terminal chat", args.provider,
                 "remote " + args.server_url if args.server_url else settings.network_mode(),
                 "" if llm else " (no model set up yet)")
    if args.mode == "graph":
        run_graph(args, llm)
        return
    try:
        asyncio.run(run_text(args, llm))
    except KeyboardInterrupt:  # Ctrl+C: asyncio has already closed the MCP session in its own task
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
