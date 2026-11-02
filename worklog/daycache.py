"""A small per-day summary cache, so pages that look at weeks or months stay fast.

Each day in the data repo gets one compact entry (tokens per account, minutes, minutes per hour, models,
and per project: name, minutes, tokens, tag, timesheet line, refs, open tasks). An entry is rebuilt only
when its day changed: a cheap signature (number and newest change time of the day's record files, plus
the day's summaries and your corrections) is compared with `os.scandir`, without reading any JSON.

The cache lives on this computer only (`~/.local/state/claude-worklog/cache/days.json`), keeps the last
400 days, and is safe to delete: it is rebuilt from the data repo.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from . import overrides
from .config import STATE_DIR
from .parser import zero_tokens

log = logging.getLogger(__name__)

VERSION = 1
KEEP_DAYS = 400


def _path() -> Path:
    return STATE_DIR / "cache" / "days.json"


def _newest(path: Path, depth: int) -> tuple[int, int]:
    """(number of .json files, newest mtime in ns + total size) below `path`, `depth` folder levels deep at most.
    The size catches a rewrite within the same clock tick on file systems with coarse times."""
    count, newest, size = 0, 0, 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth > 0:
                            c, n = _newest(Path(entry.path), depth - 1)
                            count, newest = count + c, max(newest, n)
                    elif entry.name.endswith(".json"):
                        st = entry.stat(follow_symlinks=False)
                        count += 1
                        newest = max(newest, st.st_mtime_ns)
                        size += st.st_size
                except OSError:
                    continue
    except OSError:
        pass
    return count, newest + size


def signature(store_path: Path, day: date, extra: str = "") -> str:
    """Changes whenever a record, a summary or a correction that could affect `day` changes."""
    n, newest = _newest(store_path / "records" / day.isoformat(), 2)
    _, reports = _newest(store_path / "reports" / day.isoformat(), 0)
    _, fixes = _newest(store_path / "overrides", 0)
    return f"{n}:{newest}:{reports}:{fixes}:{extra}"


def _hhmm(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


def summarize(store, day: date, ov, time_mode: str = "active") -> dict:
    """The compact entry for one day (parses that day's records once)."""
    from .report import aggregate, legacy_fit, pick_summary, summary_line, summary_metas
    from .viz import minutes_by_hour
    records = [r for r in store.iter_json(f"records/{day.isoformat()}") if isinstance(r, dict)]
    if not records:
        return {"n": 0}
    accounts, projects = aggregate(records, ov, time_mode)
    metas = summary_metas(store, day)
    models: dict[str, dict[str, int]] = {}
    for a in accounts.values():
        for m, t in a["models"].items():
            dst = models.setdefault(m, zero_tokens())
            for k, v in t.items():
                dst[k] += v
    by_key = {}
    for r in records:
        sid = str(r.get("session_id") or "")
        if sid:
            by_key[sid] = r
    out_projects = []
    for key, p in projects.items():
        s, fit, _ = pick_summary(metas, key, [x["id"] for x in p.sessions_list], legacy_fit(p, projects))
        if fit == "other":
            s = {}
        mine_tags = [ov.tags.get(x["id"]) for x in p.sessions_list if ov.tags.get(x["id"])]
        tag = max(set(mine_tags), key=mine_tags.count) if mine_tags else (s.get("tag") or "")
        mine = ov.lines.get(f"{day.isoformat()}|{key}")
        recs = [by_key[x["id"]] for x in p.sessions_list if x["id"] in by_key]
        refs: list[str] = []
        tests = Counter()
        claude = 0
        last = ""
        for r in recs:
            for ref in r.get("refs") or []:
                if isinstance(ref, str) and ref not in refs:
                    refs.append(ref)
            for k, v in (r.get("tests") or {}).items():
                if isinstance(v, int):
                    tests[k] += v
            claude += int(r.get("claude_minutes") or 0)
            last = max(last, str(r.get("end") or ""))
        out_projects.append({
            "key": key, "name": p.name, "kind": p.kind, "minutes": p.minutes,
            "tokens": sum(p.tokens.values()), "sessions": p.sessions + p.chats, "approx": p.approx,
            "accounts": sorted(p.accounts), "devices": sorted(p.devices), "tag": tag,
            "line": summary_line(s, p, mine), "summarized": bool(mine or s.get("source") == "ai"),
            "refs": refs[:20], "tests": dict(tests), "claude": claude, "branches": sorted(p.branches)[:5],
            "open": [t["text"] for t in p.tasks if t["status"] == "open"][:8],
            "done": sum(1 for t in p.tasks if t["status"] == "done"),
            "last_prompt": p.prompts[-1][1].splitlines()[0][:160] if p.prompts else "",
            "last": _hhmm(last) if last else "",
        })
    out_projects.sort(key=lambda x: -x["minutes"])
    starts = [str(r.get("start") or "") for r in records if r.get("start")]
    ends = [str(r.get("end") or "") for r in records if r.get("end")]
    return {
        "n": len(records),
        "tokens": {name: sum(a["tokens"].values()) for name, a in accounts.items()},
        "sessions": sum(a["sessions"] for a in accounts.values()),
        "chats": sum(a.get("chats", 0) for a in accounts.values()),
        "minutes": sum(p.minutes for p in projects.values()),
        "hours": minutes_by_hour(records, day),
        "models": models,
        "projects": out_projects,
        "first": _hhmm(min(starts)) if starts else "",
        "last": _hhmm(max(ends)) if ends else "",
    }


class DayCache:
    """Read with `get(day)` / `days(start, end)`; call `save()` once at the end (only writes if something changed)."""

    def __init__(self, store, time_mode: str = "active", path: Path | None = None):
        self.store = store
        self.mode = time_mode
        self.path = path or _path()
        self._ov = None
        self.dirty = False
        self.rebuilt = 0
        data = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        ok = isinstance(data, dict) and data.get("version") == VERSION and data.get("mode") == time_mode \
            and data.get("repo") == str(store.path) and isinstance(data.get("days"), dict)
        self.entries: dict[str, dict] = data["days"] if ok else {}

    @property
    def ov(self):
        if self._ov is None:
            self._ov = overrides.load(self.store.path)
        return self._ov

    def get(self, day: date) -> dict:
        sig = signature(self.store.path, day)
        hit = self.entries.get(day.isoformat())
        if isinstance(hit, dict) and hit.get("sig") == sig:
            return hit
        if sig.startswith("0:"):  # nothing recorded that day: no need to read anything
            entry = {"n": 0}
        else:
            try:
                entry = summarize(self.store, day, self.ov, self.mode)
            except Exception:  # noqa: BLE001 - one odd day must never break a page
                log.exception("could not summarize %s for the cache", day)
                return {"n": 0}
        entry["sig"] = sig
        self.entries[day.isoformat()] = entry
        self.dirty = True
        self.rebuilt += 1
        return entry

    def days(self, start: date, end: date) -> dict[date, dict]:
        out, d = {}, start
        while d <= end:
            out[d] = self.get(d)
            d += timedelta(days=1)
        return out

    def save(self) -> None:
        if not self.dirty:
            return
        keep = dict(sorted(self.entries.items(), reverse=True)[:KEEP_DAYS])
        data = {"version": VERSION, "mode": self.mode, "repo": str(self.store.path), "days": keep}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError as exc:
            log.info("could not save the day cache: %s", exc)


def open_cache(cfg) -> DayCache | None:
    from .store import GitStore
    try:
        return DayCache(GitStore(cfg.store_repo), cfg.time_mode)
    except Exception:  # noqa: BLE001
        return None
