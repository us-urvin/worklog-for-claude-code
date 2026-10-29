"""Shared fixtures: every test runs in a throwaway HOME with its own state dir and data repo."""
import subprocess
from pathlib import Path

import pytest

from worklog import cli, identity, store


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway HOME; state and logs are redirected into it too."""
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    for var, sub in (("HOME", ""), ("USERPROFILE", ""), ("XDG_CONFIG_HOME", ".config"),
                     ("XDG_STATE_HOME", ".local/state"), ("XDG_DATA_HOME", ".local/share"),
                     ("APPDATA", "AppData/Roaming"), ("LOCALAPPDATA", "AppData/Local")):
        monkeypatch.setenv(var, str(h / sub) if sub else str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    state = h / ".local/state/claude-worklog"
    for mod in (identity, store, cli):
        monkeypatch.setattr(mod, "STATE_DIR", state)
    assert Path.home() == h  # guard: never run against the real home
    return h


@pytest.fixture
def repo(home):
    r = home / "data"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


