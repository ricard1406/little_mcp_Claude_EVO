"""Which code exactly is running: the version, and the git commit it was cloned or pulled at.

    Little MCP 0.7.0 · commit fe4c8dd (2026-11-21)
    Little MCP 0.7.0 · commit fe4c8dd (2026-11-21) + local changes
    Little MCP 0.7.0                      (no git: a ZIP download, for example)

The version number changes only at a release; the commit changes with every
change. Two computers that show the same commit, without "local changes", run
the same code.
"""

import functools
import subprocess
from pathlib import Path

from . import __version__, config


def _git(root: Path, *args: str) -> str:
    """The output of a git command in the project folder; "" if git or the repository isn't there."""
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):  # git not installed, or stuck
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


@functools.cache
def describe(root: Path = config.PROJECT_ROOT) -> str:
    """'0.7.0 · commit fe4c8dd (2026-11-21)', with '+ local changes' if a tracked file was edited."""
    commit = _git(root, "log", "-1", "--format=%h (%cs)")
    if not commit:
        return __version__
    # Only files git knows: a new notes.txt, or .env and data/ (ignored), are not "changes to the code".
    edited = _git(root, "status", "--porcelain", "--untracked-files=no")
    return f"{__version__} · commit {commit}" + (" + local changes" if edited else "")
