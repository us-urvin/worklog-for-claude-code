"""0.5.1: honest hours for old records, a smarter search, tidy answers and the one-time re-read."""
from datetime import timedelta

from test_insights import TODAY, two_weeks  # noqa: F401  (fixture)

from worklog import ask, collector, daycache, web
from worklog.report import legacy_minutes


def test_old_records_without_active_time_are_not_counted_end_to_end():
    assert legacy_minutes({"api_calls": 40}, 60) == 60  # short sessions stay as they are
    assert legacy_minutes({"api_calls": 40}, 600) == 90  # a session left open all day isn't 10 hours
    assert legacy_minutes({"api_calls": 200}, 600) == 300
    assert legacy_minutes({"api_calls": 900}, 600) == 600  # never more than the span
    assert legacy_minutes({}, 600) == 600  # nothing to go on
    assert legacy_minutes({"api_calls": 5, "source": "claude.ai"}, 300) == 300


def test_search_finds_typos_other_endings_and_pull_requests(two_weeks):  # noqa: F811
    store, monday = two_weeks
    c = daycache.DayCache(store)
    assert "pr" in ask.words("my PR in ui") and "ui" in ask.words("my PR in ui") and "in" not in ask.words("in")
    hits = ask.search(c, "reviewing pull request 505", TODAY)  # other ending, "pull request" for PR
    assert hits and hits[0]["project"] == "xoo"
    hits = ask.search(c, "PR comment", TODAY)
    assert hits[0]["day"] == monday and hits[0]["matched"] == 2 and "<" not in hits[0]["line"]
    # results that match fewer words than the best one are dropped
    assert all(h["matched"] >= hits[0]["matched"] - 1 for h in hits)


def test_highlight_and_snippet_are_safe():
    pats = [ask.patterns(w) for w in ask.words("review <b>")]
    html = ask.highlight("I <b>reviewed</b> it", pats)
    assert "<mark>reviewed</mark>" in html and "&lt;b&gt;" in html
    long = "word " * 100 + "the payment iframe broke " + "tail " * 100
    cut = ask.snippet(long, [ask.patterns("payment")], 80)
    assert "payment" in cut and cut.startswith("…") and cut.endswith("…") and len(cut) <= 84


def test_claude_answers_are_tidy_html():
    html = web.answer_html("You reviewed **PR #505** on [2026-10-05].\n\n- first `fix`\n- second\n1. one\n2. two\n"
                           "## Next\n<script>x</script>")
    assert "<b>PR #505</b>" in html and '<a href="/?d=2026-10-05">2026-10-05</a>' in html
    assert "<ul><li>first <code>fix</code></li><li>second</li></ul>" in html
    assert "<ol><li>one</li><li>two</li></ol>" in html and "<p><b>Next</b></p>" in html
    assert "<script>" not in html


def test_ask_claude_checkbox_shows_only_with_ai(two_weeks, home):  # noqa: F811
    from test_accounts import make_cfg
    store, _ = two_weeks
    cfg = make_cfg(home, store.path)
    app = web.App(1)
    cfg.use_ai = False
    assert 'id="askai"' not in web._ask_box(app=app, cfg=cfg)
    cfg.use_ai = True
    box = web._ask_box(app=app, cfg=cfg)
    assert 'id="askai"' in box and "Ask Claude" in box and f'data-csrf="{app.csrf}"' in box


def test_older_days_are_read_again_once_after_updating(home, monkeypatch):
    assert web.needs_refresh()
    web._mark_refreshed()
    assert not web.needs_refresh()
    days = []
    monkeypatch.setattr(collector, "collect", lambda cfg, ds: days.extend(ds) or len(ds))
    assert collector.refresh_recent(None, days=12, batch=5) == 12
    assert days[0] == TODAY - timedelta(days=11) and days[-1] == TODAY and len(set(days)) == 12


def test_search_forgives_typos_and_word_endings(two_weeks):  # noqa: F811
    store, monday = two_weeks
    c = daycache.DayCache(store)
    hits = ask.search(c, "the paymnt ifrane flow", TODAY)  # two typos inside the words
    assert hits and hits[0]["project"] == "xoo" and hits[0]["matched"] == 3
    assert "<mark>payment</mark>" in ask.highlight(hits[0]["line"], hits[0]["pats"])
    assert ask.patterns("retry")[0].search("safe retries") and ask.patterns("reviews")[0].search("reviewed")


def test_the_same_note_on_many_days_is_one_result(two_weeks):  # noqa: F811
    store, monday = two_weeks
    hits = ask.search(daycache.DayCache(store), "qty fix", TODAY)  # last week: the same prompt on 5 days
    same = [h for h in hits if "qty fix" in h["line"]]
    assert len(same) == 1 and len(same[0]["also"]) == 4
    notes, days = ask.context(daycache.DayCache(store), "qty fix", TODAY)
    assert len(days) >= 5  # Claude still gets every day it happened
