#!/usr/bin/env python3
"""
supply_components.py — every term of a project's circulating supply and free float, printed (Jake, 2026-10-06
overnight A1-A3: "Print every component ... and find the double subtraction or mis-sized term").

    python supply_components.py Aerodrome Pendle Sky
    python supply_components.py                         every project with an on-chain set

From metrics.db, with the workbook's read-time views applied (the on-chain circulating is one): the on-chain total,
each subtracted balance (and its per-wallet composition from the latest run log line), the declared exclusions,
the locked legs subtracted for free float, which circulating the ratios use, our free float, CoinGecko's figures,
and the gap. Writes nothing. ASCII output.
"""
from __future__ import annotations

import re
import sys

import config


def _fmt(v) -> str:
    return "-" if v is None else f"{v:,.0f}"


def _composition(runlog, project: str, metric: str) -> str:
    """The latest run-log detail naming each component of a summed balance."""
    if runlog is None or runlog.empty:
        return ""
    hits = runlog[(runlog["project"] == project) & runlog["message"].astype(str).str.contains(metric + "=", regex=False)]
    if hits.empty:
        return ""
    msg = str(hits.iloc[-1]["message"])
    m = re.search(r"from \d+ components: (.*)", msg)
    return m.group(1)[:600] if m else ""


def report(name: str, rows: dict, runlog) -> list[str]:
    spec = config.circulating_onchain(name) or {}
    def now(m):
        v = (rows.get(f"{name}|{m}") or {}).get("now")
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if v != v else v
    date = lambda m: (rows.get(f"{name}|{m}") or {}).get("latest_date")  # noqa: E731
    primary = config.circulating_primary_metric(name)
    pol = config.CIRCULATING_POLICY.get(name) or {}
    out = [f"=== {name}: status {spec.get('status')}, ratios use {primary}"
           f"{', CoinGecko basis = FREE FLOAT' if config.coingecko_is_free_float(name) else ''}"]
    if pol:   # THE POLICY (2026-10-07): the figure in use, its definition, any staked add-back, what it replaced
        out += [f"  policy: {pol['source']}", f"    definition: {pol['definition']}",
                f"    staked: {pol['staked']}" + (f"; add-back: {pol['add_back']}" if pol.get("add_back") else ""),
                f"    previous: {pol['previous']}"]
    if spec.get("total"):
        out.append(f"  total ({spec['total']}): {_fmt(now(spec['total']))}  [{date(spec['total'])}]")
    for m in spec.get("subtract") or ():
        out.append(f"  - {m}: {_fmt(now(m))}  [{date(m)}]")
        comp = _composition(runlog, name, m)
        if comp:
            out.append(f"      components: {comp}")
    for d in spec.get("declared_exclusions") or ():
        out.append(f"  - declared: {d['name'][:70]} = {_fmt(d['tokens'])}"
                   + ("  (LOCKED: also inside the locked total)" if d.get("locked") else ""))
    onchain = now("circulating_supply_onchain")
    out.append(f"  = on-chain circulating (view): {_fmt(onchain)}  [{date('circulating_supply_onchain')}]")
    lock_total, legs = 0.0, []
    for lm in config.free_float_lock_metrics(name):
        v = now(lm)
        legs.append(f"{lm} {_fmt(v)}")
        lock_total += v or 0.0
    x = config.locked_excluded_from_circulating(name) if config.circulating_excludes_declared(name) else 0.0
    out.append(f"  locked for free float: {'; '.join(legs)}" + (f"; less {x:,.0f} already out of circulating" if x else ""))
    cg, cg_total = now("circulating_supply"), now("total_supply")
    circ_used = now(primary) if primary != "circulating_supply" else None
    if circ_used is not None and primary != "circulating_supply_onchain":
        out.append(f"  = {primary}: {_fmt(circ_used)}  [{date(primary)}]")
    if circ_used is None and config.circulating_onchain_primary(name) and config.coingecko_is_free_float(name) and cg:
        circ_used = cg + max(0.0, lock_total - x)
        out.append("  (no on-chain figure today: circulating = CoinGecko + the lock, so free float = CoinGecko)")
    elif circ_used is None:
        circ_used = cg
    ff = None if circ_used is None else circ_used - max(0.0, lock_total - x)
    out.append(f"  circulating the ratios use: {_fmt(circ_used)}   free float: {_fmt(ff)}")
    out.append(f"  CoinGecko: circulating {_fmt(cg)}, total {_fmt(cg_total)}")
    if cg and ff is not None:
        if config.coingecko_counts_total(name):
            cmp_to, what = now(spec["total"]), "our TOTAL (CoinGecko counts every token)"
            out.append(f"  our on-chain set is STRICTER by {_fmt((now(spec['total']) or 0) - (onchain or circ_used or 0))} "
                       f"({' + '.join(spec.get('subtract') or ())})")
        elif config.coingecko_is_free_float(name) and config.coingecko_counted_lock_legs(name):
            legs = config.coingecko_counted_lock_legs(name)
            add = sum(now(m) or 0.0 for m in legs)
            cmp_to, what = ff + add, f"our free float + {' + '.join(legs)} (CoinGecko still counts them)"
            out.append(f"  ours is STRICTER by {_fmt(add)} ({' + '.join(legs)}): Pendle-style documented method")
        elif config.coingecko_is_free_float(name):
            cmp_to, what = ff, "our free float"
        else:
            cmp_to, what = circ_used, "our circulating"
        out.append(f"  GAP: CoinGecko vs {what}: {cg / cmp_to - 1:+.2%} ({cg - cmp_to:+,.0f})")
    if primary != "circulating_supply_onchain" and onchain and circ_used:
        # THE POLICY'S CROSS-CHECK: the figure in use against our on-chain set (beyond tolerance -> review it)
        out.append(f"  CROSS-CHECK: on-chain set vs the figure in use ({primary}): {onchain / circ_used - 1:+.2%} "
                   f"({onchain - circ_used:+,.0f})")
    return out


def main(argv=None) -> int:
    import store as store_mod
    from pathlib import Path
    from build_workbook import aggregate, scoped_projects  # noqa: F401
    args = list(sys.argv[1:] if argv is None else argv)
    if not Path(store_mod.DB_PATH).exists():
        print(f"no {store_mod.DB_PATH} — run token_metrics.py first")
        return 1
    import pandas as pd
    st = store_mod.Store(store_mod.DB_PATH)
    try:
        long, status, runlog = st.load_long(), st.fetch_status(), st.run_log()
    finally:
        st.close()
    asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    data = aggregate(long, status, asof)
    rows = {r["key"]: r for r in data.to_dict("records")}
    names = args or [n for n, s in config.CIRCULATING_ONCHAIN.items() if s.get("status") in ("established", "partial")]
    for n in names:
        print("\n".join(report(n, rows, runlog)).encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
