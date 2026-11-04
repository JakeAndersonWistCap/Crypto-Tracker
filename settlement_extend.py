#!/usr/bin/env python3
"""settlement_extend.py - Ethereum settlement without Artemis, in Jake's order (P5-1.9, 2026-11-04).

    python settlement_extend.py validate                         (1) READ-ONLY: the rebuild vs the Artemis CSV
    python settlement_extend.py dry-run  [--from 2025-05-01]     (2) BigQuery DRY RUNS: bytes per weekly piece, no quota
    python settlement_extend.py backfill [--from 2025-05-01]     (3) the plan only; add --apply to read the pieces

THE RULE, STATED BEFORE ANY NUMBER IS SEEN (Jake 2026-11-04): the rebuild (DefiLlama DEX + DefiLlama NFT + BigQuery
P2P, summed on the days all three are stored - build_workbook._settlement_rebuild_views' arithmetic) is compared with
the Artemis CSV on 2026-05-28..2026-08-25. PASS only when every one of those 90 days holds both, and
sum(rebuild) / sum(CSV) - 1 is within +-1%. Anything else is NOT PASSED and the rebuild is not extended or adopted. The
weekly and daily gaps are printed beside it, not gated. The result goes to data/ethereum/settlement_overlap.json.

`validate` opens metrics.db read-only and reads no network. `dry-run` asks BigQuery for each piece's byte count
(dry_run=True: no data read, no quota used). `backfill --apply` refuses unless the validation file says PASS; it reads
each weekly piece into the P2P cache under the shared monthly budget (a piece that does not fit is held until the
month resets) and writes nothing to metrics.db: the next `python token_metrics.py` values and stores those days.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "data" / "ethereum" / "settlement_overlap.json"
OVERLAP = ("2026-05-28", "2026-08-25")
TOL = 0.01
EXTEND_FROM = "2025-05-01"


def _series(con, metric: str, source_prefix: str | None = None) -> pd.Series:
    q = "SELECT substr(date,1,10), value, source FROM metrics WHERE project='Ethereum' AND metric=? ORDER BY date"
    rows = [(d, v) for d, v, s in con.execute(q, (metric,)) if source_prefix is None or str(s).startswith(source_prefix)]
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows if v is not None}, dtype=float)
    return s.sort_index()


def overlap_check(dex: pd.Series, nft: pd.Series, p2p: pd.Series, csv: pd.Series,
                  lo: str = OVERLAP[0], hi: str = OVERLAP[1], tol: float = TOL) -> dict:
    """The rule above, on four daily series. Returns the verdict and every figure behind it."""
    days = pd.date_range(lo, hi, freq="D")
    legs = pd.concat([dex.rename("dex"), nft.rename("nft"), p2p.rename("p2p")], axis=1, sort=True).dropna()
    reb = legs.sum(axis=1)
    both = [d for d in days if d in reb.index and d in csv.index]
    missing = {k: [str(d.date()) for d in days if d not in s.index]
               for k, s in (("dex", dex), ("nft", nft), ("p2p", p2p), ("artemis_csv", csv))}
    out = {"overlap": f"{lo}..{hi}", "days_required": len(days), "days_with_both": len(both), "tolerance": tol,
           "missing_days": {k: v for k, v in missing.items() if v}}
    if both:
        r, a = reb[both], csv[both]
        out.update({"rebuild_sum": float(r.sum()), "csv_sum": float(a.sum()), "ratio_minus_1": float(r.sum() / a.sum() - 1),
                    "legs_sum": {k: float(legs.loc[both, k].sum()) for k in ("dex", "nft", "p2p")}})
        gap = (r / a - 1).abs()
        out["daily_abs_gap"] = {"median": float(gap.median()), "p90": float(gap.quantile(0.9))}
        w = pd.concat([r.rename("r"), a.rename("a")], axis=1)
        wk = w.groupby(w.index.to_period("W")).sum()
        out["weekly"] = {str(k): round(float(v), 4) for k, v in (wk["r"] / wk["a"] - 1).items()}
    complete = len(both) == len(days)
    within = complete and abs(out.get("ratio_minus_1", 1.0)) <= tol
    out["status"] = "PASS" if within else "NOT PASSED"
    out["why"] = ("every day held; " if complete else f"only {len(both)} of {len(days)} days hold both the rebuild and "
                  f"the CSV; ") + (f"rebuild / CSV - 1 = {out['ratio_minus_1']:+.2%} "
                                   f"({'within' if within else 'outside'} +-{tol:.0%})" if both else "no common day")
    return out


def cmd_validate(db: Path) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        res = overlap_check(_series(con, "dex_volume_usd"), _series(con, "nft_volume_usd"),
                            _series(con, "p2p_transfer_volume_usd"), _series(con, "settlement_volume_usd", "artemis"))
    finally:
        con.close()
    res["run_on"] = str(pd.Timestamp.now(tz="UTC").date())
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(res, indent=1))
    print(f"SETTLEMENT OVERLAP {res['overlap']}: {res['status']} - {res['why']}")
    if "rebuild_sum" in res:
        print(f"  rebuild {res['rebuild_sum'] / 1e9:,.2f}B vs Artemis CSV {res['csv_sum'] / 1e9:,.2f}B; legs: "
              + ", ".join(f"{k} {v / 1e9:,.2f}B" for k, v in res["legs_sum"].items()))
        print(f"  daily |gap| median {res['daily_abs_gap']['median']:.1%}, 90th percentile {res['daily_abs_gap']['p90']:.1%}"
              f" (reported, not gated)")
        print("  weekly: " + ", ".join(f"{k[:10]} {v:+.1%}" for k, v in res["weekly"].items()))
    for k, v in res["missing_days"].items():
        print(f"  {k} lacks {len(v)} day(s): {', '.join(v[:8])}{' ...' if len(v) > 8 else ''}")
    print(f"  written to {RESULT.relative_to(ROOT)}")
    return 0 if res["status"] == "PASS" else 3


def _p2p_reader():
    from fetch.base import FetchOutput, today
    from fetch.eth_settlement import EthP2PBigQuery, _spec
    r = EthP2PBigQuery()
    spec = _spec()
    bq = r._bq_spec()
    return r, spec, bq, FetchOutput(), today()


def _pieces(r, spec, frm: str, now) -> list:
    p2p = {**spec["p2p"], "validation_extend_from": frm}
    st = r._load()
    held = r.held_days(st.get("eth_p2p") or {})
    first = pd.Timestamp(spec["p2p"].get("validation_extend_from") or spec["p2p"]["validation_from"])
    return sorted((a, b) for a, b in r.pieces(held, p2p, now) if a < first), p2p, st


def cmd_dry_run(frm: str) -> int:
    r, spec, bq, out, now = _p2p_reader()
    client, why = r._client(bq)
    if client is None:
        print(f"NOT RUN - {why}")
        return 2
    from fetch.eth_settlement import SQL
    sql = (SQL / spec["p2p"]["sql"]).read_text()
    toks = [t[1] for t in spec["p2p"]["tokens"]]
    todo, _p2p, st = _pieces(r, spec, frm, now)
    total = 0
    print(f"ETHEREUM P2P DRY RUNS (no data read, no quota): {len(todo)} weekly piece(s) from {frm} to the day before "
          f"the stored history")
    for a, b in todo:
        n = r._dry(client, sql, {"d0": a.date(), "d1": b.date(), "tokens": toks}, bq)
        total += n
        print(f"  {a.date()}..{b.date()}  {n / 1e9:,.1f} GB")
    print(f"TOTAL {total / 1e9:,.1f} GB over {len(todo)} piece(s); {r._quota_line(bq, st)}")
    print(f"each piece is at most {int(bq['max_bytes_per_query']) / 1e9:,.0f} GB a query; a piece that does not fit "
          f"this month is held until the reset")
    return 0


def cmd_backfill(frm: str, apply: bool) -> int:
    res = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    if res.get("status") != "PASS":
        print(f"REFUSED - the overlap validation has not passed ({RESULT.relative_to(ROOT)}: "
              f"{res.get('status', 'absent')} {res.get('why', '')}). Run `python settlement_extend.py validate` first.")
        return 3
    r, spec, bq, out, now = _p2p_reader()
    todo, p2p, st = _pieces(r, spec, frm, now)
    print(f"PLAN: {len(todo)} weekly piece(s) from {frm}: "
          + ", ".join(f"{a.date()}..{b.date()}" for a, b in todo[:6]) + (" ..." if len(todo) > 6 else ""))
    if not apply:
        print("nothing read (add --apply)")
        return 0
    client, why = r._client(bq)
    if client is None:
        print(f"NOT RUN - {why}")
        return 2
    e = st.setdefault("eth_p2p", {"days": {}, "prices": {}})
    done = r._turn(client, bq, p2p, st, e, out, "Ethereum", now, unbounded=True)
    r._save(st)
    for ev in getattr(out, "log", []):
        print(f"  {ev.status}: {ev.message}")
    print("ALL PIECES READ" if done else "SOME PIECES HELD (budget) - run again after the month resets")
    print("next: `python token_metrics.py` values and stores the read days (p2p_transfer_volume_usd)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("validate", "dry-run", "backfill"))
    ap.add_argument("--from", dest="frm", default=EXTEND_FROM)
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "validate":
        return cmd_validate(Path(a.db))
    if a.mode == "dry-run":
        return cmd_dry_run(a.frm)
    return cmd_backfill(a.frm, a.apply)


if __name__ == "__main__":
    import progress  # noqa: E402
    progress.line_buffered()
    sys.exit(main())
