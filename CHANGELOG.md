# Changelog

## 0.6.0 (EVO): first release

A rewrite of [Little_MCP](https://github.com/ricard1406/Little_MCP) 0.5.2.
The features are the same; the architecture and safety are new.

### Real MCP
- The server is now a real **Model Context Protocol** server (official `mcp` SDK,
  FastMCP), not a REST API. It speaks stdio and streamable HTTP.
- The agent **discovers tools from the server** instead of keeping a hardcoded list.
- Claude Desktop, Claude Code and the MCP Inspector can use the server directly.
- **One command** to start: the agent launches the server itself over stdio.
- New MCP resource `docs://files` lists the searchable documents.

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
