"""Charts and fun bits for the day page: plain inline SVG/HTML, no external assets.

Every chart has a text alternative (aria-label / <title>) and works without JavaScript; CSS
animates it in, and `prefers-reduced-motion` turns all motion off.
"""
from __future__ import annotations

import html
import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta

MODEL_COLORS = {"Opus": "#9D8CFF", "Sonnet": "#38D9F5", "Haiku": "#F57BC3", "Fable": "#FBBF24"}
OTHER_COLOR = "#8A94B2"


def _e(v: object) -> str:
    return html.escape(str(v), quote=True)


def _short(v: float) -> str:
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if v >= div:
            return f"{v / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(int(v))


def model_color(label: str) -> str:
    return next((c for fam, c in MODEL_COLORS.items() if label.startswith(fam)), OTHER_COLOR)


# ------------------------------------------------------------------ streak + records
def streak(active: set[date], day: date) -> int:
    """Days in a row with any work, ending at `day` (or the day before, if `day` has none yet)."""
    d = day if day in active else day - timedelta(days=1)
    n = 0
    while d in active:
        n += 1
        d -= timedelta(days=1)
    return n


def is_record_day(totals: dict[date, int], day: date, min_history: int = 7) -> bool:
    """`day` beat every earlier day in `totals`, and there is enough history for that to mean something."""
    earlier = [v for d, v in totals.items() if d < day and v > 0]
    return len(earlier) >= min_history and totals.get(day, 0) > max(earlier)


# ------------------------------------------------------------------ donut: share per model
def donut(rows: list[dict]) -> str:
    """Rows from report.model_rows (any accounts); one ring segment per model family/version."""
    per: dict[str, int] = {}
    for r in rows:
        per[r["label"]] = per.get(r["label"], 0) + r["total"]
    whole = sum(per.values())
    if not whole:
        return ""
    segs, legend, cum = [], [], 0.0
    for i, (label, v) in enumerate(sorted(per.items(), key=lambda kv: -kv[1])):
        share = 100 * v / whole
        color = model_color(label)
        segs.append(f'<circle class="seg" cx="50" cy="50" r="38" pathLength="100" stroke="{color}" '
                    f'stroke-dasharray="{max(share - 0.6, 0.4):.2f} 100" stroke-dashoffset="{-cum:.2f}" '
                    f'style="animation-delay:{i * 120}ms"><title>{_e(label)}: {share:.0f}%</title></circle>')
        legend.append(f'<span><i style="background:{color}"></i>{_e(label)} {share:.0f}%</span>')
        cum += share
    top = max(per, key=per.get)
    label = ", ".join(f"{k} {100 * v / whole:.0f}%" for k, v in per.items())
    return (f'<div class="donut"><svg viewBox="0 0 100 100" role="img" aria-label="Share per model: {_e(label)}">'
            f'<circle cx="50" cy="50" r="38" class="track"/><g transform="rotate(-90 50 50)">{"".join(segs)}</g>'
            f'<text x="50" y="48" text-anchor="middle" class="big">{100 * per[top] / whole:.0f}%</text>'
            f'<text x="50" y="62" text-anchor="middle" class="small">{_e(top)}</text></svg>'
            f'<div class="legend">{"".join(legend)}</div></div>')


# ------------------------------------------------------------------ 24-hour clock
def minutes_by_hour(records: Iterable[dict], day: date) -> list[int]:
    """Minutes of work in each local hour of `day`, from each record's start time and duration."""
    hours = [0] * 24
    for r in records:
        try:
            start = datetime.fromisoformat(str(r.get("start"))).astimezone()
        except ValueError:
            continue
        span = int(r.get("span_minutes") or 0)
        end = start + timedelta(minutes=max(span, 1))
        t = start
        while t < end:
            nxt = min(end, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            if t.date() == day:
                hours[t.hour] += max(1, round((nxt - t).total_seconds() / 60)) if span else 1
            t = nxt
    return [min(h, 60) for h in hours]


def clock(hours: list[int]) -> str:
    """A 24-hour radial chart: one wedge per hour, longer when you worked more of that hour."""
    peak = max(hours) or 1
    cx = cy = 60
    inner, outer = 16, 56
    wedges = []
    for h, m in enumerate(hours):
        if not m:
            continue
        r = inner + (outer - inner) * math.sqrt(m / peak)
        a0, a1 = math.radians(h * 15 - 90 + 1), math.radians((h + 1) * 15 - 90 - 1)
        pts = [(cx + inner * math.cos(a0), cy + inner * math.sin(a0)), (cx + r * math.cos(a0), cy + r * math.sin(a0)),
               (cx + r * math.cos(a1), cy + r * math.sin(a1)), (cx + inner * math.cos(a1), cy + inner * math.sin(a1))]
        d = (f"M{pts[0][0]:.1f},{pts[0][1]:.1f} L{pts[1][0]:.1f},{pts[1][1]:.1f} "
             f"A{r:.1f},{r:.1f} 0 0 1 {pts[2][0]:.1f},{pts[2][1]:.1f} L{pts[3][0]:.1f},{pts[3][1]:.1f} "
             f"A{inner},{inner} 0 0 0 {pts[0][0]:.1f},{pts[0][1]:.1f}Z")
        wedges.append(f'<path class="wedge" d="{d}" style="animation-delay:{h * 25}ms">'
                      f'<title>{h:02d}:00–{(h + 1) % 24:02d}:00 · {m} min</title></path>')
    ticks = "".join(f'<text x="{cx + 62 * math.cos(math.radians(t * 15 - 90)):.1f}" '
                    f'y="{cy + 62 * math.sin(math.radians(t * 15 - 90)) + 3:.1f}" text-anchor="middle">{t}</text>'
                    for t in (0, 6, 12, 18))
    busiest = max(range(24), key=lambda i: hours[i])
    label = (f"Busiest hour {busiest:02d}:00 with {hours[busiest]} minutes" if any(hours) else "No work recorded")
    return (f'<svg class="clock" viewBox="-8 -8 136 136" role="img" aria-label="When you worked: {label}">'
            f'<circle cx="{cx}" cy="{cy}" r="{outer}" class="face"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{inner}" class="hub"/>'
            f'{"".join(wedges)}{ticks}</svg>')


# ------------------------------------------------------------------ 5-week calendar
def heatmap(activity: dict[date, int], day: date, weeks: int = 5) -> str:
    """GitHub-style grid of the last `weeks` weeks (Mon–Sun rows), ending with `day`'s week."""
    end = day + timedelta(days=6 - day.weekday())
    start = end - timedelta(days=7 * weeks - 1)
    vals = sorted(v for v in activity.values() if v > 0)
    cuts = [vals[int(len(vals) * q)] for q in (0.25, 0.5, 0.75)] if vals else []
    cells = []
    for i in range(7 * weeks):
        d = start + timedelta(days=i)
        v = activity.get(d, 0)
        level = 0 if v <= 0 else 1 + sum(v > c for c in cuts)
        x, y = (i // 7) * 15, (i % 7) * 15
        future = d > date.today()
        cls = f"cell l{level}" + (" sel" if d == day else "") + (" future" if future else "")
        tip = f"{d:%a %d %b}: " + (_short(v) + " tokens" if v > 1 else "some work" if v else "no work")
        rect = (f'<rect class="{cls}" x="{x}" y="{y}" width="12" height="12" rx="3" '
                f'style="animation-delay:{i * 12}ms"><title>{_e(tip)}</title></rect>')
        cells.append(rect if future else f'<a href="/?d={d.isoformat()}">{rect}</a>')
    days = "".join(f'<text x="-4" y="{r * 15 + 10}" text-anchor="end">{t}</text>'
                   for r, t in ((0, "M"), (2, "W"), (4, "F")))
    return (f'<svg class="heat" viewBox="-14 -2 {weeks * 15 + 14} 108" role="img" '
            f'aria-label="Activity over the last {weeks} weeks">{days}{"".join(cells)}</svg>')


# ------------------------------------------------------------------ projects today
def project_bars(items: list[tuple[str, int]]) -> str:
    """(project name, minutes) as horizontal bars, biggest first."""
    items = [(n, m) for n, m in items if m > 0]
    if not items:
        return ""
    items.sort(key=lambda x: -x[1])
    peak = items[0][1]
    rows = []
    for i, (n, m) in enumerate(items[:8]):
        dur = f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"
        width = max(4, 100 * m / peak)
        rows.append(f'<li><div class="pl"><span>{_e(n)}</span><b>{dur}</b></div><div class="pbar">'
                    f'<span style="width:{width:.0f}%;animation-delay:{i * 90}ms"></span></div></li>')
    return f'<ul class="projbars">{"".join(rows)}</ul>'


# ------------------------------------------------------------------ fun
_BOOKS = (("copies of Moby-Dick", 280_000), ("Harry Potter first books", 100_000),
          ("copies of The Lord of the Rings", 640_000), ("Bibles", 1_000_000), ("average novels", 120_000))


def fun_fact(day: date, tokens: int, output: int, files: int, commits: int, minutes: int) -> str:
    """One playful comparison for the day, the same every time you look at that day."""
    facts = []
    for name, size in _BOOKS:
        n = tokens / size
        if 1 <= n < 1000:
            facts.append(f"{_short(tokens)} tokens ≈ {n:.0f} {name}. Busy day!" if n >= 2
                         else f"{_short(tokens)} tokens ≈ one of the {name}, start to finish.")
    if output >= 7_000:
        facts.append(f"Claude wrote you about {output // 70:,} tweets' worth of text.")
    if files >= 3:
        facts.append(f"You and Claude touched {files} files today.")
    if commits:
        facts.append(f"{commits} commit{'s' * (commits != 1)} shipped. Nice.")
    if minutes >= 45:
        facts.append(f"{minutes // 60}h {minutes % 60:02d}m of pairing ≈ {max(1, minutes // 25)} pomodoros 🍅")
    return facts[day.toordinal() % len(facts)] if facts else ""


ROBOT = """<svg class="robot" viewBox="0 0 120 120" role="img" aria-label="A sleepy robot: nothing recorded yet">
<g class="bob"><rect x="30" y="38" width="60" height="48" rx="14" class="rb"/>
<rect x="56" y="24" width="8" height="14" rx="4" class="rb"/><circle cx="60" cy="22" r="6" class="led"/>
<path d="M42 60 q6 6 12 0" class="eye"/><path d="M66 60 q6 6 12 0" class="eye"/>
<rect x="50" y="72" width="20" height="4" rx="2" class="mouth"/>
<rect x="22" y="54" width="8" height="18" rx="4" class="rb"/>
<rect x="90" y="54" width="8" height="18" rx="4" class="rb"/></g>
<text x="92" y="30" class="z z1">z</text><text x="100" y="20" class="z z2">z</text>
<text x="108" y="10" class="z z3">Z</text></svg>"""
