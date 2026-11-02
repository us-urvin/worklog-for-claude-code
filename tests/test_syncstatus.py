"""Settings → Private data repo: connected or not, which repo, private or public, and whether syncing works."""
import io
import json
import subprocess
from urllib.error import HTTPError

import pytest
from test_accounts import make_cfg

from worklog import syncstatus, web
from worklog.store import GitStore


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def section(cfg):
    return web.data_repo_section(web.App(1), cfg)


@pytest.fixture
def no_network(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no network expected here")
    monkeypatch.setattr(syncstatus, "urlopen", fail)


def test_not_a_git_folder_says_not_set_up(home, repo, no_network):
    cfg = make_cfg(home, repo)
    cfg.store_repo = home / "nowhere"
    html = section(cfg)
    assert "Not set up" in html and "git init" in html


def test_local_only_explains_how_to_connect(home, repo, no_network):
    html = section(make_cfg(home, repo))
    assert "Local only" in html and "Not connected" in html and "remote add origin" in html and "Copy" in html


def test_connected_shows_which_repo_and_never_the_credentials(home, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "https://me:ghp_SECRET123@github.com/me/claude-worklog-data.git")
    monkeypatch.setattr(syncstatus, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
        HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))))
    syncstatus.visibility("github.com", "me/claude-worklog-data")  # what the background check does
    html = section(make_cfg(home, repo))
    assert "Connected" in html and "github.com/me/claude-worklog-data" in html
    assert 'href="https://github.com/me/claude-worklog-data"' in html
    assert "SECRET" not in html and "ghp_" not in html and "me:" not in html
    assert "🔒 Private" in html and "Not uploaded yet" in html


def test_public_repo_is_flagged_everywhere(home, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@github.com:me/claude-worklog-data.git")
    monkeypatch.setattr(syncstatus, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({"private": False}).encode()))
    assert syncstatus.visibility("github.com", "me/claude-worklog-data") == "public"
    cfg = make_cfg(home, repo)
    assert "PUBLIC" in section(cfg) and "Change visibility" in section(cfg)
    assert "is public" in web._public_repo_warning(cfg)  # the warning on every page


def test_visibility_is_cached_and_only_asked_for_github(home, monkeypatch):
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(b'{"private": true}')
    monkeypatch.setattr(syncstatus, "urlopen", fake)
    assert syncstatus.visibility("github.com", "me/r") == "private"
    assert syncstatus.visibility("github.com", "me/r") == "private" and len(calls) == 1  # cached
    assert syncstatus.visibility("gitlab.com", "me/r") == "unknown" and len(calls) == 1  # never asks other hosts
    assert calls == ["https://api.github.com/repos/me/r"]
    monkeypatch.setattr(syncstatus, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert syncstatus.visibility("github.com", "me/other") == "unknown"


def test_store_records_uploads_and_failures(home, repo, tmp_path, no_network):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    store = GitStore(repo)
    store.write_json("records/2026-11-01/dev/personal/a.json", {"a": 1})
    store.commit_and_push("test")
    assert syncstatus.last("push")["ok"] is True
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    store.write_json("records/2026-11-01/dev/personal/b.json", {"b": 1})
    store.commit_and_push("test 2")
    failed = syncstatus.last("push")
    assert failed["ok"] is False and failed["error"]


def test_failed_upload_and_waiting_changes_are_shown(home, repo, no_network):
    git(repo, "remote", "add", "origin", "git@gitlab.com:me/data.git")
    GitStore(repo).write_json("records/2026-11-01/dev/personal/a.json", {"a": 1})
    git(repo, "add", "-A")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                   check=True)
    syncstatus.record("push", False, "fatal: Could not read from remote repository.")
    html = section(make_cfg(home, repo))
    assert "Last upload failed" in html and "Could not read from remote repository" in html
    assert "1 change waiting to upload" in html
    assert "Privacy not checked" in html  # not GitHub: no request is made
    syncstatus.record("push", True)
    assert "Synced" in section(make_cfg(home, repo))


def test_test_connection_works_and_explains_failures(home, repo, tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    cfg = make_cfg(home, repo)
    ok, msg = syncstatus.test_connection(cfg)
    assert ok and "Connection works" in msg
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    ok, msg = syncstatus.test_connection(cfg)
    assert not ok and msg.startswith("Can't reach your data repo")
