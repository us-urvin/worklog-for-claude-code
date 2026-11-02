"""Ask your worklog: an instant search on this computer, and questions answered by your own Claude account.

Search looks through the day cache (projects, timesheet lines, tags, PR and ticket numbers, open tasks), then
the days' prompts if that isn't enough. "Ask Claude" picks the days the question is about (named dates such as
"last week" or "October", or the days the search found), builds a short excerpt of your saved notes, never
your code, and runs `claude -p` with the account and model chosen for summaries. The last 20 questions and
answers are kept on this computer only (`ask.json`).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import STATE_DIR, SUMMARIZER_CWD
from .redact import redact

MAX_QUESTION = 500
MAX_CONTEXT = 20_000
SEARCH_DAYS = 120
_WORD = re.compile(r"[\w#][\w#.-]*", re.U)
_STOP = set("""a an and are at be by can did do does for from had has have how i in is it last me my of on or so
that the this to was we were what when where which who why with you your week month day days today yesterday
about all any show tell list much many time hours spent work worked working""".split())
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], start=1)}


def _history_path() -> Path:
    return STATE_DIR / "ask.json"


def words(question: str) -> list[str]:
    out = []
    for w in _WORD.findall(question.lower()):
        w = w.strip(".-")
        if len(w) >= 3 and w not in _STOP or w.startswith("#") or re.match(r"^[a-z][a-z0-9]+-\d+$", w):
            out.append(w)
    return out


def date_range(question: str, today: date) -> tuple[date, date] | None:
    """The days a question names: today, yesterday, this/last week or month, last N days, a month, a date."""
    q = question.lower()
    if "yesterday" in q:
        d = today - timedelta(days=1)
        return d, d
    if "today" in q:
        return today, today
    m = re.search(r"\blast\s+(\d{1,3})\s+days?\b", q)
    if m:
        return today - timedelta(days=int(m.group(1)) - 1), today
    monday = today - timedelta(days=today.weekday())
    if "last week" in q:
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if "this week" in q:
        return monday, today
    first = today.replace(day=1)
    if "last month" in q:
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if "this month" in q:
        return first, today
    dates = [date.fromisoformat(x) for x in re.findall(r"\b(20\d\d-\d\d-\d\d)\b", q)]
    if dates:
        return min(dates), max(dates)
    for name, num in _MONTHS.items():
        if re.search(rf"\b{name}\b", q):
            year = today.year if num <= today.month else today.year - 1
            start = date(year, num, 1)
            end = (date(year + (num == 12), 1 if num == 12 else num + 1, 1) - timedelta(days=1))
            return start, min(end, today)
    return None


def _score(text: str, terms: list[str]) -> int:
    t = text.lower()
    return sum(3 if term.startswith("#") or "-" in term else 1 for term in terms if term in t)


def search(cache, question: str, today: date, limit: int = 15) -> list[dict]:
    """Days and projects that match the question, best first: {"day", "project", "line", "minutes", "why"}."""
    terms = words(question)
    span = date_range(question, today)
    start, end = span or (today - timedelta(days=SEARCH_DAYS - 1), today)
    entries = cache.days(start, end)
    hits = []
    for d, e in entries.items():
        for p in e.get("projects") or []:
            fields = {"project": p["name"], "line": p.get("line", ""), "tag": p.get("tag", ""),
                      "refs": " ".join(p.get("refs") or []), "tasks": " ".join(p.get("open") or []),
                      "prompt": p.get("last_prompt", "")}
            score = sum(_score(v, terms) for v in fields.values()) if terms else 0
            if score or (span and not terms):
                why = [k for k, v in fields.items() if terms and _score(v, terms)]
                hits.append({"day": d, "project": p["name"], "line": p.get("line", ""), "minutes": p["minutes"],
                             "tag": p.get("tag", ""), "refs": p.get("refs") or [], "score": score,
                             "why": ", ".join(why) or "in the days you named"})
    if terms and len(hits) < 5:  # look deeper: the prompts themselves
        from .report import aggregate
        known = {(h["day"], h["project"]) for h in hits}
        for d in sorted(entries, reverse=True):
            if not entries[d].get("n"):
                continue
            records = list(cache.store.iter_json(f"records/{d.isoformat()}"))
            _, projects = aggregate(records, cache.ov, cache.mode)
            for p in projects.values():
                score = sum(_score(q, terms) for _, q in p.prompts)
                if score and (d, p.name) not in known:
                    line = next((q.splitlines()[0][:160] for _, q in p.prompts if _score(q, terms)), "")
                    hits.append({"day": d, "project": p.name, "line": line, "minutes": p.minutes, "tag": "",
                                 "refs": p.refs, "score": score, "why": "your prompts"})
            if len(hits) >= limit:
                break
    hits.sort(key=lambda h: (-h["score"], -h["day"].toordinal()))
    return hits[:limit]


def context(cache, question: str, today: date) -> tuple[str, list[date]]:
    """A short, redacted excerpt of the saved notes for the days the question is about (newest first)."""
    span = date_range(question, today)
    if span:
        days = [d for d, e in cache.days(*span).items() if e.get("n")]
    else:
        found = search(cache, question, today, limit=40)
        days = sorted({h["day"] for h in found}, reverse=True)[:14] or [
            d for d, e in cache.days(today - timedelta(days=29), today).items() if e.get("n")]
    days = sorted(set(days), reverse=True)
    from .report import aggregate
    parts, used = [], 0
    for d in days:
        e = cache.get(d)
        records = list(cache.store.iter_json(f"records/{d.isoformat()}"))
        _, projects = aggregate(records, cache.ov, cache.mode)
        prompts = {p.name: [q.splitlines()[0][:120] for _, q in p.prompts[:8]] for p in projects.values()}
        lines = [f"## {d.isoformat()} ({d:%A})"]
        for p in e.get("projects") or []:
            bits = [f"- {p['name']}: {p['minutes']} min"]
            if p.get("tag"):
                bits.append(f"kind: {p['tag']}")
            if p.get("line"):
                bits.append(f"summary: {p['line']}")
            if p.get("refs"):
                bits.append("refs: " + ", ".join(p["refs"]))
            if p.get("open"):
                bits.append("open tasks: " + "; ".join(p["open"][:5]))
            if prompts.get(p["name"]):
                bits.append("prompts: " + " | ".join(prompts[p["name"]]))
            lines.append("; ".join(bits))
        block = redact("\n".join(lines))
        if used + len(block) > MAX_CONTEXT:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts), days


def ask_claude(cfg, question: str, notes: str, today: date) -> str:
    """Claude's answer, or raises RuntimeError with a plain message."""
    from .report import _claude_env
    exe = shutil.which(cfg.claude_bin)
    if not exe:
        raise RuntimeError("Claude Code isn't installed where Worklog can find it (Settings → Summaries).")
    if not notes:
        return "There's nothing recorded for the days your question is about."
    prompt = (
        f"Today is {today.isoformat()}. Answer a developer's question about their own work, using ONLY the work "
        "log notes below (taken from their Claude Code sessions). Answer in plain, simple English, short: one "
        "sentence to a short list. Give hours as e.g. '3h 20m'. Mention the day of each fact as [YYYY-MM-DD]. If "
        "the notes don't contain the answer, say so in one sentence. Do not use any tools.\n\n"
        f"<question>\n{question[:MAX_QUESTION]}\n</question>\n\n<notes>\n{notes}\n</notes>")
    SUMMARIZER_CWD.mkdir(parents=True, exist_ok=True)  # the collector never records these runs
    cmd = [exe, "-p", prompt, "--output-format", "text"]
    if cfg.summary_model:
        cmd += ["--model", cfg.summary_model]
    try:
        out = subprocess.run(cmd, cwd=SUMMARIZER_CWD, env=_claude_env(cfg), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=cfg.ai_timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Claude didn't answer: {exc}") from None
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError("Claude didn't answer: " + (out.stderr.strip().splitlines() or ["no output"])[-1][:200])
    return redact(out.stdout.strip())[:4000]


def history() -> list[dict]:
    try:
        data = json.loads(_history_path().read_text(encoding="utf-8"))
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def remember(question: str, answer: str) -> None:
    items = [{"q": redact(question)[:MAX_QUESTION], "a": redact(answer)[:4000],
              "at": datetime.now().astimezone().isoformat(timespec="seconds")}] + history()
    try:
        _history_path().parent.mkdir(parents=True, exist_ok=True)
        _history_path().write_text(json.dumps(items[:20]), encoding="utf-8")
    except OSError:
        pass
