"""The phone summary: `phone/README.md` in your private data repo, made to be read in the GitHub app.

GitHub shows a folder's README.md first, so opening the data repo's `phone` folder on your phone shows
today, yesterday, this week's timesheet, open tasks and where you left off. Only one computer writes it
(Settings → Phone, `[phone] digest = true`), so two computers never edit the same file. It's rewritten
after each sync and after the nightly summaries, and only committed when its content changed: there is no
"last updated" clock in it, only the time of your latest session.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from . import insights

log = logging.getLogger(__name__)

REL = "phone/README.md"
_MARK = re.compile(r"<!-- worklog-phone device=([\w-]+) wrote=(\d{4}-\d\d-\d\d) -->")
MAX_BYTES = 20_000


def _h(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>!#~])")


def _cell(text: str, n: int = 90) -> str:
    """One line of plain text for the Markdown page: a prompt can't become a link, an image or HTML there."""
    text = " ".join(str(text or "").split()).replace("|", "/")
    text = text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"
    return _MD_SPECIAL.sub(r"\\\1", text)


def _day_section(title: str, e: dict) -> list[str]:
    if not e.get("n"):
        return [f"## {title}", "", "Nothing recorded.", ""]
    out = [f"## {title}", "", f"**{_h(e.get('minutes', 0))}** · {e.get('sessions', 0)} sessions"
           + (f" · {e.get('first')}–{e.get('last')}" if e.get("first") else ""), "",
           "| Project | Time | Kind | What |", "|---|---:|---|---|"]
    for p in (e.get("projects") or [])[:8]:
        out.append(f"| {_cell(p['name'], 30)} | {_h(p['minutes'])} | {p.get('tag') or ''} | {_cell(p.get('line'))} |")
    return out + [""]


def render(cache, today: date, device: str) -> str:
    entries = cache.days(today - timedelta(days=13), today)
    t, y = entries.get(today) or {}, entries.get(today - timedelta(days=1)) or {}
    monday = today - timedelta(days=today.weekday())
    week = insights.period(entries, monday, today)
    lines = [f"<!-- worklog-phone device={device} wrote={today.isoformat()} -->",
             "# 📱 Worklog", "",
             "_Your Claude Code work, for reading on your phone. Written by Worklog after each sync._", ""]
    latest = max((e.get("last") for d, e in entries.items() if d == today and e.get("last")), default="")
    if latest:
        lines += [f"Latest session today ended at {latest}.", ""]
    lines += _day_section(f"Today, {today:%a %d %b}", t)
    lines += _day_section(f"Yesterday, {today - timedelta(days=1):%a %d %b}", y)
    lines += [f"## This week ({_h(week.minutes)})", ""]
    rows = []
    d = monday
    while d <= today:
        for p in (entries.get(d) or {}).get("projects") or []:
            rows.append(f"| {d:%a %d} | {_cell(p['name'], 30)} | {p['minutes'] / 60:.2f} "
                        f"| {_cell(p.get('line'), 70)} |")
        d += timedelta(days=1)
    table = ["| Day | Project | Hours | Summary |", "|---|---|---:|---|"] + rows[:40]
    lines += (table if rows else ["No work yet."]) + [""]
    left = insights.leave_off(entries)
    if left:
        lines += ["## Where you left off", ""]
        for p in left:
            what = p.get("line") if p.get("summarized") else p.get("last_prompt") or p.get("line")
            when = f"{p['day']:%a %d %b}" + (f" {p['last']}" if p.get("last") else "")
            lines.append(f"- **{_cell(p['name'], 40)}** ({when}): {_cell(what, 120)}")
            lines += [f"  - [ ] {_cell(task, 100)}" for task in (p.get("open") or [])[:3]]
        lines.append("")
    tasks = []
    for d2 in sorted(entries, reverse=True)[:7]:
        for p in (entries[d2].get("projects") or []):
            for task in p.get("open") or []:
                item = f"- [ ] {_cell(task, 100)} _({_cell(p['name'], 30)})_"
                if item not in tasks:
                    tasks.append(item)
    if tasks:
        lines += ["## Open tasks", ""] + tasks[:15] + [""]
    text = "\n".join(lines) + "\n"
    return text if len(text.encode()) <= MAX_BYTES else text.encode()[:MAX_BYTES].decode("utf-8", "ignore") + "\n…\n"


def other_writer(store_path, device: str, today: date) -> str | None:
    """Another computer that wrote the phone summary in the last 2 days, if any."""
    try:
        head = (store_path / REL).read_text(encoding="utf-8")[:300]
    except OSError:
        return None
    m = _MARK.search(head)
    if not m or m.group(1) == device:
        return None
    try:
        wrote = date.fromisoformat(m.group(2))
    except ValueError:
        return None
    return m.group(1) if (today - wrote).days <= 2 else None


def write(cfg, store, today: date | None = None) -> bool:
    """Rewrite the phone summary if this computer is the one that writes it. True if the file changed.
    Call with the store locked; the caller commits."""
    if not getattr(cfg, "phone_digest", False):
        return False
    from .daycache import DayCache
    today = today or datetime.now().date()
    other = other_writer(store.path, cfg.device, today)
    if other:
        log.warning("phone summary: %s writes it; this computer (%s) leaves it alone", other, cfg.device)
        return False
    cache = DayCache(store, cfg.time_mode)
    text = render(cache, today, cfg.device)
    cache.save()
    return store.write_text(REL, text)
