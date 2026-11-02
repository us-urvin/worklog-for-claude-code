"""Daily report: merge records from all devices/accounts, summarize, render Markdown."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from . import overrides
from .config import DEFAULT_CLAUDE_DIR, SUMMARIZER_CWD, Config
from .overrides import Overrides
from .parser import is_noise, zero_tokens
from .redact import redact
from .store import GitStore
from .tags import tag_session, top_tag

log = logging.getLogger(__name__)
_MAX_AI_INPUT_CHARS = 24_000  # keep each summary call small and well below arg-size limits


def project_key(proj: dict) -> str:
    """Same repo on two machines (different paths) merges via its remote slug."""
    remote = proj.get("remote")
    return f"{remote['host']}/{remote['slug']}" if remote else f"{proj.get('kind')}:{proj.get('name')}"


def project_title(proj: dict) -> str:
    """The name on a card: the repo's own name when it has a remote, so a worktree folder
    ("xoo-fix-qty") still shows as "xoo"; else the folder or label."""
    remote = proj.get("remote")
    if isinstance(remote, dict) and remote.get("slug"):
        return str(remote["slug"]).rsplit("/", 1)[-1]
    return str(proj.get("name") or "?")


def _interval(r: dict) -> tuple[float, float] | None:
    try:
        a = datetime.fromisoformat(str(r.get("start"))).timestamp()
        b = datetime.fromisoformat(str(r.get("end"))).timestamp()
    except ValueError:
        return None
    return (a, b) if b >= a else None


def _union_minutes(spans: list[tuple[float, float]]) -> float:
    total, cur = 0.0, None
    for a, b in sorted(spans):
        if cur and a <= cur[1]:
            cur = (cur[0], max(cur[1], b))
        else:
            if cur:
                total += cur[1] - cur[0]
            cur = (a, b)
    if cur:
        total += cur[1] - cur[0]
    return total / 60


_MODEL_RE = re.compile(r"^claude-(?:(?P<a>opus|sonnet|haiku|fable)-(?P<v1>\d+(?:-\d)?)"
                       r"|(?P<v2>\d+(?:-\d)?)-(?P<b>opus|sonnet|haiku|fable))"
                       r"(?:-\d{8})?(?P<ctx>\[1m\])?$")


def model_label(model: str) -> str:
    """claude-opus-5-5 -> "Opus 5.5", claude-3-5-haiku-20241022 -> "Haiku 3.5". Unknown ids are kept as they are."""
    m = _MODEL_RE.match(model or "")
    if not m:
        return model
    family = (m["a"] or m["b"]).capitalize()
    version = (m["v1"] or m["v2"]).replace("-", ".")
    return f"{family} {version}" + (" (1M)" if m["ctx"] else "")


def model_rows(accounts: dict[str, dict]) -> list[dict]:
    """By-model rows per account, biggest first, with each model's share of that account's tokens."""
    rows = []
    for name, a in sorted(accounts.items()):
        totals = {m: sum(t.values()) for m, t in a["models"].items()}
        whole = sum(totals.values()) or 1
        for m, total in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
            sub = sum((a["models_sub"].get(m) or {}).values())
            rows.append({"account": name, "model": m, "label": model_label(m), "total": total, "sub": sub,
                         "share": round(100 * total / whole)})
    return rows


@dataclass
class ProjectDay:
    key: str
    name: str
    kind: str
    remote: dict | None
    accounts: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)
    sessions: int = 0
    minutes: int = 0
    tokens: dict[str, int] = field(default_factory=zero_tokens)
    prompts: list[tuple[str, str]] = field(default_factory=list)  # (start time, prompt)
    files: set[str] = field(default_factory=set)
    folders: set[str] = field(default_factory=set)
    commits: dict[str, str] = field(default_factory=dict)  # hash -> subject (deduped)
    branches: set[str] = field(default_factory=set)
    chats: int = 0  # claude.ai chat records (no token data)
    titles: list[str] = field(default_factory=list)  # chat titles, in order seen
    chat_items: list[dict] = field(default_factory=list)  # one per chat: start, title, prompts, summary
    wall_minutes: int = 0  # first to last message, overlapping sessions counted once
    approx: bool = False  # some records predate active time: minutes are first-to-last
    tag_weights: Counter = field(default_factory=Counter)  # tag -> minutes
    tasks: list[dict] = field(default_factory=list)  # Claude's to-do items: text, status
    sessions_list: list[dict] = field(default_factory=list)  # for "Edit": id, start, first prompt, minutes
    started_in: set[str] = field(default_factory=set)  # home-relative folders the misc sessions started in
    guess: dict | None = None  # "Probably xoo?" for sessions that found no repo
    moved: bool = False  # sessions were moved here or a rule applied
    detected: bool = False  # a session here was found by 0.4.0's detection (it started outside this repo)
    refs: list[str] = field(default_factory=list)  # PR and ticket numbers
    tests: Counter = field(default_factory=Counter)  # runs, failed, fixed
    claude_minutes: int = 0
    models: dict[str, dict[str, int]] = field(default_factory=dict)  # tokens per model, for the cost estimate

    @property
    def tag(self) -> str | None:
        return top_tag(self.tag_weights)


def is_chat(r: dict) -> bool:
    return r.get("source") == "claude.ai"


def _dedupe_chats(records: list[dict]) -> list[dict]:
    """The same export imported on two devices: keep one copy of each chat-day, the newest."""
    best: dict[tuple, dict] = {}
    out = []
    for r in records:
        if not is_chat(r):
            out.append(r)
            continue
        key = (r.get("session_id"), r.get("date"))
        old = best.get(key)
        if old is None or str(r.get("conversation_updated_at") or "") > str(old.get("conversation_updated_at") or ""):
            best[key] = r
    return out + list(best.values())


def _add(dst: dict[str, int], src: object) -> None:
    for k in dst:
        v = src.get(k, 0) if isinstance(src, dict) else 0
        dst[k] += v if isinstance(v, int) else 0


def _target(key: str, ov: Overrides, known: dict[str, dict]) -> dict:
    """What we know about the project a session is moved to."""
    if key in known:
        return known[key]
    if key in ov.projects:
        return ov.projects[key]
    if "/" in key and ":" not in key.split("/", 1)[0]:  # host/owner/repo
        host, slug = key.split("/", 1)
        return {"kind": "git", "name": slug.rsplit("/", 1)[-1], "remote": {"host": host, "slug": slug}}
    kind, _, name = key.partition(":")
    return {"kind": kind or "git", "name": name or key, "remote": None}


def aggregate(records: list[dict], ov: Overrides | None = None, time_mode: str = "active"):
    """Per-account and per-project totals for one day. `ov` applies your corrections (renames, moves,
    folder rules, tags). `time_mode` "active" counts time with messages at most 15 minutes apart;
    "wall" counts first to last message."""
    ov = ov or Overrides()
    accounts: dict[str, dict] = {}
    projects: dict[str, ProjectDay] = {}
    records = _dedupe_chats(records)
    known = {}
    for r in records:
        proj = r.get("project") or {}
        known.setdefault(project_key(proj), {"kind": proj.get("kind"), "name": proj.get("name"),
                                             "remote": proj.get("remote")})
    spans: dict[str, list[tuple[float, float]]] = {}
    active: dict[str, float] = {}
    for r in records:
        acct = accounts.setdefault(r.get("account", "?"), {
            "tokens": zero_tokens(), "sessions": 0, "chats": 0, "devices": set(), "models": {}, "models_sub": {}})
        _add(acct["tokens"], r.get("tokens"))
        if is_chat(r):
            acct["chats"] += 1
        else:
            acct["sessions"] += 1
        acct["devices"].add(r.get("device", "?"))
        for model, t in (r.get("tokens_by_model") or {}).items():
            _add(acct["models"].setdefault(model, zero_tokens()), t)
        for model, t in (r.get("subagent_tokens_by_model") or {}).items():  # records from 0.3.0 on
            _add(acct["models_sub"].setdefault(model, zero_tokens()), t)

        proj = r.get("project") or {}
        sid = str(r.get("session_id") or "")
        key = project_key(proj)
        moved_to = ov.moves.get(sid) if sid else None
        if moved_to is None and proj.get("kind") == "misc":
            moved_to = ov.rule_for(str(r.get("device", "")), proj.get("started_in") or proj.get("path"))
        info = proj
        if moved_to and moved_to != key:
            key, info = moved_to, _target(moved_to, ov, known)
        p = projects.get(key)
        if p is None:
            p = projects[key] = ProjectDay(key, ov.names.get(key) or project_title(info), str(info.get("kind")),
                                           info.get("remote"))
        p.moved = p.moved or bool(moved_to)
        p.detected = p.detected or (proj.get("detected_by") in ("cwd-other", "files", "commands") and not moved_to)
        p.accounts.add(r.get("account", "?"))
        p.devices.add(r.get("device", "?"))
        if is_chat(r):
            p.chats += 1
            if r.get("title") and r["title"] not in p.titles:
                p.titles.append(str(r["title"]))
            p.chat_items.append({"start": (r.get("start") or "")[11:16], "title": str(r.get("title") or "Chat"),
                                 "prompts": [q for q in r.get("prompts") or [] if isinstance(q, str)],
                                 "summary": str(r.get("summary") or "")})
        else:
            p.sessions += 1
        span = int(r.get("span_minutes") or 0)
        act = r.get("active_minutes")
        if not isinstance(act, int) or isinstance(act, bool):
            act, p.approx = span, p.approx or not is_chat(r)
        iv = _interval(r)
        if iv:
            spans.setdefault(key, []).append(iv)
        active[key] = active.get(key, 0) + (span if time_mode == "wall" else act)
        _add(p.tokens, r.get("tokens"))
        start = (r.get("start") or "")[11:16]
        prompts = [q for q in r.get("prompts") or [] if isinstance(q, str) and not is_noise(q)]
        p.prompts.extend((start, q) for q in prompts)
        p.files.update(r.get("files_touched") or [])
        p.branches.update(proj.get("branches") or [])
        if proj.get("folder"):
            p.folders.add(proj["folder"])
        if proj.get("kind") == "misc" and (proj.get("started_in") or proj.get("path")):
            p.started_in.add(str(proj.get("started_in") or proj.get("path")))
        if proj.get("guess") and not moved_to and not ov.noguess.get(sid) and p.guess is None:
            p.guess = dict(proj["guess"], sessions=[])
        if p.guess is not None and proj.get("guess") and not moved_to and not ov.noguess.get(sid):
            p.guess["sessions"].append(sid)
        for c in r.get("commits") or []:
            p.commits.setdefault(c.get("hash", ""), c.get("subject", ""))
        tag = ov.tags.get(sid) or tag_session(dict(r, prompts=prompts))
        p.tag_weights[tag] += max(1, act)
        for ref in r.get("refs") or []:
            if isinstance(ref, str) and ref not in p.refs and len(p.refs) < 20:
                p.refs.append(ref)
        p.tests.update({k: v for k, v in (r.get("tests") or {}).items() if isinstance(v, int)})
        p.claude_minutes += int(r.get("claude_minutes") or 0)
        for model, t in (r.get("tokens_by_model") or {}).items():
            _add(p.models.setdefault(model, zero_tokens()), t)
        for t in r.get("tasks") or []:
            if isinstance(t, dict) and t.get("text") and not any(x["text"] == t["text"] for x in p.tasks):
                p.tasks.append({"text": str(t["text"]), "status": "done" if t.get("status") == "done" else "open"})
        if sid:
            p.sessions_list.append({"id": sid, "start": start, "minutes": act, "account": r.get("account", "?"),
                                    "first": prompts[0].splitlines()[0][:120] if prompts else "",
                                    "chat": is_chat(r)})
    for key, p in projects.items():
        wall = _union_minutes(spans.get(key, []))
        p.wall_minutes = round(wall) if spans.get(key) else 0
        total = active.get(key, 0)
        # Two sessions at the same time are one stretch of your time, not two.
        p.minutes = round(min(total, wall)) if spans.get(key) and wall else round(total)
        p.prompts.sort()
        p.chat_items.sort(key=lambda c: c["start"])
        p.sessions_list.sort(key=lambda x: x["start"])
    return accounts, projects


# ------------------------------------------------------------ summarization
def _claude_env(cfg: Config) -> dict[str, str]:
    env = dict(os.environ)
    acct = cfg.account(cfg.summarizer_account)
    if acct and acct.config_dir != DEFAULT_CLAUDE_DIR:
        env["CLAUDE_CONFIG_DIR"] = str(acct.config_dir)
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    return env


_SUMMARY_KEYS = ("line", "paragraph", "bullets", "tag", "next")


def parse_summary(text: str) -> dict | None:
    """Claude's answer -> {"line", "paragraph", "bullets", "tag", "next"}, or None if it is unusable.

    Takes the JSON object even with text around it or in a code fence; falls back to "- " bullets
    (how summaries were written before 0.4.0). Every string is redacted and length-capped.
    """
    from .tags import TAGS
    out: dict | None = None
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            data = None
        if isinstance(data, dict):
            def clean(v, n):
                return redact(" ".join(str(v).split()))[:n] if isinstance(v, str) and v.strip() else ""
            def strings(v):
                return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
            bullets = [clean(b, 400) for b in strings(data.get("bullets"))]
            out = {"line": clean(data.get("line"), 160), "paragraph": clean(data.get("paragraph"), 900),
                   "bullets": ["- " + b for b in bullets if b][:8],
                   "tag": data.get("tag") if data.get("tag") in TAGS else "",
                   "next": [clean(x, 200) for x in strings(data.get("next")) if x.strip()][:6]}
            if not (out["line"] or out["paragraph"] or out["bullets"]):
                out = None
    if out is None:
        bullets = [redact(line.strip())[:400] for line in text.splitlines() if line.strip().startswith(("- ", "* "))]
        if not bullets:
            return None
        out = {"line": "", "paragraph": "", "bullets": ["- " + b[2:] for b in bullets], "tag": "", "next": []}
    if not out["bullets"] and out["paragraph"]:
        out["bullets"] = ["- " + out["paragraph"]]
    if not out["line"]:
        first = (out["paragraph"] or out["bullets"][0][2:]).split(". ")[0].rstrip(".")
        out["line"] = first[:157] + "…" if len(first) > 158 else first
    return out


def ai_summary(p: ProjectDay, day: date, cfg: Config) -> dict | None:
    """A plain-language summary of one project's day, written by `claude -p`, or None."""
    exe = shutil.which(cfg.claude_bin)
    if not exe:
        log.warning("claude binary %r not found; using plain summary", cfg.claude_bin)
        return None
    data = {
        "project": p.name, "kind": p.kind, "branches": sorted(p.branches), "folders": sorted(p.folders),
        "minutes_worked": p.minutes,
        "chats": [{"title": c["title"], "your_messages": c["prompts"], "chat_summary": c["summary"]}
                  for c in p.chat_items],
        "prompts_in_order": [q for _, q in p.prompts],
        "claude_todo_list": p.tasks,
        "commits": list(p.commits.values()), "files_edited": sorted(p.files)[:80],
    }
    if p.chat_items:
        data.pop("prompts_in_order")  # already grouped per chat
    from .tags import TAGS
    blob = json.dumps(data, ensure_ascii=False)[:_MAX_AI_INPUT_CHARS]
    prompt = (
        f"You are writing a developer's work log for {day.isoformat()}. The JSON below was extracted from their "
        "Claude Code sessions and/or claude.ai chats for ONE project. Describe what they got done or looked into.\n"
        "Write in plain, simple English that a manager or client understands: short sentences, past tense, "
        "no jargon or file paths unless they matter, no hype. Use ONLY the data given; if something is unclear, "
        "describe it in general terms rather than guess. Do not use any tools.\n"
        "Answer with ONE JSON object and nothing else:\n"
        '{"line": "one line for a timesheet, at most 120 characters, e.g. \'Reviewed PRs #503 and #505; '
        'triaged the mobile item-quantity bug\'",\n'
        ' "paragraph": "2-4 plain sentences about the day\'s work on this project",\n'
        ' "bullets": ["2-7 short points: what was built, fixed, reviewed or decided"],\n'
        f' "tag": "the ONE kind of work that took most of the time, one of: {", ".join(TAGS)}. Review = reviewing '
        'pull requests or someone else\'s code, or triaging tickets; Bug fix = finding and fixing a problem; '
        'Feature = building something new; Research = reading, asking or comparing without changing code; '
        'Planning = deciding what to do or writing a plan; Release = shipping, deploying, versions or CI setup",\n'
        ' "next": ["open items still to do, if the data shows any; else an empty list"]}\n\n'
        f"<data>\n{blob}\n</data>"
    )
    SUMMARIZER_CWD.mkdir(parents=True, exist_ok=True)  # excluded by the collector
    try:
        cmd = [exe, "-p", prompt, "--output-format", "text"]
        if cfg.summary_model:  # the background service doesn't see shell aliases or ANTHROPIC_MODEL
            cmd += ["--model", cfg.summary_model]
        out = subprocess.run(cmd, cwd=SUMMARIZER_CWD,
                             env=_claude_env(cfg), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=cfg.ai_timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("claude -p failed for %s: %s", p.name, exc)
        return None
    if out.returncode != 0:
        log.warning("claude -p exited %d for %s: %s", out.returncode, p.name, out.stderr.strip()[:300])
        return None
    parsed = parse_summary(out.stdout)
    if parsed is None:
        log.warning("claude -p gave no usable summary for %s", p.name)
    return parsed


def _clip(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def plain_line(p: ProjectDay) -> str:
    """A one-line description without AI: the chat titles, or the first prompt that says something
    (one-word answers such as "Approve" or "yes" are skipped)."""
    if p.chat_items:
        return _clip("; ".join(c["title"] for c in p.chat_items[:3]))
    firsts = [q.splitlines()[0].strip() for _, q in p.prompts if q.strip()]
    good = next((q for q in firsts if len(q.split()) >= 4), firsts[0] if firsts else "Work in Claude Code")
    return _clip(good)


def summary_line(s: dict | None, p: ProjectDay, mine: str | None = None) -> str:
    """The timesheet line: yours if you wrote one, else Claude's, else the first point of an older summary
    (written before 0.4.0, without a line), else a plain line from your prompts."""
    if mine:
        return mine
    s = s or {}
    if s.get("line"):
        return str(s["line"])
    bullets = [str(b).removeprefix("- ") for b in s.get("bullets") or [] if str(b).strip()]
    if s.get("source") == "ai" and bullets:
        return _clip(bullets[0].split(". ")[0].rstrip("."))
    return plain_line(p)


def summary_metas(store: GitStore, day: date) -> list[dict]:
    """Every device's saved summaries for `day`, newest first."""
    metas = [m for m in store.iter_json(f"reports/{day.isoformat()}") if isinstance(m, dict)]
    return sorted(metas, key=lambda m: str(m.get("generated_at", "")), reverse=True)


def legacy_fit(p: ProjectDay, projects: dict[str, ProjectDay]) -> str:
    """How far to trust a summary written before 0.4.1 (it doesn't say which sessions it covers).

    0.4.0 started filing sessions that began outside a repo under their real project. On a day where that
    happened, an older "R&D / misc" summary probably describes a session that has moved away ("other"),
    and the project it moved into is missing it ("partial"). Other days are fine.
    """
    if not any(q.detected for q in projects.values()):
        return "ok"
    if p.kind == "misc":
        return "other"
    return "partial" if p.detected else "ok"


def pick_summary(metas: list[dict], key: str, ids: list[str], legacy: str = "ok") -> tuple[dict, str, str]:
    """The saved summary that fits a card best: (summary, status, generated_at).

    Summaries written from 0.4.0 on list the sessions they cover. status is "ok" when one covers all of
    the card's sessions (or predates that list), "partial" when the best one covers only some (sessions
    were added or moved in since), and "other" when it covers none of them: it describes different work,
    for example after a session was moved to another project. Then the summary isn't shown at all.
    """
    want = set(ids)
    candidates = [(m, (m.get("summaries") or {}).get(key)) for m in metas]
    candidates = [(m, sm) for m, sm in candidates if isinstance(sm, dict)]
    if not candidates:
        return {}, "none", ""
    for m, sm in candidates:  # newest first: the newest one that covers everything
        covered = sm.get("sessions")
        if not isinstance(covered, list):
            return sm, legacy, str(m.get("generated_at", ""))
        if want <= set(covered):
            return sm, "ok", str(m.get("generated_at", ""))
    m, sm = max(candidates, key=lambda c: (len(want & set(c[1].get("sessions") or [])), str(c[0].get("generated_at"))))
    status = "partial" if want & set(sm.get("sessions") or []) else "other"
    return sm, status, str(m.get("generated_at", ""))


def chat_line(c: dict) -> str:
    """One chat as a short line: its summary if kept, else your first message."""
    detail = c["summary"] or (c["prompts"][0].splitlines()[0] if c["prompts"] else "")
    detail = detail if len(detail) <= 220 else detail[:219].rsplit(" ", 1)[0] + "…"
    return f"{c['title']}: {detail}" if detail and detail != c["title"] else c["title"]


def plain_summary(p: ProjectDay) -> list[str]:
    if p.chat_items:
        return [f"- {chat_line(c)}" for c in p.chat_items]
    lines = [f"- {q.splitlines()[0][:140]}" for _, q in p.prompts[:6]]
    if len(p.prompts) > 6:
        lines.append(f"- …and {len(p.prompts) - 6} more prompt(s)")
    return lines or ["- (no prompts captured)"]


# ------------------------------------------------------------------ render
def _n(v: int) -> str:
    return f"{v:,}"


def _dur(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def summarize_all(projects: dict[str, ProjectDay], day: date, cfg: Config, use_ai: bool) -> dict[str, dict]:
    out = {}
    for key, p in projects.items():
        smart = ai_summary(p, day, cfg) if use_ai else None
        if smart:
            out[key] = dict(smart, source="ai")
        else:
            out[key] = {"bullets": plain_summary(p), "source": "plain", "line": plain_line(p), "paragraph": "",
                        "tag": "", "next": []}
        out[key]["sessions"] = sorted(x["id"] for x in p.sessions_list)  # which sessions this summary covers
    return out


def render(day: date, cfg: Config, accounts: dict, projects: dict[str, ProjectDay], summaries: dict) -> str:
    devices = sorted({d for a in accounts.values() for d in a["devices"]})
    total_sessions = sum(a["sessions"] for a in accounts.values())
    total_chats = sum(a.get("chats", 0) for a in accounts.values())
    chats = f" · {total_chats} claude.ai chat(s)" if total_chats else ""
    out = [f"# Claude Code work log — {day.isoformat()}", "",
           f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} on `{cfg.device}` · "
           f"{total_sessions} session(s){chats} · devices: {', '.join(devices) or '—'}_", ""]
    if not accounts:
        out += ["No Claude activity recorded for this day.", ""]
        return "\n".join(out)

    if total_sessions:
        head = "| Account | Sessions | Input | Output | Cache write | Cache read | Total |"
        out += ["## Token usage by account", "", head, "|---|---:|---:|---:|---:|---:|---:|"]
        grand = zero_tokens()
        for name in sorted(accounts):
            a = accounts[name]
            if not a["sessions"]:
                continue  # chat-only account: the claude.ai export has no token data
            t = a["tokens"]
            _add(grand, t)
            out.append(f"| {name} | {a['sessions']} | {_n(t['input'])} | {_n(t['output'])} | "
                       f"{_n(t['cache_creation'])} | {_n(t['cache_read'])} | {_n(sum(t.values()))} |")
        out.append(f"| **All** | **{total_sessions}** | **{_n(grand['input'])}** | **{_n(grand['output'])}** | "
                   f"**{_n(grand['cache_creation'])}** | **{_n(grand['cache_read'])}** | "
                   f"**{_n(sum(grand.values()))}** |")
        out += ["", "### By model", "",
                "| Account | Model | Share | Input | Output | Cache write | Cache read | Sub-agents |",
                "|---|---|---:|---:|---:|---:|---:|---:|"]
        for row in model_rows(accounts):
            t = accounts[row["account"]]["models"][row["model"]]
            out.append(f"| {row['account']} | {row['label']} | {row['share']}% | {_n(t['input'])} | "
                       f"{_n(t['output'])} | {_n(t['cache_creation'])} | {_n(t['cache_read'])} | {_n(row['sub'])} |")
        out += ["", "> Token counts are usage as logged by Claude Code, not a bill. "
                "On Pro/Max plans they count against limits rather than cost money directly.", ""]

    out += ["## What I worked on", ""]
    ordered = sorted(projects.values(), key=lambda p: (p.kind == "chat", p.kind == "misc", -p.sessions, p.name))
    for p in ordered:
        where = f"`{p.remote['host']}/{p.remote['slug']}`" if p.remote else (
            "claude.ai" if p.kind == "chat" else
            "folders: " + ", ".join(sorted(p.folders)) if p.folders else "no git repo")
        work = [f"{p.sessions} session(s)"] if p.sessions else []
        work += [f"{p.chats} chat(s)"] if p.chats else []
        tokens = f"{_n(sum(p.tokens.values()))} tokens" if p.sessions else "no token data"
        s = summaries.get(p.key) or {}
        tag = s.get("tag") or p.tag
        out += [f"### {p.name}" + (f" · {tag}" if tag else ""), "",
                f"{where} · accounts: {', '.join(sorted(p.accounts))} · devices: {', '.join(sorted(p.devices))} · "
                f"{' · '.join(work)} · {'≈' if p.approx else ''}{_dur(p.minutes)} · {tokens} · "
                f"{len(p.files)} file(s) edited · {len(p.commits)} commit(s)", ""]
        if s.get("paragraph"):
            out += [s["paragraph"], ""]
        out += s.get("bullets") or plain_summary(p)
        out += [""]
        if p.tasks:
            out += ["Tasks:", ""] + [f"- [{'x' if t['status'] == 'done' else ' '}] {t['text']}" for t in p.tasks] + [""]
        if p.chat_items and summaries.get(p.key):  # AI bullets above; still list the chats themselves
            out += ["Chats:", ""] + [f"- {chat_line(c)}" for c in p.chat_items] + [""]
        if p.commits:
            out += ["Commits:", ""] + [f"- `{h}` {s}" for h, s in p.commits.items()] + [""]
    return "\n".join(out)


def build_report(cfg: Config, day: date, use_ai: bool, write: bool) -> tuple[str, str | None]:
    store = GitStore(cfg.store_repo)
    with store.lock():  # short lock: sync + read only
        store.pull()
        records = list(store.iter_json(f"records/{day.isoformat()}"))
        ov = overrides.load(store.path)
    accounts, projects = aggregate(records, ov, cfg.time_mode)
    summaries = summarize_all(projects, day, cfg, use_ai)  # AI calls run without the lock
    text = render(day, cfg, accounts, projects, summaries)
    if not write:
        return text, None
    rel = f"reports/{day.isoformat()}/{cfg.device}.md"
    meta = {"date": day.isoformat(), "device": cfg.device, "summaries": summaries,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    with store.lock():
        store.pull()
        store.write_text(rel, text)
        store.write_json(f"reports/{day.isoformat()}/{cfg.device}.json", meta)
        from . import digest
        digest.write(cfg, store)
        store.commit_and_push(f"report {day.isoformat()} ({cfg.device})")
    return text, str(cfg.store_repo / rel)


def load_saved_summaries(store: GitStore, day: date) -> dict:
    """Newest saved summaries for `day` from any device (used by the web UI)."""
    best: dict = {}
    for meta in store.iter_json(f"reports/{day.isoformat()}"):
        if isinstance(meta, dict) and meta.get("generated_at", "") > best.get("generated_at", ""):
            best = meta
    return best
