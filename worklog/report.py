"""Daily report: merge records from all devices/accounts, summarize, render Markdown."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime

from .config import DEFAULT_CLAUDE_DIR, SUMMARIZER_CWD, Config
from .parser import zero_tokens
from .redact import redact
from .store import GitStore

log = logging.getLogger(__name__)
_MAX_AI_INPUT_CHARS = 24_000  # keep each summary call small and well below arg-size limits


def project_key(proj: dict) -> str:
    """Same repo on two machines (different paths) merges via its remote slug."""
    remote = proj.get("remote")
    return f"{remote['host']}/{remote['slug']}" if remote else f"{proj.get('kind')}:{proj.get('name')}"


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


def _add(dst: dict[str, int], src: object) -> None:
    for k in dst:
        v = src.get(k, 0) if isinstance(src, dict) else 0
        dst[k] += v if isinstance(v, int) else 0


def aggregate(records: list[dict]):
    accounts: dict[str, dict] = {}
    projects: dict[str, ProjectDay] = {}
    for r in records:
        acct = accounts.setdefault(r.get("account", "?"), {
            "tokens": zero_tokens(), "sessions": 0, "devices": set(), "models": {}})
        _add(acct["tokens"], r.get("tokens"))
        acct["sessions"] += 1
        acct["devices"].add(r.get("device", "?"))
        for model, t in (r.get("tokens_by_model") or {}).items():
            _add(acct["models"].setdefault(model, zero_tokens()), t)

        proj = r.get("project") or {}
        key = project_key(proj)
        p = projects.setdefault(key, ProjectDay(key, str(proj.get("name", "?")), str(proj.get("kind")),
                                                proj.get("remote")))
        p.accounts.add(r.get("account", "?"))
        p.devices.add(r.get("device", "?"))
        p.sessions += 1
        p.minutes += int(r.get("span_minutes") or 0)
        _add(p.tokens, r.get("tokens"))
        start = (r.get("start") or "")[11:16]
        p.prompts.extend((start, q) for q in r.get("prompts") or [])
        p.files.update(r.get("files_touched") or [])
        p.branches.update(proj.get("branches") or [])
        if proj.get("folder"):
            p.folders.add(proj["folder"])
        for c in r.get("commits") or []:
            p.commits.setdefault(c.get("hash", ""), c.get("subject", ""))
    for p in projects.values():
        p.prompts.sort()
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


def ai_summary(p: ProjectDay, day: date, cfg: Config) -> list[str] | None:
    exe = shutil.which(cfg.claude_bin)
    if not exe:
        log.warning("claude binary %r not found; using plain summary", cfg.claude_bin)
        return None
    data = {
        "project": p.name, "kind": p.kind, "branches": sorted(p.branches), "folders": sorted(p.folders),
        "prompts_in_order": [q for _, q in p.prompts],
        "commits": list(p.commits.values()), "files_edited": sorted(p.files)[:80],
    }
    blob = json.dumps(data, ensure_ascii=False)[:_MAX_AI_INPUT_CHARS]
    prompt = (
        f"You are writing a developer's end-of-day work log for {day.isoformat()}. "
        "The JSON below was extracted from their Claude Code sessions for ONE project. "
        "Write 2-7 concise bullet points describing what was accomplished or investigated "
        "(features implemented, bugs fixed, refactors, research). Past tense, no fluff. "
        "Use ONLY the data given; if something is unclear, describe it generically rather than guess. "
        "Output only the bullets, each line starting with '- '. Do not use any tools.\n\n"
        f"<data>\n{blob}\n</data>"
    )
    SUMMARIZER_CWD.mkdir(parents=True, exist_ok=True)  # excluded by the collector
    try:
        out = subprocess.run([exe, "-p", prompt, "--output-format", "text"], cwd=SUMMARIZER_CWD,
                             env=_claude_env(cfg), capture_output=True, text=True, timeout=cfg.ai_timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("claude -p failed for %s: %s", p.name, exc)
        return None
    if out.returncode != 0:
        log.warning("claude -p exited %d for %s: %s", out.returncode, p.name, out.stderr.strip()[:300])
        return None
    bullets = [redact(line.strip())[:400] for line in out.stdout.splitlines()
               if line.strip().startswith(("- ", "* "))]
    return [("- " + b[2:]) for b in bullets] or None


def plain_summary(p: ProjectDay) -> list[str]:
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
        bullets = ai_summary(p, day, cfg) if use_ai else None
        out[key] = {"bullets": bullets or plain_summary(p), "source": "ai" if bullets else "plain"}
    return out


def render(day: date, cfg: Config, accounts: dict, projects: dict[str, ProjectDay], summaries: dict) -> str:
    devices = sorted({d for a in accounts.values() for d in a["devices"]})
    total_sessions = sum(a["sessions"] for a in accounts.values())
    out = [f"# Claude Code work log — {day.isoformat()}", "",
           f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} on `{cfg.device}` · "
           f"{total_sessions} session(s) · devices: {', '.join(devices) or '—'}_", ""]
    if not accounts:
        out += ["No Claude Code activity recorded for this day.", ""]
        return "\n".join(out)

    head = "| Account | Sessions | Input | Output | Cache write | Cache read | Total |"
    out += ["## Token usage by account", "", head, "|---|---:|---:|---:|---:|---:|---:|"]
    grand = zero_tokens()
    for name in sorted(accounts):
        a = accounts[name]
        t = a["tokens"]
        _add(grand, t)
        out.append(f"| {name} | {a['sessions']} | {_n(t['input'])} | {_n(t['output'])} | "
                   f"{_n(t['cache_creation'])} | {_n(t['cache_read'])} | {_n(sum(t.values()))} |")
    out.append(f"| **All** | **{total_sessions}** | **{_n(grand['input'])}** | **{_n(grand['output'])}** | "
               f"**{_n(grand['cache_creation'])}** | **{_n(grand['cache_read'])}** | **{_n(sum(grand.values()))}** |")
    out += ["", "### By model", "", "| Account | Model | Input | Output | Cache write | Cache read |",
            "|---|---|---:|---:|---:|---:|"]
    for name in sorted(accounts):
        for model, t in sorted(accounts[name]["models"].items()):
            out.append(f"| {name} | {model} | {_n(t['input'])} | {_n(t['output'])} | "
                       f"{_n(t['cache_creation'])} | {_n(t['cache_read'])} |")
    out += ["", "> Token counts are usage as logged by Claude Code, not a bill. "
            "On Pro/Max plans they count against limits rather than cost money directly.", ""]

    out += ["## What I worked on", ""]
    ordered = sorted(projects.values(), key=lambda p: (p.kind == "misc", -p.sessions, p.name))
    for p in ordered:
        where = f"`{p.remote['host']}/{p.remote['slug']}`" if p.remote else (
            "folders: " + ", ".join(sorted(p.folders)) if p.folders else "no git repo")
        out += [f"### {p.name}", "",
                f"{where} · accounts: {', '.join(sorted(p.accounts))} · devices: {', '.join(sorted(p.devices))} · "
                f"{p.sessions} session(s) · {_dur(p.minutes)} span · {_n(sum(p.tokens.values()))} tokens · "
                f"{len(p.files)} file(s) edited · {len(p.commits)} commit(s)", ""]
        out += summaries.get(p.key, {}).get("bullets") or plain_summary(p)
        out += [""]
        if p.commits:
            out += ["Commits:", ""] + [f"- `{h}` {s}" for h, s in p.commits.items()] + [""]
    return "\n".join(out)


def build_report(cfg: Config, day: date, use_ai: bool, write: bool) -> tuple[str, str | None]:
    store = GitStore(cfg.store_repo)
    with store.lock():  # short lock: sync + read only
        store.pull()
        records = list(store.iter_json(f"records/{day.isoformat()}"))
    accounts, projects = aggregate(records)
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
        store.commit_and_push(f"report {day.isoformat()} ({cfg.device})")
    return text, str(cfg.store_repo / rel)


def load_saved_summaries(store: GitStore, day: date) -> dict:
    """Newest saved summaries for `day` from any device (used by the web UI)."""
    best: dict = {}
    for meta in store.iter_json(f"reports/{day.isoformat()}"):
        if isinstance(meta, dict) and meta.get("generated_at", "") > best.get("generated_at", ""):
            best = meta
    return best
