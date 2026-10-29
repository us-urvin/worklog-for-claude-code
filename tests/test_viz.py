"""Charts and fun bits: correct numbers, safe output, and Fun mode really switches them off."""
from datetime import date, datetime, timedelta

from worklog import viz

D = date(2026, 10, 14)


def test_streak_counts_days_in_a_row():
    active = {D - timedelta(days=i) for i in range(5)} | {D - timedelta(days=9)}
    assert viz.streak(active, D) == 5
    assert viz.streak(active, D + timedelta(days=1)) == 5  # today has no work yet: yesterday's streak still counts
    assert viz.streak(active, D + timedelta(days=2)) == 0
    assert viz.streak(set(), D) == 0


def test_record_day_needs_history_and_must_beat_every_earlier_day():
    totals = {D - timedelta(days=i): 100 for i in range(1, 8)}
    assert viz.is_record_day({**totals, D: 101}, D)
    assert not viz.is_record_day({**totals, D: 100}, D)  # a tie is no record
    few = {D - timedelta(days=1): 5, D: 1_000}
    assert not viz.is_record_day(few, D)  # one earlier day isn't enough to celebrate


def test_minutes_by_hour_splits_sessions_across_hours():
    def at(h, m):
        return datetime(D.year, D.month, D.day, h, m).astimezone().isoformat()
    hours = viz.minutes_by_hour([{"start": at(9, 30), "span_minutes": 90},
                                 {"start": at(23, 50), "span_minutes": 30},  # runs past midnight
                                 {"start": "garbage", "span_minutes": 10}], D)
    assert hours[9] == 30 and hours[10] == 60 and hours[23] == 10 and sum(hours) == 100


def test_heatmap_levels_selection_and_future():
    act = {D - timedelta(days=i): (i + 1) * 1000 for i in range(10)}
    svg = viz.heatmap(act, D)
    assert svg.count('class="cell') == 35 and svg.count(" sel") == 1
    assert "l4" in svg and "l0" in svg
    assert 'href="/?d=2026-10-14"' in svg


def test_donut_merges_accounts_and_handles_empty():
    rows = [{"label": "Opus 5.5", "total": 300}, {"label": "Opus 5.5", "total": 100},
            {"label": "Haiku 5.5", "total": 100}]
    svg = viz.donut(rows)
    assert svg.count('class="seg"') == 2 and "80%" in svg and "Opus 5.5" in svg
    assert viz.donut([]) == "" and viz.donut([{"label": "x", "total": 0}]) == ""


def test_fun_fact_is_stable_per_day_and_never_empty_when_there_is_data():
    a = viz.fun_fact(D, 13_600_000, 300_000, 14, 3, 600)
    assert a == viz.fun_fact(D, 13_600_000, 300_000, 14, 3, 600) and a
    assert viz.fun_fact(D, 0, 0, 0, 0, 0) == ""
    facts = {viz.fun_fact(D + timedelta(days=i), 13_600_000, 300_000, 14, 3, 600) for i in range(20)}
    assert len(facts) > 3  # it varies from day to day


def test_project_bars_escape_names():
    html = viz.project_bars([("<script>x</script>", 90), ("b", 30), ("none", 0)])
    assert "<script>" not in html and "&lt;script&gt;" in html and "1h 30m" in html and "none" not in html
    assert viz.project_bars([]) == ""


def test_fun_mode_off_removes_the_fun(home):
    from worklog import web
    from worklog.config import load_config
    from worklog.demo import generate
    cfg = load_config(generate(home / "demo", days=14))
    day = date.today() - timedelta(days=1)
    on, _ = web.page_day(web.App(1), cfg, day)
    assert 'id="fun"' in on and 'class="fact"' in on and 'class="flame"' in on
    cfg.fun = False
    off, _ = web.page_day(web.App(1), cfg, day)
    for bit in ('id="fun"', 'class="fact"', 'class="flame"', 'class="robot"'):
        assert bit not in off
    assert 'class="clock"' in off and 'class="heat"' in off  # the charts stay
    page = web.layout("t", off, "day", "n", {}, fun=False)
    assert 'data-fun="0"' in page and "prefers-reduced-motion:reduce" in page
