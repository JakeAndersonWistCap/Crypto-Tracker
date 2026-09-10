"""
build_workbook.py — reads the store, writes token_metrics.xlsx from scratch.

Formula policy
  * Raw fetched values (Data / Monthly / Config tabs) are literals — they are source data.
  * Every derived figure on Master and the archetype tabs is an Excel formula that references
    the Data tab (INDEX/MATCH on "project|metric") and the Config & Sources tab, so the maths is
    visible and the assumptions can be flexed in the sheet.
  * Missing values are written as the text "n/a" (never 0) so a true zero and a gap look different.
  * No XLOOKUP/XMATCH/SORT/FILTER/UNIQUE/SEQUENCE. INDEX/MATCH only; sorted in Python.
  * Every denominator is guarded with IFERROR.

Colours: blue = hardcoded input / config lever · black = formula · green = cross-sheet link ·
yellow fill = manual override (entered_on in comment) · grey fill = unconfirmed split, derived
figure suppressed · orange fill = stale (last good fetch in comment).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
from config import GLOBALS, METRICS, PROJECTS

# ---------------------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------------------
FONT = "Arial"
F_BASE = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=14, bold=True)
F_SUB = Font(name=FONT, size=9, italic=True, color="666666")
F_INPUT = Font(name=FONT, size=10, color="0000FF")          # blue: hardcoded inputs / levers
F_INPUT_B = Font(name=FONT, size=10, color="0000FF", bold=True)
F_LINK = Font(name=FONT, size=10, color="008000")           # green: cross-sheet links
F_CALC = Font(name=FONT, size=10, color="000000")           # black: formulas
F_CALC_B = Font(name=FONT, size=10, color="000000", bold=True)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")

FILL_HEAD = PatternFill("solid", fgColor="1F3864")
FILL_HEAD_KEY = PatternFill("solid", fgColor="7B2D26")      # headline column header
FILL_MANUAL = PatternFill("solid", fgColor="FFFF00")
FILL_UNCONFIRMED = PatternFill("solid", fgColor="D9D9D9")
FILL_STALE = PatternFill("solid", fgColor="FCE4D6")
FILL_PAUSED = PatternFill("solid", fgColor="FFE699")
FILL_SECTION = PatternFill("solid", fgColor="EDEDED")
FILL_KEY = PatternFill("solid", fgColor="FFF2CC")           # headline figure cells

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(bottom=THIN)

FMT_USD = '$#,##0;($#,##0);-'
FMT_USD2 = '$#,##0.00;($#,##0.00);-'
FMT_USD4 = '$#,##0.0000;($#,##0.0000);-'
FMT_NUM = '#,##0;(#,##0);-'
FMT_NUM2 = '#,##0.00;(#,##0.00);-'
FMT_PCT = '0.0%;(0.0%);-'
FMT_X = '0.00"x";(0.00"x");-'
FMT_TEXT = '@'

UNIT_FMT = {"usd": FMT_USD, "tokens": FMT_NUM, "count": FMT_NUM, "pct": FMT_PCT, "days": FMT_NUM, "units": FMT_NUM}

CFG = "'Config & Sources'"
NA = '"n/a"'

# Data sheet layout (column letters)
DATA_COLS = ["key", "project", "metric", "label", "kind", "unit", "source", "latest_date",
             "now", "m1", "q0", "q1", "q2", "q3", "y1", "n_points", "status", "last_success", "entered_on", "note"]
DATA_HEAD = ["Key (project|metric)", "Project", "Metric", "Label", "Kind", "Unit", "Source (of latest point)", "Latest date",
             "Now (flow: trailing 30d sum · stock: latest)", "Prior 30d (flow: 30d ending -30d · stock: value at -30d)",
             "Q0 (flow: trailing 90d sum · stock: 90d avg)", "Q1 (90d ending -90d)", "Q2 (90d ending -180d)", "Q3 (90d ending -270d)",
             "Y1 (flow: 365d sum · stock: 365d avg)", "Points", "Status", "Last successful fetch", "Entered on (manual)", "Note"]
DC = {name: get_column_letter(i + 1) for i, name in enumerate(DATA_COLS)}

# Config table layout
CFG_COLS = [
    ("Project", 16), ("Symbol", 8), ("Archetypes", 10), ("Primary", 8), ("Held (not enabled)", 10), ("Materiality", 10),
    ("CoinGecko id", 16), ("DefiLlama fees slug", 14), ("DefiLlama protocol", 14), ("DefiLlama chain", 14),
    ("Share of revenue to buyback", 12), ("Share source URL", 30), ("Share source date", 11), ("Discretionary", 11), ("Buyback status", 11),
    ("Buyback destination", 11), ("Destination split (share burned, where destination = split)", 12), ("Burn execution", 12),
    ("Share of fees burned", 11), ("Burn source URL", 30), ("Burn source date", 11), ("Burn status", 11),
    ("Issuance schedule (tokens/day, current step)", 14), ("Schedule source URL", 30), ("Schedule source date", 11), ("Schedule status", 11),
    ("Notes", 60), ("Status changes", 40),
]
CC = {name: get_column_letter(i + 1) for i, (name, _) in enumerate(CFG_COLS)}
CFG_GLOBAL_ROWS = {"period_days": 3, "short_days": 4, "days_per_year": 5, "stale_after_days": 6}
CFG_HEADER_ROW = 9
CFG_R0 = CFG_HEADER_ROW + 1
CFG_R1 = CFG_R0 + len(PROJECTS) - 1

MONTHLY_METRICS = ["fees_usd", "revenue_usd", "price_usd", "gross_issuance_tokens", "gross_burn_tokens",
                   "customer_revenue_usd", "emissions_tokens", "actual_buyback_usd"]


def G(name: str) -> str:
    return f"{CFG}!$B${CFG_GLOBAL_ROWS[name]}"


ANN = f"({G('days_per_year')}/{G('period_days')})"   # annualisation factor for a period_days window


# ---------------------------------------------------------------------------------------
# Aggregation: long store -> one row per (project, metric)
# ---------------------------------------------------------------------------------------
def _window_sum(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    w = s[(s.index > start) & (s.index <= end)]
    return float(w.sum()) if len(w) else None


def _window_mean(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    w = s[(s.index > start) & (s.index <= end)]
    return float(w.mean()) if len(w) else None


def _at_or_before(s: pd.Series, when: pd.Timestamp) -> float | None:
    w = s[s.index <= when]
    return float(w.iloc[-1]) if len(w) else None


def aggregate(long: pd.DataFrame, fetch_status: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Every project x every metric in the library — so every INDEX/MATCH key resolves."""
    short, period, stale_days = GLOBALS["short_days"], GLOBALS["period_days"], GLOBALS["stale_after_days"]
    last_success = {}
    if fetch_status is not None and not fetch_status.empty:
        for r in fetch_status.itertuples(index=False):
            last_success[(r.source, r.project)] = r.last_success_at
    groups = {k: g for k, g in long.groupby(["project", "metric"])} if not long.empty else {}
    rows = []
    for p in PROJECTS:
        name = p["name"]
        for metric, m in METRICS.items():
            g = groups.get((name, metric))
            row = {"key": f"{name}|{metric}", "project": name, "metric": metric, "label": m["label"],
                   "kind": m["kind"], "unit": m["unit"], "source": "", "latest_date": "", "now": None, "m1": None,
                   "q0": None, "q1": None, "q2": None, "q3": None, "y1": None, "n_points": 0,
                   "status": "missing", "last_success": "", "entered_on": "", "note": ""}
            if g is None or g.empty:
                if metric in config.MANUAL_ONLY_METRICS:
                    row["status"], row["note"] = "manual only", "no API on our tier — enter via manual_overrides.csv"
                elif metric in config.DUNE_ONLY_METRICS:
                    q = (p.get("dune_queries") or {}).get(metric)
                    if q is None:
                        row["status"], row["note"] = "n/a", "not expected for this project"
                    elif not q.get("query_id"):
                        row["status"], row["note"] = "unconfigured", "Dune query_id not set in config.py"
                rows.append(row)
                continue
            g = g.sort_values("date")
            s = g.set_index("date")["value"]
            latest = g.iloc[-1]
            row["source"] = latest["source"]
            row["latest_date"] = latest["date"].strftime("%Y-%m-%d")
            row["n_points"] = int(len(g))
            if m["kind"] == "flow":
                row["now"] = _window_sum(s, asof - pd.Timedelta(days=short), asof)
                row["m1"] = _window_sum(s, asof - pd.Timedelta(days=2 * short), asof - pd.Timedelta(days=short))
                for i, q in enumerate(["q0", "q1", "q2", "q3"]):
                    row[q] = _window_sum(s, asof - pd.Timedelta(days=period * (i + 1)), asof - pd.Timedelta(days=period * i))
                row["y1"] = _window_sum(s, asof - pd.Timedelta(days=365), asof)
            else:
                row["now"] = float(latest["value"])
                row["m1"] = _at_or_before(s, asof - pd.Timedelta(days=short))
                for i, q in enumerate(["q0", "q1", "q2", "q3"]):
                    row[q] = _window_mean(s, asof - pd.Timedelta(days=period * (i + 1)), asof - pd.Timedelta(days=period * i))
                row["y1"] = _window_mean(s, asof - pd.Timedelta(days=365), asof)
            base_source = str(latest["source"]).split(":")[0]
            row["last_success"] = last_success.get((base_source, name), "") or ""
            if bool(latest["is_manual"]):
                row["status"], row["entered_on"] = "manual", latest["entered_on"]
                row["note"] = latest.get("source_note", "") or ""
            elif base_source == "schedule":
                row["status"] = "ok"
            elif (asof - latest["date"]).days > stale_days:
                row["status"] = "stale"
                row["note"] = f"last point {row['latest_date']}; last successful fetch {row['last_success'] or 'never'}"
            else:
                row["status"] = "ok"
            rows.append(row)
    return pd.DataFrame(rows, columns=DATA_COLS)


def monthly_table(long: pd.DataFrame, asof: pd.Timestamp) -> tuple[list[str], pd.DataFrame]:
    n = GLOBALS["months_in_monthly_sheet"]
    end = asof.to_period("M")
    months = [str(end - i) for i in range(n - 1, -1, -1)]
    rows = []
    groups = {k: g for k, g in long.groupby(["project", "metric"])} if not long.empty else {}
    for p in PROJECTS:
        for metric in MONTHLY_METRICS:
            m = METRICS[metric]
            g = groups.get((p["name"], metric))
            row = {"key": f"{p['name']}|{metric}", "label": f"{p['name']} — {m['label']}", "kind": m["kind"], "unit": m["unit"]}
            if g is not None and not g.empty:
                per = g.assign(month=g["date"].dt.to_period("M").astype(str)).groupby("month")["value"]
                agg = per.sum() if m["kind"] == "flow" else per.mean()
                for mo in months:
                    row[mo] = float(agg[mo]) if mo in agg.index else None
            rows.append(row)
    return months, pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------
# Formula helpers
# ---------------------------------------------------------------------------------------
class Refs:
    def __init__(self, data_rows: int, monthly_rows: int, months: list[str]):
        self.d0, self.d1 = 2, data_rows + 1
        self.m0, self.m1 = 2, monthly_rows + 1
        self.months = months
        self.mc0 = "E"                                   # first month column on Monthly
        self.mc1 = get_column_letter(4 + len(months))

    def D(self, r: int, metric: str, col: str = "q0") -> str:
        c = DC[col]
        return f'INDEX(Data!${c}${self.d0}:${c}${self.d1},MATCH($A{r}&"|{metric}",Data!$A${self.d0}:$A${self.d1},0))'

    def C(self, r: int, colname: str) -> str:
        c = CC[colname]
        return f"INDEX({CFG}!${c}${CFG_R0}:${c}${CFG_R1},MATCH($A{r},{CFG}!$A${CFG_R0}:$A${CFG_R1},0))"

    def M(self, r: int, metric: str, month_cell: str) -> str:
        return (f'INDEX(Monthly!${self.mc0}${self.m0}:${self.mc1}${self.m1},'
                f'MATCH($A{r}&"|{metric}",Monthly!$A${self.m0}:$A${self.m1},0),'
                f'MATCH({month_cell},Monthly!${self.mc0}$1:${self.mc1}$1,0))')


def pull(expr: str) -> str:
    return f"={expr}"


def calc(expr: str) -> str:
    return f"=IFERROR({expr},{NA})"


def gated(status_expr: str, expr: str, share_expr: str | None = None) -> str:
    """Suppress the derived figure when the split is unconfirmed; n/a when no share is documented."""
    inner = f"IFERROR({expr},{NA})"
    if share_expr:
        inner = f"IF(ISNUMBER({share_expr}),{inner},{NA})"
    return f'=IF({status_expr}="unconfirmed","unconfirmed",{inner})'


def delta(a: str, b: str) -> str:
    return calc(f"{a}/{b}-1")


# ---------------------------------------------------------------------------------------
# Sheet writers
# ---------------------------------------------------------------------------------------
def _set_widths(ws, widths: dict[str, float]):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _title(ws, title: str, subtitle: str):
    ws["A1"] = title
    ws["A1"].font = F_TITLE
    ws["A2"] = subtitle
    ws["A2"].font = F_SUB
    ws.sheet_view.showGridLines = False


def _header(ws, row: int, headers: list[str], key_cols: set[int] | None = None):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = F_HEAD
        c.fill = FILL_HEAD_KEY if key_cols and i in key_cols else FILL_HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[row].height = 54


def _style(cell, kind: str, fmt: str | None, bold: bool = False):
    if kind == "input":
        cell.font = F_INPUT_B if bold else F_INPUT
    elif kind == "pull":
        cell.font = F_LINK
    elif kind == "calc":
        cell.font = F_CALC_B if bold else F_CALC
    else:
        cell.font = F_BOLD if bold else F_BASE
    if fmt:
        cell.number_format = fmt


def write_config(ws):
    _title(ws, "Config & Sources", "Every fee-split / burn-split / issuance parameter with its source URL and date. Blue cells are levers: change them here and every formula follows. "
                                    "status = active | paused | unconfirmed | n/a. Unconfirmed splits are greyed on the archetype tabs and their derived figures suppressed.")
    ws["A2"].alignment = Alignment(wrap_text=False)
    labels = {"period_days": "Comparison window (days) — the Q0 window on every tab",
              "short_days": "Short window (days) — the 'latest' column",
              "days_per_year": "Days per year (annualisation)",
              "stale_after_days": "Stale after (days without a good fetch)"}
    for key, r in CFG_GLOBAL_ROWS.items():
        ws.cell(row=r, column=1, value=labels[key]).font = F_BASE
        c = ws.cell(row=r, column=2, value=GLOBALS[key])
        _style(c, "input", FMT_NUM)
        c.fill = FILL_KEY
    ws.cell(row=7, column=1, value="Annualisation factor = days per year ÷ comparison window").font = F_SUB
    c = ws.cell(row=7, column=2, value=f"={G('days_per_year')}/{G('period_days')}")
    _style(c, "calc", FMT_NUM2)

    headers = [h for h, _ in CFG_COLS]
    _header(ws, CFG_HEADER_ROW, headers)
    for i, p in enumerate(PROJECTS):
        r = CFG_R0 + i
        fs = p.get("fee_split") or {}
        bs = p.get("burn_split") or {}
        sched = p.get("issuance_schedule") or {}
        cur_step = None
        if sched.get("steps"):
            cur_step = sorted(sched["steps"], key=lambda s: s["from"])[-1]["tokens_per_day"]
        changes = "; ".join(f"{c['date']}: {c['event']} ({c['source_url']})" for c in p.get("status_changes", []))
        values = [
            p["name"], p["symbol"], ", ".join(str(a) for a in p["archetypes"]), p["archetypes"][0],
            ", ".join(str(a) for a in p.get("archetypes_held", [])) or "", p["materiality"],
            p.get("coingecko_id") or "", p.get("defillama_fees_slug") or "", p.get("defillama_protocol") or "", p.get("defillama_chain") or "",
            fs.get("share_to_buyback"), fs.get("source_url") or "", fs.get("source_date") or "",
            ("yes" if fs.get("discretionary") else "no") if fs.get("discretionary") is not None else "", fs.get("status", "n/a"),
            p.get("buyback_destination", ""), p.get("destination_split"), p.get("burn_execution", ""),
            bs.get("share_of_fees_burned") if bs else None, (bs.get("source_url") or "") if bs else "", (bs.get("source_date") or "") if bs else "",
            bs.get("status", "n/a") if bs else "n/a",
            cur_step, sched.get("source_url") or "", sched.get("source_date") or "", sched.get("status", "n/a") if sched else "n/a",
            "; ".join(x for x in [p.get("notes", ""), fs.get("note", ""), (bs or {}).get("note", "")] if x), changes,
        ]
        for j, v in enumerate(values, start=1):
            c = ws.cell(row=r, column=j, value=v)
            head = CFG_COLS[j - 1][0]
            if head in ("Share of revenue to buyback", "Share of fees burned", "Destination split (share burned, where destination = split)"):
                _style(c, "input", FMT_PCT)
            elif head == "Issuance schedule (tokens/day, current step)":
                _style(c, "input", FMT_NUM2)
            elif head in ("Discretionary", "Buyback status", "Buyback destination", "Burn execution", "Burn status", "Schedule status", "Materiality"):
                _style(c, "input", FMT_TEXT)
            else:
                _style(c, "text", FMT_TEXT)
            if head in ("Buyback status", "Burn status") and v == "unconfirmed":
                c.fill = FILL_UNCONFIRMED
            if head == "Buyback status" and v == "paused":
                c.fill = FILL_PAUSED
            if head in ("Notes", "Status changes"):
                c.alignment = Alignment(wrap_text=False)
    _set_widths(ws, {get_column_letter(i + 1): w for i, (_, w) in enumerate(CFG_COLS)})
    ws.freeze_panes = ws.cell(row=CFG_R0, column=3)


def write_data(ws, data: pd.DataFrame, asof: pd.Timestamp):
    _header(ws, 1, DATA_HEAD)
    ws["A1"].comment = Comment(f"Raw window aggregates of the store as of {asof.date()} (UTC). Literals — source data. "
                               "Missing = 'n/a'. Yellow = manual override. Orange = stale.", "token_metrics")
    num_cols = ["now", "m1", "q0", "q1", "q2", "q3", "y1"]
    for i, row in enumerate(data.itertuples(index=False), start=2):
        fmt = UNIT_FMT.get(row.unit, FMT_NUM)
        if row.metric == "price_usd":
            fmt = FMT_USD4
        for j, col in enumerate(DATA_COLS, start=1):
            v = getattr(row, col)
            c = ws.cell(row=i, column=j)
            if col in num_cols:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    c.value = "n/a"
                    _style(c, "text", FMT_TEXT)
                    c.font = Font(name=FONT, size=10, color="999999")
                else:
                    c.value = float(v)
                    _style(c, "input", fmt)
                if row.status == "manual":
                    c.fill = FILL_MANUAL
                    if col == "now":
                        c.comment = Comment(f"MANUAL OVERRIDE entered_on {row.entered_on}. {row.note}", "manual_overrides.csv")
                elif row.status == "stale":
                    c.fill = FILL_STALE
                    if col == "now":
                        c.comment = Comment(f"STALE — {row.note}", "token_metrics")
            elif col == "n_points":
                c.value = int(v)
                _style(c, "text", FMT_NUM)
            else:
                c.value = "" if v is None else str(v)
                _style(c, "text", FMT_TEXT)
                if col == "status":
                    if v == "manual":
                        c.fill = FILL_MANUAL
                    elif v == "stale":
                        c.fill = FILL_STALE
                    elif v in ("missing", "unconfigured", "manual only"):
                        c.font = Font(name=FONT, size=10, color="999999")
    _set_widths(ws, {"A": 34, "B": 14, "C": 24, "D": 30, "E": 6, "F": 7, "G": 20, "H": 11, "I": 16, "J": 16, "K": 16, "L": 16, "M": 16, "N": 16,
                     "O": 16, "P": 7, "Q": 11, "R": 20, "S": 12, "T": 50})
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(DATA_COLS))}{len(data) + 1}"


def write_monthly(ws, months: list[str], monthly: pd.DataFrame):
    heads = ["Key (project|metric)", "Label", "Kind", "Unit"] + months
    _header(ws, 1, heads)
    ws["A1"].comment = Comment("Calendar-month aggregates (flow: sum · stock: average) — literals, source data. Month labels are text.", "token_metrics")
    for i, row in enumerate(monthly.to_dict("records"), start=2):
        ws.cell(row=i, column=1, value=row["key"]).font = F_BASE
        ws.cell(row=i, column=2, value=row["label"]).font = F_BASE
        ws.cell(row=i, column=3, value=row["kind"]).font = F_BASE
        ws.cell(row=i, column=4, value=row["unit"]).font = F_BASE
        fmt = FMT_USD4 if row["key"].endswith("|price_usd") else UNIT_FMT.get(row["unit"], FMT_NUM)
        for j, mo in enumerate(months, start=5):
            v = row.get(mo)
            c = ws.cell(row=i, column=j)
            if v is None or pd.isna(v):
                c.value = "n/a"
                c.font = Font(name=FONT, size=10, color="999999")
            else:
                c.value = float(v)
                _style(c, "input", fmt)
    _set_widths(ws, {"A": 34, "B": 36, "C": 6, "D": 7, **{get_column_letter(j): 13 for j in range(5, 5 + len(months))}})
    ws.freeze_panes = "E2"


# ---------------------------------------------------------------------------------------
# Archetype tabs — column specs. Each spec: (header, builder(r, p) -> value/formula, fmt, kind[, bold])
# ---------------------------------------------------------------------------------------
def _flags(data_by_key: dict, name: str, metrics: list[str]) -> str:
    out = []
    for m in metrics:
        st = data_by_key.get(f"{name}|{m}")
        if st is None:
            continue
        if st["status"] in ("stale", "manual", "missing", "unconfigured", "manual only"):
            out.append(f"{METRICS[m]['label']}: {st['status']}" + (f" ({st['entered_on']})" if st["status"] == "manual" else ""))
    return "; ".join(out)


def _write_table(ws, R: Refs, projects: list[dict], specs: list[tuple], data_by_key: dict, flag_metrics: list[str],
                 start_row: int = 4, key_cols: set[int] | None = None) -> int:
    headers = [s[0] for s in specs] + ["Data flags (stale / manual / missing)"]
    _header(ws, start_row, headers, key_cols)
    r = start_row + 1
    for p in projects:
        for j, spec in enumerate(specs, start=1):
            head, builder, fmt, kind = spec[:4]
            bold = len(spec) > 4 and spec[4]
            v = builder(r, p)
            c = ws.cell(row=r, column=j, value=v)
            _style(c, kind, fmt, bold)
            if kind in ("calc", "pull") and bold:
                c.fill = FILL_KEY
            # visual flags from the underlying data / config
            meta = spec[5] if len(spec) > 5 else {}
            metric = meta.get("metric")
            if metric:
                st = data_by_key.get(f"{p['name']}|{metric}")
                if st is not None:
                    if st["status"] == "manual":
                        c.fill = FILL_MANUAL
                        c.comment = Comment(f"MANUAL OVERRIDE entered_on {st['entered_on']}. {st['note']}", "manual_overrides.csv")
                    elif st["status"] == "stale":
                        c.fill = FILL_STALE
                        c.comment = Comment(f"STALE — {st['note']}", "token_metrics")
            gate = meta.get("gate")
            if gate:
                status = (p.get(gate) or {}).get("status", "n/a")
                if status == "unconfirmed":
                    c.fill = FILL_UNCONFIRMED
                    c.comment = Comment(f"Split unconfirmed — derived figure suppressed until documented in config.py ({gate}).", "token_metrics")
                elif status == "paused" and meta.get("paused_fill"):
                    c.fill = FILL_PAUSED
            sf = meta.get("status_fill")
            if sf:
                status = (p.get(sf) or {}).get("status", "n/a")
                if status == "unconfirmed":
                    c.fill = FILL_UNCONFIRMED
                elif status == "paused":
                    c.fill = FILL_PAUSED
        fc = ws.cell(row=r, column=len(specs) + 1, value=_flags(data_by_key, p["name"], flag_metrics))
        fc.font = Font(name=FONT, size=9, color="C00000")
        r += 1
    ws.freeze_panes = ws.cell(row=start_row + 1, column=3)
    return r


def _trajectory(R: Refs, metric: str, label: str, fmt=FMT_PCT):
    """Δ30d, Δ3m, Δ6m, Δ9m columns for a metric (now vs prior 30d; Q0 vs Q1/Q2/Q3)."""
    return [
        (f"{label} Δ30d", lambda r, p: delta(R.D(r, metric, "now"), R.D(r, metric, "m1")), fmt, "calc"),
        (f"{label} Δ3m", lambda r, p: delta(R.D(r, metric, "q0"), R.D(r, metric, "q1")), fmt, "calc"),
        (f"{label} Δ6m", lambda r, p: delta(R.D(r, metric, "q0"), R.D(r, metric, "q2")), fmt, "calc"),
        (f"{label} Δ9m", lambda r, p: delta(R.D(r, metric, "q0"), R.D(r, metric, "q3")), fmt, "calc"),
    ]


def _lit(field):
    return lambda r, p: p.get(field, "")


def _txt(fn):
    return fn


def write_master(ws, R: Refs, data_by_key: dict):
    _title(ws, "Master — all projects", "All live figures over the comparison window (Q0 = trailing 90 days by default, set on Config & Sources). "
                                          "Token conversions use the 90-day AVERAGE price (labelled); spot price shown for reference only. Green = pulled from Data/Config, black = formula.")
    ann = ANN
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        ("Archetypes (primary first)", lambda r, p: ", ".join(str(a) for a in p["archetypes"]), FMT_TEXT, "text"),
        ("Held", lambda r, p: ", ".join(str(a) for a in p.get("archetypes_held", [])), FMT_TEXT, "text"),
        ("Materiality", lambda r, p: pull(R.C(r, "Materiality")), FMT_TEXT, "pull"),
        ("Buyback status", lambda r, p: pull(R.C(r, "Buyback status")), FMT_TEXT, "pull", False, {"status_fill": "fee_split"}),
        ("Burn status", lambda r, p: pull(R.C(r, "Burn status")), FMT_TEXT, "pull", False, {"status_fill": "burn_split"}),
        ("Price — spot ($)", lambda r, p: pull(R.D(r, "price_usd", "now")), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Price — 90d average ($)", lambda r, p: pull(R.D(r, "price_usd", "q0")), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Market cap ($)", lambda r, p: pull(R.D(r, "market_cap_usd", "now")), FMT_USD, "pull", False, {"metric": "market_cap_usd"}),
        ("Circulating supply (reported)", lambda r, p: pull(R.D(r, "circulating_supply", "now")), FMT_NUM, "pull", False, {"metric": "circulating_supply"}),
        ("FDV ($)", lambda r, p: pull(R.D(r, "fdv_usd", "now")), FMT_USD, "pull", False, {"metric": "fdv_usd"}),
        ("Fees Q0 ($)", lambda r, p: pull(R.D(r, "fees_usd", "q0")), FMT_USD, "pull", False, {"metric": "fees_usd"}),
        ("Revenue Q0 ($)", lambda r, p: pull(R.D(r, "revenue_usd", "q0")), FMT_USD, "pull", False, {"metric": "revenue_usd"}),
        ("TVL ($) — chain for A1 names, protocol otherwise",
         lambda r, p: pull(R.D(r, "tvl_usd" if 1 in p["archetypes"] else "protocol_tvl_usd", "now")), FMT_USD, "pull"),
        ("Implied buyback Q0 ($) = revenue × share", lambda r, p: gated(R.C(r, "Buyback status"), f"{R.D(r, 'revenue_usd', 'q0')}*{R.C(r, 'Share of revenue to buyback')}", R.C(r, 'Share of revenue to buyback')),
         FMT_USD, "calc", False, {"gate": "fee_split"}),
        ("Actual buyback Q0 ($) — observed", lambda r, p: pull(R.D(r, "actual_buyback_usd", "q0")), FMT_USD, "pull", False, {"metric": "actual_buyback_usd"}),
        ("Gross burn Q0 (tokens)", lambda r, p: pull(R.D(r, "gross_burn_tokens", "q0")), FMT_NUM, "pull", False, {"metric": "gross_burn_tokens"}),
        ("Gross issuance Q0 (tokens)", lambda r, p: pull(R.D(r, "gross_issuance_tokens", "q0")), FMT_NUM, "pull", False, {"metric": "gross_issuance_tokens"}),
        ("NET SUPPLY CHANGE Q0 (tokens) = issuance − burn",
         lambda r, p: calc(f"{R.D(r, 'gross_issuance_tokens', 'q0')}-{R.D(r, 'gross_burn_tokens', 'q0')}"), FMT_NUM, "calc", True),
        ("Net supply change, annualised % of circulating",
         lambda r, p: calc(f"({R.D(r, 'gross_issuance_tokens', 'q0')}-{R.D(r, 'gross_burn_tokens', 'q0')})*{ann}/{R.D(r, 'circulating_supply', 'now')}"), FMT_PCT, "calc", True),
        ("Fees ÷ issuance ($, Q0; issuance at 90d avg price)",
         lambda r, p: calc(f"{R.D(r, 'fees_usd', 'q0')}/({R.D(r, 'gross_issuance_tokens', 'q0')}*{R.D(r, 'price_usd', 'q0')})"), FMT_X, "calc"),
        ("Fees ÷ FDV (annualised)", lambda r, p: calc(f"{R.D(r, 'fees_usd', 'q0')}*{ann}/{R.D(r, 'fdv_usd', 'now')}"), FMT_PCT, "calc"),
        ("Buyback % of supply (annualised, implied)",
         lambda r, p: gated(R.C(r, "Buyback status"), f"{R.D(r, 'revenue_usd', 'q0')}*{R.C(r, 'Share of revenue to buyback')}/{R.D(r, 'price_usd', 'q0')}*{ann}/{R.D(r, 'circulating_supply', 'now')}", R.C(r, 'Share of revenue to buyback')),
         FMT_PCT, "calc", False, {"gate": "fee_split"}),
        *_trajectory(R, "fees_usd", "Fees"),
        ("Latest data date (core series)", lambda r, p: max([data_by_key.get(f"{p['name']}|{m}", {}).get("latest_date", "") or "" for m in ("price_usd", "fees_usd", "revenue_usd", "tvl_usd")] or [""]), FMT_TEXT, "text"),
        ("Notes", lambda r, p: p.get("notes", ""), FMT_TEXT, "text"),
    ]
    _write_table(ws, R, PROJECTS, specs, data_by_key,
                 ["price_usd", "fees_usd", "revenue_usd", "circulating_supply", "gross_burn_tokens", "gross_issuance_tokens", "actual_buyback_usd"],
                 key_cols={20, 21})
    _set_widths(ws, {"A": 16, "B": 8, "C": 10, "D": 6, "E": 9, "F": 10, "G": 10, **{get_column_letter(i): 14 for i in range(8, 32)}, "AF": 12, "AG": 60, "AH": 60})


def write_a4(ws, R: Refs, data_by_key: dict):
    projects = [p for p in PROJECTS if 4 in p["archetypes"]]
    _title(ws, "A4 — Permanent Burn", "Gross burn, gross issuance and NET supply change side by side. Net = issuance − burn (positive = net inflation). "
                                       "Q0 = trailing comparison window; $ conversions at 90-day average price. Implied burn = fees × documented share (suppressed when unconfirmed). "
                                       "PancakeSwap is the disclosure template: publishes net mint monthly.")
    ann = ANN
    price = lambda r: R.D(r, "price_usd", "q0")  # noqa: E731
    burn = lambda r, w="q0": R.D(r, "gross_burn_tokens", w)  # noqa: E731
    iss = lambda r, w="q0": R.D(r, "gross_issuance_tokens", w)  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        ("Burn execution", lambda r, p: pull(R.C(r, "Burn execution")), FMT_TEXT, "pull"),
        ("Burn status", lambda r, p: pull(R.C(r, "Burn status")), FMT_TEXT, "pull", False, {"status_fill": "burn_split"}),
        ("Documented share of fees burned (config)", lambda r, p: pull(R.C(r, "Share of fees burned")), FMT_PCT, "pull", False, {"gate": "burn_split"}),
        ("Fees Q0 ($)", lambda r, p: pull(R.D(r, "fees_usd", "q0")), FMT_USD, "pull", False, {"metric": "fees_usd"}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("GROSS BURN Q0 (tokens)", lambda r, p: pull(burn(r)), FMT_NUM, "pull", True, {"metric": "gross_burn_tokens"}),
        ("Gross burn Q0 ($ at avg price)", lambda r, p: calc(f"{burn(r)}*{price(r)}"), FMT_USD, "calc"),
        ("GROSS ISSUANCE Q0 (tokens)", lambda r, p: pull(iss(r)), FMT_NUM, "pull", True, {"metric": "gross_issuance_tokens"}),
        ("Gross issuance Q0 ($ at avg price)", lambda r, p: calc(f"{iss(r)}*{price(r)}"), FMT_USD, "calc"),
        ("NET SUPPLY CHANGE Q0 (tokens) = issuance − burn", lambda r, p: calc(f"{iss(r)}-{burn(r)}"), FMT_NUM, "calc", True),
        ("Net supply change ($ at avg price)", lambda r, p: calc(f"({iss(r)}-{burn(r)})*{price(r)}"), FMT_USD, "calc"),
        ("NET SUPPLY CHANGE, annualised % of circulating", lambda r, p: calc(f"({iss(r)}-{burn(r)})*{ann}/{R.D(r, 'circulating_supply', 'now')}"), FMT_PCT, "calc", True),
        ("Burn ÷ issuance (x)", lambda r, p: calc(f"{burn(r)}/{iss(r)}"), FMT_X, "calc"),
        ("Burn as share of fees (measured)", lambda r, p: calc(f"{burn(r)}*{price(r)}/{R.D(r, 'fees_usd', 'q0')}"), FMT_PCT, "calc"),
        ("Burn as % of supply (annualised)", lambda r, p: calc(f"{burn(r)}*{ann}/{R.D(r, 'circulating_supply', 'now')}"), FMT_PCT, "calc"),
        ("Issuance as % of supply (annualised)", lambda r, p: calc(f"{iss(r)}*{ann}/{R.D(r, 'circulating_supply', 'now')}"), FMT_PCT, "calc"),
        ("Implied burn Q0 (tokens) = fees × documented share ÷ avg price",
         lambda r, p: gated(R.C(r, "Burn status"), f"{R.D(r, 'fees_usd', 'q0')}*{R.C(r, 'Share of fees burned')}/{price(r)}", R.C(r, 'Share of fees burned')), FMT_NUM, "calc", False, {"gate": "burn_split"}),
        ("Actual − implied burn (tokens)", lambda r, p: gated(R.C(r, "Burn status"), f"{burn(r)}-{R.D(r, 'fees_usd', 'q0')}*{R.C(r, 'Share of fees burned')}/{price(r)}", R.C(r, 'Share of fees burned')), FMT_NUM, "calc", False, {"gate": "burn_split"}),
        ("Cross-check: Δ implied circulating supply Q0 vs Q1 (CoinGecko mcap ÷ price)",
         lambda r, p: calc(f"{R.D(r, 'circulating_supply_implied', 'q0')}-{R.D(r, 'circulating_supply_implied', 'q1')}"), FMT_NUM, "calc"),
        ("Net supply change Q1 (tokens, −3m window)", lambda r, p: calc(f"{iss(r, 'q1')}-{burn(r, 'q1')}"), FMT_NUM, "calc"),
        ("Net supply change Q2 (−6m window)", lambda r, p: calc(f"{iss(r, 'q2')}-{burn(r, 'q2')}"), FMT_NUM, "calc"),
        ("Net supply change Q3 (−9m window)", lambda r, p: calc(f"{iss(r, 'q3')}-{burn(r, 'q3')}"), FMT_NUM, "calc"),
        *_trajectory(R, "gross_burn_tokens", "Burn"),
        *_trajectory(R, "gross_issuance_tokens", "Issuance"),
        ("Notes", lambda r, p: "; ".join(x for x in [p.get("notes", ""), (p.get("burn_split") or {}).get("note", "")] if x), FMT_TEXT, "text"),
    ]
    end = _write_table(ws, R, projects, specs, data_by_key, ["fees_usd", "price_usd", "gross_burn_tokens", "gross_issuance_tokens", "circulating_supply"],
                       key_cols={8, 10, 12, 14})
    _set_widths(ws, {"A": 16, "B": 8, "C": 12, "D": 11, "E": 11, **{get_column_letter(i): 14 for i in range(6, 34)}, "AH": 60, "AI": 60})
    return projects, end


def write_a3(ws, R: Refs, data_by_key: dict):
    projects = [p for p in PROJECTS if 3 in p["archetypes"]]
    _title(ws, "A3 — Revenue Buyback", "Pipeline: revenue (DefiLlama) × documented split (config) = implied buyback $ ÷ 90d AVERAGE price = implied tokens ÷ circulating supply, annualised = buyback as % of supply. "
                                        "Actual buyback shown alongside; the gap is itself a signal. Yield destination is tracked separately from burn and never netted against it. "
                                        "Grey = split unconfirmed (derived figure suppressed). Orange status = paused.")
    ann = ANN
    price = lambda r, w="q0": R.D(r, "price_usd", w)  # noqa: E731
    rev = lambda r, w="q0": R.D(r, "revenue_usd", w)  # noqa: E731
    share = lambda r: R.C(r, "Share of revenue to buyback")  # noqa: E731
    circ = lambda r: R.D(r, "circulating_supply", "now")  # noqa: E731
    st = lambda r: R.C(r, "Buyback status")  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        ("Materiality", lambda r, p: pull(R.C(r, "Materiality")), FMT_TEXT, "pull"),
        ("Buyback status", lambda r, p: pull(st(r)), FMT_TEXT, "pull", False, {"status_fill": "fee_split"}),
        ("Discretionary?", lambda r, p: pull(R.C(r, "Discretionary")), FMT_TEXT, "pull"),
        ("Buyback destination", lambda r, p: pull(R.C(r, "Buyback destination")), FMT_TEXT, "pull"),
        ("Destination split (share burned)", lambda r, p: pull(R.C(r, "Destination split (share burned, where destination = split)")), FMT_PCT, "pull"),
        ("Documented share of revenue to buyback (config)", lambda r, p: pull(share(r)), FMT_PCT, "pull", False, {"gate": "fee_split"}),
        ("Revenue Q0 ($)", lambda r, p: pull(rev(r)), FMT_USD, "pull", False, {"metric": "revenue_usd"}),
        ("Fees Q0 ($)", lambda r, p: pull(R.D(r, "fees_usd", "q0")), FMT_USD, "pull", False, {"metric": "fees_usd"}),
        ("Implied buyback Q0 ($) = revenue × share", lambda r, p: gated(st(r), f"{rev(r)}*{share(r)}", share(r)), FMT_USD, "calc", False, {"gate": "fee_split"}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Implied buyback Q0 (tokens) = $ ÷ avg price", lambda r, p: gated(st(r), f"{rev(r)}*{share(r)}/{price(r)}", share(r)), FMT_NUM, "calc", False, {"gate": "fee_split"}),
        ("Circulating supply", lambda r, p: pull(circ(r)), FMT_NUM, "pull", False, {"metric": "circulating_supply"}),
        ("BUYBACK AS % OF SUPPLY (annualised, implied)", lambda r, p: gated(st(r), f"{rev(r)}*{share(r)}/{price(r)}*{ann}/{circ(r)}", share(r)), FMT_PCT, "calc", True, {"gate": "fee_split"}),
        ("Actual buyback Q0 ($) — observed", lambda r, p: pull(R.D(r, "actual_buyback_usd", "q0")), FMT_USD, "pull", False, {"metric": "actual_buyback_usd"}),
        ("Actual buyback Q0 (tokens) — observed", lambda r, p: pull(R.D(r, "actual_buyback_tokens", "q0")), FMT_NUM, "pull", False, {"metric": "actual_buyback_tokens"}),
        ("Actual buyback as % of supply (annualised)", lambda r, p: calc(f"{R.D(r, 'actual_buyback_tokens', 'q0')}*{ann}/{circ(r)}"), FMT_PCT, "calc", True),
        ("Implied − actual ($)", lambda r, p: gated(st(r), f"{rev(r)}*{share(r)}-{R.D(r, 'actual_buyback_usd', 'q0')}", share(r)), FMT_USD, "calc", False, {"gate": "fee_split"}),
        ("Emissions Q0 (tokens) — same period", lambda r, p: pull(R.D(r, "emissions_tokens", "q0")), FMT_NUM, "pull", False, {"metric": "emissions_tokens"}),
        ("Net absorption Q0 (tokens) = actual buyback − emissions", lambda r, p: calc(f"{R.D(r, 'actual_buyback_tokens', 'q0')}-{R.D(r, 'emissions_tokens', 'q0')}"), FMT_NUM, "calc", True),
        ("Net absorption, implied basis (tokens)", lambda r, p: gated(st(r), f"{rev(r)}*{share(r)}/{price(r)}-{R.D(r, 'emissions_tokens', 'q0')}", share(r)), FMT_NUM, "calc", False, {"gate": "fee_split"}),
        ("Coverage ratio = actual buyback ÷ revenue (>1 ⇒ treasury-funded)", lambda r, p: calc(f"{R.D(r, 'actual_buyback_usd', 'q0')}/{rev(r)}"), FMT_X, "calc"),
        ("Fees ÷ FDV (annualised)", lambda r, p: calc(f"{R.D(r, 'fees_usd', 'q0')}*{ann}/{R.D(r, 'fdv_usd', 'now')}"), FMT_PCT, "calc"),
        ("Tokens locked (ve)", lambda r, p: pull(R.D(r, "locked_tokens", "now")), FMT_NUM, "pull", False, {"metric": "locked_tokens"}),
        ("Lock rate = locked ÷ circulating", lambda r, p: calc(f"{R.D(r, 'locked_tokens', 'now')}/{circ(r)}"), FMT_PCT, "calc"),
        ("Average lock duration (days)", lambda r, p: pull(R.D(r, "avg_lock_duration_days", "now")), FMT_NUM, "pull", False, {"metric": "avg_lock_duration_days"}),
        ("Effective float = circulating − locked", lambda r, p: calc(f"{circ(r)}-{R.D(r, 'locked_tokens', 'now')}"), FMT_NUM, "calc"),
        ("Yield destination (tracked separately from burn)", lambda r, p: pull(R.C(r, "Buyback destination")), FMT_TEXT, "pull"),
        *_trajectory(R, "revenue_usd", "Revenue"),
        ("Buyback % supply at Q1 (−3m window, implied)", lambda r, p: gated(st(r), f"{rev(r, 'q1')}*{share(r)}/{price(r, 'q1')}*{ann}/{circ(r)}", share(r)), FMT_PCT, "calc", False, {"gate": "fee_split"}),
        ("Buyback % supply at Q2 (−6m)", lambda r, p: gated(st(r), f"{rev(r, 'q2')}*{share(r)}/{price(r, 'q2')}*{ann}/{circ(r)}", share(r)), FMT_PCT, "calc", False, {"gate": "fee_split"}),
        ("Buyback % supply at Q3 (−9m)", lambda r, p: gated(st(r), f"{rev(r, 'q3')}*{share(r)}/{price(r, 'q3')}*{ann}/{circ(r)}", share(r)), FMT_PCT, "calc", False, {"gate": "fee_split"}),
        ("Notes", lambda r, p: "; ".join(x for x in [p.get("notes", ""), (p.get("fee_split") or {}).get("note", "")] if x), FMT_TEXT, "text"),
    ]
    end = _write_table(ws, R, projects, specs, data_by_key, ["revenue_usd", "fees_usd", "price_usd", "circulating_supply", "actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "locked_tokens"],
                       key_cols={15, 18, 21})
    _set_widths(ws, {"A": 16, "B": 8, "C": 10, "D": 11, "E": 10, "F": 11, "G": 11, **{get_column_letter(i): 14 for i in range(8, 38)}, "AL": 60, "AM": 60})
    return projects, end


def write_a1(ws, R: Refs, data_by_key: dict, months: list[str]):
    projects = [p for p in PROJECTS if 1 in p["archetypes"]]
    _title(ws, "A1 — Infrastructure", "Demand: transactions, fees, fee per tx, active addresses (low weight), stablecoin supply, TVL (primary), RWA on chain (both sources, divergence shown). "
                                       "Supply: gross issuance, staking rate, float, net issuance after burn where an A4 block applies. Key ratio: fees ÷ issuance ($, issuance at 90d average price).")
    ann = ANN
    price = lambda r, w="q0": R.D(r, "price_usd", w)  # noqa: E731
    iss = lambda r, w="q0": R.D(r, "gross_issuance_tokens", w)  # noqa: E731
    fees = lambda r, w="q0": R.D(r, "fees_usd", w)  # noqa: E731
    circ = lambda r: R.D(r, "circulating_supply", "now")  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        ("Transactions Q0", lambda r, p: pull(R.D(r, "tx_count", "q0")), FMT_NUM, "pull", False, {"metric": "tx_count"}),
        ("Fees Q0 ($)", lambda r, p: pull(fees(r)), FMT_USD, "pull", False, {"metric": "fees_usd"}),
        ("Fee per transaction ($)", lambda r, p: calc(f"{fees(r)}/{R.D(r, 'tx_count', 'q0')}"), FMT_USD4, "calc"),
        ("Active addresses (latest, low weight)", lambda r, p: pull(R.D(r, "active_addresses", "now")), FMT_NUM, "pull", False, {"metric": "active_addresses"}),
        ("Stablecoin supply on chain ($)", lambda r, p: pull(R.D(r, "stablecoin_supply_usd", "now")), FMT_USD, "pull", False, {"metric": "stablecoin_supply_usd"}),
        ("TVL — chain ($) [primary demand metric]", lambda r, p: pull(R.D(r, "tvl_usd", "now")), FMT_USD, "pull", True, {"metric": "tvl_usd"}),
        ("RWA on chain — DefiLlama RWA category ($)", lambda r, p: pull(R.D(r, "rwa_defillama_usd", "now")), FMT_USD, "pull", False, {"metric": "rwa_defillama_usd"}),
        ("RWA on chain — RWA.xyz ($, manual)", lambda r, p: pull(R.D(r, "rwa_xyz_usd", "now")), FMT_USD, "pull", False, {"metric": "rwa_xyz_usd"}),
        ("RWA divergence = (RWA.xyz − DefiLlama) ÷ DefiLlama", lambda r, p: calc(f"({R.D(r, 'rwa_xyz_usd', 'now')}-{R.D(r, 'rwa_defillama_usd', 'now')})/{R.D(r, 'rwa_defillama_usd', 'now')}"), FMT_PCT, "calc"),
        ("Gross issuance Q0 (tokens)", lambda r, p: pull(iss(r)), FMT_NUM, "pull", False, {"metric": "gross_issuance_tokens"}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Gross issuance Q0 ($ at avg price)", lambda r, p: calc(f"{iss(r)}*{price(r)}"), FMT_USD, "calc"),
        ("Issuance as % of supply (annualised)", lambda r, p: calc(f"{iss(r)}*{ann}/{circ(r)}"), FMT_PCT, "calc"),
        ("Staked tokens", lambda r, p: pull(R.D(r, "staked_tokens", "now")), FMT_NUM, "pull", False, {"metric": "staked_tokens"}),
        ("Staking rate = staked ÷ circulating", lambda r, p: calc(f"{R.D(r, 'staked_tokens', 'now')}/{circ(r)}"), FMT_PCT, "calc"),
        ("Resulting float = circulating − staked", lambda r, p: calc(f"{circ(r)}-{R.D(r, 'staked_tokens', 'now')}"), FMT_NUM, "calc"),
        ("Gross burn Q0 (tokens, A4 names)", lambda r, p: pull(R.D(r, "gross_burn_tokens", "q0")), FMT_NUM, "pull", False, {"metric": "gross_burn_tokens"}),
        ("Net issuance after burn (tokens) — burn subtracted only where a burn series exists",
         lambda r, p: calc(f"IF(ISNUMBER({R.D(r, 'gross_burn_tokens', 'q0')}),{iss(r)}-{R.D(r, 'gross_burn_tokens', 'q0')},{iss(r)})"), FMT_NUM, "calc"),
        ("FEES ÷ ISSUANCE (x) — key ratio", lambda r, p: calc(f"{fees(r)}/({iss(r)}*{price(r)})"), FMT_X, "calc", True),
        ("Fees ÷ issuance at Q1 (−3m window)", lambda r, p: calc(f"{fees(r, 'q1')}/({iss(r, 'q1')}*{price(r, 'q1')})"), FMT_X, "calc"),
        ("Fees ÷ issuance at Q2 (−6m)", lambda r, p: calc(f"{fees(r, 'q2')}/({iss(r, 'q2')}*{price(r, 'q2')})"), FMT_X, "calc"),
        ("Fees ÷ issuance at Q3 (−9m)", lambda r, p: calc(f"{fees(r, 'q3')}/({iss(r, 'q3')}*{price(r, 'q3')})"), FMT_X, "calc"),
        *_trajectory(R, "tvl_usd", "TVL"),
        *_trajectory(R, "fees_usd", "Fees"),
        *_trajectory(R, "stablecoin_supply_usd", "Stablecoins"),
        ("Notes", lambda r, p: p.get("notes", ""), FMT_TEXT, "text"),
    ]
    end = _write_table(ws, R, projects, specs, data_by_key, ["tx_count", "fees_usd", "tvl_usd", "stablecoin_supply_usd", "rwa_defillama_usd", "rwa_xyz_usd", "gross_issuance_tokens", "price_usd", "staked_tokens", "circulating_supply"],
                       key_cols={8, 21})
    _set_widths(ws, {"A": 16, "B": 8, **{get_column_letter(i): 14 for i in range(3, 38)}, "AK": 60, "AL": 60})
    # Monthly block for the time chart: fees ÷ issuance by month
    block = _monthly_block(ws, R, projects, end + 2, months, "Fees ÷ issuance by month (x) — fees ÷ (issuance tokens × monthly average price)",
                           lambda r, mc: calc(f"{R.M(r, 'fees_usd', mc)}/({R.M(r, 'gross_issuance_tokens', mc)}*{R.M(r, 'price_usd', mc)})"), FMT_X)
    return projects, end, block


def write_a2(ws, R: Refs, data_by_key: dict, months: list[str]):
    projects = [p for p in PROJECTS if 2 in p["archetypes"]]
    _title(ws, "A2 — Coordination Mechanism", "Demand: physical/resource supply units, utilisation, end-user revenue ($). Supply: emissions to suppliers; split between supplier earnings from emissions vs actual customer payment. "
                                               "Key ratio: customer revenue per unit of emission, tracked over time. Most inputs come from Dune queries or manual overrides — see Data flags.")
    price = lambda r, w="q0": R.D(r, "price_usd", w)  # noqa: E731
    rev = lambda r, w="q0": R.D(r, "customer_revenue_usd", w)  # noqa: E731
    emi = lambda r, w="q0": R.D(r, "emissions_tokens", w)  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        ("Materiality", lambda r, p: pull(R.C(r, "Materiality")), FMT_TEXT, "pull"),
        ("Supply units (nodes / hotspots / GPUs), latest", lambda r, p: pull(R.D(r, "supply_units", "now")), FMT_NUM, "pull", False, {"metric": "supply_units"}),
        ("Capacity utilisation (latest)", lambda r, p: pull(R.D(r, "utilisation_pct", "now")), FMT_PCT, "pull", False, {"metric": "utilisation_pct"}),
        ("Customer revenue Q0 ($)", lambda r, p: pull(rev(r)), FMT_USD, "pull", True, {"metric": "customer_revenue_usd"}),
        ("Emissions to suppliers Q0 (tokens)", lambda r, p: pull(emi(r)), FMT_NUM, "pull", True, {"metric": "emissions_tokens"}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Emissions Q0 ($ at avg price)", lambda r, p: calc(f"{emi(r)}*{price(r)}"), FMT_USD, "calc"),
        ("CUSTOMER REVENUE PER TOKEN EMITTED ($/token) — key ratio", lambda r, p: calc(f"{rev(r)}/{emi(r)}"), FMT_USD4, "calc", True),
        ("Customer revenue ÷ emissions value (x)", lambda r, p: calc(f"{rev(r)}/({emi(r)}*{price(r)})"), FMT_X, "calc", True),
        ("Supplier earnings from emissions ($)", lambda r, p: calc(f"{emi(r)}*{price(r)}"), FMT_USD, "calc"),
        ("Supplier earnings from customers ($)", lambda r, p: pull(rev(r)), FMT_USD, "pull"),
        ("Customer share of supplier earnings", lambda r, p: calc(f"{rev(r)}/({rev(r)}+{emi(r)}*{price(r)})"), FMT_PCT, "calc"),
        ("Revenue per supply unit Q0 ($)", lambda r, p: calc(f"{rev(r)}/{R.D(r, 'supply_units', 'now')}"), FMT_USD, "calc"),
        ("Publisher Conviction ($, OriginTrail)", lambda r, p: pull(R.D(r, "publisher_conviction_usd", "now")), FMT_USD, "pull", False, {"metric": "publisher_conviction_usd"}),
        ("Staked tokens (float metric, not demand)", lambda r, p: pull(R.D(r, "staked_tokens", "now")), FMT_NUM, "pull", False, {"metric": "staked_tokens"}),
        ("Rev ÷ emission at Q1 (−3m, $/token)", lambda r, p: calc(f"{rev(r, 'q1')}/{emi(r, 'q1')}"), FMT_USD4, "calc"),
        ("Rev ÷ emission at Q2 (−6m)", lambda r, p: calc(f"{rev(r, 'q2')}/{emi(r, 'q2')}"), FMT_USD4, "calc"),
        ("Rev ÷ emission at Q3 (−9m)", lambda r, p: calc(f"{rev(r, 'q3')}/{emi(r, 'q3')}"), FMT_USD4, "calc"),
        *_trajectory(R, "customer_revenue_usd", "Customer revenue"),
        *_trajectory(R, "supply_units", "Supply units"),
        *_trajectory(R, "emissions_tokens", "Emissions"),
        ("Notes", lambda r, p: p.get("notes", ""), FMT_TEXT, "text"),
    ]
    end = _write_table(ws, R, projects, specs, data_by_key, ["supply_units", "utilisation_pct", "customer_revenue_usd", "emissions_tokens", "price_usd", "publisher_conviction_usd"],
                       key_cols={10, 11})
    _set_widths(ws, {"A": 16, "B": 8, "C": 10, **{get_column_letter(i): 14 for i in range(4, 34)}, "AG": 60, "AH": 60})
    block = _monthly_block(ws, R, projects, end + 2, months, "Customer revenue per token emitted by month ($/token)",
                           lambda r, mc: calc(f"{R.M(r, 'customer_revenue_usd', mc)}/{R.M(r, 'emissions_tokens', mc)}"), FMT_USD4)
    return projects, end, block


def _monthly_block(ws, R: Refs, projects: list[dict], start_row: int, months: list[str], title: str, builder, fmt):
    """A rows=projects x cols=months grid of formulas over the Monthly sheet. Returns (header_row, first_row, last_row, n_months)."""
    ws.cell(row=start_row, column=1, value=title).font = F_BOLD
    hdr = start_row + 1
    ws.cell(row=hdr, column=1, value="Project").font = F_HEAD
    ws.cell(row=hdr, column=1).fill = FILL_HEAD
    for j, mo in enumerate(months, start=2):
        c = ws.cell(row=hdr, column=j, value=mo)   # month label as text — matches Monthly!row 1
        c.font = F_HEAD
        c.fill = FILL_HEAD
        c.alignment = Alignment(horizontal="center")
        c.number_format = FMT_TEXT
    r = hdr + 1
    for p in projects:
        ws.cell(row=r, column=1, value=p["name"]).font = F_BASE
        for j, _ in enumerate(months, start=2):
            mc = f"{get_column_letter(j)}${hdr}"
            c = ws.cell(row=r, column=j, value=builder(r, mc))
            _style(c, "calc", fmt)
        r += 1
    return hdr, hdr + 1, r - 1, len(months)


def write_charts(ws, sheets: dict):
    _title(ws, "Charts", "Native Excel charts — live off the archetype tabs. Change a config lever and they move.")
    a3p, a3_end = sheets["a3"]
    a4p, a4_end = sheets["a4"]
    a1p, a1_end, a1_block = sheets["a1"]
    a2p, a2_end, a2_block = sheets["a2"]
    ws_a3, ws_a4, ws_a1, ws_a2 = sheets["ws_a3"], sheets["ws_a4"], sheets["ws_a1"], sheets["ws_a2"]

    # 1. Buyback as % of supply — all A3 names, single axis (implied vs actual)
    ch = BarChart()
    ch.type = "col"
    ch.title = "Buyback as % of circulating supply (annualised) — archetype 3"
    ch.y_axis.title = "% of supply"
    ch.y_axis.numFmt = "0.0%"
    cats = Reference(ws_a3, min_col=1, min_row=5, max_row=4 + len(a3p))
    ch.add_data(Reference(ws_a3, min_col=15, min_row=4, max_row=4 + len(a3p)), titles_from_data=True)
    ch.add_data(Reference(ws_a3, min_col=18, min_row=4, max_row=4 + len(a3p)), titles_from_data=True)
    ch.set_categories(cats)
    ch.width, ch.height = 28, 12
    ws.add_chart(ch, "A4")

    # 2. Gross burn vs gross issuance — all A4 names, paired bars ($ at 90d avg price)
    ch = BarChart()
    ch.type = "col"
    ch.grouping = "clustered"
    ch.title = "Gross burn vs gross issuance, Q0 ($ at 90d average price) — archetype 4"
    ch.y_axis.title = "$"
    ch.y_axis.numFmt = '$#,##0'
    cats = Reference(ws_a4, min_col=1, min_row=5, max_row=4 + len(a4p))
    ch.add_data(Reference(ws_a4, min_col=9, min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
    ch.add_data(Reference(ws_a4, min_col=11, min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
    ch.set_categories(cats)
    ch.width, ch.height = 28, 12
    ws.add_chart(ch, "A30")

    # 2b. Same, as % of supply annualised — comparable across names
    ch = BarChart()
    ch.type = "col"
    ch.grouping = "clustered"
    ch.title = "Burn vs issuance as % of circulating supply (annualised) — archetype 4"
    ch.y_axis.numFmt = "0.0%"
    cats = Reference(ws_a4, min_col=1, min_row=5, max_row=4 + len(a4p))
    ch.add_data(Reference(ws_a4, min_col=17, min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
    ch.add_data(Reference(ws_a4, min_col=18, min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
    ch.set_categories(cats)
    ch.width, ch.height = 28, 12
    ws.add_chart(ch, "A56")

    # 3. Fees ÷ issuance over time — A1 names (monthly block on the A1 tab)
    hdr, r0, r1, nm = a1_block
    ch = LineChart()
    ch.title = "Fees ÷ issuance by month (x) — archetype 1"
    ch.y_axis.title = "x"
    ch.y_axis.numFmt = '0.00'
    for r in range(r0, r1 + 1):
        ch.add_data(Reference(ws_a1, min_col=1, max_col=1 + nm, min_row=r, max_row=r), from_rows=True, titles_from_data=True)
    ch.set_categories(Reference(ws_a1, min_col=2, max_col=1 + nm, min_row=hdr, max_row=hdr))
    ch.width, ch.height = 28, 12
    ws.add_chart(ch, "A82")

    # 4. Customer revenue per unit of emission over time — A2 names
    hdr, r0, r1, nm = a2_block
    ch = LineChart()
    ch.title = "Customer revenue per token emitted by month ($/token) — archetype 2"
    ch.y_axis.numFmt = '$#,##0.00'
    for r in range(r0, r1 + 1):
        ch.add_data(Reference(ws_a2, min_col=1, max_col=1 + nm, min_row=r, max_row=r), from_rows=True, titles_from_data=True)
    ch.set_categories(Reference(ws_a2, min_col=2, max_col=1 + nm, min_row=hdr, max_row=hdr))
    ch.width, ch.height = 28, 12
    ws.add_chart(ch, "A108")


def write_runlog(ws, runlog: pd.DataFrame, fetch_status: pd.DataFrame, run_id: str | None, asof: pd.Timestamp, overrides_n: int):
    _title(ws, "Run Log", f"Run {run_id or '(build only)'} — workbook built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — data as of {asof.date()} (UTC)")
    r = 4
    ws.cell(row=r, column=1, value="Rows fetched per source (this run)").font = F_BOLD
    r += 1
    _header(ws, r, ["Source", "OK calls", "Rows", "Failed", "Unconfigured / manual-only"])
    ws.row_dimensions[r].height = 18
    r += 1
    if runlog is not None and not runlog.empty:
        for src, g in runlog.groupby("source", sort=True):
            vals = [src, int((g["status"] == "ok").sum()), int(g["rows"].fillna(0).sum()), int((g["status"] == "failed").sum()),
                    int((g["status"] == "unconfigured").sum())]
            for j, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=j, value=v)
                _style(c, "text", FMT_NUM if j > 1 else FMT_TEXT)
                if j == 4 and v:
                    c.font = Font(name=FONT, size=10, color="C00000", bold=True)
            r += 1
    else:
        ws.cell(row=r, column=1, value="no fetch log for this run").font = F_SUB
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Fetch failures (this run) — last known values carried forward and marked stale").font = F_BOLD
    r += 1
    _header(ws, r, ["Timestamp (UTC)", "Source", "Project", "Message"])
    ws.row_dimensions[r].height = 18
    r += 1
    fails = runlog[runlog["status"] == "failed"] if runlog is not None and not runlog.empty else pd.DataFrame()
    if fails.empty:
        ws.cell(row=r, column=1, value="none").font = F_SUB
        r += 1
    for row in fails.itertuples(index=False):
        for j, v in enumerate([row.ts, row.source, row.project or "", row.message], start=1):
            ws.cell(row=r, column=j, value=v).font = Font(name=FONT, size=10, color="C00000")
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Unconfigured slots (Dune query ids missing in config.py) and manual-only metrics").font = F_BOLD
    r += 1
    _header(ws, r, ["Timestamp (UTC)", "Source", "Project", "Message"])
    ws.row_dimensions[r].height = 18
    r += 1
    unc = runlog[runlog["status"] == "unconfigured"] if runlog is not None and not runlog.empty else pd.DataFrame()
    if unc.empty:
        ws.cell(row=r, column=1, value="none").font = F_SUB
        r += 1
    for row in unc.itertuples(index=False):
        for j, v in enumerate([row.ts, row.source, row.project or "", row.message], start=1):
            ws.cell(row=r, column=j, value=v).font = Font(name=FONT, size=10, color="808080")
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Last successful fetch per source / project (all runs)").font = F_BOLD
    r += 1
    _header(ws, r, ["Source", "Project", "Last attempt (UTC)", "Last success (UTC)", "Last rows", "Last error"])
    ws.row_dimensions[r].height = 18
    r += 1
    if fetch_status is not None and not fetch_status.empty:
        for row in fetch_status.sort_values(["source", "project"]).itertuples(index=False):
            vals = [row.source, row.project, row.last_attempt_at or "", row.last_success_at or "", int(row.last_rows or 0), row.last_error or ""]
            for j, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=j, value=v)
                c.font = F_BASE if not (j == 6 and v) else Font(name=FONT, size=10, color="C00000")
            r += 1
    r += 1
    ws.cell(row=r, column=1, value=f"Manual overrides loaded: {overrides_n}").font = F_BOLD
    r += 1
    ws.cell(row=r, column=1, value="Every call this run").font = F_BOLD
    r += 1
    _header(ws, r, ["Timestamp (UTC)", "Source", "Project", "Rows", "Status", "Message"])
    ws.row_dimensions[r].height = 18
    r += 1
    if runlog is not None and not runlog.empty:
        for row in runlog.itertuples(index=False):
            for j, v in enumerate([row.ts, row.source, row.project or "", int(row.rows or 0), row.status, row.message], start=1):
                ws.cell(row=r, column=j, value=v).font = F_BASE
            r += 1
    _set_widths(ws, {"A": 22, "B": 18, "C": 18, "D": 22, "E": 12, "F": 80})


# ---------------------------------------------------------------------------------------
def build_workbook(store, path: Path | str, run_id: str | None = None, asof: pd.Timestamp | None = None) -> Path:
    path = Path(path)
    long = store.load_long()
    fetch_status = store.fetch_status()
    runlog = store.run_log(run_id) if run_id else store.run_log()
    overrides_n = int(long["is_manual"].sum()) if not long.empty else 0
    asof = asof or pd.Timestamp.now('UTC').tz_localize(None).normalize()

    data = aggregate(long, fetch_status, asof)
    data_by_key = {r["key"]: r for r in data.to_dict("records")}
    months, monthly = monthly_table(long, asof)
    R = Refs(len(data), len(monthly), months)

    wb = Workbook()
    ws_master = wb.active
    ws_master.title = "Master"
    ws_a1 = wb.create_sheet("A1 Infrastructure")
    ws_a2 = wb.create_sheet("A2 Coordination")
    ws_a3 = wb.create_sheet("A3 Revenue Buyback")
    ws_a4 = wb.create_sheet("A4 Permanent Burn")
    ws_ch = wb.create_sheet("Charts")
    ws_cfg = wb.create_sheet("Config & Sources")
    ws_log = wb.create_sheet("Run Log")
    ws_data = wb.create_sheet("Data")
    ws_mon = wb.create_sheet("Monthly")

    write_config(ws_cfg)
    write_data(ws_data, data, asof)
    write_monthly(ws_mon, months, monthly)
    write_master(ws_master, R, data_by_key)
    a4 = write_a4(ws_a4, R, data_by_key)
    a3 = write_a3(ws_a3, R, data_by_key)
    a1 = write_a1(ws_a1, R, data_by_key, months)
    a2 = write_a2(ws_a2, R, data_by_key, months)
    write_charts(ws_ch, {"a3": a3, "a4": a4, "a1": a1, "a2": a2, "ws_a3": ws_a3, "ws_a4": ws_a4, "ws_a1": ws_a1, "ws_a2": ws_a2})
    write_runlog(ws_log, runlog, fetch_status, run_id, asof, overrides_n)

    # legend on Master
    legend_row = 4 + len(PROJECTS) + 3
    legend = [("Legend", F_BOLD, None), ("Blue text = hardcoded input / config lever", F_INPUT, None), ("Black = formula", F_CALC, None),
              ("Green = pulled from another sheet", F_LINK, None), ("Yellow fill = manual override (entered_on in comment)", F_BASE, FILL_MANUAL),
              ("Grey fill = split unconfirmed, derived figure suppressed", F_BASE, FILL_UNCONFIRMED), ("Orange fill = stale (last good fetch in comment)", F_BASE, FILL_STALE),
              ("Amber fill = programme paused", F_BASE, FILL_PAUSED), ("Highlighted columns = headline figures", F_BASE, FILL_KEY),
              ("n/a = no value in the store (never a zero)", Font(name=FONT, size=10, color="999999"), None)]
    for i, (text, font, fill) in enumerate(legend):
        c = ws_master.cell(row=legend_row + i, column=1, value=text)
        c.font = font
        if fill:
            c.fill = fill

    wb.save(path)
    return path
