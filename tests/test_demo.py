"""`claude-worklog demo`: fake data only, confined to a temporary HOME."""
import json
import os
import signal
from datetime import datetime
from pathlib import Path

import pytest

from worklog import cli, demo, services, web
from worklog.config import load_config
from worklog.report import aggregate
from worklog.store import GitStore

NOW = datetime(2026, 9, 26, 15, 0)  # a Saturday afternoon; covers weekdays and a weekend


@pytest.fixture
def demo_home(tmp_path):
    home = tmp_path / "home"
    cfg_path = demo.generate(home, days=14, seed=7, now=NOW)
    return home, cfg_path


def _all_records(store: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((store / "records").rglob("*.json"))]


def test_generate_writes_only_inside_the_fake_home(tmp_path, demo_home):
    home, _ = demo_home
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written and all(home in p.parents for p in written)


def test_generated_config_and_records_cover_the_brief(demo_home):
    home, cfg_path = demo_home
    cfg = load_config(cfg_path)
    assert [a.name for a in cfg.accounts] == ["personal", "work"]
    assert cfg.use_ai is False  # the demo must never call `claude -p`
    assert home in cfg.store_repo.parents and all(home in a.config_dir.parents for a in cfg.accounts)
    records = _all_records(cfg.store_repo)
    assert len({r["date"] for r in records}) == 14
    assert {r["device"] for r in records} == set(demo.DEVICES)
    assert {r["account"] for r in records} == {"personal", "work"}
    assert len({r["project"]["name"] for r in records}) >= 4
    for r in records:  # the same shape the collector writes, so the UI and report can read it
        assert set(r) >= {"schema", "session_id", "date", "account", "device", "project", "tokens",
                          "tokens_by_model", "prompts", "files_touched", "commits", "span_minutes"}
        assert r["start"] <= f"{NOW.date()}T{NOW.hour:02d}" or r["date"] != NOW.date().isoformat()


def test_generated_data_contains_no_personal_data(demo_home):
    home, cfg_path = demo_home
    real_home = os.path.expanduser("~")
    for p in load_config(cfg_path).store_repo.rglob("*.json"):
        text = p.read_text()
        assert real_home not in text and "@" not in text


def test_generate_is_deterministic(tmp_path):
    a = demo.generate(tmp_path / "a", seed=3, now=NOW)
    b = demo.generate(tmp_path / "b", seed=3, now=NOW)
    ra, rb = _all_records(load_config(a).store_repo), _all_records(load_config(b).store_repo)
    assert [r["tokens"] for r in ra] == [r["tokens"] for r in rb]


def test_demo_data_renders_in_the_ui(demo_home):
    _, cfg_path = demo_home
    cfg = load_config(cfg_path)
    past = NOW.date().replace(day=NOW.day - 2)
    records = list(GitStore(cfg.store_repo).iter_json(f"records/{past.isoformat()}"))
    accounts, projects = aggregate(records)
    assert accounts and projects
    body, err = web.page_day(web.App(8766), cfg, past)
    assert err is None
    assert "Summary by Claude" in body  # past days carry saved summaries
    assert any(p.name in body for p in projects.values())


def test_demo_env_points_everything_at_the_fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/somewhere/real")
    monkeypatch.setenv("GIT_DIR", "/somewhere/real/.git")
    home = tmp_path / "home"
    env = demo.demo_env(home)
    assert env["HOME"] == str(home) and env[services.DEMO_ENV] == "1"
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        assert env[var].startswith(str(home))
    assert "CLAUDE_CONFIG_DIR" not in env and "GIT_DIR" not in env


def test_systemd_is_disabled_in_demo_mode(monkeypatch):
    monkeypatch.setenv(services.DEMO_ENV, "1")
    monkeypatch.setattr(services.subprocess, "run", lambda *a, **k: pytest.fail("systemctl was called"))
    assert services.available() is False
    assert not services.report_timer_enabled()
    assert set(services.status().values()) == {"disabled in demo mode"}
    with pytest.raises(RuntimeError):
        services.set_report_timer(True)


class _FakeServer:
    """Stands in for the `serve` subprocess; `wait` behaves like the user stopping the demo."""

    def __init__(self, cmd, env, cwd, stop):
        self.cmd, self.env, self.home, self.stop = cmd, env, Path(env["HOME"]), stop
        self.config_existed = (self.home / ".config/claude-worklog/config.toml").exists()
        self.terminated = False

    def wait(self, timeout=None):
        if not self.terminated:
            self.stop()
        return 0

    def terminate(self):
        self.terminated = True


@pytest.mark.parametrize("how", ["ctrl-c", "sigterm"])
def test_run_serves_from_a_temp_home_and_cleans_up(monkeypatch, capsys, how):
    servers = []

    def stop():
        if how == "ctrl-c":
            raise KeyboardInterrupt
        os.kill(os.getpid(), signal.SIGTERM)  # handled by run(): same cleanup as Ctrl+C

    real_popen = demo.subprocess.Popen

    def fake_popen(cmd, *args, **kw):
        if cmd[0] == "git":  # `git init` of the fake data repo runs for real
            return real_popen(cmd, *args, **kw)
        servers.append(_FakeServer(cmd, kw["env"], kw["cwd"], stop))
        return servers[-1]

    monkeypatch.setattr(demo.subprocess, "Popen", fake_popen)
    before = signal.getsignal(signal.SIGTERM)
    assert cli.main(["demo", "--port", "8799"]) == 0
    srv = servers[0]
    assert srv.cmd[-3:] == ["serve", "--port", "8799"] and srv.config_existed
    assert srv.env[services.DEMO_ENV] == "1"
    assert srv.home != Path.home()
    assert srv.terminated
    assert not srv.home.exists()  # temporary folder removed on exit
    assert signal.getsignal(signal.SIGTERM) == before
    assert "8799" in capsys.readouterr().out
