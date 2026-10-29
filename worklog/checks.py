"""The setup checklist: what Worklog needs, checked live, with simple steps for the user's own system.

Required checks must all pass before Worklog can do its job; tips make it better. The web UI
shows the full checklist until every required check has passed once, then only a slim warning
for a check that breaks later. Everything here only looks; nothing is changed.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import STATE_DIR, Config, home_relative

CLAUDE_INSTALL_URL = "https://code.claude.com/docs"
GUIDE_URL = "https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md"
CACHE_SECONDS = 30.0


@dataclass
class Check:
    id: str
    label: str  # what it is, in plain words
    ok: bool
    required: bool
    why: str = ""  # one sentence: why it matters
    steps: list[tuple[str, str]] = field(default_factory=list)  # (plain text, command or "") for this OS
    link: tuple[str, str] | None = None  # (text, url)
    action: str = ""  # a web UI action that fixes it, e.g. "install_hook:work"


def os_kind() -> str:
    """"linux", "wsl", "mac" or "windows"."""
    system = platform.system()
    if system == "Darwin":
        return "mac"
    if system == "Windows":
        return "windows"
    try:
        if "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


OS_NAMES = {"linux": "Linux", "wsl": "Windows (WSL)", "mac": "macOS", "windows": "Windows"}


# ------------------------------------------------------------------ steps per OS
def _git_steps(kind: str) -> list[tuple[str, str]]:
    if kind == "mac":
        return [("Open the Terminal app and run this. A window asks to install the developer tools: click Install.",
                 "xcode-select --install")]
    if kind == "windows":
        return [("Open PowerShell and run this, then close and reopen PowerShell.", "winget install --id Git.Git -e"),
                ("No winget? Download Git from git-scm.com and click Next through the installer.", "")]
    return [("Open a terminal and run this (Ubuntu, Debian, WSL). It asks for your password.",
             "sudo apt update && sudo apt install -y git"),
            ("Fedora: ", "sudo dnf install -y git")]


def _data_steps(kind: str, store: Path) -> list[tuple[str, str]]:
    where = home_relative(store)
    if kind == "windows":
        return [("In PowerShell, create the folder your log is saved in:", f'git init "{store}"')]
    return [("In a terminal, create the folder your log is saved in:", f"git init {where}")]


def _service_steps(kind: str) -> list[tuple[str, str]]:
    steps = [("Run this once. Worklog then starts by itself every time you log in.", "claude-worklog install-service")]
    if kind == "wsl":
        steps.append(("On WSL, systemd must be on: add these two lines to /etc/wsl.conf, then run "
                      "`wsl --shutdown` in Windows and reopen Ubuntu.", "[boot]\nsystemd=true"))
    return steps


# ------------------------------------------------------------------ the checks
def _claude_folders(cfg: Config | None) -> list[Path]:
    dirs = [a.config_dir for a in cfg.accounts] if cfg else []
    return dirs or [Path.home() / ".claude"]


def run(cfg: Config | None, watcher_running: bool = False, kind: str | None = None) -> list[Check]:
    """All checks, required first. With no config yet (first run), only the basics."""
    from . import services
    from .hooks import hook_installed
    kind = kind or os_kind()
    out: list[Check] = []

    claude = shutil.which(cfg.claude_bin if cfg else "claude")
    out.append(Check("claude", "Claude Code is installed", bool(claude), True,
                     "Worklog reads what Claude Code writes down while you work.",
                     [("Install Claude Code with its official guide, then open a new terminal and run `claude`.", "")],
                     ("Claude Code install guide", CLAUDE_INSTALL_URL)))
    used = any((d / "projects").is_dir() for d in _claude_folders(cfg))
    out.append(Check("used", "Claude Code has been used at least once", used, True,
                     "Worklog needs a first session to find Claude Code's files.",
                     [("Open a terminal, run this, log in, and ask Claude anything.", "claude")]))
    out.append(Check("git", "Git is installed", bool(shutil.which("git")), True,
                     "Your work log is saved with git, so it keeps a history and can sync between computers.",
                     _git_steps(kind)))
    if cfg is None:
        return out

    out.append(Check("data", "The folder for your log is ready", (cfg.store_repo / ".git").is_dir(), True,
                     f"Worklog saves each day in {home_relative(cfg.store_repo)}.", _data_steps(kind, cfg.store_repo)))
    for a in cfg.accounts:
        hooked = hook_installed(a)
        out.append(Check(f"hook:{a.name}", f"Sessions of {a.name} are tracked", hooked or watcher_running, True,
                         "The hook tells Worklog when a session ends. While Worklog runs, it also notices account "
                         "switches on its own.",
                         [("Click the button, or run:", f"claude-worklog install-hook --account {a.name}")],
                         action="" if hooked else f"install_hook:{a.name}"))
    auto = services.autostart_ok()
    if auto is not None:
        out.append(Check("autostart", "Worklog starts by itself when you log in", auto, True,
                         "So your log keeps filling up after a restart, without you doing anything.",
                         _service_steps(kind)))

    email = _git_out(["git", "config", "--global", "user.email"])
    out.append(Check("gitemail", "Your git e-mail is set (shows your commits)", bool(email), False,
                     "Worklog lists the commits you made each day, matched by this e-mail.",
                     [("Run this with the e-mail you use for commits:",
                       'git config --global user.email "you@example.com"')]))
    remote = _git_out(["git", "-C", str(cfg.store_repo), "remote"]) if (cfg.store_repo / ".git").is_dir() else ""
    out.append(Check("remote", "Several computers: your log syncs to a private repo", bool(remote), False,
                     "Only needed if you use Claude Code on more than one computer.",
                     [("See the guide: create a private repo and connect this folder to it.", "")],
                     ("Full setup guide", GUIDE_URL + "#full-setup")))
    strong = bool(cfg.summary_model) or not cfg.use_ai
    out.append(Check("model", "Summaries are written by a strong model", strong, False,
                     "Pick Opus under Settings → Summaries for the most accurate daily summaries.",
                     [("Open Settings → Summaries and choose Opus.", "")]))
    return out


def _git_out(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


# ------------------------------------------------------------------ cache + "done once" memory
_cache: dict[tuple, tuple[float, list[Check]]] = {}
_cache_lock = threading.Lock()


def cached(cfg: Config | None, watcher_running: bool = False) -> list[Check]:
    """`run` at most every CACHE_SECONDS (it calls git and the service manager)."""
    key = (watcher_running,) + ((str(cfg.path), str(cfg.store_repo), cfg.claude_bin, cfg.summary_model, cfg.use_ai,
                                 tuple((a.name, str(a.config_dir)) for a in cfg.accounts)) if cfg else ())
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    checks = run(cfg, watcher_running)
    with _cache_lock:
        _cache.clear()
        _cache[key] = (now, checks)
    return checks


def forget_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _ui_state_path() -> Path:
    return STATE_DIR / "ui.json"


def ui_flag(name: str, path: Path | None = None) -> bool:
    """A remembered yes/no on this device (setup_done, star_asked)."""
    try:
        return bool(json.loads((path or _ui_state_path()).read_text(encoding="utf-8")).get(name))
    except (OSError, ValueError, AttributeError):
        return False


def set_ui_flag(name: str, path: Path | None = None) -> None:
    path = path or _ui_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    if data.get(name):
        return
    data[name] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def setup_done(path: Path | None = None) -> bool:
    """True once every required check has passed at least once on this device."""
    return ui_flag("setup_done", path)


def mark_setup_done(path: Path | None = None) -> None:
    set_ui_flag("setup_done", path)
