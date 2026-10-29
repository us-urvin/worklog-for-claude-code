"""Tell people when a new version is out, and update in one click.

The check is one small request to PyPI's public JSON API, at most once a day, made by the
local service (never by the browser). Nothing about you or your work is sent. Turn it off
with Settings → "Check for updates" (`[ui] check_updates = false`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from . import __version__
from .config import STATE_DIR

log = logging.getLogger(__name__)

PACKAGE = "worklog-for-claude-code"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CHANGELOG_URL = "https://github.com/us-urvin/worklog-for-claude-code/blob/main/CHANGELOG.md"
CHECK_EVERY = 24 * 3600  # seconds


def _state_path() -> Path:
    return STATE_DIR / "update.json"


def parse(v: str) -> tuple[int, ...]:
    """"0.3.0" -> (0, 3, 0). Pre-releases ("0.4.0rc1", "0.4.0.dev1") count as older than the release."""
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", v.strip())
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    return nums + ((-1,) if m.group(2) else (0,))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse(latest) > parse(current)


def fetch_latest(timeout: float = 5.0) -> str | None:
    """The newest release on PyPI (skips yanked and pre-releases), or None when offline."""
    try:
        req = Request(PYPI_URL, headers={"Accept": "application/json", "User-Agent": f"claude-worklog/{__version__}"})
        with urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
            data = json.loads(r.read(2_000_000).decode("utf-8"))
    except (OSError, ValueError) as exc:
        log.info("update check failed (offline?): %s", exc)
        return None
    latest = str((data.get("info") or {}).get("version") or "")
    return latest if re.match(r"^\d+(\.\d+)*$", latest) else None


def load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def check(force: bool = False, now: float | None = None) -> str | None:
    """Ask PyPI if the last check is older than a day; returns the newest version known."""
    now = now or time.time()
    state = load_state()
    if force or now - float(state.get("checked_at", 0)) >= CHECK_EVERY:
        latest = fetch_latest()
        state["checked_at"] = now
        if latest:
            state["latest"] = latest
        _save_state(state)
    return state.get("latest")


def available() -> str | None:
    """A newer version than the running one, from the last check (no network)."""
    latest = load_state().get("latest")
    return latest if isinstance(latest, str) and is_newer(latest) else None


def note_updated(version: str) -> None:
    state = load_state()
    state["just_updated"] = version
    _save_state(state)


def pop_updated(current: str = __version__) -> str | None:
    """The version we just updated to, once, and only to that version (the old process must not use it up)."""
    state = load_state()
    if state.get("just_updated") != current:
        return None
    state.pop("just_updated")
    _save_state(state)
    return current


def dismiss(version: str) -> None:
    state = load_state()
    state["dismissed"] = version
    _save_state(state)


def dismissed(version: str) -> bool:
    return load_state().get("dismissed") == version


# ------------------------------------------------------------------ how to update this install
def install_kind() -> str:
    """"pipx", "venv" (pip in a virtual environment), "dev" (a source checkout) or "system"."""
    here = Path(__file__).resolve()
    if "site-packages" not in here.parts and "dist-packages" not in here.parts:
        return "dev"
    prefix = Path(sys.prefix).as_posix()
    if "/pipx/venvs/" in prefix or "\\pipx\\venvs\\" in sys.prefix:
        return "pipx"
    if sys.prefix != sys.base_prefix:
        return "venv"
    return "system"


def upgrade_command(kind: str | None = None) -> list[str] | None:
    """The command that upgrades this very install, or None when it should be done by hand."""
    kind = kind or install_kind()
    if kind == "pipx":
        pipx = shutil.which("pipx")
        return [pipx, "upgrade", PACKAGE] if pipx else [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]
    if kind == "venv":
        return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]
    return None  # a source checkout, or a system Python we shouldn't touch


def manual_command(kind: str | None = None) -> str:
    kind = kind or install_kind()
    if kind == "dev":
        return "git pull"
    if kind == "system":
        return f"python3 -m pip install --user --upgrade {PACKAGE}"
    return f"pipx upgrade {PACKAGE}"


def upgrade(timeout: int = 300) -> tuple[bool, str]:
    """Run the upgrade. Returns (ok, message)."""
    cmd = upgrade_command()
    if cmd is None:
        return False, f"Update this install by hand: {manual_command()}"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Update failed: {exc}. Try by hand: {manual_command()}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        return False, f"Update failed ({tail[0][:200]}). Try by hand: {manual_command()}"
    return True, "Updated."


def installed_version() -> str:
    """The version now on disk (after an upgrade it differs from the running __version__)."""
    try:
        r = subprocess.run([sys.executable, "-c", "import worklog; print(worklog.__version__)"],
                           capture_output=True, text=True, timeout=30, cwd=str(Path.home()))
        return r.stdout.strip() or __version__
    except (OSError, subprocess.TimeoutExpired):
        return __version__


def restart_server(port: int, close) -> None:
    """Replace this web server process with a fresh one running the new code.

    `close` shuts the listening sockets first. On Linux and macOS exec keeps the process id,
    so systemd and launchd keep supervising it; on Windows a detached new process takes over.
    """
    args = [sys.executable, "-m", "worklog.cli", "serve", "--port", str(port)]
    if sys.platform == "win32":
        close()  # free the port for the new process (on Unix, exec closes the sockets itself)
        from .services import _detached
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **_detached())
        os._exit(0)
    os.execv(sys.executable, args)
