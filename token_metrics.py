#!/usr/bin/env python3
"""
token_metrics.py — orchestrator.

    python token_metrics.py

One command, no arguments. Runs every source tier in order, updates the local store
(metrics.db), loads manual_overrides.csv last, and rebuilds token_metrics.xlsx from scratch.

FOUR FLAGS, ALL NARROWING, NONE CHANGING WHAT A FIGURE MEANS:
    --no-fetch            rebuild the workbook from the store and fetch nothing. Every display
                          fix, every label, every confidence rule is read-time, so this is the
                          loop for working on any of them — and it is the ONLY way to rebuild
                          without spending a day's politeness budget on the free endpoints.
    --project NAME        fetch one project. Repeatable. Names are exact and a wrong one is
                          refused with the list, rather than silently fetching nothing.
    --portfolio           fetch only the projects named in portfolio.txt. This is the DEFAULT
                          when that file exists; --all restores the full 30.
    --all                 every project, whatever portfolio.txt says.

A NARROWED RUN STILL BUILDS THE WHOLE WORKBOOK. The store holds every project's history and the
workbook is built from the store, so the projects that were not fetched render from what they
already have and go stale in the ordinary way. Nothing is blanked for having been skipped, and
nothing is reported as fresh that is not.

    free API  ->  contract read  ->  render the public page  ->  paid API

First run  backfills the entire available history from every source, so the 3/6/9-month
           trajectory columns are populated on run one rather than accumulating from install.
Later runs extend the series and re-fetch a trailing 30-day window to catch source revisions.
           Tier 4 (Dune) is skipped for any series the store already has history for.

** THE 30-DAY WINDOW NARROWS THE REQUEST ON EXACTLY ONE SOURCE. ** Checked, 2026-09-22, because
"incremental" reads like a network saving and is mostly not one:
    coingecko   days=30 goes into the request. Real, and it saves the transfer.
    defillama   the full daily history is downloaded and trimmed to 30 days locally —
                /summary/fees/{slug} has no date parameter to pass. Saves storage and
                validation work, and no network time.
    dune        the query executes in full whatever the window says; a Dune query has no
                incremental mode. A metric being fetched for the first time ignores the window
                deliberately, so the backfill is never truncated.
    chain / hypercore / tron / scrape
                accept the window and ignore it, correctly: they read a current value, not a
                series. There is no window to apply to one number.
This is recorded rather than "fixed" — trimming after the fact is right for a source with no
date parameter, the alternative being to store a year of history every day. It matters because
the way to make a run faster is --portfolio or --project, not a shorter window.

A failed source never kills the run: it is logged to the Run Log, the last known value is
carried forward and marked stale, and anything unresolved lands in the Gap Report.

Useful environment flags (all optional, all in .env):
    DUNE_API_KEY                     tier 4 backfill
    COINGECKO_API_KEY                raises the CoinGecko free-tier rate limit
    RPC_ETHEREUM / RPC_BSC / ...     comma-separated override of the public RPC fallback list
    TOKEN_METRICS_ALLOW_UNVERIFIED=1 read contract addresses not yet verified against protocol docs
    TOKEN_METRICS_DUNE_ALWAYS=1      re-pull Dune even where the store already has history
    TOKEN_METRICS_SOURCES=path       use a different sources.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

import config
import fetch
import store as store_mod
from build_workbook import build_workbook

ROOT = Path(__file__).resolve().parent
OVERRIDES_CSV = ROOT / "manual_overrides.csv"
PORTFOLIO_TXT = ROOT / config.PORTFOLIO_FILE
WORKBOOK = ROOT / "token_metrics.xlsx"
REFETCH_WINDOW_DAYS = 30
SEED_WINDOW_DAYS = 365     # --seed nearblocks reads a year


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Fetch supply/demand metrics and rebuild token_metrics.xlsx.",
        epilog="With no flags: fetch every project (or, if portfolio.txt exists, the projects "
               "named in it) and rebuild the workbook.")
    ap.add_argument("--no-fetch", action="store_true",
                    help="rebuild the workbook from the store without fetching anything")
    ap.add_argument("--project", action="append", default=[], metavar="NAME",
                    help="fetch only this project; repeatable. Exact name, as in config.")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--portfolio", action="store_true",
                       help=f"fetch only the projects named in {config.PORTFOLIO_FILE} "
                            f"(the default when that file exists)")
    scope.add_argument("--all", action="store_true",
                       help="fetch every project, whatever portfolio.txt says")
    ap.add_argument("--seed", choices=["nearblocks", "geodnet", "plume_staking", "hl_candles", "plume_settlement", "mev_relays",
                             "near_bigquery", "chainlink_fees", "pendle_gauges", "aero_epochs"],
                    help="one-off: run only this source with NO time budget, to finish a first "
                         "read that routine runs (60s) take many runs to complete. Stores what it "
                         "reads; records no gaps and does not rebuild the workbook. plume_settlement: a "
                         "year of Plume P2P transfers from Blockscout (~21,755 pages, ~1.5h). hl_candles: a year "
                         "of daily candles for every Hyperliquid perp market (~1 call each, paced). plume_staking: the live diamond read at the first block of each past day "
                         "(locked_tokens, gross/net APR, commission); stops where rpc.plume.org "
                         "serves no historical state. near_bigquery: NEAR's daily top-up, then P2P month "
                         "chunks from BigQuery until only the reserve for the rest of the month's "
                         "top-ups is left of the 900 GB budget. chainlink_fees: a year of Chainlink's "
                         "fee-line event logs (CCIP 1.2/1.5 and 2.0 OnRamps, VRF v2.5, Automation v2.3) on "
                         "Ethereum, Arbitrum, Polygon, Base and OP; routine runs skip it until it is complete. pendle_gauges: "
                         "Pendle's gauge-payout log scans on mainnet, Arbitrum and Optimism, to the head. aero_epochs: every Q0 "
                         "Aerodrome voter epoch (fees, bribes, totalWeight at each epoch start).")
    ap.add_argument("--seed-days", type=int, default=None,
                    help="--seed mev_relays only: seed this many days back instead of the configured year "
                         "(e.g. 90 first; a later full seed resumes from the days already held)")
    return ap.parse_args(argv)


def seed_nearblocks(st, log) -> int:
    """Finish NearBlocks' first buyback read in one sitting. Added 2026-09-28.

    Routine runs give NearBlocks 60s, and the read resumes across runs (fetch/nearblocks.py
    `pending`), so it always finishes eventually; this finishes it now. No budget and no other
    source, with the heartbeat on. Ctrl-C is safe: every page is saved as it is read. The state
    file is printed before and after, so the cursor visibly advancing is the evidence.

    Deliberately records NO gaps and NO review items: a one-source run would otherwise replace
    the Gap Report with 'everything else is missing'.
    """
    from fetch import Heartbeat
    from fetch.logcache import LogCache
    from fetch.nearblocks import NearBlocks
    from fetch.validate import validate_frame

    near = [p for p in config.PROJECTS if p.get("near_account_flows") or p.get("nearblocks")]

    def state_line():
        lines = []
        for p in near:
            for flow in p.get("near_account_flows") or []:
                f = LogCache().root / f"nearblocks-{flow['account']}.json"
                try:
                    stt = json.loads(f.read_text())
                except (OSError, ValueError):
                    lines.append(f"{flow['account']}: no state yet ({f})")
                    continue
                pend = stt.get("pending") or {}
                lines.append(
                    f"{flow['account']}: " + (
                        f"PARTIAL read — cursor {pend.get('cursor')}, {pend.get('rows', 0)} txn(s) "
                        f"read so far" if pend else
                        f"complete — {len(stt.get('by_day') or {})} day(s) kept, newest txn "
                        f"{stt.get('newest_ts')}") + f" ({f})")
        return "; ".join(lines) or "no NEAR account flows configured"

    run_id = fetch.new_run_id()
    log.info("--seed nearblocks: BEFORE — %s", state_line())
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        # A YEAR, NOT THE ROUTINE 30 DAYS (2026-09-28): the first seed kept 28 days. No page cap.
        NearBlocks(last_dates=st.last_dates(), page_cap=10_000).run(near, SEED_WINDOW_DAYS, out)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
    log.info("--seed nearblocks: AFTER (%.0fs, %d row(s) stored) — %s",
             time.monotonic() - t0, written, state_line())
    return 0


def seed_geodnet(st, log) -> int:
    """Fill GEODNET's mining-wallet release (balance_flows) in one sitting. 2026-09-28 (B1).

    The release is external inflow minus the change in the two wallets' daily balances: one
    boundary block and one archive balanceOf per wallet per day, for a year, plus the few inflow
    events. A routine run fills what its 60s allow, newest day first; this runs with no budget
    and the heartbeat on. Ctrl-C loses nothing: the state is saved after every day
    (<TOKEN_METRICS_LOGCACHE>/balance-flow-<key>.json).

    Records NO gaps and NO review items, for the same reason as --seed nearblocks. Days held
    before and after are printed.
    """
    from fetch import Heartbeat
    from fetch.balance_flow import BalanceFlow
    from fetch.logcache import LogCache
    from fetch.validate import validate_frame

    geod = [p for p in config.PROJECTS if p["name"] == "GEODNET" and p.get("balance_flows")]

    def state_line():
        lines, root = [], LogCache().root
        for p in geod:
            for spec in p["balance_flows"]:
                try:
                    stt = json.loads((root / f"balance-flow-{spec['key']}.json").read_text())
                except (OSError, ValueError):
                    stt = {}
                bal = sorted((stt.get("balance") or {}))
                lines.append(f"{spec['key']}: " + (f"{len(bal)} daily balance(s), {bal[0]}..{bal[-1]}"
                                                   if bal else "no state yet"))
        return "; ".join(lines) or "GEODNET has no balance_flows configured"

    run_id = fetch.new_run_id()
    log.info("--seed geodnet: BEFORE — %s", state_line())
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        BalanceFlow(unbounded=True).run(geod, None, out)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed geodnet: %s %s — %s", e.status, e.project, e.message)
    log.info("--seed geodnet: AFTER (%.0fs, %d row(s) stored) — %s",
             time.monotonic() - t0, written, state_line())
    return 0


def seed_near_bigquery(st, log) -> int:
    """NEAR from BigQuery with no chunk limit (Jake, 2026-10-01): the top-up, then every month chunk the
    budget allows AFTER reserving the rest of the month's top-ups, newest first — only reads config
    Near.near_bigquery.approved allows. Records no gaps."""
    from fetch.near_bigquery import NearBigQuery
    from fetch.validate import validate_frame
    pl = [p for p in config.PROJECTS if p.get("near_bigquery")]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    NearBigQuery(stored_long=st.load_long()).run(pl, None, out, unbounded=True)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed near_bigquery: %s — %s", e.status, e.message)
    log.info("--seed near_bigquery: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_plume_settlement(st, log) -> int:
    """Plume's settlement-volume rebuild, the first year in one sitting (Jake, 2026-10-01): every
    ERC-20 transfer page back a year and each day's native transfers, no time budget, progress
    cached (an interrupted seed resumes). Records no gaps."""
    from fetch import Heartbeat
    from fetch.plume_settlement import PlumeSettlement
    from fetch.validate import validate_frame
    pl = [p for p in config.PROJECTS if (p.get("settlement_rebuild") or {}).get("engine", "blockscout") == "blockscout"]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        PlumeSettlement(max_seconds=None).run(pl, None, out, unbounded=True)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed plume_settlement: %s — %s", e.status, e.message)
    log.info("--seed plume_settlement: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_mev_relays(st, log, days: int | None = None) -> int:
    """Ethereum's MEV year in one sitting (Jake, 2026-10-02): every covered relay's delivered payloads
    back a year (~16k pages, 1/s per relay, relays side by side) and the non-relay blocks' priority fees
    on ETHEREUM_RPC_URL; per-day aggregates checkpointed, so an interrupted seed resumes. `days` (--seed-days)
    seeds fewer days first (Jake, probes6: offer 90)."""
    from fetch import Heartbeat
    from fetch.mev_relays import MevRelays
    from fetch.validate import validate_frame
    pl = [p for p in config.PROJECTS if p.get("mev_relays")]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        MevRelays(max_seconds=None, days=days).run(pl, None, out, unbounded=True)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed mev_relays: %s — %s", e.status, e.message)
    log.info("--seed mev_relays: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_chainlink_fees(st, log) -> int:
    """Chainlink's fee lines that bypass the aggregator, the first year in one sitting (Jake,
    2026-10-05): every stream (Router OnRampSet history, each OnRamp, the VRF v2.5 coordinator, the
    Automation v2.3 registry, per chain) read to the head with NO time budget, in passes of
    SEED_PASS_S with the cache saved after each, so an interrupted seed resumes. Routine runs skip
    the source until this completes. Records no gaps."""
    from fetch import Heartbeat
    from fetch.chainlink_fees import ChainlinkFees
    from fetch.validate import validate_frame
    pl = [p for p in config.PROJECTS if p.get("chainlink_fee_lines")]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        ChainlinkFees().run(pl, None, out, unbounded=True)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed chainlink_fees: %s — %s", e.status, e.message)
    log.info("--seed chainlink_fees: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_aero_epochs(st, log) -> int:
    """Aerodrome's voter epochs, every Q0 epoch in one sitting (Jake's run 2026-10-09 ~14:37: fourteen epochs in one
    read outran the aero_voter tier's 180s budget and stored none). fetch/aero_voter.py with no per-run cap and no
    tier budget: fees and bribes per epoch, and Voter.totalWeight at each epoch's start block (archive). Records no
    gaps."""
    from fetch import Heartbeat
    from fetch.aero_voter import AeroVoter
    from fetch.validate import validate_frame
    pl = [p for p in config.PROJECTS if p.get("voter_epochs")]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        AeroVoter(max_backfill=0).run(pl, None, out)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed aero_epochs: %s — %s", e.status, e.message)
    log.info("--seed aero_epochs: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_pendle_gauges(st, log) -> int:
    """Pendle's gauge-payout scans, every chain, in one sitting (Jake's run 2026-10-09 ~14:10: the Arbitrum and
    Optimism scans seed from block 0 at 140s a run, store nothing until their whole history reconciles, and the
    emissions sum is refused until they have). Runs the explorer_gauge and explorer_gauge_l2 tiers' scans with NO
    time budget; each resumes from its cache. Stores what reconciles (and the once-a-day direct counts); records
    no gaps."""
    from fetch import Heartbeat
    from fetch.logscan import LogScan
    from fetch.validate import validate_frame
    pl = [p for p in config.PROJECTS if p["name"] == "Pendle"]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        for tier in ("explorer_gauge", "explorer_gauge_l2"):
            LogScan(own_tier=tier, unbounded=True).run(pl, None, out)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed pendle_gauges: %s — %s", e.status, e.message)
    log.info("--seed pendle_gauges: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_hl_candles(st, log) -> int:
    """Hyperliquid's perps volume, the first year in one sitting (Jake, 2026-10-01): one 1d
    candleSnapshot per perp market (main dex + HIP-3), paced to the published weight limit, with
    no time budget. Progress is cached per market, so an interrupted seed resumes. Records no gaps."""
    from fetch import Heartbeat
    from fetch.hl_candles import HLCandles
    from fetch.validate import validate_frame
    hl = [p for p in config.PROJECTS if p.get("hl_candles")]
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    with Heartbeat():
        HLCandles(max_seconds=None).run(hl, None, out, unbounded=True)
    prior = st.latest_values()
    frames = [validate_frame(f, prior, out) for f in out.frames]
    written = sum(st.upsert(f) for f in frames if f is not None and not f.empty)
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        log.info("--seed hl_candles: %s — %s", e.status, e.message)
    log.info("--seed hl_candles: AFTER (%.0fs) — %d row(s) stored", time.monotonic() - t0, written)
    return 0


def seed_plume_staking(st, log) -> int:
    """Plume's staking history from the LIVE diamond (Jake's run 2026-09-30 17:21: locked_tokens and
    staking_yield_pct held 1 day each, flagged NOT FORWARD-ONLY). One read of the diamond at the
    first block of each past day, a year back, newest first; days already held are skipped. It
    STOPS at the first day rpc.plume.org will not serve state for — the answer to whether Plume's
    RPC serves history, printed — or at the diamond's deployment (recorded as the series' start).
    Records NO gaps and NO review items."""
    from fetch import Heartbeat
    from fetch.archive import record_series_start
    from fetch.plume_staking import PlumeStaking
    from fetch.validate import validate_frame
    import pandas as pd
    plume = [p for p in config.PROJECTS if p.get("plume_staking")]
    spec = plume[0]["plume_staking"] if plume else {}
    long = st.load_long()
    held = long[(long["project"] == "Plume") & (long["metric"] == spec.get("stake_metric"))] if not long.empty else long
    have = set(pd.to_datetime(held["date"]).dt.normalize()) if not held.empty else set()
    log.info("--seed plume_staking: BEFORE — %d day(s) of %s held", len(have), spec.get("stake_metric"))
    run_id = fetch.new_run_id()
    out = fetch.FetchOutput()
    t0 = time.monotonic()
    prior = st.latest_values()
    written = 0

    def flush():
        # CHECKPOINT (Jake, 2026-10-01): the days read so far go to the store at every progress
        # line, so an interrupted seed keeps them and resumes past them (`have`).
        nonlocal written
        frames = [validate_frame(f, prior, out) for f in out.frames]
        out.frames.clear()
        written += sum(st.upsert(f) for f in frames if f is not None and not f.empty)

    with Heartbeat():
        res = PlumeStaking().seed(plume, SEED_WINDOW_DAYS, out, have=have, flush=flush)
    flush()
    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
        if e.status != "ok":
            log.info("--seed plume_staking: %s — %s", e.status, e.message)
    if res["mechanism_start"]:
        for m in ("stake_metric", "apr_metric", "commission_metric", "net_apr_metric"):
            record_series_start("Plume", spec[m], res["mechanism_start"],
                                f"the live staking diamond {spec['address']} has no code before "
                                f"{res['mechanism_start']} (its deployment)")
    log.info("--seed plume_staking: AFTER (%.0fs) — %d day(s) read, %d row(s) stored; %s",
             time.monotonic() - t0, res["stored_days"], written,
             f"STOPPED at {res['stopped']} — earlier days are FORWARD-ONLY on this RPC" if res["stopped"]
             else f"series starts {res['mechanism_start']} (deployment)" if res["mechanism_start"]
             else f"the whole {SEED_WINDOW_DAYS} days were served")
    return 0


def resolve_scope(args, log) -> list[dict]:
    """Which projects this run fetches. REFUSES on a name it does not recognise.

    A mistyped --project that silently fetched nothing would look identical to a run where every
    source had nothing to add, and the same is true of a typo in portfolio.txt — which is worse,
    because it persists. Both are reported with the list of valid names instead.
    """
    if args.project:
        unknown = [n for n in args.project if n not in config.PROJECT_BY_NAME]
        if unknown:
            raise SystemExit(
                f"unknown project name(s): {', '.join(unknown)}\n"
                f"Names are exact. Valid: {', '.join(p['name'] for p in config.PROJECTS)}")
        picked = [config.PROJECT_BY_NAME[n] for n in args.project]
        log.info("scope: %d project(s) named on the command line — %s",
                 len(picked), ", ".join(p["name"] for p in picked))
        return picked

    names, unknown = config.read_portfolio(PORTFOLIO_TXT)
    if unknown:
        # NOT FATAL, AND NOT IGNORED. A typo here would park a held asset, and a series that
        # stops collecting cannot be backfilled — CoinGecko serves total_supply as a current
        # value only. So the run widens rather than narrows, and says exactly what it could not
        # match.
        log.error("%s: %d name(s) not recognised and IGNORED — %s. Fetching every project this "
                  "run rather than risk parking a held asset on a typo. Valid names: %s",
                  PORTFOLIO_TXT.name, len(unknown), ", ".join(unknown),
                  ", ".join(p["name"] for p in config.PROJECTS))
        return list(config.PROJECTS)
    if args.all:
        log.info("scope: --all — every project (%d)", len(config.PROJECTS))
        return list(config.PROJECTS)
    if names:
        log.info("scope: %s — %d of %d projects (%s). Use --all for the rest.",
                 PORTFOLIO_TXT.name, len(names), len(config.PROJECTS), ", ".join(names))
        return [config.PROJECT_BY_NAME[n] for n in names]
    if args.portfolio:
        raise SystemExit(
            f"--portfolio needs {PORTFOLIO_TXT.name}, which does not exist (or is empty).\n"
            f"Create it with one project name per line — the held ones — and the default run "
            f"will use it. Names are exact:\n  "
            + "\n  ".join(p["name"] for p in config.PROJECTS))
    log.info("scope: every project (%d) — no %s on file",
             len(config.PROJECTS), PORTFOLIO_TXT.name)
    return list(config.PROJECTS)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("token_metrics")
    load_dotenv(ROOT / ".env")
    # NO KEY IN ANY LOG LINE OR PRINT (2026-09-29): after .env, so its values are known secrets.
    from fetch.base import install_redaction
    install_redaction()

    st = store_mod.Store(store_mod.DB_PATH)

    if args.seed:
        rc = {"nearblocks": seed_nearblocks, "geodnet": seed_geodnet,
              "plume_staking": seed_plume_staking, "hl_candles": seed_hl_candles,
              "plume_settlement": seed_plume_settlement, "near_bigquery": seed_near_bigquery,
              "chainlink_fees": seed_chainlink_fees, "pendle_gauges": seed_pendle_gauges,
              "aero_epochs": seed_aero_epochs,
              "mev_relays": lambda st_, log_: seed_mev_relays(st_, log_, args.seed_days)}[args.seed](st, log)
        st.close()
        return rc

    # ===== BUILD ONLY. =====
    # Every display rule in this project is READ-TIME — the confidence bands, the withheld
    # mechanisms, the labels, the window arithmetic — so all of them are worked on against the
    # store as it stands. Refetching to see a label change is a day's politeness budget spent on
    # free endpoints for nothing, and the standing rule is one run a day.
    if args.no_fetch:
        # THE SAME SCOPE AS A FETCHING RUN. --no-fetch is how every read-time change is checked,
        # so a build that quietly widened to 30 projects would show a workbook nobody is going to
        # get from a real run.
        scope = resolve_scope(args, log)
        log.info("--no-fetch: rebuilding %s from the store, fetching nothing (%d project(s))",
                 WORKBOOK.name, len(scope))
        build_workbook(st, WORKBOOK, only=[p["name"] for p in scope])
        log.info("wrote %s", WORKBOOK)
        st.close()
        return 0

    projects = resolve_scope(args, log)
    run_id = fetch.new_run_id()
    first_run = st.is_empty()
    window = None if first_run else REFETCH_WINDOW_DAYS
    log.info("run %s — %s", run_id, "FIRST RUN: full backfill" if first_run else f"incremental, trailing {window}d re-fetch")

    # Manual overrides load LAST into the store, but the Gap Report needs to know what they
    # cover before the gap detector runs, so read the file's keys up front.
    try:
        n_manual = st.load_overrides_csv(OVERRIDES_CSV)
        manual_keys = set(
            st.conn.execute("SELECT DISTINCT project, metric FROM manual_overrides").fetchall()
        )
        st.record_fetch(run_id, "manual", None, n_manual, "ok", f"{OVERRIDES_CSV.name}: {n_manual} overrides loaded")
    except Exception as e:  # noqa: BLE001
        n_manual, manual_keys = 0, set()
        st.record_fetch(run_id, "manual", None, 0, "failed", f"{OVERRIDES_CSV.name}: {e}")
        log.error("manual overrides failed: %s", e)

    # The change-threshold check compares against the newest figure of any date.
    prior_values = st.latest_values()
    # DIFFERENCING is different: its inputs must come from an EARLIER DAY. Two runs on one day
    # write to the same (date, project, metric) key, so a same-day prior makes the second run
    # overwrite the first's correct flow with a dust delta — which is exactly what happened to
    # PancakeSwap's 59,857,159.01 burn. Anchoring on the last earlier-dated row makes a same-day
    # re-run recompute the identical answer instead of destroying it.
    today_key = fetch.today().date().isoformat()
    before = st.values_before(today_key)
    prior_values_for_delta = {k: v[0] for k, v in before.items()}
    prior_dates = {k: v[1] for k, v in before.items()}
    prior_sources = {k: v[2] for k, v in before.items()}
    has_history = {k for k, _ in prior_values.items()}
    # ENDPOINTS THE STORE HAS WATCHED 404 WITHOUT EVER WORKING. Not called this run, retried
    # automatically once 14 days pass without an attempt. Derived from fetch_status rather than
    # declared in config: a hand-maintained list of absent resources goes stale against reality
    # and nothing reconciles it back.
    absent = st.known_absent() | st.declared_absent(projects)
    if absent:
        log.info("%d source/project pair(s) are KNOWN ABSENT and will not be called: %s",
                 len(absent), ", ".join(f"{s}/{p}" for s, p in sorted(absent)))

    # ===== HISTORY SHORTER THAN THE SOURCE'S: RE-READ A YEAR. 2026-09-28. =====
    backfill = set()
    if not first_run:
        from fetch import backfill as bf
        backfill, why = bf.plan(projects, st.first_dates())
        if why:
            log.info("backfill: %d series shorter than %d days are re-read over the full year "
                     "this run — %s", len(why), fetch.base.BACKFILL_DAYS, "; ".join(why))

    started = time.monotonic()
    out = fetch.fetch_all(
        projects, window,
        backfill=backfill,
        prior_values=prior_values,
        prior_values_for_delta=prior_values_for_delta,
        prior_dates=prior_dates,
        prior_sources=prior_sources,
        has_history=has_history,
        last_dates=st.last_dates(),
        known_absent=absent,
        manual_keys=manual_keys,
        # THE STORE'S OWN HISTORY, for the level-break check. Every other check works on what
        # just arrived; a level break is in the shape of the last month, which is why none of
        # them could see Morpho's source change after the day it happened.
        stored_long=st.load_long(),
    )

    written = st.upsert(out.frame())
    log.info("upserted %d rows", written)
    if backfill:
        frame = out.frame()
        answered = (set(map(tuple, frame[["project", "metric"]].drop_duplicates().values))
                    if not frame.empty else set())
        bf.record(backfill, st.first_dates(), answered)

    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
    n_review = st.record_review(run_id, out.review)
    n_gaps = st.record_gaps(run_id, out.gaps)
    n_staged = st.record_staging(run_id, out.staged)

    # ===== WHERE THE TIME WENT, PER TIER. =====
    # Not a profile and not trying to be: it answers the one question that decides what to do
    # about a slow run — which TIER is slow. A tier-2 run is contract reads over public RPC and a
    # tier-4 run is Dune; they have nothing in common and the remedy for one does nothing for the
    # other. The row count beside the time is what separates "slow" from "doing a lot".
    elapsed = time.monotonic() - started
    # ===== ACTION FIRST (Jake's run 2026-10-06 11:01): what needs a human goes at the TOP of the summary.
    for line in run_banner(out.log):
        log.warning(line)
    rows_by_source, fails_by_source, skips_by_source = {}, {}, {}
    for e in out.log:
        rows_by_source[e.source] = rows_by_source.get(e.source, 0) + int(e.rows or 0)
        fails_by_source[e.source] = fails_by_source.get(e.source, 0) + int(e.status == "failed")
        skips_by_source[e.source] = skips_by_source.get(e.source, 0) + int(e.status == "skipped")
    log.info("fetch took %.1fs across %d project(s):", elapsed, len(projects))
    for t in sorted(out.timings, key=lambda x: -x["seconds"]):
        src = t["source"]
        log.info("    tier %-3s %-16s %7.1fs  %6d rows  %d failed  %d skipped",
                 t["tier"], src, t["seconds"], t.get("rows", rows_by_source.get(src, 0)),
                 t.get("failed", fails_by_source.get(src, 0)), t.get("skipped", skips_by_source.get(src, 0)))
    # SLOWEST FIRST, and the slowest line is the answer. A source taking minutes and returning
    # nothing is the most useful row in a slow run's log, and a rows-only summary cannot show it.

    failures = [e for e in out.log if e.status == "failed"]
    skips = [e for e in out.log if e.status == "skipped"]
    log.info("run summary: %d rows | %d fetch failures | %d SKIPPED (ran nothing) | %d review items "
             "| %d gaps | %d manual overrides | %d staged (captured, used by nothing)",
             written, len(failures), len(skips), n_review, n_gaps, n_manual, n_staged)
    if failures:
        log.warning("%d fetch failures — see the Run Log tab", len(failures))
    # ACTION NEEDED (probes6 4, Jake 2026-10-02): an expired Google login stops every NEAR BigQuery read;
    # say what to run at the end of the run, not only in the Run Log.
    for e in out.log:
        if e.source == "near_bigquery" and ("gcloud auth application-default login" in e.message
                                            or e.message.startswith("REMINDER:")):
            log.warning("ACTION NEEDED — %s: %s", e.project, e.message)
    # A skip is not a success. It produces no error, no failure and no gap, so without this it
    # reads exactly like a source that ran and had nothing to add — which is how a backfill that
    # never executed gets reported as one that worked.
    if skips:
        log.warning("%d source/metric pair(s) were SKIPPED and fetched nothing:", len(skips))
        for e in skips:
            log.warning("    SKIPPED  tier %s  %s / %s — %s", e.tier, e.source, e.project, e.message)
    if n_gaps:
        log.warning("%d unresolved metrics — see the Gap Report tab, it is the to-do list", n_gaps)

    # THE WORKBOOK IS DRAWN AT THE SCOPE THAT WAS FETCHED. Rendering a project this run never
    # touched puts a frozen cell next to a fresh one with nothing to tell them apart.
    build_workbook(st, WORKBOOK, run_id=run_id, only=[p["name"] for p in projects])
    log.info("wrote %s — %d project(s) rendered%s", WORKBOOK, len(projects),
             "" if len(projects) == len(config.PROJECTS)
             else f" of {len(config.PROJECTS)}; the rest keep their stored history and are "
                  f"redrawn by --all")
    st.close()
    return 0


NETWORK_SIGNS = ("timed out", "timeout", "connection", "could not connect", "couldn't connect", "refused",
                 "reset by peer", "temporarily unavailable", "max retries", "nameresolution", "name resolution",
                 "failed to resolve", "502", "503", "504", "remote end closed", "remotedisconnected")
NETWORK_WIDE_MIN_SOURCES = 4


def run_banner(log_entries) -> list[str]:
    """The lines that need Jake BEFORE anything else in the run summary (2026-10-06):

    1. NEAR's Google login expired — his org's policy forces re-authentication about daily — with the two
       gcloud lines to paste.
    2. FAILURES THAT LOOK NETWORK-WIDE: when sources on at least NETWORK_WIDE_MIN_SOURCES different
       adapters timed out or could not connect in one run, it is said plainly — likely the connection, so
       rerun — rather than left as N unrelated-looking failures. A tier that times out keeps what it had
       already produced, and the store is only ever upserted, so earlier values stand either way."""
    out = []
    reauth = [e for e in log_entries if e.source == "near_bigquery" and e.status == "failed"
              and "re-authentication" in e.message.lower()]
    if reauth:
        proj = (config.PROJECT_BY_NAME.get("Near") or {}).get("near_bigquery", {}).get("project", "near-data-510309")
        out += ["=" * 78,
                "ACTION NEEDED — NEAR BigQuery: Google login expired (your org forces re-auth about daily). Paste:",
                "    gcloud auth application-default login",
                f"    gcloud auth application-default set-quota-project {proj}",
                "  then rerun. Every NEAR BigQuery read was skipped this run; stored values stand."]
    net = {}
    for e in log_entries:
        if e.status != "failed":
            continue
        m = e.message.lower()
        if "tier timed out" in m or any(k in m for k in NETWORK_SIGNS):
            net.setdefault(e.source, e.message)
    if len(net) >= NETWORK_WIDE_MIN_SOURCES:
        out += ["=" * 78,
                f"NETWORK-WIDE TROUBLE — {len(net)} sources timed out or could not connect this run: "
                f"{', '.join(sorted(net))}.",
                "  That pattern is usually the connection, not the sources: RERUN when the network is steady. "
                "Stored values stand — a timed-out tier keeps what it read and nothing is deleted."]
    if out:
        out.append("=" * 78)
    return out


if __name__ == "__main__":
    sys.exit(main())
