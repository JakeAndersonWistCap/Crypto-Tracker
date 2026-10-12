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
quarters improved (direction-aware, config.TRAJECTORY["rows"]); the trough of the last four; the years to the
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
        "Group",
        "Q0", "Q-1", "Q-2", "Q-3", "Q-4", "T12M", "T12M basis", "T12M a quarter earlier (Q-1..Q-4)", "QoQ",
        "4Q growth (annual, log-linear)", "Growth label (NEW / STEP CHANGE)", "Quarters in the growth",
        "Quarters improved (direction-aware)",
        "Trough of the last 4", "Trend", "Direction basis",
        "Benchmark", "Target", "Years to benchmark", "Why n/a", "Series break (basis change)",
        "MODELLED quarters (excluded from any scoring)",
        "Source tier (headline series)",
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


def direction(hid: str) -> int | None:
    """The row's declared direction (config.TRAJECTORY["rows"]) — None when not declared: nothing defaults to +1
    (Jake's Phase 1 review 2026-10-12, item 5)."""
    row = config.TRAJECTORY["rows"].get(hid)
    return int(row[1]) if row else None


def group(hid: str) -> str:
    """Fundamentals / Supply / Valuation / Market (item 6); '' when not declared."""
    row = config.TRAJECTORY["rows"].get(hid)
    return row[0] if row else ""


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


def growth_rule(values: list, step_at: int | None = None) -> tuple[float | None, int, str, str]:
    """THE GROWTH RULES (Jake's Phase 1 review 2026-10-12, item 4) over the newest four quarters (values[0] = Q0):
    (annual growth, quarters used, label, why). A growth figure needs >= min_quarters quarters, all > 0, in ONE regime:
      * a break of basis or mechanism inside the window (`step_at` = the quarter holding it) -> "STEP CHANGE at Q-x";
      * a mechanism that starts inside the window (the oldest quarters READ 0 or less; an empty quarter is history
        not held, said as such) -> "NEW since Q-x";
      * an earliest quarter below new_share (10%) of the latest -> "NEW since Q-x", the first quarter at >= 10%.
    Otherwise the log-linear growth (loglinear_growth). The label replaces the rate; it is never both."""
    rule = config.TRAJECTORY["growth"]
    q = [_num(v) for v in values[:4]]
    if step_at is not None and step_at < 4:
        return None, 0, f"STEP CHANGE at {LABELS[step_at]}", "a break of basis inside the window"
    alive = [i for i, v in enumerate(q) if v is not None and v > 0]
    if alive and q[0] is not None and q[0] > 0:
        oldest = max(alive)
        # A MECHANISM THAT STARTS: the older quarters READ 0 (or less) — not empty. An empty quarter is history we do
        # not hold (the gap report's business), never evidence that the series is new.
        older = q[oldest + 1:4]
        if oldest < 3 and older and all(v is not None and v <= 0 for v in older):
            return None, len(alive), f"NEW since {LABELS[oldest]}", "the mechanism starts inside the window (0 before)"
        first_big = max(i for i in alive if q[i] >= rule["new_share"] * q[0])
        if q[oldest] < rule["new_share"] * q[0]:
            return (None, len(alive), f"NEW since {LABELS[first_big]}",
                    f"{LABELS[oldest]} is below {rule['new_share']:.0%} of Q0 — a new regime, not a growth rate")
    pts = [v for v in q if v is not None]
    if len(pts) < rule["min_quarters"]:
        return None, len(pts), "", f"{len(pts)} quarter(s) with a value — growth needs {rule['min_quarters']}"
    if any(v <= 0 for v in pts):
        return None, len(pts), "", "a quarter at or below 0 — log growth is undefined"
    g, n, why = loglinear_growth(values)
    return g, n, "", why


def improved(values: list, d: int | None) -> tuple[int | None, int]:
    """(quarters improved, comparisons made) over Q0 vs Q-1 .. Q-3 vs Q-4, direction-aware; strictly better counts."""
    if not d:
        return None, 0
    n = k = 0
    for newer, older in zip(values[:4], values[1:5]):
        a, b = _num(newer), _num(older)
        if a is None or b is None:
            continue
        n += 1
        k += 1 if d * (a - b) > 0 else 0
    return (k if n else None), n


def trough(values: list, d: int | None) -> str | None:
    pts = [(LABELS[i], _num(v)) for i, v in enumerate(values[:4]) if _num(v) is not None]
    if not d or not pts:
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


def arrow(q, d: int | None) -> str:
    if q is None or d is None:
        return "–"
    if d == 0:
        return "↑" if q > 0 else "↓" if q < 0 else "→"
    good = d * q
    return "▲" if good > 0 else "▼" if good < 0 else "→"


def trend_text(q, g, imp, n, d: int | None, label: str = "") -> str:
    """THE ARROW FOLLOWS THE 4-QUARTER DIRECTION (Jake's Phase 1 review 2026-10-12, item 5), with quarters improved;
    QoQ is secondary text. A growth label (NEW / STEP CHANGE) stands in for the rate and carries no arrow."""
    if d is None:
        return "direction not declared · " + (f"QoQ {q:+.1%}" if q is not None else "QoQ n/a")
    parts = [arrow(None if label else g, d)]
    parts.append(label or (f"{g:+.0%}/yr (4Q)" if g is not None else "4Q n/a"))
    if imp is not None:
        parts.append(f"{imp}/{n} improved")
    parts.append(f"QoQ {q:+.1%}" if q is not None else "QoQ n/a")
    return " · ".join(parts)


# ----------------------------------------------------------------------------------------------- lumpy flows
# LUMPY FLOWS (Jake 2026-10-12, decision 8 on 6f8c86c): a row that includes a DISCRETE flow — unlocks
# (unlock_release_tokens), a declared lumpy series (config.LUMPY_FLOWS), a series annualised by events
# (config.events_per_year) or a monthly series (three complete months, Data q0_basis) — takes its direction and growth
# from the ROLLING T12M: T12M now (Q0..Q-3) against T12M a quarter earlier (Q-1..Q-4). Single quarters are shown, never
# used for direction (no QoQ arrow, no quarters-improved count).
DISCRETE_METRICS = ("unlock_release_tokens",)


def lumpy_reason(project: str, metrics, data_by_key: dict) -> str:
    for m in sorted(metrics):
        if m in DISCRETE_METRICS:
            return f"{m} (unlocks: discrete releases)"
        if config.LUMPY_FLOWS.get((project, m)):
            return f"{m} (config.LUMPY_FLOWS)"
        if config.events_per_year(project, m):
            return f"{m} (annualised by events)"
        basis = str((data_by_key.get(f"{project}|{m}") or {}).get("q0_basis") or "")
        if "month" in basis.lower() and "trailing" not in basis.lower():
            return f"{m} (a monthly series)"
    return ""


def rolling_t12m(values: list, basis: str) -> tuple:
    """(T12M now, T12M a quarter earlier, change over the quarter, annualised growth, why None)."""
    t0, w0 = t12m(values[0:4], basis)
    t1, w1 = t12m(values[1:5], basis)
    if t0 is None or t1 is None:
        return t0, t1, None, None, f"the rolling T12M needs Q0..Q-4 ({w0 or w1})"
    if t1 <= 0 or t0 <= 0:
        return t0, t1, None, None, "a T12M at or below 0 — growth is undefined (direction still read)"
    return t0, t1, t0 / t1 - 1, (t0 / t1) ** 4 - 1, ""


def lumpy_trend_text(t0, t1, chg, annual, d: int | None, label: str = "") -> str:
    if label:
        return f"– · {label} (rolling T12M)"
    diff = None if t0 is None or t1 is None else t0 - t1
    parts = [arrow(diff, d)]
    parts.append(f"T12M {chg:+.1%} vs a quarter earlier" if chg is not None else
                 ("T12M direction only" if diff is not None else "T12M n/a"))
    parts.append(f"{annual:+.0%}/yr (rolling T12M)" if annual is not None else "growth n/a")
    return " · ".join(parts)


# ----------------------------------------------------------------------------------------------- the basis rule
# A CHANGE OF BASIS MUST NEVER READ AS A TREND (Jake 2026-10-12, decision 7 on 6f8c86c). History is restated on the
# current basis wherever it can be (archive reads at past blocks, orphan_cleanup.sql CK); where it is not (yet), the
# row says SERIES BREAK <date> and no QoQ, growth or direction is computed across it — only between quarters that lie
# wholly on the current basis.
#   the basis of a day's row = its measuring point (fetch.base._measuring_point: the composition of a summed holding is
#   part of it), except for the read-time views whose bracket NAMES the basis (BRACKET_BASIS: which circulating series
#   was used, whether NEAR's lockup rule was in force), dates stripped. A declared handover whose composition_change
#   starts "none" (or that declares none) is one basis. A derived circulating series inherits the breaks of its inputs (its total, the
#   subtracted and added stocks), and circulating_supply_used those of the series it used.
BRACKET_BASIS = ("circulating_supply_used", "circulating_supply_lockup_rule")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REF = re.compile(r'"\|([a-z0-9_]+)",Data!')


def referenced_metrics(formula) -> set[str]:
    """The Data metrics a headline formula reads (Refs.D: MATCH($A{r}&"|metric",Data!...))."""
    return set(_REF.findall(str(formula or "")))


def basis_of(metric: str, source: str) -> str:
    from fetch.base import _measuring_point
    src = str(source or "")
    if metric in BRACKET_BASIS:
        m = re.search(r"\[([^\]]*)\]", src)
        return _DATE.sub("", m.group(1)).strip() if m else src
    return _measuring_point(src)


def _inputs(project: str, metric: str, groups: dict) -> tuple:
    if metric == "circulating_supply_used":
        g = groups.get((project, metric))
        used = set()
        if g is not None and not g.empty:
            for s in g["source"].astype(str).unique():
                m = re.search(r"\[([a-z0-9_]+)", s)
                if m:
                    used.add(m.group(1))
        return tuple(sorted(used))
    spec = config.circulating_onchain(project) or {}
    if metric == "circulating_supply_onchain" or (spec.get("metric") == metric and metric.startswith("circulating")):
        return tuple(x for x in ((spec.get("total") or "total_supply"), *(spec.get("subtract") or ()),
                                 *(spec.get("add") or ())) if x)
    if metric == "circulating_supply_lockup_rule":
        return ("total_supply", "near_lockup_locked_tokens", "near_foundation_accounts_tokens")
    return ()


def _own_breaks(project: str, metric: str, groups: dict, since) -> list[tuple]:
    import pandas as pd
    g = groups.get((project, metric))
    if g is None or g.empty:
        return []
    g = g.assign(date=pd.to_datetime(g["date"])).sort_values("date")
    decl = config.declared_handover(project, metric) or {}
    # a declared handover is one series unless its composition_change says the quantity steps (Ether.fi's total)
    cc = str(decl.get("composition_change", "none")).strip().lower()
    benign = bool(decl) and cc.startswith("none")
    pts = set(decl.get("ordered_points") or ())
    out, prev = [], None
    for d, src in zip(g["date"], g["source"].astype(str)):
        b = basis_of(metric, src)
        if prev is not None and b != prev and d >= since:
            if not (benign and prev in pts and b in pts):
                out.append((d, metric, prev, b))
        prev = b
    return out


def basis_breaks(project: str, metrics, groups: dict, since) -> list[tuple]:
    """[(date, metric, old basis, new basis)] — every change of basis on or after `since` in the series the metrics
    read, their inputs included, oldest first."""
    seen, todo, out = set(), list(metrics), []
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        out += _own_breaks(project, m, groups, since)
        todo += [x for x in _inputs(project, m, groups) if x not in seen]
    return sorted(out, key=lambda t: t[0])


def modelled_quarters(project: str, metrics, groups: dict, asof, period_days: int, n: int) -> list[str]:
    """The quarters (labels) in which a series the row reads — its inputs included — holds a MODELLED leg
    (config.is_modelled; Jake 2026-10-12, Phase 1 follow-up item 2). Shown on the row; excluded from any scoring."""
    import pandas as pd
    seen, todo, days = set(), list(metrics), []
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        g = groups.get((project, m))
        if g is not None and not g.empty:
            mod = g[g["source"].astype(str).map(config.is_modelled)]
            days += list(pd.to_datetime(mod["date"]))
        todo += [x for x in _inputs(project, m, groups) if x not in seen]
    out = []
    for k in range(n):
        hi = pd.Timestamp(asof) - pd.Timedelta(days=period_days * k)
        lo = hi - pd.Timedelta(days=period_days - 1)
        if any(lo <= d <= hi for d in days):
            out.append(LABELS[k])
    return out


def clean_quarters(asof, break_day, period_days: int, n: int) -> int:
    """How many quarters, Q0 first, lie wholly on or after the break (quarter k = the period_days ending
    asof − k x period_days)."""
    import pandas as pd
    k = 0
    while k < n and (pd.Timestamp(asof) - pd.Timedelta(days=period_days * (k + 1) - 1)) >= pd.Timestamp(break_day):
        k += 1
    return k


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
