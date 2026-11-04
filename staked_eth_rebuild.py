"""staked_eth_rebuild.py — Ethereum's staked ETH for the days the beacon node cannot read back (SPEC P5-1.12).

    python staked_eth_rebuild.py              TEST ONLY: rebuild, compare with validatorqueue, print; stores nothing
    python staked_eth_rebuild.py --apply      the same, and ONLY IF the test passed, store the rebuilt days

Runs on Jake's machine (BigQuery on his Application Default Credentials, NEAR's quota project and ledger — the same
client, guard, dry run and monthly budget as the Ethereum P2P leg). Reads metrics.db read-only unless --apply passes.

1. THE ANCHOR, live: one public beacon node (config Ethereum.staked_eth_node), the finalized state — Σ actual validator
   balances B_A and Σ pending deposits Q_A at the same slot (the slot is read before and after each sum).
2. THE FLOWS, BigQuery (sql/ethereum/bigquery_staked_flows_daily.sql), every UTC day from Electra (2025-05-07) to the
   anchor: deposits (DepositEvent logs), withdrawals (blocks.withdrawals). Dry run first; refused over the per-query cap
   or the month's budget; refused when the dataset ends before the anchor or a deposit log has an unexpected layout.
3. THE ROLL BACK: L = B + Q (the lock) at the anchor; L(day before) = L(day) − deposits + withdrawals − consensus
   rewards (spec maximum, fetch.staked_eth.cl_reward_eth). B(d) = L(d) − Q(d), Q simulated forward from Electra (empty
   at the fork) at 256 ETH an epoch; the simulated queue at the anchor is printed against the measured one.
4. THE TEST (Jake: "use it only if it agrees within ±1% on the overlap"): every validatorqueue day 2025-07-28..2026-10-25
   stored as beacon_chain_eth (validatorqueue:staked_amount) against the rebuild — at least 455 days, EVERY one within
   ±1%. Printed: days compared, mean and worst gap, every day outside. The consensus-reward model is printed against
   the stored Eth2Staking deltas where both exist.
5. --apply AND PASSED: beacon_chain_eth_primary rows for the overlap days and from 2026-10-26 to the day before the
   first beacon-node row (never on a day the node holds), source bigquery_eth:staked_rebuild[...]. Otherwise nothing.
Exit code: 0 passed (and stored with --apply), 1 not passed / refused, 2 a usage or setup problem.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch import staked_eth as se  # noqa: E402
from fetch.base import _measuring_point  # noqa: E402

VQ_POINT = "validatorqueue:staked_amount"


def spec() -> dict:
    return config.PROJECT_BY_NAME["Ethereum"]["staked_eth_rebuild"]


def stored(db: Path) -> dict:
    """{"vq": {day: ETH}, "first_node_day", "eth2": {day: cumulative}} from metrics.db, read-only."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = "SELECT substr(date,1,10), value, source FROM metrics WHERE project='Ethereum' AND metric=?"
        vq = {pd.Timestamp(d): float(v) for d, v, s in con.execute(q, ("beacon_chain_eth",))
              if _measuring_point(str(s)) == VQ_POINT}
        node = [pd.Timestamp(d) for d, _v, s in con.execute(q, ("beacon_chain_eth_primary",))
                if _measuring_point(str(s)) == config.BEACON_NODE_POINT]
        es = config.PROJECT_BY_NAME["Ethereum"].get("etherscan_supply") or {}
        eth2 = {pd.Timestamp(d): float(v) for d, v, _s in con.execute(q, (es.get("consensus_metric", ""),))}
    finally:
        con.close()
    return {"vq": vq, "first_node_day": min(node) if node else None, "eth2": eth2}


def anchor(reader=None) -> dict:
    """B_A and Q_A at ONE finalized slot (each sum checks its own slot; both must name the same slot — one retry)."""
    node = config.PROJECT_BY_NAME["Ethereum"]["staked_eth_node"]["endpoints"][0]
    r = reader or se.BeaconNodeStakedEth(daily=object())
    for _attempt in range(2):
        s1, bal, n, _nz = r.read(node)
        s2, pend, npend, _ = r.read(node, field="amount", path="pending_deposits")
        if s1 == s2:
            return {"slot": s1, "time": se.slot_time(s1), "balance_eth": bal / 1e9, "pending_eth": pend / 1e9,
                    "entries": n, "pending_entries": npend}
    raise RuntimeError("the finalized slot moved between the balances and the pending-deposits reads twice — refused")


def flows_from_rows(rows: list[dict], anchor_time: pd.Timestamp) -> tuple[dict, tuple, list[str]]:
    """({day: (deposits ETH, withdrawals ETH)} for whole days, (deposits, withdrawals) of the anchor's partial day,
    [refusals]) from the query's rows. A day with blocks and no deposit log is 0 deposits (every block of it was read);
    a missing day, a bad deposit layout or a dataset that ends before the anchor is a refusal, never a 0."""
    why, out = [], {}
    day_a = pd.Timestamp(anchor_time).normalize()
    partial = None
    last_ts = None
    for r in rows:
        d = pd.Timestamp(r["day"]).normalize()
        if int(r.get("bad_layout") or 0):
            why.append(f"{d.date()}: {r['bad_layout']} deposit log(s) without the 8-byte amount layout")
        v = (int(r["deposits_gwei"]) / 1e9, int(r["withdrawals_gwei"]) / 1e9)
        ts = pd.Timestamp(r["last_block_ts"])
        ts = ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts
        last_ts = ts if last_ts is None or ts > last_ts else last_ts
        if d == day_a:
            partial = v
        else:
            out[d] = v
    if not rows:
        why.append("the query returned no day")
        return out, (0.0, 0.0), why
    first = min(out) if out else day_a
    gaps = [d for d in pd.date_range(first, day_a - pd.Timedelta(days=1)) if d not in out]
    if gaps:
        why.append(f"{len(gaps)} day(s) with no block in the dataset: {', '.join(str(g.date()) for g in gaps[:5])}")
    if partial is None or last_ts is None or last_ts < pd.Timestamp(anchor_time) - pd.Timedelta(minutes=10):
        why.append(f"the dataset ends at {last_ts}, before the anchor {pd.Timestamp(anchor_time)} — the flows up to "
                   f"the anchor are not all read; run again when it has caught up")
    return out, partial or (0.0, 0.0), why


def rebuild(anc: dict, flows: dict, partial: tuple, sp: dict | None = None) -> dict:
    """{"lock": {day: L}, "balance": {day: B}, "queue_sim_at_anchor", "queue_measured"} — L rolled back from the anchor,
    B = L − Q_sim (Q simulated forward from Electra, empty at the fork)."""
    sp = sp or spec()
    churn = float(sp["churn_eth_per_epoch"]) * int(sp["epochs_per_day"])
    day_a = pd.Timestamp(anc["time"]).normalize()
    deps = {d: v[0] for d, v in flows.items()}
    q = se.simulate_queue(deps, sp["electra_day"], day_a, churn)
    frac = (pd.Timestamp(anc["time"]) - day_a).total_seconds() / 86400.0
    q_sim_anchor = max(0.0, q[day_a] + partial[0] - churn * frac)
    lock_a = anc["balance_eth"] + anc["pending_eth"]
    lock = se.roll_back(lock_a, anc["time"], flows, partial, sp["electra_day"], int(sp["base_reward_factor"]),
                        int(sp["epochs_per_day"]), queue=q)
    bal = {d: lock[d] - q.get(d, 0.0) for d in lock}
    return {"lock": lock, "balance": bal, "queue_sim_at_anchor": q_sim_anchor, "queue_measured": anc["pending_eth"]}


def reward_check(eth2: dict, balance: dict, sp: dict | None = None) -> str:
    """The consensus-reward model against the stored Eth2Staking deltas (consecutive stored days only)."""
    sp = sp or spec()
    days = sorted(d for d in eth2 if d - pd.Timedelta(days=1) in eth2 and d in balance)
    if not days:
        return "REWARD MODEL: no two consecutive stored Eth2Staking days inside the rebuild — not checked"
    meas = sum(eth2[d] - eth2[d - pd.Timedelta(days=1)] for d in days)
    model = sum(se.cl_reward_eth(balance[d], 1.0, int(sp["base_reward_factor"]), int(sp["epochs_per_day"]))
                for d in days)
    return (f"REWARD MODEL: spec maximum {model:,.0f} ETH vs measured Eth2Staking {meas:,.0f} ETH over {len(days)} "
            f"day(s) ({(model / meas - 1) * 100 if meas else float('nan'):+.1f}%; the model is an upper bound)")


def rows_to_store(balance: dict, first_node_day, anc: dict, test: dict, sp: dict | None = None) -> pd.DataFrame:
    """The rows --apply writes: the overlap days and from the day after the overlap to the day before the first node
    row (or before the anchor's day when the node holds none) — never a day the node holds."""
    sp = sp or spec()
    lo, hi = (pd.Timestamp(x) for x in sp["overlap"])
    stop = (first_node_day if first_node_day is not None else pd.Timestamp(anc["time"]).normalize())
    days = sorted(d for d in balance if lo <= d < stop)
    src = (f"{sp['point']}[anchor slot {anc['slot']} {anc['time']:%Y-%m-%d %H:%M} UTC: Σ balances "
           f"{anc['balance_eth']:,.0f} + pending {anc['pending_eth']:,.0f} ETH; deposits − withdrawals from BigQuery, "
           f"consensus rewards at the spec maximum, queue at 256 ETH/epoch; overlap ±{test['tolerance_pct']:g}% "
           f"PASSED on {test['compared']} days, worst {test['worst_gap']:+.2f}% on {test['worst_day']}]")
    return pd.DataFrame({"date": days, "project": "Ethereum", "metric": sp["metric"],
                         "value": [balance[d] for d in days], "source": src, "tier": 1})


def bigquery_rows(anc: dict) -> list[dict]:
    """The flows query: NEAR's client (pinned project, public dataset only), dry run, cap, month budget, ledger."""
    from fetch.near_bigquery import NearBigQuery
    near = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
    sp = spec()
    bq_spec = {"project": near["project"], "project_env": near["project_env"],
               "allowed_dataset": sp["allowed_dataset"], "max_bytes_per_query": sp["max_bytes_per_query"],
               "monthly_budget_bytes": near["monthly_budget_bytes"]}
    nb = NearBigQuery()
    client, why = nb._client(bq_spec)
    if client is None:
        raise RuntimeError(f"BigQuery not available: {why}")
    bq = nb._bq
    sql = (ROOT / sp["sql"]).read_text(encoding="utf-8")
    nb._guard_sql(sql, bq_spec)
    params = [bq.ScalarQueryParameter("d0", "DATE", sp["electra_day"]),
              bq.ScalarQueryParameter("anchor", "TIMESTAMP", pd.Timestamp(anc["time"]).tz_localize("UTC").isoformat())]
    dry = client.query(sql, job_config=bq.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params))
    est = int(dry.total_bytes_processed or 0)
    st = nb._load()
    st.setdefault("ledger", {})
    month = str(pd.Timestamp.now("UTC").date())[:7]
    used = int(st["ledger"].get(month, 0))
    print(f"BIGQUERY: dry run {est / 1e9:,.2f} GB; {month} so far {used / 1e9:,.1f} of "
          f"{int(bq_spec['monthly_budget_bytes']) / 1e9:,.0f} GB; per-query cap {sp['max_bytes_per_query'] / 1e9:,.0f} GB")
    if est > int(sp["max_bytes_per_query"]):
        raise RuntimeError(f"dry run {est / 1e9:,.1f} GB is over the per-query cap — NOT RUN")
    if used + est > int(bq_spec["monthly_budget_bytes"]):
        raise RuntimeError(f"dry run {est / 1e9:,.1f} GB would take {month} past the budget — NOT RUN; next month")
    job = client.query(sql, job_config=bq.QueryJobConfig(query_parameters=params,
                                                         maximum_bytes_billed=nb.billing_cap(est)))
    rows = [dict(r.items()) for r in job.result()]
    billed = int(getattr(job, "total_bytes_billed", None) or est)
    st["ledger"][month] = used + billed
    nb._save(st)
    print(f"BIGQUERY: {len(rows)} day row(s); {billed / 1e9:,.2f} GB billed")
    return rows


def main(argv=None, reader=None, bq_rows=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--apply", action="store_true", help="store the rebuilt days, only if the test passed")
    a = ap.parse_args(argv)
    db = Path(a.db)
    if not db.exists():
        print(f"no store at {db}")
        return 2
    sp = spec()
    have = stored(db)
    anc = anchor(reader)
    print(f"ANCHOR: finalized slot {anc['slot']} ({anc['time']:%Y-%m-%d %H:%M} UTC): Σ balances "
          f"{anc['balance_eth']:,.0f} ETH over {anc['entries']:,} validators; pending deposits "
          f"{anc['pending_eth']:,.0f} ETH ({anc['pending_entries']:,})")
    try:
        rows = bq_rows(anc) if bq_rows else bigquery_rows(anc)
    except Exception as e:  # noqa: BLE001
        print(f"NOT REBUILT — {type(e).__name__}: {str(e)[:300]}. Nothing stored.")
        return 1
    flows, partial, why = flows_from_rows(rows, anc["time"])
    if why:
        print("NOT REBUILT — " + "; ".join(why) + ". Nothing stored.")
        return 1
    rb = rebuild(anc, flows, partial, sp)
    print(f"QUEUE: simulated {rb['queue_sim_at_anchor']:,.0f} ETH at the anchor vs measured {rb['queue_measured']:,.0f}"
          f" ETH ({rb['queue_sim_at_anchor'] - rb['queue_measured']:+,.0f})")
    test = se.overlap_test(rb["balance"], have["vq"], sp["overlap"][0], sp["overlap"][1], float(sp["tolerance_pct"]),
                           int(sp["min_days"]))
    print(reward_check(have["eth2"], rb["balance"], sp))
    print(f"OVERLAP TEST {'PASSED' if test['passed'] else 'NOT PASSED'}: {test['compared']} day(s) compared "
          f"{sp['overlap'][0]}..{sp['overlap'][1]} (need {test['min_days']}, every one within ±{test['tolerance_pct']:g}%)"
          + (f"; mean gap {test['mean_gap']:+.2f}%, worst {test['worst_gap']:+.2f}% on {test['worst_day']}"
             if test["compared"] else "")
          + (f"; {len(test['outside'])} day(s) OUTSIDE: {', '.join(test['outside'][:20])}"
             + (" ..." if len(test["outside"]) > 20 else "") if test["outside"] else "")
          + (f"; {len(test['missing'])} validatorqueue day(s) with no rebuild" if test["missing"] else ""))
    if not test["passed"]:
        print("NOT ADOPTED — nothing stored (SPEC P5-1.12: the rebuild is used only within ±1% on the overlap).")
        return 1
    out = rows_to_store(rb["balance"], have["first_node_day"], anc, test, sp)
    after = out[pd.to_datetime(out["date"]) > pd.Timestamp(sp["overlap"][1])]
    print(f"ROWS: {len(out)} ({len(out) - len(after)} overlap day(s), {len(after)} backfilled day(s)"
          + (f" {after['date'].min():%Y-%m-%d}..{after['date'].max():%Y-%m-%d}" if len(after) else "") + ")")
    if not a.apply:
        print("TEST ONLY — nothing stored. Run with --apply to store these rows.")
        return 0
    import store as sm
    st = sm.Store(db)
    try:
        n = st.upsert(out)
    finally:
        st.close()
    print(f"STORED {n} row(s) as beacon_chain_eth_primary ({sp['point']}).")
    return 0


if __name__ == "__main__":
    import progress  # noqa: E402 - stdout line-buffered when piped (the entry-point rule)
    progress.line_buffered()
    sys.exit(main())
