#!/usr/bin/env bash
# What the "Little MCP" icon in the apps menu runs (install.sh adds the icon).
#
# If Little MCP is already running, it only opens the page again. Otherwise it
# starts the app in the background, waits for its page, and then opens it.
# There is no terminal: the ⏻ Quit button in the page stops the app.
# What happens is in data/logs/little_mcp.log, as always. What the app would print
# on a terminal goes to data/logs/console.log: it is where a failed start shows up.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

PORT="${GRADIO_SERVER_PORT:-7860}"
URL="http://127.0.0.1:$PORT"
LOG="data/logs/console.log"

# Plain bash, no curl: curl is only installed with Ollama.
answers() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }

# Tell the user, in a desktop notification if there is a desktop, otherwise on the terminal.
say() {
    if command -v notify-send >/dev/null; then
        notify-send --app-name="Little MCP" "Little MCP" "$1"
    fi
    echo "$1" >&2
}

# The browser opened from here, with its output thrown away. Opened by the app, the browser
# wrote into console.log ("Failed sending zygote boot message", "No 3D enabled"...): harmless
# messages, but errors from another program in Little MCP's log send an expert on a false trail.
open_page() { setsid xdg-open "$URL" >/dev/null 2>&1 < /dev/null & }

if answers; then
    open_page
    exit 0
fi

if [[ ! -x .venv/bin/python ]]; then
    say "Little MCP is not installed in $(pwd). Run ./install.sh there first."
    exit 1
fi

mkdir -p data/logs
printf '\n=== %s: started from the icon ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
# setsid: the app does not stop when the program that clicked the icon ends.
# -u: Python writes to the log at once; buffered, a crash could leave the log empty.
setsid .venv/bin/python -u -m little_mcp >> "$LOG" 2>&1 < /dev/null &
app=$!

# The first start can take a while (the libraries load). Wait up to a minute, and say
# so if the app stops before its page answers, instead of failing without a word.
for _ in $(seq 1 120); do
    if answers; then
        open_page
        exit 0
    fi
    if ! kill -0 "$app" 2>/dev/null; then
        say "Little MCP could not start. What it said is in $(pwd)/$LOG"
        exit 1
    fi
    sleep 0.5
done
say "Little MCP is taking long to start. If no page opens, see $(pwd)/$LOG"
