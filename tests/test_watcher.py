"""The account watcher: sessions get the right account even without the hook.

Every test runs in a temporary HOME (fixtures from test_accounts), so nothing touches your
real ~/.claude, ~/.claude.json, config or state.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_accounts import EMAIL_A, EMAIL_B, UID_A, UID_B, login, make_cfg, records, transcript

from worklog import collector, identity, watcher, web
from worklog.config import load_config

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def wstate(home, monkeypatch):
    """Point the watcher's state file into the throwaway HOME."""
    path = home / ".local/state/claude-worklog/watch.json"
    monkeypatch.setattr(watcher, "STATE_DIR", path.parent)
    monkeypatch.setattr(watcher, "state_path", lambda: path)
    return path


def run(w: watcher.Watcher, at: datetime) -> None:
    w._last_discover = float("inf")  # no folder discovery unless a test asks for it
    w.step(now=at)


def keep_running(w: watcher.Watcher, start: datetime, end: datetime, every: int = 30) -> None:
    """Ticks as the background service does (every few seconds; 30 s keeps tests fast)."""
    t = start
    while t <= end:
        run(w, t)
        t += timedelta(seconds=every)


def test_timeline_follows_logins_and_skips_half_written_files(home, repo, wstate):
    make_cfg(home, repo)
    w = watcher.Watcher(wstate)
    login(home, UID_A, EMAIL_A)
    run(w, T0)
    (home / ".claude.json").write_text('{"oauthAccount": {"accountUuid": "')  # a tool is mid-write
    run(w, T0 + timedelta(seconds=3))
    keep_running(w, T0 + timedelta(seconds=6), T0 + timedelta(seconds=30))
    login(home, UID_B, EMAIL_B)  # the one-click switch finishes
    keep_running(w, T0 + timedelta(seconds=36), T0 + timedelta(seconds=60))  # no change: no new entry
    tl = watcher.load_state(wstate)["timeline"]["~/.claude"]
    assert [e["id"] for e in tl] == [UID_A, UID_B]
    s = watcher.load_state(wstate)
    assert watcher.login_at(s, home / ".claude", T0 + timedelta(seconds=1)).account_id == UID_A
    assert watcher.login_at(s, home / ".claude", T0 + timedelta(seconds=55)).account_id == UID_B
    # the login also appears under "Logins seen on this device", without any hook
    assert {x["account_id"] for x in identity.detected_logins()} == {UID_A, UID_B}


def test_switch_right_at_a_session_start_is_not_guessed(home, wstate):
    w = watcher.Watcher(wstate)
    login(home, UID_A, EMAIL_A)
    keep_running(w, T0, T0 + timedelta(seconds=24), every=3)
    login(home, UID_B, EMAIL_B)
    keep_running(w, T0 + timedelta(seconds=27), T0 + timedelta(seconds=40), every=3)
    s = watcher.load_state(wstate)
    assert watcher.login_at(s, home / ".claude", T0 + timedelta(seconds=28)).status == "ambiguous"
    assert watcher.login_at(s, home / ".claude", T0 + timedelta(seconds=10)).status == "match"


def test_time_the_watcher_was_off_is_unknown(home, wstate):
    w = watcher.Watcher(wstate)
    login(home, UID_A, EMAIL_A)
    keep_running(w, T0, T0 + timedelta(seconds=30))
    w2 = watcher.Watcher(wstate)  # service restarted an hour later (laptop asleep)
    run(w2, T0 + timedelta(hours=1))
    s = watcher.load_state(wstate)
    assert len(s["coverage"]) == 2
    assert watcher.login_at(s, home / ".claude", T0 + timedelta(minutes=20)).status == "uncovered"
    assert watcher.login_at(s, home / ".claude", T0 - timedelta(minutes=5)).status == "uncovered"
    assert watcher.login_at(s, home / ".claude", T0 + timedelta(hours=1)).status == "match"


def test_logged_out_or_api_key_is_none(home, wstate):
    w = watcher.Watcher(wstate)
    (home / ".claude.json").write_text(json.dumps({"primaryApiKey": "not read"}))
    run(w, T0)
    m = watcher.login_at(watcher.load_state(wstate), home / ".claude", T0 + timedelta(seconds=5))
    assert m.status == "none" and m.account_id is None


def test_collect_uses_the_watcher_when_the_hook_is_missing(home, repo, wstate, monkeypatch):
    """A one-click account switcher: no /login, no hook, the right account anyway."""
    monkeypatch.setattr(collector, "watch_state", lambda: watcher.load_state(wstate))
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    w = watcher.Watcher(wstate)
    login(home, UID_A, EMAIL_A)
    keep_running(w, now - timedelta(minutes=10), now - timedelta(minutes=6))
    transcript(home, "s-personal", (now - timedelta(minutes=8)).astimezone(), home)
    login(home, UID_B, EMAIL_B)  # switched by some tool
    keep_running(w, now - timedelta(minutes=5), now)
    transcript(home, "s-work", (now - timedelta(minutes=3)).astimezone(), home)
    collector.collect(cfg, [now.astimezone().date()])
    recs = records(repo)
    d = now.astimezone().date().isoformat()
    assert recs[f"records/{d}/dev1/work/s-work.json"]["account_source"] == "watcher"
    assert recs[f"records/{d}/dev1/personal/s-personal.json"]["account_source"] == "watcher"


def test_hook_record_beats_the_watcher(home, repo, wstate, monkeypatch, capsys):
    from test_accounts import start_session
    monkeypatch.setattr(collector, "watch_state", lambda: watcher.load_state(wstate))
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    w = watcher.Watcher(wstate)
    login(home, UID_A, EMAIL_A)
    run(w, now - timedelta(minutes=10))
    login(home, UID_B, EMAIL_B)
    start_session(home, "s1")  # the hook saw B at the start
    login(home, UID_A, EMAIL_A)  # switched back before the watcher looked
    keep_running(w, now - timedelta(minutes=9), now)
    transcript(home, "s1", (now - timedelta(minutes=8)).astimezone(), home)
    collector.collect(cfg, [now.astimezone().date()])
    rec = records(repo)[f"records/{now.astimezone().date().isoformat()}/dev1/work/s1.json"]
    assert rec["account_source"] == "hook"


def test_unknown_session_in_a_shared_folder_is_marked_default(home, repo, wstate, monkeypatch):
    monkeypatch.setattr(collector, "watch_state", lambda: watcher.load_state(wstate))
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')
    now = datetime.now().astimezone()
    transcript(home, "s-old", now, home)  # the watcher never ran
    collector.collect(cfg, [now.date()])
    rec = records(repo)[f"records/{now.date().isoformat()}/dev1/personal/s-old.json"]
    assert rec["account_source"] == "default"
    single = make_cfg(home, repo)  # one account in the folder: certain, not a guess
    transcript(home, "s-solo", now, home)
    collector.collect(single, [now.date()])
    assert records(repo)[f"records/{now.date().isoformat()}/dev1/personal/s-solo.json"]["account_source"] == "folder"


def test_discovery_finds_new_folders_and_skips_noise(home, repo):
    known = {home / ".claude"}
    (home / ".claude-client" / "projects" / "-x").mkdir(parents=True)
    (home / ".claude-client" / "projects" / "-x" / "s.jsonl").write_text("{}\n")
    tool = home / ".config" / "switcher" / "claude-profiles" / "acct2"
    tool.mkdir(parents=True)
    (home / ".config" / "switcher" / "claude-profiles" / ".claude.json").write_text("{}")
    (home / ".claude-empty").mkdir()  # no sessions, no account file: ignored
    nm = home / "code" / "node_modules" / "claude-sdk"
    nm.mkdir(parents=True)
    (nm / ".claude.json").write_text("{}")
    found, complete = watcher.discover(known, {repo}, home=home, with_processes=False)
    assert complete
    assert home / ".claude-client" in found
    assert home / ".config" / "switcher" / "claude-profiles" in found
    assert home / ".claude-empty" not in found and nm not in found
    assert not any(home / ".claude" in f.parents for f in found)  # never inside a known folder


def test_discovery_is_time_boxed(home):
    for i in range(300):
        (home / f"d{i}" / "a" / "b").mkdir(parents=True)
    found, complete = watcher.discover(set(), set(), home=home, budget=0.0, with_processes=False)
    assert complete is False  # stopped early, and said so


def test_notices_and_dismiss(home, repo, wstate, monkeypatch):
    cfg = make_cfg(home, repo)
    identity.record_session("a", home / ".claude", identity.Identity(UID_A, EMAIL_A), "start")
    identity.record_session("b", home / ".claude", identity.Identity(UID_B, EMAIL_B), "start")
    state = watcher.load_state(wstate)
    state["folders"] = ["~/.claude-client"]
    state["hooked"] = ["~/.claude"]  # the hook was installed once and is gone now
    watcher.save_state(state, wstate)
    kinds = sorted(n["kind"] for n in watcher.notices(cfg))
    assert kinds == ["folder", "hook", "login", "login"]
    text = " ".join(n["text"] for n in watcher.notices(cfg))
    assert EMAIL_B not in text and "b***@example.org" in text  # masked, never the full e-mail
    watcher.dismiss("folder:~/.claude-client", wstate)
    assert "folder" not in [n["kind"] for n in watcher.notices(cfg)]


def test_auto_setup_adds_folder_and_hook(home, repo, wstate, monkeypatch):
    cfg = make_cfg(home, repo)
    cfg.auto_setup = True
    cfg.path.write_text(__import__("worklog.config", fromlist=["x"]).dump_config(cfg))
    monkeypatch.setattr("worklog.config.load_config", lambda path=cfg.path: load_config(cfg.path))
    (home / ".claude-client" / "projects" / "-x").mkdir(parents=True)
    (home / ".claude-client" / "projects" / "-x" / "s.jsonl").write_text("{}\n")
    w = watcher.Watcher(wstate)
    w._last_discover = float("-inf")
    w.step(now=T0)
    new = load_config(cfg.path)
    assert new.account("client") and new.account("client").config_dir == home / ".claude-client"
    assert (home / ".claude-client" / "settings.json").exists()
    notes = [n["text"] for n in watcher.notices(new) if n["kind"] == "note"]
    assert any("Added ~/.claude-client as account client" in t for t in notes)


def test_add_folder_from_a_notice(home, repo, wstate, monkeypatch):
    cfg = make_cfg(home, repo)
    from worklog.config import save_config
    monkeypatch.setattr(web, "save_config", lambda c: save_config(c, c.path))
    (home / ".claude-client" / "projects" / "-x").mkdir(parents=True)
    (home / ".claude-client" / "projects" / "-x" / "s.jsonl").write_text("{}\n")
    app = web.App(8765)
    loc, err, _ = app._account_add_folder(cfg, {"folder": "~/.claude-client"})
    assert err is None and load_config(cfg.path).account("client")
    assert (home / ".claude-client" / "settings.json").exists()
    assert app._account_add_folder(cfg, {"folder": "/etc"})[1]  # outside home: refused


def test_auto_names_are_safe_and_unique():
    from pathlib import Path
    assert watcher.auto_name(Path("/h/.claude-client"), set()) == "client"
    assert watcher.auto_name(Path("/h/.claude-client"), {"client"}) == "client-2"
    assert watcher.auto_name(Path("/h/My Claude!"), set()) == "My-Claude"
    assert watcher.auto_name(Path("/h/.claude"), set()) == "account"
