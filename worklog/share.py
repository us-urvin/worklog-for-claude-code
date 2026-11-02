"""Share cards: a day, a week or a year as one 1200×630 image (SVG), safe to post.

Only totals are shown: hours, sessions, projects, kind of work, streak and the top model. Project names
appear only if you tick them; prompts, files, accounts and e-mails never do. The browser turns the SVG
into a PNG for downloading, so no image library is needed here.
"""
from __future__ import annotations

from datetime import date
from html import escape

from .report import model_label

TAG_COLORS = {"Feature": "#38D9F5", "Bug fix": "#F57BC3", "Review": "#9D8CFF", "Refactor": "#FBBF24",
              "Docs": "#4ADE9B", "Tests": "#60A5FA", "Research": "#F97316", "Planning": "#A3E635",
              "Release": "#E879F9", "Chat": "#94A3B8"}


_HEAD = "".join((
    '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630"><defs>',
    '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0B0F24"/>',
    '<stop offset="1" stop-color="#1B1446"/></linearGradient>',
    '<radialGradient id="a" cx=".15" cy=".1" r=".6"><stop offset="0" stop-color="#7C5CFF" stop-opacity=".55"/>',
    '<stop offset="1" stop-color="#7C5CFF" stop-opacity="0"/></radialGradient>',
    '<radialGradient id="b" cx=".9" cy=".95" r=".6"><stop offset="0" stop-color="#22D3EE" stop-opacity=".40"/>',
    '<stop offset="1" stop-color="#22D3EE" stop-opacity="0"/></radialGradient>',
    '<linearGradient id="t" x1="0" x2="1"><stop offset="0" stop-color="#EEF2FF"/>',
    '<stop offset=".6" stop-color="#9D8CFF"/><stop offset="1" stop-color="#38D9F5"/></linearGradient>',
    '<style>text{font-family:Inter,"Segoe UI",system-ui,sans-serif;fill:#EEF2FF}',
    '.title{font-size:64px;font-weight:800;fill:url(#t)}.sub{font-size:28px;fill:#A3AECB}',
    '.big{font-size:72px;font-weight:800}.small{font-size:26px;fill:#A3AECB}.tiny{font-size:22px;fill:#A3AECB}</style>',
    '</defs><rect width="1200" height="630" fill="url(#bg)"/><rect width="1200" height="630" fill="url(#a)"/>',
    '<rect width="1200" height="630" fill="url(#b)"/>'))


def _e(v) -> str:
    return escape(str(v), quote=True)


def _h(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def card(title: str, subtitle: str, p, streak: int = 0, names: bool = False) -> str:
    """The SVG card for a period (an insights.Period)."""
    hours = f"{p.minutes // 60}h" if p.minutes >= 600 else _h(p.minutes)  # "41h" fits; "41h 00m" would not
    stats = [(hours, "of work"), (str(p.sessions), "session" if p.sessions == 1 else "sessions"),
             (str(n_proj := len([n for n, m in p.projects.items() if m])), "project" if n_proj == 1 else "projects")]
    if streak >= 2:
        stats.append((f"{streak}", "day streak 🔥"))
    blocks = []
    for i, (big, small) in enumerate(stats):
        x = (80, 400, 660, 900)[i]
        blocks.append(f'<text x="{x}" y="330" class="big">{_e(big)}</text>'
                      f'<text x="{x}" y="372" class="small">{_e(small)}</text>')
    bar, x, total = [], 80.0, sum(p.tags.values())
    legend = []
    if total:
        for i, (tag, mins) in enumerate(p.tags.most_common(5)):
            w = 1040 * mins / total
            color = TAG_COLORS.get(tag, "#94A3B8")
            bar.append(f'<rect x="{x:.1f}" y="430" width="{max(w - 4, 2):.1f}" height="22" rx="8" fill="{color}"/>')
            share = round(100 * mins / total)
            legend.append(f'<circle cx="{88 + i * 205}" cy="489" r="7" fill="{color}"/>'
                          f'<text x="{102 + i * 205}" y="495" class="tiny">{_e(tag)} {share}%</text>')
            x += w
    extra = []
    if names and p.projects:
        top = ", ".join(n for n, _ in p.projects.most_common(3))
        extra.append(f'<text x="80" y="550" class="tiny">Top projects: {_e(top)}</text>')
    if p.models:
        extra.append(f'<text x="1120" y="590" class="tiny" text-anchor="end">Mostly '
                     f'{_e(model_label(p.models.most_common(1)[0][0]))}</text>')
    return (_HEAD + f'<text x="80" y="140" class="title">{_e(title)}</text>'
            f'<text x="80" y="190" class="sub">{_e(subtitle)}</text>'
            + "".join(blocks) + "".join(bar) + "".join(legend) + "".join(extra)
            + '<text x="80" y="590" class="tiny">Worklog for Claude Code</text></svg>')


def text_line(title: str, subtitle: str, p, streak: int = 0) -> str:
    """The same numbers as one line of text, for pasting into a post."""
    bits = [f"{_h(p.minutes)} of work", f"{p.sessions} sessions", f"{len(p.projects)} projects"]
    if p.tags:
        bits.append("mostly " + p.tags.most_common(1)[0][0].lower())
    if streak >= 2:
        bits.append(f"{streak}-day streak 🔥")
    return f"{title} ({subtitle}): " + ", ".join(bits) + ". Tracked with Worklog for Claude Code."


def titles(what: str, start: date, end: date) -> tuple[str, str]:
    if what == "day":
        return "My day with Claude", f"{start:%A %d %B %Y}"
    if what == "year":
        return f"My {start.year} with Claude", "Year in review"
    if what == "month":
        return "My month with Claude", f"{start:%B %Y}"
    return "My week with Claude", f"{start:%d %b} – {end:%d %b %Y}"
