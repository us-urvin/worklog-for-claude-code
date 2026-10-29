"""Run Worklog in the background: the web UI (always on) plus an hourly collect and the nightly report.

One set of functions, one implementation per system:

* **Linux**: systemd --user units. Timers use Persistent=true, so a run missed while the
  laptop was asleep or off happens on wake.
* **macOS**: launchd agents in ~/Library/LaunchAgents. launchd runs a missed calendar job
  after wake, too.
* **Windows**: the web UI starts at login from the per-user "Run" registry key (no admin
  rights needed); the hourly and nightly jobs are Task Scheduler tasks that also run when a
  start was missed.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
WEB, COLLECT, REPORT = "claude-worklog-web.service", "claude-worklog-collect.timer", "claude-worklog-report.timer"
# Set by `claude-worklog demo`: the demo must never start, stop or enable the user's real services.
DEMO_ENV = "CLAUDE_WORKLOG_DEMO"


def demo_mode() -> bool:
    return os.environ.get(DEMO_ENV) == "1"


def system() -> str:
    """"linux", "mac" or "windows" (WSL counts as Linux)."""
    return "mac" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else "linux"


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    if demo_mode():
        raise RuntimeError("background services are disabled in demo mode")
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)


def _python(windowless: bool = False) -> str:
    """The interpreter of the installed package (the pipx venv). On Windows, pythonw.exe has no console window."""
    exe = Path(sys.executable)
    if windowless and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def _path_env() -> str:
    """PATH for background jobs: they don't see your shell's PATH, so `claude` and `git` must be findable."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [str(Path.home() / ".local" / "bin")]
    if system() == "mac":
        extra += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    claude = shutil.which("claude")
    if claude:
        extra.append(str(Path(claude).parent))
    seen, out = set(), []
    for p in parts + extra:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return os.pathsep.join(out)


# ================================================================== Linux: systemd
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


class _Systemd:
    name = "systemd"

    def _ctl(self, *args: str) -> subprocess.CompletedProcess:
        return _run(["systemctl", "--user", *args])

    def available(self) -> bool:
        return shutil.which("systemctl") is not None

    def install(self, port: int, report: bool, start: bool) -> list[str]:
        UNIT_DIR.mkdir(parents=True, exist_ok=True)
        msgs = []
        for name, text in _units(port).items():
            (UNIT_DIR / name).write_text(text, encoding="utf-8")
            msgs.append(f"wrote {UNIT_DIR / name}")
        if not start:
            return msgs
        if not self.available():
            raise RuntimeError("systemctl not found; this needs a systemd-based system such as Ubuntu")
        for args in (("daemon-reload",), ("enable", "--now", WEB, COLLECT), ("restart", WEB)):
            r = self._ctl(*args)
            if r.returncode != 0:
                raise RuntimeError(f"systemctl --user {' '.join(args)} failed: {r.stderr.strip()}")
        msgs.append(self.set_report_timer(report))
        return msgs

    def set_report_timer(self, enabled: bool) -> str:
        r = self._ctl("enable" if enabled else "disable", "--now", REPORT)
        if r.returncode != 0:
            raise RuntimeError(f"could not {'enable' if enabled else 'disable'} {REPORT}: {r.stderr.strip()}")
        return f"daily report on this device: {'on' if enabled else 'off'}"

    def status(self) -> dict[str, str]:
        out = {}
        for unit in (WEB, COLLECT, REPORT):
            try:
                active = self._ctl("is-active", unit).stdout.strip() or "unknown"
                enabled = self._ctl("is-enabled", unit).stdout.strip() or "unknown"
            except (OSError, subprocess.TimeoutExpired):
                active = enabled = "unknown"
            out[unit] = f"{active}, {enabled}"
        return out

    def report_timer_enabled(self) -> bool:
        return self._ctl("is-enabled", REPORT).stdout.strip() == "enabled"

    def autostart_ok(self) -> bool:
        return self._ctl("is-enabled", WEB).stdout.strip() == "enabled"

    def uninstall(self) -> list[str]:
        msgs = []
        if self.available():
            self._ctl("disable", "--now", WEB, COLLECT, REPORT)
        for name in _units(0):
            if (UNIT_DIR / name).exists():
                (UNIT_DIR / name).unlink()
                msgs.append(f"removed {UNIT_DIR / name}")
        if self.available():
            self._ctl("daemon-reload")
        return msgs


# ================================================================== macOS: launchd
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
MAC_WEB, MAC_COLLECT, MAC_REPORT = "dev.worklog.web", "dev.worklog.collect", "dev.worklog.report"


def _plists(port: int) -> dict[str, dict]:
    from .config import STATE_DIR
    py, env = _python(), {"PATH": _path_env(), "PYTHONUNBUFFERED": "1"}
    log = str(STATE_DIR / "launchd.log")

    def agent(label: str, args: list[str], **extra) -> dict:
        return {"Label": label, "ProgramArguments": [py, "-m", "worklog.cli", *args], "EnvironmentVariables": env,
                "WorkingDirectory": str(Path.home()), "StandardOutPath": log, "StandardErrorPath": log,
                "ProcessType": "Background", **extra}
    return {
        MAC_WEB: agent(MAC_WEB, ["serve", "--port", str(port)], RunAtLoad=True,
                       KeepAlive={"SuccessfulExit": False}, ThrottleInterval=5),
        MAC_COLLECT: agent(MAC_COLLECT, ["--quiet", "collect", "--days", "2"], StartInterval=3600, RunAtLoad=True,
                           LowPriorityIO=True, Nice=10),
        MAC_REPORT: agent(MAC_REPORT, ["--quiet", "report", "--auto"], LowPriorityIO=True, Nice=10,
                          StartCalendarInterval=[{"Hour": 23, "Minute": 30}, {"Hour": 0, "Minute": 20}]),
    }


class _Launchd:
    name = "launchd"

    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    def _ctl(self, *args: str) -> subprocess.CompletedProcess:
        return _run(["launchctl", *args])

    def _path(self, label: str) -> Path:
        return AGENT_DIR / f"{label}.plist"

    def available(self) -> bool:
        return shutil.which("launchctl") is not None

    def _load(self, label: str) -> None:
        self._ctl("bootout", f"{self._domain()}/{label}")  # not loaded yet is fine
        r = self._ctl("bootstrap", self._domain(), str(self._path(label)))
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap {label} failed: {r.stderr.strip() or r.stdout.strip()}")
        self._ctl("enable", f"{self._domain()}/{label}")

    def install(self, port: int, report: bool, start: bool) -> list[str]:
        from .config import STATE_DIR
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        msgs = []
        for label, plist in _plists(port).items():
            self._path(label).write_bytes(plistlib.dumps(plist))
            msgs.append(f"wrote {self._path(label)}")
        if not start:
            return msgs
        if not self.available():
            raise RuntimeError("launchctl not found")
        for label in (MAC_WEB, MAC_COLLECT):
            self._load(label)
        msgs.append(self.set_report_timer(report))
        return msgs

    def _loaded(self, label: str) -> bool:
        return self._ctl("print", f"{self._domain()}/{label}").returncode == 0

    def set_report_timer(self, enabled: bool) -> str:
        if enabled:
            if not self._path(MAC_REPORT).exists():
                raise RuntimeError("run `claude-worklog install-service` first")
            self._load(MAC_REPORT)
        else:
            self._ctl("bootout", f"{self._domain()}/{MAC_REPORT}")
            self._ctl("disable", f"{self._domain()}/{MAC_REPORT}")
        return f"daily report on this device: {'on' if enabled else 'off'}"

    def status(self) -> dict[str, str]:
        out = {}
        for label in (MAC_WEB, MAC_COLLECT, MAC_REPORT):
            try:
                r = self._ctl("print", f"{self._domain()}/{label}")
            except (OSError, subprocess.TimeoutExpired):
                out[label] = "unknown"
                continue
            if r.returncode != 0:
                out[label] = "not loaded" if self._path(label).exists() else "not installed"
            else:
                out[label] = "running" if "state = running" in r.stdout else "loaded, waiting"
        return out

    def report_timer_enabled(self) -> bool:
        return self._loaded(MAC_REPORT)

    def autostart_ok(self) -> bool:
        return self._path(MAC_WEB).exists() and self._loaded(MAC_WEB)

    def uninstall(self) -> list[str]:
        msgs = []
        for label in (MAC_WEB, MAC_COLLECT, MAC_REPORT):
            if self.available():
                self._ctl("bootout", f"{self._domain()}/{label}")
            if self._path(label).exists():
                self._path(label).unlink()
                msgs.append(f"removed {self._path(label)}")
        return msgs


# ================================================================== Windows: Run key + Task Scheduler
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Worklog for Claude Code"
WIN_COLLECT, WIN_REPORT, WIN_REPORT_LATE = r"\Worklog\collect", r"\Worklog\report", r"\Worklog\report-late"


def _win_cmdline(args: list[str]) -> str:
    return subprocess.list2cmdline([_python(windowless=True), "-m", "worklog.cli", *args])


def _task_xml(command_args: list[str], trigger: str) -> str:
    """A Task Scheduler task for the current user: no admin rights, runs late if a start was missed."""
    from xml.sax.saxutils import escape
    exe, *rest = [_python(windowless=True), "-m", "worklog.cli", *command_args]
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Worklog for Claude Code</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
  </Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(exe)}</Command>
    <Arguments>{escape(subprocess.list2cmdline(rest))}</Arguments></Exec></Actions>
</Task>
"""


def _hourly() -> str:
    return ("<TimeTrigger><StartBoundary>2026-01-01T00:05:00</StartBoundary><Enabled>true</Enabled>"
            "<Repetition><Interval>PT1H</Interval></Repetition></TimeTrigger>")


def _daily(hh_mm: str) -> str:
    return (f"<CalendarTrigger><StartBoundary>2026-01-01T{hh_mm}:00</StartBoundary><Enabled>true</Enabled>"
            "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")


def _reg_get(name: str) -> str | None:
    import winreg  # Windows only
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return None


def _reg_set(name: str, value: str | None) -> None:
    import winreg  # Windows only
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except OSError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _detached() -> dict:
    """Popen keyword arguments that start a process which outlives us and opens no window."""
    if system() == "windows":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True}


class _WindowsTasks:
    name = "Task Scheduler"

    def _sch(self, *args: str) -> subprocess.CompletedProcess:
        return _run(["schtasks", *args])

    def available(self) -> bool:
        return shutil.which("schtasks") is not None

    def _create(self, task: str, xml: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "task.xml"
            f.write_text(xml, encoding="utf-16")
            r = self._sch("/Create", "/F", "/TN", task, "/XML", str(f))
        if r.returncode != 0:
            raise RuntimeError(f"schtasks could not create {task}: {r.stderr.strip() or r.stdout.strip()}")

    def install(self, port: int, report: bool, start: bool) -> list[str]:
        web = _win_cmdline(["serve", "--port", str(port)])
        if not start:
            return [f"would start at login: {web}"]
        if not self.available():
            raise RuntimeError("schtasks not found")
        _reg_set(RUN_VALUE, web)
        msgs = ["the web UI starts when you log in (Settings > Apps > Startup lists it)"]
        self._create(WIN_COLLECT, _task_xml(["--quiet", "collect", "--days", "2"], _hourly()))
        msgs.append("hourly collect scheduled")
        msgs.append(self.set_report_timer(report))
        subprocess.Popen([_python(windowless=True), "-m", "worklog.cli", "serve", "--port", str(port)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detached())
        return msgs

    def set_report_timer(self, enabled: bool) -> str:
        if enabled:
            self._create(WIN_REPORT, _task_xml(["--quiet", "report", "--auto"], _daily("23:30")))
            self._create(WIN_REPORT_LATE, _task_xml(["--quiet", "report", "--auto"], _daily("00:20")))
        else:
            for t in (WIN_REPORT, WIN_REPORT_LATE):
                self._sch("/Delete", "/F", "/TN", t)
        return f"daily report on this device: {'on' if enabled else 'off'}"

    def _exists(self, task: str) -> bool:
        return self._sch("/Query", "/TN", task).returncode == 0

    def status(self) -> dict[str, str]:
        out = {"web UI at login": "on" if _reg_get(RUN_VALUE) else "off"}
        for task in (WIN_COLLECT, WIN_REPORT):
            try:
                out[task.strip("\\")] = "scheduled" if self._exists(task) else "not scheduled"
            except (OSError, subprocess.TimeoutExpired):
                out[task.strip("\\")] = "unknown"
        return out

    def report_timer_enabled(self) -> bool:
        return self._exists(WIN_REPORT)

    def autostart_ok(self) -> bool:
        return bool(_reg_get(RUN_VALUE))

    def uninstall(self) -> list[str]:
        _reg_set(RUN_VALUE, None)
        for t in (WIN_COLLECT, WIN_REPORT, WIN_REPORT_LATE):
            self._sch("/Delete", "/F", "/TN", t)
        return ["removed the login entry and the scheduled tasks (stop a running web UI in Task Manager)"]


# ================================================================== public API
def backend():
    return {"mac": _Launchd, "windows": _WindowsTasks}.get(system(), _Systemd)()


def available() -> bool:
    return not demo_mode() and backend().available()


def install(port: int, report: bool, start: bool = True) -> list[str]:
    if demo_mode():
        raise RuntimeError("background services are disabled in demo mode")
    return backend().install(port, report, start)


def uninstall() -> list[str]:
    if demo_mode():
        raise RuntimeError("background services are disabled in demo mode")
    return backend().uninstall()


def set_report_timer(enabled: bool) -> str:
    if demo_mode():
        raise RuntimeError("background services are disabled in demo mode")
    return backend().set_report_timer(enabled)


def status() -> dict[str, str]:
    b = backend()
    if not available():
        reason = "disabled in demo mode" if demo_mode() else f"{b.name} not available"
        names = {"systemd": (WEB, COLLECT, REPORT), "launchd": (MAC_WEB, MAC_COLLECT, MAC_REPORT)}.get(
            b.name, ("web UI at login", "Worklog\\collect", "Worklog\\report"))
        return {u: reason for u in names}
    return b.status()


def configured_python() -> str | None:
    """The Python the installed background service starts, or None if it isn't installed."""
    import shlex
    kind = system()
    try:
        if kind == "mac":
            f = AGENT_DIR / f"{MAC_WEB}.plist"
            return plistlib.loads(f.read_bytes())["ProgramArguments"][0] if f.exists() else None
        if kind == "windows":
            value = _reg_get(RUN_VALUE)
            return (value.split('"')[1] if value.startswith('"') else value.split()[0]) if value else None
        f = UNIT_DIR / WEB
        if not f.exists():
            return None
        line = next((x for x in f.read_text(encoding="utf-8").splitlines() if x.startswith("ExecStart=")), "")
        return shlex.split(line[len("ExecStart="):])[0] if line else None
    except (OSError, ValueError, KeyError, IndexError, plistlib.InvalidFileException):
        return None


def report_timer_enabled() -> bool:
    try:
        return available() and backend().report_timer_enabled()
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return False


def autostart_ok() -> bool | None:
    """Whether Worklog starts by itself at login. None when it can't be known (demo mode)."""
    if demo_mode():
        return None
    if not available():
        return False
    try:
        return backend().autostart_ok()
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return False
