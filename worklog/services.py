"""systemd --user units: the web UI (always on) + collect/report timers (replace cron).

Timers use Persistent=true, so a run missed while the laptop was asleep/off happens on wake.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
WEB, COLLECT, REPORT = "claude-worklog-web.service", "claude-worklog-collect.timer", "claude-worklog-report.timer"
# Set by `claude-worklog demo`: the demo must never start, stop or enable the user's real units.
DEMO_ENV = "CLAUDE_WORKLOG_DEMO"


def demo_mode() -> bool:
    return os.environ.get(DEMO_ENV) == "1"


def _q(s: str) -> str:
    """Quote for a systemd unit file (and escape % specifiers)."""
    s = s.replace("%", "%%")
    return f'"{s}"' if any(c in s for c in ' "\\') else s


def _units(port: int) -> dict[str, str]:
    py = _q(sys.executable)  # the pipx venv's python: always has the package
    # Timers/services don't inherit your shell PATH; freeze it so `claude` and `git` are found.
    env = f'Environment={_q("PATH=" + os.environ.get("PATH", "/usr/bin:/bin"))}\nEnvironment=PYTHONUNBUFFERED=1'
    oneshot = "[Service]\nType=oneshot\nNice=10\n" + env + "\n"
    return {
        WEB: f"""[Unit]
Description=claude-worklog web UI (http://worklog.localhost:{port})

[Service]
Type=simple
ExecStart={py} -m worklog.cli serve --port {port}
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
{env}

[Install]
WantedBy=default.target
""",
        "claude-worklog-collect.service": f"""[Unit]
Description=claude-worklog: collect Claude Code sessions (backstop for the hook)

{oneshot}ExecStart={py} -m worklog.cli --quiet collect --days 2
""",
        COLLECT: """[Unit]
Description=claude-worklog hourly collect

[Timer]
OnBootSec=3min
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
""",
        "claude-worklog-report.service": f"""[Unit]
Description=claude-worklog: build daily report

{oneshot}ExecStart={py} -m worklog.cli --quiet report --auto
""",
        REPORT: """[Unit]
Description=claude-worklog daily report (23:30 today, 00:20 re-run for yesterday)

[Timer]
OnCalendar=*-*-* 23:30:00
OnCalendar=*-*-* 00:20:00
Persistent=true

[Install]
WantedBy=timers.target
""",
    }


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    if demo_mode():
        raise RuntimeError("systemd is disabled in demo mode")
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def available() -> bool:
    return not demo_mode() and shutil.which("systemctl") is not None


def install(port: int, report: bool, start: bool = True) -> list[str]:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    msgs = []
    for name, text in _units(port).items():
        (UNIT_DIR / name).write_text(text, encoding="utf-8")
        msgs.append(f"wrote {UNIT_DIR / name}")
    if not start:
        return msgs
    if not available():
        raise RuntimeError("systemctl not found; this needs a systemd-based system such as Ubuntu")
    for args in (("daemon-reload",), ("enable", "--now", WEB, COLLECT), ("restart", WEB)):
        r = _systemctl(*args)
        if r.returncode != 0:
            raise RuntimeError(f"systemctl --user {' '.join(args)} failed: {r.stderr.strip()}")
    msgs.append(set_report_timer(report))
    return msgs


def set_report_timer(enabled: bool) -> str:
    r = _systemctl("enable" if enabled else "disable", "--now", REPORT)
    if r.returncode != 0:
        raise RuntimeError(f"could not {'enable' if enabled else 'disable'} {REPORT}: {r.stderr.strip()}")
    return f"daily report on this device: {'on' if enabled else 'off'}"


def status() -> dict[str, str]:
    if not available():
        reason = "disabled in demo mode" if demo_mode() else "systemd not available"
        return {u: reason for u in (WEB, COLLECT, REPORT)}
    out = {}
    for unit in (WEB, COLLECT, REPORT):
        try:
            active = _systemctl("is-active", unit).stdout.strip() or "unknown"
            enabled = _systemctl("is-enabled", unit).stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            active = enabled = "unknown"
        out[unit] = f"{active}, {enabled}"
    return out


def report_timer_enabled() -> bool:
    try:
        return available() and _systemctl("is-enabled", REPORT).stdout.strip() == "enabled"
    except (OSError, subprocess.TimeoutExpired):
        return False
