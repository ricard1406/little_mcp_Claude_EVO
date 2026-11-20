# Changelog

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
