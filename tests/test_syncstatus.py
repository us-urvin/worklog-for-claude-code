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
    assert "SECRET" not in html and "ghp_" not in html and "me:ghp" not in html
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


def test_sync_works_without_a_git_identity_on_this_computer(home, repo, tmp_path, no_network):
    """A pull that replays local commits needs a committer name; this computer has none set."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "config", "user.useConfigOnly", "true")  # like a computer without `git config --global user.email`
    a = GitStore(repo)
    a.write_json("records/2026-11-01/a/personal/1.json", {"n": 1})
    a.commit_and_push("first")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    b = GitStore(other)
    b.write_json("records/2026-11-01/b/personal/2.json", {"n": 2})
    b.commit_and_push("from the other computer")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "offline.git"))  # local commits pile up while offline
    a.write_json("records/2026-11-01/a/personal/3.json", {"n": 3})
    a.commit_and_push("offline")
    git(repo, "remote", "set-url", "origin", str(bare))
    a.pull()
    assert syncstatus.last("pull")["ok"] is True, syncstatus.last("pull")
    assert (repo / "records/2026-11-01/b/personal/2.json").exists()
    a.commit_and_push("nothing new")  # nothing staged, but the waiting commit still goes up
    assert syncstatus.status(make_cfg(home, repo))["waiting"] == 0


def _fake_ssh(monkeypatch, *, who="us-urvin", ls_remote=(0, "", "")):
    """ssh -G answers from a ~/.ssh/config alias; ssh -T says who the key is; git ls-remote as given."""
    real_run, real_git = syncstatus.subprocess.run, syncstatus._git
    syncstatus._ssh_cache.clear()
    monkeypatch.setattr(syncstatus.shutil, "which", lambda n: "/usr/bin/" + n)

    def run(cmd, *a, **k):
        if cmd[0] == "ssh" and "-G" in cmd:
            out = "user git\nhostname github.com\nport 22\nidentityfile ~/.ssh/id_work\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[0] == "ssh" and "-T" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", f"Hi {who}! You've successfully authenticated, but GitHub "
                                                            "does not provide shell access.")
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(syncstatus.subprocess, "run", run)
    monkeypatch.setattr(syncstatus, "_git", lambda repo, *args, **k: ls_remote if args[0] == "ls-remote"
                        else real_git(repo, *args, **k))


def test_ssh_nickname_links_to_the_real_host_and_names_the_key(home, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@worklog-git:us-urvin/claude-worklog-data.git")
    _fake_ssh(monkeypatch)
    asked = []
    monkeypatch.setattr(syncstatus, "urlopen", lambda req, timeout: asked.append(req.full_url) or (_ for _ in ()).throw(
        HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))))
    cfg = make_cfg(home, repo)
    r = syncstatus.remote(cfg)
    assert r["host"] == "github.com" and r["alias"] == "worklog-git" and r["key"] == "~/.ssh/id_work"
    assert syncstatus.visibility(r["host"], r["slug"]) == "private"  # the privacy check works through the nickname
    html = section(cfg)
    assert 'href="https://github.com/us-urvin/claude-worklog-data"' in html and "https://worklog-git" not in html
    assert "worklog-git" in html and "id_work" in html and "Test connection</b> to see which GitHub account" in html


def test_test_connection_says_which_account_signs_in(home, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@worklog-git:us-urvin/claude-worklog-data.git")
    _fake_ssh(monkeypatch)
    cfg = make_cfg(home, repo)
    ok, msg = syncstatus.test_connection(cfg)
    assert ok and "signs in to GitHub as us-urvin" in msg
    assert "Signs in as us-urvin" in section(cfg)


def test_wrong_account_is_explained(home, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@gh-office:us-urvin/claude-worklog-data.git")
    err = "ERROR: Repository not found.\nfatal: Could not read from remote repository."
    _fake_ssh(monkeypatch, who="office-u", ls_remote=(128, "", err))
    ok, msg = syncstatus.test_connection(make_cfg(home, repo))
    assert not ok and "knows this computer's key as office-u" in msg
    assert "no access to us-urvin/claude-worklog-data" in msg


def test_https_and_local_remotes_are_not_treated_as_ssh(home, repo):
    git(repo, "remote", "add", "origin", "https://us-urvin@github.com/us-urvin/data.git")
    r = syncstatus.remote(make_cfg(home, repo))
    assert r["via"] == "https" and r["user"] == "us-urvin" and r["host"] == "github.com"
    assert syncstatus._ssh_parts("C:/repos/data.git") is None and syncstatus._ssh_parts("/srv/data.git") is None
    assert syncstatus._ssh_parts("ssh://git@work-gh:22/me/r.git") == ("git", "work-gh")


def test_errors_show_the_line_that_matters():
    err = ("ssh: connect to host github.com port 22: Connection timed out\nfatal: Could not read from remote "
           "repository.\n\nPlease make sure you have the correct access rights\nand the repository exists.\n")
    assert syncstatus._main_line(err).startswith("ssh: connect to host github.com port 22")
    assert syncstatus._main_line("") == "no answer"
