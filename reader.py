"""reader.py — THE READER WORKBOOK (Round P1, Jake 2026-10-27; SPEC §19 P1). NO DATA CHANGES.

build_workbook writes the full workbook — every tab, every working column — which the daily run saves as
token_metrics_audit.xlsx. This module derives token_metrics.xlsx from the same build, in memory: About, Summary, the
archetype tabs (Stage 1: A3), Definitions, and a hidden `_data` sheet holding every number the reader shows, so each
value cell is a formula on `_data` and recalculates in Excel or LibreOffice.

Cell rules (P1-2): a value cell holds a number, or one short code — n/a, blocked, not wired, by design — in the code's
colour with the reason in its comment. Headers are at most 35 characters, plain English; the full definition is the
header's comment and a Definitions row. Confidence is the fill of the audit cell each number comes from.

Nothing is lost (P1-9): reader_column_map.csv names every audit column's place in the reader (or "audit only"), and
HEADLINE_LOCATIONS maps every headline the reader shows to its new cell (measuring_guard reads them there).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormatObject, IconSet, Rule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FONT = "Arial"
DATA_SHEET = "_data"
ABOUT, SUMMARY, DEFS = "About", "Summary", "Definitions"
A3_TAB = "A3 Revenue Buyback"
AUDIT_NAME = "token_metrics_audit.xlsx"
STAGE = 1
STAGE_2_TABS = ("Business", "A1", "A2", "A4", "Comparison", "Project View", "Charts")
MAX_HEADER = 35

FMT_USD = '[>=1000000000]$#,##0.0,,,"bn";[>=1000000]$#,##0.0,,"m";$#,##0'
FMT_PRICE = "$#,##0.00##"
FMT_PCT = "0.0%"
FMT_X = '0.0"x"'
FMT_TOK = "#,##0"
FMT_RANK = "0"
TEXT_FMT = "@"

# THE FOUR CODES (P1-2): the cell's text, its colour; the reason goes in the comment
CODES = {"n/a": "808080", "blocked": "C00000", "not wired": "C65911", "by design": "44546A"}
PERIODS = (("1D", 1), ("7D", 7), ("30D", 30), ("90D", 90), ("6M", 182), ("1Y", 365), ("3Y", 1095))
DEFAULT_PERIOD = "90D"
MOVE_THRESHOLD = 0.10                      # P1-7: "what changed" lists moves above 10%
ARCHETYPE_NAMES = {1: "A1 Infrastructure", 2: "A2 Coordination", 3: "A3 Revenue Buyback", 4: "A4 Permanent Burn"}
ARCHETYPE_KEYS = {
    1: ("Settlement per token, 1-year change",
        "SETTLEMENT VOLUME PER CIRCULATING TOKEN, 1-YEAR CHANGE = (trailing-365-day settlement volume ÷ circulating "
        "supply, now) ÷ (the same a year earlier) − 1. Settlement volume is Artemis's DEX + NFT + P2P transfers, the "
        "series A1's NRR reads; it needs two years of history."),
    2: ("Customer revenue ÷ emissions (x)",
        "CUSTOMER REVENUE ÷ EMISSIONS $ (x) — customer revenue over the last 90 days ÷ the tokens emitted to suppliers "
        "over the same days at their 90-day average price (A2's column)."),
    3: ("Retired ÷ emissions (x)",
        "RETIRED BUYBACK ÷ EMISSIONS (x) — tokens bought back and burned or held, per covered day, ÷ tokens emitted per "
        "covered day, unlocks EXCLUDED, at the emission rate in force (Sky's scheduled rate where declared, else the "
        "last 90 days' measured rate). 1.0 is the crossover."),
    4: ("Burn ÷ issuance (x)",
        "BURN ÷ ISSUANCE (x) — tokens burned ÷ tokens issued over the last 90 days, each per covered day; for a "
        "pre-minted supply, burn ÷ pool release. The last 90 days' measured issuance is the rate in force (no A4 "
        "project declares a scheduled rate)."),
}

# STATE OF THE LAST BUILD — read by measuring_guard and the tests
HEADLINE_LOCATIONS: dict = {}               # (audit sheet, project, audit header) -> (reader sheet, cell)
STATS: dict = {}
COLUMN_MAP: list = []


# ============================================================================================ reading the audit build
@dataclass
class Src:
    """One audit cell, evaluated: its number (or None), its text, its fill and comment, and where it is."""
    sheet: str
    coord: str
    value: object = None
    num: float | None = None
    text: str = ""
    fill: PatternFill | None = None
    comment: str = ""

    @property
    def where(self) -> str:
        return f"audit file, {self.sheet}!{self.coord}"


class Audit:
    """The built workbook (openpyxl, in memory) and its values (xlcalc): cells found by tab, header and project."""

    def __init__(self, wb):
        import xlcalc                                      # noqa: PLC0415
        self.wb, self.X = wb, xlcalc.Workbook(wb)
        self._cols: dict = {}
        self._rows: dict = {}

    def cols(self, sheet: str, header_row: int = 4) -> dict:
        if sheet not in self._cols:
            ws = self.wb[sheet] if sheet in self.wb.sheetnames else None
            out = {}
            if ws is not None:
                for c in range(1, ws.max_column + 1):
                    h = ws.cell(header_row, c).value
                    if isinstance(h, str) and h not in out:
                        out[h] = c
            self._cols[sheet] = out
        return self._cols[sheet]

    def col(self, sheet: str, header: str) -> int | None:
        """The column of `header` on `sheet` — exact, else the one header starting with it."""
        cols = self.cols(sheet)
        if header in cols:
            return cols[header]
        hits = [c for h, c in cols.items() if h.startswith(header)]
        return hits[0] if len(hits) == 1 else None

    def header(self, sheet: str, col: int) -> str:
        return str(self.wb[sheet].cell(4, col).value or "")

    def row(self, sheet: str, project: str) -> int | None:
        if sheet not in self._rows:
            ws = self.wb[sheet] if sheet in self.wb.sheetnames else None
            out = {}
            if ws is not None:
                for r in range(5, ws.max_row + 1):
                    v = ws.cell(r, 1).value
                    if v == "Project":                     # a second table below the first (A1, A2): the first wins
                        break
                    if isinstance(v, str) and v and ws.cell(r, 2).value and v not in out:
                        out[v] = r
            self._rows[sheet] = out
        return self._rows[sheet].get(project)

    def at(self, sheet: str, row: int, col: int) -> Src:
        import sanity                                      # noqa: PLC0415
        ws = self.wb[sheet]
        c = ws.cell(row, col)
        try:
            v = self.X.value(sheet, row, col)
        except Exception as e:  # noqa: BLE001 — an unevaluable cell is no number, said in its reason
            v = f"#ERROR {type(e).__name__}"
        n = sanity.stated_number(v)
        fill = c.fill if c.fill is not None and c.fill.fill_type == "solid" else None
        return Src(sheet, c.coordinate, v, None if n is None else float(n),
                   "" if v is None else str(v), fill, c.comment.text if c.comment else "")

    def cell(self, sheet: str, header: str, project: str) -> Src | None:
        r, c = self.row(sheet, project), self.col(sheet, header)
        if r is None or c is None:
            return None
        return self.at(sheet, r, c)

    def data_field(self, key: str, header: str):
        """A field of the Data tab's row for `project|metric`."""
        if "Data" not in self._rows:
            ws = self.wb["Data"]
            self._rows["Data"] = {ws.cell(r, 1).value: r for r in range(2, ws.max_row + 1)}
            self._cols["Data"] = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
        r, c = self._rows["Data"].get(key), self._cols["Data"].get(header)
        if r is None or c is None:
            return None
        return self.X.value("Data", r, c)


# ============================================================================================ codes and plain words
def code_of(text: str) -> str:
    """One of the four codes for a cell that holds no number (P1-2)."""
    t = str(text or "").strip().lower()
    if t.startswith("blocked") or "blocked —" in t or "cannot form" in t:
        return "blocked"
    if "not wired" in t or "unwired" in t or "route not filled" in t or "route pending" in t:
        return "not wired"
    if ("none available" in t or "by design" in t or "suppressed" in t or "closed" in t or "structural" in t
            or "not applicable" in t or "programme halted" in t):
        return "by design"
    return "n/a"


_ROUND_TAG = re.compile(r"\s*\((?:[^()]*?\b(?:Round [A-Z]\d*\w*|[GDP]\d{1,2}(?:[- ]?\d+[a-z]?)*|config\.?\s?\w*|"
                        r"decision \d+ on \w+)\b[^()]*)\)")


def plain(text: str) -> str:
    """Internal codes out of reader text (P1-2): the round / config parentheticals go, Q0 reads "last 90 days"."""
    s = _ROUND_TAG.sub("", str(text or ""))
    s = re.sub(r"\bconfig\.(\w+)", r"\1", s)
    s = re.sub(r"\bQ0\b", "last 90 days", s)
    s = re.sub(r"\bQ-1\b", "the 90 days before", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _code_reason(src: Src | None, why: str = "") -> str:
    if src is None:
        return why or "no audit cell for this project"
    parts = [why] if why else []
    if src.text:
        parts.append(plain(src.text))
    if src.comment and plain(src.comment) not in parts:
        parts.append(plain(src.comment))
    parts.append(f"Working: {src.where}.")
    return "\n\n".join(p for p in parts if p)[:2400]


def reader_format(audit_fmt: str | None) -> str:
    f = str(audit_fmt or "")
    if "%" in f:
        return FMT_PCT
    if '"x"' in f or f.endswith("x"):
        return FMT_X
    if "$" in f:
        return FMT_USD
    return FMT_TOK


# ============================================================================================ the hidden data sheet
class Data:
    """`_data`: one row per project, one column per field; every number the reader shows is here."""

    def __init__(self, ws):
        self.ws = ws
        self.rows: dict = {}
        self.cols: dict = {}
        ws.cell(1, 1, "project")

    def _row(self, project: str) -> int:
        if project not in self.rows:
            self.rows[project] = len(self.rows) + 2
            self.ws.cell(self.rows[project], 1, project)
        return self.rows[project]

    def _col(self, field_id: str, label: str | None = None) -> int:
        if field_id not in self.cols:
            self.cols[field_id] = len(self.cols) + 2
            self.ws.cell(1, self.cols[field_id], label or field_id)
        return self.cols[field_id]

    def block(self, field_ids: list, labels: list) -> None:
        """Columns that must sit side by side (a period table): registered at once, labelled by period."""
        for f, lab in zip(field_ids, labels):
            self._col(f, lab)

    def put(self, project: str, field_id: str, value) -> str:
        r, c = self._row(project), self._col(field_id)
        self.ws.cell(r, c, value)
        return f"{DATA_SHEET}!${get_column_letter(c)}${r}"

    def span(self, project: str, field_ids: list) -> tuple[str, str]:
        """(the row's range over field_ids, the label row's range) — for INDEX / MATCH."""
        r = self._row(project)
        c0, c1 = self.cols[field_ids[0]], self.cols[field_ids[-1]]
        a, b = get_column_letter(c0), get_column_letter(c1)
        return f"{DATA_SHEET}!${a}${r}:${b}${r}", f"{DATA_SHEET}!${a}$1:${b}$1"


# ============================================================================================ writing cells
@dataclass
class Col:
    """A reader column: ≤35-character header, full definition, format, block, where its numbers come from."""
    header: str
    definition: str
    fmt: str = FMT_TOK
    block: str = ""
    source: str = ""                        # "<audit sheet>!<column letter>" or how it is computed
    group: int = 0                          # outline level (collapsed group)
    width: float = 14.0
    letter: str = ""
    audit: tuple | None = None              # (sheet, header) it reproduces, for the column map


@dataclass
class Sheet:
    ws: object
    cols: list = field(default_factory=list)
    numbers: int = 0
    texts: int = 0
    codes: int = 0


F_HEAD = Font(name=FONT, size=9, bold=True, color="FFFFFF")
FILL_HEAD = PatternFill("solid", fgColor="1F3864")
F_BODY = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=14, bold=True)
F_SUB = Font(name=FONT, size=9, italic=True, color="595959")
FILL_GROUP = PatternFill("solid", fgColor="D9E1F2")


def _comment(text: str, w: int = 380, h: int = 200) -> Comment:
    c = Comment(text, "token_metrics")
    c.width, c.height = w, h
    return c


def write_headers(sh: Sheet, row: int = 4, block_row: int = 3) -> None:
    ws = sh.ws
    last_block = None
    for i, col in enumerate(sh.cols, start=1):
        assert len(col.header) <= MAX_HEADER, col.header
        col.letter = get_column_letter(i)
        c = ws.cell(row, i, col.header)
        c.font, c.fill = F_HEAD, FILL_HEAD
        c.alignment = Alignment(wrap_text=True, vertical="top")
        defin = plain(col.definition) + (f"\n\nSource: {col.source}." if col.source else "")
        c.comment = _comment(defin[:2400])
        if col.block and col.block != last_block:
            b = ws.cell(block_row, i, col.block)
            b.font = Font(name=FONT, size=9, bold=True, color="1F3864")
        last_block = col.block
        ws.column_dimensions[col.letter].width = col.width
        if col.group:
            ws.column_dimensions[col.letter].outlineLevel = col.group
            ws.column_dimensions[col.letter].hidden = True
    ws.row_dimensions[row].height = 42
    ws.freeze_panes = ws.cell(row + 1, 3)


def put_number(sh: Sheet, data: Data, row: int, col: Col, project: str, field_id: str, value: float,
               fill: PatternFill | None = None, comment: str = "") -> str:
    ref = data.put(project, field_id, float(value))
    c = sh.ws.cell(row, sh.cols.index(col) + 1, f"={ref}")
    c.number_format, c.font = col.fmt, F_BODY
    if fill is not None:
        c.fill = PatternFill("solid", fgColor=fill.fgColor.rgb)
    if comment:
        c.comment = _comment(comment[:2400])
    sh.numbers += 1
    return c.coordinate


def put_formula(sh: Sheet, row: int, col: Col, formula: str, fill: PatternFill | None = None,
                comment: str = "") -> str:
    c = sh.ws.cell(row, sh.cols.index(col) + 1, formula)
    c.number_format, c.font = col.fmt, F_BODY
    if fill is not None:
        c.fill = PatternFill("solid", fgColor=fill.fgColor.rgb)
    if comment:
        c.comment = _comment(comment[:2400])
    sh.numbers += 1
    return c.coordinate


def put_code(sh: Sheet, row: int, col: Col, code: str, reason: str, fill: PatternFill | None = None) -> str:
    c = sh.ws.cell(row, sh.cols.index(col) + 1, code)
    c.font = Font(name=FONT, size=9, italic=True, color=CODES[code])
    c.alignment = Alignment(horizontal="right")
    if fill is not None:
        c.fill = PatternFill("solid", fgColor=fill.fgColor.rgb)
    c.comment = _comment(reason or code)
    sh.codes += 1
    return c.coordinate


def put_text(sh: Sheet, row: int, col: Col, text: str, comment: str = "", bold: bool = False) -> str:
    c = sh.ws.cell(row, sh.cols.index(col) + 1, text)
    c.font = F_BOLD if bold else F_BODY
    c.number_format = TEXT_FMT                     # a descriptor, never a value cell (P1-2)
    if comment:
        c.comment = _comment(comment[:2400])
    sh.texts += 1
    return c.coordinate


def put_src(sh: Sheet, data: Data, row: int, col: Col, project: str, src: Src | None, why: str = "") -> str:
    """An audit cell's number as a formula on `_data` (its fill, its comment), or its code. A BLANK audit cell (the column
    does not apply to this project — a Sky-only leg, a published-dashboard cross-check) stays blank."""
    if src is not None and src.num is None and not str(src.text or "").strip():
        return ""
    if src is not None and src.num is not None:
        note = plain(src.comment) if src.comment else ""
        return put_number(sh, data, row, col, project, f"{src.sheet}|{src.coord}", src.num, src.fill,
                          (note + "\n\n" if note else "") + f"Working: {src.where}.")
    return put_code(sh, row, col, code_of(src.text if src else ""), _code_reason(src, why),
                    src.fill if src is not None else None)


def icon_arrows(ws, rng: str) -> None:
    """▲ / ► / ▼ for a change: down below −1%, flat within ±1%, up from +1% (P1-2)."""
    iset = IconSet(iconSet="3Arrows", showValue=None, reverse=None,
                   cfvo=[FormatObject(type="num", val=-1e12), FormatObject(type="num", val=-0.01),
                         FormatObject(type="num", val=0.01)])
    ws.conditional_formatting.add(rng, Rule(type="iconSet", iconSet=iset))


# ============================================================================================ period figures (Python)
def _series(groups: dict, project: str, metric: str) -> pd.Series:
    g = groups.get((project, metric)) if groups else None
    if g is None or getattr(g, "empty", True):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = g.assign(date=pd.to_datetime(g["date"])).groupby("date")["value"].last().sort_index()
    return pd.to_numeric(s, errors="coerce").dropna()


def revenue_growth(s: pd.Series, days: int, asof: pd.Timestamp) -> tuple[float | None, str]:
    """(revenue over the P days to the series' last complete day ÷ revenue over the P days before − 1, why not) —
    every day of both windows held (P1-5)."""
    s = s[s.index < asof.normalize()]
    if s.empty:
        return None, "no revenue series"
    end = s.index.max()
    w1 = pd.date_range(end - pd.Timedelta(days=days - 1), end)
    w0 = pd.date_range(w1[0] - pd.Timedelta(days=days), w1[0] - pd.Timedelta(days=1))
    missing = [d for d in w0.append(w1) if d not in s.index]
    if missing:
        return None, (f"needs every day {w0[0].date()}..{end.date()} ({2 * days:,} days); the series is held from "
                      f"{s.index.min().date()}" + (f", {len(missing):,} day(s) missing" if s.index.min() <= w0[0]
                                                    else ""))
    a, b = float(s.loc[w1].sum()), float(s.loc[w0].sum())
    if b == 0:
        return None, f"the earlier {days} days' revenue is 0 — no growth rate"
    return a / b - 1, f"{w1[0].date()}..{end.date()} against {w0[0].date()}..{w0[-1].date()}"


def price_change(s: pd.Series, days: int, asof: pd.Timestamp) -> tuple[float | None, str]:
    s = s[s.index <= asof.normalize()]
    if s.empty:
        return None, "no price series"
    end = s.index.max()
    then = end - pd.Timedelta(days=days)
    if then not in s.index:
        return None, f"no price on {then.date()}; the series is held from {s.index.min().date()}"
    p0 = float(s.loc[then])
    if p0 == 0:
        return None, f"price 0 on {then.date()}"
    return float(s.loc[end]) / p0 - 1, f"{then.date()} to {end.date()}"


def settlement_key(groups: dict, project: str) -> tuple[float | None, str]:
    """A1's key (P1-6): (S365 ÷ circulating, now) ÷ (the same a year earlier) − 1."""
    s = _series(groups, project, "settlement_volume_365d_usd")
    c = _series(groups, project, "circulating_supply_used")
    if s.empty:
        return None, "no trailing-365-day settlement volume series"
    if c.empty:
        return None, "no circulating supply series"
    end = s.index.max()
    then = end - pd.Timedelta(days=365)
    if then not in s.index:
        return None, (f"needs the trailing-365-day settlement volume on {then.date()} (two years of history); held "
                      f"from {s.index.min().date()}")
    cn, ct = c.asof(end), c.asof(then)
    if pd.isna(cn) or pd.isna(ct) or ct <= 0 or cn <= 0:
        return None, f"no circulating supply on {then.date()} or {end.date()}"
    b = float(s.loc[then]) / ct
    if b == 0:
        return None, f"settlement volume 0 on {then.date()}"
    return (float(s.loc[end]) / cn) / b - 1, f"{then.date()} to {end.date()}"


# ============================================================================================ the build
def _green(fill) -> bool:
    import build_workbook as bw                            # noqa: PLC0415
    return fill is not None and str(fill.fgColor.rgb)[-6:] == str(bw.FILL_GREEN.fgColor.rgb)[-6:]


def _primary(p: dict) -> int:
    a = p.get("archetypes") or []
    return int(a[0]) if a else 0


def _rank_rows(keys: dict) -> list:
    """[(project, rank, key or None)] — measured keys high to low, then the unmeasured by name (P1-6)."""
    have = sorted((k for k in keys.items() if k[1] is not None), key=lambda kv: -kv[1])
    rest = sorted(k for k, v in keys.items() if v is None)
    out = [(p, i + 1, v) for i, (p, v) in enumerate(have)]
    return out + [(p, len(out) + i + 1, None) for i, p in enumerate(rest)]


def build_reader(wb, path, *, asof, groups: dict, headline_cells: list, projects: list,
                 previous: dict | None = None) -> dict:
    """Write the reader workbook at `path` from the built (audit) workbook `wb`. Returns its STATS."""
    import build_workbook as bw                            # noqa: PLC0415
    import config                                          # noqa: PLC0415
    HEADLINE_LOCATIONS.clear()
    STATS.clear()
    COLUMN_MAP.clear()
    asof = pd.Timestamp(asof)
    A = Audit(wb)
    out = Workbook()
    ws_about = out.active
    ws_about.title = ABOUT
    ws_sum = out.create_sheet(SUMMARY)
    ws_a3 = out.create_sheet(A3_TAB)
    ws_def = out.create_sheet(DEFS)
    ws_data = out.create_sheet(DATA_SHEET)
    ws_data.sheet_state = "hidden"
    data = Data(ws_data)
    sheets = {SUMMARY: Sheet(ws_sum), A3_TAB: Sheet(ws_a3)}
    keys = _archetype_keys(A, groups, projects, config, bw)
    _write_summary(sheets[SUMMARY], data, A, groups, projects, keys, asof, previous, config, bw)
    _write_a3(sheets[A3_TAB], data, A, projects, keys, headline_cells, bw)
    _write_definitions(ws_def, sheets)
    _write_about(ws_about, asof, sheets)
    out.save(path)
    import workbook_values                                 # noqa: PLC0415
    vals = workbook_values.store_values(path, out)
    STATS.update({"path": str(path), "tabs": [ws.title for ws in out.worksheets],
                  "columns": {n: len(s.cols) for n, s in sheets.items()},
                  "numbers": {n: s.numbers for n, s in sheets.items()},
                  "texts": {n: s.texts for n, s in sheets.items()},
                  "codes": {n: s.codes for n, s in sheets.items()},
                  "formulas": vals.get("formulas"), "failed": vals.get("failed")})
    _write_column_map(Path(path).with_name("reader_column_map.csv"), A, sheets)
    return dict(STATS)


def _archetype_keys(A: Audit, groups, projects, config, bw) -> dict:
    """{project: (primary archetype, key number or None, Src or None, why)} (P1-6)."""
    out = {}
    for p in projects:
        n, arch = p["name"], _primary(p)
        if arch == 1:
            v, why = settlement_key(groups, n)
            out[n] = (arch, v, None, why)
            continue
        sheet, head = {2: ("A2 Coordination", "Customer revenue ÷ emissions value (x)"),
                       3: (A3_TAB, bw.RETIRE_VS_EMISSIONS_HEAD),
                       4: ("A4 Permanent Burn", "BURN ÷ ISSUANCE (x)")}.get(arch, (None, None))
        src = A.cell(sheet, head, n) if sheet else None
        out[n] = (arch, src.num if src is not None else None, src,
                  "" if src is not None else f"{n} has no row on {sheet}" if sheet else "no archetype declared")
    return out


SUMMARY_SOURCES = {
    "mcap": ("Master", "Market cap ($)"),
    "fdv": ("Master", "FDV ($)"),
    "rev_q0": ("Master", "Revenue Q0 ($)"),
    "hvy": ("CP Comparability", "HOLDER VALUE YIELD"),
    "dil": ("CP Comparability", "TOTAL DILUTION = outflows a+b+c+d+e (annualised) ÷ tradeable float"),
    "float": ("CP Comparability", "TRADEABLE FLOAT"),
    "nhy": ("CP Comparability", "NET HOLDER YIELD"),
    "nsc_pct": ("Master", "Net supply change, annualised % of circulating"),
    "nsc_tok": ("Master", "NET SUPPLY CHANGE Q0 (tokens)"),
    "payout": ("CP Comparability", "Payout ratio — the project's own net basis"),
}


def _write_summary(sh: Sheet, data: Data, A: Audit, groups, projects, keys, asof, previous, config, bw) -> None:
    ws = sh.ws
    C = {k: Col(*v) for k, v in {
        "project": ("Project", "The project.", FMT_TOK, "", "", 0, 16),
        "token": ("Token", "The token's symbol.", FMT_TOK, "", "", 0, 8),
        "arch": ("Archetypes", "The archetypes the project is assessed under, primary first: A1 infrastructure, A2 "
                 "coordination, A3 revenue buyback, A4 permanent burn.", FMT_TOK, "", "", 0, 11),
        "mcap": ("Market cap", "Price × the circulating supply the token ratios use.", FMT_USD, "Size",
                 "Master, Market cap ($)", 0, 12),
        "fdv": ("FDV", "Fully diluted value: price × the total supply FDV uses.", FMT_USD, "Size", "Master, FDV ($)",
                0, 12),
        "rev": ("Revenue, annualised", "Revenue over the last 90 days × 365 ÷ the days the series covers (the series "
                "Master's revenue column reads).", FMT_USD, "Business", "Master, Revenue Q0 ($) and Data coverage",
                0, 13),
        "growth": ("Revenue growth, selected period", "Revenue over the selected period to the series' last day ÷ "
                   "revenue over the same number of days before it − 1. '—' where the history is shorter (the comment "
                   "says why, per period).", FMT_PCT, "Business", "computed from the stored revenue series", 0, 13),
        "key": ("Archetype key", "The primary archetype's ranking key, higher is better — see Definitions: "
                + " · ".join(f"A{a}: {k[0]}" for a, k in ARCHETYPE_KEYS.items()), FMT_X, "Rank", "A-tab key columns",
                0, 12),
        "rank": ("Rank in archetype", "Position within the primary archetype by its key; a project with no measured "
                 "key ranks last, the reason in the comment.", FMT_RANK, "Rank", "computed", 0, 9),
        "hvy": ("Holder value yield", "Revenue-funded value to holders a year (retired + distributed + liquid voter "
                "bribes) ÷ market cap.", FMT_PCT, "Holders", "CP Comparability, HOLDER VALUE YIELD", 0, 11),
        "dil": ("Total dilution, % of float", "Every outflow a year (security emissions, supply- and demand-side "
                "incentives, grants, unlocks) ÷ the tradeable float.", FMT_PCT, "Holders",
                "CP Comparability, TOTAL DILUTION ÷ tradeable float", 0, 11),
        "dil_tok": ("Total dilution, tokens a year", "Total dilution % × the tradeable float, in tokens a year.",
                    FMT_TOK, "Holders", "computed: CP total dilution × CP tradeable float", 0, 13),
        "nhy": ("Net holder yield", "Holder value yield − every outflow a year at each day's price ÷ market cap.",
                FMT_PCT, "Holders", "CP Comparability, NET HOLDER YIELD", 0, 11),
        "nsc_pct": ("Net supply change, % a year", "Issuance − burn, annualised, % of circulating supply (+ is "
                    "net inflation).", FMT_PCT, "Supply", "Master, Net supply change, annualised % of circulating",
                    0, 11),
        "nsc_tok": ("Net supply change, 90 days", "Issuance − burn over the last 90 days, in tokens.", FMT_TOK,
                    "Supply", "Master, NET SUPPLY CHANGE Q0 (tokens)", 0, 14),
        "payout": ("Payout ratio", "Credited holder value ÷ revenue on the project's own revenue basis.", FMT_PCT,
                   "Holders", "CP Comparability, Payout ratio (net basis)", 0, 10),
        "price": ("Price change, selected period", "Price on the last day held ÷ price the selected number of days "
                  "before − 1.", FMT_PCT, "Market", "computed from the stored price series", 0, 12),
        "conf": ("Confidence (share green)", "The share of this row's numbers whose source cell is GREEN "
                 "(verified source, confirmed mechanism, no partial window).", FMT_PCT, "Quality", "computed", 0, 11),
        "changed": ("What changed", "The figures on this row that moved more than 10% since the previous run, "
                    "largest first (at most three).", FMT_TOK, "Quality", "the previous run's snapshot", 0, 40),
    }.items()}
    for k, v in SUMMARY_SOURCES.items():
        if k in C:
            C[k].audit = v
    sh.cols = list(C.values())
    ws.cell(1, 1, "Summary — every project, grouped by primary archetype and ranked by its key").font = F_TITLE
    ws.cell(2, 1, "Period").font = F_BOLD
    sel = ws.cell(2, 2, DEFAULT_PERIOD)
    sel.font, sel.fill = Font(name=FONT, size=11, bold=True, color="1F3864"), PatternFill("solid", fgColor="FFF2CC")
    sel.comment = _comment("Choose the period for the two 'selected period' columns: "
                           + ", ".join(p for p, _d in PERIODS) + ". 6M = 182 days, 1Y = 365, 3Y = 1,095.")
    dv = DataValidation(type="list", formula1='"' + ",".join(p for p, _d in PERIODS) + '"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("B2")
    ws.cell(2, 4, f"As of {asof.date()} · hover any header for its definition, any cell for its source").font = F_SUB
    write_headers(sh)
    gfields = [f"growth|{p}" for p, _d in PERIODS]
    pfields = [f"price|{p}" for p, _d in PERIODS]
    data.block(gfields, [p for p, _d in PERIODS])
    data.block(pfields, [p for p, _d in PERIODS])
    prev_date, prev_vals = (previous or {}).get("date") or "", (previous or {}).get("values") or {}
    row = 5
    by_arch: dict = {}
    for p in projects:
        by_arch.setdefault(keys[p["name"]][0], []).append(p)
    pmap = {p["name"]: p for p in projects}
    for arch in sorted(by_arch):
        g = ws.cell(row, 1, f"{ARCHETYPE_NAMES.get(arch, 'No archetype')} — ranked by "
                    f"{ARCHETYPE_KEYS.get(arch, ('no key',))[0].lower()}")
        g.font, g.fill = F_BOLD, FILL_GROUP
        for c in range(2, len(sh.cols) + 1):
            ws.cell(row, c).fill = FILL_GROUP
        row += 1
        for name, rank, kv in _rank_rows({p["name"]: keys[p["name"]][1] for p in by_arch[arch]}):
            p = pmap[name]
            _summary_row(sh, data, A, C, row, p, rank, keys[name], groups, asof, gfields, pfields, prev_date,
                         prev_vals, config, bw)
            row += 1
    ws.auto_filter.ref = None


def _summary_row(sh, data, A, C, row, p, rank, key, groups, asof, gfields, pfields, prev_date, prev_vals, config,
                 bw) -> None:
    name = p["name"]
    put_text(sh, row, C["project"], name, bold=True)
    put_text(sh, row, C["token"], p.get("symbol") or "")
    put_text(sh, row, C["arch"], ", ".join(f"A{a}" for a in (p.get("archetypes") or [])) or "—")
    greens, nums, moved = 0, 0, []
    srcs = {k: A.cell(sheet, head, name) for k, (sheet, head) in SUMMARY_SOURCES.items()}

    def number(k, src, cell_key=None):
        nonlocal greens, nums
        put_src(sh, data, row, C[cell_key or k], name, src)
        if src is not None and src.num is not None:
            nums += 1
            greens += _green(src.fill)
            tab = "Master" if src.sheet == "Master" else src.sheet[:2]
            pv = (prev_vals.get(name) or {}).get(f"{tab} | {A.header(src.sheet, A.col(src.sheet, SUMMARY_SOURCES[k][1]))}")
            if isinstance(pv, (int, float)) and pv and abs(src.num / pv - 1) > MOVE_THRESHOLD:
                moved.append((abs(src.num / pv - 1), f"{C[cell_key or k].header} {src.num / pv - 1:+.0%}"))

    number("mcap", srcs["mcap"])
    number("fdv", srcs["fdv"])
    # revenue annualised = last 90 days × 365 ÷ covered days (the series Master reads)
    rq = srcs["rev_q0"]
    metric = config.revenue_headline_metric(name)
    days = A.data_field(f"{name}|{metric}", "Q0: days of the 90 the series existed for")
    if rq is not None and rq.num is not None and isinstance(days, (int, float)) and days > 0:
        ref_q = data.put(name, "rev_q0", rq.num)
        ref_d = data.put(name, "rev_q0_days", float(days))
        put_formula(sh, row, C["rev"], f"={ref_q}*365/{ref_d}", rq.fill,
                    f"Revenue over the last 90 days {rq.num:,.0f} × 365 ÷ {int(days)} covered days.\n\nWorking: "
                    f"{rq.where}; Data {name}|{metric}.")
        nums += 1
        greens += _green(rq.fill)
    else:
        put_code(sh, row, C["rev"], code_of(rq.text if rq else ""),
                 _code_reason(rq, "" if rq is not None and rq.num is not None else "no revenue figure"),
                 rq.fill if rq else None)
    # the two period columns: precomputed for every period, picked by the selector
    rev_s = _series(groups, name, metric)
    price_s = _series(groups, name, "price_usd")
    for fields, fn, s, col in ((gfields, revenue_growth, rev_s, C["growth"]),
                               (pfields, price_change, price_s, C["price"])):
        why = []
        for f, (lab, d) in zip(fields, PERIODS):
            v, w = fn(s, d, asof)
            data.put(name, f, v if v is not None else "—")
            why.append(f"{lab}: {f'{v:+.1%}' if v is not None else '—'} — {w}")
        rng, labels = data.span(name, fields)
        pick = f"INDEX({rng},1,MATCH($B$2,{labels},0))"
        put_formula(sh, row, col, f'=IF(ISNUMBER({pick}),{pick},"—")', None, "\n".join(why))
    # the archetype key and the rank
    arch, kv, ksrc, kwhy = key
    if kv is not None:
        if ksrc is not None:
            put_src(sh, data, row, C["key"], name, ksrc)
            nums += 1
            greens += _green(ksrc.fill)
        else:
            put_number(sh, data, row, C["key"], name, "key", kv, None, f"{ARCHETYPE_KEYS[arch][1]}\n\n{kwhy}.")
            nums += 1
        put_number(sh, data, row, C["rank"], name, "rank", rank, None,
                   f"{rank} in {ARCHETYPE_NAMES.get(arch, '')} by {ARCHETYPE_KEYS.get(arch, ('',))[0].lower()}.")
    else:
        put_code(sh, row, C["key"], code_of(ksrc.text if ksrc else ""),
                 _code_reason(ksrc, f"No measured key: {kwhy}" if kwhy else "No measured key."),
                 ksrc.fill if ksrc else None)
        put_number(sh, data, row, C["rank"], name, "rank", rank, None,
                   f"Ranked last in its group: no measured {ARCHETYPE_KEYS.get(arch, ('key',))[0].lower()}"
                   + (f" — {kwhy}" if kwhy else "") + ".")
    number("hvy", srcs["hvy"])
    number("dil", srcs["dil"])
    d, f = srcs["dil"], srcs["float"]
    if d is not None and d.num is not None and f is not None and f.num is not None:
        rd, rf = data.put(name, "dil_pct", d.num), data.put(name, "float", f.num)
        put_formula(sh, row, C["dil_tok"], f"={rd}*{rf}", d.fill,
                    f"{d.num:.2%} × tradeable float {f.num:,.0f}.\n\nWorking: {d.where}; {f.where}.")
        nums += 1
        greens += _green(d.fill)
    else:
        bad = d if d is None or d.num is None else f
        put_code(sh, row, C["dil_tok"], code_of(bad.text if bad else ""),
                 _code_reason(bad, "no total dilution or no tradeable float"), bad.fill if bad else None)
    number("nhy", srcs["nhy"])
    number("nsc_pct", srcs["nsc_pct"])
    number("nsc_tok", srcs["nsc_tok"])
    number("payout", srcs["payout"])
    if nums:
        put_number(sh, data, row, C["conf"], name, "confidence", greens / nums, None,
                   f"{greens} of {nums} numbers on this row come from GREEN cells.")
    else:
        put_code(sh, row, C["conf"], "n/a", "No number on this row.")
    if not prev_date:
        txt = "no previous run to compare"
    elif moved:
        txt = "; ".join(t for _m, t in sorted(moved, reverse=True)[:3]) + f" (since {prev_date})"
    else:
        txt = f"no figure moved more than {MOVE_THRESHOLD:.0%} since {prev_date}"
    put_text(sh, row, C["changed"], txt)


# ---------------------------------------------------------------------------------------------- A3
# (audit header prefix, reader header ≤35, block, group level) for every A3 column the reader shows; the order within a
# block is the reader's. Block (e) columns not listed here keep the audit order and an automatic short header.
A3_TRAJECTORY_ID = "a3_retire_vs_dilution"
A3_DRIVERS = (
    ("Base for the implied buyback ($)", "Revenue base, last 90 days"),
    ("Actual buyback Q0 ($)", "Actual buyback, last 90 days"),
    ("Actual buyback Q0 RETIRED (tokens)", "Retired: burned + held (tokens)"),
    ("Emissions Q0 (tokens)", "Emissions, last 90 days (tokens)"),
    ("UNLOCK DILUTION Q0 (tokens)", "Unlocks, last 90 days (tokens)"),
    ("@payout", "Payout ratio"),
    ("CIRCULATING RETIREMENT RATE (tokens)", "Retirement rate, % of circulating"),
    ("PROTOCOL STAKING YIELD (tokens)", "Staking yield (tokens)"),
)
A3_VALUATION = (
    ("Price — spot ($)", "Price"),
    ("Market cap ($)", "Market cap"),
    ("FDV ($)", "FDV"),
    ("Circulating supply", "Circulating supply"),
    ("@hvy", "Holder value yield"),
)
A3_SHORT = {
    "FDV RETIREMENT RATE (tokens)": "Retirement rate, % of FDV supply",
    "Circulating retirement rate ($)": "Retirement rate ($), % of mkt cap",
    "FDV retirement rate ($)": "Retirement rate ($), % of FDV",
    "Materiality": "Materiality",
    "Buyback status": "Buyback status",
    "Programmed?": "Programmed or discretionary",
    "Buyback destination": "Buyback destination",
    "Destination split (share burned)": "Share of buyback burned",
    "Share applied to this window": "Revenue share applied, 90 days",
    "Status of that window's split": "Status of that share",
    "Current documented share": "Current documented share",
    "Per-product split": "Per-product split",
    "Fees Q0 ($)": "Fees, last 90 days",
    "Implied buyback Q0 ($)": "Implied buyback, last 90 days",
    "Price — 90d average ($)": "Price, 90-day average",
    "Implied buyback Q0 (tokens)": "Implied buyback, 90 days (tokens)",
    "Supply figure complete?": "Supply figure complete?",
    "BUYBACK AS % OF SUPPLY (annualised, implied)": "Implied buyback, % of supply a year",
    "Actual buyback Q0 (tokens)": "Actual buyback, 90 days (tokens)",
    "Actual buyback Q0 BURNED (tokens)": "Bought back and burned (tokens)",
    "Actual buyback Q0 HELD (tokens)": "Bought back and held (tokens)",
    "Actual buyback Q0 DISTRIBUTED": "Bought back, paid to stakers",
    "Actual buyback as % of supply (annualised)": "Retired, % of supply a year",
    "Implied − actual ($)": "Implied minus actual buyback ($)",
    "Net absorption Q0 (tokens)": "Net absorption, 90 days (tokens)",
    "Net absorption, implied basis (tokens)": "Net absorption, implied (tokens)",
    "Unlock dilution, annualised % of free float": "Unlocks, % of free float a year",
    "TOTAL DILUTION = emissions + unlocks": "Total dilution, % of float a year",
    "Net absorption incl. unlocks Q0 (tokens)": "Net absorption incl. unlocks",
    "RETIREMENT ÷ DILUTION (x)": "Retired ÷ (emissions + unlocks)",
    "Emissions — the rate IN FORCE": "Emissions in force, a year (tokens)",
    "RETIREMENT ÷ DILUTION at the emission rate IN FORCE": "Retired ÷ dilution, rate in force",
    "Implied SKY purchased, both legs ($)": "Implied purchases, both legs ($)",
    "Implied BURN ($)": "Implied burn ($)",
    "Implied burn (tokens)": "Implied burn (tokens)",
    "Implied burn − ACTUAL burn (tokens)": "Implied minus actual burn (tokens)",
    "Coverage ratio = actual buyback ÷ revenue": "Buyback ÷ revenue (x)",
    "Fees ÷ FDV (annualised)": "Fees ÷ FDV, a year",
    "Tokens locked (ve)": "Tokens locked or staked",
    "Lock rate = locked inside circulating": "Lock rate, % of circulating",
    "Tokens locked — cross-check (protocol dashboard)": "Tokens locked, protocol dashboard",
    "Lock: contract vs dashboard divergence": "Locked: chain vs dashboard gap",
    "Average lock duration (days)": "Average lock duration (days)",
    "3-MONTH T-BILL": "3-month T-bill",
    "Spread over the 3-month T-bill": "Staking yield minus T-bill",
    "Sky Savings Rate": "Sky Savings Rate",
    "  of which BOUGHT on-chain (DEX fills; Q0": "Of which bought on-chain, 90 days",
    "  of which BOUGHT on-chain — FULL HISTORY": "Of which bought on-chain, all time",
    "PROTOCOL STAKING YIELD ($)": "Staking yield ($)",
    "VOTER APR ON VOTING POWER": "Voter APR on voting power",
    "Staking APR as the protocol publishes it": "Staking APR, as published",
    "Share of staker distributions to VIRTUAL": "Share paid to virtual balances",
    "PENDLE still in the OLD vePENDLE": "Still in old vePENDLE (tokens)",
    "sPENDLE + old vePENDLE (tokens)": "sPENDLE + old vePENDLE (tokens)",
    "Effective float": "Effective float (tokens)",
    "Destination effect on float": "Effect on float",
    "Buyback to LOCKED supply": "Buyback to locked supply",
    "Buyback to YIELD PAYOUT": "Buyback paid out as yield",
    "Reserve balance — cross-check": "Reserve balance, dashboard",
    "Contract vs dashboard divergence": "Reserve: chain vs dashboard gap",
    "Umbrella staked $ (Aave only)": "Umbrella staked (Aave, $)",
    "Revenue Δ30d": "Revenue change, 30 days",
    "Revenue Δ3m": "Revenue change, 3 months",
    "Revenue Δ6m": "Revenue change, 6 months",
    "Revenue Δ9m": "Revenue change, 9 months",
    "Buyback % supply at Q1": "Implied buyback %, 3 months ago",
    "Buyback % supply at Q2": "Implied buyback %, 6 months ago",
    "Buyback % supply at Q3": "Implied buyback %, 9 months ago",
}
TREND_SHORT = {
    "a3_circ_retirement": "Quarter change: retirement rate",
    "a3_fdv_retirement": "Quarter change: FDV retirement",
    "a3_implied_buyback_pct": "Quarter change: implied buyback",
    "a3_retired_tokens": "Quarter change: retired tokens",
    "a3_actual_buyback_pct": "Quarter change: retired %",
    "a3_net_absorption": "Quarter change: net absorption",
    "a3_retire_vs_dilution": "Quarter change: retired ÷ dilution",
    "a3_retire_vs_dilution_now": "Quarter change: at rate in force",
    "a3_protocol_yield": "Quarter change: staking yield",
    "a3_buyback_locked": "Quarter change: buyback locked",
}


def short_header(audit_header: str) -> str:
    for k, v in A3_SHORT.items():
        if audit_header.startswith(k):
            return v
    h = plain(re.split(r" = | — |\(", audit_header)[0]).strip().rstrip(",")
    h = h[:1].upper() + h[1:].lower() if h.isupper() else h
    return h[:MAX_HEADER].rstrip()


def _trajectory(A: Audit) -> dict:
    """{key "tab|project|id": [Q0, Q-1, Q-2, Q-3] (numbers or None)} from the Trajectory tab."""
    import sanity                                          # noqa: PLC0415
    if "Trajectory" not in A.wb.sheetnames:
        return {}
    ws = A.wb["Trajectory"]
    hdr = {ws.cell(4, c).value: c for c in range(1, ws.max_column + 1)}
    qc = [hdr.get(q) for q in ("Q0", "Q-1", "Q-2", "Q-3")]
    out = {}
    for r in range(5, ws.max_row + 1):
        k = ws.cell(r, 2).value
        if isinstance(k, str) and k.count("|") == 2 and None not in qc:
            vals = []
            for c in qc:
                try:
                    v = sanity.stated_number(A.X.value("Trajectory", r, c))
                except Exception:  # noqa: BLE001
                    v = None
                vals.append(None if v is None else float(v))
            out[k] = (vals, r)
    return out


def _write_a3(sh: Sheet, data: Data, A: Audit, projects, keys, headline_cells, bw) -> None:
    import config                                          # noqa: PLC0415
    ws, tab = sh.ws, A3_TAB
    names = [p["name"] for p in projects if 3 in (p.get("archetypes") or []) and A.row(tab, p["name"]) is not None]
    if not names:
        ws.cell(1, 1, "A3 — no project in scope")
        return
    audit_cols = A.cols(tab)
    used: set = set()
    cols: list = [Col("Project", "The project.", FMT_TOK, "", "", 0, 16), Col("Token", "The token's symbol.", FMT_TOK,
                                                                                  "", "", 0, 8)]
    spec: list = [None, None]                       # per reader column: ("audit", col) / ("rank",) / ...

    def add(col: Col, how):
        cols.append(col)
        spec.append(how)

    def audit_col(prefix: str):
        c = A.col(tab, prefix)
        if c is not None:
            used.add(c)
        return c

    key_c = audit_col(bw.RETIRE_VS_EMISSIONS_HEAD)
    dil_c, dil_now_c = audit_col(bw.RETIRE_VS_DILUTION_HEAD), audit_col(bw.RETIRE_VS_DILUTION_NOW_HEAD)
    add(Col("Retired ÷ emissions (x)", ARCHETYPE_KEYS[3][1], FMT_X, "(a) Rank key", f"{tab}, {bw.RETIRE_VS_EMISSIONS_HEAD}",
            0, 12, audit=(tab, bw.RETIRE_VS_EMISSIONS_HEAD)), ("audit", key_c))
    add(Col("Rank in A3", "Position by the rank key; no measured key ranks last.", FMT_RANK, "(a) Rank key",
            "computed", 0, 8), ("rank",))
    add(Col("Retired ÷ (emissions + unlocks)", "The same retirement against emissions AND unlocks (at the rate in "
            "force where one is declared) — the unlock-inclusive figure beside the key.", FMT_X, "(a) Rank key",
            f"{tab}, RETIREMENT ÷ DILUTION (at the rate in force where declared)", 0, 13,
            audit=(tab, bw.RETIRE_VS_DILUTION_HEAD)), ("dilution", dil_c, dil_now_c))
    for prefix, short in A3_VALUATION:
        if prefix == "@hvy":
            add(Col(short, "Revenue-funded value to holders a year ÷ market cap.", FMT_PCT, "(b) Valuation",
                    "CP Comparability, HOLDER VALUE YIELD", 0, 11, audit=SUMMARY_SOURCES["hvy"]),
                ("other", SUMMARY_SOURCES["hvy"]))
            continue
        c = audit_col(prefix)
        if c is not None:
            h = A.header(tab, c)
            add(Col(short, h, FMT_PRICE if prefix.startswith("Price") else reader_format(
                A.wb[tab].cell(5, c).number_format), "(b) Valuation", f"{tab}, {h}", 0, 12, audit=(tab, h)),
                ("audit", c))
    traj_heads = ("Retired ÷ dilution, latest quarter", "Retired ÷ dilution, 1 quarter back",
                  "Retired ÷ dilution, 2 quarters back", "Retired ÷ dilution, 3 quarters back")
    for i, h in enumerate(traj_heads):
        add(Col(h, f"RETIREMENT ÷ DILUTION for the 90 days ending {i * 90} days before the as-of date (Trajectory "
                "tab, the same formula on each shifted quarter).", FMT_X, "(c) Trajectory",
                "Trajectory tab, a3_retire_vs_dilution", 0, 11), ("traj", A3_TRAJECTORY_ID, i))
    add(Col("Change vs previous quarter", "(latest quarter − the quarter before) ÷ |the quarter before|; ▲ up "
            "from +1%, ► within ±1%, ▼ down.", FMT_PCT, "(c) Trajectory", "computed from the Trajectory tab", 0, 11),
        ("trend", A3_TRAJECTORY_ID))
    for prefix, short in A3_DRIVERS:
        if prefix == "@payout":
            add(Col(short, "Credited holder value ÷ revenue on the project's own revenue basis.", FMT_PCT,
                    "(d) Drivers", "CP Comparability, Payout ratio (net basis)", 0, 10,
                    audit=SUMMARY_SOURCES["payout"]), ("other", SUMMARY_SOURCES["payout"]))
            continue
        c = audit_col(prefix)
        if c is not None:
            h = A.header(tab, c)
            add(Col(short, h, reader_format(A.wb[tab].cell(5, c).number_format), "(d) Drivers", f"{tab}, {h}", 0,
                    13, audit=(tab, h)), ("audit", c))
    # (e) everything else: audit order, collapsed; Detail → Project View (Stage 2); Notes / flags → the project comment
    note_c, flag_c = A.col(tab, "Notes"), A.col(tab, "Data flags")
    used |= {1, 2, note_c, flag_c}
    traj_of = {}
    for h, c in audit_cols.items():
        if h.startswith("Trend — "):
            m = re.match(r"Trend — (\S+)", h)
            traj_of[c] = m.group(1) if m else ""
    axis_label = {"CAPTURE": "(e) More: capture", "ALIGNMENT": "(e) More: supply and dilution",
                  "BUSINESS": "(e) More: business", "CONTEXT": "(e) More: context"}
    buckets: dict = {k: [] for k in axis_label}             # (e) in four contiguous groups, audit order within each
    for h, c in sorted(audit_cols.items(), key=lambda kv: kv[1]):
        if c in used or h.startswith("Detail — ") or c in traj_of:
            continue
        axis = str(A.wb[tab].cell(3, c).value or "CONTEXT")
        axis = axis if axis in axis_label else "CONTEXT"
        lab = axis_label[axis]
        fmt = reader_format(A.wb[tab].cell(5, c).number_format)
        is_text = A.wb[tab].cell(5, c).number_format == "@" or "status" in h.lower() or h.endswith("?")
        buckets[axis].append((Col(short_header(h), h, TEXT_FMT if is_text else fmt, lab, f"{tab}, {h}", 1, 13,
                                  audit=(tab, h)), ("text" if is_text else "audit", c)))
        nxt = c + 1
        if nxt in traj_of:
            tid = traj_of[nxt]
            buckets[axis].append((Col(TREND_SHORT.get(tid, "Quarter change")[:MAX_HEADER], f"The quarter-on-quarter "
                                      f"change of the column to the left: (latest quarter − the quarter before) ÷ "
                                      f"|the quarter before| (Trajectory, {tid}); ▲ up from +1%, ► within ±1%, ▼ down.",
                                      FMT_PCT, lab, f"Trajectory tab, {tid}", 1, 11, audit=(tab, A.header(tab, nxt))),
                                  ("trend", tid)))
    for axis in axis_label:
        for col, how in buckets[axis]:
            add(col, how)
    sh.cols = cols
    ws.cell(1, 1, "A3 Revenue Buyback — ranked by retired buyback ÷ emissions").font = F_TITLE
    ws.cell(2, 1, "Blocks: (a) rank key · (b) valuation · (c) trajectory · (d) drivers · (e) more, collapsed (click "
            "+ above the columns). Hover a header for its definition, a cell for its source.").font = F_SUB
    write_headers(sh)
    traj = _trajectory(A)
    hl = {(h["project"], h["header"]): h for h in headline_cells if h.get("sheet") == tab}
    order = _rank_rows({n: _a3_key_value(A, tab, key_c, n) for n in names})
    trend_cols = []
    for i, (name, rank, kv) in enumerate(order):
        row = 5 + i
        r_a = A.row(tab, name)
        notes = [plain(A.at(tab, r_a, c).text) for c in (note_c, flag_c) if c]
        put_text(sh, row, cols[0], name, "\n\n".join(n for n in notes if n), bold=True)
        put_text(sh, row, cols[1], next((p.get("symbol") for p in projects if p["name"] == name), "") or "")
        for j, (col, how) in enumerate(zip(cols, spec)):
            if how is None:
                continue
            kind = how[0]
            if kind == "audit":
                src = A.at(tab, r_a, how[1]) if how[1] else None
                cell = put_src(sh, data, row, col, name, src) or f"{get_column_letter(j + 1)}{row}"
                h = A.header(tab, how[1]) if how[1] else ""
                if (name, h) in hl:
                    HEADLINE_LOCATIONS[(tab, name, h)] = (tab, cell)
            elif kind == "text":
                src = A.at(tab, r_a, how[1])
                put_text(sh, row, col, plain(src.text)[:120] if src.text else "—",
                         _code_reason(src) if src.text and len(plain(src.text)) > 120 else f"Working: {src.where}.")
            elif kind == "rank":
                put_number(sh, data, row, col, name, "a3_rank", rank, None,
                           f"{rank} of {len(order)} by retired buyback ÷ emissions"
                           + ("" if kv is not None else " — no measured key, ranked last") + ".")
            elif kind == "dilution":
                use = how[2] if (name in config.CURRENT_EMISSION_RATE and how[2]) else how[1]
                put_src(sh, data, row, col, name, A.at(tab, r_a, use) if use else None)
            elif kind == "other":
                put_src(sh, data, row, col, name, A.cell(how[1][0], how[1][1], name))
            elif kind == "traj":
                vals, tr = traj.get(f"A3|{name}|{how[1]}", ([None] * 4, None))
                v = vals[how[2]]
                if v is not None:
                    put_number(sh, data, row, col, name, f"traj|{how[1]}|{how[2]}", v, None,
                               f"Working: audit file, Trajectory row {tr}.")
                else:
                    put_code(sh, row, col, "n/a", "No value for this quarter on the Trajectory tab"
                             + (f" (row {tr})." if tr else " (no row)."))
            elif kind == "trend":
                vals, tr = traj.get(f"A3|{name}|{how[1]}", ([None] * 4, None))
                q0, q1 = vals[0], vals[1]
                if q0 is not None and q1 is not None and q1 != 0:
                    r0 = data.put(name, f"traj|{how[1]}|0", q0)
                    r1 = data.put(name, f"traj|{how[1]}|1", q1)
                    put_formula(sh, row, col, f"=({r0}-{r1})/ABS({r1})", None,
                                f"Latest quarter {q0:,.6g} against {q1:,.6g}.\n\nWorking: audit file, Trajectory row "
                                f"{tr}.")
                    trend_cols.append(j + 1)
                else:
                    put_code(sh, row, col, "n/a", "No change: " + ("the quarter before is 0" if q1 == 0 else
                                                                   "a quarter has no value on the Trajectory tab")
                             + (f" (row {tr})." if tr else " (no row)."))
    last = 4 + len(order)
    for c in sorted(set(trend_cols)):
        L = get_column_letter(c)
        icon_arrows(ws, f"{L}5:{L}{last}")
    ws.sheet_properties.outlinePr.summaryRight = False


def _a3_key_value(A: Audit, tab, key_c, name):
    if key_c is None:
        return None
    r = A.row(tab, name)
    return A.at(tab, r, key_c).num if r else None


# ---------------------------------------------------------------------------------------------- Definitions, About
def _write_definitions(ws, sheets: dict) -> None:
    ws.cell(1, 1, "Definitions — every reader column").font = F_TITLE
    heads = ("Tab", "Column", "Header", "Definition", "Format", "Source")
    for i, h in enumerate(heads, start=1):
        c = ws.cell(3, i, h)
        c.font, c.fill = F_HEAD, FILL_HEAD
    r = 4
    for name, sh in sheets.items():
        for col in sh.cols:
            fmt = {FMT_USD: "$ ($m / $bn)", FMT_PCT: "%", FMT_X: "x", FMT_TOK: "number", FMT_RANK: "rank",
                   FMT_PRICE: "$", TEXT_FMT: "text"}.get(col.fmt, col.fmt)
            for i, v in enumerate((name, col.letter, col.header, plain(col.definition), fmt, col.source), start=1):
                c = ws.cell(r, i, v)
                c.font = F_BODY
                c.alignment = Alignment(wrap_text=i == 4, vertical="top")
            r += 1
    for L, w in zip("ABCDEF", (18, 8, 36, 90, 12, 50)):
        ws.column_dimensions[L].width = w
    ws.freeze_panes = "A4"
    r += 1
    ws.cell(r, 1, "Codes in place of a number").font = F_BOLD
    for code, why in (("n/a", "no value — the comment says why (a missing reading, a short history, an unformed "
                       "window)"), ("blocked", "a rule refused the figure (a stale input, a basis change, a lockup "
                                    "read too old) — the comment names the rule"),
                      ("not wired", "no route to the figure yet"),
                      ("by design", "chased and closed, or suppressed on purpose — not missing data")):
        r += 1
        c = ws.cell(r, 1, code)
        c.font = Font(name=FONT, size=10, italic=True, color=CODES[code])
        ws.cell(r, 3, why).font = F_BODY


def _write_about(ws, asof, sheets: dict) -> None:
    lines = [
        ("Token metrics — reader", F_TITLE),
        (f"As of {pd.Timestamp(asof).date()}. Built from the same run as {AUDIT_NAME}, which holds every working "
         "column, the data, the checks and the run log.", F_BODY),
        ("", F_BODY),
        ("The tabs", F_BOLD),
        ("Summary — every project on one row, grouped by primary archetype and ranked by that archetype's key. Pick "
         "the period for the growth and price columns in cell B2.", F_BODY),
        ("A3 Revenue Buyback — the buyback projects in blocks: (a) the rank key, (b) valuation, (c) the last four "
         "quarters with arrows, (d) the drivers, (e) everything else, collapsed (click + above the columns).", F_BODY),
        ("Definitions — every column's full definition and where its numbers come from.", F_BODY),
        (f"Coming next: {', '.join(STAGE_2_TABS)}. Until then those figures are on {AUDIT_NAME}.", F_SUB),
        ("", F_BODY),
        ("Reading a cell", F_BOLD),
        ("A number's fill is its confidence: GREEN verified and usable · AMBER a real number with a caveat (hover it) "
         "· RED not usable. No fill: the figure is computed here from the cells it names.", F_BODY),
        ("A word in place of a number: n/a (no value), blocked (a rule refused it), not wired (no route yet), by design "
         "(closed or suppressed on purpose). The cell's comment says why. A missing figure is never shown as 0. An "
         "empty cell: the column does not apply to that project.", F_BODY),
        ("Formats: $m and $bn for dollars; % for shares and rates; 0.0x for ratios (1.0x is the crossover); tokens as "
         "whole numbers. 'Last 90 days' is the trailing window to the as-of date.", F_BODY),
        ("Hover any header for its definition; hover any number for its source cell in the audit file.", F_BODY),
    ]
    for i, (t, f) in enumerate(lines, start=1):
        c = ws.cell(i, 1, t)
        c.font = f
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 120
    ws.cell(len(lines) + 2, 1, "Tabs in this file: " + ", ".join(
        f"{n} ({len(s.cols)} columns)" for n, s in sheets.items())).font = F_SUB


# ---------------------------------------------------------------------------------------------- the column map
def _write_column_map(path: Path, A: Audit, sheets: dict) -> None:
    """Every audit column → its reader place, or why it stays audit-only (P1-9)."""
    placed: dict = {}
    for name, sh in sheets.items():
        for col in sh.cols:
            if col.audit:
                sheet, head = col.audit
                c = A.col(sheet, head)
                if c is not None:
                    placed.setdefault((sheet, c), []).append((name, col.letter, col.header))
    rows = []
    for sheet in A.wb.sheetnames:
        if sheet in ("Master", A3_TAB):
            for h, c in sorted(A.cols(sheet).items(), key=lambda kv: kv[1]):
                L = get_column_letter(c)
                hits = placed.get((sheet, c))
                if hits:
                    for rt, rl, rh in hits:
                        rows.append((AUDIT_NAME, sheet, L, h, "token_metrics.xlsx", rt, rl, rh, "moved (also kept "
                                     "on the audit file)"))
                    continue
                if h.startswith("Detail — "):
                    why = "audit file; to Project View in Stage 2"
                elif h.startswith("Trend — "):
                    why = "audit file (a trend string); the reader shows it as a quarter change where its figure is shown"
                elif sheet == "Master":
                    why = "audit file; Master's columns go to Summary / Business / Comparison in Stage 2"
                elif h in ("Project", "Symbol"):
                    why = "the row label"
                elif h in ("Notes", "Data flags (stale / manual / missing)"):
                    why = "the reader's project cell comment"
                else:
                    why = "audit file"
                rows.append((AUDIT_NAME, sheet, L, h, "", "", "", "", why))
        else:
            for h, c in sorted(A.cols(sheet).items(), key=lambda kv: kv[1]):
                hits = placed.get((sheet, c))
                L = get_column_letter(c)
                if hits:
                    for rt, rl, rh in hits:
                        rows.append((AUDIT_NAME, sheet, L, h, "token_metrics.xlsx", rt, rl, rh,
                                     "shown on the reader (kept on the audit file)"))
            rows.append((AUDIT_NAME, sheet, "(all)", "(every column)", "", "", "", "",
                         "audit file, unchanged (same tab, same columns)"))
    COLUMN_MAP[:] = rows
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(("audit_file", "audit_tab", "audit_column", "audit_header", "reader_file", "reader_tab",
                    "reader_column", "reader_header", "note"))
        w.writerows(rows)
