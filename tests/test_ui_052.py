"""0.5.2: updates show up by themselves, a clear button for search, a clickable "where you left off", and the
two-column day page."""
from datetime import timedelta

from test_insights import TODAY, two_weeks  # noqa: F401  (fixture)

from worklog import daycache, updates, web
from worklog.daycache import doing_prompt


def test_updates_are_checked_every_few_hours_not_once_a_day(home, monkeypatch):
    assert updates.CHECK_EVERY <= 3 * 3600
    calls = []
    monkeypatch.setattr(updates, "fetch_latest", lambda **kw: calls.append(1) or "9.9.9")
    updates.check(now=1_000_000)
    updates.check(now=1_000_000 + 3600)  # an hour later: no new request
    updates.check(now=1_000_000 + updates.CHECK_EVERY + 1)
    assert len(calls) == 2
    updates.check(force=True, now=1_000_000 + updates.CHECK_EVERY + 2)  # the "Check for updates" button
    assert len(calls) == 3


def test_check_for_updates_button_and_status(home, repo, monkeypatch):
    from test_accounts import make_cfg
    cfg = make_cfg(home, repo)
    monkeypatch.setattr(web, "_try_config", lambda: cfg)
    app = web.App(1)
    page = web.layout("x", "", "day", "n", {}, csrf=app.csrf)
    assert 'action="/update/check"' in page and f'value="{app.csrf}"' in page and "Check for updates" in page
    monkeypatch.setattr(updates, "check", lambda force=False, now=None: "99.0.0")
    monkeypatch.setattr(updates, "version_on_disk", lambda: web.__version__)
    loc, err, _ = app.post("/update/check", {})
    assert err is None
    for _ in range(100):
        snap = app.jobs.snapshot()
        if not snap.get("running") and snap.get("finished"):
            break
        import time
        time.sleep(0.05)
    assert "99.0.0 is out" in snap["message"] and "Update now" in snap["message"]


def test_search_box_has_a_clear_button(two_weeks, home):  # noqa: F811
    from test_accounts import make_cfg
    store, _ = two_weeks
    cfg = make_cfg(home, store.path)
    empty = web._ask_box(app=web.App(1), cfg=cfg)
    assert 'type="search"' in empty and 'class="qclear"' in empty and " hidden>" in empty  # nothing to clear yet
    filled = web._ask_box("review", app=web.App(1), cfg=cfg)
    assert 'class="qclear" href="/ask"' in filled and " hidden>" not in filled
    page = web.page_ask(web.App(1), cfg, {"q": "review"})
    assert "✕ Clear search" in page


def test_where_you_left_off_skips_filler_and_links_to_the_card(two_weeks, home):  # noqa: F811
    assert doing_prompt([(0, "fix the refund retry loop in the webhook worker"), (1, "yes update that"),
                         (2, "ok continue")]) == "fix the refund retry loop in the webhook worker"
    assert doing_prompt([(0, "ok")]) == "ok"  # nothing better: still say something
    assert doing_prompt([]) == ""
    from test_accounts import make_cfg
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    entries = daycache.DayCache(store).days(monday - timedelta(days=7), TODAY)
    from worklog import insights
    html = web._leave_off_html(insights.leave_off(entries), cfg)
    anchor = web.card_anchor("github.com/xoikospos/xoo")
    assert f'class="stretch" href="/?d={monday.isoformat()}#{anchor}"' in html  # the whole card opens that day
    assert "HEAD" not in html and "Open →" in html
    body, _ = web.page_day(web.App(1), cfg, monday)
    assert f'id="{anchor}"' in body  # ... and lands on the project's card


def test_day_page_has_a_feed_and_a_folding_side_column(two_weeks, home):  # noqa: F811
    from test_accounts import make_cfg
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    body, _ = web.page_day(web.App(1), cfg, monday)
    assert '<div class="dayboard"><main class="feed">' in body and '<aside class="side"' in body
    for key in ("chart", "projects", "rhythm", "accounts", "models"):
        assert f'data-key="{key}"' in body
    assert 'data-key="rhythm" data-phone="closed"' in body  # big parts start folded on a phone
    assert 'class="toolbar"' in body and "Sync now" in body and 'class="cardfoot"' in body
    page = web.layout("x", body, "day", "n", {})
    assert '<body class="day"' in page


def test_a_fresh_morning_still_shows_where_you_left_off(two_weeks, home, monkeypatch):  # noqa: F811
    from test_accounts import make_cfg
    store, monday = two_weeks
    cfg = make_cfg(home, store.path)
    tomorrow = TODAY + timedelta(days=1)
    monkeypatch.setattr(web, "date", type("D", (web.date,), {"today": classmethod(lambda c: tomorrow)}))
    body, _ = web.page_day(web.App(1), cfg, tomorrow)
    assert "Nothing recorded for this day yet" in body
    assert "Where you left off" in body and 'action="/ask"' in body
