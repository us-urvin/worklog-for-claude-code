"""0.5.0: the day cache keeps long-range pages fast and is always right."""
import json
import time

from test_smart import MISC, XOO, _put, _rec

from worklog import daycache, overrides
from worklog.store import GitStore


def _seed(repo, days=("2026-11-01", "2026-11-02")):
    store = GitStore(repo)
    for d in days:
        _put(store, d, "Office", "work", _rec(f"a-{d}", "09:00", "10:00", project=XOO, active=50))
    return store


def _count_reads(monkeypatch):
    seen = []
    real = GitStore.iter_json

    def counting(self, rel):
        if rel.startswith("records/"):
            seen.append(rel)
        return real(self, rel)
    monkeypatch.setattr(GitStore, "iter_json", counting)
    return seen


def test_a_day_is_read_once_then_served_from_the_cache(repo, monkeypatch):
    from datetime import date
    store = _seed(repo)
    reads = _count_reads(monkeypatch)
    c = daycache.DayCache(store)
    e = c.get(date(2026, 11, 2))
    assert e["minutes"] == 50 and e["projects"][0]["name"] == "xoo" and e["tokens"] == {"work": 10}
    assert c.get(date(2026, 10, 1)) == {"n": 0, "sig": c.entries["2026-10-01"]["sig"]}  # empty day: nothing read
    c.save()
    assert len(reads) == 1
    again = daycache.DayCache(store)
    again.get(date(2026, 11, 2))
    assert len(reads) == 1 and again.rebuilt == 0 and not again.dirty


def test_changes_from_another_computer_or_an_edit_rebuild_the_day(repo, monkeypatch):
    from datetime import date
    store = _seed(repo)
    c = daycache.DayCache(store)
    c.get(date(2026, 11, 2))
    c.save()
    _put(store, "2026-11-02", "Home", "work", _rec("b", "11:00", "11:40", project=MISC, active=40))  # a pull
    c = daycache.DayCache(store)
    assert c.get(date(2026, 11, 2))["minutes"] == 90 and c.rebuilt == 1
    c.save()
    overrides.change(store, "Office", {"names": {"github.com/xoikospos/xoo": "Xoikos POS"}})
    c = daycache.DayCache(store)
    assert c.get(date(2026, 11, 2))["projects"][0]["name"] == "Xoikos POS"


def test_a_broken_or_old_cache_is_simply_rebuilt(repo, home):
    from datetime import date
    store = _seed(repo)
    path = daycache._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert daycache.DayCache(store).get(date(2026, 11, 2))["minutes"] == 50
    path.write_text(json.dumps({"version": 0, "days": {"2026-11-02": {"sig": "x", "minutes": 999}}}))
    assert daycache.DayCache(store).get(date(2026, 11, 2))["minutes"] == 50
    c = daycache.DayCache(store, time_mode="wall")  # another time setting: other numbers, so another cache
    assert c.get(date(2026, 11, 2))["minutes"] == 60


def test_cache_keeps_400_days(repo):
    store = GitStore(repo)
    c = daycache.DayCache(store)
    c.entries = {f"2025-{m:02d}-{d:02d}": {"n": 0, "sig": "x"} for m in range(1, 13) for d in range(1, 29)}
    c.entries.update({f"2026-{m:02d}-{d:02d}": {"n": 0, "sig": "x"} for m in range(1, 12) for d in range(1, 29)})
    c.dirty = True
    c.save()
    kept = json.loads(daycache._path().read_text(encoding="utf-8"))["days"]
    assert len(kept) == 400 and "2026-11-28" in kept and "2025-01-01" not in kept


def test_a_year_of_work_stays_fast_and_small(repo):
    """365 days x 6 sessions: the first pass reads everything once, the next one reads nothing."""
    from datetime import date, timedelta
    store = GitStore(repo)
    start = date(2025, 11, 3)
    for i in range(365):
        d = (start + timedelta(days=i)).isoformat()
        for n in range(6):
            r = _rec(f"s{i}-{n}", "09:00", "10:00", project=XOO, active=45)
            r["start"], r["end"] = f"{d}T{8 + n:02d}:00:00+00:00", f"{d}T{8 + n:02d}:50:00+00:00"
            _put(store, d, "Office", "work", r)
    c = daycache.DayCache(store)
    c.days(start, start + timedelta(days=364))
    c.save()
    assert daycache._path().stat().st_size < 1_000_000
    t = time.perf_counter()
    again = daycache.DayCache(store)
    again.days(start, start + timedelta(days=364))
    assert again.rebuilt == 0 and time.perf_counter() - t < 2.0  # generous for slow CI machines
