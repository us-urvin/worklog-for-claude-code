"""0.5.0: Trends, where you left off, Ask, goals, badges and the year in review."""
from datetime import date, timedelta

import pytest
from test_smart import MISC, XOO, _put, _rec

from worklog import ask, daycache, insights, web
from worklog.store import GitStore

TODAY = date.today()


def _day(store, d: date, sid, project=XOO, start="09:00", end="10:00", active=50, **kw):
    r = _rec(sid, start, end, project=project, active=active, **kw)
    r["start"], r["end"] = f"{d.isoformat()}T{start}:00+00:00", f"{d.isoformat()}T{end}:00+00:00"
    _put(store, d.isoformat(), "Office", "work", r)


@pytest.fixture
def two_weeks(repo):
    store = GitStore(repo)
    monday = TODAY - timedelta(days=TODAY.weekday())
    for i in range(5):  # last week: 5 days of xoo
        _day(store, monday - timedelta(days=7 - i), f"lw{i}", active=60, refs=["#503"],
             prompts=["review PR 503 for the qty fix"])
    _day(store, monday, "tw0", end="11:00", active=120, refs=["#505", "XOO2-295"],
         tests={"runs": 2, "failed": 1, "fixed": 1},
         claude_minutes=40, tasks=[{"text": "Confirm qty bug with Priti", "status": "open"}],
         prompts=["/code-review 505 with proper check", "also look at the payment iframe flow",
                  "post this comment on PR 505"])
    _day(store, monday, "tw1", project=MISC, start="11:30", end="12:00", active=30,
         prompts=["why is the mic not working"])
    store.write_json(f"reports/{monday.isoformat()}/Office.json", {"generated_at": "2000-01-01", "summaries": {
        "github.com/xoikospos/xoo": {"source": "ai", "line": "Reviewed PR #505", "tag": "Review",
                                     "sessions": ["tw0"]}}})
    return store, monday


def test_periods_and_plain_sentences(two_weeks):
    store, monday = two_weeks
    c = daycache.DayCache(store)
    entries = c.days(monday - timedelta(days=7), TODAY)
    now = insights.period(entries, monday, TODAY)
    before = insights.period(entries, monday - timedelta(days=7), monday - timedelta(days=1))
    assert now.minutes == 150 and before.minutes == 300 and now.refs == {"#505", "XOO2-295"}
    assert now.tests_fixed == 1 and now.claude == 40 and now.tags == {"Review": 120}
    said = insights.sentences(now, before)
    assert said[0].startswith("This week: 2h 30m over 1 day (-50%)")
    assert any("xoo" in x for x in said) and any("fixed" in x for x in said)
    assert insights.sentences(insights.Period(TODAY, TODAY), before) == ["No work recorded this week yet."]


def test_where_you_left_off(two_weeks):
    store, monday = two_weeks
    left = insights.leave_off(daycache.DayCache(store).days(monday - timedelta(days=7), TODAY))
    assert [x["name"] for x in left] == ["R&D / misc", "xoo"]  # the mic question was later that day
    xoo = left[1]
    assert xoo["line"] == "Reviewed PR #505" and xoo["open"] == ["Confirm qty bug with Priti"]


def test_ask_dates_and_search(two_weeks):
    store, monday = two_weeks
    t = date(2026, 11, 4)  # a Wednesday
    assert ask.date_range("what did I do last week?", t) == (date(2026, 10, 26), date(2026, 11, 1))
    assert ask.date_range("hours yesterday", t) == (date(2026, 11, 3),) * 2
    assert ask.date_range("work in October", t) == (date(2026, 10, 1), date(2026, 10, 31))
    assert ask.date_range("the last 3 days", t) == (date(2026, 11, 2), t)
    assert ask.date_range("on 2026-10-05 and 2026-10-07", t) == (date(2026, 10, 5), date(2026, 10, 7))
    assert ask.date_range("when did I review it", t) is None
    c = daycache.DayCache(store)
    hits = ask.search(c, "when did I review PR #505?", TODAY)
    assert hits[0]["project"] == "xoo" and hits[0]["day"] == monday and "refs" in hits[0]["why"]
    deep = ask.search(c, "payment iframe", TODAY)  # only in a prompt in the middle of the session
    assert deep and deep[0]["project"] == "xoo" and deep[0]["why"] == "your prompts"
    assert ask.search(c, "mic not working", TODAY)[0]["project"] == "R&D / misc"
    assert ask.search(c, "zzqx nothing", TODAY) == []


def test_ask_context_is_small_redacted_and_answer_is_stored(two_weeks, monkeypatch):
    store, monday = two_weeks
    c = daycache.DayCache(store)
    notes, days = ask.context(c, "what did I do this week? my key is sk-ant-api03-abcdefghijklmnopq", TODAY)
    assert days == [monday] and "xoo: 120 min" in notes and "refs: #505, XOO2-295" in notes
    assert len(notes) <= ask.MAX_CONTEXT
    from test_accounts import make_cfg
    seen = []

    def fake(cmd, **kw):
        seen.append(cmd[2])
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, f"You reviewed PR #505 [{monday.isoformat()}].", "")
    monkeypatch.setattr(ask.shutil, "which", lambda n: "/usr/bin/claude")
    monkeypatch.setattr(ask.subprocess, "run", fake)
    cfg = make_cfg(store.path.parent, store.path)
    answer = ask.ask_claude(cfg, "When did I review 505?", notes, TODAY)
    assert answer.startswith("You reviewed PR #505") and "<notes>" in seen[0]
    ask.remember("my token sk-ant-api03-abcdefghijklmnopq?", answer)
    assert "sk-ant" not in ask.history()[0]["q"] and ask.history()[0]["a"] == answer
    assert ask.ask_claude(cfg, "x", "", TODAY).startswith("There's nothing recorded")


def test_goals_badges_and_year(two_weeks):
    store, monday = two_weeks
    entries = daycache.DayCache(store).days(monday - timedelta(days=30), TODAY)
    g = insights.goal_progress(entries, monday, {"hours": 2, "tokens": 100})
    assert g["hours"]["done"] == 150 and g["hours"]["share"] == 1.0 and g["streak"] == 1
    assert g["tokens"]["used"] == 20 and insights.goal_progress(entries, monday, {}) is None
    b = {x["id"]: x["earned"] for x in insights.badges(entries)}
    assert b["first-session"] == monday - timedelta(days=7) and b["streak-7"] is None
    y = insights.year_review(entries, monday.year)
    assert y["top_projects"][0][0] == "xoo" and y["longest_streak"] >= 1


def test_trends_and_ask_pages(two_weeks, home, monkeypatch):
    from test_accounts import make_cfg
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    app = web.App(1)
    html = web.page_trends(app, cfg, {})
    assert "Trends" in html and "In short" in html and 'class="cols"' in html and "Badges" in html
    assert "/trends?view=month" in html
    month = web.page_trends(app, cfg, {"view": "month"})
    assert "Hours per month" in month
    html = web.page_ask(app, cfg, {"q": "review PR 505"})
    assert "Found" in html and "xoo" in html
    assert "Ask Claude" not in html  # AI is off in this config: search only
    body, _ = web.page_day(app, cfg, TODAY)
    if TODAY == monday:
        assert "Where you left off" in body
    assert 'action="/ask"' in body


# ------------------------------------------------------------------ phone summary, share card, year, settings
from worklog import digest, share  # noqa: E402


def test_phone_summary_is_small_private_and_single_writer(two_weeks, home):
    from test_accounts import make_cfg
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    cfg.device = "Office"
    assert digest.write(cfg, store) is False  # off by default
    cfg.phone_digest = True
    assert digest.write(cfg, store, today=TODAY) is True
    text = (store.path / "phone/README.md").read_text()
    assert text.startswith("<!-- worklog-phone device=Office") and "## This week" in text
    assert "| Day | Project |" in text
    assert "Confirm qty bug with Priti" in text and len(text.encode()) <= digest.MAX_BYTES
    assert "sk-ant" not in text and "@" not in text
    assert digest.write(cfg, store, today=TODAY) is False  # nothing new: no change, so no commit
    assert digest.other_writer(store.path, "Home", TODAY) == "Office"  # a second computer is refused
    assert digest.other_writer(store.path, "Home", TODAY + timedelta(days=5)) is None  # an old one isn't
    cfg.device = "Home"
    assert digest.write(cfg, store, today=TODAY) is False
    assert not (store.path / "README.md").exists()  # the repo's own README is never touched


def test_share_card_shows_totals_only(two_weeks, home, monkeypatch):
    from test_accounts import make_cfg
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    _, start, end, p, streak, names = web._share_period(cfg, store, {"what": "week", "d": monday.isoformat()})
    svg = share.card(*share.titles("week", start, end), p, streak, names)
    import xml.dom.minidom
    xml.dom.minidom.parseString(svg)
    assert "xoo" not in svg and "mic" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    named = share.card(*share.titles("week", start, end), p, streak, True)
    assert "Top projects: xoo" in named
    assert "<script" not in share.card("<script>", "x", p) and "&lt;script&gt;" in share.card("<script>", "x", p)
    html = web.page_share(web.App(1), cfg, {"what": "week", "d": monday.isoformat()})
    assert 'src="/share.svg?what=week' in html and "Download PNG" in html


def test_year_page_and_settings(two_weeks, home, monkeypatch):
    from test_accounts import make_cfg

    from worklog.config import load_config
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    monkeypatch.setattr(web, "_try_config", lambda: load_config(cfg.path))
    app = web.App(1)
    html = web.page_year(app, cfg, {"y": str(monday.year)})
    assert f"Your {monday.year}" in html and "Hours per month" in html and "Top projects" in html
    assert web.page_year(app, cfg, {"y": "nonsense"})  # falls back to this year
    assert app.post("/settings/goals", {"hours": "4", "tokens": "50000000"})[1] is None
    cfg = load_config(cfg.path)
    assert cfg.goal_hours == 4 and cfg.goal_tokens == 50_000_000
    assert app.post("/settings/goals", {"hours": "30"})[1].startswith("Goals")
    body, _ = web.page_day(app, cfg, TODAY)
    if store.path.joinpath("records", TODAY.isoformat()).exists():
        assert 'class="goals"' in body
    digest_file = store.path / "phone/README.md"
    digest_file.parent.mkdir(parents=True, exist_ok=True)
    digest_file.write_text(f"<!-- worklog-phone device=Home wrote={TODAY.isoformat()} -->\n")
    err = app.post("/settings/phone", {"on": "1"})[1]
    assert err and "Home already writes" in err
