# Changelog

## Unreleased

- **myTinySQL is now the default database.** A Python port of the Rust version, in
  `little_mcp/tinysql/`, with one file per Rust module. It passes the same 8 golden
  scripts as C++ and Rust, and reads and writes the same files.
- **New tools `tiny_sql_read` and `tiny_sql_write`:** the model can create tables and
  store rows by writing SQL, and your own parser decides what can run. Write scripts
  are all-or-nothing.
- **The web UI is now the default.** `python -m little_mcp text` starts the terminal chat.
  Gradio is now a required dependency, and the `ui` extra is gone.
- **The web UI asks before changing data**, with Allow and Decline buttons and the whole
  change on screen. Before, it could only block changes or allow them all.
- **Ctrl+C closes cleanly.** The web UI printed pages of errors when stopped, because the MCP
  session was opened in one task and closed in another. The terminal chat waited for Enter
  after Ctrl+C at the prompt. Both now quit at once.
- **Decimal numbers:** `NUMBER` holds values like `1338.80`, they compare as numbers, and a
  value that isn't a number is refused with a hint (`1.338,80` → `1338.80`).
- **SQL console:** `python -m little_mcp.tinysql` opens your tables, with arrow-key history,
  and is safe to use while the chat is running.
- **New type `DATE`** (`YYYY-MM-DD`) and the word `TODAY`, which follows your time zone
  (`LITTLE_MCP_TIMEZONE` in `.env`). `get_datetime` uses the same zone for local time.
- **`ALTER TABLE ... ADD`** and **`ALTER TABLE ... MODIFY`:** add a column, or change a
  column's width or type, for example when a column is too small.
- The read tool's error now names a refused statement properly ("CREATE TABLE", not "CREATETABLE").
- **Ask less often:** "Ask before changes" in the web UI, and `--ask` on the command line.
  With "Not for new rows", a script of `INSERT`s runs without the Allow question.
- The web UI counts the seconds while the model works, and has a **Stop** button.
- **Chat history:** every conversation is saved in `data/chats/`. The web UI lists them,
  and you can open an old chat and carry on, even after a restart.
- The terminal shows the whole change before asking, instead of a shortened version.
- Stock changes on myTinySQL hold a file lock, so two servers cannot overwrite each other.

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
