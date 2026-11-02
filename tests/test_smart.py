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


# ------------------------------------------------------------------ corrections (overrides)
from worklog import overrides  # noqa: E402
from worklog.report import aggregate, project_title  # noqa: E402
from worklog.store import GitStore  # noqa: E402
from worklog.tags import tag_session  # noqa: E402


def _rec(sid, start, end, *, project, prompts=("go",), active=None, device="Office", files=(), tasks=None, **kw):
    r = {"session_id": sid, "account": "work", "device": device, "project": project,
         "start": f"2026-11-02T{start}:00+00:00", "end": f"2026-11-02T{end}:00+00:00",
         "span_minutes": int((datetime.fromisoformat(f"2026-11-02T{end}") -
                              datetime.fromisoformat(f"2026-11-02T{start}")).total_seconds() // 60),
         "prompts": list(prompts), "files_touched": list(files), "tokens": {"input": 10}, **kw}
    if active is not None:
        r["active_minutes"] = active
    if tasks is not None:
        r["tasks"] = tasks
    return r


XOO = {"kind": "git", "name": "xoo-fix-qty", "remote": {"host": "github.com", "slug": "xoikospos/xoo"}}
MISC = {"kind": "misc", "name": "R&D / misc", "path": "~/development", "folder": "development"}


def test_worktree_cards_use_the_repo_name():
    assert project_title(XOO) == "xoo" and project_title(MISC) == "R&D / misc"


def test_newest_change_wins_across_devices_and_none_resets(repo):
    store = GitStore(repo)
    overrides.change(store, "Office", {"names": {"github.com/xoikospos/xoo": "Xoikos POS"}}, now=100)
    overrides.change(store, "Home", {"names": {"github.com/xoikospos/xoo": "XOO"}}, now=200)
    assert overrides.load(repo).names["github.com/xoikospos/xoo"] == "XOO"
    overrides.change(store, "Office", {"names": {"github.com/xoikospos/xoo": None}}, now=300)  # Reset
    assert "github.com/xoikospos/xoo" not in overrides.load(repo).names
    overrides.change(store, "Home", {"tags": {"s1": "Review"}}, now=400)
    overrides.change(store, "Office", {"tags": {"s1": "Docs"}}, now=400)  # same moment: device name decides
    assert overrides.load(repo).tags["s1"] == "Docs"  # "Office" sorts after "Home", on every computer


def test_rules_are_per_device_and_match_subfolders(repo):
    store = GitStore(repo)
    overrides.change(store, "Office", {"rules": {"~/development": "github.com/xoikospos/xoo"}})
    ov = overrides.load(repo)
    assert ov.rule_for("Office", "~/development") == "github.com/xoikospos/xoo"
    assert ov.rule_for("Office", "~/development/scratch") == "github.com/xoikospos/xoo"
    assert ov.rule_for("Office", "~/developments") is None
    assert ov.rule_for("Home", "~/development") is None  # the same path may mean something else there


def test_moves_rules_names_and_tags_apply_when_a_day_is_read(repo):
    records = [
        _rec("a", "09:00", "10:00", project=XOO, active=50, prompts=["implement qty field"], files=["app/qty.py"]),
        _rec("b", "11:00", "11:40", project=MISC, active=40, prompts=["/code-review 505 with proper check"]),
        _rec("c", "12:00", "12:30", project=dict(MISC, path="~/notes"), active=30, prompts=["plan the sprint"]),
    ]
    _, projects = aggregate(records)
    assert set(projects) == {"github.com/xoikospos/xoo", "misc:R&D / misc"}
    store = GitStore(repo)
    overrides.change(store, "Office", {"rules": {"~/development": "github.com/xoikospos/xoo"},
                                       "names": {"github.com/xoikospos/xoo": "Xoikos POS"},
                                       "tags": {"c": "Planning"}})
    _, projects = aggregate(records, overrides.load(repo))
    xoo, misc = projects["github.com/xoikospos/xoo"], projects["misc:R&D / misc"]
    assert xoo.name == "Xoikos POS" and xoo.sessions == 2 and xoo.moved and xoo.minutes == 90
    assert xoo.tag_weights == {"Feature": 50, "Review": 40} and xoo.tag == "Feature"
    assert misc.sessions == 1 and misc.tag == "Planning"
    # a single session moved to a brand-new project
    overrides.change(store, "Office", {"moves": {"c": "custom:sprint"},
                                       "projects": {"custom:sprint": {"kind": "custom", "name": "Sprint planning",
                                                                      "remote": None}}})
    _, projects = aggregate(records, overrides.load(repo))
    assert projects["custom:sprint"].name == "Sprint planning" and "misc:R&D / misc" not in projects


def test_rules_never_refile_sessions_that_found_a_repo(repo):
    overrides.change(GitStore(repo), "Office", {"rules": {"~/development": "custom:x"}})
    rec = _rec("a", "09:00", "10:00", project=dict(XOO, path="~/development/xoo"), active=60)
    _, projects = aggregate([rec], overrides.load(repo))
    assert list(projects) == ["github.com/xoikospos/xoo"]


def test_time_is_active_and_overlaps_count_once():
    recs = [_rec("a", "09:00", "11:00", project=XOO, active=60),  # two windows open at the same time
            _rec("b", "09:30", "10:30", project=XOO, active=50)]
    _, p = aggregate(recs)
    assert p["github.com/xoikospos/xoo"].minutes == 110  # 60 + 50 active, under the 120 wall minutes
    _, p = aggregate(recs, time_mode="wall")
    assert p["github.com/xoikospos/xoo"].minutes == 120  # 180 minutes of windows, but 9:00-11:00 is 120
    old = [_rec("a", "09:00", "10:00", project=XOO)]  # before 0.4.0: no active time recorded
    _, p = aggregate(old)
    assert p["github.com/xoikospos/xoo"].minutes == 60 and p["github.com/xoikospos/xoo"].approx


def test_old_records_lose_system_noise_and_tasks_merge():
    recs = [_rec("a", "09:00", "10:00", project=XOO, prompts=["<task-notification>\n<status>x", "fix the qty bug"],
                 tasks=[{"text": "Review PR #503", "status": "done"}, {"text": "Ask Priti", "status": "open"}]),
            _rec("b", "10:00", "11:00", project=XOO, tasks=[{"text": "Ask Priti", "status": "open"}])]
    _, p = aggregate(recs)
    x = p["github.com/xoikospos/xoo"]
    assert [q for _, q in x.prompts] == ["fix the qty bug", "go"]
    assert x.tasks == [{"text": "Review PR #503", "status": "done"}, {"text": "Ask Priti", "status": "open"}]


def test_guess_is_offered_until_answered(repo):
    guess = {"path": "~/development/xoo", "name": "xoo", "remote": XOO["remote"], "why": "it worked in xoo and api"}
    rec = _rec("b", "11:00", "11:40", project=dict(MISC, guess=guess), active=40)
    _, p = aggregate([rec])
    assert p["misc:R&D / misc"].guess["name"] == "xoo" and p["misc:R&D / misc"].guess["sessions"] == ["b"]
    overrides.change(GitStore(repo), "Office", {"noguess": {"b": True}})
    _, p = aggregate([rec], overrides.load(repo))
    assert p["misc:R&D / misc"].guess is None


# ------------------------------------------------------------------ tags
def test_tag_rules():
    t = lambda prompts, files=(), **kw: tag_session({"prompts": list(prompts), "files_touched": list(files), **kw})  # noqa: E731
    assert t(["/code-review 503 with proper check all conversation"]) == "Review"
    assert t(["fix the failing windows test"], ["worklog/locks.py"]) == "Bug fix"
    assert t(["update the guide"], ["README.md", "docs/GUIDE.md"]) == "Docs"
    assert t(["add tests"], ["tests/test_a.py", "tests/test_b.py"]) == "Tests"
    assert t(["release 0.3.2 and write the changelog"]) == "Release"
    assert t(["how does the watcher work?"]) == "Research"
    assert t(["make a plan for the timesheet"]) == "Planning"
    assert t(["add a timesheet page"], ["worklog/web.py"]) == "Feature"
    assert t(["refactor the parser"], ["worklog/parser.py"]) == "Refactor"
    assert tag_session({"source": "claude.ai"}) == "Chat"


# ------------------------------------------------------------------ plain-language summaries
from worklog.report import parse_summary  # noqa: E402


def test_summary_json_is_read_even_with_text_around_it():
    answer = ('Here you go:\n```json\n{"line": "Reviewed PRs #503 and #505; triaged the mobile qty bug",'
              ' "paragraph": "Reviewed two pull requests and posted comments.", "bullets": ["Reviewed PR #503",'
              ' "Posted a comment on PR #505"], "tag": "Review", "next": ["Confirm the qty bug with Priti"]}\n```')
    s = parse_summary(answer)
    assert s["line"].startswith("Reviewed PRs #503") and s["tag"] == "Review"
    assert s["bullets"] == ["- Reviewed PR #503", "- Posted a comment on PR #505"]
    assert s["next"] == ["Confirm the qty bug with Priti"]


def test_summary_falls_back_and_never_trusts_odd_values():
    assert parse_summary("- did a thing\n- and another") == {
        "line": "did a thing", "paragraph": "", "bullets": ["- did a thing", "- and another"], "tag": "", "next": []}
    s = parse_summary('{"paragraph": "Fixed the login. Then tidied up.", "tag": "Hacking", "bullets": "nope"}')
    assert s["tag"] == "" and s["line"] == "Fixed the login" and s["bullets"] == ["- Fixed the login. Then tidied up."]
    assert parse_summary("sorry, I can't") is None and parse_summary("{broken json") is None
    assert "sk-ant" not in parse_summary('{"line": "used key sk-ant-api03-abcdefghijklmnopqrstu"}')["line"]
