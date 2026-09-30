#!/usr/bin/env python3
"""completeness_report.py — the data room's sign-off: every headline cell, one status. 2026-09-29 (Jake).

Read-only. For each project in scope (World Mobile PARKED), every headline metric that applies to
it is classified from the same aggregation the workbook uses (build_workbook.aggregate):

    COMPLETE            populated, reconciled, and checked against an independent reference
                        where one exists (named), or "no independent reference exists"
    MATURING            correct, window still filling — with the date it is full
    WAITING ON A DATE   blocked or empty until a named date
    NEEDS JAKE          an input or decision only Jake can supply (the exact one)
    N/A / ACCEPTED LIMIT  a recorded decision (by design, answered, superseded, closed)
    BUG                 fits none of the above: listed at the end with the row's own reason

SCOPE is portfolio.txt, as the workbook builds (World Mobile parked); the projects outside it are
one PARKED line — not fetched, so stale by design. A Review Queue flag is informational.

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
            "locked_tokens", "staking_yield_pct", "settlement_volume_annual_usd",
            "net_protocol_surplus_usd")
PARKED = {"World Mobile": "PARKED (Jake, 2026-09-29): no further work until he reopens it"}
FULL_DAYS = {"price_usd": 365}             # prices value every row of every window, Y1 included
Q0_DAYS = 90
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
WITHHELD = ("orphaned", "withdrawn", "suppressed", "disputed", "blocked", "measuring_point_changed",
            "implausible_delta", "unreconciled_flow", "refuted", "out_of_bounds")

# ===== RECORDED DECISIONS, SO A SETTLED CELL NEVER READS AS A BUG. Jake, 2026-09-29. =====
# A status that is a recorded decision maps to COMPLETE, N/A or ACCEPTED LIMIT — never BUG. The
# generic records are read from config (not_applicable, UNAVAILABLE closures, a closure that
# renders 0); these are the ones settled in review that have no single config field, each with
# the record it rests on. NEEDS JAKE is ONLY an input or decision Jake alone can supply.
_SUPERSEDED = "N/A — the derived d(circulating) - d(total) route is SUPERSEDED: "
DECISIONS = {
    ("GEODNET", "gross_issuance_tokens"): (
        "N/A", "by design: all 1bn GEOD were pre-minted (GEODNET tokenomics, docs.geodnet.com; "
               "DefiLlama's geodnet adapter reads burns only), so nothing is minted; the mining-"
               "wallet release is measured as pool_release_tokens (the issuance basis) and, from "
               "2026-09-30, restated as emissions_tokens"),
    ("Maple", "emissions_tokens"): (
        "N/A", "no emission programme runs: staking rewards were sunset by MIP-019 (maple-docs "
               "@07d8ff8e syrup-tokenomics/staking.md) and Drips ended after Q4 2025, last claims "
               "18 Feb 2026 (syrupusdc-usdt-for-lenders/drips-rewards.md); the SSF's release is "
               "pool_release_tokens (H)"),
    ("Near", "emissions_tokens"): (
        "N/A", "the issuance route: NEAR's emission is gross_issuance_tokens (declared 5% protocol "
               "rule, header-supply cross-check)"),
    ("Pendle", "emissions_tokens"): (
        "N/A", "the issuance route: Pendle's emission is reported as gross_issuance_tokens"),
    ("Chainlink", "pool_release_tokens"): (
        "N/A", _SUPERSEDED + "the RewardVault emission rate (reward_emission_rate_annual) supplies "
                            "the release; CoinGecko circulating does not update"),
    ("Hyperliquid", "pool_release_tokens"): (
        "N/A", _SUPERSEDED + "HyperCore futureEmissions (future_emissions_tokens) supplies it; "
                            "CoinGecko circulating does not update"),
    ("Aethir", "pool_release_tokens"): (
        "N/A", _SUPERSEDED + "the declared emission schedule supplies emissions_tokens; CoinGecko "
                            "circulating does not update"),
    # REOPENED 2026-09-30 (H): the ACCEPTED LIMIT had no native-source evidence. The route is
    # Maple's own transparency page — the SSF chart's SYRUP balance series, less the monthly
    # buyback inflows the same page lists. The SSF address is in no Maple repository (maple-
    # labs/address-registry @3df2052c, maple-docs @07d8ff8e), so the series comes from the page.
    ("Maple", "pool_release_tokens"): (
        "NEEDS JAKE", "run `python check_offline_items.py maple_ssf_history` and paste it back: "
                      "the SSF chart's series on maple.finance's transparency page is the route; "
                      "release = balance decline net of the monthly buyback inflows it lists"),
    ("GEODNET", "locked_tokens"): (
        "NEEDS JAKE", "~3M GEOD locked, the manual row (value, source, date) — no staking contract "
                      "is established: both behavioural candidates are ruled out (0x8f10b468… is "
                      "KyberSwap's executor, 0x5fe84b85… a QuickSwap pool) and DefiLlama has no "
                      "GEODNET staking adapter. GEODNET's console or docs naming the contract "
                      "unblocks the 365-day archive read; Blockworks stays a cross-check once its "
                      "terms are read (probe blockworks_geodnet)"),
    # B (2026-09-30): The Block's page is gone. ONE definition must cover Ethereum, NEAR, Plume
    # and Hyperliquid for the Network Reserve Ratio to be comparable across A1 — Jake's pick.
    ("Ethereum", "settlement_volume_annual_usd"): (
        "NEEDS JAKE", "choose ONE settlement-volume source for all four chains (options in the "
                      "second-pass report: Artemis 'settlement volume' — keyed, definition and "
                      "chain coverage not public; Visa Onchain Analytics — adjusted stablecoin "
                      "volume, no public API; Coin Metrics — CC BY-NC); none is wired until then"),
    ("Plume", "settlement_volume_annual_usd"): (
        "NEEDS JAKE", "the same choice as Ethereum's (B): one settlement-volume definition for "
                      "Ethereum, NEAR, Plume and Hyperliquid"),
    ("Sky", "net_protocol_surplus_usd"): (
        "NEEDS JAKE", "September 2026 NPS: a manual monthly row in manual_overrides.csv when Sky "
                      "publishes it"),
}
# WAITING ON A SERIES THAT NEEDS n DAILY READINGS (Ethereum's yield: a week of d(Eth2Staking)).
# Ethereum staking_yield_pct's week-long wait on Eth2Staking was lifted 2026-09-30: the
# consensus part now has its declared history leg (ultrasound-derived issuance before the first
# d(Eth2Staking) row) and ETH.Store is backfilled per day (A1/A2).
WAIT_ON_SERIES: dict = {}


def mechanism_start(p: dict, metric: str, asof: pd.Timestamp) -> dict | None:
    """{"from", "why"} where the series' mechanism began inside the backfill year (Jake,
    2026-09-29): declared (config.MECHANISM_START), a declared issuance schedule's first step, or a
    deployment start the archive backfill recorded (fetch.archive.series_starts)."""
    name, year_ago = p["name"], str((asof - pd.Timedelta(days=365)).date())
    got = (getattr(config, "MECHANISM_START", {}) or {}).get((name, metric))
    if got:
        return got
    sched = p.get("issuance_schedule") or {}
    if sched.get("steps") and (metric == "gross_issuance_tokens"
                               or (metric == "emissions_tokens" and sched.get("also_emissions"))):
        first = min(s0["from"] for s0 in sched["steps"])
        if first > year_ago:
            return {"from": first, "why": f"the declared schedule begins {first}: "
                                          f"{str(sched.get('note', ''))[:120]}"}
    try:
        import history_audit
        st = history_audit.series_start(p, metric)
    except Exception:  # noqa: BLE001
        st = None
    return st if st and st["from"] > year_ago else None


def forward_only(name: str, metric: str) -> str | None:
    """The forward-only reason, with the native routes checked where recorded (2026-09-30)."""
    fwd = config.HISTORY_FORWARD_ONLY.get(name) or {}
    if metric not in fwd.get("metrics", ()):
        return None
    ev = fwd.get("native_checked") or []
    return fwd.get("why") + (" | native sources checked: " + "; ".join(
        f"{e['source'].split(' (')[0]}: {e['finding'][:90]}" for e in ev) if ev else "")


def portfolio_scope() -> tuple[list[dict], list[str]]:
    """(in scope, parked names): portfolio.txt's names, as the workbook builds; all if absent."""
    names, _ = config.read_portfolio(config.PORTFOLIO_FILE)
    if not names:
        return list(config.PROJECTS), []
    return ([config.PROJECT_BY_NAME[n] for n in names],
            [p["name"] for p in config.PROJECTS if p["name"] not in names])


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
    # A DECLARED PRIMARY IS CHECKED AGAINST THE DERIVED ROUTE (NEAR, 2026-09-30): more than
    # max_ratio apart either way blocks the row (build_workbook._issuance_views).
    prim = config.issuance_primary(name) or {}
    if prim.get("kind") == "declared_rate" and prim.get("metric") == metric and prim.get("observed_source_prefix"):
        refs.append(f"cross-check {prim['observed_source_prefix']} (>{prim.get('max_ratio', 10)}x apart blocks)")
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


def classify(p: dict, metric: str, row: dict, first: str | None, asof: pd.Timestamp,
             firsts: dict | None = None) -> tuple[str, str]:
    name, status = p["name"], str(row.get("status") or "missing")
    note = str(row.get("note") or "")
    kind = (config.METRICS.get(metric) or {}).get("kind")
    future = sorted(d for d in _DATE.findall(note) if d > str(asof.date()))
    decided = DECISIONS.get((name, metric))
    if decided:
        v, d = decided[:2]
        # AN ACCEPTED LIMIT NEEDS NATIVE-SOURCE EVIDENCE (Jake, 2026-09-30): the routes tried, on
        # the project's own dashboards, explorers and APIs — not only the aggregators.
        if v == "ACCEPTED LIMIT" and not (decided[2:] and decided[2]):
            return "BUG", f"ACCEPTED LIMIT without evidence that project-native sources were checked: {d}"
        return v, d
    wait = WAIT_ON_SERIES.get((name, metric))
    if wait:
        f0 = (firsts or {}).get((name, wait["series"]))
        if f0 is None or (asof - pd.Timestamp(f0)).days < wait["days"]:
            when = (str((pd.Timestamp(f0) + pd.Timedelta(days=wait["days"])).date()) if f0
                    else f"{wait['days']} days after the first {wait['series']} reading")
            return "WAITING ON A DATE", f"{when} — {wait['why']}"
    if status == "n/a":
        return "N/A", note.split(" | ")[0] or "not applicable to this project"
    closed = config.unavailable_for(name, metric)
    # A CLOSURE CLOSES A ROUTE, NOT A METRIC ANOTHER ROUTE IS FILLING (Jake, 2026-09-30):
    # Ethereum's 2026-09-22 closure is about ultrasound.money having no issuance endpoint; it
    # marked gross_issuance_tokens ACCEPTED LIMIT while Etherscan's readings were arriving. A
    # route is live when the store holds a reading inside the Q0 window.
    latest = row.get("latest_date")
    live = (latest is not None and not pd.isna(latest) and str(latest) != ""
            and (asof - pd.Timestamp(latest)).days < Q0_DAYS)
    if closed and status != "ok" and not live:
        ev = closed.get("native_checked")
        if not ev:
            return "BUG", (f"ACCEPTED LIMIT without evidence that project-native sources were "
                           f"checked (closed {closed.get('closed_on', '')}): {closed.get('summary', '')[:200]}")
        return "ACCEPTED LIMIT", (f"closed {closed.get('closed_on', '')}: {closed.get('summary', '')[:200]} | "
                                  f"native sources checked: " + "; ".join(
                                      f"{e.get('source')}: {e.get('finding')}" for e in ev)[:400])
    fwd = forward_only(name, metric)
    if status == "waiting" or (status in WITHHELD and future):
        return "WAITING ON A DATE", (future[0] + " — " if future else "") + note[:220]
    # BLOCKED ONLY UNTIL ITS READINGS COVER THE WINDOW: correct, still filling (a guard, not a fault)
    if status in WITHHELD and re.search(r"waiting for .{0,120}cover|cover(s)? the window|unequal coverage",
                                        note, re.I):
        return "MATURING", (f"blocked until its readings cover the window: {note[:200]}"
                            + (f" — FORWARD-ONLY: {fwd}" if fwd else ""))
    if status in ("missing", "gap") and config.is_manual_quarterly(name, metric):
        return "NEEDS JAKE", f"a manual quarterly row for {metric} in manual_overrides.csv"
    # A REVIEW QUEUE FLAG IS INFORMATIONAL (a change threshold, a partial-coverage note): the
    # figure is classified on its own merits and the flag rides along.
    flag = ""
    if status == "review":
        status, flag = "ok", " | informational: flagged in the Review Queue"
    if status != "ok":
        return "BUG", f"status {status}: {note[:260]}"
    need = FULL_DAYS.get(metric, Q0_DAYS)
    # COMPLETE FROM MECHANISM START (Jake, 2026-09-29): a series that begins with its mechanism
    # has no earlier history to fetch. The youth of any rate built on it is said separately.
    start = mechanism_start(p, metric, asof)
    if start and first and first <= str((pd.Timestamp(start["from"]) + pd.Timedelta(days=3)).date()):
        age = (asof - pd.Timestamp(start["from"])).days + 1
        return "COMPLETE", (f"COMPLETE FROM MECHANISM START {start['from']}: {start['why']} | "
                            f"young: rates based on it cover {age} day(s)" + flag)
    # MATURING CARRIES ITS REASON: a recorded forward-only series says why; one without a record
    # is a history a backfill should fill, and says THAT.
    why_wait = (f" — FORWARD-ONLY: {fwd}" if fwd else
                " — NOT FORWARD-ONLY: its inputs have history, so a backfill should fill it "
                "(history_audit.py names the route)")
    if kind == "flow" and str(row.get("q0_basis") or "").startswith("3 complete months"):
        cov = row.get("q0_covered_days")
        if cov is not None and not pd.isna(cov) and cov < 89:
            last = asof.to_period("M") - 1
            return "MATURING", (f"{int(cov)} of the Q0 months' days held; full with {last + 3} "
                                f"complete" + why_wait + flag)
    elif first:
        age = (asof - pd.Timestamp(first)).days + 1
        if age < need:
            full = (pd.Timestamp(first) + pd.Timedelta(days=need - 1)).date()
            return "MATURING", f"{age} of {need} days since {first}; full on {full}" + why_wait + flag
    ref = reference(p, metric, row)
    if closed:                                  # a closure that renders a measured 0 (Fluid)
        ref = f"{note[:200]} (config.UNAVAILABLE, closed {closed.get('closed_on', '')})"
    why = str(row.get("why_amber") or "")
    caveat = f" | caveat: {why[:160]}" if row.get("confidence") == "AMBER" and why and not closed else ""
    return "COMPLETE", (ref or "no independent reference exists") + caveat + flag


def report(data: pd.DataFrame, long: pd.DataFrame, asof: pd.Timestamp, only: str | None = None,
           scope: tuple | None = None) -> list[tuple]:
    firsts = (long.groupby(["project", "metric"])["date"].min().dt.strftime("%Y-%m-%d").to_dict()
              if not long.empty else {})
    rows = {(r["project"], r["metric"]): r for r in data.to_dict("records")}
    projects, parked = scope or portfolio_scope()
    out = []
    for p in projects:
        if only and p["name"] != only:
            continue
        if p["name"] in PARKED:
            out.append((p["name"], "*", "PARKED", PARKED[p["name"]]))
            continue
        for m in headline_metrics(p):
            row = rows.get((p["name"], m)) or {"status": "missing"}
            # the first date AS READ (after views: a stitched history counts), else the store's
            fd = row.get("first_date")
            first = fd if isinstance(fd, str) and fd else firsts.get((p["name"], m))
            verdict, detail = classify(p, m, row, first, asof, firsts)
            out.append((p["name"], m, verdict, detail))
    if parked and not only:
        out.append((f"{len(parked)} not in portfolio.txt", "*", "PARKED",
                    "not fetched, so stale by design: " + ", ".join(parked)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--project")
    ap.add_argument("--md", metavar="FILE")
    a = ap.parse_args(argv)
    import build_workbook as bw
    from store import Store
    scope = portfolio_scope()
    bw._SCOPE = list(scope[0])                  # the workbook's own portfolio scope, same views
    st = Store(a.db)
    try:
        long = st.load_long()
        asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
        data = bw.aggregate(long, st.fetch_status(), asof, gaps=st.gap_report(), review=st.review_queue())
    finally:
        st.close()
    table = report(data, long, asof, a.project, scope)
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
