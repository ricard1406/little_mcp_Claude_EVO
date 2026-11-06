#!/usr/bin/env bash
# Guided setup for Ubuntu / Debian.
#
# It explains every step and asks before downloading or installing anything.
# Steps that are already done are skipped, so it is safe to run again.
#
#   ./install.sh        ask before each step
#   ./install.sh --yes  accept every step without asking (for automation)
set -euo pipefail
cd "$(dirname "$0")"

CHAT_MODEL="qwen3:4b"
EMBED_MODEL="nomic-embed-text"
ASSUME_YES=0
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && ASSUME_YES=1

title() { printf '\n\033[1;34m%s\033[0m\n' "$1"; }

# ask "question" y|n  -> success if the answer is yes; y/n is the default for Enter.
ask() {
    local reply hint="[y/N]"
    [[ $2 == y ]] && hint="[Y/n]"
    if [[ $ASSUME_YES == 1 ]]; then
        echo "$1 $hint yes"
        return 0
    fi
    read -r -p "$1 $hint " reply || reply=""
    reply="${reply:-$2}"
    [[ ${reply,,} == y* || ${reply,,} == s* ]]  # yes / sì
}

APT_UPDATED=0
apt_install() {
    if [[ $APT_UPDATED == 0 ]]; then
        sudo apt-get update -q
        APT_UPDATED=1
    fi
    sudo apt-get install -y -q --no-install-recommends "$@"
}

ollama_ready() {
    for _ in $(seq 1 30); do
        ollama list >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

pull_model() {  # pull_model name "description" default
    if ollama list 2>/dev/null | grep -q "^$1"; then
        echo "$1 is already downloaded."
    elif ask "Download $1 ($2)?" "$3"; then
        ollama pull "$1"
    else
        echo "Skipped $1."
    fi
}

cat <<EOF
Little MCP EVO setup
====================
This script can do the following steps. It asks you before each one.

  1. Python venv support   system package, about 5 MB     (asks for your password)
  2. Python libraries      about 200 MB download, about 900 MB on disk, in ./.venv
  3. Settings file         creates .env, where you put your API keys
  4. Ollama (optional)     runs AI models on this computer; a large download,
                           often more than 1 GB, plus a background service
  5. Ollama models (optional)
       $EMBED_MODEL     about 270 MB   for searching your documents
       $CHAT_MODEL             about 2.5 GB   for chatting locally, without Claude

Steps 1-3 are required. If you have a Claude API key, you can skip steps 4-5:
everything works except searching your documents, which needs $EMBED_MODEL.
EOF
ask "Start?" y || exit 0

title "1. Python venv support"
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c "import ensurepip" 2>/dev/null || missing+=(python3-venv)
if ((${#missing[@]} == 0)); then
    echo "Already installed."
elif ask "Install ${missing[*]} with apt?" y; then
    apt_install "${missing[@]}"
else
    echo "The project can't run without it. Stopping here."
    exit 1
fi

title "2. Python libraries"
do_install=0
if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import little_mcp" 2>/dev/null; then
    echo "Already installed in .venv."
    if ask "Check for updates (small download if nothing changed)?" n; then do_install=1; fi
else
    ask "Download and install them (about 200 MB)?" y || { echo "The project can't run without them. Stopping here."; exit 1; }
    do_install=1
fi
if [[ $do_install == 1 ]]; then
    [[ -d .venv ]] || python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    # PYTHONWARNINGS hides a harmless but alarming setuptools notice about editable installs.
    PYTHONWARNINGS=ignore .venv/bin/pip install -q -e ".[all]"
    echo "Done."
fi

title "3. Settings file"
if [[ -f .env ]]; then
    echo ".env already exists. I left it as it is."
else
    cp .env.example .env
    echo "Created .env. You will add your API keys there."
fi

title "4. Ollama (optional)"
if command -v ollama >/dev/null; then
    echo "Already installed."
elif ask "Install Ollama (often more than 1 GB)?" n; then
    if ! command -v curl >/dev/null; then
        ask "Ollama's installer needs curl (small system package). Install it?" y && apt_install curl
    fi
    if command -v curl >/dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh
    else
        echo "Skipped Ollama, because curl is not installed."
    fi
else
    echo "Skipped. Use the chat with a Claude API key."
fi

if command -v ollama >/dev/null; then
    title "5. Ollama models (optional)"
    echo "Waiting for the Ollama service..."
    if ollama_ready; then
        pull_model "$EMBED_MODEL" "about 270 MB, for document search" y
        pull_model "$CHAT_MODEL" "about 2.5 GB, for chatting without Claude" n
    else
        echo "Ollama is installed but not running. Start it with:"
        echo "    sudo systemctl start ollama"
        echo "then run ./install.sh again to download the models."
    fi
fi

title "All set!"
start="python -m little_mcp"
if ! (command -v ollama >/dev/null && ollama list 2>/dev/null | grep -q "^$CHAT_MODEL"); then
    start="python -m little_mcp --provider anthropic"
fi
cat <<EOF
Next:
  1. Add your keys:           nano .env
  2. Activate the project:    source .venv/bin/activate
  3. Start chatting:          $start

Please read DISCLAIMER.md: it explains the sample data and where your data goes.
EOF
