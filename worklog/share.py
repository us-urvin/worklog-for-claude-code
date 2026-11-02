"""Share cards: a day, a week, a month or a year as one image (SVG), ready to post anywhere.

Two sizes: wide (1200×630, for X, LinkedIn and link previews) and square (1080×1080, for Instagram).
Only totals are shown: hours, sessions, projects, streak, a bar chart of the period, the kind of work and the
top model. Project names appear only if you tick them; prompts, files, accounts and e-mails never do.
The browser turns the SVG into a PNG, so no image library is needed here.
"""
from __future__ import annotations

from datetime import date
from html import escape

from .report import model_label

REPO_URL = "github.com/us-urvin/worklog-for-claude-code"
TAG_COLORS = {"Feature": "#38D9F5", "Bug fix": "#F57BC3", "Review": "#9D8CFF", "Refactor": "#FBBF24",
              "Docs": "#4ADE9B", "Tests": "#60A5FA", "Research": "#FB923C", "Planning": "#A3E635",
              "Release": "#E879F9", "Chat": "#94A3B8"}
SIZES = {"wide": (1200, 630), "square": (1080, 1080)}


def _e(v) -> str:
    return escape(str(v), quote=True)


def _h(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _big_hours(minutes: int) -> tuple[str, str]:
    h, m = divmod(int(minutes), 60)
    if h >= 10 or not m:
        return f"{h}h" if h else f"{m}m", ""
    return f"{h}h", f"{m:02d}m"


def _defs(w: int, h: int) -> str:
    return "".join((
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><defs>',
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0A0D24"/>',
        '<stop offset=".55" stop-color="#140F3A"/><stop offset="1" stop-color="#071A2E"/></linearGradient>',
        '<radialGradient id="b1"><stop offset="0" stop-color="#7C5CFF" stop-opacity=".75"/>',
        '<stop offset="1" stop-color="#7C5CFF" stop-opacity="0"/></radialGradient>',
        '<radialGradient id="b2"><stop offset="0" stop-color="#22D3EE" stop-opacity=".55"/>',
        '<stop offset="1" stop-color="#22D3EE" stop-opacity="0"/></radialGradient>',
        '<radialGradient id="b3"><stop offset="0" stop-color="#F472B6" stop-opacity=".5"/>',
        '<stop offset="1" stop-color="#F472B6" stop-opacity="0"/></radialGradient>',
        '<linearGradient id="t" x1="0" x2="1"><stop offset="0" stop-color="#FFFFFF"/>',
        '<stop offset=".5" stop-color="#B9ABFF"/><stop offset="1" stop-color="#5FE3FA"/></linearGradient>',
        '<linearGradient id="hot" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C4B5FD"/>',
        '<stop offset=".5" stop-color="#5FE3FA"/><stop offset="1" stop-color="#F9A8D4"/></linearGradient>',
        '<linearGradient id="bar" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#7C5CFF"/>',
        '<stop offset="1" stop-color="#22D3EE"/></linearGradient>',
        '<linearGradient id="barhot" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#F472B6"/>',
        '<stop offset="1" stop-color="#FBBF24"/></linearGradient>',
        '<linearGradient id="logo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9D8CFF"/>',
        '<stop offset=".55" stop-color="#38D9F5"/><stop offset="1" stop-color="#F57BC3"/></linearGradient>',
        '<filter id="glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="14"/></filter>',
        '<pattern id="dots" width="26" height="26" patternUnits="userSpaceOnUse">',
        '<circle cx="2" cy="2" r="1.4" fill="#FFFFFF" fill-opacity=".07"/></pattern>',
        '<style>text{font-family:Inter,"Segoe UI",Ubuntu,system-ui,sans-serif;fill:#EEF2FF}',
        '.brand{font-size:22px;font-weight:800;letter-spacing:.18em}.period{font-size:22px;font-weight:600;fill:#DDE3FF}',
        '.title{font-weight:800;fill:url(#t)}.big{font-weight:900;fill:url(#hot)}.unit{font-weight:800;fill:#C7CEEA}',
        '.under{font-weight:600;fill:#C7CEEA}.chip{font-size:24px;font-weight:700}.lab{font-size:15px;fill:#A3AECB}',
        '.tag{font-size:19px;font-weight:700;fill:#0B0F24}.foot{font-size:18px;fill:#A3AECB}</style></defs>',
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/><rect width="{w}" height="{h}" fill="url(#dots)"/>',
        f'<circle cx="{w * .12:.0f}" cy="{h * .08:.0f}" r="{w * .42:.0f}" fill="url(#b1)"/>',
        f'<circle cx="{w * .95:.0f}" cy="{h * .92:.0f}" r="{w * .45:.0f}" fill="url(#b2)"/>',
        f'<circle cx="{w * .78:.0f}" cy="{h * .05:.0f}" r="{w * .28:.0f}" fill="url(#b3)"/>',
    ))


def _logo(x: float, y: float, s: float = 40) -> str:
    k = s / 32
    return (f'<g transform="translate({x:.0f},{y:.0f}) scale({k:.3f})">'
            '<rect width="32" height="32" rx="9" fill="url(#logo)"/>'
            '<rect x="7" y="17" width="4" height="8" rx="2" fill="#0B0F24"/><rect x="14" y="11" width="4" height="14" '
            'rx="2" fill="#0B0F24"/><rect x="21" y="7" width="4" height="18" rx="2" fill="#0B0F24"/></g>')


def _chip(x: float, y: float, text: str, accent: str = "#FFFFFF") -> tuple[str, float]:
    w = 26 + len(text) * 13.2
    return (f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="48" rx="24" fill="#FFFFFF" fill-opacity=".08" '
            f'stroke="{accent}" stroke-opacity=".35"/>'
            f'<text x="{x + 16:.0f}" y="{y + 32:.0f}" class="chip">{_e(text)}</text>',
            w)


def _chart(x: float, y: float, w: float, h: float, series: list[tuple[str, int]]) -> str:
    """A glass panel with one rounded bar per day (or hour, or month); the best one glows warm."""
    if not series or not any(m for _, m in series):
        return ""
    peak = max(m for _, m in series) or 1
    best = max(range(len(series)), key=lambda i: series[i][1])
    n = len(series)
    gap = 6 if n > 20 else 12 if n > 8 else 18
    pad = 26
    bw = (w - 2 * pad - gap * (n - 1)) / n
    base = y + h - 46
    top = y + 34
    out = [f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="26" fill="#FFFFFF" fill-opacity=".06" '
           f'stroke="#FFFFFF" stroke-opacity=".12"/>']
    every = 1 if n <= 12 else 6 if n == 24 else 5
    for i, (label, m) in enumerate(series):
        bx = x + pad + i * (bw + gap)
        bh = max(4.0, (base - top) * m / peak) if m else 4.0
        fill = "url(#barhot)" if i == best and m else "url(#bar)" if m else "#FFFFFF"
        opacity = "" if m else ' fill-opacity=".12"'
        out.append(f'<rect x="{bx:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                   f'rx="{min(bw / 2, 9):.1f}" fill="{fill}"{opacity}/>')
        if i % every == 0 or i == n - 1 and n <= 12:
            out.append(f'<text x="{bx + bw / 2:.1f}" y="{base + 28:.0f}" class="lab" '
                       f'text-anchor="middle">{_e(label)}</text>')
    if series[best][1]:
        bx = x + pad + best * (bw + gap) + bw / 2
        bh = max(4.0, (base - top) * series[best][1] / peak)
        out.append(f'<text x="{bx:.1f}" y="{base - bh - 10:.1f}" class="lab" text-anchor="middle" '
                   f'style="font-weight:800;fill:#FDE68A">{_e(_h(series[best][1]))}</text>')
    return "".join(out)


def _hours_text(big: str, small: str, y: int, size: int, unit_size: int, max_w: float) -> str:
    """The big glowing hours number (a blurred copy behind a sharp one) and its small unit, shrunk to fit `max_w`."""
    em = 0.64  # about how wide one heavy digit is, per pixel of font size
    size = int(min(size, max_w / (em * len(big) + (em * 0.55 * len(small) * unit_size / size if small else 0))))
    unit_size = min(unit_size, int(size * 0.4))
    style = f'class="big" style="font-size:{size}px"'
    out = (f'<text x="50" y="{y}" {style} filter="url(#glow)" opacity=".55">{_e(big)}</text>'
           f'<text x="50" y="{y}" {style}>{_e(big)}</text>')
    if small:
        out += (f'<text x="{50 + em * size * len(big) + 10:.0f}" y="{y}" class="unit" '
                f'style="font-size:{unit_size}px">{_e(small)}</text>')
    return out


def _tags(x: float, y: float, p, max_w: float) -> str:
    total = sum(p.tags.values())
    if not total:
        return ""
    out, cx = [], x
    for tag, mins in p.tags.most_common(4):
        text = f"{tag} {round(100 * mins / total)}%"
        w = 24 + len(text) * 10.6
        if cx + w > x + max_w:
            break
        color = TAG_COLORS.get(tag, "#94A3B8")
        out.append(f'<rect x="{cx:.0f}" y="{y:.0f}" width="{w:.0f}" height="36" rx="18" fill="{color}"/>'
                   f'<text x="{cx + 12:.0f}" y="{y + 25:.0f}" class="tag">{_e(text)}</text>')
        cx += w + 10
    return "".join(out)


def card(title: str, subtitle: str, p, streak: int = 0, names: bool = False, size: str = "wide",
         series: list[tuple[str, int]] | None = None, tags: bool = True) -> str:
    """The SVG card for a period (an insights.Period). `series` is the bar chart: (label, minutes).
    `names` adds the top project names, `tags=False` leaves out the kind of work."""
    w, h = SIZES.get(size, SIZES["wide"])
    big, small = _big_hours(p.minutes)
    n_proj = len([1 for _, m in p.projects.items() if m])
    chips = [f"{p.sessions} session{'s' * (p.sessions != 1)}", f"{n_proj} project{'s' * (n_proj != 1)}"]
    if streak >= 2:
        chips.append(f"🔥 {streak}-day streak")
    top_model = model_label(p.models.most_common(1)[0][0]) if p.models else ""
    parts = [_defs(w, h), _logo(56, 48), '<text x="110" y="78" class="brand">WORKLOG</text>']
    pw = 30 + len(subtitle) * 11.5
    parts.append(f'<rect x="{w - 56 - pw:.0f}" y="44" width="{pw:.0f}" height="46" rx="23" '
                 'fill="#FFFFFF" fill-opacity=".09" '
                 f'stroke="#FFFFFF" stroke-opacity=".18"/><text x="{w - 56 - pw / 2:.0f}" y="75" class="period" '
                 f'text-anchor="middle">{_e(subtitle)}</text>')
    if size == "square":
        parts.append(f'<text x="56" y="190" class="title" style="font-size:62px">{_e(title)}</text>')
        parts.append(_hours_text(big, small, 395, 230, 90, w - 112))
        parts.append('<text x="60" y="450" class="under" style="font-size:32px">of focused work with Claude</text>')
        cx = 56.0
        for c in chips:
            chip, cw = _chip(cx, 490, c, "#FDBA74" if "🔥" in c else "#FFFFFF")
            parts.append(chip)
            cx += cw + 12
        parts.append(_chart(56, 572, w - 112, 300, series or []))
        parts.append(_tags(56, 900, p, w - 112) if tags else "")
        foot_y = 1030
    else:
        parts.append(f'<text x="56" y="170" class="title" style="font-size:52px">{_e(title)}</text>')
        parts.append(_hours_text(big, small, 370, 190, 72, 520))
        parts.append('<text x="58" y="418" class="under" style="font-size:26px">of focused work with Claude</text>')
        cy, cx = 450.0, 56.0
        for c in chips:
            chip, cw = _chip(cx, cy, c, "#FDBA74" if "🔥" in c else "#FFFFFF")
            if cx + cw > 560:
                cy, cx = cy + 58, 56.0
                chip, cw = _chip(cx, cy, c, "#FDBA74" if "🔥" in c else "#FFFFFF")
            parts.append(chip)
            cx += cw + 12
        parts.append(_chart(600, 200, 544, 270, series or []))
        parts.append(_tags(600, 490, p, 544) if tags else "")
        foot_y = 592
    extra = []
    if names and p.projects:
        extra.append("Top: " + ", ".join(n for n, _ in p.projects.most_common(3)))
    if top_model:
        extra.append(f"Mostly {top_model}")
    if extra:
        parts.append(f'<text x="{w - 56}" y="{foot_y}" class="foot" text-anchor="end">{_e(" · ".join(extra))}</text>')
    parts.append(f'<text x="56" y="{foot_y}" class="foot">Made with Worklog for Claude Code</text></svg>')
    return "".join(parts)


def text_post(title: str, subtitle: str, p, streak: int = 0, names: bool = False,
              series: list[tuple[str, int]] | None = None, tags: bool = True) -> str:
    """The same numbers as a ready-to-paste post, one fact per line."""
    lines = [f"🗓️ {title} ({subtitle})", "", f"⏱️ {_h(p.minutes)} of focused work"]
    n_proj = len([1 for _, m in p.projects.items() if m])
    lines.append(f"💬 {p.sessions} session{'s' * (p.sessions != 1)} · 📁 {n_proj} project{'s' * (n_proj != 1)}")
    if streak >= 2:
        lines.append(f"🔥 {streak}-day streak")
    total = sum(p.tags.values()) if tags else 0
    if total:
        lines.append("🧩 " + " · ".join(f"{t} {round(100 * m / total)}%" for t, m in p.tags.most_common(3)))
    if series and any(m for _, m in series):
        label, m = max(series, key=lambda s: s[1])
        if len(series) == 24:
            lines.append(f"📈 Busiest hour: {label}:00 ({_h(m)})")
        elif len(series) == 12:
            lines.append(f"📈 Best month: {label} ({_h(m)})")
        elif len(series) == 7:
            lines.append(f"📈 Best day: {label} ({_h(m)})")
        else:
            lines.append(f"📈 Best day: {label} {subtitle[:3]} ({_h(m)})")
    if p.models:
        lines.append(f"🤖 Mostly {model_label(p.models.most_common(1)[0][0])}")
    if names and p.projects:
        lines.append("📌 " + ", ".join(n for n, _ in p.projects.most_common(3)))
    return "\n".join(lines + ["", "Tracked with Worklog for Claude Code", f"https://{REPO_URL}", "", HASHTAGS])


HASHTAGS = "#ClaudeCode #BuildInPublic"
X_LIMIT = 280


def x_length(text: str) -> int:
    """How X counts a post: a link is always 23, most other symbols and emoji count as 2 (rounded up here)."""
    import re
    n = 0
    for part in re.split(r"(https?://\S+)", text):
        if part.startswith(("http://", "https://")):
            n += 23
        else:
            n += sum(1 if ord(ch) <= 0x10FF or 0x2010 <= ord(ch) <= 0x201F else 2 for ch in part)
    return n


def x_post(title: str, subtitle: str, p, streak: int = 0, names: bool = False, tags: bool = True) -> str:
    """A shorter post that fits X's 280 characters, the link included."""
    n_proj = len([1 for _, m in p.projects.items() if m])
    head = [f"🗓️ {title} ({subtitle})", "",
            f"⏱️ {_h(p.minutes)} of focused work",
            f"💬 {p.sessions} session{'s' * (p.sessions != 1)} · 📁 {n_proj} project{'s' * (n_proj != 1)}"]
    optional = []
    if streak >= 2:
        optional.append(f"🔥 {streak}-day streak")
    total = sum(p.tags.values()) if tags else 0
    if total:
        optional.append("🧩 " + " · ".join(f"{t} {round(100 * m / total)}%" for t, m in p.tags.most_common(2)))
    if names and p.projects:
        optional.append("📌 " + ", ".join(n for n, _ in p.projects.most_common(2)))
    tail = ["", f"Tracked with Worklog for Claude Code https://{REPO_URL}", HASHTAGS]
    while True:
        text = "\n".join(head + optional + tail)
        if x_length(text) <= X_LIMIT or not optional:
            return text
        optional.pop()  # the least important line goes first


def post_links(linkedin_text: str, x_text: str) -> dict[str, str]:
    """Web addresses that open a new post with the text filled in (you may be asked to sign in first)."""
    from urllib.parse import quote
    return {"linkedin": "https://www.linkedin.com/feed/?shareActive=true&text=" + quote(linkedin_text, safe=""),
            "x": "https://x.com/intent/post?text=" + quote(x_text, safe="")}


def text_line(title: str, subtitle: str, p, streak: int = 0) -> str:
    """One line (kept for the image's alt text)."""
    bits = [f"{_h(p.minutes)} of work", f"{p.sessions} sessions", f"{len(p.projects)} projects"]
    if streak >= 2:
        bits.append(f"{streak}-day streak")
    return f"{title} ({subtitle}): " + ", ".join(bits) + "."


def titles(what: str, start: date, end: date) -> tuple[str, str]:
    if what == "day":
        return "My day with Claude", f"{start:%a %d %b %Y}"
    if what == "year":
        return f"My {start.year} with Claude", "Year in review"
    if what == "month":
        return "My month with Claude", f"{start:%B %Y}"
    return "My week with Claude", f"{start:%d %b} – {end:%d %b %Y}"
