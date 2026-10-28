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

import contextlib
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
import trajectory
import wiring
from config import GLOBALS, METRICS, PROJECTS
from fetch.base import _measuring_point

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
FILL_REVIEW = PatternFill("solid", fgColor="E4DFEC")      # flagged to the Review Queue
# Confidence bands. These answer "what can I act on", which the Gap Report does not.
FILL_GREEN = PatternFill("solid", fgColor="D6E9CE")
FILL_AMBER = PatternFill("solid", fgColor="FDE9D0")
FILL_RED = PatternFill("solid", fgColor="F4CCCC")
# "N/A" IS NOT A FOURTH GRADE OF CONFIDENCE, IT IS THE ABSENCE OF A QUESTION. Added 2026-09-22.
# RED means "not a number: suppressed, refused or gapped" — every one of those is something
# WRONG, and a reader scanning for problems is right to read it that way. "This quantity does not
# exist for this project" is not a problem, and counting it as one inflates the RED figure with
# rows that need no work and can never be fixed. It gets a grey fill and is left out of the
# tally entirely, which is why it is absent from this map rather than pointing at a colour.
CONFIDENCE_NA = "N/A"
FILL_NA = PatternFill("solid", fgColor="F2F2F2")
CONFIDENCE_FILL = {"GREEN": FILL_GREEN, "AMBER": FILL_AMBER, "RED": FILL_RED,
                   CONFIDENCE_NA: FILL_NA}
FILL_GAP = PatternFill("solid", fgColor="F2F2F2")         # unresolved — see the Gap Report
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
# How many staged rows per field reach the sheet. The table keeps all of them.
STAGING_ROWS_PER_FIELD = 30
FMT_X = '0.00"x";(0.00"x");-'
FMT_TEXT = '@'
FMT_DATE = 'yyyy-mm-dd'

UNIT_FMT = {"usd": FMT_USD, "tokens": FMT_NUM, "count": FMT_NUM, "pct": FMT_PCT, "days": FMT_NUM, "units": FMT_NUM,
            "date": FMT_DATE}   # "date" values are Excel serials (buyback_last_inflow_date)

CFG = "'Config & Sources'"
_WINDOWS: dict[str, tuple[str, str]] = {}   # window key -> (start, end) ISO dates, set per build
NA = '"n/a"'
# A figure that was chased and closed reads DIFFERENTLY from one that is merely absent. "n/a"
# means no value in the store — possibly a bug, possibly not yet sourced. This means somebody
# looked, there is no route, and the cell is empty on purpose.
CLOSED_TEXT = "none available"

# THE FIVE CONSECUTIVE TRAILING 90-DAY BLOCKS: Q0 (newest) .. Q4 (theory layer, Phase 1, 2026-10-12)
QUARTERS = ("q0", "q1", "q2", "q3", "q4")
# Data sheet layout (column letters)
DATA_COLS = ["key", "project", "metric", "label", "kind", "unit", "source", "tier", "latest_date",
             "now", "m1", "q0", "q1", "q2", "q3", "y1", "n_points", "status", "confidence", "why_amber",
             "last_success", "entered_on", "note", "q0_covered_days", "q0_events",
             "schedule_end", "q0_pre_end_days", "last_nonzero_date", "silent_days", "silence_flag",
             "q0_basis", "now_covered_days", "m1_covered_days", "q1_covered_days", "q2_covered_days",
             "q3_covered_days", "first_date",
             # TRAJECTORY (theory layer, Phase 1, 2026-10-12): a fifth quarter and each quarter's events, so every
             # headline can be re-computed over Q-1..Q-4 by the same formula (build_workbook.ShiftRefs)
             "q4", "q4_covered_days", "q1_events", "q2_events", "q3_events", "q4_events"]
DATA_HEAD = ["Key (project|metric)", "Project", "Metric", "Label", "Kind", "Unit", "Source (of latest point)", "Tier", "Latest date",
             "Now (flow: trailing 30d sum · stock: latest)", "Prior 30d (flow: 30d ending -30d · stock: value at -30d)",
             "Q0 (flow: trailing 90d sum, or 3 complete months for a monthly series — see Q0 basis · stock: 90d avg)", "Q1 (90d ending -90d)", "Q2 (90d ending -180d)", "Q3 (90d ending -270d)",
             "Y1 (flow: 365d sum · stock: 365d avg)", "Points", "Status", "Confidence", "Why not green",
             "Last successful fetch", "Entered on (manual)", "Note",
             # APPENDED, so every existing column letter is unchanged. Flows only (not monthly).
             "Q0: days of the 90 the series existed for", "Q0: days with a non-zero value (events)",
             "Schedule end (declared in config; schedule:config rows only)",
             "Q0: days on or before the schedule end (only once it has passed)",
             "Last non-zero value (flows; the scan's stored last-inflow date where there is one)",
             "Days since then (only where a program cadence is documented)",
             "Program silence flag (PROGRAM_CADENCE)",
             "Q0 basis (flows): trailing days, or three complete calendar months for a monthly series",
             # PER-WINDOW COVERAGE (2026-09-29): a ratio of two flows divides rates, each over its
             # OWN covered days (_flow_rate), never a 90-day sum by a 1-day one.
             "Now: days of the 30 the series covered", "Prior 30d: days covered",
             "Q1: days of the 90 covered", "Q2: days covered", "Q3: days covered",
             # 2026-09-29: the series' first date AS READ (after the read-time views — a stitched
             # history counts), which is what the completeness report measures history from.
             "First date held",
             "Q4 (90d ending -360d; flow: sum · stock: avg)", "Q4: days covered",
             "Q1: days with a non-zero value (events)", "Q2: events", "Q3: events", "Q4: events"]
DC = {name: get_column_letter(i + 1) for i, name in enumerate(DATA_COLS)}
# Appended day-count columns that formulas READ as numbers (see write_data).
NUMERIC_APPENDED = ("q0_covered_days", "q0_events", "q0_pre_end_days", "silent_days",
                    "now_covered_days", "m1_covered_days", "q1_covered_days", "q2_covered_days",
                    "q3_covered_days", "q4_covered_days", "q1_events", "q2_events", "q3_events", "q4_events")

# Config table layout
CFG_COLS = [
    ("Project", 16), ("Symbol", 8), ("Archetypes", 10), ("Primary", 8), ("Held (not enabled)", 10), ("Materiality", 10),
    ("CoinGecko id", 16), ("DefiLlama fees slug", 14), ("DefiLlama protocol", 14), ("DefiLlama chain", 14),
    ("Share of revenue to buyback", 12), ("Share source URL", 30), ("Share source date", 11), ("Programmed (contract-enforced)", 12), ("Buyback status", 11),
    ("Buyback destination", 11), ("Destination effect on float", 14),
    ("Destination split (share burned, where destination = split)", 12), ("Burn execution", 12),
    ("Share of fees burned", 11), ("Burn source URL", 30), ("Burn source date", 11), ("Burn status", 11),
    ("Issuance schedule (tokens/day, current step)", 14), ("Schedule source URL", 30), ("Schedule source date", 11), ("Schedule status", 11),
    ("Per-product split (never collapsed)", 34),
    ("Share Q0 (trailing window)", 12), ("Status Q0", 11),
    ("Share Q1 (-3m window)", 12), ("Status Q1", 11),
    ("Share Q2 (-6m window)", 12), ("Status Q2", 11),
    ("Share Q3 (-9m window)", 12), ("Status Q3", 11),
    ("Revenue accruing but NOT booked", 40),
    ("Governance-settable parameters (never hardcoded)", 44),
    ("Self-reported figure preferred", 16),
    ("Notes", 60), ("Status changes", 40),
    # THE SPLIT'S OWN STATUS (config burn_split.status), for the implied-burn gate — "Burn status" is derived from the
    # data since 2026-10-11 (derived_statuses) and no longer reads "unconfirmed" verbatim
    ("Burn split status", 11),
]
CC = {name: get_column_letter(i + 1) for i, (name, _) in enumerate(CFG_COLS)}
CFG_GLOBAL_ROWS = {"period_days": 3, "short_days": 4, "days_per_year": 5, "stale_after_days": 6}
CFG_HEADER_ROW = 9
CFG_R0 = CFG_HEADER_ROW + 1

# ===== WHICH PROJECTS THIS BUILD RENDERS. Added 2026-09-23. =====
# Portfolio scope was applied to the FETCH and not to the BUILD, so a run that politely fetched
# 16 projects still rendered 30 — fourteen of them frozen at whatever the store last held, with
# nothing on the sheet saying the run had not touched them. A parked project's rows stay in the
# store untouched; they are simply not drawn.
#
# ONE MUTABLE, READ THROUGH ONE FUNCTION, because eleven call sites is exactly the number at
# which passing a list down by hand goes wrong in one of them — and the one that got missed
# would render a project the rest of the workbook has no row for, producing #N/A in every
# INDEX/MATCH that reached it.
_SCOPE: list = list(PROJECTS)


def scoped_projects() -> list:
    """The projects this build renders — all of them unless build_workbook narrowed the scope."""
    return _SCOPE


def _cfg_r1() -> int:
    """Last row of the Config & Sources table. A FUNCTION because the table is as long as the
    scope, and the INDEX/MATCH ranges built off it must match what was actually written."""
    return CFG_R0 + len(scoped_projects()) - 1

MONTHLY_METRICS = ["fees_usd", "revenue_usd", "price_usd", "gross_issuance_tokens", "gross_burn_tokens",
                   "customer_revenue_usd", "emissions_tokens", "actual_buyback_usd"]


WINDOW_SHARE_COL = {"q0": "Share Q0 (trailing window)", "q1": "Share Q1 (-3m window)",
                    "q2": "Share Q2 (-6m window)", "q3": "Share Q3 (-9m window)"}
WINDOW_STATUS_COL = {"q0": "Status Q0", "q1": "Status Q1", "q2": "Status Q2", "q3": "Status Q3"}
_WINDOW_COLS_BY_NAME = {**{v: ("share", q) for q, v in WINDOW_SHARE_COL.items()},
                        **{v: ("status", q) for q, v in WINDOW_STATUS_COL.items()}}


def _programmed_label(value) -> str:
    """programmed is tri-state: True, False, or 'unconfirmed_conflict' where sources disagree."""
    if value is None:
        return "n/a — not documented (no fee split on file says whether it is contract-enforced)"
    if value == "unconfirmed_conflict":
        return "sources conflict"
    return "yes" if value else "no"


# globals beside the window settings (label, value cell): the NRR yardstick (Jake's Phase 1 review 2026-10-12, item 7)
CFG_GLOBAL_EXTRA = {"nrr_m2_gdp": ("D3", "E3", "M2 / GDP — the NRR yardstick (Jake's May note): NRR headroom = it ÷ NRR")}


def G(name: str) -> str:
    if name in CFG_GLOBAL_EXTRA:
        cell = CFG_GLOBAL_EXTRA[name][1]
        return f"{CFG}!${cell[0]}${cell[1:]}"
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


def _latest_complete_month(s: pd.Series, asof: pd.Timestamp) -> tuple[float | None, str]:
    """The sum over the most recent month that has FINISHED, and which month that was.

    A monthly series cannot fill a 30-day trailing window, and the current month is incomplete by
    construction — the query that builds it drops the partial month rather than publishing a
    month-to-date figure as though it were a month. So "trailing 30 days" over a monthly series
    reports whatever happens to fall inside the window: usually one month, sometimes two, and
    sometimes — when the last complete month ended more than 30 days ago — NOTHING, which is what
    GEODNET's buyback was doing. The cell was blank while 41 perfectly good monthly rows sat in
    the store.

    Reporting the latest COMPLETE month is the honest answer, and the month is named so nobody
    reads a September figure as an August one.
    """
    if s.empty:
        return None, ""
    current = asof.to_period("M")
    finished = s[s.index.to_period("M") < current]
    if finished.empty:
        return None, ""
    month = finished.index.to_period("M").max()
    return float(finished[finished.index.to_period("M") == month].sum()), str(month)


def _month_block(s: pd.Series, asof: pd.Timestamp, first_back: int, n: int) -> tuple[float | None, str]:
    """Sum over n COMPLETE calendar months, the newest being `first_back` months before the last
    complete one. Added 2026-09-28 (Jake).

    A MONTHLY SERIES' Q0 IS THREE COMPLETE MONTHS, NOT 90 DAYS. The day window (asof-90, asof]
    dropped Maple's June buyback, dated 30 June, by one day on 28 September — an artefact of where
    the boundary fell, not a property of the data. Calendar months are counted from asof's month
    (the current month is never complete), and a row counts by the month it is IN, so month-start
    (Dune) and month-end (Maple) dating give the same answer. None when no row falls in the block.
    """
    last = asof.to_period("M") - 1 - first_back
    months = {last - k for k in range(n)}
    if s.empty:
        return None, ""
    w = s[s.index.to_period("M").isin(months)]
    first = min(months)
    label = f"{n} complete months {first.strftime('%b %Y')}–{last.strftime('%b %Y')}"
    return (float(w.sum()) if len(w) else None), label


def _month_coverage(row: dict, s: pd.Series, asof: pd.Timestamp, windows=("now", "m1", "q0", "q1", "q2", "q3", "q4")) -> None:
    """Covered days of a COMPLETE-MONTHS window: the days of the months in it that hold a row.

    Sky's net_protocol_surplus_usd holds July and August only, so its Q0 block (Jun-Aug) covers
    62 days, not 92 — and a ratio against a trailing-90-day buyback divides by those 62.
    """
    last = asof.to_period("M") - 1
    have = set(s.index.to_period("M"))
    blocks = {"now": [last], "m1": [last - 1]}
    for i, q in enumerate(QUARTERS):
        blocks[q] = [last - 3 * i - k for k in range(3)]
    for w in windows:
        row[f"{w}_covered_days"] = sum(m.days_in_month for m in blocks[w] if m in have)


def _months_until_live(row: dict, name: str, metric: str, g: pd.DataFrame, s: pd.Series,
                       asof: pd.Timestamp, short: int, period: int) -> None:
    """COMPLETE MONTHS, until the full live leg of a monthly-led handover covers the window.

    GEODNET (Jake's run 2026-09-29): burn Q0 4,029,999.99 against ~9M. The trailing (07-01, 09-29]
    window dropped July's Dune row (dated 07-01, on the boundary) and filled September with the
    Polygon-only middle leg, so release/burn read 2.73x. Until the last declared leg (Polygon +
    Solana) is itself as old as a window, that window is taken in complete calendar months — the
    Dune rows count both chains — exactly as a monthly series' windows are (_month_block). Each
    window switches back to trailing days on its own as the live leg reaches its length.
    q0_covered_days becomes the days in the block, so _annualise divides by them.
    """
    legs = config.handover_monthly_leg(name, metric)
    if not legs or g is None or g.empty:
        return
    pts = g["source"].astype(str).map(_measuring_point)
    live = g.loc[pts == legs[1], "date"]
    live_days = int((asof - live.min()).days) + 1 if len(live) else 0
    why = (f"the full live read ({legs[1]}) covers {live_days} day(s), so this window is complete "
           f"calendar months from {legs[0]} (both chains) until it covers the window")

    switched = [w for w, n in (("now", short), ("m1", short), *((q, period) for q in QUARTERS)) if live_days < n]
    _month_coverage(row, s, asof, switched)
    if live_days < short:
        row["now"], month = _latest_complete_month(s, asof)
        if month:
            prior = pd.Period(month) - 1
            p_rows = s[s.index.to_period("M") == prior]
            row["m1"] = float(p_rows.sum()) if len(p_rows) else None
            row["period_label"] = month
            row["covered_days"] = row["window_days"] = pd.Period(month).days_in_month
    if live_days < period:
        for i, q in enumerate(QUARTERS):
            row[q], label = _month_block(s, asof, 3 * i, 3)
            if q == "q0":
                row["q0_basis"] = f"{label} — {why}"
        last = asof.to_period("M") - 1
        for i, q in enumerate(QUARTERS):
            row[f"{q}_events"] = int((s.index.to_period("M").isin({last - 3 * i - k for k in range(3)}) & (s > 0)).sum())
    if live_days < 365:
        row["y1"], _ = _month_block(s, asof, 0, 12)


def _observed_cadence(g) -> str:
    """'monthly' / 'weekly' / 'daily' from the series' OWN spacing — the median gap between its last seven distinct
    dates (declared zeros left out). G11 A4 (Jake's 10-21 run): Maple's buyback / retired / holder-value views (month
    ends from the transparency page) and Aethir's arr_usd (monthly network revenue) read "stale — no fresh value in 7
    days" because a read-time view declares no granularity of its own; Pendle's sPENDLE buybacks are weekly."""
    if g is None or getattr(g, "empty", True):
        return "daily"
    src = g["source"].astype(str) if "source" in g else None
    d = pd.to_datetime(g["date"] if src is None else g.loc[~src.str.startswith("declared:zero"), "date"])
    d = pd.Series(sorted(set(d.dt.normalize())))[-7:]
    if len(d) < 3:
        return "daily"
    gap = d.diff().dt.days.dropna().median()
    return "monthly" if gap >= 25 else "weekly" if gap >= 6 else "daily"


def _stale_limit(name: str, metric: str, default: int, g=None) -> int:
    """The staleness threshold: config's (declared cadence) and, never below it, the series' observed cadence's."""
    lim = config.stale_after_days(name, metric, default)
    seen = config.STALE_AFTER_DAYS_BY_GRANULARITY.get(_observed_cadence(g)) or 0
    return max(int(lim), int(seen))


def _age_in_days(latest: pd.Timestamp, asof: pd.Timestamp, granularity: str) -> int:
    """How old the newest point is, measured from the END of the period it represents.

    A PERIODIC POINT IS LABELLED WITH ITS PERIOD'S START. Dune's monthly rows are dated to the
    first of the month, so a complete August sits at 2026-08-01 and looks 51 days old on the 21st
    of September when it is in fact the most recent month that exists. Measuring from the period's
    end — 2026-08-31, 21 days — is what the threshold was always meant to compare against.

    Daily points are their own period, so this is the identity for them.
    """
    # the period rule lives in config.period_end (Round G7 item 3), shared with stale_series and the reference matcher
    if granularity == "monthly":
        end = (latest.to_period("M") + 1).to_timestamp() - pd.Timedelta(days=1)
    elif granularity == "weekly":
        end = latest + pd.Timedelta(days=6)
    else:
        end = latest
    return max(int((asof - end).days), 0)


COVERED_SETS: dict = {}       # (project, metric) -> the days a daily flow's rows cover (D6-1), set per build
WINDOW_HOLES: dict = {}       # (project, metric, window) -> its interior days with no row (D6-1), named by D7-2's BLOCKED


def drop_store_only(long: pd.DataFrame) -> pd.DataFrame:
    """D7-1: a STORE-ONLY project (config.STORE_ONLY_PROJECTS — Pyth's STOCK reads) is stored, never shown: its rows
    leave the build before anything reads them, whatever the scope."""
    if long is None or long.empty or not config.STORE_ONLY_PROJECTS:
        return long
    return long[~long["project"].isin(config.STORE_ONLY_PROJECTS)]


def _covered_day_sets(groups: dict) -> dict:
    """D6-1: per daily flow, THE DAYS ITS ROWS COVER — a row covers its own day, or its declared span (`[span=Nd]`: the N
    days ending on its date); a DERIVED row with no span of its own covers what its inputs' rows on the same date cover
    (wiring.view_edges: the view that wrote it read them — Hyperliquid's buyback $ = tokens x price inherits the tokens'
    `[span=4d]`). A derived series never gains a day it holds no row for. Read by _interior_holes."""
    from fetch.validate import _span_days                  # noqa: PLC0415
    spans: dict = {}
    stock_days: dict = {}

    def _stock_span(name: str, src: str, day) -> int:
        # A ROW STORED BEFORE THE FETCH TAGGED ITS SPAN (fetch/__init__ tags `derived:d_<stock>` since 2026-10-09):
        # d(stock) between two readings covers every day since the stock's previous reading — the row's own definition
        m_ = re.match(r"derived:d\(([a-z0-9_]+)\)", src)
        stock = m_.group(1) if m_ else re.split(r"[+\-\[:(]", src[len("derived:d_"):])[0]
        stock = "total_supply" if stock == "supply" else stock
        if (name, stock) not in stock_days:
            gs = groups.get((name, stock))
            stock_days[(name, stock)] = (sorted(set(pd.to_datetime(gs["date"]).dt.normalize()))
                                         if gs is not None and not gs.empty else [])
        prev = [x for x in stock_days[(name, stock)] if x < day]
        return max(int((day - prev[-1]).days), 1) if prev and day in stock_days[(name, stock)] else 1

    for (name, metric), g in groups.items():
        if g is None or getattr(g, "empty", True) or (METRICS.get(metric) or {}).get("kind") != "flow":
            continue
        d = pd.to_datetime(g["date"]).dt.normalize()
        sp: dict = {}
        for day, src in zip(d, g["source"].astype(str)):
            n = _span_days(src)
            if n == 1 and (src.startswith("derived:d_") or src.startswith("derived:d(")):
                n = _stock_span(name, src, day)
            sp[day] = max(sp.get(day, 1), n)
        spans[(name, metric)] = sp
    try:
        edges = wiring.view_edges()
    except Exception:  # noqa: BLE001 — no graph (a view called outside a tracked build): own spans only
        edges = {}
    for _ in range(4):                                     # derived of derived (tokens -> $ -> holder value), to depth 4
        changed = False
        for (name, w), ins in edges.items():
            own = spans.get((name, w))
            if not own:
                continue
            srcs = [spans.get((name, r)) for r, _f in ins]
            srcs = [x for x in srcs if x]
            for day, n in own.items():
                best = max([n] + [x.get(day, 1) for x in srcs])
                if best > n:
                    own[day] = best
                    changed = True
        if not changed:
            break
    return {k: {day - pd.Timedelta(days=i) for day, n in sp.items() for i in range(int(n))} for k, sp in spans.items()}


def _interior_holes(cov: set, start: pd.Timestamp, end: pd.Timestamp, first=None) -> list:
    """D6-1: the days in (start, end] that no row covers, between the series' first covered day (or `first`) and its last
    — days after the last row are the lag allowance (staleness reports a series that stopped), never holes."""
    if not cov:
        return []
    lo = max(start + pd.Timedelta(days=1), min(cov) if first is None else max(first, min(cov)))
    hi = min(end, max(cov))
    return [d for d in pd.date_range(lo, hi) if d not in cov] if hi >= lo else []


def _differenced_first(g: pd.DataFrame) -> bool:
    """True when the series' FIRST row is a difference of two readings (`derived:d_…` or a `:delta` source):
    dated the day it was read, it holds the interval ENDING that day, so its coverage starts the day before."""
    if g is None or g.empty or "source" not in g:
        return False
    src = str(g.sort_values("date")["source"].iloc[0])
    return src.startswith("derived:d_") or ":delta" in src


def _after_leg_gap(g: pd.DataFrame, s: pd.Series) -> pd.Series:
    """THE CURRENT LEG ONLY, WHERE AN OLDER LEG STOPS SHORT OF IT (Jake's data-accuracy pass on 1e702bb, item 3: Ethereum's
    consensus part read 0.39% — the MODELLED leg ends before Q0 and Eth2Staking starts 2026-09-30, so the series 'existed'
    across all of Q0 and ten measured days were annualised as ninety). Where two consecutive rows come from different
    measuring points AND more than a day apart, the days between are a hole, not zeros: coverage counts from the later
    leg's first row. A handover with no gap is unchanged."""
    if g is None or g.empty or s.empty or len(s) < 2:
        return s
    from fetch.base import _measuring_point              # noqa: PLC0415
    gd = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
          .drop_duplicates("date", keep="last").set_index("date"))
    mp = gd["source"].astype(str).map(_measuring_point)
    gap = gd.index.to_series().diff().dt.days
    cut = gd.index[(mp != mp.shift(1)) & (gap > 1)]
    return s[s.index >= cut.max()] if len(cut) else s


def _dense_days(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Distinct days holding a row in (start, end] — coverage for a config.DENSE_DAILY_FLOWS series (G9 F1)."""
    if s.empty:
        return 0
    idx = pd.DatetimeIndex(s.index).normalize()
    return int(idx[(idx > start) & (idx <= end)].nunique())


def _dense_missing(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """The days in (start, end] from the series' first row on that hold no row — a DENSE_DAILY_FLOWS series (G9 K3).
    The day `end` itself is left out while it is not complete (a daily query stores a day once it has closed)."""
    if s.empty:
        return []
    idx = set(pd.DatetimeIndex(s.index).normalize())
    lo = max(start + pd.Timedelta(days=1), min(idx))
    hi = end.normalize() - pd.Timedelta(days=1) if end.normalize() not in idx else end.normalize()
    return [d for d in pd.date_range(lo, hi) if d not in idx] if hi >= lo else []


def _day_ranges(days: list[pd.Timestamp]) -> str:
    """'2026-09-11, 2026-09-22..09-25' — consecutive days folded into one range."""
    out, run = [], []
    for d in sorted(days):
        if run and d - run[-1] == pd.Timedelta(days=1):
            run.append(d)
            continue
        if run:
            out.append(run)
        run = [d]
    if run:
        out.append(run)
    return ", ".join(str(r[0].date()) if len(r) == 1 else f"{r[0].date()}..{r[-1].strftime('%m-%d')}" for r in out)


def _window_coverage(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp,
                     differenced: bool = False) -> tuple[int, int]:
    """(days the series actually covers inside the window, days the window asks for).

    A TRAILING-30-DAY SUM OVER A SERIES TEN DAYS OLD IS A TEN-DAY SUM, and until 2026-09-21 it was
    printed under a 30-day header with nothing to say so. Hyperliquid's 155,971 was roughly a
    third of a real 30-day burn for exactly this reason: ~8 runs spanning ~10 days.

    Measured from the series' FIRST EVER observation, not the first inside the window: a series
    that began 10 days ago covers 10 of the 30 days no matter how many points it has, and one
    that began two years ago covers all 30 even if this window holds a single point. The question
    is how much of the window the series was alive for, not how densely it was sampled.

    DELIBERATELY NOT TRUNCATED AT THE LAST OBSERVATION. A daily series read yesterday has not
    "covered 29 of 30 days" — the one-day lag between the last read and the asof date is ordinary,
    and every series would carry a warning it had not earned. A series that has genuinely STOPPED
    is a staleness problem, and staleness is a separate mechanism that already reports it; having
    coverage report it too would put two warnings on one fault and none on the fault coverage
    exists for.
    """
    span = max(int((end - start).days), 0)
    if s.empty:
        return 0, span
    # A DIFFERENCED FLOW'S FIRST ROW COVERS THE DAY BEFORE ITS DATE (Jake's run 2026-10-06: Ethereum's issuance
    # read 20,876 against the curve's 18,070, +15.5% — seven daily Eth2Staking steps summed over "6" covered
    # days, because k daily deltas dated D1..asof were counted asof - D1 = k - 1 days). ONLY when the series
    # reaches `end`: one whose last row is the day before already gets that day from the lag allowance below
    # (not truncated at the last observation), and shifting it too would count k deltas over k + 1 days.
    first = s.index.min() - (pd.Timedelta(days=1) if differenced and s.index.max() >= end else pd.Timedelta(0))
    covered_from = max(start, first)
    if covered_from > end:
        return 0, span
    # A FIGURE IN THE WINDOW HAS COVERED AT LEAST ONE DAY (2026-09-29). A differenced flow is
    # dated the day it was read, so a series whose first row IS the asof date measured 0 days
    # here — and every covered-days annualisation fell back to x365/90: Hyperliquid's first
    # 13,964 HYPE of rewards read as a 0.013% validator yield instead of ~1.2%.
    days = int((end - covered_from).days)
    if days == 0 and ((s.index > start) & (s.index <= end)).any():
        days = 1
    return min(days, span), span


# =========================================================================================
# WITHHELD — the one place that decides a stored number is WRONG, not merely uncertain.
#
# There are seven ways a stored row's meaning can be withdrawn, and until 2026-09-15 they were
# handled in two different places with two different outcomes: three blanked the value, three
# went RED and showed it anyway. That split was an accident of the order they were found in, not
# a distinction anyone made — Maple's disputed destination was fixed by blanking because it was
# found first, and the rest inherited whatever confidence_for happened to do.
#
# STANDARDISED: all of them blank. The principle is the one that settled Maple, applied to its
# siblings — a wrong number in a cell is worse than an empty one, because a reader acts on a
# number and asks about a blank. Three of these previously put a figure on the sheet with RED
# beside it, which asks the reader to notice a colour before trusting an arithmetic result.
#
# The reason travels with the row in `note`, so nothing is lost by blanking: what the figure was,
# why it is withheld, and how to clear it are all still there.
# =========================================================================================
WITHHELD_STATUSES = ("orphaned", "withdrawn", "suppressed", "disputed", "blocked",
                     "measuring_point_changed", "implausible_delta", "unreconciled_flow",
                     "refuted", "out_of_bounds")

# A DIFFERENCED FLOW WHOSE PARENT STOCK IT CAN BE SANITY-CHECKED AGAINST.
# gross_burn_tokens is the change in burn_address_balance between two observations, so one day's
# flow can never be a large fraction of every burn ever recorded. The mapping mirrors
# config._flow_parents, inverted; it is small and explicit rather than derived, because getting it
# wrong in the derived direction would silently disarm the guard.
# ** THIS WAS A LITERAL DICT AND IT HAD ALREADY GONE STALE. Replaced 2026-09-22. **
# It read {"gross_burn_tokens": "burn_address_balance", "burn_revenue_funded":
# "burn_address_balance"}, written when burn_address_balance was the only cumulative in the book,
# with a comment saying it was explicit "because getting it wrong in the derived direction would
# silently disarm the guard".
#
# GETTING IT WRONG IN THE HAND-MAINTAINED DIRECTION IS WHAT HAPPENED. The moment per-project
# mappings arrived — Chainlink's Reserve inflow on 2026-09-22, Sky's two decomposed burn legs the
# same day — three flows had no entry here, so the telescoping check and the implausible-delta
# check never ran for them AT ALL. Not a wrong answer: no answer, and nothing said so. The guard
# built for Hyperliquid could not see two thirds of the flows that existed a day later.
#
# config.stock_for_flow INVERTS THE LIVE MAPPING, so it cannot drift from it. The only thing kept
# by hand is the alias table for a flow that is a re-labelled copy of another (burn_revenue_funded),
# and that is a fact about the labelling rather than a second copy of the mapping.
def flow_stock(project: str, metric: str) -> str | None:
    """The cumulative this flow was differenced from, for THIS project."""
    return config.stock_for_flow(project, metric)

# THE SHARE ABOVE WHICH A SINGLE OBSERVATION IS NOT A FLOW.
# 25% is deliberately loose. The failure this catches is not a figure that is somewhat too high —
# it is a measuring-point change reported as one day's burn, which lands at 90-100% of the
# cumulative. A tighter bound would start rejecting real days on a young series where the
# cumulative is still small; a looser one would let a 30x error through. Nothing between a
# quarter and a whole has ever been a real daily burn for any project in this universe.
IMPLAUSIBLE_DELTA_SHARE = 0.25

# THE TELESCOPING IDENTITY, AND HOW MUCH OF IT IS FLOATING-POINT.
# Every flow with a stock (see flow_stock) is a difference of two readings of it, so consecutive
# flows telescope: summing them over a span must give the stock's move across that span exactly,
# because every intermediate reading appears once with each sign and cancels. The only slack is
# IEEE 754: these are doubles holding figures up to ~1e10, where one ulp is ~2e-6.
#
# SO THE TOLERANCE IS FLOAT PRECISION AND NOTHING ELSE. It is not a "close enough" band, and it
# must not become one — a residual of any size that survives this is a flow that failed to
# account for a real move of the stock, which is the whole failure being looked for. This is the
# Aerodrome-buffer lesson: a tolerance added to make a check comfortable is a check switched off.
TELESCOPING_ABS_EPS = 1e-6
TELESCOPING_REL_EPS = 1e-9


def method_points(g: pd.DataFrame):
    """(measuring point per row, note) — where every point is the SAME CONTRACT (the text before the
    first '.': "plume_staking:0x30c791E4") read by different METHODS, they are ONE point when their
    values agree within config.METHOD_AGREEMENT_TOLERANCE: on every shared date, else across the
    junction between the legs. A label difference alone is not a change of what was measured
    (Jake, 2026-10-01). Values that diverge keep the points apart, and the note says by how much.
    Different contracts, or one point, are returned untouched."""
    g = g.sort_values("date")
    mp = g["source"].astype(str).map(_measuring_point)
    pts = list(dict.fromkeys(mp))
    if len(pts) < 2 or any("." not in p for p in pts) or len({p.split(".", 1)[0] for p in pts}) != 1:
        return mp, None
    latest = mp.iloc[-1]
    ref = g[mp == latest].groupby("date")["value"].last().astype(float)
    worst, how = 0.0, []
    for p in pts:
        if p == latest:
            continue
        oth = g[mp == p].groupby("date")["value"].last().astype(float)
        common = oth.index.intersection(ref.index)
        if len(common):
            a, b = oth[common], ref[common]
            where = f"{len(common)} shared date(s)"
        else:                                   # no shared date: the two values either side of the junction
            if oth.index.max() < ref.index.min():
                a, b = oth.iloc[[-1]], ref.iloc[[0]]
            else:
                a, b = oth.iloc[[0]], ref.iloc[[-1]]
            where = "the junction"
        rel = ((a.values - b.values) / pd.Series(b.values).abs().replace(0, float("nan")).values)
        d = float(pd.Series(abs(rel)).fillna(float("inf")).max())
        worst = max(worst, d)
        how.append(f"{p.split('.', 1)[1]} vs {latest.split('.', 1)[1]}: {d:.3%} at {where}")
    if worst <= config.METHOD_AGREEMENT_TOLERANCE:
        return mp.map(lambda _: latest), (f"read method changed on {latest.split('.', 1)[0]}, values agree "
                                          f"({'; '.join(how)}) — one measuring point")
    return mp, f"READ METHOD CHANGED and the values DIVERGE ({'; '.join(how)})"


def sum_components(point: str) -> frozenset:
    """The components of a summed measuring point ("chain:sum(ethereum:a+ethereum:b)" -> {ethereum:a, ethereum:b});
    a single-component point is its own one-element set (G11 C1)."""
    m = re.search(r"sum\((.*)\)$", str(point))
    if m:
        return frozenset(x for x in m.group(1).split("+") if x)
    return frozenset([str(point).split(":", 1)[1] if ":" in str(point) else str(point)])


def _handover_cut_views(groups: dict) -> None:
    """A DECLARED CUT (G11 C1, Maple treasury_holding_tokens: the transparency page is primary from 2026-09-24, and the
    chain archive backfill wrote the daoMultisig read into the days the page had no row): where a series_handover sets
    `cut`, rows of an earlier declared point dated on or after the later point's first row are dropped at read time,
    so the legs meet once. What was dropped is recorded (HANDOVER_CUT)."""
    from fetch.base import _measuring_point                        # noqa: PLC0415
    HANDOVER_CUT.clear()
    for p in scoped_projects():
        for metric, decl in (p.get("series_handover") or {}).items():
            if not isinstance(decl, dict) or not decl.get("cut"):
                continue
            g = groups.get((p["name"], metric))
            if g is None or g.empty:
                continue
            order = [_measuring_point(x) for x in decl.get("ordered_points") or ()]
            mp = g["source"].astype(str).map(_measuring_point)
            keep = pd.Series(True, index=g.index)
            for i, later in enumerate(order[1:], start=1):
                if not (mp == later).any():
                    continue
                start = g.loc[mp == later, "date"].min()
                drop = mp.isin(order[:i]) & (g["date"] >= start)
                if drop.any():
                    HANDOVER_CUT[(p["name"], metric)] = (f"{int(drop.sum())} row(s) of {', '.join(sorted(set(mp[drop])))} "
                                                         f"dated on/after {later}'s first row "
                                                         f"({pd.Timestamp(start).date()}) dropped — the declared cut")
                keep &= ~drop
            if not keep.all():
                groups[(p["name"], metric)] = g[keep]


RESTATEMENT_STATUS: dict = {}   # (project, metric) -> what a declared restatement still lacks, last build (P2b-2)


def _named_day_ranges(days, most: int = 6) -> str:
    """'2025-07-30..2025-08-02, 2025-09-01' — consecutive days joined, at most `most` ranges then a count."""
    days = sorted(pd.Timestamp(d).normalize() for d in days)
    runs = []
    for d in days:
        if runs and d - runs[-1][1] == pd.Timedelta(days=1):
            runs[-1][1] = d
        else:
            runs.append([d, d])
    txt = [f"{a.date()}" if a == b else f"{a.date()}..{b.date()}" for a, b in runs]
    return ", ".join(txt[:most]) + (f" and {len(txt) - most} more range(s)" if len(txt) > most else "")


def _series_restatement_views(groups: dict, asof) -> None:
    """A DECLARED RESTATEMENT (config.SERIES_RESTATEMENTS, P2b-2 — Chainlink's non-circulating sum on the 33-wallet set;
    P2c-5 — Ether.fi's on the 12-wallet set, Maple's and Pendle's insider stake shares on the 9- and 6-wallet sets;
    P2d-5 — Ether.fi's insider stake shares on the 13-wallet set):
    the history before the current point's first day is re-read on that point (the clean-up, then the archive). Nothing
    in the series is changed here; what the store holds decides what is said:
      * rows on a superseded point remain (the clean-up has not run): the pending handover is declared for this build
        (config.RESTATEMENT_ACTIVE — the nested_sums seam, its SERIES BREAK and overlap check, as before), and the cell
        names the days still on the superseded set — never presented as the current set;
      * none remain: no handover, one basis, no break. A day of the history span (TRAJECTORY history_days) before the
        current point's first day with no row on it is a HOLE, named on the cell — never filled, never 0."""
    from fetch.base import _measuring_point                        # noqa: PLC0415
    RESTATEMENT_STATUS.clear()
    config.RESTATEMENT_ACTIVE.clear()
    for (proj, metric), r in config.SERIES_RESTATEMENTS.items():
        g = groups.get((proj, metric))
        if g is None or g.empty:
            continue
        dates = pd.to_datetime(g["date"]).dt.normalize()
        srcs = g["source"].astype(str)
        old = [config.restatement_superseded(proj, metric, d, s) for d, s in zip(dates, srcs)]
        old = pd.Series(old, index=g.index, dtype=bool)
        before = pd.Timestamp(r["before"])
        if old.any():
            config.RESTATEMENT_ACTIVE[(proj, metric)] = r["pending_handover"]
            # P2c-5: a set that grew more than once (Ether.fi 8 -> 10 -> 12) names every seam
            seams = ", ".join(config.restatement_seams(proj, metric))
            RESTATEMENT_STATUS[(proj, metric)] = (
                f"NOT YET RESTATED ({r['id']}): {int(old.sum())} day(s) still hold {r['superseded_label']}, not "
                f"{r['point_label']} — {_named_day_ranges(dates[old])}. The declared seam (SERIES BREAK {seams}) "
                f"stands until the daily run's CLEAN-UP {r['id']} removes them and `{r['refill']}` re-reads each day")
            continue
        cur = _measuring_point(r["point"])
        on_point = srcs.map(lambda x: config._sum_set(_measuring_point(x)) == config._sum_set(cur))
        held = set(dates[on_point & (dates < before)])
        if asof is None:
            continue
        start = (pd.Timestamp(asof).normalize() - pd.Timedelta(days=int(config.TRAJECTORY["history_days"])))
        if r.get("series_from"):
            # P2c-5: a day before the series can exist (its share token had no code: sPENDLE from 2026-01-16) is before
            # the series, not a hole — the archive's own start rule
            start = max(start, pd.Timestamp(r["series_from"]))
        span = pd.date_range(start, min(before - pd.Timedelta(days=1), pd.Timestamp(asof).normalize()), freq="D")
        holes = [d for d in span if d not in held]
        if holes:
            RESTATEMENT_STATUS[(proj, metric)] = (
                f"RESTATEMENT {r['id']} INCOMPLETE: {len(holes)} day(s) of {span[0].date()}..{span[-1].date()} hold no "
                f"row on {r['point_label']} — HOLES (never the earlier set's sum, never 0): {_named_day_ranges(holes)}; "
                f"`{r['refill']}` re-reads them (resumable)")


HANDOVER_CUT: dict = {}
HANDOVER_CONTINUED: dict = {}   # (project, metric) -> what the declared continuation added, last build (P2a D5a)


def _handover_continuation_views(groups: dict) -> None:
    """A DECLARED LATER LEG FROM ANOTHER STORED SERIES (P2a D5a, config.HANDOVER_CONTINUATION — Maple's page tile is gone,
    treasury_holding_tokens hands over to the DAO Safe on chain): the rows of `from_metric` dated AFTER the last row of
    `after_point` in the series join it under the declared `source` (its markers, PARTIAL included, kept). Read time
    only; a day with no row in `from_metric` stays a hole. Runs BEFORE the declared cut, which then drops any row of an
    earlier leg dated on or after the continuation's first day. What was added, and from when, is recorded."""
    from fetch.base import _measuring_point                        # noqa: PLC0415
    HANDOVER_CONTINUED.clear()
    scoped = {p["name"] for p in scoped_projects()}
    for (name, metric), spec in (getattr(config, "HANDOVER_CONTINUATION", {}) or {}).items():
        if name not in scoped:
            continue
        g, src = groups.get((name, metric)), groups.get((name, spec["from_metric"]))
        if g is None or g.empty or src is None or src.empty:
            continue
        mp = g["source"].astype(str).map(_measuring_point)
        lead = g[mp == _measuring_point(spec["after_point"])]
        if lead.empty:
            continue                      # the leg it follows was never stored: nothing is handed over
        last = pd.to_datetime(lead["date"]).max()
        add = src[pd.to_datetime(src["date"]) > last].copy()
        if add.empty:
            continue
        markers = [m for m in config.SOURCE_MARKERS if m != "archive"]
        add["source"] = [next((config.mark_source(spec["source"], m) for m in markers
                               if f":{m}" in str(s)), spec["source"]) for s in add["source"].astype(str)]
        add["metric"] = metric
        first = pd.to_datetime(add["date"]).min()
        groups[(name, metric)] = pd.concat([g, _as_stored(add, g.columns)], ignore_index=True)
        HANDOVER_CONTINUED[(name, metric)] = (
            f"HANDED OVER from {(first.date())}: {spec['after_point']}'s last stored row is {last.date()}; from the next "
            f"day the series reads {spec['from_metric']} ({len(add)} row(s) to {pd.to_datetime(add['date']).max().date()})"
            f" — {spec.get('decided', 'declared in config.HANDOVER_CONTINUATION')}")


MONTHLY_FLOW_FORMED: dict = {}   # (project, metric) -> months formed / holes, last build (P2a D5b)


def _monthly_flow_views(groups: dict) -> None:
    """A MONTHLY FLOW FROM DAILY LOG SCANS (P2a D5b, config.MONTHLY_FLOW_VIEWS — Maple's SSF Safe: in − out, and the
    buyback route's inflow on its own). A calendar month is formed only when EVERY day of it is held by EVERY leg (the
    scans store each covered day, zeros included, so a missing day is a HOLE and the month is not formed — never summed
    as if it were 0); its row is dated to the month's last day; signed sum(plus) − sum(minus). Read time only."""
    MONTHLY_FLOW_FORMED.clear()
    scoped = {p["name"] for p in scoped_projects()}
    for (name, metric), spec in (getattr(config, "MONTHLY_FLOW_VIEWS", {}) or {}).items():
        if name not in scoped:
            continue
        legs = [(m, 1.0) for m in spec.get("plus") or ()] + [(m, -1.0) for m in spec.get("minus") or ()]
        series, cols = [], None
        for m, sign in legs:
            d = _daily(groups.get((name, m)))
            if d.empty:
                series = None
                break
            cols = groups[(name, m)].columns
            if config.scanned_from_genesis(name, m):
                # A SCAN READ FROM THE CHAIN'S START (config.scanned_from_genesis) stores from the holder's first
                # Transfer: the days before it in that month are observed zeros (every block was read), not holes
                first = d.index.min()
                lead = pd.date_range(first.to_period("M").start_time.normalize(), first - pd.Timedelta(days=1), freq="D")
                d = pd.concat([pd.Series(0.0, index=lead), d]).sort_index() if len(lead) else d
            series.append(sign * d.astype(float))
        if not series:
            continue
        days = series[0].index
        for s_ in series[1:]:
            days = days.intersection(s_.index)
        if days.empty:
            continue
        held = pd.Series(1, index=days)
        rows, holes = [], []
        for per in sorted(set(days.to_period("M"))):
            want = pd.date_range(per.start_time.normalize(), per.end_time.normalize(), freq="D")
            missing = want.difference(held.index)
            if len(missing):
                if missing.min() < days.max():   # a day missing before the newest held day is a HOLE; after it,
                    holes.append(f"{per} ({len(missing)} day(s) not held)")   # the month is not over yet
                continue
            rows.append((want[-1], float(sum(s_.reindex(want).sum() for s_ in series))))
        MONTHLY_FLOW_FORMED[(name, metric)] = {"months": len(rows), "holes": holes}
        if not rows:
            continue
        src = (f"derived:monthly_flow[{spec.get('label', metric)}: "
               + " − ".join(filter(None, (" + ".join(spec.get("plus") or ()), " + ".join(spec.get("minus") or ()))))
               + "; complete months only]")
        view = pd.DataFrame({"date": [d for d, _ in rows], "project": name, "metric": metric,
                             "value": [v for _, v in rows], "tier": 2, "source": src})
        groups[(name, metric)] = _as_stored(view, cols)


def handover_refusal(project: str, metric: str, points, spans: dict, edges: dict | None = None) -> str | None:
    """None when a multi-point series is a DECLARED, non-overlapping handover; else why not.

    Three ways to fail, and all three keep the series blanked:
      * nothing declared — the ordinary case, an accidental change of measuring point;
      * the stored points are not the declared pair — a third source appeared, or one of the two
        is not the one named. A declaration covers the pair it names and no other;
      * the declared points OVERLAP in the store. This is the one the declaration cannot assert
        away, and the one a re-run of a backfill actually causes: if the historical leg reaches
        forward into the live period, the same burn is counted twice and the sum is wrong in the
        direction that looks like a busier month.
    """
    decl = config.declared_handover(project, metric)
    if not decl:
        return "no handover is declared for this series"
    # G11 C1 (Jake's 10-21 run: Ethereum total_supply_protocol hidden though its handover was declared): the stored
    # points are measuring points — bracket annotations stripped — so the declared ones are read the same way
    # ("ultrasound:supply-over-time[next-day 00:00Z]" IS the point ultrasound:supply-over-time)
    from fetch.base import _measuring_point                        # noqa: PLC0415
    # A WALLET SET THAT GREW (G11 C1, Ether.fi insider_stake_shares): a declared `nested_sums` handover accepts sum points
    # whose component sets only grow, oldest to newest, with no two in use on the same day
    if decl.get("nested_sums"):
        order = sorted(points, key=lambda p_: spans[p_][0] if p_ in spans else pd.Timestamp.max)
        sets = [sum_components(p_) for p_ in order]
        if all(a <= b for a, b in zip(sets, sets[1:])) and all(
                spans[a][1] < spans[b][0] for a, b in zip(order, order[1:]) if a in spans and b in spans):
            return None
        return (f"the declared wallet-set growth does not hold: {order} — each later set must contain the earlier "
                f"one, and no two may be in use on the same day")
    declared = tuple(dict.fromkeys(_measuring_point(x) for x in (decl.get("ordered_points") or ())))
    # THE LEGS PRESENT MUST ALL BE DECLARED; A DECLARED LEG MAY BE ABSENT (2026-09-28). GEODNET's
    # handover gained a third leg (the Polygon + Solana sum) that has no rows until the first run
    # reads it — and a store whose Polygon rows were cleaned has no middle leg. Requiring every
    # declared point would blank the series in both cases. Order and no-overlap are still checked
    # over the legs that ARE present, in the declared order.
    extra = sorted(set(points) - set(declared))
    if extra or len(set(points) & set(declared)) < 2:
        return (f"the stored series reads from {sorted(points)}, and the declared handover covers "
                f"{list(declared)}" + (f" — {extra} is outside it" if extra else ""))
    ordered = tuple(p for p in declared if p in set(points))
    for earlier, later in zip(ordered, ordered[1:]):
        a, b = spans.get(earlier), spans.get(later)
        if a is None or b is None:
            return f"no stored dates for {earlier if a is None else later}"
        if a[1] >= b[0]:
            return (f"the declared legs OVERLAP: {earlier} runs to {a[1]:%Y-%m-%d} and {later} "
                    f"starts {b[0]:%Y-%m-%d}. The overlapping period is counted twice")
        # A DECLARED max_step IS CHECKED AT THE SEAM (G9 C2, GEODNET supply_units): with no common day, the earlier
        # leg's last value against the later leg's first. A step past it is a different quantity, not a handover.
        if decl.get("max_step") is not None:
            ea, eb = (edges or {}).get(earlier), (edges or {}).get(later)
            if ea is None or eb is None or not eb[0]:
                return f"the declared max_step cannot be checked: no values for {earlier if ea is None else later}"
            step = abs(ea[1] / eb[0] - 1)
            if not step <= decl["max_step"]:
                return (f"the declared handover's seam step is {step:.3%} ({earlier} {ea[1]:,.6g} on "
                        f"{a[1]:%Y-%m-%d}, {later} {eb[0]:,.6g} on {b[0]:%Y-%m-%d}), over the declared "
                        f"{decl['max_step']:.1%}: not the same quantity")
    return None


def withheld_for(project: str, metric: str, row: dict) -> tuple[str, str] | None:
    """(status, reason) when config says this stored figure is wrong, else None.

    ONE FUNCTION, TWO CALLERS, SO THEY CANNOT DISAGREE. aggregate() uses it to blank the value
    and set the status; confidence_for() uses it to return RED with the same reason. Before this
    existed, aggregate knew about three of them and confidence_for knew about five, which is
    exactly how three mechanisms ended up displaying a number they had just declared wrong.

    Order matters only for which reason is reported when several apply; every one of them blanks,
    so no case can be lost by the ordering. Most specific first: a re-purposed contract is a more
    useful thing to be told than a changed measuring point, even when both are true.
    """
    source = str(row.get("source") or "")

    # 1. REMOVED. The contract key is gone from config, so the row measures something this tool
    #    decided not to measure. Sky's burn_zero rows outlived their contract by days.
    orphans = config.orphaned_contract_keys(project, source)
    if orphans:
        return "orphaned", (
            f"ORPHANED — this row was written by contract(s) {', '.join(orphans)}, which are no "
            f"longer in config. The store upserts and never deletes, so it survived their removal. "
            f"It measures something this tool has decided not to measure, and its age says nothing "
            f"about that. Clear it from the store; see orphan_cleanup.sql.")

    # 2. RE-PURPOSED. The key is still there and its KIND moved. The orphan check above asks only
    #    whether the key exists, so Ether.fi's sethfi rows passed it for two runs after the change.
    withdrawn = config.withdrawn_contract_keys(project, metric, source)
    if withdrawn:
        return "withdrawn", (
            f"MEASURING CONTRACT WITHDRAWN — this row was written by {', '.join(withdrawn)}, which "
            f"still exists in config but no longer serves {metric}. The reading may be perfectly "
            f"good; it belongs in a different column. Re-attribute or clear it; see "
            f"orphan_cleanup.sql.")

    # 3. DERIVATION SWITCHED OFF. Suppression stops NEW rows and cannot touch old ones — the same
    #    asymmetry that let Maple's 0.51 survive its dispute.
    supp = config.derivation_suppressed(project, metric)
    if supp and supp.get("renders_as") == "n/a":
        supp = None          # handled as n/a in aggregate — see the note there
    if supp:
        return "suppressed", (
            f"DERIVATION SUPPRESSED — this figure came from a derivation config has since switched "
            f"off, and it is a value the tool would no longer compute. {supp.get('why', '')} "
            f"Cleared by: {supp.get('resolves_when', 'see config')}. Clear the stored rows; see "
            f"orphan_cleanup.sql.").strip()

    # 4. ROLE IN DOUBT. The address is right and the read works; what the contract IS for this
    #    project is disputed. Burn, hold and distribute have opposite signs on float.
    disputed = config.destination_disputed(project, metric)
    if disputed:
        return "disputed", (
            f"DESTINATION DISPUTED — the role of contract(s) {', '.join(disputed['contracts'])} as "
            f"this project's destination is in doubt, so no figure is shown whatever the store "
            f"holds. {disputed.get('why', '')}").strip()

    # 5. TWO MEASURING POINTS. A window spanning the change reports the move between two different
    #    addresses as though it were a flow.
    points = row.get("measuring_points") or ()
    if len(points) > 1 and (refusal := handover_refusal(project, metric, points, row.get("point_spans") or {},
                                                        row.get("point_edges"))):
        return "measuring_point_changed", (
            f"MEASURING POINT CHANGED — this series was read from {len(points)} different places "
            f"over its history ({', '.join(sorted(points))}). A window spanning the change reports "
            f"the move between two different addresses as though it were a flow. The figure is not "
            f"understated or overstated by a little; it is the gap between two unrelated "
            f"measurements. Clear the superseded rows, or declare the pair a handover if the two "
            f"legs are one continuous series — {refusal}."
            + (f" {row['method_change']}." if row.get("method_change") else ""))

    # 6. ONE OBSERVATION IS TOO LARGE TO BE A FLOW — the read-time half of case 5.
    #
    #    Case 5 fires on EVIDENCE of a measuring-point change: two distinct source strings in the
    #    stored history. Case 5 and the write-time guard in fetch.base.derive_flow_from_cumulative
    #    between them were supposed to make this impossible. Both missed the same row, and the
    #    audit of 2026-09-21 found it still on the sheet a week later:
    #
    #      Uniswap gross_burn_tokens "now" (trailing 30d)  111,941,581
    #      Uniswap burn_address_balance (cumulative)       111,953,581
    #
    #    One day's burn, 99.99% of every UNI ever burned, against a real rate of 100-200k/day.
    #    It was written on 2026-09-14 when the read moved from fire_pit to burn_dead; the write-
    #    time guard was added AFTER it, and it cannot reach back. Case 5 could not see it either,
    #    because the row carries the NEW source (chain:ethereum:burn_dead:...:delta) — the
    #    fire_pit rows that would have made two measuring points were deleted by the orphan
    #    cleanup, taking the evidence with them and leaving the consequence behind.
    #
    #    SO THIS CHECKS THE ARITHMETIC RATHER THAN THE PROVENANCE. A differenced flow is bounded
    #    by the stock it is differenced from, whatever its source string says and whoever deleted
    #    what. It needs no history, no second source and no config declaration — which is what
    #    makes it the check that still works when the other two have been outmanoeuvred.
    cum, biggest = row.get("cumulative_ref"), row.get("max_single_delta")
    if cum and biggest and biggest > cum * IMPLAUSIBLE_DELTA_SHARE:
        parent = flow_stock(project, metric) or "the cumulative balance"
        return "implausible_delta", (
            f"ONE OBSERVATION IS {biggest / cum:.0%} OF THE CUMULATIVE — not a flow. A single "
            f"reading of {biggest:,.0f} sits against a {parent} of {cum:,.0f}, so this window "
            f"reports most of every {metric.replace('_', ' ')} ever recorded as though it "
            f"happened in one period. The usual cause is a measuring-point change differenced "
            f"across: the address moved, and the step between two unrelated balances was stored "
            f"as a burn. Clear the offending row from the store — see orphan_cleanup.sql — and "
            f"this clears itself; nothing in config needs changing.")

    # 7. THE FLOW DOES NOT ACCOUNT FOR THE STOCK'S MOVE — the telescoping identity, broken.
    #
    #    Case 6 asks whether ONE observation is too big. This asks whether ALL of them add up,
    #    which is a different failure and catches the opposite sign: a flow series that is too
    #    SMALL. Hyperliquid, 2026-09-21: burn_address_balance moved 231,934 across the day and
    #    gross_burn_tokens recorded 83,344 of it. Nothing about the 83,344 looked wrong — it is
    #    the right order of magnitude, from the right address, on the right day, and it was
    #    roughly a third of the truth.
    #
    #    THE CAUSE WAS THE SAME-DAY RE-RUN, now fixed at the write path: the hypercore adapter
    #    differenced against latest_values() (this morning's reading) while carrying
    #    values_before()'s date, so each of the four runs that day wrote only the increment since
    #    the last one and overwrote the previous row on the (date, project, metric) key. The fix
    #    stops new rows being written that way. It cannot repair the rows already stored, and no
    #    guard keyed on plausibility could ever have seen them — only the arithmetic can.
    #
    #    ZERO TOLERANCE BEYOND FLOAT PRECISION. The identity is exact, so any residual that
    #    survives TELESCOPING_*_EPS is tokens the flow column failed to report.
    moved, accounted = row.get("flow_stock_move"), row.get("flow_accounted")
    if moved is not None and accounted is not None:
        residual = moved - accounted
        if abs(residual) > max(TELESCOPING_ABS_EPS, abs(moved) * TELESCOPING_REL_EPS):
            parent = flow_stock(project, metric) or "the cumulative balance"
            span = row.get("flow_span") or ("?", "?")
            return "unreconciled_flow", (
                f"THE FLOWS DO NOT SUM TO THE STOCK'S MOVE — {accounted:,.4f} recorded against "
                f"{moved:,.4f} of movement in {parent} between {span[0]} and {span[1]}, leaving "
                f"{residual:,.4f} unaccounted for. Each of these figures is the difference of two "
                f"readings of the same balance, so consecutive ones must telescope: every "
                f"intermediate reading appears once with each sign and cancels. They do not, so "
                f"at least one period's movement was never written or was written twice — the "
                f"usual cause is a same-day re-run differencing against its own earlier row and "
                f"overwriting it on the (date, project, metric) key. The column is not slightly "
                f"off; it is missing whole periods. Clear the affected rows and let them "
                f"re-derive — see orphan_cleanup.sql.")

    # 8. MECHANISM REFUTED. Scoped to BURN_METRICS — a refuted burn mechanism says nothing about
    #    total_supply. No "does this project claim a burn" guard is needed: burn_mechanism()
    #    defaults to 'assumed' and never to 'refuted', so this only exists where declared.
    if metric in config.BURN_METRICS:
        mech = config.burn_mechanism(config.PROJECT_BY_NAME.get(project) or {})
        if mech.get("status") == "refuted":
            return "refuted", (
                f"MECHANISM REFUTED — this project does not burn by {mech.get('model')!r} in a way "
                f"{metric} can measure, and that has been positively established rather than "
                f"assumed. The reading may be real; it is not this metric. {mech.get('note') or ''} "
                f"Source: {mech.get('source_url') or mech.get('source_note') or 'see burn_mechanism in config'}. "
                f"Clear the superseded rows; the adapter already refuses to write new ones.").strip()

    # 8b. REAL BUT UNCLASSIFIED. After REFUTED: a mechanism that does not exist outranks a
    #     figure whose label is undecided. The figure passed every check and its meaning is undecided —
    #     Sky's other_burn_balance, confirmed by the supply identity and dominated by migration
    #     plumbing (config Sky.classification_pending). Blanked like the rest: a reader acts on
    #     the number under the label, and the label is what is not settled.
    #     ALSO BLOCKED (2026-09-28): a READ-TIME VIEW decided the series cannot be shown —
    #     declared vs observed issuance more than 10x apart, a first-party series that has not
    #     landed, a derived release over a flat circulating figure. See _issuance_views.
    pending = config.classification_pending(project, metric)
    if pending or (project, metric) in _VIEW_BLOCKS:
        return "blocked", (_VIEW_BLOCKS.get((project, metric)) or (
            f"BLOCKED — {pending['reason']}. Cleared by: "
            f"{pending.get('resolves_when', 'see config')}."))

    # 9. THE STORED STOCK IS OUTSIDE ITS DECLARED BOUND. Added 2026-09-24.
    #    fetch.validate rejects an out-of-bound value at WRITE time, and cannot reach a row
    #    stored before the bound was declared. Aethir's locked_tokens was that row: 264.947427
    #    ATH, stored before the 1.2bn floor existed, rendering as "review" and readable as real.
    #    The same config.sanity_bounds the write path uses, so the two cannot disagree. Stocks
    #    only: a flow's `now` is a window sum and the bounds are per observation.
    latest = row.get("latest_value")
    if latest is not None and (config.METRICS.get(metric) or {}).get("kind") == "stock":
        lo, hi = config.sanity_bounds(project, metric, row.get("latest_date") or None)
        if (lo is not None and latest < lo) or (hi is not None and latest > hi):
            why = ((config.PROJECT_BY_NAME.get(project) or {}).get("sanity") or {}).get(metric, {}).get("why", "")
            return "out_of_bounds", (
                f"OUTSIDE ITS DECLARED BOUND — the stored figure {latest:,.6f} is outside "
                f"[{'-inf' if lo is None else f'{lo:,.0f}'}, {'inf' if hi is None else f'{hi:,.0f}'}], the range the write path now enforces. It was stored "
                f"before that bound existed, and a new read outside it is rejected, not stored. "
                f"{why} Clear the stored row; see orphan_cleanup.sql.").strip()
    return None


def daily_twin(daily: pd.Series, monthly: pd.Series, tol: float = 0.005) -> tuple[bool, str]:
    """(is the "daily" series its monthly twin?, the evidence). G13 1c (Jake's 10-21 store: buyback_spending_usd_ba_daily
    equal to buyback_spending_usd_ba to the dollar in EVERY window, Now and Prior 30d included — 2,791,800 / 1,644,300 —
    while G12's quarter comparison let it through). VALUE-BASED, so the twin is found whatever day of the month the
    answer dates its rows on and whatever zero rows or extra history it carries: every NON-ZERO row of the daily series
    inside the monthly series' span (less a month) matches a monthly row's value within `tol`, dated within 31 days of
    it, and at least two rows match. A real daily series (many rows a month, each a fraction of the month) never does."""
    d = daily[daily.abs() > 0]
    if d.empty or monthly.empty:
        return False, "nothing to compare"
    lo, hi = monthly.index.min() - pd.Timedelta(days=31), monthly.index.max() + pd.Timedelta(days=31)
    d = d[(d.index >= lo) & (d.index <= hi)]
    if len(d) < 2:
        return False, f"{len(d)} non-zero row(s) inside the monthly span"
    matched = 0
    for day, v in d.items():
        near = monthly[(monthly.index >= day - pd.Timedelta(days=31)) & (monthly.index <= day + pd.Timedelta(days=31))]
        if not any(abs(float(v) / float(m) - 1) <= tol for m in near if m):
            return False, f"{day.date()} {float(v):,.0f} matches no monthly row within 31 days"
        matched += 1
    return True, (f"every one of its {matched} non-zero row(s) {d.index.min().date()}..{d.index.max().date()} is a "
                  f"monthly row's value (±{tol:.1%}, ±31 days)")


SUBSIDY_COMMON: dict = {}           # project -> {"from", "first", "last", "days"} of the last build (G13 6)


def _per_day(g) -> pd.Series:
    """A flow as its amount PER DAY on every day it covers, by its observed cadence: a weekly row (dated the week's
    first day, _age_in_days) spread over its seven days, a monthly row over its month, a daily row as it is. G13 6:
    Aethir's revenue is weekly, its (b) daily — a sum over the days both "hold" would set a week against a day."""
    # D5-1a: a month spread over its days (the history before a weekly series) is already one row per day
    spread = (g["source"].astype(str).str.contains(MONTH_SPREAD_TAG, regex=False)
              if g is not None and not g.empty and "source" in g else None)
    if spread is not None and spread.any():
        rest = _per_day(g[~spread]) if (~spread).any() else pd.Series(dtype=float)
        out = pd.concat([_daily(g[spread]), rest])
        return out[~out.index.duplicated(keep="last")].sort_index()
    s = _daily(g)
    cad = _observed_cadence(g)
    if s.empty or cad == "daily":
        return s
    parts = []
    for d, v in s.items():
        days = (pd.date_range(d, d + pd.Timedelta(days=6)) if cad == "weekly"
                else pd.date_range(d.to_period("M").start_time, d.to_period("M").end_time.normalize()))
        parts.append(pd.Series(float(v) / len(days), index=days))
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="last")].sort_index()


def not_read_since(groups: dict, name: str, metric: str, g=None) -> str:
    """G14 1: "STALE — last read <date>, not read on <day>" for a chain-read STOCK whose newest row is older than the
    project's newest price row (the run's day for that project: its chain tier timed out or failed), else ''. The last
    stored value stands; a fallback never writes into the series (fetch._refuse_fallback_into_primary)."""
    if (METRICS.get(metric) or {}).get("kind") != "stock":
        return ""
    g = groups.get((name, metric)) if g is None else g
    px = groups.get((name, "price_usd"))
    if g is None or g.empty or px is None or px.empty:
        return ""
    g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
    if not str(g["source"].iloc[-1]).startswith("chain:"):
        return ""
    last, day = g["date"].iloc[-1], pd.to_datetime(px["date"]).max().normalize()
    return (f"STALE — last read {last.date()}, not read on {day.date()} (the chain read did not answer); the last "
            f"stored value stands" if last < day else "")


def _subsidy_common_views(groups: dict, asof) -> None:
    """G13 6 (config.SUBSIDY_COMPLETE_FROM): subsidy cover's numerator and denominator on the days EVERY leg covers from
    the day the (b) denominator is complete — the first day on its measured leg (else the declared date) — inside Q0,
    each leg per day by its cadence (_per_day). Written as subsidy_cover_num_usd / subsidy_cover_den_usd (each the
    same days), so the cell's ratio is per covered day by construction."""
    SUBSIDY_COMMON.clear()
    period = int(GLOBALS["period_days"])
    hi = pd.Timestamp(asof).normalize()
    lo = hi - pd.Timedelta(days=period)
    for (name, cat), spec in config.SUBSIDY_COMPLETE_FROM.items():
        p = config.PROJECT_BY_NAME.get(name)
        g = groups.get((name, spec["metric"]))
        if p is None or g is None or g.empty:
            continue
        meas = g[g["source"].astype(str).map(lambda s: trajectory.basis_of(spec["metric"], s)) == spec["measured_point"]]
        start = pd.to_datetime(meas["date"]).min().normalize() if not meas.empty else pd.Timestamp(spec["declared"])
        num = _per_day(groups.get((name, _cp_revenue_metric(p))))
        dens = [_per_day(groups.get((name, f"outflow_{c}_usd"))) for c in (cat,)]
        if num.empty or any(d.empty for d in dens):
            continue
        df = pd.concat([num.rename("n")] + [d.rename(f"d{i}") for i, d in enumerate(dens)], axis=1, sort=True).dropna()
        df = df[(df.index >= start) & (df.index > lo) & (df.index <= hi)]
        info = {"from": str(start.date()), "days": int(len(df)),
                "first": str(df.index.min().date()) if len(df) else None,
                "last": str(df.index.max().date()) if len(df) else None}
        SUBSIDY_COMMON[name] = info
        if len(df) < RATE_MIN_DAYS:
            continue                          # the cell says NEEDS HISTORY: nothing would read the two series
        cols = g.columns
        tag = f"derived:subsidy_common[({cat}) complete from {info['from']}; {info['days']} common day(s)]"
        for m, v in (("subsidy_cover_num_usd", df["n"]), ("subsidy_cover_den_usd", df.drop(columns="n").sum(axis=1))):
            groups[(name, m)] = _as_stored(pd.DataFrame({"date": v.index, "project": name, "metric": m,
                                                         "value": v.values, "tier": 2, "source": tag}), cols)


def _daily_twin_views(groups: dict, asof) -> None:
    """G11 D3 / G13 1c (config.DAILY_TWIN): a declared "daily" series whose rows are its monthly twin's values
    (daily_twin) is the monthly series under a daily name. Windowed as days, a month's total dated on one day lands in
    whichever trailing window holds that day — which is why it equalled the monthly series in Now and Prior 30d. It is
    dropped, the reason said on its row; the monthly series stays (the Credibility reference for actual_buyback_usd)."""
    for (name, daily_m), monthly_m in config.DAILY_TWIN.items():
        d_, m_ = _daily(groups.get((name, daily_m))), _daily(groups.get((name, monthly_m)))
        if d_.empty or m_.empty:
            continue
        twin, why = daily_twin(d_, m_)
        if twin:
            groups.pop((name, daily_m), None)
            _VIEW_BLOCKS[(name, daily_m)] = (f"BLOCKED — NOT DAILY: {why} — the by-day read returned the monthly series "
                                             f"({monthly_m}, kept as the reference); dropped until a by-day answer is "
                                             f"daily (fetch/sky_accounting.looks_daily refuses it)")


HANDOVER_OVERLAP: dict = {}     # (project, metric) -> the declared handover's overlap check, last build (G11 B8 / C1)


def handover_overlap(groups: dict, project: str, spec: dict) -> dict:
    """A DECLARED HANDOVER'S OVERLAP CHECK (G11 B8 / C1): the two legs, each stored as its own series (spec["a"] — the
    earlier point, spec["b"] — the later), compared on every day both hold. {"common_days", "latest", "a", "b", "gap",
    "worst_gap", "passed", "text"}. passed: at least min_days common days, every one within tolerance_pct. No common day
    is said, never passed: the seam is then unchecked.

    ONE LEG OF A STITCHED SERIES (P2a D4/D5, Jake 2026-10-27): spec["a_point"] / spec["b_point"] restrict a side to the
    rows of ONE measuring point (fetch.base._measuring_point; a summed point matches by its component SET, so the order
    the wallets were read in does not matter) — Maple's page tile is a leg of treasury_holding_tokens, compared with the
    chain series on the days both were stored. Nothing else changes: the same tolerance, the same minimum days."""
    from fetch.base import _measuring_point                        # noqa: PLC0415

    def daily(m, point=None):
        g = groups.get((project, m))
        if g is None or g.empty:
            return pd.Series(dtype=float)
        if point:
            want = _measuring_point(point)
            mp = g["source"].astype(str).map(_measuring_point)
            same = mp.map(lambda x: x == want or ("sum(" in x and "sum(" in want
                                                  and sum_components(x) == sum_components(want)))
            g = g[same.astype(bool)]
            if g.empty:
                return pd.Series(dtype=float)
        return (g.assign(d=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("d", keep="last")
                .set_index("d")["value"].astype(float))
    a, b = daily(spec["a"], spec.get("a_point")), daily(spec["b"], spec.get("b_point"))
    tol, need = float(spec.get("tolerance_pct", 2.0)), int(spec.get("min_days", 3))
    common = a.index.intersection(b.index)
    common = common[a[common] != 0]
    out = {"a_metric": spec["a"], "b_metric": spec["b"], "common_days": int(len(common)), "tolerance_pct": tol,
           "min_days": need, "passed": False}
    la = spec["a"] + (f" [{spec['a_label']}]" if spec.get("a_label") else "")
    lb = spec["b"] + (f" [{spec['b_label']}]" if spec.get("b_label") else "")
    if not len(common):
        out["text"] = (f"OVERLAP CHECK: {la} and {lb} share no stored day — the seam is UNCHECKED"
                       + (f" ({spec['why_none']})" if spec.get("why_none") else ""))
        return out
    gap = (b[common] / a[common] - 1.0) * 100.0
    last, worst = common.max(), gap.abs().idxmax()
    out.update(latest=str(last.date()), a=float(a[last]), b=float(b[last]), gap=float(gap[last]),
               worst_gap=float(gap[worst]), worst_day=str(worst.date()))
    out["passed"] = len(common) >= need and float(gap.abs().max()) <= tol
    out["text"] = (f"OVERLAP CHECK {'PASSED' if out['passed'] else 'NOT PASSED'}: {lb} vs {la} on "
                   f"{len(common)} common day(s) — {last.date()}: {b[last]:,.6g} vs {a[last]:,.6g} ({gap[last]:+.2f}%); "
                   f"worst {gap[worst]:+.2f}% on {worst.date()}; tolerance ±{tol:g}% over ≥{need} days"
                   + ("" if out["passed"] else f" — {spec.get('if_not_passed', 'a CHANGE OF BASIS at the seam, not a flow')}"))
    return out


def handover_reference_check(groups: dict, project: str, spec: dict) -> dict:
    """A DECLARED HANDOVER CHECKED AGAINST ONE REFERENCE (P2c-1, Aerodrome fdv_usd): each leg's supply basis at the
    cutover — the last row of the earlier point before spec["cutover"], the first row of the later point from it, both
    from the build's spec["metric"] series — and each of the declared same-block reads (spec["block_reads"]), against
    spec["reference"]["value"], within tolerance_pct (the repo's ±2%, as handover_overlap). Same shape as
    handover_overlap: {"passed", "text", "legs": [...]}. A leg with no stored row is UNCHECKED, never passed; a leg
    outside the tolerance is NOT PASSED with its gap, absolute and %, in the text (never hidden)."""
    from fetch.base import _measuring_point                        # noqa: PLC0415
    ref, tol = spec["reference"], float(spec.get("tolerance_pct", 2.0))
    cut = pd.Timestamp(spec["cutover"])
    rv = float(ref["value"])

    def judge(label, value):
        if value is None:
            return {"leg": label, "value": None, "gap": None, "gap_pct": None, "passed": False,
                    "text": f"{label}: no stored row — UNCHECKED"}
        gap = float(value) - rv
        pct = gap / rv * 100.0
        ok = abs(pct) <= tol
        return {"leg": label, "value": float(value), "gap": gap, "gap_pct": pct, "passed": ok,
                "text": f"{label} {float(value):,.0f}, gap {gap:+,.0f} ({pct:+.2f}%) "
                        + ("within" if ok else "OUTSIDE") + f" ±{tol:g}%"}
    g = groups.get((project, spec["metric"]))
    legs = []
    for i, (name, point) in enumerate(spec["legs"]):
        val, day = None, None
        if g is not None and not g.empty:
            d = g.assign(_d=pd.to_datetime(g["date"]).dt.normalize(),
                         _p=g["source"].astype(str).map(_measuring_point))
            d = d[d["_p"] == _measuring_point(point)].sort_values("_d")
            d = d[d["_d"] < cut] if i == 0 else d[d["_d"] >= cut]
            if not d.empty:
                row = d.iloc[-1] if i == 0 else d.iloc[0]
                val, day = float(row["value"]), row["_d"].date()
        legs.append(judge(f"{name} leg ({point}{', ' + str(day) if day else ''})", val))
    reads = [judge(f"{name} at block {r['block']:,}", r.get(name)) for r in spec.get("block_reads") or ()
             for name, _pt in spec["legs"]]
    passed = all(x["passed"] for x in legs + reads)
    text = (f"REFERENCE CHECK {'PASSED' if passed else 'NOT PASSED'}: each leg's {spec['metric']} at the "
            f"{cut.date()} cutover vs {ref['what']} {rv:,.2f} at Base block {int(ref['block']):,} ({ref['given']}; "
            f"read {ref['read_on']}) — " + "; ".join(x["text"] for x in legs)
            + (" | the same at one block (read " + ref["read_on"] + "): " + "; ".join(x["text"] for x in reads)
               if reads else "")
            + ("" if passed else f" — {spec.get('if_not_passed', 'a CHANGE OF BASIS at the seam, not a flow')}"))
    return {"passed": passed, "text": text, "legs": legs, "block_reads": reads, "tolerance_pct": tol,
            "reference": rv}


def _handover_overlap_checks(groups: dict) -> None:
    """Every declared series_handover with an "overlap" spec, checked on this build's groups (G11 B8 / C1); and every
    one with a "reference_check" (P2c-1) whose series reaches its cutover in this build."""
    HANDOVER_OVERLAP.clear()
    for p in scoped_projects():
        # P2b-2: a restatement's pending handover is declared for the build that found the superseded rows
        active = {m: d for (n, m), d in config.RESTATEMENT_ACTIVE.items() if n == p["name"]}
        for metric, decl in {**active, **(p.get("series_handover") or {})}.items():
            if isinstance(decl, dict) and decl.get("overlap"):
                HANDOVER_OVERLAP[(p["name"], metric)] = handover_overlap(groups, p["name"], decl["overlap"])
            if isinstance(decl, dict) and decl.get("reference_check"):
                g = groups.get((p["name"], metric))
                rc = decl["reference_check"]
                if g is not None and not g.empty and pd.to_datetime(g["date"]).max() >= pd.Timestamp(rc["cutover"]):
                    HANDOVER_OVERLAP[(p["name"], metric)] = handover_reference_check(groups, p["name"], rc)


SERIES_BREAK_NOTED: dict = {}   # (project, metric) -> the declared break's text, when the series holds its day (P2c-2)


def _series_break_notes(groups: dict) -> None:
    """P2c-2: a declared break whose named day is in this build's series (config.SERIES_BREAK_NOTES) — its text travels
    on the Data row note and the audit cell comment (aggregate / confidence_for)."""
    SERIES_BREAK_NOTED.clear()
    for (proj, metric), b in (getattr(config, "SERIES_BREAK_NOTES", {}) or {}).items():
        g = groups.get((proj, metric))
        if g is not None and not g.empty and (pd.to_datetime(g["date"]).dt.normalize() == pd.Timestamp(b["day"])).any():
            SERIES_BREAK_NOTED[(proj, metric)] = f"DECLARED SERIES BREAK {b['break']}: {b['text']}"


def confidence_for(project: str, metric: str, row: dict, asof: pd.Timestamp) -> tuple[str, str]:
    """GREEN / AMBER / RED, derived mechanically. Returns (band, why).

    Every input is state already held — status, point count, the PARTIAL marker in the source
    string, the verified flag on the contracts serving the metric, the burn mechanism's status,
    and the two declared cases (manual-quarterly age, and non-comparable composition). Nothing
    here is a judgement typed in by hand, because a hand-assigned confidence is an opinion that
    goes stale the moment the underlying data moves.
    """
    # EMPTY OR NOT-APPLICABLE CELLS. These are states of the STORE, not verdicts on a figure —
    # there is no number here to be wrong — so they are kept apart from the withheld cases.
    if row["status"] == "n/a":
        # THE ONE BAND THAT IS NOT A VERDICT ON A FIGURE. There is no cell to fill here, so
        # there is nothing to be right or wrong about — see CONFIDENCE_NA. The note carries the
        # project's own reason where it declared one, because "not applicable" on its own sends
        # a reader looking for the source that is missing.
        return CONFIDENCE_NA, (str(row.get("note") or "").split(" | ")[0]
                               or "not applicable to this project")
    if row["status"] == "pending":                   # G10 2e: a first epoch not yet closed — dated, not a failure
        return "AMBER", str(row.get("note") or "").split(" | ")[0]
    if row["status"] in ("missing", "gap", "waiting"):
        return "RED", {"missing": "no value in the store",
                       "gap": "unresolved — see the Gap Report",
                       # RED because the cell is empty, not because anything is wrong: the guard is
                       # armed and waiting on a suppressed primary. The note says which.
                       "waiting": "armed cross-check, waiting on a suppressed primary — see the note",
                       }[row["status"]]

    # THE SEVEN WITHHELD CASES, all RED and all blanked by aggregate(). Asked here directly rather
    # than read off row["status"], so confidence_for gives the same answer whether or not
    # aggregate has already run over the row.
    w = withheld_for(project, metric, row)
    if w:
        return "RED", w[1]

    why = []
    # correct, and answering a different question from the column it sits in (failure mode 4)
    # ** THE ROW'S OWN SOURCE DECIDES. ** A caveat about how a figure was computed is retired
    # by the figure changing source, not by a config flag saying a better route exists — a
    # confirmed route that fails to write leaves the OLD rows on the sheet, and they still have
    # the old bias. See config.is_non_comparable.
    sus = config.break_suspect(project, metric, row.get("source"))
    if sus:
        why.append(sus)
    inc = config.incentive_caveat(project, metric)
    if inc:
        why.append(inc)
    m_org = re.search(r"\[(organic window \(post-Points-Season-2\)[^\]]*)\]", str(row.get("source") or ""))
    if m_org:
        why.append(f"{m_org.group(1)} — the full year, incentive period included, is network_reserve_ratio_incl_incentive")
    nc = config.is_non_comparable(project, metric, row.get("source"))
    if nc:
        why.append(f"NOT COMPARABLE: {nc['why']} Use {nc['use_instead']}")
    # A buyback whose destination is indeterminate cannot be read as retiring supply OR as
    # locking it — those are opposite signs, and the truth is neither. Same band as PARTIAL and
    # single-observation, decided here rather than by a separate check somewhere else.
    ind = config.destination_indeterminate(project, metric)
    if ind:
        why.append(f"DESTINATION INDETERMINATE: repurchased tokens go to {ind['fund']}, whose stated "
                   f"uses include {', '.join(ind['stated_uses'])} — so {ind['why_indeterminate']}. "
                   f"Do not net it against emissions like a burn, or count it as locked supply")
    # THE FINE SERIES DISAGREES WITH THE PROTOCOL'S OWN COARSE FIGURE. A real number, qualified:
    # one of the two publications is wrong or a period has been mis-transcribed, and the stored
    # series is the one every downstream figure is computed from. Not withheld — withholding it
    # would leave the downstream cells with nothing, and the disagreement is what needs reading.
    # THE TELESCOPING CHECK DID NOT RUN. Not RED — nothing is known to be wrong — but not
    # silent either: a flow whose reconciliation was skipped looks exactly like one that passed,
    # and the five projects whose SQL preview came back blank are the argument for saying so.
    blocked = row.get("flow_recon_blocked")
    if blocked:
        why.append(f"THE FLOW RECONCILIATION DID NOT RUN — {blocked}")
    rec = row.get("reconciliation") or {}
    if rec.get("status") == "disagrees":
        bits = "; ".join(f"{d['period']}: months sum to {d['summed']:,.0f} against a published "
                         f"{d['published']:,.0f}, off by {d['off_by']:+,.0f}" for d in rec["detail"])
        why.append(f"DOES NOT RECONCILE WITH THE PUBLISHED PERIOD TOTAL — {bits}. {rec.get('why', '')}")
    if ":PARTIAL" in str(row.get("source") or ""):
        why.append("PARTIAL — summed over known components only, so it understates")
    if ":estimate" in str(row.get("source") or ""):
        why.append("ESTIMATE — part of it is estimated from other stored series, not read (see the source)")
    # THE CAP IN THE SUPPLY COLUMN. The number is right and answers a different question, which
    # is failure mode 4 in a form no PARTIAL marker catches: nothing about 2,000,000,000 looks
    # incomplete. Scoped to total_supply itself — the project's other metrics are unaffected.
    if metric == "total_supply":
        cap = config.supply_denominator_unusable(project)
        if cap:
            why.append(f"THIS IS THE CAP, NOT THE MINTED SUPPLY: {cap}")
    # AN ACCEPTED HANDOVER IS STILL TWO SOURCES, AND THE READER HAS TO KNOW WHERE THE SEAM IS.
    # The declaration stops the series being blanked; it does not make it a single measurement.
    # Where the legs also differ in WHAT they cover — GEODNET's backfill sums Polygon and Solana
    # burns while the live read is Polygon alone — that difference is the disclosure, because a
    # month-on-month comparison across the seam is the reading it breaks.
    handover = config.declared_handover(project, metric)
    if handover and len(row.get("measuring_points") or ()) > 1:
        legs = " then ".join(handover.get("ordered_points") or ())
        why.append(f"STITCHED SERIES: {legs}, handed over with no overlap"
                   + (f". {handover['composition_change']}" if handover.get("composition_change") else ""))
    # G11 B8 / C1: a declared handover's overlap check travels on the cell, whether or not this row's points differ
    # (a read-time view such as circulating_supply_used keeps one measuring point while its basis changes)
    if row.get("modelled"):
        why.append(f"{row['modelled']} — not a measurement; shown, excluded from any scoring")
    if (project, metric) in RESTATEMENT_STATUS:
        why.append(RESTATEMENT_STATUS[(project, metric)])     # P2b-2: days still on the superseded set, or holes
    if (project, metric) in HANDOVER_CUT:
        why.append(HANDOVER_CUT[(project, metric)])
    if (project, metric) in SERIES_BREAK_NOTED:
        why.append(SERIES_BREAK_NOTED[(project, metric)])    # P2c-2: a named day off its series' basis, not smoothed
    if (MONTHLY_FLOW_FORMED.get((project, metric)) or {}).get("holes"):
        why.append("months NOT FORMED (a day not held is a hole, never a zero): "
                   + "; ".join(MONTHLY_FLOW_FORMED[(project, metric)]["holes"]))
    if (project, metric) in HANDOVER_CONTINUED:
        why.append(HANDOVER_CONTINUED[(project, metric)])     # P2a D5a: the declared next leg, and from when
    if (project, metric) in LOCKUP_AGE_AMBER:
        why.append(LOCKUP_AGE_AMBER[(project, metric)])      # D3-1: a lockup read 11-30 days old
    ho = HANDOVER_OVERLAP.get((project, metric))
    if ho and (not ho["passed"] or why):          # a PASSED seam alone does not turn a cell AMBER
        why.append(f"DECLARED HANDOVER ({(handover or {}).get('declared', '')}): "
                   + ho["text"])
    # A FAILED VALIDATION IS NEVER GREEN (G10 2d: settlement_rebuilt_usd read GREEN beside "validation FAILED" in its own
    # source). The result travels on the cell.
    vfail = re.search(r"validation (FAILED|NOT VALIDATED)(?::\s*([^\]]*))?", str(row.get("source") or ""))
    if vfail:
        why.append(f"VALIDATION {vfail.group(1)}" + (f": {vfail.group(2).strip()[:300]}" if vfail.group(2) else ""))
    if int(row.get("n_points") or 0) < 2:
        why.append("a single observation — no trend, and nothing to validate it against")
    # A SHORT WINDOW IS NOT A SMALL NUMBER. A 10-day sum under a 30-day header understates by
    # roughly two thirds and looks exactly like a real fall in activity — the one thing a reader
    # is most likely to act on. Point count does not catch it: eight points over ten days is a
    # densely sampled series that still covers a third of the window.
    covered, window = row.get("covered_days"), row.get("window_days")
    if window and covered is not None and covered < window:
        why.append(f"covers only {covered} of the {window} days in the window — the figure is a "
                   f"{covered}-day total, not a low {window}-day one")
    if row.get("q0_dense_note"):
        why.append(row["q0_dense_note"])      # G9 K3: the Q0 days a dense daily series does not hold, named
    if row["status"] == "review":
        why.append("flagged to the Review Queue")
    if row["status"] == "stale":
        limit = (config.MANUAL_QUARTERLY_STALE_DAYS if config.is_manual_quarterly(project, metric)
                 else row.get("stale_limit") or config.stale_after_days(project, metric, GLOBALS["stale_after_days"]))
        why.append(f"stale — no fresh value in {limit} days")
    if row["status"] == "manual" and config.is_manual_quarterly(project, metric):
        entered = str(row.get("entered_on") or "")[:10]
        try:
            age = (asof - pd.Timestamp(entered)).days
            if age > config.MANUAL_QUARTERLY_STALE_DAYS:
                why.append(f"hand-entered {age} days ago — due for a quarterly refresh")
        except (ValueError, TypeError):
            why.append("hand-entered, with no entry date recorded")
    # THE MECHANISM FLAG APPLIES ONLY WHERE A BURN IS ACTUALLY CLAIMED.
    # config.burn_mechanism() returns model 'undetermined' / status 'assumed' for any project
    # with no block, which is right for a project that burns and has not said how — and WRONG for
    # one that does not burn at all. gross_burn_tokens is in the metric library for every
    # archetype 1 chain, so Plume (archetype [1], no burn_split, no burn_read_method, no
    # burn_mechanism block, no burn anywhere in its design) was carrying "the burn MECHANISM is
    # assumed, not documented" on a burn it has never claimed to have. That is an AMBER that tells
    # the reader to go and check something that does not exist, which is worse than silence
    # because it spends attention.
    #
    # A project claims a burn if it holds archetype 4, or declares a share of fees burned, or
    # declares a read route for one, or declares a mechanism block at all. Absent all four there
    # is nothing to flag. This NARROWS the flag; it never suppresses one on a project that does
    # burn, because any of those four is enough to keep it firing.
    if metric in config.BURN_METRICS:
        p = config.PROJECT_BY_NAME.get(project) or {}
        claims_a_burn = (4 in (p.get("archetypes") or ())
                         or p.get("burn_split") is not None
                         or p.get("burn_read_method") is not None
                         or p.get("burn_mechanism") is not None)
        if claims_a_burn and config.burn_mechanism(p).get("status") == "assumed":
            why.append("the burn MECHANISM is assumed, not documented")
    unverified = config.metric_addresses_unverified(project, metric)
    if unverified:
        why.append(f"read from address(es) never verified against protocol docs: {', '.join(unverified)}")
    if row.get("not_read"):
        why.append(row["not_read"])                  # G14 1: the last stored value stands, dated

    return ("AMBER", " | ".join(why)) if why else ("GREEN", "")


def _reward_end_views(groups: dict, asof: pd.Timestamp) -> None:
    """A reward RATE is not carried past its end date (Chainlink, Jake 2026-09-29).

    Every RewardVault bucket ends 2026-11-27. A read after that returns the rate as 0 by itself
    (fetch/reward_vault.py counts active buckets only); what this stops is the LAST read before
    the end standing in as "now" on a later build — a failed read, or no run since. BLOCKED, with
    the date, until a read after the end shows the rate (0, or a top-up, which the reader flags).
    """
    for (name, metric), g in groups.items():
        if metric != "reward_rate_ends_unix" or g.empty:
            continue
        g = g.sort_values("date")
        end = pd.Timestamp(int(float(g["value"].iloc[-1])), unit="s").normalize()
        rate = groups.get((name, "reward_emission_rate_annual"))
        if rate is None or rate.empty or asof <= end:
            continue
        last = rate["date"].max()
        if last <= end:
            _VIEW_BLOCKS[(name, "reward_emission_rate_annual")] = (
                f"BLOCKED — REWARDS ENDED {end.date()}: every RewardVault bucket's "
                f"rewardDurationEndsAt had passed by {asof.date()}, and the rate was last read "
                f"{last.date()}, before the end. It is not carried past its end date. A read after "
                f"the end shows the rate as it stands — 0, or a top-up, which is flagged "
                f"(reward_vault_topup).")


CONSENSUS_HISTORY_POINT = "derived:consensus_formula"
CONSENSUS_MEASURED_POINT = "ultrasound:issuance"


def _ultrasound_issuance(groups: dict, name: str) -> pd.Series:
    """D5-2a: gross issuance per day MEASURED from the supply history route (config.SUPPLY_HISTORY: ultrasound's
    next-day 00:00Z supply point = the end of day D) — d(supply) on consecutive days + the stored burn of the day.
    Post-merge ETH issuance is the net consensus reward (the execution layer issues nothing). Empty when not declared."""
    h = config.SUPPLY_HISTORY.get(name) or {}
    iss = h.get("issuance") or {}
    u, b = groups.get((name, h.get("from_metric"))), groups.get((name, iss.get("burn")))
    if not h or u is None or u.empty or b is None or b.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    ser = lambda g: (g.assign(date=pd.to_datetime(g["date"]).dt.normalize())  # noqa: E731
                     .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index())
    lead = ser(u).shift(-int(h.get("shift_days", 0)), freq="D")
    d_ = lead.diff()[lead.index.to_series().diff().dt.days == 1]
    bb = ser(b)
    days = d_.index.intersection(bb.index)
    return (d_[days] + bb[days]).sort_index()


def _consensus_history_views(groups: dict, asof=None) -> None:
    """ETHEREUM'S CONSENSUS REWARDS BEFORE ETHERSCAN'S FIRST READ (Jake's Phase 1 review 2026-10-12, item 8c: a history
    route for validator yields). Eth2Staking (etherscan ethsupply2) is current-state only, so consensus_rewards_tokens
    starts 2026-09-30 and the validator yield had one quarter. The consensus spec fixes issuance by stake: a year's
    rewards at full participation = 166.32 x sqrt(staked ETH) (config VALIDATOR_YIELD issuance_formula,
    consensus-specs@e321975f). Per day before the first measured day: that ÷ 365 on the day's staked ETH
    (beacon_chain_eth, a year of history), x k = the measured rewards ÷ the formula over the days both exist — so the
    older leg is on the measured basis (participation and penalties folded into k). Labelled with k and its days;
    declared as a handover. No measured overlap: nothing derived. D5-2a: where ultrasound's supply history covers a day
    before the first Etherscan read, that day is MEASURED (_ultrasound_issuance) and the formula is used only before
    ultrasound's first day."""
    spec = config.VALIDATOR_YIELD.get("Ethereum") or {}
    f = spec.get("issuance_formula")
    cm = spec.get("consensus_metric")
    if not f or not cm or "Ethereum" not in {p["name"] for p in scoped_projects()}:
        return
    meas, stake = groups.get(("Ethereum", cm)), groups.get(("Ethereum", spec["stake_metric"]))
    if meas is None or meas.empty or stake is None or stake.empty:
        return
    s = lambda g: (g.assign(date=pd.to_datetime(g["date"]).dt.normalize())  # noqa: E731
                   .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
    from fetch.validate import _span_days                  # noqa: PLC0415
    m, st = s(meas), s(stake)
    formula = (float(f["coefficient"]) * st.clip(lower=0) ** 0.5) / float(GLOBALS["days_per_year"])
    # EACH MEASURED ROW AGAINST THE FORMULA OVER ITS OWN SPAN (a delta dated d with [span=4d] holds four days' rewards)
    spans = (meas.assign(date=pd.to_datetime(meas["date"]).dt.normalize()).drop_duplicates("date", keep="last")
             .set_index("date")["source"].astype(str).map(_span_days))
    num = den = 0.0
    used = []
    for d, v in m.items():
        days = pd.date_range(end=d, periods=int(spans.get(d, 1)), freq="D")
        if all(x in formula.index for x in days):
            num, den = num + float(v), den + float(formula[days].sum())
            used.append(d)
    if len(used) < 3 or den <= 0:
        return
    k = num / den
    both = pd.DatetimeIndex(used)
    # D5-2a: MEASURED BEFORE MODELLED — ultrasound's issuance (d supply + burn) on every day it covers before the first
    # Etherscan read; the formula only on the days before that (never interleaved: a day neither measures stays empty)
    meas_hist = _ultrasound_issuance(groups, "Ethereum")
    meas_hist = meas_hist[meas_hist.index < m.index.min()]
    older = formula[formula.index < m.index.min()]
    if not meas_hist.empty:
        older = older[older.index < meas_hist.index.min()]
    # G11 B6 (Jake's 10-21 run: a hole 07-23..09-28 — the leg stopped at the start of Q0, the measured series starts
    # 09-29/30): the MODELLED leg now runs to the day before the first measured read, so every day to the measured
    # series is covered. (It was held out of Q0 since 2026-10-12 — Phase 1 follow-up item 2 — which left the hole once
    # Q0 moved past the measured start.) The days inside the headline window carry their own label, so a Q0 cell that
    # sums them reads MODELLED in part.
    if older.empty and meas_hist.empty:
        return
    q0_from = (pd.Timestamp(asof) - pd.Timedelta(days=int(GLOBALS["period_days"]) - 1)) if asof is not None else None
    base = (f"{CONSENSUS_HISTORY_POINT}[MODELLED — excluded from scoring: {f['coefficient']} x sqrt(staked ETH) / 365 "
            f"x k={k:.4f}; k = measured "
            f"Eth2Staking ÷ formula over {len(both)} reading(s) {both.min().date()}..{both.max().date()}, each over "
            f"its own span")
    src = [base + ("; INSIDE THE HEADLINE WINDOW — fills the days before the first measured read (G11 B6)]"
                   if q0_from is not None and d >= q0_from else "]") for d in older.index]
    view = pd.DataFrame({"date": older.index, "project": "Ethereum", "metric": cm, "value": (older * k).values,
                         "tier": 2, "source": src})
    mview = pd.DataFrame({"date": meas_hist.index, "project": "Ethereum", "metric": cm, "value": meas_hist.values,
                          "tier": 3, "source": f"{CONSENSUS_MEASURED_POINT}[MEASURED: d(ultrasound supply-over-time, "
                                               f"next-day 00:00Z) + gross_burn_tokens — SPEC D5-2a]"})
    groups[("Ethereum", cm)] = pd.concat([_as_stored(view, meas.columns), _as_stored(mview, meas.columns), meas],
                                         ignore_index=True).sort_values("date")


PRICE_HISTORY_POINT = "defillama_coins:history"


PRICE_TIER_PREFIX = "price_history:"


def _price_tier_views(groups: dict) -> None:
    """ONE PRICE SERIES (G2 addendum A1, Jake's run on e2250e5: 11 headline cells — A3 buyback % of supply, A2 free
    float ÷ ARR, A2 customer revenue ÷ emissions — read "price_usd (q0): measuring_point_changed"). The G2.1 tier
    (fetch/price_history.py) wrote its rows as `price_history:coins.llama.fi[coingecko:<id>]`, a measuring point outside
    price_usd's declared handover (defillama_coins:history -> coingecko), and filled ANY day without a price — days inside
    CoinGecko's own range too, interleaved with it. Read time, no tolerance and no allowlist: a tier row dated BEFORE
    CoinGecko's first stored day is the declared older leg (relabelled `defillama_coins:history[...]`, the same
    CoinGecko price relayed by DefiLlama at 00:00 UTC); a tier row inside CoinGecko's range is not shown (CoinGecko's
    own series holds that range; its missing day stays a gap). orphan_cleanup.sql CV does the same in the store."""
    for (name, metric), g in list(groups.items()):
        if metric != "price_usd" or g.empty:
            continue
        src = g["source"].astype(str)
        tier = src.str.startswith(PRICE_TIER_PREFIX)
        if not tier.any():
            continue
        cg = g[src.map(_measuring_point) == "coingecko"]
        first = pd.to_datetime(cg["date"]).min() if not cg.empty else None
        dates = pd.to_datetime(g["date"])
        keep_tier = tier & (dates < first) if first is not None else tier
        # "price_history:coins.llama.fi[coingecko:<id>]" -> "defillama_coins:history[coins.llama.fi coingecko:<id>]",
        # what fetch/price_history.py writes from now on (one bracket: the annotation stripper reads one level)
        inner = lambda x: x[len(PRICE_TIER_PREFIX):].replace("[", " ").replace("]", "")  # noqa: E731
        relabelled = g["source"].where(~keep_tier, [f"{PRICE_HISTORY_POINT}[{inner(x)}]" if t else x
                                                    for x, t in zip(src, keep_tier)])
        groups[(name, metric)] = g.assign(source=relabelled)[~tier | keep_tier].reset_index(drop=True)


def _price_history_views(groups: dict) -> None:
    """Q-4 PRICES (Jake's Phase 1 review 2026-10-12, item 8b). CoinGecko's free market_chart serves 365 days; the fifth
    quarter needs 455. DefiLlama's coins chart (price_usd_llama, fetch/xref — asked over the whole window once) is the
    same market price: its days BEFORE CoinGecko's first stored price become price_usd's older leg, source
    `defillama_coins:history[<its key>]` — a declared handover (config: price_usd defillama_coins:history -> coingecko,
    composition none), so the series is one. Never a day CoinGecko has; only where price_usd is CoinGecko's."""
    for p in scoped_projects():
        name = p["name"]
        px, ll = groups.get((name, "price_usd")), groups.get((name, "price_usd_llama"))
        if px is None or px.empty or ll is None or ll.empty:
            continue
        decl = config.declared_handover(name, "price_usd") or {}
        if tuple(decl.get("ordered_points") or ()) != (PRICE_HISTORY_POINT, "coingecko"):
            continue
        if not set(px["source"].astype(str).map(_measuring_point)) <= {"coingecko", PRICE_HISTORY_POINT}:
            continue
        first = pd.to_datetime(px["date"]).min()
        older = ll[pd.to_datetime(ll["date"]) < first]
        if older.empty:
            continue
        older = older.assign(metric="price_usd",
                             source=[f"{PRICE_HISTORY_POINT}[{str(sv).split(':', 1)[-1]}]" for sv in older["source"]])
        groups[(name, "price_usd")] = pd.concat([older[px.columns.intersection(older.columns)], px],
                                                ignore_index=True).sort_values("date")


def _monthly_leg_views(groups: dict) -> None:
    """A month a monthly handover leg covers keeps ONLY that leg's row (config.handover_monthly_leg).

    GEODNET (2026-09-29): Dune 8683175's monthly row is the whole month on Polygon AND Solana. A
    later leg's daily row dated in the same month is the same burn again — and the Polygon-only
    middle leg is a narrower count of it. Dropped at read time, never deleted. Runs FIRST, so the
    relabelled buyback copies the corrected series.
    """
    for (name, metric), g in list(groups.items()):
        legs = config.handover_monthly_leg(name, metric)
        if not legs or g.empty:
            continue
        pts = g["source"].astype(str).map(_measuring_point)
        # MONTHLY ROWS NEVER SHARE DATES WITH THE DAILY SERIES (Jake, 2026-10-05, generalised). A
        # monthly row whose month starts on or after the daily series' first day is the CROSS-CHECK
        # (config.monthly_crosscheck_metric), not the series: the daily rows hold that month.
        cross = config.monthly_crosscheck_metric(name, metric)
        daily = g[pts != legs[0]]
        if cross and not daily.empty:
            # ONE DAY OF SLACK: the collision itself hides the 1st, so a series whose first stored
            # day is the 2nd covers the month from its 1st.
            first = pd.Timestamp(daily["date"].min()).normalize() - pd.Timedelta(days=1)
            mv = (pts == legs[0]) & (g["date"] >= first)
            if mv.any():
                held = groups.get((name, cross))
                moved = g[mv].assign(metric=cross)
                if held is not None and not held.empty:
                    moved = moved[~moved["date"].isin(set(held["date"]))]
                    moved = pd.concat([held, moved], ignore_index=True)
                groups[(name, cross)] = moved.sort_values("date")
                g, pts = g[~mv], pts[~mv]
                groups[(name, metric)] = g
        per = g["date"].dt.to_period("M")
        # ...UNLESS THE FULL LIVE LEG HOLDS EVERY DAY OF THE MONTH (archive_backfill.py,
        # 2026-09-29): then the daily rows are the same burn at daily resolution, and they win.
        full = pts == legs[1]
        whole = {m for m, n in g.loc[full].groupby(per[full])["date"].nunique().items()
                 if n >= m.days_in_month}
        months = set(per[pts == legs[0]]) - whole
        keep = (((pts == legs[0]) & ~per.isin(whole))
                | (~(pts == legs[0]) & ~per.isin(months)))
        if not keep.all():
            groups[(name, metric)] = g[keep]


def _measured_emissions_views(groups: dict) -> None:
    """SUPPLIER emissions measured from the project's own cumulatives (config issuance_schedule
    `emissions_measured_from`). Aethir (Jake's labels, 2026-10-01): emissions_tokens = the day-on-day
    rise of checker-node rewards (base + bonus + airdrop) PLUS edge rewards (earnings + stipend),
    over consecutive days on which EVERY stock was read; the row is dated the later day and says its
    span when it is longer than one. A stock that FALLS leaves that day out (a rebase is not a
    negative emission). From the first measured day the schedule's emissions rows give way — never
    deleted — so the series has the declared handover and no overlap; before it they stand.
    gross_issuance_tokens stays on the schedule. No measured day: the schedule's rows stand."""
    for p in scoped_projects():
        name = p["name"]
        # A SCHEDULE THAT NO LONGER ISSUES (Aethir, Jake 2026-10-01): gross_issuance_tokens it wrote
        # before is not shown beside the declared zero — the stored rows go by SQL BM.
        if (p.get("issuance_schedule") or {}).get("emissions_only"):
            g = groups.get((name, "gross_issuance_tokens"))
            if g is not None and not g.empty:
                src = g["source"].astype(str)
                groups[(name, "gross_issuance_tokens")] = g[~(src.str.startswith("schedule:config")
                                                            & ~src.str.endswith(":declared"))]
        spec = (p.get("issuance_schedule") or {}).get("emissions_measured_from")
        if not spec:
            continue
        stocks = []
        for m in spec["stocks"]:
            g = groups.get((name, m))
            if g is None or g.empty:
                stocks = []
                break
            stocks.append(g.sort_values("date").drop_duplicates("date", keep="last")
                          .set_index("date")["value"].astype(float))
        if not stocks:
            continue
        common = sorted(set.intersection(*(set(s_.index) for s_ in stocks)))
        adds = [_added_component(groups, name, a) for a in spec.get("adds") or ()]
        released = [_released_component(groups, name, a) for a in spec.get("adds") or ()]
        rows, earned_rows = [], []
        # THE BREAKDOWN (Jake, 2026-10-05, E): each component's own daily series beside the sum, so
        # A2's emissions can be reconciled — checker, edge, compute earned, compute released.
        part_of = spec.get("component_metrics") or {}
        parts: dict = {}

        def part(metric, day, v, src_):
            if metric and v is not None:
                parts.setdefault(metric, []).append({"date": day, "project": name, "metric": metric,
                                                     "value": float(v), "source": src_, "tier": 3})
        for prev, day in zip(common, common[1:]):
            deltas = [s_[day] - s_[prev] for s_ in stocks]
            if any(d < 0 for d in deltas):
                continue
            span = (pd.Timestamp(day) - pd.Timestamp(prev)).days
            src = spec["source"] + (f"[span={span}d]" if span > 1 else "")
            for m, dv in zip(spec["stocks"], deltas):
                part(part_of.get(m), day, dv, src)
            extra, missing, rel_extra, rel_missing = 0.0, [], 0.0, []
            for a, (stock, daily), rel in zip(spec.get("adds") or (), adds, released):
                v = _component_over(stock, daily, prev, day)
                if v is None:
                    missing.append(a["what"])
                else:
                    extra += v
                if rel is None:                          # no locked split: released = earned
                    rv = v
                else:
                    rv = _component_over(rel, {}, prev, day)
                part(a.get("earned_metric"), day, v, src)
                part(a.get("released_metric"), day, rv, src)
                if rv is None:
                    rel_missing.append(a["what"] + (" released (total - locked)" if v is not None else ""))
                else:
                    rel_extra += rv
            esrc = src + (config.mark_source("", "PARTIAL") + f"[{', '.join(missing)} not covered]" if missing else "")
            earned_rows.append({"date": day, "project": name, "metric": "emissions_earned_tokens",
                                "value": float(sum(deltas)) + extra, "source": esrc + "[EARNED: accrued, vesting — a "
                                                                                       "commitment]", "tier": 3})
            if spec.get("released"):
                rsrc = src + f"[{spec['released']}]"
                if rel_missing:
                    rsrc = config.mark_source(rsrc, "PARTIAL") + f"[{', '.join(rel_missing)} not covered]"
                rows.append({"date": day, "project": name, "metric": "emissions_tokens",
                             "value": float(sum(deltas)) + rel_extra, "source": rsrc, "tier": 3})
            else:
                rows.append({"date": day, "project": name, "metric": "emissions_tokens",
                             "value": float(sum(deltas)) + extra, "source": esrc, "tier": 3})
        if not rows:
            continue
        meas = pd.DataFrame(rows)
        if spec.get("released"):
            # RELEASED vs EARNED (Jake, 2026-10-02): emissions_tokens — supply trajectory and free-float
            # dilution — is what REACHED holders; the earned figure is a commitment, kept beside it
            groups[(name, "emissions_released_tokens")] = _as_stored(meas.assign(metric="emissions_released_tokens"),
                                                                     meas.columns)
            groups[(name, "emissions_earned_tokens")] = _as_stored(pd.DataFrame(earned_rows), meas.columns)
        for m, prt in parts.items():
            groups[(name, m)] = _as_stored(pd.DataFrame(prt), meas.columns)
        _release_view(groups, name, spec, meas, common)
        sched = groups.get((name, "emissions_tokens"))
        if sched is not None and not sched.empty:
            sched = sched[sched["date"] < meas["date"].min()]
            cols = sched.columns
            groups[(name, "emissions_tokens")] = pd.concat([sched, _as_stored(meas, cols)],
                                                           ignore_index=True).sort_values("date")
        else:
            groups[(name, "emissions_tokens")] = _as_stored(meas, meas.columns)


def _emissions_basis(p: dict, data_by_key: dict) -> str:
    """Which series feeds emissions_tokens now, for a project that measures it from components
    (Jake, 2026-10-05: Aethir's breakdown read n/a while A2 read 4.71% — that 4.71% was the declared
    schedule, 2,874,743 ATH/day of checker BASE rewards, 258.7M per 90 days). Empty elsewhere."""
    sched = p.get("issuance_schedule") or {}
    if not sched.get("emissions_measured_from"):
        return ""
    name = p["name"]
    parts = [(lab, (data_by_key.get(f"{name}|{m}") or {}).get("first_date")) for m, lab in _EMISSION_PARTS]
    have = [(lab, str(d)[:10]) for lab, d in parts if d is not None and not pd.isna(d) and str(d)]
    rate = next((st["tokens_per_day"] for st in sorted(sched.get("steps") or [], key=lambda x: x["from"])[-1:]), None)
    decl = (f"DECLARED SCHEDULE until then: {rate:,.0f} {p['symbol']}/day of checker BASE rewards only "
            f"({rate * 90:,.0f} per 90 days)" if rate else "the declared schedule until then")
    if not have:
        return ("NOT YET MEASURED — the component series start with the SECOND consecutive day on which every "
                "dashboard cumulative (ecosystem checker, cloud host, edge) is read; " + decl)
    first = min(d for _, d in have)
    return (f"MEASURED from {first} (ecosystem checker + cloud host + edge); {decl} before it. Components from: "
            + "; ".join(f"{lab} {d}" for lab, d in have))


_REVENUE_LEG_LABELS = ("payments collected at the fee aggregator (core)", "Direct Requests",
                       "CCIP 1.2/1.5 fees (gross)", "VRF v2.5 payments", "Automation v2.3 premium")


def _revenue_legs(p: dict) -> list:
    """[core, *plus] metrics of a project's customer_revenue_components, in _REVENUE_LEG_LABELS order."""
    spec = p.get("customer_revenue_components") or {}
    return [spec["core"], *spec["plus"]] if spec else []


# Aethir (Jake, 2026-10-06): the ecosystem page's three supplier legs; the compute-rewards add-on is retired
# (cloudHostRewards is the compute providers' leg).
_EMISSION_PARTS = (("emissions_checker_tokens", "checker-node rewards"),
                   ("emissions_cloud_host_tokens", "cloud host (compute provider) rewards"),
                   ("emissions_edge_tokens", "edge rewards"))


def _added_component(groups: dict, name: str, a: dict):
    """(cumulative stock by day, daily rate by day) for a supplier component added to measured
    emissions (Aethir compute rewards, Jake 2026-10-02): the stock as read, and each weekly figure
    spread evenly over the seven days from its label."""
    g = groups.get((name, a["stock"]))
    stock = (g.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
             if g is not None and not g.empty else pd.Series(dtype=float))
    w = groups.get((name, a.get("weekly_flow")))
    daily = {}
    if w is not None and not w.empty:
        for d, v in w.drop_duplicates("date", keep="last")[["date", "value"]].itertuples(index=False):
            for k in range(7):
                daily[pd.Timestamp(d) + pd.Timedelta(days=k)] = float(v) / 7
    return stock, daily


def _released_component(groups: dict, name: str, a: dict):
    """(total - locked) by day for a component with a LOCKED split (Aethir compute rewards: totalRewards -
    totalLockedRewards), or None when the component has no locked stock declared."""
    if not a.get("locked_stock"):
        return None
    g, lk = groups.get((name, a["stock"])), groups.get((name, a["locked_stock"]))
    if g is None or g.empty or lk is None or lk.empty:
        return pd.Series(dtype=float)
    tot = g.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
    lock = lk.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
    both = tot.index.intersection(lock.index)
    return (tot[both] - lock[both]).sort_index()


def _component_over(stock, daily: dict, prev, day) -> float | None:
    """The component's amount over (prev, day]: the stock's rise when both ends were read (a fall
    is no measurement), else the weekly figures' days, else None (not covered)."""
    if prev in stock.index and day in stock.index and stock[day] >= stock[prev]:
        return float(stock[day] - stock[prev])
    days = pd.date_range(pd.Timestamp(prev) + pd.Timedelta(days=1), day)
    if len(days) and all(d in daily for d in days):
        return float(sum(daily[d] for d in days))
    return None


def _release_view(groups: dict, name: str, spec: dict, supplier: pd.DataFrame, common: list) -> None:
    """pool_release_tokens = supplier rewards + the staker schedule's rise over the SAME span (Aethir,
    Jake 2026-10-01: both are releases from pre-minted pools). The staker series is cumulative and
    weekly; each of its steps is added to the supplier row whose span (previous reading, day] holds
    it. Only days with a measured supplier row; no measured staker point at all: nothing."""
    if not spec.get("release_metric"):
        return
    st = groups.get((name, spec["release_adds"]))
    if st is None or st.empty:
        return
    cum = st.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
    steps = cum.diff().dropna()
    steps = steps[steps > 0]
    prev = dict(zip(common[1:], common[:-1]))
    rows = []
    for r in supplier.itertuples(index=False):
        p0 = prev.get(r.date)
        add = float(steps[(steps.index > p0) & (steps.index <= r.date)].sum()) if p0 is not None else 0.0
        rows.append({"date": r.date, "project": name, "metric": spec["release_metric"],
                     "value": float(r.value) + add,
                     "source": spec["release_source"] + (r.source[len(spec["source"]):] if
                                                         r.source.startswith(spec["source"]) else ""),
                     "tier": 3})
    groups[(name, spec["release_metric"])] = _as_stored(pd.DataFrame(rows), supplier.columns)


def _usd_history_views(groups: dict) -> None:
    """A token flow's history BEFORE its live read = a USD series / same-day price (config
    `burn_history`). Hyperliquid, Jake 2026-09-30: the Assistance Fund's buyback IS its burn, so
    gross_burn_tokens before the first live AF delta = DefiLlama holders revenue / HYPE price that
    day. Prepended under its own source — the declared first leg of the series_handover — and
    never overlapping the live read; a day with no same-day price is left out."""
    for p in scoped_projects():
        name = p["name"]
        spec = p.get("burn_history") or {}
        if not spec:
            continue
        usd, px = groups.get((name, spec["usd_metric"])), groups.get((name, "price_usd"))
        if usd is None or usd.empty or px is None or px.empty:
            continue
        live = groups.get((name, spec["metric"]))
        pmap = px.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        pmap = pmap[pmap > 0]
        h = usd.drop_duplicates("date", keep="last")
        if live is not None and not live.empty:
            h = h[h["date"] < live["date"].min()]
        h = h[h["date"].isin(pmap.index)].copy()
        if h.empty:
            continue
        h["value"] = h["value"].astype(float).values / pmap.loc[h["date"]].values
        h["metric"], h["source"] = spec["metric"], spec["source"]
        cols = live.columns if live is not None and not live.empty else usd.columns
        parts = [_as_stored(h, cols)] + ([live] if live is not None and not live.empty else [])
        groups[(name, spec["metric"])] = pd.concat(parts, ignore_index=True).sort_values("date")


def _burn_total_views(groups: dict) -> None:
    """A burn TOTAL summed from its components, per day, at read time (config.burn_total).

    Hyperliquid (Jake, 2026-09-29): total_burn_tokens = gross_burn_tokens (the Assistance Fund)
    + core_burn_tokens (the fall in tokenDetails.totalSupply). A day is in the total only where
    EVERY component has a figure — a day with one leg missing would read as a smaller burn, not
    as a gap. Where no core_burn_tokens row exists for a day, the Core leg is rebuilt from the
    stored stock (core_from_stock): the fall between consecutive readings, dated the later one,
    exactly as the fetch-time differencing would have written it; a rise is not a burn and the
    day is left out. Runs BEFORE _relabel_views, which reads the total as the buyback's origin.
    """
    for p in scoped_projects():
        name = p["name"]
        spec = config.burn_total(name)
        if not spec:
            continue
        per_leg = {}
        for m in spec["components"]:
            g = groups.get((name, m))
            per_leg[m] = ({} if g is None or g.empty else
                          g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).to_dict())
        cfs = spec.get("core_from_stock") or {}
        stock = groups.get((name, cfs.get("stock")))
        if cfs and stock is not None and not stock.empty:
            st = stock.drop_duplicates("date", keep="last").sort_values("date")
            vals, dates = st["value"].astype(float).tolist(), st["date"].tolist()
            leg = per_leg.setdefault(cfs["metric"], {})
            for i in range(1, len(dates)):
                fall = vals[i - 1] - vals[i]
                if dates[i] not in leg and fall >= 0:
                    leg[dates[i]] = fall
        days = sorted(set.intersection(*(set(v) for v in per_leg.values()))) if per_leg else []
        if not days:
            continue
        # OPTIONAL LEGS (G6 decision 0c, Jake 2026-10-19: the HyperEVM gas burn is A1 only): added on the total's own days
        # where held, never cutting a day — and never before the total's first day, where the history leg's ASXN
        # HyperEVM figure already stands
        opt = {}
        for m in spec.get("optional_components") or ():
            g = groups.get((name, m))
            if g is not None and not g.empty:
                opt[m] = g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).to_dict()
        base_vals = [sum(per_leg[m][d] for m in spec["components"]) for d in days]
        # A DECLARED COMPONENT, NOT A NEW MEASURING POINT (Round G8 item 1, Jake 2026-10-20: G7 named the optional leg in
        # the source — "burn_total(gross+core+hyperevm_priority_burn_tokens)" — a third basis that blanked 22 Hyperliquid
        # cells). The source keeps the total's own basis; the component is a bracketed annotation (stripped by
        # config.strip_source_annotations) naming it and its start date (config.DECLARED_COMPONENTS).
        base_src = "derived:burn_total(" + "+".join(spec["components"]) + ")"
        starts = {m: min(o) for m, o in opt.items() if o}
        view = pd.DataFrame({"date": days, "project": name, "metric": spec["metric"],
                             "value": [v + sum(o.get(d, 0.0) for o in opt.values()) for v, d in zip(base_vals, days)],
                             # a day after the component's start that it did not report is SAID, never a silent 0
                             "source": [base_src + "".join(f"[+{m} declared component from {starts[m]:%Y-%m-%d}]"
                                                           if d in o else f"[{m} not read this day — excluded]"
                                                           for m, o in opt.items() if d >= starts[m])
                                        for d in days],
                             "tier": 2})
        a4v = spec.get("a4_view") or {}
        a4_view = (pd.DataFrame({"date": days, "project": name, "metric": a4v["metric"], "value": base_vals,
                                 "source": "derived:burn_total(" + "+".join(spec["components"]) + ")", "tier": 2})
                   if a4v else None)
        # BEFORE THE FIRST COMPLETE DAY, ONE DECLARED LEG STANDS IN (Hyperliquid, Jake 2026-09-30):
        # the Assistance Fund's burn history (its buyback / same-day price) — the Core leg has no
        # history. Only rows from that leg's declared history source, only before the total's first
        # day: a declared series_handover, marked by its own source.
        hl = spec.get("history_leg") or {}
        g0 = groups.get((name, hl.get("component")))
        if hl and g0 is not None and not g0.empty:
            h = g0[(g0["source"].astype(str) == hl["source"]) & (g0["date"] < min(days))]
            # THE FUND'S OWN READS BETWEEN ITS DECLARED HISTORY AND THE TOTAL'S FIRST DAY STAY IN THE LEG (round 3
            # review H1, Jake 2026-10-14): Hyperliquid's leg kept only the DefiLlama rows (to 2026-09-11), so the
            # Fund's spotClearinghouseState reads 2026-09-12..09-28 fell out of the total — a 17-day hole, and the
            # coverage then counted from the Core leg's first day (18 of 90) while Q0 still summed the history:
            # 2,146,959 HYPE annualised over 18 days read -18.25% net supply. Same component, same leg, after the
            # declared rows only.
            if not h.empty:
                h = pd.concat([h, g0[(g0["date"] > h["date"].max()) & (g0["date"] < min(days))]])
            if not h.empty:
                h = h.drop_duplicates("date", keep="last")[["date", "value"]].assign(
                    project=name, metric=spec["metric"], tier=2,
                    source=f"{hl['source']}[{hl['component']} leg only: the other legs are unmeasured "
                           f"before {min(days).date()}]")
                # THE NON-AF LEGS' HISTORY (probes6 2a): each declared extra leg held for the day is added,
                # and the source says which; a day without them keeps the Fund-only label.
                extra = {}
                px = groups.get((name, "price_usd"))
                px = (px.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
                      if px is not None and not px.empty else pd.Series(dtype=float))
                for leg in spec.get("history_extra") or ():
                    leg = leg if isinstance(leg, dict) else {"tokens": leg}
                    ser, label = pd.Series(dtype=float), leg["tokens"]
                    g = groups.get((name, leg.get("usd")))        # probes7: a USD leg, at the same-day price
                    if g is not None and not g.empty:
                        u = g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
                        both = u.index.intersection(px[px > 0].index)
                        ser, label = u[both] / px[both], f"{leg['usd']}/price"
                    g = groups.get((name, leg["tokens"]))         # HYPE as read wins on its days
                    if g is not None and not g.empty:
                        t = g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
                        ser = t.combine_first(ser) if not ser.empty else t
                        label = leg["tokens"] if label == leg["tokens"] else f"{leg['tokens']} | {label}"
                    if not ser.empty:
                        extra[label] = ser
                def with_extra(h, skip=()):
                    vals, srcs = [], []
                    for d, v in zip(h["date"], h["value"].astype(float)):
                        got = [m for m in extra if d in extra[m].index and not m.startswith(tuple(skip))]
                        vals.append(v + sum(float(extra[m][d]) for m in got))
                        srcs.append(f"{hl['source']}[{hl['component']} + {' + '.join(got)} (ASXN): the Core "
                                    f"leg's own read starts {min(days).date()}]" if got else
                                    f"{hl['source']}[{hl['component']} leg only: the other legs are unmeasured "
                                    f"before {min(days).date()}]")
                    return h.assign(value=vals, source=srcs)
                h0 = h
                if extra:
                    h = with_extra(h0)
                view = pd.concat([h[view.columns], view], ignore_index=True).sort_values("date")
                if a4_view is not None:
                    h4 = with_extra(h0, a4v.get("exclude_extra") or ()) if extra else h0
                    a4_view = pd.concat([h4[a4_view.columns], a4_view], ignore_index=True).sort_values("date")
        first = groups.get((name, spec["components"][0]))
        groups[(name, spec["metric"])] = _as_stored(view, first.columns if first is not None else view.columns)
        if a4_view is not None:
            groups[(name, a4v["metric"])] = _as_stored(a4_view, first.columns if first is not None else a4_view.columns)


# (project, flow metric) -> tokens moved out by _one_off_views, which the flow-vs-stock
# reconciliation adds back; and the blocks it raises, re-applied after _VIEW_BLOCKS is cleared.
_ONE_OFF_REMOVED: dict = {}
_ONE_OFF_BLOCKS: dict = {}
_ONE_OFF_UNMATCHED: dict = {}     # (project, release metric) -> one-off tokens no stored fall matched (G11 B1)
UNLOCK_LEGACY_DROPPED: dict = {}  # (project, release metric, leg) -> legacy rows past config.UNLOCK_LEGACY_CUT (P2a D2)


def _one_off_views(groups: dict) -> None:
    """A declared one-off never enters a rate (Uniswap's 100M retroactive burn, Jake 2026-09-29).

    For each `one_off_flows` entry: the ONE differenced row of the flow inside `window` whose value
    is at least `tokens` has `tokens` moved to one_off_burn_tokens, dated as that row. What remains
    on the row is that interval's ongoing burn. No such row: the one-off predates the differenced
    series, nothing moves. Two or more: BLOCKED — a second event that size is a finding.
    """
    _ONE_OFF_REMOVED.clear()
    _ONE_OFF_BLOCKS.clear()
    for p in scoped_projects():
        name = p["name"]
        for spec in p.get("one_off_flows") or ():
            key = (name, spec["metric"])
            g = groups.get(key)
            if g is None or g.empty:
                continue
            lo, hi = (pd.Timestamp(x) for x in spec["window"])
            delta = g["source"].astype(str).str.contains(":delta", na=False)
            hit = g[delta & (g["date"] >= lo) & (g["date"] <= hi) & (g["value"].astype(float) >= spec["tokens"])]
            if hit.empty:
                continue
            if len(hit) > 1:
                _ONE_OFF_BLOCKS[key] = (
                    f"BLOCKED — {len(hit)} differenced rows in {lo.date()}..{hi.date()} are each at "
                    f"least the declared one-off ({spec['tokens']:,.0f}: {spec['what']}), on "
                    f"{', '.join(str(d.date()) for d in hit['date'])}. One is the one-off; a second "
                    f"event that size is a finding, not a rate.")
                continue
            i = hit.index[0]
            g = g.copy()
            g.loc[i, "value"] = float(g.loc[i, "value"]) - float(spec["tokens"])
            g.loc[i, "source"] = config.mark_source(str(g.loc[i, "source"]), "one-off-removed")
            groups[key] = g
            # THE STORED TRANSACTION DATES THE ONE-OFF (Round G8 item 4c): `--seed uni_retro_burn` stores the event
            # from the tx itself on its block's day; where that row is held it stands, else the differenced row's day
            held = groups.get((name, "one_off_burn_tokens"))
            if held is None or held.empty or not held["source"].astype(str).str.startswith("tx:").any():
                one = g.loc[[i]].copy()
                one["metric"] = "one_off_burn_tokens"
                one["value"] = float(spec["tokens"])
                one["source"] = f"one_off:{spec['what']} ({spec['source_url']})"
                groups[(name, "one_off_burn_tokens")] = _as_stored(one, g.columns)
            _ONE_OFF_REMOVED[key] = _ONE_OFF_REMOVED.get(key, 0.0) + float(spec["tokens"])


def _relabel_views(groups: dict) -> None:
    """A buyback that IS the burn is read as a VIEW of the burn series, not from stored copies.

    ** HYPERLIQUID'S BUYBACK AND BURN DISAGREED ON ONE FLOW. Fixed 2026-09-28. ** Run
    20260928T090446Z: gross_burn_tokens Q0 379,892.53, actual_buyback_tokens Q0 147,958.35 — the
    Assistance Fund buyback and the burn are one event. fetch._derive_buyback re-labels only the
    burn rows produced IN THAT RUN (and prices the usd twin from those), so the buyback series
    begins at the first run after the route existed while the burn series also holds every older
    row (including the rebuilt deltas). Same flow, different start dates.
    Here the whole origin series stands in for the buyback on every read — marked as-buyback (and
    PARTIAL on a split route) exactly as the stored copies are — and actual_buyback_usd is that
    series x the same-day price. Skipped where a Dune query SOURCES the column (GEODNET) or a
    manual override is present: a measurement is never replaced by a view.
    """
    for p in scoped_projects():
        name = p["name"]
        origin = config.relabelled_from(name, "actual_buyback_tokens")
        if not origin or config.dune_query_declared(name, "actual_buyback_tokens"):
            continue
        og = groups.get((name, origin))
        held = groups.get((name, "actual_buyback_tokens"))
        if og is None or og.empty:
            continue
        if held is not None and "is_manual" in held and held["is_manual"].fillna(False).astype(bool).any():
            continue
        # NOR A MEASURED SERIES (2026-09-28): Sky's buyback is now a Transfer scan of every SKY
        # bought; the split route's burn-leg relabel must not overwrite it.
        # (Rows from a Dune query no longer declared for the column — GEODNET's, removed the same
        # day — are superseded, not a measurement.)
        if held is not None and not held.empty:
            src = held["source"].astype(str)
            measured = ~src.str.contains("as-buyback", na=False) & ~src.str.startswith("dune:")
            if measured.any():
                continue
        split = config.buyback_route(name).get("route") == "split"
        mark = (lambda x: config.mark_source(config.mark_source(x, "as-buyback"), "PARTIAL")) if split \
            else (lambda x: config.mark_source(x, "as-buyback"))
        tok = og.copy()
        tok["metric"] = "actual_buyback_tokens"
        tok["source"] = tok["source"].astype(str).map(mark)
        tok = _as_stored(tok, og.columns)
        # THE HISTORY BEFORE THE LIVE READ (Hyperliquid, Jake 2026-09-29): DefiLlama's holders
        # revenue IS the Assistance Fund's buyback spend, with full history. Days BEFORE the live
        # series' first day are prepended as usd / same-day price, under their own source — the
        # declared first leg of the series_handover. The live read stays primary from its first day.
        bh = (p.get("buyback_history") or {})
        hu, px_h = groups.get((name, bh.get("usd_metric"))), groups.get((name, "price_usd"))
        if bh and hu is not None and not hu.empty and px_h is not None and not px_h.empty:
            pmap = px_h.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
            h = hu[hu["date"] < og["date"].min()].copy()
            h = h[h["date"].isin(pmap.index[pmap > 0])]
            if not h.empty:
                h["value"] = h["value"].astype(float).values / pmap.loc[h["date"]].values
                h["metric"], h["source"] = "actual_buyback_tokens", bh["source"]
                tok = pd.concat([_as_stored(h, og.columns), tok], ignore_index=True).sort_values("date")
        groups[(name, "actual_buyback_tokens")] = tok
        if config.dune_query_declared(name, "actual_buyback_usd"):
            continue
        px = groups.get((name, "price_usd"))
        if px is None or px.empty:
            continue
        price_on = px.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        # A MONTHLY ROW IS PRICED OVER ITS MONTH (2026-09-29). A Dune monthly total dated the 1st
        # was dropped whenever that one day had no price, which left A3 on the live days while
        # A4 read the whole history. Such a row is valued at the mean price across the month it
        # covers — never at today's. A daily row still needs its own day's price.
        def row_price(r):
            if r["date"] in price_on.index:
                return price_on[r["date"]]
            if str(r["source"]).startswith("dune:"):
                m = price_on[(price_on.index >= r["date"]) & (price_on.index < r["date"] + pd.offsets.MonthBegin(1))]
                return float(m.mean()) if not m.empty else None
            return None
        prices = tok.apply(row_price, axis=1) if not tok.empty else pd.Series(dtype=float)
        usd = tok[prices.notna()].copy()
        usd["value"] = usd["value"].astype(float) * prices[prices.notna()].astype(float)
        usd["metric"] = "actual_buyback_usd"
        usd["source"] = "derived:tokens*price"
        if not usd.empty:
            groups[(name, "actual_buyback_usd")] = _as_stored(usd, og.columns)


def _near_buyback_views(groups: dict) -> None:
    """NEAR'S BUYBACK = THE DAY'S CHANGE IN THE THREE REVENUE WALLETS' COMBINED CLOSE (Jake, 2026-10-07 14:17).
    Internal moves cancel in the sum and its change is what was bought and held. All-time, two native outflows left the
    three — both bridged off NEAR, not retired (config.NEAR_REVENUE_OUTFLOWS, round 3 B5): the day's fall nets them, and
    the source names them. Stored transaction-scan rows of actual_buyback_tokens are superseded (the view replaces the
    group); actual_buyback_usd = tokens x the same-day price. A negative day (gas, or a move out of the three) is kept
    as measured and named in its source."""
    for p in scoped_projects():
        bh = (p.get("nearblocks") or {}).get("balance_history")
        if not bh:
            continue
        name, g = p["name"], groups.get((p["name"], bh["metric"]))
        if g is None or g.empty:
            continue
        s = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
             .set_index("date")["value"].astype(float).sort_index())
        d = s.diff()
        d = d[(d.index.to_series().diff() == pd.Timedelta(days=1)).values & d.notna().values]
        if d.empty:
            continue
        named: dict = {}
        for o in config.NEAR_REVENUE_OUTFLOWS:          # one entry per transfer: a day's transfers summed (run Y2)
            k = pd.Timestamp(o["date"])
            named[k] = {**o, "tokens": named[k]["tokens"] + o["tokens"]} if k in named else dict(o)
        # a balance read once a day sees a move on its day or the next read
        named.update({k + pd.Timedelta(days=1): o for k, o in list(named.items()) if k + pd.Timedelta(days=1) not in named})

        def src(day, v):
            o = named.get(day) if v < 0 else None
            tag = (f"[nets {o['tokens']:,.0f} NEAR {o['class']} ({o['wallet']} -> {o['to'][:12]}…)]" if o else
                   "[NEGATIVE: a move out of the three]" if v < 0 else "")
            return f"derived:d({bh['metric']})" + tag
        tok = pd.DataFrame({"date": d.index, "project": name, "metric": bh["flow"], "value": d.values, "tier": 1,
                            "source": [src(day, v) for day, v in zip(d.index, d.values)]})
        groups[(name, bh["flow"])] = _as_stored(tok, g.columns)
        px = groups.get((name, "price_usd"))
        if px is None or px.empty:
            continue
        pmap = (px.assign(date=pd.to_datetime(px["date"]).dt.normalize()).drop_duplicates("date", keep="last")
                .set_index("date")["value"].astype(float))
        both = d[d.index.isin(pmap.index)]
        if both.empty:
            continue
        usd = pd.DataFrame({"date": both.index, "project": name, "metric": "actual_buyback_usd",
                            "value": (both * pmap.reindex(both.index)).values, "tier": 1,
                            "source": "derived:tokens*price"})
        groups[(name, "actual_buyback_usd")] = _as_stored(usd, g.columns)


def _aethir_arr_view(groups: dict) -> None:
    """ARR FOR RATIOS (Jake, 2026-10-10): `arr_view.metric` = the last `months` complete months of `monthly` x `times`,
    one row per month that completes a run of `months` consecutive stored months, dated the day after it ends. The
    dashboard tile goes to `tile_metric`; rows stored under arr_usd before the decision (the tile, aethir_page) are
    carried there at read time, so no stored row is rewritten."""
    for p in scoped_projects():
        spec = p.get("arr_view")
        if not spec:
            continue
        name, metric, tile = p["name"], spec["metric"], spec["tile_metric"]
        old = groups.pop((name, metric), None)
        if old is not None and not old.empty:
            have = groups.get((name, tile))
            old = old.assign(metric=tile)
            if have is not None and not have.empty:
                old = old[~pd.to_datetime(old["date"]).dt.normalize().isin(
                    pd.to_datetime(have["date"]).dt.normalize())]
                old = pd.concat([have, old], ignore_index=True)
            groups[(name, tile)] = old
        g = groups.get((name, spec["monthly"]))
        if g is None or g.empty:
            continue
        m = (g.assign(month=pd.to_datetime(g["date"]).dt.to_period("M")).drop_duplicates("month", keep="last")
             .set_index("month")["value"].astype(float).sort_index())
        n, rows = int(spec["months"]), []
        for end in m.index:
            run = [end - i for i in range(n)]
            if all(x in m.index for x in run):
                rows.append({"date": (end + 1).start_time, "project": name, "metric": metric,
                             "value": float(sum(m[x] for x in run)) * float(spec["times"]), "tier": 3,
                             "source": f"derived:{spec['monthly']} last {n} complete months x {spec['times']:g}"})
        if rows:
            groups[(name, metric)] = _as_stored(pd.DataFrame(rows), g.columns)


def _epoch_paid_views(groups: dict, asof: pd.Timestamp) -> None:
    """config.EPOCH_PAID_AT (round 3 B4): every row of the declared weekly series re-dated by `days` — from the epoch
    it was read in to the flip that paid it — and a row whose paying flip is after the as-of left out (not paid yet).
    Read-time: the stored rows are untouched, so the restatement covers the whole history and every later read alike."""
    for name, spec in config.EPOCH_PAID_AT.items():
        for m in spec["metrics"]:
            g = groups.get((name, m))
            if g is None or g.empty:
                continue
            d = pd.to_datetime(g["date"]) + pd.Timedelta(days=int(spec["days"]))
            keep = d.dt.normalize() <= pd.Timestamp(asof).normalize()
            # the source is left as stored: it names WHAT was read (its measuring point), and the re-dating is declared
            # in config.EPOCH_PAID_AT and on METRICS.md
            groups[(name, m)] = g.assign(date=d.values)[keep.values]


def _history_prepend_views(groups: dict) -> None:
    """config.HISTORY_PREPEND (fix round 3): the days before a series' first stored row, read from another stored series
    on the same basis (a declared handover). Read-time only; a stored row always stands."""
    for (name, metric), h in config.HISTORY_PREPEND.items():
        if name not in {p["name"] for p in scoped_projects()}:
            continue
        live, src = groups.get((name, metric)), groups.get((name, h["from"]))
        if src is None or src.empty:
            continue
        cols = (live if live is not None and not live.empty else src).columns
        first = pd.to_datetime(live["date"]).min().normalize() if live is not None and not live.empty else None
        if h.get("spread") == "monthly":
            src = _month_spread(src)
        lead = src.assign(date=pd.to_datetime(src["date"]).dt.normalize())
        if first is not None:
            lead = lead[lead["date"] < first]
        if lead.empty:
            continue
        lead = lead.assign(metric=metric)
        groups[(name, metric)] = (pd.concat([_as_stored(lead, cols), live], ignore_index=True)
                                  if live is not None and not live.empty else _as_stored(lead, cols))


def _retired_series_views(groups: dict) -> None:
    """config.RETIRED_SERIES (D5-3c): a series shown nowhere as a figure — any row still stored (before its clean-up
    runs) is dropped at read time, and every cell that reads it says why (BLOCKED, kept through the build)."""
    for (name, metric), why in config.RETIRED_SERIES.items():
        groups.pop((name, metric), None)
        _EARLY_BLOCKS[(name, metric)] = why


MONTH_SPREAD_TAG = "spread evenly over each month's days"


def _month_spread(src: pd.DataFrame) -> pd.DataFrame:
    """D5-1a: each monthly row (one complete month, dated in it) as its value ÷ the month's days on every day of the
    month, its source tagged MONTH_SPREAD_TAG — daily rows (_per_day reads them as one day each)."""
    rows = []
    for r in src.assign(date=pd.to_datetime(src["date"]).dt.normalize()).drop_duplicates("date", keep="last") \
            .to_dict("records"):
        days = pd.date_range(r["date"].to_period("M").start_time, r["date"].to_period("M").end_time.normalize())
        rows += [{**r, "date": d, "value": float(r["value"]) / len(days),
                  "source": f"{r['source']}[{MONTH_SPREAD_TAG} — before the weekly series (SPEC D5-1a)]"} for d in days]
    return pd.DataFrame(rows, columns=src.columns)


def _hl_model_views(groups: dict) -> None:
    """THE MODELLED REWARDS HISTORY (config.HL_REWARD_MODEL; fix round 3, B1). rate = k ÷ sqrt(stake); with the
    reference wallet's gross rate a(D): rewards(D) = k² ÷ (365 · a(D)), stake(D) = (k ÷ a(D))². k = the median of
    a · sqrt(S) over the days a stored validatorSummaries read S and a both exist. Prepended to the rewards and stake
    series before their first stored rows (MODELLED source: excluded from any scoring), and the reserve modelled
    backward from its first stored read as its own series."""
    for p in scoped_projects():
        name = p["name"]
        spec = config.HL_REWARD_MODEL.get(name)
        if not spec:
            continue
        a = _daily(groups.get((name, spec["ref_yield"])))
        stake = _daily(groups.get((name, spec["stake"])))
        if a.empty or stake.empty:
            continue
        a = a[a > 0]
        both = a.index.intersection(stake.index)
        if both.empty:
            _VIEW_BLOCKS[(name, f"{spec['emissions']}:modelled_history")] = (
                "not modelled: no day holds both the reference yield and a stored stake read to calibrate k")
            continue
        k = float((a[both] * stake[both] ** 0.5).median())
        rew, stk = k * k / (365.0 * a), (k / a) ** 2
        cols = next(g.columns for (n_, _m), g in groups.items() if n_ == name and g is not None and not g.empty)
        tag = f"{spec['source']}[k={k:.3f} on {len(both)} day(s)]"
        # the stake model whole, under its own name; locked_tokens' MODELLED leg is read back from it (G13 1a: the
        # back-fill's dependency is the code's own — locked_tokens_modelled is an INPUT of locked_tokens)
        groups[(name, spec["stake_modelled"])] = _as_stored(pd.DataFrame(
            {"date": stk.index, "project": name, "metric": spec["stake_modelled"], "value": stk.values, "tier": 1,
             "source": tag}), cols)
        stk = _daily(groups[(name, spec["stake_modelled"])])
        for metric, model in ((spec["emissions"], rew), (spec["stake"], stk)):
            g = groups.get((name, metric))
            first = pd.to_datetime(g["date"]).min().normalize() if g is not None and not g.empty else None
            lead = model[model.index < first] if first is not None else model
            if lead.empty:
                continue
            view = _as_stored(pd.DataFrame({"date": lead.index, "project": name, "metric": metric,
                                            "value": lead.values, "tier": 1, "source": tag}), cols)
            groups[(name, metric)] = pd.concat([view, g], ignore_index=True) if first is not None else view

        fe = _daily(groups.get((name, spec["future"])))
        if fe.empty:
            continue
        first_fe, fe0 = fe.index.min(), float(fe.iloc[0])
        em = _daily(groups.get((name, spec["emissions"])))
        days = em[em.index <= first_fe].sort_index(ascending=False)
        # FE at the close of D = FE(first read) + the rewards paid after D up to the first read
        vals, run = {}, 0.0
        for d, v in days.items():
            vals[d - pd.Timedelta(days=1)] = fe0 + run + float(v)
            run += float(v)
        if vals:
            s_ = pd.Series(vals).sort_index()
            groups[(name, spec["future_modelled"])] = _as_stored(pd.DataFrame(
                {"date": s_.index, "project": name, "metric": spec["future_modelled"], "value": s_.values, "tier": 1,
                 "source": tag}), cols)


def _same_basis_views(groups: dict) -> None:
    """ONE BASIS, SEVERAL ROUTES (config.SAME_BASIS; fix round 2, R10): each member route's rows are relabelled to the
    canonical measuring point, the original route kept in brackets — never a change of measuring point."""
    from fetch.base import _measuring_point          # noqa: PLC0415
    for (name, metric), spec in config.SAME_BASIS.items():
        g = groups.get((name, metric))
        if g is None or g.empty:
            continue
        canon = spec["canonical"]
        members = tuple(spec["members"])

        def relabel(src: str) -> str:
            mp = _measuring_point(str(src))
            if mp == canon or not any(mp == m or mp.startswith(m) for m in members):
                return src
            return f"{canon}[via {config.strip_source_annotations(str(src))} — same basis]"
        groups[(name, metric)] = g.assign(source=g["source"].astype(str).map(relabel))


def _declared_zero_views(groups: dict, asof) -> None:
    """0, NOT n/a, AFTER A PROGRAMME ENDED (Jake's alignment round, SPEC.md rule 3, B10): each config.DECLARED_ZERO
    series gets a 0 on every day from its `since` to the build date that has no stored row, source
    `declared:zero[<why>]`. A stored row always stands; nothing before `since` is touched."""
    end = pd.Timestamp(asof).normalize() if asof is not None else pd.Timestamp.now().normalize()
    for (name, metric), spec in config.DECLARED_ZERO.items():
        if name not in {p["name"] for p in scoped_projects()}:
            continue
        g = groups.get((name, metric))
        have = set(pd.to_datetime(g["date"]).dt.normalize()) if g is not None and not g.empty else set()
        lo = max(pd.Timestamp(spec["since"]), end - pd.Timedelta(days=int(config.TRAJECTORY["history_days"])))
        hi = end
        if spec.get("until_first_row"):              # G10 3b: 0 before the first stored row, where config records the
            if not have or pd.Timestamp(spec["record_date"]) < min(have):   # programme began on or after it
                continue
            hi = min(have) - pd.Timedelta(days=1)
        days = [d for d in pd.date_range(lo, hi) if d not in have]
        if not days:
            continue
        cols = g.columns if g is not None and not g.empty else _view_cols(groups, name)
        z = _as_stored(pd.DataFrame({"date": days, "project": name, "metric": metric, "value": 0.0, "tier": 3,
                                     "source": f"declared:zero[{spec['why'][:120]}]"}), cols)
        groups[(name, metric)] = z if g is None or g.empty else pd.concat([g, z], ignore_index=True).sort_values("date")


def _foundation_buyback_views(groups: dict) -> None:
    """AERODROME'S FOUNDATION BUY-AND-LOCK (external audit 2026-10-09, item 1): actual_buyback_tokens = the day's change
    in the AERO the buyback wallets hold locked (foundation_locked_aero_tokens) + min(change in their rebase claimable,
    0) — a rebase claim moves claimable into the lock, so the two cancel; accrual (claimable rising) is not a buy.
    Consecutive days only; a negative day (a lock leaving the wallets) is kept as measured
    (check_offline_items.py aerodrome_foundation_q0 names those days). ONE source string for every row, seeded or daily
    (Jake's run on 20d0eb4: a "[NEGATIVE …]" annotation stripped to a trailing space and read as a second measuring
    point, blanking the series as BUG). actual_buyback_usd = tokens x the same-day price."""
    for p in scoped_projects():
        fb = p.get("foundation_buyback")
        if not fb:
            continue
        name, g = p["name"], groups.get((p["name"], fb["stock_metric"]))
        if fb.get("trace"):
            _foundation_trace_rows(groups, p, fb)
        if g is None or g.empty:
            if fb.get("trace") and fb.get("basis") == "bought_plus_compounded":
                _foundation_agreed_basis(groups, p, fb, lambda m: _ser_of(groups, name, m), _view_cols(groups, name))
            continue
        ser = lambda m: (lambda x: None if x is None or x.empty else (  # noqa: E731
            x.assign(date=pd.to_datetime(x["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            .set_index("date")["value"].astype(float).sort_index()))(groups.get((name, m)))
        if fb.get("basis") == "bought_plus_compounded":
            _foundation_agreed_basis(groups, p, fb, ser, g.columns)
            continue
        st, cl = ser(fb["stock_metric"]), ser(fb["claimable_metric"])
        d = st.diff()
        d = d[(d.index.to_series().diff() == pd.Timedelta(days=1)).values & d.notna().values]
        if cl is not None:
            dc = cl.diff().reindex(d.index)
            d = d + dc.clip(upper=0).fillna(0.0)
        if d.empty:
            continue
        tok = pd.DataFrame({"date": d.index, "project": name, "metric": fb["flow"], "value": d.values, "tier": 2,
                            "source": f"derived:d({fb['stock_metric']}) + min(d({fb['claimable_metric']}), 0)"})
        groups[(name, fb["flow"])] = _as_stored(tok, g.columns)
        px = groups.get((name, "price_usd"))
        if px is None or px.empty:
            continue
        pmap = (px.assign(date=pd.to_datetime(px["date"]).dt.normalize()).drop_duplicates("date", keep="last")
                .set_index("date")["value"].astype(float))
        both = d[d.index.isin(pmap.index)]
        if both.empty:
            continue
        usd = pd.DataFrame({"date": both.index, "project": name, "metric": "actual_buyback_usd",
                            "value": (both * pmap.reindex(both.index)).values, "tier": 2,
                            "source": "derived:tokens*price"})
        groups[(name, "actual_buyback_usd")] = _as_stored(usd, g.columns)


def _ser_of(groups: dict, name: str, metric: str):
    x = groups.get((name, metric))
    if x is None or x.empty:
        return None
    return (x.assign(date=pd.to_datetime(x["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            .set_index("date")["value"].astype(float).sort_index())


def _view_cols(groups: dict, name: str):
    g = next((g for (n, _m), g in groups.items() if n == name and g is not None and not g.empty), None)
    return g.columns if g is not None else ["date", "project", "metric", "value", "source", "tier"]


TRACE_SOURCE = "derived:foundation_buyback[{label}; buy {buy}; forward {fwd}]"


def _foundation_trace_rows(groups: dict, p: dict, fb: dict) -> None:
    """THE TRACED PURCHASES (Jake 2026-10-12, Aerodrome round item 1; config foundation_buyback.trace): tier-3 rows,
    one per purchase day — AERO bought (foundation_buyback_trace_tokens) and USDC paid (_usd), both transactions in the
    source."""
    tr, name = fb["trace"], p["name"]
    cols = _view_cols(groups, name)
    rows = tr["rows"]
    src = [f"trace:base:foundation_buyer_safe[{tr['label']}; buy {r['buy_tx']}; forward {r['forward_tx']}; block "
           f"{r['block']}]" for r in rows]
    for metric, key in (("foundation_buyback_trace_tokens", "aero"), ("foundation_buyback_trace_usd", "usdc")):
        groups[(name, metric)] = _as_stored(pd.DataFrame(
            {"date": pd.to_datetime([r["date"] for r in rows]), "project": name, "metric": metric,
             "value": [float(r[key]) for r in rows], "tier": 3, "source": src}), cols)


def trace_in_use(p: dict, scan: pd.Series | None) -> bool:
    """The trace stands for its lock until the chain scan holds the lock's day (the agreed basis counts the same AERO
    on that day): then the scan replaces it, never both."""
    tr = (p.get("foundation_buyback") or {}).get("trace")
    if not tr:
        return False
    return scan is None or pd.Timestamp(tr["lock"]["date"]) not in scan.index


def _foundation_agreed_basis(groups: dict, p: dict, fb: dict, ser, cols) -> None:
    """THE BASIS JAKE AGREED (2026-10-11, B): per day, actual_buyback_tokens = AERO bought and locked in one
    transaction + locks paid from the balance MATCHED OLDEST-FIRST to the wallets' purchases (a running pool: every
    stored purchase day up to and including the lock's day, less what earlier locks used) + compounded voting rewards
    on accrual (owners' share). The rebase (foundation_rebase_owner_tokens) and withdrawManaged realisations are not in
    it. Only days the window read stored; none stored -> no series (the old stock-change basis is retired, not a
    fallback). actual_buyback_usd = tokens x the same-day price."""
    wm = fb["window_metrics"]
    parts = {k: ser(m) for k, m in wm.items()}
    days = pd.DatetimeIndex([])
    if not any(v is None for k, v in parts.items() if k != "rebase_owner"):
        days = parts["compounded_owner"].index
        for k in ("bought_same_tx", "lock_from_balance", "purchased"):
            days = days.intersection(parts[k].index)
    if days.empty:
        _foundation_with_trace(groups, p, fb, None, cols)
        return
    pool, tok = 0.0, []
    purchased = parts["purchased"].sort_index()
    for d in days.sort_values():
        pool += float(purchased[(purchased.index <= d) & (purchased.index > (tok[-1][0] if tok else pd.Timestamp.min))]
                      .sum())
        matched = min(float(parts["lock_from_balance"].get(d, 0.0)), pool)
        pool -= matched
        tok.append((d, float(parts["bought_same_tx"].get(d, 0.0)) + matched + float(parts["compounded_owner"].get(d, 0.0))))
    idx = pd.DatetimeIndex([d for d, _v in tok])
    vals = pd.Series([v for _d, v in tok], index=idx)
    _foundation_with_trace(groups, p, fb, vals, cols)


def _foundation_with_trace(groups: dict, p: dict, fb: dict, scan: pd.Series | None, cols) -> None:
    """actual_buyback_tokens / _usd from the chain scan, with the trace standing in for its lock until the scan holds
    the lock's day (trace_in_use). Trace days carry the USDC actually paid; scan days tokens x the same-day price. One
    measuring point: every source is derived:foundation_buyback[...] (the bracket is an annotation)."""
    name = p["name"]
    tok, usd, src = {}, {}, {}
    if scan is not None:
        for d, v in scan.items():
            tok[d], src[d] = float(v), "derived:foundation_buyback[bought+matched+compounded]"
    tr = fb.get("trace")
    if tr and trace_in_use(p, scan):
        for r in tr["rows"]:
            d = pd.Timestamp(r["date"])
            tok[d] = tok.get(d, 0.0) + float(r["aero"])
            usd[d] = usd.get(d, 0.0) + float(r["usdc"])
            src[d] = TRACE_SOURCE.format(label=tr["label"], buy=r["buy_tx"], fwd=r["forward_tx"])
    if not tok:
        return
    idx = pd.DatetimeIndex(sorted(tok))
    tier = [3 if "trace" in src[d] else 2 for d in idx]
    groups[(name, fb["flow"])] = _as_stored(pd.DataFrame(
        {"date": idx, "project": name, "metric": fb["flow"], "value": [tok[d] for d in idx], "tier": tier,
         "source": [src[d] for d in idx]}), cols)
    px = groups.get((name, "price_usd"))
    pmap = (px.assign(date=pd.to_datetime(px["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            .set_index("date")["value"].astype(float)) if px is not None and not px.empty else pd.Series(dtype=float)
    days = [d for d in idx if d in usd or d in pmap.index]
    if not days:
        return
    groups[(name, "actual_buyback_usd")] = _as_stored(pd.DataFrame(
        {"date": days, "project": name, "metric": "actual_buyback_usd",
         "value": [usd[d] if d in usd else tok[d] * float(pmap[d]) for d in days],
         "tier": [3 if d in usd else 2 for d in days],
         "source": ["derived:tokens*price[trace: USDC actually paid]" if d in usd else "derived:tokens*price"
                    for d in days]}), cols)


# ===== EVERY VIEW EMITS A STORED ROW'S COLUMNS. Fixed 2026-09-28 after a build crash. =====
# Run 20260928T142424Z: aggregate() died on latest["is_manual"] — the declared-issuance view built
# NEAR's series from scratch and copied the stored columns from the derived series it replaced,
# which that store did not have. A view row must be indistinguishable in SHAPE from a stored one
# (store.load_long: date, project, metric, value, source, tier, fetched_at, is_manual, entered_on,
# source_note), whatever else is or is not in the store.
_STORED_DEFAULTS = {"tier": None, "fetched_at": "", "is_manual": False, "entered_on": "",
                    "source_note": ""}


def _utilisation_views(groups: dict) -> None:
    """UTILISATION = DELIVERED / ONLINE HOURS (Aethir, Jake's probes8 2026-10-02). From two cumulatives read
    daily: utilisation_pct (PRIMARY) = their rises over the same span — the latest reading at least
    `window_days` earlier, within `max_span_days` (span stated when not 7) — and utilisation_cumulative_pct
    = the cumulative ratio. Rows stored under utilisation_pct by the old containers x 168h derivation are
    shown as the lower bound (utilisation_containers_pct), never mixed into the primary."""
    for p in config.PROJECTS:
        uv = p.get("utilisation_view")
        if not uv:
            continue
        name = p["name"]

        def stock(metric):
            g = groups.get((name, metric))
            if g is None or g.empty:
                return pd.Series(dtype=float)
            g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            return g.set_index("date")["value"].astype(float).sort_index()
        old = groups.get((name, uv["metric"]))
        cols = old.columns if old is not None and not old.empty else None
        if old is not None and not old.empty:                 # the old derivation's rows -> the lower bound
            moved = old[old["source"].astype(str).str.startswith(uv["old_source"])]
            if not moved.empty:
                lb = groups.get((name, uv["lower_bound_metric"]))
                lb = pd.concat([x for x in (lb, moved.assign(metric=uv["lower_bound_metric"])) if x is not None],
                               ignore_index=True).drop_duplicates(["date"], keep="first")
                groups[(name, uv["lower_bound_metric"])] = lb
            rest = old.drop(moved.index)
            if rest.empty:
                groups.pop((name, uv["metric"]), None)
            else:
                groups[(name, uv["metric"])] = rest
        d, o = stock(uv["delivered"]), stock(uv["online"])
        both = d.index.intersection(o.index)
        if both.empty:
            continue
        cum = pd.DataFrame({"date": both, "project": name, "metric": uv["cumulative_metric"],
                            "value": (d[both] / o[both]).values, "tier": 3,
                            "source": f"derived:utilisation[{uv['delivered']} / {uv['online']}, cumulative]"})
        groups[(name, uv["cumulative_metric"])] = _as_stored(cum, cols if cols is not None else cum.columns)
        rows = []
        for day in both:
            prior = [x for x in both if day - pd.Timedelta(days=int(uv["max_span_days"])) <= x
                     <= day - pd.Timedelta(days=int(uv["window_days"]))]
            if not prior:
                continue
            p0 = max(prior)
            dd, do = d[day] - d[p0], o[day] - o[p0]
            if do <= 0 or dd < 0:
                continue
            span = (day - p0).days
            rows.append({"date": day, "project": name, "metric": uv["metric"], "value": dd / do, "tier": 3,
                         "source": f"derived:utilisation[weekly: rise of {uv['delivered']} / rise of {uv['online']}"
                                   + (f", span={span}d" if span != int(uv["window_days"]) else "") + "]"})
        if rows:
            v = pd.DataFrame(rows)
            groups[(name, uv["metric"])] = _as_stored(v, cols if cols is not None else v.columns)
        else:
            _VIEW_BLOCKS_PENDING[(name, uv["metric"])] = (
                f"WAITING — the weekly ratio needs both cumulatives read {uv['window_days']} days apart; held "
                f"together on {len(both)} day(s) since {both.min().date()}. The cumulative ratio "
                f"({uv['cumulative_metric']}) and the lower bound ({uv['lower_bound_metric']}) stand meanwhile")


# set before _VIEW_BLOCKS is cleared; re-applied after (like _ONE_OFF_BLOCKS)
_VIEW_BLOCKS_PENDING: dict = {}
# blocks raised by the views that run before _VIEW_BLOCKS is cleared mid-aggregate (G2-D1: the summed emissions view
# blocked Morpho's emissions_tokens there and the block was lost, so the cell read "no rows stored"); re-applied after
_EARLY_BLOCKS: dict = {}


def _mev_estimate_views(groups: dict) -> None:
    """THE NON-RELAY LEG BY ESTIMATE (probes6 1c, Jake 2026-10-02). Where a day's execution reward holds the
    relay-delivered value only (PARTIAL — the per-block leg not read), add the day's total priority fees —
    (fees - burn) in USD, DefiLlama, over the day's price — times the NON-RELAY share of its slots
    (1 - mev_relay_block_share as stored for that day). Marked `estimate`; a day without fees, burn, price
    or share stays as stored (PARTIAL)."""
    for p in config.PROJECTS:
        spec, ys = p.get("mev_relays"), config.VALIDATOR_YIELD.get(p["name"]) or {}
        if not spec or spec.get("nonrelay_leg", "estimate") != "estimate" or not ys.get("fees_metric"):
            continue
        name = p["name"]
        exe = groups.get((name, "execution_rewards_eth"))
        if exe is None or exe.empty:
            continue

        def daily(metric):
            g = groups.get((name, metric))
            if g is None or g.empty:
                return {}
            g = g.sort_values("date").drop_duplicates("date", keep="last")
            return dict(zip(pd.to_datetime(g["date"]).dt.normalize(), g["value"].astype(float)))
        fees, burn, px, share = (daily(ys["fees_metric"]), daily(ys["burned_metric"]), daily("price_usd"),
                                 daily("mev_relay_block_share"))
        tag = "[non-relay leg ESTIMATED: (DefiLlama fees - burn) / price x non-relay share of slots]"
        out = []
        for r in exe.to_dict("records"):
            src = str(r["source"])
            d = pd.Timestamp(r["date"]).normalize()
            if ":PARTIAL[non-relay leg by ESTIMATE" not in src or any(d not in m for m in (fees, burn, px, share)) \
                    or px[d] <= 0:
                out.append(r)
                continue
            prio = max(fees[d] - burn[d], 0.0) / px[d]
            rest = src.split("]", 1)[1] if "]" in src else ""       # a [relays not read: ...] tag is kept
            base = config.mark_source(src.split(":PARTIAL")[0], "estimate") + tag
            if rest:
                base = config.mark_source(base, "PARTIAL") + rest
            out.append({**r, "value": float(r["value"]) + prio * max(1.0 - share[d], 0.0), "source": base})
        groups[(name, "execution_rewards_eth")] = _as_stored(pd.DataFrame(out), exe.columns)


def _as_stored(view: pd.DataFrame, columns) -> pd.DataFrame:
    """The view with every stored column present, defaulted where a view has no value for it."""
    view = view.copy()
    for col in list(columns) + list(_STORED_DEFAULTS):
        if col not in view.columns:
            view[col] = _STORED_DEFAULTS.get(col, "")
    return view


# (project, metric) -> reason, set by the read-time views on each aggregate() call and read by
# withheld_for (case 8c) and the A1 validator-yield cell. A view that decides a series cannot be
# shown says why here, once, and every consumer blanks it the same way.
_VIEW_BLOCKS: dict = {}


def _leg_disagrees(src: pd.DataFrame, held: pd.DataFrame | None, tol: float) -> str | None:
    """None when the history leg and the live series agree within `tol` on the latest day both
    hold; else why it is not prepended (no shared day, or the two differ by more)."""
    if held is None or held.empty:
        return "no live reading to check the history leg against yet"
    a = src.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
    b = held.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
    common = a.index.intersection(b.index)
    if common.empty:
        return (f"the history leg ({a.index.min().date()}..{a.index.max().date()}) shares no day with "
                f"the live read (from {b.index.min().date()}) — nothing to check agreement on")
    d = common.max()
    if b[d] == 0 or abs(a[d] / b[d] - 1) > tol:
        return (f"history leg {a[d]:,.0f} vs live {b[d]:,.0f} on {d.date()} "
                f"({(a[d] / b[d] - 1) if b[d] else float('inf'):+.1%}) — beyond ±{tol:.0%}; not prepended")
    return None


def _history_leg_views(groups: dict) -> None:
    """A flow's DECLARED history leg, prepended at read time (config `history_legs`).

    Ethereum, A1 2026-09-30: consensus_rewards_tokens is d(Eth2Staking), read from 2026-09-29
    only. New ETH IS consensus rewards (net of penalties), so for the days before its first row
    the ultrasound-leg gross issuance stands in — a declared series_handover, never overlapping,
    and never stored under this metric."""
    for p in scoped_projects():
        name = p["name"]
        for metric, leg in (p.get("history_legs") or {}).items():
            src = groups.get((name, leg["from_metric"]))
            if src is None or src.empty:
                continue
            src = src[src["source"].astype(str).str.startswith(leg["source_prefix"])]
            held = groups.get((name, metric))
            # AGREEMENT FIRST, where the leg asks for it (Hyperliquid's DefiLlama staking series,
            # 2026-09-30): the two measuring points must agree within `agree_within` on the latest
            # day both hold, or the history is not prepended and the reason is recorded.
            tol = leg.get("agree_within")
            if tol is not None:
                why = _leg_disagrees(src, held, float(tol))
                if why:
                    _VIEW_BLOCKS[(name, f"{metric}:history_leg")] = why
                    # said where it is seen: on the candidate series' own Data row
                    cand = groups[(name, leg["from_metric"])].copy()
                    cand["source"] = cand["source"].astype(str) + f"[NOT {metric}'s history: {why}]"
                    groups[(name, leg["from_metric"])] = cand
                    continue
            if held is not None and not held.empty:
                src = src[src["date"] < held["date"].min()]
            if src.empty:
                continue
            view = src.copy()
            view["metric"] = metric
            cols = held.columns if held is not None and not held.empty else src.columns
            parts = [_as_stored(view, cols)] + ([held] if held is not None and not held.empty else [])
            groups[(name, metric)] = pd.concat(parts, ignore_index=True).sort_values("date")


MODEL_INVERSE_SQRT = "model:inverse_sqrt(locked_tokens)"


def _emissions_model_views(groups: dict) -> None:
    """Hyperliquid (Jake, 2026-09-30): staking rewards per day from the published formula,
    rate_at x sqrt(at_staked / S) x S / 365, over the stake history (config `reward_formula`).

    Always shown as its own row. It is prepended to the observed series (the fall in
    futureEmissions) as history ONLY when the two agree within `agree_within` over at least
    `min_shared_days` shared days; the row's source carries the ratio either way."""
    for p in scoped_projects():
        name = p["name"]
        spec = p.get("reward_formula") or {}
        stake = groups.get((name, spec.get("stake_metric")))
        if not spec or stake is None or stake.empty:
            continue
        s = stake.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        s = s[s > 0]
        if s.empty:
            continue
        model = spec["rate_at"] * (spec["at_staked"] * s) ** 0.5 / 365.0
        obs = groups.get((name, spec["observed_metric"]))
        o = pd.Series(dtype=float) if obs is None or obs.empty else \
            obs.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        common = model.index.intersection(o.index)
        ratio = float(o[common].sum() / model[common].sum()) if len(common) and model[common].sum() else None
        agrees = (ratio is not None and len(common) >= int(spec["min_shared_days"])
                  and abs(ratio - 1) <= float(spec["agree_within"]))
        tag = (f"{MODEL_INVERSE_SQRT}[observed/model={ratio:.2f} over {len(common)}d]" if ratio is not None
               else f"{MODEL_INVERSE_SQRT}[no shared day with {spec['observed_metric']}]")
        view = pd.DataFrame({"date": model.index, "project": name, "metric": spec["metric"],
                             "value": model.values, "source": tag, "tier": 1})
        groups[(name, spec["metric"])] = _as_stored(view, stake.columns)
        if name in config.HL_REWARD_MODEL:
            continue                                     # fix round 3: the reference-yield model is the history leg
        if not agrees:
            _VIEW_BLOCKS[(name, f"{spec['observed_metric']}:history_leg")] = (
                f"modelled rewards not used as history: observed/model = "
                f"{'n/a' if ratio is None else f'{ratio:.2f}'} over {len(common)} shared day(s); needs "
                f"±{spec['agree_within']:.0%} over >= {spec['min_shared_days']} days")
            continue
        first = o.index.min()
        hist = view[view["date"] < first].copy()
        if hist.empty:
            continue
        hist["metric"], hist["source"] = spec["observed_metric"], MODEL_INVERSE_SQRT
        groups[(name, spec["observed_metric"])] = pd.concat([_as_stored(hist, obs.columns), obs],
                                                            ignore_index=True).sort_values("date")


def _first_party_history_views(groups: dict) -> None:
    """FIRST-PARTY CIRCULATING HISTORY (fix round 2, H1; config.FIRST_PARTY_HISTORY): the days before the first stored
    first-party read, on the same basis — the published monthly table (Aethir) or a third party's figure that mirrors
    it exactly (Plume, from 2026-01-01). Read-time only, under its own source, a declared handover. Also the schedule's
    monthly step as scheduled_release_tokens (the Flows tab's '+ scheduled releases')."""
    for p in scoped_projects():
        name = p["name"]
        h = config.FIRST_PARTY_HISTORY.get(name)
        if h:
            g = groups.get((name, h["metric"]))
            first = pd.to_datetime(g["date"]).min().normalize() if g is not None and not g.empty else None
            src_g = None
            if h["route"] == "schedule":
                start = pd.Period(min(config.CIRCULATING_SCHEDULE[name]["table"]), "M").start_time
                end = (first - pd.Timedelta(days=1)) if first is not None else None
                if end is not None and end >= start:
                    days = pd.date_range(start, end, freq="D")
                    vals = [config.circulating_schedule_value(name, d.to_period("M")) for d in days]
                    src_g = pd.DataFrame({"date": days, "value": vals}).dropna()
            else:
                m = groups.get((name, h["mirror"]))
                if m is not None and not m.empty and first is not None:
                    m = m.assign(date=pd.to_datetime(m["date"]).dt.normalize())
                    m = m[(m["date"] >= pd.Timestamp(h["from"])) & (m["date"] < first)]
                    src_g = m[["date", "value"]].drop_duplicates("date", keep="last")
            if src_g is not None and not src_g.empty and g is not None:
                lead = pd.DataFrame({"date": src_g["date"].values, "project": name, "metric": h["metric"],
                                     "value": src_g["value"].astype(float).values, "source": h["source"], "tier": 3})
                groups[(name, h["metric"])] = pd.concat([_as_stored(lead, g.columns), g], ignore_index=True)
        if name in config.CIRCULATING_SCHEDULE:
            tbl = config.CIRCULATING_SCHEDULE[name]["table"]
            months = sorted(tbl)
            rows = [(pd.Period(b, "M").start_time, float(tbl[b]) - float(tbl[a]))
                    for a, b in zip(months, months[1:])]
            days = pd.date_range(pd.Period(months[1], "M").start_time, pd.Period(months[-1], "M").end_time.normalize())
            step = dict(rows)
            cols = next((g_.columns for (n_, _m), g_ in groups.items() if n_ == name and g_ is not None), None)
            if cols is not None:
                groups[(name, "scheduled_release_tokens")] = _as_stored(pd.DataFrame(
                    {"date": days, "project": name, "metric": "scheduled_release_tokens",
                     "value": [max(0.0, step.get(d, 0.0)) for d in days], "tier": 3,
                     "source": f"config:circulating_schedule[{config.CIRCULATING_SCHEDULE[name]['source']}]"}), cols)


def _subtract_when_complete(groups: dict, name: str, s: pd.Series, tag: str, swc: dict) -> tuple[pd.Series, str]:
    """G18-3c (Sky's endowments): a leg taken out of circulating only once it is held for EVERY day from its declared
    start (0 before it, by evidence) through the series' last day — the whole series moves to the new basis in one
    build, never a series on two bases. Until then the old basis stands and swc["state"] says what is pending."""
    leg = _daily(groups.get((name, swc["metric"])))
    start = pd.Timestamp(swc["zero_before"])
    need = s.index[s.index >= start]
    have = need.intersection(leg.index)
    if len(need) and len(have) == len(need):
        swc["state"] = (f"in force: {swc['metric']} held on all {len(need)} day(s) from {start.date()}; "
                        f"{leg.reindex(need).iloc[-1]:,.0f} SKY taken out on {need[-1].date()}")
        out = s - leg.reindex(s.index).where(s.index >= start, 0.0)
        return out, tag + f"[− {swc['metric']}]"
    first = leg.index.min().date() if not leg.empty else None
    swc["state"] = (f"PENDING — {swc['metric']} held on {len(have)} of {len(need)} day(s) from {start.date()}"
                    + (f" (live reads from {first})" if first else "") + "; the subtraction switches on when every day "
                    "is held — `python archive_backfill.py --run --project Sky` fills the history")
    return s, tag


def _circulating_views(groups: dict) -> None:
    """circulating_supply_onchain := on-chain total − the documented non-circulating set, per day
    (M, Jake 2026-09-30; fetch/circulating.py). Only days where every input was read."""
    from fetch import circulating as circ_mod
    for p in scoped_projects():
        name = p["name"]
        spec = config.circulating_onchain(name) or {}
        if spec.get("status") not in ("established", "partial"):
            continue
        # THE ADDED-BACK LEGS TOO (Jake's review of the ef6dc86 run, item 9): fetch/circulating.series adds spec["add"]
        # from the frame it is given; without them in it, Sky's MKR-backed SKY (decision 2a) was an empty series, every
        # day dropped, and circulating_used fell back to CoinGecko (23,433.6M against the decided ~23,291.6M)
        need = [spec["total"], *spec.get("subtract", ()), *spec.get("add", ())]
        parts = [groups.get((name, m)) for m in need]
        if any(g is None or g.empty for g in parts):
            continue
        s = circ_mod.series(pd.concat(parts, ignore_index=True), name)
        if s.empty:
            continue
        tag = circ_mod.SOURCE + ("" if spec["status"] == "established" else "[PARTIAL set]")
        swc = spec.get("subtract_when_complete")
        if swc:
            s, tag = _subtract_when_complete(groups, name, s, tag, swc)
        view = pd.DataFrame({"date": s.index, "project": name, "metric": "circulating_supply_onchain",
                             "value": s.values, "source": tag, "tier": 2})
        groups[(name, "circulating_supply_onchain")] = _as_stored(view, parts[0].columns)


PROVISIONAL: dict = {}      # (project, metric) -> [(day, read_at)] left out of this build (G15 5)


PROVIDER_RUNS: dict = {}     # (provider, project) -> start times of the runs that read it (store.provider_runs, G16 1)


def _provisional_views(groups: dict) -> None:
    """G16 1 (config.provisional): a LISTED series' LATEST row from a provider that publishes early (config.
    provisional_series — it read low on at least two days, or is declared) is left out of every window — its day reads
    as not yet held — and named on the Data row, until a later run re-reads it (PROVIDER_RUNS: two successful reads of
    the provider for the project after the day closed, up to the row's fetched_at)."""
    PROVISIONAL.clear()
    for key in list(groups.keys()):
        # read UNTRACKED: the view trims a series' own rows and depends on no other series — a tracked read of every
        # key would make every series of a project a "parent" of its trimmed flows in the wiring audit
        g = dict.get(groups, key)
        if g is None or g.empty or "fetched_at" not in g:
            continue
        p, m = key
        # the LATEST day only: once a newer day is held, a run has read past it (a source that re-reads its trailing
        # window has replaced it; one that does not would otherwise hold it provisional for ever)
        tail = g[pd.to_datetime(g["date"]) == pd.to_datetime(g["date"]).max()]
        prov = [i for i, d, s_, fa in zip(tail.index, tail["date"], tail["source"], tail["fetched_at"])
                if config.provisional(p, m, s_, d, fa, PROVIDER_RUNS.get((str(s_).split(":")[0].lower(), p), ()))]
        if prov:
            PROVISIONAL[key] = [(str(pd.Timestamp(g.loc[i, "date"]).date()), str(g.loc[i, "fetched_at"])) for i in prov]
            groups[key] = g.drop(index=prov)


def _without_provisional(long: pd.DataFrame) -> pd.DataFrame:
    """The store frame less the rows _provisional_views held back, so a check that reproduces a headline from the store
    (the Credibility rows) reads the same days the headline does."""
    if long is None or long.empty or not PROVISIONAL:
        return long
    held = {(p, m, d) for (p, m), rows in PROVISIONAL.items() for d, _fa in rows}
    keys = zip(long["project"], long["metric"], pd.to_datetime(long["date"]).dt.strftime("%Y-%m-%d"))
    return long[[k not in held for k in keys]]


def _partial_sum_views(groups: dict) -> None:
    """A SUMMED METRIC NEVER READS A PARTIAL DAY (Jake, 2026-10-05). Before that date
    fetch/llama.py _sum_slugs stored a day on which an active service had no point, flagged
    "[missing: …]" in its source — Chainlink's Q0 customer revenue read $692,288 instead of ~$15.26M
    after DefiLlama timeouts. Such rows are dropped at read time, so the day is uncovered (the
    coverage caveats say so) until the next complete run re-stores it."""
    for p in scoped_projects():
        for spec in p.get("defillama_sum_slugs") or ():
            key = (p["name"], spec["metric"])
            g = groups.get(key)
            if g is None or g.empty:
                continue
            bad = g["source"].astype(str).str.contains("[missing:", regex=False)
            if bad.any():
                groups[key] = g[~bad]


def _customer_revenue_views(groups: dict) -> None:
    """CUSTOMER REVENUE = THE AGGREGATOR CORE + THE LINES THAT BYPASS IT (Chainlink, Jake 2026-10-05;
    config customer_revenue_components). A day is summed only when the core and EVERY plus line hold a
    row for it — a missing line refuses the day, never a partial sum. Replaces whatever the store holds
    under the metric (the retired DefiLlama listing sum: orphan_cleanup.sql BY)."""
    for p in scoped_projects():
        spec = p.get("customer_revenue_components")
        if not spec:
            continue
        name, legs = p["name"], (spec["core"], *spec["plus"])
        series, sources = [], []
        for m in legs:
            g = groups.get((name, m))
            if g is None or g.empty:
                series = None
                break
            g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            series.append(g.set_index("date")["value"].astype(float))
            sources.append(g.set_index("date")["source"].astype(str))
        if series is None:
            groups.pop((name, spec["metric"]), None)
            _VIEW_BLOCKS_PENDING[(name, spec["metric"])] = (
                "NOT YET SUMMED — a component has no rows yet: " + ", ".join(
                    m for m in legs if groups.get((name, m)) is None or groups[(name, m)].empty)
                + ". Not measured at all: " + spec.get("not_measured", ""))
            continue
        days = sorted(set.intersection(*(set(s_.index) for s_ in series)))
        if not days:
            continue
        total = sum(s_.reindex(days) for s_ in series)
        cols = groups[(name, spec["core"])].columns
        # A PARTIAL LEG MAKES A PARTIAL SUM (Chainlink's fee lines without Base/OP, 2026-10-05): the day is
        # stored, and the marker names which legs are short.
        base_src = f"derived:sum({' + '.join(legs)})"
        srcs = []
        for d in days:
            short = [m for m, sr in zip(legs, sources) if ":PARTIAL" in sr.get(d, "")]
            srcs.append(config.mark_source(base_src, "PARTIAL") + f"[{', '.join(short)} partial]" if short else base_src)
        view = pd.DataFrame({"date": days, "project": name, "metric": spec["metric"], "value": total.values,
                             "source": srcs, "tier": 2})
        groups[(name, spec["metric"])] = _as_stored(view, cols)


def chosen_circulating_metric(p: dict) -> str:
    """The circulating series every token ratio uses (_circ): the project's own figure where one exists, else
    CoinGecko's (config.circulating_primary_metric — the policy of 2026-10-07)."""
    return config.circulating_primary_metric(p["name"])


def _cg_plus_staked_views(groups: dict) -> None:
    """circulating_supply_cg_plus_staked := CoinGecko's circulating + the staked tokens it excludes, added back ONCE
    (POLICY 2026-10-07; Aerodrome: veAERO's locked principal less the team's 95M, which is out of circulating by
    Jake's 2026-09-30 decision). Free float (circulating − the lock inside it) is then CoinGecko's own figure.
    Only days where both are read."""
    for p in scoped_projects():
        name = p["name"]
        if config.circulating_primary_metric(name) != "circulating_supply_cg_plus_staked":
            continue
        cg, lk = groups.get((name, "circulating_supply")), groups.get((name, config.lock_display_metric(name)))
        if cg is None or cg.empty or lk is None or lk.empty:
            continue
        s = lambda g: (g.assign(date=pd.to_datetime(g["date"]).dt.normalize())  # noqa: E731
                       .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
        x = config.locked_excluded_from_circulating(name)
        both = pd.concat([s(cg).rename("cg"), s(lk).rename("lk")], axis=1).dropna()
        if both.empty:
            continue
        vals = both["cg"] + (both["lk"] - x).clip(lower=0)
        view = pd.DataFrame({"date": both.index, "project": name, "metric": "circulating_supply_cg_plus_staked",
                             "value": vals.values, "tier": 1,
                             "source": f"derived:circulating_supply+{config.lock_display_metric(name)}"
                                       + (f"-{x:.0f}" if x else "")})
        groups[(name, "circulating_supply_cg_plus_staked")] = _as_stored(view, cg.columns)


def _market_cap_views(groups: dict) -> None:
    """MARKET CAP = PRICE x THE CIRCULATING THE TOKEN RATIOS USE (Jake, 2026-10-05). It was CoinGecko's
    market cap — price x CoinGecko's circulating — while every token ratio used our chosen circulating,
    so Aethir's free-float market cap ($166.6M, price x (24.05bn first-party - locked)) exceeded its
    market cap ($150.6M, CoinGecko's ~20.1bn), and Hyperliquid's market cap ($20.77bn) was price x
    ~222.6M where we use 298.67M. Per day: price x the chosen circulating (first-party / on-chain where
    established, CoinGecko's on days it has none — the same fallback as _circ), net of GEODNET's
    un-netted Solana burn as _circ nets it. CoinGecko's own market cap stays beside it as
    market_cap_usd_coingecko, the cross-check."""
    for p in scoped_projects():
        name = p["name"]
        cg = groups.get((name, "market_cap_usd"))
        px = groups.get((name, "price_usd"))
        if px is None or px.empty:
            continue

        def daily(metric):
            g = groups.get((name, metric))
            if g is None or g.empty:
                return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
            g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
            return g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        price = daily("price_usd")
        chosen = chosen_circulating_metric(p)
        own, fallback = daily(chosen), daily("circulating_supply")
        if config.no_coingecko_fallback(name) and chosen != "circulating_supply":
            fallback = fallback.iloc[0:0]            # another basis, never a stand-in (Phase 1 review, item 2)
        elif config.circulating_onchain_only(name, chosen) and not own.empty:
            fallback = fallback.iloc[0:0]   # P2d-3b: an on-chain route never takes CoinGecko once it has any on-chain day
        # A HAND-ENTERED MONTHLY FIGURE HOLDS UNTIL IT IS STALE (GEODNET's own circulating, 2026-10-07): carried
        # forward up to 45 days, the AMBER threshold of a monthly manual series.
        if chosen != "circulating_supply" and not own.empty and config.series_granularity(name, chosen) == "monthly":
            own = own.reindex(own.index.union(price.index)).ffill(limit=45).dropna()
        # ONE BASIS FOR MARKET CAP AND EVERY RATIO (Jake's review of 97b29ae, item 8: Aerodrome's market cap read
        # CoinGecko's 1.005bn on the latest day — our on-chain figure had no row that day — while A3's ratios used
        # the on-chain 1.554bn). A daily figure of our own is carried forward within its age limit
        # (credibility.now_max_age: 3 days), so the latest day keeps our basis; past it, the fallback below.
        elif chosen != "circulating_supply" and not own.empty:
            import credibility as _cred                   # noqa: PLC0415
            own = own.reindex(own.index.union(price.index)).ffill(limit=_cred.now_max_age(name, chosen)).dropna()
        # THE FALLBACK _circ USES: CoinGecko's figure, plus the locked tokens inside our convention where CoinGecko's
        # is free float (config.coingecko_is_free_float — Pendle), less any lock declared out of circulating
        if chosen != "circulating_supply" and config.coingecko_is_free_float(name) and not fallback.empty:
            legs = [daily(m) for m in config.free_float_lock_metrics(name)]
            if legs and all(not g.empty for g in legs):
                lock = sum(g.reindex(fallback.index, method="ffill") for g in legs)
                x = config.locked_excluded_from_circulating(name)
                if x and config.circulating_excludes_declared(name):
                    lock = (lock - x).clip(lower=0)
                fallback = (fallback + lock).dropna()
        circ = own.combine_first(fallback) if chosen != "circulating_supply" else fallback
        if config.supply_unnetted_burn(name):
            tot, gross, burned = daily("total_supply"), daily("total_supply_gross"), daily("burn_address_balance")
            idx = circ.index
            adj = (tot.reindex(idx, method="ffill") - (gross.reindex(idx, method="ffill")
                                                       - burned.reindex(idx, method="ffill"))).round(2)
            # The un-netted burn is CoinGecko's: a project's own figure is never adjusted for it (2026-10-07).
            # (any figure other than CoinGecko's: a project's own, a labelled manual one, or the on-chain set, which
            # already nets every burn it counts — GEODNET's Solana burn account included)
            if chosen != "circulating_supply":
                adj = adj.where(~idx.isin(own.index), 0.0)
            circ = (circ - adj).where(adj >= 0)
        both = price.index.intersection(circ.dropna().index)
        if both.empty:
            continue
        basis = {d: (chosen if (chosen != "circulating_supply" and d in own.index) else "circulating_supply")
                 for d in both}
        view = pd.DataFrame({"date": both, "project": name, "metric": "market_cap_usd",
                             "value": (price[both] * circ[both]).values, "tier": 1,
                             "source": [f"derived:price_x_circulating[{basis[d]}"
                                        + (", net of the un-netted burn" if config.supply_unnetted_burn(name) else "")
                                        + "]" for d in both]})
        cols = cg.columns if cg is not None and not cg.empty else px.columns
        if cg is not None and not cg.empty:
            groups[(name, "market_cap_usd_coingecko")] = _as_stored(cg.assign(metric="market_cap_usd_coingecko"), cols)
        groups[(name, "market_cap_usd")] = _as_stored(view, cols)
        # THE CIRCULATING SUPPLY USED — the same per-day series market cap multiplies (item 9: Master showed
        # CoinGecko's 222.4M for Hyperliquid while market cap used our 302.2M). _circ reads it first, so the ratios,
        # Master and every A-tab show and divide by one figure; CoinGecko's stays beside it as the cross-check.
        cd = circ.dropna()
        used = pd.DataFrame({"date": cd.index, "project": name, "metric": "circulating_supply_used",
                             "value": cd.values, "tier": 1,
                             "source": [f"derived:circulating_used[{chosen if (chosen != 'circulating_supply' and d in own.index) else 'circulating_supply' + ('+locked' if config.coingecko_is_free_float(name) and chosen != 'circulating_supply' else '')}]"
                                        for d in cd.index]})
        groups[(name, "circulating_supply_used")] = _as_stored(used, cols)


def _history_class(p: dict, metrics, data_by_key: dict) -> str:
    """'HISTORY <CLASS>: <metric> from <first date> — <reason>' for the youngest input of a trajectory row (Round G 2.2).
    CLASS is FORWARD-ONLY (the source keeps no history), SOURCE-LIMITED (no route to earlier days) or BUG (a route exists
    and has not filled it — the fix named). '' when no input has a first date."""
    import history_gaps as hg                              # noqa: PLC0415 — the same classifier the gap list uses
    name = p["name"]
    firsts = []
    for m in metrics:
        row = data_by_key.get(f"{name}|{m}") or {}
        f = str(row.get("first_date") or "")[:10]
        if f:
            firsts.append((f, m, row))
    if not firsts:
        return ""
    first, m, row = max(firsts)
    if m == "fdv_usd" and name in config.FDV_SHORT_REASON:
        return f"HISTORY {config.FDV_SHORT_REASON[name].split(':')[0]}: fdv_usd from {first} — " \
               f"{config.FDV_SHORT_REASON[name].split(':', 1)[1].strip()}"
    try:
        reason, fix, cost, _cmd = hg.fix_for(p, m, row)
    except Exception as e:  # noqa: BLE001 — the label never breaks the build
        return f"HISTORY: {m} from {first} (not classified: {e})"
    if reason.startswith("FORWARD-ONLY") or (reason.startswith("CoinGecko serves this as a current snapshot")
                                             and not fix.startswith("orphan_cleanup")):
        cls = "FORWARD-ONLY"
    elif cost == "none" or fix.startswith("none"):
        cls = "SOURCE-LIMITED"
    else:
        cls = f"BUG (a route exists and has not filled it; cost: {cost})"
    return f"HISTORY {cls}: {m} from {first} — {reason}; fix: {fix}"[:400]


def _fdv_history(name: str, groups: dict, primary: pd.Series, daily) -> tuple[str, pd.Series | None]:
    """(note, the total for days BEFORE the primary total's first row) from config.FDV_TOTAL_HISTORY — only when it
    measures the same quantity: median |alt / primary − 1| <= FDV_HISTORY_TOL over >= FDV_HISTORY_MIN_OVERLAP common
    days (Round G 2.2). (note, None) when the check fails or nothing is configured; the note says why."""
    spec = config.FDV_TOTAL_HISTORY.get(name)
    if not spec or primary is None or primary.empty:
        return "", None
    a_ = groups.get((name, spec["total"]))
    if a_ is None or a_.empty:
        return "", None
    alt = daily(a_)
    if spec.get("less"):
        b_ = groups.get((name, spec["less"]))
        if b_ is None or b_.empty:
            return "", None
        alt = (alt - daily(b_)).dropna()
    # THE MOST RECENT common days: a primary that changed its own definition (CoinGecko's GEODNET total became net of
    # the burn on 2026-10-12, −3.0%) is compared on what it means now, and extends once enough days agree
    common = alt.index.intersection(primary.index).sort_values()[-config.FDV_HISTORY_MIN_OVERLAP:]
    if len(common) < config.FDV_HISTORY_MIN_OVERLAP:
        return (f"(history NOT extended: {spec['total']} overlaps the total on {len(common)} day(s), fewer than "
                f"{config.FDV_HISTORY_MIN_OVERLAP})"), None
    dev = (alt[common] / primary[common] - 1).abs().median()
    if not dev <= config.FDV_HISTORY_TOL:
        return (f"(history NOT extended: {spec['label']} differs from the total by a median {dev:.3%} over "
                f"{len(common)} common day(s), over the {config.FDV_HISTORY_TOL:.1%} the same-quantity check allows)"), None
    note = (f"(total before {primary.index.min().date()}: {spec['label']} — the same quantity, median |diff| "
            f"{dev:.4%} over {len(common)} common day(s))")
    return note, alt


TOTAL_SUPPLY_HISTORY_NOTE: dict = {}      # project -> the same-quantity check's note, last build (Round G7 item 2e)


def _total_supply_history_views(groups: dict) -> None:
    """total_supply BEFORE ITS FIRST ROW from the declared older leg (Round G7 item 2e, Jake 2026-10-19: Near and
    Ethereum total_supply starts 2026-09-11 — CoinGecko's — leaving the 2026-06-30 references NOT WIRED). The leg is the one
    FDV history already uses (config.TOTAL_SUPPLY_HISTORY: NEAR's block-header total_supply, ultrasound.money's supply),
    under the SAME same-quantity check, run every build (_fdv_history: >= FDV_HISTORY_MIN_OVERLAP common days, median
    |diff| <= FDV_HISTORY_TOL). Only days before the primary's first row; each keeps its own source, so the series is a
    declared handover (series_handover total_supply). A failed check extends nothing and says why."""
    for name in [p["name"] for p in scoped_projects() if p["name"] in config.TOTAL_SUPPLY_HISTORY]:
        tot, leg = groups.get((name, "total_supply")), config.TOTAL_SUPPLY_HISTORY[name]
        legrows = groups.get((name, leg["total"]))
        if tot is None or tot.empty or legrows is None or legrows.empty:
            continue
        s = lambda g: (g.assign(date=pd.to_datetime(g["date"]).dt.normalize())  # noqa: E731
                       .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
        note, alt = _fdv_history(name, groups, s(tot), s)
        TOTAL_SUPPLY_HISTORY_NOTE[name] = note
        if alt is None:
            continue
        first = pd.to_datetime(tot["date"]).min().normalize()
        lr = legrows.assign(date=pd.to_datetime(legrows["date"]).dt.normalize()).drop_duplicates("date", keep="last")
        older = lr[lr["date"] < first].assign(metric="total_supply")
        if older.empty:
            continue
        groups[(name, "total_supply")] = _as_stored(pd.concat([older[tot.columns.intersection(older.columns)], tot],
                                                              ignore_index=True).sort_values("date"), tot.columns)


def _fdv_views(groups: dict) -> None:
    """FDV = PRICE x THE TOTAL SUPPLY WE USE, where total supply is on-chain first (config.TOTAL_SUPPLY_ONCHAIN_FIRST;
    Ether.fi, Jake's review of 97b29ae item 6: CoinGecko's FDV $700.3M rested on its 965.35M total — below the
    circulating basis — while ETHFI.totalSupply() read 998,535,999). Per day, price x total_supply (the stored series,
    whose latest leg is the chain read). CoinGecko's own FDV stays beside it as fdv_usd_coingecko."""
    # EVERY PROJECT, NOT ONLY THE ON-CHAIN-FIRST ONES (Jake's sanity run 2026-10-11: Bitcoin, Ethereum and Canton read
    # FDV < market cap by 0.003-0.03% — CoinGecko's FDV is ITS price x total at ITS timestamp, the market cap ours x the
    # circulating used). One price, one day: FDV = price_usd x total_supply, CoinGecko's FDV beside it as a cross-check.
    for name in [p["name"] for p in scoped_projects()]:
        px, tot, cg = (groups.get((name, m)) for m in ("price_usd", "total_supply", "fdv_usd"))
        net = config.FDV_TOTAL_NET.get(name)
        if net:
            # S6 (fix round 2): the total net of a burned balance — per day where both are stored, labelled
            a_, b_ = groups.get((name, net["total"])), groups.get((name, net["less"]))
            if a_ is None or a_.empty or b_ is None or b_.empty:
                continue
            ka = a_.assign(date=pd.to_datetime(a_["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            kb = b_.assign(date=pd.to_datetime(b_["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            j = ka.merge(kb[["date", "value"]].rename(columns={"value": "less"}), on="date")
            if j.empty:
                continue
            tot = j.assign(value=j["value"].astype(float) - j["less"].astype(float),
                           source=f"derived:{net['total']}-{net['less']}")[a_.columns.intersection(j.columns)]
        if px is None or px.empty or tot is None or tot.empty:
            continue
        s = lambda g: (g.assign(date=pd.to_datetime(g["date"]).dt.normalize())  # noqa: E731
                       .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
        price, total = s(px), s(tot)
        src = {d: str(v) for d, v in (tot.assign(date=pd.to_datetime(tot["date"]).dt.normalize())
                                      .drop_duplicates("date", keep="last").set_index("date")["source"]).items()}
        # carried forward within its age limit, as market cap carries the circulating used — the latest price day
        # keeps the same basis (credibility.now_max_age)
        import credibility as _cred                       # noqa: PLC0415
        hist_note, hist_days = _fdv_history(name, groups, total, s)
        total = total.reindex(total.index.union(price.index)).ffill(limit=_cred.now_max_age(name, "total_supply")).dropna()
        if hist_days is not None and not hist_days.empty:
            total = pd.concat([hist_days[hist_days.index < total.index.min()], total]) if not total.empty else hist_days
        both = price.index.intersection(total.index)
        if both.empty:
            continue
        # THE BRACKET NAMES THE TOTAL'S MEASURING POINT, NOT ITS RAW SOURCE (fix round 2, R3: Sky / Morpho / Ether.fi /
        # Fluid FDV read "measuring point changed" — a nested "[chain backfill (pre-handover)]" and an ":archive" leg
        # inside the bracket survived the annotation stripper as a second point). The value reads now; a change of the
        # total's basis is a SERIES BREAK on the trend only (trajectory.BRACKET_BASIS reads this bracket)
        from fetch.base import _measuring_point          # noqa: PLC0415
        src = {d: _measuring_point(v) for d, v in src.items()}
        # ===== THE 2026-09-11 HANDOVER, DECLARED AND CHECKED (Round G9 C1, Jake: Master FDV blank for Ethereum, Uniswap,
        # Near and Plume since G6 — "MEASURING POINT CHANGED … total before 2026-09-11"). G7's history leg did extend
        # the total, but (a) Uniswap / Plume: its note was appended OUTSIDE the bracket, so the value's measuring point
        # differed before the primary's first day; (b) Near / Ethereum: G7 extended total_supply itself
        # (TOTAL_SUPPLY_HISTORY), so the bracket named the older leg's source before 09-11. Both are one quantity
        # (price x total supply) under a declared handover: every day before the primary total's first day carries
        # the PRIMARY's point, and the leg is named in its own bracket — only when the same-quantity check passes
        # (fdv_handover_check: >= FDV_HISTORY_MIN_OVERLAP common days, median |diff| <= FDV_HISTORY_TOL; with no common
        # days, the two totals compared at the handover date within the same tolerance). A failed check changes
        # nothing: the older days keep their own point and the series reads MEASURING POINT CHANGED, honestly.
        pp = src[max(src)] if src else "?"
        first_pp = min((d for d, v in src.items() if v == pp), default=None)
        ok, hnote = fdv_handover_check(name, groups, total, src, pp, s)
        if hist_note and first_pp is not None and not ok:
            ok, hnote = True, hist_note.strip("()")       # the Uniswap / Plume leg: _fdv_history's own check passed
        last = pd.Series(src, dtype=object).reindex(total.index).ffill().bfill().fillna("?").to_dict()
        FDV_HANDOVER_NOTE[name] = hnote
        view = pd.DataFrame({"date": both, "project": name, "metric": "fdv_usd",
                             "value": (price[both] * total[both]).values, "tier": 1,
                             "source": [(f"derived:price_x_total_supply[{pp}][{hnote}]"
                                         if ok and first_pp is not None and d < first_pp
                                         else f"derived:price_x_total_supply[{last.get(d, '?')}]") for d in both]})
        cols = cg.columns if cg is not None and not cg.empty else px.columns
        if cg is not None and not cg.empty:
            groups[(name, "fdv_usd_coingecko")] = _as_stored(cg.assign(metric="fdv_usd_coingecko"), cols)
            # a day with no total of ours keeps CoinGecko's FDV (the series never loses a day it had)
            cgd = cg.assign(date=pd.to_datetime(cg["date"]).dt.normalize())
            rest = cgd[~cgd["date"].isin(both)]
            if not rest.empty:
                view = pd.concat([view, rest.assign(metric="fdv_usd")[view.columns.intersection(rest.columns)]],
                                 ignore_index=True).sort_values("date")
        groups[(name, "fdv_usd")] = _as_stored(view, cols)


FDV_HANDOVER_NOTE: dict = {}      # project -> the 2026-09-11 handover check's note, last build (G9 C1)


def fdv_handover_check(name: str, groups: dict, total: pd.Series, src: dict, pp: str, daily) -> tuple[bool, str]:
    """(passes, note) for the declared handover of FDV's total at the primary total's first day (G9 C1). The older
    leg is config.FDV_TOTAL_HISTORY[name]["total"] (minus "less"); compared with the PRIMARY rows only (those whose point
    is `pp`): >= FDV_HISTORY_MIN_OVERLAP common days within FDV_HISTORY_TOL median |diff| — or, with no common days, the
    leg's last value before the handover against the primary's first, within the same tolerance."""
    spec = config.FDV_TOTAL_HISTORY.get(name)
    if not spec or not src:
        return False, ""
    prim_days = sorted(d for d, v in src.items() if v == pp)
    older = [d for d, v in src.items() if v != pp and d < prim_days[0]] if prim_days else []
    if not older:
        return False, ""                                   # nothing before the primary carries another point
    a_ = groups.get((name, spec["total"]))
    if a_ is None or a_.empty:
        return False, f"handover NOT checked: no {spec['total']} rows"
    alt = daily(a_)
    if spec.get("less"):
        b_ = groups.get((name, spec["less"]))
        if b_ is None or b_.empty:
            return False, f"handover NOT checked: no {spec['less']} rows"
        alt = (alt - daily(b_)).dropna()
    prim = total.reindex(prim_days).dropna()
    common = alt.index.intersection(prim.index).sort_values()[-config.FDV_HISTORY_MIN_OVERLAP:]
    h = prim.index.min()
    if len(common) >= config.FDV_HISTORY_MIN_OVERLAP:
        dev = float((alt[common] / prim[common] - 1).abs().median())
        how = f"median |diff| {dev:.4%} over {len(common)} common day(s)"
    else:
        before = alt[alt.index < h]
        if before.empty:
            return False, f"handover NOT checked: {spec['total']} has no value before {h.date()}"
        dev = abs(float(before.iloc[-1]) / float(prim.iloc[0]) - 1)
        how = (f"no common days: {spec['total']} {before.index[-1].date()} vs the total {h.date()}, |diff| "
               f"{dev:.4%}")
    if not dev <= config.FDV_HISTORY_TOL:
        return False, f"handover NOT passed ({how} > {config.FDV_HISTORY_TOL:.1%})"
    return True, f"total before {h.date()}: {spec['label']} — declared handover, same quantity, {how}"


NEAR_LOCKUP_MAX_AGE_DAYS = 30      # D3-1 (Jake 2026-10-26): a read up to 30 days old forms the rule; BLOCKED after
NEAR_LOCKUP_FRESH_DAYS = 10        # a weekly read, plus slack: older than this (and at most 30) the rule reads AMBER
# D3-1: (project, metric) -> why, for the cells formed from a lockup read older than NEAR_LOCKUP_FRESH_DAYS (AMBER via
# confidence_for). Set by _near_lockup_views on each build.
LOCKUP_AGE_AMBER: dict = {}


def _near_lockup_views(groups: dict) -> None:
    """NEAR UNDER JAKE'S RULE (2026-10-11 C, clarified 2026-10-12 decision 8 on e4e50b0): everything NOT YET
    TRANSFERABLE leaves circulating — the lockups' LOCKED amount (still unvested + vested-but-lockup-locked; NEAR's own
    'locked', with the buggy-code lockups recomputed as NEAR's lakehouse does), the same as unreleased vesting elsewhere;
    released-but-not-withdrawn stays circulating. lockup.near and contributors.near leave too (decision 3 on 6f8c86c).
    circulating_supply_lockup_rule = total_supply − LOCKED − those two accounts, from the latest
    weekly lockup read on or before the day and at most NEAR_LOCKUP_MAX_AGE_DAYS old. A day without such a read carries
    NEAR's own figure, and its source says so; a day with one names the read's date."""
    LOCKUP_AGE_AMBER.clear()
    for p in scoped_projects():
        if config.circulating_primary_metric(p["name"]) != "circulating_supply_lockup_rule":
            continue
        name = p["name"]
        fp = groups.get((name, "circulating_supply_first_party"))
        tot_g = groups.get((name, "total_supply"))
        s = lambda g: (None if g is None or g.empty else  # noqa: E731
                       g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
                       .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
        base, total, lk = s(fp), s(tot_g), s(groups.get((name, "near_lockup_locked_tokens")))
        # lockup.near + contributors.near out too (Jake 2026-10-12, decision 3 on 6f8c86c): a read at most
        # NEAR_LOCKUP_MAX_AGE_DAYS old; without one the day keeps NEAR's own figure (never a rule figure on a basis
        # missing a leg)
        fa = s(groups.get((name, "near_foundation_accounts_tokens")))
        days = (base.index if base is not None else pd.DatetimeIndex([]))
        if total is not None:
            days = days.union(total.index)
        if days.empty:
            continue
        # G18-1: NEAR's own figure stands in only BEFORE the first lockup read (the declared start, 2026-10-11). After it,
        # a day whose read is older than NEAR_LOCKUP_MAX_AGE_DAYS is NOT FORMED — never NEAR's own figure (another
        # basis: the buggy-code lockups uncorrected) and never CoinGecko's (config no_coingecko_fallback); the latest
        # day says so (BLOCKED, with the read's age), so the headline never changes basis unflagged
        first_read = lk.index.min() if lk is not None and not lk.empty else None
        unformed = []
        idx, vals, srcs, read_on = [], [], [], []
        for d in days:
            read = acc = None
            if lk is not None and fa is not None and total is not None and d in total.index:
                w = lk[(lk.index <= d) & (lk.index >= d - pd.Timedelta(days=NEAR_LOCKUP_MAX_AGE_DAYS))]
                read = (w.index[-1], float(w.iloc[-1])) if not w.empty else None
                w = fa[(fa.index <= d) & (fa.index >= d - pd.Timedelta(days=NEAR_LOCKUP_MAX_AGE_DAYS))]
                acc = float(w.iloc[-1]) if not w.empty else None
            if read is not None and acc is not None:
                idx.append(d)
                vals.append(float(total[d]) - read[1] - acc)
                srcs.append(f"derived:circulating_lockup_rule[total_supply − LOCKED − lockup.near − contributors.near, "
                            f"lockups read {read[0].date()}]")
                read_on.append(read)
            elif first_read is not None and d >= first_read:
                unformed.append(d)
            elif base is not None and d in base.index:
                idx.append(d)
                vals.append(float(base[d]))
                srcs.append("derived:circulating_lockup_rule[first_party; no lockup read in force]")
        # D3-1: A READ 11-30 DAYS OLD STILL FORMS THE RULE, AMBER — the reason names the read, its age and the most the
        # figure can be off by: circulating can move between reads by at most LOCKED itself (no rate is established)
        if idx and read_on:
            last_d, (rd, locked) = idx[-1], read_on[-1]
            age = (last_d - rd).days
            if age > NEAR_LOCKUP_FRESH_DAYS:
                tot = float(total[last_d]) if total is not None and last_d in total.index else None
                share = f", {locked / tot:.2%} of total supply" if tot else ""
                why = (f"lockups read {rd.date()}, {age} days old on {last_d.date()} (AMBER: a read up to "
                       f"{NEAR_LOCKUP_MAX_AGE_DAYS} days old forms NEAR's lockup rule, BLOCKED after) — between reads "
                       f"circulating can move by at most LOCKED itself ({locked:,.0f} NEAR{share})")
                for m in ("circulating_supply_lockup_rule", "circulating_supply_used", "market_cap_usd"):
                    LOCKUP_AGE_AMBER[(name, m)] = why
        import credibility as _cred                       # noqa: PLC0415
        carry = int(_cred.now_max_age(name, "circulating_supply_lockup_rule"))
        if unformed and unformed[-1] == days.max() and not any(
                d >= days.max() - pd.Timedelta(days=carry) for d in idx):
            last = lk.index.max()
            age = (days.max() - last).days
            why = (f"BLOCKED — NEAR'S LOCKUP RULE CANNOT FORM ON {days.max().date()}: the last weekly lockup read is "
                   f"{last.date()}, {age} days old (the rule takes a read at most {NEAR_LOCKUP_MAX_AGE_DAYS} days old); "
                   f"no CoinGecko or first-party figure stands in (another basis). Run `python near_lockups_weekly.py "
                   f"--force` (~90 min, resumable; the \"Crypto-Tracker NEAR lockups\" task runs it each Sunday)")
            for m in ("circulating_supply_lockup_rule", "circulating_supply_used", "market_cap_usd"):
                LOCKUP_AGE_AMBER.pop((name, m), None)
            for m in ("circulating_supply_lockup_rule", "circulating_supply_used", "market_cap_usd"):
                _VIEW_BLOCKS[(name, m)] = why
        if not vals:
            continue
        cols = (fp if fp is not None else tot_g).columns
        groups[(name, "circulating_supply_lockup_rule")] = _as_stored(pd.DataFrame(
            {"date": idx, "project": name, "metric": "circulating_supply_lockup_rule", "value": vals,
             "tier": 1, "source": srcs}), cols)


def _epoch_primary_views(groups: dict, asof=None) -> None:
    """D4-5 (Jake 2026-10-27, decision 5 on D3): A PROJECT'S OWN PER-EPOCH FIGURE IS THE PRIMARY BASIS (config.EPOCH_PRIMARY;
    Pendle's fees, revenue and holders revenue). Each complete epoch's figure — its `epoch` series plus every `plus` leg
    stored for that epoch — is spread evenly over the epoch's days (source pendle_api:epoch_spread[...], naming the epoch,
    its legs and any leg not tracked that epoch). The project's DAILY cross-check (DefiLlama, stored under `daily`) stands
    only where no epoch is published: before the first stored epoch (its own source, "defillama") and after the last
    complete one — the epoch in progress (defillama:in_progress_epoch[...]). An epoch ends where the next stored one
    begins, at most `epoch_days`; a missing epoch between two stored ones leaves its days unformed — never a daily figure
    in the middle of the epoch leg (unknown is never 0). The three legs are a declared handover (config series_handover). With no
    epoch stored the cross-check stands alone, as stored."""
    for p in scoped_projects():
        spec = config.EPOCH_PRIMARY.get(p["name"])
        if not spec:
            continue
        name, n = p["name"], int(spec["epoch_days"])
        for metric, leg in spec["series"].items():
            dlg = groups.get((name, leg["daily"]))
            ep = _daily(groups.get((name, leg["epoch"])))
            base = dlg if dlg is not None and not dlg.empty else groups.get((name, leg["epoch"]))
            if base is None or base.empty:
                continue
            dl = (dlg.assign(date=pd.to_datetime(dlg["date"]).dt.normalize()).sort_values("date")
                  .drop_duplicates("date", keep="last") if dlg is not None and not dlg.empty else None)
            rows, kept = [], sorted(ep.index)
            # AN EPOCH ENDS WHERE THE NEXT BEGINS, at most `n` days (Pendle's first sPENDLE epoch, 2026-01-29, ran 12 days
            # to 02-10); a longer gap before the next stored epoch is days no epoch covers — left unformed
            length = {st: min(n, (nx - st).days) for st, nx in zip(kept, kept[1:])}
            if kept:
                length[kept[-1]] = n
            plus = {m: _daily(groups.get((name, m))) for m in leg.get("plus", ())}
            for st in kept:
                k = length[st]
                v, parts, untracked = float(ep[st]), [f"{leg['epoch']} {float(ep[st]):,.0f}"], []
                for m, ser in plus.items():
                    if st in ser.index:
                        v += float(ser[st])
                        parts.append(f"{m} {float(ser[st]):,.0f}")
                    else:
                        untracked.append(m)
                src = (f"pendle_api:epoch_spread[epoch {st.date()}: {' + '.join(parts)}, spread evenly over its {k} days"
                       + (f"; not tracked this epoch: {', '.join(untracked)}" if untracked else "") + "]")
                for d in pd.date_range(st, periods=k):
                    rows.append((d, v / k, src[:500]))
            if dl is not None:
                first = kept[0] if kept else None
                last_end = kept[-1] + pd.Timedelta(days=length[kept[-1]] - 1) if kept else None
                for d, v, s_ in zip(dl["date"], dl["value"].astype(float), dl["source"].astype(str)):
                    if first is None or d < first:
                        rows.append((d, v, s_))
                    elif d > last_end and (asof is None or d <= pd.Timestamp(asof)):
                        rows.append((d, v, f"defillama:in_progress_epoch[{spec.get('daily_label', leg['daily'])} "
                                           f"({leg['daily']}) for the epoch from {(last_end + pd.Timedelta(days=1)).date()}"
                                           f", until it closes and is published]"))
            if not rows:
                continue
            rows.sort(key=lambda r: r[0])
            groups[(name, metric)] = _as_stored(pd.DataFrame(
                {"date": [r[0] for r in rows], "project": name, "metric": metric, "value": [r[1] for r in rows],
                 "tier": 1, "source": [r[2] for r in rows]}), base.columns)


def _daily(g) -> pd.Series:
    """A stored or view frame as one value per day (the last row of a day), float."""
    if g is None or g.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
    return g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)


def _total_supply_net_views(groups: dict) -> None:
    """TOTAL SUPPLY, OURS, NET OF WHAT IS BURNED (Round G2, Jake 2026-10-18 — D2 Hyperliquid, L3 GEODNET;
    config.TOTAL_SUPPLY_NET): per day, the total leg − each burned leg, only on days every leg holds a row (a missing leg
    is not a total). CoinGecko's own total stays beside it as total_supply_coingecko; a day before our first total keeps
    CoinGecko's row, its source naming it, as FDV keeps CoinGecko's FDV."""
    for p in scoped_projects():
        spec = config.TOTAL_SUPPLY_NET.get(p["name"])
        if not spec:
            continue
        name = p["name"]
        legs = [spec["total"], *spec["less"]]
        ss = [_daily(groups.get((name, m))) for m in legs]
        cg = groups.get((name, "total_supply"))
        if cg is not None and not cg.empty:
            prev = groups.get((name, "total_supply_coingecko"))
            moved = cg.assign(metric="total_supply_coingecko")
            groups[(name, "total_supply_coingecko")] = _as_stored(
                moved if prev is None or prev.empty else pd.concat([prev, moved]).drop_duplicates("date", keep="first"),
                cg.columns)
        missing = [m for m, s_ in zip(legs, ss) if s_.empty]
        if missing:
            # NOT CoinGecko's figure in its place: that is the gross basis (the burns inside it), another quantity —
            # FDV and every ratio over the total read BLOCKED too until the leg is stored (G2-D2: --seed hyperevm_burn)
            _VIEW_BLOCKS[(name, "total_supply")] = ("BLOCKED — the net total supply is not formed: no rows stored for "
                                                    + ", ".join(missing))
            groups.pop((name, "total_supply"), None)
            continue
        df = pd.concat(ss, axis=1).dropna()
        if df.empty:
            continue
        net = df.iloc[:, 0] - df.iloc[:, 1:].sum(axis=1)
        cols = next(g for g in (groups.get((name, m)) for m in legs) if g is not None).columns
        view = pd.DataFrame({"date": net.index, "project": name, "metric": "total_supply", "value": net.values,
                             "tier": 1, "source": f"derived:{spec['total']}-{'-'.join(spec['less'])}"})
        cgt = groups.get((name, "total_supply_coingecko"))
        if cgt is not None and not cgt.empty:
            cgd = cgt.assign(date=pd.to_datetime(cgt["date"]).dt.normalize())
            early = cgd[cgd["date"] < net.index.min()]
            if not early.empty:
                view = pd.concat([early.assign(metric="total_supply")[view.columns.intersection(early.columns)], view],
                                 ignore_index=True)
        groups[(name, "total_supply")] = _as_stored(view.sort_values("date"), cols)


def _wm_total_supply_views(groups: dict) -> None:
    """WORLD MOBILE'S TOTAL SUPPLY = OPTION C (Round G7 0a, Jake 2026-10-19; config <project>.total_supply_option_c):
    per day where both are read, B = total_supply_gross (the EVM chains + Solana) + wmtx_cardano_supply_tokens, stored as
    its own row total_supply_with_cardano_tokens; C = B + the Ethereum holders' supply not yet re-issued (from the
    exploit day; before it, Ethereum is inside total_supply_gross) = Ethereum totalSupply at the block before the first
    counterfeit mint − the re-issue confirmed on Base — or World Mobile's published snapshot total in its place once one
    exists. C is total_supply (FDV reads it); the stored total keeps the days before C's first, CoinGecko's moves beside
    it as total_supply_coingecko."""
    for p in scoped_projects():
        spec = p.get("total_supply_option_c")
        if not spec:
            continue
        name = p["name"]
        gross, card = _daily(groups.get((name, "total_supply_gross"))), _daily(groups.get((name, "wmtx_cardano_supply_tokens")))
        both = pd.concat([gross.rename("g"), card.rename("c")], axis=1).dropna()
        if both.empty:
            continue
        cols = groups[(name, "total_supply_gross")].columns
        b = both["g"] + both["c"]
        groups[(name, "total_supply_with_cardano_tokens")] = _as_stored(pd.DataFrame({
            "date": b.index, "project": name, "metric": "total_supply_with_cardano_tokens", "value": b.values, "tier": 1,
            "source": "derived:total_supply_gross+wmtx_cardano_supply_tokens[option B: the chains read]"}), cols)
        if spec.get("snapshot_total") is not None:
            leg = float(spec["snapshot_total"]) - float(spec["reissue_confirmed"])
            how = (f"World Mobile's published snapshot total {float(spec['snapshot_total']):,.6f} ({spec.get('snapshot_source')})"
                   f" − re-issue confirmed on Base {float(spec['reissue_confirmed']):,.6f}")
        else:
            leg = float(spec["eth_pre_exploit"]) - float(spec["reissue_confirmed"])
            how = (f"{spec['label']}: Ethereum totalSupply {float(spec['eth_pre_exploit']):,.6f} at block "
                   f"{int(spec['eth_block']):,} − re-issue confirmed on Base {float(spec['reissue_confirmed']):,.6f}; "
                   f"{spec['recovery_mints_note']}")
        after = b.index >= pd.Timestamp(spec["from"])
        c = b + leg * after
        view = pd.DataFrame({"date": b.index, "project": name, "metric": "total_supply", "value": c.values, "tier": 1,
                             "source": [f"derived:option_c[{how}]" if a else "derived:option_c[before the exploit: "
                                        "Ethereum inside total_supply_gross]" for a in after]})
        old = groups.get((name, "total_supply"))
        if old is not None and not old.empty:
            prev = groups.get((name, "total_supply_coingecko"))
            moved = old[old["source"].astype(str).str.startswith("coingecko")].assign(metric="total_supply_coingecko")
            if not moved.empty:
                groups[(name, "total_supply_coingecko")] = _as_stored(
                    moved if prev is None or prev.empty else pd.concat([prev, moved]).drop_duplicates("date", keep="first"),
                    old.columns)
            od = old.assign(date=pd.to_datetime(old["date"]).dt.normalize())
            early = od[od["date"] < b.index.min()]
            if not early.empty:
                view = pd.concat([early[view.columns.intersection(early.columns)], view], ignore_index=True)
        groups[(name, "total_supply")] = _as_stored(view.sort_values("date"), cols)


def _revenue_total_views(groups: dict) -> None:
    """REVENUE FROM EVERY STREAM (alignment round 2026-10-13, C10; config.REVENUE_TOTAL): revenue_total_usd = the sum
    of the streams per day, only on days every stream holds a row — a missing stream is a gap, never a smaller total."""
    for p in scoped_projects():
        name = p["name"]
        for metric, legs in (("revenue_total_usd", config.REVENUE_TOTAL.get(name)),
                             ("fees_total_usd", config.FEES_TOTAL.get(name))):
            if not legs:
                continue
            ss = [_daily(groups.get((name, m))) for m in legs]
            missing = [m for m, s_ in zip(legs, ss) if s_.empty]
            if missing:
                _VIEW_BLOCKS[(name, metric)] = "BLOCKED — no rows stored for " + ", ".join(missing)
                continue
            # A LEG THAT HAD NOT STARTED IS 0 BEFORE ITS START (Round G2 D4: AQAv2 pays from 2026-08-26 — config
            # REVENUE_LEG_FROM), on the other legs' days only; after its start a missing day is a gap as before
            ss = [s_ if (name, m) not in config.REVENUE_LEG_FROM else
                  pd.concat([pd.Series(0.0, index=[d for d in ss[0].index
                                                   if d < pd.Timestamp(config.REVENUE_LEG_FROM[(name, m)])]), s_])
                  for m, s_ in zip(legs, ss)]
            tot = pd.concat(ss, axis=1).dropna().sum(axis=1)
            if tot.empty:
                continue
            cols = next(g for g in (groups.get((name, m)) for m in legs) if g is not None).columns
            groups[(name, metric)] = _as_stored(pd.DataFrame(
                {"date": tot.index, "project": name, "metric": metric, "value": tot.values, "tier": 1,
                 "source": f"derived:{metric.removesuffix('_usd')}[{'+'.join(legs)}]"}), cols)


def _stake_prorata_views(groups: dict) -> None:
    """REWARDS TO CIRCULATING STAKE (alignment round 2026-10-13, C1; config.STAKE_PRORATA): per day, emissions x
    (staked − staked by the wallets outside circulating) ÷ staked, only on days all three series hold a row. Hyperliquid's
    outflow (a): the team / Foundation / Grants stake earns rewards that stay outside circulating."""
    for p in scoped_projects():
        spec = config.STAKE_PRORATA.get(p["name"])
        if not spec:
            continue
        name = p["name"]
        e, st, out = (_daily(groups.get((name, spec[k]))) for k in ("emissions", "staked", "outside"))
        if e.empty or st.empty or out.empty:
            missing = [spec[k] for k, s_ in (("emissions", e), ("staked", st), ("outside", out)) if s_.empty]
            _VIEW_BLOCKS[(name, spec["metric"])] = "BLOCKED — no rows stored for " + ", ".join(missing)
            continue
        df = pd.concat([e.rename("e"), st.rename("s"), out.rename("o")], axis=1).dropna()
        df = df[df["s"] > 0]
        v = (df["e"] * (df["s"] - df["o"]).clip(lower=0.0) / df["s"])
        if v.empty:
            _VIEW_BLOCKS[(name, spec["metric"])] = "BLOCKED — emissions, total stake and outside stake share no day"
            continue
        cols = next(g for g in (groups.get((name, spec[k])) for k in ("emissions", "staked")) if g is not None).columns
        groups[(name, spec["metric"])] = _as_stored(pd.DataFrame(
            {"date": v.index, "project": name, "metric": spec["metric"], "value": v.values, "tier": 2,
             "source": f"derived:stake_prorata[{spec['emissions']}x({spec['staked']}-{spec['outside']})/"
                       f"{spec['staked']}]"}), cols)


def _cashback_funding_views(groups: dict) -> None:
    """config.CASHBACK_FUNDING (round 3 W3; FR2 S2): the payouts a programme's market purchases had not yet covered —
    the rise in the running maximum of max(0, Σ paid − Σ bought) — written as `out`, the dilutive part. Before `since`
    nothing was bought (a payout then is all from stock); from it a day needs a row of both series (a gap, not a 0)."""
    for name, spec in config.CASHBACK_FUNDING.items():
        paid, bought = _daily(groups.get((name, spec["paid"]))), _daily(groups.get((name, spec["bought"])))
        if paid.empty or bought.empty:
            continue
        since = pd.Timestamp(spec["since"])
        # before the programme nothing was bought: a payout then came out of stock in full
        b = bought.reindex(paid.index.union(bought.index))
        b[(b.index < since) & b.isna()] = 0.0
        both = pd.concat([paid.rename("p"), b.rename("b")], axis=1).dropna()
        if both.empty:
            continue
        gap = (both["p"].cumsum() - both["b"].cumsum()).clip(lower=0.0).cummax()
        out = gap.diff().fillna(gap.iloc[0])
        cols = groups[(name, spec["paid"])].columns
        groups[(name, spec["out"])] = _as_stored(pd.DataFrame(
            {"date": out.index, "project": name, "metric": spec["out"], "value": out.values, "tier": 2,
             "source": f"derived:cashback_funding[max(0,Σ{spec['paid']}−Σ{spec['bought']}) since {spec['since']}]"}),
            cols)


EVIDENCE_SRC = "evidence:"          # the measuring point of a step-2/3/4 leg: evidence:<step>:<metric>
# (project, metric) -> {"label", "note", "verdict": VERIFIED | CHECK | LIVE-UNVERIFIED, "check": text, "days": n}
EVIDENCE_RESULTS: dict = {}


def _evidence_check(e: dict, filled: pd.Series, groups: dict, name: str, asof) -> tuple[str, str]:
    """(verdict, text) of an entry's on-chain CHECK over the Q0..Q-4 blocks both cover (SPEC §1 rule 13)."""
    c = e.get("check")
    if not c:
        return "LIVE-UNVERIFIED", "no on-chain check exists for this flow"
    chk = _daily(groups.get((name, c["metric"])))
    if chk.empty or filled.empty or asof is None:
        return "LIVE-UNVERIFIED", f"check {c['metric']}: nothing stored to compare"
    tol, rel, parts, bad, n = float(c.get("tolerance", 0.05)), c.get("relation", "equal"), [], [], 0
    for k in range(5):
        a, b = _flow_window(asof, k)
        v, nv = _flow_sum(filled, a, b)
        w, nw = _flow_sum(chk, a, b)
        if v is None or w is None or not nv or not nw:
            continue
        # A QUARTER THE CHECK DOES NOT SPAN IS NOT JUDGED: its series must start by the block's first day and reach its
        # last month (a monthly step series' last row may sit up to a month before the block's end)
        if chk.index.min() > a + pd.Timedelta(days=1) or chk.index.max() < b - pd.Timedelta(days=31):
            continue
        n += 1
        ok = ((w <= v * (1 + tol)) if rel == "lower_bound" else (v <= w * (1 + tol)) if rel == "upper_bound"
              else (abs(w / v - 1) <= tol if v else w == 0))
        parts.append(f"{trajectory.LABELS[k]} {v:,.0f} vs check {w:,.0f}")
        if not ok:
            bad.append(trajectory.LABELS[k])
    if not n:
        return "LIVE-UNVERIFIED", f"check {c['metric']}: no quarter holds both"
    what = (f"{c['metric']} ({c.get('what', '')}) as "
            + {"lower_bound": "a lower bound", "upper_bound": "an upper bound"}.get(rel, "a reconciliation")
            + f" within {tol:.0%}: " + "; ".join(parts))
    if bad:
        return "CHECK", f"{what} — DISAGREES in {', '.join(bad)}"
    # VERIFIED ONLY BY AN INDEPENDENT MEASUREMENT (SPEC §1 rule 13: "the chain check agrees"): a bound or a
    # reconciliation against another published figure agrees, and says so, but grades LIVE-UNVERIFIED
    return ("VERIFIED" if c.get("onchain", False) else "LIVE-UNVERIFIED"), what + " — agrees"


def evidence_on(name: str, metric: str) -> dict | None:
    """The EVIDENCE_RESULTS entry a series reads — its own, or (an outflow_<c>_tokens/usd view) one of its category's
    legs' (G9 N). None when the series is wholly on-chain."""
    if (name, metric) in EVIDENCE_RESULTS:
        return EVIDENCE_RESULTS[(name, metric)]
    m = re.match(r"outflow_([a-e])_(?:tokens|usd)$", str(metric or ""))
    if m:
        for leg in config.outflow_metrics(name, m.group(1)) or ():
            if (name, leg) in EVIDENCE_RESULTS:
                return EVIDENCE_RESULTS[(name, leg)]
    return None


def _revenue_from_fees_views(groups: dict) -> None:
    """G9 N (config.REVENUE_FROM_FEES): a project whose every fee is received by the protocol (Plume: the whole L2 base
    fee to its RewardDistributor) has revenue = its measured fees, day by day, where no revenue row is stored; the check
    against an aggregator's fee figure is said on the row."""
    asof = pd.Timestamp(_WINDOWS["q0"][1]) if _WINDOWS.get("q0") else None
    for name, spec in config.REVENUE_FROM_FEES.items():
        if not any(p["name"] == name for p in scoped_projects()):
            continue
        f, have = groups.get((name, spec["fees"])), groups.get((name, spec["revenue"]))
        if f is None or f.empty or (have is not None and not have.empty):
            continue
        groups[(name, spec["revenue"])] = f.assign(metric=spec["revenue"], source=f"derived:revenue_from_fees["
                                                   f"{spec['fees']}: {spec['why']}]")
        verdict, chk = _evidence_check({"check": spec.get("check")}, _daily(f), groups, name, asof)
        EVIDENCE_RESULTS[(name, spec["revenue"])] = {
            "label": f"ON-CHAIN: revenue = {spec['fees']} ({spec['why']})", "verdict": verdict, "check": chk,
            "days": len(f), "note": f"ON-CHAIN: revenue = {spec['fees']} — {spec['why']}. CHECK: {chk}"}


def _pendle_rate_check(groups: dict) -> None:
    """G9 N (config.PENDLE_RATE_CHECK): per gauge chain, the log scan's PENDLE (emissions_tokens_gauge_<chain>) against
    the sampled rates' (emissions_tokens_gauge_rate_<chain>) over their common days — total ÷ total within tolerance on
    min_days days → VERIFIED, else CHECK with both figures; said on the scan's Data row. A chain whose scan holds nothing
    shows the rate series as its only figure (its own row)."""
    o = config.PENDLE_RATE_CHECK
    for c in o["chains"]:
        scan_m = "emissions_tokens_gauge_mainnet" if c == "ethereum" else f"emissions_tokens_gauge_{c}"
        rate, scan = _daily(groups.get(("Pendle", f"emissions_tokens_gauge_rate_{c}"))), _daily(groups.get(("Pendle", scan_m)))
        if rate.empty:
            continue
        common = rate.index.intersection(scan.index)
        if scan.empty or len(common) < int(o["min_days"]):
            EVIDENCE_RESULTS[("Pendle", scan_m)] = {
                "label": "ON-CHAIN (sampled rate)", "verdict": "LIVE-UNVERIFIED", "days": len(common), "note": "",
                "check": (f"{c}: the sampled rates hold {len(rate)} day(s), the log scan " +
                          ("nothing" if scan.empty else f"{len(common)} common day(s) (needs {o['min_days']})"))}
            continue
        a, b = float(scan[common].sum()), float(rate[common].sum())
        ok = (abs(a / b - 1) <= float(o["tolerance"])) if b else a == 0
        EVIDENCE_RESULTS[("Pendle", scan_m)] = {
            "label": "ON-CHAIN (log scan; sampled rate the check)", "verdict": "VERIFIED" if ok else "CHECK",
            "days": len(common), "note": "",
            "check": (f"{c} {common.min().date()}..{common.max().date()} ({len(common)} days): log scan {a:,.1f} vs "
                      f"rate x seconds {b:,.1f} PENDLE" + (f" — agrees within {float(o['tolerance']):.0%}" if ok
                                                          else " — DISAGREES"))}


def _treasury_split_views(groups: dict) -> None:
    """G9 L3 (config.TREASURY_SHARE_SPLIT): the validators' part of the mint = the measured mint less the treasury's
    MEASURED inflow that day; on a day the treasury read does not cover, less `rate` of the mint (MECHANISM: the protocol
    constant x the measured mint), the day's source says which. The measured days are the mechanism's check."""
    for name, sp in config.TREASURY_SHARE_SPLIT.items():
        if not any(p["name"] == name for p in scoped_projects()):
            continue
        if (name, sp["gross"]) in _VIEW_BLOCKS:         # a blocked mint blocks both of its parts, with its reason
            for m in (sp["out"], sp["m_view"]):
                _VIEW_BLOCKS[(name, m)] = _VIEW_BLOCKS[(name, sp["gross"])]
            continue
        gross, tr = _daily(groups.get((name, sp["gross"]))), _daily(groups.get((name, sp["treasury"])))
        if gross.empty:
            continue
        rate = float(sp["rate"])
        rows = []
        for d, v in gross.items():
            if d in tr.index:
                rows.append((d, v - float(tr[d]), f"derived:validator_rewards[{sp['gross']} − {sp['treasury']} (measured)]"))
            else:
                rows.append((d, v * (1 - rate), f"derived:validator_rewards[{sp['gross']} x (1 − {rate:g}): MECHANISM "
                                                f"{sp['mechanism']} x the measured mint — the treasury read does not "
                                                f"cover this day]"))
        g = groups[(name, sp["gross"])]
        if sp.get("emissions") and (groups.get((name, "emissions_tokens")) is None
                                    or groups[(name, "emissions_tokens")].empty):
            # G10 1: emissions_model "minted" — the emissions ARE the mint to validators; nothing stores emissions_tokens
            # for NEAR, so it is the measured mint less the treasury's share (the same rows as line (a))
            groups[(name, "emissions_tokens")] = _as_stored(pd.DataFrame(
                {"date": [r[0] for r in rows], "project": name, "metric": "emissions_tokens",
                 "value": [r[1] for r in rows], "tier": 1, "source": [r[2] for r in rows]}), g.columns)
        groups[(name, sp["out"])] = _as_stored(pd.DataFrame(
            {"date": [r[0] for r in rows], "project": name, "metric": sp["out"], "value": [r[1] for r in rows],
             "tier": 1, "source": [r[2] for r in rows]}), g.columns)
        # LINE m, THE SAME DAYS (counted once, never dropped): the measured inflow where read, else the same constant x
        # the measured mint — so the treasury's share leaves (a) only on a day it is in m
        mrows = [(d, float(tr[d]) if d in tr.index else v * rate,
                  f"derived:treasury_mint[{sp['treasury']} (measured)]" if d in tr.index else
                  f"derived:treasury_mint[{rate:g} x {sp['gross']}: MECHANISM {sp['mechanism']}]")
                 for d, v in gross.items()]
        groups[(name, sp["m_view"])] = _as_stored(pd.DataFrame(
            {"date": [r[0] for r in mrows], "project": name, "metric": sp["m_view"], "value": [r[1] for r in mrows],
             "tier": 1, "source": [r[2] for r in mrows]}), g.columns)
        both = gross.index.intersection(tr.index)
        if len(both):
            share = float(tr[both].sum()) / float(gross[both].sum())
            chk = (f"{sp['treasury']} ÷ {sp['gross']} over {len(both)} measured day(s) = {share:.4f} (the protocol "
                   f"constant {rate:g} of the epoch's MAXIMUM reward; validators earn slightly less than 1 − {rate:g})")
            EVIDENCE_RESULTS[(name, sp["out"])] = {
                "label": "ON-CHAIN (mint − measured treasury inflow; the protocol constant where not yet read)",
                "verdict": "VERIFIED" if abs(share - rate) <= 0.002 else "CHECK", "check": chk, "days": len(both),
                "note": ""}


def _ccip_fee_overlap_view(groups: dict) -> None:
    """G9 K4 (config.CCIP_FEE_OVERLAP): the per-chain CCIP 2.0 fee read from api.ccip.chain.link's message detail is
    checked against the same chain's OnRamp logs (Arbitrum) over their common days — total API ÷ total log within the
    tolerance on at least min_days days — and only then is the Base leg (no free log route) added to the log legs' total,
    day by day where both hold. A failed or too-short check builds nothing and says why (CHECK, both figures)."""
    o = config.CCIP_FEE_OVERLAP
    name = o["project"]
    if not any(p["name"] == name for p in scoped_projects()):
        return
    api, log_, leg, into = (_daily(groups.get((name, o[k]))) for k in ("api", "log", "leg", "into"))
    common = api.index.intersection(log_.index)
    if len(common) < int(o["min_days"]) or not float(log_[common].sum()):
        _VIEW_BLOCKS[(name, o["out"])] = (f"NOT FORMED — the Arbitrum overlap holds {len(common)} common day(s) "
                                          f"(needs {o['min_days']})")
        return
    r = float(api[common].sum()) / float(log_[common].sum())
    chk = (f"Arbitrum overlap {common.min().date()}..{common.max().date()} ({len(common)} days): message detail "
           f"${float(api[common].sum()):,.0f} vs OnRamp logs ${float(log_[common].sum()):,.0f} = {r:.4f}")
    ok = abs(r - 1) <= float(o["tolerance"])
    EVIDENCE_RESULTS[(name, o["out"])] = {
        "label": "ON-CHAIN (log legs) + PUBLISHED: api.ccip.chain.link message detail (Base)",
        "verdict": "VERIFIED" if ok else "CHECK", "days": len(common),
        "check": chk + (f" — agrees within {float(o['tolerance']):.0%}" if ok else
                        f" — DISAGREES beyond {float(o['tolerance']):.0%}: the Base leg is not added"),
        "note": ""}
    if not ok:
        _VIEW_BLOCKS[(name, o["out"])] = "CHECK — " + EVIDENCE_RESULTS[(name, o["out"])]["check"]
        return
    both = into.index.intersection(leg.index)
    if both.empty:
        return
    g = groups.get((name, o["into"]))
    tot = into[both] + leg[both]
    groups[(name, o["out"])] = _as_stored(pd.DataFrame(
        {"date": both, "project": name, "metric": o["out"], "value": tot.values, "tier": 2,
         "source": f"derived:ccip_v2_fees_with_base[{o['into']} + {o['leg']}; {chk}]"}), g.columns)


def _evidence_views(groups: dict, usd: bool) -> None:
    """THE EVIDENCE LADDER (G9 N; config.EVIDENCE_LADDER): each published period's figure spread evenly over its days
    into the target series, on the days no stored row covers — tagged with its step and source. A target holding stored
    rows inside an entry's span is left to them (superseded, said in EVIDENCE_RESULTS)."""
    asof = pd.Timestamp(_WINDOWS["q0"][1]) if _WINDOWS.get("q0") else None
    for (name, metric), e in config.EVIDENCE_LADDER.items():
        if (METRICS.get(metric, {}).get("unit") == "usd") != usd:
            continue
        if not any(p["name"] == name for p in scoped_projects()):
            continue
        g = groups.get((name, metric))
        days, quotes = {}, []
        for per in e["periods"]:
            span = pd.date_range(per["from"], per["to"])
            for d_ in span:
                days[d_] = days.get(d_, 0.0) + float(per["value"]) / len(span)
            quotes.append(f"{per['from']}..{per['to']} {per['value']:,.2f}")
        if not days:
            continue
        lo, hi = min(days), max(days)
        stored = pd.Series(dtype=float)
        if g is not None and not g.empty:
            own = g[~g["source"].astype(str).str.startswith(EVIDENCE_SRC)]
            stored = own[(pd.to_datetime(own["date"]).dt.normalize() >= lo)
                         & (pd.to_datetime(own["date"]).dt.normalize() <= hi)]
            if not stored.empty:
                EVIDENCE_RESULTS[(name, metric)] = {
                    "label": config.evidence_label(e), "verdict": "superseded",
                    "note": f"{config.evidence_label(e)} not used: {len(stored)} stored row(s) of {metric} inside "
                            f"{lo.date()}..{hi.date()} — the measured leg stands", "check": "", "days": 0}
                continue
        slug = e["step"].split(" ")[0].lower()
        src = (f"{EVIDENCE_SRC}{slug}:{metric}[{config.evidence_label(e)}, read {e['read_on']}; "
               f"{e.get('spread', 'spread evenly over each published period')}]")
        s_ = pd.Series(days).sort_index()
        if asof is not None:
            s_ = s_[s_.index <= asof]                   # a published schedule's FUTURE days are not flows yet
        if s_.empty:
            continue
        cols = next((g_.columns for (n_, _m), g_ in groups.items() if n_ == name and g_ is not None and not g_.empty),
                    None)
        if cols is None:
            continue
        view = _as_stored(pd.DataFrame({"date": s_.index, "project": name, "metric": metric, "value": s_.values,
                                        "tier": int(e.get("tier", 2)), "source": src}), cols)
        groups[(name, metric)] = view if g is None or g.empty else pd.concat([g, view], ignore_index=True)
        _VIEW_BLOCKS.pop((name, metric), None)          # a step-2/3/4 value is a value: nothing withholds it
        verdict, chk = _evidence_check(e, s_, groups, name, asof)
        EVIDENCE_RESULTS[(name, metric)] = {
            "label": config.evidence_label(e), "verdict": verdict, "check": chk, "days": len(s_),
            "from": str(s_.index.min().date()), "to": str(s_.index.max().date()),
            "note": (f"{config.evidence_label(e)} (read {e['read_on']}; {e.get('url', '')}) — "
                     f"{e.get('spread', 'each published period spread evenly over its days')}: "
                     f"{'; '.join(quotes)}; a 90-day block is the pro-rata blend of the periods it overlaps. "
                     f"CHECK: {chk}")}


def _outflow_views(groups: dict) -> None:
    """THE FIVE OUTFLOW CATEGORIES (Jake 2026-10-12, Phase 2 amendment 3): per project and category (config
    OUTFLOW_CATEGORIES), outflow_<c>_tokens = the category's series summed per day — a day only where EVERY series in
    the category has a row (a missing leg is a gap, never a smaller outflow) — and outflow_<c>_usd = that x the day's
    price. Read-time views; the source names the series and the category's basis is on Config & Sources / METRICS.md."""
    for p in scoped_projects():
        name = p["name"]
        price = _daily(groups.get((name, "price_usd")))
        for cat in config.OUTFLOW_CATEGORY_NAMES:
            ms = config.outflow_metrics(name, cat)
            if not ms:
                continue
            legs = [_daily(groups.get((name, m))) for m in ms]
            # A LEG THAT IS BLOCKED OR EMPTY BLOCKS THE CATEGORY, WITH ITS REASON (Jake's run on 3500042, item 24: CP
            # NET HOLDER YIELD read a bare n/a for Hyperliquid and NEAR while holder value was ok — a missing outflow
            # leg left no outflow row, so the formula errored silently). The outflow rows now say which leg and why.
            why = [f"{m}: {_VIEW_BLOCKS[(name, m)]}" if (name, m) in _VIEW_BLOCKS else f"{m}: no rows stored"
                   for m, leg in zip(ms, legs) if (name, m) in _VIEW_BLOCKS or leg.empty]
            if why:
                for unit in ("tokens", "usd"):
                    _VIEW_BLOCKS[(name, f"outflow_{cat}_{unit}")] = (
                        f"BLOCKED — outflow ({cat}) is not formed: " + "; ".join(why))
                continue
            tot = pd.concat(legs, axis=1).dropna().sum(axis=1)
            if tot.empty:
                continue
            cols = next(g for g in (groups.get((name, m)) for m in ms) if g is not None).columns
            src = f"derived:outflow_{cat}[{'+'.join(ms)}]"
            groups[(name, f"outflow_{cat}_tokens")] = _as_stored(pd.DataFrame(
                {"date": tot.index, "project": name, "metric": f"outflow_{cat}_tokens", "value": tot.values, "tier": 2,
                 "source": src}), cols)
            usd = (tot * price.reindex(tot.index)).dropna()
            if not usd.empty:
                groups[(name, f"outflow_{cat}_usd")] = _as_stored(pd.DataFrame(
                    {"date": usd.index, "project": name, "metric": f"outflow_{cat}_usd", "value": usd.values,
                     "tier": 2, "source": src + "*price"}), cols)


def _holder_value_views(groups: dict) -> None:
    """HOLDER VALUE, FUNDING-AWARE (Jake 2026-10-12, Phase 2 item 3 and LITMUS A): per day, $ retired (retired_buyback_usd,
    burned + held) + $ distributed (actual_buyback_usd − retired_buyback_usd, the bought tokens paid out) + the declared
    non-buyback payouts to holders (config HOLDER_VALUE distributed_extra). Each day's BUYBACK value is credited only
    where its programme was revenue-funded on that day (config.holder_funding) — holder_value_revenue_usd; treasury- or
    emissions-funded value is recorded as holder_value_other_usd and never credited. A declared payout to holders out of
    revenue (Aerodrome's voter fees, Sky's USDS to stakers) is credited.
    LIQUID BRIBES (Jake 2026-10-12 evening, decision 4; HOLDER_VALUE `bribes`): from the bribe series' first day, each
    epoch's bribes less its illiquid part join the credited figure (source derived:holder_value_with_liquid_bribes — a
    declared handover); `illiquid_less` takes the illiquid fee leg out (the illiquid-token rule). The same figure
    without the bribes is holder_value_fees_only_usd."""
    for p in scoped_projects():
        name = p["name"]
        spec = config.HOLDER_VALUE.get(name) or {}
        act = _daily(groups.get((name, "actual_buyback_usd")))
        extra = [_daily(groups.get((name, m))) for m in spec.get("distributed_extra", ())]
        if act.empty and not any(not e.empty for e in extra):
            continue
        # the whole buyback $ = retired (burned + held) + distributed: the funding decides whether it is credited
        credit, other = {}, {}
        for d, v in act.items():
            if config.holder_funding(name, d)[0] == "revenue":
                credit[d] = float(v)
            else:
                # 0 CREDITED, NOT n/a (fix round 2, R2: Ether.fi's HVY went from 0 to n/a once its Q0 top-ups were
                # recorded as treasury-funded — a day with a buyback that is not revenue-funded credits 0)
                other[d] = float(v)
                credit.setdefault(d, 0.0)
        for e in extra:
            for d, v in e.items():
                credit[d] = credit.get(d, 0.0) + float(v)
        for m in spec.get("illiquid_less", ()):
            for d, v in _daily(groups.get((name, m))).items():
                if d in credit:
                    credit[d] = max(0.0, credit[d] - float(v))
        fees_only = dict(credit)
        br, bribe_from = spec.get("bribes"), None
        if br:
            bribe_from = pd.Timestamp(br["from"])
            gross, ill = _daily(groups.get((name, br["gross"]))), _daily(groups.get((name, br["illiquid"])))
            for d, v in gross.items():
                if d >= bribe_from:
                    credit[d] = credit.get(d, 0.0) + max(0.0, float(v) - float(ill.get(d, 0.0)))
        cols = next(g for g in (groups.get((name, m)) for m in ("actual_buyback_usd", "fees_usd", "price_usd"))
                    if g is not None and not g.empty).columns

        def src_for(d, base):
            if bribe_from is not None and d >= bribe_from and base == "derived:holder_value[revenue-funded]":
                return "derived:holder_value_with_liquid_bribes[revenue-funded + liquid bribes]"
            return base
        series = [("holder_value_revenue_usd", credit, "derived:holder_value[revenue-funded]"),
                  ("holder_value_other_usd", other, "derived:holder_value[treasury/emissions-funded]")]
        if br:
            series.append(("holder_value_fees_only_usd", fees_only, "derived:holder_value_fees_only[revenue-funded]"))
        for metric, vals, src in series:
            if vals:
                s_ = pd.Series(vals).sort_index()
                groups[(name, metric)] = _as_stored(pd.DataFrame(
                    {"date": s_.index, "project": name, "metric": metric, "value": s_.values, "tier": 2,
                     "source": [src_for(d, src) for d in s_.index]}), cols)


def _reward_funding_views(groups: dict) -> None:
    """config.REWARD_FUNDING: the reward series split per day by its funding period into one series per funding (every
    day in each, 0 where another funding pays), so a category reads the same days as the reward itself."""
    for p in scoped_projects():
        name = p["name"]
        spec = config.REWARD_FUNDING.get(name)
        g = groups.get((name, spec["metric"])) if spec else None
        if g is None or g.empty:
            continue
        g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
        fund = [(config.reward_funding(name, d) or ("unfunded", ""))[0] for d in g["date"]]
        for funding, metric in spec["to"].items():
            vals = [float(v) if f == funding else 0.0 for v, f in zip(g["value"], fund)]
            groups[(name, metric)] = _as_stored(pd.DataFrame(
                {"date": g["date"].values, "project": name, "metric": metric, "value": vals, "tier": 2,
                 "source": f"derived:{spec['metric']}[{funding}-funded, config.REWARD_FUNDING]"}), g.columns)


def _issuance_from_supply_views(groups: dict) -> None:
    """G11 B2 (config.ISSUANCE_FROM_TOTAL_SUPPLY): gross_issuance_tokens = the rises of the stored total_supply between
    consecutive stored days at most max_gap_days apart (a fall is 0), where nothing stores gross issuance. Each mint day
    is compared with the declared mints (ISSUER_MINTS events) on its source."""
    for name, spec in config.ISSUANCE_FROM_TOTAL_SUPPLY.items():
        if not any(p["name"] == name for p in scoped_projects()):
            continue
        have = groups.get((name, "gross_issuance_tokens"))
        if spec.get("plus_burn"):
            _issuance_supply_plus_burn(groups, name, spec, have)
            continue
        if have is not None and not have.empty:
            continue
        ts = _daily(groups.get((name, "total_supply")))
        if len(ts) < 2:
            continue
        gap = ts.index.to_series().diff().dt.days
        rise = ts.diff().clip(lower=0.0)[(gap <= int(spec["max_gap_days"])).values].dropna()
        if rise.empty:
            continue
        declared = {pd.Timestamp(e["date"]): float(e["tokens"])
                    for e in (config.ISSUER_MINTS.get(name) or {}).get("events") or ()}
        g0 = groups[(name, "total_supply")]
        srcs = []
        for d, v in rise.items():
            m = next((t for dd, t in declared.items() if abs((d - dd).days) <= 2), None)
            chk = (f"; declared mint {m:,.0f} — {'agrees' if m and abs(v / m - 1) <= 0.01 else 'DIFFERS'}" if m and v
                   else "")
            srcs.append(f"derived:d_supply[total_supply rises; no burn — {spec['why'][:120]}{chk}]")
        groups[(name, "gross_issuance_tokens")] = _as_stored(pd.DataFrame(
            {"date": rise.index, "project": name, "metric": "gross_issuance_tokens", "value": rise.values, "tier": 2,
             "source": srcs}), g0.columns)


def _issuance_supply_plus_burn(groups: dict, name: str, spec: dict, have) -> None:
    """G11 C6 (Sky): gross issuance = d(total supply) + the burn that day, between consecutive stored days, where the
    token's burn decrements its total supply (config burn_mechanism protocol_level_destruction). The burn before its
    first possible day (spec burn_from) is 0 by the mechanism's record; a later day without a burn row is unknown and
    left out. A negative result (an impossible negative mint) is left out and named. Only days BEFORE the first stored
    gross_issuance_tokens row: the stored series stands from its first day (declared handover)."""
    ts = _daily(groups.get((name, spec.get("supply_metric", "total_supply"))))
    if len(ts) < 2:
        return
    burn = _daily(groups.get((name, spec["plus_burn"])))
    first = pd.to_datetime(have["date"]).min() if have is not None and not have.empty else None
    # THE READING DATED D IS D's OPENING where spec close_shift_days = 1 (Sky's archive read: the Stage 2 burn of
    # 2026-09-13 is in the reading dated 09-14) — so the day's change is the NEXT reading less this one
    shift = int(spec.get("close_shift_days", 0))
    nxt = ts.shift(-shift) if shift else ts
    prv = ts if shift else ts.shift(1)
    idx = ts.index.to_series()
    gap = (idx.shift(-shift) - idx).dt.days if shift else idx.diff().dt.days
    rows, odd = [], []
    for d, dv in (nxt - prv).items():
        if pd.isna(dv) or gap[d] != 1 or (first is not None and d >= first):
            continue
        if d in burn.index:
            b = float(burn[d])
        elif d < pd.Timestamp(spec["burn_from"]):
            b = 0.0
        else:
            continue                                    # a burn could have happened and is not stored: unknown
        v = float(dv) + b
        if v < -float(spec.get("tolerance_tokens", 1.0)):
            odd.append(f"{d.date()} {v:,.0f}")
            continue
        rows.append((d, max(v, 0.0)))
    if not rows:
        return
    src = (f"derived:d_supply[d({spec.get('supply_metric', 'total_supply')}) + {spec['plus_burn']}; {spec['why'][:120]}"
           + (f"; {len(odd)} negative day(s) left out: {', '.join(odd[:3])}" if odd else "") + "]")
    cols = groups[(name, spec.get("supply_metric", "total_supply"))].columns
    view = _as_stored(pd.DataFrame({"date": [r[0] for r in rows], "project": name, "metric": "gross_issuance_tokens",
                                    "value": [r[1] for r in rows], "tier": 2, "source": src}), cols)
    groups[(name, "gross_issuance_tokens")] = (pd.concat([view, have[view.columns.intersection(have.columns)]],
                                                         ignore_index=True).sort_values("date")
                                               if have is not None and not have.empty else view)


def _issuer_mint_views(groups: dict) -> None:
    """issuer_mint_tokens (config.ISSUER_MINTS): new supply minted straight into a non-circulating wallet. Aerodrome's
    team emission from the Minter's own formula at each flip (the rebase row of that flip x the teamRate in force);
    dated mints where a project declares `events`."""
    for p in scoped_projects():
        name = p["name"]
        spec = config.ISSUER_MINTS.get(name)
        if not spec:
            continue
        rows = []
        if spec["kind"] == "minter_team_formula":
            g = groups.get((name, spec["growth_metric"]))
            if g is None or g.empty:
                continue
            g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            for d, growth in zip(g["date"], g["value"].astype(float)):
                rate = config.issuer_mint_rate(name, d)
                if rate is None:
                    continue
                rows.append((d, rate * (growth + spec["frozen_weekly"]) / (10_000 - rate), spec["source"]))
        if spec["kind"] == "series":
            g = groups.get((name, spec["metric"]))
            if g is None or g.empty:
                continue
            g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            rows += [(d, float(v), spec["source"]) for d, v in zip(g["date"], g["value"])]
        if spec["kind"] == "supply_rise":
            rows += _supply_rise_rows(groups.get((name, spec["metric"])), spec)
        for ev in spec.get("events") or ():
            rows.append((pd.Timestamp(ev["date"]), float(ev["tokens"]), spec["source"]))
        if not rows:
            continue
        cols = next(g_.columns for (n_, _m), g_ in groups.items() if n_ == name and g_ is not None)
        groups[(name, "issuer_mint_tokens")] = _as_stored(pd.DataFrame(
            {"date": [r[0] for r in rows], "project": name, "metric": "issuer_mint_tokens",
             "value": [r[1] for r in rows], "tier": 2, "source": [r[2] for r in rows]}), cols)


def _supply_rise_rows(g, spec: dict) -> list[tuple]:
    """G9 D2: a token whose ONLY mint path lands with the issuer (config.ISSUER_MINTS kind "supply_rise") mints, per day,
    the RISE of its stored on-chain totalSupply (rows read at the declared `point` only) since the previous stored day
    (at most SUPPLY_RISE_MAX_GAP days before, the same measuring point); a fall is a burn, not a negative mint (0). A pair of days further apart, or read
    at different points, stores nothing — the gap stays visible as days not covered."""
    from fetch.base import _measuring_point          # noqa: PLC0415
    if g is None or g.empty:
        return []
    g = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
         .drop_duplicates("date", keep="last"))
    out, prev = [], None
    for d, v, src in zip(g["date"], g["value"].astype(float), g["source"].astype(str)):
        mp = _measuring_point(src)
        if mp != spec["point"]:
            continue                                    # only the declared on-chain read; another source is no mint
        if prev is not None and (d - prev[0]).days <= SUPPLY_RISE_MAX_GAP and mp == prev[2]:
            share = float(spec.get("share", 1.0))     # PancakeSwap: devaddr's 1/11 of every CAKE mint (G9 J2)
            out.append((d, share * max(0.0, v - prev[1]),
                        f"{spec['source']}[rise of {spec['metric']}" + (f" x {spec['share_label']}]" if
                                                                         spec.get("share_label") else "]")))
        prev = (d, v, mp)
    return out


SUPPLY_RISE_MAX_GAP = 3


def _supplier_b_views(groups: dict) -> None:
    """G9 I3 (config.SUPPLIER_B_VIEW): a project's supply-side (b) series. On a day the base leg is the DECLARED schedule
    (source starting `schedule_prefix`: Aethir's checker-node base rewards, PARTIAL), the weekly legs are added at their
    week's total ÷ 7 for each of the seven days from the served label; from the base's first MEASURED day the base stands
    alone (it holds those legs already: Aethir's cloud hosts are the compute providers). A schedule day no weekly leg
    covers keeps the schedule alone, and its source says the leg is missing."""
    for name, spec in config.SUPPLIER_B_VIEW.items():
        base = groups.get((name, spec["base"]))
        if base is None or base.empty:
            continue
        b = (base.assign(date=pd.to_datetime(base["date"]).dt.normalize()).sort_values("date")
             .drop_duplicates("date", keep="last").set_index("date"))
        per_day: dict = {}
        for m in spec["weekly_adds"]:
            w = _daily(groups.get((name, m)))
            for d, v in w.items():
                for k in range(7):
                    per_day.setdefault(m, {})[d + pd.Timedelta(days=k)] = float(v) / 7.0
        rows = []
        for d, r in b.iterrows():
            v, src = float(r["value"]), str(r["source"])
            if src.startswith(spec["schedule_prefix"]):
                got = [m for m in spec["weekly_adds"] if d in per_day.get(m, {})]
                v += sum(per_day[m][d] for m in got)
                miss = [m for m in spec["weekly_adds"] if m not in got]
                src = (f"derived:supplier_b[{spec['base']} (schedule)"
                       + "".join(f" + {m}/7" for m in got) + "]"
                       + (f"[PARTIAL: {', '.join(miss)} not on file this day]" if miss else "")
                       + f"[{spec['note']}]")
            rows.append((d, v, src))
        groups[(name, spec["metric"])] = _as_stored(pd.DataFrame(
            {"date": [x[0] for x in rows], "project": name, "metric": spec["metric"], "value": [x[1] for x in rows],
             "tier": 2, "source": [x[2] for x in rows]}), base.columns)


def _stock_rise_views(groups: dict) -> None:
    """G9 D2 (config.STOCK_RISE_VIEWS): a flow published only as a cumulative — Aethir's staking-rewards emission
    schedule — read as the rise between consecutive stored points at most `max_gap_days` apart, dated on the later point;
    a fall is not a negative payment (0). The first point, and a pair further apart, store nothing."""
    for (name, metric), spec in config.STOCK_RISE_VIEWS.items():
        g = groups.get((name, spec["stock"]))
        if g is None or g.empty:
            continue
        s = _daily(g)
        prev = s.index.to_series().shift(1)
        ok = (s.index.to_series() - prev).dt.days.le(spec["max_gap_days"])
        rise = s.diff().clip(lower=0)[ok.values].dropna()
        if rise.empty:
            continue
        groups[(name, metric)] = _as_stored(pd.DataFrame(
            {"date": rise.index, "project": name, "metric": metric, "value": rise.values, "tier": 2,
             "source": f"derived:stock_rise[{spec['stock']}]"}), g.columns)


def _unlock_views(groups: dict) -> None:
    """UNLOCK DILUTION (Jake 2026-10-11, decision A2 on 5b87610): tokens RELEASED from the project's non-circulating
    wallets, per day = the fall in their summed stock (config.unlock_stock_metrics) since the previous stored day; a
    day the stock rises releases 0 (an inflow is not a negative unlock). KEPT OUT OF emissions_tokens (Jake 2026-09-24,
    EMISSIONS_ALIAS_DECLINED stands). A day counts only when EVERY leg holds a row that day and on its previous stored
    day (at most 3 days before), measured by the SAME wallet set (the source names the composition): a wallet added to
    the set is a change of measuring point, never an unlock (Aerodrome's filing wallets 2026-10-07: 4.9M -> 36.2M)."""
    _ONE_OFF_UNMATCHED.clear()
    UNLOCK_LEGACY_DROPPED.clear()
    for p in scoped_projects():
        name = p["name"]
        # the whole set, and any declared SUBSET (config.UNLOCK_RELEASE_SUBSETS: Hyperliquid's Foundation / Grants
        # wallets, which the circulating figure in use counts — Total Yield's issuer-release leg, item 17 overnight)
        for metric, legs in (("unlock_release_tokens", config.unlock_stock_metrics(name)),
                             *((m, tuple(ls)) for m, ls in (config.UNLOCK_RELEASE_SUBSETS.get(name) or {}).items())):
            if legs:
                _unlock_release_view(groups, name, metric, legs)


def _unlock_release_view(groups: dict, name: str, metric: str, legs: tuple) -> None:
    """The release series `metric` from the falls of `legs`' summed stock (see _unlock_views)."""
    from fetch.base import _measuring_point          # noqa: PLC0415
    diffs = []
    cut_note = ""
    for m in legs:
        g = groups.get((name, m))
        if g is None or g.empty:
            return
        g = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
             .drop_duplicates("date", keep="last").set_index("date"))
        # P2a D2 (config.UNLOCK_LEGACY_CUT): a LEGACY read of a leg dated after its declared end measures a stock the
        # migration moved to the new token — its fall is the migration, never a release; a v3 row is read
        cut = config.unlock_leg_legacy_cut(name, m)
        if cut:
            gone = (g.index > pd.Timestamp(cut["last_day"])) & ~g["source"].astype(str).str.startswith(cut["v3_prefix"])
            if gone.any():
                UNLOCK_LEGACY_DROPPED[(name, metric, m)] = int(gone.sum())
                cut_note = f"[legacy legs read to {cut['last_day']}, config.UNLOCK_LEGACY_CUT]"
                g = g[~gone]
            if g.empty:
                return
        v, mp = g["value"].astype(float), g["source"].astype(str).map(_measuring_point)
        gap = v.index.to_series().diff().dt.days
        ok = (mp == mp.shift(1)) & (gap <= config.unlock_max_gap_days(m))
        # a fall over k days is spread evenly over those k days (a weekly read's release is 7 days' worth), so the
        # covered-day count the annualisation divides by stays a count of days
        per_day = {}
        for d, fall, k in zip(v.index[ok], (v.shift(1) - v)[ok], gap[ok]):
            for j in range(int(k)):
                per_day[d - pd.Timedelta(days=j)] = fall / int(k)
        diffs.append(pd.Series(per_day, dtype=float).sort_index().rename(m))
    rel = pd.concat(diffs, axis=1).dropna()
    if rel.empty:
        return
    # RELEASES ONLY: a day the set's stock RISES is not a negative unlock — the Foundation's AERO purchases
    # (Aerodrome's 0x834C/0x623C are in the set) and Ether.fi's buyback Safe would otherwise be counted twice in
    # net absorption incl. unlocks (once as buyback, once as a negative release)
    total = rel.sum(axis=1)
    # A MINT INTO THE SET IS NOT A RETURN (fix round 2, S5/S3): issuer_mint_tokens landed in a wallet of the set; the
    # stock's rise by that amount is new supply to an insider (its own line), so it is added back to the fall
    # — but only against a rise the stock actually shows, on the mint's day or the next read (a daily read taken before
    # the mint sees it a day later); a mint the stock never shows is never turned into a release
    if metric == "unlock_release_tokens" and (g_m := groups.get((name, "issuer_mint_tokens"))) is not None:
        mints = (g_m.assign(date=pd.to_datetime(g_m["date"]).dt.normalize()).groupby("date")["value"].sum()
                 .astype(float))
        exact = bool((config.ISSUER_MINTS.get(name) or {}).get("exact_dates"))
        for d, m in mints.items():
            if exact:                                   # dated by the same ledger as the stock: net in full that day
                if d in total.index:
                    total.loc[d] += float(m)
                continue
            left = float(m)
            for day in (d, d + pd.Timedelta(days=1)):
                if left <= 0 or day not in total.index:
                    continue
                take = min(left, max(0.0, -float(total.loc[day])))
                total.loc[day] += take
                left -= take
    # A DECLARED TREASURY-FUNDED ONE-OFF IS NOT A RELEASE (alignment round 2026-10-13, C1): Uniswap's 100M UNIfication
    # burn left the Timelock (one_off_flows, funding treasury) — a burn, not spend or an unlock; taken out on its day
    # G11 B1 (Jake's 10-21 run: (d) read 105M = $627.2M in Q-3): the Timelock is read at each day's FIRST block, so a burn
    # executed 2025-12-27 20:33 UTC shows as the fall between the 12-27 and 12-28 readings — dated 12-28. The one-off is
    # taken out of the fall on its landed day or the next stored days (up to 3), never past the fall itself, and only
    # from a release series whose legs hold the wallet it left (`from_stock`) — never the vesting contracts' releases.
    for f in (config.PROJECT_BY_NAME.get(name) or {}).get("one_off_flows") or ():
        if f.get("funding") != "treasury" or (f.get("from_stock") and f["from_stock"] not in legs):
            continue
        d, left = pd.Timestamp(f.get("landed") or "1900-01-01"), float(f["tokens"])
        for day in (d + pd.Timedelta(days=j) for j in range(4)):
            if left <= 0 or day not in total.index:
                continue
            take = min(left, max(0.0, float(total.loc[day])))
            total.loc[day] -= take
            left -= take
        if left > 0:
            _ONE_OFF_UNMATCHED[(name, metric)] = left     # said on the Flows memo, never turned into a "return"
    returned = (-total).clip(lower=0.0)
    total = total.clip(lower=0.0)
    cols = next(g.columns for m in legs if (g := groups.get((name, m))) is not None)
    split_src = ""
    if metric == "unlock_release_tokens" and (sp := config.STAKING_FUNDING_SPLIT.get(name)):
        total, split_src = _staking_funding_split(groups, name, sp, total, cols)
    # A LEG COUNTED ELSEWHERE (config.RELEASE_LESS, round 3 W7): taken out on its day or the next stored day, never < 0
    for m in (config.RELEASE_LESS.get(name) or ()) if metric == "unlock_release_tokens" else ():
        less = _daily(groups.get((name, m)))
        if less.empty:
            continue
        total = total.copy()
        for d, v in less[less > 0].items():
            left = float(v)
            for day in (d, d + pd.Timedelta(days=1)):
                if left > 1e-9 and day in total.index:
                    t = min(left, float(total.loc[day]))
                    total.loc[day] -= t
                    left -= t
        split_src += f"-{m}"
    groups[(name, metric)] = _as_stored(pd.DataFrame(
        {"date": total.index, "project": name, "metric": metric, "value": total.values,
         "tier": 1, "source": f"derived:unlock_release[{'+'.join(legs)}]{split_src}{cut_note}"}), cols)
    # F1 (fix round 2): the rises of the same stock, on the same gated days — tokens RETURNED to the issuer side (the
    # token-flow statement's "− returned to issuer-side wallets"); never netted into the release
    if metric == "unlock_release_tokens":
        groups[(name, "issuer_return_tokens")] = _as_stored(pd.DataFrame(
            {"date": returned.index, "project": name, "metric": "issuer_return_tokens", "value": returned.values,
             "tier": 1, "source": f"derived:issuer_return[{'+'.join(legs)}]{cut_note}"}), cols)


# P1 (round 3 review, Jake 2026-10-14: "FIFO matching of staking-vault funding — ACCEPTED, labelled as an attribution
# convention in the cell note and SPEC"). LINK is fungible: which release funded the vault is a convention, not a fact.
FIFO_CONVENTION = "ATTRIBUTION CONVENTION: FIFO matching of the forwards to the releases (ACCEPTED, Jake 2026-10-14)"


def _staking_funding_split(groups: dict, name: str, sp: dict, total: pd.Series, cols) -> tuple[pd.Series, str]:
    """THE STAKING PART OF EACH RELEASE, OUT OF (e) (round 3 B2; config.STAKING_FUNDING_SPLIT, CL_STAKING_PATH). The lumps
    the 27 wallets sent to the path's first wallet queue up FIFO; each day's forward from it draws the queue down, and
    the share of that day's forward that reached the staking hops (funding ÷ forwarded, at most 1) is attributed back to
    the lumps it drew on. That attributed LINK is taken out of the release on the lump's day (or the next stored day —
    a balance read once a day sees a transfer on its next read), never below 0, and written as its own series. A lump
    not yet forwarded stays in (e); a forward the queue cannot cover (no lump stored before it) is not attributed."""
    def daily(m):
        g = groups.get((name, m))
        if g is None or g.empty:
            return None
        return (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).groupby("date")["value"].sum().astype(float)
                .sort_index())
    lumps, fwd, fund = daily(sp["lumps"]), daily(sp["forwarded"]), daily(sp["funding"])
    if lumps is None or fwd is None or fund is None:
        return total, ""
    queue = [[d, float(v)] for d, v in lumps.items() if v > 0]
    attrib: dict = {}
    for d, out_ in fwd.items():
        if out_ <= 0:
            continue
        share = min(1.0, float(fund.get(d, 0.0)) / out_)
        left = out_
        for q in queue:
            if left <= 0:
                break
            if q[0] > d or q[1] <= 0:
                continue
            take = min(q[1], left)
            q[1] -= take
            left -= take
            if share > 0:
                attrib[q[0]] = attrib.get(q[0], 0.0) + take * share
    if not attrib:
        return total, ""
    total = total.copy()
    taken = {}
    for d, v in sorted(attrib.items()):
        left = v
        for day in (d, d + pd.Timedelta(days=1)):
            if left <= 1e-9 or day not in total.index:
                continue
            t = min(left, float(total.loc[day]))
            total.loc[day] -= t
            taken[day] = taken.get(day, 0.0) + t
            left -= t
    # A MEASURED ZERO ON EVERY RELEASE DAY THE SCANS COVER (G17-3a, Jake's run 20261023T132001Z: CHECK — "stale, last
    # point 2025-12-20"): the three inputs are reconciled log scans, so a release day after the first attributed one
    # with nothing attributed is 0 by the convention (a lump not forwarded stays in (e)), not a day unread
    if taken:
        first = min(taken)
        ser = pd.Series({d: float(taken.get(d, 0.0)) for d in total.index if d >= first}, dtype=float).sort_index()
    else:
        ser = pd.Series(taken, dtype=float).sort_index()
    groups[(name, sp["out"])] = _as_stored(pd.DataFrame(
        {"date": ser.index, "project": name, "metric": sp["out"], "value": ser.values, "tier": 2,
         "source": f"derived:fifo[{sp['lumps']}|{sp['forwarded']}|{sp['funding']}] — {FIFO_CONVENTION}"}), cols)
    return total, f"-{sp['out']} ({FIFO_CONVENTION})"


def _net_common_views(groups: dict) -> None:
    """NET SUPPLY CHANGE ON THE DAYS BOTH SIDES ARE MEASURED (overnight 2026-10-06, A5): issuance − burn per day, only
    where both hold a row (config net_change_common_days). Q0 then sums those days, and the annualised % divides by
    the days actually covered (_annualise) — the coverage guard's mismatch cannot arise."""
    for p in scoped_projects():
        spec = p.get("net_change_common_days")
        if not spec:
            continue
        name = p["name"]
        gi, gb = groups.get((name, spec["issuance"])), groups.get((name, spec["burn"]))
        if gi is None or gi.empty or gb is None or gb.empty:
            continue
        s = lambda g: (g.assign(date=pd.to_datetime(g["date"]).dt.normalize())  # noqa: E731
                       .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
        both = pd.concat([s(gi).rename("i"), s(gb).rename("b")], axis=1).dropna()
        # a day counts only where every REQUIRED series is stored too (Ethereum: staked ETH, so the credibility curve
        # reference covers exactly the same days — Jake's run 2026-10-08)
        for m in spec.get("require") or ():
            g = groups.get((name, m))
            both = both[both.index.isin(s(g).index)] if g is not None and not g.empty else both.iloc[0:0]
        if both.empty:
            continue
        view = pd.DataFrame({"date": both.index, "project": name, "metric": spec["metric"],
                             "value": (both["i"] - both["b"]).values,
                             # A DIFFERENCE OF TWO DIFFERENCED FLOWS (Jake's run 2026-10-09 11:41, item 4: J5 annualised over 9
                             # days where I5 covers 10): its first row holds the interval ending that day, so the source is
                             # marked derived:d_ and coverage counts from the day before (_differenced_first).
                             "source": f"derived:d_net:{spec['issuance']}-{spec['burn']}(common days)", "tier": 2})
        groups[(name, spec["metric"])] = _as_stored(view, gi.columns)


def _supply_history_views(groups: dict) -> None:
    """SUPPLY HISTORY (config.SUPPLY_HISTORY, fix round 2 H1/H3): the days before the first-party supply's first row,
    read from the history route (ultrasound's next-day 00:00Z point = the end of day D), a declared handover; and the
    gross issuance of those days = d(that supply) + the stored burn, under the first-party prefix with the leg named.
    Read-time only."""
    for p in scoped_projects():
        name = p["name"]
        h = config.SUPPLY_HISTORY.get(name)
        if not h:
            continue
        live, hist = groups.get((name, h["metric"])), groups.get((name, h["from_metric"]))
        if live is None or live.empty or hist is None or hist.empty:
            continue
        first = pd.to_datetime(live["date"]).min().normalize()
        u = (hist.assign(date=pd.to_datetime(hist["date"]).dt.normalize()).drop_duplicates("date", keep="last")
             .set_index("date")["value"].astype(float))
        # day D reads the point stamped D + shift_days (00:00Z of the next day: the end of D)
        lead = u.shift(-int(h.get("shift_days", 0)), freq="D")
        lead = lead[lead.index < first]
        if lead.empty:
            continue
        cols = live.columns
        groups[(name, h["metric"])] = pd.concat([_as_stored(pd.DataFrame(
            {"date": lead.index, "project": name, "metric": h["metric"], "value": lead.values, "tier": 3,
             "source": h["source"]}), cols), live], ignore_index=True)
        iss = h.get("issuance")
        burn = groups.get((name, iss["burn"])) if iss else None
        if not iss or burn is None or burn.empty:
            continue
        b = (burn.assign(date=pd.to_datetime(burn["date"]).dt.normalize()).drop_duplicates("date", keep="last")
             .set_index("date")["value"].astype(float))
        full = pd.concat([lead, live.assign(date=pd.to_datetime(live["date"]).dt.normalize())
                          .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)])
        full = full[~full.index.duplicated(keep="last")].sort_index()
        d_ = full.diff()
        gap = full.index.to_series().diff().dt.days
        days = [d for d in d_.index if d < first and gap.get(d) == 1 and d in b.index]
        if not days:
            continue
        g_iss = groups.get((name, iss["metric"]))
        keep = g_iss[pd.to_datetime(g_iss["date"]).dt.normalize() >= first] if g_iss is not None else None
        view = _as_stored(pd.DataFrame({"date": days, "project": name, "metric": iss["metric"],
                                        "value": [float(d_[d] + b[d]) for d in days], "tier": 3,
                                        "source": iss["source"]}), cols)
        groups[(name, iss["metric"])] = pd.concat([view, keep], ignore_index=True) if keep is not None else view


ISSUANCE_OVERLAP: dict = {}        # project -> the history/primary overlap check and the derived cross-check, last build


def _measured_with_history(name: str, spec: dict, observed: pd.DataFrame, groups: dict, asof: pd.Timestamp):
    """ONE MEASURING POINT for a measured issuance series (Round G7 item 1 — NEAR, Jake 2026-10-19: "two measuring points:
    the derived reading and the measured epoch mint (2 rows)" blanked Q0 as a change of measuring point).

    The series is the SAME quantity read two ways: spec["history_metric"] (NEAR's epoch mint from Google's BigQuery blocks
    table, the year) and the primary rows (source_prefix: the epoch mint read at each minting block on NEAR's RPC). The
    primary takes over only after an OVERLAP CHECK, run every build, like FDV_TOTAL_HISTORY: at least
    overlap_check.min_days common days, each within overlap_check.tolerance_pct. Until it passes — or when it fails — the
    history leg alone stands, every day it covers. Once it passes: history before the primary's first day, the primary from
    it; the legs never share a day, so the declared handover (series_handover) holds. Days the history has not reached
    before either leg starts take the fallback (history_prefix — NEAR's derived d(header supply) + burn) under the same
    declared handover. The derived reading is also the CROSS-CHECK: its Q0 sum beside the measured one (ISSUANCE_OVERLAP)."""
    def rows_of(df):
        return df.drop_duplicates("date", keep="last").sort_values("date") if df is not None and not df.empty else None
    src = observed["source"].astype(str)
    prim = rows_of(observed[src.str.startswith(spec["source_prefix"])])
    fb = rows_of(observed[src.str.startswith(spec["history_prefix"])]) if spec.get("history_prefix") else None
    hist = rows_of(groups.get((name, spec["history_metric"])))
    oc = spec.get("overlap_check") or {}
    need, tol = int(oc.get("min_days", 3)), float(oc.get("tolerance_pct", 1.0))
    check = {"history_metric": spec["history_metric"], "min_days": need, "tolerance_pct": tol, "common_days": 0,
             "worst_pct": None, "passed": False}
    if hist is not None and prim is not None:
        h = hist.set_index("date")["value"].astype(float)
        q = prim.set_index("date")["value"].astype(float)
        common = h.index.intersection(q.index)
        common = common[h[common] != 0]
        check["common_days"] = int(len(common))
        if len(common):
            pct = ((q[common] / h[common]) - 1.0) * 100.0
            worst = pct.abs().idxmax()
            check.update(worst_pct=float(pct[worst]), worst_day=str(worst.date()),
                         pairs=[(str(d.date()), float(h[d]), float(q[d])) for d in common[-7:]])
            check["passed"] = len(common) >= need and float(pct.abs().max()) <= tol
    if hist is None:
        legs = [x for x in (fb, prim) if x is not None]
        if len(legs) == 2:                       # no history leg stored: the fallback before the primary's first day
            legs = [fb[fb["date"] < prim["date"].min()], prim]
        out = pd.concat(legs, ignore_index=True) if legs else None
        check["used"] = "no history leg stored: " + ("fallback + primary" if len(legs) == 2 else "one leg")
    else:
        h0 = hist["date"].min()
        if check["passed"]:
            p0 = prim["date"].min()
            parts = [hist[hist["date"] < p0], prim]
            check["used"] = f"history to {(p0 - pd.Timedelta(days=1)).date()}, primary from {p0.date()}"
        else:
            parts = [hist]
            check["used"] = ("history leg alone — the primary takes over after "
                             f"{need} common days within {tol}% (has {check['common_days']}"
                             + (f", worst {check['worst_pct']:+.3f}% on {check['worst_day']}" if check["worst_pct"]
                                is not None else "") + ")")
        if fb is not None:
            parts = [fb[fb["date"] < h0]] + parts
        hrows = pd.concat(parts, ignore_index=True)
        out = hrows.assign(metric=observed["metric"].iloc[0]) if not hrows.empty else None
    # THE CROSS-CHECK: the derived reading's Q0 beside the measured series' Q0, on the days both cover
    if fb is not None and out is not None and not out.empty:
        start = asof - pd.Timedelta(days=GLOBALS["period_days"])
        m = out[(out["date"] > start) & (out["date"] <= asof)].set_index("date")["value"].astype(float)
        d = fb[(fb["date"] > start) & (fb["date"] <= asof)].set_index("date")["value"].astype(float)
        both = m.index.intersection(d.index)
        if len(both):
            check["q0_cross_check"] = {"days": int(len(both)), "measured": float(m[both].sum()),
                                       "derived": float(d[both].sum()),
                                       "derived_vs_measured_pct": (float(d[both].sum()) / float(m[both].sum()) - 1) * 100
                                       if float(m[both].sum()) else None}
    ISSUANCE_OVERLAP[name] = check
    return out


def _issuance_views(groups: dict, asof: pd.Timestamp) -> None:
    """The PRIMARY issuance series, for every consumer. See config.ISSUANCE_PRIMARY.

    declared_rate  gross_issuance_tokens := rate x total supply / 365 per day, to the day before
                   asof, supply forward-filled; from the first stored supply, the declared
                   history_supply_metric filling the days before the primary supply starts and
                   the superseded rate applying before effective_from (with no superseded rate,
                   nothing before effective_from is shown). The derived series it replaces is the
                   cross-check: annualised over the days it covers, more than max_ratio apart
                   either way BLOCKS the row with both figures.
    first_party    only rows from source_prefix; until any fall inside the Q0 window the row is
                   BLOCKED with the declared reason instead of showing the derivation.
    And for EVERY project: a derived pool release (d(circulating) - d(total)) whose circulating
    input did not move across the Q0 window is BLOCKED — the route cannot see a release then.
    """
    period, ann = GLOBALS["period_days"], GLOBALS["days_per_year"]
    start = asof - pd.Timedelta(days=period)
    in_q0 = lambda g: g[(g["date"] > start) & (g["date"] <= asof)]  # noqa: E731
    for p in scoped_projects():
        name = p["name"]
        spec = config.issuance_primary(name)
        if spec:
            metric, key = spec["metric"], (name, spec["metric"])
            observed = groups.get(key)
            # THE GUARD'S CROSS-CHECK READS ONLY THE CURRENT DERIVATION (NEAR, 2026-09-28): rows
            # derived from CoinGecko's slow total are not the series the guard should test now.
            pref = spec.get("observed_source_prefix")
            if pref and observed is not None and not observed.empty:
                observed = observed[observed["source"].astype(str).str.startswith(pref)]
            if spec["kind"] == "declared_rate":
                rate = p
                for k in spec["rate_path"]:
                    rate = rate[k]
                sup = groups.get((name, spec["supply_metric"]))
                if sup is not None and not sup.empty:
                    sup = sup.drop_duplicates("date", keep="last").sort_values("date")
                    declared = p.get("issuance_rate_declared") or {}
                    eff = str(declared.get("effective_from") or "")
                    eff_ts = pd.Timestamp(eff + ("-01" if len(eff) == 7 else "")) if eff else None
                    # THE DECLARED ROUTE OVER THE WHOLE STORED SUPPLY (NEAR, Jake 2026-09-30).
                    # CoinGecko's total_supply starts 2026-09-11; the archival block-header
                    # total_supply covers the year, so it supplies the level for the days before
                    # the primary supply's first row. Before effective_from the SUPERSEDED rate
                    # applies (NEAR: 5% until the 2025-10-30 halving); with no superseded rate
                    # declared, nothing before effective_from is shown.
                    old_rate = (declared.get("supersedes") or {}).get(spec["rate_path"][-1])
                    hist = groups.get((name, spec.get("history_supply_metric")))
                    hist = (hist.drop_duplicates("date", keep="last").sort_values("date")
                            if hist is not None and not hist.empty else None)
                    first = min(sup["date"].min(), hist["date"].min() if hist is not None else sup["date"].min())
                    if eff_ts is not None and old_rate is None:
                        first = max(first, eff_ts)
                    days = pd.date_range(first, asof - pd.Timedelta(days=1), freq="D")
                    ffill = lambda s: s.set_index("date")["value"].astype(float).reindex(  # noqa: E731
                        days.union(s["date"])).sort_index().ffill().reindex(days)
                    level = ffill(sup)
                    used = pd.Series(spec["supply_metric"], index=days)
                    if hist is not None:
                        early = level.isna()
                        level = level.fillna(ffill(hist))
                        used[early] = spec["history_supply_metric"]
                    level = level.dropna()
                    used = used[level.index]
                    rates = pd.Series(float(rate), index=level.index)
                    if eff_ts is not None and old_rate is not None:
                        rates[rates.index < eff_ts] = float(old_rate)
                    # ONE MEASURING POINT: every row's source is the current declared route, and a
                    # row computed otherwise says how in brackets (which _measuring_point strips).
                    label = f"declared:{rate:.2%}/yr x {spec['supply_metric']}"
                    srcs = [label if (r == float(rate) and u == spec["supply_metric"])
                            else f"{label}[{r:.2%}/yr x {u}]" for r, u in zip(rates, used)]
                    view = pd.DataFrame({"date": level.index, "project": name, "metric": metric,
                                         "value": level.values * rates.values / ann,
                                         "source": srcs, "tier": 1})
                    groups[key] = _as_stored(view, sup.columns)
                    decl = float(rate) * float(sup["value"].iloc[-1])
                    if observed is not None and not observed.empty:
                        ob = in_q0(observed)
                        if not ob.empty:
                            covered = (ob["date"].max() - ob["date"].min()).days + 1
                            obs = float(ob["value"].astype(float).sum()) / covered * ann
                            k = float(spec.get("max_ratio", 10))
                            if obs <= 0 or decl / obs > k or obs / decl > k:
                                _VIEW_BLOCKS[key] = (
                                    f"BLOCKED — declared {decl:,.0f} vs observed {obs:,.0f} "
                                    f"issuance/yr differ >{k:.0f}x (observed = the derived "
                                    f"series, annualised over its {covered} covered day(s)); one "
                                    f"of them is wrong, so neither is shown")
            elif spec["kind"] == "first_party":
                # history_prefix (Ethereum, A1 2026-09-30): the declared history leg before the
                # first-party one — both shown, as the declared handover stitches them.
                prefixes = tuple(x for x in (spec["source_prefix"], spec.get("history_prefix")) if x)
                mine = (observed[observed["source"].astype(str).str.startswith(prefixes)]
                        if observed is not None and not observed.empty else None)
                # ONE MEASURING POINT OVER THE WINDOW (NEAR, Round G7 item 1): the same quantity stored under another
                # metric is the history leg, handed over only after a checked overlap
                if spec.get("history_metric") and observed is not None:
                    mine = _measured_with_history(name, spec, observed, groups, asof)
                # ONE ROW A DAY, THE PRIMARY'S WHERE BOTH HOLD (NEAR, G6 2026-10-19): the history leg fills only the
                # days the primary has not reached.
                if mine is not None and not mine.empty and spec.get("prefer_primary_per_day"):
                    rank = (~mine["source"].astype(str).str.startswith(spec["source_prefix"])).astype(int)
                    mine = (mine.assign(_r=rank).sort_values(["date", "_r"])
                            .drop_duplicates("date", keep="first").drop(columns="_r"))
                guard = spec.get("declared_guard")
                if guard and mine is not None and not in_q0(mine).empty:
                    rate = p
                    for k in guard["rate_path"]:
                        rate = rate[k]
                    sup = groups.get((name, guard["supply_metric"]))
                    if sup is not None and not sup.empty:
                        ob = in_q0(mine)
                        covered = (ob["date"].max() - ob["date"].min()).days + 1
                        obs = float(ob["value"].astype(float).sum()) / covered * ann
                        decl = float(rate) * float(sup.sort_values("date")["value"].iloc[-1])
                        k = float(guard.get("max_ratio", 10))
                        if obs <= 0 or decl / obs > k or obs / decl > k:
                            _VIEW_BLOCKS[key] = (
                                f"BLOCKED — measured {obs:,.0f} vs declared {decl:,.0f} issuance/yr "
                                f"({float(rate):.2%} x {guard['supply_metric']}) differ >{k:.0f}x; one of them is "
                                f"wrong, so neither is shown")
                if mine is not None and not in_q0(mine).empty:
                    groups[key] = _as_stored(mine, observed.columns)
                elif observed is not None and not observed.empty:
                    _VIEW_BLOCKS[key] = f"BLOCKED — {spec['block_reason']}"
            elif spec["kind"] == "declared_curve":
                # A MODEL, EVALUATED ON THE CALENDAR (World Mobile, A8 2026-09-28): each day is
                # S(t1) - S(t0) with S(t) = S0 (t+1)^k, t in years since spec["t0"]. It needs no
                # stored input, so it never inherits what bridging does to the EVM supply sum.
                curve = config.issuance_curve(name)
                if curve and any(g[0] == name for g in groups):   # only where the project has data
                    k, s0 = float(curve["k"]), config.issuance_curve_s0(curve)
                    t0 = pd.Timestamp(spec["t0"])
                    days = pd.date_range(max(t0, asof - pd.Timedelta(days=2 * 365)),
                                         asof - pd.Timedelta(days=1), freq="D")
                    t = ((days - t0).days.to_numpy(dtype=float)) / 365.25
                    s = lambda x: s0 * (x + 1.0) ** k  # noqa: E731
                    view = pd.DataFrame({"date": days, "project": name, "metric": metric,
                                         "value": s(t + 1 / 365.25) - s(t),
                                         "source": spec["source_label"], "tier": 1})
                    cols = observed.columns if observed is not None and not observed.empty else view.columns
                    groups[key] = _as_stored(view, cols)
        # A DERIVED RELEASE OVER A FLAT CIRCULATING FIGURE MEASURES NOTHING.
        rel, circ = groups.get((name, "pool_release_tokens")), groups.get((name, "circulating_supply"))
        if (rel is not None and not rel.empty and circ is not None and (name, "pool_release_tokens") not in _VIEW_BLOCKS
                and in_q0(rel)["source"].astype(str).str.startswith(config.POOL_RELEASE_DERIVED_SOURCE).all()):
            c = in_q0(circ)
            if len(c) >= 2 and c["value"].astype(float).nunique() == 1:
                _VIEW_BLOCKS[(name, "pool_release_tokens")] = (
                    f"BLOCKED — circulating not updating, so release is unmeasurable by this route: "
                    f"the derived release is d(circulating) - d(total), and circulating_supply read "
                    f"{float(c['value'].iloc[-1]):,.0f} on all {len(c)} stored day(s) of the window")


_LAST_GROUPS: dict = wiring.TrackingDict()   # aggregate()'s groups after every read-time view (reads recorded, G12)


def aggregate(long: pd.DataFrame, fetch_status: pd.DataFrame, asof: pd.Timestamp,
              gaps: pd.DataFrame | None = None, review: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every project x every metric in the library — so every INDEX/MATCH key resolves."""
    gap_by_key = {}
    if gaps is not None and not gaps.empty:
        for r in gaps.to_dict("records"):
            gap_by_key.setdefault((r["project"], r["metric"]), r)
    review_keys = set()
    if review is not None and not review.empty:
        # AN ACCEPTED ROW NO LONGER FLAGS ITS SERIES (Round G5 item 1: 15 cells lost VERIFIED on 10-19 because single-day
        # change flags marked whole Q0 figures "flagged in the Review Queue"). A row config.REVIEW_ANSWERED decides ACCEPT
        # for its date is a reviewed value; REJECT and every open row still flag.
        review_keys = {(r["project"], r["metric"]) for r in review.to_dict("records")
                       if (config.review_answered(r) or {}).get("decision") != "ACCEPT"}
    applicable = {p["name"]: set(config.metrics_for_project(p)) for p in scoped_projects()}
    short, period, stale_days = GLOBALS["short_days"], GLOBALS["period_days"], GLOBALS["stale_after_days"]
    last_success = {}
    if fetch_status is not None and not fetch_status.empty:
        for r in fetch_status.itertuples(index=False):
            last_success[(r.source, r.project)] = r.last_success_at
    groups = {k: g for k, g in long.groupby(["project", "metric"])} if not long.empty else {}
    # G12 A1: every view's reads and writes are recorded — the wiring audit's dependency graph (wiring.py)
    groups = wiring.TrackingDict(groups)
    _EARLY_BLOCKS.clear()
    _retired_series_views(groups)     # D5-3c: a series whose stored figures measure nothing it claims — dropped, said why
    _provisional_views(groups)        # G15 5: yesterday read before 16:00 UTC is stored, not used
    _series_restatement_views(groups, asof)  # P2b-2: a declared restatement — its pending seam, or its holes, named
    _handover_continuation_views(groups)   # P2a D5a: a declared later leg read from another stored series (Maple)
    _handover_cut_views(groups)       # G11 C1: a declared cut — the earlier leg's rows after the later leg starts dropped
    _partial_sum_views(groups)    # FIRST: nothing downstream may read a partial sum
    _epoch_primary_views(groups, asof)   # D4-5: Pendle's own epoch figures, spread; DefiLlama only where none
    _same_basis_views(groups)     # one basis read by several routes is one measuring point (config.SAME_BASIS)
    _epoch_paid_views(groups, asof)  # round 3 B4: a weekly row dated by the flip that paid it (config.EPOCH_PAID_AT)
    _history_prepend_views(groups)  # fix round 3: a series' history from another route on the same basis
    _declared_zero_views(groups, asof)   # an ENDED programme reads 0 after its end (config.DECLARED_ZERO)
    _price_tier_views(groups)     # G2 A1: the price_history tier's rows are the declared older leg, never interleaved
    _price_history_views(groups)  # Q-4 prices: DefiLlama coins before CoinGecko's free 365 days (review item 8b)
    _consensus_history_views(groups, asof)  # ETH consensus rewards before Etherscan's first read (review 8c)
    _monthly_leg_views(groups)
    _measured_emissions_views(groups)
    _usd_history_views(groups)    # BEFORE the burn total, which sums the leg it extends
    _near_buyback_views(groups)
    _foundation_buyback_views(groups)
    _aethir_arr_view(groups)
    _burn_total_views(groups)
    _one_off_views(groups)        # BEFORE the relabel, so a burn-route buyback copies the ongoing flow
    _relabel_views(groups)
    _restatement_views(groups)
    _buyback_tokens_from_usd_views(groups)
    _measured_buyback_views(groups)   # G11 B9: Pendle's buyback = its own per-epoch distribution; derived = cross-check
    _buyback_split_views(groups)  # AFTER every view that builds actual_buyback_* (retirement = burned + held)
    _emissions_from_metric_views(groups)
    # EACH QUARTER END, NOT ONLY ASOF (Round G7 item 2c): the shifted quarters read these stocks at asof − k x period
    _quarterly_views(lambda g, a: _trailing_yield_views(g), groups, asof)
    _quarterly_views(_payday_yield_views, groups, asof)
    _quarterly_views(_epoch_mean_views, groups, asof)
    _native_fee_usd_views(groups)
    _mev_estimate_views(groups)
    _VIEW_BLOCKS_PENDING.clear()
    _customer_revenue_views(groups)   # the aggregator core + the lines that bypass it (Chainlink)
    _utilisation_views(groups)
    _VIEW_BLOCKS.clear()
    _VIEW_BLOCKS.update(_ONE_OFF_BLOCKS)
    _VIEW_BLOCKS.update(_VIEW_BLOCKS_PENDING)
    _VIEW_BLOCKS.update(_EARLY_BLOCKS)
    # THE HISTORY LEGS FIRST (2026-09-30): Ethereum's first-party issuance view keeps its
    # declared ETH.Store leg (history_prefix) only if the leg is already in the group.
    _history_leg_views(groups)
    _emissions_model_views(groups)
    _hl_model_views(groups)           # fix round 3 B1: Hyperliquid's MODELLED rewards / stake / reserve history
    _onchain_release_views(groups)    # BEFORE the issuance views that read pool_release_tokens
    _release_floor_views(groups)      # a release is never negative, whatever stored it (alignment D2)
    _supply_history_views(groups)     # fix round 2 H1/H3: ETH supply + issuance before Etherscan's first read
    _issuance_views(groups, asof)
    _emissions_from_metric_views(groups, after_issuance=True)   # emissions read from the issuance view (Pendle)
    _reward_end_views(groups, asof)
    _net_common_views(groups)     # AFTER the issuance views it differences
    _first_party_history_views(groups)   # fix round 2, H1: Aethir's table / Plume's mirror before the first read
    _total_supply_net_views(groups)   # Round G2 D2/L3: total supply = our total net of what is burned (FDV reads it)
    _wm_total_supply_views(groups)    # Round G7 0a: World Mobile's total = option C, option B its own row
    _circulating_views(groups)
    _cg_plus_staked_views(groups)
    _near_lockup_views(groups)    # BEFORE market cap: NEAR's circulating under Jake's lockup rule
    _market_cap_views(groups)     # AFTER the on-chain circulating it may use; BEFORE NRR reads it
    _fdv_views(groups)            # price x the total supply used, where total supply is on-chain first
    _total_supply_history_views(groups)   # Round G7 2e: the declared older leg before total_supply's first row
    _reward_funding_views(groups)  # fix round 2 S1: a reward split by what funds it (mint / treasury / revenue)
    _treasury_split_views(groups)       # G9 L3: NEAR's (a) = the epoch mint less the treasury's 1/10 (line m)
    _issuer_mint_views(groups)    # fix round 2 S5/S3: mints straight to issuer-side wallets, BEFORE the unlock view
    _issuance_from_supply_views(groups)   # G11 B2: Maple's gross issuance = the rises of its stored total supply
    _unlock_views(groups)         # releases from the non-circulating wallets (Jake 2026-10-11, A2)
    _revenue_total_views(groups)  # alignment C10: NEAR's revenue = gas + Intents fee switch
    _stake_prorata_views(groups)  # alignment C1: Hyperliquid's (a) = rewards to circulating stake only
    _cashback_funding_views(groups)  # round 3 W3: the cashback its market purchases did not cover — (c)
    EVIDENCE_RESULTS.clear()
    _evidence_views(groups, usd=False)  # G9 N: published / aggregator / mechanism figures in tokens (config.EVIDENCE_LADDER)
    _stock_rise_views(groups)     # G9 D2: a declared flow read as the rises of a published cumulative (config.STOCK_RISE_VIEWS)
    _supplier_b_views(groups)     # G9 I3: Aethir's (b) = checker schedule + weekly compute rewards before the measured sum
    _outflow_views(groups)        # Phase 2: the five outflow categories, tokens and $ (after unlocks and emissions)
    _evidence_views(groups, usd=True)   # G9 N: ... in $ — after the outflow views, so a $ outflow line can be filled
    _ccip_fee_overlap_view(groups)      # G9 K4: CCIP 2.0 fees with Base from the message detail, Arbitrum the check
    _pendle_rate_check(groups)          # G9 N: each Pendle gauge chain's log scan against its sampled rates
    _revenue_from_fees_views(groups)    # G9 N: Plume's revenue = the fees its RewardDistributor receives
    _holder_value_views(groups)   # Phase 2: revenue-funded holder value, credited vs recorded (LITMUS A)
    _settlement_views(groups)
    _monthly_flow_views(groups)       # P2a D5b: complete-month flows from daily log scans (Maple's SSF Safe)
    _handover_overlap_checks(groups)  # G11 B8 / C1: each declared handover's two legs compared on their common days
    _series_break_notes(groups)       # P2c-2: a declared break's named day held by the series -> its note on the cell
    _daily_twin_views(groups, asof)   # G11 D3: a "daily" series that IS its monthly twin in every window is dropped
    _LAST_GROUPS.clear()
    _subsidy_common_views(groups, asof)   # G13 6: subsidy cover over the days every leg covers once (b) is complete
    _LAST_GROUPS.update(groups)       # the views, for Credibility rows that read them (credibility.VIEWS_LONG)
    COVERED_SETS.clear()
    COVERED_SETS.update(_covered_day_sets(groups))   # D6-1: the days each daily flow's rows cover
    WINDOW_HOLES.clear()
    # The latest value of every series, keyed the same way — so a flow can be checked against the
    # stock it was differenced from without depending on the order METRICS happens to iterate in.
    latest_value = {}
    for key, gg in groups.items():
        if gg.empty:
            continue
        try:
            latest_value[key] = float(gg.sort_values("date").iloc[-1]["value"])
        except (TypeError, ValueError):
            continue
    rows = []
    held = {}                          # (project, metric) -> (status, reason) of a withheld row
    for p in scoped_projects():
        name = p["name"]
        for metric, m in METRICS.items():
            g = groups.get((name, metric))
            row = {"key": f"{name}|{metric}", "project": name, "metric": metric,
                   "label": config.metric_label(name, metric),
                   "kind": m["kind"], "unit": config.metric_unit(name, metric), "source": "", "tier": "", "latest_date": "", "now": None, "m1": None,
                   "q0": None, "q1": None, "q2": None, "q3": None, "q4": None, "y1": None, "n_points": 0,
                   "status": "missing", "last_success": "", "entered_on": "", "note": "",
                   "measuring_points": (), "max_single_delta": None, "cumulative_ref": None,
                   "flow_stock_move": None, "flow_accounted": None, "flow_residual": None,
                   "flow_span": None, "point_spans": {}, "reconciliation": None,
                   "flow_recon_blocked": None,
                   "granularity": "daily", "period_label": "",
                   "covered_days": None, "window_days": None, "hole_note": "", "q0_basis": "",
                   "now_covered_days": None, "m1_covered_days": None, "q1_covered_days": None,
                   "q2_covered_days": None, "q3_covered_days": None, "q4_covered_days": None,
                   "q1_events": None, "q2_events": None, "q3_events": None, "q4_events": None}
            # A DECLARED n/a, OR A DECIDED SPLIT, READS n/a WHATEVER IS STORED. 2026-09-28.
            # The store never deletes, so a metric declared not_applicable after rows were written
            # (Hyperliquid's gross_issuance_tokens: derived zeros from d(total_supply_gross)) kept
            # rendering them; and Sky's decided five-way split kept rendering "BLOCKED — cleared
            # by" as though a decision were still owed. Either way the reason is the note, so the
            # decision is on the sheet rather than behind it. A row a DIFFERENT withheld case
            # catches (orphaned, withdrawn, ...) keeps that status: it still needs cleaning up.
            pending = config.classification_pending(name, metric) or {}
            na_note = config.declared_na(name, metric) or ""
            if pending.get("decided"):
                na_note += f" Evidence: {pending.get('reason')}."
            if g is None or g.empty:
                gap = gap_by_key.get((name, metric))
                if na_note:
                    row["status"], row["note"] = "n/a", na_note
                elif (name, metric) in _VIEW_BLOCKS:
                    # A VIEW THAT REFUSED TO FORM SAYS WHY (G2-D1: Morpho's emissions_tokens, the sum of four chains'
                    # claims, read "no rows stored" while the view named the chains still missing)
                    row["status"], row["note"] = "blocked", _VIEW_BLOCKS[(name, metric)]
                elif gap is not None:
                    row["status"], row["note"] = "gap", f"{gap['reason']} | {gap['suggestion']}"
                elif metric not in applicable.get(name, ()):
                    row["status"], row["note"] = "n/a", "not applicable to this project's archetypes"
                # AN ARMED GUARD WITH NOTHING TO COMPARE AGAINST IS NOT AN UNBUILT METRIC.
                # A cross-check secondary whose PRIMARY is suppressed by config is working exactly
                # as designed: it will sit empty until the primary becomes reportable, and then it
                # is what confirms it. Rendering that as a plain "gap" makes it look identical to
                # "nobody has built this yet", which is the opposite of the reason for arming it.
                # The existing gap text is KEPT on the row, so a genuine scrape failure is never
                # dressed up as patience.
                waiting = config.cross_check_waiting_on_primary(name, metric)
                if waiting:
                    row["status"] = "waiting"
                    prior_note = row["note"]
                    row["note"] = (
                        f"WAITING ON THE PRIMARY — this is the cross-check for "
                        f"{waiting['primary']}, which is suppressed because contract(s) "
                        f"{', '.join(waiting['contracts'])} have a DISPUTED role. The guard is "
                        f"armed and correctly idle; it starts comparing the moment the primary "
                        f"becomes reportable."
                        + (f" | {prior_note}" if prior_note else ""))
                # A SERIES WHOSE FIRST EPOCH HAS NOT CLOSED IS PENDING, NOT A GAP (G10 2e: Pendle's subsidy-targeting rows
                # read "gap / RED" while the audit classed them C — first epoch closes 2026-11-03). AMBER, dated.
                fe = config.first_epoch_closes(name, metric)
                if fe and row["status"] in ("missing", "gap") and asof < pd.Timestamp(fe):
                    prior_note = row["note"]
                    row["status"] = "pending"
                    row["note"] = (f"first epoch closes {fe} — no complete epoch before it (valid from {fe})"
                                   + (f" | {prior_note}" if prior_note else ""))
                # A HALTED PROGRAMME READS 0, NOT "missing" (Fluid, Jake 2026-09-29). A closure
                # that records renders_as_zero_since (config.UNAVAILABLE) makes every window that
                # starts on or after that date a measured 0; a window reaching back before it
                # stays empty, because that history is unmeasured, not zero.
                closed = config.unavailable_for(name, metric) or {}
                zero_from = closed.get("renders_as_zero_since")
                if zero_from and not na_note and m["kind"] == "flow":
                    z = pd.Timestamp(zero_from)
                    spans = {"now": (short, 0), "m1": (2 * short, short), "y1": (365, 0),
                             **{q: (period * (i + 1), period * i) for i, q in enumerate(QUARTERS)}}
                    for w, (back, fwd) in spans.items():
                        row[w] = 0.0 if asof - pd.Timedelta(days=back) >= z else None
                    row["status"], row["q0_events"] = "ok", 0
                    row["q0_covered_days"] = period if row["q0"] == 0.0 else None
                    row["source"] = f"closed:{closed.get('closed_on', '')}"
                    row["note"] = (f"0 — {closed.get('summary', '')[:200]} Windows starting before "
                                   f"{zero_from} are unmeasured and left empty.")
                row["confidence"], row["why_amber"] = confidence_for(name, metric, row, asof)
                rows.append(row)
                continue
            g = g.sort_values("date")
            s = g.set_index("date")["value"]
            latest = g.iloc[-1]
            row["source"] = latest["source"]
            # Every distinct place this series was read from. More than one means the window can
            # span a change of address, and the delta across it is not a flow.
            # A READ METHOD CHANGING ON THE SAME CONTRACT is one measuring point while the values
            # agree (Jake, 2026-10-01: Plume's totalAmountStaked vs getValidatorsList.sum(totalStaked)).
            # a DECLARED ZERO after a programme ended (config.DECLARED_ZERO) is the same series, not another point
            g_pts = g[~g["source"].astype(str).str.startswith("declared:zero")]
            mps, row["method_change"] = method_points(g_pts if not g_pts.empty else g)
            row["measuring_points"] = tuple(sorted(set(mps)))
            # WHEN each measuring point was in use. A declared handover is accepted only if the
            # stored dates show no overlap, so the spans have to travel with the row.
            row["point_spans"] = {mp: (gg["date"].min(), gg["date"].max()) for mp, gg in g.groupby(mps)}
            # ...and each point's first and last VALUE, for a handover that declares a max_step at the seam (G9 C2)
            row["point_edges"] = {mp: (float(gg["value"].iloc[0]), float(gg["value"].iloc[-1]))
                                  for mp, gg in g.sort_values("date").groupby(mps)}
            # A DIFFERENCED FLOW IS BOUNDED BY THE STOCK IT CAME FROM. Captured here rather than
            # inside withheld_for because only aggregate() has the series; withheld_for sees one
            # row. Restricted to rows whose source carries the :delta marker, so a genuine
            # period figure from Dune or a dashboard is never measured against a balance it has
            # no arithmetic relationship to.
            parent = flow_stock(name, metric)
            if parent:
                cumulative = latest_value.get((name, parent))
                deltas = g[g["source"].astype(str).str.contains(":delta", na=False)].sort_values("date")
                if cumulative and not deltas.empty:
                    row["cumulative_ref"] = cumulative
                    row["max_single_delta"] = float(deltas["value"].max())
                # THE TELESCOPING IDENTITY. Consecutive differences of one stock must sum to the
                # stock's move across the same span — every intermediate reading cancels. The
                # start point is the last stock reading STRICTLY BEFORE the first delta, because
                # that is the number the first delta was differenced against.
                row.update(telescoping(name, metric, parent, g, groups.get((name, parent))))
            # ===== THE FINE SERIES MUST ADD UP TO THE COARSE PUBLISHED FIGURE. =====
            # Only where every period of it is present: two months of a quarter compared against
            # the quarter's own total is guaranteed to disagree, and flagging that would report a
            # missing month as an error in the months that are there.
            recon = config.period_reconciliation(name, metric)
            if recon:
                row["reconciliation"] = _reconcile_periods(name, metric, s, recon)
            row["tier"] = "" if pd.isna(latest.get("tier")) else int(latest["tier"])
            row["latest_date"] = latest["date"].strftime("%Y-%m-%d")
            row["first_date"] = g["date"].min().strftime("%Y-%m-%d")
            row["n_points"] = int(len(g))
            granularity = config.series_granularity(name, metric)
            row["granularity"] = granularity
            if m["kind"] == "flow" and granularity == "monthly":
                # A MONTHLY SERIES CANNOT FILL A 30-DAY WINDOW. Reporting the latest COMPLETE
                # month, named, beats reporting whatever happened to land inside 30 days — which
                # for GEODNET's buyback was NOTHING at all while 41 good monthly rows sat in the
                # store. The prior column becomes the month before it, so the comparison is
                # month-on-month rather than a 30-day window against a month.
                row["now"], this_month = _latest_complete_month(s, asof)
                if this_month:
                    prior = pd.Period(this_month) - 1
                    p_rows = s[s.index.to_period("M") == prior]
                    row["m1"] = float(p_rows.sum()) if len(p_rows) else None
                    row["period_label"] = this_month
                # Q0..Q4 AND Y1 IN COMPLETE CALENDAR MONTHS (2026-09-28) — see _month_block.
                for i, q in enumerate(QUARTERS):
                    row[q], label = _month_block(s, asof, 3 * i, 3)
                    if q == "q0":
                        row["q0_basis"] = label
                row["y1"], _ = _month_block(s, asof, 0, 12)
                _month_coverage(row, s, asof)
                # EVENTS in the Q0 block: months with a non-zero total (events x cadence, 2026-09-29)
                last_m = asof.to_period("M") - 1
                for i, q in enumerate(QUARTERS):
                    qm = s[s.index.to_period("M").isin({last_m - 3 * i - k for k in range(3)})]
                    row[f"{q}_events"] = int((qm.groupby(qm.index.to_period("M")).sum() > 0).sum())
            elif m["kind"] == "flow":
                row["q0_basis"] = f"trailing {period} days to {asof.date()}"
                row["now"] = _window_sum(s, asof - pd.Timedelta(days=short), asof)
                row["m1"] = _window_sum(s, asof - pd.Timedelta(days=2 * short), asof - pd.Timedelta(days=short))
                for i, q in enumerate(QUARTERS):
                    lo_q, hi_q = asof - pd.Timedelta(days=period * (i + 1)), asof - pd.Timedelta(days=period * i)
                    row[q] = _window_sum(s, lo_q, hi_q)
                    row[f"{q}_events"] = int(((s.index > lo_q) & (s.index <= hi_q) & (s > 0)).sum())
                row["y1"] = _window_sum(s, asof - pd.Timedelta(days=365), asof)
                # HOW MUCH OF THE WINDOW THE SERIES WAS ACTUALLY ALIVE FOR. Only on non-monthly
                # flows: a monthly series reports a named month above and is not pretending to be
                # a 30-day sum, so "covers 30 of 30" would be answering a question nobody asked.
                w_start = asof - pd.Timedelta(days=short)
                diff_first = _differenced_first(g)
                # A SCAN READ FROM THE CHAIN'S START COVERS THE WHOLE WINDOW (Jake's run 2026-10-09 11:41): its days are
                # the scanned range, not the days with events — so coverage counts from the window's start.
                s_cov = (pd.concat([pd.Series([0.0], index=[pd.Timestamp("1970-01-01")]), s])
                         if config.scanned_from_genesis(name, metric) and not s.empty else _after_leg_gap(g, s))
                if len(s_cov) < len(s) and not s_cov.empty:     # the current leg: its own first row decides
                    diff_first = _differenced_first(g[pd.to_datetime(g["date"]).dt.normalize() >= s_cov.index.min()])
                row["covered_days"], row["window_days"] = _window_coverage(s_cov, w_start, asof, diff_first)
                dense = (name, metric) in config.DENSE_DAILY_FLOWS
                # THE SAME TWO QUESTIONS FOR THE Q0 WINDOW, which A4's headline annualises
                # (x days_per_year / period_days). A Q0 sum over a series that began 42 days ago
                # is a 42-day sum; a Q0 holding ONE discrete burn is an event, not a rate. Both
                # are what A4's window caveat reads (_a4_window_caveat). Added 2026-09-25.
                q_start = asof - pd.Timedelta(days=period)
                row["q0_covered_days"], _ = _window_coverage(s_cov, q_start, asof, diff_first)
                if dense:
                    # G9 F1: A SERIES WITH ONE ROW PER DAY BY CONSTRUCTION COUNTS ITS DAYS, NOT ITS SPAN. Ethereum's P2P
                    # leg read GREEN with 37 days held of the 85 since its first (a 48-day hole the span ignores):
                    # a missing day of a daily query is a day not read, never a quiet day.
                    row["covered_days"] = _dense_days(s, w_start, asof)
                    row["q0_covered_days"] = _dense_days(s, q_start, asof)
                    # G9 K3: THE DAYS NOT HELD ARE NAMED, with the series' own reason (CCIP: refused for >10%
                    # unpriced), on the row and on the cell — "Q0 COVERS n OF 90 DAYS — not held: <days>".
                    miss = _dense_missing(s, q_start, asof)
                    if miss:
                        row["q0_dense_note"] = (f"Q0 COVERS {row['q0_covered_days']} OF {period} DAYS — not held: "
                                                f"{_day_ranges(miss)} ({config.DENSE_DAILY_FLOWS[(name, metric)]}); "
                                                f"the Q0 sum is annualised over the {row['q0_covered_days']} "
                                                f"day(s) held")
                # D6-1 (SPEC §1 rule 9): A DAY WITH NO ROW IS A HOLE, NEVER A ZERO. A daily flow's interior days that no row
                # covers (its own day, its [span=Nd], or — a derived row — its inputs' span) come off the span counted above.
                # Not for a scan read from genesis (every scanned day is read), a period-dated (weekly / epoch / monthly)
                # series, or a DENSE_DAILY_FLOWS series (counted already); days after the last row are the lag allowance.
                cov_set = (COVERED_SETS.get((name, metric))
                           if not dense and not config.scanned_from_genesis(name, metric)
                           and _observed_cadence(g) == "daily" else None)
                first_cov = s_cov.index.min() if cov_set and not s_cov.empty else None
                if cov_set:
                    h_w = _interior_holes(cov_set, w_start, asof, first_cov)
                    h_q = _interior_holes(cov_set, q_start, asof, first_cov)
                    row["covered_days"] = max(int(row["covered_days"] or 0) - len(h_w), 0)
                    row["q0_covered_days"] = max(int(row["q0_covered_days"] or 0) - len(h_q), 0)
                    WINDOW_HOLES[(name, metric, "now")], WINDOW_HOLES[(name, metric, "q0")] = h_w, h_q
                    if h_q:
                        row["q0_dense_note"] = (f"Q0 COVERS {row['q0_covered_days']} OF {period} DAYS — holes (no row): "
                                                f"{_day_ranges(h_q)}; the Q0 sum is annualised over the "
                                                f"{row['q0_covered_days']} day(s) covered (SPEC D6-1)")
                row["q0_events"] = int(((s.index > q_start) & (s.index <= asof) & (s > 0)).sum())
                # ** A SCHEDULE THAT HAS ENDED STILL FILLS THE WINDOW BEHIND IT. Added 2026-09-25. **
                # The mirror of a part-filled window: after the declared end the true rate is zero,
                # but Q0 keeps summing the stream's last days until they age out. The end date is
                # the schedule's OWN (config.issuance_schedule_end), and the count uses the same
                # window and first-observation basis as q0_covered_days — never typed in.
                end = (config.issuance_schedule_end(name)
                       if str(latest["source"]).startswith("schedule:config") else None)
                if end:
                    row["schedule_end"] = end
                    end_ts = pd.Timestamp(end)
                    if asof > end_ts:
                        lo = max(q_start + pd.Timedelta(days=1), s.index.min())
                        hi = min(end_ts, asof)
                        row["q0_pre_end_days"] = max(int((hi - lo).days) + 1, 0) if hi >= lo else 0
                # ===== ** A DECLARED SOURCE HOLE COMES OFF THE COVERAGE. Added 2026-09-23. **
                # The coverage above asks how long the series has EXISTED, which is the right
                # question for one that started late and the wrong one for one that stopped and
                # restarted. Morpho's fees_usd has run since 2021 and is missing nine days in
                # the middle because DefiLlama did not report Morpho Blue for them — so it reads
                # as 30 of 30 while the sum omits nine days of ~$600,000 each.
                #
                # ** DECLARED, NOT DETECTED, and that distinction is load-bearing. ** A density
                # check cannot do this: this tool observes on RUN DAYS, so interior gaps are
                # ordinary and counting them reads Hyperliquid's three points across four months
                # as twenty-seven days of absence. Only a hole a human established upstream can
                # be subtracted without guessing. See config.declared_source_hole.
                #
                # NOTHING NEW RENDERS IT. covered < window already puts "COVERS n OF 30 DAYS" on
                # the row and drops the cell to AMBER with the reason — the figure is disclosed,
                # not withheld, because a 21-day total IS the honest 21-day total once the
                # header says so.
                hole = config.declared_source_hole(name, metric)
                if hole and row["window_days"]:
                    h0 = max(pd.Timestamp(hole["from"]), w_start)
                    h1 = min(pd.Timestamp(hole["to"]), asof)
                    if h1 >= h0:
                        # D6-1: a declared hole's days with NO row are already off the coverage (_interior_holes); only
                        # its days that hold a row (a partial figure the source reported) come off here — never twice
                        lost = (sum(1 for d_ in pd.date_range(h0, h1) if d_ in cov_set) if cov_set
                                else int((h1 - h0).days) + 1)
                        row["covered_days"] = max(int(row["covered_days"]) - lost, 0)
                        # the note counts the hole's days the source did NOT report (no row) — not the days
                        # subtracted here, which are 0 when every hole day is already off as an interior hole
                        absent = (sum(1 for d_ in pd.date_range(h0, h1) if d_ not in cov_set) if cov_set
                                  else int((h1 - h0).days) + 1)
                        row["hole_note"] = (
                            f"{absent} day(s) in this window ({h0.date()}..{h1.date()}) are "
                            f"ABSENT because the source did not report them, not because the "
                            f"figure was low. {hole.get('do_not', '')}".strip())
                row["now_covered_days"] = row["covered_days"]
                row["m1_covered_days"], _ = _window_coverage(s_cov, asof - pd.Timedelta(days=2 * short),
                                                             asof - pd.Timedelta(days=short))
                if cov_set:                                  # D6-1: the same holes off every window
                    h_m = WINDOW_HOLES[(name, metric, "m1")] = _interior_holes(
                        cov_set, asof - pd.Timedelta(days=2 * short), asof - pd.Timedelta(days=short), first_cov)
                    row["m1_covered_days"] = max(int(row["m1_covered_days"] or 0) - len(h_m), 0)
                for i, q in enumerate(QUARTERS[1:], start=1):
                    lo_q, hi_q = asof - pd.Timedelta(days=period * (i + 1)), asof - pd.Timedelta(days=period * i)
                    row[f"{q}_covered_days"], _ = _window_coverage(s, lo_q, hi_q)
                    if cov_set:
                        h_i = WINDOW_HOLES[(name, metric, q)] = _interior_holes(cov_set, lo_q, hi_q, s.index.min())
                        row[f"{q}_covered_days"] = max(int(row[f"{q}_covered_days"] or 0) - len(h_i), 0)
                # The USD twin of a relabelled buyback carries "derived:tokens*price"; its legs
                # are read off the tokens series it was priced from.
                legs_g = (groups.get((name, "actual_buyback_tokens"))
                          if metric == "actual_buyback_usd" else None)
                _months_until_live(row, name, metric, g if legs_g is None else legs_g, s, asof,
                                   short, period)
            else:
                row["now"] = float(latest["value"])
                # Kept apart from `now`, which is blanked when a figure is withheld: case 9 of
                # withheld_for needs the stored value itself, on both of its calls.
                row["latest_value"] = row["now"]
                row["m1"] = _at_or_before(s, asof - pd.Timedelta(days=short))
                for i, q in enumerate(QUARTERS):
                    row[q] = _window_mean(s, asof - pd.Timedelta(days=period * (i + 1)), asof - pd.Timedelta(days=period * i))
                row["y1"] = _window_mean(s, asof - pd.Timedelta(days=365), asof)
            # ===== LAST NON-ZERO, AND IS THE PROGRAM SILENT? Added 2026-09-28. =====
            # A series of zeros is a measured "nothing happened", and it says nothing about WHEN
            # something last did. For a flow the last non-zero day comes from the stored series
            # (a monthly row counts to its month's END — it covers the whole month); for the
            # buyback, the scan's own stored last-inflow date reaches past the stored window.
            if m["kind"] == "flow":
                pos = s[s > 0]
                last_nz = None
                if len(pos):
                    last_nz = pos.index.max()
                    if granularity == "monthly":
                        last_nz = last_nz + pd.offsets.MonthEnd(0)
                if metric == "actual_buyback_tokens":
                    gd = groups.get((name, "buyback_last_inflow_date"))
                    if gd is not None and not gd.empty:
                        serial = float(gd.sort_values("date").iloc[-1]["value"])
                        # THE SCAN'S DATE IS AUTHORITATIVE (2026-09-28), not max() with the
                        # stored series: it is computed on the scan's CURRENT attribution, and
                        # stored rows can predate an attribution change — Ether.fi's non-CoW
                        # 2026-06-30 inflow made the flag read June when the last CoW purchase
                        # was 2026-04-01.
                        last_nz = pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(serial))
                if last_nz is not None:
                    row["last_nonzero_date"] = last_nz.strftime("%Y-%m-%d")
                # SILENT = more than 2x the LONGEST documented cadence since the last non-zero
                # value, AND the series has been observed past that point — so a gap in OUR runs
                # (staleness, reported elsewhere) is never mistaken for a quiet program.
                cad = config.program_cadence(name, metric)
                if cad and last_nz is not None:
                    # CADENCE + SOURCE LAG + SLACK (config.PROGRAM_SILENCE_DEFAULTS; Jake's review of 97b29ae item 15)
                    longest = max(d for _, d in cad["cadences"])
                    lag = int(cad.get("source_lag_days", config.PROGRAM_SILENCE_DEFAULTS["source_lag_days"]))
                    slack = int(cad.get("slack_days", config.PROGRAM_SILENCE_DEFAULTS["slack_days"]))
                    limit = longest + lag + slack
                    observed_quiet = (g["date"].max() - last_nz).days
                    silent = (asof - last_nz).days
                    row["silent_days"] = silent
                    # A declared day count (GEODNET: 2 days with no burn) replaces the generic rule.
                    after = cad.get("silent_after_days")
                    if after:
                        limit = after
                    if (observed_quiet >= after) if after else (silent > limit and observed_quiet > longest + slack):
                        labels = "/".join(l for l, _ in cad["cadences"])
                        row["silence_flag"] = (f"silent since {last_nz.date()} — {silent} days "
                                               f"against a documented {labels} cadence (last "
                                               f"non-zero {last_nz.date()}; flagged {'at' if after else 'past'} {limit} days)")
                        row["note"] = (f"PROGRAM SILENT — {row['silence_flag']}"
                                       + (f" | {row['note']}" if row["note"] else ""))
            base_source = str(latest["source"]).split(":")[0]
            # A DERIVED ROW'S FETCH IS THE ADAPTER BEHIND IT (accuracy pass on 1e702bb, item 14: Aerodrome's buyback read
            # "last successful fetch never" — 'derived' is no adapter; the Foundation window scan, fetch/ve_managed.py, is)
            fb = p.get("foundation_buyback") or {}
            if base_source in ("derived", "trace") and metric in (fb.get("flow"), "actual_buyback_usd") and fb:
                base_source = "ve_managed"
            row["last_success"] = last_success.get((base_source, name), "") or ""
            if bool(latest["is_manual"]):
                row["status"], row["entered_on"] = "manual", latest["entered_on"]
                row["note"] = latest.get("source_note", "") or ""
            elif base_source == "schedule":
                row["status"] = "ok"
            elif _age_in_days(latest["date"], asof, granularity) > _stale_limit(name, metric, stale_days, g):
                # THE THRESHOLD FOLLOWS THE CADENCE. A monthly series cannot have a point from
                # this week — the incomplete current month is dropped by design — so judging it
                # against the 7-day global marked a perfectly current series stale forever.
                row["status"] = "stale"
                limit = row["stale_limit"] = _stale_limit(name, metric, stale_days, g)
                # ===== WHOSE STALENESS IS IT? Added 2026-09-22. =====
                # "last point 2026-09-10; last successful fetch 2026-09-22" reads as a pipeline
                # that has fallen behind, and sends the reader to check the fetch. For Ether.fi
                # the fetch ran and SUCCEEDED — Dune query 8683038's own data ends 2026-09-10,
                # and no re-run produces a newer point. That is the source ageing, not us, and
                # it needs a different action: chase the query, not the run.
                #
                # DERIVED FROM THE TWO DATES ALREADY ON THE ROW, never declared: a fetch that
                # succeeded INSIDE the staleness window while the data did not move can only
                # mean the source had nothing newer to give.
                upstream = ""
                try:
                    if row["last_success"] and _age_in_days(
                            pd.Timestamp(str(row["last_success"])[:10]), asof, "daily") <= limit:
                        upstream = (" — ** THE SOURCE IS STALE, NOT THE FETCH. ** The last fetch "
                                    "SUCCEEDED inside the window and returned nothing newer, so "
                                    "the series has stopped at its publisher. Chase the source, "
                                    "not the run.")
                except (TypeError, ValueError):
                    upstream = ""
                fb_why = ""
                if (p.get("foundation_buyback") or {}).get("trace") and metric in (
                        "actual_buyback_tokens", "actual_buyback_usd") and str(latest["source"]).startswith(
                        ("derived:foundation_buyback[trace", "derived:tokens*price[trace")):
                    fb_why = (" — the series is still the hand TRACE (its last matched purchase); the Foundation buyback "
                              "window scan (fetch/ve_managed.py, needs BASE_RPC_URL) has stored no day. "
                              "Seed it: python token_metrics.py --seed aero_buyback")
                row["note"] = (f"last point {row['latest_date']}; last successful fetch "
                               f"{row['last_success'] or 'never'}"
                               + (f"; {granularity} series, stale after {limit} days"
                                  if granularity != "daily" else "") + upstream + fb_why)
            else:
                row["status"] = "ok"
            if (name, metric) in review_keys:
                row["status"] = "review" if row["status"] == "ok" else row["status"]
                row["note"] = (row["note"] + " | " if row["note"] else "") + "flagged in the Review Queue"
            # A MODELLED LEG, SAID ON THE ROW (Jake 2026-10-12, Phase 1 follow-up item 2)
            if g is not None and not g.empty:
                mod = g[g["source"].astype(str).map(config.is_modelled)]
                if not mod.empty:
                    md = pd.to_datetime(mod["date"])
                    row["note"] = ((row["note"] + " | " if row["note"] else "")
                                   + f"MODELLED leg {md.min().date()}..{md.max().date()} ({len(mod)} day(s)) — "
                                   f"{config.MODELLED_POINTS[_measuring_point(str(mod['source'].iloc[0]))]}; "
                                   f"excluded from any scoring")
                    # G11 D1 (Jake's 10-21 run: World Mobile's whitepaper-curve emissions read GREEN): a modelled leg
                    # is never graded like a measurement
                    row["modelled"] = f"MODELLED leg {md.min().date()}..{md.max().date()} ({len(mod)} day(s))"
            # G11 D1: A DECLARED SCHEDULE STANDING IN FOR A MEASUREMENT (config.SCHEDULE_LEGS — Aethir's emissions and
            # (b) back-filled with a constant from schedule:config before the measured series)
            sl = config.SCHEDULE_LEGS.get((name, metric))
            if sl:
                gs = groups.get((name, sl.get("from", metric)))
                if gs is not None and not gs.empty:
                    sd = pd.to_datetime(gs.loc[gs["source"].astype(str).str.startswith(sl["prefix"]), "date"])
                    if len(sd):
                        lab = f"SCHEDULE leg {sd.min().date()}..{sd.max().date()} ({len(sd)} day(s)) — {sl['why']}"
                        row["note"] = (row["note"] + " | " if row["note"] else "") + lab + "; not a measurement"
                        row["modelled"] = (row.get("modelled") + " | " if row.get("modelled") else "") + \
                            f"SCHEDULE leg {sd.min().date()}..{sd.max().date()}"
            # G14 1 (Jake's 10-22 run: the chain tier timed out and CoinGecko's total took the day): A CHAIN-READ STOCK
            # NOT READ ON THE PROJECT'S LATEST DAY keeps its last stored value — never another source's — and says so,
            # dated (AMBER via confidence_for). The project's latest day is its newest price row.
            last_read = not_read_since(groups, name, metric, g)
            if last_read:
                row["not_read"] = last_read
                row["note"] = (row["note"] + " | " if row["note"] else "") + last_read
            # G15 5: a provisional latest day, stored but left out of the windows — said, with when it was read
            if (name, metric) in PROVISIONAL:
                prov = PROVISIONAL[(name, metric)]
                row["note"] = (row["note"] + " | " if row["note"] else "") + (
                    "PROVISIONAL: " + ", ".join(f"{d} read {str(fa)[:16]}Z" for d, fa in prov)
                    + " — this series' latest day often reads low when first read ("
                    + config.provisional_series().get((name, metric), "listed") + "); not in any window or change "
                      "flag until a later run re-reads it")
            # P2c-2: a declared series break whose named day this series holds (Aerodrome 2026-10-21), said on the row
            if (name, metric) in SERIES_BREAK_NOTED:
                row["note"] = (row["note"] + " | " if row["note"] else "") + SERIES_BREAK_NOTED[(name, metric)]
            # P2d-4: A DECLARED SERIES START INSIDE THE HISTORY WINDOW (config.ARCHIVE_SERIES_START with `absent`: Pendle
            # cooldown_days, sPENDLE deployed 2026-01-15) — the days before are a pre-deployment absence, named with the
            # date FIRST (so every n/a window cell of the row carries it), never a hole to fill
            pre = config.series_absent_before(
                name, metric, asof - pd.Timedelta(days=int(config.TRAJECTORY["history_days"])))
            if pre:
                row["note"] = pre + (f" | {row['note']}" if row["note"] else "")
            # ================ ALL SEVEN WITHHELD CASES, HANDLED IN ONE PLACE ================
            # A config change guards the WRITE path and cannot touch what is already stored, and
            # aggregate() consults the Gap Report only when the store has NO rows for a key
            # (`if g is None or g.empty`, far above). So a stale figure wins by default, every
            # time, for every mechanism. Maple's 0.51 SYRUP was the first found; Ether.fi's
            # sETHFI rows and GEODNET's derived zero were the same bug wearing different hats.
            #
            # THE VALUE IS BLANKED, NOT FLAGGED — and as of 2026-09-15 that is true of all of
            # them rather than three. Three of them used to go RED and print the number anyway, which
            # asks a reader to notice a colour before trusting an arithmetic result. A wrong
            # number in a cell is worse than an empty one: a number gets acted on, a blank gets
            # asked about. The reason travels with the row in `note`, so nothing is lost.
            #
            # EVERY WINDOW IS BLANKED TOO, not just `now`. A trajectory built on a withdrawn
            # figure is still a withdrawn figure, and q0..q3 are exactly where a reader would go
            # looking for the number the blank cell did not give them.
            # ** SAY WHAT THE NUMBER ACTUALLY COVERS. ** Two different ways a figure can sit
            # under a header that overstates it, both disclosed on the row rather than left for a
            # reader to infer from the point count.
            if row["period_label"]:
                row["note"] = (f"{granularity} series — 'now' is the latest COMPLETE month "
                               f"({row['period_label']}), not a trailing 30-day sum"
                               + (f" | {row['note']}" if row["note"] else ""))
            covered, window = row["covered_days"], row["window_days"]
            if window and covered is not None and covered < window:
                # TWO REASONS A WINDOW IS SHORT, AND THEY SEND THE READER TO DIFFERENT PLACES. A
                # series that has not existed long enough is nobody's fault and needs no action;
                # a source that stopped reporting for nine days is a fact about the upstream
                # feed, and saying only "history does not span the window" would be false there.
                why = (row.get("hole_note")
                       or (f"this series' history does not span the full window, so the figure "
                           f"is a {covered}-day total under a {window}-day header, not a short "
                           f"{window} days"))
                row["note"] = (f"COVERS {covered} OF {window} DAYS — {why}"
                               + (f" | {row['note']}" if row["note"] else ""))
            if row.get("q0_dense_note"):
                row["note"] = row["q0_dense_note"] + (f" | {row['note']}" if row["note"] else "")
            ev_ = EVIDENCE_RESULTS.get((name, metric))
            if ev_ and ev_.get("check"):            # G9 N: the series' step and its check, said on its row
                row["note"] = (f"{ev_['label']} — {ev_['verdict']}: {ev_['check']}"
                               + (f" | {row['note']}" if row["note"] else ""))[:1200]

            # A SUPPRESSION WITH PROOF THAT THE QUANTITY IS ZERO IS NOT A WITHHELD FIGURE.
            # GEODNET's issuance is the case: total_supply_gross reads exactly 1,000,000,000 and
            # does not move, so nothing is minted — the derivation is switched off because a
            # derived 0 would read as "no emissions" when emissions are real and are DISTRIBUTION
            # from pre-minted wallets. RED there says "wrong number"; there is no number.
            # A BUYBACK COLUMN ON A PROJECT THAT BUYS NOTHING READS n/a, WHATEVER IS STORED.
            # Ethereum, 2026-09-28: 1,389.76 ETH of stored :as-buyback rows (the EIP-1559 burn
            # re-labelled) kept rendering after the route became "none", because the store never
            # deletes. orphan_cleanup.sql section AO lists them for review.
            if metric in config.BUYBACK_METRICS + config.BUYBACK_SPLIT_METRICS and \
                    config.buyback_route(name)["route"] == "none":
                row["status"] = "n/a"
                row["note"] = (f"{config.buyback_route(name)['reason']} {len(g)} stored row(s) "
                               f"predate this and are not shown — orphan_cleanup.sql section AO.")
                for col in ("now", "m1", *QUARTERS, "y1"):
                    row[col] = None
                row["confidence"], row["why_amber"] = confidence_for(name, metric, row, asof)
                rows.append(row)
                continue
            supp = config.derivation_suppressed(name, metric) or {}
            if supp.get("renders_as") == "n/a":
                row["status"] = "n/a"
                row["note"] = supp.get("na_reason") or supp.get("why", "")
                for col in ("now", "m1", *QUARTERS, "y1"):
                    row[col] = None
                row["confidence"], row["why_amber"] = confidence_for(name, metric, row, asof)
                rows.append(row)
                continue
            withheld = withheld_for(name, metric, row)
            if na_note and (not withheld or withheld[0] == "blocked"):
                row["status"] = "n/a"
                row["note"] = f"{na_note} {len(g)} stored row(s) are not shown (orphan_cleanup.sql lists them for review)."
                for col in ("now", "m1", *QUARTERS, "y1"):
                    row[col] = None
            elif withheld:
                row["status"], reason = withheld
                row["note"] = f"NOT REPORTED — {reason}" + (f" | {row['note']}" if row["note"] else "")
                for col in ("now", "m1", *QUARTERS, "y1"):
                    row[col] = None
                held[(name, metric)] = withheld
            # A DECLARED ZERO NEVER CLAIMS HISTORY BEFORE ITS `since` (alignment round B10): with no stored row before
            # the programme ended, a window that starts before that date is unmeasured, not 0 (Maple's Y1 would read 0
            # emissions over MIP-009's last months)
            dz = config.DECLARED_ZERO.get((name, metric))
            if dz:
                since = pd.Timestamp(dz["since"])
                real = g[~g["source"].astype(str).str.startswith("declared:zero")]
                if real.empty or pd.to_datetime(real["date"]).min() >= since:
                    spans = {"now": short, "m1": 2 * short, "y1": 365,
                             **{q: period * (i + 1) for i, q in enumerate(QUARTERS)}}
                    for w, back in spans.items():
                        if asof - pd.Timedelta(days=back) < since - pd.Timedelta(days=1):
                            row[w] = None
            # A HALTED PROGRAMME'S DECLARED ZEROS KEEP THE HALT ON THE ROW (alignment round B10: Fluid's buyback reads
            # its 0 days from config.DECLARED_ZERO so holder value reads 0, not n/a — the closure's own note stays)
            closed = config.unavailable_for(name, metric) or {}
            if closed.get("renders_as_zero_since") and str(latest["source"]).startswith("declared:zero") \
                    and row["status"] == "ok":
                row["note"] = (f"0 — {closed.get('summary', '')[:200]}"
                               + (f" | {row['note']}" if row["note"] else ""))
            row["confidence"], row["why_amber"] = confidence_for(name, metric, row, asof)
            rows.append(row)
    rows += _macro_rows(groups, asof)          # Phase 3: the global series (the T-bill), one Data row each
    # THE BUYBACK SPLIT INHERITS ITS PARENT'S WITHHOLDING (Jake's run on 20d0eb4: Aerodrome's Q0 buyback read n/a as
    # BUG while the retirement rate E14 read 0.97% and net absorption AH14 -2.076M — the split views are computed from
    # the same rows the parent blanks, under a source of their own that passed the check).
    for row in rows:
        parent = ("actual_buyback_usd" if row["metric"] == "retired_buyback_usd" else "actual_buyback_tokens")
        if row["metric"] in config.BUYBACK_SPLIT_METRICS and (row["project"], parent) in held:
            status, reason = held[(row["project"], parent)]
            row["status"] = status
            row["note"] = f"NOT REPORTED — inherits {parent}'s: {reason}"
            for col in ("now", "m1", *QUARTERS, "y1"):
                row[col] = None
            row["confidence"], row["why_amber"] = confidence_for(row["project"], row["metric"], row, asof)
    # MIGRATION WATCH (Jake, 2026-10-11): from label_from until the RUNBOOK plan is built, the watched rows say so
    mw = config.AERODROME_MIGRATION_WATCH
    if migration_watch_on(asof):
        for row in rows:
            if row["project"] == mw["project"] and row["metric"] in mw["label_metrics"]:
                row["note"] = mw["label"] + (f" | {row['note']}" if row.get("note") else "")
    return pd.DataFrame(rows, columns=DATA_COLS)


def _macro_rows(groups: dict, asof: pd.Timestamp) -> list[dict]:
    """ONE DATA ROW PER GLOBAL SERIES (Phase 3, 2026-10-12 night; config.MACRO_METRICS under config.MACRO_PROJECT): a
    stock's 'now' is its latest value within 7 days (a T-bill is not quoted on weekends and holidays), each quarter the
    mean of its days, as for any stock. Missing, the row says so; the T-bill benchmark then reads n/a."""
    out = []
    period = GLOBALS["period_days"]
    for metric in config.MACRO_METRICS:
        m = METRICS[metric]
        row = {c: None for c in DATA_COLS}
        row.update({"key": f"{config.MACRO_PROJECT}|{metric}", "project": config.MACRO_PROJECT, "metric": metric,
                    "label": m["label"], "kind": m["kind"], "unit": m["unit"], "source": "", "tier": "",
                    "latest_date": "", "n_points": 0, "status": "missing", "note": "", "confidence": "",
                    "why_amber": "", "last_success": "", "entered_on": ""})
        g = groups.get((config.MACRO_PROJECT, metric))
        if g is not None and not g.empty:
            s = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
                 .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float))
            last = s.index[-1]
            row.update({"source": str(g.sort_values("date")["source"].iloc[-1]), "tier": 1,
                        "latest_date": last.strftime("%Y-%m-%d"), "first_date": s.index[0].strftime("%Y-%m-%d"),
                        "n_points": int(len(s))})
            fresh = (asof - last).days <= 7
            row["now"] = float(s.iloc[-1]) if fresh else None
            row["status"] = "ok" if fresh else "stale"
            if not fresh:
                row["note"] = f"latest {last.date()} is more than 7 days before {asof.date()}"
            for i, q in enumerate(QUARTERS):
                row[q] = _window_mean(s, asof - pd.Timedelta(days=period * (i + 1)), asof - pd.Timedelta(days=period * i))
        out.append(row)
    return out


def v3_rows_since(groups: dict | None = None) -> str | None:
    """The first date a row from the post-migration reader (config.AERODROME_MIGRATION v3_source) is held, else None."""
    m = config.AERODROME_MIGRATION
    first = None
    for (proj, _metric), g in (_LAST_GROUPS if groups is None else groups).items():
        if proj != m["project"] or g is None or g.empty or "source" not in g:
            continue
        x = g[g["source"].astype(str).str.startswith(m.get("v3_source", "aero_v3"))]
        if not x.empty:
            d = pd.to_datetime(x["date"]).min()
            first = d if first is None or d < first else first
    return None if first is None else str(first.date())


def migration_watch_on(asof, groups: dict | None = None) -> bool:
    """config.AERODROME_MIGRATION_WATCH applies from label_from until `built` — set in config, or (Round G 2.10: only
    aero_migration.json needs filling on launch day) the first day the v3 reader's rows are held."""
    mw = config.AERODROME_MIGRATION_WATCH
    if mw.get("built") or v3_rows_since(groups):
        return False
    return pd.Timestamp(asof).normalize() >= pd.Timestamp(mw["label_from"])


def monthly_table(long: pd.DataFrame, asof: pd.Timestamp) -> tuple[list[str], pd.DataFrame]:
    n = GLOBALS["months_in_monthly_sheet"]
    end = asof.to_period("M")
    months = [str(end - i) for i in range(n - 1, -1, -1)]
    rows = []
    groups = {k: g for k, g in long.groupby(["project", "metric"])} if not long.empty else {}
    for p in scoped_projects():
        for metric in MONTHLY_METRICS:
            m = METRICS[metric]
            g = groups.get((p["name"], metric))
            row = {"key": f"{p['name']}|{metric}",
               "label": f"{p['name']} — {config.metric_label(p['name'], metric)}",
               "kind": m["kind"], "unit": config.metric_unit(p["name"], metric)}
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
# THE TRAJECTORY SHIFT (theory layer, Phase 1, 2026-10-12; trajectory.py): while k > 0 every Data reference a builder
# writes is moved k quarters back, so re-running a headline's own builder gives that headline k quarters ago.
_SHIFT = {"k": 0}
_SHIFT_NA = {"m1", "m1_covered_days", "y1", "now_covered_days", "q0_pre_end_days", "silent_days"}


def _shift_col(metric: str, col: str, k: int) -> str | None:
    """The Data column `col` k quarters back, or None (the reference becomes NA()) where the window has no counterpart:
    a flow's trailing-30-day 'now', the prior 30 days, Y1, a quarter past Q4."""
    m = re.fullmatch(r"q(\d)(_covered_days|_events)?", col)
    if m:
        j = int(m.group(1)) + k
        return f"q{j}{m.group(2) or ''}" if f"q{j}" in QUARTERS else None
    if col == "now":
        return f"q{k}" if (METRICS.get(metric) or {}).get("kind") == "stock" and f"q{k}" in QUARTERS else None
    if col in _SHIFT_NA:
        return None
    return col


class shifted:
    """with shifted(k): builders write their formulas k quarters back."""
    def __init__(self, k: int):
        self.k = k

    def __enter__(self):
        self.prev, _SHIFT["k"] = _SHIFT["k"], self.k
        return self

    def __exit__(self, *exc):
        _SHIFT["k"] = self.prev
        return False


_PROJECT = {"name": None}         # the project whose row is being built (G13 2: config.metric_alias)


@contextlib.contextmanager
def _project_row(name: str | None):
    was = _PROJECT["name"]
    _PROJECT["name"] = name
    try:
        yield
    finally:
        _PROJECT["name"] = was


class Refs:
    def __init__(self, data_rows: int, monthly_rows: int, months: list[str]):
        self.d0, self.d1 = 2, data_rows + 1
        self.m0, self.m1 = 2, monthly_rows + 1
        self.months = months
        self.mc0 = "E"                                   # first month column on Monthly
        self.mc1 = get_column_letter(4 + len(months))

    def D(self, r: int, metric: str, col: str = "q0") -> str:
        # ONE DEFINITION PER PROJECT (G13 2): while a project's row is built, a metric it defines otherwise reads its own
        # (World Mobile's fees are its own chain's gas: every fee-derived cell, Δ and ratio included, not fees_usd)
        if _PROJECT["name"]:
            metric = config.metric_alias(_PROJECT["name"], metric)
        if _SHIFT["k"]:
            col = _shift_col(metric, col, _SHIFT["k"])
            if col is None:
                return "NA()"
        c = DC[col]
        return f'INDEX(Data!${c}${self.d0}:${c}${self.d1},MATCH($A{r}&"|{metric}",Data!$A${self.d0}:$A${self.d1},0))'

    def X(self, metric: str, col: str = "now") -> str:
        """A GLOBAL series' Data cell (config.MACRO_PROJECT, e.g. the 3-month T-bill): the same on every row; a shifted
        quarter (the trajectory engine) takes that quarter's mean."""
        if _SHIFT["k"]:
            col = _shift_col(metric, col, _SHIFT["k"])
            if col is None:
                return "NA()"
        c = DC[col]
        return (f'INDEX(Data!${c}${self.d0}:${c}${self.d1},MATCH("{config.MACRO_PROJECT}|{metric}",'
                f'Data!$A${self.d0}:$A${self.d1},0))')

    def C(self, r: int, colname: str) -> str:
        if _SHIFT["k"] and colname in _WINDOW_COLS_BY_NAME:
            kind, q = _WINDOW_COLS_BY_NAME[colname]
            j = int(q[1:]) + _SHIFT["k"]
            table = WINDOW_SHARE_COL if kind == "share" else WINDOW_STATUS_COL
            if f"q{j}" not in table:
                # G11 A3: Config resolves the split for Q0..Q-3 only; Q-4 says so in words, as those windows do when no
                # split is documented — NA() made every implied-buyback Q-4 cell #N/A (its status compared an error)
                return ('"n/a — no split for this window (Config & Sources resolves Q0..Q-3)"' if kind == "share"
                        else '"n/a — no split documented for this window"')
            colname = table[f"q{j}"]
        c = CC[colname]
        r1 = _cfg_r1()
        return f"INDEX({CFG}!${c}${CFG_R0}:${c}${r1},MATCH($A{r},{CFG}!$A${CFG_R0}:$A${r1},0))"

    def M(self, r: int, metric: str, month_cell: str) -> str:
        if _SHIFT["k"]:
            return "NA()"                                # a month column is not a trailing window
        return (f'INDEX(Monthly!${self.mc0}${self.m0}:${self.mc1}${self.m1},'
                f'MATCH($A{r}&"|{metric}",Monthly!$A${self.m0}:$A${self.m1},0),'
                f'MATCH({month_cell},Monthly!${self.mc0}$1:${self.mc1}$1,0))')


def pull(expr: str) -> str:
    return f"={expr}"


def calc(expr: str) -> str:
    return f"=IFERROR({expr},{NA})"


def _burn_fee_base(p: dict) -> str:
    """The fee series a documented burn share is a share OF: config burn_split.fee_base_metric, else fees_usd."""
    return (p.get("burn_split") or {}).get("fee_base_metric") or "fees_usd"


IMPLIED_ACTUAL_ON = {"buyback": "'Actual buyback Q0 ($) — observed' (A3)", "burn": "'GROSS BURN Q0 (tokens)' (A4)"}


def gated(status_expr: str, expr: str, share_expr: str | None = None, kind: str = "buyback") -> str:
    """Suppress the derived figure when the split is unconfirmed; n/a when no share is documented.
    G10 2a (Jake 2026-10-21): an unconfirmed split is NOT a missing value — the protocol publishes no split, so the
    implied figure does not exist by design; the cell says so and names where the ACTUAL figure is (N/A BY DESIGN)."""
    if isinstance(share_expr, str) and share_expr.startswith('"n/a'):
        return f"={share_expr}"                         # G11 A3: a window Config does not resolve says so
    inner = f"IFERROR({expr},{NA})"
    if share_expr:
        inner = f"IF(ISNUMBER({share_expr}),{inner},{NA})"
    return (f'=IF({status_expr}="unconfirmed","n/a — no published buyback/burn split (config fee_split status '
            f'unconfirmed) — the actual {kind} is on {IMPLIED_ACTUAL_ON[kind]}",{inner})')


def threshold_gated(project: dict, expr: str) -> str:
    """Suppress a derived buyback where the buyback is a SWITCH rather than a rate.

    Fluid's buyback activates at $10m annualised protocol revenue. Below that there is no
    buyback at all, so applying a share to revenue does not produce a small buyback — it
    produces one that does not exist. The switch is decided by the protocol's governance, not by
    our revenue figure: inferring it from our own number is circular, and the protocol may not
    measure revenue the way DefiLlama does.
    """
    t = project.get("buyback_threshold")
    if not t:
        return expr
    if t.get("status") == "active":
        return expr
    label = ("below threshold" if t.get("status") == "below_threshold"
             else "threshold unconfirmed")
    return f'="{label}"'


def _reconcile_periods(project: str, metric: str, s: pd.Series, recon: dict) -> dict | None:
    """Sum the stored fine-grained series by coarse period and compare against the published one.

    Returns None where nothing can be judged — no reference, or no coarse period whose parts are
    ALL present. That is the common case early in a series' life and it is not a finding: it is
    the check armed and correctly idle, the same state as a cross-check waiting on its primary.
    """
    p = config.PROJECT_BY_NAME.get(project) or {}
    ref = (p.get(recon.get("reference") or "") or {}).get(recon.get("against") or "") or []
    if s.empty or not ref:
        return None
    months = {pd.Period(d, freq="M"): float(v) for d, v in s.items()}
    tol = float(recon.get("tolerance", 0.005))
    checked, disagreed = [], []
    for entry in ref:
        try:
            q = pd.Period(str(entry["period"]).replace("-Q", "Q"), freq="Q")
        except ValueError:
            continue                            # an annual reference has no month decomposition
        parts = [m for m in months if m.asfreq("Q") == q]
        want = [q.start_time.to_period("M") + i for i in range(3)]
        if recon.get("requires_complete_period", True) and not all(w in months for w in want):
            continue
        got, expected = sum(months[m] for m in parts), float(entry["usd"])
        checked.append(str(entry["period"]))
        if expected and abs(got - expected) / abs(expected) > tol:
            disagreed.append({"period": str(entry["period"]), "summed": got, "published": expected,
                              "off_by": got - expected})
    if not checked:
        return {"status": "idle", "why": (
            f"no complete {recon.get('against', 'period')} yet — the check needs every part of a "
            f"period present before it can compare, because a partial sum against a published "
            f"total is guaranteed to disagree. Armed and correctly silent.")}
    if disagreed:
        return {"status": "disagrees", "periods": checked, "detail": disagreed,
                "why": recon.get("why", "")}
    return {"status": "ok", "periods": checked}


def telescoping(project: str, metric: str, parent: str, series, stock) -> dict:
    """Reconcile a differenced flow against the stock it came from, or say why it cannot be.

    THE IDENTITY: consecutive differences of one stock telescope, so summing them over a span
    must give the stock's move across that span exactly — every intermediate reading appears
    once with each sign and cancels.

    ** A CHECK THAT CANNOT RUN SAYS SO. ** This was an inline block with a silent `if`: no
    anchor, no reconciliation, no trace. A row whose check never ran is indistinguishable on the
    sheet from one that ran and passed, which is how a guard built for Hyperliquid could have
    failed to see Hyperliquid. Every branch leaves a reason behind now, and it is a function
    rather than four levels of nesting so the reasons can be tested.
    """
    deltas = series[series["source"].astype(str).str.contains(":delta", na=False)].sort_values("date")
    if deltas.empty:
        return {"flow_recon_blocked": (
            f"no differenced rows: this {metric} series carries no source marked :delta, so "
            f"there is nothing to telescope. A backfilled period figure is not a difference of "
            f"two readings and is correctly exempt.")}
    if stock is None or stock.empty:
        return {"flow_recon_blocked": (
            f"the stock series {parent!r} is EMPTY for this project, so the flows have nothing "
            f"to be reconciled against. They were differenced from something; if that something "
            f"is not in the store, the rows that produced them have been deleted or were written "
            f"under another name.")}
    st = stock.sort_values("date")
    # OVER THE DATES BOTH HOLD (G14 2, Jake's 10-22 run: GEODNET's and Hyperliquid's burn flows ran to 10-22 while the
    # stock's tier timed out at 10-21 — "THE FLOWS DO NOT SUM", −105,000 and −22,648, 9 BROKEN cells). A flow row dated
    # after the stock's last reading is not unaccounted for: the reading it telescopes to is not stored yet. The check
    # ends at the earlier of the two series' last days and says which flow rows wait.
    end = min(deltas.iloc[-1]["date"], st.iloc[-1]["date"])
    waiting = deltas[deltas["date"] > end]
    deltas = deltas[deltas["date"] <= end]
    if deltas.empty:
        return {"flow_recon_blocked": (
            f"no {parent} reading on or after the first {metric} row ({pd.Timestamp(waiting.iloc[0]['date']):%Y-%m-%d})"
            f" — the stock series ends before the flow series begins.")}
    first, last = deltas.iloc[0]["date"], deltas.iloc[-1]["date"]
    ends, starts = st[st["date"] <= last], st[st["date"] < first]
    if starts.empty:
        # THE ANCHOR IS THE NUMBER THE FIRST DELTA WAS SUBTRACTED FROM. Its absence cannot happen
        # from a clean run — a delta needs a reading strictly earlier than itself, so that reading
        # existed when the flow was written. Missing now means stock rows were removed.
        return {"flow_recon_blocked": (
            f"NO STOCK READING BEFORE THE FIRST FLOW. The earliest {metric} row is dated "
            f"{pd.Timestamp(first):%Y-%m-%d} and the earliest {parent} row is "
            f"{pd.Timestamp(st.iloc[0]['date']):%Y-%m-%d} — but a differenced flow is computed "
            f"from a reading STRICTLY EARLIER than itself, so that reading existed when the flow "
            f"was written and is not in the store now. The flows cannot be checked against a "
            f"stock whose start has been cut off.")}
    if ends.empty:
        return {"flow_recon_blocked": (
            f"no {parent} reading at or before the last {metric} row "
            f"({pd.Timestamp(last):%Y-%m-%d}) — the stock series ends before the flow series does.")}
    moved = float(ends.iloc[-1]["value"]) - float(starts.iloc[-1]["value"])
    # A DATED ONE-OFF moved out of the flow (_one_off_views) is still part of the stock's move.
    accounted = float(deltas["value"].sum()) + _ONE_OFF_REMOVED.get((project, metric), 0.0)
    return {"flow_stock_move": moved, "flow_accounted": accounted,
            "flow_residual": moved - accounted,
            "flow_span": (starts.iloc[-1]["date"].strftime("%Y-%m-%d"),
                          ends.iloc[-1]["date"].strftime("%Y-%m-%d")),
            "flow_recon_blocked": None,
            "flow_recon_waiting": (f"{len(waiting)} {metric} row(s) after {pd.Timestamp(end):%Y-%m-%d} wait for the "
                                   f"{parent} reading they telescope to; reconciled over the dates both hold"
                                   if len(waiting) else None)}


def _burn_leg_dbd(project: dict) -> str | None:
    """G10 3a: a Q0 window applied day by day has no burn-leg share for its days before the Stage 2 date — said, never the
    Stage 2 5% applied backwards. None where the window is not applied day by day."""
    if _SHIFT["k"] or not _DAY_BY_DAY.get((project["name"], "q0")) or not _leg(project, "burn", lambda sh: "x"):
        return None
    rb = config.revenue_base(project["name"]) or {}
    # the date first: a formula's text literal is cut at 255 characters
    return (f'="n/a — BLOCKED: valid from {(pd.Timestamp(rb.get("effective_from")) + pd.Timedelta(days=89)).date()} — '
            f'the revenue base applies from {rb.get("effective_from")} — this window spans that date; the buying share is '
            f'applied day by day, {(rb.get("day_by_day") or {}).get("not_burn", "no burn share is recorded before it")}"')


def _leg(project: dict, key: str, build) -> str:
    """Render one documented allocation leg, or an empty cell where the project declares none.

    EMPTY, NOT ZERO AND NOT "n/a": a project without a declared multi-leg allocation has not
    failed to report one — the question does not arise for it. A 0 here would enter the
    comparison columns as a measured nothing.
    """
    leg = next((l for l in config.stage_split_legs(project["name"]) if l["key"] == key), None)
    return build(leg["share"]) if leg else ""


def base_gated(project: dict, expr: str, window: str | None = None) -> str:
    """Suppress a derived buyback where the SHARE is confirmed but the BASE it multiplies is not.

    threshold_gated's twin, same shape: a project-level dict, absent for every project this does
    not apply to, so it is a no-op everywhere except where explicitly declared.

    THE FAILURE THIS GUARDS AGAINST IS NOT "THE SHARE IS UNCERTAIN" — that case is already
    handled by gated()'s "unconfirmed" status, and conflating the two would hide a confirmed
    number (Sky's Stage 2 split, stated in Sky's own words) behind the same grey used for a
    number nobody has sourced at all. This is the case where the RATE is solid and the
    DENOMINATOR it is applied to is not established to be the same quantity the formula uses —
    Sky states its Stage 2 shares as percentages of "monthly Net Protocol Surplus"; the formula
    multiplies DefiLlama's revenue_usd. A confirmed rate applied to the wrong base produces a
    confidently wrong number, the same failure shape as World Mobile's withdrawn 11.41% test.
    """
    b = project.get("revenue_base_uncertain")
    if not b or b.get("status") == "confirmed":
        return expr

    # ===== THE GREY LIFTS WHERE THE RIGHT BASE IS SOURCED AND THE WINDOW IS IN SCOPE. =====
    # Not because a mapping between the two quantities was found — there is none, and
    # revenue_base_uncertain gives two independent reasons why there never will be — but because
    # the formula now multiplies the base the protocol actually names. config.revenue_base says
    # which metric that is and FROM WHEN.
    #
    # A WINDOW THAT ENDS BEFORE THE BASE TAKES EFFECT STAYS GREY, and a window that SPANS the
    # boundary stays grey too. Sky's 27.5/22.5/5 allocation is Stage 2's and Stage 2 went
    # on-chain 2026-08-17 (config.revenue_base); before it the 55% figure was a per-cycle internal split of what the Smart Burn
    # Engine had already received, not a share of surplus. Applying an NPS base to those windows
    # multiplies the right number by a share that did not exist yet — which is the same class of
    # confidently-wrong figure this gate was built to stop, pointing the other way.
    base = config.revenue_base(project["name"])
    if base:
        effective = str(base.get("effective_from") or "")
        if not effective:
            return expr
        start_iso, end_iso = _WINDOWS.get(window or "q0", ("", ""))
        if not start_iso:
            # The window bounds are not resolved — which happens when base_gated is called
            # outside a build. Say so rather than guessing a side of the boundary: a message
            # asserting the window "ends before" a date nobody computed would be a made-up fact
            # in the one place this function exists to prevent them.
            return '="window not resolved — cannot tell if the base applies"'
        if start_iso >= effective:
            return expr
        if end_iso >= effective and (base.get("day_by_day") and _DAY_BY_DAY.get((project["name"], window or "q0"))):
            return expr                              # G10 3a: the window's share is the day-by-day one (Config)
        if end_iso >= effective:                     # G9 B1: a window short of the base is MISSING C, with its date
            valid = (pd.Timestamp(effective) + pd.Timedelta(days=(pd.Timestamp(end_iso) - pd.Timestamp(start_iso)).days)
                     ).date()
            return (f'="n/a — BLOCKED: the revenue base applies from {effective} — this window spans that date; '
                    f'valid from {valid}"')
        return f'="n/a — the revenue base applies from {effective} — this window ends before it (no share existed)"'
    return '="n/a — UNCONFIRMED: no revenue base declared"'


def day_by_day_share(name: str, start, end, i: int, asof) -> tuple[float, str] | None:
    """THE SHARE APPLIED DAY BY DAY (G10 3a; config revenue_base.day_by_day): (base-weighted share, why) for a window that
    SPANS a change of split — Σ_d base_d × share_d ÷ Σ_d base_d over the days the base's own window sums (a monthly base:
    the i-th block of three complete months, each month spread evenly over its days; a daily base: (start, end]). So
    base × this share is the day-by-day sum exactly. None when not declared, when the window is wholly inside one split,
    when any day's share is undocumented, or when the base sums to zero."""
    rb = config.revenue_base(name) or {}
    if not rb.get("day_by_day"):
        return None
    eff = str(rb.get("effective_from") or "")
    s0, e0 = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if not eff or not (s0 < pd.Timestamp(eff) <= e0):
        return None
    base = _daily(_LAST_GROUPS.get((name, config.revenue_base_metric(name))))
    if base.empty:
        return None
    days = {}
    if config.series_granularity(name, config.revenue_base_metric(name)) == "monthly":
        last = pd.Timestamp(asof).to_period("M") - 1 - 3 * i
        for m, v in base.groupby(base.index.to_period("M")).sum().items():
            if last - 2 <= m <= last:
                for d in pd.date_range(m.start_time, m.end_time.normalize(), freq="D"):
                    days[d] = float(v) / m.days_in_month
    else:
        days = {d: float(v) for d, v in base.items() if s0 < d <= e0}
    num, den, regimes = 0.0, 0.0, {}
    for d, b in sorted(days.items()):
        a = config.split_for_window(name, d.date().isoformat(), d.date().isoformat())
        sh = a.get("share_to_buyback")
        if not isinstance(sh, (int, float)) or a.get("status") == "unconfirmed":
            return None
        num, den = num + b * sh, den + b
        regimes.setdefault(float(sh), [d, d])[1] = d
    if not den or len(regimes) < 2:
        return None
    parts = ", ".join(f"{sh:.1%} {a.date()}..{b.date()}" for sh, (a, b) in sorted(regimes.items(), key=lambda kv: kv[1][0]))
    return num / den, (f"day by day ({rb['day_by_day']['declared_by']}): each day's {config.revenue_base_metric(name)} × "
                       f"the share in force that day — {parts}; {rb['day_by_day']['monthly']}; base-weighted share "
                       f"{num / den:.2%}")


def supply_additive(project: dict) -> bool:
    """Is this project's 'buyback' actually an issuance event?"""
    return bool(project.get("buyback_is_supply_additive"))


def _absorption_emissions(R, r: int, p: dict, w: str = "q0") -> str:
    """The emissions net absorption subtracts: config.absorption_emission_metrics (Aerodrome, Jake 2026-10-11 decision
    A3: the Minter's gauge emissions AND the RewardsDistributor rebase, consistent with Pendle's gauge emissions),
    else emissions_tokens."""
    ms = config.absorption_emission_metrics(p["name"])
    return "(" + "+".join(R.D(r, m, w) for m in ms) + ")" if len(ms) > 1 else R.D(r, ms[0], w)


def _absorption_emissions_cell(R, r: int, p: dict) -> str:
    ms = config.absorption_emission_metrics(p["name"])
    if len(ms) == 1:
        return pull(R.D(r, ms[0], "q0"))
    return _coverage_guard(R, r, [(m.replace("_tokens", "").replace("_", " "), m, "q0") for m in ms],
                           "+".join(R.D(r, m, "q0") for m in ms), same_span=True)


def _absorption_guard(R, r: int, p: dict, legs: list, expr: str, same_span: bool | None = None,
                      bare: bool = False) -> str:
    """_coverage_guard over `legs` plus one leg per absorption emissions metric. same_span compares the first two legs
    (the buyback / revenue side against the first emissions series), as before; every leg must still cover a week."""
    ems = [("emissions" if len(config.absorption_emission_metrics(p["name"])) == 1 else
            m.replace("_tokens", "").replace("_", " "), m, "q0") for m in config.absorption_emission_metrics(p["name"])]
    return _coverage_guard(R, r, list(legs) + ems, expr, same_span=same_span, bare=bare)


def net_absorption(project: dict, buyback_expr: str, emissions_expr: str) -> str:
    """Buyback minus emissions — with the sign of the buyback term decided by config.

    Aethir's 'Checker Node Buyback' repurchases NFTs by paying in eATH that KEEPS EARNING ATH
    through its lockup, so it ADDS circulating supply. Subtracting it as though it retired supply
    gets the sign wrong on the one figure this whole tool exists to produce. Where a buyback is
    supply-additive it is added to the issuance side instead: net absorption falls, net issuance
    rises, and the figure says what actually happened.
    """
    sign = "-" if supply_additive(project) else ""
    return f"{sign}{buyback_expr}-{emissions_expr}"


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


def _period_windows(asof: pd.Timestamp) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """The four comparison windows, as (label, start, end). Must match aggregate()'s q0..q3."""
    period = GLOBALS["period_days"]
    out = []
    for i, label in enumerate(["Q0", "Q1", "Q2", "Q3"]):
        end = asof - pd.Timedelta(days=period * i)
        out.append((label, end - pd.Timedelta(days=period), end))
    return out


def buyback_silence(data: pd.DataFrame | None) -> dict:
    """{project: last non-zero buyback date} where the PROGRAM SILENCE flag is up on its buyback
    series (aggregate's silence_flag, PROGRAM_CADENCE). Ether.fi, Jake 2026-10-05: "active" for a
    buyback silent since 2026-04-01 (CoW-only) read as if it were running."""
    out = {}
    if data is None or data.empty or "silence_flag" not in data.columns:
        return out
    for r in data.to_dict("records"):
        cad = config.program_cadence(r["project"], r["metric"])
        # A STRING FLAG ONLY: an unflagged row holds NaN, which is truthy — every project with a declared cadence read
        # "SILENT since <last non-zero>" (Pendle after one day; Jake's review of 97b29ae, item 15)
        flag = r.get("silence_flag")
        if (cad and str(r["metric"]).startswith("actual_buyback") and isinstance(flag, str) and flag
                and isinstance(r.get("last_nonzero_date"), str) and r["last_nonzero_date"]):
            out[r["project"]] = r["last_nonzero_date"]
    return out


def _window_value(data: pd.DataFrame | None, project: str, metric: str, col: str):
    """One Data column of a series: a number (> 0 kept as is) or a non-empty text; else None."""
    if data is None or data.empty or col not in data:
        return None
    hit = data[(data["project"] == project) & (data["metric"] == metric)]
    if hit.empty:
        return None
    v = hit.iloc[0].get(col)
    if isinstance(v, (int, float)) and not pd.isna(v):
        return float(v)
    return str(v) if isinstance(v, str) and v and not v.startswith("n/a") else None


def _q0(data: pd.DataFrame | None, project: str, metric: str):
    if data is None or data.empty:
        return None
    hit = data[(data["project"] == project) & (data["metric"] == metric)]
    if hit.empty:
        return None
    v = hit.iloc[0].get("q0")
    return float(v) if isinstance(v, (int, float)) and not pd.isna(v) else None


def derived_statuses(data: pd.DataFrame | None, silence: dict | None = None) -> dict:
    """{project: {"buyback": label, "burn": label}} FROM THE DATA (Jake's review of 97b29ae, 2026-10-11, item 13: Fluid
    buyback 'active' though halted 2026-05-11, Maple burn 'active' with no burn, Uniswap burn 'unconfirmed' with 5.886M
    measured, Chainlink buyback 'unconfirmed' with $14.37M into the Reserve, Hyperliquid burn 'n/a' with 2.21M
    measured). Order: a halt on file (UNAVAILABLE renders_as_zero_since) -> 'halted since'; the cadence silence flag
    on a declared-active programme -> 'SILENT since'; a measured Q0 flow > 0 -> 'active (measured)'; a measured Q0
    of 0 -> 'none measured in Q0'; no measured series -> the config status, marked '(declared; no … series measured)'."""
    silence = silence or {}
    out = {}
    for p in scoped_projects():
        name = p["name"]
        fs, bs = p.get("fee_split") or {}, p.get("burn_split") or {}
        fs_st, bs_st = fs.get("status") or "n/a", (bs.get("status") if bs else None) or "n/a"
        closed = (config.unavailable_for(name, "actual_buyback_tokens") or
                  config.unavailable_for(name, "actual_buyback_usd") or {})
        vals = [v for v in (_q0(data, name, "actual_buyback_usd"), _q0(data, name, "actual_buyback_tokens"))
                if v is not None]
        if closed.get("renders_as_zero_since"):
            bb = f"halted since {closed['renders_as_zero_since']}"
        elif name in silence and fs_st == "active":
            bb = f"SILENT since {silence[name]} (declared active; program cadence broken)"
            # G11 B11 (Jake's 10-21 run: Ether.fi "SILENT since 2026-09-02" while its Programmatic Buybacks Safe bought
            # 1.4M ETHFI in 30 days): a SIDE programme measured on its own series (config.SIDE_BUYBACK) is named beside
            # the silent wallet buyback — with what happens to its tokens, which is why it is not credited
            side = config.SIDE_BUYBACK.get(name)
            v30 = _window_value(data, name, side["metric"], "now") if side else None
            if side and v30:
                last = _window_value(data, name, side["metric"], "latest_date")
                bb = (f"wallet buyback silent since {silence[name]}; {side['label']} active "
                      f"({v30 / 1e6:,.1f}M {p['symbol']}/30d" + (f", last {last}" if last else "")
                      + f") — {side['fate']}")
        elif any(v > 0 for v in vals):
            bb = "active (measured Q0)"
        elif vals:
            bb = "none measured in Q0" + (f" (declared {fs_st})" if fs_st != "n/a" else "")
        elif fs.get("share_to_buyback") == 0 and fs_st != "n/a":
            bb = "none (declared: 0% of revenue to buyback)"
        else:
            bb = ("n/a — no buyback programme declared (config fee_split)" if fs_st == "n/a"
                  else f"{fs_st} (declared; no buyback series measured)")
        bvals = [v for v in (_q0(data, name, config.a4_burn_metric(name)), _q0(data, name, "gross_burn_tokens"))
                 if v is not None]
        if any(v > 0 for v in bvals):
            bn = "active (measured Q0)"
        elif bvals:
            bn = "none measured in Q0" + (f" (declared {bs_st})" if bs_st != "n/a" else "")
        # A DECLARED 0% SPLIT IS NO BURN (Jake's sanity run 2026-10-11 / decision A6: Maple, Bitcoin and Zcash read
        # 'active (declared; no burn series measured)' — the split IS active, at 0%: there is no burn to measure)
        elif bs.get("share_of_fees_burned") == 0 and bs_st != "n/a":
            bn = "none (declared: 0% of fees burned)"
        else:
            bn = ("n/a — no burn programme declared (config burn_split)" if bs_st == "n/a"
                  else f"{bs_st} (declared; no burn series measured)")
        out[name] = {"buyback": bb, "burn": bn}
    return out


CFG_WRITTEN: dict = {}     # (project, Config & Sources column) -> the value written this build (G9 B3: _reasoned_na)


_DAY_BY_DAY: dict = {}               # (project, window) -> why, where the Config window share is applied day by day


def write_config(ws, asof: pd.Timestamp, silence: dict | None = None, statuses: dict | None = None):
    silence = silence or {}
    statuses = statuses or {}
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
    for key, (lab, cell, text) in CFG_GLOBAL_EXTRA.items():
        ws[lab] = text
        ws[lab].font = F_BASE
        ws[cell] = GLOBALS[key]
        _style(ws[cell], "input", FMT_NUM2)
        ws[cell].fill = FILL_KEY
    ws.cell(row=7, column=1, value="Annualisation factor = days per year ÷ comparison window").font = F_SUB
    c = ws.cell(row=7, column=2, value=f"={G('days_per_year')}/{G('period_days')}")
    _style(c, "calc", FMT_NUM2)

    ws.cell(row=8, column=1, value=
            "Splits change. Where a project documents a history, each comparison window below carries the split that "
            "actually applied to it — the current split is NEVER applied retroactively. A window spanning a change, or "
            "sitting in a period we have not documented, reads 'unconfirmed' and its derived figure is suppressed."
            ).font = F_SUB

    windows = _period_windows(asof)
    headers = [h for h, _ in CFG_COLS]
    _header(ws, CFG_HEADER_ROW, headers)
    for i, p in enumerate(scoped_projects()):
        r = CFG_R0 + i
        fs = p.get("fee_split") or {}
        bs = p.get("burn_split") or {}
        sched = p.get("issuance_schedule") or {}
        cur_step = None
        if sched.get("steps"):
            cur_step = sorted(sched["steps"], key=lambda s: s["from"])[-1]["tokens_per_day"]
        changes = "; ".join(f"{c['date']}: {c['event']} ({c['source_url']})" for c in p.get("status_changes", []))

        # A per-product split is NEVER collapsed into one number.
        per_product = ("; ".join(f"{prod}: {rendered}" for prod, rendered, _ in config.per_product_shares(p["name"]))
                       or "n/a — one split for every product (no per-product shares)")
        share_cell = "per-product — see column" if config.share_is_per_product(p["name"]) else fs.get("share_to_buyback")

        # The split that actually applied to each window.
        window_cells = []
        for wi, (_, start, end) in enumerate(windows):
            applied = config.split_for_window(p["name"], start.date().isoformat(), end.date().isoformat())
            sh, stat = applied["share_to_buyback"], applied["status"]
            dbd = day_by_day_share(p["name"], start, end, wi, asof) if stat == "unconfirmed" else None
            _DAY_BY_DAY.pop((p["name"], f"q{wi}"), None)
            if dbd:                                  # G10 3a: a window spanning a split change, applied day by day
                sh, stat = dbd[0], f"active — {dbd[1]}"[:240]
                _DAY_BY_DAY[(p["name"], f"q{wi}")] = dbd[1]
            # SILENT THROUGH THE WHOLE WINDOW: no buyback since before it opened (Jake, 2026-10-05).
            quiet = silence.get(p["name"])
            if stat == "active" and quiet and pd.Timestamp(quiet) < start:
                stat = f"SILENT — no buyback since {quiet}"
            window_cells.append("per-product" if isinstance(sh, dict) else (
                sh if sh is not None else "n/a — no share of revenue to buyback documented for this window"))
            window_cells.append("n/a — no split documented for this window" if stat == "n/a" else stat)

        # Revenue legs that have started accruing but have not paid — excluded from revenue.
        accruing = "; ".join(
            f"{rs['name']} ({rs.get('share', '?')}, live {rs.get('live_from', '?')}, "
            f"first payment {rs.get('first_payment_date', '?')}) — ACCRUING, NOT BOOKED"
            for rs in (p.get("revenue_sources") or []) if not rs.get("booked", True)
        )
        gov = "; ".join(
            f"{k}={'(not set)' if v.get('value') is None else v['value']} "
            f"[{'programmed' if v.get('programmed') else 'governance-movable'}; {v.get('controller', '?')}]"
            for k, v in (p.get("governance_parameters") or {}).items()
        )
        sr_bits = []
        if p.get("self_reported_net_mint"):
            sr_bits.append("net mint")
        if p.get("self_reported_burn"):
            sr_bits.append("burn")
        self_reported = (", ".join(sr_bits) + " — preferred over derived") if sr_bits else ""

        values = [
            p["name"], p["symbol"], ", ".join(str(a) for a in p["archetypes"]), p["archetypes"][0],
            ", ".join(str(a) for a in p.get("archetypes_held", [])) or "", p["materiality"],
            p.get("coingecko_id") or "", p.get("defillama_fees_slug") or "", p.get("defillama_protocol") or "", p.get("defillama_chain") or "",
            share_cell, fs.get("source_url") or "", fs.get("source_date") or "",
            _programmed_label(fs.get("programmed")),
            (statuses.get(p["name"]) or {}).get("buyback") or
            (f"SILENT since {silence[p['name']]} (declared {fs.get('status')}; program cadence broken)"
             if p["name"] in silence and fs.get("status") == "active" else fs.get("status", "n/a")),
            p.get("buyback_destination", ""), p.get("destination_effect", ""),
            p.get("destination_split"), p.get("burn_execution", ""),
            bs.get("share_of_fees_burned") if bs else None, (bs.get("source_url") or "") if bs else "", (bs.get("source_date") or "") if bs else "",
            (statuses.get(p["name"]) or {}).get("burn") or (bs.get("status", "n/a") if bs else "n/a"),
            cur_step, sched.get("source_url") or "", sched.get("source_date") or "", sched.get("status", "n/a") if sched else "n/a",
            per_product, *window_cells, accruing, gov, self_reported,
            "; ".join(x for x in [p.get("notes", ""), fs.get("note", ""), (bs or {}).get("note", "")] if x), changes,
            bs.get("status", "n/a") if bs else "n/a",
        ]
        for j, v in enumerate(values, start=1):
            c = ws.cell(row=r, column=j, value=v)
            head = CFG_COLS[j - 1][0]
            CFG_WRITTEN[(p["name"], head)] = v
            if head.startswith("Share Q") or head in ("Share of revenue to buyback", "Share of fees burned",
                                                      "Destination split (share burned, where destination = split)"):
                _style(c, "input", FMT_TEXT if isinstance(v, str) else FMT_PCT)
                if isinstance(v, str):
                    c.font = Font(name=FONT, size=10, color="999999")
            elif head == "Issuance schedule (tokens/day, current step)":
                _style(c, "input", FMT_NUM2)
            elif head in ("Programmed (contract-enforced)", "Buyback status", "Buyback destination",
                          "Destination effect on float", "Burn execution", "Burn status", "Schedule status", "Materiality"):
                _style(c, "input", FMT_TEXT)
            else:
                _style(c, "text", FMT_TEXT)
            if (head in ("Buyback status", "Burn status") or head.startswith("Status Q")) and str(v).startswith("unconfirmed"):
                c.fill = FILL_UNCONFIRMED
            if (head in ("Buyback status", "Burn status") or head.startswith("Status Q")) and (
                    str(v).startswith(("paused", "SILENT", "halted", "none measured"))):
                c.fill = FILL_PAUSED
            if head == "Programmed (contract-enforced)" and v == "sources conflict":
                c.fill = FILL_UNCONFIRMED
            if head == "Revenue accruing but NOT booked" and v:
                c.fill = FILL_PAUSED
            if head == "Governance-settable parameters (never hardcoded)" and v:
                c.fill = FILL_UNCONFIRMED
            if head == "Per-product split (never collapsed)" and v and not str(v).startswith("n/a"):
                c.fill = FILL_KEY
            if head in ("Notes", "Status changes"):
                c.alignment = Alignment(wrap_text=False)
    _write_closed_records(ws, _cfg_r1() + 3)
    _set_widths(ws, {get_column_letter(i + 1): w for i, (_, w) in enumerate(CFG_COLS)})
    ws.freeze_panes = ws.cell(row=CFG_R0, column=3)


def _write_closed_records(ws, start_row: int) -> int:
    """Figures that were chased and closed, and limitations on figures that work.

    Neither belongs in the Gap Report — a permanent entry on a to-do list teaches the reader to
    skim the list. They belong here, with what was tried, so the next person does not repeat the
    work or mistake a deliberately empty cell for a broken one.
    """
    records = getattr(config, "UNAVAILABLE", [])
    r = start_row
    ws.cell(row=r, column=1, value="Closed — chased, no route available (NOT gaps, and not to be re-attempted)").font = F_BOLD
    r += 1
    if not records:
        ws.cell(row=r, column=1, value="none").font = F_SUB
        return r + 1
    _header(ws, r, ["Project", "Figure", "Kind", "Closed on", "Summary", "What was tried", "Impact", "Reopen if"])
    ws.row_dimensions[r].height = 18
    r += 1
    for u in records:
        kind = u.get("kind", "figure")
        vals = [u["project"], u["metric"],
                "no route to the figure" if kind == "figure" else "figure works; HISTORY unavailable",
                u["closed_on"], u["summary"], u["what_was_tried"], u["impact"], u["reopen_if"]]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.font = F_SUB if j > 4 else F_BASE
            c.number_format = FMT_TEXT
            c.alignment = Alignment(wrap_text=False)
            if j <= 4:
                c.fill = FILL_UNCONFIRMED
        r += 1
    return r


def _literal(c) -> None:
    """Keep text that begins with "=" as TEXT. Added 2026-09-24.

    ** openpyxl STORES ANY STRING STARTING WITH "=" AS A FORMULA. ** GEODNET's and Sky's
    actual_buyback_tokens reasons began "= gross_burn_tokens — ...", so they were written as a
    broken formula and rendered EMPTY — the "null reason" that survived a fix to the reason path,
    because the reason path was never the problem. Called on every free-text cell that carries a
    gap reason or note; never on a cell this file means as a formula.
    """
    if isinstance(c.value, str) and c.value.startswith("="):
        c.data_type = "s"


def write_data(ws, data: pd.DataFrame, asof: pd.Timestamp):
    _header(ws, 1, DATA_HEAD)
    ws["A1"].comment = Comment(f"Raw window aggregates of the store as of {asof.date()} (UTC). Literals — source data. "
                               "Missing = 'n/a'. Yellow = manual override. Orange = stale.", "token_metrics")
    num_cols = ["now", "m1", *QUARTERS, "y1"]
    for i, row in enumerate(data.itertuples(index=False), start=2):
        fmt = UNIT_FMT.get(row.unit, FMT_NUM)
        if row.metric == "price_usd":
            fmt = FMT_USD4
        for j, col in enumerate(DATA_COLS, start=1):
            v = getattr(row, col)
            c = ws.cell(row=i, column=j)
            if col in num_cols:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    # NO BARE n/a (alignment round, SPEC.md rule 2): every pull of a missing Data value carries the
                    # row's reason — its status and the first part of its note ("n/a — Data: …")
                    st = str(row.status or "")
                    note = str(row.note or "").split(" | ")[0]
                    why = (_NA_BLANK_STATUS.get(st, st) if st not in ("ok", "") else "no value in this window")
                    c.value = (f"n/a — Data: {why}" + (f" — {note}" if note and note != "nan" else ""))[:240]
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
            elif col in ("n_points", "tier"):
                c.value = int(v) if str(v) not in ("", "None") else ""
                _style(c, "text", FMT_NUM)
            elif col in NUMERIC_APPENDED:
                # ** WRITTEN AS NUMBERS, OR NOTHING. Fixed 2026-09-28. ** These were falling to the
                # literal branch below and landing as TEXT ("14.0", "nan"), so every formula
                # testing ISNUMBER(q0_covered_days) took its fallback: the covered-days
                # annualisation never fired on a real sheet while its formula-level tests passed.
                ok = isinstance(v, (int, float)) and not pd.isna(v)
                c.value = int(v) if ok else ""
                _style(c, "text", FMT_NUM)
            else:
                c.value = "" if v is None else str(v)
                _literal(c)
                _style(c, "text", FMT_TEXT)
                if col == "status":
                    if v == "manual":
                        c.fill = FILL_MANUAL
                    elif v == "stale":
                        c.fill = FILL_STALE
                    elif v == "review":
                        c.fill = FILL_REVIEW
                    elif v in ("missing", "gap", "n/a"):
                        # "missing" ALONE does not say WHY: it covers both "nobody has looked
                        # yet" and "chased and permanently closed" (config.UNAVAILABLE), which
                        # fetch/gaps.py deliberately keeps OFF the Gap Report so a dead end does
                        # not sit on the to-do list forever — see detect()'s own comment. The A3
                        # tab already distinguishes these with CLOSED_TEXT and a full comment;
                        # the Data tab did not, so the same word covered two different states
                        # with no way to tell them apart here.
                        if v == "missing":
                            closed = config.unavailable_for(row.project, row.metric)
                            if closed:
                                c.comment = Comment(
                                    f"CLOSED, not an open gap — {closed['summary']}\n\n"
                                    f"Impact: {closed['impact']}\n\n"
                                    f"Reopen if: {closed['reopen_if']}\n\n"
                                    f"Closed on {closed['closed_on']}. Recorded in config.py "
                                    f"UNAVAILABLE.", "token_metrics")
                        c.font = Font(name=FONT, size=10, color="999999")
    _set_widths(ws, {"A": 34, "B": 14, "C": 24, "D": 30, "E": 6, "F": 7, "G": 22, "H": 5, "I": 11, "J": 16, "K": 16, "L": 16,
                     "M": 16, "N": 16, "O": 16, "P": 16, "Q": 7, "R": 11, "S": 20, "T": 12, "U": 60})
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
def circular_gated(p: dict, formula: str) -> str:
    """Blank a comparison that is guaranteed zero for this project, and say why.

    ** A STRUCTURAL ZERO IS WORSE THAN A BLANK. ** Where DefiLlama derives a protocol's fees FROM
    its on-chain burn, "implied buyback minus actual buyback" is (burn/share) x share - burn: it
    is zero however wrong either figure is, and that zero sits in the row a reader consults to
    decide whether a declared split is being honoured. The most reassuring answer the sheet can
    give, and it would survive the burn read breaking entirely.

    The reason goes in the cell rather than in a comment, because a reader who finds an empty
    cell where a difference used to be will otherwise assume a broken read.
    """
    why = config.implied_check_is_circular(p["name"])
    return f'"{why[:250]}"' if why else formula


def _net_change(R, r: int, p: dict, iss, burn) -> str:
    """Net supply change for one project.

    Where the PROTOCOL ITSELF publishes net mint (self_reported_net_mint in config), that figure
    is preferred over the derived one, per the build spec. The preference is keyed on the config
    flag, NOT merely on a row being present: a project that does not publish net mint always uses
    issuance − burn, even if a stray net_mint_monthly row turns up from somewhere.
    """
    derived = f"{iss(r)}-{burn(r, p)}"
    if not p.get("self_reported_net_mint"):
        return derived
    sr = R.D(r, "net_mint_monthly", "q0")
    return f"IF(ISNUMBER({sr}),{sr},{derived})"


def _burns_nothing(p: dict) -> bool:
    """No burn series applies to this project (config.metrics_for_project): its burn is zero by design, not missing."""
    return config.a1_burn_metric(p["name"]) not in config.metrics_for_project(p) and not p.get("self_reported_burn")


def _net_guarded(R, r: int, p: dict, iss, burn, bm: str | None = None) -> str:
    """_net_change as a cell: a SELF-REPORTED figure as published; a derived one BLOCKED where
    issuance and burn cover materially different days of the window."""
    name = p["name"]
    nc = p.get("net_change_common_days")
    if nc:                                     # on the days BOTH sides are measured (A5, 2026-10-06)
        # ... and BLOCKED where those days are not the window (accuracy pass on 1e702bb, item 5: Ethereum netted 13
        # days of issuance against 90 of burn under a Q0 header) — the same equal-coverage guard as every other net
        return _coverage_guard(R, r, [("issuance", config.issuance_basis(name), "q0"),
                                      ("burn", config.a1_burn_metric(name), "q0")],
                               R.D(r, nc["metric"], "q0"), same_span=True, p=p)
    # EVERY BURN THE SUPPLY SEES (Phase 2 amendment 1, config.a1_burn_metric): Hyperliquid's A4 burn is the Assistance
    # Fund alone, its net supply change counts the Core burns too (on A4 without the HyperEVM gas burn: `bm` given)
    bm = bm or config.a1_burn_metric(name)
    if _premint(p):
        # MINT − BURN, AND NOTHING IS MINTED (item 9): −burn, or 0 where nothing burns
        guarded = ("=0" if _burns_nothing(p) else
                   _coverage_guard(R, r, [("burn", bm, "q0")], f"-{R.D(r, bm, 'q0')}", p=p))
    elif _burns_nothing(p):
        # NO BURN MECHANISM, NO BURN LEG (the headline_starved rule's first real hit, Chainlink 2026-10-12): net
        # issuance is the issuance; reading a burn series that cannot exist made every net figure n/a
        im = config.issuance_basis(name)
        guarded = _coverage_guard(R, r, [("issuance", im, "q0")], iss(r), p=p)
    else:
        guarded = _guarded_net(R, r, p, config.issuance_basis(name), bm, total=f"{iss(r)}-{R.D(r, bm, 'q0')}")
    if not p.get("self_reported_net_mint"):
        return guarded
    sr = R.D(r, "net_mint_monthly", "q0")
    return f"=IF(ISNUMBER({sr}),{sr},{guarded[1:]})"


def _price_net(cell: str, price: str) -> str:
    """A net-change CELL (=...) in dollars; a BLOCKED text passes through unpriced."""
    x = cell[1:]
    return f"=IFERROR(IF(ISNUMBER({x}),({x})*{price},{x}),{NA})"


def _net_annual_pct(R, r: int, p: dict, circ: str, bm: str | None = None) -> str:
    """Net supply change per year, % of circulating: each side annualised over ITS OWN covered
    days (was the Q0 difference x365/90, 2026-09-29), BLOCKED where either covers under a week."""
    name = p["name"]
    nc = p.get("net_change_common_days")
    if nc:                                     # annualised over the days both sides are measured (A5)
        m = nc["metric"]
        # the same equal-coverage guard as the net total beside it (item 5): one rule, both cells
        return _coverage_guard(R, r, [("issuance", config.issuance_basis(name), "q0"),
                                      ("burn", config.a1_burn_metric(name), "q0")],
                               f"{_annualise(R, r, p, m, R.D(r, m, 'q0'))}/({circ})", same_span=True, p=p)
    im, bm = config.issuance_basis(name), (bm or config.a1_burn_metric(name))
    if _premint(p):                            # mint − burn with nothing minted (item 9)
        return ("=0" if _burns_nothing(p) else
                _coverage_guard(R, r, [("burn", bm, "q0")],
                                f"-{_annualise(R, r, p, bm, R.D(r, bm, 'q0'))}/({circ})", p=p))
    if _burns_nothing(p):                      # no burn mechanism: the net rate is the issuance rate
        return _coverage_guard(R, r, [("issuance", im, "q0")],
                               f"{_annualise(R, r, p, im, R.D(r, im, 'q0'))}/({circ})", p=p)
    derived = (f"({_annualise(R, r, p, im, R.D(r, im, 'q0'))}-"
               f"{_annualise(R, r, p, bm, R.D(r, bm, 'q0'))})/({circ})")
    # THE SAME EQUAL-COVERAGE GUARD AS THE NET TOTAL BESIDE IT (Jake's sanity run 2026-10-11, mixed_windows:
    # Hyperliquid's net read BLOCKED — issuance 10 days, burn 90 — while this % read 43.656%, and Venice AI's 4.704%
    # beside a BLOCKED net). Two rates over different spans are not one net rate; same_span blocks both cells alike.
    guarded = _coverage_guard(R, r, [("issuance", im, "q0"), ("burn", bm, "q0")], derived, same_span=True, p=p)
    if not p.get("self_reported_net_mint"):
        return guarded
    sr = R.D(r, "net_mint_monthly", "q0")
    return (f"=IF(ISNUMBER({sr}),IFERROR({_annualise(R, r, p, 'net_mint_monthly', sr)}/({circ}),{NA}),"
            f"{guarded[1:]})")


# ---------------------------------------------------------------------------------------
# Valuation-framework headlines — the "What Do I Own?" note's figures, at the LEFT of each
# archetype tab: project | symbol | price | market cap | that archetype's ratios | the rest.
# ---------------------------------------------------------------------------------------
def _valuation_head(R: Refs, circulating: bool = False, fdv: bool = False) -> list[tuple]:
    """The "What Do I Own?" valuation inputs every archetype tab leads with (SPEC §1b T-f). G6 item 8: circulating supply
    on A1 / A2 / A4 (A3 has its own beside the float block) and FDV on A3 (spec_coverage)."""
    return [
        ("Price — spot ($)", lambda r, p: pull(R.D(r, "price_usd", "now")), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Market cap ($)", lambda r, p: pull(R.D(r, "market_cap_usd", "now")), FMT_USD, "pull", False, {"metric": "market_cap_usd"}),
        *([("Circulating supply (used, tokens)", lambda r, p: pull(_circ(R, r, p)), FMT_NUM, "pull", False,
            {"metric": "circulating_supply_used"})] if circulating else []),
        *([("FDV ($)", lambda r, p: pull(R.D(r, "fdv_usd", "now")), FMT_USD, "pull", False, {"metric": "fdv_usd"})]
          if fdv else []),
    ]


def _key_cols(specs: list[tuple], *prefixes: str) -> set[int]:
    """1-based column numbers of the headers starting with any prefix — never hard-coded."""
    return {i for i, s in enumerate(specs, start=1) if any(s[0].startswith(x) for x in prefixes)}


_DETAIL_FMT = {"num": FMT_NUM, "usd": FMT_USD, "pct": FMT_PCT, "ratio": '#,##0.0000;(#,##0.0000);-',
               "date": FMT_DATE, "unix_date": FMT_DATE}


def _detail_spec(R: Refs, d: dict) -> tuple:
    """One G12 detail column (config.DETAIL_COLUMNS): the project's own series, blank for a project it does not apply
    to. A stock reads its latest, a flow its window; ratio_to divides, per_day takes the window's per-covered-day
    rate, unix_date turns unix seconds into a date."""
    cells, kind = d["cells"], d.get("fmt", "num")

    def build(r, p):
        m, w = cells.get(p["name"], (None, None))
        if not m:
            return ""
        x = _flow_rate(R, r, m, w) if d.get("per_day") else R.D(r, m, w)
        if d.get("ratio_to"):
            return calc(f"{x}/{R.D(r, *d['ratio_to'])}")
        if kind == "unix_date":
            return calc(f"{x}/86400+25569")
        return calc(x) if d.get("per_day") else pull(x)
    return (d["header"], build, _DETAIL_FMT[kind], "pull", False,
            {"metric_fn": lambda n, _c=cells: (_c.get(n) or (None,))[0]})


def _with_details(tab: str, specs: list[tuple], R: Refs) -> list[tuple]:
    """THE G12 WIRING (Jake 2026-10-21): every series that measures supply, demand or value capture is SHOWN on its
    archetype tab — each config.DETAIL_COLUMNS entry right after the column its `after` names (before Notes when that
    column is absent), so the figure sits beside the one it explains."""
    out = list(specs)
    for d in config.detail_columns(tab):
        spec = _detail_spec(R, d)
        at = next((i for i, s in enumerate(out) if str(s[0]).startswith(d.get("after") or "\0")), None)
        if at is None:
            at = next((i - 1 for i, s in enumerate(out) if s[0] == "Notes"), len(out) - 1)
        out.insert(at + 1, spec)
    return out


def _widths(specs: list[tuple]) -> dict:
    """Project 16, Symbol 8, Notes and the flags column 60, everything else 14 — by position."""
    out = {get_column_letter(i): 14 for i in range(1, len(specs) + 2)}
    out["A"], out["B"] = 16, 8
    for i, s in enumerate(specs, start=1):
        if s[0] == "Notes":
            out[get_column_letter(i)] = 60
    out[get_column_letter(len(specs) + 1)] = 60
    return out


def _lumpy(project_name: str) -> bool:
    """A flow that arrives in DISCRETE events on a declared cadence longer than a day.

    config.PROGRAM_CADENCE: Sky's monthly Stage 2 burn, Maple monthly, Ether.fi weekly/monthly,
    Pendle biweekly. GEODNET's daily burn and every undeclared flow are CONTINUOUS.
    """
    cad = config.program_cadence(project_name)
    return bool(cad) and max(d for _, d in cad["cadences"]) > 1


# ===== RATIOS OF FLOWS NEED EQUAL WINDOWS. 2026-09-29 (Jake). =====
# Ethereum's A4 BURN ÷ ISSUANCE read 15.68x: 3,773.86 ETH of burn over 90 backfilled days divided
# by 240.74 ETH of issuance from ONE reading. Every ratio (and difference) of two flows now divides
# RATES — each window total over the days that series actually covered in that window
# (<w>_covered_days) — and is BLOCKED, with each side's coverage named, where either side covers
# fewer than RATE_MIN_DAYS: under a week is not a rate (Ethereum's one reading spanned hours).
RATE_MIN_DAYS = 7
_COV = {"now": "now_covered_days", "m1": "m1_covered_days", "q0": "q0_covered_days",
        "q1": "q1_covered_days", "q2": "q2_covered_days", "q3": "q3_covered_days", "q4": "q4_covered_days"}


def _nominal(w: str) -> str:
    return G("short_days") if w in ("now", "m1") else G("period_days")


def _flow_rate(R: Refs, r: int, metric: str, w: str = "q0", total: str | None = None,
               p: dict | None = None) -> str:
    """A flow's window total PER COVERED DAY (the window's nominal days where none is recorded).

    A DISCRETE series declared to annualise by events (config.event_cadence_days — Sky's monthly
    burn, Maple's monthly buybacks) is (total / events) x events a year / days a year over Q0: one
    monthly burn in 16 covered days is not a daily rate of burn/16 (Jake, 2026-09-29)."""
    cov = R.D(r, metric, _COV[w])
    t = total or R.D(r, metric, w)
    per_year = config.events_per_year(p["name"], metric) if p else None
    if per_year and w == "q0":
        ev = R.D(r, metric, "q0_events")
        return (f"IF(AND(ISNUMBER({ev}),{ev}>0),({t})/{ev}*{per_year:g}/{G('days_per_year')},"
                f"(({t})/IF(ISNUMBER({cov}),{cov},{_nominal(w)})))")
    return f"(({t})/IF(ISNUMBER({cov}),{cov},{_nominal(w)}))"


def _fee_per_tx(R: Refs, r: int, p: dict, avg_price: str) -> str:
    """Fees ÷ transactions, Q0 per covered day — of the SAME layer (D, Jake 2026-10-05: Hyperliquid
    read HyperCore fees ÷ HyperEVM transactions, $6.944). A project declaring fee_per_tx divides the
    declared fee legs (tokens priced at the 90-day average) by its transactions instead."""
    spec = config.fee_per_tx(p["name"])
    if not spec:
        return _coverage_guard(R, r, [("fees", "fees_usd", "q0"), ("transactions", "tx_count", "q0")],
                               f"{_flow_rate(R, r, 'fees_usd')}/{_flow_rate(R, r, 'tx_count')}")
    legs = [(m, m, "q0") for m in spec["fee_metrics"]] + [("transactions", "tx_count", "q0")]
    fee = "+".join(_flow_rate(R, r, m) for m in spec["fee_metrics"])
    if spec.get("unit") == "tokens":
        fee = f"({fee})*{avg_price}"
    return _coverage_guard(R, r, legs, f"({fee})/{_flow_rate(R, r, 'tx_count')}")


def _coverage_guard(R: Refs, r: int, legs: list[tuple[str, str, str]], expr: str,
                    same_span: bool | None = None, bare: bool = False, p: dict | None = None) -> str:
    """=IF(a side covers < RATE_MIN_DAYS, "BLOCKED — ... each side's coverage", expr).

    legs: (label, metric, window). same_span: also block where the two sides' covered days differ
    by more than a tenth of the window. D7-2 (SPEC §1 rule 9 — "two sides of a net OR RATIO over materially
    different spans are BLOCKED"): None, the default, means every guard with two or more legs — a ratio of two
    per-covered-day rates included (Hyperliquid's BURN ÷ ISSUANCE read 0.0501x with the release covering 7 of its 12
    days against a burn covering 87 of 90). The BLOCKED text names each leg's holes where the build knows them.
    A DECLARED ZERO IS FULL COVERAGE (Jake's review of 97b29ae, item 2: Sky's net supply read "issuance covers 5
    days" on Master while A4 passed): with `p` given, the issuance leg of a project declaring zero issuance
    (config.issuance_declared_zero) covers the whole window — the declaration's own check (_zero_issuance) still
    blocks a positive measured issuance."""
    z = config.issuance_declared_zero(p["name"]) if p else None
    if z:
        legs = [(lab, m, w) for lab, m, w in legs if m != z.get("check_metric", "gross_issuance_tokens")] \
            if len(legs) > 1 else legs
        if len(legs) < 2:
            same_span = False
    if same_span is None:
        same_span = len(legs) >= 2
    conds, parts = [], []
    for lab, m, w in legs:
        c, n = R.D(r, m, _COV[w]), _nominal(w)
        conds.append(f"IFERROR(AND(ISNUMBER({c}),{c}<{RATE_MIN_DAYS}),FALSE)")
        # the leg names its series and window (G10 3c: the audit dates a class C cell from the SHORT leg's first row)
        parts.append(f'"{lab} ({m}, {w}) covers "&IFERROR(IF(ISNUMBER({c}),{c},{n}),"?")&" days of a "&{n}&"-day window'
                     f'{_holes_text(m, w)}"')
    # every further leg against the first (two legs: exactly as before; Aerodrome's net absorption has three since
    # 2026-10-11 — buyback, gauge emissions, rebase)
    if same_span and len(legs) >= 2:
        (_, ma, wa) = legs[0]
        for (_, mb, wb) in legs[1:]:
            ca, cb, na, nb = R.D(r, ma, _COV[wa]), R.D(r, mb, _COV[wb]), _nominal(wa), _nominal(wb)
            conds.append(f"IFERROR(ABS(IF(ISNUMBER({ca}),{ca},{na})-IF(ISNUMBER({cb}),{cb},{nb}))>{na}/10,FALSE)")
    msg = '"BLOCKED — unequal coverage: "&' + '&"; "&'.join(parts)
    if bare:            # nested inside gated()/circular_gated(), which supply the IFERROR and "="
        return f"IF(OR({','.join(conds)}),{msg},{expr})"
    return f"=IF(OR({','.join(conds)}),{msg},IFERROR({expr},{NA}))"


def _holes_text(metric: str, w: str) -> str:
    """D7-2: ' — holes: <days>' for the project whose row is being built, where the build found interior days with no
    row in that window (WINDOW_HOLES, D6-1); '' otherwise (a shifted Trajectory quarter: its own window's holes are not
    this one's). Quotes are doubled for the formula string."""
    name = _PROJECT["name"]
    if not name or _SHIFT["k"]:
        return ""
    h = WINDOW_HOLES.get((name, config.metric_alias(name, metric), w))
    if not h:
        return ""
    txt = _day_ranges(h)
    if len(txt) > 150:                       # Excel: a string constant in a formula is at most 255 characters
        txt = txt[:150].rsplit(", ", 1)[0] + f", … ({len(h)} days)"
    return f" — holes: {txt}".replace('"', '""')


def _guarded_net(R: Refs, r: int, p: dict, iss_m: str, burn_m: str, w: str = "q0",
                 total: str | None = None) -> str:
    """issuance − burn over window w, BLOCKED where the two series' coverage differs (same_span)."""
    expr = total or f"{R.D(r, iss_m, w)}-{R.D(r, burn_m, w)}"
    return _coverage_guard(R, r, [("issuance", iss_m, w), ("burn", burn_m, w)], expr, same_span=True, p=p)


def _annualise(R: Refs, r: int, p: dict, metric: str, total: str) -> str:
    """A Q0-window sum as a yearly figure — over the days the series has ACTUALLY covered.

    ** x365/90 UNDERSTATES EVERY SERIES YOUNGER THAN THE WINDOW. Fixed 2026-09-28. ** Run
    20260928T090446Z: Chainlink's reserve inflow had ~60 days stored and Hyperliquid's buyback ~17,
    so the retirement rates read 0.21% and 0.28% — a 60- and a 17-day sum divided as if it were 90.
    A CONTINUOUS flow is annualised as sum / covered_days x days_per_year while coverage is short
    (q0_covered_days, the column built for Sky's caveat), and x days_per_year/period_days once the
    window is full — the same number then.
    A DISCRETE flow (a declared cadence longer than a day, or a monthly series) is NOT: one monthly
    burn in 14 days of coverage annualised over those 14 days would claim ~26 burns a year. Those
    keep x365/90 and the window caveat that says so.
    """
    # A MONTHLY SERIES' Q0 IS THREE COMPLETE CALENDAR MONTHS (see _month_block), so a year is
    # exactly four of them — not days_per_year/period_days, which would overstate by 365/360.
    # ...OVER THE MONTHS ACTUALLY PRESENT (2026-09-29): Sky's NPS holds July and August only, and
    # x12/3 on two months overstated it by half. q0_covered_days is the days of the months held.
    # EVENTS x CADENCE where declared (Sky's monthly burn, Maple's monthly buybacks; 2026-09-29):
    # the mean event times the events a year holds — not the days the window happened to cover.
    per_year = config.events_per_year(p["name"], metric)
    if per_year:
        ev = R.D(r, metric, "q0_events")
        return f"IF(AND(ISNUMBER({ev}),{ev}>0),({total})/{ev}*{per_year:g},({total})*{ANN})"
    if config.series_granularity(p["name"], metric) == "monthly":
        cov = R.D(r, metric, "q0_covered_days")
        return (f"IF(AND(ISNUMBER({cov}),{cov}>0),({total})*{G('days_per_year')}/{cov},"
                f"({total})*12/3)")
    if _lumpy(p["name"]):
        return f"({total})*{ANN}"
    # q0_covered_days ABOVE the period is a complete-months Q0 (92 days for Jun-Aug; see
    # _months_until_live), divided by its own days rather than by 90.
    cov = R.D(r, metric, "q0_covered_days")
    return (f"IF(AND(ISNUMBER({cov}),{cov}>0,{cov}<>{G('period_days')}),"
            f"({total})*{G('days_per_year')}/{cov},({total})*{ANN})")


def _suffix_fmt(fmt: str, suffix: str) -> str:
    """Append literal text to every section of a number format, so it shows beside any value."""
    lit = '"' + suffix.replace('"', "'") + '"'
    return ";".join(sec + lit for sec in (fmt or "General").split(";"))


def _program_flag(p: dict, data_by_key: dict) -> tuple[str, str] | None:
    """(in-cell suffix, comment) for a buyback that has not moved — or None.

    SILENT when the documented program cadence is broken (aggregate's silence_flag, from
    config.PROGRAM_CADENCE); otherwise "no inflow since <date>" whenever the buyback's Q0 is zero
    and the last non-zero date is known. The zero is REAL (a reconciled scan says so) and stays
    on the sheet; the annotation says what kind of zero it is. Added 2026-09-28 for Ether.fi.
    """
    name = p["name"]
    bb = data_by_key.get(f"{name}|actual_buyback_tokens") or {}
    cad = config.program_cadence(name)
    prow = data_by_key.get(f"{name}|{cad['metric']}") if cad else None
    prow = prow or {}

    def txt(v):
        return v if isinstance(v, str) and v else None

    last = txt(bb.get("last_nonzero_date")) or txt(prow.get("last_nonzero_date"))
    flag = txt(prow.get("silence_flag"))
    scan = next((sc for sc in p.get("log_scans") or [] if sc.get("metric") == "actual_buyback_tokens"), None)
    where = ", ".join(scan["holders"]) if scan else "the tracked address"
    tail = (f"\n\nThe figure is what {where} received. When inflows resume the silence flag clears "
            f"ONLY if they land there. A restart at a different address would leave this flag and "
            f"the zero in place — a new receiving address is a config change, not a read.")
    if flag:
        days = prow.get("silent_days")
        days = f" {int(days)}d" if isinstance(days, (int, float)) and days == days else ""
        return (f" · no inflow since {last} · SILENT{days}",
                f"PROGRAM SILENT — {flag}." + tail)
    q0 = bb.get("q0")
    if last and isinstance(q0, (int, float)) and q0 == 0:
        return (f" · no inflow since {last}",
                f"No inflow since {last}: the trailing window reads 0 because nothing arrived, "
                f"not because nothing was read." + tail)
    return None


def _protocol_yield_flag(p: dict, data_by_key: dict) -> tuple[str, str] | None:
    """The program flag, plus the yield's own recipient caveat where config declares one.

    Ether.fi's protocol staking yield assumes bought ETHFI reaches sETHFI holders — true of the
    old programme, unconfirmed for the new one (config PROTOCOL_YIELD caveat, 2026-09-28).
    """
    flag = _program_flag(p, data_by_key)
    caveat = (config.PROTOCOL_YIELD.get(p["name"]) or {}).get("caveat")
    if not caveat:
        return flag
    suffix = (flag[0] if flag else "") + " · recipients unconfirmed"
    note = (f"YIELD ASSUMPTION — {caveat}. The yield counts buybacks as paid to stakers; under the "
            f"new programme that is not established.") + (f"\n\n{flag[1]}" if flag else "")
    return suffix, note


def _retirement_zero(p: dict) -> str | None:
    """Why this project's retirement rate is a STRUCTURAL 0 — or None.

    Aerodrome (Jake, 2026-09-30): no AERO is bought — 100% of fees go to veAERO voters in the
    pairs' own tokens (fee_split destination_model distribute_to_voters). The rate is 0, not
    missing; what stakers earn is on PROTOCOL STAKING YIELD."""
    fs = p.get("fee_split") or {}
    if fs.get("destination_model") == "distribute_to_voters" and \
            "actual_buyback_tokens" not in config.metrics_for_project(p):
        return ("0 — no token is bought: 100% of fees are paid to voters in the pairs' own tokens "
                "(fee_split: distribute_to_voters). What stakers earn is on PROTOCOL STAKING YIELD.")
    return None


def _implied_zero(p: dict) -> str | None:
    """Why this project's IMPLIED buyback (revenue x a protocol share) is a structural 0 — or None. Aerodrome sets no
    share of fees aside for buybacks: 100% goes to voters. The Foundation is ONE of those voters and buys AERO with its
    share (foundation_buyback.funding) — that is the ACTUAL buyback, measured, never added to this one."""
    fs = p.get("fee_split") or {}
    if fs.get("destination_model") == "distribute_to_voters":
        return ("0 — no protocol share of fees is set aside for buybacks: 100% is paid to voters in the pairs' own "
                "tokens (fee_split: distribute_to_voters). The Foundation is one of those voters and buys AERO with "
                "its share; that is the ACTUAL buyback, not this implied one.")
    return None


def _a3_headline(R: Refs, data_by_key: dict | None = None) -> list[tuple]:
    """BOTH RETIREMENT RATES, READ TOGETHER. The gap between them is the dilution warning; the circulating rate alone is
    the flattering half. Each is gated on its own inputs (Phase 1 review 2026-10-12, item 3): a quarter with no total
    supply blanks the FDV rate, never the circulating one.

    TOKENS PRIMARY (Jake, 2026-09-30): the pair is buyback TOKENS (annualised) over circulating
    and over the FDV supply (FDV ÷ spot price) — price-independent, so a rising token does not
    shrink it the way dollars spent at buy-time over today's market cap do (Hyperliquid read 3.9%
    in tokens against 3.1% in dollars). The dollar pair stays alongside."""
    # RETIREMENT = BURNED + HELD (Jake's decision 2026-10-10): the rates read retired_buyback_* (the buyback x its
    # burned + held share, _buyback_split_views); distributed buybacks are staking yield. Annualised over the
    # BUYBACK's coverage (same days — the retired series is a share of it).
    bt = lambda r: R.D(r, "retired_buyback_tokens", "q0")  # noqa: E731
    bb = lambda r: R.D(r, "retired_buyback_usd", "q0")  # noqa: E731
    mc = lambda r: R.D(r, "market_cap_usd", "now")  # noqa: E731
    fdv = lambda r: R.D(r, "fdv_usd", "now")  # noqa: E731
    circ = lambda r, p: _circ(R, r, p)  # noqa: E731
    # EACH QUARTER ON ITS OWN SUPPLY (Jake's Phase 1 review, 2026-10-12 item 3): the FDV supply is the total supply FDV
    # multiplies (_fdv_views: FDV = price x total_supply, so FDV ÷ price IS it) read directly, so a shifted quarter takes
    # that quarter's total — FDV ÷ price at "now" had history only where FDV did. And each rate is gated on ITS OWN
    # inputs: the circulating rate never waits on an FDV (a3_circ_retirement read 1 quarter where a3_actual_buyback_pct,
    # the same numerator over the same circulating, read 4).
    fdv_supply = lambda r: R.D(r, "total_supply", "now")  # noqa: E731
    gate_t = lambda r, p: f"AND(ISNUMBER({bt(r)}),ISNUMBER({circ(r, p)}))"  # noqa: E731
    gate_f = lambda r: f"AND(ISNUMBER({bt(r)}),ISNUMBER({fdv_supply(r)}))"  # noqa: E731
    gate = lambda r: f"AND(ISNUMBER({bb(r)}),ISNUMBER({mc(r)}))"  # noqa: E731
    gate_fd = lambda r: f"AND(ISNUMBER({bb(r)}),ISNUMBER({fdv(r)}))"  # noqa: E731
    # THE RATES READ 0 FOR THE SAME REASON THE BUYBACK DOES, and would keep reading 0 if the
    # program restarted at another address — so the program flag rides on them too.
    flag = lambda p: (_program_flag(p, data_by_key or {})  # noqa: E731
                      or ((" · no buyback — fees to voters", _retirement_zero(p)) if _retirement_zero(p) else None))

    def zero_or(expr_fn):
        def build(r, p):
            return "=0" if _retirement_zero(p) else calc(expr_fn(r, p))
        return build
    return [
        ("CIRCULATING RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised) ÷ circulating — primary",
         zero_or(lambda r, p: f"IF({gate_t(r, p)},{_annualise(R, r, p, 'actual_buyback_tokens', bt(r))}/({circ(r, p)}),{NA})"),
         FMT_PCT, "calc", True, {"metric": "actual_buyback_tokens", "flag_fn": flag}),
        ("FDV RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised) ÷ FDV supply (the total supply FDV uses) — always read with the rate to its left",
         zero_or(lambda r, p: f"IF({gate_f(r)},{_annualise(R, r, p, 'actual_buyback_tokens', bt(r))}/{fdv_supply(r)},{NA})"),
         FMT_PCT, "calc", True, {"metric": "actual_buyback_tokens", "flag_fn": flag}),
        ("Circulating retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ market cap — alongside",
         zero_or(lambda r, p: f"IF({gate(r)},{_annualise(R, r, p, 'actual_buyback_usd', bb(r))}/{mc(r)},{NA})"),
         FMT_PCT, "calc", False, {"metric": "actual_buyback_usd", "flag_fn": flag}),
        ("FDV retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ FDV — alongside",
         zero_or(lambda r, p: f"IF({gate_fd(r)},{_annualise(R, r, p, 'actual_buyback_usd', bb(r))}/{fdv(r)},{NA})"),
         FMT_PCT, "calc", False, {"metric": "actual_buyback_usd", "flag_fn": flag}),
    ]


def _no_issuance_text(p: dict) -> str:
    """'n/a — …' for fees ÷ issuance where nothing is issued (Round G6 item 9: the ratio read MISSING "gross_issuance_tokens
    nothing stored" on Chainlink, Ether.fi, Fluid, GEODNET, Hyperliquid, Aethir, Plume — pre-minted tokens with no
    issuance to divide by); a fixed supply with a recorded mint stream (Maple) keeps the ratio. '' otherwise."""
    why = config.fixed_supply_reason(p["name"])
    if p["name"] in getattr(config, "LATENT_SUPPLY", {}):
        return ""                        # minted lazily (Pendle): issuance exists, accrued but unminted (G6 item 3)
    if why and "gross_issuance_tokens" not in config.metrics_for_project(p):
        return f"n/a — no issuance to divide by: {why}"[:240]
    if why and p["name"] not in config.FIXED_SUPPLY_NOT:
        return f"n/a — no issuance to divide by: {why}"[:240]
    return ""


def _fixed_supply_text(p: dict) -> str:
    """'0 — fixed supply …' for a project that mints nothing (config.fixed_supply_reason; fix round 2, R9), else ''."""
    why = config.fixed_supply_reason(p["name"])
    if why:
        return f"0 — fixed supply: {why}"[:240]
    # MINTED ONLY INTO THE ISSUER'S OWN WALLET (fix round 2, S3: Maple): the mints are new supply, but none reaches
    # circulation until it leaves the treasury, then it is a release (e) — said with the Q0 figure, never a bare 0
    if p["name"] in config.ISSUANCE_FROM_TOTAL_SUPPLY and _LAST_GROUPS.get((p["name"], "gross_issuance_tokens")) is not None:
        return ""                                   # G11 B2: the mint is shown, derived from the stored total supply
    if p["name"] in config.FIXED_SUPPLY_NOT and (config.ISSUER_MINTS.get(p["name"]) or {}).get("events"):
        g = _LAST_GROUPS.get((p["name"], "issuer_mint_tokens"))
        q0 = None
        if g is not None and "q0" in _WINDOWS:
            a_, b_ = (pd.Timestamp(x) for x in _WINDOWS["q0"])
            d_ = pd.to_datetime(g["date"])
            q0 = float(g.loc[(d_ > a_) & (d_ <= b_), "value"].sum())
        return (f"n/a — minted only into the issuer's treasury: {q0:,.0f} in Q0 (issuer_mint_tokens, Flows memo; "
                f"{config.FIXED_SUPPLY_NOT[p['name']]}) — circulating only when it leaves, then a release (e)")[:240] \
            if q0 is not None else ""
    return ""


def _premint(p: dict) -> bool:
    """A pre-minted supply (config.issuance_basis = pool_release_tokens): nothing is minted, so net ISSUANCE is
    mint − burn = −burn; what enters circulation is an issuer RELEASE, shown on its own line (accuracy pass on 1e702bb,
    item 9: Chainlink's net issuance read 18,750,000 on a fixed-supply token — a release labelled as issuance)."""
    return config.issuance_basis(p["name"]) == "pool_release_tokens"


def _basis_iss(R: Refs, p: dict):
    m = config.issuance_basis(p["name"])
    return lambda r, w="q0": R.D(r, m, w)


def _a4_window_caveat(p: dict, data_by_key: dict) -> str:
    """Why A4's headline for this project is NOT YET A RATE, in numbers — or "" when it is.

    THE TWO SIDES OF THE CROSSOVER ARE TRUNCATED DIFFERENTLY, and "unaffected" was the wrong word
    for that (corrected 2026-09-25). A continuous schedule in a part-filled Q0 window is short in
    proportion to the days elapsed; a discrete burn is either in the window or not. Their ratio is
    what actually happened in the window on both sides, with no padding, and it moves as the
    window fills even when nothing about the economics changes: linearly on the issuance side, in
    steps of one burn on the burn side. So it is not comparable across periods until the window
    is full AND holds at least three burn events (a 90-day window holds ~3 monthly burns).
    """
    name = p["name"]
    period, ann = GLOBALS["period_days"], GLOBALS["days_per_year"]

    def num(v):
        return isinstance(v, (int, float)) and v == v

    parts, until = [], []
    iss = data_by_key.get(f"{name}|{config.issuance_basis(name)}") or {}
    c = iss.get("q0_covered_days")
    # THE MIRROR CASE, checked first: a schedule past its declared end OVERSTATES what is issued
    # now (the true rate is zero) for as long as the window still reaches back into it. When it
    # fires, the "run-rate" wording below does not apply — there is no running rate.
    pre = iss.get("q0_pre_end_days")
    expired = ""
    if iss.get("schedule_end") and num(pre) and pre > 0:
        expired = (f"ISSUANCE STREAM ENDED {iss['schedule_end']} — Q0 window still includes "
                   f"{int(pre)} pre-expiry day(s); annualised figure reflects a schedule that no "
                   f"longer applies, not current issuance. Clears once the full {period}-day "
                   f"window is entirely post-expiry.")
    elif num(c) and 0 < c < period and num(iss.get("q0")):
        shown, true = iss["q0"] * ann / period, iss["q0"] / c * ann
        parts.append(f"ISSUANCE covers {int(c)} of {period} days, so annualised as if full it reads "
                     f"{shown:,.0f}/yr against a {true:,.0f}/yr run-rate ({period / c:.2f}x low)")
        until.append(f"the issuance window fills ({period - int(c)} more day(s))")
    b = data_by_key.get(f"{name}|{config.a4_burn_metric(name)}") or {}
    k, bc = b.get("q0_events"), b.get("q0_covered_days")
    if num(k) and 0 < k < 3:
        parts.append(f"BURN is {int(k)} discrete event(s) in the window, not a rate"
                     + (f" (the series covers {int(bc)} of {period} days)"
                        if num(bc) and bc < period else ""))
        until.append("at least 3 burn events are in it")
    stable = ("NOT A STABLE RATE YET — " + "; ".join(parts) + ". Burn yield and burn ÷ issuance "
              "show what actually happened in this window on both sides, with no padding; they "
              "will move as the window fills even if nothing changes, and are not comparable "
              "across periods until " + " and ".join(until) + ".") if parts else ""
    return " | ".join(x for x in (expired, stable) if x)


def _ff_lock(R: Refs, r, p: dict) -> str:
    """The locked tokens INSIDE the circulating in use (free float = circulating − this): locked_tokens plus any
    declared extra lock leg (config.free_float_lock_metrics), less locked tokens already out of circulating."""
    if p.get("free_float_lock_zero"):
        return "0"
    legs = [R.D(r, m, "now") for m in config.free_float_lock_metrics(p.get("name", ""))]
    # a missing extra leg makes the cell n/a (an error propagates) — never a silent 0 that reads free float HIGH
    base = legs[0] if len(legs) == 1 else "(" + "+".join(legs) + ")"
    # less the stake held outside circulating — the declared constant or the MEASURED balances (Aerodrome's filing
    # wallets' managed veNFT AERO, Jake's probes16; Hyperliquid's issuer-side stake, item 23 / accuracy item 1)
    x = config.locked_excluded_from_circulating(p.get("name", ""))
    if x and config.circulating_excludes_declared(p["name"]):
        return f"MAX(0,{base}-{x:.0f})"
    xm = [R.D(r, m, "now") for m in config.locked_excluded_metrics(p.get("name", ""))]
    if xm and config.circulating_excludes_declared(p["name"]):
        return f"MAX(0,{base}-" + "-".join(xm) + ")"
    return base


def _stake_inside(R: Refs, r, p: dict, metric: str = "locked_tokens", col: str = "now") -> str:
    """STAKE INSIDE CIRCULATING (Jake's data-accuracy pass on 1e702bb, item 1): the stake series `metric` LESS the stake
    held by wallets already outside circulating — the declared constant (config.locked_excluded_from_circulating) or the
    measured balances (config.locked_excluded_metrics: Hyperliquid's team / Foundation / Grants stake, 301.3M of 440.1M
    on 2026-10-13; Aerodrome's filing wallets' managed veNFTs). Every float, lock rate and whole-token yield reads this,
    so a stake held outside circulating is never subtracted from (or divided by) the circulating figure."""
    name = p.get("name", "")
    base = R.D(r, metric, col)
    if not config.circulating_excludes_declared(name):
        return base
    x = config.locked_excluded_from_circulating(name)
    if x:
        return f"MAX(0,{base}-{x:.0f})"
    xm = [R.D(r, m, col) for m in config.locked_excluded_metrics(name)]
    if xm:
        return f"MAX(0,{base}-" + "-".join(xm) + ")"
    return base


def _circ(R: Refs, r, p: dict) -> str:
    """circulating_supply "now" — NET of any burn the provider has not netted, where declared.

    GEODNET, 2026-09-28 (config supply_unnetted_burn): CoinGecko's total_supply is 1bn minus the
    POLYGON burn exactly (961,518,067.62 = 1e9 - 38,481,932.38 on 2026-09-21), so ~29.4M GEOD
    burned on Solana still counts as supply. The un-netted burn is measured, not assumed:
        CoinGecko total - (contract totalSupply - summed burn balance)
    which is the Solana balance while CoinGecko nets Polygon only, and falls to ~0 by itself if
    it starts netting both. Negative means CoinGecko nets MORE than we can see burned: the
    denominator becomes an error and the cell reads n/a rather than guessing.
    """
    used = R.D(r, "circulating_supply_used", "now")
    # never an Excel error (alignment round, Coverage: GEODNET / Aerodrome / Pendle read #VALUE! / #N/A on an empty
    # store): the fallback's error becomes the reasoned n/a of the cell that shows it
    return f"IFERROR(IF(ISNUMBER({used}),{used},{_circ_formula(R, r, p)}),{NA})"


def _circ_formula(R: Refs, r, p: dict) -> str:
    """The pre-2026-10-11 formula, kept as the fallback where no circulating_supply_used row exists."""
    c = R.D(r, "circulating_supply", "now")
    # ON-CHAIN / FIRST-PARTY FIRST where the set is established (M, Jake 2026-09-30): Ethereum's
    # total supply, Hyperliquid's own tokenDetails.circulatingSupply. CoinGecko is the fallback
    # and the cross-check. A PARTIAL set never replaces CoinGecko — it reads high.
    spec = config.circulating_onchain(p["name"]) or {}
    if config.circulating_onchain_primary(p["name"]):
        m = config.circulating_primary_metric(p["name"])
        own = R.D(r, m, "now")
        # COINGECKO'S FIGURE IS FREE FLOAT for Pendle / Aerodrome (2026-10-06 overnight, A1/A2): on a day with no
        # on-chain figure, circulating falls back to CoinGecko + the locked tokens inside it — so free float
        # (circulating − locked) is CoinGecko's own figure, never CoinGecko − locked a SECOND time.
        fb = f"{c}+{_ff_lock(R, r, p)}" if config.coingecko_is_free_float(p["name"]) else c
        if config.no_coingecko_fallback(p["name"]):
            fb = "NA()"                              # another basis, never a stand-in (Phase 1 review, item 2)
        if config.supply_unnetted_burn(p["name"]):
            # The un-netted burn is CoinGecko's: the project's own figure is used as published (GEODNET, 2026-10-07);
            # only the CoinGecko fallback is netted.
            return f"IF(ISNUMBER({own}),{own},{_net_unnetted_burn(R, r, fb)})"
        c = f"IF(ISNUMBER({own}),{own},{fb})"
    if not config.supply_unnetted_burn(p["name"]):
        return c
    return _net_unnetted_burn(R, r, c)


def _net_unnetted_burn(R: Refs, r, c: str) -> str:
    tot, gross, burned = (R.D(r, m, "now") for m in ("total_supply", "total_supply_gross",
                                                     "burn_address_balance"))
    adj = f"ROUND({tot}-({gross}-{burned}),2)"
    return f"IF({adj}>=0,{c}-{adj},NA())"


def _a4_headline(R: Refs, burn, data_by_key: dict | None = None) -> list[tuple]:
    """Burn yield in TOKEN terms (price-independent, comparable across protocols) and the
    crossover against whichever issuance applies — pool release where the supply is pre-minted."""
    circ = lambda r, p: _circ(R, r, p)  # noqa: E731
    return [
        ("PERMANENT BURN YIELD = burn as % of supply (annualised, tokens)",
         lambda r, p: calc(f"{_annualise(R, r, p, config.a4_burn_metric(p['name']), burn(r, p))}/({circ(r, p)})"), FMT_PCT, "calc", True, {"metric_fn": config.a4_burn_metric}),
        ("Issuance basis for the crossover",
         lambda r, p: ("pool_release_tokens — supply pre-minted" if config.issuance_basis(p["name"]) == "pool_release_tokens"
                       else "gross_issuance_tokens"), FMT_TEXT, "text"),
        ("BURN ÷ ISSUANCE (x) — crossover, on the basis to the left",
         lambda r, p: _zero_issuance(R, r, p, '"no issuance — burn only"') or _coverage_guard(
             R, r, [("burn", config.a4_burn_metric(p["name"]), "q0"),
                    ("issuance", config.issuance_basis(p["name"]), "q0")],
             f"{_flow_rate(R, r, config.a4_burn_metric(p['name']), p=p)}/"
             f"{_flow_rate(R, r, config.issuance_basis(p['name']), p=p)}"), FMT_X, "calc", True),
        # RIGHT BESIDE THE TWO FIGURES IT QUALIFIES. Empty where the window is full and the burn
        # is a stream; otherwise it says, with numbers, why the pair is not yet a rate.
        ("Q0 WINDOW CAVEAT — read before the yield and crossover",
         lambda r, p: _a4_window_caveat(p, data_by_key or {}), FMT_TEXT, "text", True),
        # A1 CHAINS' FEE BURNS STAY OFF A4 (T-c; G7 decision 0c, Jake 2026-10-19): the A4 net reads config.a4_net_burn_metric
        # — Hyperliquid's every burn WITHOUT the HyperEVM gas burn, which is A1 only
        ("NET SUPPLY CHANGE Q0 (tokens) — self-reported where published, else issuance − burn, on the basis to the left",
         lambda r, p: _zero_issuance(R, r, p, f"-{R.D(r, config.a4_net_burn_metric(p['name']), 'q0')}")
         or _net_guarded(R, r, p, _basis_iss(R, p), burn, bm=config.a4_net_burn_metric(p["name"])),
         FMT_NUM, "calc", True),
        ("NET SUPPLY CHANGE, annualised % of circulating (signed; + is net inflation)",
         lambda r, p: _zero_issuance(
             R, r, p, f"-{_annualise(R, r, p, config.a4_net_burn_metric(p['name']), R.D(r, config.a4_net_burn_metric(p['name']), 'q0'))}/({circ(r, p)})")
         or _net_annual_pct(R, r, p, circ(r, p), bm=config.a4_net_burn_metric(p["name"])), FMT_PCT, "calc", True),
    ]


def _zero_issuance(R: Refs, r: int, p: dict, expr: str) -> str | None:
    """A4 for a project that issues nothing (config issuance_declared_zero; Uniswap, Jake
    2026-09-30): the burn-only cell — or None for everyone else. A positive MEASURED issuance in
    the window blocks it with the figure: the declaration is checked, not trusted."""
    z = config.issuance_declared_zero(p["name"])
    if not z:
        return None
    m = R.D(r, z["check_metric"], "q0")
    return (f'=IF(IFERROR({m}>0,FALSE),"BLOCKED — issuance declared zero ({z["decided_by"]}, '
            f'{z["decided_on"]}) but "&TEXT({m},"#,##0")&" measured in the window",IFERROR({expr},{NA}))')


def _free_float_reason(p: dict) -> str | None:
    """Why free float cannot be computed for this project — or None if it can."""
    name = p["name"]
    if p.get("free_float_lock_zero"):
        return None          # nothing is locked: free float = circulating (Morpho, 2026-09-28)
    if "locked_tokens" not in config.metrics_for_project(p):
        return "n/a — " + (config.not_applicable_reason(name, "locked_tokens") or "no lock metric")[:90]
    blocked = p.get("locked_tokens_blocked")
    if blocked and "locked_tokens" not in (p.get("manual_quarterly") or ()):
        return "blocked — " + str(blocked.get("status", ""))[:90]
    return None


def _a2_headline(R: Refs) -> list[tuple]:
    """Free float, its market cap, Free Float / ARR (the note's WMTx headline) and the supply
    trajectory. Where the lock cannot be read the cells SAY SO — circulating is never passed
    off as free float, which would overstate it by everything staked."""
    circ = lambda r, p: _circ(R, r, p)  # noqa: E731
    # A DECLARED ZERO LOCK (free_float_lock_zero) is the number 0, not a missing figure.
    # LOCKED TOKENS ALREADY OUT OF CIRCULATING ARE SUBTRACTED ONCE (Jake, 2026-09-30): free float =
    # circulating − the locked tokens INSIDE circulating. Where the circulating figure in use has
    # the declared locked exclusions taken out (Aerodrome's team 95M, once its on-chain set is
    # primary), they leave the locked total too.
    def lock(r, p=None):
        return _ff_lock(R, r, p or {})
    price = lambda r: R.D(r, "price_usd", "now")  # noqa: E731
    # OVER COVERED DAYS (2026-09-28): a young customer-revenue series is not divided as if it
    # were 90 days old — see _annualise.
    # THE PROJECT'S OWN ARR IS THE HEADLINE WHERE IT PUBLISHES ONE (Aethir's demand-metric tile,
    # Jake 2026-10-01); annualised customer revenue otherwise, and where the ARR cell is empty.
    def arr(r, p):
        ann = _annualise(R, r, p, "customer_revenue_usd", R.D(r, "customer_revenue_usd", "q0"))
        if not p.get("arr_view") and not any(f.get("metric") == "arr_usd"
                                             for f in ((p.get("dashboard_pages") or {}).get("labelled") or {}).values()):
            return ann
        own = R.D(r, "arr_usd", "now")
        return f"IF(ISNUMBER({own}),{own},{ann})"
    emi = lambda r: R.D(r, "emissions_tokens", "q0")  # noqa: E731
    ff = lambda r, p: f"IF(AND(ISNUMBER({circ(r, p)}),ISNUMBER({lock(r, p)})),{circ(r, p)}-{lock(r, p)},{NA})"  # noqa: E731

    def cell(expr_fn):
        def build(r, p):
            why = _free_float_reason(p)
            return why if why else calc(expr_fn(r, p))
        return build

    partial = {"partial_fn": lambda p: config.lock_partial_reason(p["name"]),
               "partial_direction": "the lock figure is incomplete, so FREE FLOAT READS HIGH by "
                                    "whatever the missing leg holds; ratios built on it inherit that."}
    return [
        ("FREE FLOAT = circulating − locked (tokens)", cell(ff), FMT_NUM, "calc", True,
         {"metric": "locked_tokens", **partial, "partial_fmt": '#,##0" PARTIAL↑";(#,##0)" PARTIAL↑"'}),
        ("Free float market cap ($, spot)", cell(lambda r, p: f"({ff(r, p)})*{price(r)}"), FMT_USD, "calc", False,
         {"metric": "locked_tokens", **partial, "partial_fmt": '$#,##0" PARTIAL↑";($#,##0)" PARTIAL↑"'}),
        ("FREE FLOAT ÷ ARR (x) = free float market cap ÷ ARR (the project's own where published, else customer revenue annualised) — headline",
         cell(lambda r, p: f"({ff(r, p)})*{price(r)}/({arr(r, p)})"), FMT_X, "calc", True,
         {"metric": "customer_revenue_usd", **partial, "partial_fmt": '0.00"x PARTIAL↑";(0.00"x)" PARTIAL↑"'}),
        ("SUPPLY TRAJECTORY = emissions (annualised) ÷ free float — annual dilution %",
         lambda r, p: _free_float_reason(p) or calc(f"{_annualise(R, r, p, 'emissions_tokens', emi(r))}/({ff(r, p)})"), FMT_PCT, "calc", True,
         {"metric": "emissions_tokens", **partial, "partial_fmt": '0.0%" PARTIAL↓";(0.0%)" PARTIAL↓"'}),
        _published_unlock_column(),
    ]


UNLOCK_HEAD = "UNLOCK DILUTION Q0 (tokens) — released from the non-circulating wallets, NOT emissions"
UNLOCK_PCT_HEAD = "Unlock dilution, annualised % of free float"
TOTAL_DILUTION_HEAD = "TOTAL DILUTION = emissions + unlocks, annualised % of free float"
NET_ABS_UNLOCK_HEAD = "Net absorption incl. unlocks Q0 (tokens) = RETIRED buyback − emissions − unlocks"


def _ff_expr(R, r: int, p: dict) -> str:
    """Free float = circulating − the lock inside it (as A2's FREE FLOAT column)."""
    c, lk = _circ(R, r, p), _ff_lock(R, r, p)
    return f"IF(AND(ISNUMBER({c}),ISNUMBER({lk})),{c}-{lk},{NA})"


def _unlock_cols(R: Refs, absorption: bool = False) -> list[tuple]:
    """UNLOCK DILUTION (Jake 2026-10-11, decision A2 on 5b87610) — one block, the same cells on A2, A3 and A4 (the
    cross-tab test holds them equal): the Q0 releases from the non-circulating wallets (unlock_release_tokens,
    _unlock_views), annualised over the days covered, as % of free float; TOTAL DILUTION = emissions (the tab's
    absorption basis, config.absorption_emission_metrics) + unlocks; on A3 net absorption on that basis. Blank where the
    project has no non-circulating wallet set (config.unlock_stock_metrics). Unlocks stay OUT of emissions_tokens
    (Jake 2026-09-24)."""
    def has(p):
        return bool(config.unlock_stock_metrics(p["name"]))

    def unl(r):
        return R.D(r, "unlock_release_tokens", "q0")

    def ann_unl(r, p):
        return _annualise(R, r, p, "unlock_release_tokens", unl(r))

    def ann_em(r, p):
        return "+".join(_annualise(R, r, p, m, R.D(r, m, "q0")) for m in config.absorption_emission_metrics(p["name"]))

    def ff_cell(fn):
        def build(r, p):
            if not has(p):
                return ""
            why = _free_float_reason(p)
            return why if why else calc(fn(r, p))
        return build
    cols = [
        # NOT BOLD (not Credibility headlines yet): no independent reference exists for most projects' releases
        # (Chainlink's reconciled outflow scan is the one candidate) — RUNBOOK open issues; export_snapshot carries
        # them by header (EXTRA_SNAPSHOT_HEADERS)
        (UNLOCK_HEAD, lambda r, p: pull(unl(r)) if has(p) else "", FMT_NUM, "pull", False,
         {"metric": "unlock_release_tokens"}),
        (UNLOCK_PCT_HEAD, ff_cell(lambda r, p: _coverage_guard(
            R, r, [("unlocks", "unlock_release_tokens", "q0")],
            f"({ann_unl(r, p)})/({_ff_expr(R, r, p)})")[1:]), FMT_PCT, "calc", False,
         {"metric": "unlock_release_tokens"}),
        (TOTAL_DILUTION_HEAD, ff_cell(lambda r, p: _absorption_guard(      # D7-2: a SUM over a stock, not a net
            R, r, p, [("unlocks", "unlock_release_tokens", "q0")],
            f"(({ann_em(r, p)})+({ann_unl(r, p)}))/({_ff_expr(R, r, p)})", same_span=False)[1:]), FMT_PCT, "calc", False,
         {"metric": "unlock_release_tokens"}),
    ]
    if absorption:
        cols.append((NET_ABS_UNLOCK_HEAD, lambda r, p: "" if not has(p) else _absorption_guard(
            R, r, p, [("buyback", "actual_buyback_tokens", "q0"), ("unlocks", "unlock_release_tokens", "q0")],
            net_absorption(p, R.D(r, "retired_buyback_tokens", "q0"), _absorption_emissions(R, r, p)) + f"-{unl(r)}",
            same_span=True), FMT_NUM, "calc", False, {"supply_additive": True}))
    return cols


RETIRE_VS_DILUTION_HEAD = ("RETIREMENT ÷ DILUTION (x) = RETIRED buyback ÷ (emissions + unlocks), per covered day — "
                           "1.0 is the crossover")


def _retire_vs_dilution_col(R: Refs) -> tuple:
    """THE A3 CROSSOVER AS A RATIO (theory layer, Phase 1, 2026-10-12): retired buyback tokens (burned + held) per
    covered day ÷ (the tab's absorption emissions + unlock releases, each per its own covered day). Denominator-free,
    so it is the series the retirement-vs-dilution benchmark runs on (config.TRAJECTORY). Unlocks join only where the
    project has a non-circulating wallet set (config.unlock_stock_metrics); a structural 0 retirement reads 0."""
    def build(r, p):
        if _retirement_zero(p):
            return "=0"
        name = p["name"]
        ems = list(config.absorption_emission_metrics(name))
        unl = ["unlock_release_tokens"] if config.unlock_stock_metrics(name) else []
        den = "+".join(_flow_rate(R, r, m, p=p) for m in ems + unl)
        legs = [("buyback", "retired_buyback_tokens", "q0")] + [("unlocks", m, "q0") for m in unl]
        return _absorption_guard(R, r, p, legs, f"{_flow_rate(R, r, 'retired_buyback_tokens', p=p)}/({den})")
    return (RETIRE_VS_DILUTION_HEAD, build, FMT_X, "calc", False,
            {"metric": "retired_buyback_tokens", "trajectory_id": "a3_retire_vs_dilution"})


CURRENT_RATE_HEAD = "Emissions — the rate IN FORCE, annual (tokens) — the Q0 figure is the measured blend"
RETIRE_VS_DILUTION_NOW_HEAD = ("RETIREMENT ÷ DILUTION at the emission rate IN FORCE (x) — forward-looking rows "
                               "(years to crossover) use it")


def _current_rate_cols(R: Refs) -> list[tuple]:
    """THE EMISSION RATE IN FORCE (Jake 2026-10-12, decision 2 on 6f8c86c): where a project's emissions are re-set by
    schedule (Sky's DssVest awards), Q0 measures the blend and these show the rate in force, and the crossover ratio at
    it — the series the benchmark's years use (config.CURRENT_EMISSION_RATE). Blank elsewhere."""
    def has(p):
        return p["name"] in config.CURRENT_EMISSION_RATE

    def rate(r, p):
        return R.D(r, config.CURRENT_EMISSION_RATE[p["name"]]["metric"], "now")

    def ratio(r, p):
        if not has(p):
            return ""
        if _retirement_zero(p):
            return "=0"
        cr = config.CURRENT_EMISSION_RATE[p["name"]]
        name = p["name"]
        others = [m for m in config.absorption_emission_metrics(name) if m != cr["replaces"]]
        unl = ["unlock_release_tokens"] if config.unlock_stock_metrics(name) else []
        den = "+".join([f"({rate(r, p)}/{G('days_per_year')})"] + [_flow_rate(R, r, m, p=p) for m in others + unl])
        legs = [("buyback", "retired_buyback_tokens", "q0")] + [("unlocks", m, "q0") for m in unl]
        return _coverage_guard(R, r, legs, f"{_flow_rate(R, r, 'retired_buyback_tokens', p=p)}/({den})")
    return [
        (CURRENT_RATE_HEAD, lambda r, p: pull(rate(r, p)) if has(p) else "", FMT_NUM, "pull", False,
         {"metric": "emissions_rate_current_annual_tokens"}),
        (RETIRE_VS_DILUTION_NOW_HEAD, ratio, FMT_X, "calc", False,
         {"metric": "retired_buyback_tokens", "trajectory_id": "a3_retire_vs_dilution_now"}),
    ]


RETIRE_VS_EMISSIONS_HEAD = ("RETIRED ÷ EMISSIONS (x) = RETIRED buyback ÷ emissions, unlocks EXCLUDED, at the emission "
                            "rate IN FORCE — A3's rank key (P1-6)")


def _retire_vs_emissions_col(R: Refs) -> tuple:
    """A3'S RANK KEY (P1-6, Jake 2026-10-27): retired buyback tokens (burned + held) per covered day ÷ the tab's
    absorption emissions per covered day — unlocks excluded — with the emission rate IN FORCE in place of the series it
    replaces where one is declared (config.CURRENT_EMISSION_RATE: Sky); elsewhere the last 90 days' measured rate is the
    rate in force. A structural 0 retirement reads 0; a project with no emissions series says so."""
    def build(r, p):
        if _retirement_zero(p):
            return "=0"
        name = p["name"]
        cr = config.CURRENT_EMISSION_RATE.get(name)
        ems = [m for m in config.absorption_emission_metrics(name) if not cr or m != cr["replaces"]]
        parts = ([f"({R.D(r, cr['metric'], 'now')}/{G('days_per_year')})"] if cr else []) + \
            [_flow_rate(R, r, m, p=p) for m in ems]
        if not parts:
            return "n/a — no emissions series to divide by"
        legs = [("buyback", "retired_buyback_tokens", "q0")]
        expr = f"{_flow_rate(R, r, 'retired_buyback_tokens', p=p)}/({'+'.join(parts)})"
        return _coverage_guard(R, r, legs + [(m, m, "q0") for m in ems], expr) if cr else \
            _absorption_guard(R, r, p, legs, expr)
    return (RETIRE_VS_EMISSIONS_HEAD, build, FMT_X, "calc", False, {"metric": "retired_buyback_tokens"})


EXTRA_SNAPSHOT_HEADERS = (UNLOCK_HEAD, UNLOCK_PCT_HEAD, TOTAL_DILUTION_HEAD, NET_ABS_UNLOCK_HEAD, RETIRE_VS_DILUTION_HEAD,
                          CURRENT_RATE_HEAD, RETIRE_VS_DILUTION_NOW_HEAD, RETIRE_VS_EMISSIONS_HEAD)


def _published_unlock_column() -> tuple:
    """A2 (Aethir, Jake 2026-10-07): UNLOCK-DRIVEN dilution from the project's own published circulating schedule
    (config.CIRCULATING_SCHEDULE), next 12 months, beside supplier emissions — the schedule counts every unlock
    (team, investors, ecosystem), not only rewards. Blank where no schedule is published."""
    def build(r, p):
        ch = config.circulating_schedule_change(p["name"], pd.Timestamp.today())
        return ch["next"][2] if ch and ch.get("next") else ""

    def note(p):
        ch = config.circulating_schedule_change(p["name"], pd.Timestamp.today())
        if not ch:
            return None
        spec = config.CIRCULATING_SCHEDULE[p["name"]]
        parts = [f"{k}: {v[0]} -> {v[1]} {v[2]:+.1%}" for k, v in ch.items() if k in ("last", "next")]
        return (" · schedule", f"PUBLISHED CIRCULATING SCHEDULE ({spec['source']}, read by {spec['read_by']}): "
                               + "; ".join(parts) + ". Counts every unlock, not only supplier rewards.")
    return ("PUBLISHED UNLOCK SCHEDULE — next 12 months, circulating growth from the project's own table (all unlocks)",
            build, FMT_PCT, "calc", False, {"flag_fn": note})


def _eth_yield_parts(R: Refs, r: int, p: dict, spec: dict) -> tuple[str, str, list]:
    """(consensus, execution, coverage legs) for the consensus_plus_execution method (Ethereum,
    2026-09-29): consensus rewards (d Eth2Staking, Jake's decision — issuance where no
    consensus_metric is declared) and priority fees, each annualised over its own covered days,
    over ETH on the beacon chain. Priority fees = DefiLlama fees - burned revenue, at the Q0
    average price. EXCLUDING MEV."""
    stake = R.D(r, spec["stake_metric"], "now")
    cm = spec.get("consensus_metric") or spec["issuance_metric"]
    fm, bm = spec["fees_metric"], spec["burned_metric"]
    cons = f"{_annualise(R, r, p, cm, R.D(r, cm, 'q0'))}/{stake}"
    prio = f"({R.D(r, fm, 'q0')}-{R.D(r, bm, 'q0')})"
    exe = f"{_annualise(R, r, p, fm, prio)}/{R.D(r, 'price_usd', 'q0')}/{stake}"
    legs = [("consensus rewards", cm, "q0"), ("fees", fm, "q0"), ("burned fees", bm, "q0")]
    em = spec.get("execution_metric")
    if em:
        # MEV INCLUDED (Jake, 2026-10-02): relay-delivered value + non-relay priority fees, in ETH, where
        # the series is held; the fee-based estimate (excluding MEV) otherwise. A PARTIAL execution row
        # (no non-relay leg) carries its marker to the cell's caveat like any other.
        mev = f"{_annualise(R, r, p, em, R.D(r, em, 'q0'))}/{stake}"
        exe = f"IF(ISNUMBER({R.D(r, em, 'q0')}),{mev},{exe})"
        legs.append(("execution rewards (MEV included)", em, "q0"))
    return cons, exe, legs


def _eth_yield_columns(R: Refs) -> list[tuple]:
    """Beside A1's validator yield, for a consensus_plus_execution project only (Ethereum): the
    two parts and issuance against the protocol's own maximum (beaconcha.in's cross-check was
    dropped with the source, 2026-10-01)."""
    def spec_of(p):
        s = config.VALIDATOR_YIELD.get(p["name"]) or {}
        return s if s.get("method") == "consensus_plus_execution" else None

    def part(i):
        def build(r, p):
            s = spec_of(p)
            if not s:
                return ""
            parts = _eth_yield_parts(R, r, p, s)
            return _coverage_guard(R, r, parts[2], parts[i], same_span=False)    # D7-2: yield parts add, not net
        return build

    def ceiling(r, p):
        s = spec_of(p)
        f = (s or {}).get("issuance_formula")
        if not f:
            return ""
        # THE STAKE OVER THE SAME WINDOW (A3, 2026-09-30): ultrasound.money's daily staked-ETH
        # series, Q0 average, where declared; today's reading otherwise.
        fsm = s.get("formula_stake_metric")
        stake = R.D(r, fsm, "q0") if fsm else R.D(r, s["stake_metric"], "now")
        im = s["issuance_metric"]
        return _coverage_guard(R, r, [("issuance", im, "q0")],
                               f"{_annualise(R, r, p, im, R.D(r, im, 'q0'))}/({f['coefficient']}*SQRT({stake}))")
    return [
        ("Consensus part (d Eth2Staking ÷ ETH on the beacon chain)", part(0), FMT_PCT, "calc"),
        ("Execution part (relay-delivered value + non-relay priority fees ÷ the same; MEV INCLUDED where held)", part(1), FMT_PCT, "calc"),
        ("Issuance ÷ protocol maximum 166.32·√staked (consensus-specs; <1 = missed duties)", ceiling, FMT_X, "calc"),
    ]


def _net_inflation(R: Refs, r: int, p: dict) -> str:
    """Net supply change, annualised, as a fraction of circulating — A4's headline, which Master's 'Net supply change,
    annualised % of circulating' shows (cross-tab linked): the same expression for Total Yield (decision 9)."""
    circ = _circ(R, r, p)
    bm = config.a1_burn_metric(p["name"])
    return (_zero_issuance(R, r, p, f"-{_annualise(R, r, p, bm, R.D(r, bm, 'q0'))}/({circ})")
            or _net_annual_pct(R, r, p, circ))


TOTAL_YIELD_STAKERS_HEAD = ("TOTAL YIELD — stakers = validator staking yield − net inflation − issuer releases (annual; "
                            "rewards and foundation releases paid from issuer reserves count)")
TOTAL_YIELD_HOLDERS_HEAD = "Total yield — non-stakers = − (net inflation + issuer releases) (annual)"


def _total_yield_dilution(R: Refs, r: int, p: dict) -> str | None:
    """Total Yield's dilution as an expression (no "="): net inflation (Master's figure, _net_inflation) + the issuer
    releases not already inside it (config.TOTAL_YIELD_RELEASES — Jake's overnight review of ab5f2a9, item 17), each
    annualised over its own covered days, ÷ circulating; BLOCKED text where a release leg covers under a week. None where
    no net-inflation figure exists. A project whose releases cannot be measured returns its reason (text, not a formula)."""
    spec = config.TOTAL_YIELD_RELEASES.get(p["name"]) or {}
    if spec.get("unmeasured"):
        return "n/a — " + spec["unmeasured"]
    ni = _net_inflation(R, r, p)
    ni = ni[1:] if isinstance(ni, str) and ni.startswith("=") else None
    if ni is None:
        return None
    adds = list(spec.get("add") or ())
    # a PRE-MINTED supply's releases are no longer inside net issuance (item 9): Total Yield subtracts them explicitly,
    # so its figure is unchanged — net inflation (mint − burn) + releases
    if _premint(p) and "pool_release_tokens" not in adds and not spec.get("replaces_premint"):
        adds = ["pool_release_tokens"] + adds
    if not adds:
        return ni
    circ = _circ(R, r, p)
    rel = "+".join(_annualise(R, r, p, m, R.D(r, m, "q0")) for m in adds)
    return _coverage_guard(R, r, [(m.replace("_tokens", "").replace("_", " "), m, "q0") for m in adds],
                           f"({ni})+({rel})/({circ})", bare=True, p=p, same_span=False)   # D7-2: releases ADD


def _total_yield_cols(R: Refs, vyield) -> list[tuple]:
    """TOTAL YIELD (Jake 2026-10-12, decision 9 on 6f8c86c; A1): a staker earns the validator yield and is diluted by net
    inflation; a non-staker is only diluted. Net inflation = Master's 'Net supply change, annualised % of circulating'
    (_net_inflation) PLUS the issuer releases not inside it (staking rewards and foundation releases paid from issuer
    reserves — item 17 overnight, config.TOTAL_YIELD_RELEASES). The 3-month T-bill benchmark (Phase 3) sits on the
    stakers' figure."""
    def inner(f):
        return f[1:] if isinstance(f, str) and f.startswith("=") else None

    def stakers(r, p):
        vy = inner(vyield(r, p))
        if vy is None:
            return ""                                     # no validator yield on file: no staker figure
        dil = _total_yield_dilution(R, r, p)
        if dil is None:
            return "n/a — no net inflation figure"
        if dil.startswith("n/a"):
            return dil
        # a BLOCKED net inflation (unequal coverage) passes through with its reason, never a bare n/a
        return calc(f"IF(ISNUMBER({dil}),({vy})-({dil}),{dil})")

    def holders(r, p):
        dil = _total_yield_dilution(R, r, p)
        if dil is None:
            return "n/a — no net inflation figure"
        if dil.startswith("n/a"):
            return dil
        return calc(f"IF(ISNUMBER({dil}),-({dil}),{dil})")
    return [(TOTAL_YIELD_STAKERS_HEAD, stakers, FMT_PCT, "calc", True,
             {"metric_fn": lambda n: {"Ethereum": "beacon_chain_eth", "Near": "total_supply",
                                      "Chainlink": "reward_emission_rate_annual"}.get(n)}),
            (TOTAL_YIELD_HOLDERS_HEAD, holders, FMT_PCT, "calc", False,
             {"trajectory_id": "a1_total_yield_holders"}),
            *_tbill_cols(R, [("Total yield — stakers", stakers), ("Total yield — non-stakers", holders)])]


TBILL_HEAD = ("3-MONTH T-BILL — risk-free benchmark (US Treasury 13-week coupon equivalent; the quarter's mean on a "
              "shifted quarter)")


def _tbill_cols(R: Refs, yields: list[tuple]) -> list[tuple]:
    """THE RISK-FREE BENCHMARK BESIDE A YIELD (Phase 3, overnight 2026-10-12): the 3-month T-bill (Data 'Macro|
    tbill_3m_yield', fetch/treasury.py) and, per yield, yield − T-bill. A yield that is text (n/a with its reason) or
    blank leaves its spread blank; no T-bill stored reads n/a, never 0."""
    tb = lambda: R.X("tbill_3m_yield", "now")  # noqa: E731 — built per cell, so a builder can be listed without Refs

    def spread(fn):
        def build(r, p):
            f = fn(r, p)
            if not (isinstance(f, str) and f.startswith("=")):
                return ""
            return calc(f"({f[1:]})-({tb()})")
        return build
    return [(TBILL_HEAD, lambda r, p: pull(tb()), FMT_PCT, "pull"),
            *[(f"Spread over the 3-month T-bill: {label}", spread(fn), FMT_PCT, "calc") for label, fn in yields],
            (SSR_HEAD, lambda r, p: pull(R.X("sky_savings_rate", "now")), FMT_PCT, "pull")]


SSR_HEAD = ("Sky Savings Rate — the on-chain dollar savings rate (sUSDS ssr(), fetch/sky_ssr.py), beside the T-bill")


def _nrr_headroom_col(R: Refs, metric: str, what: str, tid: str) -> tuple:
    """NRR HEADROOM (Jake's Phase 1 review 2026-10-12, item 7): NRR is valuation headroom, not a target — M2/GDP
    (Config: nrr_m2_gdp, 0.71) ÷ NRR. Above 1, the market cap is below what the volume would carry at the money-supply
    ratio; the volume itself is the Fundamentals trajectory beside it."""
    def build(r, p):
        if metric not in config.metrics_for_project(p):
            return ""
        n = R.D(r, metric, "now")
        return calc(f"IF(AND(ISNUMBER({n}),{n}>0),{G('nrr_m2_gdp')}/{n},{NA})")
    return (f"NRR HEADROOM ({what}) = M2/GDP ÷ NRR — valuation headroom, not a target", build, FMT_X, "calc", False,
            {"metric": metric, "trajectory_id": tid})


def _a1_headline(R: Refs) -> list[tuple]:
    """The VALIDATOR yield (securing the chain) — never a protocol revenue share — Total Yield beside it (stakers and
    non-stakers, decision 9 on 6f8c86c), and the Network Reserve Ratio with its settlement-volume input (Artemis,
    2026-09-30)."""
    def vyield(r, p):
        spec = config.VALIDATOR_YIELD.get(p["name"])
        if not spec:
            return ""
        if spec["method"] == "stored":
            return pull(R.D(r, spec["metric"], "now"))
        if spec["method"] == "declared_share":
            # DECLARED PRIMARY, OBSERVED AS THE GUARD (2026-09-28). The declared issuance is the
            # protocol rate x live total supply; the observed one is annualised over the days it
            # actually covers. More than max_ratio apart either way BLOCKS the cell with both
            # figures on it — one of them is wrong, and the sheet should not pick.
            def walk(path):
                v = p
                for k in path:
                    v = v[k]
                return v
            rate, share = walk(spec["rate_path"]), walk(spec["share_path"])
            supply = R.D(r, spec["supply_metric"], "now")
            stake = R.D(r, "locked_tokens", "now")
            # THE GUARD NOW LIVES IN _issuance_views (2026-09-28), so the yield, the crossover
            # and the trajectory block together and on the same figures.
            block = _VIEW_BLOCKS.get((p["name"], spec["observed_metric"]))
            if block:
                return block
            decl = f"({rate}*{supply})"
            val = f"{decl}*{share}/{stake}"
            return calc(f"IF(AND(ISNUMBER({supply}),ISNUMBER({stake})),{val},{NA})")
        if spec["method"] == "consensus_plus_execution":
            cons, exe, legs = _eth_yield_parts(R, r, p, spec)
            return _coverage_guard(R, r, legs, f"{cons}+{exe}", same_span=False)   # D7-2: a SUM of two yields
        if spec["method"] == "rate_share":
            # Chainlink (2026-09-29): an emission RATE (tokens/yr) over stake — no annualising.
            rate, stake = R.D(r, spec["rate_metric"], "now"), R.D(r, spec["stake_metric"], "now")
            return calc(f"IF(AND(ISNUMBER({rate}),ISNUMBER({stake})),{rate}*{spec.get('share', 1.0)}/{stake},{NA})")
        if spec["method"] == "issuance_share":
            share = spec.get("share")
            if share is None:
                share = p
                for k in spec["share_path"]:
                    share = share[k]
            # Chainlink (B2): rewards paid from its vault over staked principal.
            im, sm = spec.get("issuance_metric", "gross_issuance_tokens"), spec.get("stake_metric", "locked_tokens")
            iss, stake = R.D(r, im, "q0"), R.D(r, sm, "now")
            return calc(f"IF(AND(ISNUMBER({iss}),ISNUMBER({stake})),{_annualise(R, r, p, im, iss)}*{share}/{stake},{NA})")
        return "pending — " + spec.get("why", "")[:80]
    return [
        ("VALIDATOR STAKING YIELD (annual) — securing the chain, NOT a protocol revenue share",
         vyield, FMT_PCT, "calc", True,
         {"metric_fn": lambda n: {"Ethereum": "beacon_chain_eth", "Near": "total_supply",
                                  "Chainlink": "reward_emission_rate_annual"}.get(n)}),
        *_total_yield_cols(R, vyield),
        ("Validator yield — caveat (a maturing rate)",
         lambda r, p: (config.VALIDATOR_YIELD.get(p["name"]) or {}).get("maturing", ""), FMT_TEXT, "text"),
        # WHERE THE HEADLINE IS NET OF COMMISSION (Plume, Jake 2026-10-05), the gross rate beside it.
        ("Validator yield GROSS of commission (where the headline is net)",
         lambda r, p: pull(R.D(r, (config.VALIDATOR_YIELD.get(p["name"]) or {})["gross_metric"], "now"))
         if (config.VALIDATOR_YIELD.get(p["name"]) or {}).get("gross_metric") else "", FMT_PCT, "pull", False,
         {"metric_fn": lambda n: (config.VALIDATOR_YIELD.get(n) or {}).get("gross_metric")}),
        *_eth_yield_columns(R),
        # SETTLEMENT VOLUME AND NRR (Jake, 2026-09-30): Artemis's daily settlement volume (DEX +
        # NFT trading + P2P transfers), summed over the 365 days ending on the export's LAST date,
        # and market cap on that same date over it. The date column is the flag: stale — AMBER —
        # after 45 days (config.ARTEMIS_SETTLEMENT).
        ("Settlement volume, trailing 365d ($) — Artemis: DEX + NFT + P2P transfers",
         lambda r, p: pull(R.D(r, "settlement_volume_365d_usd", "now")) if "settlement_volume_365d_usd"
         in config.metrics_for_project(p) else "", FMT_USD, "pull", False,
         {"metric": "settlement_volume_365d_usd", "trajectory_id": "a1_settlement_volume"}),
        # G11 B5 (Jake's 10-21 run: the label said "last date of the Artemis export" over 2026-09-19 while the CSV
        # ends 2026-08-25): the date is the NRR inputs' last day; where a declared rebuild extends the CSV, the cell
        # says where the CSV ends and the rebuild begins
        ("Settlement volume: last date of the NRR inputs (both end here) — the Artemis export, then any declared "
         "rebuild",
         lambda r, p: (f'=IF({R.D(r, "network_reserve_ratio", "latest_date")}="",'
                       f'"n/a — no settlement series stored (Artemis export not read for this project)",'
                       f'{R.D(r, "network_reserve_ratio", "latest_date")}{_settlement_end_text(p["name"])})')
         if "network_reserve_ratio" in config.metrics_for_project(p) else "", FMT_TEXT, "pull", False,
         {"metric": "network_reserve_ratio"}),
        ("NRR ON SETTLEMENT VOLUME — market cap ÷ trailing-365d settlement volume (Artemis definition: "
         "DEX + NFT + P2P), same end date",
         lambda r, p: pull(R.D(r, "network_reserve_ratio", "now")) if "network_reserve_ratio"
         in config.metrics_for_project(p) else "", FMT_PCT, "pull", True, {"metric": "network_reserve_ratio"}),
        _nrr_headroom_col(R, "network_reserve_ratio", "settlement", "a1_nrr_settlement_headroom"),
        # AN INCENTIVE PERIOD (Plume, Jake 2026-10-02): the column above reads the ORGANIC window; the
        # full-year figure, incentive period included, sits here — blank for every other project.
        ("NRR ON SETTLEMENT VOLUME — FULL YEAR, incentive-inflated (includes the incentive period; Plume: "
         "Points Season 2 farming to 2026-03-31)",
         lambda r, p: pull(R.D(r, "network_reserve_ratio_incl_incentive", "now")) if "network_reserve_ratio_incl_incentive"
         in config.metrics_for_project(p) else "", FMT_PCT, "pull", False, {"metric": "network_reserve_ratio_incl_incentive"}),
        # TRADING THROUGHPUT (Jake, 2026-09-30): DefiLlama DEX + perps by chain, one source for all
        # four chains. A DIFFERENT DEFINITION, so its own columns — never mixed with the above.
        ("Trading throughput, trailing 365d ($) — DefiLlama DEX + perps; NOT settlement volume",
         lambda r, p: pull(R.D(r, "trading_throughput_365d_usd", "now")) if "trading_throughput_365d_usd"
         in config.metrics_for_project(p) else "", FMT_USD, "pull", False,
         {"metric": "trading_throughput_365d_usd", "trajectory_id": "a1_throughput_volume"}),
        ("NRR ON TRADING THROUGHPUT — market cap ÷ trailing-365d DEX + perps volume (DefiLlama)",
         lambda r, p: pull(R.D(r, "network_reserve_ratio_throughput", "now")) if "network_reserve_ratio_throughput"
         in config.metrics_for_project(p) else "", FMT_PCT, "pull", True, {"metric": "network_reserve_ratio_throughput"}),
        _nrr_headroom_col(R, "network_reserve_ratio_throughput", "trading throughput", "a1_nrr_throughput_headroom"),
        ("Which NRR is meaningful here",
         lambda r, p: config.TRADING_THROUGHPUT["meaningful"].get(p["name"], "")
         if p["name"] in config.TRADING_THROUGHPUT["chains"] else "", FMT_TEXT, "text"),
    ]


def _settlement_end_text(name: str) -> str:
    """G11 B5: '&" — …"' naming where the Artemis CSV ends and a declared rebuild carries on (settlement_volume_usd rows
    whose source is the rebuild), or "" where the series is the CSV alone."""
    g = _LAST_GROUPS.get((name, config.ARTEMIS_SETTLEMENT["metric"]))
    if g is None or g.empty:
        return ""
    src = g["source"].astype(str)
    reb, csv = g[src.str.startswith("derived:settlement_rebuild")], g[~src.str.startswith("derived:settlement_rebuild")]
    if reb.empty:
        return ""
    end = pd.to_datetime(csv["date"]).max() if not csv.empty else None
    v = SETTLEMENT_VALIDATION.get(name) or {}
    return (f'&" — Artemis CSV to {end.date() if end is not None else "?"}, then the rebuild (DEX + NFT + P2P) to '
            f'{pd.to_datetime(reb["date"]).max().date()}{"; " + v["legs_end"] if v.get("legs_end") else ""}"')


def _token_yield(R: Refs, data_by_key: dict | None = None):
    """A3, Pendle (2026-09-29): the staking yield in TOKENS — PENDLE bought (annualised over the
    days it covers) / reward-bearing sPENDLE (lock + lock_add). Price-free, so a rising PENDLE
    does not understate it the way USD spend at buy-time over staked value at today's price does.

    TOKENS PRIMARY (Jake, 2026-09-30): every PROTOCOL_YIELD project gets this column, placed
    before the dollar yield. Where no token series is declared, what stakers are paid (dollars,
    as DefiLlama counts it) is put in tokens at the Q0 AVERAGE price — the price over the days it
    was paid — rather than at today's spot, which is what the dollar column divides by."""
    def build(r, p):
        spec = config.PROTOCOL_YIELD.get(p["name"]) or {}
        if not spec:
            why = config.PROTOCOL_YIELD_NOT_APPLICABLE.get(p["name"])
            return f"n/a — {why}" if why else ""
        ty = spec.get("token_yield")
        if ty and ty.get("payday"):               # payment-day prices (Jake's price convention, 2026-10-09)
            cell = R.D(r, ty["payday"]["metric"], "now")
            return calc(f"IF(ISNUMBER({cell}),{cell},{NA})")
        if ty and ty.get("epoch_mean"):           # the mean of the per-epoch APRs (Pendle, Jake's 5c decision 2026-10-09)
            cell = R.D(r, ty["epoch_mean"]["metric"], "now")
            return calc(f"IF(ISNUMBER({cell}),{cell},{NA})")
        base = R.D(r, spec["lock"], "now")
        if spec.get("lock_add"):
            add = R.D(r, spec["lock_add"], "now")
            base = f"({base}+IF(ISNUMBER({add}),{add},0))"
        if not ty:
            rev, pq0 = R.D(r, spec["revenue"], "q0"), R.D(r, "price_usd", "q0")
            tok = f"({rev}/{pq0})"
            return calc(f"IF(AND(ISNUMBER({rev}),ISNUMBER({pq0}),ISNUMBER({base})),"
                        f"{_annualise(R, r, p, spec['revenue'], tok)}/{base},{NA})")
        # THE TRAILING-YEAR HEADLINE (Ether.fi, Jake 2026-10-07): reward tokens over the last `trailing_days` / the
        # AVERAGE staked over them, computed at read time (_trailing_yield_views).
        if ty.get("trailing_days"):
            # THE ACCOUNTANT'S RATE GROWTH where declared (Ether.fi, Jake's probes16) — the token count is its reference
            ty_cell = R.D(r, "token_yield_accountant_trailing_pct" if ty.get("accountant_rate_metric")
                          else "token_yield_trailing_pct", "now")
            return calc(f"IF(ISNUMBER({ty_cell}),{ty_cell},{NA})")
        tok = R.D(r, ty["tokens"], "q0")
        # PER EPOCH (Pendle, 2026-09-29): the mean distribution of the epochs in Q0 x epochs/yr —
        # a 90-day window holds 6 or 7 fortnightly epochs, and x365/90 would swing by one.
        if ty.get("epoch_days"):
            k = R.D(r, ty["tokens"], "q0_events")
            annual = f"({tok}/{k})*365.25/{ty['epoch_days']}"
            return calc(f"IF(AND(ISNUMBER({tok}),ISNUMBER({k}),{k}>0,ISNUMBER({base})),{annual}/{base},{NA})")
        return calc(f"IF(AND(ISNUMBER({tok}),ISNUMBER({base})),{_annualise(R, r, p, ty['tokens'], tok)}/{base},{NA})")

    def note(p):
        ty = ((config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {})
        if ty.get("note"):                                   # a project's own wording (Ether.fi, 2026-10-06)
            # ... with the programme's silence flag kept beside it (SILENT / last inflow), never dropped.
            flag = _program_flag(p, data_by_key or {})
            return ((flag[0] + " · tokens", ty["note"] + "\n\n" + flag[1]) if flag
                    else (" · tokens", ty["note"]))
        own = ((" · tokens", f"TOKEN YIELD — PENDLE DISTRIBUTED per epoch (Pendle's "
                             f"sPendleHistoricalData), mean over the Q0 epochs x 365.25/"
                             f"{ty.get('epoch_days', '?')}, over real + virtual sPENDLE. Airdrops (in "
                             f"kind) excluded. Before 2026-09-29: {ty.get('was', 'buyback tokens')}. "
                             f"Cross-check: {ty['cross_check']}.")
               if ty else None)
        return own or _protocol_yield_flag(p, data_by_key or {})
    return ("PROTOCOL STAKING YIELD (tokens) — primary: tokens paid to stakers per year ÷ reward-bearing stake "
            "(real + virtual); dollars paid are converted at each PAYMENT DAY's price (Aerodrome, Sky — Jake's price "
            "convention 2026-10-09) over the mean stake in the same window",
            build, FMT_PCT, "calc", True,
            {"metric_fn": lambda n: (((config.PROTOCOL_YIELD.get(n) or {}).get("token_yield") or {}).get("tokens")
                                     or (config.PROTOCOL_YIELD.get(n) or {}).get("revenue")),
             "partial_fn": lambda p: (config.lock_partial_reason(p["name"], config.PROTOCOL_YIELD[p["name"]]["lock"])
                                      or _virtual_missing(p["name"], data_by_key or {})
                                      if p["name"] in config.PROTOCOL_YIELD else None),
             "partial_direction": "the locked value is incomplete, so the yield READS HIGH.",
             "partial_fmt": '0.0%" PARTIAL↑";(0.0%)" PARTIAL↑"',
             "flag_fn": note})


def _topup_split(R: Refs):
    """A3, Ether.fi (Jake, 2026-10-06 16:33): the token yield's rewards, split "of which BOUGHT" = ETHFI bought on
    DEXs over Q0 (token_yield `bought`) ÷ the Q0 top-ups, capped at 100%. ETHFI is fungible, so purchases are
    ALLOCATED first; the remainder was transferred in (treasury / foundation). Blank where nothing is declared."""
    def build(r, p):
        ty = ((config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {})
        if not ty.get("bought"):
            return ""
        t, b = R.D(r, ty["tokens"], "q0"), R.D(r, ty["bought"], "q0")
        return calc(f"IF(AND(ISNUMBER({t}),ISNUMBER({b}),{t}>0),IF({b}<{t},{b},{t})/{t},{NA})")

    def note(p):
        ty = ((config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {})
        if not ty.get("bought"):
            return None
        return (" · allocated", "OF WHICH BOUGHT — ETHFI bought on DEXs in Q0 ÷ ETHFI topped up in Q0, capped at "
                                "100%. Purchases are allocated first (fungible tokens cannot be traced); 1 − this "
                                "share was TRANSFERRED in (treasury / foundation), not bought.")
    return ("  of which BOUGHT on-chain (DEX fills; Q0, purchases allocated first) — the rest transferred in",
            build, FMT_PCT, "calc", False,
            {"metric_fn": lambda n: ((config.PROTOCOL_YIELD.get(n) or {}).get("token_yield") or {}).get("bought"),
             "flag_fn": note})


def _topup_split_alltime(R: Refs):
    """A3, Ether.fi (Jake, 2026-10-06 17:20): "of which bought on-chain" over the FULL history — the top-up Safe's
    all-time ETHFI inflows that were DEX fills (~6% since 2024-07), stored by the buyback scan each run."""
    def build(r, p):
        ty = ((config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {})
        m = ty.get("bought_share_alltime")
        return pull(R.D(r, m, "now")) if m else ""
    return ("  of which BOUGHT on-chain — FULL HISTORY (the top-up Safe's ETHFI inflows since 2024-07)",
            build, FMT_PCT, "pull", False,
            {"metric_fn": lambda n: ((config.PROTOCOL_YIELD.get(n) or {}).get("token_yield") or {}).get("bought_share_alltime")})


def _published_apr(R: Refs):
    """A3, Pendle (2026-09-29): the protocol's OWN APR (lastEpochApr), beside our token yield —
    a cross-check, never the headline."""
    def build(r, p):
        ty = ((config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {})
        m = ty.get("published_apr_metric")
        return pull(R.D(r, m, "now")) if m else ""
    return ("Staking APR as the protocol publishes it (Pendle lastEpochApr) — cross-check",
            build, FMT_PCT, "pull", False,
            {"metric_fn": lambda n: ((config.PROTOCOL_YIELD.get(n) or {}).get("token_yield") or {})
             .get("published_apr_metric")})


def _virtual_share(R: Refs):
    """A3, Pendle (2026-09-29): the share of staker distributions going to VIRTUAL balances —
    lock_add ÷ (lock + lock_add). Beside the yield so the split is read, not buried in the
    denominator; the boost's decay date rides on the cell. Blank where nothing is shared."""
    def build(r, p):
        spec = config.PROTOCOL_YIELD.get(p["name"]) or {}
        if not spec.get("lock_add"):
            return ""
        real, add = R.D(r, spec["lock"], "now"), R.D(r, spec["lock_add"], "now")
        return calc(f"IF(AND(ISNUMBER({real}),ISNUMBER({add}),({real}+{add})>0),{add}/({real}+{add}),{NA})")

    def decay(p):
        d = (config.PROTOCOL_YIELD.get(p["name"]) or {}).get("lock_add_decay")
        if not d:
            return None
        return (f" · decays by {d['ends_by']}",
                f"VIRTUAL SHARE — {d['what']} by {d['ends_by']}; the share should fall toward 0 as "
                f"they do. Source: {d['source']}.")
    return ("Share of staker distributions to VIRTUAL balances (legacy boosts) = virtual ÷ (real + virtual)",
            build, FMT_PCT, "calc", False,
            {"metric": "locked_tokens_virtual", "flag_fn": decay})


def _virtual_missing(name: str, data_by_key: dict) -> str | None:
    """Why the protocol yield reads high: a declared lock_add that has no stored value yet."""
    add = (config.PROTOCOL_YIELD.get(name) or {}).get("lock_add")
    if not add:
        return None
    now = (data_by_key.get(f"{name}|{add}") or {}).get("now")
    if isinstance(now, (int, float)) and now == now:
        return None
    return (f"{add} is not read yet, so the denominator is real staked alone — rewards are shared "
            f"over real + virtual balances")


def _restatement_views(groups: dict) -> None:
    """A column that IS another column, rebuilt from it at READ TIME. Added 2026-09-28.

    fetch._restate_metrics writes the copy when a run fetches the source column, so a --no-fetch
    rebuild — or a run where the copy was added after the source was stored — showed nothing:
    Pendle's holders_revenue_usd was stored (Q0 $1,373,516) and actual_buyback_usd was blank.
    Built here from whatever the source column holds, honouring from_date. A column with any row
    from another source is a measurement and is left alone.
    """
    for p in scoped_projects():
        name = p["name"]
        for metric, spec in config.metric_restatements(name).items():
            src = groups.get((name, spec["equals"]))
            if src is None or src.empty:
                continue
            restated = f"derived:={spec['equals']}"
            held = groups.get((name, metric))
            if held is not None and not held.empty and (held["source"].astype(str) != restated).any():
                continue
            view = src.copy()
            if spec.get("source_prefix"):
                view = view[view["source"].astype(str).str.startswith(spec["source_prefix"])]
            if spec.get("from_date"):
                view = view[view["date"] >= pd.Timestamp(spec["from_date"])]
            if view.empty:
                continue
            view["metric"], view["source"] = metric, restated
            groups[(name, metric)] = _as_stored(view, src.columns)


def _buyback_tokens_from_usd_views(groups: dict) -> None:
    """actual_buyback_tokens := actual_buyback_usd / price_usd on the SAME date. 2026-09-28.

    Pendle (config.buyback_tokens_from_usd): the usd column is itself a read-time restatement of
    holders_revenue_usd, so this runs after _restatement_views. A day with no same-day price is
    left out — valuing it at the latest price would invent a token count. A column with any row
    from another source is a measurement and is left alone.
    """
    for p in scoped_projects():
        name = p["name"]
        if not config.buyback_tokens_from_usd(name):
            continue
        usd, px = groups.get((name, "actual_buyback_usd")), groups.get((name, "price_usd"))
        if usd is None or usd.empty or px is None or px.empty:
            continue
        held = groups.get((name, "actual_buyback_tokens"))
        if held is not None and not held.empty and (held["source"].astype(str) != DERIVED_USD_OVER_PRICE).any():
            continue
        price_on = px.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        price_on = price_on[price_on > 0]
        tok = usd[usd["date"].isin(price_on.index)].copy()
        if tok.empty:
            continue
        tok["value"] = tok["value"].astype(float) / tok["date"].map(price_on)
        tok["metric"], tok["source"] = "actual_buyback_tokens", DERIVED_USD_OVER_PRICE
        groups[(name, "actual_buyback_tokens")] = _as_stored(tok, usd.columns)


def _measured_buyback_views(groups: dict) -> None:
    """THE BUYBACK FROM THE PROJECT'S OWN PER-EPOCH FIGURE (G11 B9, Jake's 10-21 run — config.MEASURED_BUYBACK; Pendle:
    spendle/data buybackAmounts). Each epoch dated d pays out days d..d+epoch_days−1, so its amount is spread evenly
    over them — never past the store's last priced day. The LATEST epoch reading 0 is Pendle's publish lag (the amount
    is not out yet), not a zero: left out and said. actual_buyback_usd = tokens x the same-day price. The derived series
    (holders revenue / same-day price) moves to <metric>_derived and stays the cross-check."""
    for name, spec in config.MEASURED_BUYBACK.items():
        if name not in {p["name"] for p in scoped_projects()}:
            continue
        ep, px = groups.get((name, spec["metric"])), groups.get((name, "price_usd"))
        for m in ("actual_buyback_tokens", "actual_buyback_usd"):
            g = groups.get((name, m))
            if g is not None and not g.empty:
                groups[(name, f"{m}_derived")] = _as_stored(g.assign(metric=f"{m}_derived"), g.columns)
        if ep is None or ep.empty:
            continue
        e = (ep.assign(date=pd.to_datetime(ep["date"]).dt.normalize()).drop_duplicates("date", keep="last")
             .sort_values("date"))
        lag = ""
        if float(e["value"].iloc[-1]) == 0.0:
            lag = (f"; the epoch of {e['date'].iloc[-1].date()} reads 0 — Pendle's publish lag, not a zero: left out "
                   f"until its amount is published")
            e = e.iloc[:-1]
        if e.empty:
            continue
        end = pd.to_datetime(px["date"]).max() if px is not None and not px.empty else None
        n = int(spec["epoch_days"])
        rows = []
        for d, v in zip(e["date"], e["value"].astype(float)):
            for day in pd.date_range(d, periods=n, freq="D"):
                if end is None or day <= end:
                    rows.append((day, v / n))
        if not rows:
            continue
        src = (f"{e['source'].iloc[-1]}[{spec['label']}: each epoch spread over its {n} days{lag}; cross-check "
               f"actual_buyback_tokens_derived (holders revenue / same-day price)]")[:500]
        tok = pd.DataFrame({"date": [r[0] for r in rows], "project": name, "metric": "actual_buyback_tokens",
                            "value": [r[1] for r in rows], "tier": 2, "source": src})
        groups[(name, "actual_buyback_tokens")] = _as_stored(tok, ep.columns)
        if px is None or px.empty:
            continue
        price_on = px.assign(date=pd.to_datetime(px["date"]).dt.normalize()).drop_duplicates("date", keep="last") \
            .set_index("date")["value"].astype(float)
        usd = tok[tok["date"].isin(price_on.index[price_on > 0])].copy()
        usd["value"] = usd["value"].astype(float).values * price_on.loc[usd["date"]].values
        usd["metric"], usd["source"] = "actual_buyback_usd", "derived:tokens*price"
        if not usd.empty:
            groups[(name, "actual_buyback_usd")] = _as_stored(usd, ep.columns)
        # lastEpochApr read 0 (Pendle's publish lag, the same as the epoch's 0): a 0 is no APR — the last COMPLETE
        # epoch's published APR stands in where stored, else the row is left out and the reason said
        am, ea = spec.get("apr_metric"), groups.get((name, spec.get("epoch_apr_metric") or ""))
        ag = groups.get((name, am)) if am else None
        if ag is not None and not ag.empty and (ag["value"].astype(float) == 0).any():
            zero = ag["value"].astype(float) == 0
            if ea is not None and not ea.empty:
                es = ea.assign(date=pd.to_datetime(ea["date"]).dt.normalize()).drop_duplicates("date", keep="last") \
                    .set_index("date")["value"].astype(float).sort_index()
                fill = ag[zero].copy()
                prev = [es[es.index <= pd.Timestamp(d)] for d in fill["date"]]
                keep = [len(x) > 0 for x in prev]
                fill = fill[keep]
                fill["value"] = [float(x.iloc[-1]) for x in prev if len(x)]
                fill["source"] = [f"{spec['epoch_apr_metric']}[lastEpochApr read 0 (publish lag): the last complete "
                                  f"epoch's APR, {x.index[-1].date()}]" for x in prev if len(x)]
                ag = pd.concat([ag[~zero], fill], ignore_index=True).sort_values("date")
            else:
                ag = ag[~zero]
            if ag.empty:
                _EARLY_BLOCKS[(name, am)] = ("BLOCKED — lastEpochApr read 0: Pendle's publish lag, not a 0% "
                                             "APR; no complete epoch's APR (pendle_epoch_apr_published) stored")
                groups.pop((name, am), None)
            else:
                groups[(name, am)] = _as_stored(ag, ag.columns)


def _buyback_split_views(groups: dict) -> None:
    """THE BUYBACK BY DESTINATION (Jake's decision 2026-10-10): each day's actual_buyback_tokens x that day's shares
    (config.buyback_destination_shares — dated, sourced) -> buyback_burned_tokens / _held_ / _distributed_, and
    retired_buyback_tokens / _usd = burned + held. Retirement = burned or held; paid out to stakers is staking yield.
    The full buyback stays on its own row. A day with no declared share is left out of the split and named."""
    for p in scoped_projects():
        name = p["name"]
        tok = groups.get((name, "actual_buyback_tokens"))
        if tok is None or tok.empty or config.buyback_route(name)["route"] == "none":
            continue
        cols = tok.columns
        dates = pd.to_datetime(tok["date"]).dt.normalize()
        shares = [config.buyback_destination_shares(name, d.date()) for d in dates]
        keep = [sh is not None for sh in shares]
        if not any(keep):
            continue
        base = tok[keep].copy()
        sh = [x for x in shares if x is not None]
        v = base["value"].astype(float).values
        tags = sorted({(x["from"] or "start", round(x["burn"], 4), round(x["hold"], 4), round(x["distribute"], 4))
                       for x in sh})
        how = "; ".join(f"from {f}: burn {b:.0%} / hold {h:.0%} / distribute {d:.0%}" for f, b, h, d in tags)
        for metric, parts in (("buyback_burned_tokens", ("burn",)), ("buyback_held_tokens", ("hold",)),
                              ("buyback_distributed_tokens", ("distribute",)),
                              ("retired_buyback_tokens", ("burn", "hold"))):
            f = base.copy()
            f["value"] = v * [sum(x[k] for k in parts) for x in sh]
            f["metric"], f["source"] = metric, f"derived:actual_buyback_tokens x {'+'.join(parts)} share ({how})"
            groups[(name, metric)] = _as_stored(f, cols)
        usd = groups.get((name, "actual_buyback_usd"))
        if usd is not None and not usd.empty:
            ud = pd.to_datetime(usd["date"]).dt.normalize()
            ush = [config.buyback_destination_shares(name, d.date()) for d in ud]
            uk = [x is not None for x in ush]
            if any(uk):
                f = usd[uk].copy()
                f["value"] = f["value"].astype(float).values * [x["burn"] + x["hold"] for x in ush if x is not None]
                f["metric"], f["source"] = "retired_buyback_usd", f"derived:actual_buyback_usd x burn+hold share ({how})"
                groups[(name, "retired_buyback_usd")] = _as_stored(f, usd.columns)


def _wallet_set(source: str) -> str:
    """The components a summed balance was read from — "chain:sum(a+b+c)" -> "a+b+c" (sorted) — with PARTIAL kept as a
    marker; any other source is its own key."""
    m = re.search(r"sum\(([^)]*)\)", source or "")
    key = "+".join(sorted(m.group(1).split("+"))) if m else (source or "")
    return key + ("|PARTIAL" if "PARTIAL" in (source or "") else "")


def _onchain_release_views(groups: dict) -> None:
    """pool_release_tokens := −d(the named non-circulating balances), day over consecutive stored days (Chainlink, Jake's
    sign-off round 2026-10-07: the on-chain set is primary, so the release is what its wallets actually let out). The
    CoinGecko-derived series it replaces stays as pool_release_tokens_coingecko — its circulating moves in steps, so
    it is the cross-check only. Declared by CIRCULATING_ONCHAIN[name]["release_from"]."""
    for name, spec in config.CIRCULATING_ONCHAIN.items():
        legs = spec.get("release_from")
        if not legs or (name, legs[0]) not in groups:
            continue
        held = groups.get((name, "pool_release_tokens"))
        series, sets = [], []
        for m in legs:
            g = groups.get((name, m))
            if g is None or g.empty:
                series = None
                break
            g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            series.append(g.set_index("date")["value"].astype(float))
            sets.append(g.set_index("date")["source"].astype(str).map(_wallet_set))
        if not series:
            continue
        bal = pd.concat(series, axis=1).dropna().sum(axis=1).sort_index()
        # THE SAME WALLETS ON BOTH DAYS, OR NO RELEASE THAT DAY (Jake's run 2026-10-09 ~10:15, Chainlink: our release
        # read −400,000 over Q0 while the 27 wallets held 251,900,000.10 at both ends with no transfer). A summed
        # balance's source names its components (chain:sum(a+b+...)); a day whose set differs from the day before —
        # wallets added to config — re-bases the sum and is NOT a release, and a PARTIAL day (a component refused) is
        # not a whole sum. Those steps are left out, never differenced.
        key = pd.concat(sets, axis=1).reindex(bal.index).astype(str).agg("|".join, axis=1)
        prev = bal.shift(1)
        step = ((bal.index.to_series().diff().dt.days == 1) & (key == key.shift(1))
                & ~key.str.contains("PARTIAL", regex=False))
        # a day the wallets RECEIVED is not a negative release (item 2 of the accuracy pass): clipped at 0
        rel = (prev - bal)[step].dropna().clip(lower=0.0)
        if rel.empty:
            continue
        cols = held.columns if held is not None and not held.empty else groups[(name, legs[0])].columns
        if held is not None and not held.empty:
            groups[(name, "pool_release_tokens_coingecko")] = _as_stored(
                held.assign(metric="pool_release_tokens_coingecko"), held.columns)
        groups[(name, "pool_release_tokens")] = _as_stored(pd.DataFrame(
            {"date": rel.index, "project": name, "metric": "pool_release_tokens", "value": rel.values,
             "source": f"derived:-d({'+'.join(legs)})", "tier": 2}), cols)
    # FIRST-PARTY RELEASE (Hyperliquid, Jake's sign-off round 2026-10-07): d(the project's own circulating) + the
    # named flows that took tokens back out of it the same day (the Assistance Fund buyback)
    for name, spec in config.CIRCULATING_ONCHAIN.items():
        fp = spec.get("release_first_party")
        if not fp or (name, fp["stock"]) not in groups:
            continue
        g = groups[(name, fp["stock"])]
        if g is None or g.empty:
            continue
        gd = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
              .set_index("date").sort_index())
        stock = gd["value"].astype(float)
        # NEVER ACROSS A CHANGE OF BASIS (Jake's accuracy pass on 1e702bb, item 2: Hyperliquid's Q0 release read
        # −59,682,296 — the 2026-10-11 handover of circulating_supply_first_party to "net of the Foundation and Grants
        # wallets" (−63.5M) was differenced as a release). A day whose reading comes from another measuring point
        # than the day before re-bases the stock and is left out, as the wallet-set rule above does.
        from fetch.base import _measuring_point          # noqa: PLC0415
        mp = gd["source"].astype(str).map(_measuring_point)
        step = (stock.index.to_series().diff().dt.days == 1) & (mp == mp.shift(1))
        rel = (stock - stock.shift(1))[step].dropna()
        for m in fp.get("plus") or ():
            f = groups.get((name, m))
            if f is None or f.empty:
                fs = pd.Series(0.0, index=rel.index)
            else:
                fs = (f.assign(date=pd.to_datetime(f["date"]).dt.normalize()).groupby("date")["value"].sum()
                      .astype(float))
            rel = rel.add(fs.reindex(rel.index).fillna(0.0))
        # A RELEASE IS NEVER NEGATIVE (item 2): a day the circulating figure fell net of the buyback is not a negative
        # release — clipped at 0, as the unlock releases are (_unlock_release_view)
        rel = rel.clip(lower=0.0)
        if rel.empty:
            continue
        held = groups.get((name, "pool_release_tokens"))
        if held is not None and not held.empty:
            groups[(name, "pool_release_tokens_coingecko")] = _as_stored(
                held.assign(metric="pool_release_tokens_coingecko"), held.columns)
        groups[(name, "pool_release_tokens")] = _as_stored(pd.DataFrame(
            {"date": rel.index, "project": name, "metric": "pool_release_tokens", "value": rel.values,
             "source": f"derived:d({fp['stock']})+{'+'.join(fp.get('plus') or ())}", "tier": 2}), g.columns)


def _release_floor_views(groups: dict) -> None:
    """A RELEASE IS NEVER NEGATIVE, WHATEVER STORED IT (alignment round 2026-10-13, D2): Maple's own page reports the
    Syrup Strategic Fund's monthly release, and two months read negative (2025-11-30 −3,052,593; 2026-07-31 −971,641 —
    the fund RECEIVED). An inflow to an issuer wallet is not a negative release (item 2 of the accuracy pass): read as 0,
    the source saying so; the stored row is untouched."""
    for (name, metric), g in list(groups.items()):
        if metric != "pool_release_tokens" or g is None or g.empty:
            continue
        neg = g["value"].astype(float) < 0
        if not neg.any():
            continue
        g = g.copy()
        g.loc[neg, "source"] = g.loc[neg, "source"].astype(str) + "[negative: the wallet received -> 0]"
        g.loc[neg, "value"] = 0.0
        groups[(name, metric)] = g


_ISSUANCE_VIEW_METRICS = ("gross_issuance_tokens",)


def _emissions_from_metric_views(groups: dict, after_issuance: bool = False) -> None:
    """emissions_tokens := the MEASURED release named by config `emissions_from_metric` (Sky, Jake's run
    2026-10-07: the declared vest streams read 73.2M over Q0 where SKY actually released to the farm was 189.1M —
    the declared steps missed two earlier streams). The measured rows replace the column; rows the schedule wrote
    into emissions_tokens before 2026-10-07 are not shown (orphan_cleanup.sql section CE lists them). No measured
    rows yet: the column is empty, never the old partial schedule.

    gross_issuance_tokens (Pendle, Jake's run 2026-10-09 ~10:15, 5c) is built by _issuance_views, so it is copied on
    the second call (after_issuance=True), from the view, with its block if the view is blocked."""
    for p in scoped_projects():
        src_metric = p.get("emissions_from_metric")
        if not src_metric or (src_metric in _ISSUANCE_VIEW_METRICS) != after_issuance:
            continue
        name = p["name"]
        if after_issuance and (name, src_metric) in _VIEW_BLOCKS:
            _VIEW_BLOCKS[(name, "emissions_tokens")] = _VIEW_BLOCKS[(name, src_metric)]
        if isinstance(src_metric, (tuple, list)):
            # SEVERAL SCANNED STREAMS, SUMMED BY DAY (Pendle, Jake's run 2026-10-09 11:41: the gauge payouts of every
            # chain read). Each scan zero-fills from its first activity, so a day before a stream's first row is an
            # observed 0; a day after a stream's LAST row is not yet scanned, so the sum stops at the earliest last row.
            parts = {m: groups.get((name, m)) for m in src_metric}
            got = {m: g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
                   for m, g in parts.items() if g is not None and not g.empty}
            missing = [m for m in src_metric if m not in got]
            if not got and (name, "emissions_tokens") not in groups:
                continue
            if missing:
                # EVERY LISTED STREAM OR NO COLUMN (Jake's run 2026-10-09 ~14:10: the sum read mainnet only, 77,160, while
                # Arbitrum paid 6,610.56 — its scan was still seeding). A stream that has stored nothing is not a 0 — and
                # rows still stored under emissions_tokens itself (Morpho's Ethereum claims before run_sql CT renames
                # them, G2-D1) are one leg, not the sum, so they are not shown either.
                _VIEW_BLOCKS[(name, "emissions_tokens")] = _EARLY_BLOCKS[(name, "emissions_tokens")] = (
                    f"BLOCKED — the sum of {', '.join(src_metric)} is not formed: {', '.join(missing)} "
                    f"stored nothing yet (a log scan stores only once its history, or its 100-day window, reconciles). "
                    + ("`python token_metrics.py --seed pendle_gauges` finishes the seed in one sitting" if name == "Pendle"
                       else "The next token_metrics.py run reads them (config log_scans / EXPLORER_PROBED_SCANS)"))
                groups.pop((name, "emissions_tokens"), None)
                continue
            # A HAND-ENTERED STREAM DOES NOT BOUND THE SUM (Pendle Optimism, Jake 2026-10-09 17:05: "paid nothing in
            # Q0", one manual 0 with its evidence): its reading covers the days to the build, so only scanned streams
            # set the last day summed
            scanned = [m for m in got if not parts[m]["source"].astype(str).eq("manual").all()]
            end = min(got[m].index.max() for m in (scanned or list(got)))
            # A WINDOW-ONLY STREAM BOUNDS THE START (G2-D1, Morpho Katana): an rpc_window scan has no full history, so a
            # day before its first stored row was never read — not an observed 0. The sum starts at that row.
            window_only = {sp.get("metric") for sp in p.get("log_scans") or () if sp.get("rpc_window")}
            start = max((got[m].index.min() for m in got if m in window_only), default=None)
            idx = sorted(set().union(*(s.index for s in got.values())))
            total = pd.concat([s.reindex(idx).fillna(0.0) for s in got.values()], axis=1).sum(axis=1)
            total = total[total.index <= end]
            if start is not None:
                total = total[total.index >= start]
            base = next(iter(parts[m] for m in got))
            groups[(name, "emissions_tokens")] = _as_stored(pd.DataFrame(
                {"date": total.index, "project": name, "metric": "emissions_tokens", "value": total.values,
                 "source": f"derived:sum({'+'.join(got)})", "tier": 2}), base.columns)
            continue
        measured = groups.get((name, src_metric))
        held = groups.get((name, "emissions_tokens"))
        if measured is not None and not measured.empty:
            groups[(name, "emissions_tokens")] = _as_stored(measured.assign(metric="emissions_tokens"),
                                                            measured.columns)
        elif held is not None and not held.empty:
            groups[(name, "emissions_tokens")] = held[~held["source"].astype(str).str.startswith("schedule")]


def _quarterly_views(fn, groups: dict, asof: pd.Timestamp) -> None:
    """Run a one-row-at-asof view builder at EACH quarter end — asof − k x period_days, k = 4..0 — on the store cut at
    that day, and keep every row it writes (Round G7 item 2c, Jake 2026-10-19: rows whose Q0 has a value but whose shifted
    quarters return none — the protocol-yield views wrote one row, at asof, so Q-1..Q-4 found nothing). The builder is
    the same one Q0 uses, run on what was stored by that quarter's end: Q-k is built the way Q0 is. Q0's row is the one
    the builder writes for asof itself, unchanged."""
    period = int(GLOBALS["period_days"])
    before = {k: id(v) for k, v in groups.items()}
    made: dict = {}
    made_k: dict = {}
    for k in range(len(QUARTERS) - 1, -1, -1):
        a_k = pd.Timestamp(asof) - pd.Timedelta(days=period * k)
        if k == 0:
            sub = groups
        else:
            # only the projects these views read (config.PROTOCOL_YIELD) — the others would be copied for nothing
            sub = {key: (g[pd.to_datetime(g["date"]) <= a_k] if g is not None and not g.empty and "date" in g else g)
                   for key, g in groups.items() if key[0] in config.PROTOCOL_YIELD}
        ids = {key: id(v) for key, v in sub.items()}
        fn(sub, a_k)
        for key, v in sub.items():
            if (ids.get(key) != id(v)) and v is not None and not v.empty:
                made.setdefault(key, []).append(v)
                made_k.setdefault(key, []).append(k)
    for key, frames in made.items():
        if before.get(key) == id(groups.get(key)) and key in before and len(frames) == 1:
            continue
        # ONE MEASURING POINT ACROSS THE QUARTERS: a builder's source text carries that quarter's own figures (rates,
        # windows), which the measuring-point checks would read as a new source each quarter. A shifted quarter's rows
        # take Q0's source with their own text as a bracketed annotation (stripped by config.strip_source_annotations).
        q0 = next((f for f, k in zip(frames, made_k[key]) if k == 0), None)
        if q0 is not None and "source" in q0 and len(q0):
            base = str(q0["source"].iloc[-1])
            frames = [f if k == 0 else f.assign(source=[
                f"{base}[as at {pd.Timestamp(d):%Y-%m-%d}: {str(s_).replace('[', '(').replace(']', ')')}]"
                for d, s_ in zip(f["date"], f["source"])]) for f, k in zip(frames, made_k[key])]
        joined = pd.concat(frames, ignore_index=True)
        joined = joined.assign(_d=pd.to_datetime(joined["date"]).dt.normalize()).sort_values("_d") \
            .drop_duplicates("_d", keep="last").drop(columns="_d")
        groups[key] = joined


def _payday_yield_views(groups: dict, asof: pd.Timestamp) -> None:
    """THE PROTOCOL YIELD AT PAYMENT-DAY PRICES (Jake's price convention, 2026-10-09 — Aerodrome and Sky): token_yield
    `payday` = {flow ($ paid), price, stock (the stake in tokens), days, metric, year}: each day's $ / that day's price,
    summed over the last `days` days to asof, x year / the days counted, over the MEAN stake stored in the same window —
    credibility.payday_yield, the arithmetic the Credibility rows reproduce. The dollar column on the same basis is the
    same number (the price cancels), so both columns read this row. One row, at asof."""
    import credibility as cred                       # noqa: PLC0415
    for p in scoped_projects():
        pd_ = ((config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {}).get("payday")
        if not pd_:
            continue
        name = p["name"]
        s = lambda m: (lambda g: None if g is None or g.empty else g.assign(  # noqa: E731
            date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            .set_index("date")["value"].astype(float).sort_index())(groups.get((name, m)))
        lo = asof - pd.Timedelta(days=int(pd_["days"]))
        # THE ILLIQUID-TOKEN RULE (Jake 2026-10-09 17:05): the flow less what a sale could not have realised, as a
        # labelled line (rewards_illiquid_excluded_usd) — never dropped silently
        il = pd_.get("illiquid") or {}
        r = cred.payday_headline(s(pd_["flow"]), s(pd_["price"]), s(pd_["stock"]), lo, asof,
                                 float(pd_.get("year", 365)), pd_.get("stake_epoch_days"),
                                 s(il["fees"]) if il else None, s(il["bribes"]) if il else None, bool(il))
        if r is None:
            continue
        if il and r.get("took") is not None:
            groups[(name, il["metric"])] = _as_stored(pd.DataFrame(
                {"date": [r["last"]], "project": [name], "metric": [il["metric"]],
                 "value": [r["took"]["fees"] + r["took"]["bribes"]],
                 "source": [f"derived:{il['fees']} + {il['bribes']} over the {pd_['days']}d window (fees on the week "
                            f"before each epoch, bribes on its own days)"], "tier": [2]}), groups[(name, pd_["flow"])].columns)
        over = "each epoch's" if pd_.get("stake_epoch_days") else "mean"
        groups[(name, pd_["metric"])] = _as_stored(pd.DataFrame(
            {"date": [r["last"]], "project": [name], "metric": [pd_["metric"]], "value": [r["value"]],
             "source": [f"derived:{pd_['flow']} / same-day {pd_['price']}, {pd_['days']}d, / {over} {pd_['stock']} — "
                        f"{r['how']}"], "tier": [2]}), groups[(name, pd_["flow"])].columns)
        if pd_.get("annual_metric"):                  # the tokens a year, for a column on another denominator
            groups[(name, pd_["annual_metric"])] = _as_stored(pd.DataFrame(
                {"date": [r["last"]], "project": [name], "metric": [pd_["annual_metric"]],
                 "value": [r["tokens"] * float(pd_.get("year", 365)) / r["days"]],
                 "source": [f"derived:{pd_['flow']} / same-day {pd_['price']}, {r['days']} day(s), annualised"],
                 "tier": [2]}), groups[(name, pd_["flow"])].columns)


def _epoch_mean_views(groups: dict, asof: pd.Timestamp) -> None:
    """THE PENDLE HEADLINE = THE MEAN OF THE PER-EPOCH APRs, EACH OVER ITS OWN STAKE (Jake's 5c decision, 2026-10-09):
    ours where our stake exists at the epoch (from ~2026-09-11, when virtual sPENDLE history starts), Pendle's published
    APR for that epoch before it (first-party; it embeds the epoch's own stake). Not mean distribution / today's stake.
    credibility.epoch_apr_table is the one computation; the source text names each epoch's source. No row when a Q0
    epoch has neither — the mean is never formed over a subset."""
    import credibility as cred                       # noqa: PLC0415
    for p in scoped_projects():
        ty = (config.PROTOCOL_YIELD.get(p["name"]) or {}).get("token_yield") or {}
        em = ty.get("epoch_mean")
        if not em:
            continue
        name = p["name"]
        s = lambda m: (lambda g: None if g is None or g.empty else g.assign(  # noqa: E731
            date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
            .set_index("date")["value"].astype(float).sort_index())(groups.get((name, m)))
        stakes = {m: s(m) for m in (em["stock"], *em["plus"])}
        lag, _ = cred.epoch_publish_lag(name, ty["tokens"], int(ty["epoch_days"]))
        lo, hi = cred._q0(asof)
        # BEFORE THE API'S HISTORY: the calibrated on-chain rebuild of virtual sPENDLE (Jake's 1c decision, 2026-10-09)
        fb, cs = None, em.get("calibrate")
        if cs:
            # THE ONE GUARDED COMPUTATION (credibility.pendle_calibration: read times from fetched_at, the PENDLE-locked
            # bound — Jake's run 2026-10-09 ~14:10), on the same rows
            parts = [groups.get((name, cs[k])) for k in ("ve", "api", "legacy") if cs.get(k)]
            parts = [g for g in parts if g is not None and not g.empty]
            cal = cred.pendle_calibration(name, pd.concat(parts, ignore_index=True).assign(
                date=lambda d: pd.to_datetime(d["date"]).dt.normalize()) if parts else None)
            if cal:
                fb = {cs["api"]: (cal["calibrated"], cal["label"])}
        tab = cred.epoch_apr_table(s(ty["tokens"]), s(em["published"]), stakes, lo, hi, int(ty["epoch_days"]),
                                   int(em.get("after_days", 7)), lag, fallback=fb)
        got = cred.epoch_headline(tab)
        if got is None:
            continue
        v, n, src = got
        base = next(k for k in ((name, ty["tokens"]), (name, em["published"])) if k in groups)
        groups[(name, em["metric"])] = _as_stored(pd.DataFrame(
            {"date": [asof.normalize()], "project": [name], "metric": [em["metric"]], "value": [v],
             "source": [f"derived:mean of {n} Q0 epoch APRs — {src}"], "tier": [2]}), groups[base].columns)


def _trailing_yield_views(groups: dict) -> None:
    """token_yield_trailing_pct := reward tokens over the trailing `trailing_days` / the AVERAGE staked over the stored
    days of that window, x 365 / the days covered (a series younger than the window is annualised over its own span).
    token_yield_share_price_trailing_pct := the vault's share price over the same window, annualised. One row each, on
    the latest day of the reward series (Ether.fi, Jake 2026-10-07: the headline is the trailing year; top-ups arrive
    in lumps, so 90 days swings with each one)."""
    for p in scoped_projects():
        spec = config.PROTOCOL_YIELD.get(p["name"]) or {}
        ty = spec.get("token_yield") or {}
        n = ty.get("trailing_days")
        if not n:
            continue
        name = p["name"]
        # THE ACCOUNTANT'S RATE (Ether.fi, Jake's probes16): the vault's true share price, read today and 365 / 180 / 90
        # days back — its growth over the trailing window is the headline wherever it is stored.
        acm = ty.get("accountant_rate_metric")
        ac = groups.get((name, acm)) if acm else None
        if ac is not None and not ac.empty:
            a_ = ac.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index()
            a_ = a_[a_.index >= a_.index[-1] - pd.Timedelta(days=int(n) + 3)]
            span = (a_.index[-1] - a_.index[0]).days if len(a_) > 1 else 0
            if span >= 7 and a_.iloc[0]:
                g = (float(a_.iloc[-1]) / float(a_.iloc[0])) ** (365.0 / span) - 1.0
                groups[(name, "token_yield_accountant_trailing_pct")] = _as_stored(pd.DataFrame(
                    {"date": [a_.index[-1]], "project": [name], "metric": ["token_yield_accountant_trailing_pct"],
                     "value": [g], "source": [f"derived:{acm} {float(a_.iloc[0]):.6f} ({a_.index[0].date()}) -> "
                                               f"{float(a_.iloc[-1]):.6f} ({a_.index[-1].date()}), annualised over "
                                               f"{span} day(s)"], "tier": [2]}), ac.columns)
        tok, lock = groups.get((name, ty["tokens"])), groups.get((name, spec["lock"]))
        if tok is None or tok.empty or lock is None or lock.empty:
            continue
        t = tok.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index()
        end = t.index[-1]
        lo = end - pd.Timedelta(days=int(n))
        t = t[t.index > lo]
        lk = lock.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index()
        lk = lk[(lk.index > lo) & (lk.index <= end)]
        covered = (end - t.index[0]).days + 1
        if lk.empty or not lk.mean() or covered <= 0:
            continue
        # TIME-WEIGHTED (Jake's run 2026-10-07 17:08: 25.97% vs the share price's 9.95%): each day's reward over the
        # ETHFI staked THAT day (last stored value on or before it), summed — what a holder earned, which tracks the
        # share price. Rewards over the window's AVERAGE stake over-weight top-ups landing when the vault is large.
        daily = t / lk.reindex(t.index.union(lk.index)).sort_index().ffill().reindex(t.index)
        daily = daily.replace([float("inf"), float("-inf")], float("nan")).dropna()
        if daily.empty:
            continue
        v = float(daily.sum()) * 365.0 / covered
        src = (f"derived:sum over {len(daily)} day(s) of {ty['tokens']} / that day's {spec['lock']} "
               f"({float(t.sum()):,.0f} tokens; average stake {float(lk.mean()):,.0f}), {t.index[0].date()}..{end.date()} "
               f"x 365/{covered}" + ("" if covered >= int(n) else f" [covers {covered} of {n} days]"))
        groups[(name, "token_yield_trailing_pct")] = _as_stored(pd.DataFrame(
            {"date": [end], "project": [name], "metric": ["token_yield_trailing_pct"], "value": [v],
             "source": [src], "tier": [2]}), tok.columns)
        spm = ty.get("share_price_metric")
        sp = groups.get((name, spm)) if spm else None
        if sp is not None and not sp.empty:
            s_ = sp.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index()
            s_ = s_[(s_.index > end - pd.Timedelta(days=int(n))) & (s_.index <= end)]
            span = (s_.index[-1] - s_.index[0]).days if len(s_) > 1 else 0
            if span >= 7 and s_.iloc[0]:
                g = (float(s_.iloc[-1]) / float(s_.iloc[0])) ** (365.0 / span) - 1.0
                groups[(name, "token_yield_share_price_trailing_pct")] = _as_stored(pd.DataFrame(
                    {"date": [s_.index[-1]], "project": [name], "metric": ["token_yield_share_price_trailing_pct"],
                     "value": [g], "source": [f"derived:{spm} {s_.index[0].date()}..{s_.index[-1].date()}, annualised"],
                     "tier": [2]}), sp.columns)


def _native_fee_usd_views(groups: dict) -> None:
    """fees_usd := fees in the native coin x price_usd on the SAME date (config
    `fees_usd_from_native`). Plume, Jake's probe 4c 2026-09-30: the explorer's txnsFee is in PLUME.

    growthepie's rows under fees_usd are retired from the metric (now fees_usd_growthepie; SQL
    section BE) and are not shown in it; a row from any other source is a measurement and leaves
    the column alone. A day with no same-day price is left out."""
    for p in scoped_projects():
        name = p["name"]
        spec = p.get("fees_usd_from_native") or {}
        nat, px = groups.get((name, spec.get("native_metric"))), groups.get((name, "price_usd"))
        if not spec or nat is None or nat.empty or px is None or px.empty:
            continue
        target = spec.get("usd_metric", "fees_usd")   # Ethereum (B11): a reference series beside DefiLlama's
        held = groups.get((name, target))
        if target == "fees_usd" and held is not None and not held.empty:
            src = held["source"].astype(str)
            if (~src.str.startswith("growthepie") & (src != spec["source"])).any():
                continue
        price_on = px.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)
        price_on = price_on[price_on > 0]
        usd = nat[nat["date"].isin(price_on.index)].drop_duplicates("date", keep="last").copy()
        if usd.empty:
            continue
        usd["value"] = usd["value"].astype(float) * usd["date"].map(price_on)
        usd["metric"], usd["source"] = target, spec["source"]
        groups[(name, target)] = _as_stored(usd, nat.columns)


DERIVED_USD_OVER_PRICE = "derived:usd/price"


def _settlement_views(groups: dict) -> None:
    """Network Reserve Ratio = market cap / trailing-365-day settlement volume. Jake, 2026-09-30.

    EVERY DAY BOTH SIDES EXIST (Jake's run 2026-09-30 17:21: the ratio was built for the export's
    last day only, 37 days of history from 2026-08-25, although the volume covers three years and
    market cap a year). For each day D the window is the 365 days ending D, and it must hold all
    365 of them — a short sum would understate the volume and overstate the ratio, so a day with a
    blank Artemis day in its window gets nothing. Market cap is read on D; an earlier one is used
    only within 7 days, and that row's source states the lag. Both views are dated to D, so the
    latest point is the export's last date, which is what the cell's staleness (45 days,
    config.ARTEMIS_SETTLEMENT) and its Latest date read.
    """
    spec = config.ARTEMIS_SETTLEMENT
    for p in scoped_projects():
        _rebuilt_settlement_view(groups, p)
    _settlement_rebuild_views(groups)       # G7 item 8: Ethereum's rebuild beside the CSV, extending it only if valid
    for p in scoped_projects():
        _nrr_views(groups, p["name"], spec["metric"], "settlement_volume_365d_usd", "network_reserve_ratio",
                   int(spec["window_days"]), config.ARTEMIS_DERIVED, _settlement_short_why(groups, p))
    # TRADING THROUGHPUT (Jake, 2026-09-30): the same ratio on DefiLlama's DEX + perps volume, for
    # every chain from one source. A DIFFERENT DEFINITION — its own metrics, never mixed with the above.
    tt = config.TRADING_THROUGHPUT
    for p in scoped_projects():
        _throughput_view(groups, p["name"])
        _nrr_views(groups, p["name"], tt["metric"], tt["sum_metric"], tt["nrr_metric"], int(tt["window_days"]),
                   (tt["sum_metric"], tt["nrr_metric"]),
                   "DefiLlama's DEX + perps series holds {have} of the {n} days ending {last} — a short "
                   "window would understate throughput and overstate the ratio")


def _settlement_short_why(groups: dict, p: dict) -> str:
    """Why the 365-day settlement sum is not formed, in the series' OWN terms (G11 D5, Jake's 10-21 run: Near's reason
    read "the Artemis export holds 203 of 365 days" — Near's settlement is the DEX + P2P rebuild, its P2P read from
    BigQuery from 2026-04-01). `{have}`, `{n}`, `{last}` are filled by _nrr_views."""
    rb = p.get("settlement_rebuild") or {}
    if not rb:
        return ("the Artemis export holds {have} of the {n} days ending {last} — a short window would understate "
                "settlement volume and overstate the ratio; re-export the full history")
    leg = groups.get((p["name"], rb["p2p_metric"]))
    first = pd.to_datetime(leg["date"]).min().date() if leg is not None and not leg.empty else None
    return ("the settlement REBUILD (" + rb["dex_metric"] + " + " + rb["p2p_metric"]
            + (f", P2P from {({'bigquery': 'BigQuery'}).get(rb.get('engine'), rb.get('engine') or 'its source')} from "
               f"{first}" if first else "")
            + ") holds {have} of the {n} days ending {last} — a 365-day sum needs every day; it forms once the P2P leg "
              "has a year behind it")


SETTLEMENT_VALIDATION: dict = {}    # project -> the last build's rebuild-vs-CSV validation (G7 item 8)


def settlement_validation(rebuilt: pd.Series, csv: pd.Series, spec: dict) -> dict:
    """The rebuild against Artemis's CSV on the days both hold, up to spec['to'] (G7 item 8; the tolerance is stated in
    config before any number): the daily gap reported (median, 90th percentile of |gap|), every complete Monday..Sunday
    week's sum within ±weekly_tol, and at least min_days. {"status": PASSED / FAILED / NOT VALIDATED, ...}."""
    to = pd.Timestamp(spec["to"])
    both = pd.concat([rebuilt.rename("r"), csv.rename("c")], axis=1, sort=True).dropna()
    both = both[(both.index <= to) & (both["c"] > 0)]
    out = {"days": int(len(both)), "tol": float(spec["weekly_tol"]), "min_days": int(spec["min_days"])}
    if len(both) < int(spec["min_days"]):
        return out | {"status": "NOT VALIDATED", "why": f"{len(both)} overlapping day(s) up to {to.date()}, fewer than "
                                                         f"{int(spec['min_days'])}"}
    gap = both["r"] / both["c"] - 1
    wk = both.groupby(both.index.to_period("W-SUN"))
    full = wk.size()[wk.size() == 7].index
    wsum = wk.sum().loc[full]
    wgap = wsum["r"] / wsum["c"] - 1
    out |= {"from": str(both.index.min().date()), "to": str(both.index.max().date()),
            "daily_median_abs_gap": float(gap.abs().median()), "daily_p90_abs_gap": float(gap.abs().quantile(0.9)),
            "weeks": int(len(wgap)), "weekly_max_abs_gap": float(wgap.abs().max()) if len(wgap) else None,
            "weeks_outside": int((wgap.abs() > float(spec["weekly_tol"])).sum()),
            "total_ratio": float(both["r"].sum() / both["c"].sum())}
    if not len(wgap):
        return out | {"status": "NOT VALIDATED", "why": "no complete week in the overlap"}
    ok = out["weeks_outside"] == 0
    return out | {"status": "PASSED" if ok else "FAILED",
                  "why": (f"{out['weeks']} complete week(s) {out['from']}..{out['to']}: weekly |gap| max "
                          f"{out['weekly_max_abs_gap']:.1%} against ±{float(spec['weekly_tol']):.0%}"
                          + ("" if ok else f" — {out['weeks_outside']} week(s) outside")
                          + f"; daily |gap| median {out['daily_median_abs_gap']:.1%}, p90 "
                            f"{out['daily_p90_abs_gap']:.1%}; rebuild / CSV over the overlap {out['total_ratio']:.3f}")}


def _short_usd(v: float) -> str:
    """$5.7B / $8.7M / $0.7M / $65K — a size named in a note."""
    a = abs(v)
    return (f"${v / 1e9:,.1f}B" if a >= 1e9 else f"${v / 1e6:,.1f}M" if a >= 1e5 else f"${v / 1e3:,.0f}K" if a >= 1e3
            else f"${v:,.0f}")


def _settlement_rebuild_views(groups: dict) -> None:
    """ETHEREUM'S SETTLEMENT REBUILT FROM FREE SOURCES (Round G7 item 8, config SETTLEMENT_REBUILD.ethereum_rebuild):
    settlement_rebuilt_usd = DEX + NFT + P2P on the days all three are stored, always shown beside the CSV. Validated
    against the CSV (settlement_validation). PASSED: settlement_volume_usd gains the rebuild's days after the CSV's last,
    each labelled a different measuring point with the validation that admitted it. Otherwise the CSV stands alone and
    SETTLEMENT_VALIDATION says why."""
    spec = config.SETTLEMENT_REBUILD.get("ethereum_rebuild")
    if not spec or spec["project"] not in {p["name"] for p in scoped_projects()}:
        return
    name = spec["project"]
    legs = {k: _daily(groups.get((name, m))) for k, m in spec["legs"].items()}
    missing = [spec["legs"][k] for k, v in legs.items() if v.empty]
    csv = _daily(groups.get((name, config.ARTEMIS_SETTLEMENT["metric"])))
    if missing:
        SETTLEMENT_VALIDATION[name] = {"status": "NOT VALIDATED",
                                       "why": "leg(s) not stored: " + ", ".join(missing)
                                              + (" (the P2P leg needs SETTLEMENT_REBUILD.ethereum_rebuild.p2p.approved"
                                                 " — the dry-run bytes are in the Run Log)"
                                                 if spec["legs"]["p2p"] in missing else "")}
        return
    df = pd.concat([v.rename(k) for k, v in legs.items()], axis=1, sort=True).dropna()
    if df.empty:
        SETTLEMENT_VALIDATION[name] = {"status": "NOT VALIDATED", "why": "no day holds all three legs"}
        return
    rebuilt = df.sum(axis=1)
    # G15 3 (Jake's 10-23 store: NFT lacks 2026-09-20..09-23, DEX and P2P present): THE NFT LEG ABSENT AT ITS SOURCE. The
    # NFT read stores DefiLlama's whole daily chart and omits a day only where the chart has no point or a null for it
    # (fetch/eth_settlement.EthNftVolume, which names such days) — so a day INSIDE the NFT series' span where DEX and P2P
    # are stored and NFT is not is a source gap. The rebuild stands on DEX + P2P there, flagged on the row with the NFT
    # leg's size on the nearby days; no zero is stored and the NFT leg itself stays absent. A day outside the NFT span
    # (before its first row or after its last) is not filled.
    nft = legs["nft"]
    both = pd.concat([legs["dex"].rename("dex"), legs["p2p"].rename("p2p")], axis=1, sort=True).dropna()
    gap = both[(both.index > nft.index.min()) & (both.index < nft.index.max()) & ~both.index.isin(nft.index)]
    nft_gap_note = {}
    for d in gap.index:
        near = [x for x in df.index if abs((x - d).days) <= 7]
        n_lo, n_hi = (float(df.loc[near, "nft"].min()), float(df.loc[near, "nft"].max())) if near else (None, None)
        p_lo, p_hi = (float(df.loc[near, "p2p"].min()), float(df.loc[near, "p2p"].max())) if near else (None, None)
        nft_gap_note[d] = ("NFT leg absent (source gap): DEX + P2P only on this day"
                           + (f"; NFT {_short_usd(n_lo)}–{_short_usd(n_hi)} vs P2P {_short_usd(p_lo)}–{_short_usd(p_hi)} "
                              f"on the days within 7 either side" if near else ""))
    if len(gap):
        rebuilt = pd.concat([rebuilt, gap.sum(axis=1)]).sort_index()
    cols = groups[(name, spec["legs"]["dex"])].columns
    val = settlement_validation(rebuilt, csv, spec["validation"]) if not csv.empty else \
        {"status": "NOT VALIDATED", "why": "no Artemis CSV rows to validate against"}
    # G11 B5 (Jake's 10-21 run: the rebuild read BELOW its own legs — Q0 −5.5%, 30 days below P2P alone — and the
    # 365-day sum and NRR stopped at 2026-09-19): the rebuild is a day ONLY where all three legs are stored, so a window
    # sum covers the common days and the series ends on the shortest leg's last day. Each leg's end and the common days
    # are stated; a leg's missing day is never filled (unknown is never 0).
    ends = {k: v.index.max() for k, v in legs.items()}
    short = min(ends, key=ends.get)
    # THE DAYS DROPPED, PER LEG: every day inside the legs' joint span that one leg lacks (so the rebuild lacks it, and a
    # trailing-365-day sum containing it is not formed — the 365d / NRR stop at the first such day)
    span = pd.date_range(max(v.index.min() for v in legs.values()), max(ends.values()), freq="D")
    miss = {k: span.difference(v.index) for k, v in legs.items()}
    val["dropped_days"] = {k: [str(d_.date()) for d_ in m_] for k, m_ in miss.items() if len(m_)}
    val["legs_end"] = (f"the rebuild is a day only where all three legs are stored — it ends with the shortest leg "
                       f"({short} to {ends[short].date()}; "
                       + ", ".join(f"{k} to {d_.date()}" for k, d_ in ends.items()) + ")"
                       + ("; days a leg lacks inside the joint span: " + "; ".join(
                           f"{k} {len(m_)} ({', '.join(str(d_.date()) for d_ in m_[:4])}{'…' if len(m_) > 4 else ''})"
                           for k, m_ in miss.items() if len(m_)) if any(len(m_) for m_ in miss.values()) else ""))
    val["common_days"] = int(len(df))
    val["nft_gap_days"] = [str(d.date()) for d in sorted(nft_gap_note)]
    SETTLEMENT_VALIDATION[name] = val
    tag = f"{spec['label']}; validation {val['status']}: {val.get('why', '')}; {val['legs_end']}"[:500]
    groups[(name, "settlement_rebuilt_usd")] = _as_stored(pd.DataFrame({
        "date": rebuilt.index, "project": name, "metric": "settlement_rebuilt_usd", "value": rebuilt.values, "tier": 1,
        "source": [f"derived:dex+nft+p2p[{(nft_gap_note[d] + '; ' + tag)[:600]}]" if d in nft_gap_note
                   else f"derived:dex+nft+p2p[{tag}]" for d in rebuilt.index]}), cols)
    # G13 1a: the live settlement_volume_usd's days after the handover ARE the rebuilt series — read back from it, so
    # the dependency is the code's own (the wiring audit's INPUT edge), not a local copy beside it
    rebuilt = _daily(groups[(name, "settlement_rebuilt_usd")])
    # G10 4 (Jake 2026-10-21): A DECLARED HANDOVER. From its date the rebuild IS the live settlement_volume_usd whatever
    # this build's validation says — never scaled to the CSV; the CSV stays the reference check and its days are never
    # overwritten (the rebuild starts the day after the CSV's last). Without the declaration only a PASSED validation
    # extends the series, as before.
    ho = spec.get("handover")
    if csv.empty or (val["status"] != "PASSED" and not ho):
        return
    start = max(csv.index.max() + pd.Timedelta(days=1), pd.Timestamp(ho["from"])) if ho else \
        csv.index.max() + pd.Timedelta(days=1)
    after = rebuilt[rebuilt.index >= start]
    if after.empty:
        return
    how = (f"DECLARED HANDOVER from {ho['from']} ({ho['declared_by']}): {ho['validation_note']}; never scaled; "
           f"reference check: the Artemis CSV to {csv.index.max().date()}; this build's validation {val['status']}"
           if ho else f"validated against the Artemis CSV {val['from']}..{val['to']}")
    ext = pd.DataFrame({"date": after.index, "project": name, "metric": config.ARTEMIS_SETTLEMENT["metric"],
                        "value": after.values, "tier": 1,
                        "source": f"derived:settlement_rebuild[{spec['label']}; {how}]"[:500]})
    g = groups[(name, config.ARTEMIS_SETTLEMENT["metric"])]
    groups[(name, config.ARTEMIS_SETTLEMENT["metric"])] = _as_stored(
        pd.concat([g, ext[g.columns.intersection(ext.columns)]], ignore_index=True).sort_values("date"), g.columns)


def _rebuilt_settlement_view(groups: dict, p: dict) -> None:
    """settlement_volume_usd REBUILT by Artemis's method where Artemis has no series (Plume, Jake
    2026-10-01): DEX volume + P2P transfer volume per day, NFT ~0 — a day only where both legs have
    a value. Every row says UNVALIDATED (config <project>.settlement_rebuild)."""
    spec = p.get("settlement_rebuild") or {}
    name = p["name"]
    if not spec or (groups.get((name, "settlement_volume_usd")) is not None
                    and not groups[(name, "settlement_volume_usd")].empty):
        return
    legs = []
    for m in (spec["dex_metric"], spec["p2p_metric"]):
        g = groups.get((name, m))
        if g is None or g.empty:
            return
        legs.append(g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
                    .set_index("date")["value"].astype(float).rename(m))
    both = pd.concat(legs, axis=1).dropna()
    if both.empty:
        return
    view = pd.DataFrame({"date": both.index, "project": name, "metric": "settlement_volume_usd",
                         "value": both.sum(axis=1).values,
                         "source": f"derived:{spec['dex_metric']}+{spec['p2p_metric']}[{spec['label']}; NFT ~0]",
                         "tier": 1})
    groups[(name, "settlement_volume_usd")] = _as_stored(view, groups[(name, spec["p2p_metric"])].columns)


def _throughput_view(groups: dict, name: str) -> None:
    """trading_throughput_usd = dex_volume_usd + perps_volume_usd per day, at read time. A day is
    in the sum only where every leg the chain is COVERED for has a value; a leg DefiLlama carries
    no adapter for on that chain (config.TRADING_THROUGHPUT coverage) is absent, not zero, and the
    row's source says so."""
    tt = config.TRADING_THROUGHPUT
    cov = (tt["chains"].get(name) or {}).get("covered") or ()
    if not cov:
        return
    legs = []
    for m in cov:
        g = groups.get((name, m))
        if g is None or g.empty:
            return
        legs.append(g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
                    .set_index("date")["value"].astype(float).rename(m))
    both = pd.concat(legs, axis=1).dropna()
    if both.empty:
        return
    missing = [m for m in tt["legs"] if m not in cov]
    src = "derived:" + "+".join(cov) + (f"[no DefiLlama {'/'.join(missing)} adapter on this chain]" if missing else "")
    view = pd.DataFrame({"date": both.index, "project": name, "metric": tt["metric"],
                         "value": both.sum(axis=1).values, "source": src, "tier": 1})
    groups[(name, tt["metric"])] = _as_stored(view, groups[(name, cov[0])].columns)


def _nrr_views(groups: dict, name: str, vol_metric: str, sum_metric: str, nrr_metric: str, n: int,
               blocked: tuple, short_why: str) -> None:
    """market cap / trailing-n-day volume, for EVERY day with all n days of volume behind it and a
    market cap on that day (or within 7 days before, the lag stated on the row)."""
    sv = groups.get((name, vol_metric))
    if sv is None or sv.empty:
        return
    daily = sv.assign(date=pd.to_datetime(sv["date"]).dt.normalize()) \
              .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index()
    last = daily.index.max()
    full = daily.reindex(pd.date_range(daily.index.min(), last, freq="D"))
    sums = full.rolling(n, min_periods=n).sum().dropna()
    src = str(sv.sort_values("date").iloc[-1]["source"])
    brk = config.series_break(name, vol_metric)
    if brk and brk.get("status") == "INCENTIVE":
        # AN INCENTIVE PERIOD IS NOT A DEFECT (Plume, Jake 2026-10-02): the PRIMARY ratio reads the organic
        # window — clean_from onward, annualised over the covered days until a full window exists — and the
        # FULL-YEAR ratio is stored beside it under its own metrics, labelled incentive-inflated.
        inc = brk["incentive"]
        if not sums.empty:
            _nrr_rows(groups, name, sv, sums, inc["sum_metric"], inc["nrr_metric"], n, src,
                      f"[{inc['full_label']}]")
        else:
            _VIEW_BLOCKS[(name, inc["nrr_metric"])] = (
                f"NOT AVAILABLE — the full year needs {n} days of {vol_metric} and {int(full.notna().sum())} are "
                f"held ({daily.index.min().date()}..{last.date()}); the incentive period's native transfers are "
                f"not read (config {name}.settlement_rebuild native_from / native_max_pages_per_day)")
        clean = pd.Timestamp(brk["clean_from"])
        org = full[full.index >= clean]
        rows = {}
        for d in org.index:
            win = org[(org.index > d - pd.Timedelta(days=n)) & (org.index <= d)]
            cov = int(win.notna().sum())
            if cov >= int(inc.get("min_days", 30)):
                rows[d] = (float(win.sum()) * n / cov, cov)
        if not rows:
            for m in blocked:
                _VIEW_BLOCKS[(name, m)] = (f"the organic window ({inc['organic_label']}) from {clean.date()} has "
                                           f"fewer than {inc.get('min_days', 30)} covered days")
            return
        osums = pd.Series({d: v for d, (v, _) in rows.items()})
        covd = {d: c for d, (_, c) in rows.items()}
        _nrr_rows(groups, name, sv, osums, sum_metric, nrr_metric, n, src,
                  lambda d: f"[{inc['organic_label']}" + (f": annualised over {covd[d]} covered day(s)]"
                                                         if covd[d] < n else "]"))
        return
    # AN UNEXPLAINED BREAK IS NEVER SPANNED (NEAR, Jake 2026-10-01): only windows starting on or after
    # its clean date are kept; with none, the sum and the ratio are blocked with the break's reason.
    if brk:
        sums = sums[sums.index - pd.Timedelta(days=n - 1) >= pd.Timestamp(brk["clean_from"])]
        if sums.empty:
            for m in blocked:
                _VIEW_BLOCKS[(name, m)] = (f"no {n}-day window yet starts after the unexplained break "
                                           f"{brk['between'][0]}..{brk['between'][1]}: {brk['why']}")
            return
    if sums.empty:
        have = len(daily[daily.index > last - pd.Timedelta(days=n)])
        for m in blocked:
            _VIEW_BLOCKS[(name, m)] = short_why.format(have=have, n=n, last=last.date())
        return
    # G11 B5 (Jake's 10-21 run: Ethereum's 365-day sum and NRR stopped at 2026-09-19 while the daily series ran to
    # 10-18): a window holding a blank day is not formed, so the sums stop before the first blank day after them —
    # the blank days are named on every row
    stop = ""
    if sums.index.max() < last:
        blank = full[(full.index > sums.index.max()) & full.isna()].index
        stop = (f"[stops at {sums.index.max().date()}: {vol_metric} has no value on {len(blank)} day(s) after it ("
                + ", ".join(str(d.date()) for d in blank[:5]) + ("…" if len(blank) > 5 else "")
                + f") — a {n}-day window holding a blank day is not formed]")
    _nrr_rows(groups, name, sv, sums, sum_metric, nrr_metric, n, src, stop)


def _nrr_rows(groups: dict, name: str, sv, sums, sum_metric: str, nrr_metric: str, n: int, src: str, tag) -> None:
    """The trailing-n sum and market cap / sum, per day, under the given metrics; `tag` (text, or a
    function of the day) is appended to every row's source."""
    lab = tag if callable(tag) else (lambda d: tag)
    tot = pd.DataFrame({"date": sums.index, "project": name, "metric": sum_metric, "value": sums.values,
                        "source": [f"derived:sum{n}({src}){lab(d)}" for d in sums.index]})
    groups[(name, sum_metric)] = _as_stored(tot, sv.columns)
    last = sums.index.max()
    mc = groups.get((name, "market_cap_usd"))
    if mc is None or mc.empty:
        return
    mcd = mc.assign(date=pd.to_datetime(mc["date"]).dt.normalize()) \
            .drop_duplicates("date", keep="last")[["date", "value"]].astype({"value": float}).sort_values("date")
    mcd["date"] = mcd["date"].astype("datetime64[ns]")        # one unit on both sides of the asof join
    both = pd.merge_asof(pd.DataFrame({"date": sums.index.astype("datetime64[ns]"), "vol": sums.values}),
                         mcd.rename(columns={"value": "cap"}).assign(on=mcd["date"]),
                         on="date", direction="backward", tolerance=pd.Timedelta(days=7)).dropna()
    both = both[both["vol"] > 0]
    if both.empty:
        _VIEW_BLOCKS[(name, nrr_metric)] = (
            f"no day with both a full {n}-day volume window (to {last.date()}) and a market cap on that "
            f"day or within 7 days before it")
        return
    lag = (both["date"] - both["on"]).dt.days
    base = f"derived:market_cap/{sum_metric.replace('_volume', '').replace('_usd', '')}"
    nrr = pd.DataFrame({"date": both["date"].values, "project": name, "metric": nrr_metric,
                        "value": (both["cap"] / both["vol"]).values,
                        "source": [base + (f"[market cap {d} day(s) earlier]" if d else "") + lab(pd.Timestamp(day))
                                   for d, day in zip(lag, both["date"])]})
    groups[(name, nrr_metric)] = _as_stored(nrr, sv.columns)


def _protocol_yield(R: Refs, data_by_key: dict | None = None):
    """A3 supporting column: revenue share paid to a protocol's stakers — kept apart from the
    validator yield by name and by tab, so it can never be summed into archetype-1 Total Yield."""
    def build(r, p):
        spec = config.PROTOCOL_YIELD.get(p["name"])
        # THE SAME BASIS AS THE TOKEN COLUMN (Jake's price convention, 2026-10-09): at payment-day prices the dollar
        # yield, $ paid / (stake x the payment-weighted price), is the token yield — the price cancels — so both read
        # the one payday row. Today's-price APR is a labelled second figure on Credibility (in_apr_today_price).
        pdy = ((spec or {}).get("token_yield") or {}).get("payday")
        if pdy:
            cell = R.D(r, pdy["metric"], "now")
            return calc(f"IF(ISNUMBER({cell}),{cell},{NA})")
        if spec:
            rev, lock = R.D(r, spec["revenue"], "q0"), R.D(r, spec["lock"], "now")
            px = R.D(r, "price_usd", "now")
            # lock_add (Pendle's virtual sPENDLE, 2026-09-28): joins the denominator when read;
            # until then the real lock alone is used and the cell is marked READS HIGH.
            if spec.get("lock_add"):
                add = R.D(r, spec["lock_add"], "now")
                lock = f"({lock}+IF(ISNUMBER({add}),{add},0))"
            return calc(f"IF(AND(ISNUMBER({rev}),ISNUMBER({lock}),ISNUMBER({px})),{_annualise(R, r, p, spec['revenue'], rev)}/({lock}*{px}),{NA})")
        why = config.PROTOCOL_YIELD_NOT_APPLICABLE.get(p["name"])
        return f"n/a — {why}" if why else ""
    return ("PROTOCOL STAKING YIELD ($) — alongside: what a REAL staker earns per staked token = holders revenue (annualised) ÷ "
            "locked value at spot (real + virtual where rewards are shared with virtual balances) — revenue share, NOT a validator yield. "
            "Aerodrome and Sky: at PAYMENT-DAY prices over the mean stake (Jake's price convention, 2026-10-09), which "
            "makes it equal to the token column; today's-price APR is a labelled second figure on Credibility",
            build, FMT_PCT, "calc", False,
            {"metric": "holders_revenue_usd",
             "partial_fn": lambda p: (config.lock_partial_reason(p["name"], config.PROTOCOL_YIELD[p["name"]]["lock"])
                                      or _virtual_missing(p["name"], data_by_key or {})
                                      if p["name"] in config.PROTOCOL_YIELD else None),
             "partial_direction": "the locked value is incomplete, so the yield READS HIGH.",
             "partial_fmt": '0.0%" PARTIAL↑";(0.0%)" PARTIAL↑"',
             # Ether.fi's yield reads 0 because its buyback has not moved, and its recipient
             # class is unconfirmed under the new programme (2026-09-28).
             "flag_fn": lambda p: _protocol_yield_flag(p, data_by_key or {})})


def _voter_apr_voting_power(R: Refs):
    """A3, Aerodrome (Jake, 2026-10-07): the aggregate voter APR on Aerodrome's own vAPR definition — voter rewards
    (fees + bribes, annualised) / (veAERO VOTING POWER x price). Voting power decays with lock time, so it is below
    AERO locked (881.1M vs 1.053bn on 2026-10-07) and this rate is above the yield on AERO locked beside it. Blank
    where PROTOCOL_YIELD declares no `voting_power`."""
    def build(r, p):
        spec = config.PROTOCOL_YIELD.get(p["name"]) or {}
        if not spec.get("voting_power"):
            return ""
        pdy = (spec.get("token_yield") or {}).get("payday") or {}
        if pdy.get("annual_metric"):              # payment-day prices (Jake's price convention, 2026-10-09)
            ann, vp = R.D(r, pdy["annual_metric"], "now"), R.D(r, spec["voting_power"], "now")
            return calc(f"IF(AND(ISNUMBER({ann}),ISNUMBER({vp}),{vp}>0),{ann}/{vp},{NA})")
        rev, vp, px = R.D(r, spec["revenue"], "q0"), R.D(r, spec["voting_power"], "now"), R.D(r, "price_usd", "now")
        return calc(f"IF(AND(ISNUMBER({rev}),ISNUMBER({vp}),ISNUMBER({px}),{vp}>0),"
                    f"{_annualise(R, r, p, spec['revenue'], rev)}/({vp}*{px}),{NA})")
    return ("VOTER APR ON VOTING POWER — Aerodrome's vAPR denominator: voter rewards (fees + bribes) in AERO at each "
            "payment day's price, annualised, ÷ veAERO voting power. The yield column divides by the votes cast",
            build, FMT_PCT, "calc", False,
            {"metric_fn": lambda n: (config.PROTOCOL_YIELD.get(n) or {}).get("voting_power")})


def _flags(data_by_key: dict, name: str, metrics: list[str]) -> str:
    out = []
    for m in metrics:
        st = data_by_key.get(f"{name}|{m}")
        if st is None:
            continue
        if st["status"] in ("stale", "manual", "missing", "unconfigured", "manual only"):
            out.append(f"{METRICS[m]['label']}: {st['status']}" + (f" ({st['entered_on']})" if st["status"] == "manual" else ""))
    return "; ".join(out)


def _confidence_tally(ws, row: int, projects: list[dict], specs: list[tuple], data_by_key: dict) -> int:
    """How much of THIS tab can be acted on today. The headline the Gap Report cannot give."""
    counts = {"GREEN": 0, "AMBER": 0, "RED": 0}
    for p in projects:
        for spec in specs:
            meta = spec[5] if len(spec) > 5 else {}
            # metric_fn rows were silently absent from this tally: it read only the static tag,
            # so a row whose metric is per project counted for nobody. Fixed 2026-09-23 when the
            # locked-tokens rows became per project — it had been true of the revenue row since
            # metric_fn was introduced.
            metric = meta["metric_fn"](p["name"]) if meta.get("metric_fn") else meta.get("metric")
            if not metric:
                continue
            st = data_by_key.get(f"{p['name']}|{metric}")
            if st and st.get("confidence") in counts:
                counts[st["confidence"]] += 1
    total = sum(counts.values()) or 1
    ws.cell(row=row, column=1, value="Confidence on this tab").font = F_BOLD
    for i, band in enumerate(("GREEN", "AMBER", "RED")):
        c = ws.cell(row=row + 1 + i, column=1,
                    value=f"{band}  {counts[band]} of {sum(counts.values())}  ({counts[band] / total:.0%})")
        c.font = F_BASE
        c.fill = CONFIDENCE_FILL[band]
    legend = ("GREEN = verified source, mechanism confirmed, no PARTIAL marker, more than one observation. Use it.   "
              "AMBER = a real number, qualified — hover the cell for why.   "
              "RED = not a number: suppressed, refused or gapped.   "
              "N/A = the quantity does not exist for this project — no work to do, and not counted above.")
    ws.cell(row=row + 4, column=1, value=legend).font = F_SUB
    return row + 6


# ---------------------------------------------------------------------------------------
# CREDIBILITY (Jake, 2026-10-05): every headline cell on A1-A4 beside an independent reference.
# ---------------------------------------------------------------------------------------
# THE TABS WHOSE BOLD FIGURES ARE HEADLINES (Credibility, Trajectory, the snapshot, cross-tab): A1-A4 and, from Phase 2
# (Jake 2026-10-12), CP — the comparability layer across every project
HEADLINE_TABS = ("A1", "A2", "A3", "A4", "CP")
_HEADLINE_CELLS: list = []          # filled by _write_table while A1-A4 are written
_TAB_CELLS: dict = {}               # (sheet prefix "A1".."A4", project, header) -> (sheet title, coordinate)
CREDIBILITY_ROWS: list = []         # the Credibility tab's rows, in tab order (credibility_report.py --roots)

# (tab prefix, header prefix, stable id) — the id is what config.CREDIBILITY keys on, so a header can
# be reworded without losing its checks. A headline column not listed here fails the test that
# requires every headline cell to have an id.
HEADLINE_IDS = (
    ("A1", "VALIDATOR STAKING YIELD", "a1_validator_yield"),
    ("A1", "TOTAL YIELD — stakers", "a1_total_yield_stakers"),
    ("A1", "NET ISSUANCE AFTER BURN, annualised", "a1_net_issuance_pct"),
    ("A1", "NET ISSUANCE AFTER BURN Q0", "a1_net_issuance"),
    ("A1", "Gross burn Q0 (tokens) — every burn", "a1_gross_burn"),
    ("A1", "Gross issuance Q0 (tokens)", "a1_gross_issuance"),
    ("A1", "NRR ON SETTLEMENT VOLUME", "a1_nrr_settlement"),
    ("A1", "NRR ON TRADING THROUGHPUT", "a1_nrr_throughput"),
    ("A1", "TVL", "a1_tvl"),
    ("A1", "FEES ÷ ISSUANCE", "a1_fees_issuance"),
    ("A2", "FREE FLOAT ÷ ARR", "a2_ff_arr"),
    ("A2", "FREE FLOAT = circulating", "a2_free_float"),
    ("A2", "SUPPLY TRAJECTORY", "a2_trajectory"),
    ("A2", "Customer revenue Q0", "a2_customer_revenue"),
    ("A2", "Emissions to suppliers Q0", "a2_emissions"),
    ("A2", "CUSTOMER REVENUE PER TOKEN EMITTED", "a2_rev_per_token"),
    ("A2", "Customer revenue ÷ emissions value", "a2_rev_vs_emissions_value"),
    ("A3", "CIRCULATING RETIREMENT RATE (tokens)", "a3_circ_retirement"),
    ("A3", "FDV RETIREMENT RATE (tokens)", "a3_fdv_retirement"),
    ("A3", "BUYBACK AS % OF SUPPLY (annualised, implied)", "a3_implied_buyback_pct"),
    ("A3", "Actual buyback as % of supply", "a3_actual_buyback_pct"),
    ("A3", "Net absorption Q0", "a3_net_absorption"),
    ("A3", "PROTOCOL STAKING YIELD (tokens)", "a3_protocol_yield"),
    ("A3", "Buyback to LOCKED supply", "a3_buyback_locked"),
    ("A4", "PERMANENT BURN YIELD", "a4_burn_yield"),
    ("A4", "BURN ÷ ISSUANCE", "a4_crossover"),
    ("A4", "NET SUPPLY CHANGE Q0", "a4_net_change"),
    ("A4", "NET SUPPLY CHANGE, annualised", "a4_net_change_pct"),
    ("A4", "GROSS BURN Q0", "a4_gross_burn"),
    ("A4", "GROSS ISSUANCE Q0", "a4_gross_issuance"),
    ("A4", "Pool release Q0", "a4_pool_release"),
    ("CP", "TRADEABLE FLOAT", "cp_float"),
    ("CP", "HOLDER VALUE YIELD", "cp_hvy"),
    ("CP", "NET HOLDER YIELD", "cp_nhy"),
    ("CP", "SUBSIDY COVER", "cp_subsidy_cover"),
    ("CP", "BUSINESS 1 — core usage", "cp_business_1"),
    ("CP", "BUSINESS 2 — core usage", "cp_business_2"),
    ("CP", "BUSINESS 3 — core usage", "cp_business_3"),
    ("CP", "BUSINESS 4 — core usage", "cp_business_4"),
    ("CP", "MARKET SHARE vs its category", "cp_market_share"),
    ("CP", "MARKET SHARE, SECOND BASIS", "cp_market_share_2"),
)


def headline_id(sheet: str, header: str) -> str | None:
    for tab, prefix, hid in HEADLINE_IDS:
        if sheet.startswith(tab) and header.startswith(prefix):
            return hid
    return None


def _traj_hid(title: str, spec: tuple) -> str | None:
    """The trajectory id of a table column, or None when the column has no trajectory row (trajectory.py): an A-tab
    headline (bold, a figure) or a column declared `trajectory`; a Master column listed in config.TRAJECTORY."""
    head, kind = spec[0], spec[3]
    meta = spec[5] if len(spec) > 5 else {}
    if title == "Master":
        return trajectory.master_id(head) if head in config.TRAJECTORY["master_headers"] else None
    if title[:2] not in HEADLINE_TABS or kind not in ("calc", "pull"):
        return None
    if meta.get("trajectory_id"):
        return meta["trajectory_id"]
    return headline_id(title, head) if (len(spec) > 4 and spec[4]) else None


def _trend_spec(title: str, spec: tuple, hid: str) -> tuple:
    """The trend cell beside a headline: arrow · QoQ · 4-quarter growth · quarters improved, read from the
    Trajectory tab. A Master column that REFERENCES an A-tab cell shows that cell's trajectory (one figure, one row)."""
    head = spec[0]

    def build(r, p):
        if title == "Master":
            ref = _master_ref_for(head, p)
            if ref:
                m = re.match(r"^='(.+)'!([A-Z]+\d+)$", ref)
                k = trajectory.CELL_KEY.get((m.group(1), m.group(2))) if m else None
                if k is None and m and (m.group(1), m.group(2)) in trajectory.CELL_SPEC:
                    sp = trajectory.CELL_SPEC[(m.group(1), m.group(2))]
                    k = trajectory.register(m.group(1), p, sp["header"], sp["hid"], sp["builder"], sp["fmt"],
                                            sp["metric"], m.group(2))
                if k is None:
                    return "n/a — the A-tab cell it reads has no trajectory"
                return trajectory.trend_formula(k)
            return trajectory.trend_formula(trajectory.key("Master", p["name"], hid))
        return trajectory.trend_formula(trajectory.key(title[:2], p["name"], hid))
    return (f"Trend — {hid} (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab)", build, FMT_TEXT, "calc",
            False, {"trend_of": hid})


def _with_trend_columns(title: str, specs: list[tuple], key_cols: set[int] | None) -> tuple[list[tuple], set[int] | None]:
    out, pos = [], {}
    for i, spec in enumerate(specs, start=1):
        out.append(spec)
        pos[i] = len(out)
        hid = _traj_hid(title, spec)
        if hid:
            out.append(_trend_spec(title, spec, hid))
    return out, ({pos[i] for i in key_cols} if key_cols else key_cols)


_REF = re.compile(r'INDEX\(Data!\$([A-Z]+)\$\d+:\$[A-Z]+\$\d+,MATCH\((?:\$A\d+&"\|([a-z0-9_]+)"|"([A-Za-z.]+)\|([a-z0-9_]+)")')
_NA_BLANK_STATUS = {"missing": "no rows stored", "n/a": "not applicable to this project", "stale": "stale",
                    "blocked": "BLOCKED", "pending": "PENDING"}


def _reasoned_na(formula: str, p: dict, data_by_key: dict) -> str:
    """NO BARE n/a (Jake's alignment round, SPEC.md rule 2): a formula's "n/a" fallback names the first input that has
    no value for this project in the window the formula reads, with that input's Data status and note (an input that
    is absent wins over one that is merely stale) — or says the inputs are present and the computation itself has no
    value (a ratio over zero, an empty window)."""
    col_of = {v: k for k, v in DC.items()}
    absent, weak = "", ""
    for letter, metric, gproj, gmetric in _REF.findall(formula):
        col = col_of.get(letter, "now")
        if col.endswith(("_covered_days", "_events")):
            continue
        key = f"{gproj}|{gmetric}" if gproj else f"{p['name']}|{metric}"
        m = gmetric or metric
        row = data_by_key.get(key)
        if row is None:
            absent = absent or f"{m}: no Data row for this project"
            continue
        val = row.get(col)
        st = str(row.get("status") or "")
        note = str(row.get("note") or "").split(" | ")[0]
        desc = f"{m} ({col}): " + (_NA_BLANK_STATUS.get(st, st) or "no value in this window") + (f" — {note}" if note else "")
        # G12 C8 (Jake's 10-21 workbook: A2 "Supply units Δ3m/6m/9m" read "n/a — supply_units (q1): ok"): an input
        # whose series STARTS after the window opens is not "ok" there — it needs history, from a date
        first, win = str(row.get("first_date") or "")[:10], _WINDOWS.get(col)
        first = first if re.fullmatch(r"\d{4}-\d{2}-\d{2}", first) else ""
        if (val is None or (isinstance(val, float) and pd.isna(val))) and st not in _NA_BLANK_STATUS and first and \
                win and first > win[0]:
            valid = (pd.Timestamp(first) + (pd.Timestamp(win[1]) - pd.Timestamp(win[0]))
                     + (pd.Timestamp(_WINDOWS["q0"][1]) - pd.Timestamp(win[1])) if _WINDOWS.get("q0") else None)
            desc = (f"NEEDS HISTORY: {m} is held from {first}, after the {col.upper()} window opens ({win[0]})"
                    + (f" — valid from {valid.date()}" if valid is not None else ""))
        if val is None or (isinstance(val, float) and pd.isna(val)):
            absent = absent or desc
        elif st in _NA_BLANK_STATUS:
            weak = weak or desc
    why = absent or weak or _no_value_cause(formula, p, data_by_key) or \
        "the inputs are present; the computation has no value (a ratio over zero or an empty window)"
    text = ("n/a — " + why)[:240].replace('"', "'")
    return formula.replace(NA, f'"{text}"')


_CFG_REF = re.compile(r"'Config & Sources'!\$([A-Z]+)\$\d+:\$[A-Z]+\$\d+")
_CFG_EQ = re.compile(r"'Config & Sources'!\$([A-Z]+)\$\d+:\$[A-Z]+\$\d+,MATCH\([^)]*\)\)=\"([a-z_]+)\"")


def _no_value_cause(formula: str, p: dict, data_by_key: dict) -> str:
    """WHY A FORMULA WHOSE INPUTS ARE ALL PRESENT HAS NO VALUE (Round G9 B3, Jake: 43 cells said "the inputs are present;
    the computation has no value"). Read from what the formula reads, in order:
      1. a Config & Sources column it tests for a value (=“yield_payout”) holding another — the declared destination;
      2. a Config & Sources column it multiplies by that holds TEXT — the declared share / split / status, quoted
         ("no share of revenue to buyback documented for this window");
      3. a Data input that is 0 in the window it reads — a ratio over zero.
    "" when none applies (the caller keeps the generic text, which coverage reads as unresolved)."""
    inv = {v: k for k, v in CC.items()}
    name = p["name"]
    for letter, want in _CFG_EQ.findall(formula):
        head = inv.get(letter)
        v = CFG_WRITTEN.get((name, head)) if head else None
        # the "unconfirmed" test is the GATE (gated(): an unconfirmed split shows no figure), never the line's
        # applicability — a status other than "unconfirmed" is exactly when the line computes
        if want == "unconfirmed":
            continue
        if head and str(v) != want:
            return f"{head} is {v!r} (declared), not {want!r} — this line applies only to that destination"
    for letter in dict.fromkeys(_CFG_REF.findall(formula)):
        head = inv.get(letter)
        v = CFG_WRITTEN.get((name, head)) if head else None
        # the MATCH key column (Project) and the status columns are not the declared split
        if head and isinstance(v, str) and v and head != "Project" and not head.startswith("Status") \
                and head not in ("Buyback status", "Burn split status"):
            return f"{head}: {v}"[:200] + " (declared in config)"
    # a share the formula needs and config leaves BLANK: no documented value — an unconfirmed split, said as one
    for letter in dict.fromkeys(_CFG_REF.findall(formula)):
        head = inv.get(letter)
        if head and head != "Project" and (name, head) in CFG_WRITTEN and CFG_WRITTEN[(name, head)] in (None, "") \
                and head.lower().startswith("share"):
            # G10 2a: no published split is a fact about the protocol, not a missing value — N/A BY DESIGN
            kind = "burn" if "burn" in head.lower() else "buyback"
            return (f"no published buyback/burn split ({head} has no documented value in config) — the actual {kind} "
                    f"is on {IMPLIED_ACTUAL_ON[kind]}")
    col_of = {v: k for k, v in DC.items()}
    for letter, metric, gproj, gmetric in _REF.findall(formula):
        row = data_by_key.get(f"{gproj}|{gmetric}" if gproj else f"{name}|{metric}")
        col = col_of.get(letter, "now")
        if row is not None and isinstance(row.get(col), (int, float)) and row.get(col) == 0 \
                and not col.endswith(("_covered_days", "_events")):
            return f"a ratio over zero: {gmetric or metric} ({col}) is 0 in the window it reads"
    return ""


def _write_table(ws, R: Refs, projects: list[dict], specs: list[tuple], data_by_key: dict, flag_metrics: list[str],
                 start_row: int = 4, key_cols: set[int] | None = None) -> int:
    specs, key_cols = _with_trend_columns(ws.title, specs, key_cols)
    headers = [s[0] for s in specs] + ["Data flags (stale / manual / missing)"]
    _header(ws, start_row, headers, key_cols)
    r = start_row + 1
    for p in projects:
        for j, spec in enumerate(specs, start=1):
            head, builder, fmt, kind = spec[:4]
            bold = len(spec) > 4 and spec[4]
            with _project_row(p["name"]):
                v = builder(r, p)
            if isinstance(v, str) and v.startswith("=") and NA in v:
                v = _reasoned_na(v, p, data_by_key)
            c = ws.cell(row=r, column=j, value=v)
            if ws.title[:2] in HEADLINE_TABS:
                _TAB_CELLS[(ws.title[:2], p["name"], head)] = (ws.title, c.coordinate)
            _style(c, kind, fmt, bold)
            if kind in ("calc", "pull") and bold:
                c.fill = FILL_KEY
            # visual flags from the underlying data / config
            meta = spec[5] if len(spec) > 5 else {}
            # A PER-PROJECT SCOPE ON THE NUMBER ITSELF (G11 C5: Hyperliquid's transactions are HyperEVM's only) — the cell
            # stays a number, its format says what it counts
            if meta.get("fmt_fn") and meta["fmt_fn"](p["name"]):
                c.number_format = meta["fmt_fn"](p["name"])
            # A cell whose figure — or whose only input — was chased and closed says so, instead
            # of showing "n/a" next to twenty real ones and reading as missing data.
            # metric_fn: the metric this cell reads is PER PROJECT. Sky's implied-buyback base is
            # net_protocol_surplus_usd and everyone else's is revenue_usd, so a fixed "metric" tag
            # would attach the wrong series' confidence, coverage and staleness to the cell.
            if meta.get("metric_fn"):
                meta = dict(meta, metric=meta["metric_fn"](p["name"]))
            if meta.get("metric"):
                meta = dict(meta, metric=config.metric_alias(p["name"], meta["metric"]))
            dep = meta.get("closed_with") or meta.get("metric")
            closed = config.unavailable_for(p["name"], dep) if dep else None
            if closed and closed.get("route_only"):
                closed = None               # one ROUTE was closed; the figure itself is live
            # THE CREDIBILITY TAB READS THIS CELL (Jake, 2026-10-05): every headline figure on A1-A4,
            # by address, so its row there shows exactly what this tab shows.
            if bold and kind in ("calc", "pull") and ws.title[:2] in HEADLINE_TABS:
                _HEADLINE_CELLS.append({"sheet": ws.title, "project": p["name"], "header": head,
                                        "cell": c.coordinate, "kind": kind, "fmt": fmt,
                                        "id": headline_id(ws.title, head), "metric": meta.get("metric"),
                                        # the input series, metric_fn resolved (sanity.starved_headlines)
                                        "input_metric": meta.get("metric") or (meta["metric_fn"](p["name"])
                                                                               if meta.get("metric_fn") else None),
                                        "closed": closed})
            # A HALTED PROGRAMME IS A MEASURED ZERO (Fluid, 2026-09-28): 0 with the halt date,
            # like the "silent since" flag, not "none available".
            if closed and closed.get("renders_as_zero_since"):
                c.value = 0
                c.comment = Comment(
                    f"0 — PROGRAMME HALTED, silent since {closed['renders_as_zero_since']}. "
                    f"{closed['summary']}\n\nReopen if: {closed['reopen_if']}", "token_metrics")
                continue
            if closed:
                c.value = closed.get("cell_text") or CLOSED_TEXT     # G17-1a: a closure may name its reason on the cell
                c.font = Font(name=FONT, size=10, color="999999", italic=True)
                c.number_format = FMT_TEXT
                c.comment = Comment(
                    f"CLOSED, not missing. {closed['summary']}\n\n"
                    f"What was tried: {closed['what_was_tried']}\n\n"
                    f"Impact: {closed['impact']}\n\n"
                    f"Reopen if: {closed['reopen_if']}\n\n"
                    f"Closed on {closed['closed_on']}. Recorded in config.py UNAVAILABLE.", "token_metrics")
                continue
            # THE TRAJECTORY ROW (theory layer, Phase 1): this cell's own builder, re-run k quarters back (trajectory.py)
            hid = _traj_hid(ws.title, spec)
            orig = getattr(builder, "_orig", builder)
            if ws.title[:2] in HEADLINE_TABS and kind in ("calc", "pull") and not meta.get("trend_of"):
                trajectory.CELL_SPEC[(ws.title, c.coordinate)] = {
                    "header": head, "hid": hid or headline_id(ws.title, head) or trajectory.slug(head),
                    "builder": orig, "fmt": fmt, "metric": meta.get("metric")}
            # a Master cell that IS a reference to an A-tab cell has no row of its own: its trend reads that cell's
            if hid and v != "" and not (ws.title == "Master" and isinstance(v, str) and v.startswith("='")):
                trajectory.register(ws.title, p, head, hid, orig, fmt, meta.get("metric"), c.coordinate)
            metric = meta.get("metric")
            if metric:
                st = data_by_key.get(f"{p['name']}|{metric}")
                if st is not None:
                    band = st.get("confidence")
                    if band in CONFIDENCE_FILL:
                        c.fill = CONFIDENCE_FILL[band]
                        if band == "AMBER" and st.get("why_amber"):
                            c.comment = Comment(f"AMBER — a real number, qualified:\n\n{st['why_amber']}",
                                                "token_metrics")
                    if st["status"] == "manual":
                        c.fill = FILL_MANUAL
                        c.comment = Comment(f"MANUAL OVERRIDE entered_on {st['entered_on']}. {st['note']}", "manual_overrides.csv")
                    elif st["status"] == "stale":
                        c.fill = FILL_STALE
                        c.comment = Comment(f"STALE — {st['note']}", "token_metrics")
                    # A review flag was reaching the Data tab and stopping there, so a figure the
                    # validator would not accept silently still rendered as a plain number on the
                    # tab people actually read. A flagged ZERO is the worst case of that: it looks
                    # exactly like a measured zero.
                    elif st["status"] == "review":
                        c.fill = FILL_REVIEW
                        c.comment = Comment(
                            f"FLAGGED — do not read this as a measured figure. {st['note']}\n\n"
                            f"See the Review Queue tab for the reason, and the Gap Report for what "
                            f"would settle it.", "token_metrics")
            gw = meta.get("gate_window")
            if gw:
                from datetime import timedelta as _td
                applied = config.split_for_window(p["name"], _WINDOWS[gw][0], _WINDOWS[gw][1])
                if _DAY_BY_DAY.get((p["name"], gw)) and not _SHIFT["k"]:
                    # G10 3a: the window's share is applied day by day — said on the cell, never greyed as unconfirmed
                    c.comment = Comment(f"Share applied DAY BY DAY: {_DAY_BY_DAY[(p['name'], gw)]}", "token_metrics")
                elif applied["status"] == "unconfirmed":
                    c.fill = FILL_UNCONFIRMED
                    c.comment = Comment(
                        f"Split for this window is unconfirmed: {applied['why']}. "
                        f"The derived figure is suppressed rather than borrowing today's split.", "token_metrics")
                elif applied["status"] == "paused":
                    c.fill = FILL_PAUSED
            gate = meta.get("gate")
            if gate:
                status = (p.get(gate) or {}).get("status", "n/a")
                if status == "unconfirmed":
                    c.fill = FILL_UNCONFIRMED
                    c.comment = Comment(f"Split unconfirmed — derived figure suppressed until documented in config.py ({gate}).", "token_metrics")
                elif status == "paused" and meta.get("paused_fill"):
                    c.fill = FILL_PAUSED
            if meta.get("threshold") and p.get("buyback_threshold"):
                t = p["buyback_threshold"]
                if t.get("status") != "active":
                    c.fill = FILL_UNCONFIRMED
                    c.comment = Comment(
                        f"SUPPRESSED — this buyback is a SWITCH, not a rate. It activates at "
                        f"${t['threshold_usd_annualised']:,.0f} annualised protocol revenue, and which "
                        f"side of that line the protocol currently sits on is {t.get('status')}. "
                        f"Applying a share to revenue below the threshold does not produce a small "
                        f"buyback — it produces one that does not exist.\n\n{t.get('note', '')}",
                        "token_metrics")
            # THE SHARE IS RIGHT; THE BASE IT IS APPLIED TO MIGHT NOT BE. Distinct from the plain
            # "unconfirmed" gate above (which means the SPLIT itself is not sourced) — this means
            # the split IS sourced, in the protocol's own words, but stated against a quantity
            # ("Net Protocol Surplus", a treasury cash-flow figure) not established to equal the
            # revenue_usd the formula actually multiplies. Grey for the same reason: do not show a
            # number here with the same confidence as a genuinely-derived one.
            if meta.get("base") and p.get("revenue_base_uncertain"):
                b = p["revenue_base_uncertain"]
                if b.get("status") != "confirmed":
                    c.fill = FILL_UNCONFIRMED
                    c.comment = Comment(
                        f"BASE UNCONFIRMED — the split itself IS sourced ({b.get('share_source', 'see config')}), "
                        f"but it is stated as a share of {b.get('basis_stated_by_protocol', 'a different quantity')}"
                        f", which has not been established to equal {b.get('compared_against', 'the revenue figure used here')}"
                        f". Applying a confirmed rate to an unconfirmed base produces a confidently "
                        f"wrong number rather than an obviously missing one.\n\n{b.get('reason', '')}",
                        "token_metrics")
            if meta.get("supply_additive") and supply_additive(p):
                c.fill = FILL_STALE
                c.comment = Comment(
                    "SIGN FLIPPED — this project's 'buyback' ADDS circulating supply, so it is added to "
                    "the issuance side rather than subtracted from it. "
                    f"{(p.get('buyback_is_supply_additive') or {}).get('effect', '')}", "token_metrics")
            sf = meta.get("status_fill")
            if sf:
                status = (p.get(sf) or {}).get("status", "n/a")
                if status == "unconfirmed":
                    c.fill = FILL_UNCONFIRMED
                elif status == "paused":
                    c.fill = FILL_PAUSED
            # PARTIAL INPUT, FLAGGED ON THE CELL ITSELF: the word is in the displayed number, so
            # it cannot be missed by someone who never hovers. The value stays numeric.
            pf = meta.get("partial_fn")
            why = pf(p) if pf else None
            if why:
                c.fill = FILL_STALE
                c.number_format = meta.get("partial_fmt", c.number_format)
                c.comment = Comment(f"PARTIAL — {meta.get('partial_direction', '')}\n\n{why}",
                                    "token_metrics")
            # A PROGRAM ANNOTATION, ON THE CELL ITSELF (2026-09-28): "no inflow since <date>" and,
            # past twice the documented cadence, SILENT. Composes with PARTIAL rather than
            # replacing it; the value stays numeric.
            ff = meta.get("flag_fn")
            flag = ff(p) if ff else None
            if flag:
                suffix, note = flag
                if c.number_format != FMT_TEXT:
                    c.number_format = _suffix_fmt(c.number_format, suffix)
                prior = c.comment.text if c.comment else ""
                c.comment = Comment((prior + "\n\n" if prior else "") + note, "token_metrics")
            # P2c-6: a note the column computes per project (a Δ compared on a declared one basis names its source),
            # opening the comment
            nf = meta.get("note_fn")
            cnote = nf(p) if nf else ""
            if cnote:
                prior = c.comment.text if c.comment else ""
                c.comment = Comment(cnote + ("\n\n" + prior if prior else ""), "token_metrics")
        fc = ws.cell(row=r, column=len(specs) + 1, value=_flags(data_by_key, p["name"], flag_metrics))
        fc.font = Font(name=FONT, size=9, color="C00000")
        r += 1
    ws.freeze_panes = ws.cell(row=start_row + 1, column=3)
    return r


def _a4_iss_metric(name: str) -> str:
    """G10 1 (Jake 2026-10-21): the issuance A4's net-change, Δ and %-of-supply columns read. gross_issuance_tokens,
    except where the supply is PRE-MINTED and the minted figure is suppressed by declaration (GEODNET: nothing is minted,
    config issuance_derivation) — there the MEASURED release from the mining wallets (config.issuance_basis =
    pool_release_tokens, the primary Jake chose 2026-09-28) is what enters circulation, and the Data row names it."""
    p = config.PROJECT_BY_NAME.get(name) or {}
    # G11 C7 (Jake's 10-21 run: Hyperliquid's Master read "0 — fixed supply" while 11 A4 cells were blocked on a 29-day
    # gross_issuance_tokens): ONE BASIS — a pre-minted supply whose minted figure is declared n/a (Hyperliquid: "PRE-MINTED
    # TOKEN, ZERO ISSUANCE BY CONSTRUCTION") reads its release too, as GEODNET does
    if config.issuance_basis(name) == "pool_release_tokens" and (
            (p.get("issuance_derivation") or {}).get("suppressed")
            or config.not_applicable_reason(name, "gross_issuance_tokens")):
        return "pool_release_tokens"
    return "gross_issuance_tokens"


def _stitch_breaks(name: str, metric: str) -> list[tuple[str, str]]:
    """[(day, why)] — the stitch of a DECLARED handover whose composition_change says the quantity steps (G12 C6:
    Hyperliquid's buyback, DefiLlama's revenue model before the first Assistance Fund read, the Fund's own balance
    from it), on the series itself or on the one it is derived from (actual_buyback_usd from actual_buyback_tokens).
    The day is the first row off the first leg in this build's series. A benign handover ("none …") is one series."""
    out = []
    for m in dict.fromkeys((metric, metric.replace("_usd", "_tokens"))):
        decl = config.declared_handover(name, m) or {}
        cc = str(decl.get("composition_change", "none")).strip().lower()
        pts = tuple(decl.get("ordered_points") or ())
        g = _LAST_GROUPS.get((name, m))
        if not decl or cc.startswith("none") or len(pts) < 2 or g is None or g.empty:
            continue
        first_leg = g["source"].astype(str).map(lambda s: trajectory.basis_of(m, s)) == pts[0]
        later, earlier = g[~first_leg], g[first_leg]
        if later.empty or earlier.empty:
            continue
        out.append((str(pd.to_datetime(later["date"]).min().date()),
                    f"{m}: {decl['composition_change']}"))
    return out


def _comparison_lo(b: str) -> str | None:
    """The first day a Δ comparison against window `b` reads (m1: twice the short window back from Q0's end)."""
    lo = _WINDOWS.get(b, (None,))[0] if b in _WINDOWS else None
    end = _WINDOWS.get("q0", (None, None))[1]
    if b == "m1" and end:
        lo = str((pd.Timestamp(end) - pd.Timedelta(days=2 * GLOBALS["short_days"])).date())
    return lo


def _in_comparison(day: str, b: str) -> bool:
    lo, end = _comparison_lo(b), _WINDOWS.get("q0", (None, None))[1]
    return bool(lo and end and lo <= day <= end)


def _window_span(w: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(first, last) day of a Δ window as aggregate() sums it — (start, end]: now / m1 the short windows ending at Q0's
    end, else _WINDOWS."""
    end = _WINDOWS.get("q0", (None, None))[1]
    if w in ("now", "m1") and end:
        k, e = int(GLOBALS["short_days"]), pd.Timestamp(end)
        e = e - pd.Timedelta(days=k) if w == "m1" else e
        return e - pd.Timedelta(days=k - 1), e
    if w in _WINDOWS:
        return pd.Timestamp(_WINDOWS[w][0]) + pd.Timedelta(days=1), pd.Timestamp(_WINDOWS[w][1])
    return None


def _every_stitch(name: str, metric: str) -> list[tuple[str, str]]:
    """[(day, why)] — EVERY day this build's series steps from one leg of a declared composition-change handover to
    another (_stitch_breaks names the first only; Pendle's D4-5 handover steps again at the epoch in progress, whose
    DefiLlama days sit inside Q0 and the 'now' window). Read only for a series declaring a one-basis comparison
    (config.COMPARISON_BASIS, P2c-6)."""
    out = []
    for m in dict.fromkeys((metric, metric.replace("_usd", "_tokens"))):
        decl = config.declared_handover(name, m) or {}
        cc = str(decl.get("composition_change", "none")).strip().lower()
        g = _LAST_GROUPS.get((name, m))
        if not decl or cc.startswith("none") or len(tuple(decl.get("ordered_points") or ())) < 2 or g is None \
                or g.empty:
            continue
        s = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date")
             .drop_duplicates("date", keep="last"))
        legs = s["source"].astype(str).map(lambda x: trajectory.basis_of(m, x)).tolist()
        for day, prev, now in zip(s["date"].iloc[1:], legs, legs[1:]):
            if now != prev:
                out.append((str(day.date()), f"{m}: {decl['composition_change']}"))
    return out


def _one_basis_comparison(name: str, mm: str, a: str, b: str) -> tuple[dict, str, str, str] | None:
    """P2c-6 (config.COMPARISON_BASIS): (declaration, stitch day, stitch why, problem) where the Δ of `mm` between windows
    a and b crosses a DECLARED composition-change stitch (_every_stitch) and the series declares a one-basis
    comparison; problem is "" when the declared series holds rows of its one basis in both windows, else the reason it
    cannot be formed. None where no declared stitch is inside the comparison, or no comparison basis is declared, or a
    source reclassification of `mm` is inside it too (that stays "n/a — SERIES BREAK")."""
    cb = config.comparison_basis(name, mm)
    if not cb or _SHIFT["k"]:
        return None
    if any(_in_comparison(day, b) for day, _w in config.SOURCE_RECLASSIFICATIONS.get((name, mm), ())):
        return None
    hit = next(((day, why) for day, why in _every_stitch(name, mm) if _in_comparison(day, b)), None)
    if hit is None:
        return None
    cm, problems = cb["metric"], []
    for day, _why in (*config.SOURCE_RECLASSIFICATIONS.get((name, cm), ()), *_stitch_breaks(name, cm)):
        if _in_comparison(day, b):
            problems.append(f"{cm} itself has a SERIES BREAK {day} inside this comparison")
    g = _LAST_GROUPS.get((name, cm))
    for w in (a, b):
        span = _window_span(w)
        rows = None
        if g is not None and not g.empty and span:
            d = pd.to_datetime(g["date"]).dt.normalize()
            rows = g[(d >= span[0]) & (d <= span[1])]
        if rows is None or rows.empty:
            problems.append(f"no {cm} row in {w.upper()}")
            continue
        bases = sorted({trajectory.basis_of(cm, s) for s in rows["source"].astype(str).unique()})
        if bases != [cb["basis"]]:
            problems.append(f"{cm} in {w.upper()} is not one basis ({', '.join(bases)}; declared {cb['basis']})")
    return cb, hit[0], hit[1], "; ".join(problems)


def _one_basis_note(name: str, mm: str, a: str, b: str) -> str:
    """The cell comment of a Δ compared on a declared one basis (P2c-6): it names the source and says the basis differs
    from the headline's; where the comparison cannot be formed, why. '' otherwise."""
    got = _one_basis_comparison(name, mm, a, b)
    if got is None:
        return ""
    cb, day, _why, problem = got
    if problem:
        return (f"Not compared: the headline {mm} series steps at the declared handover ({day}, inside this comparison) "
                f"and the one-basis comparison on {cb['source']} ({cb['metric']}) cannot be formed — {problem}. "
                f"{cb['reason']}.")
    return (f"Compared on ONE basis: {cb['source']}, both windows ({cb['metric']}; {a.upper()} vs {b.upper()}, per "
            f"covered day). This basis differs from the headline {mm} figure ({cb['headline_basis']}), whose series "
            f"steps at the declared handover on {day}, inside this comparison. {cb['reason']}.")


def _trajectory(R: Refs, metric: str, label: str, fmt=FMT_PCT):
    """Δ30d, Δ3m, Δ6m, Δ9m columns for a metric (now vs prior 30d; Q0 vs Q1/Q2/Q3).

    `metric` may be a function of the project name (A4's burn is per project). P2c-6: a comparison that crosses a
    declared composition-change stitch is made on the series' declared one basis (config.COMPARISON_BASIS) where that
    series holds both windows — the same per-covered-day rates and coverage rules — and the cell comment says so."""
    m = metric if callable(metric) else (lambda _n: metric)  # noqa: E731

    def d(a: str, b: str):
        # A FLOW compares PER-DAY RATES over each window's own covered days, BLOCKED under a week
        # (2026-09-29): a Q0 the series covered 42 days of against a full Q1 read as a -53% fall.
        def cell(r, p):
            mm = m(p["name"])
            # P2c-6: ONE BASIS ACROSS A DECLARED STITCH (Pendle's fees: DefiLlama pendle-v2 daily, both windows)
            one = _one_basis_comparison(p["name"], mm, a, b)
            if one is not None:
                cb, day, why, problem = one
                if problem:
                    # the reason first; the handover's own wording is on the comment
                    return (f"n/a — SERIES BREAK {day} (declared handover of {why.split(':')[0]}, inside this "
                            f"comparison); not formed on {cb['source']}: {problem}")[:250]
                cm = cb["metric"]
                return _coverage_guard(R, r, [(f"{a.upper()} {cb['source']}", cm, a),
                                              (f"{b.upper()} {cb['source']}", cm, b)],
                                       f"{_flow_rate(R, r, cm, a)}/{_flow_rate(R, r, cm, b)}-1")
            # A DECLARED RECLASSIFICATION INSIDE THE COMPARISON IS NOT A CHANGE (fix round 2, R4: Plume's chain TVL
            # Δ9m read −96% across DefiLlama's moves of RWA out of Plume Mainnet's chain TVL). The trajectory already
            # marks the SERIES BREAK; the Δ columns now say it instead of a number
            for day, why in (*config.SOURCE_RECLASSIFICATIONS.get((p["name"], mm), ()), *_stitch_breaks(p["name"], mm)):
                if _in_comparison(day, b):
                    return f"n/a — SERIES BREAK {day}: {why[:120]} (inside this comparison)"
            if (config.METRICS.get(mm) or {}).get("kind") != "flow":
                return delta(R.D(r, mm, a), R.D(r, mm, b))
            return _coverage_guard(R, r, [(a.upper(), mm, a), (b.upper(), mm, b)],
                                   f"{_flow_rate(R, r, mm, a)}/{_flow_rate(R, r, mm, b)}-1")
        return cell

    def note(a: str, b: str):
        return lambda p: _one_basis_note(p["name"], m(p["name"]), a, b)
    return [
        (f"{label} Δ30d", d("now", "m1"), fmt, "calc", False, {"note_fn": note("now", "m1")}),
        (f"{label} Δ3m", d("q0", "q1"), fmt, "calc", False, {"note_fn": note("q0", "q1")}),
        (f"{label} Δ6m", d("q0", "q2"), fmt, "calc", False, {"note_fn": note("q0", "q2")}),
        (f"{label} Δ9m", d("q0", "q3"), fmt, "calc", False, {"note_fn": note("q0", "q3")}),
    ]


def _lit(field):
    return lambda r, p: p.get(field, "")


def _txt(fn):
    return fn


def _mcap_date_note(data_by_key: dict, name: str) -> str:
    """'as of <date>' when the market cap's newest row is not dated the spot price's day, else '' (G5 item 8d)."""
    mc = (data_by_key.get(f"{name}|market_cap_usd") or {}).get("latest_date") or ""
    px = (data_by_key.get(f"{name}|price_usd") or {}).get("latest_date") or ""
    if mc and px and str(mc)[:10] != str(px)[:10]:
        return f"as of {str(mc)[:10]} (spot price {str(px)[:10]})"
    return ""


def _fees_q0_col(R: Refs) -> tuple:
    """"Fees Q0 ($)": fees_usd, or — for a project whose only fee series is its own chain's gas
    (config.MASTER_FEES_OVERRIDE, G11 C8) — that series, labelled on the number. ONE spec on Master and on every
    A-tab: Master's cell is a reference to the A-tab's (cross_tab.MASTER_SOURCES), so an override on Master alone never
    reached the cell (G12 B2: Jake's 10-21 workbook still read the DefiLlama gap for World Mobile)."""
    def metric(n):
        return config.MASTER_FEES_OVERRIDE.get(n, ("fees_usd",))[0]
    return ("Fees Q0 ($)", lambda r, p: pull(R.D(r, metric(p["name"]), "q0")), FMT_USD, "pull", False,
            {"metric_fn": metric,
             "fmt_fn": lambda n: (f'$#,##0" ({config.MASTER_FEES_OVERRIDE[n][1]})"' if n in config.MASTER_FEES_OVERRIDE
                                  else None)})


def write_master(ws, R: Refs, data_by_key: dict):
    # THE TITLE STATES THE SCOPE. "all projects" on a 16-project workbook is the same class of
    # untruth as a stale cell presented as fresh: it tells the reader nothing is missing.
    _n, _all = len(scoped_projects()), len(PROJECTS)
    _heading = ("Master — all projects" if _n == _all
                else f"Master — {_n} of {_all} projects (portfolio scope; --all renders the rest)")
    _title(ws, _heading, "All live figures over the comparison window (Q0 = trailing 90 days by default, set on Config & Sources). "
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
        # THE DATE BESIDE THE MARKET CAP WHEN ITS INPUTS DIFFER (Round G5 item 8d, Jake 2026-10-19): market cap is price x
        # the circulating used on one day; when that day is not the spot price's, the cell says which day it is
        ("Market cap date (shown only when not the spot price's date)",
         lambda r, p: _mcap_date_note(data_by_key, p["name"]), FMT_TEXT, "text"),
        # THE SUPPLY ACTUALLY USED (item 9, 2026-10-11): the figure market cap multiplies; CoinGecko's beside it
        ("Circulating supply (used)", lambda r, p: pull(_circ(R, r, p)), FMT_NUM, "pull", False, {"metric": "circulating_supply_used"}),
        ("Circulating supply — CoinGecko (cross-check)", lambda r, p: pull(R.D(r, "circulating_supply", "now")), FMT_NUM, "pull", False, {"metric": "circulating_supply"}),
        ("Market cap — CoinGecko ($, cross-check: price x CoinGecko's circulating)",
         lambda r, p: pull(R.D(r, "market_cap_usd_coingecko", "now")), FMT_USD, "pull", False,
         {"metric": "market_cap_usd_coingecko"}),
        # G12 B1 (Jake 2026-10-21): CoinGecko's 30-day spot volume beside the market cap, and turnover = that volume ÷
        # the market cap — how much of the float trades in a month (liquidity, not demand for the token's cash flows)
        ("Trading volume, 30d ($) — CoinGecko spot, all venues",
         lambda r, p: pull(R.D(r, "volume_usd", "now")), FMT_USD, "pull", False, {"metric": "volume_usd"}),
        ("Turnover = 30d volume ÷ market cap (x)",
         lambda r, p: calc(f"{R.D(r, 'volume_usd', 'now')}/{R.D(r, 'market_cap_usd', 'now')}"), FMT_X, "calc", False,
         {"metric": "volume_usd"}),
        ("FDV ($)", lambda r, p: pull(R.D(r, "fdv_usd", "now")), FMT_USD, "pull", False, {"metric": "fdv_usd"}),
        _fees_q0_col(R),
        ("Revenue Q0 ($)", lambda r, p: pull(R.D(r, config.revenue_headline_metric(p["name"]), "q0")), FMT_USD, "pull",
         False, {"metric_fn": config.revenue_headline_metric}),
        # G11 C4 (Jake's 10-21 run: Chainlink's Master TVL n/a — it is A1 but not a chain, and protocol_tvl_usd $2.0B is
        # stored): chain TVL for the A1 names that ARE chains (config is_chain), protocol TVL otherwise
        # (the header keeps its text: the trend and Trajectory ids are built from it — "A1 names" now means chains)
        ("TVL ($) — chain for A1 names, protocol otherwise",
         lambda r, p: pull(R.D(r, _master_tvl_metric(p), "now")), FMT_USD, "pull", False,
         {"metric_fn": lambda n: _master_tvl_metric(config.PROJECT_BY_NAME[n])}),
        ("Implied buyback Q0 ($) = revenue × the split that applied to this window",
         lambda r, p: gated(R.C(r, WINDOW_STATUS_COL["q0"]), f"{R.D(r, 'revenue_usd', 'q0')}*{R.C(r, WINDOW_SHARE_COL['q0'])}", R.C(r, WINDOW_SHARE_COL["q0"])),
         FMT_USD, "calc", False, {"gate_window": "q0"}),
        ("Actual buyback Q0 ($) — observed", lambda r, p: pull(R.D(r, "actual_buyback_usd", "q0")), FMT_USD, "pull", False, {"metric": "actual_buyback_usd"}),
        ("Gross burn Q0 (tokens)", lambda r, p: pull(R.D(r, config.a1_burn_metric(p["name"]), "q0")), FMT_NUM, "pull", False, {"metric_fn": config.a1_burn_metric}),
        ("Gross issuance Q0 (tokens)", lambda r, p: _fixed_supply_text(p) or pull(R.D(r, "gross_issuance_tokens", "q0")),
         FMT_NUM, "pull", False, {"metric": "gross_issuance_tokens"}),
        ("NET SUPPLY CHANGE Q0 (tokens) = issuance − burn",
         lambda r, p: _zero_issuance(R, r, p, f"-{R.D(r, config.a1_burn_metric(p['name']), 'q0')}")
         or _net_guarded(R, r, p, _basis_iss(R, p), lambda r_, p_, w="q0": R.D(r_, config.a1_burn_metric(p_["name"]), w)),
         FMT_NUM, "calc", True),
        # ONE EXPRESSION (_net_inflation, A4's headline and A1's net issuance %): Master shows the same figure whether or
        # not the project has an A4 row (Phase 2 amendment 1 took the fee-burn chains off A4)
        ("Net supply change, annualised % of circulating",
         lambda r, p: _net_inflation(R, r, p), FMT_PCT, "calc", True),
        ("Issuer releases Q0 (tokens) — pre-minted supply released into circulation, NOT issuance",
         lambda r, p: pull(R.D(r, "pool_release_tokens", "q0")) if _premint(p) else "", FMT_NUM, "pull", False,
         {"metric_fn": lambda n: "pool_release_tokens" if config.issuance_basis(n) == "pool_release_tokens" else None}),
        ("Fees ÷ issuance ($, Q0; issuance at 90d avg price)",
         lambda r, p: _no_issuance_text(p) or _coverage_guard(R, r, [("fees", "fees_usd", "q0"), ("issuance", "gross_issuance_tokens", "q0")],
                                      f"{_flow_rate(R, r, 'fees_usd')}/({_flow_rate(R, r, 'gross_issuance_tokens')}*{R.D(r, 'price_usd', 'q0')})"), FMT_X, "calc"),
        ("Fees ÷ FDV (annualised)", lambda r, p: calc(f"{_annualise(R, r, p, 'fees_usd', R.D(r, 'fees_usd', 'q0'))}/{R.D(r, 'fdv_usd', 'now')}"), FMT_PCT, "calc"),
        ("Buyback % of supply (annualised, implied, split as at this window)",
         lambda r, p: gated(R.C(r, WINDOW_STATUS_COL["q0"]), f"{_annualise(R, r, p, 'revenue_usd', R.D(r, 'revenue_usd', 'q0'))}*{R.C(r, WINDOW_SHARE_COL['q0'])}/{R.D(r, 'price_usd', 'q0')}/{R.D(r, 'circulating_supply', 'now')}", R.C(r, WINDOW_SHARE_COL["q0"])),
         FMT_PCT, "calc", False, {"gate_window": "q0"}),
        *_trajectory(R, "fees_usd", "Fees"),
        ("Latest data date (core series)", lambda r, p: max([data_by_key.get(f"{p['name']}|{m}", {}).get("latest_date", "") or "" for m in ("price_usd", "fees_usd", "revenue_usd", "tvl_usd")] or [""]), FMT_TEXT, "text"),
        ("Notes", lambda r, p: p.get("notes", ""), FMT_TEXT, "text"),
    ]
    specs = [_master_ref(s) for s in specs]
    _write_table(ws, R, scoped_projects(), specs, data_by_key,
                 ["price_usd", "fees_usd", "revenue_usd", "circulating_supply", "gross_burn_tokens", "gross_issuance_tokens", "actual_buyback_usd"],
                 key_cols=_key_cols(specs, "Gross burn Q0 (tokens)", "Gross issuance Q0 (tokens)"))
    _assert_master_refs(ws)
    _set_widths(ws, {"A": 16, "B": 8, "C": 10, "D": 6, "E": 9, "F": 10, "G": 10, **{get_column_letter(i): 14 for i in range(8, 32)}, "AF": 12, "AG": 60, "AH": 60})


class CrossTabMismatch(RuntimeError):
    """A Master column that summarises an A-tab figure is not a reference to that A-tab cell."""


def _master_ref_for(head: str, p: dict) -> str | None:
    """'=\'<A-tab>\'!<cell>' for a Master column that summarises an A-tab figure the project has (cross_tab
    .MASTER_SOURCES, the project's own archetype tabs first), else None."""
    import cross_tab                                      # noqa: PLC0415
    sources = cross_tab.sources_for(head, p["name"])
    if not sources:
        return None
    hit = cross_tab.resolve(sources, p.get("archetypes") or [], _TAB_CELLS, p["name"])
    return f"='{hit[0]}'!{hit[1]}" if hit else None


def _master_ref(spec: tuple) -> tuple:
    """The spec with its builder replaced by the A-tab reference where one resolves; its own formula otherwise (a
    project with no A-tab row for that figure). The confidence tag (meta metric) stays."""
    head, builder = spec[0], spec[1]

    def build(r, p, _b=builder, _h=head):
        return _master_ref_for(_h, p) or _b(r, p)
    build._orig = builder                       # the trajectory re-runs Master's own formula (trajectory.py)
    return (head, build, *spec[2:])


def _assert_master_refs(ws) -> None:
    """THE BUILD REFUSES A MASTER THAT DISAGREES WITH ITS A-TAB BY CONSTRUCTION (Jake, 2026-10-11, item 16): every
    mapped Master cell whose project has the A-tab figure must be exactly that reference. The value-level check
    (cross_tab.check_workbook) runs in the tests on every real dump and on demand on today's store."""
    import cross_tab                                      # noqa: PLC0415
    hr = next((r for r in range(1, 12) if ws.cell(r, 1).value == "Project"), None)
    heads = {c: ws.cell(hr, c).value for c in range(1, ws.max_column + 1)}
    bad = []
    for r in range(hr + 1, ws.max_row + 1):
        name = ws.cell(r, 1).value
        if not name:
            break
        p = config.PROJECT_BY_NAME.get(name) or {"name": name}
        for c, h in heads.items():
            if h in cross_tab.MASTER_SOURCES:
                want = _master_ref_for(h, p)
                got = ws.cell(r, c).value
                # a closed / halted figure is written by _write_table over the formula — the A-tab cell carries
                # the same treatment, so only a FORMULA that is not the reference is a mismatch
                if want and isinstance(got, str) and got.startswith("=") and got != want:
                    bad.append(f"{name} | {h}: Master reads {got[:120]} — the A-tab cell is {want}")
    if bad:
        raise CrossTabMismatch("Master disagrees with the A-tabs by construction:\n  " + "\n  ".join(bad))


# ===== CP — THE COMPARABILITY LAYER (Jake 2026-10-12, Phase 2: A3/A4 items 1, 3-5, amendments 1-5, LITMUS A-E) =====
# Raw metrics only: no score, band or leaderboard. One supply definition everywhere — total / max, circulating (our
# convention: treasury, team / investor unvested, foundation and operating reserves and burned are out; staked is in),
# TRADEABLE FLOAT = circulating − staked / locked − protocol / treasury-held (zero inside circulating by the convention,
# so float is A2's free float). Every retirement and dilution rate on BOTH bases; the five outflow categories (config
# OUTFLOW_CATEGORIES); holder value funding-aware (config HOLDER_VALUE); attributes from phase2_attributes.json.
CP_FLOAT_HEAD = ("TRADEABLE FLOAT = circulating − staked/locked − protocol/treasury-held (tokens) — protocol/treasury-"
                 "held is already out of circulating by the convention, so this is A2's free float")
CP_HVY_HEAD = ("HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid bribes to voters (annualised) ÷ "
               "market cap — treasury- or emissions-funded buybacks are NOT credited (LITMUS A); fee-market burns are on "
               "A1 net issuance")
CP_NHY_HEAD = ("NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e at each day's price, annualised) ÷ "
               "market cap")
CP_SUBSIDY_HEAD = "SUBSIDY COVER = revenue ÷ $ supply-side incentives (b), per covered day"
CP_SUBSIDY_BC_HEAD = "Subsidy cover incl. demand-side = revenue ÷ $ (b + c), per covered day"
CP_DILUTION_CATS = ("a", "b", "c", "d", "e")
CP_SUPPLIER_HEAD = ("Supplier payout per $ of fees = (fees to suppliers + incentive $ to suppliers (b)) ÷ fees, per "
                    "covered day (PROPOSED)")


def _subsidy_dates_fmt(name: str):
    """The covered dates ON the number where subsidy cover is computed over the common complete days (G13 6)."""
    info = SUBSIDY_COMMON.get(name) or {}
    if (name, "b") not in config.SUBSIDY_COMPLETE_FROM or int(info.get("days") or 0) < RATE_MIN_DAYS:
        return None
    return (f'0.00"x over {info["first"]}..{info["last"]} ({info["days"]} common days; (b) complete from '
            f'{info["from"]})"')


def _cp_revenue_metric(p: dict) -> str:
    """Subsidy cover's numerator: customer revenue where the project has the series (archetype 2's demand), else fees."""
    return "customer_revenue_usd" if "customer_revenue_usd" in config.metrics_for_project(p) else "fees_usd"


def _master_tvl_metric(p: dict) -> str:
    """Master's TVL: the chain's own (tvl_usd) for an A1 name that is a chain, the protocol's (protocol_tvl_usd)
    otherwise — Chainlink is A1 and no chain (G11 C4)."""
    return "tvl_usd" if 1 in p["archetypes"] and p.get("is_chain") else "protocol_tvl_usd"


def _cp_cats(p: dict, cats=CP_DILUTION_CATS) -> list[str]:
    return [c for c in cats if config.outflow_metrics(p["name"], c)]


def _cp_lock(R: Refs, r, p: dict) -> str:
    """Staked / locked inside circulating for CP: the declared stake series (config.CP_STAKE_METRIC), else A2's lock."""
    m = config.CP_STAKE_METRIC.get(p["name"])
    return R.D(r, m, "now") if m else _ff_lock(R, r, p)


def _cp_float_reason(p: dict) -> str | None:
    return None if p["name"] in config.CP_STAKE_METRIC else _free_float_reason(p)


def _cp_float(R: Refs, r, p: dict) -> str:
    c, lk = _circ(R, r, p), _cp_lock(R, r, p)
    return f"IF(AND(ISNUMBER({c}),ISNUMBER({lk})),{c}-{lk},{NA})"


def _cp_inner(f):
    return f[1:] if isinstance(f, str) and f.startswith("=") else None


def _q0_days(data_by_key: dict, name: str, metric: str) -> float | None:
    """A series' Q0 covered days from its Data row, None when nothing is stored (Round G 2.7: OUTFLOW_INCOMPLETE lifts)."""
    row = data_by_key.get(f"{name}|{metric}") or {}
    v = row.get("q0_covered_days")
    if v is None or (isinstance(v, float) and pd.isna(v)) or not row.get("n_points"):
        return None
    return float(v)


def _cp_cols(R: Refs, data_by_key: dict) -> list[tuple]:
    circ = lambda r, p: _circ(R, r, p)  # noqa: E731
    flt = lambda r, p: _cp_float(R, r, p)  # noqa: E731
    mc = lambda r: R.D(r, "market_cap_usd", "now")  # noqa: E731

    def ann(r, p, m):
        return _annualise(R, r, p, m, R.D(r, m, "q0"))

    def both(label, legs_fn, num_fn, zero_fn=None, blank_fn=None, tid=None):
        """One rate as two columns: ÷ circulating and ÷ tradeable float."""
        def mk(den_fn, basis):
            def build(r, p):
                if blank_fn and blank_fn(p):
                    return ""
                if zero_fn and zero_fn(p):
                    return "=0"
                if basis == "float" and _cp_float_reason(p):
                    return _cp_float_reason(p)
                d = den_fn(r, p)
                # D7-2: a SUM of separately annualised flows over a stock — rule 9's net / ratio test does not apply
                return _coverage_guard(R, r, legs_fn(p), f"IF(ISNUMBER({d}),({num_fn(r, p)})/({d}),{NA})",
                                       same_span=False)
            return build
        return [(f"{label} ÷ circulating", mk(circ, "circ"), FMT_PCT, "calc"),
                (f"{label} ÷ tradeable float", mk(flt, "float"), FMT_PCT, "calc")]

    def has_bb(p):
        return "retired_buyback_tokens" in config.metrics_for_project(p) or "actual_buyback_tokens" in \
            config.metrics_for_project(p)

    def burn_m(p):
        return config.a1_burn_metric(p["name"])

    rates = []
    rates += both("Retirement rate (RETIRED buyback tokens, burned + held, annualised)",
                  lambda p: [("buyback", "actual_buyback_tokens", "q0")],
                  lambda r, p: ann(r, p, "retired_buyback_tokens"),
                  zero_fn=_retirement_zero, blank_fn=lambda p: not has_bb(p) and not _retirement_zero(p))
    rates += both("Burn rate (every burn, fee-market included — A1's basis, annualised)",
                  lambda p: [("burn", burn_m(p), "q0")],
                  lambda r, p: ann(r, p, burn_m(p)),
                  blank_fn=lambda p: burn_m(p) not in config.metrics_for_project(p))
    for c in CP_DILUTION_CATS:
        nm = config.OUTFLOW_CATEGORY_NAMES[c]
        rates += both(f"Dilution ({c}) {nm} (annualised)",
                      lambda p, c=c: [(f"outflow {c}", f"outflow_{c}_tokens", "q0")],
                      lambda r, p, c=c: ann(r, p, f"outflow_{c}_tokens"),
                      blank_fn=lambda p, c=c: not config.outflow_metrics(p["name"], c))
    rates += both("TOTAL DILUTION = outflows a+b+c+d+e (annualised)",
                  lambda p: [(f"outflow {c}", f"outflow_{c}_tokens", "q0") for c in _cp_cats(p)],
                  lambda r, p: "+".join(ann(r, p, f"outflow_{c}_tokens") for c in _cp_cats(p)),
                  blank_fn=lambda p: not _cp_cats(p))

    outflow_usd = []
    for c in ("a", "b", "c", "d", "e"):
        nm = config.OUTFLOW_CATEGORY_NAMES[c]
        outflow_usd.append((f"Outflow ({c}) {nm} — $ a year (each day's tokens x that day's price, annualised)",
                            lambda r, p, c=c: "" if not config.outflow_metrics(p["name"], c) else _coverage_guard(
                                R, r, [(f"outflow {c}", f"outflow_{c}_usd", "q0")], ann(r, p, f"outflow_{c}_usd")),
                            FMT_USD, "calc"))
    outflow_usd.append(("Outflow categories — basis and what has no series yet (config OUTFLOW_CATEGORIES)",
                        lambda r, p: "; ".join(x for x in [
                            ", ".join(f"({c}) {'+'.join(config.outflow_metrics(p['name'], c))}"
                                      for c in config.OUTFLOW_CATEGORY_NAMES if config.outflow_metrics(p["name"], c)),
                            (config.OUTFLOW_CATEGORIES.get(p["name"]) or {}).get("basis", ""),
                            ("PENDING — " + (config.OUTFLOW_CATEGORIES.get(p["name"]) or {})["pending"])
                            if (config.OUTFLOW_CATEGORIES.get(p["name"]) or {}).get("pending") else ""] if x)
                        or ("no outflow category assigned" if p["name"] == "GEODNET"
                            else "no outflow category assigned"), FMT_TEXT, "text"))

    def subsidy(cats):
        def build(r, p):
            got = [c for c in cats if config.outflow_metrics(p["name"], c)]
            if "b" not in got:
                # NEVER A BLANK (alignment round C3): say what the project's outflows are instead
                held = [c for c in config.OUTFLOW_CATEGORY_NAMES if config.outflow_metrics(p["name"], c)]
                return ("n/a — no supply-side incentive (b) series on file; outflows held: "
                        + (", ".join(f"({c}) {config.OUTFLOW_CATEGORY_NAMES[c]}" for c in held) or "none")
                        + " — see the outflow basis column")
            rv = _cp_revenue_metric(p)
            # V1: a denominator known to miss part of what is paid blocks the ratio (unequal coverage), named
            gaps = config.outflow_incomplete(p["name"], got, q0_days=lambda m, n=p["name"]: _q0_days(data_by_key, n, m),
                                             window=int(GLOBALS["period_days"]))
            if gaps and (p["name"], "b") in config.SUBSIDY_COMPLETE_FROM and got == ["b"]:
                # G13 6: per covered day, over the days every leg covers once (b) is complete — never lost to BLOCKED
                info = SUBSIDY_COMMON.get(p["name"]) or {}
                if int(info.get("days") or 0) >= RATE_MIN_DAYS:
                    return calc(f"{R.D(r, 'subsidy_cover_num_usd', 'q0')}/{R.D(r, 'subsidy_cover_den_usd', 'q0')}")
                frm = info.get("from") or config.SUBSIDY_COMPLETE_FROM[(p["name"], "b")]["declared"]
                valid = (pd.Timestamp(frm) + pd.Timedelta(days=RATE_MIN_DAYS - 1)).date()
                return (f"n/a — NEEDS HISTORY: the (b) denominator is complete from {frm} — "
                        f"{int(info.get('days') or 0)} common day(s) of the {RATE_MIN_DAYS} a per-day ratio needs; "
                        f"valid from {valid}")
            if gaps:
                return ("BLOCKED — unequal coverage: the incentive denominator is incomplete — " + "; ".join(gaps)
                        + f" (numerator {rv}, whole)")
            legs = [("revenue", rv, "q0")] + [(f"outflow {c}", f"outflow_{c}_usd", "q0") for c in got]
            den = "+".join(_flow_rate(R, r, f"outflow_{c}_usd") for c in got)
            return _coverage_guard(R, r, legs, f"{_flow_rate(R, r, rv)}/({den})")
        return build

    def hv_has(p):
        return "holder_value_revenue_usd" in config.metrics_for_project(p)

    def hvy_expr(r, p):
        v = R.D(r, "holder_value_revenue_usd", "q0")
        return f"IF(AND(ISNUMBER({v}),ISNUMBER({mc(r)})),{_annualise(R, r, p, 'holder_value_revenue_usd', v)}/{mc(r)},{NA})"

    def hvy(r, p):
        if not hv_has(p):
            return "n/a — no buyback programme and no declared payout to holders"
        return _coverage_guard(R, r, [("holder value", "holder_value_revenue_usd", "q0")], hvy_expr(r, p))

    def hv_fees_only(r, p):
        if "holder_value_fees_only_usd" not in config.metrics_for_project(p):
            return ""
        v = R.D(r, "holder_value_fees_only_usd", "q0")
        return _coverage_guard(R, r, [("holder value, fees only", "holder_value_fees_only_usd", "q0")],
                               f"IF(AND(ISNUMBER({v}),ISNUMBER({mc(r)})),"
                               f"{_annualise(R, r, p, 'holder_value_fees_only_usd', v)}/{mc(r)},{NA})")

    def hv_other(r, p):
        if "holder_value_other_usd" not in config.metrics_for_project(p):
            return ""
        v = R.D(r, "holder_value_other_usd", "q0")
        return calc(f"IF(ISNUMBER({v}),{_annualise(R, r, p, 'holder_value_other_usd', v)}/{mc(r)},0)")

    def nhy(r, p):
        if not hv_has(p):
            return "n/a — no holder value yield"
        cats = _cp_cats(p)
        dil = "+".join(ann(r, p, f"outflow_{c}_usd") for c in cats) or "0"
        legs = [("holder value", "holder_value_revenue_usd", "q0")] + \
            [(f"outflow {c}", f"outflow_{c}_usd", "q0") for c in cats]
        out = _coverage_guard(R, r, legs, f"({hvy_expr(r, p)})-({dil})/{mc(r)}")[1:]
        # NAME THE STARVED LEG (item 24, Jake's run on 3500042): an outflow with no value in the window says which
        # and why (its Data note), never a bare n/a
        for c in reversed(cats):
            q = R.D(r, f"outflow_{c}_usd", "q0")
            note = R.D(r, f"outflow_{c}_usd", "note")
            out = (f'IF(NOT(ISNUMBER({q})),"n/a — outflow ({c}) = {"+".join(config.outflow_metrics(p["name"], c))} '
                   f'has no value in this window: "&'
                   f'IFERROR(IF({note}="","no rows stored",{note}),"no Data row"),{out})')
        return "=" + out

    def funding_text(p):
        h = config.HOLDER_VALUE.get(p["name"]) or {}
        parts = [f"{a}..{b or 'now'}: {f} — {w}" for a, b, f, w in h.get("funding") or ()]
        if h.get("distributed_extra"):
            parts.append(f"credited payout to holders: {'+'.join(h['distributed_extra'])} ({h.get('extra_why', '')})")
        if not parts and hv_has(p):
            parts.append("revenue (default: the programme's documented base)")
        return "; ".join(parts)

    vyield = next(s for s in _a1_headline(R) if s[0].startswith("VALIDATOR STAKING YIELD"))[1]
    tyield = _token_yield(R, data_by_key)[1]

    def whole(yfn, lock_fn):
        def build(r, p):
            y = _cp_inner(yfn(r, p))
            lk = lock_fn(r, p)
            if y is None or lk is None:
                return ""
            return calc(f"({y})*({lk})/({circ(r, p)})")
        return build

    def v_lock(r, p):
        if not config.VALIDATOR_YIELD.get(p["name"]):
            return None
        return _stake_inside(R, r, p, config.VALIDATOR_YIELD[p["name"]].get("stake_metric") or "locked_tokens")

    def t_lock(r, p):
        spec = config.PROTOCOL_YIELD.get(p["name"])
        if not spec:
            return None
        base = R.D(r, spec["lock"], "now")
        if spec.get("lock_add"):
            add = R.D(r, spec["lock_add"], "now")
            base = f"({base}+IF(ISNUMBER({add}),{add},0))"
        return base

    def payout(base_fn, why_same=None):
        # THE REVENUE-FUNDED PART: where liquid bribes are credited (Aerodrome, decision 4) the payout ratio reads the
        # fees-only figure — bribes are holder income, not an allocation of the project's revenue
        def build(r, p):
            if not hv_has(p):
                return ""
            rv = base_fn(p)
            if why_same and rv in ("fees_usd", "fees_total_usd"):
                return why_same
            hv = ("holder_value_fees_only_usd" if "holder_value_fees_only_usd" in config.metrics_for_project(p)
                  else "holder_value_revenue_usd")
            return _coverage_guard(R, r, [("holder value", hv, "q0"), ("revenue", rv, "q0")],
                                   f"{_flow_rate(R, r, hv)}/{_flow_rate(R, r, rv)}")
        return build

    def has_lock(p):
        return bool(config.CP_STAKE_METRIC.get(p["name"])) or \
            config.lock_display_metric(p["name"]) in config.metrics_for_project(p)

    def monitor(r, p, ratio):
        """W6 (round 3 review): the share-token projects — NOT WIRED until a checked issuer-side wallet holds shares."""
        mon = config.INSIDER_SHARE_MONITOR[p["name"]]
        n = R.D(r, mon["numerator"], "now")
        txt = mon["not_wired"].replace('"', '""')
        return f'=IF(AND(ISNUMBER({n}),{n}>0),{ratio(n, R.D(r, mon["denominator"], "now"))},"{txt}")'

    def insider(r, p):
        spec = config.INSIDER_LOCK.get(p["name"])
        if not spec and p["name"] in config.INSIDER_SHARE_MONITOR:
            return monitor(r, p, lambda n, d: f"IF(AND(ISNUMBER({d}),{d}>0),{n}/{d},{NA})")
        if not spec:
            # NEVER A BLANK WHERE THE QUESTION APPLIES (alignment round C5); THE ROUTE PER PROJECT (Round G 2.11)
            route = (config.INSIDER_SHARE_ROUTES.get(p["name"]) or {}).get("cell")
            return (route or "NOT WIRED — no issuer / insider wallet is identified among the stakers or lockers (route: "
                    "the staking contract's depositors crossed with the issuer-wallet list)") if has_lock(p) else ""
        lapsed = dated_zero_lapsed(p, spec)
        if lapsed:
            return lapsed
        n, d = R.D(r, spec[0], "now"), R.D(r, spec[1], "now")
        return calc(f"IF(AND(ISNUMBER({n}),ISNUMBER({d}),{d}>0),{n}/{d},{NA})")

    def dated_zero_lapsed(p, spec):
        """D2-4e: a DATED ZERO (config.INSIDER_DATED_ZERO — GEODNET's filing statement) holds only while a read confirmed
        it within INSIDER_DATED_ZERO_LAPSE_DAYS: its series stale means it lapsed — MISSING E, naming the lapse."""
        dz = config.INSIDER_DATED_ZERO.get(p["name"])
        row = data_by_key.get(f"{p['name']}|{spec[0]}") or {}
        if not dz or row.get("status") != "stale":
            return ""
        return (f"MISSING E — the dated zero lapsed: {p['name']}'s filing statement (filed {dz['filed']}, "
                f"{dz['question'].upper()}) was last confirmed {row.get('latest_date') or '?'}, over "
                f"{config.INSIDER_DATED_ZERO_LAPSE_DAYS} days before this build — re-check {dz['url']}")[:240]

    def disc_tokens(r, p):
        spec = config.DISCRETIONARY_SUPPLY.get(p["name"])
        if not spec:
            return config.DISCRETIONARY_NA.get(p["name"], "")
        return calc("+".join(R.D(r, m, "now") for m in spec[0]))

    def disc_pct(r, p):
        spec = config.DISCRETIONARY_SUPPLY.get(p["name"])
        if not spec:
            return ""
        return calc(f"({'+'.join(R.D(r, m, 'now') for m in spec[0])})/({circ(r, p)})")

    def caps_text(p):
        cap = config.HOLDING_CAPS.get(p["name"])
        if not cap:
            return ""
        rd = cap.get("reading")
        util = f"; {rd[1] / cap['cap_tokens']:.1%} of the cap on {rd[0]} ({rd[2]})" if rd else ""
        return f"{cap['component']}: cap {cap['cap_tokens']:,.0f}{util}. Source: {cap['source']}"

    def leakage_ii(r, p):
        """LEAKAGE (ii), PROPOSED (SPEC.md section 9): incentive spend not returned as revenue = max(0, 1 − cover)."""
        f = subsidy(("b",))(r, p)
        inner = _cp_inner(f)
        if inner is None:
            return f
        return f"=IF(ISNUMBER({inner}),MAX(0,1-({inner})),{inner})"

    def leakage_i(r, p):
        """LEAKAGE (i), PROPOSED (SPEC.md section 9; round 3 W5): the share of DISTRIBUTED holder value the issuer-side
        wallets receive = their share of the reward-bearing stake (config.INSIDER_LOCK, now) x distributed ÷ credited
        holder value over Q0 (distributed = credited − retired: what was paid out rather than burned or held)."""
        if not hv_has(p):
            return ""
        spec = config.INSIDER_LOCK.get(p["name"])
        if not spec and p["name"] in config.INSIDER_SHARE_MONITOR:
            cr, rt = R.D(r, "holder_value_revenue_usd", "q0"), R.D(r, "retired_buyback_usd", "q0")
            return monitor(r, p, lambda n, d: (
                f'IF(AND(ISNUMBER({d}),{d}>0,ISNUMBER({cr})),IF({cr}>0,({n}/{d})*MAX(0,{cr}-IF(ISNUMBER({rt}),{rt},0))/'
                f'{cr},"n/a — no credited holder value in Q0"),{NA})'))
        if not spec:
            # NOTHING DISTRIBUTED -> NOTHING TO LEAK (Round G 2.11): (i) = insider share x distributed / credited, so a
            # quarter whose credited holder value was all retired (burned / held) reads 0 whatever the insider share;
            # only a distributing quarter needs the share, and then the cell says what is missing
            route = ((config.INSIDER_SHARE_ROUTES.get(p["name"]) or {}).get("cell")
                     or "NOT WIRED — no issuer / insider wallet is identified among the stakers (route: the stake's "
                        "holders crossed with the issuer-wallet list)").replace('"', '""')
            cr, rt = R.D(r, "holder_value_revenue_usd", "q0"), R.D(r, "retired_buyback_usd", "q0")
            return (f'=IF(NOT(ISNUMBER({cr})),{NA},IF({cr}<=0,"n/a — no credited holder value in Q0",'
                    f'IF({cr}-IF(ISNUMBER({rt}),{rt},0)<=0,0,"{route}")))')
        n, d = R.D(r, spec[0], "now"), R.D(r, spec[1], "now")
        cr, rt = R.D(r, "holder_value_revenue_usd", "q0"), R.D(r, "retired_buyback_usd", "q0")
        num = lambda x: f"IF(ISNUMBER({x}),{x},0)"  # noqa: E731
        if config.INSIDER_DATED_ZERO.get(p["name"]):
            # D2-4e: NOTHING DISTRIBUTED -> NOTHING TO LEAK, whatever the share (as before the dated zero); a
            # distributing quarter needs the share — the dated zero while it holds, else what lapsed or is not read
            lapsed = dated_zero_lapsed(p, spec)
            share = "NA()" if lapsed else f"IF(AND(ISNUMBER({n}),ISNUMBER({d}),{d}>0),{n}/{d},NA())"
            return (f'=IF(NOT(ISNUMBER({cr})),{NA},IF({cr}<=0,"n/a — no credited holder value in Q0",'
                    f'IF({cr}-{num(rt)}<=0,0,'
                    + (f'"{lapsed}"' if lapsed else f"{share}*({cr}-{num(rt)})/{cr}") + ")))")
        return (f'=IF(AND(ISNUMBER({n}),ISNUMBER({d}),{d}>0,ISNUMBER({cr})),IF({cr}>0,({n}/{d})*MAX(0,{cr}-{num(rt)})/'
                f'{cr},"n/a — no credited holder value in Q0"),{NA})')

    def leakage_iii(r, p):
        """LEAKAGE (iii), PROPOSED (SPEC.md section 9; round 3 W5): buyback execution = $ spent ÷ (tokens bought x the
        day's price) − 1, per covered day — only where the $ SPENT is measured on its own (config.LEAKAGE_III); where
        the buyback $ is tokens x price by construction the gap is 0 by definition and the cell says so."""
        if "actual_buyback_usd" not in config.metrics_for_project(p):
            return ""
        spec = config.LEAKAGE_III.get(p["name"])
        if not spec:
            return ("n/a — no measured $ spend: the buyback $ is tokens x the day's price by construction (route: each "
                    "fill's paid amount from the swap logs)")
        sp = spec["spend"]
        return _coverage_guard(R, r, [("spend", sp, "q0"), ("bought at market", "actual_buyback_usd", "q0")],
                               f"({_flow_rate(R, r, sp)})/({_flow_rate(R, r, 'actual_buyback_usd')})-1")

    def uniformity(r, p):
        """UNIFORMITY, PROPOSED (SPEC.md section 9): whole-token yield ÷ staker yield (1 = every holder alike)."""
        for yfn, lk in ((vyield, v_lock), (tyield, t_lock)):
            y, w = _cp_inner(yfn(r, p)), _cp_inner(whole(yfn, lk)(r, p))
            if y is not None and w is not None:
                return (f'=IFERROR(IF(AND(ISNUMBER({y}),ISNUMBER({w}),({y})<>0),({w})/({y}),'
                        f'"n/a — the staker yield is not a number in this window"),'
                        f'"n/a — the staker yield is not a number in this window")')
        return "n/a — no staker yield on this project (nothing is paid to stakers)"

    def non_stakers(r, p):
        if not hv_has(p):
            return "n/a — no holder value"
        ret = R.D(r, "retired_buyback_tokens", "q0")
        bm = config.a1_burn_metric(p["name"])
        burn = R.D(r, bm, "q0") if bm in config.metrics_for_project(p) else "0"
        num = lambda x: f"IF(ISNUMBER({x}),{x},0)"  # noqa: E731
        return (f'=IF(OR({num(ret)}>0,{num(burn)}>0),"yes — retired / burned tokens reach every holder",'
                f'"no — Q0 holder value was paid out, not retired")')

    def supplier_fee_only(r, p):
        rb = config.revenue_ratio_base_metric(p["name"])
        if rb == "fees_usd":
            return "n/a — the project's revenue basis is gross fees (nothing is paid to suppliers out of them on our basis)"
        fm = config.fees_headline_metric(p["name"])
        return _coverage_guard(R, r, [("fees", fm, "q0"), ("revenue", rb, "q0")],
                               f"1-{_flow_rate(R, r, rb)}/{_flow_rate(R, r, fm)}")

    def supplier(r, p):
        """SUPPLIER PAYOUT (alignment round C4, PROPOSED in SPEC.md): what suppliers receive per $ of fees = (the fees
        paid to them + the incentive $ paid to them, outflow (b)) ÷ fees. Without a (b) series the incentive leg is 0."""
        rb = config.revenue_ratio_base_metric(p["name"])
        if rb == "fees_usd":
            return "n/a — the project's revenue basis is gross fees (nothing is paid to suppliers out of them on our basis)"
        if not config.outflow_metrics(p["name"], "b"):
            return supplier_fee_only(r, p)
        fm = config.fees_headline_metric(p["name"])
        return _coverage_guard(R, r, [("fees", fm, "q0"), ("revenue", rb, "q0"), ("outflow b", "outflow_b_usd", "q0")],
                               f"(({_flow_rate(R, r, fm)})-({_flow_rate(R, r, rb)})"
                               f"+({_flow_rate(R, r, 'outflow_b_usd')}))/({_flow_rate(R, r, fm)})")

    def retention(p):
        rows = [t for t in config.RETENTION_TESTS if t["project"] == p["name"]]
        return "; ".join(f"{t['programme']} (ended {t['ended']}) — {t['series']}: 30d after ÷ 30d before "
                         f"{t['after_30d']:.2f}, latest ÷ before {t['latest']:.2f} ({t['endpoint']})" for t in rows)

    attr = lambda what: (lambda r, p: config.phase2_attribute_text(p["name"], what))  # noqa: E731
    return [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        ("Market cap ($)", lambda r, p: pull(mc(r)), FMT_USD, "pull", False, {"metric": "market_cap_usd"}),
        # G6 item 8 (spec_coverage): price, emissions and unlocks on CP, the same cells the A-tabs read
        ("Price — spot ($)", lambda r, p: pull(R.D(r, "price_usd", "now")), FMT_USD4, "pull", False,
         {"metric": "price_usd"}),
        ("Emissions Q0 (tokens) — every incentive programme, as the A-tabs read it",
         lambda r, p: _fixed_supply_text(p) or pull(R.D(r, "emissions_tokens", "q0")), FMT_NUM, "pull", False,
         {"metric": "emissions_tokens"}),
        ("Unlocks Q0 (tokens) — releases from the non-circulating wallets",
         lambda r, p: (pull(R.D(r, "unlock_release_tokens", "q0")) if config.unlock_stock_metrics(p["name"])
                       else "n/a — no non-circulating wallet set on this project (config.unlock_stock_metrics)"),
         FMT_NUM, "pull", False, {"metric": "unlock_release_tokens"}),
        # ---- SUPPLY LAYERS (item 1) ----
        ("Max supply (tokens)", lambda r, p: pull(R.D(r, "max_supply", "now")), FMT_NUM, "pull", False, {"metric": "max_supply"}),
        ("Total supply (tokens)", lambda r, p: pull(R.D(r, "total_supply", "now")), FMT_NUM, "pull", False, {"metric": "total_supply"}),
        ("Circulating supply (tokens) — our convention, the figure in use", lambda r, p: pull(circ(r, p)), FMT_NUM,
         "pull", False, {"metric": "circulating_supply_used"}),
        ("Staked / locked inside circulating (tokens)",
         lambda r, p: _cp_float_reason(p) or calc(_cp_lock(R, r, p)), FMT_NUM, "calc", False,
         {"metric_fn": lambda n: config.CP_STAKE_METRIC.get(n, "locked_tokens")}),
        (CP_FLOAT_HEAD, lambda r, p: _cp_float_reason(p) or calc(flt(r, p)), FMT_NUM, "calc", True,
         {"metric_fn": lambda n: config.CP_STAKE_METRIC.get(n, "locked_tokens"),
          "partial_fn": lambda p: config.lock_partial_reason(p["name"]),
          "partial_direction": "the lock figure is incomplete, so TRADEABLE FLOAT READS HIGH.",
          "partial_fmt": '#,##0" PARTIAL↑";(#,##0)" PARTIAL↑"'}),
        ("Tradeable float ÷ circulating", lambda r, p: _cp_float_reason(p) or calc(f"({flt(r, p)})/({circ(r, p)})"),
         FMT_PCT, "calc"),
        # ---- EVERY RETIREMENT AND DILUTION RATE ON BOTH BASES (item 1) ----
        *rates,
        # ---- THE FIVE OUTFLOW CATEGORIES IN $ (amendment 3) ----
        *outflow_usd,
        # ---- SUBSIDY COVER (item 5, amendment 3) ----
        ("Subsidy cover — revenue basis", lambda r, p: _cp_revenue_metric(p) if _cp_cats(p, ("b", "c")) else "",
         FMT_TEXT, "text"),
        (CP_SUBSIDY_HEAD, subsidy(("b",)), FMT_X, "calc", True,
         {"metric": "outflow_b_usd", "fmt_fn": _subsidy_dates_fmt}),
        (CP_SUBSIDY_BC_HEAD, subsidy(("b", "c")), FMT_X, "calc"),
        # ---- HOLDER VALUE (item 3, LITMUS A) ----
        (CP_HVY_HEAD, hvy, FMT_PCT, "calc", True, {"metric": "holder_value_revenue_usd"}),
        ("Holder value yield — fees only (where liquid bribes are credited: Aerodrome, decision 4)", hv_fees_only,
         FMT_PCT, "calc"),
        ("Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ market cap", hv_other, FMT_PCT, "calc"),
        ("Holder value funding by period (config HOLDER_VALUE)", lambda r, p: funding_text(p), FMT_TEXT, "text"),
        (CP_NHY_HEAD, nhy, FMT_PCT, "calc", True, {"metric": "holder_value_revenue_usd"}),
        *_tbill_cols(R, [("Holder value yield", hvy), ("Net holder yield", nhy),
                         ("Staker yield — validator", lambda r, p: vyield(r, p)),
                         ("Staker yield — protocol, tokens", lambda r, p: tyield(r, p))]),
        # ---- STAKER YIELD vs WHOLE-TOKEN YIELD (item 3) ----
        ("Staker yield — validator (A1)", lambda r, p: vyield(r, p), FMT_PCT, "calc"),
        ("Whole-token yield — validator = staker yield × staked share (locked ÷ circulating, now)",
         whole(vyield, v_lock), FMT_PCT, "calc"),
        ("Staker yield — protocol, tokens (A3)", lambda r, p: tyield(r, p), FMT_PCT, "calc"),
        ("Whole-token yield — protocol = staker yield × reward-bearing stake ÷ circulating (now)",
         whole(tyield, t_lock), FMT_PCT, "calc"),
        ("Lock terms per yield — lock / unstake / liquid wrapper", attr("lock_terms"), FMT_TEXT, "text"),
        ("Payout currency per yield", attr("payout_currency"), FMT_TEXT, "text"),
        # ---- PAYOUT RATIO (item 3) ----
        ("Payout ratio — gross = credited holder value ÷ fees", payout(lambda p: config.fees_headline_metric(p["name"])),
         FMT_PCT, "calc"),
        ("Payout ratio — the project's own net basis = credited holder value ÷ revenue (config revenue basis)",
         payout(lambda p: config.revenue_ratio_base_metric(p["name"]), "n/a — the project's net basis is gross fees (as left)"),
         FMT_PCT, "calc"),
        # ---- ATTRIBUTES (amendments 2 and 5: never penalties) ----
        ("Destination durability (burn / held-timelocked / held-discretionary)", attr("destination"), FMT_TEXT, "text"),
        ("Mechanism certainty + dated log of changes", attr("certainty"), FMT_TEXT, "text"),
        ("CAPTURE STATUS + switch power + dated switch signals", attr("capture"), FMT_TEXT, "text"),
        ("Insider share of staked / locked supply", insider, FMT_PCT, "calc"),
        ("Insider share — what is divided", lambda r, p: (config.INSIDER_LOCK.get(p["name"]) or (
            "", "", (config.INSIDER_SHARE_MONITOR.get(p["name"]) or {}).get("what", "")))[2],
         FMT_TEXT, "text"),
        # ---- LEAKAGE AND UNIFORMITY (alignment round C12 — PROPOSED definitions, SPEC.md section 9) ----
        ("LEAKAGE (ii) — incentive spend not returned as revenue = max(0, 1 − subsidy cover) (PROPOSED)", leakage_ii,
         FMT_PCT, "calc"),
        ("Leakage (i) — insider share of distributed holder value = insider share of the stake x distributed ÷ "
         "credited holder value, Q0 (PROPOSED)", leakage_i, FMT_PCT, "calc"),
        ("Leakage (iii) — buyback execution: $ spent ÷ (tokens bought x the day's price) − 1, per covered day "
         "(PROPOSED)", leakage_iii, FMT_PCT, "calc"),
        ("UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED)", uniformity, FMT_X, "calc"),
        ("Non-stakers receive value (yes / no) (PROPOSED)", non_stakers, FMT_TEXT, "calc"),
        # C5: insider holdings INSIDE circulating — by the convention every identified issuer / insider wallet is
        # outside circulating, so where circulating is our on-chain set the share is 0 by construction
        ("Insider share of circulating = insider-held tokens counted inside circulating ÷ circulating",
         lambda r, p: ("=0" if (config.CIRCULATING_ONCHAIN.get(p["name"]) or {}).get("status") == "established"
                       and config.circulating_primary_metric(p["name"]) in ("circulating_supply_onchain",)
                       else "=0" if p["name"] == "Hyperliquid"    # tokenDetails less the Foundation + Grants (A1)
                       else "n/a — circulating is a published / aggregator figure: which insider wallets it counts is "
                            "not established"), FMT_PCT, "calc"),
        # ---- DEMAND (LITMUS B; lock rate on the demand side, amendment 5) ----
        ("Holding demand — lock rate = locked ÷ circulating",
         lambda r, p: calc(f"({_stake_inside(R, r, p, config.lock_display_metric(p['name']))})/({circ(r, p)})"),
         FMT_PCT, "calc"),
        ("Holding demand — components and caps (cap utilisation where read)",
         lambda r, p: "; ".join(x for x in [caps_text(p), config.phase2_attribute_text(p["name"], "holding_demand")
                                            if p["name"] in config.PHASE2_ATTRIBUTES else ""] if x), FMT_TEXT, "text"),
        ("Flow demand — buyback $ (every funding source, annualised) ÷ market cap",
         lambda r, p: "" if "actual_buyback_usd" not in config.metrics_for_project(p) else _coverage_guard(
             R, r, [("buyback", "actual_buyback_usd", "q0")], f"{ann(r, p, 'actual_buyback_usd')}/{mc(r)}"),
         FMT_PCT, "calc"),
        # ---- DISCRETIONARY SUPPLY (LITMUS C) ----
        ("Discretionary supply (tokens) — issuer-side wallets with no published release schedule", disc_tokens,
         FMT_NUM, "calc"),
        ("Discretionary supply ÷ circulating", disc_pct, FMT_PCT, "calc"),
        ("Discretionary supply — which wallets", lambda r, p: (config.DISCRETIONARY_SUPPLY.get(p["name"]) or ((), ""))[1],
         FMT_TEXT, "text"),
        # ---- SUPPLIER PAYOUT (LITMUS D) and RETENTION (LITMUS E) ----
        (CP_SUPPLIER_HEAD, supplier, FMT_X, "calc"),
        ("Supplier payout, fees only = (fees − revenue) ÷ fees, per covered day", supplier_fee_only, FMT_PCT, "calc"),
        ("Post-incentive retention (LITMUS E) — usage after ÷ before the programme ended",
         lambda r, p: retention(p), FMT_TEXT, "text"),
        # ---- PENDLE BOROS ON ITS OWN LINES (alignment round C16) ----
        ("Boros fees Q0 ($) — Pendle Boros, its own line (NOT in the sPENDLE buyback base)",
         lambda r, p: pull(R.D(r, "boros_fees_usd", "q0")) if p["name"] == "Pendle" else "", FMT_USD, "pull", False,
         {"metric_fn": lambda n: "boros_fees_usd" if n == "Pendle" else None}),
        ("Boros volume Q0 ($) — every market's traded units x its collateral's daily price (round 3 W2); BASIS: "
         "trailing 90 days to the as-of date (workbook basis). The research figures of 2026-10-13 (Q0 $3.043B, Q-4 "
         "$2.36B) are 91-day quarters ending the day before — the same series reads $2.922B / $2.506B on this basis",
         lambda r, p: pull(R.D(r, "boros_volume_usd", "q0")) if p["name"] == "Pendle" else "", FMT_USD, "pull", False,
         {"metric_fn": lambda n: "boros_volume_usd" if n == "Pendle" else None}),
        # ---- SUBSIDY TARGETING BY POOL (Round G8 4a): each pool's emissions against its own fees, last complete epoch
        ("Subsidy targeting by pool — basis", lambda r, p: config.SUBSIDY_TARGETING_ROUTES.get(p["name"], ""),
         FMT_TEXT, "text"),
        ("Subsidy targeting — FEE COVER of the emitted pools = Σ pool fees ÷ Σ emissions $ (last complete epoch)",
         lambda r, p: pull(R.D(r, "subsidy_fee_cover_pools", "now")) if p.get("subsidy_targeting") else "", FMT_X,
         "pull", False, {"metric_fn": lambda n: "subsidy_fee_cover_pools" if n in ("Aerodrome", "Pendle") else None}),
        ("Subsidy targeting — share of emissions to pools whose own fees cover them (last complete epoch)",
         lambda r, p: pull(R.D(r, "subsidy_emissions_to_covered_share", "now")) if p.get("subsidy_targeting") else "",
         FMT_PCT, "pull", False,
         {"metric_fn": lambda n: "subsidy_emissions_to_covered_share" if n in ("Aerodrome", "Pendle") else None}),
        ("ONE-OFF BURN EVENTS — dated, each with its funding; never in a rate, trend or holder value",
         lambda r, p: config.one_off_events_text(p["name"]), FMT_TEXT, "text"),
        ("Attributes NOT established (research of 2026-10-12)", attr("unverified"), FMT_TEXT, "text"),
        # ---- PHASE 4: THE BUSINESS EACH PROJECT IS JUDGED ON; PHASE 3: ITS MARKET SHARE (overnight 2026-10-12) ----
        *_business_cols(R),
        ("Market share — basis and peer set (config.MARKET_SHARE)", lambda r, p: _market_share_basis(p), FMT_TEXT,
         "text"),
        (MARKET_SHARE_HEAD, lambda r, p: "" if p["name"] not in config.MARKET_SHARE else (
            "n/a — " + config.MARKET_SHARE[p["name"]]["na"] if "na" in config.MARKET_SHARE[p["name"]]
            else calc(R.D(r, "market_share_pct", "now"))), FMT_PCT, "calc", True,   # a share WE derive
         {"metric_fn": lambda n: "market_share_pct" if "na" not in (config.MARKET_SHARE.get(n) or {"na": 1}) else None}),
        ("Market share, second basis — basis and peer set (config.MARKET_SHARE_2)",
         lambda r, p: (config.MARKET_SHARE_2.get(p["name"]) or {}).get("basis", ""), FMT_TEXT, "text"),
        (MARKET_SHARE_2_HEAD, lambda r, p: calc(R.D(r, "market_share_2_pct", "now"))
         if p["name"] in config.MARKET_SHARE_2 else "", FMT_PCT, "calc", True,
         {"metric_fn": lambda n: "market_share_2_pct" if n in config.MARKET_SHARE_2 else None}),
        # ---- PHASE 3: ETF / ETP AND TREASURY-COMPANY HOLDINGS — dated issuer readings, never trended ----
        *[c for kind, lab in (("etf_etp", "ETF / ETP holdings"), ("dat", "Treasury-company (DAT) holdings"))
          for c in ((f"{lab} (tokens) — each product's latest dated issuer reading, summed (dates differ)",
                     (lambda kind: lambda r, p: _holding_cell(p, kind))(kind), FMT_NUM, "calc"),
                    (f"{lab} ÷ circulating",
                     (lambda kind: lambda r, p: (calc(f"{config.etf_dat(p['name'], kind)[0]!r}/({circ(r, p)})")
                                                 if config.etf_dat(p["name"], kind)[0] is not None else ""))(kind),
                     FMT_PCT, "calc"))],
        ("ETF / ETP and DAT holders — product, tokens, as-of date (etf_dat_holdings.json)",
         lambda r, p: "; ".join(x for x in (config.etf_dat(p["name"], "etf_etp")[1],
                                            config.etf_dat(p["name"], "dat")[1]) if x), FMT_TEXT, "text"),
    ]


def _holding_cell(p: dict, kind: str):
    v, why = config.etf_dat(p["name"], kind)
    return f"={v!r}" if v is not None else f"n/a — {why}"


MARKET_SHARE_HEAD = "MARKET SHARE vs its category (now; a shifted quarter: that quarter's mean) — peer set beside"
MARKET_SHARE_2_HEAD = ("MARKET SHARE, SECOND BASIS — chain fees vs 13 chains (now; a shifted quarter: that quarter's "
                       "mean)")
BUSINESS_HEAD = "BUSINESS {i} — core usage (a stock: now; a flow: Q0 annualised over its covered days)"


def _market_share_basis(p: dict) -> str:
    v = config.MARKET_SHARE.get(p["name"])
    if not v:
        return ""
    return v.get("na") and f"n/a — {v['na']}" or v["basis"]


def _business_metric(name: str, i: int) -> str | None:
    ms = config.BUSINESS_SLOTS.get(name) or ()
    return ms[i] if i < len(ms) else None


def _business_cols(R: Refs) -> list[tuple]:
    """PHASE 4 (Jake's overnight review of ab5f2a9, 2026-10-12): up to config.BUSINESS_SLOT_COUNT usage series per
    project (config.BUSINESS_SLOTS), each a CP headline so it carries a trajectory row (Fundamentals, +1), a
    Credibility verdict and the sanity rules. Beside each: what the series is, its unit and its source kind (a
    DASHBOARD source says so, with what an on-chain replacement would need)."""
    def what(i):
        def build(r, p):
            m = _business_metric(p["name"], i)
            if not m:
                return (config.BUSINESS_NOT_WIRED.get(p["name"], "") if i == 0 and p["name"] not in
                        config.BUSINESS_SLOTS else "")
            md = METRICS[m]
            unit = {"usd": "$", "count": "count", "tokens": "tokens"}.get(md["unit"], md["unit"])
            per = " a year" if md["kind"] == "flow" else ""
            sk = md.get("source_kind") or ""
            rep = md.get("onchain_replacement") or ""
            gap = config.BUSINESS_NOT_WIRED.get(p["name"], "") if i == len(config.BUSINESS_SLOTS[p["name"]]) - 1 else ""
            return "; ".join(x for x in (f"{m} ({unit}{per})", sk and f"source: {sk}",
                                         rep and f"on-chain replacement: {rep}", gap and f"NOT WIRED: {gap}") if x)
        return build

    def value(i):
        def build(r, p):
            m = _business_metric(p["name"], i)
            if not m:
                return ""
            if m not in config.metrics_for_project(p):
                return f"n/a — {m} is not a series of {p['name']}"
            if METRICS[m]["kind"] == "stock":
                return pull(R.D(r, m, "now"))
            q0 = R.D(r, m, "q0")
            return _coverage_guard(R, r, [(m, m, "q0")], _annualise(R, r, p, m, q0))
        return build
    out = []
    for i in range(config.BUSINESS_SLOT_COUNT):
        out += [(f"Business {i + 1} — series, unit, source", what(i), FMT_TEXT, "text"),
                (BUSINESS_HEAD.format(i=i + 1), value(i), FMT_NUM, "calc", True,
                 {"metric_fn": (lambda n, i=i: _business_metric(n, i))})]
    return out


def _axis_row(ws) -> None:
    """THE SPEC.md AXIS OF EVERY COLUMN (Jake's alignment round C13), on the row above the header: BUSINESS / CAPTURE /
    ALIGNMENT / CONTEXT, so the three scores can be built from the workbook without restructuring. Written only where
    that row is free."""
    import spec                                            # noqa: PLC0415
    hr = next((r for r in range(1, 12) if ws.cell(r, 1).value == "Project"), None)
    if not hr or hr < 2 or any(ws.cell(hr - 1, c).value not in (None, "") for c in range(1, ws.max_column + 1)):
        return
    ws.cell(hr - 1, 1, value="Axis (SPEC.md) →").font = F_BOLD
    for c in range(2, ws.max_column + 1):
        h = ws.cell(hr, c).value
        if h:
            ws.cell(hr - 1, c, value=spec.axis_of(spec.concept_for(ws.title, str(h))) or "—")


def write_cp(ws, R: Refs, data_by_key: dict):
    projects = scoped_projects()
    _title(ws, "CP — Comparability", "One supply definition for every project: circulating (our convention) and "
                                     "TRADEABLE FLOAT = circulating − staked/locked − protocol/treasury-held. Every "
                                     "retirement and dilution rate on both. The five outflow categories (a security "
                                     "emissions, b supply-side, c demand-side, d ecosystem / grants, e unlocks), "
                                     "subsidy cover, holder value yield (revenue-funded only) and net holder yield, "
                                     "staker vs whole-token yield, and the attributes (lock terms, payout currency, "
                                     "destination, mechanism certainty, capture status) — attributes, never "
                                     "penalties. RAW METRICS ONLY: no scores, bands or leaderboards.")
    specs = _with_details("CP", _cp_cols(R, data_by_key), R)
    end = _write_table(ws, R, projects, specs, data_by_key,
                       ["market_cap_usd", "circulating_supply", "locked_tokens", "fees_usd"],
                       key_cols=_key_cols(specs, "TRADEABLE FLOAT", "HOLDER VALUE YIELD", "NET HOLDER YIELD",
                                          "SUBSIDY COVER"))
    _confidence_tally(ws, end + 2, projects, specs, data_by_key)
    _set_widths(ws, _widths(specs))
    return projects, end


def _a4_phase2_cols(R: Refs) -> list[tuple]:
    """A4 ADDITIONS (Jake 2026-10-12, Phase 2 item 4): cumulative burn as % of max supply; the fees at which burn meets
    issuance at the measured burn share (revenue required for crossover); the governance lever — the burn share of fees
    that reaches crossover on today's fees and issuance, with no growth; the burn split by source (every burn on A1 vs
    the A4 burn: the difference is the fee-market / other burn); the treasury governance could redeploy (config
    DISCRETIONARY_SUPPLY). Burn in tokens and $ beside issuance in tokens is the GROSS BURN / ISSUANCE block above."""
    def a4b(p):
        return config.a4_burn_metric(p["name"])

    def iss_m(p):
        return config.issuance_basis(p["name"])

    def cum(r, p):
        if "burn_address_balance" not in config.metrics_for_project(p):
            return "n/a — no cumulative burn stock (burn_address_balance) on file"
        b, mx = R.D(r, "burn_address_balance", "now"), R.D(r, "max_supply", "now")
        return calc(f"IF(AND(ISNUMBER({mx}),{mx}>0),{b}/{mx},{NA})")

    def legs(p):
        return [("burn", a4b(p), "q0"), ("issuance", iss_m(p), "q0"), ("fees", "fees_usd", "q0")]

    def required(r, p):
        if p.get("issuance_declared_zero"):
            return "n/a — no issuance: burn needs no revenue to exceed it"
        return _coverage_guard(R, r, legs(p),
                               f"{_flow_rate(R, r, 'fees_usd')}*{_flow_rate(R, r, iss_m(p))}"
                               f"/{_flow_rate(R, r, a4b(p), p=p)}*{G('days_per_year')}")

    def lever(r, p):
        if p.get("issuance_declared_zero"):
            return "n/a — no issuance"
        px = R.D(r, "price_usd", "q0")
        return _coverage_guard(R, r, [("issuance", iss_m(p), "q0"), ("fees", "fees_usd", "q0")],
                               f"{_flow_rate(R, r, iss_m(p))}*{px}/{_flow_rate(R, r, 'fees_usd')}")

    def split(r, p):
        a1, a4 = config.a1_burn_metric(p["name"]), a4b(p)
        if a1 == a4:
            return ""
        return _coverage_guard(R, r, [("every burn", a1, "q0"), ("A4 burn", a4, "q0")],
                               f"{R.D(r, a1, 'q0')}-{R.D(r, a4, 'q0')}", same_span=True)

    def split_text(p):
        a1, a4 = config.a1_burn_metric(p["name"]), a4b(p)
        return (f"A4 burn = {a4}; every burn (A1) = {a1}" + ("" if a1 == a4 else
                "; the difference is the burn outside the A4 mechanism (fee-market / other)"))

    def disc(r, p, usd=False):
        spec = config.DISCRETIONARY_SUPPLY.get(p["name"])
        if not spec:
            return ""
        t = "+".join(R.D(r, m, "now") for m in spec[0])
        return calc(f"({t})*{R.D(r, 'price_usd', 'now')}" if usd else t)
    return [
        ("Cumulative burn ÷ max supply", cum, FMT_PCT, "calc"),
        ("Revenue required for crossover ($ fees a year) = fees × issuance ÷ burn, per covered day — at the measured "
         "burn share", required, FMT_USD, "calc"),
        ("Governance lever = burn share of fees that reaches crossover with no growth (issuance $ ÷ fees)", lever,
         FMT_PCT, "calc"),
        ("Burn split Q0 (tokens): every burn − the A4 burn", split, FMT_NUM, "calc"),
        ("Burn split — by source", lambda r, p: split_text(p), FMT_TEXT, "text"),
        ("One-off burn Q0..Y1 (tokens) — the event row, out of every rate and trend (one_off_burn_tokens, trailing 365d)",
         lambda r, p: pull(R.D(r, "one_off_burn_tokens", "y1")) if "one_off_burn_tokens" in config.metrics_for_project(p)
         else "", FMT_NUM, "pull", False, {"metric": "one_off_burn_tokens"}),
        ("One-off burn events", lambda r, p: config.one_off_events_text(p["name"]), FMT_TEXT, "text"),
        ("Treasury governance could redeploy (tokens) — discretionary issuer-side wallets", disc, FMT_NUM, "calc"),
        ("Treasury governance could redeploy ($, spot)", lambda r, p: disc(r, p, usd=True), FMT_USD, "calc"),
    ]


def write_a4(ws, R: Refs, data_by_key: dict):
    projects = [p for p in scoped_projects() if 4 in p["archetypes"]]
    _title(ws, "A4 — Permanent Burn", "Gross burn, gross issuance and NET supply change side by side. Where the protocol publishes net mint itself "
                                       "that SELF-REPORTED figure is used and the derived one is shown beside it for comparison; a gap between them means one is wrong. "
                                       "Otherwise net = issuance − burn (positive = net inflation). "
                                       "Q0 = trailing comparison window; $ conversions at 90-day average price. Implied burn = fees × documented share (suppressed when unconfirmed). "
                                       "PancakeSwap is the disclosure template: publishes net mint monthly.")
    ann = ANN
    price = lambda r: R.D(r, "price_usd", "q0")  # noqa: E731
    # PER PROJECT, not a constant: config.a4_burn_metric. Sky's gross_burn_tokens is the Pause
    # Proxy's whole history (blocked — two unrelated spell burns), so its headline reads the
    # Stage 2 leg alone. Everyone else reads gross_burn_tokens, unchanged.
    burn = lambda r, p, w="q0": R.D(r, config.a4_burn_metric(p["name"]), w)  # noqa: E731
    iss = lambda r, w="q0": R.D(r, "gross_issuance_tokens", w)  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        # MOVED HERE 2026-09-24 from further right (burn yield, crossover ratio, both net-change
        # columns) — not duplicated. The crossover now runs on config.issuance_basis: pool
        # release for pre-minted supply (GEODNET, Hyperliquid), gross issuance for everyone else.
        *_valuation_head(R, circulating=True),
        *_a4_headline(R, burn, data_by_key),
        *_unlock_cols(R),
        ("Burn execution", lambda r, p: pull(R.C(r, "Burn execution")), FMT_TEXT, "pull"),
        ("Burn status", lambda r, p: pull(R.C(r, "Burn status")), FMT_TEXT, "pull", False, {"status_fill": "burn_split"}),
        ("Documented share of fees burned (config)", lambda r, p: pull(R.C(r, "Share of fees burned")), FMT_PCT, "pull", False, {"gate": "burn_split"}),
        _fees_q0_col(R),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("GROSS BURN Q0 (tokens)", lambda r, p: pull(burn(r, p)), FMT_NUM, "pull", True, {"metric_fn": config.a4_burn_metric}),
        ("Gross burn Q0 ($ at avg price)", lambda r, p: calc(f"{burn(r, p)}*{price(r)}"), FMT_USD, "calc"),
        ("GROSS ISSUANCE Q0 (tokens)", lambda r, p: _fixed_supply_text(p) or pull(iss(r)), FMT_NUM, "pull", True,
         {"metric": "gross_issuance_tokens"}),
        ("Gross issuance Q0 ($ at avg price)", lambda r, p: _fixed_supply_text(p) or calc(f"{iss(r)}*{price(r)}"),
         FMT_USD, "calc"),
        # ===== POOL RELEASE, BESIDE BURN. Added 2026-09-24, for GEODNET. =====
        # GROSS ISSUANCE reads n/a here for any premint project whose issuance_derivation is
        # suppressed (GEODNET: all supply pre-minted, nothing is MINTED, so the quantity that
        # column asks for is genuinely zero — see config.py issuance_derivation.na_reason). The
        # crossover Jake actually wants — tokens ENTERING CIRCULATION vs tokens leaving it — is
        # real for those projects too, just measured as pool_release_tokens (distribution from a
        # pre-minted pool) rather than as minting. Blank for every project without the metric,
        # same convention as Net mint Q0 above; filled and placed beside GROSS BURN so the two
        # numbers Jake needs are in adjacent cells instead of two unrelated ones he has to hunt
        # down and compare by hand.
        ("Pool release Q0 (tokens) — measured, premint distribution (where issuance is n/a)",
         lambda r, p: pull(R.D(r, "pool_release_tokens", "q0")), FMT_NUM, "pull", True, {"metric": "pool_release_tokens"}),
        ("Release ÷ burn (x) — premint projects, in place of burn ÷ issuance",
         lambda r, p: _coverage_guard(R, r, [("release", "pool_release_tokens", "q0"),
                                             ("burn", config.a4_burn_metric(p["name"]), "q0")],
                                      f"{_flow_rate(R, r, 'pool_release_tokens')}/{_flow_rate(R, r, config.a4_burn_metric(p['name']), p=p)}"),
         FMT_X, "calc"),
        ("Net mint Q0 (SELF-REPORTED by the protocol)", lambda r, p: pull(R.D(r, "net_mint_monthly", "q0")), FMT_NUM, "pull", False, {"metric": "net_mint_monthly"}),
        ("Self-reported figure preferred?", lambda r, p: ", ".join(
            x for x in ["net mint" if p.get("self_reported_net_mint") else "",
                        "burn" if p.get("self_reported_burn") else ""] if x), FMT_TEXT, "text"),
        ("Derived net supply change (issuance − burn), for comparison",
         lambda r, p: _guarded_net(R, r, p, _a4_iss_metric(p["name"]), config.a1_burn_metric(p["name"])), FMT_NUM, "calc"),
        # D1-3: nothing issued and nothing self-reported (Hyperliquid: pre-minted) — n/a, never a history wait
        ("Self-reported − derived (a gap here means one of the two is wrong)",
         lambda r, p: (_no_issuance_text(p).replace("no issuance to divide by", "nothing issued and nothing self-reported")
                       if not p.get("self_reported_net_mint") else "") or _guarded_net(
             R, r, p, "gross_issuance_tokens", config.a1_burn_metric(p["name"]),
                                   total=f"{R.D(r, 'net_mint_monthly', 'q0')}-({iss(r)}-{burn(r, p)})"), FMT_NUM, "calc"),
        ("Net supply change ($ at avg price)",
         lambda r, p: _price_net(_net_guarded(R, r, p, _basis_iss(R, p), burn), price(r)), FMT_USD, "calc"),
        ("Burn as share of fees (measured)",
         lambda r, p: _coverage_guard(R, r, [("burn", config.a4_burn_metric(p["name"]), "q0"), ("fees", "fees_usd", "q0")],
                                      f"{_flow_rate(R, r, config.a4_burn_metric(p['name']), p=p)}*{price(r)}/{_flow_rate(R, r, 'fees_usd')}"),
         FMT_PCT, "calc"),
        ("Issuance as % of supply (annualised)", lambda r, p: calc(f"{_annualise(R, r, p, _a4_iss_metric(p['name']), R.D(r, _a4_iss_metric(p['name']), 'q0'))}/{R.D(r, 'circulating_supply', 'now')}"), FMT_PCT, "calc"),
        *_a4_phase2_cols(R),
        # THE BASE THE SHARE IS OF (Uniswap, G6 2026-10-19): config burn_split.fee_base_metric — Uniswap's documented
        # share is 100% of PROTOCOL fees (revenue_usd), not of LP fees; fees_usd everywhere else.
        ("Implied burn Q0 (tokens) = fees × documented share ÷ avg price",
         lambda r, p: gated(R.C(r, "Burn split status"), f"{R.D(r, _burn_fee_base(p), 'q0')}*{R.C(r, 'Share of fees burned')}/{price(r)}", R.C(r, 'Share of fees burned'), kind="burn"), FMT_NUM, "calc", False, {"gate": "burn_split"}),
        ("Actual − implied burn (tokens)", lambda r, p: circular_gated(p, gated(R.C(r, "Burn split status"), _coverage_guard(
            R, r, [("burn", config.a4_burn_metric(p["name"]), "q0"), ("fees", _burn_fee_base(p), "q0")],
            f"{burn(r, p)}-{R.D(r, _burn_fee_base(p), 'q0')}*{R.C(r, 'Share of fees burned')}/{price(r)}", same_span=True, bare=True),
            R.C(r, 'Share of fees burned'))), FMT_NUM, "calc", False, {"gate": "burn_split"}),
        ("Supply figure complete?", lambda r, p: ("PARTIAL — " + (p.get("supply_partial_reason", "")[:90]))
         if p.get("supply_is_partial") else "", FMT_TEXT, "text"),
        ("Cross-check: Δ implied circulating supply Q0 vs Q1 (CoinGecko mcap ÷ price)",
         lambda r, p: calc(f"{R.D(r, 'circulating_supply_implied', 'q0')}-{R.D(r, 'circulating_supply_implied', 'q1')}"), FMT_NUM, "calc"),
        ("Net supply change Q1 (tokens, −3m window)", lambda r, p: _guarded_net(R, r, p, _a4_iss_metric(p["name"]), config.a1_burn_metric(p["name"]), "q1"), FMT_NUM, "calc"),
        ("Net supply change Q2 (−6m window)", lambda r, p: _guarded_net(R, r, p, _a4_iss_metric(p["name"]), config.a1_burn_metric(p["name"]), "q2"), FMT_NUM, "calc"),
        ("Net supply change Q3 (−9m window)", lambda r, p: _guarded_net(R, r, p, _a4_iss_metric(p["name"]), config.a1_burn_metric(p["name"]), "q3"), FMT_NUM, "calc"),
        *_trajectory(R, config.a4_burn_metric, "Burn"),
        *_trajectory(R, _a4_iss_metric, "Issuance"),
        # FOOTNOTES FIRST: burned SKY that is deliberately NOT in any burn column above
        # (Sky's five-way split, 2026-09-24). Leading, so the text that explains a small
        # headline next to a huge on-chain total is the first thing in the cell.
        ("Notes", lambda r, p: "; ".join(x for x in [*("FOOTNOTE — " + t for t in config.burn_footnotes(p["name"])),
                                                     p.get("notes", ""), (p.get("burn_split") or {}).get("note", "")] if x), FMT_TEXT, "text"),
    ]
    specs = _with_details("A4", specs, R)
    end = _write_table(ws, R, projects, specs, data_by_key,
                       ["fees_usd", "price_usd", "gross_burn_tokens", "sky_stage2_burn_tokens", "gross_issuance_tokens", "pool_release_tokens", "circulating_supply"],
                       key_cols=_key_cols(specs, "PERMANENT BURN YIELD", "BURN ÷ ISSUANCE", "NET SUPPLY CHANGE",
                                          "GROSS BURN Q0", "GROSS ISSUANCE Q0", "Pool release Q0"))
    _confidence_tally(ws, end + 2, scoped_projects(), specs, data_by_key)
    _set_widths(ws, _widths(specs))
    return projects, end


def write_a3(ws, R: Refs, data_by_key: dict):
    projects = [p for p in scoped_projects() if 3 in p["archetypes"]]
    _title(ws, "A3 — Revenue Buyback", "Pipeline: revenue (DefiLlama) × documented split (config) = implied buyback $ ÷ 90d AVERAGE price = implied tokens ÷ circulating supply, annualised = buyback as % of supply. "
                                        "Actual buyback shown alongside; the gap is itself a signal. Yield destination is tracked separately from burn and never netted against it. "
                                        "Grey = split unconfirmed (derived figure suppressed), OR split confirmed but its BASE unconfirmed (hover for which). Orange status = paused.")
    ann = ANN
    price = lambda r, w="q0": R.D(r, "price_usd", w)  # noqa: E731
    # THE BASE IS PER PROJECT, not a constant "revenue_usd". Almost everywhere it still is —
    # config.revenue_base_metric defaults to it — but Sky's documented shares are shares of Net
    # Protocol Surplus, which is a different quantity that cannot be derived from revenue_usd.
    # Pointing the formula at the metric the protocol actually names is what lets base_gated stop
    # greying the cell; see config.revenue_base.
    rev = lambda r, p, w="q0": R.D(r, config.revenue_base_metric(p["name"]), w)  # noqa: E731
    circ = lambda r, p: _circ(R, r, p)  # noqa: E731
    # The split that applied to each window — NEVER the current split applied backwards.
    share = lambda r, w="q0": R.C(r, WINDOW_SHARE_COL[w])  # noqa: E731
    st = lambda r, w="q0": R.C(r, WINDOW_STATUS_COL[w])  # noqa: E731
    # NO TOKEN IS BOUGHT WHERE FEES GO TO VOTERS IN THE PAIRS' OWN TOKENS (Aerodrome; Credibility pass
    # 2026-10-05: the implied cell read 18.25% because share_to_buyback 1.0 is the share DISTRIBUTED, not
    # bought). The implied figures are a structural 0, as the retirement rates already are.
    no_buy = lambda p: "=0" if _implied_zero(p) else None  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        *_valuation_head(R, fdv=True),
        *_a3_headline(R, data_by_key),
        ("Materiality", lambda r, p: pull(R.C(r, "Materiality")), FMT_TEXT, "pull"),
        # THE PROGRAMME'S STATUS, as on Master and Config (Jake's review of 97b29ae, 2026-10-11: this read the Q0 window
        # split's status — Pendle 'active' here, 'SILENT since …' on Master). The window's split status has its own
        # column below ("Status of that window's split").
        ("Buyback status", lambda r, p: pull(R.C(r, "Buyback status")), FMT_TEXT, "pull", False, {"status_fill": "fee_split"}),
        ("Programmed? (contract-enforced vs revisable by governance)", lambda r, p: pull(R.C(r, "Programmed (contract-enforced)")), FMT_TEXT, "pull"),
        ("Buyback destination", lambda r, p: pull(R.C(r, "Buyback destination")), FMT_TEXT, "pull"),
        ("Destination split (share burned)", lambda r, p: pull(R.C(r, "Destination split (share burned, where destination = split)")), FMT_PCT, "pull"),
        ("Share applied to this window (not necessarily today's)", lambda r, p: no_buy(p) or pull(share(r)), FMT_PCT, "pull", False, {"gate_window": "q0"}),
        ("Status of that window's split", lambda r, p: pull(st(r)), FMT_TEXT, "pull", False, {"gate_window": "q0"}),
        ("Current documented share (may differ from the window above)", lambda r, p: no_buy(p) or pull(R.C(r, "Share of revenue to buyback")), FMT_PCT, "pull"),
        ("Per-product split (never collapsed into one number)", lambda r, p: pull(R.C(r, "Per-product split (never collapsed)")), FMT_TEXT, "pull"),
        # THE ROW IS NAMED FOR WHAT IT HOLDS, per project. For Sky it is Net Protocol Surplus,
        # and calling it "Revenue" would put the exact confusion this whole thread is about back
        # on the sheet. The metric tag follows too, so the coverage and confidence lookups read
        # the series the figure actually came from.
        ("Base for the implied buyback ($) — revenue, or the metric the protocol's share names",
         lambda r, p: pull(rev(r, p)), FMT_USD, "pull", False, {"metric_fn": config.revenue_base_metric}),
        _fees_q0_col(R),
        ("Implied buyback Q0 ($) = revenue × share", lambda r, p: no_buy(p) or base_gated(p, gated(st(r), f"{rev(r, p)}*{share(r)}", share(r))), FMT_USD, "calc", False, {"gate": "fee_split", "base": True}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Implied buyback Q0 (tokens) = $ ÷ avg price", lambda r, p: no_buy(p) or base_gated(p, gated(st(r), f"{rev(r, p)}*{share(r)}/{price(r)}", share(r))), FMT_NUM, "calc", False, {"gate": "fee_split", "base": True}),
        # THE FIGURE THE RATIOS DIVIDE BY (item 9, 2026-10-11): was CoinGecko's while the ratios used ours
        ("Circulating supply", lambda r, p: pull(_circ(R, r, p)), FMT_NUM, "pull", False, {"metric": "circulating_supply_used"}),
        ("Supply figure complete?", lambda r, p: ("PARTIAL — " + (p.get("supply_partial_reason", "")[:90]))
         if p.get("supply_is_partial") else "", FMT_TEXT, "text"),
        ("BUYBACK AS % OF SUPPLY (annualised, implied)",
         lambda r, p: no_buy(p) or base_gated(p, threshold_gated(p, gated(st(r), f"{_annualise(R, r, p, config.revenue_base_metric(p['name']), rev(r, p))}*{share(r)}/{price(r)}/({circ(r, p)})", share(r)))),
         FMT_PCT, "calc", True, {"gate": "fee_split", "threshold": True, "base": True,
                                 "flag_fn": lambda p: ((" · no protocol share to buybacks — paid to voters", _implied_zero(p))
                                                       if _implied_zero(p) else None)}),
        ("Actual buyback Q0 ($) — observed", lambda r, p: pull(R.D(r, "actual_buyback_usd", "q0")), FMT_USD, "pull", False,
         {"metric": "actual_buyback_usd", "flag_fn": lambda p: _program_flag(p, data_by_key)}),
        ("Actual buyback Q0 (tokens) — observed", lambda r, p: pull(R.D(r, "actual_buyback_tokens", "q0")), FMT_NUM, "pull", False,
         {"metric": "actual_buyback_tokens", "flag_fn": lambda p: _program_flag(p, data_by_key)}),
        # THE BUYBACK BY DESTINATION (Jake 2026-10-10): the full buyback above, split; retirement = burned + held
        ("Actual buyback Q0 BURNED (tokens)", lambda r, p: pull(R.D(r, "buyback_burned_tokens", "q0")), FMT_NUM, "pull", False,
         {"metric": "buyback_burned_tokens"}),
        ("Actual buyback Q0 HELD (tokens) — treasury, reserve, foundation-locked", lambda r, p: pull(R.D(r, "buyback_held_tokens", "q0")),
         FMT_NUM, "pull", False, {"metric": "buyback_held_tokens"}),
        ("Actual buyback Q0 DISTRIBUTED to stakers (tokens) — staking yield, not retirement",
         lambda r, p: pull(R.D(r, "buyback_distributed_tokens", "q0")), FMT_NUM, "pull", False,
         {"metric": "buyback_distributed_tokens"}),
        # THE TOKENS RETIRED, A FUNDAMENTALS TRAJECTORY (Jake's Phase 1 review 2026-10-12, item 6: the divergence row
        # sets retired / burned tokens' growth against price's)
        ("Actual buyback Q0 RETIRED (tokens) = burned + held", lambda r, p: pull(R.D(r, "retired_buyback_tokens", "q0")),
         FMT_NUM, "pull", False, {"metric": "retired_buyback_tokens", "trajectory_id": "a3_retired_tokens"}),
        ("Actual buyback as % of supply (annualised) — RETIRED (burned + held) only",
         lambda r, p: calc(f"{_annualise(R, r, p, 'actual_buyback_tokens', R.D(r, 'retired_buyback_tokens', 'q0'))}/({circ(r, p)})"), FMT_PCT, "calc", True),
        # A STRUCTURAL-ZERO IMPLIED BUYBACK IS 0 HERE TOO (Jake 2026-10-12, on e4e50b0: Aerodrome's implied net absorption
        # read -14.41M, i.e. ~45M tokens of implied buyback = Q0 revenue x the 100% VOTER share / price — no protocol
        # share of fees goes to buybacks). Implied − actual = −actual; implied net absorption = −emissions.
        ("Implied − actual ($)",
         lambda r, p: calc(f"-{R.D(r, 'actual_buyback_usd', 'q0')}") if _implied_zero(p) else
         circular_gated(p, base_gated(p, threshold_gated(p, gated(st(r), _coverage_guard(
             R, r, [("revenue", config.revenue_base_metric(p["name"]), "q0"), ("buyback", "actual_buyback_usd", "q0")],
             f"{rev(r, p)}*{share(r)}-{R.D(r, 'actual_buyback_usd', 'q0')}", same_span=True, bare=True), share(r))))),
         FMT_USD, "calc", False, {"gate": "fee_split", "threshold": True, "base": True}),
        ("Emissions Q0 (tokens) — same period", lambda r, p: _absorption_emissions_cell(R, r, p), FMT_NUM, "pull", False,
         {"metric": "emissions_tokens"}),
        ("Net absorption Q0 (tokens) = RETIRED buyback (burned + held) − emissions",
         lambda r, p: _absorption_guard(R, r, p, [("buyback", "actual_buyback_tokens", "q0")],
                                        net_absorption(p, R.D(r, "retired_buyback_tokens", "q0"),
                                                       _absorption_emissions(R, r, p)), same_span=True),
         FMT_NUM, "calc", True, {"supply_additive": True}),
        ("Net absorption, implied basis (tokens)",
         lambda r, p: _absorption_guard(R, r, p, [], net_absorption(p, "0", _absorption_emissions(R, r, p)))
         if _implied_zero(p) else base_gated(p, threshold_gated(p, gated(st(r), _absorption_guard(
             R, r, p, [("revenue", config.revenue_base_metric(p["name"]), "q0")],
             net_absorption(p, f"{rev(r, p)}*{share(r)}/{price(r)}", _absorption_emissions(R, r, p)),
             same_span=True, bare=True), share(r)))),
         FMT_NUM, "calc", False, {"gate": "fee_split", "threshold": True, "base": True}),
        *_unlock_cols(R, absorption=True),
        _retire_vs_dilution_col(R),
        *_current_rate_cols(R),
        _retire_vs_emissions_col(R),
        # ===== DOCUMENTED ALLOCATION LEGS, ONE ROW EACH, NEVER COLLAPSED. =====
        # Blank for every project that declares none, which is all of them but Sky. The legs
        # carry their own effect on supply, because a single "implied buyback" figure is right
        # as buy pressure and wrong as supply reduction by the ratio between the two legs, and
        # nothing on a row of numbers tells a reader which they are looking at.
        # G10 3a: both legs together ARE the buying share, so a window applied day by day reads the Config window share
        # (the buying share in force each day); the 5% burn leg has no recorded share before Stage 2 and stays blocked
        ("Implied SKY purchased, both legs ($) — buy pressure, NOT supply reduction",
         lambda r, p: _leg(p, "sky_buying", lambda sh: base_gated(
             p, gated(st(r), f"{rev(r, p)}*{share(r) if _DAY_BY_DAY.get((p['name'], 'q0')) and not _SHIFT['k'] else sh}",
                      share(r)))),
         FMT_USD, "calc", False, {"gate": "fee_split", "base": True}),
        ("Implied BURN ($) — the only leg that removes supply",
         lambda r, p: _burn_leg_dbd(p) or _leg(p, "burn", lambda sh: base_gated(
             p, gated(st(r), f"{rev(r, p)}*{sh}", share(r)))), FMT_USD, "calc", False,
         {"gate": "fee_split", "base": True}),
        ("Implied burn (tokens) = burn $ ÷ avg price",
         lambda r, p: _burn_leg_dbd(p) or _leg(p, "burn", lambda sh: base_gated(
             p, gated(st(r), f"{rev(r, p)}*{sh}/{price(r)}", share(r)))), FMT_NUM, "calc", False,
         {"gate": "fee_split", "base": True}),
        # C3: THE IMPLIED BURN AGAINST THE BURN THAT ACTUALLY HAPPENED. Same comparison the tab
        # makes for buybacks two rows up, on the one leg where an on-chain figure exists — Sky's
        # burn is read from Transfer-to-zero events, so the actual is a measurement and not
        # another derivation. A persistent gap is a finding about the base, the share, or the
        # read; it is not an error in any of them by itself.
        ("Implied burn − ACTUAL burn (tokens) — the 5% leg against the chain",
         lambda r, p: _burn_leg_dbd(p) or _leg(p, "burn", lambda sh: base_gated(
             p, gated(st(r), _coverage_guard(
                 R, r, [("revenue", config.revenue_base_metric(p["name"]), "q0"), ("burn", "gross_burn_tokens", "q0")],
                 f"{rev(r, p)}*{sh}/{price(r)}-{R.D(r, 'gross_burn_tokens', 'q0')}", same_span=True, bare=True),
                      share(r)))), FMT_NUM, "calc", False,
         {"gate": "fee_split", "base": True, "metric": "gross_burn_tokens"}),
        ("Coverage ratio = actual buyback ÷ revenue (>1 ⇒ treasury-funded)",
         lambda r, p: _coverage_guard(R, r, [("buyback", "actual_buyback_usd", "q0"), ("revenue", config.revenue_ratio_base_metric(p["name"]), "q0")],
                                      f"{_flow_rate(R, r, 'actual_buyback_usd', p=p)}/{_flow_rate(R, r, config.revenue_ratio_base_metric(p['name']))}"),
         FMT_X, "calc"),
        ("Fees ÷ FDV (annualised)", lambda r, p: calc(f"{_annualise(R, r, p, 'fees_usd', R.D(r, 'fees_usd', 'q0'))}/{R.D(r, 'fdv_usd', 'now')}"), FMT_PCT, "calc"),
        # PER PROJECT, not fixed: Aerodrome shows veAERO.supply() and everyone else shows the
        # escrow's token balance. See config.LOCK_DISPLAY_METRIC for why only Aerodrome differs,
        # and note that a blanket flip here would have blanked this column for the other 26
        # projects, since ve_locked_supply_tokens is only_projects Aerodrome.
        ("Tokens locked (ve)",
         lambda r, p: pull(R.D(r, config.lock_display_metric(p["name"]), "now")),
         FMT_NUM, "pull", False, {"metric_fn": config.lock_display_metric}),
        # The lock rate divides the SAME figure the row above displays. Leaving this on
        # locked_tokens would have put a ratio on the sheet whose numerator was not the number
        # printed directly above it.
        ("Lock rate = locked inside circulating ÷ circulating (stake held by wallets outside circulating excluded)",
         lambda r, p: calc(f"({_stake_inside(R, r, p, config.lock_display_metric(p['name']))})/({circ(r, p)})"),
         FMT_PCT, "calc"),
        ("Tokens locked — cross-check (protocol dashboard)", lambda r, p: pull(R.D(r, "locked_tokens_dashboard", "now")),
         FMT_NUM, "pull", False, {"metric": "locked_tokens_dashboard"}),
        # The divergence is meaningless without the cross-check, so it inherits the cross-check's
        # closed state rather than showing its own "n/a" beside it.
        ("Lock: contract vs dashboard divergence (flagged beyond tolerance)",
         lambda r, p: calc(f"{R.D(r, 'locked_tokens', 'now')}/{R.D(r, 'locked_tokens_dashboard', 'now')}-1"),
         FMT_PCT, "calc", False, {"closed_with": "locked_tokens_dashboard"}),
        ("Average lock duration (days)", lambda r, p: pull(R.D(r, "avg_lock_duration_days", "now")), FMT_NUM, "pull", False, {"metric": "avg_lock_duration_days"}),
        _token_yield(R, data_by_key),
        *_tbill_cols(R, [("Protocol staking yield (tokens)", _token_yield(R, data_by_key)[1])]),
        _topup_split(R),
        _topup_split_alltime(R),
        _protocol_yield(R, data_by_key),
        _voter_apr_voting_power(R),
        _published_apr(R),
        _virtual_share(R),
        # ===== PENDLE ONLY: THE OLD, UNMIGRATED CONTRACT'S OWN BALANCE. Added 2026-09-24. =====
        # locked_tokens (the column above, via contracts.spendle_underlying) is PENDLE.balanceOf
        # (sPENDLE) — the NEW contract, unchanged by this addition. This column is PENDLE.balanceOf
        # (the DEPRECATED vePENDLE contract, 0x4f30A9…3170210, address from Pendle's own
        # deployments/1-core.json, verified 2026-09-24) — Jake's instruction: a second figure,
        # never summed into locked_tokens. Blank for every project but Pendle.
        ("PENDLE still in the OLD vePENDLE contract (deprecated, unmigrated) — NEVER summed into locked_tokens",
         lambda r, p: pull(R.D(r, "locked_tokens_legacy_vependle", "now")),
         FMT_NUM, "pull", False, {"metric": "locked_tokens_legacy_vependle"}),
        # THE SANITY CHECK JAKE ASKED FOR: does new-contract + old-contract land near the ~100M+
        # the press reports? If it does, that is real corroboration this explanation (migration
        # left two pools of real PENDLE, not one inflated number) is the right one.
        # GATED ON ISNUMBER(the legacy figure): that column is blank for every project but
        # Pendle, and "blank + a number" evaluates to the number in Excel — without the guard
        # every other project would show a "sum" that is silently just its own locked_tokens.
        ("sPENDLE + old vePENDLE (tokens) — sanity check against the press's >100M figures",
         lambda r, p: calc(f"IF(ISNUMBER({R.D(r, 'locked_tokens_legacy_vependle', 'now')}),"
                           f"{R.D(r, 'locked_tokens', 'now')}+{R.D(r, 'locked_tokens_legacy_vependle', 'now')},"
                           f"{NA})"),
         FMT_NUM, "calc", False, {"metric": "locked_tokens_legacy_vependle"}),
        ("Effective float = circulating − ve locked − held reserve (a hold removes supply, a payout returns it)",
         lambda r, p: calc(
             f"({circ(r, p)})"
             f"-IF(ISNUMBER({R.D(r, 'locked_tokens', 'now')}),{_stake_inside(R, r, p)},0)"
             f"-IF(AND({R.C(r, 'Destination effect on float')}=\"locked_supply\","
             f"ISNUMBER({R.D(r, 'buyback_fund_balance', 'now')})),{R.D(r, 'buyback_fund_balance', 'now')},0)"),
         FMT_NUM, "calc"),
        ("Destination effect on float", lambda r, p: pull(R.C(r, "Destination effect on float")), FMT_TEXT, "pull"),
        # A hold and a payout move float in OPPOSITE directions, so they never share a formula.
        # Chainlink's Reserve is the case: a multi-day withdrawal timelock with no withdrawals
        # expected for years, so accumulated LINK is locked supply, not a distribution.
        # THE COLUMN IS FOR HELD BUYBACKS (Jake's probes15, root C): a destination of 'hold' shows the held balance
        # even where the hold is classed treasury-redeployable (NEAR's three DAO revenue wallets) — that class only
        # decides whether the effective float above subtracts it, which this cell does not change.
        ("Buyback to LOCKED supply (destination = hold) — reduces effective float",
         lambda r, p: (calc(f"IF(OR({R.C(r, 'Destination effect on float')}=\"locked_supply\","
                            f"{R.C(r, 'Buyback destination')}=\"hold\"),"
                            f"{R.D(r, 'buyback_fund_balance', 'now')},{NA})")), FMT_NUM, "calc", True),
        ("Buyback to YIELD PAYOUT (destination = distribute) — re-enters float, never netted against burn",
         # the DISTRIBUTED part of the buyback (Jake 2026-10-10): Aerodrome's fees go to voters but its Foundation
         # buy-and-lock is HELD, so the whole actual buyback is no longer a payout wherever the fees are
         lambda r, p: (calc(f"IF({R.C(r, 'Destination effect on float')}=\"yield_payout\","
                            f"{R.D(r, 'buyback_distributed_tokens', 'q0')},{NA})")), FMT_NUM, "calc"),
        ("Reserve balance — cross-check (protocol dashboard)", lambda r, p: pull(R.D(r, "buyback_fund_balance_dashboard", "now")),
         FMT_NUM, "pull", False, {"metric": "buyback_fund_balance_dashboard"}),
        ("Contract vs dashboard divergence (flagged when beyond tolerance)",
         lambda r, p: calc(f"{R.D(r, 'buyback_fund_balance', 'now')}/{R.D(r, 'buyback_fund_balance_dashboard', 'now')}-1"),
         FMT_PCT, "calc"),
        ("Umbrella staked $ (Aave only) — protocol risk cover, NOT AAVE supply, excluded from every float figure",
         lambda r, p: pull(R.D(r, "umbrella_staked_usd", "now")), FMT_USD, "pull", False, {"metric": "umbrella_staked_usd"}),
        *_trajectory(R, "revenue_usd", "Revenue"),
        ("Buyback % supply at Q1 (−3m window, split as at Q1)", lambda r, p: no_buy(p) or base_gated(p, gated(st(r, 'q1'), f"{rev(r, p, 'q1')}*{share(r, 'q1')}/{price(r, 'q1')}*{ann}/({circ(r, p)})", share(r, 'q1')), window='q1'), FMT_PCT, "calc", False, {"gate_window": "q1"}),
        ("Buyback % supply at Q2 (−6m, split as at Q2)", lambda r, p: no_buy(p) or base_gated(p, gated(st(r, 'q2'), f"{rev(r, p, 'q2')}*{share(r, 'q2')}/{price(r, 'q2')}*{ann}/({circ(r, p)})", share(r, 'q2')), window='q2'), FMT_PCT, "calc", False, {"gate_window": "q2"}),
        ("Buyback % supply at Q3 (−9m, split as at Q3)", lambda r, p: no_buy(p) or base_gated(p, gated(st(r, 'q3'), f"{rev(r, p, 'q3')}*{share(r, 'q3')}/{price(r, 'q3')}*{ann}/({circ(r, p)})", share(r, 'q3')), window='q3'), FMT_PCT, "calc", False, {"gate_window": "q3"}),
        ("Notes", lambda r, p: "; ".join(x for x in [p.get("notes", ""), (p.get("fee_split") or {}).get("note", "")] if x), FMT_TEXT, "text"),
    ]
    specs = _with_details("A3", specs, R)
    end = _write_table(ws, R, projects, specs, data_by_key, ["revenue_usd", "fees_usd", "price_usd", "circulating_supply", "actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "locked_tokens"],
                       key_cols=_key_cols(specs, "CIRCULATING RETIREMENT", "FDV RETIREMENT",
                                          "BUYBACK AS % OF SUPPLY", "Net absorption Q0"))
    _confidence_tally(ws, end + 2, projects, specs, data_by_key)
    _set_widths(ws, _widths(specs))
    return projects, end


def write_a1(ws, R: Refs, data_by_key: dict, months: list[str]):
    projects = [p for p in scoped_projects() if 1 in p["archetypes"]]
    _title(ws, "A1 — Infrastructure", "Demand: transactions, fees, fee per tx, active addresses (low weight), stablecoin supply, TVL (primary), RWA on chain (both sources, divergence shown). "
                                       "Supply: gross issuance, staking rate, float, net issuance after burn where an A4 block applies. Key ratio: fees ÷ issuance ($, issuance at 90d average price).")
    ann = ANN
    price = lambda r, w="q0": R.D(r, "price_usd", w)  # noqa: E731
    iss = lambda r, w="q0": R.D(r, "gross_issuance_tokens", w)  # noqa: E731
    circ = lambda r: R.D(r, "circulating_supply", "now")  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        *_valuation_head(R, circulating=True),
        *_a1_headline(R),
        ("Transactions Q0", lambda r, p: pull(R.D(r, "tx_count", "q0")), FMT_NUM, "pull", False,
         {"metric": "tx_count", "fmt_fn": lambda n: (f'#,##0" ({config.TX_COUNT_SCOPE[n]})"'
                                                     if n in config.TX_COUNT_SCOPE else None)}),
        _fees_q0_col(R),
        ("Fee per transaction ($)", lambda r, p: _fee_per_tx(R, r, p, price(r)), FMT_USD4, "calc"),
        ("Fee per transaction — what it divides",
         lambda r, p: (config.fee_per_tx(p["name"]) or {}).get("label", "fees_usd ÷ tx_count, Q0 per covered day"),
         FMT_TEXT, "text"),
        ("Active addresses (latest, low weight)", lambda r, p: pull(R.D(r, "active_addresses", "now")), FMT_NUM, "pull", False, {"metric": "active_addresses"}),
        ("Stablecoin supply on chain ($)", lambda r, p: pull(R.D(r, "stablecoin_supply_usd", "now")), FMT_USD, "pull", False, {"metric": "stablecoin_supply_usd"}),
        ("TVL — chain ($) [primary demand metric]", lambda r, p: pull(R.D(r, "tvl_usd", "now")), FMT_USD, "pull", True, {"metric": "tvl_usd"}),
        ("RWA on chain — DefiLlama RWA category ($)", lambda r, p: pull(R.D(r, "rwa_defillama_usd", "now")), FMT_USD, "pull", False, {"metric": "rwa_defillama_usd"}),
        # RWA.xyz columns REMOVED 2026-09-28: rwa_xyz_usd was retired as covered by
        # rwa_defillama_usd and is not in METRICS, so these lookups were #N/A on every row.
        ("Gross issuance Q0 (tokens)", lambda r, p: _fixed_supply_text(p) or pull(iss(r)), FMT_NUM, "pull", True,
         {"metric": "gross_issuance_tokens"}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Gross issuance Q0 ($ at avg price)", lambda r, p: _fixed_supply_text(p) or calc(f"{iss(r)}*{price(r)}"),
         FMT_USD, "calc"),
        # G11 C7: ONE BASIS with A4 — a pre-minted token whose minted figure is declared n/a reads its release
        ("Issuance as % of supply (annualised)",
         lambda r, p: calc(f"{_annualise(R, r, p, _a4_iss_metric(p['name']), R.D(r, _a4_iss_metric(p['name']), 'q0'))}"
                           f"/{circ(r)}"), FMT_PCT, "calc"),
        # staked_tokens WAS MERGED INTO locked_tokens (2bc2e5d); these three still looked it up and
        # read #N/A on every row until 2026-09-28.
        # G11 C3 (Jake's 10-21 run: Ethereum's staked / staking rate / float read n/a with beacon_chain_eth 43.67M
        # stored): the stake is the declared stake series where there is one (config.CP_STAKE_METRIC — ETH staked on
        # the beacon chain), as CP reads it, else locked_tokens
        ("Staked tokens (locked_tokens)",
         lambda r, p: pull(R.D(r, config.CP_STAKE_METRIC.get(p["name"], "locked_tokens"), "now")), FMT_NUM, "pull",
         False, {"metric_fn": lambda n: config.CP_STAKE_METRIC.get(n, "locked_tokens")}),
        ("Staking rate = staked inside circulating ÷ circulating",
         lambda r, p: calc(f"({_cp_lock(R, r, p)})/({_circ(R, r, p)})"), FMT_PCT, "calc"),
        # THE SAME FLOAT AS A2 AND CP (item 1): circulating − the lock inside it (_ff_lock; the declared stake series
        # where CP reads one)
        ("Resulting float = circulating − staked inside circulating",
         lambda r, p: calc(f"({_circ(R, r, p)})-({_cp_lock(R, r, p)})"), FMT_NUM, "calc"),
        # THE HEADLINE BURN, as A4 reads it (Jake's review of 97b29ae, item 5: NEAR's Master 61,580 vs A1 69,238 were two
        # series; the headline is config.a4_burn_metric — for NEAR the chain's record, Jake's run 2026-10-08)
        # EVERY BURN, fee-market burns included (Phase 2 amendment 1: they come off A4 and feed net issuance here)
        ("Gross burn Q0 (tokens) — every burn, fee-market included", lambda r, p: pull(R.D(r, config.a1_burn_metric(p["name"]), "q0")), FMT_NUM, "pull", True, {"metric_fn": config.a1_burn_metric}),
        # THE SAME NET AS A4'S HEADLINE (Phase 2 amendment 1: the fee-burn chains' net change lives here now): common days
        # where declared, self-reported where published, the equal-coverage guard otherwise
        ("NET ISSUANCE AFTER BURN Q0 (tokens) — issuance − every burn, on the basis A4 uses",
         lambda r, p: _zero_issuance(R, r, p, f"-{R.D(r, config.a1_burn_metric(p['name']), 'q0')}")
         or _net_guarded(R, r, p, _basis_iss(R, p), lambda r_, p_, w="q0": R.D(r_, config.a1_burn_metric(p_["name"]), w)),
         FMT_NUM, "calc", True),
        ("NET ISSUANCE AFTER BURN, annualised % of circulating (+ is net inflation; fee-market burns included)",
         lambda r, p: _net_inflation(R, r, p), FMT_PCT, "calc", True),
        # ISSUER RELEASES ON THEIR OWN LINE (accuracy pass item 9): a pre-minted supply mints nothing; what its issuer
        # wallets release into circulation is here, never inside net issuance (Total Yield subtracts it explicitly)
        ("Issuer releases Q0 (tokens) — pre-minted supply released into circulation, NOT issuance",
         lambda r, p: pull(R.D(r, "pool_release_tokens", "q0")) if _premint(p) else "", FMT_NUM, "pull", False,
         {"metric_fn": lambda n: "pool_release_tokens" if config.issuance_basis(n) == "pool_release_tokens" else None}),
        ("FEES ÷ ISSUANCE (x) — key ratio", lambda r, p: _no_issuance_text(p) or _coverage_guard(R, r, [("fees", "fees_usd", "q0"), ("issuance", "gross_issuance_tokens", "q0")],
                                      f"{_flow_rate(R, r, 'fees_usd', 'q0')}/({_flow_rate(R, r, 'gross_issuance_tokens', 'q0')}*{price(r, 'q0')})"), FMT_X, "calc", True),
        ("Fees ÷ issuance at Q1 (−3m window)", lambda r, p: _no_issuance_text(p) or _coverage_guard(R, r, [("fees", "fees_usd", "q1"), ("issuance", "gross_issuance_tokens", "q1")],
                                      f"{_flow_rate(R, r, 'fees_usd', 'q1')}/({_flow_rate(R, r, 'gross_issuance_tokens', 'q1')}*{price(r, 'q1')})"), FMT_X, "calc"),
        ("Fees ÷ issuance at Q2 (−6m)", lambda r, p: _no_issuance_text(p) or _coverage_guard(R, r, [("fees", "fees_usd", "q2"), ("issuance", "gross_issuance_tokens", "q2")],
                                      f"{_flow_rate(R, r, 'fees_usd', 'q2')}/({_flow_rate(R, r, 'gross_issuance_tokens', 'q2')}*{price(r, 'q2')})"), FMT_X, "calc"),
        ("Fees ÷ issuance at Q3 (−9m)", lambda r, p: _no_issuance_text(p) or _coverage_guard(R, r, [("fees", "fees_usd", "q3"), ("issuance", "gross_issuance_tokens", "q3")],
                                      f"{_flow_rate(R, r, 'fees_usd', 'q3')}/({_flow_rate(R, r, 'gross_issuance_tokens', 'q3')}*{price(r, 'q3')})"), FMT_X, "calc"),
        *_trajectory(R, "tvl_usd", "TVL"),
        *_trajectory(R, "fees_usd", "Fees"),
        *_trajectory(R, "stablecoin_supply_usd", "Stablecoins"),
        ("Notes", lambda r, p: p.get("notes", ""), FMT_TEXT, "text"),
    ]
    specs = _with_details("A1", specs, R)
    end = _write_table(ws, R, projects, specs, data_by_key, ["tx_count", "fees_usd", "tvl_usd", "stablecoin_supply_usd", "rwa_defillama_usd", "gross_issuance_tokens", "price_usd", "locked_tokens", "circulating_supply"],
                       key_cols=_key_cols(specs, "VALIDATOR STAKING YIELD", "TVL — chain", "FEES ÷ ISSUANCE"))
    _confidence_tally(ws, end + 2, projects, specs, data_by_key)
    _set_widths(ws, _widths(specs))
    # Monthly block for the time chart: fees ÷ issuance by month
    block = _monthly_block(ws, R, projects, end + 9, months, "Fees ÷ issuance by month (x) — fees ÷ (issuance tokens × monthly average price)",
                           lambda r, mc: calc(f"{R.M(r, 'fees_usd', mc)}/({R.M(r, 'gross_issuance_tokens', mc)}*{R.M(r, 'price_usd', mc)})"), FMT_X)
    return projects, end, block


def write_a2(ws, R: Refs, data_by_key: dict, months: list[str]):
    projects = [p for p in scoped_projects() if 2 in p["archetypes"]]
    _title(ws, "A2 — Coordination Mechanism", "Demand: physical/resource supply units, utilisation, end-user revenue ($). Supply: emissions to suppliers; split between supplier earnings from emissions vs actual customer payment. "
                                               "Key ratio: customer revenue per unit of emission, tracked over time. Most inputs come from Dune queries or manual overrides — see Data flags.")
    price = lambda r, w="q0": R.D(r, "price_usd", w)  # noqa: E731
    rev = lambda r, w="q0": R.D(r, "customer_revenue_usd", w)  # noqa: E731
    emi = lambda r, w="q0": R.D(r, "emissions_tokens", w)  # noqa: E731
    specs = [
        ("Project", lambda r, p: p["name"], FMT_TEXT, "text"),
        ("Symbol", lambda r, p: p["symbol"], FMT_TEXT, "text"),
        *_valuation_head(R, circulating=True),
        *_a2_headline(R),
        *_unlock_cols(R),
        ("Materiality", lambda r, p: pull(R.C(r, "Materiality")), FMT_TEXT, "pull"),
        ("Supply units (nodes / hotspots / GPUs), latest", lambda r, p: pull(R.D(r, "supply_units", "now")), FMT_NUM, "pull", False, {"metric": "supply_units"}),
        ("Capacity utilisation (latest)", lambda r, p: pull(R.D(r, "utilisation_pct", "now")), FMT_PCT, "pull", False, {"metric": "utilisation_pct"}),
        ("Customer revenue Q0 ($)", lambda r, p: pull(rev(r)), FMT_USD, "pull", True, {"metric": "customer_revenue_usd"}),
        # WHAT CUSTOMER REVENUE IS MADE OF (Chainlink, Jake 2026-10-05): the aggregator core and each
        # line that bypasses it, Q0 each; blank where a project declares no components.
        *[(f"  of which {lab} Q0 ($)", (lambda i: lambda r, p: (
            pull(R.D(r, _revenue_legs(p)[i], "q0")) if len(_revenue_legs(p)) > i else ""))(i),
           FMT_USD, "pull", False, {"metric_fn": (lambda i: lambda n: (_revenue_legs(config.PROJECT_BY_NAME[n]) + [None] * 9)[i])(i)})
          for i, lab in enumerate(_REVENUE_LEG_LABELS)],
        ("  Customer revenue — not measured",
         lambda r, p: (p.get("customer_revenue_components") or {}).get("not_measured", ""), FMT_TEXT, "text"),
        ("  Reserve inflow ÷ payments collected at the aggregator (Q0, $) — share converted to LINK",
         lambda r, p: _coverage_guard(R, r, [("reserve inflow", "actual_buyback_usd", "q0"), ("aggregator", "fees_usd", "q0")],
                                      f"{_flow_rate(R, r, 'actual_buyback_usd')}/{_flow_rate(R, r, 'fees_usd')}")
         if p.get("customer_revenue_components") else "", FMT_PCT, "calc"),
        ("Emissions to suppliers Q0 (tokens)", lambda r, p: pull(emi(r)), FMT_NUM, "pull", True, {"metric": "emissions_tokens"}),
        # THE BREAKDOWN (Jake, 2026-10-05, E): what the figure to the left is made of, where the
        # project measures emissions from its components (Aethir). Each is Q0 tokens over its own
        # covered days; "% of circulating, annualised" puts released and earned side by side.
        ("  Emissions basis — what feeds the figure to the left",
         lambda r, p: _emissions_basis(p, data_by_key), FMT_TEXT, "text"),
        *[(f"  of which {lab} Q0 (tokens)", (lambda m: lambda r, p: pull(R.D(r, m, "q0")))(m), FMT_NUM, "pull",
           False, {"metric": m}) for m, lab in _EMISSION_PARTS],
        ("  Emissions RELEASED, % of circulating (annualised over covered days)",
         lambda r, p: calc(f"{_annualise(R, r, p, 'emissions_tokens', emi(r))}/({_circ(R, r, p)})"), FMT_PCT, "calc",
         False, {"metric": "emissions_tokens"}),
        ("  Emissions EARNED (incl. vesting), % of circulating (annualised over covered days)",
         lambda r, p: calc(f"{_annualise(R, r, p, 'emissions_earned_tokens', R.D(r, 'emissions_earned_tokens', 'q0'))}"
                           f"/({_circ(R, r, p)})"), FMT_PCT, "calc", False, {"metric": "emissions_earned_tokens"}),
        ("Price — 90d average ($)", lambda r, p: pull(price(r)), FMT_USD4, "pull", False, {"metric": "price_usd"}),
        ("Emissions Q0 ($ at avg price)", lambda r, p: calc(f"{emi(r)}*{price(r)}"), FMT_USD, "calc"),
        ("CUSTOMER REVENUE PER TOKEN EMITTED ($/token) — key ratio", lambda r, p: _coverage_guard(R, r, [("customer revenue", "customer_revenue_usd", "q0"), ("emissions", "emissions_tokens", "q0")],
                                      f"{_flow_rate(R, r, 'customer_revenue_usd', 'q0')}/{_flow_rate(R, r, 'emissions_tokens', 'q0')}"), FMT_USD4, "calc", True),
        ("Customer revenue ÷ emissions value (x)", lambda r, p: _coverage_guard(R, r, [("customer revenue", "customer_revenue_usd", "q0"), ("emissions", "emissions_tokens", "q0")],
                                      f"{_flow_rate(R, r, 'customer_revenue_usd', 'q0')}/({_flow_rate(R, r, 'emissions_tokens', 'q0')}*{price(r)})"), FMT_X, "calc", True),
        ("Supplier earnings from emissions ($)", lambda r, p: calc(f"{emi(r)}*{price(r)}"), FMT_USD, "calc"),
        ("Supplier earnings from customers ($)", lambda r, p: pull(rev(r)), FMT_USD, "pull"),
        ("Customer share of supplier earnings", lambda r, p: _coverage_guard(R, r, [("customer revenue", "customer_revenue_usd", "q0"), ("emissions", "emissions_tokens", "q0")],
                                      f"{_flow_rate(R, r, 'customer_revenue_usd', 'q0')}/({_flow_rate(R, r, 'customer_revenue_usd', 'q0')}+{_flow_rate(R, r, 'emissions_tokens', 'q0')}*{price(r)})"), FMT_PCT, "calc"),
        ("Revenue per supply unit Q0 ($)", lambda r, p: calc(f"{rev(r)}/{R.D(r, 'supply_units', 'now')}"), FMT_USD, "calc"),
        ("Publisher Conviction ($, OriginTrail)", lambda r, p: pull(R.D(r, "publisher_conviction_usd", "now")), FMT_USD, "pull", False, {"metric": "publisher_conviction_usd"}),
        ("Staked tokens (float metric, not demand)", lambda r, p: pull(R.D(r, "locked_tokens", "now")), FMT_NUM, "pull", False, {"metric": "locked_tokens"}),
        ("Rev ÷ emission at Q1 (−3m, $/token)", lambda r, p: _coverage_guard(R, r, [("customer revenue", "customer_revenue_usd", "q1"), ("emissions", "emissions_tokens", "q1")],
                                      f"{_flow_rate(R, r, 'customer_revenue_usd', 'q1')}/{_flow_rate(R, r, 'emissions_tokens', 'q1')}"), FMT_USD4, "calc"),
        ("Rev ÷ emission at Q2 (−6m)", lambda r, p: _coverage_guard(R, r, [("customer revenue", "customer_revenue_usd", "q2"), ("emissions", "emissions_tokens", "q2")],
                                      f"{_flow_rate(R, r, 'customer_revenue_usd', 'q2')}/{_flow_rate(R, r, 'emissions_tokens', 'q2')}"), FMT_USD4, "calc"),
        ("Rev ÷ emission at Q3 (−9m)", lambda r, p: _coverage_guard(R, r, [("customer revenue", "customer_revenue_usd", "q3"), ("emissions", "emissions_tokens", "q3")],
                                      f"{_flow_rate(R, r, 'customer_revenue_usd', 'q3')}/{_flow_rate(R, r, 'emissions_tokens', 'q3')}"), FMT_USD4, "calc"),
        *_trajectory(R, "customer_revenue_usd", "Customer revenue"),
        *_trajectory(R, "supply_units", "Supply units"),
        *_trajectory(R, "emissions_tokens", "Emissions"),
        ("Notes", lambda r, p: p.get("notes", ""), FMT_TEXT, "text"),
    ]
    specs = _with_details("A2", specs, R)
    end = _write_table(ws, R, projects, specs, data_by_key, ["supply_units", "utilisation_pct", "customer_revenue_usd", "emissions_tokens", "price_usd", "publisher_conviction_usd"],
                       key_cols=_key_cols(specs, "FREE FLOAT", "SUPPLY TRAJECTORY", "CUSTOMER REVENUE PER TOKEN",
                                          "Customer revenue ÷ emissions value"))
    _confidence_tally(ws, end + 2, projects, specs, data_by_key)
    _set_widths(ws, _widths(specs))
    block = _monthly_block(ws, R, projects, end + 9, months, "Customer revenue per token emitted by month ($/token)",
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


def _hcol(ws, prefix: str, row: int = 4) -> int:
    """The column whose header (on the tab's header row) starts with `prefix`. Charts used to
    point at fixed indices, and every column added since pushed them onto the wrong series —
    the A3 chart was plotting the 90-day price. Looked up by name, they cannot drift."""
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=row, column=c).value
        if isinstance(v, str) and v.startswith(prefix):
            return c
    raise KeyError(f"no header starting {prefix!r} on {ws.title}")


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
    ch.add_data(Reference(ws_a3, min_col=_hcol(ws_a3, "BUYBACK AS % OF SUPPLY"), min_row=4, max_row=4 + len(a3p)), titles_from_data=True)
    ch.add_data(Reference(ws_a3, min_col=_hcol(ws_a3, "Actual buyback as % of supply"), min_row=4, max_row=4 + len(a3p)), titles_from_data=True)
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
    ch.add_data(Reference(ws_a4, min_col=_hcol(ws_a4, "Gross burn Q0 ($"), min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
    ch.add_data(Reference(ws_a4, min_col=_hcol(ws_a4, "Gross issuance Q0 ($"), min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
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
    ch.add_data(Reference(ws_a4, min_col=_hcol(ws_a4, "PERMANENT BURN YIELD"), min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
    ch.add_data(Reference(ws_a4, min_col=_hcol(ws_a4, "Issuance as % of supply"), min_row=4, max_row=4 + len(a4p)), titles_from_data=True)
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



CRED_HEAD = ["Project", "Tab", "Cell", "What is checked", "Our value", "Independent reference (source)",
             "Reference value", "Reference date", "Gap %", "Tolerance ±", "Verdict",
             "Note — why, and what would resolve it"]


# A JUDGED ROW AT OR PAST THIS SHARE OF ITS TOLERANCE READS "(NEAR LIMIT)" — still a pass (Jake, 2026-10-11)
NEAR_LIMIT_SHARE = 0.8


def _now_staleness(ours: dict, row: dict, asof) -> tuple[str, str] | None:
    """("STALE" | "FRESH", label) for an input row judged on 'now' figures, or None when both are at most a day old
    (Jake's run on ab3c7fe: one failed read must not open sign-off). FRESH = the last stored value, older than today
    but within credibility.now_max_age; STALE = older than that, and the row is CHECK."""
    parts, worst = [], None
    for side, day, lim in (("ours", ours.get("latest_date"), ours.get("max_age")),
                           ("ref", row.get("date"), row.get("ref_max_age"))):
        if not day or not lim:
            continue
        age = (pd.Timestamp(asof).normalize() - pd.Timestamp(str(day)[:10])).days
        if age <= 1:                                     # today's or yesterday's: the ordinary state, unlabelled
            continue
        over = age > int(lim)
        worst = "STALE" if over or worst == "STALE" else "FRESH"
        parts.append(f"{side} last stored {str(day)[:10]}, {age} d old {'>' if over else '<='} {lim} d")
    return (worst, "; ".join(parts)) if parts else None


def _empty_by_design(wb, ref: str) -> bool:
    """True when "'<sheet>'!<cell>" names a written sheet's cell that holds nothing (None or "") — a cell the tab
    leaves blank because the column does not apply to the project (coverage_audit: N/A BY DESIGN)."""
    sh, _, addr = ref.lstrip("=").rpartition("!")
    sh = sh.strip("'")
    if sh not in wb.sheetnames or not addr:
        return False
    try:
        return wb[sh][addr].value in (None, "")
    except (ValueError, KeyError):
        return False


def write_credibility(ws, R: Refs, data_by_key: dict, long: pd.DataFrame, asof) -> list[dict]:
    """THE CREDIBILITY TAB (Jake, 2026-10-05): every headline cell on A1-A4 for the projects in
    config.CREDIBILITY_PROJECTS, plus each project's input rows, beside an independent reference.
    OUR VALUE IS THE HEADLINE CELL ITSELF (a formula to it), so the row can never disagree with the tab
    it checks. Gap and verdict are formulas; UNVERIFIABLE and the not-yet-read CHECKs are literals
    with their reason. A re-read of our own source is FRESH-only — never PASS. Rows: credibility.py."""
    import credibility as cred
    from openpyxl.formatting.rule import FormulaRule
    in_scope = {p["name"] for p in scoped_projects()}
    frames = [g for g in _LAST_GROUPS.values() if g is not None and not g.empty]
    cred.VIEWS_LONG = (pd.concat(frames, ignore_index=True).assign(date=lambda d: pd.to_datetime(d["date"]))
                       if frames else None)
    rows = cred.build_rows(_HEADLINE_CELLS, data_by_key, long, asof, projects=in_scope,
                           previous=PREVIOUS_SNAPSHOT.get("values"), prev_date=PREVIOUS_SNAPSHOT.get("date", ""))
    projects = [n for n in config.CREDIBILITY_PROJECTS if n in in_scope]
    parked = "; ".join(f"{k}: {v}" for k, v in config.CREDIBILITY_PARKED.items())
    _title(ws, "Credibility — every headline beside an independent reference",
           "PASS = an independent source agrees within tolerance · '(inputs)' = a derived figure, judged by its input "
           "rows: PASS only when every input passes · CHECK = it disagrees, or a reference exists "
           "but was not read (the note says how to resolve) · FRESH-only = the only reference re-reads our own "
           "source: current, not verified — never a PASS · UNVERIFIABLE = no independent source exists (the note "
           f"says why). Our value is the headline cell itself. Parked: {parked or 'none'}.")
    s0 = 4
    # THE FINISH LINE (overnight sign-off round 2026-10-07): a project is SIGNED OFF when every row is PASS, N/A,
    # DOCUMENTED LIMITATION, MATURING or VERIFIED FINDING (credibility.SIGNED); OPEN = the rest.
    _header(ws, s0, ["Project", "PASS", "CHECK", "FRESH-only", "UNVERIFIABLE", "N/A (no figure by design)", "Rows",
                     "DOCUMENTED LIMITATION", "MATURING", "VERIFIED FINDING", "OPEN", "Sign-off"])
    d_head = s0 + len(projects) + 3
    d0, d1 = d_head + 1, d_head + max(len(rows), 1)
    K, A = f"$K${d0}:$K${d1}", f"$A${d0}:$A${d1}"
    for i, n in enumerate(projects + ["All"]):
        r = s0 + 1 + i
        ws.cell(row=r, column=1, value=n).font = F_BOLD if n == "All" else F_BASE
        crit = (f'{A},$A{r},' if n != "All" else "")
        for j, v in enumerate(("PASS*", "CHECK*", "FRESH-only*", "UNVERIFIABLE*", "N/A*"), start=2):
            ws.cell(row=r, column=j, value=f'=COUNTIFS({crit}{K},"{v}")')
        ws.cell(row=r, column=7, value=f"=COUNTIF({A},$A{r})" if n != "All" else f"=COUNTA({A})")
        for j, v in enumerate(("DOCUMENTED LIMITATION*", "MATURING*", "VERIFIED FINDING*"), start=8):
            ws.cell(row=r, column=j, value=f'=COUNTIFS({crit}{K},"{v}")')
        ws.cell(row=r, column=11, value=f"=G{r}-B{r}-F{r}-H{r}-I{r}-J{r}")
        ws.cell(row=r, column=12, value=f'=IF(K{r}=0,"SIGNED OFF","OPEN")').font = F_BOLD
    _header(ws, d_head, CRED_HEAD)

    def text(v):                     # a note that starts with "=" must never become a formula
        return (" " + v) if isinstance(v, str) and v[:1] in "=+-@" else v
    # G11 A2: A CONTINUITY CHECK NAMES THE CELLS IT LOST. The headline tabs are complete here: each held cell is evaluated
    # (xlcalc, the values the file carries) and the note leads with those that no longer read a number — Jake read the
    # old note's full list of previous values and found every one present
    _X = None
    for row in rows:
        held = row.pop("held", None)
        if row.get("mode") == "continuity" and held:
            if _X is None:
                import sanity                              # noqa: PLC0415
                import xlcalc                              # noqa: PLC0415
                _X = xlcalc.Workbook(ws.parent)
            lost = []
            for sh, cell, head, was in held:
                try:
                    c = ws.parent[sh][cell]
                    now = _X.value(sh, c.row, c.column)
                except Exception as e:  # noqa: BLE001
                    now = f"unevaluable ({type(e).__name__})"
                if (not isinstance(now, (int, float)) or isinstance(now, bool)) and sanity.stated_number(now) is None:
                    lost.append(f"{sh[:2]} {head[:60]} ({cell}) was {was:,.6g}, now {str(xlcalc.display(now))[:80]}")
            row["note"] = ((f"LOST NOW ({len(lost)}): " + "; ".join(lost) + " | ") if lost else
                           "every held headline still reads a number | ") + row["note"]
            row["note"] = row["note"][:2000]
    for k, row in enumerate(rows):
        r = d0 + k
        ws.cell(row=r, column=1, value=row["project"])
        ws.cell(row=r, column=2, value=row["tab"])
        ws.cell(row=r, column=3, value=row["cell"])
        ws.cell(row=r, column=4, value=text(row["what"]))
        ours = row["ours"]
        f = (f"={ours['cell']}" if "cell" in ours else ours.get("value") if "py" in ours else
             f'=IFERROR({R.D(r, ours["metric"], ours.get("window", "now"))},{NA})')
        if "fallback" in ours:                          # the last stored value, dated (credibility.now_max_age)
            d = R.D(r, ours["metric"], ours.get("window", "now"))
            f = f'=IF(ISNUMBER({d}),{d},{ours["fallback"]!r})'
        # G12 A2 (Jake 2026-10-21): a row reading a cell that is EMPTY BY DESIGN (CP's BUSINESS 4 for a project with
        # three series, the second market-share basis for a non-chain, A3's protocol staking yield where nothing is
        # staked for yield) showed "our value 0" in Excel. It says so instead, and is N/A — never a measured zero.
        if "cell" in ours and _empty_by_design(ws.parent, ours["cell"]):
            f = "no figure by design"
            row.update(mode="static", verdict="N/A (no figure by design)",
                       note=f"the cell {ours['cell']} is empty by design (the column does not apply to this project)"
                            + (f" | {row['note']}" if row.get("note") else ""))
        stale = _now_staleness(ours, row, asof)
        c = ws.cell(row=r, column=5, value=f)
        _style(c, "pull", row.get("fmt") or FMT_NUM2)
        ws.cell(row=r, column=6, value=text(row["source"]))
        g = ws.cell(row=r, column=7, value=row["value"])
        _style(g, "input", row.get("fmt") or FMT_NUM2)
        ws.cell(row=r, column=8, value=row["date"])
        # A ZERO REFERENCE (a halted programme) is met by a zero, and by nothing else.
        # G13 7 (Jake's 10-21 store: 345 Credibility formula cells saved blank, read as 0 by a recalculating reader):
        # the gap with nothing to compare says which side is missing — never an empty string
        gap = ws.cell(row=r, column=9, value=f'=IF(AND(ISNUMBER(E{r}),ISNUMBER(G{r})),'
                                             f'IF(G{r}=0,IF(E{r}=0,0,1),(E{r}-G{r})/ABS(G{r})),'
                                             f'IF(ISNUMBER(G{r}),"no figure of ours","no reference value"))')
        gap.number_format = FMT_PCT
        tol = row.get("tol")
        t = ws.cell(row=r, column=10, value=(tol / 100.0) if tol is not None else None)
        t.number_format = FMT_PCT
        if row["mode"] == "static" and row["verdict"] == "N/A":
            # A CELL DECLARED EMPTY BY DESIGN THAT SHOWS A NUMBER is a contradiction — said, not hidden.
            v = f'=IF(AND(ISNUMBER(E{r}),E{r}<>0),"CHECK (figure where none is expected)","N/A")'
        elif row["mode"] == "static":
            v = row["verdict"]
        elif row["mode"] == "continuity":
            # zero tolerance: every headline that read a value last run reads one now, or it is a CHECK
            v = (f'=IF(E{r}=G{r},"PASS","CHECK (HEADLINE LOST: of the G headlines that read a value in the previous '
                 f'run, only E still do — the note lists them; Review Queue: sanity headline_lost names each)")')
        elif row["mode"] == "derived":
            cells = [f"K{d0 + i}" for i in row.get("input_rows") or ()]
            anycheck = "OR(" + ",".join(f'LEFT({x},5)="CHECK"' for x in cells) + ")" if cells else "FALSE"
            allpass = "AND(" + ",".join(f'OR(LEFT({x},4)="PASS",LEFT({x},3)="N/A")' for x in cells) + ")"
            anyunv = "OR(" + ",".join(f'LEFT({x},5)="UNVER"' for x in cells) + ")"
            # SIGNED INPUTS (sign-off round): every input PASS / N/A / LIMITATION / MATURING / FINDING -> the derived
            # row carries the weakest of them (MATURING, then LIMITATION, then FINDING)
            allsigned = "AND(" + ",".join(
                f'OR(LEFT({x},4)="PASS",LEFT({x},3)="N/A",LEFT({x},10)="DOCUMENTED",LEFT({x},8)="MATURING",'
                f'LEFT({x},8)="VERIFIED")' for x in cells) + ")"
            anymat = "OR(" + ",".join(f'LEFT({x},8)="MATURING"' for x in cells) + ")"
            anylim = "OR(" + ",".join(f'LEFT({x},10)="DOCUMENTED"' for x in cells) + ")"
            if not cells:
                v = "UNVERIFIABLE (inputs)"
            elif row.get("missing_inputs"):
                v = f'=IF({anycheck},"CHECK (inputs)","UNVERIFIABLE (inputs)")'
            else:
                v = (f'=IF({anycheck},"CHECK (inputs)",IF({allpass},"PASS (inputs)",'
                     f'IF({allsigned},IF({anymat},"MATURING (inputs)",IF({anylim},"DOCUMENTED LIMITATION (inputs)",'
                     f'"VERIFIED FINDING (inputs)")),'
                     f'IF({anyunv},"UNVERIFIABLE (inputs)","FRESH-only (inputs)"))))')
        else:
            # a first-party-only row documented as such (sign-off round): its FRESH-only reads as the limitation
            ok = ((row.get("fresh_label") or "FRESH-only") if row["mode"] == "same_source" else "PASS")
            if stale and stale[0] == "STALE":
                v = f"CHECK (STALE: {stale[1]})"
            else:
                tag = f" [{stale[1]}]" if stale else ""
                # NEAR LIMIT (Jake, 2026-10-11): a judged row whose |gap| is at least 80% of its tolerance still
                # passes, and says so — it starts with the same word, so sign-off counts are unchanged. A zero
                # tolerance (an exact match required) is never "near": its only pass is a gap of 0
                v = (f'=IF(NOT(ISNUMBER(G{r})),"CHECK (no reference)",IF(NOT(ISNUMBER(E{r})),"CHECK (no figure)",'
                     f'IF(ABS(I{r})<=J{r},IF(AND(J{r}>0,ABS(I{r})>={NEAR_LIMIT_SHARE}*J{r}),"{ok} (NEAR LIMIT){tag}","{ok}{tag}"),'
                     f'"CHECK{tag}")))')
        ws.cell(row=r, column=11, value=v).font = F_BOLD
        note = row.get("note") or ""
        # A MODELLED LEG UNDER THE HEADLINE (Jake 2026-10-12, Phase 1 follow-up item 2): said on its row
        if row.get("tab") and row.get("cell") and row["tab"] in ws.parent.sheetnames:
            hf = ws.parent[row["tab"]][row["cell"]].value
            mods = sorted(m for m in trajectory.referenced_metrics(hf)
                          if (g := _LAST_GROUPS.get((row["project"], m))) is not None and not g.empty
                          and g["source"].astype(str).map(config.is_modelled).any())
            if mods:
                note = (f"MODELLED history under this headline ({', '.join(mods)}: config.MODELLED_POINTS) — the "
                        f"trajectory quarters it covers are excluded from any scoring; Q0 is judged as read"
                        + (f" | {note}" if note else ""))
        mw = config.AERODROME_MIGRATION_WATCH
        if row["project"] == mw["project"] and row.get("id") in mw["label_rows"] and migration_watch_on(asof):
            note = mw["label"] + (f" | {note}" if note else "")
        ws.cell(row=r, column=12, value=text(note))
    rng = f"K{d0}:K{d1}"
    for test, fill in (('ISNUMBER(SEARCH("NEAR LIMIT",{c}))', FILL_AMBER),
                       ('LEFT({c},4)="PASS"', FILL_GREEN), ('LEFT({c},5)="CHECK"', FILL_RED),
                       ('LEFT({c},10)="DOCUMENTED"', FILL_GREEN), ('LEFT({c},8)="MATURING"', FILL_AMBER),
                       ('LEFT({c},8)="VERIFIED"', FILL_GREEN),
                       ('LEFT({c},5)="FRESH"', FILL_AMBER), ('LEFT({c},5)="UNVER"', FILL_UNCONFIRMED),
                       ('LEFT({c},3)="N/A"', FILL_UNCONFIRMED)):
        ws.conditional_formatting.add(rng, FormulaRule(formula=[test.format(c=f"K{d0}")], fill=fill))
    for col, w in zip("ABCDEFGHIJKL", (13, 18, 22, 60, 16, 48, 16, 12, 9, 10, 20, 90)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = ws.cell(row=d0, column=5)
    ws.auto_filter.ref = f"A{d_head}:L{d1}"
    return rows


def write_gap_report(ws, gaps: pd.DataFrame, run_id: str | None):
    """The to-do list. Every metric no tier could resolve, with the reason and the fix.

    Anything unresolved after all tiers appears here, not as a blank cell on Master. A silently
    stale cell is worse than a visible gap; a blank one is worse still.
    """
    _title(ws, "Gap Report — the to-do list",
           "Ranked by priority. P1 = a headline metric with NO automated route of any kind. P2 = a decision a human must "
           "make. P3/P4 = blocked on something specific and fixable (an address, a read method), headline first. P6 = no "
           "source covers it yet. P5 = ANSWERED, NOT OPEN — checked and absent, refused by robots.txt, deliberately "
           "disabled, or pointing at the row that carries the figure; listed last with its reasoning, never counted as open "
           "work. Work top down.")
    headers = ["Priority", "Project", "Metric", "Tiers attempted", "Reason unresolved", "What would fix it"]
    _header(ws, 4, headers)
    r = 5
    BOARD_INPUTS["gaps"].clear()
    GAP_ROWS.clear()
    if gaps is None or gaps.empty:
        ws.cell(row=r, column=1, value="No gaps — every applicable metric resolved.").font = F_BOLD
        _set_widths(ws, {"A": 14, "B": 16, "C": 26, "D": 12, "E": 62, "F": 86})
        return r
    # G11 A5b: A GAP THE STORE HAS SINCE ANSWERED IS NOT ON THE LIST. The run's gap detection judges the series one
    # route was meant to fill (Plume fees_usd "DefiLlama returned no data") while another route stored it (Blockscout's
    # txnsFee, current): a row whose (project, metric) holds a current stored or view series is dropped, said in one line
    g = gaps.copy()
    keep = [not _gap_answered_by_store(str(pj), str(m)) for pj, m in zip(g["project"], g["metric"])]
    dropped = g[[not k for k in keep]]
    g = g[keep]
    if not dropped.empty:
        ws.cell(row=2, column=8, value=f"{len(dropped)} gap row(s) dropped: a current series is stored for them — "
                                       + "; ".join(f"{a}/{b}" for a, b in zip(dropped["project"], dropped["metric"]))[:400]
                ).font = F_SUB
    if g.empty:
        ws.cell(row=r, column=1, value="No gaps — every applicable metric resolved.").font = F_BOLD
        return r
    # config gaps first (they block derived figures), then by project, then metric
    g["_open"] = g["metric"].astype(str).str.startswith("[open]")
    g["_config"] = g["metric"].astype(str).str.startswith("[config]")
    if "priority" not in g.columns:
        g["priority"], g["priority_label"] = 5, "P5 uncovered"
    # ANSWERED ROWS LAST: they are the record of what was settled, not the next thing to do.
    g["_answered"] = g["priority"] == 5
    g = g.sort_values(["_answered", "priority", "project", "metric"])
    for (proj, pr), n in g.groupby(["project", "priority"]).size().items():      # the Coverage scoreboard (G4 7f)
        BOARD_INPUTS["gaps"][(str(proj), int(pr))] = int(n)
    GAP_ROWS[:] = g.to_dict("records")                       # the MISSING status's route and fix (G5 item 5g)
    for row in g.to_dict("records"):
        is_config = bool(row["_config"]) or bool(row["_open"])
        vals = [row.get("priority_label", ""), row["project"], row["metric"], row.get("tiers_attempted", ""),
                row["reason"], row.get("suggestion", "")]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=j, value=str(v) if v is not None else "")
            _literal(c)
            c.font = F_BASE
            c.number_format = FMT_TEXT
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if str(row.get("priority_label", "")).startswith("P1"):
                c.fill = FILL_STALE          # highest priority: no automated route at all
            elif is_config:
                c.fill = FILL_UNCONFIRMED
            if j == 5 and not is_config:
                c.font = Font(name=FONT, size=10, color="C00000")
        ws.row_dimensions[r].height = 30
        r += 1
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:F{r - 1}"
    _set_widths(ws, {"A": 14, "B": 16, "C": 26, "D": 12, "E": 62, "F": 86})
    return r


def write_staging(ws, staged: pd.DataFrame, run_id: str | None):
    """Figures a source returned that are deliberately NOT metrics.

    Nothing on this sheet feeds a calculation anywhere in the workbook, and nothing should start
    doing so by accident. It exists so a useful column found while mapping a query is neither
    thrown away nor quietly promoted into a number somebody is relying on.
    """
    _title(ws, "Staging — captured, used by nothing",
           "Values a source returned that no metric in the library takes. They are recorded so the decision to "
           "adopt one can be made on real numbers rather than re-discovered later. NO cell on any other sheet "
           "reads this one. Promoting a figure from here into a metric is a deliberate change to config.py.")
    headers = ["Project", "Field", "Date", "Value", "Source", "Tier", "Note"]
    _header(ws, 4, headers)
    r = 5
    if staged is None or staged.empty:
        ws.cell(row=r, column=1, value="Nothing staged this run.").font = F_BOLD
        _set_widths(ws, {"A": 16, "B": 20, "C": 11, "D": 18, "E": 20, "F": 6, "G": 80})
        return
    # A daily staging column is 794 rows per backfill, and two of them bury the sheet. The
    # staging TABLE keeps every row — nothing is discarded — but the sheet shows the most recent
    # slice per field and says how many it is not showing, so the count stays honest.
    staged = staged.sort_values(["project", "name", "date"])
    shown, hidden = [], 0
    for (proj, field), g in staged.groupby(["project", "name"], sort=True):
        hidden += max(0, len(g) - STAGING_ROWS_PER_FIELD)
        shown.extend(g.tail(STAGING_ROWS_PER_FIELD).to_dict("records"))
    if hidden:
        c = ws.cell(row=r, column=1,
                    value=f"Showing the most recent {STAGING_ROWS_PER_FIELD} row(s) per field. "
                          f"{hidden:,} older row(s) are in the staging table in metrics.db, not discarded.")
        c.font = F_SUB
        r += 1
    for row in shown:
        ws.cell(row=r, column=1, value=row["project"]).font = F_BASE
        ws.cell(row=r, column=2, value=row["name"]).font = F_BASE
        ws.cell(row=r, column=3, value=row.get("date") or "").font = F_BASE
        v = row.get("value")
        c = ws.cell(row=r, column=4)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            c.value, c.font, c.number_format = "n/a", Font(name=FONT, size=10, color="999999"), FMT_TEXT
        else:
            c.value, c.font, c.number_format = float(v), F_BASE, FMT_NUM2
        ws.cell(row=r, column=5, value=str(row.get("source") or "")).font = F_BASE
        t = row.get("tier")
        ws.cell(row=r, column=6, value="" if t is None or pd.isna(t) else int(t)).font = F_BASE
        c = ws.cell(row=r, column=7, value=str(row.get("note") or ""))
        _literal(c)
        c.font = F_SUB
        c.number_format = FMT_TEXT
        r += 1
    _set_widths(ws, {"A": 16, "B": 20, "C": 11, "D": 18, "E": 20, "F": 6, "G": 80})


def _gap_answered_by_store(project: str, metric: str) -> bool:
    """A metric row (not an [open] / [config] / [answered] question) whose series is held — stored or built at read
    time — and current by its own cadence at the build's as-of (G11 A5b)."""
    if metric.startswith("[") or not _WINDOWS.get("q0"):
        return False
    g = _LAST_GROUPS.get((project, metric))
    if g is None or g.empty:
        return False
    asof = pd.Timestamp(_WINDOWS["q0"][1])
    last = pd.to_datetime(g["date"]).max().normalize()
    return (asof - last).days <= _stale_limit(project, metric, GLOBALS["stale_after_days"], g)


def _write_manual_quarterly(ws, row: int, long: pd.DataFrame) -> int:
    """Figures that are typed in, not fetched — with when each was last entered.

    Deliberately NOT in the gap list above. A number that changes once a year is not an
    unresolved gap, and a permanent entry on a to-do list is how the to-do list stops being read.
    """
    entries = [(p["name"], m) for p in scoped_projects() for m in (p.get("manual_quarterly") or ())]
    ws.cell(row=row, column=1,
            value=f"Manual — review quarterly ({len(entries)}). Hand-entered by design, not gaps. "
                  f"Stale after {config.MANUAL_QUARTERLY_STALE_DAYS} days.").font = F_BOLD
    row += 1
    _header(ws, row, ["Project", "Metric", "Last entered", "Age (days)", "Value", "Status"])
    ws.row_dimensions[row].height = 18
    row += 1
    if not entries:
        ws.cell(row=row, column=1, value="none").font = F_SUB
        return row + 1
    asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    for project, metric in sorted(entries):
        g = long[(long["project"] == project) & (long["metric"] == metric)] if not long.empty else long
        entered, age, value = "never", None, None
        # G11 A5c: a series FILLED by a route (Aethir's utilisation_pct, derived at read time) is not due for a hand
        # entry — "never — DUE" sat beside a stored 0.642
        filled = _LAST_GROUPS.get((project, metric))
        if (g is None or g.empty or not bool(g.sort_values("date").iloc[-1].get("is_manual", True))) \
                and filled is not None and not filled.empty:
            last = filled.sort_values("date").iloc[-1]
            if not str(last["source"]).startswith("manual"):
                vals = [project, metric, str(last["date"])[:10], None, float(last["value"]),
                        f"filled by {str(last['source'])[:60]} — no hand entry needed"]
                for j, v in enumerate(vals, start=1):
                    c = ws.cell(row=row, column=j, value="n/a" if v is None else v)
                    c.font = F_BASE
                    c.number_format = FMT_NUM if isinstance(v, (int, float)) else FMT_TEXT
                row += 1
                continue
        if g is not None and not g.empty:
            last = g.sort_values("date").iloc[-1]
            entered = str(last.get("entered_on") or last["date"])[:10]
            value = float(last["value"])
            try:
                age = (asof - pd.Timestamp(entered)).days
            except (ValueError, TypeError):
                age = None
        overdue = age is None or age > config.MANUAL_QUARTERLY_STALE_DAYS
        vals = [project, metric, entered, age, value,
                "DUE — enter a fresh figure" if overdue else "current"]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=row, column=j)
            if v is None:
                c.value, c.font, c.number_format = "n/a", Font(name=FONT, size=10, color="999999"), FMT_TEXT
            else:
                c.value = v
                c.font = F_BASE
                c.number_format = FMT_NUM if isinstance(v, (int, float)) else FMT_TEXT
            if overdue:
                c.fill = FILL_AMBER
        row += 1
    return row + 1


def write_review_queue(ws, review: pd.DataFrame, run_id: str | None):
    """Values rejected by sanity bounds or flagged by the change threshold.

    out_of_bounds     -> REJECTED, not stored. A wrong number in the sheet is worse than a gap.
    change_threshold  -> STORED BUT FLAGGED. A genuine step change (a halving, a one-off burn)
                         must not be silently dropped, so it is kept and surfaced here.
    address_unverified-> a contract address read without being verified against protocol docs.
    """
    _title(ws, "Review Queue",
           "Values the validation layer would not accept silently. Out-of-bounds values were REJECTED and are not in the store. "
           "Large moves were STORED BUT FLAGGED — check them before trusting the cell. Unverified addresses were read under "
           "TOKEN_METRICS_ALLOW_UNVERIFIED. Bounds and thresholds are set per metric in config.py and per source in sources.yaml.")
    # "Compared against" and the prior DATE sit next to the prior VALUE deliberately. A
    # change_threshold row used to say a number moved without saying against what, and twelve of
    # them on one run were all the same comparison — a Sunday against a Friday. The two dates and
    # the rule that picked them are the first thing a reader needs and the last thing they could
    # reconstruct from the sheet.
    headers = ["Project", "Metric", "Date", "Prior date", "Value", "Prior value", "Change",
               "Reason", "Action", "Source", "Tier", "Compared against"]
    _header(ws, 4, headers)
    r = 5
    BOARD_INPUTS["review_open"].clear()
    if review is None or review.empty:
        ws.cell(row=r, column=1, value="Nothing flagged this run.").font = F_BOLD
        _set_widths(ws, {"A": 16, "B": 26, "C": 11, "D": 13, "E": 18, "F": 18, "G": 11, "H": 18, "I": 15, "J": 26, "K": 6, "L": 64})
        return
    rv = review.sort_values(["reason", "project", "metric"])
    # ANSWERED ROWS LAST, NOT COUNTED (G4 7b): a row config.REVIEW_ANSWERED decides is written under its own heading
    # with the decision and the numbers; everything else is open work
    recs = rv.to_dict("records")
    answered = [(row, config.review_answered(row)) for row in recs]
    open_rows = [row for row, a in answered if a is None]
    answered = [(row, a) for row, a in answered if a is not None]
    for row in open_rows:
        BOARD_INPUTS["review_open"][str(row["project"])] += 1
    for row in open_rows:
        rejected = row.get("action") == "rejected"
        ws.cell(row=r, column=1, value=row["project"]).font = F_BASE
        ws.cell(row=r, column=2, value=row["metric"]).font = F_BASE
        ws.cell(row=r, column=3, value=row.get("date") or "").font = F_BASE
        # BLANK, NOT "n/a": an out_of_bounds row has no prior date because it made no comparison,
        # which is a different thing from a comparison whose prior date is unknown.
        ws.cell(row=r, column=4, value=row.get("prior_date") or "").font = F_BASE
        for j, key, fmt in ((5, "value", FMT_NUM2), (6, "prior_value", FMT_NUM2)):
            v = row.get(key)
            c = ws.cell(row=r, column=j)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                c.value, c.font, c.number_format = "n/a", Font(name=FONT, size=10, color="999999"), FMT_TEXT
            else:
                c.value, c.font, c.number_format = float(v), F_BASE, fmt
        # change is a formula so the reader can see the arithmetic
        c = ws.cell(row=r, column=7, value=f"=IFERROR(E{r}/F{r}-1,{NA})")
        _style(c, "calc", FMT_PCT)
        for j, key in ((8, "reason"), (9, "action"), (10, "source"), (12, "basis")):
            c = ws.cell(row=r, column=j, value=str(row.get(key) or ""))
            c.font = F_BASE
            c.number_format = FMT_TEXT
        t = row.get("tier")
        ws.cell(row=r, column=11, value="" if t is None or pd.isna(t) else int(t)).font = F_BASE
        fill = FILL_STALE if rejected else FILL_REVIEW
        for j in range(1, 13):
            if not ws.cell(row=r, column=j).fill.fgColor.rgb or ws.cell(row=r, column=j).fill.patternType is None:
                ws.cell(row=r, column=j).fill = fill
        r += 1
    last_open = r - 1
    if answered:
        r += 1
        ws.cell(row=r, column=1, value=f"ANSWERED — not open ({len(answered)}): decided in config.REVIEW_ANSWERED, "
                                       f"with the numbers; listed so the decision is on the sheet, never counted as "
                                       f"open work").font = F_SUB
        r += 1
        for row, a in answered:
            vals = [row["project"], row["metric"], row.get("date") or "", row.get("prior_date") or "",
                    row.get("value"), row.get("prior_value"), "", row.get("reason") or "",
                    f"ANSWERED: {a['decision']}", str(row.get("source") or ""), row.get("tier"), a["why"]]
            for j, v in enumerate(vals, start=1):
                if isinstance(v, float) and pd.isna(v):
                    v = None
                c = ws.cell(row=r, column=j, value=v if not isinstance(v, float) else float(v))
                c.font = F_SUB
            r += 1
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:L{max(last_open, 4)}"
    _set_widths(ws, {"A": 16, "B": 26, "C": 11, "D": 13, "E": 18, "F": 18, "G": 11, "H": 18, "I": 15, "J": 26, "K": 6, "L": 64})


def run_log_actions(runlog: pd.DataFrame | None) -> list[str]:
    """The lines the Run Log opens with when something needs Jake: an expired NEAR BigQuery login this run (Round G
    1.7(a)), with the two gcloud commands."""
    if runlog is None or runlog.empty or "source" not in runlog:
        return []
    from fetch.near_bigquery import LOGIN_COMMANDS
    nb = runlog[runlog["source"].astype(str) == "near_bigquery"]
    msgs = " ".join(nb["message"].astype(str)).lower() if not nb.empty else ""
    if not any(k in msgs for k in ("re-authentication", "near bigquery skipped", "reauthentication")):
        return []
    return ["ACTION NEEDED — NEAR BigQuery: the Google login expired, so every NEAR BigQuery read stopped this run "
            "(stored values stand). Paste:",
            f"    {LOGIN_COMMANDS[0]}",
            f"    {LOGIN_COMMANDS[1]}",
            "  The next run tops up every missed day (each series resumes from its last stored day)."]


def write_runlog(ws, runlog: pd.DataFrame, fetch_status: pd.DataFrame, run_id: str | None, asof: pd.Timestamp, overrides_n: int):
    _title(ws, "Run Log", f"Run {run_id or '(build only)'} — workbook built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — data as of {asof.date()} (UTC)")
    r = 4
    # ACTION NEEDED FIRST (Round G 1.7(a)): an expired NEAR BigQuery login, with the two commands to paste
    for line in run_log_actions(runlog):
        c = ws.cell(row=r, column=1, value=line)
        c.font = Font(name=FONT, size=11, color="C00000", bold=True)
        r += 1
    if r > 4:
        r += 1
    ws.cell(row=r, column=1, value="Rows fetched per source (this run)").font = F_BOLD
    r += 1
    # "Skipped" gets its own column, next to Failed rather than folded into Unconfigured. A
    # skipped source produces no error, no failure and no gap, so anywhere it is merged with
    # "nothing to do" it reads as a clean run that simply had nothing to add.
    _header(ws, r, ["Source", "Tier", "OK calls", "Rows", "Failed", "Skipped (ran nothing)",
                    "Unconfigured / not applicable"])
    ws.row_dimensions[r].height = 18
    r += 1
    if runlog is not None and not runlog.empty:
        for src, g in runlog.groupby("source", sort=True):
            tiers = sorted({int(t) for t in g["tier"].dropna().unique()}) if "tier" in g else []
            vals = [src, ", ".join(str(t) for t in tiers), int((g["status"] == "ok").sum()),
                    int(g["rows"].fillna(0).sum()), int((g["status"] == "failed").sum()),
                    int((g["status"] == "skipped").sum()),
                    int((g["status"] == "unconfigured").sum())]
            for j, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=j, value=v)
                _style(c, "text", FMT_NUM if j > 2 else FMT_TEXT)
                if j == 5 and v:
                    c.font = Font(name=FONT, size=10, color="C00000", bold=True)
                if j == 6 and v:
                    c.font = Font(name=FONT, size=10, color="B85C00", bold=True)
                    c.fill = FILL_STALE
            r += 1
    else:
        ws.cell(row=r, column=1, value="no fetch log for this run").font = F_SUB
        r += 1
    r += 1
    ws.cell(row=r, column=1,
            value="Skipped (this run) — these sources COULD have run and deliberately did not. "
                  "A skip is not a success: nothing was fetched.").font = F_BOLD
    r += 1
    _header(ws, r, ["Timestamp (UTC)", "Source", "Project", "Message"])
    ws.row_dimensions[r].height = 18
    r += 1
    skipped = runlog[runlog["status"] == "skipped"] if runlog is not None and not runlog.empty else pd.DataFrame()
    if skipped.empty:
        ws.cell(row=r, column=1, value="none — every configured source ran").font = F_SUB
        r += 1
    for row in skipped.itertuples(index=False):
        for j, v in enumerate([row.ts, row.source, row.project or "", row.message], start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.font = Font(name=FONT, size=10, color="B85C00")
            c.fill = FILL_STALE
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
    _set_widths(ws, {"A": 22, "B": 18, "C": 18, "D": 22, "E": 12, "F": 22, "G": 80})


# ---------------------------------------------------------------------------------------
# =====================================================================================================================
# THE FLOWS TAB (alignment round 2026-10-13, C11; SPEC.md section 8): per project and quarter (Q0..Q-4, the trailing
# 90-day blocks), a TOKEN-FLOW statement — opening circulating + minted + released − returned − burned = expected closing, against
# the closing circulating actually stored; the residual is what no series explains — and a VALUE-FLOW statement — fees ->
# suppliers / revenue -> holder value credited (retired / distributed) / recorded not credited / retained; incentives
# beside. Computed here from the same views every tab reads (values, not formulas). |residual| > 0.5% of opening, or a
# leg not fully covered, reads CHECK with the reason — never a silent balance.
FLOWS_SHEET = "Flows"
FLOWS_HEAD = ["Project", "Statement", "Line", "Q0", "Q-1", "Q-2", "Q-3", "Q-4", "Unit", "Series", "Note"]
FLOWS_ROWS: list = []
FLOW_RESIDUAL_LIMIT = 0.005


def _flow_window(asof, k: int):
    end = pd.Timestamp(asof).normalize() - pd.Timedelta(days=GLOBALS["period_days"] * k)
    return end - pd.Timedelta(days=GLOBALS["period_days"]), end


def _flow_sum(s: pd.Series, start, end):
    """(sum over (start, end], days with a row) — a series with no row in the window is (None, 0)."""
    w = s[(s.index > start) & (s.index <= end)] if not s.empty else s
    return (float(w.sum()) if not w.empty else None), int(w.index.nunique()) if not w.empty else 0


def _flow_at(s: pd.Series, day, slack: int = 3):
    """The stock's value on the last stored day at or before `day`, within `slack` days — else None."""
    if s.empty:
        return None
    w = s[(s.index <= day) & (s.index > day - pd.Timedelta(days=slack + 1))]
    return float(w.iloc[-1]) if not w.empty else None


def flow_statements(groups: dict, asof) -> list[dict]:
    """The rows of the Flows tab (see FLOWS_HEAD); also returned for the tests and coverage_audit."""
    period = GLOBALS["period_days"]
    out = []
    for p in scoped_projects():
        name = p["name"]
        # what the project's tabs read: the metrics that apply to it, and any series stored or built for it (Aerodrome's
        # Minter emissions are stored though no archetype lists them)
        have = set(config.metrics_for_project(p)) | {m for (n, m) in groups if n == name}
        d = lambda m: _daily(groups.get((name, m)))  # noqa: E731
        circ = d("circulating_supply_used")
        gc = groups.get((name, "circulating_supply_used"))
        circ_mp = (pd.Series(gc["source"].astype(str).map(lambda x: trajectory.basis_of("circulating_supply_used", x)).values,
                             index=pd.to_datetime(gc["date"]).dt.normalize()) if gc is not None and not gc.empty
                   else pd.Series(dtype=str))
        pre = _premint(p)
        mint_m = None if pre else (config.issuance_basis(name) if config.issuance_basis(name) in have else None)
        rel_m = "pool_release_tokens" if pre else ("unlock_release_tokens" if config.unlock_stock_metrics(name) else None)
        # F1 (fix round 2): where circulating IS a published schedule, the schedule's own step is the release leg (it
        # counts every unlock — pool releases included, so they are not added again)
        sched = (name, "scheduled_release_tokens") in groups and config.circulating_primary_metric(name) == \
            (config.FIRST_PARTY_HISTORY.get(name) or {}).get("metric")
        rel_lab = "+ scheduled releases (published schedule)" if sched else "+ released from issuer wallets"
        rel_m = "scheduled_release_tokens" if sched else rel_m
        burn_m = config.a1_burn_metric(name) if config.a1_burn_metric(name) in have else None
        ret_m = ("issuer_return_tokens" if rel_m == "unlock_release_tokens" and (name, "issuer_return_tokens") in groups
                 else None)
        # G1/F2 (fix round 2): where the pre-minted release is the MINING wallets' (b) and the unlock leg (e) reads the
        # other issuer wallets — disjoint by construction (config.mining_wallets / discretionary_wallets) — both legs
        # move circulating, so both are on the statement (GEODNET)
        disc_m = ("unlock_release_tokens" if pre and rel_m == "pool_release_tokens" and config.mining_wallets(name)
                  and config.unlock_stock_metrics(name) else None)
        if disc_m:
            ret_m = "issuer_return_tokens" if (name, "issuer_return_tokens") in groups else None
        legs = [("+ minted", mint_m, 1), (rel_lab, rel_m, 1),
                *([("+ released from the other issuer wallets (e)", disc_m, 1)] if disc_m else []),
                ("− returned to issuer-side wallets", ret_m, -1), ("− burned", burn_m, -1)]
        # a project whose supply enters circulating by named legs measured over the whole window (config
        # FLOW_RELEASE_LEGS; Hyperliquid since the ledger replay, fix round 3 B1) reads them instead of the pool release
        if config.FLOW_RELEASE_LEGS.get(name):
            fl = config.FLOW_RELEASE_LEGS[name]
            ret_m = "issuer_return_tokens" if (name, "issuer_return_tokens") in groups else None
            legs = [("+ minted", mint_m, 1), *[(lab, m, 1) for lab, m in fl],
                    ("− returned to issuer-side wallets", ret_m, -1), ("− burned", burn_m, -1)]
        note_tok = {"Sky": "MKR -> SKY conversions (converter 0xA1Ea1bA1…) enter circulating with NO series — NOT WIRED "
                           "(149.6M SKY in the 90 days to 2026-10-12, research of 2026-10-13); they are not dilution "
                           "(SPEC.md section 12, item 7) and show here as residual",
                    "Plume": "Plume's non-insider unlocks (ecosystem / community / foundation) have no series — the "
                             "insider leg (e) is DefiLlama's reading of the published chart (G11 B4); the rest of the "
                             "monthly schedule steps (+212,116,114.6 about the 21st from 2026-03, every category) shows "
                             "here as residual"}.get(name, "")
        tok = {"open": [], "close": [], "expected": [], "residual": [], "pct": [], "status": []}
        leg_vals = {lab: [] for lab, _m, _s in legs}
        for k in range(5):
            start, end = _flow_window(asof, k)
            o, c = _flow_at(circ, start), _flow_at(circ, end)
            exp, why = o, []
            unm = config.FLOW_UNMEASURED_RETURNS.get(name)
            for lab, m, sign in legs:
                if m is None and lab == "− returned to issuer-side wallets" and unm and \
                        pd.Timestamp(unm["from"]) <= end and pd.Timestamp(unm["to"]) > start:
                    # D6-4: a flow INTO an issuer-side wallet happened and no series carries it — n/a, never a claimed 0
                    leg_vals[lab].append(None)
                    why.append("named: " + unm["why"])
                    continue
                if m is None:
                    leg_vals[lab].append(0.0)
                    continue
                v, n = _flow_sum(d(m), start, end)
                leg_vals[lab].append(v if v is not None else None)
                if n < period:
                    why.append(f"{m} covers {n}/{period} days")
                if exp is not None:
                    exp = exp + sign * (v or 0.0)
            tok["open"].append(o)
            tok["close"].append(c)
            tok["expected"].append(exp)
            if o is None or c is None:
                tok["residual"].append(None)
                tok["pct"].append(None)
                # P2d-3b: an on-chain route has no CoinGecko days before it, so a quarter can be n/a — a named
                # unmeasured flow in it is still named, never dropped
                tok["status"].append("n/a — circulating not stored within 3 days of the quarter's "
                                     + ("start" if o is None else "end") + "".join("; " + w for w in why))
                continue
            res = c - exp
            pct = res / o if o else None
            tok["residual"].append(res)
            tok["pct"].append(pct)
            # a change of circulating basis inside the quarter (SPEC.md section 7) explains a step the flows cannot
            pts = circ_mp[(circ_mp.index > start - pd.Timedelta(days=4)) & (circ_mp.index <= end)].unique()
            if len(pts) > 1:
                why.append(f"the circulating basis changes inside the quarter ({' -> '.join(pts)}) — a SERIES BREAK, "
                           f"not a flow")
            if note_tok:
                why.append("named: " + note_tok.split(" — ")[0])
            # a quarter whose circulating is wholly or partly a THIRD PARTY's figure (CoinGecko's circulating, with or
            # without the staked add-back): its revisions are not flows, so the flows cannot reconcile it — named,
            # and restated on our basis once the archive backfill fills the on-chain total (H1)
            third = [b for b in circ_mp[(circ_mp.index > start - pd.Timedelta(days=4)) & (circ_mp.index <= end)].unique()
                     if str(b).startswith("circulating_supply") and not str(b).startswith(
                         ("circulating_supply_onchain", "circulating_supply_first_party", "circulating_supply_lockup"))]
            if third:
                why.append(f"named: the quarter's circulating is a third party's figure ({', '.join(third)}) — its "
                           f"revisions are not flows; restated on our basis by the archive backfill (H1)")
            if pct is not None and abs(pct) > FLOW_RESIDUAL_LIMIT:
                # T1 (fix round 2): a residual beyond ±0.5% with NO named cause (a leg short of the quarter, a basis
                # change, a declared unwired flow) is BROKEN; with one it is CHECK and the cause is on the line
                lead = f"residual {pct:+.2%} of opening is unexplained by the series"
                if any(" covers " in w for w in why):
                    # V6 (Jake's round-2 run, GEODNET Q-4 +2.71%): a leg short of the quarter is a COVERAGE gap — the
                    # expected closing lacks the uncovered days' flow — not a measured difference
                    lead = (f"residual {pct:+.2%} of opening is a COVERAGE gap (a leg is short of the quarter — the "
                            f"expected closing lacks its uncovered days), not a measured difference")
                tok["status"].append(("CHECK — " + "; ".join([lead] + why)) if why else f"BROKEN — {lead}; no named cause")
            else:
                tok["status"].append(("CHECK — " + "; ".join(why)) if why else "OK — balances within ±0.5%")
        basis = (("releases = the named legs: " + ", ".join(m for _l, m in config.FLOW_RELEASE_LEGS[name]))
                 if config.FLOW_RELEASE_LEGS.get(name) else
                 "pre-minted supply: nothing is minted; releases = pool_release_tokens" if pre else
                 "releases = the falls of the non-circulating wallets (unlock_release_tokens)" if rel_m else
                 "no issuer-wallet release series")
        rows = [("Opening circulating", tok["open"], "tokens", "circulating_supply_used (start of the quarter)", ""),
                *[(lab, leg_vals[lab], "tokens",
                   m or ("— NOT MEASURED where n/a: " + config.FLOW_UNMEASURED_RETURNS[name]["why"]
                         if lab == "− returned to issuer-side wallets" and name in config.FLOW_UNMEASURED_RETURNS
                         else "— (none on this project: 0)"), "") for lab, m, _s in legs],
                ("Expected closing (opening + minted + released − returned − burned)", tok["expected"], "tokens",
                 "opening + minted + released − returned − burned", ""),
                ("Closing circulating", tok["close"], "tokens", "circulating_supply_used (end of the quarter)", ""),
                ("Residual (closing − expected)", tok["residual"], "tokens", "", note_tok),
                ("memo: minted straight to issuer-side wallets (insider emission, not circulating until released)",
                 [(_flow_sum(d("issuer_mint_tokens"), *_flow_window(asof, k))[0]
                   if (name, "issuer_mint_tokens") in groups else None) for k in range(5)], "tokens",
                 "issuer_mint_tokens" if (name, "issuer_mint_tokens") in groups else "— (none declared: config.ISSUER_MINTS)",
                 # G9 D2: a DECLARED mint route with nothing stored yet is a route not filled, not "none declared"
                 (("G9 L3: INSIDE '+ minted' — the wallet is circulating by the project's own method; its part of "
                   "the mint is shown here, not added again" if (config.ISSUER_MINTS.get(name) or {}).get("inside_minted")
                   else "not in the expected closing: it lands in a non-circulating wallet; its release is (e)")
                  if (name, "issuer_mint_tokens") in groups or name not in config.ISSUER_MINTS else
                  f"ROUTE PENDING — config.ISSUER_MINTS declares the {config.ISSUER_MINTS[name]['kind']} route "
                  f"({config.ISSUER_MINTS[name].get('metric') or config.ISSUER_MINTS[name].get('source', '')}); "
                  f"nothing stored for it yet")),
                # G11 B1: a declared one-off burn FROM AN ISSUER-SIDE WALLET (Uniswap's 100M retroactive burn left the
                # Timelock): burned supply that was never circulating — shown here, out of the release leg and out of
                # the expected closing, so the statement still balances
                *([("memo: one-off burn from issuer-side wallets (never circulating — out of the release leg and of the "
                    "expected closing)",
                    # a DECLARED single event: 0 in every quarter it did not land in (never unknown)
                    [_flow_sum(d("one_off_burn_tokens"), *_flow_window(asof, k))[0] or 0.0 for k in range(5)], "tokens",
                    "one_off_burn_tokens",
                    "; ".join(f"{f['what']}, landed {f.get('landed')}" for f in p.get("one_off_flows") or ())
                    + ("; NOT MATCHED to a stored fall: "
                       + ", ".join(f"{m} {v:,.0f}" for (n_, m), v in _ONE_OFF_UNMATCHED.items() if n_ == name)
                       if any(n_ == name for n_, _m in _ONE_OFF_UNMATCHED) else ""))]
                  if (name, "one_off_burn_tokens") in groups else []),
                ("Residual ÷ opening", tok["pct"], "pct", "", f"CHECK beyond ±{FLOW_RESIDUAL_LIMIT:.1%}"),
                ("Status", tok["status"], "text", "", basis)]
        for line, vals, unit, series, note in rows:
            out.append({"Project": name, "Statement": "Token flow", "Line": line,
                        **{lab: v for lab, v in zip(trajectory.LABELS, vals)}, "Unit": unit, "Series": series,
                        "Note": note})
        # ---- VALUE FLOW ----
        rev_m = config.revenue_headline_metric(name)
        rb = config.revenue_ratio_base_metric(name)
        hv = config.HOLDER_VALUE.get(name) or {}
        vrows = {}
        series = {"fees": config.fees_headline_metric(name), "revenue": rev_m, "retired": "retired_buyback_usd",
                  "bought": "actual_buyback_usd", "credited": "holder_value_revenue_usd",
                  "other": "holder_value_other_usd", "incent_b": "outflow_b_usd", "incent_c": "outflow_c_usd",
                  "incent_a": "outflow_a_usd", "incent_d": "outflow_d_usd", "incent_e": "outflow_e_usd",
                  "credited_fees": "holder_value_fees_only_usd", "rbase": rb}
        for key_, m in series.items():
            vrows[key_] = [(_flow_sum(d(m), *_flow_window(asof, k))[0] if m in have else None) for k in range(5)]
        extra = [[_flow_sum(d(m), *_flow_window(asof, k))[0] for k in range(5)] for m in hv.get("distributed_extra") or ()]
        sup, dist, kept, stat = [], [], [], []
        for k in range(5):
            f, r_, cr = vrows["fees"][k], vrows["revenue"][k], vrows["credited"][k]
            rbv = vrows["rbase"][k]
            sup.append(None if rb == "fees_usd" or f is None or rbv is None else f - rbv)
            b, rt = vrows["bought"][k], vrows["retired"][k]
            ex = sum(e[k] or 0.0 for e in extra) if extra else 0.0
            dist.append(((b or 0.0) - (rt or 0.0) + ex) if (b is not None or extra) else None)
            kept.append(None if r_ is None else r_ - (cr or 0.0))
            if r_ is None:
                stat.append("n/a — no revenue series in this quarter" if rev_m in have else "n/a — no revenue line on this project")
            # liquid bribes are credited holder income, not revenue (Aerodrome, decision 4): the check reads fees only
            cr_fees = vrows["credited_fees"][k] if vrows["credited_fees"][k] is not None else cr
            if r_ is None:
                pass
            elif cr_fees is not None and r_ > 0 and cr_fees > r_ * (1 + FLOW_RESIDUAL_LIMIT):
                stat.append(f"CHECK — holder value credited exceeds revenue by {cr_fees / r_ - 1:.1%} (paid from "
                            f"outside the quarter's revenue, or a timing gap)")
            elif cr is not None and r_ <= 0 < cr:
                stat.append("CHECK — holder value credited with no revenue in the quarter")
            else:
                stat.append("OK — credited holder value within revenue")
        vlines = [("Fees paid by customers", vrows["fees"], "usd", config.fees_headline_metric(name), ""),
                  ("→ to suppliers (fees − revenue)", sup, "usd", f"fees_usd − {rb}",
                   "n/a: the revenue basis is gross fees" if rb == "fees_usd" else ""),
                  ("Revenue (every stream)", vrows["revenue"], "usd", rev_m, ""),
                  ("→ retired (bought, burned + held)", vrows["retired"], "usd", "retired_buyback_usd", ""),
                  ("→ distributed to holders", dist, "usd",
                   "actual − retired buyback" + ("".join(f" + {m}" for m in hv.get("distributed_extra") or ())), ""),
                  ("Holder value CREDITED (revenue-funded)", vrows["credited"], "usd", "holder_value_revenue_usd", ""),
                  ("Holder value recorded, NOT credited", vrows["other"], "usd", "holder_value_other_usd",
                   "treasury / emissions-funded (config HOLDER_VALUE funding)"),
                  ("→ retained (revenue − credited holder value)", kept, "usd", f"{rev_m} − holder_value_revenue_usd",
                   "treasury, opex and insiders together — the insider leg has no series (NOT WIRED)"),
                  ("Incentives paid beside: supply side (b)", vrows["incent_b"], "usd", "outflow_b_usd", ""),
                  ("Incentives paid beside: demand side (c)", vrows["incent_c"], "usd", "outflow_c_usd", ""),
                  # THE OTHER THREE OUTFLOW CATEGORIES (Round G6 item 8, spec_coverage): each has its line on every
                  # project; a project without the category reads blank with its series named
                  ("Outflows beside: security emissions (a)", vrows["incent_a"], "usd", "outflow_a_usd",
                   "" if "outflow_a_usd" in have else "no (a) on this project"),
                  ("Outflows beside: ecosystem / grants (d)", vrows["incent_d"], "usd", "outflow_d_usd",
                   "" if "outflow_d_usd" in have else "no (d) on this project"),
                  ("Outflows beside: unlocks (e)", vrows["incent_e"], "usd", "outflow_e_usd",
                   "" if "outflow_e_usd" in have else "no (e) on this project"),
                  # G11 D7 (Jake's 10-21 run: NEAR's treasury mint sat inside "+ minted" on no value-flow line): a
                  # declared memo (config.VALUE_FLOW_MEMO) shows the $ of a minted leg and says which line it is not
                  *[(lab, [_flow_sum(_usd_daily(d(m), d("price_usd")), *_flow_window(asof, k))[0] for k in range(5)],
                     "usd", f"{m} x price_usd", why) for lab, m, why in config.VALUE_FLOW_MEMO.get(name, ())],
                  ("Status", stat, "text", "", "")]
        out[:] = [_no_such_flow(r) for r in out]
        for line, vals, unit, series_, note in vlines:
            ev = evidence_on(name, series_)
            if ev and ev["verdict"] != "superseded" and ev.get("note"):    # G9 N: the line carries its step and check
                note = ev["note"] + (f" | {note}" if note else "")
            if (name, line) in config.FLOW_LINE_NOTES:
                note = config.FLOW_LINE_NOTES[(name, line)] + (f" | {note}" if note else "")
            out.append(_no_such_flow({"Project": name, "Statement": "Value flow", "Line": line,
                        **{lab: v for lab, v in zip(trajectory.LABELS, vals)}, "Unit": unit, "Series": series_,
                        "Note": note}, have))
    return out


def _usd_daily(tokens: pd.Series, price: pd.Series) -> pd.Series:
    """tokens x the same day's price, on the days both hold (a day without a price is left out, never valued at another
    day's)."""
    if tokens.empty or price.empty:
        return pd.Series(dtype=float)
    both = tokens.index.intersection(price.index)
    return (tokens[both] * price[both]).sort_index()


HOLDER_OTHER_LINE = "Holder value recorded, NOT credited"


def _checked_text(fl: dict) -> str:
    """G9 N3: the step-2/3/4 sources a line without a value was checked against, with dates."""
    ch = fl.get("checked") or ()
    return ("; checked: " + "; ".join(f"{src} ({d}): {what}" for src, d, what in ch)) if ch else ""


def _no_such_flow(row: dict, have: set | None = None) -> dict:
    """CLASS A (Round G8 item 2, Jake 2026-10-20): a Flows line with no value in any quarter whose mechanism does not
    exist per a PRIMARY source (config.NO_SUCH_FLOW) reads 0 in every quarter, labelled "0 — no such flow" with the
    evidence and its source. A line with any value, or with no recorded evidence, is left as it is: unknown is never 0.
    G9 D2 — every line, not only the legs (config.flow_line_evidence): "included in line X" writes n/a naming the line
    that holds the amount; class E and a pending route keep the line empty and say why in the Note. The holder value
    NOT credited line is structural: it exists only where config.HOLDER_VALUE declares a non-revenue funding period
    (holder_value_other_usd's only_projects); elsewhere nothing is recorded outside revenue, so it reads 0."""
    if any(row.get(lab) is not None for lab in trajectory.LABELS):
        return row
    name, line = row.get("Project"), row.get("Line")
    leg = next((k for k, lab in config.FLOW_LEG_LINES.items() if lab == line), None)
    ev = config.NO_SUCH_FLOW.get((name, leg)) if leg else None
    if not ev and (fl := config.flow_line_evidence(name, line)):
        v = fl["verdict"]
        if v == "no_such_flow":
            ev = fl
        elif v == "included_in":
            txt = f"n/a — included in line '{fl['line']}'"
            return {**row, **{lab: txt for lab in trajectory.LABELS},
                    "Note": f"{txt}: {fl['evidence']} ({fl['source']}, read {fl['read_on']})"[:400]}
        elif v == "class_e":
            return {**row, "Note": (f"NO SOURCE — {fl['evidence']}; would be measured by: {fl['would_measure']} "
                                    f"({fl['source']}, read {fl['read_on']})" + _checked_text(fl))[:1200]}
        elif v == "pending":
            return {**row, "Note": (f"ROUTE PENDING — {fl['why']}" + _checked_text(fl))[:1200]}
    if not ev and line == HOLDER_OTHER_LINE and have is not None and "holder_value_other_usd" not in have:
        ev = {"evidence": f"no holder value on {name} is funded from outside revenue — config.HOLDER_VALUE declares no "
                          f"treasury- or emissions-funded period for it (holder_value_other_usd exists for "
                          f"{', '.join(config.METRICS['holder_value_other_usd'].get('only_projects') or ())} only)",
              "source": "config.HOLDER_VALUE / config.holder_funding", "read_on": "every build"}
    if not ev:
        return row
    return {**row, **{lab: 0.0 for lab in trajectory.LABELS},
            "Note": f"0 — no such flow: {ev['evidence']} ({ev['source']}, read {ev['read_on']})"[:400]}


def write_flows(wb, rows: list[dict]) -> None:
    ws = wb.create_sheet(FLOWS_SHEET, index=wb.sheetnames.index("CP Comparability") + 1)
    _title(ws, "Flows — token-flow and value-flow statements per project per quarter",
           "Token flow: opening circulating + minted + released − returned − burned = expected closing, against the closing "
           "circulating stored; the residual is what no series explains (CHECK beyond ±0.5% of opening, or where a "
           "leg does not cover the quarter). Value flow: fees -> suppliers / revenue -> retired / distributed / "
           "retained, incentives beside; CHECK where credited holder value exceeds revenue. Q0..Q-4 = the trailing "
           "90-day blocks. Values computed at build from the views every tab reads (SPEC.md section 8).")
    hr = 4
    _header(ws, hr, FLOWS_HEAD)
    for i, row in enumerate(rows, start=hr + 1):
        for j, h in enumerate(FLOWS_HEAD, start=1):
            v = row.get(h)
            if v is None and h in trajectory.LABELS:
                v = "n/a"
            c = ws.cell(row=i, column=j, value=v)
            fmt = (FMT_PCT if row["Unit"] == "pct" else FMT_USD if row["Unit"] == "usd" else FMT_NUM) \
                if h in trajectory.LABELS and isinstance(v, (int, float)) else FMT_TEXT
            _style(c, "calc", fmt, bold=row["Line"] in ("Status", "Residual ÷ opening"))
            if h in trajectory.LABELS and isinstance(v, str) and v.startswith("CHECK"):
                c.fill = FILL_AMBER
    ws.freeze_panes = ws.cell(row=hr + 1, column=4)
    for col, w in zip("ABCDEFGHIJK", (14, 12, 40, 16, 16, 16, 16, 16, 7, 40, 60)):
        ws.column_dimensions[col].width = w


def write_trajectory(wb, data_by_key: dict, asof=None) -> list[dict]:
    """THE TRAJECTORY TAB (theory layer, Phase 1, 2026-10-12; trajectory.py). One row per registered headline: Q0..Q-4
    as live formulas (the cell's own builder under shifted(k)), then the derived columns, computed here with xlcalc and
    written as values. Returns the rows as dicts (export_snapshot, history_gaps, the tests)."""
    import coverage_audit                                  # noqa: PLC0415
    import xlcalc                                          # noqa: PLC0415
    ws = wb.create_sheet(trajectory.SHEET, index=wb.sheetnames.index("Credibility") + 1)
    _title(ws, "Trajectory — every headline over five consecutive trailing 90-day blocks",
           "Q0..Q-4 re-run each headline's own formula k quarters back (Data q0..q4). T12M sums a window total and "
           "averages a rate. QoQ, 4Q growth (log-linear, annual), quarters improved (direction-aware) and the trough "
           "are computed at build. Years to benchmark at the trailing growth; n/a with the reason. No scores.")
    hr = 4
    _header(ws, hr, trajectory.HEAD)
    # FOUR GROUPS (Jake's Phase 1 review 2026-10-12, item 6): Fundamentals, Supply, Valuation, Market — a section each,
    # projects in tab order inside it; a row whose id has no declared group goes last, said
    order = {g: i for i, g in enumerate(config.TRAJECTORY["groups"])}
    rows = sorted(trajectory.ROWS.items(), key=lambda kv: order.get(trajectory.group(kv[1]["hid"]), len(order)))
    first = hr + 1
    written = []
    r, current = first - 1, None
    for k, spec in rows:
        grp = trajectory.group(spec["hid"]) or "UNGROUPED — no group declared (config.TRAJECTORY['rows'])"
        if grp != current:
            r += 1
            c = ws.cell(row=r, column=trajectory.COL["Headline"], value=f"{grp.upper()}")
            c.font = F_BOLD
            current = grp
        r += 1
        p = spec["project"]
        ws.cell(row=r, column=trajectory.COL["Group"], value=trajectory.group(spec["hid"]) or "not declared")
        ws.cell(row=r, column=trajectory.COL["Axis (SPEC.md)"], value=trajectory.axis(k.split("|")[0], spec["header"]))
        ws.cell(row=r, column=1, value=p["name"])
        ws.cell(row=r, column=2, value=k)
        ws.cell(row=r, column=3, value=k.split("|")[0])
        ws.cell(row=r, column=4, value=spec["hid"])
        ws.cell(row=r, column=5, value=spec["header"])
        ws.cell(row=r, column=6, value=trajectory.direction(spec["hid"], k.split("|")[0], spec["header"]))
        forms = []
        for q in range(len(trajectory.LABELS)):
            with shifted(q), _project_row(p["name"]):
                try:
                    f = spec["builder"](r, p)
                except Exception as e:  # noqa: BLE001 — a builder that cannot shift is said, never guessed
                    f = f"n/a — {type(e).__name__}"
            forms.append(f)
        # a formula reading no Data window (Config or text only) has no trajectory; a CONSTANT (a structural "=0") is
        # the same figure in every quarter and keeps its values
        independent = (isinstance(forms[0], str) and forms[0].startswith("=") and "Data!" not in forms[0]
                       and not re.fullmatch(r"=-?[\d.]+", forms[0]) and all(f == forms[0] for f in forms))
        for q, f in enumerate(forms):
            c = ws.cell(row=r, column=trajectory.COL[trajectory.LABELS[q]], value=f)
            _style(c, "calc", spec["fmt"])
        written.append({"row": r, "key": k, "spec": spec, "independent": independent, "q0_formula": forms[0]})
    _set_widths(ws, {"A": 14, "B": 34, "E": 48, **{get_column_letter(trajectory.COL[h]): 14 for h in trajectory.LABELS}})
    ws.freeze_panes = ws.cell(row=first, column=3)

    # THE DERIVED COLUMNS — evaluated here (xlcalc, the evaluator the sanity rules use), written as values
    X = xlcalc.Workbook(wb)

    def val(sheet, rr, cc):
        try:
            v = X.value(sheet, rr, cc)
        except Exception:  # noqa: BLE001
            return None
        return None if isinstance(v, xlcalc.XLError) else v
    out = []
    q0_by_key = {w["key"]: val(trajectory.SHEET, w["row"], trajectory.COL["Q0"]) for w in written}
    for w in written:
        r, k, spec = w["row"], w["key"], w["spec"]
        d = trajectory.direction(spec["hid"], k.split("|")[0], spec["header"])
        qs = [val(trajectory.SHEET, r, trajectory.COL[lab]) for lab in trajectory.LABELS]
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string  # noqa: PLC0415
        hs, hc = spec["cell"]
        col_l, row_n = coordinate_from_string(hc)
        headline = val(hs, row_n, column_index_from_string(col_l))
        rec = {"key": k, "project": spec["project"]["name"], "tab": k.split("|")[0], "hid": spec["hid"],
               "header": spec["header"], "direction": d, **{lab: trajectory._num(v) for lab, v in zip(trajectory.LABELS, qs)}}
        why = []
        if w["independent"]:
            why.append("the headline does not read a trailing window (Config / text only)")
            qs = [None] * len(qs)
        # THE BASIS RULE (Jake 2026-10-12, decision 7 on 6f8c86c): no QoQ, growth or direction across a change of
        # basis — the statistics use only the quarters wholly on the current basis; the Q cells stay as they read
        brk, brk_text, step_at = None, "", None
        q0_cell = qs[0]                     # the Q0 the credibility check compares, whatever the basis rule leaves
        if asof is not None and not w["independent"]:
            since = pd.Timestamp(asof) - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
            found = trajectory.basis_breaks(spec["project"]["name"], trajectory.referenced_metrics(w["q0_formula"]),
                                            _LAST_GROUPS, since)
            if found:
                brk = found[-1]
                n_clean = trajectory.clean_quarters(asof, brk[0], int(config.TRAJECTORY["period_days"]), len(qs))
                if n_clean < len(qs):
                    brk_text = (f"SERIES BREAK {brk[0].date()} — {brk[1]}: {brk[2][:60]} -> {brk[3][:60]}"
                                + (f" (+{len(found) - 1} earlier: " + ", ".join(  # G13 8: each earlier stitch named
                                    f"{x[0].date()} {x[1]}" for x in found[-4:-1]) + ("…" if len(found) > 4 else "")
                                    + ")" if len(found) > 1 else ""))
                    # P2c-2: a declared break with a cell note (config.SERIES_BREAK_NOTES — Aerodrome's 2026-10-21) is
                    # named in full, whichever break is the latest: its text says what the day before it reads
                    named = dict.fromkeys(
                        config.SERIES_BREAK_NOTES[(spec["project"]["name"], x[1])]["text"] for x in found
                        if (spec["project"]["name"], x[1]) in config.SERIES_BREAK_NOTES
                        and str(x[0].date()) == config.SERIES_BREAK_NOTES[(spec["project"]["name"], x[1])]["break"])
                    brk_text += "".join(f" · DECLARED: {t}" for t in named)
                    why.append(f"{brk_text}; statistics over the {n_clean} quarter(s) wholly after it — restate the "
                               f"history on the current basis to lift it (RUNBOOK 11bp)")
                    # the basis rule: no QoQ, growth or direction across the break (ef6dc86). P2b-2 (0e7fc8b) had moved
                    # these two lines under its restatement test, so a break left the statistics spanning it
                    qs = list(qs[:n_clean]) + [None] * (len(qs) - n_clean)
                    step_at = n_clean            # the quarter that holds the break
        # P2b-2: a declared restatement not yet complete says what it lacks — the days still on the superseded set (the
        # break above is its seam), or the days not yet re-read (holes)
        if asof is not None and not w["independent"]:
            for m in sorted(trajectory.referenced_metrics(w["q0_formula"])):
                if (spec["project"]["name"], m) in RESTATEMENT_STATUS:
                    why.append(f"{m}: {RESTATEMENT_STATUS[(spec['project']['name'], m)]}")
        # MODELLED QUARTERS (Jake 2026-10-12, Phase 1 follow-up item 2): shown, and excluded from any scoring
        modelled = ([] if asof is None or w["independent"] else
                    trajectory.modelled_quarters(spec["project"]["name"], trajectory.referenced_metrics(w["q0_formula"]),
                                                 _LAST_GROUPS, asof, int(config.TRAJECTORY["period_days"]), len(qs)))
        if modelled:
            why.append(f"MODELLED in {', '.join(modelled)} (config.MODELLED_POINTS) — shown, excluded from any scoring")
        q = trajectory.qoq(qs[0], qs[1])
        # THE GROWTH RULES (item 4): >= 3 quarters, all > 0, one regime — else NEW since / STEP CHANGE at, never a rate
        # THE YOUNGEST INPUT'S FIRST DAY (review of the ef6dc86 run, item 10): a part-quarter at the start is no base
        starts_in = None
        if asof is not None and not w["independent"]:
            firsts = [(data_by_key.get(f"{spec['project']['name']}|{m}") or {}).get("first_date")
                      for m in trajectory.referenced_metrics(w["q0_formula"])]
            firsts = [f for f in firsts if isinstance(f, str) and f]
            if firsts:
                starts_in = trajectory.partial_start_quarter(max(firsts), asof, int(config.TRAJECTORY["period_days"]),
                                                             len(qs))
            # A THIN OLDEST QUARTER IS A PART-QUARTER TOO (fix round 2, R5: Chainlink's CCIP messages read −84%/yr over
            # 0/2 comparisons — the oldest quarter held a few days of a backfill, not its first stored day): a flow
            # quarter its inputs cover on fewer than 90% of its days is no growth base
            if starts_in is None:
                alive = [i for i, v in enumerate(qs[:4]) if isinstance(v, (int, float)) and v > 0]
                if alive:
                    oldest = max(alive)
                    covs = [(data_by_key.get(f"{spec['project']['name']}|{m}") or {}).get(f"q{oldest}_covered_days")
                            for m in trajectory.referenced_metrics(w["q0_formula"])
                            if (config.METRICS.get(m) or {}).get("kind") == "flow"]
                    covs = [float(c) for c in covs if isinstance(c, (int, float)) and c == c]
                    if covs and min(covs) < 0.9 * int(config.TRAJECTORY["period_days"]):
                        starts_in = oldest
        # A SHORT HISTORY SAYS WHICH KIND IT IS (Round G 2.2): fewer than five quarters with a value -> the youngest input
        # and FORWARD-ONLY (the source keeps none) / SOURCE-LIMITED (no route) / BUG (a route exists, not yet run)
        if asof is not None and not w["independent"] and sum(1 for v in qs if trajectory._num(v) is not None) < len(qs):
            # G9 E1: a headline that is n/a BY ITS OWN TEXT in every quarter (no split documented, a destination this line
            # does not apply to) has no history to fill — saying "HISTORY BUG … re-reads 455 days" sent the reader after
            # a backfill that could never change it
            q0t = qs[0] if isinstance(qs[0], str) else ""
            # G10 1: a cell that reads a declared TEXT value (Uniswap's crossover "no issuance — burn only") is the same
            # case — the inputs are stored; the line has no number by its own rule, not for want of history
            declared = q0t and not q0t.startswith("#") and (
                q0t.lower().startswith("n/a") or not q0t.lower().startswith(("blocked", "missing", "history", "not wired")))
            if (declared and all(trajectory._num(v) is None for v in qs)
                    and not coverage_audit.DISHONEST_NA.search(q0t)):
                why.append(f"n/a in every quarter by the cell's own declaration (nothing to backfill) — {q0t[:200]}")
            elif hc := _history_class(spec["project"], trajectory.referenced_metrics(w["q0_formula"]), data_by_key):
                why.append(hc)
        g, n_g, g_label, why_g = trajectory.growth_rule(qs, step_at, starts_in)
        imp, n_imp = trajectory.improved(qs, d)
        tr = trajectory.trough(qs, d)
        t, t_basis = trajectory.t12m(qs, trajectory.t12m_basis(spec["header"], spec["fmt"]))
        # LUMPY FLOWS (decision 8): direction and growth from the rolling T12M, never a single quarter
        lumpy = ("" if w["independent"] else
                 trajectory.lumpy_reason(spec["project"]["name"], trajectory.referenced_metrics(w["q0_formula"]),
                                         data_by_key))
        t_prev, chg, dir_basis = None, None, "4 quarters (log-linear growth, quarters improved); QoQ secondary"
        if lumpy:
            t, t_prev, chg, g, why_l = trajectory.rolling_t12m(qs, trajectory.t12m_basis(spec["header"], spec["fmt"]))
            n_g, why_g = (5 if t_prev is not None else 0), why_l
            imp, n_imp = None, 0
            dir_basis = f"rolling T12M — a discrete flow: {lumpy}"
            if step_at is not None and step_at <= 4 and not g_label:
                g_label = f"STEP CHANGE at {trajectory.LABELS[step_at]}"
            if g_label:
                g, chg = None, None
        b = trajectory.benchmark_for(spec["hid"])
        target, why_t = trajectory.benchmark_target(b, data_by_key) if b else (None, "")
        # FORWARD-LOOKING ROWS USE THE RATE IN FORCE (Jake 2026-10-12, decision 2): the benchmark's "now" is the same
        # ratio at the emission rate in force where one is declared (config.CURRENT_EMISSION_RATE); growth stays trailing
        now_for_years, now_note = qs[0], ""
        if b and spec["project"]["name"] in config.CURRENT_EMISSION_RATE:
            alt = trajectory._num(q0_by_key.get(k + "_now"))
            if alt is not None:
                now_for_years, now_note = alt, "now = the ratio at the emission rate in force"
        yrs, why_y = trajectory.years_to(target, now_for_years, g, d if d else 1, why_t) if b else (None, "")
        if now_note and yrs is not None:
            why_y = now_note
        if d is None:
            why.append("direction not declared (config.TRAJECTORY['rows'])")
        if g_label:
            why.append(f"growth: {g_label} — {why_g}")
        elif why_g:
            why.append(f"growth: {why_g}")
        if b and (yrs is None or now_note):
            why.append(f"benchmark: {why_y}")
        src = data_by_key.get(f"{spec['project']['name']}|{spec['metric']}") or {} if spec["metric"] else {}
        same = (trajectory._num(q0_cell) is not None and trajectory._num(headline) is not None
                and abs(q0_cell - headline) <= 1e-9 * max(1.0, abs(headline))) or \
               (trajectory._num(q0_cell) is None and trajectory._num(headline) is None)
        verdict = (("PASS — Q0 is the headline cell" + ("" if trajectory._num(headline) is not None else " (both n/a)"))
                   if same else
                   f"CHECK — Q0 {q0_cell!r} vs the headline cell {headline!r}")
        g_se, g_se_why = trajectory.loglinear_growth_se(qs) if g is not None else (None, "no growth figure (a label "
                                                                                      "stands in)")
        rec.update({"T12M": t, "T12M basis": t_basis, "QoQ": q, "growth_4q": g, "growth_quarters": n_g,
                    "growth_4q_se": g_se,
                    "improved": imp, "comparisons": n_imp, "trough": tr,
                    "modelled_quarters": modelled,
                    "trend": (f"SERIES BREAK {brk[0].date()} · " if brk_text else "")
                             + (f"MODELLED {', '.join(modelled)} · " if modelled else "")
                             + (trajectory.lumpy_trend_text(t, t_prev, chg, g, d, g_label) if lumpy else
                                trajectory.trend_text(q, g, imp, n_imp, d, g_label)),
                    "T12M_prev": t_prev, "direction_basis": dir_basis, "growth_label": g_label,
                    "group": trajectory.group(spec["hid"]),
                    "series_break": brk_text,
                    "benchmark": b["label"] if b else "", "target": target, "years": yrs,
                    "why": "; ".join(why), "tier": src.get("tier", ""), "q0_coverage": src.get("q0_covered_days"),
                    "verdict": verdict, "cell": f"'{hs}'!{hc}"})
        put = {"T12M": t, "T12M basis": t_basis, "T12M a quarter earlier (Q-1..Q-4)": t_prev,
               "Direction basis": dir_basis, "QoQ": q, "4Q growth (annual, log-linear)": g,
               "Growth label (NEW / STEP CHANGE)": g_label,
               "Quarters in the growth": n_g, "Quarters improved (direction-aware)":
               (f"{imp}/{n_imp}" if imp is not None else "n/a"), "Trough of the last 4": tr or "n/a",
               "Trend": rec["trend"], "Benchmark": rec["benchmark"], "Target": target,
               "Years to benchmark": (yrs if yrs is not None else ("n/a" if b else "")), "Why n/a": rec["why"],
               "Series break (basis change)": brk_text,
               "MODELLED quarters (excluded from any scoring)": ", ".join(modelled),
               "Source tier (headline series)": rec["tier"], "Q0 coverage days (headline series)": rec["q0_coverage"],
               "Credibility (Q0 = headline cell)": verdict, "Headline cell": rec["cell"],
               "4Q growth — standard error (annual; SPEC §12 item 14)":
                   (g_se if g is not None and g_se is not None else ("n/a — " + (g_se_why or "no growth figure")
                                                                    if g is None or g_se is None else ""))}
        for h, v in put.items():
            c = ws.cell(row=r, column=trajectory.COL[h], value=v)
            fmt = (FMT_PCT if h in ("QoQ", "4Q growth (annual, log-linear)",
                                    "4Q growth — standard error (annual; SPEC §12 item 14)") and isinstance(v, float) else
                   spec["fmt"] if h in ("T12M", "Target", "T12M a quarter earlier (Q-1..Q-4)") else "0.0" if h == "Years to benchmark" else FMT_TEXT)
            _style(c, "calc", fmt)
        out.append(rec)
    _write_divergence(ws, r + 3, out)
    return out


DIVERGENCE_HEAD = ["Project", "DIVERGENCE — business vs price (4Q growth, annual)", "Revenue 4Q growth (business, USD)",
                   "Revenue row", "Price 4Q growth", "REVENUE − PRICE (pp) — the headline gap",
                   "Capture: tokens retired / burned 4Q growth (separate — falls mechanically as price rises)",
                   "Retired / burned row", "Reading", "Revenue basis note"]


def _write_divergence(ws, r0: int, recs: list) -> list[dict]:
    """THE DIVERGENCE ROW, per project (Jake's Phase 1 review 2026-10-12, item 6; reshaped on the review of the ef6dc86
    run, item 11): the HEADLINE GAP is revenue's 4Q growth (the business, in USD) less price's. Tokens retired / burned
    are a separate CAPTURE column, never averaged in: at a fixed USD spend, retirement in tokens falls mechanically as
    the price rises, so averaging it with revenue would count price twice. Each growth obeys the growth rules (a NEW /
    STEP CHANGE label stands in for it). A revenue basis with a declared caveat (config.TRAJECTORY divergence
    revenue_notes — NEAR: gas only until NEAR Intents fees are a revenue line) says so beside it."""
    spec = config.TRAJECTORY["divergence"]
    notes = spec.get("revenue_notes") or {}
    by = {(x["project"], x["hid"]): x for x in recs}
    _header(ws, r0, DIVERGENCE_HEAD)
    out = []
    for i, name in enumerate(dict.fromkeys(x["project"] for x in recs)):
        def pick(ids):
            """The first row with a growth; else the first with a NEW / STEP label; else the first present."""
            have = [by[(name, h)] for h in ids if (name, h) in by]
            hit = (next((x for x in have if x["growth_4q"] is not None), None)
                   or next((x for x in have if x.get("growth_label")), None) or (have[0] if have else None))
            if hit is None:
                return None, "", "no row"
            return hit["growth_4q"], hit["hid"], hit.get("growth_label") or ""
        rev, rev_id, rev_l = pick(spec["revenue"])
        ret, ret_id, ret_l = pick(spec["retired"])
        px, _px_id, px_l = pick(spec["price"])
        gap = (rev - px) if rev is not None and px is not None else None
        if name in config.DIVERGENCE_EXCLUDED:          # G3: a revenue implied from the burn cannot diverge from it
            gap, rev_l = None, "EXCLUDED — " + config.REVENUE_IMPLIED[name][:90]
        band = config.TRAJECTORY["divergence_in_line_pp"]
        reading = (rev_l if name in config.DIVERGENCE_EXCLUDED else
                   "n/a — " + "; ".join(x for x in (rev_l and f"revenue {rev_l}", px_l and f"price {px_l}") if x)
                   if gap is None else
                   # AN "IN LINE" BAND (alignment round 2026-10-13, B14: Ether.fi's −0.7pp read "price ahead"):
                   # a gap within ±config.TRAJECTORY["divergence_in_line_pp"] is no divergence
                   f"in line (within ±{band:.0%})" if abs(gap) <= band else
                   "business ahead of price" if gap > 0 else "price ahead of the business")
        if reading == "n/a — ":
            reading = "n/a — a growth missing"
        vals = [name, "revenue vs price", rev if rev is not None else (rev_l or "n/a"), rev_id,
                px if px is not None else (px_l or "n/a"), gap,
                ret if ret is not None else (ret_l or "n/a"), ret_id, reading,
                "; ".join(x for x in (notes.get(name, ""), config.REVENUE_IMPLIED.get(name, "")) if x)]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=r0 + 1 + i, column=j, value=v)
            _style(c, "calc", FMT_PCT if j in (3, 5, 6, 7) and isinstance(v, float) else FMT_TEXT)
        out.append(dict(zip(DIVERGENCE_HEAD, vals)))
    DIVERGENCE_ROWS[:] = out
    return out


DIVERGENCE_ROWS: list = []      # the last build's divergence rows
TRAJECTORY_ROWS: list = []      # the last build's trajectory rows (write_trajectory)


PARK_NOTE_PREFIX = "PARKED — migration:"


def _with_park_note(p: dict, asof) -> dict:
    """P2c-3: Aerodrome's park statement (config.aerodrome_park_reason, with its figures) as the first line of its Notes
    on the audit file, while it is parked. The reader drops that line (reader.audit_notes) and says it in one sentence."""
    if p.get("name") != "Aerodrome":
        return p
    why = config.aerodrome_park_reason(day=asof)
    if not why:
        return p
    return dict(p, notes="\n".join(x for x in (why, p.get("notes", "")) if x))


def build_workbook(store, path: Path | str, run_id: str | None = None,
                   asof: pd.Timestamp | None = None, only: list[str] | None = None, previous="auto",
                   reader_path: Path | str | None = None) -> Path:
    """Render the workbook. `only` narrows it to those project names; None renders every project. `reader_path`
    (P1, the daily run): the full workbook at `path` is the AUDIT file, and the reader (reader.py) is written there.

    ** PORTFOLIO SCOPE APPLIED TO THE FETCH AND NOT TO THE BUILD. Fixed 2026-09-23. ** A run that
    politely fetched 16 projects still drew 30 tabs' worth of rows, fourteen of them frozen at
    whatever the store last held with nothing on the sheet saying the run had not touched them —
    a stale cell presented exactly like a fresh one, which is the failure this project corrects
    in every other form.

    Parked projects are NOT deleted from the store. Their history stays where it is and comes
    back the moment they are named again, because a series that stops collecting cannot be
    backfilled from a provider that only serves current values.
    """
    global _SCOPE
    path = Path(path)
    if only:
        missing = [n for n in only if n not in config.PROJECT_BY_NAME]
        if missing:
            # REFUSE. A name that matches nothing would silently shrink the workbook, and a
            # workbook missing a held asset looks the same as one where that asset has no data.
            raise ValueError(f"build scope names unknown project(s): {', '.join(missing)}")
        _SCOPE = [config.PROJECT_BY_NAME[n] for n in only]
    else:
        _SCOPE = list(PROJECTS)
    long = store.load_long()
    long = drop_store_only(long)
    # G16 1: the provisional list counts this store's low reads; a listed row is released by the runs that re-read it
    import store as _store_mod                       # noqa: PLC0415
    _early = config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD
    config.set_provisional_from_store(_store_mod.low_read_days(store.conn, _early, config.PROVISIONAL_LOW_READ_RATIO))
    PROVIDER_RUNS.clear()
    PROVIDER_RUNS.update(_store_mod.provider_runs(store.conn, _early))
    # G18-6: Plume's market-share cells are class C while the weekly RWA turn is part-way
    config.set_rwa_turn_progress(_store_mod.rwa_turn_progress(store.conn, config.RWA_SHARE_TURN["project"]))
    fetch_status = store.fetch_status()
    runlog = store.run_log(run_id) if run_id else store.run_log()
    gaps = store.gap_report(run_id) if run_id else store.gap_report()
    review = store.review_queue(run_id) if run_id else store.review_queue()
    staged = store.staging(run_id) if run_id else store.staging()
    # NO KEY ON THE SHEET (2026-09-29): rows stored before the redactor existed are redacted as
    # they are rendered — Run Log messages, fetch errors, gap reasons, review bases.
    from fetch.base import redact
    for frame, cols in ((runlog, ("message",)), (fetch_status, ("last_error",)),
                        (gaps, ("reason", "suggestion")), (review, ("basis", "source"))):
        if frame is not None and not getattr(frame, "empty", True):
            for c in cols:
                if c in frame.columns:
                    frame[c] = frame[c].map(lambda v: redact(v) if isinstance(v, str) else v)

    # EVERY TAB NARROWS, NOT JUST THE ONES BUILT FROM `data`. A Gap Report listing a project the
    # run never fetched is a to-do list item for work that is out of scope, and it is the tab
    # most likely to be read as the work queue.
    if only:
        keep = {p["name"] for p in scoped_projects()} | {config.MACRO_PROJECT}   # the global series stay in scope
        long = long[long["project"].isin(keep)] if not long.empty else long
        gaps = gaps[gaps["project"].isin(keep)] if gaps is not None and not gaps.empty else gaps
        review = review[review["project"].isin(keep)] if review is not None and not review.empty else review
        staged = staged[staged["project"].isin(keep)] if staged is not None and not staged.empty else staged
        if fetch_status is not None and not fetch_status.empty:
            fetch_status = fetch_status[fetch_status["project"].isin(keep)]
    overrides_n = int(long["is_manual"].sum()) if not long.empty else 0
    asof = asof or pd.Timestamp.now('UTC').tz_localize(None).normalize()
    # P2c-3: a parked project's full statement heads its Notes on every audit tab (the reader shows one plain sentence)
    _SCOPE = [_with_park_note(p, asof) for p in _SCOPE]
    # THE CONTINUITY GUARD (Phase 1 review 2026-10-12, item 1): the previous run's snapshot — read automatically only for
    # the real store (ROOT/metrics.db), so a build of a fixture never compares with the machine's snapshots; a caller
    # may pass (date, values) itself
    PREVIOUS_SNAPSHOT.clear()
    if previous == "auto":
        if Path(getattr(store, "path", "")).resolve() == (Path(__file__).resolve().parent / "metrics.db"):
            import export_snapshot as _es                  # noqa: PLC0415
            d, v = _es.load_previous(asof)
            PREVIOUS_SNAPSHOT.update({"date": d, "values": v})
    elif previous:
        PREVIOUS_SNAPSHOT.update({"date": previous[0], "values": previous[1]})

    _WINDOWS.clear()
    for label, start, end in _period_windows(asof):
        _WINDOWS[label.lower()] = (start.date().isoformat(), end.date().isoformat())

    wiring.reset()
    data = aggregate(long, fetch_status, asof, gaps=gaps, review=review)
    data_by_key = wiring.TrackingDict({r["key"]: r for r in data.to_dict("records")})
    months, monthly = monthly_table(long, asof)
    R = Refs(len(data), len(monthly), months)

    wb = Workbook()
    ws_master = wb.active
    ws_master.title = "Master"
    ws_a1 = wb.create_sheet("A1 Infrastructure")
    ws_a2 = wb.create_sheet("A2 Coordination")
    ws_a3 = wb.create_sheet("A3 Revenue Buyback")
    ws_a4 = wb.create_sheet("A4 Permanent Burn")
    ws_cp = wb.create_sheet("CP Comparability")
    ws_cred = wb.create_sheet("Credibility")
    ws_ch = wb.create_sheet("Charts")
    ws_cfg = wb.create_sheet("Config & Sources")
    ws_gap = wb.create_sheet("Gap Report")
    ws_rev = wb.create_sheet("Review Queue")
    ws_stg = wb.create_sheet("Staging")
    ws_log = wb.create_sheet("Run Log")
    ws_data = wb.create_sheet("Data")
    ws_mon = wb.create_sheet("Monthly")

    write_config(ws_cfg, asof, silence=buyback_silence(data), statuses=derived_statuses(data, buyback_silence(data)))
    write_data(ws_data, data, asof)
    write_monthly(ws_mon, months, monthly)
    _HEADLINE_CELLS.clear()
    _TAB_CELLS.clear()
    trajectory.reset()
    with wiring.phase("shown"):                # G12 A1: what the display writers read is SHOWN
        a4 = write_a4(ws_a4, R, data_by_key)
        a3 = write_a3(ws_a3, R, data_by_key)
        a1 = write_a1(ws_a1, R, data_by_key, months)
        a2 = write_a2(ws_a2, R, data_by_key, months)
        write_cp(ws_cp, R, data_by_key)            # Phase 2 comparability (2026-10-12): reads no other tab
        # MASTER AFTER THE A-TABS (Jake's review of 97b29ae, 2026-10-11): every Master column that summarises an A-tab
        # figure is a REFERENCE to that A-tab cell, so the headline and the A-tabs cannot disagree (cross_tab.py)
        write_master(ws_master, R, data_by_key)
    for _ws in (ws_master, ws_a1, ws_a2, ws_a3, ws_a4, ws_cp):     # the SPEC.md axis of every column (C13)
        _axis_row(_ws)
    with wiring.phase("shown"):
        FLOWS_ROWS[:] = flow_statements(_LAST_GROUPS, asof)          # alignment C11: the Flows tab
        write_flows(wb, FLOWS_ROWS)
    with wiring.phase("cross"):
        CREDIBILITY_ROWS[:] = write_credibility(ws_cred, R, data_by_key, _without_provisional(long), asof)
    write_charts(ws_ch, {"a3": a3, "a4": a4, "a1": a1, "a2": a2, "ws_a3": ws_a3, "ws_a4": ws_a4, "ws_a1": ws_a1, "ws_a2": ws_a2})
    gap_end = write_gap_report(ws_gap, gaps, run_id) or (len(gaps) + 8 if gaps is not None else 8)
    _write_manual_quarterly(ws_gap, gap_end + 2, long)
    write_review_queue(ws_rev, review, run_id)
    write_staging(ws_stg, staged, run_id)
    write_runlog(ws_log, runlog, fetch_status, run_id, asof, overrides_n)

    # legend on Master
    legend_row = 4 + len(scoped_projects()) + 3
    legend = [("Legend", F_BOLD, None), ("Blue text = hardcoded input / config lever", F_INPUT, None), ("Black = formula", F_CALC, None),
              ("Green = pulled from another sheet", F_LINK, None), ("Yellow fill = manual override (entered_on in comment)", F_BASE, FILL_MANUAL),
              ("Grey fill = split unconfirmed, derived figure suppressed", F_BASE, FILL_UNCONFIRMED), ("Orange fill = stale (last good fetch in comment)", F_BASE, FILL_STALE),
              ("Amber fill = programme paused", F_BASE, FILL_PAUSED), ("Highlighted columns = headline figures", F_BASE, FILL_KEY),
              ("CONFIDENCE BANDS — what can be acted on today:", F_BOLD, None),
              ("  GREEN = verified source, mechanism confirmed, no PARTIAL marker, more than one "
               "observation. Use it.", F_BASE, FILL_GREEN),
              ("  AMBER = a real number, qualified — PARTIAL, single observation, assumed mechanism, "
               "unverified address, a stale hand-entry, or correct-but-not-comparable. Hover the cell.",
               F_BASE, FILL_AMBER),
              ("  RED = not a number: suppressed by design, refused, or gapped.", F_BASE, FILL_RED),
              ("Lilac fill = flagged to the Review Queue — NOT a measured figure. A lilac 0 in a burn "
               "column may mean no burn, or that the burn did not route to the address we watch", F_BASE, FILL_REVIEW),
              ("n/a = no value in the store (never a zero) — see the Gap Report for why", Font(name=FONT, size=10, color="999999"), None),
              ("\"none available\" = chased and CLOSED, not missing — the cell is empty on purpose. "
               "Hover it, or see Closed on Config & Sources", Font(name=FONT, size=10, color="999999", italic=True), None),
              ("Source tiers: 1 free API · 2 contract read · 3 protocol dashboard · 4 Dune backfill · 5 off-chain operational", F_SUB, None)]
    for i, (text, font, fill) in enumerate(legend):
        c = ws_master.cell(row=legend_row + i, column=1, value=text)
        c.font = font
        if fill:
            c.fill = fill

    # THE TRAJECTORY TAB (theory layer, Phase 1): after every tab it reads is written
    with wiring.phase("shown"):
        TRAJECTORY_ROWS[:] = write_trajectory(wb, data_by_key, asof=asof)
    # THE WIRING AUDIT (Round G12 A1): every series with a number, SHOWN / CROSS-CHECK / INPUT / NOT SHOWN / UNWIRED, from
    # the dependency graph this build recorded; beside the workbook (wiring.json) and summarised on the Run Log
    WIRING.clear()
    try:
        WIRING.update(wiring.audit(data.to_dict("records"), wb, CREDIBILITY_ROWS, FLOWS_ROWS,
                                   projects={p["name"] for p in scoped_projects()}))
        wiring.write(WIRING, Path(path).with_name("wiring.json"))
    except Exception as e:  # noqa: BLE001 — an audit that cannot run is said, never a silent pass
        WIRING[("(all)", "(wiring audit not run)")] = {"class": "ERROR", "why": f"{type(e).__name__}: {str(e)[:200]}",
                                                       "label": "", "latest": None, "latest_date": ""}

    # SANITY RULES ON THE BUILT NUMBERS (Jake's review of 97b29ae, item 17 — permanent): evaluated in Python (xlcalc)
    # and written under the Review Queue; credibility_report prints them (SANITY_HITS)
    SANITY_HITS.clear()
    try:
        import sanity                                      # noqa: PLC0415
        SANITY_HITS.extend(sanity.check_workbook(wb))
        if PREVIOUS_SNAPSHOT.get("values"):
            SANITY_HITS.extend(sanity.lost_headlines(wb, PREVIOUS_SNAPSHOT["values"], _HEADLINE_CELLS,
                                                     PREVIOUS_SNAPSHOT.get("date", "")))
        # n/a WHILE ITS INPUT IS FULL (Jake's review of the ef6dc86 run, item 7): the continuity guard compares with the
        # previous snapshot, so a headline already n/a there (GEODNET's A4 gross burn) passed it
        SANITY_HITS.extend(sanity.starved_headlines(wb, _HEADLINE_CELLS, data_by_key, asof))
    except Exception as e:  # noqa: BLE001 — a rule that cannot run is said, never a silent pass
        SANITY_HITS.append(("(all)", "sanity_not_run", f"{type(e).__name__}: {str(e)[:200]}"))
    _write_sanity(ws_rev, SANITY_HITS)
    # THE BUILD'S HITS BESIDE THE WORKBOOK (Round G7 item 7): `sanity.py --workbook` re-evaluates the workbook alone and
    # cannot see the rules that need the build's state (headline_lost, headline_starved) — it printed 0 while the Review
    # Queue said 5. It reads this file, so both count the same open hits.
    try:
        Path(path).with_name("sanity_hits.json").write_text(__import__("json").dumps(
            {"workbook": Path(path).name, "built": str(asof)[:10],
             "hits": [list(h) for h in SANITY_HITS]}, indent=1), encoding="utf-8")
    except OSError:
        pass
    # THE COVERAGE AUDIT (alignment round 2026-10-13, D1): every column against SPEC.md, as a tab and a CSV beside the
    # workbook; a failure to audit is said on the tab, never a silent pass
    COVERAGE_ROWS.clear()
    try:
        import coverage_audit                              # noqa: PLC0415
        def _series_green(project, metric, _cache={}):            # noqa: B006 — memo for this build only
            k = f"{project}|{metric}"
            if k not in _cache:
                row = data_by_key.get(k)
                try:
                    _cache[k] = bool(row) and confidence_for(project, metric, row, asof)[0] == "GREEN"
                except Exception:  # noqa: BLE001 — an input that cannot be judged is not verified
                    _cache[k] = False
            return _cache[k]
        COVERAGE_ROWS.extend(coverage_audit.audit(wb, TRAJECTORY_ROWS, FLOWS_ROWS, groups=_LAST_GROUPS,
                                                  series_green=_series_green, gap_rows=list(GAP_ROWS),
                                                  sanity_hits=SANITY_HITS, windows=dict(_WINDOWS)))
        board = coverage_audit.scoreboard(COVERAGE_ROWS, BOARD_INPUTS["gaps"], BOARD_INPUTS["review_open"])
        # THE PREVIOUS BUILD, BEFORE IT IS OVERWRITTEN (Round G5 item 1): its coverage rows name every cell that was
        # VERIFIED, and its scoreboard gives "change since the previous run"
        csv_path = Path(path).with_name("coverage_audit.csv")
        board_path = Path(path).with_name("scoreboard.json")
        prev_rows = coverage_audit.read_csv(csv_path)
        try:
            prev_board = __import__("json").loads(board_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev_board = None
        LOST_VERIFIED[:] = coverage_audit.lost_verified(prev_rows, COVERAGE_ROWS)
        coverage_audit.write(wb, COVERAGE_ROWS, csv_path, board=board)
        _board_unwired(board, WIRING)
        try:
            board_path.write_text(__import__("json").dumps(board, indent=1), encoding="utf-8")
        except OSError:
            pass
        SCOREBOARD_LINES[:] = scoreboard_lines(board, coverage_audit.board_delta(board, prev_board))
        SCOREBOARD_LINES.extend(wiring_lines(WIRING))
        SCOREBOARD_LINES.extend(lost_verified_lines(LOST_VERIFIED))
        _write_runlog_scoreboard(ws_log, SCOREBOARD_LINES)
    except Exception as e:  # noqa: BLE001
        wb.create_sheet("Coverage").cell(1, 1, value=f"COVERAGE AUDIT NOT RUN: {type(e).__name__}: {str(e)[:300]}")
    wb.save(path)
    # THE FILE CARRIES ITS VALUES (Round G11 A1, Jake 2026-10-21): openpyxl writes formulas with empty caches, so a
    # reader that does not recalculate (data_only, the guard, Credibility CONTINUITY) saw blanks. Every formula is
    # evaluated (xlcalc) and its value written as the cached value. TOKEN_METRICS_STORE_VALUES=0 skips it (tests).
    WORKBOOK_VALUES.clear()
    if os.environ.get("TOKEN_METRICS_STORE_VALUES", "1") != "0":
        try:
            import workbook_values                         # noqa: PLC0415
            WORKBOOK_VALUES.update(workbook_values.store_values(path, wb))
        except Exception as e:  # noqa: BLE001 — a file without values is said, never silently left blank
            WORKBOOK_VALUES.update({"error": f"{type(e).__name__}: {str(e)[:200]}"})
    # THE READER (P1, Jake 2026-10-27): derived from this build in memory. A reader that cannot be built is REPLACED by
    # a one-tab file saying so — never left as yesterday's file looking current.
    READER_STATS.clear()
    if reader_path:
        write_reader(wb, reader_path, path, asof)
    return path


def write_reader(wb, reader_path, audit_path, asof) -> dict:
    """THE READER (P1): reader.build_reader on this build — or, when it fails, a one-tab file at reader_path saying so
    (the error in READER_STATS), never yesterday's reader left looking current."""
    try:
        import reader                                      # noqa: PLC0415
        READER_STATS.update(reader.build_reader(wb, reader_path, asof=asof, groups=_LAST_GROUPS,
                                                headline_cells=list(_HEADLINE_CELLS),
                                                projects=scoped_projects(), previous=dict(PREVIOUS_SNAPSHOT)))
    except Exception as e:  # noqa: BLE001
        import traceback                                   # noqa: PLC0415
        READER_STATS.update({"error": f"{type(e).__name__}: {str(e)[:300]}", "trace": traceback.format_exc()[-1500:]})
        err = Workbook()
        err.active.title = "READER NOT BUILT"
        err.active.cell(1, 1, f"THE READER WAS NOT BUILT on {pd.Timestamp(asof).date()}: {READER_STATS['error']} — "
                              f"every figure is on {Path(audit_path).name}")
        err.save(reader_path)
    return dict(READER_STATS)


WORKBOOK_VALUES: dict = {}      # {"formulas", "valued", "failed", ...} — the cached values written into the last build
READER_STATS: dict = {}         # P1: the reader's stats (reader.STATS), or its error


SANITY_HITS: list = []          # (project, rule, detail) from the last build (sanity.py)
COVERAGE_ROWS: list = []        # the coverage audit of the last build (coverage_audit.py)
PREVIOUS_SNAPSHOT: dict = {}    # {"date", "values"} — the previous run's snapshot, for the continuity guard


def _write_sanity(ws, found: list) -> None:
    """The sanity-rule hits, under the Review Queue's own rows. A headline_lost hit that config.REGRESSION_ALLOWLIST
    covers (the Coverage tab already reads it N/A BY DESIGN) is listed under ANSWERED with the allowance's reason, not
    as an open hit (G4 7b)."""
    allowed = [(h, config.regression_allowance(h[0], h[2]) if h[1] == "headline_lost" else None) for h in found]
    answered = [(h, a) for h, a in allowed if a is not None]
    found = [h for h, a in allowed if a is None]
    for proj, _rule, _d in found:
        BOARD_INPUTS["review_open"][str(proj)] += 1
    r = ws.max_row + 3
    ws.cell(row=r, column=1, value=f"SANITY RULES — {len(found)} hit(s) on this build's numbers (sanity.py): FDV < "
                                   f"market cap; circulating > total; market cap != price x circulating shown; "
                                   f"buyback > revenue unfunded; a status label against the data; a ratio beside a "
                                   f"BLOCKED total; the same figure on two tabs").font = F_SUB
    for i, (proj, rule, detail) in enumerate(found, start=1):
        ws.cell(row=r + i, column=1, value=proj).font = F_BASE
        ws.cell(row=r + i, column=2, value=rule).font = F_BASE
        c = ws.cell(row=r + i, column=3, value=detail)
        c.font = F_BASE
        c.fill = FILL_REVIEW
    r += len(found) + 2
    if answered:
        ws.cell(row=r, column=1, value=f"ANSWERED — not open ({len(answered)}): headlines lost on purpose, named in "
                                       f"config.REGRESSION_ALLOWLIST with the reason and the date they clear by "
                                       f"(Coverage: N/A BY DESIGN)").font = F_SUB
        for i, ((proj, rule, detail), a) in enumerate(answered, start=1):
            ws.cell(row=r + i, column=1, value=proj).font = F_SUB
            ws.cell(row=r + i, column=2, value=f"{rule} — ANSWERED").font = F_SUB
            ws.cell(row=r + i, column=3, value=f"{detail[:200]} || allow-listed until {a.get('until', '-')}: "
                                               f"{a['reason']}"[:600]).font = F_SUB


# THE SCOREBOARD (G4 item 7f): counts the Gap Report and the Review Queue leave here as they are written, for the block
# at the top of Coverage and the Run Log's per-project line
BOARD_INPUTS: dict = {"gaps": {}, "review_open": Counter()}
SCOREBOARD_LINES: list = []     # the Run Log's per-project scoreboard lines of the last build


LOST_VERIFIED: list = []          # coverage rows VERIFIED in the previous build and not in this one (G5 item 1)
GAP_ROWS: list = []               # the Gap Report rows of this build (G5 item 5g)


def scoreboard_lines(board: dict, delta: dict | None = None) -> list[str]:
    """One line per project (ALL first): the scoreboard as text, for the Run Log and the console; each count with its
    change since the previous build where one is known (G5 item 1)."""
    out = []
    for p, b in board.items():
        d = (delta or {}).get(p) or {}

        def n(k):
            ch = d.get(k)
            return f"{b[k]}" + (f" ({ch:+d})" if ch else "")
        out.append(f"SCOREBOARD {p}: VERIFIED {n('VERIFIED')} · VERIFIED (derived) {n('VERIFIED (derived)')} · "
                   f"LIVE-UNVERIFIED {n('LIVE-UNVERIFIED')} · N/A {n('N/A')} · MISSING {n('MISSING')} · "
                   f"NOT WIRED {n('NOT WIRED')} · BROKEN {n('BROKEN')} · CHECK {n('CHECK')} · Gap P4 {n('Gap P4')} / "
                   f"P5 {n('Gap P5')} / P6 {n('Gap P6')} · Review Queue {n('Review Queue')} · verified share "
                   f"{b['Verified share']} · populated {b.get('Populated', '-')} · MISSING by cause "
                   f"{b.get('MISSING by cause', '-')}")
    return out


WIRING: dict = {}               # (project, metric) -> the wiring audit's class of the last build (G12 A1)


def wiring_lines(result: dict) -> list[str]:
    """The Run Log's wiring line: UNWIRED n, by project (G12 A1)."""
    s = wiring.summary(result)
    un = {p: c.get("UNWIRED", 0) for p, c in s["by_project"].items() if c.get("UNWIRED")}
    return [f"WIRING: UNWIRED {s['unwired']} — " + (", ".join(f"{p} {n}" for p, n in sorted(un.items())) or "none")
            + " · " + ", ".join(f"{k} {v}" for k, v in sorted(s["counts"].items()))
            + " (python check_offline_items.py wiring lists them)"]


def _board_unwired(board: dict, result: dict) -> None:
    """Each scoreboard project gains "UNWIRED" (the ALL row the total) — G12 A1."""
    s = wiring.summary(result)
    for p, b in board.items():
        if isinstance(b, dict):
            b["UNWIRED"] = s["unwired"] if p == "ALL" else (s["by_project"].get(p) or {}).get("UNWIRED", 0)


def lost_verified_lines(lost: list[dict]) -> list[str]:
    """One Run Log line per cell that was VERIFIED in the previous build and is not now, with why (G5 item 1)."""
    return [f"VERIFIED LOST {r['Project']} | {r['Tab']} | {str(r['Column'])[:70]}: now {r['Status']} — "
            f"{(r.get('Reason / route / error') or 'no reason recorded')[:300]}" for r in lost]


def _write_runlog_scoreboard(ws, lines: list[str]) -> None:
    """The scoreboard at the top of the Run Log (under its title), one line per project — after every build, so after
    every full run (G4 7f)."""
    if not lines:
        return
    ws.insert_rows(4, amount=len(lines) + 2)
    ws.cell(row=4, column=1, value="SCOREBOARD — this build (the same block opens the Coverage tab)").font = F_BOLD
    for i, line in enumerate(lines, start=5):
        ws.cell(row=i, column=1, value=line).font = F_BASE
