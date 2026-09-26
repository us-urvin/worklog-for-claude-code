"""Local web UI: daily dashboard + settings. Standard library only.

Security model (it can edit config and run git/claude, so it is locked down):
  * binds to loopback only (127.0.0.1 and ::1), never to the network
  * Host header must be a known loopback name   -> blocks DNS-rebinding attacks
  * every POST needs a per-process CSRF token and a same-origin Origin header
  * all output is HTML-escaped; strict Content-Security-Policy; no external assets
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import services
from .collector import collect, sanitize_remote
from .config import (
    _NAME_RE,
    CONFIG_PATH,
    Account,
    Config,
    ConfigError,
    _expand,
    example_config,
    home_relative,
    load_config,
    save_config,
    under_home,
)
from .hooks import hook_installed, install_hook
from .identity import detected_logins, mask_email
from .report import aggregate, build_report, load_saved_summaries, plain_summary
from .store import GitStore

log = logging.getLogger(__name__)
DEFAULT_PORT = 8765
HOST_NAME = "worklog.localhost"  # *.localhost resolves to loopback in browsers and systemd-resolved
MAX_BODY = 64 * 1024
COLORS = ["#2F5BEA", "#0F9D8A", "#C98A12", "#8A4FBF", "#C4456A", "#3E8E3E"]


def e(v: object) -> str:
    return html.escape(str(v), quote=True)


OK_MESSAGES = {
    "setup": "Setup saved. Next: install the hook for each account below.",
    "account_added": "Account added. Log in to it once in a terminal (command below), then install its hook.",
    "account_removed": "Account removed from this device. Its records already synced are kept.",
    "saved": "Settings saved.",
    "named": "Login named. Its sessions are counted under that account from the next sync.",
    "job": "Started. This page updates when it finishes.",
}


# ================================================================== jobs
class Jobs:
    """One background job at a time (sync / summaries), so the UI never blocks."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"running": None, "finished": None, "message": "", "ok": True}

    def start(self, name: str, fn) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state["running"] = name

        def run():
            try:
                msg, ok = fn(), True
            except Exception as exc:  # noqa: BLE001 - report any failure to the UI
                log.exception("%s failed", name)
                msg, ok = f"{name} failed: {exc}", False
            with self._lock:
                self._state.update(running=None, finished=datetime.now().strftime("%H:%M"), message=msg, ok=ok)

        threading.Thread(target=run, daemon=True).start()
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


# ================================================================== helpers
def _try_config() -> Config | None:
    try:
        return load_config()
    except ConfigError:
        return None


def _store(cfg: Config) -> GitStore | None:
    try:
        return GitStore(cfg.store_repo)
    except ConfigError:
        return None


def _n(v: int) -> str:
    return f"{v:,}"


def _short(v: int) -> str:
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if v >= div:
            return f"{v / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(v)


def _dur(m: int) -> str:
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _join(items) -> str:
    items = [e(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _transcript_count(a: Account, cap: int = 5000) -> int:
    base = a.config_dir / "projects"
    n = 0
    if base.is_dir():
        for _ in base.rglob("*.jsonl"):
            n += 1
            if n >= cap:
                break
    return n


def _git_out(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ================================================================== layout
CSS = """
:root{--paper:#EEF1F4;--sheet:#fff;--ink:#1B2A41;--muted:#5B6B82;--rule:#D3DAE3;--accent:#2F5BEA;--ok:#0F7B5F;--bad:#B3261E;
font-family:"Ubuntu","Noto Sans","DejaVu Sans",system-ui,sans-serif;color:var(--ink);background:var(--paper);
font-variant-numeric:tabular-nums;line-height:1.5}
@media (prefers-color-scheme:dark){:root{--paper:#121822;--sheet:#1A2230;--ink:#DCE3ED;--muted:#93A1B5;--rule:#2A3444;--accent:#7B9BFF;--ok:#4CC9A0;--bad:#FF8A80}}
*{box-sizing:border-box}body{margin:0}a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 64px}
header{display:flex;align-items:center;gap:20px;padding:18px 0;border-bottom:1px solid var(--rule);margin-bottom:28px}
.brand{font-weight:700;font-size:1.05rem;color:var(--ink);text-decoration:none;margin-right:auto}
nav{display:flex;gap:22px}nav a{color:var(--muted);text-decoration:none;padding:4px 2px}nav a[aria-current]{color:var(--ink);border-bottom:2px solid var(--accent)}
h1{font-size:clamp(2rem,5vw,3.1rem);line-height:1.05;margin:0;font-weight:700;letter-spacing:-.02em}
h1 small{display:block;font-size:.4em;font-weight:400;color:var(--muted);letter-spacing:0;margin-top:6px}
h2{font-size:1.15rem;margin:0 0 12px}h3{font-size:1.1rem;margin:0}
.daynav{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap}.daynav .arrows{display:flex;gap:6px;margin-left:auto}
.lede{font-size:1.15rem;max-width:62ch;margin:14px 0 22px;color:var(--ink)}
.strip{background:var(--sheet);border:1px solid var(--rule);border-radius:10px;padding:14px 14px 6px;margin-bottom:30px}
.strip svg{width:100%;height:auto;display:block}.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:.85rem;color:var(--muted);margin:4px 2px 6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:36px;align-items:start}
@media (max-width:860px){.cols{grid-template-columns:1fr}}
.entry{padding:20px 0;border-top:1px solid var(--rule)}.entry:first-of-type{border-top:0;padding-top:4px}
.entry .where{color:var(--muted);font-size:.9rem;margin:2px 0 10px}.entry ul{margin:8px 0 12px;padding-left:1.1em;max-width:70ch}
.entry li{margin:3px 0}.src{font-size:.8rem;color:var(--muted)}
dl.facts{display:flex;flex-wrap:wrap;gap:4px 22px;margin:8px 0 0;font-size:.88rem}dl.facts div{display:flex;gap:6px}
dl.facts dt{color:var(--muted)}dl.facts dd{margin:0}
details{margin-top:8px;font-size:.9rem}summary{cursor:pointer;color:var(--muted)}details ul{margin:6px 0}
aside .panel{background:var(--sheet);border:1px solid var(--rule);border-radius:10px;padding:16px;margin-bottom:18px}
table{width:100%;border-collapse:collapse;font-size:.88rem}th,td{padding:6px 4px;text-align:right;border-bottom:1px solid var(--rule)}
td{white-space:nowrap}th{vertical-align:bottom;line-height:1.2}th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:500}tr:last-child td{border-bottom:0}
.tablewrap{overflow-x:auto}
button,.btn{font:inherit;font-size:.9rem;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:7px;padding:7px 14px;cursor:pointer;text-decoration:none;display:inline-block}
button.quiet,.btn.quiet{background:transparent;color:var(--accent)}button.danger{background:transparent;border-color:var(--bad);color:var(--bad)}
button:disabled{opacity:.55;cursor:progress}
.banner{border-radius:8px;padding:10px 14px;margin-bottom:22px;border:1px solid var(--rule);background:var(--sheet)}
.banner.ok{border-color:var(--ok)}.banner.bad{border-color:var(--bad);color:var(--bad)}
section.block{background:var(--sheet);border:1px solid var(--rule);border-radius:10px;padding:20px;margin-bottom:22px}
section.block p.help{color:var(--muted);margin:0 0 14px;max-width:70ch}
label{display:block;font-size:.9rem;margin:12px 0 4px}input[type=text],input[type=date],select{font:inherit;width:100%;max-width:420px;padding:7px 9px;border:1px solid var(--rule);border-radius:6px;background:var(--paper);color:var(--ink)}
.check{display:flex;gap:8px;align-items:center;margin:12px 0}.check label{margin:0}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px}
code,.cmd{font-family:"Ubuntu Mono","DejaVu Sans Mono",monospace;font-size:.9em}
.cmd{display:block;background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:8px 10px;margin:6px 0;overflow-x:auto;white-space:pre}
.state-ok{color:var(--ok)}.state-bad{color:var(--bad)}.muted{color:var(--muted)}
.empty{padding:28px;border:1px dashed var(--rule);border-radius:10px;text-align:center;color:var(--muted)}
@media (prefers-reduced-motion:no-preference){.bar{transition:opacity .15s}a:hover .bar{opacity:.8}}
"""

SCRIPT = """
(function(){var b=document.getElementById('jobstate');if(!b||b.dataset.running!=='1')return;
function poll(){fetch('/api/status',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){
if(!s.running){location.reload()}else{setTimeout(poll,2000)}}).catch(function(){setTimeout(poll,4000)})}
setTimeout(poll,2000)})();
"""


def layout(title: str, body: str, active: str, nonce: str, job: dict, ok: str | None = None, error: str | None = None) -> str:
    nav = "".join(f'<a href="{h}"{" aria-current=page" if k == active else ""}>{t}</a>'
                  for k, h, t in (("day", "/", "Today"), ("settings", "/settings", "Settings")))
    banners = ""
    if job.get("running"):
        banners += f'<div class="banner" id="jobstate" data-running="1" role="status">{e(job["running"])}…</div>'
    elif job.get("message") and job.get("finished"):
        cls = "ok" if job.get("ok") else "bad"
        banners += f'<div class="banner {cls}" role="status">{e(job["message"])} <span class="muted">({e(job["finished"])})</span></div>'
    if ok in OK_MESSAGES:
        banners += f'<div class="banner ok" role="status">{e(OK_MESSAGES[ok])}</div>'
    if error:
        banners += f'<div class="banner bad" role="alert">{e(error)}</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} – Worklog</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><a class="brand" href="/">Worklog</a><nav>{nav}</nav></header>{banners}{body}</div>
<script nonce="{nonce}">{SCRIPT}</script></body></html>"""


# ================================================================== pages
def _chart(history: list[tuple[date, dict[str, int]]], accounts: list[str], selected: date) -> str:
    w, h, top, bottom = 700, 130, 8, 22
    peak = max((sum(v.values()) for _, v in history), default=0) or 1
    slot = w / len(history)
    bw = slot * 0.62
    parts = []
    for i, (d, per) in enumerate(history):
        x = i * slot + (slot - bw) / 2
        y = h - bottom
        total = sum(per.values())
        rects = []
        for name in accounts:
            v = per.get(name, 0)
            if not v:
                continue
            bh = (h - top - bottom) * v / peak
            y -= bh
            rects.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" '
                         f'fill="{COLORS[accounts.index(name) % len(COLORS)]}"/>')
        if not rects:
            rects.append(f'<rect x="{x:.1f}" y="{h - bottom - 2}" width="{bw:.1f}" height="2" fill="var(--rule)"/>')
        is_sel = d == selected
        label_weight = "700" if is_sel else "400"
        label_fill = "var(--ink)" if is_sel else "var(--muted)"
        mark = (f'<rect x="{x - 3:.1f}" y="{top - 4}" width="{bw + 6:.1f}" height="{h - bottom - top + 6}" rx="4" '
                f'fill="none" stroke="var(--accent)" stroke-width="1.5"/>') if is_sel else ""
        tip = f"{d:%a %d %b}: {_n(total)} tokens" + "".join(f", {k} {_n(v)}" for k, v in per.items() if v)
        parts.append(f'<a href="/?d={d.isoformat()}"><title>{e(tip)}</title>{mark}{"".join(rects)}'
                     f'<rect x="{i * slot:.1f}" y="0" width="{slot:.1f}" height="{h}" fill="transparent"/>'
                     f'<text x="{x + bw / 2:.1f}" y="{h - 6}" text-anchor="middle" font-size="11" '
                     f'font-weight="{label_weight}" fill="{label_fill}">{d.day}</text></a>')
    legend = "".join(f'<span><i style="background:{COLORS[i % len(COLORS)]}"></i>{e(n)}</span>'
                     for i, n in enumerate(accounts))
    return (f'<div class="strip"><div class="legend">{legend or "<span>No activity yet</span>"}'
            f'<span style="margin-left:auto">Last {len(history)} days, peak {_short(peak) if peak > 1 else 0} tokens</span></div>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Tokens per day for the last {len(history)} days">'
            f'{"".join(parts)}</svg></div>')


def page_day(app: App, cfg: Config, day: date) -> tuple[str, str | None]:
    store = _store(cfg)
    if store is None:
        return (f'<div class="empty">No data repo found at <code>{e(home_relative(cfg.store_repo))}</code>. '
                f'<a href="/settings">Set it up in Settings</a>.</div>'), None
    records = list(store.iter_json(f"records/{day.isoformat()}"))
    accounts, projects = aggregate(records)
    saved = load_saved_summaries(store, day).get("summaries", {})

    history_days = [day - timedelta(days=i) for i in reversed(range(14))]
    history, names = [], [a.name for a in cfg.accounts]
    for d in history_days:
        per: dict[str, int] = {}
        for r in store.iter_json(f"records/{d.isoformat()}"):
            name = r.get("account", "?")
            per[name] = per.get(name, 0) + sum(v for v in (r.get("tokens") or {}).values() if isinstance(v, int))
            if name not in names:
                names.append(name)  # account configured on another device only
        history.append((d, per))

    prev_d, next_d = day - timedelta(days=1), day + timedelta(days=1)
    today = date.today()
    arrows = (f'<a class="btn quiet" href="/?d={prev_d.isoformat()}" aria-label="Previous day">‹</a>'
              + (f'<a class="btn quiet" href="/?d={next_d.isoformat()}" aria-label="Next day">›</a>' if day < today else "")
              + ('<a class="btn quiet" href="/">Today</a>' if day != today else ""))
    jump = (f'<form method="get" action="/" class="row" style="margin:0"><label for="d" class="muted" style="margin:0">Go to</label>'
            f'<input type="date" id="d" name="d" value="{day.isoformat()}" max="{today.isoformat()}" style="width:auto">'
            f'<button class="quiet">Show</button></form>')
    head = (f'<div class="daynav"><h1>{day:%A, %d %B}<small>{day.year}'
            f'{", today" if day == today else ""}</small></h1><div class="arrows">{arrows}</div></div>')

    busy = " disabled" if app.jobs.snapshot().get("running") else ""
    actions = (f'<div class="row"><form method="post" action="/action/collect"><input type="hidden" name="csrf" value="{app.csrf}">'
               f'<button{busy}>Sync now</button></form>'
               f'<form method="post" action="/action/report"><input type="hidden" name="csrf" value="{app.csrf}">'
               f'<input type="hidden" name="d" value="{day.isoformat()}">'
               f'<button class="quiet"{busy}>{"Rewrite summaries" if saved else "Write summaries"}</button></form>{jump}</div>')

    if not records:
        body = (head + '<p class="lede">Nothing recorded for this day yet.</p>' + _chart(history, names, day)
                + '<div class="empty">Sessions appear here after a Claude Code session ends, or when you press '
                  '<b>Sync now</b>. Other devices show up once they have synced.</div>' + actions)
        return body, None

    total = sum(sum(a["tokens"].values()) for a in accounts.values())
    sessions = sum(a["sessions"] for a in accounts.values())
    devices = sorted({d for a in accounts.values() for d in a["devices"]})
    lede = (f'<p class="lede">{_n(total)} tokens across {sessions} session{"s" * (sessions != 1)} in '
            f'{len(projects)} project{"s" * (len(projects) != 1)}, on {_join(devices)}.</p>')

    entries = []
    for p in sorted(projects.values(), key=lambda p: (p.kind == "misc", -sum(p.tokens.values()))):
        s = saved.get(p.key)
        bullets = s["bullets"] if s else plain_summary(p)
        src = ("Summary by Claude" if s and s.get("source") == "ai" else
               "Your prompts. Press “Write summaries” for a written summary." if not s else "Your prompts")
        items = "".join(f"<li>{e(b[2:] if b.startswith('- ') else b)}</li>" for b in bullets)
        where = (f"{e(p.remote['host'])}/{e(p.remote['slug'])}" if p.remote
                 else ("Folders: " + _join(sorted(p.folders)) if p.folders else "Not in a git repo"))
        facts = [("Accounts", _join(sorted(p.accounts))), ("Devices", _join(sorted(p.devices))),
                 ("Sessions", str(p.sessions)), ("Time", _dur(p.minutes)),
                 ("Tokens", _n(sum(p.tokens.values()))), ("Files edited", str(len(p.files)))]
        dl = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts)
        extra = ""
        if p.commits:
            extra += (f'<details><summary>{len(p.commits)} commit{"s" * (len(p.commits) != 1)}</summary><ul>'
                      + "".join(f"<li><code>{e(h)}</code> {e(sub)}</li>" for h, sub in p.commits.items()) + "</ul></details>")
        if p.files:
            extra += ('<details><summary>Files edited</summary><ul>'
                      + "".join(f"<li><code>{e(f)}</code></li>" for f in sorted(p.files)[:60]) + "</ul></details>")
        entries.append(f'<article class="entry"><h3>{e(p.name)}</h3><div class="where">{where}</div>'
                       f'<ul>{items}</ul><div class="src">{e(src)}</div><dl class="facts">{dl}</dl>{extra}</article>')

    acct_rows = "".join(
        f'<tr><td><i style="display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;'
        f'background:{COLORS[names.index(n) % len(COLORS)] if n in names else "var(--muted)"}"></i>{e(n)}</td>'
        f'<td>{_short(a["tokens"]["input"])}</td><td>{_short(a["tokens"]["output"])}</td>'
        f'<td>{_short(a["tokens"]["cache_creation"])}</td><td>{_short(a["tokens"]["cache_read"])}</td>'
        f'<td><b>{_short(sum(a["tokens"].values()))}</b></td></tr>' for n, a in sorted(accounts.items()))
    model_rows = "".join(
        f'<tr><td>{e(n)}</td><td style="text-align:left">{e(m)}</td><td>{_short(sum(t.values()))}</td></tr>'
        for n, a in sorted(accounts.items()) for m, t in sorted(a["models"].items()))
    aside = (f'<aside><div class="panel"><h2>Tokens by account</h2><div class="tablewrap"><table>'
             f'<tr><th>Account</th><th>In</th><th>Out</th><th>Cache<br>write</th><th>Cache<br>read</th><th>Total</th></tr>'
             f'{acct_rows}</table></div></div><div class="panel"><h2>By model</h2><div class="tablewrap"><table>'
             f'<tr><th>Account</th><th style="text-align:left">Model</th><th>Total</th></tr>{model_rows}</table></div>'
             f'<p class="muted" style="font-size:.8rem;margin:10px 0 0">Usage as logged by Claude Code, not a bill.</p></div></aside>')
    body = (head + lede + _chart(history, names, day) + actions
            + f'<div class="cols" style="margin-top:26px"><main><h2>What I worked on</h2>{"".join(entries)}</main>{aside}</div>')
    return body, None


def page_setup(app: App, form: dict | None = None) -> str:
    f = form or {}
    default_device = re.search(r'device_name = "([^"]+)"', example_config()).group(1)

    def v(k: str, d: str = "") -> str:
        return e(f.get(k, d))

    return f"""<h1>Set up this device<small>One time only. You can change everything later in Settings.</small></h1>
<form method="post" action="/setup" style="margin-top:24px"><input type="hidden" name="csrf" value="{app.csrf}">
<section class="block"><h2>This device</h2><p class="help">A short name so the report can tell your machines apart. Use a different name on each device.</p>
<label for="device">Device name</label><input type="text" id="device" name="device" value="{v('device', default_device)}" required pattern="[A-Za-z0-9_-]{{1,40}}"></section>
<section class="block"><h2>Data repo</h2><p class="help">A private git repo (GitHub, GitLab or Bitbucket) that every device syncs to. Create it empty on the website first.
If this device already has a clone, just enter its folder. Otherwise also paste the clone URL and it will be cloned for you. The URL must work without a password prompt (SSH deploy key, see README).</p>
<label for="store">Folder</label><input type="text" id="store" name="store" value="{v('store', '~/claude-worklog-data')}" required>
<label for="clone">Clone URL (only if the folder doesn't exist yet)</label><input type="text" id="clone" name="clone" value="{v('clone')}" placeholder="git@github.com:you/claude-worklog-data.git"></section>
<section class="block"><h2>First Claude account</h2><p class="help">The account you use with plain <code>claude</code> lives in <code>~/.claude</code>. You can add more accounts after setup.</p>
<label for="aname">Account name</label><input type="text" id="aname" name="aname" value="{v('aname', 'personal')}" required pattern="[A-Za-z0-9_-]{{1,40}}">
<label for="adir">Claude config folder</label><input type="text" id="adir" name="adir" value="{v('adir', '~/.claude')}" required></section>
<button>Save setup</button></form>"""


def _detected_section(cfg: Config, csrf_field: str) -> str:
    """Logins seen by the SessionStart hook. E-mails are masked; forms carry an opaque ref, never the id."""
    by_dir = cfg.accounts_by_dir()
    rows = []
    for login in detected_logins():
        for d in login["dirs"]:
            if d not in by_dir:
                continue  # folder not configured on this device
            current = cfg.resolve_account(d, login["account_id"], login["email"])
            linked = current.match_account_id == login["account_id"] or (
                current.match_email and login["email"] and current.match_email.casefold() == login["email"].casefold())
            status = (f'Counted as <b>{e(current.name)}</b>' if linked
                      else f'Counted as <b>{e(current.name)}</b> <span class="muted">(folder default)</span>')
            names = "".join(f'<option value="{e(a.name)}">' for a in by_dir[d])
            list_id = f"names-{login['ref']}-{len(rows)}"
            seen = login["last_seen"][:16].replace("T", " ")
            rows.append(
                f'<div class="entry"><h3>{e(mask_email(login["email"]))}</h3>'
                f'<div class="where"><code>{e(home_relative(d))}</code> · last seen {e(seen)} · {status}</div>'
                f'<form method="post" action="/accounts/name" class="row" style="margin-top:4px">{csrf_field}'
                f'<input type="hidden" name="ref" value="{e(login["ref"])}"><input type="hidden" name="dir" value="{e(home_relative(d))}">'
                f'<label for="{list_id}-in" class="muted" style="margin:0">Account name</label>'
                f'<input type="text" id="{list_id}-in" name="name" list="{list_id}" value="{e(current.name) if linked else ""}" '
                f'required pattern="[A-Za-z0-9_-]{{1,40}}" style="width:auto"><datalist id="{list_id}">{names}</datalist>'
                f'<button class="quiet">{"Rename" if linked else "Name this account"}</button></form></div>')
    body = "".join(rows) or ('<div class="empty">No logins recorded yet. Install the hook, then start a Claude Code '
                             'session. Each account you log in with appears here.</div>')
    return (f'<section class="block"><h2>Detected accounts</h2><p class="help">Accounts seen logged in when a session started. '
            f'If you switch accounts in one folder with <code>/logout</code> and <code>/login</code>, give each one a name so '
            f'its sessions are counted separately. Sessions from a login without a name go to the folder&#39;s default account. '
            f'Only the name is written to your data repo; the e-mail and account id stay on this device.</p>{body}</section>')


def page_settings(app: App, cfg: Config) -> str:
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    store = _store(cfg)
    if store:
        remotes = [sanitize_remote(u) for u in _git_out(cfg.store_repo, "remote", "get-url", "origin").splitlines()]
        remote = next((f"{r['host']}/{r['slug']}" for r in remotes if r), "none (local only)")
        last = _git_out(cfg.store_repo, "log", "-1", "--format=%cr") or "no commits yet"
        store_state = f'<span class="state-ok">Connected</span>. Remote: {e(remote)}. Last change {e(last)}.'
    else:
        store_state = '<span class="state-bad">Not a git clone.</span> Clone your private data repo into this folder.'

    rows = []
    for a in cfg.accounts:
        exists = a.config_dir.is_dir()
        n = _transcript_count(a) if exists else 0
        hooked = hook_installed(a)
        env = "" if a.config_dir == Path.home() / ".claude" else f"CLAUDE_CONFIG_DIR={home_relative(a.config_dir)} "
        login = f'<span class="cmd">{e(env)}claude\n# then type /login and sign in with this account</span>'
        state = (f'<span class="state-ok">{n} session file{"s" * (n != 1)}</span>' if exists and n
                 else '<span class="state-bad">Not logged in yet</span>' if not exists else '<span class="muted">No sessions yet</span>')
        hook_btn = ('<span class="state-ok">Hook installed</span>' if hooked else
                    f'<form method="post" action="/accounts/hook">{c}<input type="hidden" name="name" value="{e(a.name)}">'
                    f'<button class="quiet">Install hook</button></form>')
        remove = (f'<form method="post" action="/accounts/remove">{c}<input type="hidden" name="name" value="{e(a.name)}">'
                  f'<button class="danger">Remove</button></form>') if len(cfg.accounts) > 1 else ""
        rows.append(f'<div class="entry"><h3>{e(a.name)}</h3><div class="where"><code>{e(home_relative(a.config_dir))}</code></div>'
                    f'<div class="row" style="margin-top:4px">{state}{hook_btn}{remove}</div>'
                    f'{"" if exists and n else "<details open><summary>How to log in</summary>" + login + "</details>"}</div>')

    detected = _detected_section(cfg, c)
    acct_opts = "".join(f'<option{" selected" if a.name == cfg.summarizer_account else ""}>{e(a.name)}</option>' for a in cfg.accounts)
    found = shutil.which(cfg.claude_bin)
    claude_state = (f'<span class="state-ok">Found at {e(home_relative(found))}</span>' if found
                    else '<span class="state-bad">Not found. Summaries will fall back to your prompts.</span>')
    svc = services.status()
    svc_rows = "".join(f"<tr><td><code>{e(u)}</code></td><td>{e(s)}</td></tr>" for u, s in svc.items())
    report_on = services.report_timer_enabled()
    return f"""<h1>Settings<small>Device {e(cfg.device)}</small></h1>
<section class="block" style="margin-top:24px"><h2>Claude accounts on this device</h2>
<p class="help">Each account keeps its sessions in its own folder. Installing the hook makes every finished session sync automatically.</p>
{"".join(rows)}
<form method="post" action="/accounts/add" style="border-top:1px solid var(--rule);padding-top:14px;margin-top:6px">{c}
<h3>Add an account</h3><label for="name">Name</label><input type="text" id="name" name="name" placeholder="work" required pattern="[A-Za-z0-9_-]{{1,40}}">
<label for="dir">Claude config folder (leave empty for <code>~/.claude-&lt;name&gt;</code>)</label><input type="text" id="dir" name="dir" placeholder="~/.claude-work">
<div class="row"><button>Add account</button></div></form></section>
{detected}

<section class="block"><h2>Summaries and daily report</h2>
<form method="post" action="/settings/report">{c}
<div class="check"><input type="checkbox" id="use_ai" name="use_ai" value="1"{" checked" if cfg.use_ai else ""}><label for="use_ai">Write summaries with Claude (uses a small amount of tokens)</label></div>
<label for="summ">Account that writes the summaries</label><select id="summ" name="summarizer">{acct_opts}</select>
<label for="bin">Claude command</label><input type="text" id="bin" name="claude_bin" value="{e(cfg.claude_bin)}"><div class="muted" style="font-size:.85rem;margin-top:4px">{claude_state}</div>
<div class="row"><button>Save changes</button></div></form>
<form method="post" action="/settings/report-timer" style="border-top:1px solid var(--rule);padding-top:14px;margin-top:16px">{c}
<p class="help">Build the daily report automatically at 23:30 on this device. Turn this on for one device only.</p>
<input type="hidden" name="enabled" value="{"0" if report_on else "1"}">
<button class="quiet"{"" if services.available() else " disabled"}>{"Turn off daily report here" if report_on else "Turn on daily report here"}</button></form></section>

<section class="block"><h2>This device</h2>
<form method="post" action="/settings/device">{c}
<label for="device">Device name</label><input type="text" id="device" name="device" value="{e(cfg.device)}" required pattern="[A-Za-z0-9_-]{{1,40}}">
<p class="muted" style="font-size:.85rem">Renaming files new sessions under the new name. Earlier days keep the old one.</p>
<label for="store">Data repo folder</label><input type="text" id="store" name="store" value="{e(home_relative(cfg.store_repo))}" required>
<p style="font-size:.9rem">{store_state}</p><div class="row"><button>Save changes</button></div></form></section>

<section class="block"><h2>Background services</h2><p class="help">These keep running after you close the browser and start again when you log in.</p>
<div class="tablewrap"><table><tr><th>Service</th><th>State</th></tr>{svc_rows}</table></div>
<p class="muted" style="font-size:.85rem">Config file: <code>{e(home_relative(cfg.path))}</code></p></section>"""


# ================================================================== app / routing
class App:
    def __init__(self, port: int):
        self.port = port
        self.csrf = secrets.token_urlsafe(32)
        self.jobs = Jobs()
        self.hosts = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", HOST_NAME, "[::1]")}

    # ---- POST handlers: return (redirect_location | None, error | None, form)
    def post(self, path: str, form: dict):
        cfg = _try_config()
        if path == "/setup":
            return self._setup(form)
        if cfg is None:
            return "/setup", None, form
        if path == "/action/collect":
            today = date.today()
            started = self.jobs.start("Syncing", lambda: f"Synced: {collect(cfg, [today - timedelta(days=1), today])} new or updated session record(s).")
            return ("/?ok=job" if started else "/"), None, form
        if path == "/action/report":
            day = _parse_date(form.get("d")) or date.today()

            def job():
                collect(cfg, [day])
                build_report(cfg, day, use_ai=cfg.use_ai, write=True)
                return f"Summaries written for {day:%d %b}."
            started = self.jobs.start("Writing summaries", job)
            return (f"/?d={day.isoformat()}&ok=job" if started else f"/?d={day.isoformat()}"), None, form
        if path == "/accounts/add":
            return self._account_add(cfg, form)
        if path == "/accounts/remove":
            name = form.get("name", "")
            keep = [a for a in cfg.accounts if a.name != name]
            if not keep or len(keep) == len(cfg.accounts):
                return None, "That account can't be removed.", form
            cfg.accounts = keep
            if cfg.summarizer_account == name:
                cfg.summarizer_account = keep[0].name
            return self._save(cfg, "account_removed")
        if path == "/accounts/name":
            return self._account_name(cfg, form)
        if path == "/accounts/hook":
            acct = cfg.account(form.get("name", ""))
            if acct is None:
                return None, "Unknown account.", form
            ok, msg = install_hook(acct)
            return ("/settings?ok=saved" if ok else None), (None if ok else msg), form
        if path == "/settings/report":
            summ = form.get("summarizer", "")
            binv = form.get("claude_bin", "").strip()
            if cfg.account(summ) is None:
                return None, "Pick one of your accounts to write summaries.", form
            if not binv or len(binv) > 300 or any(ch in binv for ch in "\n\r\0"):
                return None, "Enter the claude command or its full path.", form
            cfg.use_ai, cfg.summarizer_account, cfg.claude_bin = form.get("use_ai") == "1", summ, binv
            return self._save(cfg, "saved")
        if path == "/settings/report-timer":
            try:
                services.set_report_timer(form.get("enabled") == "1")
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                return None, str(exc), form
            return "/settings?ok=saved", None, form
        if path == "/settings/device":
            device = form.get("device", "")
            store = _expand(form.get("store", "") or "~/claude-worklog-data")
            if not _NAME_RE.match(device):
                return None, "Device name: use 1-40 letters, digits, '_' or '-'.", form
            if not under_home(store):
                return None, "The data repo folder must be inside your home folder.", form
            cfg.device, cfg.store_repo = device, store
            return self._save(cfg, "saved")
        return None, "Unknown action.", form

    def _save(self, cfg: Config, ok: str):
        try:
            save_config(cfg)
        except (ConfigError, OSError) as exc:
            return None, f"Couldn't save settings: {exc}", {}
        return f"/settings?ok={ok}", None, {}

    def _account_add(self, cfg: Config, form: dict):
        name = form.get("name", "").strip()
        if not _NAME_RE.match(name):
            return None, "Account name: use 1-40 letters, digits, '_' or '-'.", form
        if cfg.account(name):
            return None, f"There is already an account called {name}.", form
        cdir = _expand(form.get("dir", "").strip() or f"~/.claude-{name}")
        if not under_home(cdir):
            return None, "The Claude config folder must be inside your home folder.", form
        if any(a.config_dir == cdir for a in cfg.accounts):
            return None, ("Another account already uses that folder. To count a second login in the same folder, "
                          "start a session with it, then name it under Detected accounts."), form
        cfg.accounts.append(Account(name, cdir))
        return self._save(cfg, "account_added")

    def _account_name(self, cfg: Config, form: dict):
        """Give a detected login a name: link it to an existing account in its folder, or add one."""
        name, ref_ = form.get("name", "").strip(), form.get("ref", "")
        login = next((x for x in detected_logins() if secrets.compare_digest(x["ref"], ref_)), None)
        if login is None:
            return None, "That login is no longer recorded on this device.", form
        cdir = _expand(form.get("dir", ""))
        if cdir not in login["dirs"] or cdir not in cfg.accounts_by_dir():
            return None, "That folder isn't configured for this login.", form
        if not _NAME_RE.match(name):
            return None, "Account name: use 1-40 letters, digits, '_' or '-'.", form
        uid = login["account_id"]
        target = cfg.account(name)
        if target is not None and target.config_dir != cdir:
            return None, f"{name} is an account for another folder. Pick a different name.", form
        if target is not None and target.match_account_id not in (None, uid):
            return None, f"{name} is already linked to another login.", form
        linked = next((a for a in cfg.accounts_by_dir()[cdir] if a.match_account_id == uid), None)
        if target is None and linked is not None:  # new name for an already-named login: rename it
            cfg.accounts = [replace(a, name=name) if a is linked else a for a in cfg.accounts]
            if cfg.summarizer_account == linked.name:
                cfg.summarizer_account = name
        elif target is None:
            cfg.accounts.append(Account(name, cdir, match_account_id=uid))
        elif linked is not None and linked is not target:
            return None, f"This login is already named {linked.name}. Rename that one instead.", form
        else:  # link an existing account (for example the folder's default) to this login
            cfg.accounts = [replace(a, match_account_id=uid) if a is target else a for a in cfg.accounts]
        return self._save(cfg, "named")

    def _setup(self, form: dict):
        device, aname = form.get("device", "").strip(), form.get("aname", "").strip()
        store, adir = _expand(form.get("store", "").strip()), _expand(form.get("adir", "").strip())
        clone = form.get("clone", "").strip()
        if not _NAME_RE.match(device) or not _NAME_RE.match(aname):
            return None, "Names can use 1-40 letters, digits, '_' or '-'.", form
        if not under_home(store) or not under_home(adir):
            return None, "Folders must be inside your home folder.", form
        if not (store / ".git").exists():
            if not clone:
                return None, f"{home_relative(store)} is not a git clone yet. Paste the clone URL of your private data repo.", form
            if clone.startswith("-") or not re.match(r"^(https://|ssh://|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:|[A-Za-z0-9_.-]+:)\S+$", clone):
                return None, "That doesn't look like a git clone URL (use git@host:owner/repo.git or https://…).", form
            if store.exists() and any(store.iterdir()):
                return None, f"{home_relative(store)} already exists and isn't empty.", form
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
            env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
            try:
                r = subprocess.run(["git", "clone", "--", clone, str(store)], capture_output=True, text=True, timeout=120, env=env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return None, f"git clone failed: {exc}", form
            if r.returncode != 0:
                return None, f"git clone failed: {r.stderr.strip()[-300:]}", form
        cfg = Config(device=device, store_repo=store, accounts=[Account(aname, adir)], summarizer_account=aname,
                     claude_bin=shutil.which("claude") or "claude")
        try:
            save_config(cfg, CONFIG_PATH)
        except (ConfigError, OSError) as exc:
            return None, f"Couldn't save settings: {exc}", form
        return "/settings?ok=setup", None, form


def _parse_date(v: str | None) -> date | None:
    try:
        d = date.fromisoformat(v or "")
    except ValueError:
        return None
    return min(d, date.today())


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "claude-worklog"
        sys_version = ""

        def log_message(self, fmt, *args):  # route access logs to our logger, not stderr
            log.debug("%s %s", self.address_string(), fmt % args)

        # ---------------------------------------------------------- guards
        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").lower() in app.hosts

        def _send(self, status: int, body: str, ctype="text/html; charset=utf-8", nonce: str = ""):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")  # "no-referrer" makes browsers send Origin: null on POST
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
                             "img-src 'self' data:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location: str):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _page(self, title, body, active, ok=None, error=None, status=200):
            nonce = secrets.token_urlsafe(16)
            self._send(status, layout(title, body, active, nonce, app.jobs.snapshot(), ok, error), nonce=nonce)

        # ---------------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "Forbidden host", "text/plain")
            try:
                url = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/api/status":
                    return self._send(200, json.dumps(app.jobs.snapshot()), "application/json")
                cfg = _try_config()
                if url.path == "/setup" or (cfg is None and url.path in ("/", "/settings")):
                    if cfg is not None:
                        return self._redirect("/settings")
                    return self._page("Set up", page_setup(app), "settings")
                if url.path == "/":
                    day = _parse_date(q.get("d")) or date.today()
                    body, err = page_day(app, cfg, day)
                    return self._page(f"{day:%d %b %Y}", body, "day", q.get("ok"), err)
                if url.path == "/settings":
                    return self._page("Settings", page_settings(app, cfg), "settings", q.get("ok"))
                return self._send(404, "Not found", "text/plain")
            except Exception:  # noqa: BLE001
                log.exception("GET %s failed", self.path)
                return self._send(500, "Something went wrong. See ~/.local/state/claude-worklog/worklog.log", "text/plain")

        # ---------------------------------------------------------- POST
        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "Forbidden host", "text/plain")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc.lower() not in app.hosts:
                return self._send(403, "Cross-origin request blocked", "text/plain")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                return self._send(413, "Request too large", "text/plain")
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8", "replace"),
                                                   keep_blank_values=True).items()}
            if not secrets.compare_digest(form.get("csrf", ""), app.csrf):
                return self._send(403, "Form expired. Go back and reload the page.", "text/plain")
            path = urlsplit(self.path).path
            try:
                location, error, kept = app.post(path, form)
            except Exception as exc:  # noqa: BLE001
                log.exception("POST %s failed", path)
                location, error, kept = None, f"Something went wrong: {exc}", form
            if location:
                return self._redirect(location)
            cfg = _try_config()
            if cfg is None or path == "/setup":
                return self._page("Set up", page_setup(app, kept), "settings", error=error, status=400)
            return self._page("Settings", page_settings(app, cfg), "settings", error=error, status=400)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def serve(port: int = DEFAULT_PORT) -> None:
    app = App(port)
    handler = make_handler(app)
    try:
        servers = [_Server(("127.0.0.1", port), handler)]
    except OSError as exc:
        raise SystemExit(f"Port {port} is busy ({exc}). Is the service already running? Try another --port.") from exc
    try:
        servers.append(_Server6(("::1", port), handler))  # browsers may try ::1 first for *.localhost
    except OSError:
        log.info("IPv6 loopback unavailable; serving on 127.0.0.1 only")
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"Worklog UI: http://{HOST_NAME}:{port}  (also http://localhost:{port})", flush=True)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.server_close()
