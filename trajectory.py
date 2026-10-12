"""
trajectory.py — every headline over five consecutive trailing 90-day blocks (theory layer, Phase 1; Jake 2026-10-12).

THE SAME FORMULA, SHIFTED. A headline cell is a formula over the Data tab's window columns (q0, q0_covered_days,
q0_events, a stock's "now"). Re-running the cell's own builder while build_workbook.Refs maps each reference k quarters
back (q0 -> qk, q1 -> q(k+1), q0_covered_days -> qk_covered_days, q0_events -> qk_events, the window's split share and
status -> the earlier window's, a STOCK's "now" -> its mean over quarter k) gives that headline as it would have read
k quarters ago — never a re-implementation that could drift from the cell. A reference with no shifted counterpart (a
flow's trailing-30-day "now", m1, y1, a quarter past Q-4) becomes NA(), so the shifted cell reads n/a rather than a
number from the wrong window. Q0 is the unshifted formula, so it IS the headline (the Trajectory tab checks that).

THE TRAJECTORY TAB holds, per (tab, project, headline): Q0 .. Q-4 as live formulas; T12M (the four latest quarters
summed for a window total, averaged for a rate, ratio or level); QoQ; the 4-quarter log-linear growth (annual); the
quarters improved (direction-aware, config.TRAJECTORY["direction"]); the trough of the last four; the years to the
row's benchmark (config.TRAJECTORY["benchmarks"]) at that growth; the source tier and Q0 coverage of the headline's
series; and the credibility verdict (Q0 equals the headline cell). The derived columns are computed when the workbook
is built (xlcalc, the evaluator the sanity rules and the Credibility report use) and written as values; Q0..Q-4 stay
formulas. Each headline cell on the tabs gets a trend cell beside it that reads this tab.
"""
from __future__ import annotations

import math
import re

import config

LABELS = ("Q0", "Q-1", "Q-2", "Q-3", "Q-4")
SHEET = "Trajectory"
HEAD = ["Project", "Key (tab|project|headline id)", "Tab", "Headline id", "Headline", "Direction (+1 higher is better)",
        "Q0", "Q-1", "Q-2", "Q-3", "Q-4", "T12M", "T12M basis", "QoQ", "4Q growth (annual, log-linear)",
        "Quarters in the growth", "Quarters improved (direction-aware)", "Trough of the last 4", "Trend",
        "Benchmark", "Target", "Years to benchmark", "Why n/a", "Source tier (headline series)",
        "Q0 coverage days (headline series)", "Credibility (Q0 = headline cell)", "Headline cell"]
COL = {h: i + 1 for i, h in enumerate(HEAD)}
MAX_ROWS = 6000                       # the trend cells' lookup range on this tab

ROWS: dict = {}                       # key -> row spec, in the order the tabs registered them (reset per build)
CELL_KEY: dict = {}                   # (sheet title, cell) -> key, for Master columns that reference an A-tab cell
CELL_SPEC: dict = {}                  # (sheet title, cell) -> spec of EVERY numeric A-tab cell, so Master can ask for one


def reset() -> None:
    ROWS.clear()
    CELL_KEY.clear()
    CELL_SPEC.clear()


def slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(text).lower())).strip("_")[:60]


def master_id(header: str) -> str:
    return "master_" + slug(header)


def direction(hid: str) -> int:
    return int(config.TRAJECTORY["direction"].get(hid, 1))


def key(tab: str, project: str, hid: str) -> str:
    return f"{tab}|{project}|{hid}"


def t12m_basis(header: str, fmt: str) -> str:
    """A window TOTAL (a 'Q0' figure in tokens or dollars) sums its four quarters; a rate, ratio or level averages."""
    return "sum" if "Q0" in str(header) and "%" not in str(fmt) and "x" not in str(fmt).lower() else "mean"


def register(sheet: str, project: dict, header: str, hid: str, builder, fmt: str, metric: str | None,
             cell: str) -> str:
    k = key(sheet[:2] if sheet != "Master" else "Master", project["name"], hid)
    ROWS.setdefault(k, {"sheet": sheet, "project": project, "header": header, "hid": hid, "builder": builder,
                        "fmt": fmt, "metric": metric, "cell": (sheet, cell)})
    CELL_KEY[(sheet, cell)] = k
    return k


def trend_formula(k: str) -> str:
    c = _letter(COL["Trend"])
    b = _letter(COL["Key (tab|project|headline id)"])
    return (f'=IFERROR(INDEX({SHEET}!${c}$2:${c}${MAX_ROWS},MATCH("{k}",{SHEET}!${b}$2:${b}${MAX_ROWS},0)),'
            f'"n/a — no trajectory row")')


def _letter(i: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(i)


# ----------------------------------------------------------------------------------------------- the statistics (pure)
def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) else None


def qoq(q0, q1):
    a, b = _num(q0), _num(q1)
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b)


def loglinear_growth(values: list) -> tuple[float | None, int, str]:
    """(annual growth, quarters used, why None) from the newest four quarters (values[0] = Q0). Log-linear: ln(v) on
    the quarter index over the quarters that hold a value; every one must be > 0 (a log of 0 or a negative is
    undefined — said, never dropped quietly). Needs two quarters. Annual = exp(4 x slope) - 1."""
    pts = [(3 - i, _num(v)) for i, v in enumerate(values[:4])]
    pts = [(x, v) for x, v in pts if v is not None]
    if len(pts) < 2:
        return None, len(pts), f"{len(pts)} quarter(s) with a value — growth needs two"
    if any(v <= 0 for _x, v in pts):
        return None, len(pts), "a quarter at or below 0 — log growth is undefined"
    xs, ys = [x for x, _ in pts], [math.log(v) for _, v in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return math.exp(4 * slope) - 1, len(pts), ""


def improved(values: list, d: int) -> tuple[int | None, int]:
    """(quarters improved, comparisons made) over Q0 vs Q-1 .. Q-3 vs Q-4, direction-aware; strictly better counts."""
    if d == 0:
        return None, 0
    n = k = 0
    for newer, older in zip(values[:4], values[1:5]):
        a, b = _num(newer), _num(older)
        if a is None or b is None:
            continue
        n += 1
        k += 1 if d * (a - b) > 0 else 0
    return (k if n else None), n


def trough(values: list, d: int) -> str | None:
    pts = [(LABELS[i], _num(v)) for i, v in enumerate(values[:4]) if _num(v) is not None]
    if d == 0 or not pts:
        return None
    lab, _v = (min if d > 0 else max)(pts, key=lambda t: t[1])
    return lab


def t12m(values: list, basis: str) -> tuple[float | None, str]:
    q = [_num(v) for v in values[:4]]
    if any(v is None for v in q):
        return None, f"needs all four quarters ({sum(v is not None for v in q)} held)"
    return (sum(q) if basis == "sum" else sum(q) / 4), basis


def years_to(target: float | None, now, growth, d: int, why_target: str = "") -> tuple[float | None, str]:
    """Years until `now` reaches `target` at the annual `growth`: 0 once met; n/a when the growth is 0 or points away,
    there is no growth (no history), the value is not positive, or there is no target."""
    x = _num(now)
    if target is None:
        return None, why_target or "no target value"
    if x is None:
        return None, "no Q0 value"
    if d * (x - target) >= 0:
        return 0.0, "met"
    if x <= 0:
        return None, "Q0 at or below 0 — no log path to the target"
    g = _num(growth)
    if g is None:
        return None, "no trailing growth (history short of two quarters, or a quarter ≤ 0)"
    if d * g <= 0:
        return None, f"trailing growth {g:+.1%}/yr points away from the target"
    return math.log(target / x) / math.log(1 + g), ""


def arrow(q, d: int) -> str:
    if q is None:
        return "–"
    if d == 0:
        return "↑" if q > 0 else "↓" if q < 0 else "→"
    good = d * q
    return "▲" if good > 0 else "▼" if good < 0 else "→"


def trend_text(q, g, imp, n, d: int) -> str:
    parts = [arrow(q, d)]
    parts.append(f"{q:+.1%} QoQ" if q is not None else "QoQ n/a")
    parts.append(f"{g:+.0%}/yr (4Q)" if g is not None else "4Q n/a")
    if imp is not None:
        parts.append(f"{imp}/{n} improved")
    return " · ".join(parts)


# ----------------------------------------------------------------------------------------------- benchmarks
def benchmark_for(hid: str) -> dict | None:
    for b in config.TRAJECTORY["benchmarks"]:
        if hid in b["on"]:
            return b
    return None


def benchmark_target(b: dict, data_by_key: dict) -> tuple[float | None, str]:
    if "target" in b:
        return float(b["target"]), ""
    prm = config.TRAJECTORY["params"].get(b.get("target_param", ""), {})
    if prm.get("value") is not None:
        return float(prm["value"]), ""
    m = prm.get("metric")
    if m:
        row = (data_by_key or {}).get(f"{prm.get('project', '')}|{m}") or {}
        v = _num(row.get("now"))
        if v is not None:
            return v, ""
        return None, f"no {m} stored yet ({prm.get('source', '')})"
    return None, "no target configured"
