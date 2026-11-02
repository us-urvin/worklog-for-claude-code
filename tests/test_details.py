"""0.5.0: richer session details (PR and ticket refs, test runs, Claude's time). Cost was removed in 0.5.2."""
import re

from test_smart import _parse, _result, _tool, _user, _write

from worklog.parser import find_refs, run_outcome


def test_refs_are_found_without_false_alarms():
    text = ("/code-review 505; also PR #503 and XOO2-295, see https://github.com/x/y/pull/77, gh pr view 12. "
            "Not these: UTF-8, SHA-256, ISO-8601, v0.4.1, 10:30, #fff, color #000000, CVE-2024-1234 step-2")
    assert find_refs(text) == ["#503", "#505", "#77", "#12", "XOO2-295"]
    assert find_refs("fix/XOO2-294-qty", branch=True) == ["XOO2-294"]
    assert find_refs("feature/add-login-2", branch=True) == [] and find_refs("main", branch=True) == []
    assert find_refs("please review the changes") == []


def test_test_runs_failed_and_fixed(tmp_path):
    f = _write(tmp_path / "s1.jsonl", [
        _user("u1", 0, "fix the failing tests in #505"),
        _tool("a1", 1, "Bash", {"command": "cd api && python -m pytest -q"}, tool_id="b1"),
        _result("r1", 2, "b1", "===== 2 failed, 40 passed in 3.1s ====="),
        _tool("a2", 4, "Bash", {"command": "ls test-data"}, tool_id="b2"),  # not a test run
        _result("r2", 4.1, "b2", "a b c"),
        _tool("a3", 6, "Bash", {"command": "python -m pytest -q"}, tool_id="b3"),
        _result("r3", 7, "b3", "42 passed in 3.0s"),
        _user("u2", 20, "thanks, now push it"),
        _tool("a4", 21, "Bash", {"command": "git push"}, tool_id="b4"),
    ])
    sd = _parse(f)["s1"]
    assert sd.test_counts() == {"runs": 2, "failed": 1, "fixed": 1}
    assert sd.refs == ["#505"]
    # Claude worked 0->7 (7 min) after the first prompt and 20->21 after the second: 8 minutes
    assert sd.claude_minutes() == 8 and sd.active_minutes() == 21


def test_test_outcome_reading():
    assert run_outcome("== 3 failed, 10 passed in 1.2s ==", False) is False
    assert run_outcome("== 12 passed in 0.5s ==", False) is True
    assert run_outcome("Tests: 0 failed, 5 passed", False) is True
    assert run_outcome("", True) is False and run_outcome("compiled", False) is None
    assert run_outcome("test result: ok. 9 passed", False) is True


def test_cards_show_refs_tests_and_time_but_no_cost(home, repo, monkeypatch):
    from datetime import date

    from test_accounts import make_cfg
    from test_smart import XOO, _put, _rec

    from worklog import web
    from worklog.config import load_config
    from worklog.store import GitStore
    cfg = make_cfg(home, repo)
    monkeypatch.setattr(web, "_try_config", lambda: load_config(cfg.path))
    rec = _rec("a", "09:00", "10:00", project=XOO, active=50, refs=["#505", "XOO2-295"],
               tests={"runs": 3, "failed": 1, "fixed": 1}, claude_minutes=20,
               tokens_by_model={"claude-opus-5-5": {"input": 1_000_000, "output": 0, "cache_creation": 0,
                                                    "cache_read": 0}})
    _put(GitStore(repo), "2026-11-02", "dev1", "personal", rec)
    app = web.App(1)
    body, _ = web.page_day(app, cfg, date(2026, 11, 2))
    assert 'href="https://github.com/xoikospos/xoo/pull/505"' in body and '<span class="ref">XOO2-295</span>' in body
    assert "3 runs · 1 fixed" in body and "20m · 30m" in body
    assert "API prices" not in body and "$" not in re.sub(r"<script.*?</script>", "", body, flags=re.S)
    assert app.post("/settings/links", {"tracker": "javascript:alert(1)"})[1].startswith("Ticket links")
    assert app.post("/settings/links", {"tracker": "https://acme.atlassian.net/browse/"})[1] is None
    cfg = load_config(cfg.path)
    assert cfg.tracker_url == "https://acme.atlassian.net/browse/"
    body, _ = web.page_day(app, cfg, date(2026, 11, 2))
    assert 'href="https://acme.atlassian.net/browse/XOO2-295"' in body
