"""0.4.0 "smart basics": noise filtering, active time, Claude's to-do lists, and finding the project."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

from worklog.collector import detect_project
from worklog.parser import ParseStats, SessionDay, command_places, is_noise, parse_transcript

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return path


def _parse(path, day=None):
    sessions, stats = {}, ParseStats()
    day = day or T0.astimezone().date()
    parse_transcript(path, day, sessions, set(), set(), stats)
    return sessions


def _user(uid, minute, text, sid="s1", cwd="/tmp", **kw):
    return {"type": "user", "uuid": uid, "sessionId": sid, "timestamp": _ts(minute), "cwd": cwd,
            "message": {"role": "user", "content": text}, **kw}


def _tool(uid, minute, name, inp, sid="s1", cwd="/tmp", tool_id=None):
    return {"type": "assistant", "uuid": uid, "sessionId": sid, "timestamp": _ts(minute), "cwd": cwd,
            "message": {"id": uid, "model": "claude-x", "content": [
                {"type": "tool_use", "id": tool_id or uid, "name": name, "input": inp}]}}


def _result(uid, minute, tool_id, text, sid="s1"):
    return {"type": "user", "uuid": uid, "sessionId": sid, "timestamp": _ts(minute),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


# ------------------------------------------------------------------ noise
def test_system_messages_are_not_prompts_but_typed_tags_are():
    assert is_noise("<task-notification>\n<task-type>queued-remote-notifications</task-type>")
    assert is_noise("<bash-input>git status</bash-input>")
    assert is_noise("<user-prompt-submit-hook>blocked</user-prompt-submit-hook>")
    assert not is_noise("<div> is broken on mobile, fix it")
    assert not is_noise("<my-button> doesn't render")
    assert not is_noise("/code-review 503 with proper check")


def test_task_notification_is_not_shown_as_a_prompt(tmp_path):
    f = _write(tmp_path / "s1.jsonl", [
        _user("u1", 0, "/code-review 505 with proper check all conversation"),
        _user("u2", 1, "<task-notification>\n<task-type>queued-remote-notifications</task-type>\n<status>pending"),
        _user("u3", 2, "post this comment on PR 505"),
    ])
    assert _parse(f)["s1"].prompts == ["/code-review 505 with proper check all conversation",
                                        "post this comment on PR 505"]


# ------------------------------------------------------------------ active time
def test_active_time_skips_breaks(tmp_path):
    # 09:00-09:20 working, a 2-hour lunch, 11:20-11:30 working: 30 active minutes, not 150
    f = _write(tmp_path / "s1.jsonl", [_user(f"u{i}", m, "go") for i, m in enumerate([0, 5, 10, 20, 140, 145, 150])])
    sd = _parse(f)["s1"]
    assert sd.active_minutes() == 30
    assert round((sd.last - sd.first).total_seconds() / 60) == 150


# ------------------------------------------------------------------ to-do lists
def test_todowrite_last_list_wins(tmp_path):
    f = _write(tmp_path / "s1.jsonl", [
        _tool("a1", 0, "TodoWrite", {"todos": [{"content": "Review PR #503", "status": "in_progress"},
                                               {"content": "Post comment", "status": "pending"}]}),
        _tool("a2", 5, "TodoWrite", {"todos": [{"content": "Review PR #503", "status": "completed"},
                                               {"content": "Post comment", "status": "pending"}]}),
    ])
    assert _parse(f)["s1"].task_list() == [{"text": "Review PR #503", "status": "done"},
                                            {"text": "Post comment", "status": "open"}]


def test_task_tools_are_followed_by_id(tmp_path):
    f = _write(tmp_path / "s1.jsonl", [
        _tool("a1", 0, "TaskCreate", {"subject": "Fix sync identity"}, tool_id="t1"),
        _result("r1", 0.1, "t1", "Task #7 created successfully: Fix sync identity"),
        _tool("a2", 1, "TaskCreate", {"subject": "Draft release notes"}, tool_id="t2"),
        _result("r2", 1.1, "t2", [{"type": "text", "text": "Task #8 created successfully"}]),
        _tool("a3", 2, "TaskCreate", {"subject": "Scratch idea"}, tool_id="t3"),
        _result("r3", 2.1, "t3", "Task #9 created successfully"),
        _tool("a4", 3, "TaskUpdate", {"taskId": "7", "status": "completed"}),
        _tool("a5", 4, "TaskUpdate", {"taskId": "9", "status": "deleted"}),
    ])
    assert _parse(f)["s1"].task_list() == [{"text": "Fix sync identity", "status": "done"},
                                            {"text": "Draft release notes", "status": "open"}]


def test_a_task_made_yesterday_and_finished_today_is_recorded(tmp_path):
    f = _write(tmp_path / "s1.jsonl", [
        _tool("a1", -24 * 60, "TaskCreate", {"subject": "Ship 0.3.2"}, tool_id="t1"),
        _result("r1", -24 * 60 + 0.1, "t1", "Task #1 created successfully"),
        _tool("a2", -24 * 60 + 1, "TaskCreate", {"subject": "Old finished thing"}, tool_id="t2"),
        _result("r2", -24 * 60 + 1.1, "t2", "Task #2 created successfully"),
        _tool("a3", -24 * 60 + 2, "TaskUpdate", {"taskId": "2", "status": "completed"}),
        _tool("a4", 0, "TaskUpdate", {"taskId": "1", "status": "completed"}),
    ])
    # today: #1 was finished; #2 was finished yesterday, so it isn't part of today
    assert _parse(f)["s1"].task_list() == [{"text": "Ship 0.3.2", "status": "done"}]


def test_subagent_lists_are_ignored(tmp_path):
    e = _tool("a1", 0, "TodoWrite", {"todos": [{"content": "agent step", "status": "pending"}]})
    e["isSidechain"] = True
    assert _parse(_write(tmp_path / "s1.jsonl", [e]))["s1"].task_list() == []


# ------------------------------------------------------------------ finding the project
def test_command_places():
    dirs, slugs = command_places('cd xoo && gh pr checkout 505 -R xoikospos/xoo; git -C "/a b/c" log', "/home/u/dev")
    assert dirs == ["/home/u/dev/xoo", "/a b/c"] and slugs == ["xoikospos/xoo"]
    assert command_places("cd $TMP && ls; cd -", "/x") == ([], [])
    assert command_places("gh pr view 1 --repo=me/app", None) == ([], ["me/app"])


def _repo(path, remote=None):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


def _sd(cwd, files=(), dirs=None, slugs=None):
    sd = SessionDay("s1", T0.date())
    sd.cwd_counts[str(cwd)] += 1
    sd.files.update(str(f) for f in files)
    sd.work_dirs.update(dirs or {})
    sd.repo_slugs.update(slugs or {})
    return sd


def test_review_started_outside_the_repo_is_found_by_its_commands(home):
    """The screenshot: a PR review of xoo started in ~/development showed as "R&D / misc"."""
    dev = home / "development"
    xoo = _repo(dev / "xoo", "git@github.com:xoikospos/xoo.git")
    root, how, guess, _ = detect_project(_sd(dev, dirs={str(xoo): 3, str(xoo / "src"): 1}))
    assert root == xoo and how == "commands" and guess is None


def test_detection_order(home):
    dev = home / "development"
    a, b = _repo(dev / "a"), _repo(dev / "b")
    (a / "sub").mkdir()
    assert detect_project(_sd(a / "sub"))[:2] == (a, "cwd")
    assert detect_project(_sd(dev, files=[a / "x.py", a / "y.py", b / "z.py"]))[:2] == (a, "files")
    # equally likely: ask, don't guess silently
    root, how, guess, _ = detect_project(_sd(dev, dirs={str(a): 3, str(b): 3}))
    assert root is None and guess["name"] in ("a", "b") and "worked in" in guess["why"]
    # only named on the command line
    assert detect_project(_sd(dev, slugs={"xoikospos/xoo": 2}))[3] == "xoikospos/xoo"
    # nothing to go on
    assert detect_project(_sd(dev)) == (None, "", None, None)


def test_a_dotfiles_repo_in_home_never_claims_sessions(home):
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    (home / "notes").mkdir()
    assert detect_project(_sd(home / "notes", dirs={str(home / "notes"): 3})) == (None, "", None, None)
