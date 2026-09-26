"""Collector: local Claude Code transcripts -> redacted per-session records in the store."""
from __future__ import annotations

import logging
import re
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .config import SUMMARIZER_CWD, Account, Config
from .identity import Identity, session_logins
from .parser import ParseStats, SessionDay, parse_transcript
from .redact import redact
from .store import GitStore

log = logging.getLogger(__name__)
SCHEMA = 1
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _home_relative(p: Path | str) -> str:
    p = str(p)
    home = str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def find_git_root(start: Path) -> Path | None:
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    return None


def sanitize_remote(url: str) -> dict | None:
    """Reduce a remote URL to host + repo slug. Credentials are always dropped.

    Handles https://user:token@host/o/r.git, ssh://git@host:22/o/r.git, git@host:o/r.git
    """
    url = url.strip()
    if not url:
        return None
    m = re.match(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$", url)  # scp-like syntax
    if m and "://" not in url:
        host, path = m.group(1), m.group(2)
    else:
        parts = urlsplit(url)
        if not parts.hostname:
            return None
        host, path = parts.hostname, parts.path
    slug = path.strip("/").removesuffix(".git")
    return {"host": host.lower(), "slug": slug} if slug else None


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("git %s failed in %s: %s", args[0], root, exc)
        return None
    return out.stdout if out.returncode == 0 else None


def _commits(root: Path, day: date) -> list[dict]:
    """Your own non-merge commits on `day` (local time), across all branches."""
    email = (_git(root, "config", "user.email") or "").strip()
    if not email:
        return []  # no identity -> can't tell which commits are yours; include none
    since = datetime.combine(day, time.min).astimezone().isoformat()
    until = datetime.combine(day + timedelta(days=1), time.min).astimezone().isoformat()
    out = _git(root, "log", "--all", "--no-merges", f"--author={email}", f"--since={since}",
               f"--until={until}", "--format=%h%x09%s", "--max-count=100")
    commits = []
    for line in (out or "").splitlines():
        h, _, subject = line.partition("\t")
        if h:
            commits.append({"hash": h, "subject": redact(subject)[:200]})
    return commits


def build_record(sd: SessionDay, account: Account, cfg: Config) -> dict | None:
    cwd = sd.cwd_counts.most_common(1)[0][0] if sd.cwd_counts else None
    cwd_path = Path(cwd) if cwd else None
    if cwd_path and (cwd_path == SUMMARIZER_CWD or SUMMARIZER_CWD in cwd_path.parents):
        return None  # our own `claude -p` summarization runs
    if sd.api_calls == 0 and not sd.prompts:
        return None

    root = find_git_root(cwd_path) if cwd_path and cwd_path.exists() else None
    project: dict = {"branches": sorted(sd.branches)}
    if root:
        remote = sanitize_remote(_git(root, "remote", "get-url", "origin") or "")
        project.update(kind="git", name=root.name, path=_home_relative(root), remote=remote)
    else:
        project.update(kind="misc", name=cfg.misc_label, path=_home_relative(cwd) if cwd else None,
                       folder=cwd_path.name if cwd_path else None, remote=None)

    files = []
    for f in sorted(sd.files)[:200]:
        fp = Path(f)
        files.append(str(fp.relative_to(root)) if root and root in fp.parents else _home_relative(fp))

    prompts = [redact(p)[: cfg.max_prompt_chars] for p in sd.prompts[: cfg.max_prompts]]
    minutes = round((sd.last - sd.first).total_seconds() / 60) if sd.first and sd.last else 0
    return {
        "schema": SCHEMA,
        "session_id": sd.session_id,
        "date": sd.day.isoformat(),
        "account": account.name,
        "device": cfg.device,
        "project": project,
        "start": sd.first.isoformat(timespec="seconds") if sd.first else None,
        "end": sd.last.isoformat(timespec="seconds") if sd.last else None,
        "span_minutes": minutes,
        "api_calls": sd.api_calls,
        "tokens": sd.tokens,
        "tokens_by_model": sd.tokens_by_model,
        "prompt_count": len(sd.prompts),
        "prompts": prompts,
        "files_touched": files,
        "tool_calls": dict(sd.tool_calls),
        "commits": _commits(root, sd.day) if root and cfg.include_commits else [],
    }


def _transcripts(config_dir: Path, label: str, day: date) -> list[Path]:
    base = config_dir / "projects"
    if not base.is_dir():
        log.warning("account %s: no transcripts dir at %s", label, base)
        return []
    cutoff = datetime.combine(day, time.min).astimezone().timestamp()
    found = []
    for p in base.rglob("*.jsonl"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue  # deleted while scanning
        if mtime >= cutoff:  # a file untouched since before `day` can't hold entries for it
            found.append((mtime, p))
    return [p for _, p in sorted(found)]


def _move_stale(store: GitStore, day: date, device: str, account: str, fname: str) -> int:
    """Move this session's record from another account's folder into `account`'s.

    Happens when a login is named after its sessions were already collected under the
    folder's default. Only called for sessions whose login the hook recorded, so records
    written before accounts were tracked are never touched. Git history keeps the old path.
    """
    moved = 0
    base = store.path / "records" / day.isoformat() / device
    for old in sorted(base.glob(f"*/{fname}")) if base.is_dir() else []:
        if old.parent.name == account:
            continue
        src, dst = str(old.relative_to(store.path)), f"records/{day.isoformat()}/{device}/{account}/{fname}"
        store.move(src, dst)
        log.info("session %s: record moved from account %s to %s", fname[:8], old.parent.name, account)
        moved += 1
    return moved


def collect(cfg: Config, days: list[date]) -> int:
    store = GitStore(cfg.store_repo)
    logins: dict[str, Identity] = session_logins()  # local state written by the SessionStart hook
    written = 0
    with store.lock():
        store.pull()
        for day in days:
            # Several accounts may share one folder: read it once, then pick the account per session.
            for config_dir, group in cfg.accounts_by_dir().items():
                label = "/".join(a.name for a in group)
                stats = ParseStats()
                sessions: dict[str, SessionDay] = {}
                seen_usage: set[tuple] = set()
                seen_uuids: set[str] = set()
                for path in _transcripts(config_dir, label, day):
                    try:
                        parse_transcript(path, day, sessions, seen_usage, seen_uuids, stats)
                    except OSError as exc:
                        log.warning("cannot read %s: %s", path, exc)
                for sd in sessions.values():
                    login = logins.get(sd.session_id)
                    account = (cfg.resolve_account(config_dir, login.account_id, login.email) if login
                               else cfg.default_account(config_dir))  # unrecorded / older sessions
                    record = build_record(sd, account, cfg)
                    if record is None:
                        continue
                    fname = f"{_SAFE.sub('_', sd.session_id)}.json"
                    if login:
                        written += _move_stale(store, day, cfg.device, account.name, fname)
                    rel = f"records/{day.isoformat()}/{cfg.device}/{account.name}/{fname}"
                    written += store.write_json(rel, record)
                log.info("%s %s: %d files, %d lines (%d malformed), %d sessions",
                         day, label, stats.files, stats.lines, stats.bad_lines, len(sessions))
        if written:
            store.commit_and_push(f"collect {cfg.device}: {written} record(s)")
    return written
