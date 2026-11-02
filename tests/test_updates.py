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
    assert updates.upgrade_command("pipx") == ["/usr/bin/pipx", "upgrade", "--pip-args=--no-cache-dir",
                                               "worklog-for-claude-code"]
    assert updates.upgrade_command("venv")[-4:] == ["install", "--upgrade", "--no-cache-dir", "worklog-for-claude-code"]
    assert "--no-cache-dir" in updates.manual_command("pipx")  # a stale pip cache can't hide the new version
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


def _pypi(version, uploaded):
    files = [{"upload_time_iso_8601": uploaded}] if uploaded else []
    body = json.dumps({"info": {"version": version}, "releases": {version: files}, "urls": files}).encode()
    return lambda req, timeout: io.BytesIO(body)


def test_a_release_is_announced_only_once_pypi_has_settled(ustate, monkeypatch):
    from datetime import datetime, timezone
    t0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(updates, "urlopen", _pypi("9.9.9", "2026-11-02T12:00:00.000000Z"))
    assert updates.check(force=True, now=t0 + 60) is None  # just published: pip may not see it yet
    assert updates.check(now=t0 + 600) is None  # not re-checked every request...
    assert updates.check(now=t0 + 60 + updates.RETRY) == "9.9.9"  # ...but an hour later, not a day
    monkeypatch.setattr(updates, "urlopen", _pypi("10.0.0", None))
    assert updates.fetch_latest() is None  # listed but no files uploaded yet


def test_update_now_explains_when_pypi_is_still_catching_up(ustate, repo, monkeypatch):
    make_cfg(ustate, repo)
    _state(ustate, "9.9.9")
    monkeypatch.setattr(updates, "install_kind", lambda: "pipx")
    monkeypatch.setattr(updates, "upgrade", lambda: (True, "Updated."))
    monkeypatch.setattr(updates, "installed_version", lambda: __version__)  # pip found nothing newer
    app = web.App(1)
    app._update({})
    for _ in range(100):
        if not app.jobs.snapshot()["running"]:
            break
        time.sleep(0.02)
    snap = app.jobs.snapshot()
    assert "still publishing Worklog 9.9.9" in snap["message"] and "Try again in a few minutes" in snap["message"]


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


def _job(message, ok=True, age=0.0):
    return {"running": None, "finished": "11:21", "finished_at": time.time() - age, "message": message, "ok": ok}


def test_job_results_go_away_by_themselves_and_can_be_closed():
    page = web.layout("t", "", "day", "n", _job("Updated 178 session record(s)"), csrf="tok")
    assert "Updated 178 session record(s)" in page and 'action="/jobs/dismiss"' in page
    assert "Updated 178" not in web.layout("t", "", "day", "n", _job("Updated 178 session record(s)", age=121))
    failed = _job("Syncing failed: offline", ok=False, age=300)
    assert "Syncing failed" in web.layout("t", "", "day", "n", failed)  # errors stay longer
    assert "Syncing failed" not in web.layout("t", "", "day", "n", _job("Syncing failed", ok=False, age=901))
    restarting = _job("Updated to 9.9.9. Restarting…", age=600)
    assert 'id="restarting"' in web.layout("t", "", "day", "n", restarting)  # never hidden while restarting


def test_dismiss_clears_the_last_result(ustate, repo, monkeypatch):
    cfg = make_cfg(ustate, repo)
    monkeypatch.setattr(web, "_try_config", lambda: cfg)
    app = web.App(1)
    app.jobs.start("Syncing", lambda: "Synced: 3 records")
    for _ in range(100):
        if not app.jobs.snapshot()["running"]:
            break
        time.sleep(0.02)
    assert app.jobs.snapshot()["message"] == "Synced: 3 records"
    app.post("/jobs/dismiss", {})
    assert app.jobs.snapshot()["message"] == ""


def test_version_on_disk_reads_the_installed_files():
    assert updates.version_on_disk() == __version__


def test_restart_banner_after_a_terminal_upgrade(ustate, repo, monkeypatch):
    cfg = make_cfg(ustate, repo)
    monkeypatch.setattr(web, "_try_config", lambda: cfg)
    app = web.App(1)
    assert web._restart_html(app) == ""  # same version on disk: nothing to do
    monkeypatch.setattr(updates, "version_on_disk", lambda: "9.9.9")
    html = web._restart_html(app)
    assert "Worklog 9.9.9 is installed" in html and f"still runs {__version__}" in html and 'action="/restart"' in html
    restarted = []
    monkeypatch.setattr(app, "restart", lambda: restarted.append(True))
    monkeypatch.setattr(web.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda s: fn()})())
    loc, err, _ = app.post("/restart", {})
    assert err is None and loc == "/?ok=job"
    for _ in range(100):
        if not app.jobs.snapshot()["running"]:
            break
        time.sleep(0.02)
    assert app.jobs.snapshot()["message"] == "Updated to 9.9.9. Restarting…" and restarted == [True]


def test_script_sent_forms_get_answers_in_place(ustate, repo):
    app = web.App(1)
    assert web._fetch_answer(app, "/settings/watch", None, "Bad value") == {"ok": False, "error": "Bad value"}
    assert web._fetch_answer(app, "/settings/watch", "/settings?ok=saved", None) == {
        "ok": True, "message": web.OK_MESSAGES["saved"]}
    assert web._fetch_answer(app, "/sync/test", "/settings?ok=job#data-repo", None)["job"]  # follow the job
    app.jobs.start("Syncing", lambda: time.sleep(0.3) or "done")
    busy = web._fetch_answer(app, "/sync/test", "/settings#data-repo", None)  # couldn't start: something else runs
    assert busy["ok"] is False and "Syncing" in busy["error"]
