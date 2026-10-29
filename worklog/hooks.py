"""Install/inspect the Claude Code hooks for an account (shared by CLI and web UI).

One command handles both events; it reads `hook_event_name` from stdin:
  * SessionStart: records which account is logged in (for accounts sharing a folder)
  * SessionEnd:   starts a background collect
"""
from __future__ import annotations

import json
import shlex
import shutil
import sys
from datetime import datetime

from .config import Account

HOOK_MARKER = "claude-worklog hook"
HOOK_EVENTS = ("SessionStart", "SessionEnd")


def _quote(path: str) -> str:
    """Quote a program path for the shell Claude Code runs hooks in (bash, also on Windows)."""
    if sys.platform == "win32":
        return f'"{path}"' if any(c in path for c in ' ()&') else path
    return shlex.quote(path)


def hook_command() -> str:
    exe = shutil.which("claude-worklog")
    return f"{_quote(exe)} hook" if exe else f"{_quote(sys.executable)} -m worklog.cli hook  # {HOOK_MARKER}"


def _is_ours(command: object) -> bool:
    return isinstance(command, str) and (HOOK_MARKER in command or "worklog.cli hook" in command)


def _events_in(settings: object) -> set[str]:
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    found = set()
    for event in HOOK_EVENTS:
        groups = hooks.get(event) if isinstance(hooks, dict) else None
        for group in groups if isinstance(groups, list) else []:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if any(isinstance(h, dict) and _is_ours(h.get("command")) for h in entries or []):
                found.add(event)
    return found


def installed_events(account: Account) -> set[str]:
    """Which of our hook events are present in the account's settings.json."""
    try:
        return _events_in(json.loads((account.config_dir / "settings.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def default_model(account: Account) -> str | None:
    """The `model` the account's settings.json pins (what `/model` saves), or None for the plan's default."""
    try:
        settings = json.loads((account.config_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    model = settings.get("model") if isinstance(settings, dict) else None
    return model.strip()[:100] if isinstance(model, str) and model.strip() else None


def hook_installed(account: Account) -> bool:
    return installed_events(account) >= set(HOOK_EVENTS)


def install_hook(account: Account) -> tuple[bool, str]:
    """Idempotently add the missing hooks. Backs up settings.json first. Returns (ok, message)."""
    path = account.config_dir / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"{account.name}: {path} is not valid JSON ({exc}); nothing changed"
    if not isinstance(settings, dict):
        return False, f"{account.name}: {path} is not a JSON object; nothing changed"
    missing = [ev for ev in HOOK_EVENTS if ev not in _events_in(settings)]
    if not missing:
        return True, f"{account.name}: hooks already installed"
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict) or any(not isinstance(hooks.get(ev, []), list) for ev in missing):
        return False, f"{account.name}: unexpected \"hooks\" layout in {path}; nothing changed"
    if path.exists():
        shutil.copy2(path, path.with_name(f"settings.json.bak-{datetime.now():%Y%m%d%H%M%S}"))
    for ev in missing:
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": hook_command()}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        shutil.copymode(path, tmp)  # keep the original file permissions
    tmp.replace(path)
    return True, f"{account.name}: {', '.join(missing)} hook installed in {path}"
