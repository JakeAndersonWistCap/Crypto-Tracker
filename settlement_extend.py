#!/usr/bin/env python3
"""settlement_extend.py - Ethereum settlement without Artemis (P5-1.9, Jake 2026-11-04).

    python settlement_extend.py validate                         READ-ONLY REFERENCE: the rebuild's legs beside the CSV
    python settlement_extend.py dry-run  [--from 2024-11-04]     BigQuery DRY RUNS: bytes per weekly piece, no quota
    python settlement_extend.py cex-sweep [--week 2026-10-26]    the exchange double count: DRY RUN; --apply reads it
    python settlement_extend.py backfill [--from 2024-11-04]     the plan only; add --apply to read the pieces

JAKE'S DECISIONS (2026-11-04): the BigQuery definition IS the series - there is NO +-1% test against Artemis. `validate`
prints the rebuild's legs and the ratio to the Artemis CSV on 2026-05-28..2026-08-25 as a reference and decides nothing.
The extension reaches back to 2024-11-04, so A1's key (a 1-year change of a trailing-365-day sum: 730 days) forms on one
definition. `backfill --apply` refuses until Jake's budget decision is declared
(config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]["extend_approved"]).

`cex-sweep` sizes the exchange double count before any Artemis removal: one Monday..Sunday week of P2P transfers (the
daily SQL's rules) INTO the wallets exchanges publish in their own proof-of-reserves lists
(research/cex_por/ethereum_por_wallets.json, each list cited), split by the sender (the same exchange, another listed
exchange, an unlisted account), valued as the P2P leg is valued and shown as a share of that week's stored
p2p_transfer_volume_usd. Nothing is subtracted. Without --apply it only dry-runs (no data read, no quota).

`validate` and `cex-sweep` open metrics.db read-only. `dry-run` asks BigQuery for byte counts only. Nothing here writes
metrics.db: after a backfill, the next `python token_metrics.py` values and stores the days.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "data" / "ethereum" / "settlement_overlap.json"
SWEEP_DIR = ROOT / "data" / "ethereum"
POR_LISTS = ROOT / "research" / "cex_por" / "ethereum_por_wallets.json"
CEX_SQL = "bigquery_p2p_cex_week.sql"
OVERLAP = ("2026-05-28", "2026-08-25")
EXTEND_FROM = "2024-11-04"         # Jake 2026-11-04: A1's key needs 730 days on one definition


def _series(con, metric: str, source_prefix: str | None = None) -> pd.Series:
    q = "SELECT substr(date,1,10), value, source FROM metrics WHERE project='Ethereum' AND metric=? ORDER BY date"
    rows = [(d, v) for d, v, s in con.execute(q, (metric,)) if source_prefix is None or str(s).startswith(source_prefix)]
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows if v is not None}, dtype=float)
    return s.sort_index()


def overlap_check(dex: pd.Series, nft: pd.Series, p2p: pd.Series, csv: pd.Series,
                  lo: str = OVERLAP[0], hi: str = OVERLAP[1]) -> dict:
    """The rebuild beside the Artemis CSV on four daily series - a REFERENCE, never a test (Jake 2026-11-04: the
    BigQuery definition is the series, no +-1% test). Every figure behind the ratio is returned."""
    days = pd.date_range(lo, hi, freq="D")
    legs = pd.concat([dex.rename("dex"), nft.rename("nft"), p2p.rename("p2p")], axis=1, sort=True).dropna()
    reb = legs.sum(axis=1)
    both = [d for d in days if d in reb.index and d in csv.index]
    missing = {k: [str(d.date()) for d in days if d not in s.index]
               for k, s in (("dex", dex), ("nft", nft), ("p2p", p2p), ("artemis_csv", csv))}
    out = {"overlap": f"{lo}..{hi}", "days_required": len(days), "days_with_both": len(both),
           "reference": "decides nothing - Jake 2026-11-04: the BigQuery definition is the series; no +-1% test",
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
    out["summary"] = (f"{len(both)} of {len(days)} days hold both; " + (
        f"rebuild / CSV - 1 = {out['ratio_minus_1']:+.2%}" if both else "no common day"))
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
    print(f"SETTLEMENT OVERLAP {res['overlap']} (REFERENCE ONLY - decides nothing): {res['summary']}")
    if "rebuild_sum" in res:
        print(f"  rebuild {res['rebuild_sum'] / 1e9:,.2f}B vs Artemis CSV {res['csv_sum'] / 1e9:,.2f}B; legs: "
              + ", ".join(f"{k} {v / 1e9:,.2f}B" for k, v in res["legs_sum"].items()))
        print(f"  daily |gap| median {res['daily_abs_gap']['median']:.1%}, 90th percentile {res['daily_abs_gap']['p90']:.1%}")
        print("  weekly: " + ", ".join(f"{k[:10]} {v:+.1%}" for k, v in res["weekly"].items()))
    for k, v in res["missing_days"].items():
        print(f"  {k} lacks {len(v)} day(s): {', '.join(v[:8])}{' ...' if len(v) > 8 else ''}")
    print(f"  written to {RESULT.relative_to(ROOT)}")
    return 0


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


def extend_approval() -> dict | None:
    import config
    return config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"].get("extend_approved")


def cmd_backfill(frm: str, apply: bool) -> int:
    ok = extend_approval()
    if apply and not ok:
        print("REFUSED - the extension waits on Jake's BigQuery budget decision (config.SETTLEMENT_REBUILD"
              "['ethereum_rebuild']['p2p']['extend_approved'] is not declared). Nothing read.")
        return 3
    r, spec, bq, out, now = _p2p_reader()
    todo, p2p, st = _pieces(r, spec, frm, now)
    print(f"PLAN: {len(todo)} weekly piece(s) from {frm}: "
          + ", ".join(f"{a.date()}..{b.date()}" for a, b in todo[:6]) + (" ..." if len(todo) > 6 else ""))
    if not apply:
        print("nothing read (add --apply); the extension approval is "
              + (f"declared: {ok}" if ok else "NOT declared yet (Jake's budget decision)"))
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


# ---------- the exchange double count, sized (P5-1.9, Jake 2026-11-04) ----------

def por_wallets(path: Path | None = None) -> tuple[list[str], list[str], dict, list[str]]:
    """(addresses, exchange names, the lists, addresses dropped). Every address lower-cased, once; an address two
    exchanges both list is dropped and named (it cannot be given to one of them)."""
    lists = json.loads(Path(path or POR_LISTS).read_text(encoding="utf-8"))
    owner: dict[str, set] = {}
    for ex, v in lists.items():
        for a in v.get("eth_addresses") or ():
            owner.setdefault(str(a).lower(), set()).add(ex)
    dropped = sorted(a for a, exs in owner.items() if len(exs) > 1)
    keep = sorted((a, next(iter(exs))) for a, exs in owner.items() if len(exs) == 1)
    return [a for a, _ in keep], [e for _, e in keep], lists, dropped


def _stored_days(con, metric: str) -> pd.Series:
    q = "SELECT substr(date,1,10), value FROM metrics WHERE project='Ethereum' AND metric=? AND value IS NOT NULL"
    return pd.Series({pd.Timestamp(d): float(v) for d, v in con.execute(q, (metric,))}, dtype=float).sort_index()


def sweep_week(p2p: pd.Series, week: str | None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The Monday..Sunday week asked for, or the newest week whose seven days all hold a stored P2P row."""
    if week:
        a = pd.Timestamp(week)
        if a.weekday() != 0:
            raise ValueError(f"--week {week} is not a Monday")
        return a, a + pd.Timedelta(days=6)
    held = set(p2p.index)
    for mon in sorted({d - pd.Timedelta(days=d.weekday()) for d in held}, reverse=True):
        if all(mon + pd.Timedelta(days=i) in held for i in range(7)):
            return mon, mon + pd.Timedelta(days=6)
    raise ValueError("no Monday..Sunday week with all seven P2P days stored")


def value_sweep(rows: list[dict], eth_px: pd.Series, prices: dict, tokens: tuple) -> tuple[list[dict], list[str]]:
    """Each row's USD, valued as the P2P leg is valued: ETH and WETH at the stored Ethereum close; a stablecoin at
    DefiLlama's price that day, its decimals checked against the declared ones. A row that cannot be valued is not
    counted and is named - never valued 0."""
    dec = {t[1]: int(t[2]) for t in tokens}
    weth = next(t[1] for t in tokens if t[0] == "WETH")
    out, why = [], []
    for r in rows:
        day, tok = pd.Timestamp(r["day"]), str(r["token"]).lower()
        tok = "ETH" if tok == "eth" else tok
        p_eth = eth_px.get(day)
        if tok in ("ETH", weth):
            if p_eth is None or pd.isna(p_eth):
                why.append(f"{day.date()} {tok}: no stored Ethereum price_usd")
                continue
            usd = float(Decimal(str(r["amount"])) / Decimal(10) ** 18) * float(p_eth)
        else:
            p = (prices.get(str(day.date())) or {}).get(f"ethereum:{tok}") or (None, None)
            if p[0] is None or p[1] is None or int(p[1]) != dec.get(tok):
                why.append(f"{day.date()} {tok}: " + ("unpriced" if p[0] is None else f"decimals {p[1]}"))
                continue
            usd = float(Decimal(str(r["amount"])) / Decimal(10) ** dec[tok]) * float(p[0])
        out.append({**r, "token": tok, "usd": usd})
    return out, why


def sweep_summary(valued: list[dict], p2p_week: float) -> dict:
    by = {}
    for r in valued:
        k = (r["exchange"], r["sender"])
        by[k] = by.get(k, 0.0) + r["usd"]
    legs = {s: sum(v for (_e, s2), v in by.items() if s2 == s) for s in ("same_exchange", "other_exchange", "unlisted")}
    tot = sum(legs.values())
    return {"p2p_week_usd": p2p_week, "into_listed_wallets_usd": tot,
            "share_of_p2p": tot / p2p_week if p2p_week else None,
            "by_sender": {s: {"usd": v, "share_of_p2p": v / p2p_week if p2p_week else None} for s, v in legs.items()},
            "by_exchange": {f"{e} / {s}": v for (e, s), v in sorted(by.items(), key=lambda kv: -kv[1])}}


def cmd_cex_sweep(week: str | None, apply: bool, db: Path) -> int:
    addrs, names, lists, dropped = por_wallets()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        p2p, eth_px = _stored_days(con, "p2p_transfer_volume_usd"), _stored_days(con, "price_usd")
    finally:
        con.close()
    a, b = sweep_week(p2p, week)
    days = pd.date_range(a, b)
    lacking = [str(d.date()) for d in days if d not in p2p.index]
    exs = sorted(set(names))
    print(f"EXCHANGE DOUBLE COUNT, week {a.date()}..{b.date()}: {len(addrs)} published proof-of-reserves wallet(s) of "
          f"{len(exs)} exchange(s) ({', '.join(exs)})" + (f"; {len(dropped)} address(es) listed by two exchanges "
                                                          f"dropped: {', '.join(dropped)}" if dropped else ""))
    r, spec, bq, out, now = _p2p_reader()
    client, why = r._client(bq)
    if client is None:
        print(f"NOT RUN - {why}")
        return 2
    from fetch.eth_settlement import SQL
    sql = (SQL / CEX_SQL).read_text()
    params = {"d0": a.date(), "d1": b.date(), "tokens": [t[1] for t in spec["p2p"]["tokens"]],
              "cex_addr": addrs, "cex_name": names}
    est = r._dry(client, sql, params, bq)
    print(f"  dry run: {est / 1e9:,.1f} GB (no data read, no quota)")
    if not apply:
        print("  nothing read (add --apply to run this one query under the shared monthly budget)")
        return 0
    st = r._load()
    rows = r._run(client, "cex-sweep", sql, params, bq, st, out, "Ethereum", backfill=True)
    for ev in getattr(out, "log", []):
        print(f"  {ev.status}: {ev.message}")
    if rows is None:
        return 2
    e = (st.get("eth_p2p") or {})
    prices = dict(e.get("prices") or {})
    for d in days:
        key = str(d.date())
        stables = sorted({str(x["token"]).lower() for x in rows if str(x["day"])[:10] == key} - {"eth"}
                         - {t[1] for t in spec["p2p"]["tokens"] if t[0] == "WETH"})
        if stables and not all(f"ethereum:{t}" in (prices.get(key) or {}) for t in stables):
            prices[key] = {c: list(v) for c, v in r._price(spec["p2p"], d, [f"ethereum:{t}" for t in stables]).items()}
    valued, why = value_sweep(rows, eth_px, prices, spec["p2p"]["tokens"])
    res = sweep_summary(valued, float(p2p.reindex(days).sum()))
    res.update({"week": f"{a.date()}..{b.date()}", "p2p_days_lacking": lacking, "rows_not_valued": why,
                "dry_run_bytes": est, "dropped_addresses": dropped, "subtracted": False,
                "lists": {k: {f: v.get(f) for f in ("page_url", "data_url", "read_on")} | {
                    "n_eth": len(v.get("eth_addresses") or ())} for k, v in lists.items()}})
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    path = SWEEP_DIR / f"cex_sweep_{a.date()}.json"
    path.write_text(json.dumps(res, indent=1, default=str))
    sh = res["share_of_p2p"]
    print(f"  INTO LISTED WALLETS ${res['into_listed_wallets_usd'] / 1e9:,.2f}B of the week's stored P2P "
          f"${res['p2p_week_usd'] / 1e9:,.2f}B = " + (f"{sh:.1%}" if sh is not None else "n/a")
          + " (NOT subtracted)")
    for s, v in res["by_sender"].items():
        print(f"    from {s:<15} ${v['usd'] / 1e9:,.2f}B" + (f"  {v['share_of_p2p']:.1%}" if v["share_of_p2p"] is not None
                                                            else ""))
    for k, v in list(res["by_exchange"].items())[:12]:
        print(f"    {k:<32} ${v / 1e9:,.2f}B")
    if lacking:
        print(f"  the week's stored P2P lacks {len(lacking)} day(s): {', '.join(lacking)} - the share is not formed "
              f"on a full week")
    if why:
        print(f"  {len(why)} row(s) not valued (not counted): {'; '.join(why[:6])}")
    print(f"  written to {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("validate", "dry-run", "cex-sweep", "backfill"))
    ap.add_argument("--week", default=None, help="cex-sweep: the Monday of the week (default: the newest full week)")
    ap.add_argument("--from", dest="frm", default=EXTEND_FROM)
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "validate":
        return cmd_validate(Path(a.db))
    if a.mode == "dry-run":
        return cmd_dry_run(a.frm)
    if a.mode == "cex-sweep":
        return cmd_cex_sweep(a.week, a.apply, Path(a.db))
    return cmd_backfill(a.frm, a.apply)


if __name__ == "__main__":
    import progress  # noqa: E402
    progress.line_buffered()
    sys.exit(main())
