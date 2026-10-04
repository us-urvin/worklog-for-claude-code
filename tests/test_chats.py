"""claude.ai chat export import. Every export here is synthetic, built in code."""
import http.client
import json
import logging
import os
import socket
import subprocess
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from worklog import chats, cli, identity, store, web
from worklog.config import ConfigError, dump_config, load_config
from worklog.report import aggregate, render

ACCT = "66666666-aaaa-4aaa-8aaa-666666666666"
SECRET_REPLY = "ASSISTANT-REPLY-should-never-be-stored"
SECRET_SUMMARY = "CHAT-SUMMARY-should-never-be-stored"
SECRET_ATTACHMENT = "ATTACHMENT-CONTENT-should-never-be-stored"
SECRET_THINKING = "THINKING-should-never-be-stored"
SECRET_TOOL_INPUT = "TOOL-INPUT-should-never-be-stored"


# ------------------------------------------------------------------ helpers
def ts(d: date, h: int, m: int = 0) -> str:
    """A local wall-clock time as the export writes it (UTC with Z)."""
    local = datetime(d.year, d.month, d.day, h, m).astimezone()
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def human(text: str, when: str, **kw) -> dict:
    return {"uuid": f"h-{when}-{len(text)}", "sender": "human", "text": text, "created_at": when,
            "content": [{"type": "text", "text": text}] if text else [], "attachments": [], "files": [],
            "parent_message_uuid": "p", **kw}


def assistant(when: str, tools: list[dict] | None = None) -> dict:
    content = [{"type": "thinking", "thinking": SECRET_THINKING},
               {"type": "text", "text": SECRET_REPLY}] + (tools or [])
    return {"uuid": f"a-{when}", "sender": "assistant", "text": SECRET_REPLY, "created_at": when,
            "content": content, "attachments": [], "files": [], "parent_message_uuid": "p"}


def tool(name: str, tid: str, **inp) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "integration_name": "Claude Code",
            "input": {"content": SECRET_TOOL_INPUT, **inp}}


def conv(uuid: str, msgs: list[dict], name: str = "Plan the launch", updated: str = "2026-09-21T10:00:00Z") -> dict:
    return {"uuid": uuid, "name": name, "summary": SECRET_SUMMARY, "created_at": msgs[0]["created_at"] if msgs else "",
            "updated_at": updated, "account": {"uuid": ACCT}, "chat_messages": msgs}


def write_zip(path: Path, convs: list[dict], member: str = "conversations.json") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, json.dumps(convs))
    return path


D = date(2026, 9, 20)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    for var, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"),
                     ("XDG_DATA_HOME", ".local/share")):
        monkeypatch.setenv(var, str(h / sub) if sub else str(h))
    state = h / ".local/state/claude-worklog"
    for mod in (identity, store, cli, chats):
        monkeypatch.setattr(mod, "STATE_DIR", state)
    assert Path.home() == h
    return h


@pytest.fixture
def cfg(home):
    repo = home / "data"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    p = home / ".config/claude-worklog/config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'device_name = "dev1"\nstore_repo = "{repo}"\n'
                 '[[accounts]]\nname = "personal"\nconfig_dir = "~/.claude"\n'
                 '[[accounts]]\nname = "work"\nconfig_dir = "~/.claude-work"\n'
                 '[collect]\nmax_prompt_chars = 40\n[report]\nuse_ai = false\n')
    return load_config(p)


def records(cfg) -> dict[str, dict]:
    return {str(p.relative_to(cfg.store_repo)): json.loads(p.read_text())
            for p in sorted(cfg.store_repo.rglob("records/**/*.json"))}


def commits(cfg) -> int:
    out = subprocess.run(["git", "-C", str(cfg.store_repo), "rev-list", "--count", "--all"],
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def basic_export() -> list[dict]:
    return [conv("c1", [human("add a login page", ts(D, 9)), assistant(ts(D, 9, 1))])]


# ------------------------------------------------------------------ reading
def test_reads_zip_json_and_folder_and_merges_parts(tmp_path):
    convs = basic_export()
    (tmp_path / "conversations.json").write_text(json.dumps(convs))
    assert chats.load_conversations(tmp_path / "conversations.json")[0][0]["uuid"] == "c1"
    z = write_zip(tmp_path / "conversations-000.zip", convs, member="export/conversations.json")
    assert chats.load_conversations(z)[0][0]["uuid"] == "c1"

    folder = tmp_path / "dl"
    folder.mkdir()
    old = conv("c1", [human("old", ts(D, 9))], updated="2026-09-20T00:00:00Z")
    new = conv("c1", [human("new", ts(D, 9))], updated="2026-09-22T00:00:00Z")
    write_zip(folder / "conversations-000.zip", [new, conv("c2", [human("x", ts(D, 10))])])
    write_zip(folder / "conversations-001.zip", [old])
    (folder / "memories-000.zip").write_bytes(b"ignored")
    got, bad, files = chats.load_conversations(folder)
    assert sorted(c["uuid"] for c in got) == ["c1", "c2"] and bad == 0 and len(files) == 2
    assert next(c for c in got if c["uuid"] == "c1")["chat_messages"][0]["text"] == "new"


@pytest.mark.parametrize("content, msg", [
    (json.dumps([{"id": "x", "mapping": {}}]), "ChatGPT"),
    (json.dumps({"conversations": []}), "expected a list"),
    ("{not json", "not valid JSON"),
    (json.dumps([{"foo": 1}]), "no chat_messages"),
])
def test_rejects_files_that_are_not_claude_exports(tmp_path, content, msg):
    p = tmp_path / "conversations.json"
    p.write_text(content)
    with pytest.raises(chats.ExportError, match=msg):
        chats.load_conversations(p)


def test_rejects_wrong_zip_and_oversized_member(tmp_path, monkeypatch):
    with pytest.raises(chats.ExportError, match="no conversations.json"):
        chats.load_conversations(write_zip(tmp_path / "memories-000.zip", [], member="memories.json"))
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(chats.ExportError, match="not a readable zip"):
        chats.load_conversations(tmp_path / "broken.zip")
    monkeypatch.setattr(chats, "MAX_JSON_BYTES", 100)
    with pytest.raises(chats.ExportError, match="larger than"):
        chats.load_conversations(write_zip(tmp_path / "big.zip", [conv("c", [human("x" * 500, ts(D, 9))])]))
    with pytest.raises(chats.ExportError, match="does not exist"):
        chats.load_conversations(tmp_path / "missing.zip")


def test_malformed_chats_and_messages_are_counted_not_fatal(tmp_path):
    good = conv("c1", [human("ok", ts(D, 9)), "junk", human("no time", "yesterday"), human("x", None)])
    p = tmp_path / "conversations.json"
    p.write_text(json.dumps([good, {"uuid": "c2"}, 7]))
    convs, bad, _ = chats.load_conversations(p)
    assert len(convs) == 1 and bad == 2
    days, bad_msgs = chats.split_days(convs[0])
    assert bad_msgs == 3 and days[D].prompts == ["ok"]


# ------------------------------------------------------------------ per-day rules
def test_only_real_prompts_are_kept_and_days_split_at_local_midnight():
    msgs = [
        human("first question", ts(D, 23, 50)),
        human("", ts(D, 23, 51)),  # tool-loop turn
        human("", ts(D, 23, 52), content=[{"type": "injected_prompt_block", "prompt": "system stuff"}]),
        human("first question", ts(D, 23, 55)),  # edited/retried branch repeats the prompt
        assistant(ts(D, 23, 56)),
        human("after midnight", ts(D + timedelta(days=1), 0, 10)),
    ]
    days, bad = chats.split_days(conv("c1", msgs))
    assert bad == 0 and set(days) == {D, D + timedelta(days=1)}
    assert days[D].prompts == ["first question"] and days[D].messages == 5
    assert days[D + timedelta(days=1)].prompts == ["after midnight"]
    since, _ = chats.split_days(conv("c1", msgs), since=D + timedelta(days=1))
    assert set(since) == {D + timedelta(days=1)}


def test_record_redacts_truncates_and_never_holds_claudes_text(cfg):
    key = "sk-ant-api03-" + "a" * 30
    msgs = [human(f"my key is {key} and this prompt is far longer than forty characters", ts(D, 9),
                  attachments=[{"file_name": "notes.txt", "extracted_content": SECRET_ATTACHMENT}]),
            assistant(ts(D, 9, 5), [tool("Write", "toolu_01A", file_path="/home/claude/app/src/main.py")])]
    days, _ = chats.split_days(conv("c1", msgs, name=f"Debug {key}"))
    rec = chats.build_record(days[D], "personal", cfg)
    blob = json.dumps(rec)
    for secret in (key, SECRET_REPLY, SECRET_SUMMARY, SECRET_ATTACHMENT, SECRET_THINKING, SECRET_TOOL_INPUT,
                   "toolu_01A", ACCT):
        assert secret not in blob
    assert rec["prompts"][0].startswith("my key is [REDACTED]") and len(rec["prompts"][0]) == 40
    assert rec["title"] == "Debug [REDACTED]"
    assert rec["source"] == "claude.ai" and rec["session_id"] == "chat-c1"
    assert rec["tokens"] == {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    assert rec["project"]["name"] == "app" and rec["files_touched"] == ["src/main.py"]
    assert rec["tool_calls"] == {"Write": 1}


def test_untitled_chat_and_assistant_only_day():
    days, _ = chats.split_days(conv("c1", [human("hi", ts(D, 9))], name=""))
    assert chats.build_record(days[D], "personal", load_cfg_stub())["title"] == "Untitled chat"
    days, _ = chats.split_days(conv("c2", [assistant(ts(D, 9))]))
    assert chats.build_record(days[D], "personal", load_cfg_stub()) is None


def load_cfg_stub():
    from worklog.config import Account, Config
    return Config(device="dev1", store_repo=Path("/nonexistent"), accounts=[Account("personal", Path("/x"))])


# ------------------------------------------------------------------ import
def test_import_is_idempotent_and_an_older_export_never_overwrites(cfg, tmp_path):
    newer = conv("c1", [human("one", ts(D, 9)), human("two", ts(D, 10))], updated="2026-09-22T00:00:00Z")
    res = chats.import_chats(cfg, write_zip(tmp_path / "conversations-000.zip", [newer]))
    assert (res.chats, res.days, res.written, res.account) == (1, 1, 1, "personal")
    path = f"records/{D.isoformat()}/dev1/personal/chat-c1.json"
    assert records(cfg)[path]["prompts"] == ["one", "two"] and commits(cfg) == 1

    again = chats.import_chats(cfg, tmp_path / "conversations-000.zip")
    assert again.written == 0 and commits(cfg) == 1

    older = conv("c1", [human("one", ts(D, 9))], updated="2026-09-21T00:00:00Z")
    res = chats.import_chats(cfg, write_zip(tmp_path / "old.zip", [older]))
    assert res.stale == 1 and res.written == 0 and records(cfg)[path]["prompts"] == ["one", "two"]
    assert chats.load_state()["last_import"]["chats"] == 1


def test_chats_that_are_local_claude_code_sessions_are_skipped(cfg, home, tmp_path):
    d = home / ".claude/projects/-work"
    d.mkdir(parents=True)
    (d / "s1.jsonl").write_text(json.dumps({"type": "assistant", "timestamp": ts(D, 9), "message": {
        "content": [{"type": "tool_use", "id": "toolu_01LOCAL", "name": "Bash", "input": {}}]}}) + "\n")
    os.utime(d / "s1.jsonl", (time.time(), time.time()))
    dup = conv("c1", [human("fix it", ts(D, 9)), assistant(ts(D, 9, 1), [tool("Bash", "toolu_01LOCAL")])])
    other = conv("c2", [human("other", ts(D, 9)), assistant(ts(D, 9, 1), [tool("Bash", "toolu_01CLOUD")])])
    res = chats.import_chats(cfg, write_zip(tmp_path / "c.zip", [dup, other]))
    assert res.duplicates == 1 and res.days == 1
    assert list(records(cfg)) == [f"records/{D.isoformat()}/dev1/personal/chat-c2.json"]


def test_account_choice_is_remembered_and_records_move(cfg, tmp_path):
    z = write_zip(tmp_path / "c.zip", basic_export())
    assert chats.import_chats(cfg, z).account == "personal"
    with pytest.raises(chats.ExportError, match="no account named"):
        chats.import_chats(cfg, z, account="nope")
    res = chats.import_chats(cfg, z, account="work")
    assert res.account == "work" and list(records(cfg)) == [f"records/{D.isoformat()}/dev1/work/chat-c1.json"]
    assert chats.import_chats(cfg, z).account == "work"  # remembered for this claude.ai login
    state = json.loads(chats.state_path().read_text())
    assert ACCT not in json.dumps(state) and oct(chats.state_path().stat().st_mode)[-3:] == "600"


def test_cli_import_chats(cfg, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    z = write_zip(tmp_path / "c.zip", basic_export())
    try:
        assert cli.main(["import-chats", str(z)]) == 0
        assert "Imported 1 chat(s)" in capsys.readouterr().out
        (tmp_path / "bad.json").write_text("[]x")
        assert cli.main(["import-chats", str(tmp_path / "bad.json")]) == 1
        assert "not valid JSON" in capsys.readouterr().out
    finally:
        root = logging.getLogger()
        for h in root.handlers[:]:  # cli._setup_logging adds handlers on every call
            root.removeHandler(h)
            h.close()


# ------------------------------------------------------------------ downloads
def test_downloads_scan_is_opt_in_and_skips_partial_recent_and_seen(cfg, home):
    dl = home / "Downloads"
    dl.mkdir()
    z = write_zip(dl / "conversations-000.zip", basic_export())
    old = time.time() - 60
    os.utime(z, (old, old))
    assert chats.scan_downloads(cfg) == []  # toggle is off: nothing is read

    cfg.watch_downloads = True
    (dl / "conversations-000.zip.crdownload").write_bytes(b"")
    assert chats.scan_downloads(cfg) == []  # browser still writing
    (dl / "conversations-000.zip.crdownload").unlink()
    fresh = write_zip(dl / "conversations-001.zip", [conv("c9", [human("new", ts(D, 9))])])
    assert chats.pending_downloads(cfg) == [z]  # fresh file has not settled yet
    results = chats.scan_downloads(cfg)
    assert len(results) == 1 and results[0].days == 1
    assert chats.scan_downloads(cfg) == []  # already imported
    assert z.exists() and fresh.exists()  # never moved or deleted

    bad = dl / "conversations-x.json"
    bad.write_text(json.dumps([{"mapping": {}}]))
    os.utime(bad, (old, old))
    os.utime(fresh, (old, old))
    results = chats.scan_downloads(cfg)
    assert len(results) == 1  # the bad file is skipped, the settled one imported
    assert chats.pending_downloads(cfg) == []  # and the bad one is not retried every sync


def test_downloads_dir_from_xdg_user_dirs(home):
    from worklog.config import default_downloads_dir
    assert default_downloads_dir() == home / "Downloads"
    (home / ".config").mkdir(exist_ok=True)
    (home / ".config/user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n')
    assert default_downloads_dir() == home / "Téléchargements"


# ------------------------------------------------------------------ report + config
def test_report_counts_chats_once_across_devices_without_tokens(cfg, tmp_path):
    chats.import_chats(cfg, write_zip(tmp_path / "c.zip", basic_export()))
    rec = next(iter(records(cfg).values()))
    copy = dict(rec, device="laptop")
    accounts, projects = aggregate([rec, copy])
    p = projects["chat:claude.ai chat"]
    assert p.chats == 1 and p.sessions == 0 and p.titles == ["Plan the launch"]
    text = render(D, cfg, accounts, projects, {})
    assert "1 claude.ai chat(s)" in text and "no token data" in text and "Token usage" not in text
    assert "- Plan the launch" in text

    body, err = web.page_day(web.App(8765), cfg, D)
    assert err is None and "1 claude.ai chat" in body and "no data from claude.ai" in body


def test_config_chat_section_roundtrip_and_validation(cfg):
    cfg.chat_account, cfg.watch_downloads, cfg.downloads_dir = "work", True, Path.home() / "dl"
    p = cfg.path.with_name("roundtrip.toml")
    p.write_text(dump_config(cfg))
    again = load_config(p)
    assert (again.chats_account, again.watch_downloads, again.downloads) == ("work", True, Path.home() / "dl")
    p.write_text(dump_config(cfg).replace('account = "work"', 'account = "ghost"'))
    with pytest.raises(ConfigError, match="ghost"):
        load_config(p)
    assert load_config(cfg.path).chats_account == "personal"  # default: first account


# ------------------------------------------------------------------ web
@pytest.fixture
def server(cfg, monkeypatch):
    monkeypatch.setattr(web, "load_config", lambda: cfg)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = web.App(port)
    srv = web._Server(("127.0.0.1", port), web.make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, port
    srv.shutdown()
    srv.server_close()


def _post(port, path, body: bytes, headers: dict):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", path, body=body, headers={"Host": f"localhost:{port}", **headers})
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def test_upload_needs_csrf_and_imports_in_the_background(server, cfg, tmp_path):
    app, port = server
    data = write_zip(tmp_path / "c.zip", basic_export()).read_bytes()
    assert _post(port, "/sources/upload", data, {"X-CSRF-Token": "wrong"})[0] == 403
    assert _post(port, "/sources/upload", data, {"X-CSRF-Token": app.csrf,
                                                  "Origin": "http://evil.example"})[0] == 403
    status, body = _post(port, "/sources/upload", data, {"X-CSRF-Token": app.csrf,
                                                          "X-File-Name": "conversations-000.zip"})
    assert status == 202, body
    for _ in range(100):
        if not app.jobs.snapshot()["running"] and app.jobs.snapshot()["finished"]:
            break
        time.sleep(0.05)
    assert "Imported 1 chat(s)" in app.jobs.snapshot()["message"]
    assert list((chats.STATE_DIR / "uploads").iterdir()) == []  # temp copy removed
    assert any(k.endswith("chat-c1.json") for k in records(cfg))


def test_upload_rejects_oversize_and_bad_files(server, monkeypatch):
    app, port = server
    monkeypatch.setattr(web, "MAX_UPLOAD", 10)
    assert _post(port, "/sources/upload", b"x" * 11, {"X-CSRF-Token": app.csrf})[0] == 413
    monkeypatch.setattr(web, "MAX_UPLOAD", 1000)
    status, _ = _post(port, "/sources/upload", b'[{"mapping": {}}]', {"X-CSRF-Token": app.csrf})
    assert status == 202
    for _ in range(100):
        if app.jobs.snapshot()["finished"]:
            break
        time.sleep(0.05)
    snap = app.jobs.snapshot()
    assert not snap["ok"] and "ChatGPT" in snap["message"]


def test_sources_page_and_settings(server, cfg, monkeypatch):
    app, _ = server
    html_out = web.page_sources(app, cfg)
    assert "claude.ai chats" in html_out and "Not imported yet" in html_out and "Coming later" in html_out
    assert "Nothing in your Downloads folder is read" in html_out
    saved = []
    monkeypatch.setattr(web, "save_config", lambda c: saved.append(c))
    loc, err, _ = app.post("/sources/settings", {"account": "work", "watch": "1", "downloads": "~/dl"})
    assert err is None and loc == "/sources?ok=chat_saved"
    new = saved[-1]
    assert new.chat_account == "work" and new.watch_downloads and new.downloads_dir == Path.home() / "dl"
    assert app.post("/sources/settings", {"account": "work", "downloads": "/etc"})[1]
    assert app.post("/sources/settings", {"account": "ghost"})[1]
