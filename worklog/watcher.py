"""Notice Claude logins as they happen, so sessions get the right account even without the hook.

Claude Code's transcripts don't say which account ran a session. The hook records it at
SessionStart, but people forget it, and one-click account switchers change the login
without telling anyone. Every switching method has to change the logged-in account in
the folder's `.claude.json`, so this module watches those files instead:

* a **timeline** per Claude folder: when the login changed and to whom;
* **coverage**: the time spans the watcher was running, so a gap is never mistaken for
  "nothing changed";
* **discovery** of Claude folders that aren't configured yet, and of hooks that vanished.

The collector asks `login_at(folder, start)` for sessions the hook didn't record. Only the
account id and e-mail are read (via `identity.read_identity`); credentials are never opened.
Everything stays in a local state file (mode 600), like the hook's.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR, STATE_DIR, _expand, home_relative
from .identity import KEEP_DAYS, Identity, _lock, _save_state, default_claude_dir, identity_path, read_identity

log = logging.getLogger(__name__)

TICK = 3.0  # seconds between checks of the account files
DISCOVER_EVERY = 60.0  # seconds between looks for new folders and missing hooks
FLUSH_EVERY = 30.0  # how often the "still running" end of the coverage span is saved
AMBIGUOUS = timedelta(seconds=2 * TICK)  # a login change this close to a session start is a coin toss
SLEEP_GAP = timedelta(seconds=90)  # a longer pause between ticks (suspend) starts a new coverage span
DISCOVER_BUDGET = 0.2  # seconds a discovery run may spend walking folders
_SKIP_DIRS = {"node_modules", ".git", ".cache", "cache", "caches", "Cache", "Caches", "__pycache__", ".venv",
              "venv", "site-packages", "Trash", ".Trash", "projects", "claude-worklog", "claude-worklog-data"}
STATE_VERSION = 1


def state_path() -> Path:
    return STATE_DIR / "watch.json"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(s: object) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ state file
def _empty() -> dict:
    return {"version": STATE_VERSION, "timeline": {}, "coverage": [], "folders": [], "hooked": [],
            "dismissed": [], "notes": []}


def load_state(path: Path | None = None) -> dict:
    """Read the watcher state. Missing or damaged files give an empty state."""
    path = path or state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return _empty()
    state = _empty()
    if isinstance(data, dict):
        if isinstance(data.get("timeline"), dict):
            state["timeline"] = {k: [e for e in v if isinstance(e, dict) and _parse(e.get("at"))]
                                 for k, v in data["timeline"].items() if isinstance(k, str) and isinstance(v, list)}
        for key in ("coverage", "folders", "hooked", "dismissed", "notes"):
            if isinstance(data.get(key), list):
                state[key] = data[key]
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or state_path()
    with _lock(path, 2.0):
        _save_state(state, path)


def _prune(state: dict, now: datetime) -> None:
    cutoff = now - timedelta(days=KEEP_DAYS)
    for d, entries in state["timeline"].items():
        old = [e for e in entries if _parse(e["at"]) < cutoff]
        state["timeline"][d] = old[-1:] + [e for e in entries if _parse(e["at"]) >= cutoff]  # keep the login in force
    state["coverage"] = [c for c in state["coverage"]
                         if isinstance(c, list) and len(c) == 2 and (_parse(c[1]) or now) >= cutoff]
    state["notes"] = state["notes"][-20:]


# ------------------------------------------------------------------ attribution
@dataclass(frozen=True)
class Match:
    account_id: str | None  # None: logged out or an API-key login
    status: str  # "match" | "none" | "ambiguous" | "uncovered"


def login_at(state: dict, config_dir: Path, when: datetime) -> Match:
    """Which login the watcher saw in `config_dir` at `when`.

    "uncovered": the watcher wasn't running then (or never saw this folder), so nobody knows.
    "ambiguous": the login changed within a couple of ticks of `when`; we refuse to guess.
    """
    when = when.astimezone(timezone.utc)
    spans = [(_parse(c[0]), _parse(c[1])) for c in state["coverage"] if isinstance(c, list) and len(c) == 2]
    if not spans:
        return Match(None, "uncovered")  # the watcher has never run
    # The newest span's end is saved only every FLUSH_EVERY seconds, so it may lag behind by that much.
    slacks = [timedelta(seconds=TICK * 3)] * (len(spans) - 1) + [timedelta(seconds=FLUSH_EVERY + TICK * 3)]
    covered = any(a and b and a - timedelta(seconds=1) <= when <= b + s
                  for (a, b), s in zip(spans, slacks, strict=True))
    entries = sorted(state["timeline"].get(home_relative(config_dir), []), key=lambda e: e["at"])
    before = [e for e in entries if _parse(e["at"]) <= when]
    if not covered or not before:
        return Match(None, "uncovered")
    for i, e in enumerate(entries):
        if i and abs(_parse(e["at"]) - when) <= AMBIGUOUS and e.get("id") != entries[i - 1].get("id"):
            return Match(None, "ambiguous")
    uid = before[-1].get("id")
    return Match(uid, "match" if uid else "none")


# ------------------------------------------------------------------ discovery
def is_claude_folder(d: Path) -> bool:
    """Holds Claude Code session logs or its own account file."""
    try:
        if (d / ".claude.json").is_file():
            return True
        projects = d / "projects"
        return projects.is_dir() and any(True for _ in _limited_glob(projects, "*/*.jsonl", 1))
    except OSError:
        return False


def _limited_glob(base: Path, pattern: str, limit: int):
    for i, p in enumerate(base.glob(pattern)):
        if i >= limit:
            return
        yield p


def _proc_config_dirs() -> set[Path]:
    """CLAUDE_CONFIG_DIR of this user's running processes (Linux only, best effort)."""
    found: set[Path] = set()
    if not sys.platform.startswith("linux"):
        return found
    uid = os.getuid()
    for proc in Path("/proc").iterdir() if Path("/proc").is_dir() else []:
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            env = (proc / "environ").read_bytes()
        except OSError:
            continue
        for item in env.split(b"\0"):
            if item.startswith(b"CLAUDE_CONFIG_DIR="):
                value = item.split(b"=", 1)[1].decode("utf-8", "replace").strip()
                if value:
                    found.add(_expand(value))
    return found


def _roots(home: Path) -> list[Path]:
    roots = [home, home / ".config", home / "Library" / "Application Support"]
    for var in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    return [r for r in roots if r.is_dir()]


def discover(known: set[Path], skip: set[Path], home: Path | None = None, budget: float = DISCOVER_BUDGET,
             with_processes: bool = True) -> tuple[list[Path], bool]:
    """Claude folders under the usual places that aren't in `known`. Returns (folders, finished in time)."""
    home = home or Path.home()
    deadline = time.monotonic() + budget
    found: set[Path] = set()
    candidates: list[Path] = [home / ".claude", *sorted(home.glob(".claude-*"))]
    if with_processes:
        candidates += sorted(_proc_config_dirs())
    complete = True
    stack: list[tuple[Path, int]] = [(r, 0) for r in _roots(home)]
    seen: set[Path] = set()
    while stack:
        if time.monotonic() > deadline:
            complete = False
            break
        d, depth = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        try:
            children = [c for c in d.iterdir() if c.is_dir() and not c.is_symlink()]
        except OSError:
            continue
        for c in children:
            if c.name in _SKIP_DIRS or c in skip or c in known:
                continue
            if "claude" in c.name.lower():
                candidates.append(c)
            if depth < 2:
                stack.append((c, depth + 1))
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r in known or r in skip or any(k in r.parents for k in known) or any(s in r.parents or s == r for s in skip):
            continue
        if is_claude_folder(r):
            found.add(r)
    return sorted(found), complete


def auto_name(folder: Path, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "-", folder.name.lstrip(".").removeprefix("claude-").removeprefix("claude"))
    base = base.strip("-_")[:30] or "account"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


# ------------------------------------------------------------------ the watcher
class Watcher:
    """Background thread: checks account files every TICK seconds, looks for new folders every minute."""

    def __init__(self, path: Path | None = None, tick: float = TICK):
        self.path = path or state_path()
        self.tick = tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stamps: dict[Path, tuple[int, int] | None] = {}
        self._last_tick: datetime | None = None
        self._last_flush: datetime | None = None
        self._last_discover = 0.0
        self.running = False

    # ---- lifecycle
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="worklog-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        self.running = True
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001 - a watcher hiccup must never take the web UI down
                log.exception("watcher step failed")
            self._stop.wait(self.tick)
        self._flush(force=True)
        self.running = False

    # ---- one round
    def folders(self) -> list[Path]:
        """Folders whose login is watched: every configured account folder, plus ~/.claude."""
        from .config import ConfigError, load_config
        dirs = {default_claude_dir()}
        try:
            dirs |= {a.config_dir for a in load_config().accounts}
        except ConfigError:
            pass
        return sorted(dirs)

    def step(self, now: datetime | None = None) -> None:
        now = now or _now()
        monotonic = time.monotonic()
        folders = self.folders()
        with _lock(self.path, 5.0):  # held for the whole round, so a click on "dismiss" is never lost
            state = load_state(self.path)
            changed = self._cover(state, now)
            for d in folders:
                changed |= self._check_login(state, d, now)
            if monotonic - self._last_discover >= DISCOVER_EVERY:
                self._last_discover = monotonic
                changed |= self.discover_round(state, now)
            due = self._last_flush is None or (now - self._last_flush).total_seconds() >= FLUSH_EVERY
            if changed or due:
                _prune(state, now)
                _save_state(state, self.path)
                self._last_flush = now
        self._last_tick = now

    def _cover(self, state: dict, now: datetime) -> bool:
        spans = state["coverage"]
        gap = self._last_tick is None or now - self._last_tick > SLEEP_GAP
        if gap or not spans:
            spans.append([_iso(now), _iso(now)])  # first round, or back from sleep: a new span
            return True
        spans[-1][1] = _iso(now)
        return False

    def _check_login(self, state: dict, config_dir: Path, now: datetime) -> bool:
        f = identity_path(config_dir)
        try:
            st = f.stat()
            stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if config_dir in self._stamps and self._stamps[config_dir] == stamp:
            return False
        ident = self._read(f) if stamp else None
        if stamp and ident is False:
            return False  # half-written file: try again next tick, keep the old stamp
        self._stamps[config_dir] = stamp
        uid = ident.account_id if isinstance(ident, Identity) else None
        key = home_relative(config_dir)
        entries = state["timeline"].setdefault(key, [])
        if entries and entries[-1].get("id") == uid:
            return False
        entries.append({"at": _iso(now), "id": uid})
        if isinstance(ident, Identity):
            from .identity import note_login
            try:
                note_login(config_dir, ident, now=now.astimezone())
            except TimeoutError:
                log.warning("account state busy; login noted next time")
        log.info("login in %s is now %s", key, "an account" if uid else "none (logged out or API key)")
        return True

    @staticmethod
    def _read(f: Path) -> Identity | None | bool:
        """The login in `f`; None when logged out; False when the file is mid-write (unparsable)."""
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return False
        return read_identity(f)

    def discover_round(self, state: dict, now: datetime) -> bool:
        from .config import ConfigError, load_config
        from .hooks import hook_installed
        try:
            cfg = load_config()
        except ConfigError:
            return False
        known = {a.config_dir for a in cfg.accounts}
        skip = {cfg.store_repo, DATA_DIR, STATE_DIR}
        folders, complete = discover(known, skip)
        if not complete:
            log.info("folder discovery stopped early (large home folder); it continues next round")
        before = set(state["folders"])
        state["folders"] = sorted({home_relative(f) for f in folders})
        changed = set(state["folders"]) != before
        hooked = set(state["hooked"])
        for a in cfg.accounts:
            key = home_relative(a.config_dir)
            if hook_installed(a):
                if key not in hooked:
                    hooked.add(key)
                    changed = True
                state["dismissed"] = [k for k in state["dismissed"] if k != f"hook:{key}"]
        if hooked != set(state["hooked"]):
            state["hooked"] = sorted(hooked)
        if cfg.auto_setup:
            changed |= self._auto_setup(cfg, state, folders, now)
        return changed

    def _auto_setup(self, cfg, state: dict, folders: list[Path], now: datetime) -> bool:
        """Opt-in: add new folders as accounts and (re)install missing hooks, then leave a note."""
        from .config import Account, save_config
        from .hooks import hook_installed, install_hook
        notes, changed, added = [], False, []
        taken = {a.name for a in cfg.accounts}
        for f in folders:
            name = auto_name(f, taken)
            taken.add(name)
            added.append(Account(name, f))
        if added:
            cfg.accounts.extend(added)
            try:
                save_config(cfg, cfg.path)
            except Exception as exc:  # noqa: BLE001 - leave it to the user; the banner still shows
                log.warning("auto-setup could not save the config: %s", exc)
                return False
            notes += [f"Added {home_relative(a.config_dir)} as account {a.name}" for a in added]
            state["folders"] = []
            changed = True
        new = {a.name for a in added}
        for a in cfg.accounts:  # new accounts, and hooks that were installed once and are gone now
            if (a.name in new or home_relative(a.config_dir) in state["hooked"]) and not hook_installed(a):
                ok, msg = install_hook(a)
                notes.append(f"Installed the hook for {a.name}" if ok else msg)
                changed = True
        for n in notes:
            state["notes"].append({"at": _iso(now), "text": n})
        return changed

    def _flush(self, force: bool = False) -> None:
        if force and self._last_tick:
            try:
                with _lock(self.path, 2.0):
                    state = load_state(self.path)
                    if state["coverage"]:
                        state["coverage"][-1][1] = _iso(self._last_tick)
                        _save_state(state, self.path)
            except (OSError, TimeoutError):
                pass


# ------------------------------------------------------------------ notices for the UI
def notices(cfg, state: dict | None = None) -> list[dict]:
    """What the user should know: {key, kind, text, action} for each open item, newest first.

    kinds: "login" (a login sharing a folder has no name), "folder" (a Claude folder that isn't
    an account), "hook" (a hook that was installed is gone), "apikey", "note" (auto-setup did it).
    """
    from .hooks import hook_installed
    from .identity import detected_logins, mask_email, ref
    state = state or load_state()
    dismissed = set(state["dismissed"])
    out: list[dict] = []
    by_dir = cfg.accounts_by_dir()
    logins = detected_logins()
    per_dir: dict[Path, list[dict]] = {}
    for login in logins:
        for d in login["dirs"]:
            per_dir.setdefault(d, []).append(login)
    for d, group in per_dir.items():
        if d not in by_dir or len(group) < 2:
            continue
        for login in group:
            acct = cfg.resolve_account(d, login["account_id"], login["email"])
            if acct.match_account_id == login["account_id"] or (
                    acct.match_email and login["email"] and acct.match_email.casefold() == login["email"].casefold()):
                continue
            key = f"login:{ref(login['account_id'])}:{home_relative(d)}"
            out.append({"key": key, "kind": "login", "action": "/settings#logins",
                        "text": f"New account found: {mask_email(login['email'])} in {home_relative(d)}. "
                                f"Give it a name so its sessions are counted separately."})
    for f in state["folders"]:
        out.append({"key": f"folder:{f}", "kind": "folder", "action": "/settings#add",
                    "text": f"New Claude folder found: {f}. Add it as an account to track its sessions.",
                    "folder": f})
    hooked = set(state["hooked"])
    for a in cfg.accounts:
        key = home_relative(a.config_dir)
        if key in hooked and not hook_installed(a):
            out.append({"key": f"hook:{key}", "kind": "hook", "action": "/settings",
                        "text": f"The hook for {a.name} is gone (another tool may have rewritten its settings). "
                                f"Reinstall it, or the watcher covers it while Worklog runs.", "account": a.name})
    for d, entries in state["timeline"].items():
        folder = _expand(d)
        if entries and entries[-1].get("id") is None and len(by_dir.get(folder, [])) > 1:
            if (folder / "projects").is_dir():
                out.append({"key": f"apikey:{d}", "kind": "apikey", "action": "/settings",
                            "text": f"No Claude login found in {d} (logged out or using an API key). "
                                    f"Sessions there go to its default account."})
    for n in reversed(state["notes"]):
        out.append({"key": f"note:{n.get('at')}", "kind": "note", "action": "/settings",
                    "text": str(n.get("text", ""))})
    return [n for n in out if n["key"] not in dismissed]


def dismiss(key: str, path: Path | None = None) -> None:
    path = path or state_path()
    with _lock(path, 2.0):
        state = load_state(path)
        if key.startswith("note:"):
            state["notes"] = [n for n in state["notes"] if f"note:{n.get('at')}" != key]
        elif key not in state["dismissed"]:
            state["dismissed"].append(key)
        _save_state(state, path)
