"""Which code exactly is running: version, commit, and local changes."""

import re
import subprocess

import pytest

from little_mcp import __version__, version


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin", "HOME": str(root)})


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "server.py").write_text("print('v1')\n")
    git(tmp_path, "add", "server.py")
    git(tmp_path, "commit", "-q", "-m", "first")
    version.describe.cache_clear()
    yield tmp_path
    version.describe.cache_clear()


def test_a_clean_clone_shows_version_and_commit(repo):
    assert re.fullmatch(rf"{re.escape(__version__)} · commit [0-9a-f]{{7,}} \(\d{{4}}-\d\d-\d\d\)",
                        version.describe(repo))


def test_edited_code_is_flagged(repo):
    (repo / "server.py").write_text("print('edited by hand')\n")
    assert version.describe(repo).endswith(" + local changes")


def test_new_or_ignored_files_are_not_code_changes(repo):
    (repo / "notes.txt").write_text("my notes\n")  # untracked, like .env or data/
    assert "local changes" not in version.describe(repo)


def test_without_git_only_the_version(tmp_path):
    version.describe.cache_clear()
    assert version.describe(tmp_path) == __version__  # a ZIP download: no .git folder


def test_the_command_line_and_the_log_say_the_same(capsys):
    from little_mcp.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--version"])
    assert capsys.readouterr().out.strip() == f"Little MCP {version.describe()}"
