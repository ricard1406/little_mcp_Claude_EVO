#!/usr/bin/env bash
# What the "Little MCP" icon in the apps menu runs (install.sh adds the icon).
#
# If Little MCP is already running, it only opens the page again. Otherwise it
# starts the app in the background, and the app opens the page when it is ready.
# There is no terminal: the ⏻ Quit button in the page stops the app.
# What the app prints goes to data/little_mcp.log, for when something goes wrong.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

PORT="${GRADIO_SERVER_PORT:-7860}"
URL="http://127.0.0.1:$PORT"
LOG="data/little_mcp.log"

# Plain bash, no curl: curl is only installed with Ollama.
answers() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }

# Tell the user, in a desktop notification if there is a desktop, otherwise on the terminal.
say() {
    if command -v notify-send >/dev/null; then
        notify-send --app-name="Little MCP" "Little MCP" "$1"
    fi
    echo "$1" >&2
}

if answers; then
    xdg-open "$URL" >/dev/null 2>&1 &
    exit 0
fi

if [[ ! -x .venv/bin/python ]]; then
    say "Little MCP is not installed in $(pwd). Run ./install.sh there first."
    exit 1
fi

mkdir -p data
printf '\n=== %s: started from the icon ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
# setsid: the app does not stop when the program that clicked the icon ends.
# -u: Python writes to the log at once; buffered, a crash could leave the log empty.
setsid .venv/bin/python -u -m little_mcp --open-browser >> "$LOG" 2>&1 < /dev/null &
app=$!

# The first start can take a while (the libraries load). Wait up to a minute, and say
# so if the app stops before its page answers, instead of failing without a word.
for _ in $(seq 1 120); do
    if answers; then
        exit 0
    fi
    if ! kill -0 "$app" 2>/dev/null; then
        say "Little MCP could not start. What it said is in $(pwd)/$LOG"
        exit 1
    fi
    sleep 0.5
done
say "Little MCP is taking long to start. If no page opens, see $(pwd)/$LOG"
