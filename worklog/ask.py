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


_SHORT = {"pr", "prs", "mr", "ui", "qa", "ci", "db", "js", "ts", "go"}  # short words that mean something


def words(question: str) -> list[str]:
    out = []
    for w in _WORD.findall(question.lower()):
        w = w.strip(".-?!,")
        if not w or w in out:
            continue
        if (len(w) >= 3 and w not in _STOP) or w in _SHORT or w.startswith("#") or re.match(r"^[a-z][a-z0-9]+-\d+$", w):
            out.append(w)
    return out


def patterns(term: str) -> list[re.Pattern]:
    """How a word of the question is found in your notes. Words match by their start, so a typo at the end
    ("reviewn") or another ending ("reviewed", "reviews") still matches; "PR" also finds "pull request" and #505."""
    if term.startswith("#") or re.match(r"^[a-z][a-z0-9]+-\d+$", term):
        return [re.compile(re.escape(term), re.I)]
    if term in ("pr", "prs", "mr"):
        return [re.compile(r"\b(?:prs?|mrs?|pull requests?|merge requests?)\b", re.I), re.compile(r"(?<![\w&])#\d+\b")]
    base = re.sub(r"(?:ies|es|ed|ing|s|y|e)$", "", term) if len(term) > 4 else term
    stem = base[:5] if len(base) >= 3 else term
    return [re.compile(rf"\b{re.escape(stem)}", re.I)]


def _close_words(term: str, vocab: set[str]) -> list[str]:
    """Words in your notes spelled almost like `term` (for typos such as "setlement" or "fixx")."""
    import difflib
    if len(term) < 4 or term.startswith("#") or term in _SHORT:
        return []
    return difflib.get_close_matches(term, vocab, n=3, cutoff=0.8)


def _matches(text: str, pats: list[list[re.Pattern]]) -> tuple[set[int], int]:
    """(which question words appear in `text`, how many times in all)."""
    hit, count = set(), 0
    for i, group in enumerate(pats):
        n = sum(len(pt.findall(text)) for pt in group)
        if n:
            hit.add(i)
            count += n
    return hit, count


def snippet(text: str, pats: list[list[re.Pattern]], width: int = 200) -> str:
    """About `width` characters of `text` around the first match, cut at word boundaries."""
    text = " ".join(text.split())
    starts = [m.start() for group in pats for pt in group for m in [pt.search(text)] if m]
    if len(text) <= width:
        return text
    first = min(starts) if starts else 0
    a = max(0, first - width // 3)
    b = min(len(text), a + width)
    a = text.rfind(" ", 0, a) + 1 if a else 0
    cut = text[a:b]
    if b < len(text):
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
    return ("…" if a else "") + cut + ("…" if b < len(text) else "")


def highlight(text: str, pats: list[list[re.Pattern]]) -> str:
    """`text` as safe HTML with the matching words in <mark>."""
    from html import escape
    spans = sorted((m.start(), m.end()) for group in pats for pt in group for m in pt.finditer(text))
    out, pos = [], 0
    for a, b in spans:
        if a < pos:
            continue
        end = b
        while end < len(text) and (text[end].isalnum() or text[end] == "_"):
            end += 1  # mark the whole word ("revie" -> "reviewed")
        out.append(escape(text[pos:a]) + "<mark>" + escape(text[a:end]) + "</mark>")
        pos = end
    return "".join(out) + escape(text[pos:])


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


DEEP_DAYS = 60  # your prompts are searched this far back; summaries and tasks as far as SEARCH_DAYS


def search(cache, question: str, today: date, limit: int = 15) -> list[dict]:
    """Days and projects that match the question, best first. Each hit: day, project, minutes, tag, refs,
    `line` (an excerpt), `matched` (how many words of the question it has), `why` (where they were found) and
    `pats` (what was looked for, typo fixes included, for highlighting) and `also` (other days with the same note)."""
    from .report import aggregate
    terms = words(question)
    pats = [patterns(t) for t in terms]
    span = date_range(question, today)
    start, end = span or (today - timedelta(days=SEARCH_DAYS - 1), today)
    entries = cache.days(start, end)
    found: dict[tuple, dict] = {}

    def add(d, name, minutes, tag, refs, where, text):
        hit, count = _matches(text, pats) if pats else (set(), 0)
        if pats and not hit:
            return
        h = found.setdefault((d, name), {"day": d, "project": name, "minutes": minutes, "tag": tag, "refs": refs,
                                         "hit": set(), "count": 0, "why": [], "best": (0, 0, "")})
        h["hit"] |= hit
        h["count"] += count
        if where not in h["why"]:
            h["why"].append(where)
        if (len(hit), count) > h["best"][:2]:
            h["best"] = (len(hit), count, text)

    texts: list[tuple] = []  # (args, where, text): everything that can be searched, read once
    for d, e in entries.items():
        for p in e.get("projects") or []:
            args = (d, p["name"], p["minutes"], p.get("tag", ""), p.get("refs") or [])
            if not pats:
                if span:
                    texts.append((args, "the days you named", p.get("line", "")))
                continue
            texts += [(args, "project name", p["name"]), (args, "summary", p.get("line", "")),
                      (args, "kind of work", p.get("tag", "")), (args, "PR or ticket", " ".join(p.get("refs") or [])),
                      (args, "open tasks", " · ".join(p.get("open") or []))]
    if pats:  # the prompts themselves: where most of what you did is written down
        deep_from = max(start, today - timedelta(days=DEEP_DAYS - 1))
        for d in sorted((d for d in entries if d >= deep_from and entries[d].get("n")), reverse=True):
            _, projects = aggregate(list(cache.store.iter_json(f"records/{d.isoformat()}")), cache.ov, cache.mode)
            for p in projects.values():
                for _, q in p.prompts:
                    texts.append(((d, p.name, p.minutes, "", p.refs), "your prompts", q))
        missing = [i for i, group in enumerate(pats) if not any(pt.search(t) for _, _, t in texts for pt in group)]
        if missing:  # a word found nowhere is probably misspelt: also look for the closest real words
            vocab = {w for _, _, t in texts for w in re.findall(r"[a-z]{3,}", t.lower())}
            for i in missing:
                pats[i] = pats[i] + [re.compile(rf"\b{re.escape(w)}\b", re.I) for w in _close_words(terms[i], vocab)]
    for args, where, text in texts:
        add(*args, where, text)
    hits = []
    for h in found.values():
        text = h["best"][2]
        hits.append({"day": h["day"], "project": h["project"], "minutes": h["minutes"], "tag": h["tag"],
                     "refs": h["refs"], "line": snippet(text, pats) if pats else text, "matched": len(h["hit"]),
                     "score": 10 * len(h["hit"]) + min(h["count"], 9), "why": ", ".join(h["why"][:3]),
                     "of": len(terms), "pats": pats})
    hits.sort(key=lambda h: (-h["score"], -h["day"].toordinal()))
    unique: dict[tuple, dict] = {}  # the same note on many days is shown once, with the other days listed
    for h in hits:
        first = unique.setdefault((h["project"], h["line"]), dict(h, also=[]))
        if first is not h and first["day"] != h["day"]:
            first["also"].append(h["day"])
    hits = list(unique.values())
    for h in hits:
        h["also"].sort(reverse=True)
    if hits and len(terms) > 1:  # when some hits have every word, the ones with only one word are mostly noise
        best = hits[0]["matched"]
        hits = [h for h in hits if h["matched"] >= max(1, best - 1)]
    return hits[:limit]


def context(cache, question: str, today: date) -> tuple[str, list[date]]:
    """A short, redacted excerpt of the saved notes for the days the question is about (newest first)."""
    span = date_range(question, today)
    if span:
        days = [d for d, e in cache.days(*span).items() if e.get("n")]
    else:
        found = search(cache, question, today, limit=40)
        days = sorted({d for h in found for d in [h["day"], *h.get("also", [])]}, reverse=True)[:14] or [
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
        "log notes below (taken from their Claude Code sessions). Answer in plain, simple English that anyone "
        "understands: start with the direct answer in one sentence, then add at most a short list of '- ' points if "
        "it helps. No headings, no tables, no jargon. Give hours as e.g. '3h 20m'. Put the day of each fact as "
        "[YYYY-MM-DD]. Words may have typos (e.g. 'reviewn' means 'reviewed'). If the notes don't contain the "
        "answer, say so in one sentence. Do not use any tools.\n\n"
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
