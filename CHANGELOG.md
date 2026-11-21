# Changelog

## Unreleased

### ⚙️ Settings tab
- **Set up the app from the page, not from `.env`.** A new tab in the web UI: the AI
  model (Claude or the local one), the Claude key with a free **Test the key** button,
  the weather key, and when to ask before changing data. **Save** writes `.env`, and
  the next message uses the new settings without a restart.
- **The first start opens on Settings** when the chat can't work yet (no Claude key,
  or no local model), with a welcome that says what is missing. Before, the app
  stopped with "Set ANTHROPIC_API_KEY in your .env file".
- Saved keys never go back to the browser: the page shows only their last 4 characters.
  `.env` is now readable by its owner only (`chmod 600`), since it holds keys.
- Errors a user can fix are explained in plain words: a refused Claude key, a key
  without API access, and Anthropic credit used up.
- The choices saved in Settings become the defaults of `--provider` and `--ask`.
  A flag still wins, for that run. `install.sh` now ends by pointing to the page.
- After the first test on a fresh machine:
  - **Save takes you back to the chat**, with a notice that fades by itself. If
    something is still missing, it stays on Settings and the welcome says what.
  - **Claude is preselected when the local model isn't downloaded.** With nothing
    saved, the app starts with Claude if there is a key, the local model if it is
    there, and Claude otherwise.
  - **No more "Save password?"** from the browser after Save. The key fields are no
    longer password fields, which browsers took for a sign-in; they still show dots.
  - **"Show the tools in the chat": Yes / No.** No hides the 🛠️ boxes and shows only
    the answers. The question before a change always appears.
  - A pasted key that ends with a line break is trimmed instead of refused.
- **README: a 5-command Quick start** at the top. The details of the installer and
  the settings moved to "Installation in detail".

### ⚙️ Settings → Network: two computers without the terminal
- **Share this computer's tools with other computers.** The HTTP server now runs
  inside the app, next to its own chat: one command, one terminal. The tab shows the
  addresses to use, the password, the Claude Code command to copy, and the firewall
  command when `ufw` is on. Sharing starts by itself at the next launch.
- **Use the tools of another computer.** Type its address as you like it
  (`192.168.1.20`, `ubuntu-TEST.local`): the page completes it. **🔌 Test the
  connection** lists the other computer's tools, or says in plain words what's wrong.
- **🎲 Make one** creates a network password. It is shown, not hidden, because you
  type it on the other computer; the API keys stay hidden.
- The server can be started and stopped again in the same run. Shared on the
  network, it accepts requests addressed by IP or name: the MCP library's localhost
  check is turned off there, as with `MCP_HOST=0.0.0.0`, and the password guards the tools.
- A busy port is explained ("maybe a Little MCP server started in a terminal").
- The server's "wrong password" answer points to ⚙️ Settings, not only to `.env`.
- At start, a missing key no longer prints "Set ANTHROPIC_API_KEY in your .env
  file" next to "the page will open on Settings": just the page.
- **The 💾 Save button stays in view** at the bottom of the window. With the Network
  section the tab got longer than a screen, and in a test the form looked done while
  Save, out of sight, was never pressed.

### An icon in the apps menu: no terminal at all
- **`install.sh` step 6** adds a "Little MCP" icon to the apps menu (a `.desktop`
  file in your own folder, no sudo). One click starts the app and opens the page;
  a second click only opens the page again, instead of starting a second app.
- **`launch.sh`**, what the icon runs: the app's messages go to `data/little_mcp.log`,
  and if the app can't start, a desktop notification says so and where to look.
- **⏻ Quit** at the top of the page stops the app cleanly, sharing included. Started
  from the icon there is no terminal and no Ctrl+C. Quit doesn't send Ctrl+C to the
  app: a program started in the background ignores it, and the app would have kept
  running unseen.
- `--open-browser` opens the page when it is ready.
- **Gradio's usage statistics are off.** Gradio sent them to huggingface.co by
  default; the log of a test showed it. This app says where your data goes, and that
  was a place nobody chose.

### Other changes
- **An optional password for the HTTP server.** Set `LITTLE_MCP_PASSWORD` in `.env`
  on the server and on the client. The server then refuses any request without it,
  before MCP sees it. Until now, anything that could reach the port could call
  every tool, including the ones that change data, without being asked. Empty
  means no password, as before; the server then warns when it listens on the network.
- A wrong password, or a server that doesn't answer at `--server-url`, now says so
  in plain words, instead of `ExceptionGroup: unhandled errors in a TaskGroup`.
- `install.sh` checks for an NVIDIA or AMD graphics card before offering `qwen3:4b`.
  Without one (a virtual machine, most laptops), it says the 2.5 GB model would be
  very slow, before you download it rather than after.

## 0.7.0: myTinySQL, 2026-11-14

Little MCP gets its own database: a Python port of
[myTinySQL](https://github.com/ricard1406/myTinySql-rust), and the AI can now create
tables and store your data in it by writing SQL. The web UI becomes the default, with
chat history, approval buttons and a token counter.

### myTinySQL
- **The default database.** A Python port of the Rust version, in `little_mcp/tinysql/`,
  one file per Rust module. It passes the same 8 golden scripts as C++ and Rust, and reads
  and writes the same files. [docs/rust-vs-python.md](docs/rust-vs-python.md) compares the code.
- **New tools `tiny_sql_read` and `tiny_sql_write`:** the model creates tables and stores
  rows by writing SQL, and your own parser decides what can run. A write script is
  all-or-nothing.
- **New in the dialect** (Python only for now):
  - `DATE` (`YYYY-MM-DD`) and the word `TODAY`, which follows your time zone
    (`LITTLE_MCP_TIMEZONE` in `.env`). `get_datetime` uses the same zone for local time.
  - **Decimal numbers:** `NUMBER` holds values like `1338.80`, and they compare as numbers.
    A value that isn't a number is refused with a hint (`1.338,80` → `1338.80`).
  - `ALTER TABLE ... ADD` and `ALTER TABLE ... MODIFY`: add a column, or change a column's
    width or type, for example when a column is too small.
- **SQL console:** `python -m little_mcp.tinysql` opens your tables, with arrow-key history,
  and is safe to use while the chat is running.
- Stock changes hold a file lock, so two servers cannot overwrite each other.

### Web UI
- **The default.** `python -m little_mcp` opens the web page; `python -m little_mcp text`
  starts the terminal chat. Gradio is now a required dependency, and the `ui` extra is gone.
- **Asks before changing data**, with Allow and Decline buttons and the whole change on
  screen. Before, it could only block changes or allow them all.
- **"Ask before changes":** Always, Not for new rows, or Never; also `--ask` on the command line.
- **Chat history:** every conversation is saved in `data/chats/`. Open an old chat from the
  list and carry on, even after a restart.
- **Token usage:** a line under the chat shows the tokens read and written, for the last
  answer and the whole chat (the terminal chat prints it too).
- Counts the seconds while the model works, and has a **Stop** button.
- The chat history column wraps on a narrow window instead of covering the chat.

### Claude
- **Prompt caching:** the tool descriptions, the instructions and the conversation so far
  are cached for 5 minutes, so later model calls read them back at a fraction of the price.
  In testing, two thirds of a first answer's input already came from the cache.

### Fixes
- **Ctrl+C closes cleanly** in the web UI, the terminal chat and the HTTP server: no pages
  of errors, and no waiting for Enter at the terminal prompt.
- The HTTP server no longer prints a traceback when a client on another computer
  disconnects normally (a false alarm of the MCP library), or when you stop it while a
  client such as Claude Code is still connected.
- The terminal shows the whole change before asking, instead of a shortened version.
- An error from the read tool names a refused statement properly ("CREATE TABLE", not
  "CREATETABLE").

### Upgrading from 0.6.0
- Your own `.env` still says `DB_BACKEND=sqlite`, so the inventory stays in SQLite. To use
  myTinySQL, change it to `DB_BACKEND=tinysql`. Stock levels are not copied over: myTinySQL
  starts with the demo values.
- Optionally add `LITTLE_MCP_TIMEZONE=Europe/Rome` (your zone) to `.env`; see `.env.example`.
- After `git pull`, run `pip install -e ".[all]"` once more, in case Gradio is missing.

## 0.6.0 (EVO): first release, 2026-11-07

A rewrite of [Little_MCP](https://github.com/ricard1406/Little_MCP) 0.5.2.
The features are the same; the architecture and safety are new.

### Real MCP
- The server is now a real **Model Context Protocol** server (official `mcp` SDK,
  FastMCP), not a REST API. It speaks stdio and streamable HTTP.
- The agent **discovers tools from the server** instead of keeping a hardcoded list.
- Claude Desktop, Claude Code and the MCP Inspector can use the server directly.
- **One command** to start: the agent launches the server itself over stdio.
- New MCP resource `docs://files` lists the searchable documents.
- The server can run on another machine (HTTP, or SSH for untrusted networks).
  The README has a step-by-step guide, tested between two machines.

### Safety
- **No more raw SQL from the LLM.** The two SQL endpoints are replaced by four
  tools (`list_inventory`, `find_item`, `adjust_stock`, `set_stock`) that
  run parameterized queries on whitelisted tables.
- Write tools are marked with MCP annotations, and the agent **asks before every
  change** (terminal) or blocks changes (web UI, unless `--allow-writes` is set).
- The old `GET /put_SQL_insert` could be triggered from any web page you visited.
  The MCP HTTP transport rejects plain GET requests.
- The calculator parses expressions safely (no `eval`).
- `.env` is git-ignored, and `.env.example` documents the settings. The
  `--api-key` flag was removed so keys stay out of shell history.
- The MCP server only receives the settings it needs; the Anthropic key stays with the agent.

### Fixes
- `python -m little_mcp` works without arguments; `text` mode is the default.
- The SQL tools no longer fail out of the box. SQLite is the default backend, so
  no MariaDB install and no "uncomment this import" step are needed.
- The calculator accepts normal expressions ("15 * 3 + 7"), not only "ADD, 1, 2".
- Every network call has a timeout.
- The document index is rebuilt when files in `data/` change (it used to keep stale data).
- All PDFs, `.txt` and `.md` files in `data/` are searchable, not only one hardcoded file.
- The app works from any folder (paths are resolved from the project root).
- Each web UI tab has its own conversation. Before, everyone shared one history.
- Long conversations are trimmed before reaching small local models.
- The weather tool returns a short summary, which is easier for small models.

### Claude
- Default model is `claude-opus-5-5`, with adaptive thinking and an `--effort` flag.
- `--think` shows Claude's summarized reasoning.
- Server-side fallback is enabled, so a request declined by a safety classifier
  is retried on a suitable model instead of failing.

### Project
- Python package with `pyproject.toml` and optional extras: `claude`, `ui`, `mariadb`, `dev`.
- A pytest suite of 42 tests that need no LLM or network.
- Uses LangChain v1 (`create_agent`). `langchain-community` is no longer needed.
- Guided `install.sh` for Ubuntu/Debian: it lists download sizes and asks before
  each step, and Ollama is optional.
- `DISCLAIMER.md`: sample data, AI accuracy, and where your data goes.
