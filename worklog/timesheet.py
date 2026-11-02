"""Timesheet: one row per day and project, with hours, a tag and a one-line summary, ready to export.

Hours follow Settings → Summaries → Time (active time by default: breaks over 15 minutes are left out,
and sessions running at the same time count once). The summary is your own edited line if you wrote one,
else Claude's timesheet line, else a plain line from your first prompt (marked as not summarized).
Exports: CSV (opens in Excel), .xlsx (written with the standard library), Markdown and tab-separated text.
"""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from xml.sax.saxutils import escape

from . import overrides
from .report import aggregate, legacy_fit, pick_summary, summary_line, summary_metas
from .store import GitStore

MAX_DAYS = 92
COLUMNS = ("Date", "Project", "Hours", "Tag", "Summary")
EXTRA = ("Accounts", "Devices", "Sessions", "Tokens")


@dataclass
class Row:
    day: date
    project: str
    key: str
    minutes: int
    tag: str
    summary: str
    summarized: bool
    accounts: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    sessions: int = 0
    tokens: int = 0
    approx: bool = False

    def hours(self, round_to: int = 0) -> float:
        m = self.minutes
        if round_to and m:
            m = max(round_to, round(m / round_to) * round_to)  # never round real work down to zero
        return round(m / 60, 2)


def week_bounds(today: date, offset: int = 0) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return start, start + timedelta(days=6)


def month_bounds(today: date, offset: int = 0) -> tuple[date, date]:
    y, m = today.year, today.month + offset
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    start = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start, nxt - timedelta(days=1)


def build(store: GitStore, start: date, end: date, *, time_mode: str = "active", account: str | None = None,
          project: str | None = None) -> list[Row]:
    """Rows for start..end (inclusive), oldest first. Raises ValueError for a range that is too long."""
    if end < start:
        start, end = end, start
    if (end - start).days + 1 > MAX_DAYS:
        raise ValueError(f"Pick at most {MAX_DAYS} days.")
    if not account:  # the day cache already holds every project-day: no need to read the records again
        from .daycache import DayCache
        cache = DayCache(store, time_mode)
        rows = [Row(d, p["name"], p["key"], p["minutes"], p["tag"], p["line"], p["summarized"], p["accounts"],
                    p["devices"], p["sessions"], p["tokens"], p["approx"])
                for d, entry in cache.days(start, end).items() for p in entry.get("projects") or []
                if not project or p["key"] == project]
        cache.save()
        rows.sort(key=lambda r: (r.day, -r.minutes, r.project))
        return rows
    ov = overrides.load(store.path)
    rows = []
    d = start
    while d <= end:
        records = [r for r in store.iter_json(f"records/{d.isoformat()}")
                   if isinstance(r, dict) and (not account or r.get("account") == account)]
        if records:
            _, projects = aggregate(records, ov, time_mode)
            metas = summary_metas(store, d)
            for key, p in projects.items():
                if project and key != project:
                    continue
                sm, fit, _ = pick_summary(metas, key, [x["id"] for x in p.sessions_list], legacy_fit(p, projects))
                if fit == "other":
                    sm = {}
                mine = ov.lines.get(f"{d.isoformat()}|{key}")
                summarized = bool(mine or sm.get("source") == "ai")
                mine_tags = [ov.tags.get(x["id"]) for x in p.sessions_list if ov.tags.get(x["id"])]
                tag = max(set(mine_tags), key=mine_tags.count) if mine_tags else sm.get("tag") or ""
                rows.append(Row(d, p.name, key, p.minutes, tag, summary_line(sm, p, mine), summarized,
                                sorted(p.accounts), sorted(p.devices), p.sessions + p.chats, sum(p.tokens.values()),
                                p.approx))
        d += timedelta(days=1)
    rows.sort(key=lambda r: (r.day, -r.minutes, r.project))
    return rows


def totals(rows: list[Row], round_to: int = 0) -> list[tuple[str, float]]:
    """Hours per project over the whole range, biggest first."""
    per: dict[str, float] = {}
    for r in rows:
        per[r.project] = per.get(r.project, 0) + r.hours(round_to)
    return sorted(((k, round(v, 2)) for k, v in per.items()), key=lambda kv: (-kv[1], kv[0]))


def missing_days(rows: list[Row]) -> list[date]:
    return sorted({r.day for r in rows if not r.summarized})


def _cells(r: Row, round_to: int, extra: bool) -> list:
    cells = [r.day.isoformat(), r.project, r.hours(round_to), r.tag, r.summary]
    if extra:
        cells += [", ".join(r.accounts), ", ".join(r.devices), r.sessions, r.tokens]
    return cells


def _text(v) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def _header(extra: bool) -> list[str]:
    return list(COLUMNS + (EXTRA if extra else ()))


def _safe(v):
    """Spreadsheet apps run cells that start with = + - @ as formulas: keep them as text."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def to_csv(rows: list[Row], round_to: int = 0, extra: bool = False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(_header(extra))
    for r in rows:
        w.writerow([_safe(_text(c)) for c in _cells(r, round_to, extra)])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")  # the BOM makes Excel read UTF-8 correctly


def to_tsv(rows: list[Row], round_to: int = 0, extra: bool = False) -> str:
    """Pastes into Google Sheets, Excel or Jira tables as columns."""
    clean = lambda v: _safe(_text(v)).replace("\t", " ").replace("\n", " ")  # noqa: E731
    lines = ["\t".join(_header(extra))]
    lines += ["\t".join(clean(c) for c in _cells(r, round_to, extra)) for r in rows]
    return "\n".join(lines) + "\n"


def to_markdown(rows: list[Row], round_to: int = 0, extra: bool = False) -> str:
    clean = lambda v: _text(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    head = _header(extra)
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---:" if h in ("Hours", "Sessions", "Tokens") else "---"
                                                          for h in head) + "|"]
    out += ["| " + " | ".join(clean(c) for c in _cells(r, round_to, extra)) + " |" for r in rows]
    if rows:
        total = round(sum(r.hours(round_to) for r in rows), 2)
        out.append("| **Total** |  | **" + f"{total:.2f}" + "** |" + " |" * (len(head) - 3))
    return "\n".join(out) + "\n"


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def to_xlsx(rows: list[Row], round_to: int = 0, extra: bool = False) -> bytes:
    """A real Excel file with one sheet: dates as dates, hours as numbers, text as text (never formulas)."""
    def cell(ref: str, v, style: int = 0) -> str:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'

    xml_rows = ['<row r="1">' + "".join(cell(f"{_col(i)}1", h, 3) for i, h in enumerate(_header(extra))) + "</row>"]
    epoch = date(1899, 12, 30)
    for n, r in enumerate(rows, start=2):
        values = _cells(r, round_to, extra)
        parts = [cell(f"A{n}", (r.day - epoch).days, 1)]  # an Excel date serial, shown as a date
        for i, v in enumerate(values[1:], start=1):
            parts.append(cell(f"{_col(i)}{n}", v, 2 if i == 2 else 0))
        xml_rows.append(f'<row r="{n}">' + "".join(parts) + "</row>")
    if rows:
        n = len(rows) + 2
        xml_rows.append(f'<row r="{n}">' + cell(f"A{n}", "Total", 3) +
                        f'<c r="C{n}" s="4"><f>SUM(C2:C{n - 1})</f></c></row>')
    widths = [12, 28, 8, 12, 80] + ([22, 16, 10, 14] if extra else [])
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, start=1))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              '<xf numFmtId="2" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>'
              '</cellXfs></styleSheet>')
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Timesheet" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/></Relationships>'),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": styles,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()
