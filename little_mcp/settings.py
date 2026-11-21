"""The settings a user can change from the web page, saved in the project's .env file.

The page writes the same file a user would edit by hand, so both ways keep
working: nano .env, or the ⚙️ Settings tab. A command-line option, such as
--provider, still wins over .env when the app starts.

Secrets (API keys) never travel back to the browser. The page shows only their
last 4 characters, and an empty field on Save means "keep the saved key".
"""

import os
import shutil
from pathlib import Path

import requests
from dotenv import set_key

from . import config

ENV_FILE = config.PROJECT_ROOT / ".env"

PROVIDERS = {
    "anthropic": "Claude (needs an API key, fast and accurate)",
    "ollama": "Local model with Ollama (no key, slow without a graphics card)",
}

# Names in .env. The LITTLE_MCP_ ones are new with the Settings tab; the keys are the usual ones.
PROVIDER = "LITTLE_MCP_PROVIDER"
ASK = "LITTLE_MCP_ASK"
SHOW_TOOLS = "LITTLE_MCP_SHOW_TOOLS"
CLAUDE_KEY = "ANTHROPIC_API_KEY"
WEATHER_KEY = "OPENWEATHER_API_KEY"


def get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def show_tools() -> bool:
    """Whether the chat shows each tool call and its result (yes by default: it is how you learn what the AI does)."""
    return get(SHOW_TOOLS, "yes").lower() not in ("no", "false", "0")


def default_provider(ollama_model: str) -> str:
    """The model to start with when no --provider is given.

    The one saved in Settings; otherwise Claude if there is a key, the local model if
    it is downloaded, and Claude again if nothing is set up: the welcome then asks for
    a key, instead of preselecting a local model that isn't there.
    """
    saved = get(PROVIDER).lower()
    if saved in PROVIDERS:
        return saved
    if get(CLAUDE_KEY):
        return "anthropic"
    return "ollama" if setup_problem("ollama", ollama_model) == "" else "anthropic"


def mask(secret: str) -> str:
    """'sk-ant-api03-...7f2a' -> '••••7f2a': enough to recognise a key, not to use it."""
    secret = secret.strip()
    if not secret:
        return ""
    return "••••" + secret[-4:] if len(secret) > 8 else "••••"


def save(values: dict[str, str], path: Path = ENV_FILE) -> None:
    """Write the values into .env, keeping every other line and comment as it was.

    The running app sees them at once too (os.environ), including the MCP server
    it starts next. The file is made readable by its owner only: it holds keys.
    """
    # A pasted key often ends with a line break: trim the ends, refuse one in the middle,
    # which would write a second setting into the file.
    values = {name: value.strip() for name, value in values.items()}
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} cannot contain a line break.")
    if not path.exists():
        example = path.with_name(".env.example")
        if example.exists():
            shutil.copyfile(example, path)
        else:
            path.touch()
    for name, value in values.items():
        set_key(path, name, value.strip(), quote_mode="auto")
        os.environ[name] = value.strip()
    path.chmod(0o600)


def check_claude_key(key: str) -> tuple[bool, str]:
    """Ask Anthropic whether the key works, with a request that costs nothing.

    Listing the models uses no tokens. It cannot tell whether there is credit
    left: the Console's Billing page shows that.
    """
    import anthropic

    if not key.strip():
        return False, "There is no key to test. Paste one first."
    try:
        anthropic.Anthropic(api_key=key.strip(), max_retries=0, timeout=10).models.list(limit=1)
    except anthropic.AuthenticationError:
        return False, "❌ Anthropic refused this key. Check that you copied all of it."
    except anthropic.PermissionDeniedError:
        return False, "❌ This key is valid, but not allowed to use the API."
    except anthropic.APIConnectionError:
        return False, "❌ Could not reach Anthropic. Is this computer online?"
    except anthropic.APIStatusError as error:
        return False, f"❌ Anthropic answered with an error ({error.status_code}). Try again in a minute."
    return True, "✅ The key works. (This test is free. Your credit is shown on console.anthropic.com → Billing.)"


def ollama_models() -> list[str] | None:
    """The models Ollama has downloaded, or None if Ollama is not running here."""
    host = get("OLLAMA_HOST", "127.0.0.1:11434")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        response = requests.get(f"{host}/api/tags", timeout=2)
        response.raise_for_status()
        return [m["name"] for m in response.json().get("models", [])]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def setup_problem(provider: str, ollama_model: str) -> str:
    """What stops the chat from working with this provider, in plain words; "" if nothing does."""
    if provider == "anthropic":
        if not get(CLAUDE_KEY):
            return "Claude needs an API key. Paste yours below (from console.anthropic.com → API Keys)."
        return ""
    models = ollama_models()
    if models is None:
        return ("Ollama is not running on this computer. Run ./install.sh to install it, "
                "or choose Claude and paste an API key.")
    wanted = ollama_model if ":" in ollama_model else ollama_model + ":latest"
    if wanted not in models:
        return (f"The local model {ollama_model} is not downloaded. Run: ollama pull {ollama_model} "
                "(about 2.5 GB), or choose Claude and paste an API key.")
    return ""
