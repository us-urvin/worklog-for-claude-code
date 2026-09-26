"""Several Claude accounts sharing one config folder.

Every test runs in a temporary HOME with its own state dir and data repo, so nothing
touches your real ~/.claude, ~/.claude.json, config, state or ~/claude-worklog-data.
"""
import io
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from worklog import cli, collector, identity, store, web
from worklog.config import Account, Config, ConfigError, dump_config, load_config
from worklog.hooks import install_hook, installed_events

UID_A, EMAIL_A = "11111111-aaaa-4aaa-8aaa-111111111111", "alice.personal@example.com"
UID_B, EMAIL_B = "22222222-bbbb-4bbb-8bbb-222222222222", "bob.work@example.org"


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway HOME; state and logs are redirected into it too."""
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    for var, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"),
                     ("XDG_DATA_HOME", ".local/share")):
        monkeypatch.setenv(var, str(h / sub) if sub else str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    state = h / ".local/state/claude-worklog"
    for mod in (identity, store, cli):
        monkeypatch.setattr(mod, "STATE_DIR", state)
    assert Path.home() == h  # guard: never run against the real home
    return h


def login(home: Path, uid: str | None, email: str | None, path: Path | None = None) -> None:
    """Simulate `/login`: write oauthAccount (no credentials) into the fake ~/.claude.json."""
    path = path or home / ".claude.json"
    data = {"numStartups": 3}
    if uid:
        data["oauthAccount"] = {"accountUuid": uid, "emailAddress": email, "displayName": "x"}
    path.write_text(json.dumps(data))


def transcript(home: Path, sid: str, ts: datetime, cwd: Path, tokens: int = 100) -> None:
    iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    d = home / ".claude" / "projects" / "-work"
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "uuid": f"{sid}-u", "sessionId": sid, "timestamp": iso, "cwd": str(cwd),
         "message": {"role": "user", "content": f"prompt for {sid}"}},
        {"type": "assistant", "uuid": f"{sid}-a", "sessionId": sid, "timestamp": iso, "requestId": f"r-{sid}",
         "message": {"id": f"m-{sid}", "model": "claude-x", "content": [],
                     "usage": {"input_tokens": tokens, "output_tokens": 1}}},
    ]
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


@pytest.fixture
def repo(home):
    r = home / "data"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def make_cfg(home: Path, repo: Path, extra: str = "") -> Config:
    p = home / ".config/claude-worklog/config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'device_name = "dev1"\nstore_repo = "{repo}"\n'
                 f'[[accounts]]\nname = "personal"\nconfig_dir = "~/.claude"\n{extra}\n'
                 '[report]\nuse_ai = false\n')
    return load_config(p)


def records(repo: Path) -> dict[str, dict]:
    return {str(p.relative_to(repo)): json.loads(p.read_text()) for p in sorted(repo.rglob("records/**/*.json"))}


def start_session(home: Path, sid: str, capsys=None) -> None:
    """Run the real hook entry point as Claude Code would at SessionStart."""
    payload = json.dumps({"session_id": sid, "hook_event_name": "SessionStart", "source": "startup",
                          "cwd": str(home), "permission_mode": "default"})
    old = os.sys.stdin
    try:
        os.sys.stdin = io.StringIO(payload)
        assert cli.main(["hook"]) == 0
    finally:
        os.sys.stdin = old
        root = logging.getLogger()
        for h in root.handlers[:]:  # _setup_logging adds handlers on every call
            root.removeHandler(h)
            h.close()


# ------------------------------------------------------------------ identity detection
def test_read_identity_from_fake_claude_json(home):
    login(home, UID_A, EMAIL_A)
    path = identity.identity_path(home / ".claude")
    assert path == home / ".claude.json"
    assert identity.read_identity(path) == identity.Identity(UID_A, EMAIL_A)
    # a custom folder keeps its own file inside it
    assert identity.identity_path(home / ".claude-work") == home / ".claude-work" / ".claude.json"
    # CLAUDE_CONFIG_DIR=~/.claude set explicitly -> the file inside it
    assert identity.identity_path(home / ".claude", explicit=True) == home / ".claude" / ".claude.json"


@pytest.mark.parametrize("content", ['{"oauthAccount": null}', "{broken", "[]",
                                     '{"oauthAccount": {"emailAddress": "x@y.z"}}', ""])
def test_read_identity_logged_out_or_damaged(home, content, caplog):
    (home / ".claude.json").write_text(content)
    assert identity.read_identity(home / ".claude.json") is None
    assert identity.read_identity(home / "missing.json") is None
    assert "x@y.z" not in caplog.text


def test_mask_email_and_ref():
    assert identity.mask_email("alice@gmail.com") == "a***@gmail.com"
    assert identity.mask_email(None) == "(no e-mail)"
    r = identity.ref(UID_A)
    assert len(r) == 12 and UID_A not in r


# ------------------------------------------------------------------ session mapping
def test_session_start_hook_records_account_privately(home, capsys):
    login(home, UID_A, EMAIL_A)
    start_session(home, "sess-1")
    assert capsys.readouterr().out == ""  # SessionStart stdout would be injected into Claude's context
    state_file = home / ".local/state/claude-worklog/accounts.json"
    assert (state_file.stat().st_mode & 0o777) == 0o600
    assert identity.session_logins()["sess-1"] == identity.Identity(UID_A, EMAIL_A)
    log_text = (home / ".local/state/claude-worklog/worklog.log").read_text()
    assert EMAIL_A not in log_text and UID_A not in log_text


def test_first_start_wins_and_end_is_only_a_fallback(home):
    a, b = identity.Identity(UID_A, EMAIL_A), identity.Identity(UID_B, EMAIL_B)
    identity.record_session("s1", home / ".claude", a, "start")
    identity.record_session("s1", home / ".claude", b, "start")  # e.g. resume after switching
    identity.record_session("s1", home / ".claude", b, "end")
    identity.record_session("s2", home / ".claude", b, "end")    # started before the hook existed
    got = identity.session_logins()
    assert got["s1"].account_id == UID_A and got["s2"].account_id == UID_B
    assert {x["email"] for x in identity.detected_logins()} == {EMAIL_A, EMAIL_B}


def test_hook_never_fails(home, monkeypatch):
    monkeypatch.setattr(identity, "record_session", lambda *a, **k: 1 / 0)
    login(home, UID_A, EMAIL_A)
    start_session(home, "boom")  # asserts exit code 0
    start_session(home, "")      # no session id
    old = os.sys.stdin
    try:
        os.sys.stdin = io.StringIO("not json")
        assert cli.main(["hook"]) == 0
    finally:
        os.sys.stdin = old


def test_old_state_entries_are_pruned(home):
    old = datetime.now().astimezone() - timedelta(days=identity.KEEP_DAYS + 5)
    identity.record_session("ancient", home / ".claude", identity.Identity(UID_A, None), "start", now=old)
    identity.record_session("fresh", home / ".claude", identity.Identity(UID_A, None), "start")
    assert set(identity.session_logins()) == {"fresh"}


# ------------------------------------------------------------------ config
def test_config_backward_compatible_and_roundtrip(home, repo):
    cfg = make_cfg(home, repo)  # the 0.1.0 format, no match_* keys
    assert cfg.resolve_account(home / ".claude", UID_B, EMAIL_B).name == "personal"
    cfg = make_cfg(home, repo,
                   f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n'
                   '[[accounts]]\nname = "club"\nconfig_dir = "~/.claude"\nmatch_email = "Carol@Example.com"\n')
    d = home / ".claude"
    assert cfg.default_account(d).name == "personal"
    assert cfg.resolve_account(d, UID_B, None).name == "work"
    assert cfg.resolve_account(d, "other", "carol@example.com").name == "club"  # e-mail match ignores case
    assert cfg.resolve_account(d, UID_A, EMAIL_A).name == "personal"
    p = home / "roundtrip.toml"
    p.write_text(dump_config(cfg))
    assert load_config(p).accounts == cfg.accounts


def test_config_rejects_ambiguous_shared_folder(home, repo):
    with pytest.raises(ConfigError, match="share"):
        make_cfg(home, repo, '[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\n')
    with pytest.raises(ConfigError, match="same login"):
        make_cfg(home, repo, f'[[accounts]]\nname = "a"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n'
                             f'[[accounts]]\nname = "b"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')


# ------------------------------------------------------------------ collecting
def test_switching_accounts_mid_day(home, repo, capsys):
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')
    now = datetime.now().astimezone().replace(microsecond=0)
    login(home, UID_A, EMAIL_A)
    start_session(home, "morning")
    transcript(home, "morning", now, home, tokens=100)
    login(home, UID_B, EMAIL_B)  # /logout, /login with the work account
    start_session(home, "afternoon")
    transcript(home, "afternoon", now, home, tokens=900)
    transcript(home, "legacy", now, home, tokens=5)  # no hook data: falls back to the folder default

    assert collector.collect(cfg, [now.date()]) == 3
    recs = records(repo)
    day = now.date().isoformat()
    assert recs[f"records/{day}/dev1/personal/morning.json"]["account"] == "personal"
    assert recs[f"records/{day}/dev1/work/afternoon.json"]["account"] == "work"
    assert recs[f"records/{day}/dev1/personal/legacy.json"]["tokens"]["input"] == 5
    assert len(recs) == 3


def test_no_email_or_account_id_in_generated_data(home, repo, capsys):
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_email = "{EMAIL_B}"\n')
    now = datetime.now().astimezone()
    login(home, UID_B, EMAIL_B)
    start_session(home, "s1")
    transcript(home, "s1", now, home)
    collector.collect(cfg, [now.date()])
    from worklog.report import build_report
    build_report(cfg, now.date(), use_ai=False, write=True)
    blob = "".join(p.read_text() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts)
    blob += subprocess.run(["git", "-C", str(repo), "log", "-p"], capture_output=True, text=True).stdout
    assert "work" in blob
    for secret in (EMAIL_B, UID_B, "bob.work", "example.org"):
        assert secret not in blob


def test_naming_a_login_later_moves_its_records(home, repo, capsys):
    day = datetime.now().astimezone()
    d = day.date().isoformat()
    # A record from before accounts were tracked, which must stay untouched.
    old_rel = f"records/{d}/dev1/personal/untracked.json"
    (repo / old_rel).parent.mkdir(parents=True)
    (repo / old_rel).write_text('{"account": "personal", "session_id": "untracked"}\n')

    cfg = make_cfg(home, repo)
    login(home, UID_B, EMAIL_B)
    start_session(home, "s-work")
    transcript(home, "s-work", day, home)
    collector.collect(cfg, [day.date()])
    assert f"records/{d}/dev1/personal/s-work.json" in records(repo)  # unnamed login -> default

    # The user names that login "work" in the UI.
    cfg = make_cfg(home, repo, f'[[accounts]]\nname = "work"\nconfig_dir = "~/.claude"\nmatch_account_id = "{UID_B}"\n')
    collector.collect(cfg, [day.date()])
    recs = records(repo)
    assert f"records/{d}/dev1/work/s-work.json" in recs
    assert f"records/{d}/dev1/personal/s-work.json" not in recs  # moved, not duplicated
    assert recs[old_rel] == {"account": "personal", "session_id": "untracked"}


# ------------------------------------------------------------------ hooks install
def test_install_hook_adds_both_events_idempotently(home):
    acct = Account("personal", home / ".claude")
    settings = home / ".claude/settings.json"
    other = {"type": "command", "command": "some-other-tool"}
    settings.write_text(json.dumps({"model": "x", "hooks": {
        "SessionStart": [{"hooks": [other]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": "/usr/bin/claude-worklog hook"}]}]}}))
    assert installed_events(acct) == {"SessionEnd"}
    ok, msg = install_hook(acct)
    assert ok and "SessionStart" in msg
    data = json.loads(settings.read_text())
    assert data["model"] == "x" and data["hooks"]["SessionStart"][0]["hooks"] == [other]
    assert installed_events(acct) == {"SessionStart", "SessionEnd"}
    assert len(list(settings.parent.glob("settings.json.bak-*"))) == 1
    before = settings.read_text()
    assert install_hook(acct) == (True, "personal: hooks already installed")
    assert settings.read_text() == before


# ------------------------------------------------------------------ web UI
def test_detected_accounts_panel_masks_and_names(home, repo, monkeypatch):
    cfg = make_cfg(home, repo)
    identity.record_session("s", home / ".claude", identity.Identity(UID_B, "<b>" + EMAIL_B), "start")
    html_out = web._detected_section(cfg, '<input type="hidden" name="csrf" value="t">')
    assert EMAIL_B not in html_out and UID_B not in html_out and "<b><" not in html_out
    assert "&lt;***@example.org" in html_out and "folder default" in html_out

    saved = []
    monkeypatch.setattr(web, "save_config", lambda c: saved.append(c))
    app = web.App(8765)
    ref = identity.ref(UID_B)
    loc, err, _ = app._account_name(cfg, {"ref": ref, "dir": "~/.claude", "name": "work"})
    assert err is None and loc == "/settings?ok=named"
    work = saved[-1].account("work")
    assert work.match_account_id == UID_B and work.config_dir == home / ".claude"
    # bad input is rejected without saving
    for form in ({"ref": "nope", "dir": "~/.claude", "name": "x"},
                 {"ref": ref, "dir": "~/elsewhere", "name": "x"},
                 {"ref": ref, "dir": "~/.claude", "name": "bad name!"}):
        n = len(saved)
        assert app._account_name(cfg, form)[1] and len(saved) == n
