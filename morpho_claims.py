#!/usr/bin/env python3
"""morpho_claims.py - MORPHO claimed on Ethereum, split by reward date, and the A2 key on each basis (P5-1C.C5,
Jake 2026-11-04).

    python morpho_claims.py                       DRY RUN (the default): the bytes the BigQuery read would scan - no data
                                                  read, no quota spent
    python morpho_claims.py --apply               read the window's events (one query, the shared monthly budget) and
                                                  write data/morpho/claims_split_<from>_<to>.json
    python morpho_claims.py --recorded            no BigQuery: the live read recorded in config.MORPHO_CLAIMS_SPLIT
    python morpho_claims.py ... --from 2026-08-07 --to 2026-11-04 --db metrics.db --max-gb 300

WHAT IT READS: bigquery-public-data.crypto_ethereum.logs (sql/ethereum/bigquery_morpho_claims.sql) - every
Claimed(user, MORPHO, amount) out of Merkl's Distributor 0x3Ef3D8bA... and the legacy Morpho URD 0x330eefa8..., and the
URD's RootSet events, in the window. THE SPLIT BY REWARD DATE: a URD claim pays what a root committed, so with no
RootSet inside the window (the URD's last root is 2025-08-11, config.MORPHO_CLAIMS_SPLIT) every URD claim is an EARLIER
reward claimed late; after a RootSet inside the window the URD's claims are UNDETERMINED (never assumed in-window).
Merkl's tree updates every few hours, so its claims are bounded instead: at most the window's PROGRAMMED amount (Merkl's
campaigns, prorated) can be the window's own rewards. THE A2 KEY (customer revenue ÷ emissions $ at the 90-day average
price, each per covered day) is printed on three bases - CLAIMED (as built: every claim), PROGRAMMED (Merkl's schedule
on every chain) and SPLIT (claims whose reward date can be in the window) - from metrics.db, opened read-only. The key's
basis in the workbook is NOT changed by this script. The numerator is GROSS BORROWER INTEREST (config.NUMERATOR_LABEL).
Nothing here writes metrics.db.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "morpho"
MAX_GB = 300.0                     # --apply refuses a dry run above this (logs, 90 days: the dry run prints the size)
WEI = 10 ** 18


def _cfg() -> dict:
    import config
    return config.MORPHO_CLAIMS_SPLIT


def query_params(cfg: dict, d0: str, d1: str) -> dict:
    """The SQL's parameters: dates, the distributors and topic0s lower-case (Claimed first: the SQL keeps a Claimed
    row only for the reward token), and the token left-padded to 32 bytes."""
    import pandas as pd
    return {"d0": pd.Timestamp(d0).date(), "d1": pd.Timestamp(d1).date(),
            "distributors": [a.lower() for a in cfg["distributors"].values()],
            "topics": [cfg["topics"]["Claimed"].lower(), cfg["topics"]["RootSet"].lower()],
            "token_topics": ["0x" + cfg["token"].lower()[2:].rjust(64, "0")]}


def parse_rows(rows: list[dict], cfg: dict) -> list[dict]:
    """Query rows -> [{"day", "block", "who" ('merkl' | 'urd'), "event" ('Claimed' | 'RootSet'), "amount"}] (MORPHO)."""
    by_addr = {a.lower(): k for k, a in cfg["distributors"].items()}
    by_topic = {t.lower(): k for k, t in cfg["topics"].items()}
    out = []
    for r in rows:
        who, ev = by_addr.get(str(r["distributor"]).lower()), by_topic.get(str(r["topic0"]).lower())
        if who is None or ev is None:
            continue
        data = str(r.get("data") or "0x")[2:]
        amt = int(data[:64], 16) / WEI if ev == "Claimed" and data else 0.0
        out.append({"day": str(r["day"])[:10], "block": int(r["block_number"]), "who": who, "event": ev,
                    "amount": amt})
    return sorted(out, key=lambda e: e["block"])


def split(events: list[dict], programmed_eth: float | None) -> dict:
    """The window's claims split by reward date. URD: claims before the first RootSet inside the window are earlier
    rewards (the root they pay was committed before the window); claims from that RootSet on are undetermined. Merkl:
    at most the window's programmed amount on Ethereum is the window's own; the rest is earlier rewards claimed late.
    With no programmed amount for the window (None) Merkl's claims are not bounded: earlier rewards at least 0."""
    roots = [e["block"] for e in events if e["who"] == "urd" and e["event"] == "RootSet"]
    first_root = min(roots) if roots else None
    urd = [e for e in events if e["who"] == "urd" and e["event"] == "Claimed"]
    merkl = sum(e["amount"] for e in events if e["who"] == "merkl" and e["event"] == "Claimed")
    urd_pre = sum(e["amount"] for e in urd if first_root is None or e["block"] < first_root)
    urd_all = sum(e["amount"] for e in urd)
    in_max = merkl if programmed_eth is None else min(merkl, max(programmed_eth, 0.0))
    return {"merkl_claimed": merkl, "urd_claimed": urd_all, "claimed": merkl + urd_all,
            "urd_earlier_rewards": urd_pre, "urd_undetermined": urd_all - urd_pre,
            "urd_roots_in_window": [e["day"] for e in events if e["who"] == "urd" and e["event"] == "RootSet"],
            "merkl_window_rewards_max": in_max, "merkl_earlier_rewards_min": merkl - in_max,
            "programmed_ethereum": programmed_eth,
            "claims": sum(1 for e in events if e["event"] == "Claimed")}


def a2_key(rev_usd: float, rev_days: int, tokens: float, tok_days: int, avg_price: float) -> float | None:
    """The A2 key as the workbook forms it: customer revenue per covered day ÷ (tokens per covered day x the 90-day
    average price). None where an input is missing or the denominator is 0 (unknown is never 0)."""
    if not (rev_days and tok_days and avg_price) or tokens is None or tokens <= 0 or rev_usd is None:
        return None
    return (rev_usd / rev_days) / (tokens / tok_days * avg_price)


def key_bases(store: dict, sp: dict, programmed_all: float) -> dict:
    """{basis: (tokens, key)}. CLAIMED = emissions_tokens as stored (every chain's claims). PROGRAMMED = Merkl's
    schedule on every chain. SPLIT = the stored claims less the URD's earlier rewards (and its undetermined claims), with
    Merkl's Ethereum claims at most the window's programmed amount - 'high' takes the most a claim can be in-window,
    'low' treats every Merkl Ethereum claim as earlier rewards."""
    e = store.get("emissions_tokens")
    base = (e or 0.0) - sp["urd_claimed"]
    rows = {"claimed": e, "programmed": programmed_all or None,
            "split_high": base - sp["merkl_earlier_rewards_min"] if e is not None else None,
            "split_low": base - sp["merkl_claimed"] if e is not None else None}
    # the store holding FEWER claims than the read (an older store, a narrower scan) leaves a split unknown, never < 0
    rows = {k: (t if t is None or t > 0 else None) for k, t in rows.items()}
    # Merkl's schedule covers every day of the window; the claims, the days the store holds them
    days = {k: (store.get("window_days") if k == "programmed" else store.get("emis_days")) for k in rows}
    return {k: (t, a2_key(store.get("rev_usd"), store.get("rev_days"), t, days[k], store.get("price")))
            for k, t in rows.items()}


def store_inputs(db: Path, d0: str, d1: str) -> dict:
    """Morpho's window sums from metrics.db, opened READ-ONLY: customer_revenue_usd and the claims (the legs
    emissions_tokens is summed from, config emissions_from_metric - else a stored emissions_tokens), each with its days
    holding a row, and the window's average price_usd. A leg with no row in the window leaves the claims unknown."""
    import config
    import pandas as pd
    out: dict = {"window_days": int((pd.Timestamp(d1) - pd.Timestamp(d0)).days) + 1}
    if not Path(db).exists():
        return out
    legs = tuple((config.PROJECT_BY_NAME.get("Morpho") or {}).get("emissions_from_metric") or ("emissions_tokens",))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = ("SELECT COUNT(*), SUM(value) FROM metrics WHERE project='Morpho' AND metric=? AND value IS NOT NULL "
             "AND substr(date,1,10) BETWEEN ? AND ?")
        n, s = con.execute(q, ("customer_revenue_usd", d0, d1)).fetchone()
        out["rev_days"], out["rev_usd"] = int(n or 0), s
        got = [con.execute(q, (m, d0, d1)).fetchone() for m in legs]
        if not any(n for n, _s in got):                              # an older store: emissions_tokens itself
            got = [con.execute(q, ("emissions_tokens", d0, d1)).fetchone()]
        known = all(n for n, _s in got)
        out["emissions_tokens"] = sum(s or 0.0 for _n, s in got) if known else None
        out["emis_days"] = min(int(n or 0) for n, _s in got) if known else 0
        n, s = con.execute(q, ("price_usd", d0, d1)).fetchone()
        out["price"] = (s / n) if n else None
    finally:
        con.close()
    return out


def report(sp: dict, store: dict, programmed_all: float, d0: str, d1: str) -> list[str]:
    L = [f"MORPHO CLAIMS ON ETHEREUM {d0}..{d1}: {sp['claims']:,} claims, {sp['claimed']:,.2f} MORPHO",
         f"  Merkl Distributor   {sp['merkl_claimed']:>16,.2f}   of it the window's own rewards at most "
         f"{sp['merkl_window_rewards_max']:,.2f} (programmed on Ethereum "
         + (f"{sp['programmed_ethereum']:,.1f}" if sp["programmed_ethereum"] is not None else "not read for this window")
         + "); earlier "
         f"rewards at least {sp['merkl_earlier_rewards_min']:,.2f}",
         f"  Morpho URD (legacy) {sp['urd_claimed']:>16,.2f}   earlier rewards {sp['urd_earlier_rewards']:,.2f}; "
         f"undetermined {sp['urd_undetermined']:,.2f}"
         + (f" (RootSet inside the window: {', '.join(sp['urd_roots_in_window'])})" if sp["urd_roots_in_window"]
            else " (no RootSet inside the window)")]
    if store.get("rev_usd") is None:
        L.append("  A2 KEY: no metrics.db rows for Morpho in the window - keys not computed")
        return L
    L.append(f"  A2 KEY = gross borrower interest {store['rev_usd']:,.0f} over {store['rev_days']} day(s) ÷ (MORPHO "
             f"per covered day of {store.get('emis_days')} x the window's average price "
             + (f"{store['price']:.4f})" if store.get("price") else "n/a)"))
    for k, (t, v) in key_bases(store, sp, programmed_all).items():
        L.append(f"    {k:<11} tokens {t:>14,.1f}   key " + (f"{v:,.2f}x" if v is not None else "n/a")
                 if t is not None else f"    {k:<11} n/a (the store's claims are missing, or fewer than the claims "
                                       f"read - a store older than the window?)")
    return L


def main(argv=None) -> int:
    cfg = _cfg()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="frm", default=cfg["window"][0])
    ap.add_argument("--to", default=cfg["window"][1])
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--apply", action="store_true", help="read the events (BigQuery, under the shared budget)")
    ap.add_argument("--recorded", action="store_true", help="use the live read recorded in config, no BigQuery")
    ap.add_argument("--max-gb", type=float, default=MAX_GB)
    a = ap.parse_args(argv)
    prog = cfg["programmed"]
    recorded_window = a.recorded or (a.frm, a.to) == tuple(cfg["window"])
    # Merkl's programmed amounts are recorded for config's window only: another window has none (unknown, never 0)
    programmed_all = sum(prog[k] for k in ("ethereum", "base", "katana", "arbitrum")) if recorded_window else None
    programmed_eth = prog["ethereum"] if recorded_window else None
    if a.recorded:
        store = store_inputs(Path(a.db), *cfg["window"])
        c = cfg["claimed"]
        events = [{"day": cfg["window"][0], "block": 0, "who": k, "event": "Claimed", "amount": v} for k, v in c.items()]
        print(f"RECORDED READ ({cfg['read_on']}, {cfg['window'][0]}..{cfg['window'][1]}; config.MORPHO_CLAIMS_SPLIT) - "
              "no BigQuery")
        sp = dict(split(events, programmed_eth), claims=sum(cfg["n_claims"].values()))
        print("\n".join(report(sp, store, programmed_all, *cfg["window"])))
        return 0
    store = store_inputs(Path(a.db), a.frm, a.to)
    from fetch.base import FetchOutput
    from fetch.eth_settlement import SQL, EthP2PBigQuery
    r = EthP2PBigQuery()
    bq = r._bq_spec()
    client, why = r._client(bq)
    if client is None:
        print(f"NOT RUN - {why}")
        return 2
    sql = (SQL / cfg["sql"]).read_text()
    params = query_params(cfg, a.frm, a.to)
    est = r._dry(client, sql, params, bq)
    print(f"MORPHO CLAIMS {a.frm}..{a.to}: dry run {est / 1e9:,.1f} GB (no data read, no quota)")
    if not a.apply:
        print("  nothing read (add --apply to run this one query under the shared monthly budget)")
        return 0
    if est > a.max_gb * 1e9:
        print(f"  REFUSED - the dry run is {est / 1e9:,.1f} GB, above the {a.max_gb:,.0f} GB this run allows "
              "(--max-gb). Nothing read.")
        return 3
    st, out = r._load(), FetchOutput()
    rows = r._run(client, "morpho-claims", sql, params, bq, st, out, "Morpho", backfill=True)
    for ev in getattr(out, "log", []):
        print(f"  {ev.status}: {ev.message}")
    if rows is None:
        return 2
    sp = split(parse_rows(rows, cfg), programmed_eth)
    print("\n".join(report(sp, store, programmed_all, a.frm, a.to)))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"claims_split_{a.frm}_{a.to}.json"
    path.write_text(json.dumps({"window": [a.frm, a.to], "dry_run_bytes": est, "split": sp, "store": store,
                                "programmed": prog, "keys": {k: list(v) for k, v in
                                                             key_bases(store, sp, programmed_all).items()}},
                               indent=1, default=str))
    print(f"  written to {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return 0


if __name__ == "__main__":
    import progress  # noqa: E402
    progress.line_buffered()
    sys.exit(main())
