#!/usr/bin/env bash
# One-step setup for Ubuntu / Debian. Safe to run more than once.
#
#   ./install.sh               everything, including the local chat model
#   ./install.sh --claude-only skip the local chat model (Claude does the chatting)
set -euo pipefail
cd "$(dirname "$0")"

CHAT_MODEL="qwen3:4b"
EMBED_MODEL="nomic-embed-text"
CLAUDE_ONLY=0
[[ "${1:-}" == "--claude-only" ]] && CLAUDE_ONLY=1

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

step "System packages (asks for your password)"
sudo apt-get update -q
sudo apt-get install -y -q git curl python3 python3-venv python3-pip

step "Python environment in .venv"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
# PYTHONWARNINGS hides a harmless but alarming setuptools notice about editable installs.
PYTHONWARNINGS=ignore .venv/bin/pip install -q -e ".[all]"

step "Settings file (.env)"
if [[ -f .env ]]; then
    echo ".env already exists, leaving it as it is."
else
    cp .env.example .env
    echo "Created .env from .env.example."
fi

step "Ollama"
if command -v ollama >/dev/null; then
    echo "Ollama is already installed."
else
    curl -fsSL https://ollama.com/install.sh | sh
fi

step "Local models (this can take a while)"
ollama pull "$EMBED_MODEL"
if [[ $CLAUDE_ONLY == 0 ]]; then
    ollama pull "$CHAT_MODEL"
fi

step "Done!"
cat <<EOF
Next:
  1. Add your keys:      nano .env
  2. Activate the env:   source .venv/bin/activate
  3. Start chatting:     python -m little_mcp$([[ $CLAUDE_ONLY == 1 ]] && echo " --provider anthropic")
EOF
