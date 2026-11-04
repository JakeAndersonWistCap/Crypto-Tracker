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
CEX_SQL = "bigquery_p2p_week_split.sql"
MAX_GB = 25.0                      # cex-sweep --apply refuses a dry run above this (Jake: ~10 GB expected)
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
    lists = {k: v for k, v in json.loads(Path(path or POR_LISTS).read_text(encoding="utf-8")).items()
             if not k.startswith("_")}
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
        if not r.get("to_exchange", r.get("exchange")):
            continue
        r = {**r, "exchange": r.get("to_exchange", r.get("exchange"))}
        k = (r["exchange"], r["sender"])
        by[k] = by.get(k, 0.0) + r["usd"]
    legs = {s: sum(v for (_e, s2), v in by.items() if s2 == s) for s in ("same_exchange", "other_exchange", "unlisted")}
    tot = sum(legs.values())
    # The double count's BOUNDS: a list of hot/cold wallets puts a deposit-address sweep under "unlisted"; a list that
    # holds deposit addresses (OKX's is unlabelled) puts it under "same_exchange". So: low = the same exchange's
    # internal moves only; high = those plus every unlisted sender (which also holds users sending straight to a
    # listed wallet, and users depositing into a listed deposit address). Neither is subtracted.
    low, high = legs["same_exchange"], legs["same_exchange"] + legs["unlisted"]
    share = (lambda x: x / p2p_week if p2p_week else None)
    return {"p2p_week_usd": p2p_week, "into_listed_wallets_usd": tot,
            "share_of_p2p": share(tot),
            "double_count_bounds": {"low_usd": low, "low_share": share(low), "high_usd": high, "high_share": share(high)},
            "by_sender": {s: {"usd": v, "share_of_p2p": v / p2p_week if p2p_week else None} for s, v in legs.items()},
            "by_exchange": {f"{e} / {s}": v for (e, s), v in sorted(by.items(), key=lambda kv: -kv[1])}}


def split_summary(valued: list[dict], legs: dict) -> dict:
    """Hypothesis (a): the P2P leg by how each transfer was made, and the rebuild for the week with and without each
    correction. legs: the week's STORED dex / nft / p2p (and artemis where the week is in the CSV's span)."""
    by = {c: 0.0 for c in ("direct", "contract_call_single_token", "contract_call_multi_token")}
    tok = {}
    for r in valued:
        by[r["call_class"]] = by.get(r["call_class"], 0.0) + r["usd"]
        k = (r["token"], r["call_class"])
        tok[k] = tok.get(k, 0.0) + r["usd"]
    p2p_q = sum(by.values())
    dex, nft, p2p = legs.get("dex"), legs.get("nft"), legs.get("p2p")
    multi = by["contract_call_multi_token"]
    out = {"p2p_by_call_class_usd": by, "p2p_query_usd": p2p_q,
           "p2p_query_vs_stored": (p2p_q / p2p - 1) if p2p else None,
           "by_token_and_class_usd": {f"{t} / {c}": v for (t, c), v in sorted(tok.items(), key=lambda kv: -kv[1])},
           "multi_token_vs_dex": (multi / dex) if dex else None}
    if None not in (dex, nft, p2p):
        cex = sweep_summary(valued, p2p)["double_count_bounds"]
        base = dex + nft + p2p
        out["rebuild_week_usd"] = {
            "as built (DEX + NFT + P2P)": base,
            "(a) without the multi-token contract-call P2P": base - multi,
            "(b) without the DEX leg (P2P + NFT)": nft + p2p,
            "exchange double count removed, low": base - cex["low_usd"],
            "exchange double count removed, high": base - cex["high_usd"],
            "(a) + exchange low": base - multi - cex["low_usd"],
            "(a) + exchange high": base - multi - cex["high_usd"]}
        if legs.get("artemis"):
            out["vs_artemis"] = {k: v / legs["artemis"] - 1 for k, v in out["rebuild_week_usd"].items()}
    return out


def cmd_cex_sweep(week: str | None, apply: bool, db: Path, max_gb: float = MAX_GB) -> int:
    """ONE WEEK, ONE QUERY (Jake 2026-11-04): every P2P transfer of the week, split by how it was made (hypothesis (a))
    and by whether it went into a wallet an exchange publishes (the double count). Dry run first; --apply runs it only
    when the dry run is at most max_gb."""
    addrs, names, lists, dropped = por_wallets()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        p2p, eth_px = _stored_days(con, "p2p_transfer_volume_usd"), _stored_days(con, "price_usd")
        dex_s, nft_s = _stored_days(con, "dex_volume_usd"), _stored_days(con, "nft_volume_usd")
        art = _series(con, "settlement_volume_usd", "artemis")
    finally:
        con.close()
    a, b = sweep_week(p2p, week)
    days = pd.date_range(a, b)
    lacking = {k: [str(d.date()) for d in days if d not in s.index] for k, s in (("p2p", p2p), ("dex", dex_s),
                                                                                ("nft", nft_s))}
    lacking = {k: v for k, v in lacking.items() if v}
    legs = {k: (float(s.reindex(days).sum()) if k not in lacking else None)
            for k, s in (("dex", dex_s), ("nft", nft_s), ("p2p", p2p))}
    if all(d in art.index for d in days):
        legs["artemis"] = float(art.reindex(days).sum())
    exs = sorted(set(names))
    print(f"P2P WEEK SPLIT {a.date()}..{b.date()} (DEX hypothesis (a) + exchange double count): {len(addrs)} published "
          f"proof-of-reserves wallet(s) of {len(exs)} exchange(s) ({', '.join(exs)})"
          + (f"; {len(dropped)} address(es) listed by two exchanges dropped: {', '.join(dropped)}" if dropped else ""))
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
    if est > max_gb * 1e9:
        print(f"  REFUSED - the dry run is {est / 1e9:,.1f} GB, above the {max_gb:,.0f} GB this run allows "
              f"(--max-gb). Nothing read.")
        return 3
    st = r._load()
    rows = r._run(client, "p2p-week-split", sql, params, bq, st, out, "Ethereum", backfill=True)
    for ev in getattr(out, "log", []):
        print(f"  {ev.status}: {ev.message}")
    if rows is None:
        return 2
    e = (st.get("eth_p2p") or {})
    prices = dict(e.get("prices") or {})
    weth = {t[1] for t in spec["p2p"]["tokens"] if t[0] == "WETH"}
    for d in days:
        key = str(d.date())
        stables = sorted({str(x["token"]).lower() for x in rows if str(x["day"])[:10] == key} - {"eth"} - weth)
        if stables and not all(f"ethereum:{t}" in (prices.get(key) or {}) for t in stables):
            prices[key] = {c: list(v) for c, v in r._price(spec["p2p"], d, [f"ethereum:{t}" for t in stables]).items()}
    valued, why = value_sweep(rows, eth_px, prices, spec["p2p"]["tokens"])
    res = sweep_summary(valued, legs["p2p"] or 0.0)
    res.update(split_summary(valued, legs))
    res.update({"week": f"{a.date()}..{b.date()}", "stored_legs_usd": legs, "legs_lacking_days": lacking,
                "rows_not_valued": why, "dry_run_bytes": est, "dropped_addresses": dropped, "subtracted": False,
                "lists": {k: {f: v.get(f) for f in ("page_url", "data_url", "read_on")} | {
                    "n_eth": len(v.get("eth_addresses") or ())} for k, v in lists.items()}})
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    path = SWEEP_DIR / f"p2p_week_split_{a.date()}.json"
    path.write_text(json.dumps(res, indent=1, default=str))
    B = lambda x: f"${x / 1e9:,.2f}B" if x is not None else "n/a"      # noqa: E731
    pc = lambda x: f"{x:+.1%}" if x is not None else "n/a"             # noqa: E731
    print(f"  P2P from the query {B(res['p2p_query_usd'])} vs stored {B(legs['p2p'])} ({pc(res['p2p_query_vs_stored'])}"
          f" - the query must reproduce the stored leg)")
    print("  (a) HOW THE P2P LEG WAS MADE:")
    for c, v in res["p2p_by_call_class_usd"].items():
        print(f"    {c:<28} {B(v)}" + (f"  {v / res['p2p_query_usd']:.1%} of P2P" if res["p2p_query_usd"] else ""))
    print(f"    multi-token contract calls / the week's DEX leg {B(legs['dex'])} = "
          + (f"{res['multi_token_vs_dex']:.2f}" if res["multi_token_vs_dex"] is not None else "n/a"))
    for k, v in list(res["by_token_and_class_usd"].items())[:10]:
        print(f"      {k:<52} {B(v)}")
    sh = res["share_of_p2p"]
    print(f"  EXCHANGE DOUBLE COUNT: into listed wallets {B(res['into_listed_wallets_usd'])} = "
          + (f"{sh:.1%}" if sh is not None else "n/a") + " of the week's P2P (NOT subtracted)")
    bd = res["double_count_bounds"]
    if bd["low_share"] is not None:
        print(f"    bounds: low {bd['low_share']:.1%} (same exchange's own moves) .. high {bd['high_share']:.1%} "
              f"(+ every unlisted sender)")
    for s, v in res["by_sender"].items():
        print(f"    from {s:<15} {B(v['usd'])}")
    for k, v in list(res["by_exchange"].items())[:8]:
        print(f"    {k:<32} {B(v)}")
    if "rebuild_week_usd" in res:
        print("  THE WEEK'S REBUILD, WITH AND WITHOUT EACH CORRECTION" + (" (vs Artemis's CSV)" if "vs_artemis" in res
                                                                         else ""))
        for k, v in res["rebuild_week_usd"].items():
            print(f"    {k:<50} {B(v)}" + (f"  {pc(res['vs_artemis'][k])}" if "vs_artemis" in res else ""))
    for k, v in lacking.items():
        print(f"  the week's stored {k} lacks {len(v)} day(s): {', '.join(v)}")
    if why:
        print(f"  {len(why)} row(s) not valued (not counted): {'; '.join(why[:6])}")
    print(f"  written to {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return 0

def _fit(y: pd.Series, x: pd.Series) -> dict:
    """Least squares y = c + s x on the common days: slope, intercept, R^2 (reported, never a test)."""
    d = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    if len(d) < 3 or d["x"].var() == 0:
        return {"n": len(d)}
    s = d["x"].cov(d["y"]) / d["x"].var()
    c = d["y"].mean() - s * d["x"].mean()
    r2 = d["x"].corr(d["y"]) ** 2
    return {"n": len(d), "slope": float(s), "intercept": float(c), "r2": float(r2)}


def p2p_by_token(st: dict, eth_px: pd.Series, tokens: tuple, days) -> pd.DataFrame:
    """The P2P cache's per-token amounts valued as the leg is valued: one column per token symbol, a row per day held."""
    sym = {t[1]: t[0] for t in tokens}
    e = st.get("eth_p2p") or {}
    rows = []
    for d in days:
        key = str(d.date())
        held = (e.get("days") or {}).get(key)
        if not held:
            continue
        for k, amt in held.items():
            kk = str(k).lower()
            addr = "ETH" if kk == "eth" else next((a for a, s in sym.items() if kk in (a, s.lower())), kk)
            rows.append({"day": key, "token": addr, "amount": amt})
    valued, _why = value_sweep(rows, eth_px, e.get("prices") or {}, tokens)
    if not valued:
        return pd.DataFrame()
    df = pd.DataFrame(valued)
    df["sym"] = [("ETH" if t == "ETH" else sym.get(t, t)) for t in df["token"]]
    return df.pivot_table(index=pd.to_datetime(df["day"]), columns="sym", values="usd", aggfunc="sum").sort_index()


def cmd_explain(db: Path, st: dict | None = None) -> int:
    """READ-ONLY, NO BIGQUERY (Jake 2026-11-04): hypothesis (b) and the weekly gaps on the overlap. Per week: Artemis's
    CSV, the three stored legs, the gap with and without the DEX leg, the P2P leg by token (the P2P cache, valued as the
    leg is) and the ETH close. Per day: how well P2P alone, and P2P + DEX, track the CSV."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dex, nft, p2p = (_series(con, m) for m in ("dex_volume_usd", "nft_volume_usd", "p2p_transfer_volume_usd"))
        art, eth_px = _series(con, "settlement_volume_usd", "artemis"), _stored_days(con, "price_usd")
    finally:
        con.close()
    days = pd.date_range(*OVERLAP)
    f = pd.concat([art.rename("artemis"), p2p.rename("p2p"), dex.rename("dex"), nft.rename("nft")], axis=1) \
        .reindex(days).dropna()
    print(f"EXPLAIN {OVERLAP[0]}..{OVERLAP[1]} (read-only; decides nothing): {len(f)} of {len(days)} days hold all four")
    if f.empty:
        return 0
    tot = f.sum()
    for label, num in (("P2P alone", tot["p2p"]), ("P2P + NFT (hypothesis (b): no DEX)", tot["p2p"] + tot["nft"]),
                       ("DEX + NFT + P2P (as built)", tot["p2p"] + tot["dex"] + tot["nft"])):
        print(f"  {label:<40} ${num / 1e9:,.2f}B vs Artemis ${tot['artemis'] / 1e9:,.2f}B = {num / tot['artemis'] - 1:+.2%}")
    fb = _fit(f["artemis"] - f["p2p"], f["dex"])
    if "slope" in fb:
        print(f"  daily (Artemis - P2P) on DEX: slope {fb['slope']:.2f}, R^2 {fb['r2']:.2f} over {fb['n']} days "
              f"(about 1 if Artemis holds a DEX leg our P2P lacks; about 0 if not)")
    for lab, x in (("P2P alone", f["p2p"]), ("P2P + DEX + NFT", f["p2p"] + f["dex"] + f["nft"])):
        r = _fit(f["artemis"], x)
        if "r2" in r:
            print(f"  daily Artemis on {lab}: R^2 {r['r2']:.3f}, slope {r['slope']:.2f}")
    if st is None:
        from fetch.eth_settlement import EthP2PBigQuery
        st = EthP2PBigQuery()._load()
    from fetch.eth_settlement import _spec
    toks = _spec()["p2p"]["tokens"]
    bt = p2p_by_token(st, eth_px, toks, f.index)
    w = f.groupby(f.index.to_period("W")).sum()
    wbt = bt.groupby(bt.index.to_period("W")).sum() if not bt.empty else pd.DataFrame()
    wpx = eth_px.reindex(f.index).groupby(f.index.to_period("W")).mean()
    cols = list(wbt.columns) if not wbt.empty else []
    print("  WEEK        Artemis    P2P      DEX    NFT  built-Art  (P2P+NFT)-Art  ETH close  P2P mix: "
          + " ".join(f"{c:>5}" for c in cols))
    for k, r in w.iterrows():
        built = (r["p2p"] + r["dex"] + r["nft"]) / r["artemis"] - 1
        nodex = (r["p2p"] + r["nft"]) / r["artemis"] - 1
        mix = ""
        if k in getattr(wbt, "index", []):
            s = wbt.loc[k].sum()
            mix = " ".join(f"{wbt.loc[k, c] / s:5.1%}" for c in cols)
        print(f"  {str(k)[:10]}  {r['artemis'] / 1e9:7.1f}B {r['p2p'] / 1e9:7.1f}B {r['dex'] / 1e9:6.1f}B "
              f"{r['nft'] / 1e9:5.2f}B  {built:+8.1%}  {nodex:+12.1%}  {wpx.get(k, float('nan')):9,.0f}  {mix}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("validate", "explain", "dry-run", "cex-sweep", "backfill"))
    ap.add_argument("--week", default=None, help="cex-sweep: the Monday of the week (default: the newest full week)")
    ap.add_argument("--from", dest="frm", default=EXTEND_FROM)
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-gb", type=float, default=MAX_GB, help="cex-sweep --apply: refuse a dry run above this")
    a = ap.parse_args(argv)
    if a.mode == "validate":
        return cmd_validate(Path(a.db))
    if a.mode == "explain":
        return cmd_explain(Path(a.db))
    if a.mode == "dry-run":
        return cmd_dry_run(a.frm)
    if a.mode == "cex-sweep":
        return cmd_cex_sweep(a.week, a.apply, Path(a.db), a.max_gb)
    return cmd_backfill(a.frm, a.apply)


if __name__ == "__main__":
    import progress  # noqa: E402
    progress.line_buffered()
    sys.exit(main())
