"""Which Claude account was logged in for each session (several accounts, one config folder).

Claude Code keeps the logged-in account in the `oauthAccount` object of its global
config file (~/.claude.json for the default folder, <CLAUDE_CONFIG_DIR>/.claude.json
otherwise). We read only `accountUuid` and `emailAddress` from it. Tokens live in a
different file (.credentials.json) that this module never opens.

The SessionStart hook stores session_id -> account id in a LOCAL state file
(~/.local/state/claude-worklog/accounts.json, mode 600). The collector later uses it to
pick the configured account name for each session. Raw ids and e-mails stay in that
file (and in config.toml if you match by them); only the account NAME reaches records.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import STATE_DIR, _expand, home_relative

log = logging.getLogger(__name__)
STATE_VERSION = 1
KEEP_DAYS = 120  # longer than Claude Code's default transcript retention
_MAX_CONFIG_BYTES = 20_000_000


@dataclass(frozen=True)
class Identity:
    account_id: str
    email: str | None


# ------------------------------------------------------------------ reading
def default_claude_dir() -> Path:
    return (Path.home() / ".claude").resolve()


def identity_path(config_dir: Path, explicit: bool = False) -> Path:
    """Global config file Claude Code uses for `config_dir`.

    `explicit` means CLAUDE_CONFIG_DIR was set; then the file is always inside it.
    Without it, the default ~/.claude folder keeps the file next to it at ~/.claude.json.
    """
    if not explicit and config_dir.resolve() == default_claude_dir():
        return Path.home() / ".claude.json"
    return config_dir / ".claude.json"


def read_identity(path: Path) -> Identity | None:
    """The logged-in account from a Claude Code global config file, or None.

    Never logs the values it reads.
    """
    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            log.warning("%s is unexpectedly large; not reading account", path)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("cannot read account from %s: %s", path, type(exc).__name__)
        return None
    oa = data.get("oauthAccount") if isinstance(data, dict) else None
    if not isinstance(oa, dict):
        return None  # logged out, or an API-key login without an OAuth account
    uid = oa.get("accountUuid")
    if not isinstance(uid, str) or not uid.strip() or len(uid) > 200:
        return None
    email = oa.get("emailAddress")
    email = email.strip() if isinstance(email, str) and "@" in email and len(email) <= 254 else None
    return Identity(uid.strip(), email)


def hook_config_dir() -> tuple[Path, bool]:
    """(config folder of the running Claude Code session, whether it was set explicitly)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return (_expand(env), True) if env else (default_claude_dir(), False)


# ------------------------------------------------------------------ privacy helpers
def ref(account_id: str) -> str:
    """Short opaque handle for an account id, safe to put in HTML forms and logs."""
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:12]


def mask_email(email: str | None) -> str:
    """alice@example.com -> a***@example.com (for display only)."""
    if not email or "@" not in email:
        return "(no e-mail)"
    local, _, domain = email.rpartition("@")
    return f"{local[:1]}***@{domain}"


# ------------------------------------------------------------------ state file
def state_path() -> Path:
    return STATE_DIR / "accounts.json"


def _empty() -> dict:
    return {"version": STATE_VERSION, "sessions": {}, "logins": {}}


def load_state(path: Path | None = None) -> dict:
    """Read the state file. Missing or damaged files give an empty state (logged)."""
    path = path or state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    state = _empty()
    for key in ("sessions", "logins"):
        if isinstance(data.get(key), dict):
            state[key] = {k: v for k, v in data[key].items() if isinstance(k, str) and isinstance(v, dict)}
    return state


def _save_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never world-readable, even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


@contextmanager
def _lock(path: Path, timeout: float):
    """Short exclusive lock for the state file (separate from the store lock, which can be held long)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as fh:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() > deadline:
                    raise TimeoutError("account state is locked") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _prune(state: dict, now: datetime) -> None:
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    state["sessions"] = {k: v for k, v in state["sessions"].items() if str(v.get("at", "")) >= cutoff}


def record_session(session_id: str, config_dir: Path, ident: Identity | None, event: str,
                   path: Path | None = None, now: datetime | None = None, timeout: float = 2.0) -> None:
    """Remember which account a session runs under.

    event "start": the account at SessionStart. The first start wins, so a later resume or
    compaction of the same session can't re-assign it.
    event "end": stored separately; used only if no start was recorded (sessions that were
    already running when the hook was installed). A different account at end is logged.
    """
    path = path or state_path()
    now = now or datetime.now().astimezone()
    stamp = now.isoformat(timespec="seconds")
    with _lock(path, timeout):
        state = load_state(path)
        entry = state["sessions"].setdefault(session_id, {"dir": home_relative(config_dir), "at": stamp})
        if ident is not None:
            key = "id" if event == "start" else "end_id"
            if entry.get(key) is None:
                entry[key] = ident.account_id
            if event == "end" and entry.get("id") not in (None, ident.account_id):
                log.warning("session %s: account changed during the session (%s -> %s); "
                            "counted under the account it started with",
                            session_id[:8], ref(entry["id"]), ref(ident.account_id))
            login = state["logins"].setdefault(ident.account_id, {"first_seen": stamp, "dirs": []})
            login["last_seen"] = stamp
            if ident.email:
                login["email"] = ident.email
            d = home_relative(config_dir)
            if d not in login.get("dirs", []):
                login["dirs"] = sorted({*login.get("dirs", []), d})
        _prune(state, now)
        _save_state(state, path)


def session_logins(path: Path | None = None) -> dict[str, Identity]:
    """session_id -> Identity for every session with a recorded account."""
    state = load_state(path)
    logins = state["logins"]
    out = {}
    for sid, entry in state["sessions"].items():
        uid = entry.get("id") or entry.get("end_id")
        if isinstance(uid, str) and uid:
            email = (logins.get(uid) or {}).get("email")
            out[sid] = Identity(uid, email if isinstance(email, str) else None)
    return out


def detected_logins(path: Path | None = None) -> list[dict]:
    """Accounts seen logged in, newest first: {ref, account_id, email, dirs, last_seen}."""
    rows = []
    for uid, info in load_state(path)["logins"].items():
        dirs = [_expand(d) for d in info.get("dirs", []) if isinstance(d, str)]
        email = info.get("email") if isinstance(info.get("email"), str) else None
        rows.append({"ref": ref(uid), "account_id": uid, "email": email, "dirs": dirs,
                     "last_seen": str(info.get("last_seen", ""))})
    return sorted(rows, key=lambda r: r["last_seen"], reverse=True)


# ------------------------------------------------------------------ hook entry point
def note_session(session_id: object, event: str) -> None:
    """Called from the hook. Records the current login; never raises."""
    try:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            log.warning("%s hook without a usable session_id; account not recorded", event)
            return
        config_dir, explicit = hook_config_dir()
        ident = read_identity(identity_path(config_dir, explicit))
        if ident is None:
            log.info("session %s: no logged-in Claude account found for %s", session_id[:8], home_relative(config_dir))
        record_session(session_id, config_dir, ident, event)
        log.info("session %s %s: account %s", session_id[:8], event, ref(ident.account_id) if ident else "unknown")
    except Exception:  # noqa: BLE001 - a tracking hook must never break Claude Code
        log.exception("recording the account for a session failed")
