"""The ⚙️ Settings tab's helpers: saving .env, masking keys, testing a key, telling what is missing."""

import os
import stat

import httpx
import pytest
from dotenv import dotenv_values

from little_mcp import settings
from little_mcp.cli import parse_args

NAMES = ("ANTHROPIC_API_KEY", "OPENWEATHER_API_KEY", "LITTLE_MCP_PROVIDER", "LITTLE_MCP_ASK")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    # save() writes os.environ too: setting them here makes monkeypatch put them back afterwards.
    for name in NAMES:
        monkeypatch.setenv(name, "")


def test_mask_shows_only_the_end():
    assert settings.mask("sk-ant-api03-abcdefghij-7f2a") == "••••7f2a"
    assert settings.mask("short") == "••••"
    assert settings.mask("  ") == ""


def test_save_keeps_comments_and_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# my comment\nDB_BACKEND=tinysql\nANTHROPIC_API_KEY=old\n")
    settings.save({"ANTHROPIC_API_KEY": "sk-new", "LITTLE_MCP_PROVIDER": "anthropic"}, env)
    text = env.read_text()
    assert text.startswith("# my comment\nDB_BACKEND=tinysql\n") and "old" not in text
    # Read back the way the app reads it (a value with "-" is written in quotes, which is fine).
    assert dotenv_values(env) == {"DB_BACKEND": "tinysql", "ANTHROPIC_API_KEY": "sk-new",
                                  "LITTLE_MCP_PROVIDER": "anthropic"}
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-new"  # the running app sees it at once
    assert stat.S_IMODE(env.stat().st_mode) == 0o600  # it holds keys: only its owner can read it


def test_save_starts_from_the_example_when_there_is_no_env(tmp_path):
    (tmp_path / ".env.example").write_text("# Copy this file\nEMBED_MODEL=nomic-embed-text\n")
    settings.save({"LITTLE_MCP_ASK": "never"}, tmp_path / ".env")
    text = (tmp_path / ".env").read_text()
    assert text.startswith("# Copy this file") and "LITTLE_MCP_ASK=never" in text


def test_save_refuses_a_line_break(tmp_path):
    # "key\nOTHER=x" would write a second setting into the file.
    with pytest.raises(ValueError, match="line break"):
        settings.save({"ANTHROPIC_API_KEY": "sk\nLITTLE_MCP_ASK=never"}, tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


class FakeModels:
    def __init__(self, error):
        self.error = error

    def list(self, limit):
        if self.error:
            raise self.error


def fake_anthropic(monkeypatch, error=None):
    import anthropic

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels(error)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)


def test_checking_a_good_key(monkeypatch):
    fake_anthropic(monkeypatch)
    ok, text = settings.check_claude_key("sk-good")
    assert ok and "works" in text and "Billing" in text


def test_checking_a_bad_key(monkeypatch):
    import anthropic

    request = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    error = anthropic.AuthenticationError("bad", response=httpx.Response(401, request=request), body=None)
    fake_anthropic(monkeypatch, error)
    ok, text = settings.check_claude_key("sk-bad")
    assert not ok and "refused" in text


def test_checking_without_a_key():
    ok, text = settings.check_claude_key("  ")
    assert not ok and "no key" in text


def test_what_stops_claude(monkeypatch):
    assert "API key" in settings.setup_problem("anthropic", "qwen3:4b")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert settings.setup_problem("anthropic", "qwen3:4b") == ""


@pytest.mark.parametrize("models, problem", [
    (None, "not running"),
    (["nomic-embed-text:latest"], "not downloaded"),
    (["qwen3:4b", "nomic-embed-text:latest"], ""),
])
def test_what_stops_the_local_model(monkeypatch, models, problem):
    monkeypatch.setattr(settings, "ollama_models", lambda: models)
    found = settings.setup_problem("ollama", "qwen3:4b")
    assert (problem in found) if problem else found == ""


def test_the_saved_choices_are_the_defaults_and_the_command_line_wins(monkeypatch):
    monkeypatch.setenv("LITTLE_MCP_PROVIDER", "anthropic")
    monkeypatch.setenv("LITTLE_MCP_ASK", "except-inserts")
    assert parse_args([]).provider == "anthropic"
    assert parse_args([]).ask == "except-inserts"
    assert parse_args(["--provider", "ollama", "--ask", "never"]).provider == "ollama"
    monkeypatch.setenv("LITTLE_MCP_PROVIDER", "gpt")  # not a choice: ignored
    assert parse_args([]).provider == "ollama"
