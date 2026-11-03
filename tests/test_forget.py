"""Forget a login: deletes exactly its Worklog history on this device, after a confirmation."""
import json
from datetime import datetime, timedelta

import pytest
from test_accounts import (
    EMAIL_A,
    EMAIL_B,
    UID_A,
    UID_B,
    login,
    make_cfg,
    record_for,
    records,
    start_session,
    transcript,
)

from worklog import collector, forget, identity, watcher, web
from worklog.config import load_config, save_config

UID_C, EMAIL_C = "33333333-cccc-4ccc-8ccc-333333333333", "stranger@viitor.example"


@pytest.fixture
def isolated(home, monkeypatch):
    wpath = home / ".local/state/claude-worklog/watch.json"
    monkeypatch.setattr(watcher, "state_path", lambda: wpath)
    monkeypatch.setattr(collector, "watch_state", lambda: watcher.load_state(wpath))
    return home


def setup_user_case(home, repo):
    """Like the reported setup: three logins in ~/.claude, two named, one ("stranger") without a name."""
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "laracopilot"\nconfig_dir = "~/.claude"\n'
                               f'match_account_id = "{UID_B}"\n')
    cfg.accounts[0] = cfg.accounts[0].__class__("personal", cfg.accounts[0].config_dir, match_account_id=UID_A)
    cfg.accounts.reverse()  # laracopilot first: it is the folder default for logins without a name
    save_config(cfg, cfg.path)
    cfg = load_config(cfg.path)
    now = datetime.now().astimezone().replace(microsecond=0)
    for uid, email, sid, days_ago in ((UID_A, EMAIL_A, "s-personal", 0), (UID_B, EMAIL_B, "s-lara", 0),
                                      (UID_C, EMAIL_C, "s-stranger-old", 6), (UID_C, EMAIL_C, "s-stranger", 0)):
        login(home, uid, email)
        start_session(home, sid)
        transcript(home, sid, now - timedelta(days=days_ago), home)
    collector.collect(cfg, [now.date() - timedelta(days=6), now.date()])
    return cfg, now


def paths(repo):
    return sorted(records(repo))


def test_forget_an_unnamed_login_deletes_only_its_sessions(isolated, repo):
    cfg, now = setup_user_case(isolated, repo)
    before = paths(repo)
    assert len(before) == 4 and sum("s-stranger" in p for p in before) == 2
    p = forget.plan(cfg, identity.ref(UID_C))
    assert p.sessions == {"s-stranger", "s-stranger-old"} and len(p.files) == 2 and len(p.days) == 2
    assert p.account is None  # it was never named: no account goes away

    msg = forget.apply(load_config(cfg.path), p)
    assert "deleted 2 session record(s) from 2 day(s)" in msg
    after = paths(repo)
    assert not any("s-stranger" in x for x in after) and len(after) == 2
    assert {x.split("/")[3] for x in after} == {"personal", "laracopilot"}
    assert UID_C not in {x["account_id"] for x in identity.detected_logins()}

    # a later sync never writes the forgotten sessions again
    collector.collect(load_config(cfg.path), [now.date() - timedelta(days=6), now.date()])
    assert not any("s-stranger" in x for x in paths(repo))


def test_forget_a_named_login_removes_its_account_and_rehomes_the_rest(isolated, repo):
    cfg, now = setup_user_case(isolated, repo)
    cfg.summarizer_account = "laracopilot"
    save_config(cfg, cfg.path)
    p = forget.plan(load_config(cfg.path), identity.ref(UID_B))
    assert p.account == "laracopilot" and p.sessions == {"s-lara"}
    msg = forget.apply(load_config(cfg.path), p)
    new = load_config(cfg.path)
    assert new.account("laracopilot") is None and new.summarizer_account == "personal"
    assert "removed account laracopilot" in msg and "moved 2 other record(s)" in msg
    after = paths(repo)
    assert not any("s-lara" in x for x in after)
    # the stranger's sessions had fallen back to laracopilot: now with the folder's new default, not orphaned
    assert all("/laracopilot/" not in x for x in after)
    assert sum("/personal/s-stranger" in x for x in after) == 2
    assert records(repo)[next(x for x in after if "s-stranger" in x)]["account"] == "personal"


def test_forget_the_only_account_keeps_it_but_unlinks(isolated, repo):
    cfg = make_cfg(isolated, repo, "")
    cfg.accounts[0] = cfg.accounts[0].__class__("solo", cfg.accounts[0].config_dir, match_account_id=UID_A)
    cfg.summarizer_account = "solo"
    save_config(cfg, cfg.path)
    cfg = load_config(cfg.path)
    login(isolated, UID_A, EMAIL_A)
    start_session(isolated, "s1")
    transcript(isolated, "s1", datetime.now().astimezone(), isolated)
    collector.collect(cfg, [datetime.now().date()])
    p = forget.plan(cfg, identity.ref(UID_A))
    assert p.keep_account
    forget.apply(cfg, p)
    new = load_config(cfg.path)
    assert [a.name for a in new.accounts] == ["solo"] and new.accounts[0].match_account_id is None
    assert paths(repo) == []


def test_forget_finds_sessions_the_watcher_matched(isolated, repo):
    cfg = make_cfg(isolated, repo,
                   f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')
    now = datetime.now().astimezone().replace(microsecond=0)
    w = watcher.Watcher(watcher.state_path())
    t = now - timedelta(minutes=20)
    login(isolated, UID_B, EMAIL_B)  # switched by some tool; no hook anywhere
    while t <= now:
        w._last_discover = float("inf")
        w.step(now=t)
        t += timedelta(seconds=30)
    transcript(isolated, "s-w", now - timedelta(minutes=10), isolated)
    collector.collect(cfg, [now.date() - timedelta(days=1), now.date()])  # just after midnight it's yesterday's
    rec = record_for(repo, "work", "s-w")
    assert rec["account_source"] == "watcher"
    p = forget.plan(cfg, identity.ref(UID_B))
    assert p.sessions == {"s-w"} and p.account == "work"


def test_forget_drops_the_saved_summaries_of_those_projects(isolated, repo):
    cfg, now = setup_user_case(isolated, repo)
    d = now.date().isoformat()
    rep = repo / "reports" / d
    rep.mkdir(parents=True)
    (rep / "dev1.json").write_text(json.dumps({"summaries": {"misc:R&D / misc": {"bullets": ["- secret work"]},
                                                             "other": {"bullets": ["- keep me"]}}}))
    (rep / "dev1.md").write_text("- secret work\n")
    forget.apply(load_config(cfg.path), forget.plan(cfg, identity.ref(UID_C)))
    meta = json.loads((rep / "dev1.json").read_text())
    assert "misc:R&D / misc" not in meta["summaries"] and "other" in meta["summaries"]
    assert not (rep / "dev1.md").exists()


def test_the_same_account_logging_in_again_is_tracked_again(isolated, repo):
    cfg, now = setup_user_case(isolated, repo)
    forget.apply(load_config(cfg.path), forget.plan(cfg, identity.ref(UID_C)))
    login(isolated, UID_C, EMAIL_C)
    start_session(isolated, "s-stranger-new")
    transcript(isolated, "s-stranger-new", now, isolated)
    collector.collect(load_config(cfg.path), [now.date()])
    assert any("s-stranger-new" in x for x in paths(repo))
    assert UID_C in {x["account_id"] for x in identity.detected_logins()}


def test_ui_asks_first_and_explains_what_is_lost(isolated, repo, monkeypatch):
    cfg, _ = setup_user_case(isolated, repo)
    monkeypatch.setattr(web, "_try_config", lambda: load_config(cfg.path))
    app = web.App(8765)
    ref = identity.ref(UID_C)
    settings = web.page_settings(app, cfg)
    assert f'href="/settings?forget={ref}#forget"' in settings and "Forget…" in settings

    card = web.page_settings(app, cfg, ref)
    assert "Forget s***@viitor.example?" in card and "2 Claude Code session records" in card
    assert "can&#39;t be undone" in card and "Claude Code&#39;s own files" in card and 'required' in card
    assert EMAIL_C not in card and UID_C not in card  # masked, as everywhere

    n = len(paths(repo))
    loc, err, _ = app.post("/accounts/forget", {"ref": ref})  # the box isn't ticked: nothing happens
    assert err and len(paths(repo)) == n
    loc, err, _ = app.post("/accounts/forget", {"ref": "000000000000", "confirm": "1"})
    assert err and len(paths(repo)) == n
    loc, err, _ = app.post("/accounts/forget", {"ref": ref, "confirm": "1"})
    assert err is None and loc.startswith("/settings?ok=job")
    import time
    for _ in range(200):
        if not app.jobs.snapshot()["running"]:
            break
        time.sleep(0.02)
    assert app.jobs.snapshot()["ok"], app.jobs.snapshot()["message"]
    assert not any("s-stranger" in x for x in paths(repo))
