#!/usr/bin/env python3
"""archive_backfill.py — a year of daily history for every state-based series. 2026-09-29 (Jake).

Plain mode is a PLAN and reads nothing from the network: every state-based series in scope, how
many of the last 365 days it is missing, and every series that cannot be backfilled with the
reason. --run reads, within a time budget, newest days first, and re-running continues.

    python archive_backfill.py                     the plan
    python archive_backfill.py --run               read (20 min budget), then report coverage
    python archive_backfill.py --run --budget-min 60 --project Uniswap
    python archive_backfill.py --run --near        NEAR's header supply and stake too
    python archive_backfill.py --run --no-solana   skip GEODNET's Solana burn-account history

WHAT IT WRITES: only (date, project, metric) keys that hold nothing — never an overwrite — with
source strings identical to the live reads plus the `archive` marker, then the flows of
cumulative stocks (burn deltas) for the new dates. Rows whose stored neighbours were read from a
different measuring point are refused and listed. See fetch/archive.py.
"""
from __future__ import annotations

import argparse
import sys
import time

import pandas as pd

import config
from fetch import archive as ar
from fetch.base import today


def scoped(names: list[str]) -> list[dict]:
    if names:
        return [config.PROJECT_BY_NAME[n] for n in names]
    port, _ = config.read_portfolio(config.PORTFOLIO_FILE)
    return [config.PROJECT_BY_NAME[n] for n in port] if port else list(config.PROJECTS)


def solana_providers(projects: list[dict]) -> dict:
    """(project, key) -> SolanaAccountHistory for every SPL TOKEN ACCOUNT read (GEODNET's burn)."""
    out = {}
    for p in projects:
        for key, spec in (p.get("contracts") or {}).items():
            if spec["kind"] == "spl_token_account":
                out[(p["name"], key)] = ar.SolanaAccountHistory(account=spec["address"],
                                                                decimals=int(spec.get("decimals", 9)))
    return out


def coverage(store, projects: list[dict], metrics_by_project: dict) -> dict:
    since = str((today() - pd.Timedelta(days=ar.ARCHIVE_DAYS)).date())
    out = {}
    for p in projects:
        for m in sorted(metrics_by_project.get(p["name"], ())):
            n = store.conn.execute("SELECT COUNT(DISTINCT date) FROM metrics WHERE project=? AND "
                                   "metric=? AND date>=?", (p["name"], m, since)).fetchone()[0]
            out[(p["name"], m)] = n
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--project", action="append", default=[])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--budget-min", type=float, default=ar.DEFAULT_BUDGET_S / 60)
    ap.add_argument("--near", action="store_true", help="also NEAR's archival header supply and stake")
    ap.add_argument("--no-solana", action="store_true")
    a = ap.parse_args(argv)
    import store as store_mod
    st = store_mod.Store(a.db)
    projects = scoped(a.project)
    sol = {} if a.no_solana else solana_providers(projects)
    chains_all = {spec["chain"] for p in projects for m in ar.state_metrics(p)
                  for _, spec in ar.serving_contracts(p, m)} - {"solana"}
    bf = ar.ArchiveBackfill(st, projects, budget_s=a.budget_min * 60, solana=sol)

    if not a.run:
        ok, no = bf.targets(chains_all)          # plan assumes every chain has an archive endpoint
        cov = coverage(st, projects, ok)
        print(f"PLAN — {sum(len(v) for v in ok.values())} backfillable series (if each chain's "
              f"endpoint serves archive state; --run checks), {len(no)} not:\n")
        for (n, m), days in sorted(cov.items()):
            print(f"  {n:<13} {m:<32} {days:>4} of {ar.ARCHIVE_DAYS} days stored")
        print("\nNOT BACKFILLABLE:")
        for n, m, why in no:
            print(f"  {n:<13} {m:<32} {why}")
        return 0

    # WHICH CHAINS SERVE STATE A YEAR BACK: one eth_getBalance at that day's first block.
    archive_chains, day = set(), today() - pd.Timedelta(days=ar.ARCHIVE_DAYS)
    for c in sorted(chains_all):
        try:
            w3 = bf.reader.web3(c)
            blk = ar.DayBlocks(w3, c).at(day, int(w3.eth.block_number))
            good, why = ar.archive_ok(w3, blk)
        except Exception as e:  # noqa: BLE001
            good, why = False, f"{type(e).__name__}: {str(e)[:160]}"
        print(f"  archive {c:<9} {'YES' if good else 'NO '} — {why}")
        if good:
            archive_chains.add(c)
    ok, _ = bf.targets(archive_chains)
    before = coverage(st, projects, ok)
    t0 = time.monotonic()
    res = bf.run(archive_chains)
    if a.near:
        near = config.PROJECT_BY_NAME.get("Near")
        if near and near in projects:
            na, rows, since = ar.NearArchive(), [], today() - pd.Timedelta(days=ar.ARCHIVE_DAYS)
            deadline = t0 + a.budget_min * 60
            for i in range(1, ar.ARCHIVE_DAYS + 1):
                if time.monotonic() > deadline:
                    print("  near: budget spent; re-run to continue")
                    break
                d = today() - pd.Timedelta(days=i)
                try:
                    rows += na.rows(near, d)
                except Exception as e:  # noqa: BLE001
                    print(f"  near {d.date()}: {e}")
                    break
            n = ar._write_new(st, pd.DataFrame(rows)) if rows else 0
            print(f"  near: {n} new row(s) since {since.date()}")
    after = coverage(st, projects, ok)
    print(f"\nRAN {res['seconds']}s. Days stored in the last {ar.ARCHIVE_DAYS}, before -> after:")
    for k in sorted(after):
        print(f"  {k[0]:<13} {k[1]:<32} {before.get(k, 0):>4} -> {after[k]:>4}"
              + (f"  (+{res['written'].get(k, 0)} archive)" if res["written"].get(k) else ""))
    for (n, m), k in sorted(res["flows"].items()):
        print(f"  flow {n}/{m}: {k} new row(s) differenced from the backfilled stock")
    for (n, m), why in sorted(res["refused"].items()):
        print(f"  REFUSED {n}/{m}: {why}")
    for line in res["report"]:
        print(f"  {line}")
    for n, m, why in res["skipped"]:
        print(f"  not backfillable {n}/{m}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
