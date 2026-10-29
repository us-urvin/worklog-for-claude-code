"""The setup checklist: right checks, steps for each OS, shown until done, then only what breaks."""
import pytest
from test_accounts import make_cfg

from worklog import checks, services, web
from worklog.hooks import install_hook


@pytest.fixture
def ready(home, repo, monkeypatch):
    """A device where every required check passes."""
    monkeypatch.setattr(checks.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(services, "autostart_ok", lambda: True)
    monkeypatch.setattr(checks, "_ui_state_path", lambda: home / ".local/state/claude-worklog/ui.json")
    checks.forget_cache()
    cfg = make_cfg(home, repo)
    install_hook(cfg.accounts[0])
    return cfg


def by_id(items):
    return {x.id: x for x in items}


def test_all_required_pass_on_a_ready_device(ready):
    items = by_id(checks.run(ready, kind="linux"))
    assert all(x.ok for x in items.values() if x.required)
    assert {"claude", "used", "git", "data", "hook:personal", "autostart"} <= set(items)
    assert items["model"].ok  # summaries are off on this device, so the model doesn't matter
    ready.use_ai = True
    assert not by_id(checks.run(ready, kind="linux"))["model"].ok  # on, but still the account default


@pytest.mark.parametrize("kind,expect", [("mac", "xcode-select --install"),
                                         ("windows", "winget install --id Git.Git -e"),
                                         ("linux", "sudo apt update && sudo apt install -y git"),
                                         ("wsl", "sudo apt update && sudo apt install -y git")])
def test_git_steps_match_the_os(ready, monkeypatch, kind, expect):
    real = checks.shutil.which
    monkeypatch.setattr(checks.shutil, "which", lambda n: None if n == "git" else real(n))
    git = by_id(checks.run(ready, kind=kind))["git"]
    assert not git.ok and any(cmd == expect for _, cmd in git.steps)


def test_wsl_explains_systemd(ready, monkeypatch):
    monkeypatch.setattr(services, "autostart_ok", lambda: False)
    auto = by_id(checks.run(ready, kind="wsl"))["autostart"]
    assert not auto.ok and any("systemd=true" in cmd for _, cmd in auto.steps)


def test_watcher_counts_as_tracking_when_the_hook_is_missing(home, repo, monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(services, "autostart_ok", lambda: True)
    cfg = make_cfg(home, repo)
    assert not by_id(checks.run(cfg))["hook:personal"].ok
    assert by_id(checks.run(cfg, watcher_running=True))["hook:personal"].ok
    assert by_id(checks.run(cfg))["hook:personal"].action == "install_hook:personal"


def test_first_run_shows_only_the_basics(home, monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda n: None)
    items = checks.run(None, kind="mac")
    assert [x.id for x in items] == ["claude", "used", "git"]
    assert any(cmd == "xcode-select --install" for _, cmd in items[2].steps)
    html = web.page_setup(web.App(1))
    assert "Before you start" in html and "Set up this device" in html


def test_card_until_done_then_only_a_slim_warning(ready, monkeypatch):
    app = web.App(1)
    monkeypatch.setattr(services, "autostart_ok", lambda: False)  # one required step open
    checks.forget_cache()
    first = web.top_panels(app, ready, "day")
    assert "Get Worklog ready" in first and "class=\"checklist\"" in first and 'class="quiet copy"' in first
    assert "Get Worklog ready" not in web.top_panels(app, ready, "settings")  # Settings has its own copy

    monkeypatch.setattr(services, "autostart_ok", lambda: True)  # the user fixed it
    checks.forget_cache()
    assert web.top_panels(app, ready, "day") == ""  # done: the card disappears for good
    assert checks.setup_done()

    monkeypatch.setattr(services, "autostart_ok", lambda: False)  # later something breaks
    checks.forget_cache()
    later = web.top_panels(app, ready, "day")
    assert "Get Worklog ready" not in later and "stopped working" in later and "Fix it" in later


def test_settings_always_has_the_full_checklist(ready, monkeypatch):
    html = web.page_settings(web.App(1), ready)
    assert 'id="checklist"' in html and "Setup checklist" in html and "Make it even better" in html


def test_demo_mode_skips_the_checklist(ready, monkeypatch):
    monkeypatch.setattr(services, "demo_mode", lambda: True)
    monkeypatch.setattr(services, "autostart_ok", lambda: False)
    checks.forget_cache()
    assert "Get Worklog ready" not in web.top_panels(web.App(1), ready, "day")


def test_commands_are_escaped(ready, monkeypatch):
    item = checks.Check("x", "<b>label</b>", False, True, "why <i>", [("text <u>", 'echo "<script>"')])
    html = web._check_item(web.App(1), item, True)
    assert "<script>" not in html and "&lt;script&gt;" in html and "<b>label" not in html
