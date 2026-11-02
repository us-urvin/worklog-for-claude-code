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
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, chats, checks, daycache, forget, overrides, services, syncstatus, updates, viz, watcher
from .collector import collect
from .config import (
    _NAME_RE,
    CONFIG_PATH,
    MODEL_RE,
    Account,
    Config,
    ConfigError,
    _expand,
    default_downloads_dir,
    example_config,
    home_relative,
    load_config,
    save_config,
    under_home,
)
from .hooks import hook_installed, install_hook
from .identity import detected_logins, mask_email
from .report import (
    aggregate,
    build_report,
    legacy_fit,
    model_rows,
    pick_summary,
    plain_summary,
    project_key,
    project_title,
    summary_line,
    summary_metas,
)
from .store import GitStore
from .tags import TAGS

log = logging.getLogger(__name__)
DEFAULT_PORT = 8765
# HTML pattern for names. "-" is escaped: browsers compile patterns with the v flag, where a bare "-" fails.
NAME_PATTERN = r"[A-Za-z0-9_\-]{1,40}"
HISTORY_DAYS = 35  # five weeks: the calendar on the day page (the bar chart shows the last 14)
REFILE_DAYS = 30  # Claude Code's default transcript retention
HOST_NAME = "worklog.localhost"  # *.localhost resolves to loopback in browsers and systemd-resolved
MAX_BODY = 64 * 1024
MAX_UPLOAD = 200 * 1024 * 1024  # claude.ai conversations export (zip or json)
EXPORT_SETTINGS_URL = "https://claude.ai/settings/data-privacy-controls"
COLORS = ["#8B7CFF", "#22D3EE", "#F472B6", "#FBBF24", "#34D399", "#FB923C"]  # readable on dark and light glass


def e(v: object) -> str:
    return html.escape(str(v), quote=True)


OK_MESSAGES = {
    "setup": "Setup saved. Next: install the hook for each account below.",
    "account_added": "Account added. Log in to it once in a terminal (command below), then install its hook.",
    "account_removed": "Account removed from this device. Its records already synced are kept.",
    "saved": "Settings saved.",
    "named": "Login named. Its sessions from the last 30 days are being moved to that account now.",
    "job": "Started. This page updates when it finishes.",
    "chat_saved": "Chat import settings saved.",
    "edited": "Saved. Your changes sync to your other computers too.",
    "guess_yes": "Moved. Press Write summaries to update the summary.",
    "guess_no": "Got it. Those sessions stay where they are.",
}


# ================================================================== jobs
class JobFailed(Exception):
    """A job that failed in an expected way; its message is shown as is."""


class Jobs:
    """One background job at a time (sync / summaries), so the UI never blocks."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"running": None, "finished": None, "finished_at": 0.0, "message": "", "ok": True}

    def start(self, name: str, fn) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state["running"] = name

        def run():
            try:
                msg, ok = fn(), True
            except JobFailed as exc:  # an expected failure that already explains itself
                msg, ok = str(exc), False
            except Exception as exc:  # noqa: BLE001 - report any failure to the UI
                log.exception("%s failed", name)
                msg, ok = f"{name} failed: {exc}", False
            with self._lock:
                self._state.update(running=None, finished=datetime.now().strftime("%H:%M"), finished_at=time.time(),
                                   message=msg, ok=ok)

        threading.Thread(target=run, daemon=True).start()
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def dismiss(self) -> None:
        """Hide the last result (the ✕ on its banner)."""
        with self._lock:
            if not self._state["running"]:
                self._state.update(message="", finished=None, finished_at=0.0)


# How long a finished job's message stays on the pages (errors longer, so they aren't missed).
RESULT_SECONDS, ERROR_SECONDS = 120, 900


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
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ================================================================== layout
CSS = """
:root{--chev:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5l5 5 5-5' fill='none' stroke='%23A3AECB' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");--icon-filter:invert(.85);color-scheme:dark;--bg:#070B1A;--ink:#EEF2FF;--muted:#A3AECB;--rule:rgba(255,255,255,.11);--glass:rgba(22,28,56,.55);
--glass-hi:rgba(255,255,255,.07);--field:rgba(8,12,30,.55);--accent:#9D8CFF;--accent-2:#38D9F5;--accent-3:#F57BC3;
--ok:#4ADE9B;--bad:#FF8A9A;--on-accent:#0B0F24;--shadow:0 18px 50px rgba(0,0,0,.45);--glow:0 0 0 1px rgba(157,140,255,.35),0 8px 30px rgba(120,100,255,.25);
--aurora-1:rgba(124,92,255,.62);--aurora-2:rgba(34,211,238,.45);--aurora-3:rgba(244,114,182,.38);
font-family:"Inter","Ubuntu","Noto Sans","DejaVu Sans",system-ui,sans-serif;color:var(--ink);background:var(--bg);
font-variant-numeric:tabular-nums;line-height:1.55;-webkit-font-smoothing:antialiased}
@media (prefers-color-scheme:light){:root{--chev:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5l5 5 5-5' fill='none' stroke='%23525C7A' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");--icon-filter:none;color-scheme:light;--bg:#F4F6FF;--ink:#141936;--muted:#525C7A;--rule:rgba(20,25,54,.12);
--glass:rgba(255,255,255,.62);--glass-hi:rgba(255,255,255,.75);--field:rgba(255,255,255,.85);--accent:#5B45F0;--accent-2:#0A7393;--accent-3:#C42A86;
--ok:#047857;--bad:#BE123C;--on-accent:#fff;--shadow:0 14px 40px rgba(60,60,140,.14);--glow:0 0 0 1px rgba(91,69,240,.3),0 8px 26px rgba(91,69,240,.18);
--aurora-1:rgba(140,110,255,.40);--aurora-2:rgba(56,217,245,.32);--aurora-3:rgba(245,123,195,.28)}}
*{box-sizing:border-box}[hidden]{display:none!important}html{background:var(--bg)}body{margin:0;min-height:100vh;position:relative;overflow-x:hidden}
body::before{content:"";position:fixed;inset:-20vmax;z-index:-1;pointer-events:none;filter:blur(60px);
background:radial-gradient(38vmax 30vmax at 12% 8%,var(--aurora-1),transparent 70%),
radial-gradient(34vmax 28vmax at 88% 18%,var(--aurora-2),transparent 70%),
radial-gradient(40vmax 30vmax at 60% 100%,var(--aurora-3),transparent 70%)}
@media (prefers-reduced-motion:no-preference){body::before{animation:drift 26s ease-in-out infinite alternate}
@keyframes drift{to{transform:translate3d(3vmax,-2vmax,0) rotate(6deg)}}}
a{color:var(--accent);text-underline-offset:3px}
:focus-visible{outline:2px solid var(--accent-2);outline-offset:3px;border-radius:6px}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px 56px}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:10px 12px 10px 16px;margin:12px 0 30px;
background:var(--glass);border:1px solid var(--rule);border-radius:18px;backdrop-filter:blur(18px) saturate(140%);-webkit-backdrop-filter:blur(18px) saturate(140%);box-shadow:var(--shadow)}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.05rem;color:var(--ink);text-decoration:none;margin-right:auto;letter-spacing:-.01em}
.brand svg{width:28px;height:28px;flex:none}
nav{display:flex;gap:4px;background:var(--glass-hi);border:1px solid var(--rule);border-radius:999px;padding:4px;max-width:100%;
overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}nav::-webkit-scrollbar{display:none}nav a{flex:none;white-space:nowrap}
nav a{color:var(--muted);text-decoration:none;padding:6px 14px;border-radius:999px;font-weight:600;font-size:.92rem}
nav a:hover{color:var(--ink)}
nav a[aria-current]{color:var(--on-accent);background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:var(--glow)}
h1{font-size:clamp(2rem,5.2vw,3.3rem);line-height:1.04;margin:0;font-weight:800;letter-spacing:-.03em}
h1 small{display:block;font-size:.36em;font-weight:500;color:var(--muted);letter-spacing:0;margin-top:8px}
.grad{background:linear-gradient(120deg,var(--ink) 20%,var(--accent) 60%,var(--accent-2));-webkit-background-clip:text;background-clip:text;color:transparent}
h2{font-size:1.12rem;margin:0 0 12px;letter-spacing:-.01em}h3{font-size:1.08rem;margin:0;letter-spacing:-.01em}
.daynav{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap}.daynav .arrows{display:flex;gap:6px;margin-left:auto}
.lede{font-size:1.05rem;max-width:66ch;margin:14px 0 20px;color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0 22px}
@media (max-width:760px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
.tile{padding:16px 18px;position:relative;overflow:hidden}
.tile .k{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700}
.tile .v{font-size:clamp(1.5rem,3.4vw,2.1rem);font-weight:800;letter-spacing:-.02em;margin-top:4px;line-height:1.1}
.tile::after{content:"";position:absolute;right:-30px;top:-30px;width:90px;height:90px;border-radius:50%;
background:radial-gradient(circle,var(--tile-glow,var(--accent)) 0,transparent 70%);opacity:.35}
.tile:nth-child(2){--tile-glow:var(--accent-2)}.tile:nth-child(3){--tile-glow:var(--accent-3)}.tile:nth-child(4){--tile-glow:var(--ok)}
.glass,.strip,aside .panel,section.block,.entry,.tile{background:var(--glass);border:1px solid var(--rule);border-radius:18px;
backdrop-filter:blur(16px) saturate(140%);-webkit-backdrop-filter:blur(16px) saturate(140%);box-shadow:var(--shadow)}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){:root{--glass:rgba(22,28,56,.96)}
@media (prefers-color-scheme:light){:root{--glass:rgba(255,255,255,.97)}}}
.strip{padding:16px 16px 8px;margin-bottom:18px}
.strip svg{width:100%;height:auto;display:block}.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:.85rem;color:var(--muted);margin:2px 2px 8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:24px;align-items:start;margin-top:26px}
@media (max-width:900px){.cols{grid-template-columns:minmax(0,1fr)}}
main>h2{margin-left:4px}
.entry{padding:20px 22px;margin-bottom:14px;transition:border-color .2s,transform .2s}
.entry:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--rule))}
.entry .where{color:var(--muted);font-size:.88rem;margin:3px 0 10px;overflow-wrap:anywhere}
.entry ul{margin:8px 0 12px;padding-left:1.15em;max-width:72ch}.entry li{margin:4px 0}.entry li::marker{color:var(--accent)}
.src{font-size:.78rem;color:var(--muted);display:inline-block;padding:2px 10px;border:1px solid var(--rule);border-radius:999px;background:var(--glass-hi)}
dl.facts{display:flex;flex-wrap:wrap;gap:6px 18px;margin:12px 0 0;font-size:.86rem}dl.facts div{display:flex;gap:6px}
dl.facts dt{color:var(--muted)}dl.facts dd{margin:0;font-weight:600}
details{margin-top:10px;font-size:.9rem}summary{cursor:pointer;color:var(--muted);font-weight:600}summary:hover{color:var(--ink)}details ul{margin:6px 0}
aside .panel{padding:18px 16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:.84rem}th,td{padding:7px 3px;text-align:right;border-bottom:1px solid var(--rule)}
td{white-space:nowrap}th{vertical-align:bottom;line-height:1.2}th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.05em}tr:last-child td{border-bottom:0}
.tablewrap{overflow-x:auto}
.models{list-style:none;margin:0;padding:0}.models li{padding:9px 0;border-bottom:1px solid var(--rule)}.models li:last-child{border-bottom:0}
.models .top{display:flex;gap:8px;align-items:baseline}.models .name{font-weight:700}.models .who{color:var(--muted);font-size:.8rem}
.models .num{margin-left:auto;font-weight:700}.models .bar{height:6px;border-radius:99px;background:var(--glass-hi);margin:6px 0 4px;overflow:hidden}
.models .bar span{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.models .sub{font-size:.78rem;color:var(--muted)}
button,.btn{font:inherit;font-size:.9rem;font-weight:700;border:0;color:var(--on-accent);border-radius:12px;padding:9px 16px;cursor:pointer;text-decoration:none;
display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:var(--glow);transition:transform .15s,filter .15s}
button:hover,.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
button.quiet,.btn.quiet{background:var(--glass-hi);color:var(--ink);box-shadow:none;border:1px solid var(--rule)}
button.quiet:hover,.btn.quiet:hover{border-color:var(--accent)}
button.danger{background:transparent;border:1px solid var(--bad);color:var(--bad);box-shadow:none}
button:disabled{opacity:.5;cursor:not-allowed;transform:none;filter:none}
.banner{border-radius:14px;padding:12px 16px;margin-bottom:20px;border:1px solid var(--rule);background:var(--glass);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
.banner.ok{border-color:var(--ok);box-shadow:0 0 24px color-mix(in srgb,var(--ok) 25%,transparent)}.banner.bad{border-color:var(--bad);color:var(--bad)}
section.block{padding:22px 24px;margin-bottom:20px}
section.block p.help{color:var(--muted);margin:0 0 14px;max-width:72ch}
label{display:block;font-size:.88rem;font-weight:600;margin:14px 0 6px}
input[type=text],input[type=date],input[type=number],select,textarea{font:inherit;width:100%;max-width:440px;padding:9px 12px;min-height:44px;line-height:1.3;border:1px solid var(--rule);border-radius:12px;background-color:var(--field);color:var(--ink)}
select{-webkit-appearance:none;appearance:none;padding-right:36px;cursor:pointer;background-image:var(--chev);background-repeat:no-repeat;background-position:right 13px center;background-size:12px 8px}
select:hover,input[type=text]:hover,input[type=date]:hover,input[type=number]:hover,textarea:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--rule))}
select option,select optgroup{background:var(--bg);color:var(--ink)}
input[type=date]::-webkit-calendar-picker-indicator{cursor:pointer;opacity:.75;filter:var(--icon-filter)}
input[type=number]{max-width:140px}
input[type=text]:focus,input[type=date]:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:var(--glow)}
input[type=file]{font:inherit;color:var(--muted);max-width:100%}
input[type=file]::file-selector-button{font:inherit;font-weight:700;border:1px solid var(--rule);border-radius:10px;padding:7px 12px;margin-right:10px;background:var(--glass-hi);color:var(--ink);cursor:pointer}
input[type=checkbox]{-webkit-appearance:none;appearance:none;width:20px;height:20px;flex:none;margin:1px 0 0;cursor:pointer;
border:1.5px solid color-mix(in srgb,var(--ink) 28%,transparent);border-radius:6px;background:var(--field);transition:background .15s,border-color .15s,box-shadow .15s}
input[type=checkbox]:hover{border-color:var(--accent)}
input[type=checkbox]:checked{border-color:transparent;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3.5 8.5l3 3 6-7' fill='none' stroke='white' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/14px 14px no-repeat,linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:var(--glow)}
input[type=checkbox]:focus-visible{outline:2px solid var(--accent-2);outline-offset:2px}
input[type=checkbox]:disabled{opacity:.5;cursor:not-allowed}
.check{display:flex;gap:10px;align-items:flex-start;margin:14px 0}.check label{margin:0;font-weight:500}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px}
code,.cmd{font-family:"JetBrains Mono","Ubuntu Mono","DejaVu Sans Mono",monospace;font-size:.88em}
code{background:var(--glass-hi);border:1px solid var(--rule);border-radius:6px;padding:1px 6px}
.cmd{display:block;background:var(--field);border:1px solid var(--rule);border-radius:12px;padding:10px 12px;margin:6px 0;overflow-x:auto;white-space:pre}
.state-ok{color:var(--ok);font-weight:700}.state-bad{color:var(--bad);font-weight:700}.muted{color:var(--muted)}
.pill{display:inline-block;font-size:.78rem;font-weight:700;padding:2px 10px;border-radius:999px;border:1px solid var(--rule);background:var(--glass-hi)}
.pill.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,transparent)}.pill.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,transparent)}
.empty{padding:30px;border:1px dashed var(--rule);border-radius:18px;text-align:center;color:var(--muted);background:var(--glass)}
ul.chats{list-style:none;padding-left:0}ul.chats>li{margin:10px 0}ul.msgs{margin:2px 0 0;color:var(--muted);font-size:.92rem}
.chatsum{color:var(--muted);font-size:.92rem;max-width:72ch;margin-top:2px}
.acct{padding:16px 0;border-top:1px solid var(--rule)}.acct:first-of-type{border-top:0;padding-top:4px}
.acct .meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.setups{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:4px 0 16px}@media (max-width:700px){.setups{grid-template-columns:1fr}}
.setups div{border:1px solid var(--rule);border-radius:14px;padding:12px 14px;background:var(--glass-hi);font-size:.9rem}
.setups b{display:block;margin-bottom:4px}
.checklist{padding:20px 22px;margin-bottom:18px;border-radius:20px;background:var(--glass);border:1px solid color-mix(in srgb,var(--accent) 50%,var(--rule));
backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:var(--glow),var(--shadow)}
.clhead{display:flex;gap:16px;align-items:center;margin-bottom:6px}.clhead h2{margin:0}.clhead p{margin:2px 0 0}
.ring{width:64px;height:64px;flex:none}.ring .track{fill:none;stroke:var(--glass-hi);stroke-width:5}
.ring .fill{fill:none;stroke:var(--accent-2);stroke-width:5;stroke-linecap:round}
.ring text{font:800 10px/1 system-ui,sans-serif;fill:var(--ink)}
ol.checks{list-style:none;margin:10px 0 0;padding:0}ol.checks>li{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--rule)}
ol.checks>li:first-child{border-top:0}ol.checks .tick{flex:none;width:22px;height:22px;border-radius:50%;display:grid;place-items:center;
font-weight:800;font-size:.8rem;border:2px solid var(--rule);color:var(--muted)}ol.checks .tick.ok{background:var(--ok);border-color:var(--ok);color:var(--on-accent)}
ol.checks li.done span:last-child{color:var(--muted);text-decoration:line-through;text-decoration-color:color-mix(in srgb,var(--muted) 50%,transparent)}
ol.checks details{margin:0;flex:1;font-size:.95rem}ol.checks summary{color:var(--ink);font-weight:700}
.fix{padding:6px 0 4px}.fix p{margin:6px 0;max-width:72ch}.fix .why{color:var(--muted)}
.cmdrow{display:flex;gap:8px;align-items:center;min-width:0}.cmdrow .cmd{flex:1;margin:4px 0;min-width:0}
ol.checks>li>details,ol.checks>li>span{min-width:0}.tips{margin-top:12px}
.notice{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px 14px;margin-bottom:12px;border-radius:16px;
background:var(--glass);border:1px solid var(--rule);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);box-shadow:var(--shadow)}
.notice .ico{font-size:1.3rem}.notice .txt{flex:1 1 280px}.notice .acts{display:flex;gap:8px;align-items:center}
.notice .acts form{margin:0}.notice.hook,.notice.apikey{border-color:color-mix(in srgb,var(--bad) 55%,var(--rule))}
.danger-zone{border-color:color-mix(in srgb,var(--bad) 60%,var(--rule))!important;box-shadow:0 0 0 1px color-mix(in srgb,var(--bad) 35%,transparent),var(--shadow)}
.danger-zone h2{color:var(--bad)}.danger-zone ul{margin:6px 0 12px;padding-left:1.2em}.danger-zone li{margin:4px 0}
button.danger-solid{background:#C2334B;color:#fff;box-shadow:none}
#forget,#logins,#checklist,#add,#data-repo{scroll-margin-top:96px}
#data-repo .meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center}a.btn.forget{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,var(--rule))}
.banner.closable{display:flex;align-items:center;gap:12px}.banner.closable>span{flex:1}
.banner .close{margin:0}.banner .close button{padding:4px 10px}
.notice.update{border-color:color-mix(in srgb,var(--ok) 55%,var(--rule))}.notice.update code{margin-right:4px}
.notice.login,.notice.folder{border-color:color-mix(in srgb,var(--accent) 55%,var(--rule))}
/* ---- motion: everything here is switched off by prefers-reduced-motion below */
@keyframes rise{from{opacity:0;transform:translateY(14px)}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes draw{from{stroke-dasharray:0 100}}
@keyframes pop{from{opacity:0;transform:scale(.3)}}
@keyframes flicker{0%,100%{transform:scale(1) rotate(-3deg)}50%{transform:scale(1.15) rotate(4deg)}}
@keyframes bob{50%{transform:translateY(-4px)}}
@keyframes zz{0%{opacity:0;transform:translate(0,6px)}30%{opacity:1}100%{opacity:0;transform:translate(6px,-10px)}}
@keyframes blink{50%{opacity:.25}}
@keyframes toastin{from{opacity:0;transform:translate(-50%,-16px)}}
@keyframes hue{to{filter:blur(60px) hue-rotate(360deg)}}
@keyframes bounce{to{transform:scaleY(1.18)}}
@keyframes wiggle{from{transform:rotate(-2deg)}to{transform:rotate(2deg)}}
.tile{animation:rise .6s cubic-bezier(.2,.8,.2,1) both}.tile:nth-child(2){animation-delay:.08s}.tile:nth-child(3){animation-delay:.16s}.tile:nth-child(4){animation-delay:.24s}
.entry,.insights .panel,aside .panel,.strip{animation:rise .55s cubic-bezier(.2,.8,.2,1) both}.strip{animation-delay:.1s}
.insights .panel:nth-child(2){animation-delay:.08s}.insights .panel:nth-child(3){animation-delay:.16s}
.entry:nth-of-type(2){animation-delay:.06s}.entry:nth-of-type(3){animation-delay:.12s}.entry:nth-of-type(4){animation-delay:.18s}
.entry:hover,.insights .panel:hover,.tile:hover{transform:translateY(-2px);transition:transform .2s}
svg .bar{transform-box:fill-box;transform-origin:50% 100%;animation:grow .75s cubic-bezier(.2,.9,.3,1.15) both}
.insights{display:grid;grid-template-columns:1.05fr 1fr 1.2fr;gap:16px;margin-top:22px}@media (max-width:900px){.insights{grid-template-columns:1fr}}
.insights .panel{padding:18px;background:var(--glass);border:1px solid var(--rule);border-radius:18px;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:var(--shadow)}
.clock{width:100%;max-width:230px;display:block;margin:0 auto}.clock .face{fill:var(--glass-hi);stroke:var(--rule)}.clock .hub{fill:var(--bg)}
.clock .wedge{fill:var(--accent-2);fill-opacity:.85;transform-box:view-box;transform-origin:60px 60px;animation:pop .6s cubic-bezier(.2,.9,.3,1.2) both}
.clock .wedge:hover{fill:var(--accent-3)}.clock text,.heat text{font:600 7px/1 system-ui,sans-serif;fill:var(--muted)}
.heat{width:100%;max-width:190px;display:block;margin:0 auto}.heat .cell{transform-box:fill-box;transform-origin:center;animation:pop .45s ease-out both}
.heat .l0{fill:color-mix(in srgb,var(--muted) 16%,transparent)}.heat .l1{fill:color-mix(in srgb,var(--accent) 35%,transparent)}.heat .l2{fill:color-mix(in srgb,var(--accent) 60%,transparent)}
.heat .l3{fill:var(--accent)}.heat .l4{fill:var(--accent-2)}.heat .sel{stroke:var(--ink);stroke-width:1.5}.heat .future{opacity:.25}
.streaktxt{margin:8px 0 0;font-size:.9rem}
.projbars{list-style:none;margin:0;padding:0}.projbars li{margin:0 0 10px}.projbars .pl{display:flex;gap:8px;font-size:.88rem}
.projbars .pl span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.projbars .pl b{margin-left:auto}
.pbar{height:8px;border-radius:99px;background:var(--glass-hi);overflow:hidden;margin-top:4px}
.pbar span{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent-2),var(--accent-3));transform-origin:left;animation:growx .8s cubic-bezier(.2,.8,.2,1) both}
.donut{display:flex;gap:14px;align-items:center;margin:0 0 6px}.donut svg{width:110px;height:110px;flex:none}
.donut .track{fill:none;stroke:var(--glass-hi);stroke-width:12}.donut .seg{fill:none;stroke-width:12;animation:draw 1s cubic-bezier(.3,.7,.2,1) both}
.donut .big{font:800 15px/1 system-ui,sans-serif;fill:var(--ink)}.donut .small{font:600 7.5px/1 system-ui,sans-serif;fill:var(--muted)}
.donut .legend{display:flex;flex-direction:column;gap:4px;font-size:.82rem;margin:0}
.flame{display:inline-flex;align-items:center;gap:4px;margin-left:10px;padding:2px 10px;border-radius:99px;font-size:.9rem;font-weight:700;
color:var(--ink);background:color-mix(in srgb,#FB923C 18%,transparent);border:1px solid color-mix(in srgb,#FB923C 45%,transparent);vertical-align:2px}
.flame i{font-style:normal;display:inline-block;animation:flicker 1.1s ease-in-out infinite}
.fact{margin:-6px 0 18px;color:var(--muted);font-size:.95rem}
.robot{width:120px;height:120px;display:block;margin:0 auto 6px}.robot .rb{fill:var(--glass-hi);stroke:var(--accent);stroke-width:2}
.robot .eye{fill:none;stroke:var(--ink);stroke-width:3;stroke-linecap:round}.robot .mouth{fill:var(--muted)}
.robot .led{fill:var(--accent-3);animation:blink 2.4s ease-in-out infinite}.robot .bob{animation:bob 3s ease-in-out infinite}
.robot .z{font:800 11px/1 system-ui,sans-serif;fill:var(--accent-2);opacity:0;animation:zz 3s ease-out infinite}
.robot .z2{animation-delay:1s}.robot .z3{animation-delay:2s;font-size:14px}
.toast{position:fixed;top:18px;left:50%;transform:translateX(-50%);z-index:60;padding:12px 22px;border-radius:16px;font-weight:800;
background:var(--glass);border:1px solid var(--rule);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:var(--glow),var(--shadow);animation:toastin .35s ease-out both}
#confetti{position:fixed;inset:0;pointer-events:none;z-index:59}
.flash{position:fixed;right:18px;bottom:18px;z-index:61;display:flex;gap:12px;align-items:center;max-width:min(460px,calc(100vw - 32px));
padding:12px 12px 12px 18px;border-radius:16px;font-weight:600;background:var(--glass);border:1px solid var(--rule);
backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:var(--glow),var(--shadow);animation:rise .3s ease-out both}
.flash span{flex:1;overflow-wrap:anywhere}.flash button{padding:4px 10px}.flash.ok{border-color:var(--ok)}.flash.bad{border-color:var(--bad)}
.flash.run span::before{content:"";display:inline-block;width:14px;height:14px;margin-right:10px;vertical-align:-2px;border-radius:50%;
border:2px solid var(--accent-2);border-right-color:transparent;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (max-width:560px){.flash{left:16px;right:16px;bottom:16px;max-width:none}}
dialog.dlg{width:min(560px,calc(100vw - 32px));max-height:calc(100vh - 48px);overflow:auto;padding:22px 24px;border-radius:20px;color:var(--ink);
background:var(--bg);border:1px solid var(--rule);box-shadow:var(--glow),var(--shadow)}
dialog.dlg::backdrop{background:rgba(5,8,20,.55);backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)}
dialog.dlg p{overflow-wrap:anywhere;max-width:none}dialog.dlg.bad{border-color:color-mix(in srgb,var(--bad) 60%,var(--rule))}
dialog.dlg.bad h2{color:var(--bad)}dialog.dlg.ok{border-color:color-mix(in srgb,var(--ok) 60%,var(--rule))}
dialog.dlg .forget-body ul{padding-left:1.2em}dialog.dlg .row:last-child:empty{display:none}
button.busy{position:relative;color:transparent!important}button.busy::after{content:"";position:absolute;inset:0;margin:auto;width:16px;height:16px;
border-radius:50%;border:2px solid var(--ink);border-right-color:transparent;animation:spin .8s linear infinite}
.cardhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.cardhead h3{margin-right:auto}
.pill.tag{color:var(--accent-2);border-color:color-mix(in srgb,var(--accent-2) 45%,transparent)}
a.btn.edit{padding:5px 12px;font-size:.82rem}
.para{margin:8px 0 6px;max-width:72ch;font-size:1rem}.entry details{margin-top:6px}
.refs{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 8px}.ref{font-size:.78rem;font-weight:700;padding:2px 9px;border-radius:8px;
border:1px solid color-mix(in srgb,var(--accent-2) 45%,var(--rule));color:var(--accent-2);background:var(--glass-hi);text-decoration:none;
font-family:"JetBrains Mono","Ubuntu Mono",monospace}a.ref:hover{border-color:var(--accent-2)}
.tline{font-size:.88rem;margin:8px 0;padding:8px 12px;border-radius:12px;background:var(--glass-hi);border:1px solid var(--rule);
max-width:72ch;overflow-wrap:anywhere}
.guess{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0;padding:10px 12px;border-radius:14px;
border:1px dashed color-mix(in srgb,var(--accent) 55%,var(--rule));background:var(--glass-hi)}
.guess>span:first-child{flex:1 1 240px}.guess .acts{display:flex;gap:8px}.guess form{margin:0}.guess button{padding:6px 12px}
.tasks{margin:12px 0;padding:10px 14px;border-radius:14px;background:var(--glass-hi);border:1px solid var(--rule)}
.taskhead{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.taskhead .acts{margin-left:auto;display:flex;gap:6px}
.taskhead button{padding:4px 10px;font-size:.8rem}
.tasks ul{list-style:none;padding:0;margin:8px 0 0}.tasks li{margin:4px 0}.tasks li.done{color:var(--muted)}
.edit-card label:first-of-type{margin-top:4px}.edit-card .help{margin:4px 0 0!important;font-size:.85rem}
fieldset.sessions{border:1px solid var(--rule);border-radius:12px;margin:14px 0 0;padding:6px 12px}
fieldset.sessions legend{font-size:.88rem;font-weight:600;padding:0 6px}fieldset.sessions .check{margin:8px 0}
dialog.dlg .popup-body label{margin-top:12px}dialog.dlg .popup-body .check label{margin:0}dialog.dlg .popup-body input[type=text],dialog.dlg .popup-body select{max-width:none}
.tsform{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:flex-end;margin:16px 0 18px}.tsform label{margin:0 0 6px}
.tsform select,.tsform input[type=date]{width:auto}.tsform .check{margin:0 0 10px;align-self:flex-end}
table.sheet td,table.sheet th{white-space:normal;vertical-align:top;text-align:left}table.sheet td:first-child{white-space:nowrap}
table.sheet .num{text-align:right;white-space:nowrap}table.sheet td.sum{min-width:260px;text-align:left}
table.sheet th{text-align:left}table.sheet tfoot td{border-top:2px solid var(--rule);font-weight:700}
.exports{margin:0 0 14px}
ul.totals{list-style:none;margin:0;padding:0}ul.totals li{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--rule)}
ul.totals li:last-child{border-bottom:0}ul.totals b{margin-left:auto}
details.help-more{margin-top:16px}details.help-more ol{padding-left:1.2em;margin:8px 0}details.help-more li{margin:8px 0}
details.help-more p{margin:8px 0;max-width:72ch}
body.party::before{animation:hue 1.2s linear infinite}body.party svg .bar{animation:bounce .35s ease-in-out infinite alternate}
body.party .tile{animation:wiggle .25s ease-in-out infinite alternate}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
footer{margin-top:44px;padding:16px 4px 0;border-top:1px solid var(--rule);font-size:.85rem;color:var(--muted)}
footer a{color:var(--muted)}footer a:hover{color:var(--ink)}
@media (max-width:560px){.opt{display:none}header{padding:8px}nav a{padding:6px 10px}.brand span{display:none}section.block{padding:18px}.entry{padding:18px}}
@media (prefers-reduced-motion:no-preference){svg .bar{transition:opacity .15s}a:hover .bar{opacity:.8}}
"""

SCRIPT = """
(function(){var r=document.getElementById('restarting');if(!r)return;var from=r.dataset.version,tries=0;
function poll(){tries++;fetch('/api/status',{cache:'no-store'}).then(function(x){return x.json()}).then(function(s){
if(s.version&&s.version!==from){location.replace('/')}else if(tries<120){setTimeout(poll,1500)}}).catch(function(){if(tries<120)setTimeout(poll,1500)})}
setTimeout(poll,1500)})();
(function(){var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
var fun=document.body.dataset.fun==='1';
function short(v){var u=[[1e9,'B'],[1e6,'M'],[1e3,'k']];for(var i=0;i<u.length;i++){if(v>=u[i][0]){return (v/u[i][0]).toFixed(1).replace(/\\.0$/,'')+u[i][1]}}return String(Math.round(v))}
function fmt(v,f){v=Math.round(v);if(f==='short')return short(v);if(f==='dur'){var h=Math.floor(v/60),m=v%60;return h?h+'h '+(m<10?'0':'')+m+'m':m+'m'}return String(v)}
if(!reduce){document.querySelectorAll('[data-count]').forEach(function(el){var to=+el.dataset.count,f=el.dataset.fmt,final=el.textContent,t0=null;
if(!(to>0))return;function step(t){if(t0===null)t0=t;var p=Math.min(1,(t-t0)/800),k=1-Math.pow(1-p,3);el.textContent=p<1?fmt(to*k,f):final;if(p<1)requestAnimationFrame(step)}
el.textContent=fmt(0,f);requestAnimationFrame(step)})}
function toast(text){var t=document.createElement('div');t.className='toast';t.setAttribute('role','status');t.textContent=text;document.body.appendChild(t);setTimeout(function(){t.remove()},3200)}
function confetti(){if(reduce)return;var c=document.createElement('canvas');c.id='confetti';document.body.appendChild(c);var x=c.getContext('2d');
var W=c.width=innerWidth,H=c.height=innerHeight,cols=['#9D8CFF','#38D9F5','#F57BC3','#FBBF24','#4ADE9B'],ps=[];
for(var i=0;i<160;i++){ps.push({x:W/2+(Math.random()-.5)*W*.3,y:H*.35,vx:(Math.random()-.5)*14,vy:-Math.random()*14-4,s:4+Math.random()*6,r:Math.random()*6,vr:(Math.random()-.5)*.3,c:cols[i%cols.length]})}
var t0=performance.now();(function frame(t){x.clearRect(0,0,W,H);ps.forEach(function(p){p.vy+=.35;p.vx*=.99;p.x+=p.vx;p.y+=p.vy;p.r+=p.vr;
x.save();x.translate(p.x,p.y);x.rotate(p.r);x.fillStyle=p.c;x.fillRect(-p.s/2,-p.s/4,p.s,p.s/2);x.restore()});
if(t-t0<2200){requestAnimationFrame(frame)}else{c.remove()}})(t0)}
function seen(k){try{var s=JSON.parse(localStorage.getItem('worklog.cheers')||'[]');if(s.indexOf(k)>=0)return true;s.push(k);localStorage.setItem('worklog.cheers',JSON.stringify(s.slice(-200)));return false}catch(e){return false}}
if(fun){var lines=[];var f=document.getElementById('fun');if(f&&f.dataset.celebrate)lines=lines.concat(f.dataset.celebrate.split('\\n'));
document.querySelectorAll('.banner[data-celebrate]').forEach(function(b){lines.push(b.dataset.celebrate)});
var first=true;lines.forEach(function(l){if(!l)return;var i=l.indexOf('|'),k=l.slice(0,i),msg=l.slice(i+1);if(seen(k))return;
setTimeout(function(){confetti();toast(msg)},first?500:2600);first=false})}
if(fun){var code=['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'],buf=[],typed='',timer=null;
function party(on){document.body.classList.toggle('party',on);clearTimeout(timer);if(on){toast('🪩 Party mode! (Esc to stop)');confetti();timer=setTimeout(function(){party(false)},10000)}}
document.addEventListener('keydown',function(ev){var tag=(ev.target.tagName||'').toLowerCase();if(tag==='input'||tag==='textarea'||tag==='select')return;
if(ev.key==='Escape'){party(false);return}buf.push(ev.key);buf=buf.slice(-code.length);typed=(typed+ev.key).slice(-5).toLowerCase();
if(buf.join()===code.join()||typed==='party'){buf=[];typed='';party(true)}})}
})();
document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('button.copy');if(!b)return;
var say=function(m){b.textContent=m;setTimeout(function(){b.textContent='Copy'},1600)};
var manual=function(){var pre=b.parentNode.querySelector('pre');if(pre){var r=document.createRange();r.selectNodeContents(pre);
var s=window.getSelection();s.removeAllRanges();s.addRange(r)}say(navigator.platform.indexOf('Mac')===0?'Press \u2318C':'Press Ctrl+C')};
if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(b.dataset.copy).then(function(){say('Copied!')},manual)}else{manual()}});
var W=window.worklog={};
W.init=function(){
(function(){var r=document.getElementById('ts-range');if(!r)return;function sync(){document.querySelectorAll('.tsform .custom')
.forEach(function(x){x.hidden=r.value!=='custom'})}r.addEventListener('change',sync);sync()})();
(function(){var s=document.getElementById('model'),t=document.getElementById('model_custom');if(!s||!t)return;
function sync(){t.hidden=s.value!=='custom'}s.addEventListener('change',function(){sync();if(!t.hidden)t.focus()});sync()})();
(function(){var b=document.getElementById('jobstate');if(!b||b.dataset.running!=='1'||W.following)return;
W.wait().then(function(){W.refresh()})})();
(function(){var btn=document.getElementById('chatupload'),inp=document.getElementById('chatfile');if(!btn||!inp)return;
var out=document.getElementById('chatuploadstate');
btn.addEventListener('click',function(){var f=inp.files[0];if(!f){out.textContent='Choose the conversations zip first.';return}
if(f.size>btn.dataset.max*1){out.textContent='That file is too large.';return}
btn.disabled=true;out.textContent='Uploading '+f.name+'…';
fetch('/sources/upload',{method:'POST',body:f,headers:{'Content-Type':'application/octet-stream',
'X-CSRF-Token':btn.dataset.csrf,'X-File-Name':encodeURIComponent(f.name)}}).then(function(r){
return r.json().then(function(j){if(r.ok){W.follow('Reading the export',null,{})}else{out.textContent=j.error||'Upload failed.';btn.disabled=false}})})
.catch(function(){out.textContent='Upload failed. Is the worklog service running?';btn.disabled=false})})})();
};
W.wait=function(){return new Promise(function(done){var tries=0;(function poll(){tries++;
fetch('/api/status',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){if(s.running&&tries<900){setTimeout(poll,1000)}else{done(s)}})
.catch(function(){if(tries<900){setTimeout(poll,2000)}else{done({})}})})()})};
W.refresh=function(){var u=location.pathname+location.search.replace(/([?&])ok=[^&]*&?/,'$1').replace(/[?&]$/,'');
return fetch(u,{cache:'no-store'}).then(function(r){return r.text()}).then(function(html){
var d=new DOMParser().parseFromString(html,'text/html'),nw=d.querySelector('.wrap'),old=document.querySelector('.wrap');if(!nw||!old)return;
var y=window.scrollY,open=[].map.call(old.querySelectorAll('details[open][id]'),function(x){return x.id});
document.adoptNode(nw);old.replaceWith(nw);open.forEach(function(id){var x=document.getElementById(id);if(x)x.open=true});
window.scrollTo(0,y);document.title=d.title;if(u!==location.pathname+location.search)history.replaceState(null,'',u+location.hash);W.init()})};
W.flash=function(text,kind){var old=document.getElementById('flash');if(old)old.remove();
var f=document.createElement('div');f.id='flash';f.className='flash '+(kind||'');f.setAttribute('role',kind==='bad'?'alert':'status');
var t=document.createElement('span');t.textContent=text;f.appendChild(t);
if(kind!=='run'){var x=document.createElement('button');x.type='button';x.className='quiet';x.setAttribute('aria-label','Close');x.textContent='✕';
x.addEventListener('click',function(){f.remove()});f.appendChild(x)}
document.body.appendChild(f);if(kind==='ok')setTimeout(function(){if(f.isConnected)f.remove()},7000);return f};
W.dialog=function(title,body,opts){opts=opts||{};var d=document.createElement('dialog');d.className='dlg'+(opts.kind?' '+opts.kind:'');
var h=document.createElement('h2');h.textContent=title;d.appendChild(h);
if(typeof body==='string'){var p=document.createElement('p');p.textContent=body;d.appendChild(p)}else if(body){d.appendChild(body)}
var row=document.createElement('div');row.className='row';
if(opts.yes){var y=document.createElement('button');y.type='button';y.className=opts.danger?'danger-solid':'';y.textContent=opts.yes;
y.addEventListener('click',function(){d.close();opts.onyes()});row.appendChild(y)}
if(!opts.noclose){var c=document.createElement('button');c.type='button';c.className='quiet';c.textContent=opts.yes?'Cancel':'OK';
c.addEventListener('click',function(){d.close()});row.appendChild(c)}
d.appendChild(row);d.addEventListener('close',function(){d.remove()});document.body.appendChild(d);d.showModal();
var first=d.querySelector('input,button');if(first)first.focus();return d};
W.follow=function(name,form,opts){W.following=true;W.flash(name+'…','run');
return W.wait().then(function(s){W.following=false;var fl=document.getElementById('flash');if(fl)fl.remove();
var ok=s.ok!==false,msg=s.message||'Done.';
var csrf=form&&form.querySelector('input[name=csrf]');
if(csrf){fetch('/jobs/dismiss',{method:'POST',headers:{'X-Worklog':'fetch','Content-Type':'application/x-www-form-urlencoded'},
body:'csrf='+encodeURIComponent(csrf.value)}).catch(function(){})}
if(opts.result==='dialog'){W.dialog(ok?'✅ '+(opts.oktitle||'Done'):'⚠️ '+(opts.badtitle||'Something went wrong'),msg,{kind:ok?'ok':'bad'})}
else{W.flash(msg,ok?'ok':'bad')}return W.refresh()})};
W.send=function(form,submitter){var btns=form.querySelectorAll('button');btns.forEach(function(b){b.disabled=true});
var label=submitter&&submitter.textContent;if(submitter)submitter.classList.add('busy');
var data=new URLSearchParams(new FormData(form));if(submitter&&submitter.name)data.append(submitter.name,submitter.value);
var again=function(){btns.forEach(function(b){b.disabled=false});if(submitter){submitter.classList.remove('busy');submitter.textContent=label}};
fetch(form.action,{method:'POST',body:data,headers:{'X-Worklog':'fetch'}}).then(function(r){return r.json()}).then(function(j){
var dlg=form.closest('dialog');if(dlg&&!j.error)dlg.close();
if(j.error){again();if(dlg){var e=dlg.querySelector('.dlg-error')||dlg.insertBefore(document.createElement('p'),dlg.querySelector('form'));
e.className='dlg-error state-bad';e.textContent=j.error}else{W.flash(j.error,'bad')}return}
if(j.job){return W.follow(j.job,form,{result:form.dataset.result,oktitle:form.dataset.oktitle,badtitle:form.dataset.badtitle})}
if(j.message)W.flash(j.message,'ok');return W.refresh()}).catch(function(){again();
W.flash('Worklog didn’t answer. Is it still running? Try reloading the page.','bad')})};
document.addEventListener('submit',function(ev){var f=ev.target;
if(!f||f.method!=='post'||f.hasAttribute('data-plain')||!window.fetch||!window.DOMParser)return;
ev.preventDefault();var sub=ev.submitter;
if(f.dataset.confirm){W.dialog('Are you sure?',f.dataset.confirm,{yes:f.dataset.confirmYes||'Yes',danger:true,onyes:function(){W.send(f,sub)}});return}
W.send(f,sub)});
document.addEventListener('click',function(ev){var a=ev.target.closest&&ev.target.closest('a.forget,a[data-popup]');
if(!a||!window.fetch||!window.HTMLDialogElement)return;ev.preventDefault();var id=a.dataset.popup||'forget';
fetch(a.href,{cache:'no-store'}).then(function(r){return r.text()}).then(function(html){
var sec=new DOMParser().parseFromString(html,'text/html').getElementById(id);if(!sec){location.href=a.href;return}
var h=sec.querySelector('h2'),title=h?h.textContent:'Are you sure?';if(h)h.remove();
sec.removeAttribute('id');sec.className=id+'-body popup-body';document.adoptNode(sec);
var cancel=sec.querySelector('a.btn.quiet');if(cancel)cancel.addEventListener('click',function(e){e.preventDefault();sec.closest('dialog').close()});
W.dialog(title,sec,{kind:id==='forget'?'bad':'',noclose:true})}).catch(function(){location.href=a.href})});
document.addEventListener('change',function(ev){var s=ev.target;if(!s.dataset||!s.dataset.reveal)return;
var t=s.ownerDocument.getElementById(s.dataset.reveal)||(s.form&&s.form.querySelector('#'+s.dataset.reveal));if(!t)return;
var on=s.value==='__new__';t.hidden=!on;t.required=on;if(on)t.focus()});
W.init();
"""


# Plain links only: nothing is fetched from outside, and the page sends no referrer to another site.
_REPO = "https://github.com/us-urvin/worklog-for-claude-code"
FOOTER = (f'<footer>Worklog for Claude Code {e(__version__)} · '
          f'<a href="{_REPO}" target="_blank" rel="noopener noreferrer">⭐ Star on GitHub</a> · '
          f'<a href="{_REPO}/blob/main/docs/TROUBLESHOOTING.md" target="_blank" rel="noopener noreferrer">Help</a> · '
          f'<a href="{_REPO}/issues/new/choose" target="_blank" rel="noopener noreferrer">Report a bug</a></footer>')


LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#9D8CFF"/><stop offset=".55" stop-color="#38D9F5"/><stop offset="1" stop-color="#F57BC3"/>'
        '</linearGradient></defs><rect width="32" height="32" rx="9" fill="url(#lg)"/>'
        '<rect x="7" y="17" width="4" height="8" rx="2" fill="#0B0F24"/><rect x="14" y="11" width="4" height="14" rx="2" fill="#0B0F24"/>'
        '<rect x="21" y="7" width="4" height="18" rx="2" fill="#0B0F24"/></svg>')


def _result_visible(job: dict) -> bool:
    """A finished job's message shows for a little while, then goes away by itself."""
    if str(job.get("message", "")).startswith("Updated to"):
        return True  # the page is waiting for the restart
    age = time.time() - float(job.get("finished_at") or 0)
    return age < (RESULT_SECONDS if job.get("ok") else ERROR_SECONDS)


def layout(title: str, body: str, active: str, nonce: str, job: dict, ok: str | None = None, error: str | None = None,
           extra: str = "", fun: bool = False, csrf: str = "") -> str:
    nav = "".join(f'<a href="{h}"{" aria-current=page" if k == active else ""}>{t}</a>'
                  for k, h, t in (("day", "/", "Today"), ("sources", "/sources", "Sources"),
                                  ("timesheet", "/timesheet", "Timesheet"), ("settings", "/settings", "Settings")))
    banners = ""
    if job.get("running"):
        banners += f'<div class="banner" id="jobstate" data-running="1" role="status">{e(job["running"])}…</div>'
    elif job.get("message") and job.get("finished") and _result_visible(job):
        cls = "ok" if job.get("ok") else "bad"
        cheer = (f' data-celebrate="summaries:{e(job["finished"])}|Summaries are ready ✍️"'
                 if job.get("ok") and str(job["message"]).startswith("Summaries written") else "")
        if job.get("ok") and str(job["message"]).startswith("Updated to"):  # reload once the new version answers
            cheer += f' id="restarting" data-version="{e(__version__)}"'
        close = (f'<form method="post" action="/jobs/dismiss" class="close">'
                 f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="quiet" aria-label="Close">✕</button>'
                 f'</form>' if csrf else "")
        banners += (f'<div class="banner {cls} closable" role="status"{cheer}><span>{e(job["message"])} '
                    f'<span class="muted">({e(job["finished"])})</span></span>{close}</div>')
    if ok in OK_MESSAGES:
        banners += f'<div class="banner ok" role="status">{e(OK_MESSAGES[ok])}</div>'
    if error:
        banners += f'<div class="banner bad" role="alert">{e(error)}</div>'
    banners += extra
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} – Worklog</title>
<style>{CSS}</style></head><body data-fun="{1 if fun else 0}"><div class="wrap">
<header><a class="brand" href="/">{LOGO}<span>Worklog</span></a><nav aria-label="Main">{nav}</nav></header>{banners}{body}{FOOTER}</div>
<script nonce="{nonce}">{SCRIPT}</script></body></html>"""


# ================================================================== pages
def _chart(history: list[tuple[date, dict[str, int]]], accounts: list[str], selected: date,
           chat_counts: dict[date, int] | None = None) -> str:
    chat_counts = chat_counts or {}
    w, h, top, bottom = 700, 136, 8, 28
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
            rects.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" '
                         f'fill="{COLORS[accounts.index(name) % len(COLORS)]}" style="animation-delay:{i * 40}ms"/>')
        if not rects:
            rects.append(f'<rect x="{x:.1f}" y="{h - bottom - 2}" width="{bw:.1f}" height="2" fill="var(--rule)"/>')
        is_sel = d == selected
        label_weight = "700" if is_sel else "400"
        label_fill = "var(--ink)" if is_sel else "var(--muted)"
        mark = (f'<rect x="{x - 3:.1f}" y="{top - 4}" width="{bw + 6:.1f}" height="{h - bottom - top + 6}" rx="4" '
                f'fill="none" stroke="var(--accent-2)" stroke-width="1.5"/>') if is_sel else ""
        n_chats = chat_counts.get(d, 0)
        tip = (f"{d:%a %d %b}: {_n(total)} tokens" + "".join(f", {k} {_n(v)}" for k, v in per.items() if v)
               + (f", {n_chats} claude.ai chat{'s' * (n_chats != 1)}" if n_chats else ""))
        if n_chats:  # chats have no tokens, so mark the day under its bar
            rects.append(f'<circle cx="{x + bw / 2:.1f}" cy="{h - bottom + 7}" r="3" fill="var(--accent-3)"/>')
        parts.append(f'<a href="/?d={d.isoformat()}"><title>{e(tip)}</title>{mark}{"".join(rects)}'
                     f'<rect x="{i * slot:.1f}" y="0" width="{slot:.1f}" height="{h}" fill="transparent"/>'
                     f'<text x="{x + bw / 2:.1f}" y="{h - 6}" text-anchor="middle" font-size="11" '
                     f'font-weight="{label_weight}" fill="{label_fill}">{d.day}</text></a>')
    legend = "".join(f'<span><i style="background:{COLORS[i % len(COLORS)]}"></i>{e(n)}</span>'
                     for i, n in enumerate(accounts))
    if any(chat_counts.get(d) for d, _ in history):
        legend += '<span><i style="background:var(--accent-3);border-radius:50%"></i>claude.ai chats</span>'
    return (f'<div class="strip"><div class="legend">{legend or "<span>No activity yet</span>"}'
            f'<span style="margin-left:auto">Last {len(history)} days, peak {_short(peak) if peak > 1 else 0} tokens</span></div>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Tokens per day for the last {len(history)} days">'
            f'{"".join(parts)}</svg></div>')


def _chat_list(chat_items: list[dict], open_: bool) -> str:
    """Each chat with its title, time, and claude.ai's summary or your first messages."""
    out = []
    for c in chat_items:
        if c["summary"]:
            body = f'<div class="chatsum">{e(c["summary"])}</div>'
        else:
            msgs = [q.splitlines()[0][:160] for q in c["prompts"][:3] if q.strip()]
            more = len(c["prompts"]) - len(msgs)
            body = ('<ul class="msgs">' + "".join(f"<li>{e(m)}</li>" for m in msgs)
                    + (f'<li class="muted">…and {more} more message{"s" * (more != 1)}</li>' if more > 0 else "")
                    + "</ul>") if msgs else ""
        when = f' <span class="muted">{e(c["start"])}</span>' if c["start"] else ""
        out.append(f'<li><b>{e(c["title"])}</b>{when}{body}</li>')
    n = len(chat_items)
    lst = f'<ul class="chats">{"".join(out)}</ul>'
    if open_:
        return lst
    return f'<details><summary>{n} chat{"s" * (n != 1)}</summary>{lst}</details>'


def _summary_age(meta: dict) -> float:
    try:
        return datetime.fromisoformat(str(meta.get("generated_at"))).timestamp()
    except ValueError:
        return 0.0


def ref_url(ref: str, remote: dict | None, tracker: str = "") -> str | None:
    """Where a "#505" or "XOO2-295" points: the repo's pull request, or your tracker."""
    if ref.startswith("#") and isinstance(remote, dict) and remote.get("slug"):
        n, host, slug = ref[1:], str(remote.get("host", "")), str(remote["slug"])
        if host == "github.com":
            return f"https://github.com/{slug}/pull/{n}"
        if "gitlab" in host:
            return f"https://{host}/{slug}/-/merge_requests/{n}"
        if host == "bitbucket.org":
            return f"https://bitbucket.org/{slug}/pull-requests/{n}"
        return None
    if not ref.startswith("#") and tracker:
        return tracker + ref
    return None


def _refs_html(p, cfg) -> str:
    """PR and ticket numbers as small chips, linked when Worklog knows where they live."""
    if not p.refs:
        return ""
    chips = []
    for ref in p.refs[:12]:
        url = ref_url(ref, p.remote, cfg.tracker_url)
        chips.append(f'<a class="ref" href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(ref)}</a>' if url
                     else f'<span class="ref">{e(ref)}</span>')
    return f'<div class="refs" aria-label="Pull requests and tickets">{"".join(chips)}</div>'


def _detail_facts(p, cfg) -> list[tuple[str, str]]:
    """Tests, Claude's time compared with yours, and the cost at API prices (all from the session records)."""
    from . import cost
    out = []
    if p.tests.get("runs"):
        bits = [f'{p.tests["runs"]} run{"s" * (p.tests["runs"] != 1)}']
        if p.tests.get("fixed"):
            bits.append(f'{p.tests["fixed"]} fixed')
        elif p.tests.get("failed"):
            bits.append(f'{p.tests["failed"]} failed')
        out.append(("Tests", " · ".join(bits)))
    if p.claude_minutes and p.minutes:
        you = max(0, p.minutes - p.claude_minutes)
        out.append(("Claude / you", f"{_dur(min(p.claude_minutes, p.minutes))} · {_dur(you)}"))
    dollars, unknown = cost.estimate(p.models, cfg.cost_custom)
    if dollars > 0:
        note = " + unknown models" if unknown else ""
        out.append(("At API prices", f'<span title="An estimate at Anthropic API prices (as of {cost.PRICES_AS_OF}); '
                                     f'Pro and Max plans are not billed per token">≈ {cost.money(dollars)}{note}</span>'))
    return out


def card_tag(p, s: dict, ov) -> str | None:
    """The tag on a card: yours if you set one, else the one Claude chose when it wrote the summary.
    Worklog's own guess from keywords isn't shown: it was often wrong (a review of PRs read as Research)."""
    mine = [ov.tags.get(x["id"]) for x in p.sessions_list if ov.tags.get(x["id"])]
    if mine:
        return max(set(mine), key=mine.count)
    return s.get("tag") or None


def _tasks_html(p, s: dict) -> str:
    """Claude's to-do items for this project and day, with Copy; or the summary's suggested next steps."""
    if p.tasks:
        items = "".join(f'<li class="{t["status"]}"><span aria-hidden="true">{"✅" if t["status"] == "done" else "⬜"}'
                        f'</span> {e(t["text"])}</li>' for t in p.tasks)
        md = "\n".join(f'- [{"x" if t["status"] == "done" else " "}] {t["text"]}' for t in p.tasks)
        plain = "\n".join(("✓ " if t["status"] == "done" else "• ") + t["text"] for t in p.tasks)
        done = sum(t["status"] == "done" for t in p.tasks)
        return (f'<div class="tasks"><div class="taskhead"><b>Tasks</b> <span class="muted">{done} of {len(p.tasks)} '
                f'done</span><span class="acts"><button type="button" class="quiet copy" data-copy="{e(md)}">Copy checklist'
                f'</button><button type="button" class="quiet copy" data-copy="{e(plain)}">Copy as text</button></span></div>'
                f'<ul>{items}</ul></div>')
    nxt = [x for x in s.get("next") or [] if isinstance(x, str)]
    if nxt:
        items = "".join(f"<li>⬜ {e(x)}</li>" for x in nxt)
        text = "\n".join(f"- [ ] {x}" for x in nxt)
        return (f'<div class="tasks"><div class="taskhead"><b>Suggested next steps</b> <span class="muted">from the summary'
                f'</span><span class="acts"><button type="button" class="quiet copy" data-copy="{e(text)}">Copy</button>'
                f'</span></div><ul>{items}</ul></div>')
    return ""


def _guess_html(app: App, day: date, p) -> str:
    g = p.guess
    if not g or not g.get("sessions"):
        return ""
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    hidden = (f'{c}<input type="hidden" name="d" value="{day.isoformat()}">'
              f'<input type="hidden" name="key" value="{e(p.key)}">')
    n = len(g["sessions"])
    return (f'<div class="guess" role="note"><span>🤔 Probably <b>{e(g.get("name") or "?")}</b>? '
            f'<span class="muted">{n} session{"s" * (n != 1)} here: {e(g.get("why") or "")}</span></span>'
            f'<span class="acts"><form method="post" action="/projects/guess">{hidden}'
            f'<input type="hidden" name="answer" value="yes"><button>Yes, move</button></form>'
            f'<form method="post" action="/projects/guess">{hidden}<input type="hidden" name="answer" value="no">'
            f'<button class="quiet">No</button></form></span></div>')


def _edit_form(app: App, day: date, p, projects: dict, known: dict[str, str], ov, line: str, ai_tag: str = "") -> str:
    """The "Edit" popup for one card: rename, move (all or some sessions), folder rule, tag, timesheet line."""
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    choices = [(k, q.name) for k, q in projects.items() if k != p.key]
    choices += sorted(((k, n) for k, n in known.items() if k != p.key and k not in projects), key=lambda kv: kv[1].lower())
    opts = '<option value="">Keep it here</option>'
    if choices[: len(projects) - 1]:
        opts += '<optgroup label="Today">' + "".join(f'<option value="{e(k)}">{e(n)}</option>'
                                                     for k, n in choices[: len(projects) - 1]) + "</optgroup>"
    if choices[len(projects) - 1:]:
        opts += '<optgroup label="Recent projects">' + "".join(f'<option value="{e(k)}">{e(n)}</option>'
                                                             for k, n in choices[len(projects) - 1:]) + "</optgroup>"
    opts += '<option value="__new__">New project…</option>'
    sessions = ""
    if len(p.sessions_list) > 1:
        rows = "".join(
            f'<div class="check"><input type="checkbox" id="s-{i}" name="s_{e(x["id"])}" value="1" checked>'
            f'<label for="s-{i}"><b>{e(x["start"] or "–")}</b> · {_dur(x["minutes"])} · {e(x["first"] or "(no prompt)")}'
            f'</label></div>' for i, x in enumerate(p.sessions_list))
        sessions = f'<fieldset class="sessions"><legend>Which sessions to move</legend>{rows}</fieldset>'
    rule = ""
    folders = sorted(p.started_in)
    if folders:
        pick = (f'<code>{e(folders[0])}</code><input type="hidden" name="rule_folder" value="{e(folders[0])}">'
                if len(folders) == 1 else '<select name="rule_folder" style="width:auto">' +
                "".join(f'<option value="{e(f)}">{e(f)}</option>' for f in folders) + "</select>")
        rule = (f'<div class="check"><input type="checkbox" id="rule" name="rule" value="1"><label for="rule">Always put '
                f'sessions started in {pick} into that project on this computer (also for past days)</label></div>')
    auto = f"Automatic ({ai_tag}, from the summary)" if ai_tag else "Automatic (set when the summary is written)"
    tag_opts = f'<option value="auto">{e(auto)}</option>' + "".join(
        f'<option value="{t}"{" selected" if any(ov.tags.get(x["id"]) == t for x in p.sessions_list) else ""}>{t}</option>'
        for t in TAGS)
    return f"""<section class="block edit-card" id="edit" aria-labelledby="edit-title">
<h2 id="edit-title">Edit “{e(p.name)}”</h2>
<form method="post" action="/projects/edit">{c}<input type="hidden" name="d" value="{day.isoformat()}">
<input type="hidden" name="key" value="{e(p.key)}">
<label for="ed-name">Name on cards</label>
<input type="text" id="ed-name" name="name" value="{e(p.name)}" maxlength="80" required>
<p class="help">Shown for this project on every day.</p>
<label for="ed-move">Move to another project</label>
<select id="ed-move" name="target" data-reveal="ed-new">{opts}</select>
<input type="text" id="ed-new" name="new_name" maxlength="80" placeholder="New project name" hidden>
{sessions}{rule}
<label for="ed-tag">Kind of work</label><select id="ed-tag" name="tag">{tag_opts}</select>
<label for="ed-line">Timesheet line</label>
<input type="text" id="ed-line" name="line" value="{e(line)}" maxlength="160">
<p class="help">One line for this project on {day:%d %b}, used in the timesheet.</p>
<div class="row"><button>Save</button><a class="btn quiet" href="/?d={day.isoformat()}">Cancel</a>
<button class="quiet" name="reset" value="1" formnovalidate>Undo my changes</button></div>
</form></section>"""


RANGES = (("this-week", "This week"), ("last-week", "Last week"), ("this-month", "This month"),
          ("last-month", "Last month"), ("custom", "Custom"))


def timesheet_query(cfg: Config, q: dict) -> dict:
    """The timesheet's choices from the address bar, checked and with defaults."""
    from . import timesheet as ts
    today = date.today()
    rng = q.get("range") if q.get("range") in dict(RANGES) else "this-week"
    bounds = {"this-week": ts.week_bounds(today), "last-week": ts.week_bounds(today, -1),
              "this-month": ts.month_bounds(today), "last-month": ts.month_bounds(today, -1)}
    start, end = bounds.get(rng, ts.week_bounds(today))
    if rng == "custom":
        start, end = _parse_date(q.get("from")) or start, _parse_date(q.get("to")) or end
    account = q.get("account") if cfg.account(q.get("account", "")) else ""
    rnd = int(q.get("round")) if q.get("round") in ("15", "30") else 0
    return {"range": rng, "from": start, "to": end, "account": account, "round": rnd, "details": q.get("details") == "1"}


def _qs(t: dict, **extra) -> str:
    from urllib.parse import urlencode
    params = {"range": t["range"], "from": t["from"].isoformat(), "to": t["to"].isoformat(), "account": t["account"],
              "round": str(t["round"]), "details": "1" if t["details"] else ""}
    params.update(extra)
    return urlencode({k: v for k, v in params.items() if v not in ("", "0", None)})


def page_timesheet(app: App, cfg: Config, q: dict) -> str:
    """Hours per day and project, a tag and one line each: ready to paste or download."""
    from . import timesheet as ts
    t = timesheet_query(cfg, q)
    store = _store(cfg)
    if store is None:
        return '<div class="empty">No data repo yet. <a href="/settings">Set it up in Settings</a>.</div>'
    sel = lambda opts, cur: "".join(f'<option value="{e(v)}"{" selected" if v == cur else ""}>{e(n)}</option>'  # noqa: E731
                                    for v, n in opts)
    form = (f'<form method="get" action="/timesheet" class="tsform">'
            f'<div><label for="ts-range">Days</label><select id="ts-range" name="range">{sel(RANGES, t["range"])}</select></div>'
            f'<div class="custom"><label for="ts-from">From</label><input type="date" id="ts-from" name="from" '
            f'value="{t["from"].isoformat()}"></div><div class="custom"><label for="ts-to">To</label>'
            f'<input type="date" id="ts-to" name="to" value="{t["to"].isoformat()}"></div>'
            f'<div><label for="ts-acct">Account</label><select id="ts-acct" name="account">'
            f'{sel([("", "All accounts")] + [(a.name, a.name) for a in cfg.accounts], t["account"])}</select></div>'
            f'<div><label for="ts-round">Round each row</label><select id="ts-round" name="round">'
            f'{sel([("0", "No rounding"), ("15", "To 15 minutes"), ("30", "To 30 minutes")], str(t["round"]))}</select></div>'
            f'<div class="check"><input type="checkbox" id="ts-details" name="details" value="1"'
            f'{" checked" if t["details"] else ""}><label for="ts-details">Accounts, devices, sessions, tokens</label></div>'
            f'<div><button>Show</button></div></form>')
    head = (f'<div class="daynav"><h1><span class="grad">Timesheet</span><small>{t["from"]:%a %d %b} – '
            f'{t["to"]:%a %d %b %Y}</small></h1></div>')
    try:
        rows = ts.build(store, t["from"], t["to"], time_mode=cfg.time_mode, account=t["account"] or None)
    except ValueError as exc:
        return head + form + f'<div class="banner bad" role="alert">{e(exc)}</div>'
    if not rows:
        return head + form + ('<div class="empty"><p>No work recorded for these days'
                              + (f' on <b>{e(t["account"])}</b>' if t["account"] else "") + '.</p></div>')
    rnd, extra = t["round"], t["details"]
    total = round(sum(r.hours(rnd) for r in rows), 2)
    body_rows = []
    for r in rows:
        cells = [f'<td><a href="/?d={r.day.isoformat()}">{r.day:%a %d %b}</a></td>', f"<td>{e(r.project)}</td>",
                 f'<td class="num">{"≈" if r.approx else ""}{r.hours(rnd):.2f}</td>',
                 "<td>" + (f'<span class="pill tag">{e(r.tag)}</span>' if r.tag else "") + "</td>",
                 f'<td class="sum">{e(r.summary)}' + ("" if r.summarized else ' <span class="muted">(not summarized)</span>')
                 + "</td>"]
        if extra:
            cells += [f"<td>{e(', '.join(r.accounts))}</td>", f"<td>{e(', '.join(r.devices))}</td>",
                      f'<td class="num">{r.sessions}</td>', f'<td class="num">{_short(r.tokens)}</td>']
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    heads = "".join(f'<th{" class=num" if h in ("Hours", "Sessions", "Tokens") else ""}>{h}</th>'
                    for h in ts.COLUMNS + (ts.EXTRA if extra else ()))
    table = (f'<div class="tablewrap"><table class="sheet"><thead><tr>{heads}</tr></thead><tbody>{"".join(body_rows)}'
             f'</tbody><tfoot><tr><td>Total</td><td></td><td class="num"><b>{total:.2f}</b></td>'
             f'<td colspan="{2 + (4 if extra else 0)}"></td></tr></tfoot></table></div>')
    per = "".join(f"<li><span>{e(n)}</span><b>{h:.2f} h</b></li>" for n, h in ts.totals(rows, rnd))
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    missing = ts.missing_days(rows)
    write = ""
    if missing:
        busy = " disabled" if app.jobs.snapshot().get("running") else ""
        write = (f'<div class="notice"><span class="ico" aria-hidden="true">✍️</span><span class="txt"><b>{len(missing)} '
                 f'day{"s" * (len(missing) != 1)} without a written summary.</b> Those rows use your first prompt '
                 f'instead.</span><span class="acts"><form method="post" action="/timesheet/summaries">{c}'
                 f'<input type="hidden" name="from" value="{t["from"].isoformat()}"><input type="hidden" name="to" '
                 f'value="{t["to"].isoformat()}"><button{busy}>Write missing summaries</button></form></span></div>')
    tsv, md = ts.to_tsv(rows, rnd, extra), ts.to_markdown(rows, rnd, extra)
    exports = (f'<div class="row exports"><a class="btn" href="/timesheet/export?{e(_qs(t, fmt="xlsx"))}" download>'
               f'⬇ Excel</a><a class="btn quiet" href="/timesheet/export?{e(_qs(t, fmt="csv"))}" download>⬇ CSV</a>'
               f'<button type="button" class="quiet copy" data-copy="{e(tsv)}">Copy as table</button>'
               f'<button type="button" class="quiet copy" data-copy="{e(md)}">Copy Markdown</button></div>')
    note = ('<p class="muted" style="font-size:.85rem">Hours are active time: breaks over 15 minutes are left out, and '
            'sessions at the same time count once. ≈ marks days recorded before 0.4.0 (first to last message). '
            'Fix a project or a line with <b>✏️ Edit</b> on the day.</p>' if cfg.time_mode == "active" else
            '<p class="muted" style="font-size:.85rem">Hours are first to last message (Settings → Summaries → Time).</p>')
    return (head + form + write + f'<section class="block">{exports}{table}{note}</section>'
            + f'<section class="block"><h2>Per project</h2><ul class="totals">{per}</ul></section>')


def page_day(app: App, cfg: Config, day: date, edit_key: str | None = None) -> tuple[str, str | None]:
    store = _store(cfg)
    if store is None:
        return (f'<div class="empty">No data repo found at <code>{e(home_relative(cfg.store_repo))}</code>. '
                f'<a href="/settings">Set it up in Settings</a>.</div>'), None
    records = list(store.iter_json(f"records/{day.isoformat()}"))
    ov = overrides.load(store.path)
    accounts, projects = aggregate(records, ov, cfg.time_mode)
    metas = summary_metas(store, day)
    saved = metas[0].get("summaries", {}) if metas else {}

    history_days = [day - timedelta(days=i) for i in reversed(range(HISTORY_DAYS))]
    history, names = [], [a.name for a in cfg.accounts]
    known: dict[str, str] = {k: ov.names.get(k) or str(v.get("name") or k) for k, v in ov.projects.items()}
    chat_counts: dict[date, int] = {}
    activity: dict[date, int] = {}  # tokens per day (1 for a day with only chats): drives the calendar
    cache = daycache.DayCache(store, cfg.time_mode)  # one small entry per day: no re-reading 5 weeks of records
    for d, entry in cache.days(history_days[0], history_days[-1]).items():
        per: dict[str, int] = dict(entry.get("tokens") or {})
        for proj in entry.get("projects") or []:
            if proj.get("kind") in ("git", "custom"):
                known.setdefault(proj["key"], proj["name"])
        if entry.get("chats"):
            chat_counts[d] = entry["chats"]
        for name in per:
            if name not in names:
                names.append(name)  # account configured on another device only
        history.append((d, per))
        tokens_d = sum(per.values())
        if tokens_d or chat_counts.get(d):
            activity[d] = tokens_d or 1
    cache.save()
    chart_history = history[-14:]
    streak = viz.streak(set(activity), day)
    fun = cfg.fun

    prev_d, next_d = day - timedelta(days=1), day + timedelta(days=1)
    today = date.today()
    arrows = (f'<a class="btn quiet" href="/?d={prev_d.isoformat()}" aria-label="Previous day">‹</a>'
              + (f'<a class="btn quiet" href="/?d={next_d.isoformat()}" aria-label="Next day">›</a>' if day < today else "")
              + ('<a class="btn quiet" href="/">Today</a>' if day != today else ""))
    jump = (f'<form method="get" action="/" class="row" style="margin:0"><label for="d" class="muted" style="margin:0">Go to</label>'
            f'<input type="date" id="d" name="d" value="{day.isoformat()}" max="{today.isoformat()}" style="width:auto">'
            f'<button class="quiet">Show</button></form>')
    flame = (f'<span class="flame" title="{streak} days in a row with work"><i aria-hidden="true">🔥</i>'
             f'{streak}-day streak</span>') if fun and streak >= 2 else ""
    head = (f'<div class="daynav"><h1><span class="grad">{day:%A, %d %B}</span><small>{day.year}'
            f'{", today" if day == today else ""}{flame}</small></h1><div class="arrows">{arrows}</div></div>')
    celebrate = []
    if fun and viz.is_record_day(activity, day):
        celebrate.append(f"record:{day.isoformat()}|Your biggest day in {HISTORY_DAYS // 7} weeks! 🎉")
    if fun and day == today and streak in (3, 7, 14, 30, 60, 100) and day in activity:
        celebrate.append(f"streak{streak}:{day.isoformat()}|{streak}-day streak! 🔥")
    party = f'<div id="fun" hidden data-celebrate="{e(chr(10).join(celebrate))}"></div>' if fun else ""

    busy = " disabled" if app.jobs.snapshot().get("running") else ""
    actions = (f'<div class="row"><form method="post" action="/action/collect"><input type="hidden" name="csrf" value="{app.csrf}">'
               f'<button{busy}>Sync now</button></form>'
               f'<form method="post" action="/action/report"><input type="hidden" name="csrf" value="{app.csrf}">'
               f'<input type="hidden" name="d" value="{day.isoformat()}">'
               f'<button class="quiet"{busy}>{"Rewrite summaries" if saved else "Write summaries"}</button></form>{jump}</div>')

    if not records:
        latest = chats.chat_days(store.path, limit=1)
        if latest and latest[0][0] != day:
            d0, n0 = latest[0]
            head += (f'<p class="muted" style="margin:10px 0 0">Your latest claude.ai chats are on '
                     f'<a href="/?d={d0.isoformat()}">{d0:%a %d %b %Y}</a> ({n0}).</p>')
        robot = viz.ROBOT if fun else ""
        body = (head + '<p class="lede">Nothing recorded for this day yet.</p>' + _chart(chart_history, names, day, chat_counts)
                + f'<div class="empty">{robot}<p>Sessions appear here after a Claude Code session ends, or when you press '
                  '<b>Sync now</b>. Other devices show up once they have synced. claude.ai chats appear after you '
                  '<a href="/sources">import an export</a>.</p></div>' + actions + party)
        return body, None

    total = sum(sum(a["tokens"].values()) for a in accounts.values())
    sessions = sum(a["sessions"] for a in accounts.values())
    n_chats = sum(a.get("chats", 0) for a in accounts.values())
    devices = sorted({d for a in accounts.values() for d in a["devices"]})
    code_projects = sum(1 for p in projects.values() if p.sessions)
    chat_txt = f'{n_chats} claude.ai chat{"s" * (n_chats != 1)}'
    if sessions:
        lede = (f'<p class="lede">{_n(total)} tokens across {sessions} session{"s" * (sessions != 1)} in '
                f'{code_projects} project{"s" * (code_projects != 1)}, on {_join(devices)}'
                f'{", plus " + chat_txt if n_chats else ""}.</p>')
    else:
        lede = f'<p class="lede">{chat_txt}. claude.ai exports have no token counts.</p>'
    minutes = sum(p.minutes for p in projects.values())
    n_projects = sum(1 for p in projects.values() if p.sessions or p.chats)
    tiles = [("Tokens", _short(total) if sessions else "–", total if sessions else None, "short"),
             ("Sessions", str(sessions), sessions, "int"), ("Projects", str(n_projects), n_projects, "int"),
             ("Time", _dur(minutes), minutes, "dur")]
    lede += ('<div class="tiles">' + "".join(
        f'<div class="tile"><div class="k">{k}</div><div class="v"'
        + (f' data-count="{n}" data-fmt="{fmt}"' if n is not None else "") + f'>{e(v)}</div></div>'
        for k, v, n, fmt in tiles) + "</div>")
    if fun:
        out_tokens = sum(a["tokens"]["output"] for a in accounts.values())
        n_commits = sum(len(p.commits) for p in projects.values())
        fact = viz.fun_fact(day, total, out_tokens, sum(len(p.files) for p in projects.values()), n_commits, minutes)
        lede += f'<p class="fact"><span aria-hidden="true">✨</span> {e(fact)}</p>' if fact else ""
    insights = (
        '<div class="insights">'
        f'<div class="panel"><h2>When you worked</h2>{viz.clock(viz.minutes_by_hour(records, day))}</div>'
        f'<div class="panel"><h2>Last {HISTORY_DAYS // 7} weeks</h2>{viz.heatmap(activity, day, HISTORY_DAYS // 7)}'
        f'<p class="muted streaktxt">{"🔥 " if fun and streak >= 2 else ""}{streak} day{"s" * (streak != 1)} in a row</p></div>'
        f'<div class="panel"><h2>Projects today</h2>'
        f'{viz.project_bars([(p.name, p.minutes) for p in projects.values()]) or "<p class=muted>No time recorded.</p>"}</div>'
        '</div>')

    entries = []
    edit_html = ""
    for p in sorted(projects.values(), key=lambda p: (p.kind == "chat", p.kind == "misc", -sum(p.tokens.values()))):
        s, fit, written = pick_summary(metas, p.key, [x["id"] for x in p.sessions_list], legacy_fit(p, projects))
        if fit == "other":  # written for other sessions (moved since): don't show someone else's story
            s = {}
        stale = fit == "partial" or ov.stale.get(f"{day.isoformat()}|{p.key}", 0) > _summary_age({"generated_at": written})
        bullets = s["bullets"] if s.get("bullets") else plain_summary(p)
        if fit == "other":
            src = "Your prompts. The saved summary was about other sessions: press “Rewrite summaries”."
        elif not s:
            src = "Your prompts. Press “Write summaries” for a written summary."
        elif stale:
            src = "Summary out of date: sessions were moved or added. Press “Rewrite summaries”."
        else:
            src = "Summary by Claude" if s.get("source") == "ai" else "Your prompts"
        mine = ov.lines.get(f"{day.isoformat()}|{p.key}")
        line = summary_line(s, p, mine)
        if s.get("paragraph"):
            items = (f'<p class="para">{e(s["paragraph"])}</p><details><summary>Details</summary><ul>'
                     + "".join(f"<li>{e(b[2:] if b.startswith('- ') else b)}</li>" for b in bullets) + "</ul></details>")
        else:
            items = ("<ul>" + "".join(f"<li>{e(b[2:] if b.startswith('- ') else b)}</li>" for b in bullets) + "</ul>"
                     if s or not p.chat_items else "")
        if p.chat_items:
            if not s:
                src = ("claude.ai's summary of each chat, or your first messages. "
                       "Press “Write summaries” for a written summary of the day.")
            items += _chat_list(p.chat_items, open_=not s)
        where = (f"{e(p.remote['host'])}/{e(p.remote['slug'])}" if p.remote
                 else "From your claude.ai export" + (" · files in Claude's sandbox" if p.folders else "")
                 if p.kind == "chat"
                 else ("Folders: " + _join(sorted(p.folders)) if p.folders else
                       "Your own project" if p.kind == "custom" else "Not in a git repo"))
        facts = [("Accounts", _join(sorted(p.accounts))), ("Devices", _join(sorted(p.devices)))]
        facts += [("Sessions", str(p.sessions))] if p.sessions else []
        facts += [("Chats", str(p.chats))] if p.chats else []
        time_txt = ("≈" if p.approx else "") + _dur(p.minutes)
        facts += [("Time", f'<span title="{"Before 0.4.0: first to last message" if p.approx else "Active time"}">'
                   f'{time_txt}</span>'),
                  ("Tokens", _n(sum(p.tokens.values())) if p.sessions else "no data from claude.ai")]
        facts += [("Files edited", str(len(p.files)))] if p.files or p.sessions else []
        facts += _detail_facts(p, cfg)
        dl = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts)
        extra = ""
        if p.commits:
            extra += (f'<details><summary>{len(p.commits)} commit{"s" * (len(p.commits) != 1)}</summary><ul>'
                      + "".join(f"<li><code>{e(h)}</code> {e(sub)}</li>" for h, sub in p.commits.items()) + "</ul></details>")
        if p.files:
            extra += ('<details><summary>Files edited</summary><ul>'
                      + "".join(f"<li><code>{e(f)}</code></li>" for f in sorted(p.files)[:60]) + "</ul></details>")
        tag = card_tag(p, s, ov)
        edit_url = f"/?d={day.isoformat()}&edit={quote(p.key, safe='')}"
        head_row = (f'<div class="cardhead"><h3>{e(p.name)}</h3>'
                    + (f'<span class="pill tag">{e(tag)}</span>' if tag else "")
                    + f'<a class="btn quiet edit" href="{edit_url}#edit" data-popup="edit" '
                      f'aria-label="Edit {e(p.name)}">✏️ Edit</a></div>')
        tline = (f'<div class="tline"><span class="muted">Timesheet:</span> {e(line)}</div>'
                 if s.get("source") == "ai" or mine else "")
        entries.append(f'<article class="entry">{head_row}<div class="where">{where}</div>{_guess_html(app, day, p)}'
                       f'{_refs_html(p, cfg)}{items}{tline}{_tasks_html(p, s)}<div class="src">{e(src)}</div>'
                       f'<dl class="facts">{dl}</dl>{extra}</article>')
        if edit_key == p.key:
            edit_html = _edit_form(app, day, p, projects, known, ov, line, s.get("tag") or "")

    acct_rows = "".join(
        f'<tr><td><i style="display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;'
        f'background:{COLORS[names.index(n) % len(COLORS)] if n in names else "var(--muted)"}"></i>{e(n)}</td>'
        f'<td>{_short(a["tokens"]["input"])}</td><td>{_short(a["tokens"]["output"])}</td>'
        f'<td class="opt">{_short(a["tokens"]["cache_creation"])}</td><td class="opt">{_short(a["tokens"]["cache_read"])}</td>'
        f'<td><b>{_short(sum(a["tokens"].values()))}</b></td></tr>' for n, a in sorted(accounts.items())
        if a["sessions"])
    models_html = "".join(
        f'<li title="{e(r["model"])}"><div class="top"><span class="name">{e(r["label"])}</span>'
        f'<span class="who">{e(r["account"])}</span><span class="num">{_short(r["total"])} · {r["share"]}%</span></div>'
        f'<div class="bar"><span style="width:{max(r["share"], 1)}%"></span></div>'
        + (f'<div class="sub">{"all" if r["sub"] >= r["total"] else _short(r["sub"])} from sub-agents</div>' if r["sub"] else "")
        + "</li>" for r in model_rows(accounts))
    aside = (f'<aside><div class="panel"><h2>Tokens by account</h2><div class="tablewrap"><table>'
             f'<tr><th>Account</th><th>In</th><th>Out</th><th class="opt">Cache<br>write</th><th class="opt">Cache<br>read</th><th>Total</th></tr>'
             f'{acct_rows}</table></div></div><div class="panel"><h2>By model</h2>{viz.donut(model_rows(accounts))}<ul class="models">{models_html}</ul>'
             f'<p class="muted" style="font-size:.8rem;margin:10px 0 0">The model Claude Code logged for each reply, '
             f'including sub-agents. Usage, not a bill.</p></div></aside>')
    if not sessions:
        aside = ""  # chat-only day: no token tables to show
    body = (head + edit_html + lede + _chart(chart_history, names, day, chat_counts) + actions + insights
            + f'<div class="cols"><main><h2>What I worked on</h2>{"".join(entries)}</main>{aside}</div>' + party)
    return body, None


def page_sources(app: App, cfg: Config) -> str:
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    busy = " disabled" if app.jobs.snapshot().get("running") else ""
    code_rows = []
    for a in cfg.accounts:
        n = _transcript_count(a) if a.config_dir.is_dir() else 0
        state = (f'<span class="state-ok">Automatic</span> · {n} session file{"s" * (n != 1)}'
                 if hook_installed(a) else '<span class="state-bad">Hook not installed</span> · '
                 '<a href="/settings">install it in Settings</a>')
        code_rows.append(f'<tr><td>{e(a.name)}</td><td style="text-align:left"><code>{e(home_relative(a.config_dir))}</code>'
                         f'</td><td style="text-align:left">{state}</td></tr>')

    st = chats.load_state()
    last = st.get("last_import")
    if last:
        imported = (f'<span class="state-ok">Imported</span> {e(str(last.get("at", ""))[:16].replace("T", " "))}: '
                    f'{e(last.get("chats", 0))} chats as {e(last.get("records", 0))} day records, '
                    f'filed under <b>{e(last.get("account", ""))}</b> (from {e(last.get("file", ""))}).')
    else:
        imported = '<span class="muted">Not imported yet.</span>'
    watch = ""
    if cfg.watch_downloads:
        folder = cfg.downloads
        if not folder.is_dir():
            watch = f'<span class="state-bad">Folder not found:</span> <code>{e(home_relative(folder))}</code>'
        else:
            try:
                waiting = len(chats.pending_downloads(cfg))
            except OSError:
                waiting = 0
            watch = (f'<span class="state-ok">Watching</span> <code>{e(home_relative(folder))}</code>'
                     + (f' · {waiting} new export{"s" * (waiting != 1)} waiting for the next sync' if waiting else ""))
    acct_opts = "".join(f'<option{" selected" if a.name == cfg.chats_account else ""}>{e(a.name)}</option>'
                        for a in cfg.accounts)
    days = chats.chat_days(cfg.store_repo, limit=12) if _store(cfg) else []
    summ_note = (", plus claude.ai's own short summary of each chat (turned on below)"
                 if cfg.keep_chat_summaries else "")
    day_links = ""
    if days:
        day_links = ('<p style="margin-bottom:4px">Days with chats:</p><div class="row" style="margin-top:0;gap:6px">'
                     + "".join(f'<a class="btn quiet" href="/?d={d.isoformat()}">{d:%d %b %Y}'
                               f' <span class="muted">· {n}</span></a>' for d, n in days) + "</div>")
    dl = home_relative(cfg.downloads_dir) if cfg.downloads_dir else ""
    return f"""<h1>Sources<small>Where your work log comes from</small></h1>
<section class="block" style="margin-top:24px"><h2>Claude Code</h2>
<p class="help">Collected automatically from this device's session files when a session ends, every hour, and when you press Sync now. Includes token counts.</p>
<div class="tablewrap"><table><tr><th>Account</th><th style="text-align:left">Folder</th><th style="text-align:left">State</th></tr>{"".join(code_rows)}</table></div>
<form method="post" action="/action/collect" class="row">{c}<button{busy}>Sync now</button></form></section>

<section class="block"><h2>claude.ai chats</h2>
<p class="help">claude.ai has no live connection, so chats come from its data export. worklog keeps only chat titles,
short redacted snippets of your own messages, tool names and edited file names{summ_note}. Claude's replies and
attachments are never saved. Exports have no token counts.</p>
<p>{imported}</p>
{day_links}
<details{" open" if not last else ""}><summary>How to get the export</summary><ol>
<li>Open <a href="{EXPORT_SETTINGS_URL}" target="_blank" rel="noopener noreferrer">claude.ai settings</a> → <b>Privacy</b> → <b>Export data</b>.</li>
<li>Wait for the e-mail and open its link. It downloads a small list of files.</li>
<li>Download <code>conversations-000.zip</code> (and <code>-001</code>… if there are more). The links expire after 24 hours.</li>
<li>Upload it below, or turn on <b>Import from Downloads</b> and press Sync now.</li></ol></details>
<label for="chatfile">Upload <code>conversations-*.zip</code> or <code>conversations.json</code></label>
<div class="row" style="margin-top:4px"><input type="file" id="chatfile" accept=".zip,.json">
<button id="chatupload" data-csrf="{app.csrf}" data-max="{MAX_UPLOAD}"{busy}>Import</button>
<span id="chatuploadstate" class="muted" role="status"></span></div>

<form method="post" action="/sources/settings" style="border-top:1px solid var(--rule);padding-top:14px;margin-top:18px">{c}
<h3>Import settings</h3>
<label for="chat_account">File imported chats under</label><select id="chat_account" name="account">{acct_opts}</select>
<div class="check"><input type="checkbox" id="watch" name="watch" value="1"{" checked" if cfg.watch_downloads else ""}>
<label for="watch">Import from Downloads: on each sync, import new <code>conversations-*.zip</code> files found in the folder below</label></div>
<div class="check"><input type="checkbox" id="keep_summaries" name="keep_summaries" value="1"{" checked" if cfg.keep_chat_summaries else ""}>
<label for="keep_summaries">Also keep claude.ai's own summary of each chat (written by Claude, shortened to 600 characters and redacted). It shows what each chat was about. Re-import your export after turning this on.</label></div>
<label for="downloads">Downloads folder (leave empty for <code>{e(home_relative(default_downloads_dir()))}</code>)</label>
<input type="text" id="downloads" name="downloads" value="{e(dl)}">
<p class="muted" style="font-size:.85rem">{watch or "Off. Nothing in your Downloads folder is read."} Files are never moved or deleted.</p>
<div class="row"><button>Save</button></div></form>
<form method="post" action="/sources/scan" class="row">{c}<button class="quiet"{busy if cfg.watch_downloads else " disabled"}>Check Downloads now</button></form></section>

<section class="block"><h2>Browser extension</h2>
<p class="help">Coming later: sync claude.ai chats automatically, without exporting. <span class="muted">Not available yet.</span></p></section>"""


def page_setup(app: App, form: dict | None = None) -> str:
    f = form or {}
    default_device = re.search(r'device_name = "([^"]+)"', example_config()).group(1)

    def v(k: str, d: str = "") -> str:
        return e(f.get(k, d))

    basics = checks.run(None)
    pre = "" if all(x.ok for x in basics) else checklist_html(app, basics, "Before you start", show_tips=False)
    return pre + f"""<h1>Set up this device<small>One time only. You can change everything later in Settings.</small></h1>
<form method="post" action="/setup" data-plain style="margin-top:24px"><input type="hidden" name="csrf" value="{app.csrf}">
<section class="block"><h2>This device</h2><p class="help">A short name so the report can tell your machines apart. Use a different name on each device.</p>
<label for="device">Device name</label><input type="text" id="device" name="device" value="{v('device', default_device)}" required pattern="{NAME_PATTERN}"></section>
<section class="block"><h2>Data repo</h2><p class="help">A private git repo (GitHub, GitLab or Bitbucket) that every device syncs to. Create it empty on the website first.
If this device already has a clone, just enter its folder. Otherwise also paste the clone URL and it will be cloned for you. The URL must work without a password prompt (SSH deploy key, see Full setup in docs/GUIDE.md).</p>
<label for="store">Folder</label><input type="text" id="store" name="store" value="{v('store', '~/claude-worklog-data')}" required>
<label for="clone">Clone URL (only if the folder doesn't exist yet)</label><input type="text" id="clone" name="clone" value="{v('clone')}" placeholder="git@github.com:you/claude-worklog-data.git"></section>
<section class="block"><h2>First Claude account</h2><p class="help">The account you use with plain <code>claude</code> lives in <code>~/.claude</code>. You can add more accounts after setup.</p>
<label for="aname">Account name</label><input type="text" id="aname" name="aname" value="{v('aname', 'personal')}" required pattern="{NAME_PATTERN}">
<label for="adir">Claude config folder</label><input type="text" id="adir" name="adir" value="{v('adir', '~/.claude')}" required></section>
<button>Save setup</button></form>"""


NOTICE_ICONS = {"login": "👤", "folder": "📁", "hook": "🪝", "apikey": "🔑", "note": "✨"}


def _notices_html(app: App, cfg: Config) -> str:
    """Watcher notices: new logins and folders, vanished hooks, auto-setup notes. Each can be dismissed."""
    try:
        items = watcher.notices(cfg)
    except Exception:  # noqa: BLE001 - a damaged state file must not break every page
        log.exception("could not build notices")
        return ""
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    rows = []
    for n in items[:5]:
        if n["kind"] == "folder":
            act = (f'<form method="post" action="/accounts/add-folder">{c}<input type="hidden" name="folder" '
                   f'value="{e(n["folder"])}"><button>Add as account</button></form>')
        elif n["kind"] == "hook":
            act = (f'<form method="post" action="/accounts/hook">{c}<input type="hidden" name="name" '
                   f'value="{e(n["account"])}"><button>Reinstall hook</button></form>')
        elif n["kind"] == "login":
            act = '<a class="btn" href="/settings#logins">Name it</a>'
        else:
            act = ""
        dismiss = (f'<form method="post" action="/notices/dismiss">{c}<input type="hidden" name="key" value="{e(n["key"])}">'
                   f'<button class="quiet" aria-label="Dismiss">✕</button></form>')
        rows.append(f'<div class="notice {e(n["kind"])}" role="status"><span class="ico" aria-hidden="true">'
                    f'{NOTICE_ICONS.get(n["kind"], "•")}</span><span class="txt">{e(n["text"])}</span>'
                    f'<span class="acts">{act}{dismiss}</span></div>')
    return "".join(rows)


def _ring(done: int, total: int) -> str:
    frac = done / total if total else 1
    c = 2 * 3.14159 * 16
    return (f'<svg class="ring" viewBox="0 0 40 40" role="img" aria-label="{done} of {total} done">'
            f'<circle cx="20" cy="20" r="16" class="track"/><circle cx="20" cy="20" r="16" class="fill" '
            f'stroke-dasharray="{c * frac:.1f} {c:.1f}" transform="rotate(-90 20 20)"/>'
            f'<text x="20" y="24.5" text-anchor="middle">{done}/{total}</text></svg>')


def _check_item(app: App, ch: checks.Check, open_: bool) -> str:
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    icon = '<span class="tick ok" aria-label="done">✓</span>' if ch.ok else '<span class="tick" aria-label="to do">○</span>'
    if ch.ok:
        return f'<li class="done">{icon}<span>{e(ch.label)}</span></li>'
    body = f'<p class="why">{e(ch.why)}</p>' if ch.why else ""
    for text, cmd in ch.steps:
        body += f"<p>{e(text)}</p>" if text else ""
        if cmd:
            body += (f'<div class="cmdrow"><pre class="cmd">{e(cmd)}</pre>'
                     f'<button type="button" class="quiet copy" data-copy="{e(cmd)}">Copy</button></div>')
    if ch.link:
        body += f'<p><a href="{e(ch.link[1])}" target="_blank" rel="noopener noreferrer">{e(ch.link[0])} ↗</a></p>'
    if ch.action.startswith("install_hook:"):
        body += (f'<form method="post" action="/accounts/hook">{c}<input type="hidden" name="name" '
                 f'value="{e(ch.action.split(":", 1)[1])}"><button>Install hook</button></form>')
    return (f'<li class="todo">{icon}<details{" open" if open_ else ""}><summary>{e(ch.label)}</summary>'
            f'<div class="fix">{body}</div></details></li>')


def checklist_html(app: App, items: list, title: str = "Get Worklog ready", show_tips: bool = True) -> str:
    req = [x for x in items if x.required]
    tips = [x for x in items if not x.required]
    done = sum(x.ok for x in req)
    first_todo = next((x.id for x in req if not x.ok), None)
    lis = "".join(_check_item(app, x, x.id == first_todo) for x in req)
    tip_html = ""
    if show_tips and tips:
        tip_html = (f'<details class="tips"><summary>Make it even better (optional) · '
                    f'{sum(x.ok for x in tips)} of {len(tips)}</summary><ol class="checks">'
                    + "".join(_check_item(app, x, False) for x in tips) + "</ol></details>")
    return (f'<section class="checklist" id="checklist" aria-label="{e(title)}"><div class="clhead">{_ring(done, len(req))}'
            f'<div><h2>{e(title)}</h2><p class="muted">{done} of {len(req)} done · steps shown for '
            f'{e(checks.OS_NAMES[checks.os_kind()])}</p></div></div><ol class="checks">{lis}</ol>{tip_html}</section>')


def _update_html(app: App, cfg: Config) -> str:
    """"A new version is out" with a one-click update (or the command, when we can't do it here)."""
    new = updates.available() if cfg.check_updates and not services.demo_mode() else None
    if not new or updates.dismissed(new):
        return ""
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    busy = " disabled" if app.jobs.snapshot().get("running") else ""
    if updates.upgrade_command():
        act = f'<form method="post" action="/update" data-plain>{c}<button{busy}>Update now</button></form>'
    else:
        cmd = updates.manual_command()
        act = (f'<code>{e(cmd)}</code><button type="button" class="quiet copy" data-copy="{e(cmd)}">Copy</button>')
    return (f'<div class="notice update" role="status"><span class="ico" aria-hidden="true">🎁</span>'
            f'<span class="txt"><b>Worklog {e(new)} is out</b> (you have {e(__version__)}). '
            f'<a href="{updates.CHANGELOG_URL}" target="_blank" rel="noopener noreferrer">What\'s new ↗</a></span>'
            f'<span class="acts">{act}<form method="post" action="/notices/dismiss">{c}<input type="hidden" name="key" '
            f'value="update:{e(new)}"><button class="quiet" aria-label="Dismiss">✕</button></form></span></div>')


def _restart_html(app: App) -> str:
    """Upgraded with pipx/pip while this server kept running: offer to switch to the new version."""
    disk = updates.version_on_disk()
    if services.demo_mode() or not disk or disk == __version__ or app.jobs.snapshot().get("running"):
        return ""
    return (f'<div class="notice update" role="status"><span class="ico" aria-hidden="true">🔄</span>'
            f'<span class="txt"><b>Worklog {e(disk)} is installed</b>, but this page still runs {e(__version__)}. '
            f'Restart to use the new version.</span><span class="acts"><form method="post" action="/restart" data-plain>'
            f'<input type="hidden" name="csrf" value="{app.csrf}"><button>Restart now</button></form></span></div>')


def top_panels(app: App, cfg: Config, active: str = "") -> str:
    """Shown under the header on every page: the setup checklist (until done) and watcher notices."""
    out = _restart_html(app) + _update_html(app, cfg)
    just = updates.pop_updated()
    if just:
        out = (f'<div class="banner ok" role="status" data-celebrate="updated:{e(just)}|Worklog {e(just)} is here 🎉">'
               f'Worklog is now {e(just)}. <a href="{updates.CHANGELOG_URL}" target="_blank" '
               f'rel="noopener noreferrer">See what\'s new ↗</a></div>') + out
    if not services.demo_mode():
        items = checks.cached(cfg, app.watcher is not None and app.watcher.running)
        broken = [x for x in items if x.required and not x.ok]
        if not broken:
            checks.mark_setup_done()
        elif checks.setup_done():  # set up once already: just flag what broke, not the whole list
            out += "".join(f'<div class="notice hook" role="alert"><span class="ico" aria-hidden="true">⚠️</span>'
                           f'<span class="txt">{e(x.label)}: this stopped working.</span><span class="acts">'
                           f'<a class="btn" href="/settings#checklist">Fix it</a></span></div>' for x in broken)
        elif active != "settings":  # Settings shows the full checklist in its own section
            out += checklist_html(app, items)
    return out + _public_repo_warning(cfg) + _notices_html(app, cfg)


def _public_repo_warning(cfg: Config) -> str:
    """Shown on every page while the data repo is public (from the last check, never waits for the network)."""
    r = syncstatus.remote(cfg)
    if not r or syncstatus.cached_visibility(r["host"], r["slug"]) != "public":
        return ""
    return (f'<div class="notice hook" role="alert"><span class="ico" aria-hidden="true">⚠️</span><span class="txt">'
            f'<b>Your data repo {e(r["host"])}/{e(r["slug"])} is public.</b> Anyone can read your prompts and project names. '
            f'Make it private on GitHub.</span><span class="acts"><a class="btn" href="/settings#data-repo">How</a>'
            f'</span></div>')


MODEL_CHOICES = (("", "Account default"), ("opus", "Opus (latest)"), ("sonnet", "Sonnet (latest)"),
                 ("haiku", "Haiku (latest)"), ("fable", "Fable (latest)"))


def _forget_link(login: dict) -> str:
    return (f'<a class="btn quiet forget" href="/settings?forget={e(login["ref"])}#forget" '
            f'title="Delete Worklog\'s history of this login">Forget…</a>')


def _forget_confirm(app: App, cfg: Config, ref_: str) -> str:
    """The "are you sure?" card: exactly what forgetting this login deletes, and what it doesn't."""
    p = forget.plan(cfg, ref_)
    if p is None:
        return '<div class="banner bad" role="alert">That login is no longer recorded on this device.</div>'
    days = sorted(p.days)
    span = (f" from {len(days)} day{'s' * (len(days) != 1)} ({days[0]:%d %b %Y} – {days[-1]:%d %b %Y})"
            if len(days) > 1 else f" from {days[0]:%d %b %Y}" if days else "")
    lost = [f"<li><b>{len(p.files)} Claude Code session record{'s' * (len(p.files) != 1)}</b>{span}: what you worked "
            f"on, prompts, tokens, files and commits</li>"]
    if p.projects:
        lost.append("<li>The saved summaries of the projects in those sessions (write them again with "
                    "“Write summaries” on each day)</li>")
    if p.account and not p.keep_account:
        lost.append(f"<li>The account <b>{e(p.account)}</b>. Its other sessions (from logins without a name) move to "
                    f"the folder&#39;s default account.</li>")
    elif p.account:
        lost.append(f"<li>The link between <b>{e(p.account)}</b> and this login (it&#39;s your only account, so it stays)</li>")
    lost.append("<li>The login itself, from this list</li>")
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    return f"""<section class="block danger-zone" id="forget" role="alertdialog" aria-labelledby="forget-title">
<h2 id="forget-title">Forget {e(p.label)}?</h2>
<p><b>This permanently deletes Worklog&#39;s history of this login on this device.</b> You will lose:</p>
<ul>{"".join(lost)}</ul>
<p class="muted">Not touched: Claude Code&#39;s own files, your other accounts, and records synced from your other
computers. The old versions stay in your data repo&#39;s git history. If this account logs in again later, its new
sessions are tracked as usual.</p>
<form method="post" action="/accounts/forget">{c}<input type="hidden" name="ref" value="{e(p.ref)}">
<div class="check"><input type="checkbox" id="forget_ok" name="confirm" value="1" required>
<label for="forget_ok">I understand this deletes the history above and can&#39;t be undone</label></div>
<div class="row"><button class="danger-solid">Forget and delete</button><a class="btn quiet" href="/settings#logins">Cancel</a></div>
</form></section>"""


def _detected_section(cfg: Config, csrf_field: str) -> str:
    """Logins seen by the SessionStart hook. E-mails are masked; forms carry an opaque ref, never the id."""
    by_dir = cfg.accounts_by_dir()
    rows = []
    logins = detected_logins()
    per_dir: dict[Path, int] = {}
    for login in logins:
        for d in login["dirs"]:
            per_dir[d] = per_dir.get(d, 0) + 1
    for login in logins:
        for d in login["dirs"]:
            if d not in by_dir:
                continue  # folder not configured on this device
            current = cfg.resolve_account(d, login["account_id"], login["email"])
            linked = current.match_account_id == login["account_id"] or (
                current.match_email and login["email"] and current.match_email.casefold() == login["email"].casefold())
            seen = login["last_seen"][:16].replace("T", " ")
            where = f'<span class="muted">folder <code>{e(home_relative(d))}</code> · last seen {e(seen)}</span>'
            if len(by_dir[d]) == 1 and per_dir[d] == 1:  # the only login in its own folder: nothing to tell apart
                rows.append(f'<div class="acct"><h3>{e(mask_email(login["email"]))}</h3><div class="meta">'
                            f'<span class="pill ok">Counted as {e(current.name)}</span>{where}</div>'
                            f'<div class="row" style="margin-top:8px">{_forget_link(login)}</div></div>')
                continue
            status = (f'<span class="pill ok">Counted as {e(current.name)}</span>' if linked
                      else f'<span class="pill bad">No name yet</span> <span class="muted">counted as {e(current.name)} '
                           f'(folder default)</span>')
            names = "".join(f'<option value="{e(a.name)}">' for a in by_dir[d])
            list_id = f"names-{login['ref']}-{len(rows)}"
            rows.append(
                f'<div class="acct"><h3>{e(mask_email(login["email"]))}</h3>'
                f'<div class="meta">{status}{where}</div>'
                f'<form method="post" action="/accounts/name" class="row" style="margin-top:8px">{csrf_field}'
                f'<input type="hidden" name="ref" value="{e(login["ref"])}"><input type="hidden" name="dir" value="{e(home_relative(d))}">'
                f'<label for="{list_id}-in" class="muted" style="margin:0">Account name</label>'
                f'<input type="text" id="{list_id}-in" name="name" list="{list_id}" value="{e(current.name) if linked else ""}" '
                f'placeholder="work" required pattern="{NAME_PATTERN}" style="width:auto"><datalist id="{list_id}">{names}</datalist>'
                f'<button class="{"quiet" if linked else ""}">{"Rename" if linked else "Name this login"}</button>'
                f'{_forget_link(login)}</form></div>')
    body = "".join(rows) or ('<div class="empty">No logins recorded yet. Install the hook, then start a Claude Code '
                             'session. Each account you log in with appears here.</div>')
    return (f'<h3 id="logins" style="margin-top:22px">Logins seen on this device</h3><p class="help" style="margin-top:6px">Each account '
            f'you were logged in with when a session started. A login with <b>No name yet</b> is counted under the '
            f'folder&#39;s default account: give it a name to count it separately. Naming also moves its sessions from the '
            f'last {REFILE_DAYS} days. Only the name is written to your data repo; the e-mail and account id stay on this '
            f'device.</p>{body}')


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = max(0, int(time.time() - ts))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= size:
            n = s // size
            return f"{n} {unit}{'s' * (n != 1)} ago"
    return "just now"


def data_repo_section(app: App, cfg: Config) -> str:
    """Connected or not, to which repo, private or not, and whether uploads work."""
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    st = syncstatus.status(cfg)
    folder = e(home_relative(cfg.store_repo))
    if not st["is_repo"]:
        body = (f'<div class="meta"><span class="pill bad">Not set up</span><span class="muted"><code>{folder}</code> '
                f'is not a git folder yet.</span></div><p class="help" style="margin-top:10px">Create it with '
                f'<code>git init {folder}</code>, or clone your private repo there.</p>')
        return f'<section class="block" id="data-repo"><h2>Private data repo</h2>{body}</section>'
    buttons = (f'<div class="row"><form method="post" action="/action/collect">{c}<button>Sync now</button></form>'
               f'<form method="post" action="/sync/test" data-result="dialog" data-oktitle="Connection works" '
               f'data-badtitle="Can&#39;t connect">{c}<button class="quiet">Test connection</button>'
               f'</form></div>')
    if not st["remote"]:
        body = (f'<div class="meta"><span class="pill">Local only</span><span class="muted">Not connected. Your log '
                f'stays in <code>{folder}</code> on this computer and isn&#39;t backed up.</span></div>'
                f'<p class="help" style="margin-top:10px">Using Claude Code on more than one computer, or want a backup? '
                f'Create an <b>empty, private</b> repo on GitHub, GitLab or Bitbucket and connect this folder to it:</p>'
                f'<div class="cmdrow"><pre class="cmd">git -C {folder} remote add origin git@github.com:YOU/claude-worklog-data.git</pre>'
                f'<button type="button" class="quiet copy" data-copy="git -C {folder} remote add origin '
                f'git@github.com:YOU/claude-worklog-data.git">Copy</button></div>'
                f'<p class="help"><a href="https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#full-setup" '
                f'target="_blank" rel="noopener noreferrer">Step-by-step guide ↗</a> (including a key so the background '
                f'service can upload without a password)</p>')
        return f'<section class="block" id="data-repo"><h2>Private data repo</h2>{body}</section>'
    vis = syncstatus.cached_visibility(st.get("host"), st.get("slug"))
    if vis is None:  # ask GitHub in the background; the page never waits for it
        threading.Thread(target=syncstatus.visibility, args=(st["host"], st["slug"]), daemon=True).start()
    privacy = {"private": '<span class="pill ok">🔒 Private</span>',
               "public": '<span class="pill bad">⚠️ PUBLIC: anyone can read your log</span>',
               "unknown": ('<span class="pill">Privacy not checked (GitHub didn&#39;t answer; it&#39;s asked again later)</span>'
                           if st.get("host") == "github.com" else
                           '<span class="pill">Privacy not checked (only possible for GitHub)</span>'),
               None: '<span class="pill">Checking privacy…</span>'}[vis]
    push, pull = st["push"], st["pull"]
    if push and not push["ok"]:
        health = (f'<span class="pill bad">Last upload failed</span><span class="muted">{_ago(push["at"])}: '
                  f'{e(syncstatus._main_line(push["error"])[:200])}</span>')
    elif pull and not pull["ok"]:
        health = (f'<span class="pill bad">Last download failed</span><span class="muted">{_ago(pull["at"])}: '
                  f'{e(syncstatus._main_line(pull["error"])[:200])}</span>')
    elif push:
        health = f'<span class="pill ok">Synced</span><span class="muted">last upload {_ago(push["at"])}</span>'
    else:
        health = '<span class="pill">Not uploaded yet</span><span class="muted">the next sync uploads your log</span>'
    waiting = (f'<span class="muted">· {st["waiting"]} change{"s" * (st["waiting"] != 1)} waiting to upload</span>'
               if st["waiting"] else "")
    warn = ('<p class="state-bad" style="margin:10px 0 0"><b>This repo is public.</b> Your prompts, project names and '
            'commit messages can be read by anyone. Make it private in the repo&#39;s settings on GitHub '
            '(Settings → General → Danger Zone → Change visibility).</p>' if vis == "public" else "")
    return f"""<section class="block" id="data-repo"><h2>Private data repo</h2>
<div class="meta"><span class="pill ok">Connected</span><span>to <a href="{e(st["web_url"])}" target="_blank"
rel="noopener noreferrer"><b>{e(st["remote"])}</b> ↗</a></span>{privacy}</div>
<div class="meta" style="margin-top:8px">{health}{waiting}</div>{warn}
{_signin_html(st)}
<p class="muted" style="font-size:.85rem;margin:10px 0 0">Folder <code>{folder}</code> · branch <code>{e(st["branch"])}</code>
· last change {e(st["last_change"] or "none yet")}</p>{buttons}{_accounts_help(st, folder)}</section>"""


def _signin_html(st: dict) -> str:
    """How this computer signs in to the data repo: which account, which key, which SSH nickname."""
    site = syncstatus.SITES.get(st.get("host") or "", st.get("host") or "the host")
    bits = []
    if st.get("account"):
        bits.append(f'<span class="pill ok">Signs in as {e(st["account"])}</span>')
    if st.get("via") == "ssh":
        how = "SSH key " + (f'<code>{e(st["key"])}</code>' if st.get("key") else "(your default key)")
        if st.get("alias"):
            how += f' through the nickname <code>{e(st["alias"])}</code> from <code>~/.ssh/config</code>'
        bits.append(f'<span class="muted">{how}</span>')
    elif st.get("via") == "https":
        bits.append('<span class="muted">HTTPS, with the login saved by git' +
                    (f' for <b>{e(st["url_user"])}</b>' if st.get("url_user") else "") + "</span>")
    if not st.get("account") and st.get("via"):
        bits.append(f'<span class="muted">Click <b>Test connection</b> to see which {e(site)} account this is.</span>')
    return f'<div class="meta signin" style="margin-top:8px">{"".join(bits)}</div>' if bits else ""


def _accounts_help(st: dict, folder: str) -> str:
    """Several GitHub (or GitLab) accounts on one computer: how to make Worklog use the right one."""
    folder_alias = e(st.get("alias") or "github-worklog")
    slug = e(st.get("slug") or "YOU/claude-worklog-data")
    cfg_text = (f"Host {folder_alias}\n  HostName {e(st.get('host') or 'github.com')}\n  User git\n"
                f"  IdentityFile {e(st.get('key') or '~/.ssh/id_worklog')}\n  IdentitiesOnly yes")
    return f"""<details class="help-more"><summary>Several GitHub accounts on this computer?</summary>
<p>{e(syncstatus.SITES.get(st.get("host") or "", "The host"))} decides who you are by the <b>SSH key</b>, not by your
browser login. Worklog uploads with whatever key the repo address points to, so give the account that owns the data repo
its own key and a nickname:</p>
<ol><li>Make a key: <code>ssh-keygen -t ed25519 -f ~/.ssh/id_worklog</code>, and add <code>~/.ssh/id_worklog.pub</code> to
<b>that</b> account (Settings → SSH keys), or to the repo as a deploy key with write access.</li>
<li>Add this to <code>~/.ssh/config</code>:<div class="cmdrow"><pre class="cmd">{cfg_text}</pre>
<button type="button" class="quiet copy" data-copy="{cfg_text}">Copy</button></div></li>
<li>Point the data repo at the nickname:<div class="cmdrow"><pre class="cmd">git -C {folder} remote set-url origin git@{folder_alias}:{slug}.git</pre></div></li>
<li>Click <b>Test connection</b>: it shows which account signed in.</li></ol>
<p class="muted">Your other repos keep using your usual key. Only this folder uses the nickname. On a network
that blocks SSH (port 22), use <code>HostName ssh.github.com</code> and add <code>Port 443</code>.</p></details>"""


def settings_extras(app: App, cfg: Config) -> str:
    """Settings added in 0.5.0: ticket links (and, below, goals and the phone summary)."""
    from . import cost
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'
    return f"""<section class="block" id="links"><h2>Links and cost</h2>
<form method="post" action="/settings/links">{c}
<label for="tracker">Ticket links</label>
<input type="text" id="tracker" name="tracker" value="{e(cfg.tracker_url)}" placeholder="https://your-team.atlassian.net/browse/">
<p class="help" style="margin-top:6px">Ticket numbers such as <code>XOO2-295</code> on the cards link to this address plus the number.
Pull request numbers (<code>#505</code>) link to the project's repo by themselves.</p>
<p class="muted" style="font-size:.85rem">“At API prices” on the cards estimates what the tokens would cost at Anthropic&#39;s API
prices (as of {cost.PRICES_AS_OF}). On Pro and Max plans usage counts against your limits instead. Other prices can be set in the
<code>[cost]</code> section of the config file.</p>
<div class="row"><button>Save changes</button></div></form></section>"""


def page_settings(app: App, cfg: Config, forget_ref: str | None = None) -> str:
    c = f'<input type="hidden" name="csrf" value="{app.csrf}">'

    logins = detected_logins()
    by_dir = cfg.accounts_by_dir()
    rows = []
    for a in cfg.accounts:
        exists = a.config_dir.is_dir()
        n = _transcript_count(a) if exists else 0
        hooked = hook_installed(a)
        env = "" if a.config_dir == Path.home() / ".claude" else f"CLAUDE_CONFIG_DIR={home_relative(a.config_dir)} "
        login_cmd = f'<span class="cmd">{e(env)}claude\n# then type /login and sign in with this account</span>'
        state = (f'<span class="pill ok">{n} session file{"s" * (n != 1)}</span>' if exists and n
                 else '<span class="pill bad">Not logged in yet</span>' if not exists else '<span class="pill">No sessions yet</span>')
        hook_btn = ('<span class="pill ok">Hook installed</span>' if hooked else
                    f'<form method="post" action="/accounts/hook">{c}<input type="hidden" name="name" value="{e(a.name)}">'
                    f'<button>Install hook</button></form>')
        remove = (f'<form method="post" action="/accounts/remove" data-confirm="Remove the account {e(a.name)} from this '
                  f'device? Worklog stops tracking its folder here. Records already synced stay in your data repo." '
                  f'data-confirm-yes="Remove {e(a.name)}">{c}<input type="hidden" name="name" value="{e(a.name)}">'
                  f'<button class="danger">Remove</button></form>') if len(cfg.accounts) > 1 else ""
        mine = sorted({mask_email(x["email"]) for x in logins for d in x["dirs"]
                       if d == a.config_dir and cfg.resolve_account(d, x["account_id"], x["email"]) is a})
        shared = len(by_dir[a.config_dir]) > 1
        role = ("matched to one login" if a.matched else "folder default: sessions from logins without a name"
                if shared else "")
        who = "".join(f'<span class="pill">{e(m)}</span>' for m in mine)
        rows.append(f'<div class="acct"><h3>{e(a.name)}</h3>'
                    f'<div class="meta"><code>{e(home_relative(a.config_dir))}</code>{who}'
                    f'{f"<span class=muted>{e(role)}</span>" if role else ""}</div>'
                    f'<div class="row" style="margin-top:10px">{state}{hook_btn}{remove}</div>'
                    f'{"" if exists and n else "<details open><summary>How to log in</summary>" + login_cmd + "</details>"}</div>')

    detected = _detected_section(cfg, c)
    acct_opts = "".join(f'<option{" selected" if a.name == cfg.summarizer_account else ""}>{e(a.name)}</option>' for a in cfg.accounts)
    preset = cfg.summary_model if any(cfg.summary_model == v for v, _ in MODEL_CHOICES) else "custom"
    model_opts = "".join(f'<option value="{v}"{" selected" if v == preset else ""}>{t}</option>' for v, t in MODEL_CHOICES)
    model_opts += f'<option value="custom"{" selected" if preset == "custom" else ""}>Another model (type its id)</option>'
    summ = cfg.account(cfg.summarizer_account)
    shared_note = (f'<p class="muted" style="font-size:.85rem;margin:6px 0 0">{e(summ.name)} shares '
                   f'<code>{e(home_relative(summ.config_dir))}</code> with another account, so summaries run as '
                   f'whichever account is logged in there at the time.</p>'
                   if summ and len(by_dir.get(summ.config_dir, [])) > 1 else "")
    found = shutil.which(cfg.claude_bin)
    claude_state = (f'<span class="state-ok">Found at {e(home_relative(found))}</span>' if found
                    else '<span class="state-bad">Not found. Summaries will fall back to your prompts.</span>')
    svc = services.status()
    svc_rows = "".join(f"<tr><td><code>{e(u)}</code></td><td>{e(s)}</td></tr>" for u, s in svc.items())
    report_on = services.report_timer_enabled()
    running = app.watcher is not None and app.watcher.running
    watch_state = ('<span class="state-ok">Watching now.</span>' if running else
                   '<span class="muted">Not watching in this window (it runs in the background service).</span>')
    confirm = _forget_confirm(app, cfg, forget_ref) if forget_ref else ""
    return confirm + f"""<h1><span class="grad">Settings</span><small>Device {e(cfg.device)}</small></h1>
<section class="block" style="margin-top:24px"><h2>Claude accounts</h2>
<p class="help">Worklog counts every Claude Code session under one of these accounts. Pick the setup that matches how you use Claude Code:</p>
<div class="setups"><div><b>Each account in its own folder</b>You start one account with plain <code>claude</code> and another with
<code>CLAUDE_CONFIG_DIR=~/.claude-work claude</code>. Add each folder as an account below and install its hook.</div>
<div><b>One folder, switching with /login</b>You use plain <code>claude</code> and switch with <code>/logout</code> and
<code>/login</code>. Keep one account here, install its hook, start a session with each login, then name each login under
<i>Logins seen on this device</i>.</div></div>
{"".join(rows)}
{detected}
<form id="add" method="post" action="/accounts/add" style="border-top:1px solid var(--rule);padding-top:16px;margin-top:18px">{c}
<h3>Add an account in its own folder</h3><label for="name">Name</label><input type="text" id="name" name="name" placeholder="work" required pattern="{NAME_PATTERN}">
<label for="dir">Claude config folder (leave empty for <code>~/.claude-&lt;name&gt;</code>)</label><input type="text" id="dir" name="dir" placeholder="~/.claude-work">
<div class="row"><button>Add account</button></div></form></section>

{"" if services.demo_mode() else checklist_html(app, checks.cached(cfg, running), "Setup checklist")}

{data_repo_section(app, cfg)}

<section class="block"><h2>Account switching and extras</h2>
<p class="help">While Worklog runs, it notices when you switch Claude accounts (with <code>/login</code> or any one-click
account tool) and when a new Claude folder appears, so sessions are counted under the right account even without a hook.
{watch_state}</p>
<form method="post" action="/settings/watch">{c}
<div class="check"><input type="checkbox" id="auto_setup" name="auto_setup" value="1"{" checked" if cfg.auto_setup else ""}><label for="auto_setup">Set up new accounts automatically: add new Claude folders and reinstall missing hooks without asking</label></div>
<div class="check"><input type="checkbox" id="fun" name="fun" value="1"{" checked" if cfg.fun else ""}><label for="fun">Fun mode: confetti on records, streak flame, sleepy robot and a hidden party mode</label></div>
<div class="check"><input type="checkbox" id="check_updates" name="check_updates" value="1"{" checked" if cfg.check_updates else ""}><label for="check_updates">Tell me when a new version is out (asks pypi.org once a day; nothing about you is sent)</label></div>
<div class="row"><button>Save changes</button></div></form></section>

<section class="block"><h2>Summaries and daily report</h2>
<form method="post" action="/settings/report">{c}
<div class="check"><input type="checkbox" id="use_ai" name="use_ai" value="1"{" checked" if cfg.use_ai else ""}><label for="use_ai">Write summaries with Claude (uses a small amount of tokens)</label></div>
<label for="model">Model for summaries</label><select id="model" name="model">{model_opts}</select>
<input type="text" id="model_custom" name="model_custom" aria-label="Model id" placeholder="claude-opus-5-5" value="{e(cfg.summary_model if preset == "custom" else "")}" style="margin-top:8px">
<p class="muted" style="font-size:.85rem;margin:6px 0 0">“Account default” is whatever <code>claude</code> uses on that account (on Pro that is often Sonnet). Pick Opus for the most accurate summaries. “Another model” uses the id typed in the box.</p>
<label for="summ">Account that writes the summaries</label><select id="summ" name="summarizer">{acct_opts}</select>{shared_note}
<label for="bin">Claude command</label><input type="text" id="bin" name="claude_bin" value="{e(cfg.claude_bin)}"><div class="muted" style="font-size:.85rem;margin-top:4px">{claude_state}</div>
<div class="row"><button>Save changes</button></div></form>
<form method="post" action="/settings/report-timer" style="border-top:1px solid var(--rule);padding-top:14px;margin-top:16px">{c}
<p class="help">Build the daily report automatically at 23:30 on this device. Turn this on for one device only.</p>
<input type="hidden" name="enabled" value="{"0" if report_on else "1"}">
<button class="quiet"{"" if services.available() else " disabled"}>{"Turn off daily report here" if report_on else "Turn on daily report here"}</button></form></section>
{settings_extras(app, cfg)}
<section class="block"><h2>This device</h2>
<form method="post" action="/settings/device">{c}
<label for="device">Device name</label><input type="text" id="device" name="device" value="{e(cfg.device)}" required pattern="{NAME_PATTERN}">
<p class="muted" style="font-size:.85rem">Renaming files new sessions under the new name. Earlier days keep the old one.</p>
<label for="store">Data repo folder</label><input type="text" id="store" name="store" value="{e(home_relative(cfg.store_repo))}" required>
<p class="muted" style="font-size:.85rem">Connection and sync status: see <a href="#data-repo">Private data repo</a>.</p>
<div class="row"><button>Save changes</button></div></form></section>

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
        self.watcher: watcher.Watcher | None = None  # started by serve(); None in tests and the demo
        self.close = lambda: None  # set by serve(): closes the listening sockets

    # ---- POST handlers: return (redirect_location | None, error | None, form)
    def post(self, path: str, form: dict):
        cfg = _try_config()
        if path == "/setup":
            return self._setup(form)
        if cfg is None:
            return "/setup", None, form
        if path == "/action/collect":
            today = date.today()

            def sync():
                msg = f"Synced: {collect(cfg, [today - timedelta(days=1), today])} new or updated session record(s)."
                return " ".join([msg] + [r.message() for r in chats.scan_downloads(cfg)])
            started = self.jobs.start("Syncing", sync)
            return ("/?ok=job" if started else "/"), None, form
        if path == "/sources/scan":
            def scan():
                results = chats.scan_downloads(cfg)
                return " ".join(r.message() for r in results) or "No new exports found in your Downloads folder."
            started = self.jobs.start("Checking Downloads", scan)
            return ("/sources?ok=job" if started else "/sources"), None, form
        if path == "/sources/settings":
            account = form.get("account", "")
            if cfg.account(account) is None:
                return None, "Pick one of your accounts for imported chats.", form
            raw = form.get("downloads", "").strip()
            folder = _expand(raw) if raw else None
            if folder is not None and not under_home(folder, allow_home=True):
                return None, "The Downloads folder must be inside your home folder.", form
            cfg.chat_account = None if account == cfg.accounts[0].name else account
            cfg.watch_downloads, cfg.downloads_dir = form.get("watch") == "1", folder
            was_kept, cfg.keep_chat_summaries = cfg.keep_chat_summaries, form.get("keep_summaries") == "1"
            loc, err, kept = self._save(cfg, "chat_saved", "/sources")
            if err is None and was_kept and not cfg.keep_chat_summaries:
                self.jobs.start("Removing chat summaries",
                                lambda: f"Removed claude.ai summaries from {chats.strip_summaries(cfg)} chat record(s).")
            return loc, err, kept
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
        if path == "/jobs/dismiss":
            self.jobs.dismiss()
            return "/", None, form
        if path == "/restart":
            return self._restart_after_update(form)
        if path == "/sync/test":
            def job():
                ok, msg = syncstatus.test_connection(cfg)
                if not ok:
                    raise JobFailed(msg)
                return msg
            started = self.jobs.start("Testing the connection", job)
            return ("/settings?ok=job#data-repo" if started else "/settings#data-repo"), None, form
        if path == "/accounts/forget":
            return self._forget(cfg, form)
        if path == "/projects/edit":
            return self._project_edit(cfg, form)
        if path == "/projects/guess":
            return self._project_guess(cfg, form)
        if path == "/timesheet/summaries":
            return self._timesheet_summaries(cfg, form)
        if path == "/accounts/add-folder":
            return self._account_add_folder(cfg, form)
        if path == "/notices/dismiss":
            key = form.get("key", "")
            if not key or len(key) > 300:
                return None, "Unknown notice.", form
            if key.startswith("update:"):
                updates.dismiss(key.split(":", 1)[1])
            else:
                watcher.dismiss(key)
            return "/", None, form
        if path == "/update":
            return self._update(form)
        if path == "/settings/watch":
            cfg.auto_setup, cfg.fun = form.get("auto_setup") == "1", form.get("fun") == "1"
            cfg.check_updates = form.get("check_updates") == "1"
            return self._save(cfg, "saved")
        if path == "/accounts/remove":
            name = form.get("name", "")
            keep = [a for a in cfg.accounts if a.name != name]
            if not keep or len(keep) == len(cfg.accounts):
                return None, "That account can't be removed.", form
            cfg.accounts = keep
            if cfg.summarizer_account == name:
                cfg.summarizer_account = keep[0].name
            if cfg.chat_account == name:
                cfg.chat_account = None
            return self._save(cfg, "account_removed")
        if path == "/accounts/name":
            return self._account_name(cfg, form)
        if path == "/accounts/hook":
            acct = cfg.account(form.get("name", ""))
            if acct is None:
                return None, "Unknown account.", form
            ok, msg = install_hook(acct)
            checks.forget_cache()
            return ("/settings?ok=saved" if ok else None), (None if ok else msg), form
        if path == "/settings/report":
            summ = form.get("summarizer", "")
            binv = form.get("claude_bin", "").strip()
            if cfg.account(summ) is None:
                return None, "Pick one of your accounts to write summaries.", form
            if not binv or len(binv) > 300 or any(ch in binv for ch in "\n\r\0"):
                return None, "Enter the claude command or its full path.", form
            model = form.get("model", "").strip()
            if model == "custom":
                model = form.get("model_custom", "").strip()
            if model and not MODEL_RE.match(model):
                return None, "Model: use an alias such as opus, or a model id such as claude-opus-5-5.", form
            cfg.use_ai, cfg.summarizer_account, cfg.claude_bin = form.get("use_ai") == "1", summ, binv
            cfg.summary_model = model
            return self._save(cfg, "saved")
        if path == "/settings/report-timer":
            try:
                services.set_report_timer(form.get("enabled") == "1")
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                return None, str(exc), form
            return "/settings?ok=saved", None, form
        if path == "/settings/links":
            value = form.get("tracker", "").strip()
            if value and not re.match(r"^https?://[^\s\"'<>]{3,300}$", value):
                return None, "Ticket links: enter a web address starting with https://", form
            cfg.tracker_url = value
            return self._save(cfg, "saved")
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

    def _save(self, cfg: Config, ok: str, page: str = "/settings"):
        checks.forget_cache()
        try:
            save_config(cfg, cfg.path)
        except (ConfigError, OSError) as exc:
            return None, f"Couldn't save settings: {exc}", {}
        return f"{page}?ok={ok}", None, {}

    def upload(self, name: str, read, length: int) -> tuple[int, dict]:
        """Store an uploaded export in a private temp folder and import it in the background."""
        cfg = _try_config()
        if cfg is None:
            return 409, {"error": "Set up worklog first."}
        if self.jobs.snapshot().get("running"):
            return 409, {"error": "Another job is running. Try again when it finishes."}
        base = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:100] or "conversations"
        tmpdir = chats.STATE_DIR / "uploads" / secrets.token_hex(8)
        tmpdir.mkdir(parents=True, mode=0o700)
        head = read(min(length, 4))
        suffix = ".zip" if head.startswith(b"PK") else ".json"
        target = tmpdir / (base.rsplit(".", 1)[0] + suffix)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(head)
                left = length - len(head)
                while left > 0:
                    chunk = read(min(left, 1 << 20))
                    if not chunk:
                        raise OSError("upload ended early")
                    fh.write(chunk)
                    left -= len(chunk)
        except OSError as exc:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return 400, {"error": f"Upload failed: {exc}"}

        def job():
            try:
                return chats.import_chats(cfg, target).message()
            except chats.ExportError as exc:
                raise RuntimeError(str(exc)) from None
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        if not self.jobs.start("Importing chats", job):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return 409, {"error": "Another job is running. Try again when it finishes."}
        return 202, {"ok": True}

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
                          "start a session with it, then name it under Logins seen on this device."), form
        cfg.accounts.append(Account(name, cdir))
        return self._save(cfg, "account_added")

    def _update(self, form: dict):
        """Upgrade in the background, then restart this server on the new code (the page reloads itself)."""
        if services.demo_mode() or updates.upgrade_command() is None:
            return None, f"Update by hand: {updates.manual_command()}", form

        def job():
            ok, msg = updates.upgrade()
            if not ok:
                raise RuntimeError(msg)
            new = updates.installed_version()
            if new == __version__:
                latest = updates.available()
                if latest:  # PyPI said there is a newer one, but pip couldn't get it yet
                    raise RuntimeError(f"PyPI is still publishing Worklog {latest}, so pip found nothing newer "
                                       f"than {__version__}. Try again in a few minutes.")
                return f"Worklog {__version__} is already the newest version."
            updates.note_updated(new)  # the new version says "Worklog is now …" once
            threading.Timer(1.5, self.restart).start()  # let this message reach the page first
            return f"Updated to {new}. Restarting…"
        started = self.jobs.start("Updating Worklog", job)
        return ("/?ok=job" if started else "/"), None, form

    def _restart_after_update(self, form: dict):
        """Worklog was upgraded from a terminal: restart this server on the new code (the page reloads itself)."""
        disk = updates.version_on_disk()
        if services.demo_mode() or not disk or disk == __version__:
            return "/", None, form

        def job():
            threading.Timer(1.5, self.restart).start()
            return f"Updated to {disk}. Restarting…"
        self.jobs.start("Restarting", job)
        return "/?ok=job", None, form

    def restart(self) -> None:
        updates.restart_server(self.port, self.close)

    def _forget(self, cfg: Config, form: dict):
        """Delete a login's history after the user ticked "I understand" on the confirmation card."""
        ref_ = form.get("ref", "")
        if form.get("confirm") != "1":
            return None, "Tick “I understand” to confirm.", form
        p = forget.plan(cfg, ref_)
        if p is None:
            return None, "That login is no longer recorded on this device.", form

        def job():
            msg = forget.apply(cfg, p)
            checks.forget_cache()
            return msg
        started = self.jobs.start("Forgetting a login", job)
        return ("/settings?ok=job#logins" if started else "/settings"), None, form

    def _day_projects(self, cfg: Config, form: dict):
        """(store, day, overrides, projects) for a correction posted from a day page, or an error message."""
        day = _parse_date(form.get("d"))
        store = _store(cfg)
        if day is None or store is None:
            return None, "That day isn't available."
        ov = overrides.load(store.path)
        _, projects = aggregate(list(store.iter_json(f"records/{day.isoformat()}")), ov, cfg.time_mode)
        p = projects.get(form.get("key", ""))
        if p is None:
            return None, "That project isn't on this day any more. Reload the page."
        return (store, day, ov, projects, p), None

    def _save_corrections(self, cfg: Config, store: GitStore, edits: dict, what: str) -> None:
        with store.lock():
            store.pull()
            overrides.change(store, cfg.device, edits)
            store.commit_and_push(f"{what} on {cfg.device}")

    def _project_edit(self, cfg: Config, form: dict):
        """Rename a project, move some or all of a card's sessions, add a folder rule, set the tag or timesheet line."""
        got, err = self._day_projects(cfg, form)
        if err:
            return None, err, form
        store, day, ov, projects, p = got
        here = f"{day.isoformat()}|{p.key}"
        ids = [x["id"] for x in p.sessions_list]
        if form.get("reset") == "1":
            edits = {"names": {p.key: None}, "lines": {here: None},
                     "moves": {i: None for i in ids if i in ov.moves}, "tags": {i: None for i in ids if i in ov.tags}}
            self._save_corrections(cfg, store, edits, "undo corrections")
            return f"/?d={day.isoformat()}&ok=edited", None, form
        edits: dict[str, dict] = {s: {} for s in overrides.SECTIONS}
        name = " ".join(form.get("name", "").split())[:80]
        if not name:
            return None, "Give the project a name.", form
        if name != p.name:
            default = project_title({"kind": p.kind, "name": p.name, "remote": p.remote})
            edits["names"][p.key] = None if name == default and p.key not in ov.projects else name
        target = form.get("target", "")
        if target == "__new__":
            new_name = " ".join(form.get("new_name", "").split())[:80]
            if not new_name:
                return None, "Name the new project.", form
            slug = re.sub(r"[^a-z0-9]+", "-", new_name.lower()).strip("-")[:40] or "project"
            target = f"custom:{slug}"
            edits["projects"][target] = {"kind": "custom", "name": new_name, "remote": None}
            edits["names"][target] = new_name
        elif target:
            allowed = set(projects) | set(ov.projects)
            for d in range(1, 31):  # "Recent projects" in the form: anything seen in the last 30 days
                for r in store.iter_json(f"records/{(day - timedelta(days=d)).isoformat()}"):
                    allowed.add(project_key(r.get("project") or {}))
            if target not in allowed:
                return None, "Pick a project from the list.", form
        if target and target != p.key:
            chosen = [i for i in ids if form.get(f"s_{i}") == "1"] if len(ids) > 1 else ids
            if not chosen:
                return None, "Tick at least one session to move.", form
            edits["moves"].update({i: target for i in chosen})
            now = time.time()
            edits["stale"].update({here: now, f"{day.isoformat()}|{target}": now})
            folder = form.get("rule_folder", "")
            if form.get("rule") == "1" and folder in p.started_in:
                edits["rules"][folder] = target
        tag = form.get("tag", "auto")
        if tag == "auto":
            edits["tags"].update({i: None for i in ids if i in ov.tags})
        elif tag in TAGS:
            edits["tags"].update({i: tag for i in ids if ov.tags.get(i) != tag})
        line = " ".join(form.get("line", "").split())[:160]
        if line != (ov.lines.get(here) or ""):
            picked, fit, _ = pick_summary(summary_metas(store, day), p.key, ids, legacy_fit(p, projects))
            auto_line = summary_line(picked if fit != "other" else {}, p)
            edits["lines"][here] = None if not line or line == auto_line else line
        edits = {k: v for k, v in edits.items() if v}
        if edits:
            self._save_corrections(cfg, store, edits, "corrections")
        return f"/?d={day.isoformat()}&ok=edited", None, form

    def _project_guess(self, cfg: Config, form: dict):
        """Answer "Probably xoo?" on a card."""
        got, err = self._day_projects(cfg, form)
        if err:
            return None, err, form
        store, day, _, _, p = got
        g = p.guess or {}
        ids = [i for i in g.get("sessions") or [] if isinstance(i, str)]
        if not ids:
            return f"/?d={day.isoformat()}", None, form
        if form.get("answer") == "yes":
            proj = {"kind": "git", "name": g.get("name"), "remote": g.get("remote")}
            target = project_key(proj)
            now = time.time()
            edits = {"moves": {i: target for i in ids},
                     "stale": {f"{day.isoformat()}|{p.key}": now, f"{day.isoformat()}|{target}": now}}
            if not g.get("remote"):
                edits["projects"] = {target: proj}
            self._save_corrections(cfg, store, edits, "move guessed sessions")
            return f"/?d={day.isoformat()}&ok=guess_yes", None, form
        self._save_corrections(cfg, store, {"noguess": {i: True for i in ids}}, "keep guessed sessions")
        return f"/?d={day.isoformat()}&ok=guess_no", None, form

    def _timesheet_summaries(self, cfg: Config, form: dict):
        """Write the missing summaries for the days shown on the timesheet."""
        from . import timesheet as ts
        start, end = _parse_date(form.get("from")), _parse_date(form.get("to"))
        store = _store(cfg)
        if start is None or end is None or store is None:
            return None, "Pick the days first.", form
        try:
            days = ts.missing_days(ts.build(store, start, end, time_mode=cfg.time_mode))
        except ValueError as exc:
            return None, str(exc), form
        if not days:
            return "/timesheet", None, form
        if not cfg.use_ai:
            return None, "AI summaries are switched off in Settings → Summaries.", form

        def job():
            for d in days:
                build_report(cfg, d, use_ai=True, write=True)
            return f"Summaries written for {len(days)} day{'s' * (len(days) != 1)}."
        started = self.jobs.start("Writing summaries", job)
        return ("/timesheet?ok=job" if started else "/timesheet"), None, form

    def _account_add_folder(self, cfg: Config, form: dict):
        """Add a folder the watcher found: pick a free name from the folder, then install its hook."""
        folder = _expand(form.get("folder", ""))
        if not under_home(folder) or not watcher.is_claude_folder(folder):
            return None, "That isn't a Claude folder inside your home folder.", form
        if any(a.config_dir == folder for a in cfg.accounts):
            return "/settings?ok=saved", None, form
        name = watcher.auto_name(folder, {a.name for a in cfg.accounts})
        cfg.accounts.append(Account(name, folder))
        loc, err, kept = self._save(cfg, "account_added")
        if err is None:
            ok, msg = install_hook(Account(name, folder))
            if not ok:
                return None, f"Added {name}, but the hook could not be installed: {msg}", form
        return loc, err, kept

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
        loc, err, kept = self._save(cfg, "named")
        if err is None:
            self._refile(cfg)
        return loc, err, kept

    def _refile(self, cfg: Config) -> None:
        """Re-collect every day Claude Code still keeps, so older sessions move to the right account too."""
        today = date.today()
        days = [today - timedelta(days=n) for n in range(REFILE_DAYS - 1, -1, -1)]

        def job():
            n = collect(cfg, days)
            return (f"Updated {n} session record(s) from the last {REFILE_DAYS} days under the right account. "
                    "Days whose Claude Code logs are already deleted keep their old account.")
        self.jobs.start("Moving sessions to the right account", job)

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
                r = subprocess.run(["git", "clone", "--", clone, str(store)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
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


JOB_PATHS = {"/action/collect", "/action/report", "/sources/scan", "/sync/test", "/accounts/forget", "/update",
             "/restart", "/timesheet/summaries"}


def _fetch_answer(app: App, path: str, location: str | None, error: str | None) -> dict:
    """What a form sent by the page's script gets back: an error to show, a started job to follow, or a message."""
    if error:
        return {"ok": False, "error": error}
    ok = (parse_qs(urlsplit(location or "").query).get("ok") or [""])[0]
    running = app.jobs.snapshot().get("running")
    if ok == "job":  # it may already be done: the script then reads the result straight away
        return {"ok": True, "job": running or "Working"}
    if path in JOB_PATHS and ok != "job" and running:
        return {"ok": False, "error": f"Please wait: “{running}” is still running."}
    return {"ok": True, "message": OK_MESSAGES.get(ok, "")}


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

        def _export(self, cfg, q: dict):
            from . import timesheet as ts
            t = timesheet_query(cfg, q)
            store = _store(cfg)
            fmt = q.get("fmt") if q.get("fmt") in ("csv", "xlsx") else "csv"
            if store is None:
                return self._send(404, "No data repo", "text/plain")
            try:
                rows = ts.build(store, t["from"], t["to"], time_mode=cfg.time_mode, account=t["account"] or None)
            except ValueError as exc:
                return self._send(400, str(exc), "text/plain")
            data = (ts.to_xlsx if fmt == "xlsx" else ts.to_csv)(rows, t["round"], t["details"])
            name = f"worklog-timesheet-{t['from'].isoformat()}-to-{t['to'].isoformat()}.{fmt}"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8" if fmt == "csv" else
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location: str):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _page(self, title, body, active, ok=None, error=None, status=200):
            nonce = secrets.token_urlsafe(16)
            cfg = _try_config() if title != "Set up" else None
            extra = top_panels(app, cfg, active) if cfg else ""
            page = layout(title, body, active, nonce, app.jobs.snapshot(), ok, error, extra, fun=bool(cfg and cfg.fun),
                          csrf=app.csrf)
            self._send(status, page, nonce=nonce)

        # ---------------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "Forbidden host", "text/plain")
            try:
                url = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/api/status":
                    return self._send(200, json.dumps({**app.jobs.snapshot(), "version": __version__}),
                                      "application/json")
                cfg = _try_config()
                if url.path == "/setup" or (cfg is None and url.path in ("/", "/settings")):
                    if cfg is not None:
                        return self._redirect("/settings")
                    return self._page("Set up", page_setup(app), "settings")
                if url.path == "/":
                    day = _parse_date(q.get("d")) or date.today()
                    body, err = page_day(app, cfg, day, q.get("edit"))
                    return self._page(f"{day:%d %b %Y}", body, "day", q.get("ok"), err)
                if url.path == "/settings":
                    forget_ref = q.get("forget") if re.fullmatch(r"[0-9a-f]{12}", q.get("forget", "")) else None
                    return self._page("Settings", page_settings(app, cfg, forget_ref), "settings", q.get("ok"))
                if url.path == "/timesheet":
                    return self._page("Timesheet", page_timesheet(app, cfg, q), "timesheet", q.get("ok"))
                if url.path == "/timesheet/export":
                    return self._export(cfg, q)
                if url.path == "/sources":
                    return self._page("Sources", page_sources(app, cfg), "sources", q.get("ok"))
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
            if urlsplit(self.path).path == "/sources/upload":
                return self._upload(length)
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
            if self.headers.get("X-Worklog") == "fetch":  # the page's script: answer in place, no reload
                return self._send(200, json.dumps(_fetch_answer(app, path, location, error)), "application/json")
            if location:
                return self._redirect(location)
            cfg = _try_config()
            if cfg is None or path == "/setup":
                return self._page("Set up", page_setup(app, kept), "settings", error=error, status=400)
            if path.startswith("/sources"):
                return self._page("Sources", page_sources(app, cfg), "sources", error=error, status=400)
            return self._page("Settings", page_settings(app, cfg), "settings", error=error, status=400)

        def _upload(self, length: int):
            def reply(status: int, data: dict):
                self.close_connection = status >= 400  # an unread body must not be parsed as the next request
                return self._send(status, json.dumps(data), "application/json")
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), app.csrf):
                return reply(403, {"error": "Page expired. Reload it and try again."})
            if not 0 < length <= MAX_UPLOAD:
                return reply(413, {"error": f"Upload a file of at most {MAX_UPLOAD // (1024 * 1024)} MB."})
            name = unquote(self.headers.get("X-File-Name", "conversations"))
            try:
                status, data = app.upload(name, self.rfile.read, length)
            except Exception as exc:  # noqa: BLE001
                log.exception("upload failed")
                status, data = 500, {"error": f"Something went wrong: {exc}"}
            return reply(status, data)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def _update_loop() -> None:
    """Ask PyPI about new versions; `updates.check` itself makes sure that's at most once a day."""
    time.sleep(20)  # not during startup
    while True:
        cfg = _try_config()
        if cfg and cfg.check_updates:
            try:
                updates.check()
            except Exception:  # noqa: BLE001 - never let an update check hurt the UI
                log.exception("update check failed")
        time.sleep(3600)


def serve(port: int = DEFAULT_PORT) -> None:
    app = App(port)
    if not services.demo_mode():
        app.watcher = watcher.Watcher()
        app.watcher.start()
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
    app.close = lambda: [s.server_close() for s in servers]
    if not services.demo_mode():
        threading.Thread(target=_update_loop, name="worklog-updates", daemon=True).start()
    print(f"Worklog UI: http://{HOST_NAME}:{port}  (also http://localhost:{port})", flush=True)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.server_close()
