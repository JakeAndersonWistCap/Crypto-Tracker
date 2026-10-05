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
            "locked_tokens", "staking_yield_pct", "settlement_volume_usd", "network_reserve_ratio",
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
    ("Aethir", "gross_issuance_tokens"): (
        "COMPLETE", "0 — DECLARED (Jake, 2026-10-01): ATH is pre-minted (42bn); supplier and staker rewards "
                    "are releases from pre-minted pools (pool_release_tokens), nothing is minted. SOURCED (probes5, "
                    "2026-10-01): Ethereum totalSupply 42bn = hard cap, so mint() cannot add supply; "
                    "Arbitrum is Axelar ITS, whose mint/burn moves supply between chains"),
    # Aethir pool_release_tokens: WIRED 2026-10-01 (Jake) — supplier + staker rewards, measured from
    # the dashboard's cumulatives (build_workbook._measured_emissions_views); no decision left.
    # Maple pool_release_tokens: WIRED 2026-09-30 from Jake's probe 5 — the SSF chart's island
    # props on maple.finance/transparency (fetch/maple_transparency.ssf_series); no decision left.
    # GEODNET locked_tokens: the manual row (3,000,000 GEOD, Blockworks chart read by Jake 2026-09-30)
    # is in manual_overrides.csv since 2026-10-01 — THE ACCEPTED SOURCE (Jake, 2026-10-02): the staking contract
    # was never found (METHODOLOGY_FLAGS geodnet_locked_tokens). No decision left.
    # Maple pool_release_tokens (Jake, 2026-10-01): no release programme runs, so N/A — not HELD.
    ("Maple", "pool_release_tokens"): (
        "N/A", "no release or emission programme runs: staking rewards were sunset by MIP-019 "
               "(maple-docs syrup-tokenomics/staking.md:3) and Drips ended after Q4 2025, last claims "
               "2026-02-18 (drips-rewards.md:7-20); the SSF's holding changes are buybacks and treasury "
               "movements, not a release (probe maple_ssf_lp_test records what moves them)"),
    # SETTLEMENT VOLUME (Jake, 2026-09-30): Artemis's daily series, one definition for every chain,
    # from CSV exports read at run time (config.ARTEMIS_SETTLEMENT). Ethereum's is exported; Jake is
    # checking whether Artemis carries NEAR and Hyperliquid. Plume is a closure (not on Artemis,
    # not covered by The Block) — config.UNAVAILABLE, with its evidence.
    # Jake 2026-09-30: Artemis carries neither. Hyperliquid is CLOSED (config.UNAVAILABLE — not
    # rebuildable). NEAR's rebuild waits on two things only Jake can decide on.
    # 2026-10-01 (Jake): BigQuery's public NEAR dataset is LIVE (MAX(block_date) 2026-10-01); Dune
    # (paid plan to save a query) and Flipside (API shut) are closed. Built: fetch/near_bigquery.py.
    # BUILT (Jake, 2026-10-05): NEAR's rows are arriving (187 days held, backfill in progress), so
    # the old "NEEDS JAKE — BUILDABLE" record is retired. It is MATURING until a full year is held
    # (FULL_YEAR_FROM below); NEEDS JAKE only while the store holds none of it (PENDING_SEED).
    ("Sky", "net_protocol_surplus_usd"): (
        "NEEDS JAKE", "September 2026 NPS: a manual monthly row in manual_overrides.csv when Sky "
                      "publishes it"),
}
# WAITING ON A SERIES THAT NEEDS n DAILY READINGS (Ethereum's yield: a week of d(Eth2Staking)).
# Ethereum staking_yield_pct's week-long wait on Eth2Staking was lifted 2026-09-30: the
# consensus part now has its declared history leg (ultrasound-derived issuance before the first
# d(Eth2Staking) row) and ETH.Store is backfilled per day (A1/A2).
# A MANUAL ROW'S REFRESH CADENCE, in days (a quarter or a month, plus two weeks' grace — the same
# grace the monthly staleness threshold carries). Jake, 2026-10-01.
MANUAL_REFRESH_DAYS = {"quarterly": 106, "monthly": 45, "weekly": 14}
WAIT_ON_SERIES: dict = {
    # Aethir's release is a day-on-day rise: its first row needs two days of the checker total.
    ("Aethir", "pool_release_tokens"): {
        "series": "checker_rewards_cumulative_tokens", "days": 2,
        "why": "supplier + staker rewards are the day-on-day rise of the dashboard's reward totals; the "
               "first release row lands with the second day's reading"},
}
# WAITING ON A NAMED DATE, recorded (Jake's run 2026-09-30 17:21): a pending seed is not a bug.
# While the cell is not yet ok and the date has not passed it reads WAITING ON A DATE; after the
# date it is classified on its own status again, so a seed that never lands still surfaces.
# A SERIES WHOSE FIRST YEAR COMES FROM A ONE-OFF SEED (Jake, 2026-10-01): while the store holds none
# of it, the cell is NEEDS JAKE — run the seed — not a BUG; once rows exist it is classified as usual.
# A TRAILING-YEAR SERIES (settlement volume and the NRR built on it) is MATURING until its input
# holds a full year — dated from the input's first stored day. Jake, 2026-10-05.
# The INPUT that is stored: NEAR's settlement volume and NRR are read-time views built from the
# P2P leg (near_bigquery p2p_transfer_volume_usd), so the first stored day is that leg's (Jake's
# run 10:49: keyed on the view itself, which the store never holds, it fell through to NEEDS JAKE).
FULL_YEAR_FROM = {("Near", "settlement_volume_usd"): "p2p_transfer_volume_usd",
                  ("Near", "network_reserve_ratio"): "p2p_transfer_volume_usd"}
PENDING_SEED = {
    **{("Near", m): "BigQuery's public NEAR dataset (fetch/near_bigquery.py, approved 2026-10-01) with "
                    "Jake's Application Default Credentials (`gcloud auth application-default login`, "
                    "RUNBOOK 11n), then `token_metrics.py --seed near_bigquery`"
       for m in ("settlement_volume_usd", "network_reserve_ratio")},
    # the browser route (Jake, 2026-10-02): rendered with the tabs clicked; its keys are pinned from the probe
    ("Aethir", "compute_hours_weekly"): "read from the demand page's server payload (`weeklyComputeHo*`, pinned by "
                                        "prefix — Jake's probes8); the Run Log's aethir_page line names the full key",
    **{("Plume", m): "python token_metrics.py --seed plume_settlement (Plume's P2P transfers from Blockscout, "
                     "~21,755 pages, ~1.5h; Artemis method, UNVALIDATED) — routine runs then top it up"
       for m in ("p2p_transfer_volume_usd", "settlement_volume_usd", "network_reserve_ratio",
                 "settlement_volume_365d_usd")},
    **{("Near", m): "NEAR's P2P leg from BigQuery (fetch/near_bigquery.py, approved): run `gcloud auth "
                    "application-default login` and `gcloud auth application-default set-quota-project "
                    "near-data-510309` (RUNBOOK 11n), then python token_metrics.py --seed near_bigquery (top-up "
                    "first, then month chunks from what the 900 GB/month budget leaves after reserving the month's "
                    "top-ups; Artemis method adapted to NEAR, "
                    "UNVALIDATED)"
       for m in ("p2p_transfer_volume_usd", "settlement_volume_usd", "network_reserve_ratio",
                 "settlement_volume_365d_usd")},
    ("Near", "circulating_supply_first_party"): "NEAR's own circulating_supply from BigQuery: run `gcloud auth "
                                                "application-default login` (RUNBOOK 11n); 10 MB a run, approved",
    ("Hyperliquid", "perps_volume_usd"): "python token_metrics.py --seed hl_candles (a year of daily candles, "
                                         "~1 call per perp market) — routine runs then add each day",
    **{("Hyperliquid", m): "python token_metrics.py --seed hl_candles — the throughput sum needs its perps leg"
       for m in ("trading_throughput_usd", "trading_throughput_365d_usd", "network_reserve_ratio_throughput")},
}
WAIT_UNTIL: dict = {
    # ("Ethereum", "staking_yield_pct") waited on beaconcha.in's seed until 2026-10-01, when the
    # source was dropped (config Ethereum.beaconchain_dropped): the metric no longer applies to
    # Ethereum — its validator yield is A1's calculation from d(Eth2Staking) + priority fees.
}


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
    until = WAIT_UNTIL.get((name, metric))
    if until and status not in ("ok", "review") and str(asof.date()) < until["until"]:
        return "WAITING ON A DATE", f"{until['until']} — {until['why']}"
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
    # A SOURCED MANUAL ROW IS COMPLETE (Jake's run 2026-10-01: GEODNET locked_tokens, the Blockworks
    # chart read by Jake, came out BUG only because its status reads "manual"). Complete while it is
    # inside its refresh cadence; past it, the action is Jake's — refresh the row. A manual row with no
    # source note is still a fault: nobody could check it.
    if status == "manual":
        cadence = "quarterly" if config.is_manual_quarterly(name, metric) else config.series_granularity(name, metric)
        limit = MANUAL_REFRESH_DAYS.get(cadence) or config.stale_after_days(name, metric, 7)
        latest = row.get("latest_date")
        if not note.strip() or latest is None or (isinstance(latest, float) and pd.isna(latest)):
            return "BUG", "a manual row without a source note or a date — nobody can check it"
        age = (asof - pd.Timestamp(latest)).days
        due = (pd.Timestamp(latest) + pd.Timedelta(days=limit)).date()
        if age > limit:
            return "NEEDS JAKE", (f"refresh the {cadence} manual row: the latest ({str(latest)[:10]}) is {age} days "
                                  f"old, past its {limit}-day cadence | {note[:160]}")
        return "COMPLETE (MANUAL)", (f"{note[:200]} (dated {str(latest)[:10]}, entered "
                                     f"{str(row.get('entered_on') or '')[:10]}; refreshed {cadence}, due by {due})")
    series = FULL_YEAR_FROM.get((name, metric))
    f0 = (firsts or {}).get((name, series)) if series else None
    if series and f0 is not None and not (isinstance(f0, float) and pd.isna(f0)):
        held = (asof - pd.Timestamp(f0)).days + 1
        if held < 365:
            return "MATURING", (f"{held} of 365 days of {series} held since {str(f0)[:10]} (backfill in "
                                f"progress); the full-year figure is complete when the backfill reaches "
                                f"{(asof - pd.Timedelta(days=364)).date()}, or forward-only on "
                                f"{(pd.Timestamp(f0) + pd.Timedelta(days=364)).date()}"
                                + (f" | now: {status} — {note[:120]}" if status not in ("ok", "review") else ""))
    if status in ("missing", "gap") and (name, metric) in PENDING_SEED:
        return "NEEDS JAKE", f"run the one-off seed: {PENDING_SEED[(name, metric)]}"
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
            # THE NEWEST MONTH NOT YET PUBLISHED IS NOT A MISSING HISTORY (Jake's run 2026-10-01:
            # Maple's revenue and buybacks read "62 of the Q0 months' days ... a backfill should fill
            # it" — the page's 31 months were held; September had not been published on 1 October).
            # History before the Q0 months and nothing after the latest stored month: waiting on the
            # source, not on a backfill.
            q0_start = (last - 2).start_time
            latest = row.get("latest_date")
            if (first and pd.Timestamp(first) < q0_start and latest is not None
                    and not (isinstance(latest, float) and pd.isna(latest))
                    and pd.Timestamp(latest) < last.end_time.normalize()):
                held_to = pd.Timestamp(latest).to_period("M")
                return "WAITING ON A DATE", (
                    f"{held_to + 1}..{last} not published yet — the series is held from {str(first)[:10]} "
                    f"to {str(latest)[:10]} ({int(cov)} of the Q0 months' days); the month lands when the "
                    f"source publishes it" + flag)
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
