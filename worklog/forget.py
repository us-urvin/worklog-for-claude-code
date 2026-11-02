"""Forget a login: delete Worklog's records of its Claude Code sessions on this device.

Two steps, so the UI can show exactly what will be lost before anything happens:

* `plan(cfg, ref)` works out which sessions belong to the login (recorded by the hook, or
  matched by the account watcher), which record files and days that touches, and which
  account goes away with it. It changes nothing.
* `apply(cfg, plan)` deletes those records and the saved summaries of the projects involved,
  removes the account linked only to that login, forgets the login, and remembers the
  sessions so a later sync never writes them again.

Claude Code's own files are never touched. Records synced from other devices aren't removed
(this device can't tell which of their sessions belong to the login), and the old versions
stay in the data repo's git history.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from .config import Config, home_relative, save_config
from .identity import _lock, _save_state, detected_logins, load_state, mask_email, state_path
from .store import GitStore

log = logging.getLogger(__name__)


@dataclass
class Plan:
    ref: str
    account_id: str
    email: str | None
    dirs: list[Path]
    sessions: set[str] = field(default_factory=set)  # session ids that belong to this login
    files: list[str] = field(default_factory=list)  # record paths in the data repo (this device)
    days: set[date] = field(default_factory=set)
    projects: dict[date, set[str]] = field(default_factory=dict)  # project keys touched, per day
    account: str | None = None  # the account linked only to this login, removed with it
    keep_account: bool = False  # it's the last account: keep it, just unlink the login

    @property
    def label(self) -> str:
        return mask_email(self.email)


def _linked_account(cfg: Config, account_id: str, email: str | None):
    for a in cfg.accounts:
        if a.match_account_id == account_id:
            return a
        if a.match_email and email and a.match_email.casefold() == email.casefold():
            return a
    return None


def plan(cfg: Config, ref_: str) -> Plan | None:
    """What forgetting this login would delete. None if the login isn't known on this device."""
    from . import watcher
    from .collector import _SAFE
    from .report import project_key

    login = next((x for x in detected_logins() if x["ref"] == ref_), None)
    if login is None:
        return None
    uid = login["account_id"]
    p = Plan(ref_, uid, login["email"], login["dirs"])

    # 1. Sessions the hook saw starting (or ending) under this login.
    for sid, entry in load_state()["sessions"].items():
        if entry.get("id") == uid or (entry.get("id") is None and entry.get("end_id") == uid):
            p.sessions.add(sid)

    # 2. Records on this device: hook sessions by id, watcher-matched ones by the login timeline.
    watched = watcher.load_state()
    folders = {a.name: a.config_dir for a in cfg.accounts}
    base = cfg.store_repo / "records"
    safe_ids = {_SAFE.sub("_", s): s for s in p.sessions}
    for f in sorted(base.glob(f"*/{cfg.device}/*/*.json")) if base.is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("source") == "claude.ai":
            continue  # imported chats belong to a claude.ai login, not to a Claude Code one
        sid = str(rec.get("session_id") or "")
        mine = f.stem in safe_ids or sid in p.sessions
        if not mine and rec.get("account_source") == "watcher":
            folder = folders.get(str(rec.get("account")))
            try:
                start = datetime.fromisoformat(str(rec.get("start")))
            except ValueError:
                start = None
            if folder is not None and start is not None and folder in p.dirs:
                m = watcher.login_at(watched, folder, start)
                mine = m.status == "match" and m.account_id == uid
        if not mine:
            continue
        p.sessions.add(sid)
        rel = f.relative_to(cfg.store_repo).as_posix()
        p.files.append(rel)
        try:
            d = date.fromisoformat(f.parts[-4])
        except ValueError:
            continue
        p.days.add(d)
        p.projects.setdefault(d, set()).add(project_key(rec.get("project") or {}))

    linked = _linked_account(cfg, uid, login["email"])
    if linked is not None:
        p.account = linked.name
        p.keep_account = len(cfg.accounts) == 1
    return p


def apply(cfg: Config, p: Plan, now: datetime | None = None) -> str:
    """Do it. Returns a message for the UI."""
    from . import watcher
    now = now or datetime.now().astimezone()
    stamp = now.isoformat(timespec="seconds")
    store = GitStore(cfg.store_repo)
    with store.lock():
        store.pull()
        # 1. The account linked only to this login. Its remaining records (other logins that fell back
        #    to it as the folder default) move to the folder's new default, so nothing is left orphaned.
        moved = 0
        if p.account and not p.keep_account:
            gone = cfg.account(p.account)
            cfg.accounts = [a for a in cfg.accounts if a.name != p.account]
            if cfg.summarizer_account == p.account:
                cfg.summarizer_account = cfg.accounts[0].name
            if cfg.chat_account == p.account:
                cfg.chat_account = None
            save_config(cfg, cfg.path)
            doomed = set(p.files)
            for f in sorted((cfg.store_repo / "records").glob(f"*/{cfg.device}/{p.account}/*.json")):
                rel = f.relative_to(cfg.store_repo).as_posix()
                if rel in doomed:
                    continue
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                folder = gone.config_dir if gone else None
                new = (cfg.default_account(folder) if folder in cfg.accounts_by_dir() else cfg.accounts[0]).name
                rec["account"] = new
                store.write_json(f"records/{f.parts[-4]}/{cfg.device}/{new}/{f.name}", rec)
                f.unlink()
                moved += 1
        elif p.account and p.keep_account:
            cfg.accounts = [replace(a, match_account_id=None, match_email=None) if a.name == p.account else a
                            for a in cfg.accounts]
            save_config(cfg, cfg.path)

        # 2. The session records themselves.
        deleted = 0
        for rel in p.files:
            f = cfg.store_repo / rel
            if f.exists():
                f.unlink()
                deleted += 1
                try:
                    f.parent.rmdir()  # drop the account folder if it is now empty
                except OSError:
                    pass

        # 3. Saved summaries of the projects those sessions were in: written again on demand.
        for d, keys in p.projects.items():
            meta_file = cfg.store_repo / "reports" / d.isoformat() / f"{cfg.device}.json"
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            summaries = meta.get("summaries") or {}
            for k in keys:
                summaries.pop(k, None)
            meta["summaries"] = summaries
            store.write_json(meta_file.relative_to(cfg.store_repo).as_posix(), meta)
            md = cfg.store_repo / "reports" / d.isoformat() / f"{cfg.device}.md"
            if md.exists():
                md.unlink()  # the Markdown report mentions them too; it is rebuilt with the next summaries

        store.commit_and_push(f"forget a login on {cfg.device}: {deleted} session record(s)")

    # 4. Forget the login, remember its sessions as forgotten, and blank it out of the watcher's timeline.
    path = state_path()
    with _lock(path, 5.0):
        state = load_state(path)
        state["logins"].pop(p.account_id, None)
        state["sessions"] = {k: v for k, v in state["sessions"].items()
                             if v.get("id") != p.account_id and v.get("end_id") != p.account_id}
        for sid in p.sessions:
            state["forgotten"][sid] = stamp
        _save_state(state, path)
    with _lock(watcher.state_path(), 5.0):
        ws = watcher.load_state()
        for entries in ws["timeline"].values():
            for e in entries:
                if e.get("id") == p.account_id:
                    e["id"] = None
        ws["dismissed"] = [k for k in ws["dismissed"] if p.ref not in k]
        _save_state(ws, watcher.state_path())

    where = ", ".join(home_relative(d) for d in p.dirs) or "this device"
    parts = [f"Forgot {p.label} ({where}): deleted {deleted} session record(s) from {len(p.days)} day(s)"]
    if p.account and not p.keep_account:
        parts.append(f"removed account {p.account}" + (f", moved {moved} other record(s) to the folder default"
                                                       if moved else ""))
    log.info("forgot login %s: %d records", p.ref, deleted)
    return "; ".join(parts) + "."
