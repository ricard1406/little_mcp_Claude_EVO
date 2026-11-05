# Little MCP EVO 🤖

A small AI agent that runs on your machine, built on a **real
[Model Context Protocol](https://modelcontextprotocol.io) server**.

Ask it about the weather, the time anywhere, your own documents or your
warehouse stock, using a local model (Ollama) or Claude.

This is the next version of [Little_MCP](https://github.com/ricard1406/Little_MCP).
See [CHANGELOG.md](CHANGELOG.md) for what changed and why.

```
You: Do we have oranges in the warehouse?
  🛠️  find_item({"item": "orange"})
  ↳ [{"table": "FRUITS", "item": "ORANGE", "quantity": 3}]
Assistant: Yes, there are 3 oranges in stock.
```

## How it works

```
 ┌──────────────── agent (little_mcp.agent) ────────────────┐
 │  LLM: Ollama (local) or Claude                           │
 │  asks the server "what tools do you have?" (tools/list)  │
 └───────────────▲──────────────────────────────────────────┘
                 │  MCP over stdio (automatic) or HTTP
 ┌───────────────┴──────── server (little_mcp.server) ──────┐
 │  get_datetime   get_weather   calculate                  │
 │  search_documents (your PDFs)                            │
 │  list_inventory  find_item  adjust_stock  set_stock      │
 └──────────────────────────────────────────────────────────┘
```

The agent has **no hardcoded tools**. It discovers them from the server, so
a tool you add to `server.py` shows up in the agent on the next start. And
because it is real MCP, **Claude Desktop, Claude Code or any MCP client can
use the same server** ([see below](#use-the-server-from-claude-desktop-or-claude-code)).

## Quick start (Ubuntu / Debian)

### 1. Get the code

A fresh Ubuntu doesn't include `git`, so install it first:

```bash
sudo apt install -y git
```
```bash
git clone https://github.com/ricard1406/little_mcp_Claude_EVO.git
```
```bash
cd little_mcp_Claude_EVO
```

### 2. Install everything

```bash
./install.sh
```

The script asks for your password once, for the system packages. It's safe to
run again. It does the following:
- installs `curl` and Python's `venv` support,
- creates the Python environment in `.venv` and installs the project,
- creates your `.env` settings file from `.env.example`,
- installs [Ollama](https://ollama.com) and downloads the two local models (a few GB).

If you only want to chat with Claude, use `./install.sh --claude-only`. It
still downloads the small `nomic-embed-text` model, because document search
uses it even with Claude.

<details>
<summary>Prefer to do it by hand? Here are the same steps.</summary>

```bash
sudo apt install -y curl python3-venv
```
```bash
python3 -m venv .venv && source .venv/bin/activate
```
```bash
pip install -e ".[all]"
```
```bash
cp .env.example .env
```
```bash
curl -fsSL https://ollama.com/install.sh | sh
```
```bash
ollama pull qwen3:4b
```
```bash
ollama pull nomic-embed-text
```

</details>

### 3. Add your keys

Open the settings file in a text editor:

```bash
nano .env
```

Fill in the keys you have, after the `=` with no spaces or quotes. Leave the others empty:

```ini
OPENWEATHER_API_KEY=paste-your-openweather-key-here
ANTHROPIC_API_KEY=paste-your-anthropic-key-here
```

- `OPENWEATHER_API_KEY` turns on weather. Get a [free key](https://openweathermap.org/api).
- `ANTHROPIC_API_KEY` is only needed for Claude. Get one in the [Claude Console](https://console.anthropic.com).

In nano, save with `Ctrl+O` then `Enter`, and quit with `Ctrl+X`.

### 4. Start chatting

Each time you open a new terminal, activate the environment first:

```bash
source .venv/bin/activate
```
```bash
python -m little_mcp
```

## Run it

**One command.** The agent starts the MCP server for you; there's no second terminal.

| Command | What it does |
|---|---|
| `python -m little_mcp` | Chat in the terminal with the local model |
| `python -m little_mcp graph` | Web UI at http://127.0.0.1:7860 |
| `python -m little_mcp --provider anthropic` | Use Claude (default model `claude-opus-5-5`) |
| `python -m little_mcp --think` | Also show the model's reasoning and every tool call |
| `python -m little_mcp --model llama3.2` | Use another Ollama (or Claude) model |
| `python -m little_mcp --provider anthropic --effort high` | Let Claude think harder |

Flags can be combined. `python -m little_mcp --help` lists them all.

### Changing the inventory is always your call

`adjust_stock` and `set_stock` change data, and the server marks them as such
(MCP tool annotations). The agent reads that and:

- in the **terminal**, asks you `Allow adjust_stock(...)? [y/N]` before each change;
- in the **web UI**, blocks changes unless you start it with `--allow-writes`.

`--allow-writes` in the terminal skips the question.

### Run the server on its own

```bash
python -m little_mcp.server --transport http
```

It listens on `http://127.0.0.1:8000/mcp`. Point the agent at it with:

```bash
python -m little_mcp --server-url http://127.0.0.1:8000/mcp
```

You can also explore it with the official inspector: `npx @modelcontextprotocol/inspector`.

## Use the server from Claude Desktop or Claude Code

**Claude Code:**

```bash
claude mcp add little-mcp -- /full/path/to/little_mcp_Claude_EVO/.venv/bin/python -m little_mcp.server
```

**Claude Desktop:** add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "little-mcp": {
      "command": "/full/path/to/little_mcp_Claude_EVO/.venv/bin/python",
      "args": ["-m", "little_mcp.server"]
    }
  }
}
```

Then ask Claude *"Do we have oranges in the warehouse?"*.

## Your documents

Put PDF, `.txt` or `.md` files in `data/`. They are indexed on the first
search, and **re-indexed automatically when you add, change or delete a
file**. The index lives in `.index/`, and you can delete it any time.

## The warehouse database

By default the inventory is a small SQLite database (`data/mystore.db`),
created with demo data the first time you use it, so there's nothing to set up.

The agent never writes SQL. It can only call the four inventory tools, which
run fixed, parameterized queries on the `FRUITS` and `VEGGIE` tables. Prompt
injection or a confused model can't drop a table or read another one.

<details>
<summary><b>Use MariaDB instead</b></summary>

1. Install the connector:

   ```bash
   sudo apt install libmariadb-dev
   ```
   ```bash
   pip install -e ".[mariadb]"
   ```

2. Create the database and a user that can only do what the tools need:

   ```sql
   CREATE DATABASE MYSTORE;
   USE MYSTORE;
   CREATE TABLE FRUITS (ITEM VARCHAR(50) PRIMARY KEY, QUANTITY INT NOT NULL);
   CREATE TABLE VEGGIE (ITEM VARCHAR(50) PRIMARY KEY, QUANTITY INT NOT NULL);
   INSERT INTO FRUITS VALUES ('APPLES',2),('ORANGE',3),('APRICOT',5),('GRAPES',4),('BANANA',1);
   INSERT INTO VEGGIE VALUES ('CARROT',7),('POTATO',12),('TOMATO',6),('ONION',9);

   CREATE USER 'little_mcp'@'localhost' IDENTIFIED BY 'choose-a-password';
   GRANT SELECT, UPDATE (QUANTITY) ON MYSTORE.FRUITS TO 'little_mcp'@'localhost';
   GRANT SELECT, UPDATE (QUANTITY) ON MYSTORE.VEGGIE TO 'little_mcp'@'localhost';
   ```

3. In `.env`, set `DB_BACKEND=mariadb` and set `DB_USER` / `DB_PASSWORD` to that user.

</details>

## Add your own tool

Add a function to `little_mcp/server.py`:

```python
@mcp.tool(annotations=READ_ONLY)
def word_count(text: str) -> int:
    """Count the words in a text."""
    return len(text.split())
```

That's all. The type hints become the tool's input schema, and the docstring
becomes the description the model reads. Restart the agent and it uses the
new tool. For a tool that changes data, use
`ToolAnnotations(readOnlyHint=False)`, and the agent will ask before running it.

## Project layout

```
little_mcp/
  server.py      the MCP server and all its tools
  agent.py       the agent: LLM + tools discovered over MCP
  cli.py         terminal chat and web UI
  rag.py         document index with change detection
  store.py       inventory database (SQLite / MariaDB)
  calculator.py  safe arithmetic
  config.py      settings from .env
data/            your documents (and the demo SQLite database)
tests/           pytest suite (no LLM or network needed)
```

## Tests

```bash
pytest
```

The suite covers the calculator, the database (including SQL injection
attempts), the document index, the server over the real MCP protocol, and the
whole agent loop. A scripted fake model stands in for the LLM, so the tests
need no Ollama, no API key and no network.

## Troubleshooting

- **"Document search failed"**: start Ollama, then run `ollama pull nomic-embed-text`.
- **Weather tool says it is not configured**: set `OPENWEATHER_API_KEY` in `.env`.
- **Ollama connection refused**: run `ollama serve`, and check `ollama list`.
- **Small models ignore tools or loop**: try a bigger model (`--model qwen3:8b`), or Claude.

## License

MIT. See [LICENSE](LICENSE).
