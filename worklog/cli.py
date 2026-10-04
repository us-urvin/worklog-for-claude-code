"""Command line interface: init, doctor, install-hook, hook, collect, import-chats, report, serve, demo."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import CONFIG_PATH, STATE_DIR, ConfigError, example_config, load_config
from .hooks import HOOK_EVENTS, install_hook, installed_events

log = logging.getLogger("worklog")


def _setup_logging(verbose: bool, quiet: bool = False) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(STATE_DIR / "worklog.log", maxBytes=1_000_000, backupCount=3)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if not quiet:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
        root.addHandler(sh)


def _parse_day(value: str) -> date:
    v = value.lower()
    if v == "today":
        return date.today()
    if v == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD, 'today' or 'yesterday'") from exc


# ------------------------------------------------------------------ commands
def cmd_init(args) -> int:
    if CONFIG_PATH.exists() and not args.force:
        print(f"Config already exists: {CONFIG_PATH} (use --force to overwrite)")
        return 1
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(example_config(), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)
    print(f"Wrote {CONFIG_PATH}. Edit it, then run `claude-worklog doctor`.")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"✗ config: {exc}")
        return 1
    print(f"✓ config: {cfg.path} (device={cfg.device})")
    git_dir = cfg.store_repo / ".git"
    if git_dir.exists():
        remote = subprocess.run(["git", "-C", str(cfg.store_repo), "remote"],
                                capture_output=True, text=True).stdout.strip()
        print(f"✓ store repo: {cfg.store_repo} (remote: {remote or 'none — local only'})")
    else:
        ok = False
        print(f"✗ store repo: {cfg.store_repo} is not a git clone")
    from .identity import detected_logins, mask_email

    for a in cfg.accounts:
        proj = a.config_dir / "projects"
        n = sum(1 for _ in proj.rglob("*.jsonl")) if proj.is_dir() else 0
        mark = "✓" if proj.is_dir() else "✗"
        ok &= proj.is_dir()
        events = installed_events(a)
        missing = [ev for ev in HOOK_EVENTS if ev not in events]
        hooks = "hooks installed" if not missing else f"hook NOT installed: {', '.join(missing)}"
        role = (" [matched login]" if a.matched else
                " [default for shared folder]" if len(cfg.accounts_by_dir()[a.config_dir]) > 1 else "")
        print(f"{mark} account {a.name}{role}: {a.config_dir} ({n} transcripts, {hooks})")
    for login in detected_logins():  # e-mails masked: doctor output gets pasted into issues
        for d in login["dirs"]:
            if d in cfg.accounts_by_dir():
                name = cfg.resolve_account(d, login["account_id"], login["email"]).name
                print(f"  detected login {mask_email(login['email'])} in {d} -> counted as {name}")
    exe = shutil.which(cfg.claude_bin)
    missing = cfg.claude_bin + " not found (AI summaries will fall back to plain)"
    print(f"{'✓' if exe else '!'} claude binary: {exe or missing}")
    return 0 if ok else 1


def cmd_install_hook(args) -> int:
    """Add the SessionStart and SessionEnd hooks to each account's settings.json (backup first, idempotent)."""
    cfg = load_config()
    targets = [a for a in cfg.accounts if not args.account or a.name == args.account]
    if not targets:
        print(f"No account named {args.account!r}")
        return 1
    ok_all = True
    for a in targets:
        ok, msg = install_hook(a)
        ok_all &= ok
        print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok_all else 1


def cmd_serve(args) -> int:
    from .web import serve
    serve(args.port)
    return 0


def cmd_demo(args) -> int:
    from .demo import run
    return run(args.port, seed=args.seed)


def cmd_install_service(args) -> int:
    from . import services
    from .web import HOST_NAME
    try:
        for msg in services.install(args.port, report=args.report, start=not args.no_start):
            print(msg)
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return 1
    if not args.no_start:
        print(f"\n✓ Worklog UI is running: http://{HOST_NAME}:{args.port}")
        print("  It starts automatically whenever you log in. Remove any cron lines you added earlier.")
    return 0


def cmd_hook(args) -> int:
    """Called by Claude Code at SessionStart and SessionEnd.

    Must be fast, must never fail the session and must print nothing: SessionStart
    stdout is added to Claude's context. SessionEnd hooks share a 1.5 s budget.
    """
    from .identity import note_session

    try:
        payload = sys.stdin.read(1_000_000) if not sys.stdin.isatty() else ""
        data = json.loads(payload) if payload.strip() else {}
        data = data if isinstance(data, dict) else {}
        sid = data.get("session_id")
        event = data.get("hook_event_name") or "SessionEnd"  # hooks installed by 0.1.0 were SessionEnd only
        if event == "SessionStart":
            note_session(sid, "start")
            return 0
        log.info("SessionEnd hook for session %s; spawning background collect", str(sid)[:8])
        subprocess.Popen(  # detached so Claude Code exits immediately
            [sys.executable, "-m", "worklog.cli", "--quiet", "collect", "--days", "2"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        note_session(sid, "end")  # after the spawn: the collect must not wait on us
    except Exception:  # noqa: BLE001 - a tracking hook must never break Claude Code
        log.exception("hook failed")
    return 0


def cmd_collect(args) -> int:
    from .collector import collect

    cfg = load_config()
    end = args.date or date.today()
    days = [end - timedelta(days=i) for i in reversed(range(args.days))]
    n = collect(cfg, days)
    print(f"collected {n} new/updated record(s) for {days[0]}..{days[-1]}")
    from .chats import scan_downloads
    for res in scan_downloads(cfg):  # after collect: the store lock is not re-entrant
        print(res.message())
    return 0


def cmd_import_chats(args) -> int:
    from .chats import ExportError, import_chats

    cfg = load_config()
    try:
        res = import_chats(cfg, args.path.expanduser(), account=args.account, since=args.since)
    except ExportError as exc:
        print(f"✗ {exc}")
        return 1
    print(res.message())
    return 0


def cmd_report(args) -> int:
    from .report import build_report

    cfg = load_config()
    now = datetime.now()
    day = args.date or (date.today() - timedelta(days=1) if args.auto and now.hour < 6 else date.today())
    use_ai = cfg.use_ai and not args.no_ai
    if not args.no_collect:
        from .collector import collect
        collect(cfg, [day])
    text, path = build_report(cfg, day, use_ai=use_ai, write=not args.stdout)
    print(text if args.stdout else f"report written: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="claude-worklog", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write an example config")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)
    sub.add_parser("doctor", help="check config, store repo, accounts, hooks").set_defaults(func=cmd_doctor)
    p = sub.add_parser("install-hook", help="add SessionStart/SessionEnd hooks to each account's settings.json")
    p.add_argument("--account")
    p.set_defaults(func=cmd_install_hook)
    sub.add_parser("hook", help="(called by Claude Code)").set_defaults(func=cmd_hook)
    p = sub.add_parser("collect", help="parse transcripts and sync records")
    p.add_argument("--date", type=_parse_day, help="last day to collect (default today)")
    p.add_argument("--days", type=int, default=1, choices=range(1, 31), metavar="N", help="days back (1-30)")
    p.set_defaults(func=cmd_collect)
    p = sub.add_parser("import-chats", help="import a claude.ai data export (conversations-*.zip)")
    p.add_argument("path", type=Path, help="conversations-NNN.zip, conversations.json, or a folder with them")
    p.add_argument("--account", help="worklog account to file the chats under (remembered for this login)")
    p.add_argument("--since", type=_parse_day, help="skip messages before this day (YYYY-MM-DD)")
    p.set_defaults(func=cmd_import_chats)
    p = sub.add_parser("report", help="build the daily report")
    p.add_argument("--date", type=_parse_day, help="YYYY-MM-DD, today (default) or yesterday")
    p.add_argument("--no-ai", action="store_true", help="skip claude -p summaries")
    p.add_argument("--no-collect", action="store_true", help="don't collect this device first")
    p.add_argument("--stdout", action="store_true", help="print instead of writing to the store")
    p.add_argument("--auto", action="store_true", help="before 06:00 report yesterday, else today (for the timer)")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("serve", help="run the web UI on loopback")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("demo", help="try the web UI on generated fake data (touches none of your files)")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--seed", type=int, default=7, help="change for different fake data")
    p.set_defaults(func=cmd_demo)
    p = sub.add_parser("install-service", help="run the UI + collect/report timers as systemd user services")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--report", action=argparse.BooleanOptionalAction, default=True,
                   help="build the daily report on this device (use --no-report on all but one device)")
    p.add_argument("--no-start", action="store_true", help="only write unit files")
    p.set_defaults(func=cmd_install_service)

    args = ap.parse_args(argv)
    if args.cmd == "demo":  # its own process must not log into the real state folder
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
        return args.func(args)
    _setup_logging(args.verbose, quiet=args.quiet or args.cmd == "hook")
    if args.cmd == "serve":
        logging.getLogger().handlers[-1].setLevel(logging.INFO)  # journald shows startup + errors
    try:
        return args.func(args)
    except (ConfigError, TimeoutError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
