"""Linux, macOS and Windows: background services, locks, paths and the hook command.

System commands (systemctl, launchctl, schtasks) and the Windows registry are faked, so
these tests run on any OS and never touch the real service manager.
"""
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from worklog import hooks, locks, services
from worklog.config import home_relative


class FakeRun:
    """Records commands; answers with return code 0 (or `fail` for commands starting with given args)."""

    def __init__(self, fail: tuple = (), stdout: str = ""):
        self.calls, self.fail, self.stdout = [], fail, stdout

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.fail and tuple(cmd[: len(self.fail)]) == self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "nope")
        return subprocess.CompletedProcess(cmd, 0, self.stdout, "")


@pytest.fixture
def fake(monkeypatch, home):
    run = FakeRun()
    monkeypatch.setattr(services.subprocess, "run", run)
    monkeypatch.setattr(services.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.delenv(services.DEMO_ENV, raising=False)
    return run


# ------------------------------------------------------------------ which backend
@pytest.mark.parametrize("platform,name", [("linux", "systemd"), ("darwin", "launchd"), ("win32", "Task Scheduler")])
def test_backend_follows_the_os(monkeypatch, platform, name):
    monkeypatch.setattr(services.sys, "platform", platform)
    assert services.backend().name == name


# ------------------------------------------------------------------ macOS
def test_mac_install_writes_agents_and_loads_them(monkeypatch, fake, home):
    monkeypatch.setattr(services.sys, "platform", "darwin")
    monkeypatch.setattr(services, "AGENT_DIR", home / "Library" / "LaunchAgents")
    monkeypatch.setattr(services.os, "getuid", lambda: 501, raising=False)
    msgs = services.install(8765, report=True)
    web = plistlib.loads((home / "Library/LaunchAgents/dev.worklog.web.plist").read_bytes())
    assert web["ProgramArguments"][-4:] == ["worklog.cli", "serve", "--port", "8765"]
    assert web["RunAtLoad"] is True and web["KeepAlive"] == {"SuccessfulExit": False}
    path = web["EnvironmentVariables"]["PATH"].split(":")
    assert "/opt/homebrew/bin" in path and str(home / ".local/bin") in path  # launchd has no shell PATH
    report = plistlib.loads((home / "Library/LaunchAgents/dev.worklog.report.plist").read_bytes())
    assert report["StartCalendarInterval"] == [{"Hour": 23, "Minute": 30}, {"Hour": 0, "Minute": 20}]
    collect = plistlib.loads((home / "Library/LaunchAgents/dev.worklog.collect.plist").read_bytes())
    assert collect["StartInterval"] == 3600
    boots = [c for c in fake.calls if c[:2] == ["launchctl", "bootstrap"]]
    assert [Path(c[3]).name for c in boots] == ["dev.worklog.web.plist", "dev.worklog.collect.plist",
                                               "dev.worklog.report.plist"]
    assert all(c[2] == "gui/501" for c in boots) and "daily report on this device: on" in msgs


def test_mac_bootstrap_failure_is_reported(monkeypatch, home):
    monkeypatch.setattr(services.sys, "platform", "darwin")
    monkeypatch.setattr(services, "AGENT_DIR", home / "Library" / "LaunchAgents")
    monkeypatch.setattr(services.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(services.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(services.subprocess, "run", FakeRun(fail=("launchctl", "bootstrap")))
    with pytest.raises(RuntimeError, match="bootstrap"):
        services.install(8765, report=False)


def test_mac_status_and_uninstall(monkeypatch, fake, home):
    monkeypatch.setattr(services.sys, "platform", "darwin")
    monkeypatch.setattr(services, "AGENT_DIR", home / "Library" / "LaunchAgents")
    monkeypatch.setattr(services.os, "getuid", lambda: 501, raising=False)
    services.install(8765, report=False)
    fake.stdout = "state = running"
    assert services.status()["dev.worklog.web"] == "running"
    assert services.autostart_ok() is True
    services.uninstall()
    assert not list((home / "Library/LaunchAgents").glob("dev.worklog.*"))


# ------------------------------------------------------------------ Windows
@pytest.fixture
def win(monkeypatch, fake, home):
    reg = {}
    monkeypatch.setattr(services.sys, "platform", "win32")
    monkeypatch.setattr(services, "_reg_get", lambda name: reg.get(name))
    def reg_set(name, value):
        if value is None:
            reg.pop(name, None)
        else:
            reg[name] = value
    monkeypatch.setattr(services, "_reg_set", reg_set)
    started = []
    monkeypatch.setattr(services.subprocess, "Popen", lambda *a, **k: started.append((a, k)))
    xmls = []
    real_create = services._WindowsTasks._create

    def capture(self, task, xml):
        xmls.append((task, xml))
        return real_create(self, task, xml)
    monkeypatch.setattr(services._WindowsTasks, "_create", capture)
    return reg, started, xmls


def test_windows_install_registers_login_start_and_tasks(win, fake):
    reg, started, xmls = win
    services.install(8765, report=True)
    assert "serve --port 8765" in reg[services.RUN_VALUE]  # starts at login, no admin rights needed
    tasks = dict(xmls)
    assert set(tasks) == {r"\Worklog\collect", r"\Worklog\report", r"\Worklog\report-late"}
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in tasks[r"\Worklog\collect"]  # missed runs catch up
    assert "<Interval>PT1H</Interval>" in tasks[r"\Worklog\collect"]
    assert "T23:30:00" in tasks[r"\Worklog\report"] and "T00:20:00" in tasks[r"\Worklog\report-late"]
    assert "<RunLevel>LeastPrivilege</RunLevel>" in tasks[r"\Worklog\report"]
    creates = [c for c in fake.calls if c[:2] == ["schtasks", "/Create"]]
    assert all("/XML" in c for c in creates)
    (args, kw), = started  # the web UI starts right away, detached and without a window
    assert kw["creationflags"] & 0x08000000 and "serve" in args[0]


def test_windows_report_off_and_uninstall(win, fake):
    reg, _, _ = win
    services.install(8765, report=False)
    assert [c for c in fake.calls if c[:2] == ["schtasks", "/Delete"]]
    assert services.autostart_ok() is True
    services.uninstall()
    assert services.RUN_VALUE not in reg and services.autostart_ok() is False


def test_windows_task_xml_escapes_paths(monkeypatch):
    monkeypatch.setattr(services, "_python", lambda windowless=False: r"C:\Users\Jane & Co\pythonw.exe")
    xml = services._task_xml(["report"], services._daily("23:30"))
    assert r"C:\Users\Jane &amp; Co\pythonw.exe" in xml


# ------------------------------------------------------------------ Linux still the same
def test_linux_units_unchanged(monkeypatch, fake, home):
    monkeypatch.setattr(services.sys, "platform", "linux")
    monkeypatch.setattr(services, "UNIT_DIR", home / ".config/systemd/user")
    services.install(8765, report=False)
    assert (home / ".config/systemd/user/claude-worklog-web.service").exists()
    assert ["systemctl", "--user", "enable", "--now", services.WEB, services.COLLECT] in fake.calls


def test_demo_mode_never_calls_the_service_manager(monkeypatch):
    monkeypatch.setenv(services.DEMO_ENV, "1")
    monkeypatch.setattr(services.subprocess, "run", lambda *a, **k: pytest.fail("service manager was called"))
    for fn in (lambda: services.install(1, True), services.uninstall, lambda: services.set_report_timer(True)):
        with pytest.raises(RuntimeError):
            fn()
    assert services.autostart_ok() is None


# ------------------------------------------------------------------ portable bits
def test_lock_excludes_a_second_holder(tmp_path):
    path = tmp_path / "x.lock"
    with locks.exclusive(path, 1):
        result = []

        def other():
            try:
                with locks.exclusive(path, 0.2):
                    result.append("got it")
            except TimeoutError:
                result.append("timeout")
        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert result == ["timeout"]
    with locks.exclusive(path, 0.2):  # released afterwards
        pass


def test_home_relative_uses_forward_slashes(home):
    assert home_relative(home / "code" / "shop") == "~/code/shop"
    assert home_relative(home) == "~"
    assert home_relative(Path("/elsewhere/x")) == str(Path("/elsewhere/x"))


def test_hook_command_is_quoted(monkeypatch):
    monkeypatch.setattr(hooks.shutil, "which", lambda n: "/Users/Jane Doe/.local/bin/claude-worklog")
    monkeypatch.setattr(hooks.sys, "platform", "darwin")
    assert hooks.hook_command() == "'/Users/Jane Doe/.local/bin/claude-worklog' hook"
    monkeypatch.setattr(hooks.shutil, "which", lambda n: r"C:\Users\Jane Doe\.local\bin\claude-worklog.EXE")
    monkeypatch.setattr(hooks.sys, "platform", "win32")
    assert hooks.hook_command() == r'"C:\Users\Jane Doe\.local\bin\claude-worklog.EXE" hook'


def test_data_repo_gets_line_ending_rules(repo):
    from worklog.store import GitStore
    s = GitStore(repo)
    s.write_json("records/2026-10-01/dev/personal/a.json", {"a": 1})
    s.commit_and_push("test")
    assert (repo / ".gitattributes").read_text() == "records/** text eol=lf\nreports/** text eol=lf\n"
    assert b"\r\n" not in (repo / "records/2026-10-01/dev/personal/a.json").read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="checks the Unix detach flags")
def test_detach_flags_per_os(monkeypatch):
    assert services._detached() == {"start_new_session": True}
    monkeypatch.setattr(services.sys, "platform", "win32")
    assert services._detached()["creationflags"] & 0x00000008


def test_doctor_spots_a_service_pointing_at_a_missing_python(monkeypatch, fake, home, repo, capsys):
    from test_accounts import make_cfg

    from worklog import cli
    from worklog.config import load_config
    monkeypatch.setattr(services.sys, "platform", "linux")
    monkeypatch.setattr(services, "UNIT_DIR", home / ".config/systemd/user")
    services.install(8765, report=False)
    cfg = make_cfg(home, repo)
    monkeypatch.setattr(cli, "load_config", lambda: load_config(cfg.path))
    cli.main(["doctor"])
    assert "no longer exists" not in capsys.readouterr().out
    unit = home / ".config/systemd/user/claude-worklog-web.service"
    unit.write_text(unit.read_text().replace(sys.executable, "/gone/python3"))
    assert cli.main(["doctor"]) == 1
    assert "/gone/python3, which no longer exists" in capsys.readouterr().out
