"""Update notice and one-click update. PyPI, pip and the restart are faked here."""
import io
import json
import time

import pytest
from test_accounts import make_cfg

from worklog import __version__, updates, web


@pytest.fixture
def ustate(home, monkeypatch):
    monkeypatch.setattr(updates, "STATE_DIR", home / ".local/state/claude-worklog")
    monkeypatch.delenv("CLAUDE_WORKLOG_DEMO", raising=False)
    return home


def test_versions_compare_like_people_expect():
    assert updates.is_newer("0.3.1", "0.3.0") and updates.is_newer("0.10.0", "0.9.9")
    assert not updates.is_newer("0.3.0", "0.3.0") and not updates.is_newer("0.2.9", "0.3.0")
    assert updates.parse("0.4.0rc1") < updates.parse("0.4.0")


def test_check_asks_pypi_at_most_once_a_day(ustate, monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(json.dumps({"info": {"version": "9.9.9"}}).encode())
    monkeypatch.setattr(updates, "urlopen", fake_urlopen)
    now = time.time()
    assert updates.check(now=now) == "9.9.9"
    assert updates.check(now=now + 3600) == "9.9.9" and len(calls) == 1  # cached
    updates.check(now=now + 25 * 3600)
    assert len(calls) == 2 and calls[0] == "https://pypi.org/pypi/worklog-for-claude-code/json"
    assert updates.available() == "9.9.9"


def test_offline_or_odd_answers_are_ignored(ustate, monkeypatch):
    def offline(req, timeout):
        raise OSError("no network")
    monkeypatch.setattr(updates, "urlopen", offline)
    assert updates.check(force=True) is None and updates.available() is None
    monkeypatch.setattr(updates, "urlopen", lambda req, timeout: io.BytesIO(b'{"info": {"version": "<script>"}}'))
    assert updates.check(force=True) is None


def test_upgrade_command_matches_how_it_was_installed(monkeypatch):
    monkeypatch.setattr(updates.shutil, "which", lambda n: "/usr/bin/pipx")
    assert updates.upgrade_command("pipx") == ["/usr/bin/pipx", "upgrade", "worklog-for-claude-code"]
    assert updates.upgrade_command("venv")[-3:] == ["install", "--upgrade", "worklog-for-claude-code"]
    assert updates.upgrade_command("dev") is None and updates.upgrade_command("system") is None
    assert updates.manual_command("system").startswith("python3 -m pip install --user --upgrade")


def _state(ustate, latest):
    p = ustate / ".local/state/claude-worklog/update.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))


def test_notice_shows_button_or_command_and_can_be_dismissed(ustate, repo, monkeypatch):
    cfg = make_cfg(ustate, repo)
    app = web.App(1)
    _state(ustate, "9.9.9")
    monkeypatch.setattr(updates, "install_kind", lambda: "pipx")
    html = web._update_html(app, cfg)
    assert "Worklog 9.9.9 is out" in html and f"you have {__version__}" in html and 'action="/update"' in html
    monkeypatch.setattr(updates, "install_kind", lambda: "system")
    html = web._update_html(app, cfg)
    assert 'action="/update"' not in html and "pip install --user --upgrade" in html  # copy the command instead
    updates.dismiss("9.9.9")
    assert web._update_html(app, cfg) == ""
    _state(ustate, __version__)
    assert web._update_html(app, cfg) == ""  # nothing newer
    cfg.check_updates = False
    _state(ustate, "10.0.0")
    assert web._update_html(app, cfg) == ""  # switched off in Settings


def test_update_now_upgrades_then_restarts(ustate, repo, monkeypatch):
    make_cfg(ustate, repo)
    monkeypatch.setattr(updates, "install_kind", lambda: "pipx")
    monkeypatch.setattr(updates, "upgrade", lambda: (True, "Updated."))
    monkeypatch.setattr(updates, "installed_version", lambda: "9.9.9")
    restarted = []
    app = web.App(1)
    monkeypatch.setattr(app, "restart", lambda: restarted.append(True))
    monkeypatch.setattr(web.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda s: fn()})())
    loc, err, _ = app._update({})
    assert err is None and loc == "/?ok=job"
    for _ in range(100):
        if not app.jobs.snapshot()["running"]:
            break
        time.sleep(0.02)
    assert app.jobs.snapshot()["message"] == "Updated to 9.9.9. Restarting…" and restarted == [True]


def test_failed_update_says_what_to_do(ustate, repo, monkeypatch):
    monkeypatch.setattr(updates, "install_kind", lambda: "venv")
    monkeypatch.setattr(updates.subprocess, "run",
                        lambda *a, **k: updates.subprocess.CompletedProcess(a, 1, "", "error: externally-managed"))
    ok, msg = updates.upgrade()
    assert not ok and "externally-managed" in msg and "Try by hand" in msg


def test_updated_message_is_shown_once_and_only_by_the_new_version(ustate):
    updates.note_updated("9.9.9")
    assert updates.pop_updated("0.3.0") is None  # the old process, just before it restarts
    assert updates.pop_updated("9.9.9") == "9.9.9"  # the new one
    assert updates.pop_updated("9.9.9") is None  # only once
