#!/usr/bin/env python3
"""completeness_report.py — the data room's sign-off: every headline cell, one status. 2026-09-29 (Jake).

Read-only. For each project in scope (World Mobile PARKED), every headline metric that applies to
it is classified from the same aggregation the workbook uses (build_workbook.aggregate):

    COMPLETE            populated, reconciled, and checked against an independent reference
                        where one exists (named), or "no independent reference exists"
    MATURING            correct, window still filling — with the date it is full
    WAITING ON A DATE   blocked or empty until a named date
    NEEDS JAKE          the exact input (a manual row, a probe, a Review Queue item)
    BUG                 fits none of the above: listed at the end with the row's own reason

    python completeness_report.py                 the table, then the BUG list
    python completeness_report.py --md FILE       also write it as Markdown (for the sign-off page)
    python completeness_report.py --project Sky
"""
from __future__ import annotations

import argparse
import re
import sys

import pandas as pd

import config

HEADLINE = ("price_usd", "circulating_supply", "fees_usd", "revenue_usd", "holders_revenue_usd",
            "customer_revenue_usd", "actual_buyback_tokens", "actual_buyback_usd",
            "gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "pool_release_tokens",
            "locked_tokens", "staking_yield_pct")
PARKED = {"World Mobile": "PARKED (Jake, 2026-09-29): no further work until he reopens it"}
FULL_DAYS = {"price_usd": 365}             # prices value every row of every window, Y1 included
Q0_DAYS = 90
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
WITHHELD = ("orphaned", "withdrawn", "suppressed", "disputed", "blocked", "measuring_point_changed",
            "implausible_delta", "unreconciled_flow", "refuted", "out_of_bounds")


def headline_metrics(p: dict) -> list[str]:
    applicable = set(config.metrics_for_project(p))
    wanted = list(HEADLINE) + [config.a4_burn_metric(p["name"]), config.issuance_basis(p["name"])]
    return [m for m in dict.fromkeys(wanted) if m in applicable]


def reference(p: dict, metric: str, row: dict) -> str:
    """The independent reference this cell was checked against, where one exists."""
    name, refs = p["name"], []
    for c in p.get("cross_checks") or []:
        if c.get("primary") == metric:
            refs.append(f"cross-check {c['secondary']}")
    for r in p.get("reference_values") or []:
        if r.get("metric") == metric:
            refs.append(f"published {r.get('period')} figure")
            break
    vy = config.VALIDATOR_YIELD.get(name) or {}
    if metric == "staking_yield_pct" and vy.get("cross_check_metric"):
        refs.append(f"cross-check {vy['cross_check_metric']}")
    if "flow_stock_move" in row or ":delta" in str(row.get("source", "")):
        from build_workbook import flow_stock
        parent = flow_stock(name, metric)
        if parent:
            refs.append(f"telescopes to {parent} (flows = the stock's move)")
    if any(sc.get("metric") == metric for sc in p.get("log_scans") or []):
        refs.append("scan reconciled to balanceOf to the wei")
    return "; ".join(refs)


def classify(p: dict, metric: str, row: dict, first: str | None, asof: pd.Timestamp) -> tuple[str, str]:
    name, status = p["name"], str(row.get("status") or "missing")
    note = str(row.get("note") or "")
    kind = (config.METRICS.get(metric) or {}).get("kind")
    future = sorted(d for d in _DATE.findall(note) if d > str(asof.date()))
    if status == "n/a":
        return "N/A", note.split(" | ")[0] or "not applicable to this project"
    if status == "waiting" or (status in WITHHELD and future):
        return "WAITING ON A DATE", (future[0] + " — " if future else "") + note[:220]
    # BLOCKED ONLY UNTIL ITS READINGS COVER THE WINDOW: correct, still filling (a guard, not a fault)
    if status in WITHHELD and re.search(r"waiting for .{0,120}cover|cover(s)? the window|unequal coverage",
                                        note, re.I):
        return "MATURING", f"blocked until its readings cover the window: {note[:200]}"
    manual = (config.is_manual_quarterly(name, metric) or "manual_overrides" in note
              or "check_offline_items" in note)
    if status in ("missing", "gap") and manual:
        return "NEEDS JAKE", note[:260] or f"a manual row for {metric} in manual_overrides.csv"
    if status == "review":
        return "NEEDS JAKE", f"a Review Queue item holds this figure: {note[:220]}"
    if status != "ok":
        return "BUG", f"status {status}: {note[:260]}"
    need = FULL_DAYS.get(metric, Q0_DAYS)
    if kind == "flow" and row.get("q0_basis", "").startswith("3 complete months"):
        cov = row.get("q0_covered_days")
        if cov is not None and not pd.isna(cov) and cov < 89:
            last = asof.to_period("M") - 1
            return "MATURING", f"{int(cov)} of the Q0 months' days held; full with {last + 3} complete"
    elif first:
        age = (asof - pd.Timestamp(first)).days + 1
        if age < need:
            full = (pd.Timestamp(first) + pd.Timedelta(days=need - 1)).date()
            return "MATURING", f"{age} of {need} days since {first}; full on {full}"
    ref = reference(p, metric, row)
    why = str(row.get("why_amber") or "")
    return "COMPLETE", (ref or "no independent reference exists") + (f" | caveat: {why[:160]}" if
                                                                     row.get("confidence") == "AMBER" and why else "")


def report(data: pd.DataFrame, long: pd.DataFrame, asof: pd.Timestamp, only: str | None = None) -> list[tuple]:
    firsts = (long.groupby(["project", "metric"])["date"].min().dt.strftime("%Y-%m-%d").to_dict()
              if not long.empty else {})
    rows = {(r["project"], r["metric"]): r for r in data.to_dict("records")}
    out = []
    from build_workbook import scoped_projects
    for p in scoped_projects():
        if only and p["name"] != only:
            continue
        if p["name"] in PARKED:
            out.append((p["name"], "*", "PARKED", PARKED[p["name"]]))
            continue
        for m in headline_metrics(p):
            row = rows.get((p["name"], m)) or {"status": "missing"}
            verdict, detail = classify(p, m, row, firsts.get((p["name"], m)), asof)
            out.append((p["name"], m, verdict, detail))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--project")
    ap.add_argument("--md", metavar="FILE")
    a = ap.parse_args(argv)
    from build_workbook import aggregate
    from store import Store
    st = Store(a.db)
    try:
        long = st.load_long()
        asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
        data = aggregate(long, st.fetch_status(), asof, gaps=st.gap_report(), review=st.review_queue())
    finally:
        st.close()
    table = report(data, long, asof, a.project)
    counts = pd.Series([v for _, _, v, _ in table]).value_counts().to_dict()
    lines = [f"COMPLETENESS as of {asof.date()} — " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    current = None
    for n, m, v, d in table:
        if n != current:
            current = n
            lines.append(f"\n{n}")
        lines.append(f"  {v:<18} {m:<24} {d}")
    bugs = [(n, m, d) for n, m, v, d in table if v == "BUG"]
    lines.append(f"\nBUGS ({len(bugs)}) — cells that fit no status:" if bugs else "\nBUGS: none")
    lines += [f"  {n}/{m}: {d}" for n, m, d in bugs]
    print("\n".join(lines))
    if a.md:
        md = [f"# Data room completeness — {asof.date()}", "",
              ", ".join(f"**{k}** {v}" for k, v in sorted(counts.items())), "",
              "| Project | Metric | Status | Detail |", "|---|---|---|---|"]
        md += [f"| {n} | {m} | {v} | {d.replace('|', '/')} |" for n, m, v, d in table]
        md += ["", f"## Bugs ({len(bugs)})", ""] + [f"- {n}/{m}: {d}" for n, m, d in bugs]
        open(a.md, "w").write("\n".join(md) + "\n")
        print(f"\nwritten {a.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
