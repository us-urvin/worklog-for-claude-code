"""Trends, "where you left off", goals, badges and the year in review: all computed on this computer from the
day cache (no AI, no network), so these pages stay fast and private."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import cost


@dataclass
class Period:
    start: date
    end: date
    minutes: int = 0
    sessions: int = 0
    tokens: int = 0
    days_active: int = 0
    projects: Counter = field(default_factory=Counter)  # name -> minutes
    tags: Counter = field(default_factory=Counter)  # tag -> minutes (Claude's or yours)
    refs: set = field(default_factory=set)
    tests_runs: int = 0
    tests_fixed: int = 0
    claude: int = 0
    dollars: float = 0.0
    hours: list = field(default_factory=lambda: [0] * 24)
    models: Counter = field(default_factory=Counter)  # model -> tokens
    by_day: dict = field(default_factory=dict)  # date -> minutes

    @property
    def label(self) -> str:
        if self.start == self.end:
            return f"{self.start:%a %d %b}"
        return f"{self.start:%d %b} – {self.end:%d %b}"


def period(entries: dict[date, dict], start: date, end: date, custom_prices=None) -> Period:
    p = Period(start, end)
    d = start
    while d <= end:
        e = entries.get(d) or {}
        if e.get("n"):
            p.days_active += 1
            p.minutes += int(e.get("minutes") or 0)
            p.by_day[d] = int(e.get("minutes") or 0)
            p.sessions += int(e.get("sessions") or 0)
            p.tokens += sum((e.get("tokens") or {}).values())
            for i, m in enumerate(e.get("hours") or []):
                if i < 24:
                    p.hours[i] += m
            for model, t in (e.get("models") or {}).items():
                p.models[model] += sum(t.values())
            p.dollars += cost.estimate(e.get("models") or {}, custom_prices)[0]
            for proj in e.get("projects") or []:
                p.projects[proj["name"]] += proj["minutes"]
                if proj.get("tag"):
                    p.tags[proj["tag"]] += proj["minutes"] or 1
                p.refs.update(proj.get("refs") or [])
                p.tests_runs += (proj.get("tests") or {}).get("runs", 0)
                p.tests_fixed += (proj.get("tests") or {}).get("fixed", 0)
                p.claude += int(proj.get("claude") or 0)
        d += timedelta(days=1)
    return p


def _hours(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _change(now: float, before: float) -> str:
    if not before:
        return ""
    pct = round(100 * (now - before) / before)
    return f" ({'+' if pct >= 0 else ''}{pct}%)" if pct else " (same as before)"


def sentences(now: Period, before: Period, what: str = "week") -> list[str]:
    """3-5 plain sentences comparing two periods, worked out here (no AI)."""
    if not now.minutes and not now.sessions:
        return [f"No work recorded this {what} yet."]
    same = " compared with this point last " + what if before.end - before.start < timedelta(days=6) else ""
    change = _change(now.minutes, before.minutes)
    out = [f"This {what}: {_hours(now.minutes)} over {now.days_active} day{'s' * (now.days_active != 1)}"
           + (change[:-1] + same + ")" if change and same else change) + "."]
    if now.projects:
        name, mins = now.projects.most_common(1)[0]
        share = round(100 * mins / max(1, now.minutes))
        prev = round(100 * before.projects.get(name, 0) / max(1, before.minutes)) if before.minutes else None
        trail = f", up from {prev}%" if prev is not None and share > prev + 4 else (
            f", down from {prev}%" if prev is not None and share < prev - 4 else "")
        out.append(f"Most of it went to {name} ({share}%{trail}).")
    reviews, reviews_before = now.tags.get("Review", 0), before.tags.get("Review", 0)
    if now.tags:
        tag, _ = now.tags.most_common(1)[0]
        if reviews and reviews != reviews_before and tag == "Review":
            out.append(f"Reviews took {_hours(reviews)}, against {_hours(reviews_before)} the {what} before.")
        else:
            out.append(f"Mostly {tag.lower()} work.")
    if now.tests_fixed:
        out.append(f"{now.tests_fixed} failing test run{'s' * (now.tests_fixed != 1)} fixed.")
    if now.minutes:
        peak = max(range(24), key=lambda h: (now.hours[h], -h))
        if now.hours[peak]:
            out.append(f"Your busiest hour was {peak:02d}:00–{(peak + 1) % 24:02d}:00.")
    return out[:5]


def leave_off(entries: dict[date, dict], limit: int = 3) -> list[dict]:
    """The projects you worked on most recently, newest first, with what you were last doing."""
    seen, out = set(), []
    for d in sorted(entries, reverse=True):
        for proj in sorted(entries[d].get("projects") or [], key=lambda x: x.get("last", ""), reverse=True):
            if proj["key"] in seen or proj.get("kind") == "chat":
                continue
            seen.add(proj["key"])
            out.append(dict(proj, day=d))
            if len(out) >= limit:
                return out
    return out


# ------------------------------------------------------------------ goals
def goal_progress(entries: dict[date, dict], day: date, goals: dict) -> dict | None:
    """Today's hours against the daily goal, this week's tokens against the weekly limit, and the goal streak."""
    if not goals or not (goals.get("hours") or goals.get("tokens")):
        return None
    out: dict = {}
    if goals.get("hours"):
        target = float(goals["hours"]) * 60
        today = int((entries.get(day) or {}).get("minutes") or 0)
        out["hours"] = {"done": today, "target": target, "share": min(1.0, today / target if target else 0)}
        streak, d = 0, day
        while True:
            m = int((entries.get(d) or {}).get("minutes") or 0)
            if m >= target:
                streak += 1
            elif d != day and m > 0:
                break  # a day of work below the goal ends the streak; days off don't
            elif d not in entries:
                break
            d -= timedelta(days=1)
            if streak > 400:
                break
        out["streak"] = streak
    if goals.get("tokens"):
        start = day - timedelta(days=day.weekday())
        used = sum(sum(((entries.get(start + timedelta(days=i)) or {}).get("tokens") or {}).values())
                   for i in range((day - start).days + 1))
        limit = float(goals["tokens"])
        out["tokens"] = {"used": used, "limit": limit, "share": used / limit if limit else 0}
    return out


# ------------------------------------------------------------------ badges
BADGES = [
    ("first-session", "🌱", "First steps", "Your first day in Worklog"),
    ("big-day", "🚀", "Big day", "10M tokens in one day"),
    ("streak-7", "🔥", "On a roll", "7 days in a row"),
    ("streak-30", "🏔️", "Habit", "30 days in a row"),
    ("streak-100", "💯", "Unstoppable", "100 days in a row"),
    ("night-owl", "🦉", "Night owl", "Working after 1 am"),
    ("early-bird", "🐦", "Early bird", "Working before 6 am"),
    ("reviewer", "🔍", "Reviewer", "5 days of reviews in one week"),
    ("bug-squasher", "🐛", "Bug squasher", "5 failing test runs fixed in one week"),
    ("polyglot", "🧩", "Juggler", "5 projects in one week"),
    ("marathon", "⏱️", "Marathon", "8 hours of work in one day"),
    ("century", "🏅", "Century", "100 hours in total"),
]


def badges(entries: dict[date, dict]) -> list[dict]:
    """Every badge with the day it was first earned (None if not yet)."""
    earned: dict[str, date] = {}

    def give(key: str, d: date) -> None:
        earned.setdefault(key, d)
    run, prev, total = 0, None, 0
    week: dict[date, dict] = {}
    for d in sorted(entries):
        e = entries[d]
        if not e.get("n"):
            continue
        give("first-session", d)
        mins = int(e.get("minutes") or 0)
        total += mins
        if sum((e.get("tokens") or {}).values()) >= 10_000_000:
            give("big-day", d)
        if mins >= 8 * 60:
            give("marathon", d)
        if total >= 100 * 60:
            give("century", d)
        hours = e.get("hours") or []
        if any(hours[1:5]):
            give("night-owl", d)
        if any(hours[4:6]):
            give("early-bird", d)
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        prev = d
        for n in (7, 30, 100):
            if run >= n:
                give(f"streak-{n}", d)
        monday = d - timedelta(days=d.weekday())
        w = week.setdefault(monday, {"review_days": 0, "fixed": 0, "projects": set()})
        projects = e.get("projects") or []
        if any(p.get("tag") == "Review" for p in projects):
            w["review_days"] += 1
        w["fixed"] += sum((p.get("tests") or {}).get("fixed", 0) for p in projects)
        w["projects"].update(p["key"] for p in projects if p.get("kind") != "chat")
        if w["review_days"] >= 5:
            give("reviewer", d)
        if w["fixed"] >= 5:
            give("bug-squasher", d)
        if len(w["projects"]) >= 5:
            give("polyglot", d)
    return [{"id": k, "emoji": em, "name": n, "desc": desc, "earned": earned.get(k)} for k, em, n, desc in BADGES]


# ------------------------------------------------------------------ year in review
def year_review(entries: dict[date, dict], year: int, custom_prices=None) -> dict:
    start, end = date(year, 1, 1), date(year, 12, 31)
    p = period(entries, start, end, custom_prices)
    months = [0] * 12
    busiest: tuple[int, date | None] = (0, None)
    for d, m in p.by_day.items():
        months[d.month - 1] += m
        if m > busiest[0]:
            busiest = (m, d)
    run, best, prev = 0, 0, None
    for d in sorted(p.by_day):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best, prev = max(best, run), d
    return {"period": p, "months": months, "busiest": busiest, "longest_streak": best,
            "top_projects": p.projects.most_common(5), "top_tags": p.tags.most_common(4),
            "top_model": p.models.most_common(1)[0][0] if p.models else ""}
