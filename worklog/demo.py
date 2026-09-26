"""`claude-worklog demo`: the web UI on a throwaway HOME filled with generated fake data.

Nothing here reads or writes the real home folder. The generator only writes below the
fake home it is given, and the UI runs as a subprocess whose HOME and XDG_* variables
point there, so every path the package computes at import time (config, state, data repo,
Claude folders, ~/.gitconfig) lands inside the temporary folder. systemd calls are
disabled through services.DEMO_ENV, and the temporary folder is deleted on exit.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from .report import project_key
from .services import DEMO_ENV

DEVICES = ("laptop", "desktop")  # the demo runs as "laptop"; "desktop" plays another synced machine
MODELS = ("claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5")

# name, host, slug (None = outside any git repo), account, weight, prompts, files, commits, summary bullets
PROJECTS = [
    {"name": "storefront", "host": "github.com", "slug": "acme-shop/storefront", "account": "work", "weight": 5,
     "prompts": ["add a coupon field to the checkout form",
                 "why does the cart badge not update after removing an item?",
                 "write tests for the price formatter", "make the product grid responsive on small screens",
                 "refactor the cart store to use a reducer"],
     "files": ["src/checkout/CouponField.tsx", "src/cart/store.ts", "src/lib/price.ts", "src/lib/price.test.ts",
               "src/products/Grid.tsx"],
     "commits": ["Add coupon field to checkout", "Fix stale cart badge count", "Test price formatting edge cases",
                 "Responsive product grid"],
     "bullets": ["Added a coupon field to checkout with inline validation.",
                 "Fixed the cart badge not refreshing after an item was removed.",
                 "Covered the price formatter with tests for rounding and currencies."]},
    {"name": "payments-api", "host": "gitlab.com", "slug": "acme-shop/payments-api", "account": "work", "weight": 4,
     "prompts": ["add idempotency keys to the refund endpoint", "explain this retry loop in the webhook worker",
                 "the settlement job times out on large batches, find out why", "add a migration for refund reasons"],
     "files": ["app/refunds/views.py", "app/webhooks/worker.py", "app/settlement/jobs.py",
               "migrations/0042_refund_reason.py", "tests/test_refunds.py"],
     "commits": ["Idempotent refunds", "Batch settlement queries", "Add refund reason column"],
     "bullets": ["Made the refund endpoint idempotent using request keys.",
                 "Traced settlement timeouts to per-row queries and batched them.",
                 "Added a migration and tests for refund reasons."]},
    {"name": "infra", "host": "github.com", "slug": "acme-shop/infra", "account": "work", "weight": 2,
     "prompts": ["bump the staging node pool and add an autoscaling rule",
                 "write a runbook for rotating the DB password"],
     "files": ["terraform/staging/nodes.tf", "docs/runbooks/rotate-db-password.md"],
     "commits": ["Autoscale staging node pool", "Runbook: DB password rotation"],
     "bullets": ["Added autoscaling to the staging node pool.", "Wrote a runbook for rotating the database password."]},
    {"name": "trail-map", "host": "github.com", "slug": "demo-user/trail-map", "account": "personal", "weight": 3,
     "prompts": ["render GPX tracks on the map with elevation colors", "cache map tiles offline",
                 "add a dark theme toggle"],
     "files": ["src/map/tracks.js", "src/map/tiles.js", "src/ui/theme.css"],
     "commits": ["Color GPX tracks by elevation", "Offline tile cache", "Dark theme"],
     "bullets": ["Rendered GPX tracks colored by elevation.", "Added an offline cache for map tiles."]},
    {"name": "R&D / misc", "host": None, "slug": None, "account": "personal", "weight": 2, "folder": "scratch",
     "prompts": ["compare three ways to debounce a search box", "summarize the tradeoffs of SQLite WAL mode",
                 "write a quick script to rename photos by date"],
     "files": ["~/scratch/debounce.js", "~/scratch/rename_photos.py"],
     "commits": [],
     "bullets": ["Compared debounce approaches for a search box.", "Read up on SQLite WAL mode tradeoffs."]},
]


def demo_env(home: Path) -> dict[str, str]:
    """Environment for processes that must only ever see the fake home."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "CLAUDE_", "GIT_"))}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_STATE_HOME=str(home / ".local/state"),
               XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"),
               GIT_CONFIG_NOSYSTEM="1", PYTHONUNBUFFERED="1")
    env[DEMO_ENV] = "1"
    return env


def _config(home: Path, store: Path) -> str:
    # Absolute paths: the config is also loaded by tests that run with the real HOME.
    return f"""# claude-worklog DEMO configuration (generated, temporary)
device_name = "{DEVICES[0]}"
store_repo = {json.dumps(str(store))}

[[accounts]]
name = "personal"
config_dir = {json.dumps(str(home / ".claude"))}

[[accounts]]
name = "work"
config_dir = {json.dumps(str(home / ".claude-work"))}

[collect]
misc_label = "R&D / misc"

[report]
use_ai = false
summarizer_account = "personal"
"""


def _record(rng: random.Random, proj: dict, day: date, device: str, n: int, now: datetime) -> dict | None:
    latest = now.hour - 1 if day == now.date() else 19
    if latest < 8:
        return None  # "today" before working hours: no session has happened yet
    start = datetime(day.year, day.month, day.day, rng.randint(8, latest), rng.randint(0, 59)).astimezone()
    minutes = rng.randint(12, 150)
    end = start + timedelta(minutes=minutes)
    model = rng.choices(MODELS, weights=(6, 2, 2))[0]
    scale = {"claude-opus-5-5": 1.4, "claude-haiku-4-5": 0.6}.get(model, 1.0)
    tokens = {"input": int(rng.randint(2_000, 30_000) * scale), "output": int(rng.randint(8_000, 90_000) * scale),
              "cache_creation": int(rng.randint(40_000, 300_000) * scale),
              "cache_read": int(rng.randint(400_000, 4_000_000) * scale)}
    prompts = rng.sample(proj["prompts"], k=min(len(proj["prompts"]), rng.randint(1, 3)))
    files = sorted(rng.sample(proj["files"], k=min(len(proj["files"]), rng.randint(1, 3))))
    commits = [{"hash": f"{rng.getrandbits(28):07x}", "subject": s}
               for s in rng.sample(proj["commits"], k=min(len(proj["commits"]), rng.randint(0, 2)))]
    branches = ["main"] if not proj["slug"] else sorted({"main", rng.choice(["main", "feature/demo", "fix/demo"])})
    if proj["slug"]:
        project = {"kind": "git", "name": proj["name"], "path": f"~/code/{proj['name']}", "branches": branches,
                   "remote": {"host": proj["host"], "slug": proj["slug"]}}
    else:
        project = {"kind": "misc", "name": proj["name"], "path": f"~/{proj['folder']}", "folder": proj["folder"],
                   "branches": [], "remote": None}
    return {
        "schema": 1, "session_id": f"demo-{day:%Y%m%d}-{device}-{n:02d}", "date": day.isoformat(),
        "account": proj["account"], "device": device, "project": project,
        "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
        "span_minutes": minutes, "api_calls": rng.randint(8, 120), "tokens": tokens,
        "tokens_by_model": {model: tokens}, "prompt_count": len(prompts), "prompts": prompts,
        "files_touched": files, "tool_calls": {"Read": rng.randint(3, 40), "Edit": rng.randint(1, 20),
                                               "Bash": rng.randint(0, 15)},
        "commits": commits if proj["slug"] else [],
    }


def generate(home: Path, days: int = 14, seed: int = 7, now: datetime | None = None) -> Path:
    """Write a fake HOME: config, empty Claude folders and a data repo with `days` of records.

    Returns the config file path. Only ever writes below `home`.
    """
    now = now or datetime.now()
    rng = random.Random(seed)
    home.mkdir(parents=True, exist_ok=True)
    for sub in (".claude/projects", ".claude-work/projects"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    store = home / "claude-worklog-data"
    store.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(store)], check=True, env=demo_env(home), timeout=30)
    cfg_path = home / ".config" / "claude-worklog" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(_config(home, store), encoding="utf-8")
    cfg_path.chmod(0o600)

    weights = [p["weight"] for p in PROJECTS]
    for back in range(days):
        day = now.date() - timedelta(days=back)
        weekend = day.weekday() >= 5
        day_projects: dict[str, dict] = {}
        for device in DEVICES:
            count = rng.randint(0, 2) if weekend else rng.randint(1, 4)
            for n in range(count):
                proj = PROJECTS[-2 + rng.randint(0, 1)] if weekend else rng.choices(PROJECTS, weights=weights)[0]
                rec = _record(rng, proj, day, device, n, now)
                if rec is None:
                    continue
                rel = store / "records" / day.isoformat() / device / rec["account"] / f"{rec['session_id']}.json"
                rel.parent.mkdir(parents=True, exist_ok=True)
                rel.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                day_projects[project_key(rec["project"])] = proj
        # Past days have finished summaries; today shows raw prompts, as it would before the nightly run.
        if back > 0 and day_projects:
            summaries = {key: {"bullets": [f"- {b}" for b in p["bullets"]], "source": "ai"}
                         for key, p in day_projects.items()}
            generated = datetime(day.year, day.month, day.day, 23, 30).astimezone()
            meta = {"date": day.isoformat(), "device": DEVICES[0], "summaries": summaries,
                    "generated_at": generated.isoformat(timespec="seconds")}
            out = store / "reports" / day.isoformat() / f"{DEVICES[0]}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cfg_path


def run(port: int, seed: int = 7) -> int:
    """Generate demo data in a temporary folder, serve the UI until Ctrl+C, then delete it."""
    base = Path(tempfile.mkdtemp(prefix="claude-worklog-demo-"))
    home = base / "home"
    try:
        generate(home, seed=seed)
        print(f"Demo data (fake, temporary): {base}")
        print(f"Open http://localhost:{port}  (Ctrl+C to stop; the folder is deleted afterwards)", flush=True)
        proc = subprocess.Popen([sys.executable, "-m", "worklog.cli", "serve", "--port", str(port)],
                                env=demo_env(home), cwd=home)
        old = signal.signal(signal.SIGTERM, _interrupt)  # `kill` cleans up like Ctrl+C does
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()  # no-op if Ctrl+C already stopped it (the terminal signals both)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return 0
        finally:
            signal.signal(signal.SIGTERM, old)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _interrupt(signum, frame):
    raise KeyboardInterrupt
