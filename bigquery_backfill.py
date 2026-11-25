#!/usr/bin/env python3
"""bigquery_backfill.py - NEAR's and Ethereum's P2P history in ONE BILLED GO (Round 22, item 7; Jake 2026-11-25).

    python bigquery_backfill.py plan       the chunks and their ESTIMATED GB and cost, from the state. No network.
    python bigquery_backfill.py dry-run    every chunk's BigQuery DRY RUN (free): GB and $ per chunk and in total
    python bigquery_backfill.py run        refuses unless billing is on; dry-runs everything first, then reads

JAKE: "If Jake enables billing on near-data-510309, both histories fill in one go: NEAR back to the 2024-12-10 floor
(~2.7 TB), and Ethereum 2024-11-04 to 2026-05-31 (~0.8 TB). Write a single command that refuses unless billing is on. It
must set maximum_bytes_billed per query and total no more than 4 TB, about $25. It dry-runs first and prints the cost,
and the run is chunked and resumable. If billing stays off, the free-tier schedule in docs/r19_bigquery_plan.md stands."

OPTIONAL. Nothing schedules it. With billing off, BIGQUERY_SCHEDULE and near_p2p_history.py stand as they are.

REFUSES UNLESS BILLING IS ON - both of:
  1. config.BIGQUERY_BILLING: billing_enabled True, confirmed_by, confirmed_on (a date, not after today) - Jake's flag;
  2. the Cloud Billing API's projects.getBillingInfo for near-data-510309 answers billingEnabled = true, asked with
     Jake's Application Default Credentials. When that check cannot be made (library missing, no credentials, API not
     enabled, no permission) the run is REFUSED and says why - the flag alone is never taken as billing.
THE BYTES: every chunk is dry-run first (free). Each query's maximum_bytes_billed is its dry run x 1.05 + 10 MB
(NearBigQuery.billing_cap: billing rounds up), never above the reader's per-query cap (NEAR 400 GB, Ethereum 60 GB; a
chunk over it is halved until it fits) and never above what the 4 TB total has left. The run is refused before any
billed query when what the one-shot already billed + the sum of every planned maximum_bytes_billed is over
config.BIGQUERY_ONESHOT.total_cap_gb (4,000 GB, decimal, as the ledger counts) - so BigQuery cannot bill more than that.
THE CHUNKS (ranges from config.BIGQUERY_ONESHOT): NEAR - NEAR_P2P_HISTORY's calendar months back to its floor
(near_p2p_history.next_chunk, the days just before the stored run, never a held day); Ethereum - calendar months of the
days not yet held between its from and to. A chunk is kept whole or not at all (NearBigQuery.history_chunk; the same
rule here for Ethereum: every day with its native-ETH row).
RESUMABLE: the state is the BigQuery state file the readers already keep (<logcache>/near-bq.json, written whole by a
rename): the held days themselves (a rerun plans only days not held) and st["oneshot"]["attempts"] - one record per
billed query, written BEFORE the query as "pending" with its maximum_bytes_billed and after it as done / failed /
not_run with the bytes billed. A pending record found at the start (a run killed mid-query) counts at its maximum
against the 4 TB. The one-shot's bytes go to st["ledger_billed"], not to the free-tier ledger, so the daily reads keep
their month.
THE STORE: `run` holds the store lock (storelock.hold + runlock.RunLock, as archive_backfill.py) for its whole length,
so no daily run writes the same state file meanwhile. It writes no metrics.db row: the next
`python token_metrics.py --project Near` and `--project Ethereum` value the days and store them (under the same lock).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import near_p2p_history as nh

ROOT = Path(__file__).resolve().parent
TIB = 1024 ** 4
EXIT_REFUSED = 3
_day, _days = nh._day, nh._days


# ----- money ----------------------------------------------------------------------------------------------------------
def cost_usd(n_bytes: float, price_per_tib: float) -> float:
    """$ at Google's on-demand price per TiB, before the month's free 1 TiB (an upper bound)."""
    return float(n_bytes) / TIB * float(price_per_tib)


def money(n_bytes: float, cfg: dict) -> str:
    return f"{n_bytes / 1e9:,.1f} GB = {n_bytes / TIB:,.3f} TiB = ${cost_usd(n_bytes, cfg['price_usd_per_tib']):,.2f}"


# ----- the chunks (pure) ----------------------------------------------------------------------------------------------
def near_chunks(held, floor, min_days: int = 7, limit: int = 240) -> list:
    """NEAR's chunks back to the floor, newest first: near_p2p_history.next_chunk again and again, each time as if the
    chunk before were held. Empty when no day is held (the daily run's own read comes first)."""
    sim = {_day(d) for d in held} - {None}
    out = []
    for _ in range(int(limit)):
        c = nh.next_chunk(sim, floor, int(min_days))
        if c is None:
            break
        out.append(c)
        sim.update(_days(*c))
    return out


def eth_chunks(held, d0, d1) -> list:
    """Ethereum's chunks: the runs of days in [d0, d1] not held, cut at each calendar month, oldest first."""
    have = {_day(d) for d in held}
    out: list = []
    for d in _days(_day(d0), _day(d1)):
        if d in have:
            continue
        if out and out[-1][1] == d - dt.timedelta(days=1) and (out[-1][0].year, out[-1][0].month) == (d.year, d.month):
            out[-1][1] = d
        else:
            out.append([d, d])
    return [tuple(c) for c in out]


def halves(c) -> tuple:
    d0, d1 = c
    mid = d0 + dt.timedelta(days=((d1 - d0).days + 1) // 2 - 1)
    return (d0, mid), (mid + dt.timedelta(days=1), d1)


def fit(chunk, dry, per_query_cap: int) -> list:
    """[(d0, d1, dry bytes)]: the chunk's dry run, halved while it is over the per-query cap (a single day over the cap
    is returned as it is; the caller refuses it)."""
    n = int(dry(chunk))
    if n <= int(per_query_cap) or chunk[0] == chunk[1]:
        return [(chunk[0], chunk[1], n)]
    a, b = halves(chunk)
    return fit(a, dry, per_query_cap) + fit(b, dry, per_query_cap)


def max_billed(dry_bytes: int, per_query_cap: int) -> int:
    from fetch.near_bigquery import NearBigQuery
    return min(NearBigQuery.billing_cap(int(dry_bytes)), int(per_query_cap))


# ----- the one-shot's own record --------------------------------------------------------------------------------------
def attempts(st: dict) -> list:
    return st.setdefault("oneshot", {}).setdefault("attempts", [])


def spent_bytes(st: dict) -> int:
    """What the one-shot has billed so far: each finished query's bytes billed; a query whose end was never recorded
    (pending / interrupted) at its maximum_bytes_billed - what BigQuery may have billed for it."""
    n = 0
    for a in (st.get("oneshot") or {}).get("attempts") or []:
        n += int(a.get("max_bytes") or 0) if a.get("status") in ("pending", "interrupted") \
            else int(a.get("billed_bytes") or 0)
    return n


def mark_interrupted(st: dict) -> int:
    k = 0
    for a in attempts(st):
        if a.get("status") == "pending":
            a["status"] = "interrupted"
            k += 1
    return k


def cap_refusal(st: dict, planned: list, cfg: dict) -> str | None:
    """None when what the one-shot already billed + every planned maximum_bytes_billed fits the total cap."""
    cap = int(float(cfg["total_cap_gb"]) * 1e9)
    before, plan = spent_bytes(st), sum(int(p["max_bytes"]) for p in planned)
    if before + plan > cap:
        return (f"the total would be over the {float(cfg['total_cap_gb']):,.0f} GB cap: already billed by the one-shot "
                f"{before / 1e9:,.1f} GB + the planned maximum_bytes_billed {plan / 1e9:,.1f} GB = "
                f"{(before + plan) / 1e9:,.1f} GB - a new decision for Jake (config.BIGQUERY_ONESHOT.total_cap_gb)")
    return None


# ----- billing ----------------------------------------------------------------------------------------------------------
def flag_refusal(flag: dict, today) -> str | None:
    """Jake's dated flag (config.BIGQUERY_BILLING)."""
    f = flag or {}
    how = (f"set config.BIGQUERY_BILLING billing_enabled True, confirmed_by and confirmed_on (YYYY-MM-DD) once billing "
           f"is enabled on {f.get('project')}")
    if f.get("billing_enabled") is not True:
        return f"billing is not confirmed on {f.get('project')} (config.BIGQUERY_BILLING.billing_enabled) - {how}"
    on = _day(f.get("confirmed_on"))
    if not str(f.get("confirmed_by") or "").strip() or on is None:
        return f"the billing flag is not complete (confirmed_by and a confirmed_on date) - {how}"
    if on > _day(today):
        return f"the billing flag is dated {on}, after today ({_day(today)})"
    return None


def billing_check(project: str) -> tuple:
    """(True | False | None, how) - the Cloud Billing API's projects.getBillingInfo `billingEnabled` for `project`, with
    Application Default Credentials. None when the check cannot be made (and why)."""
    name = f"projects/{project}"
    try:
        from google.cloud import billing_v1                 # google-cloud-billing
        info = billing_v1.CloudBillingClient().get_project_billing_info(name=name)
        return bool(info.billing_enabled), f"Cloud Billing API (google-cloud-billing) {name}: billingEnabled"
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        return None, f"the Cloud Billing API call failed: {type(e).__name__}: {str(e)[:200]}"
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        return None, "neither google-cloud-billing nor google-auth[requests] is installed (pip install google-cloud-billing)"
    try:
        creds, _p = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"])
        r = AuthorizedSession(creds).get(f"https://cloudbilling.googleapis.com/v1/{name}/billingInfo", timeout=30)
        if r.status_code != 200:
            return None, f"the Cloud Billing API answered HTTP {r.status_code}: {r.text[:200]}"
        return bool(r.json().get("billingEnabled")), f"Cloud Billing API (REST) {name}/billingInfo: billingEnabled"
    except Exception as e:  # noqa: BLE001
        return None, f"the Cloud Billing API call failed: {type(e).__name__}: {str(e)[:200]}"


def billing_refusal(flag: dict, today, check=None) -> str | None:
    """None only when Jake's flag is complete AND the runtime check says billingEnabled = true."""
    why = flag_refusal(flag, today)
    if why:
        return why
    project = (flag or {}).get("project")
    ok, how = (check or billing_check)(project)
    if ok is None:
        return (f"the billing check could not be made ({how}) - refused: the flag alone is not taken as billing. "
                f"Enable the API (gcloud services enable cloudbilling.googleapis.com --project {project}) or install "
                f"google-cloud-billing, then run again")
    if ok is not True:
        return f"billing is OFF on {project} ({how} = false) although config.BIGQUERY_BILLING says it is on"
    return None


# ----- the readers ----------------------------------------------------------------------------------------------------
def readers(**kw) -> tuple:
    """(NearBigQuery, EthP2PBigQuery) on ONE state file. Tests pass client=, bq=, cache_file=."""
    from fetch.eth_settlement import EthP2PBigQuery
    from fetch.near_bigquery import NearBigQuery
    return NearBigQuery(**kw), EthP2PBigQuery(**kw)


def _ctx():
    import config
    from fetch.eth_settlement import SQL as ETH_SQL
    from fetch.eth_settlement import _spec
    from fetch.near_bigquery import _sql
    near_spec = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
    p2p = _spec()["p2p"]
    return {"config": config, "cfg": config.BIGQUERY_ONESHOT, "near_spec": near_spec, "p2p": p2p,
            "near_sql": _sql(config.NEAR_P2P_HISTORY["sql"]), "eth_sql": (ETH_SQL / p2p["sql"]).read_text(),
            "eth_tokens": [t[1] for t in p2p["tokens"]], "near_hist": config.NEAR_P2P_HISTORY}


def plan_chunks(st: dict, ctx: dict, eth) -> tuple:
    """([{"chain", "d0", "d1"}], [notes]) from the state: NEAR's then Ethereum's."""
    cfg, notes, out = ctx["cfg"], [], []
    near_held = list(st.get("days") or {})
    if not near_held:
        notes.append("NEAR: no P2P day is held in the state - the daily run's own read comes first "
                     "(`python token_metrics.py --project Near`); NEAR is not read this time")
    elif not (st.get("tokens") or {}).get("list"):
        notes.append("NEAR: no token census in the state - run `python token_metrics.py --project Near` first")
    else:
        for c in near_chunks(near_held, cfg["near"]["from"], int(ctx["near_hist"]["min_chunk_days"])):
            out.append({"chain": "Near", "d0": c[0], "d1": c[1]})
    held = eth.held_days(st.get("eth_p2p") or {})
    for c in eth_chunks(held, cfg["ethereum"]["from"], cfg["ethereum"]["to"]):
        out.append({"chain": "Ethereum", "d0": c[0], "d1": c[1]})
    return out, notes


def held_back(chunks: list, ctx: dict, db: Path) -> tuple:
    """(chunks the store can value, [lines]): a chunk over days without the store's price (and NEAR's DEX leg) would
    bill and form nothing - it is held back. Raises sqlite3.Error when the store cannot be read."""
    keep, lines = [], []
    for c in chunks:
        sub = ctx["cfg"]["near" if c["chain"] == "Near" else "ethereum"]
        gaps = nh.store_gaps(db, c["d0"], c["d1"], tuple(sub["store_needs"]), project=sub["project"])
        if gaps:
            lines.append(f"HELD BACK {c['chain']} {c['d0']}..{c['d1']}: the store holds no " + " and no ".join(
                f"{m} on {len(v)} day(s) ({v[0]}..{v[-1]})" for m, v in gaps.items()))
        else:
            keep.append(c)
    return keep, lines


def _spec_for(chain: str, ctx: dict, eth) -> dict:
    return ctx["near_spec"] if chain == "Near" else eth._bq_spec()


def _sql_params(c: dict, st: dict, ctx: dict) -> tuple:
    toks = (st.get("tokens") or {}).get("list") if c["chain"] == "Near" else ctx["eth_tokens"]
    return (ctx["near_sql"] if c["chain"] == "Near" else ctx["eth_sql"]), {"d0": c["d0"], "d1": c["d1"], "tokens": toks}


def dry_all(chunks: list, st: dict, ctx: dict, nb, eth, clients: dict, say=print) -> tuple:
    """([planned], refusal or None): every chunk dry-run (free), halved to fit its per-query cap; each planned chunk
    carries its dry bytes and its maximum_bytes_billed. Prints a line per chunk and the totals."""
    planned, bad = [], []
    cfg = ctx["cfg"]
    for c in chunks:
        r = nb if c["chain"] == "Near" else eth
        spec = _spec_for(c["chain"], ctx, eth)
        cap = int(spec["max_bytes_per_query"])

        def dry(span, c=c, r=r, spec=spec):
            sql, params = _sql_params({**c, "d0": span[0], "d1": span[1]}, st, ctx)
            return r._dry(clients[c["chain"]], sql, params, spec)
        for d0, d1, n in fit((c["d0"], c["d1"]), dry, cap):
            p = {"chain": c["chain"], "d0": d0, "d1": d1, "days": (d1 - d0).days + 1, "dry_bytes": int(n),
                 "max_bytes": max_billed(n, cap), "per_query_cap": cap}
            if n > cap:
                bad.append(f"{c['chain']} {d0}: one day dry-runs {n / 1e9:,.1f} GB, over the {cap / 1e9:,.0f} GB "
                           f"per-query cap")
            planned.append(p)
            say(f"  {p['chain']:<9} {d0}..{d1} {p['days']:>3} d  dry run {n / 1e9:>8,.2f} GB  "
                f"${cost_usd(n, cfg['price_usd_per_tib']):>6,.2f}  maximum_bytes_billed {p['max_bytes'] / 1e9:,.2f} GB")
    for chain in ("Near", "Ethereum"):
        sel = [p for p in planned if p["chain"] == chain]
        if sel:
            say(f"  {chain}: {len(sel)} chunk(s), {min(p['d0'] for p in sel)}..{max(p['d1'] for p in sel)} - dry runs "
                f"{money(sum(p['dry_bytes'] for p in sel), cfg)}")
    tot = sum(p["dry_bytes"] for p in planned)
    worst = sum(p["max_bytes"] for p in planned)
    say(f"  TOTAL dry runs {money(tot, cfg)}; the most BigQuery may bill (every maximum_bytes_billed) {money(worst, cfg)}"
        f"; already billed by the one-shot {spent_bytes(st) / 1e9:,.1f} GB; cap {float(cfg['total_cap_gb']):,.0f} GB")
    say(f"  price: ${cfg['price_usd_per_tib']} per TiB ({cfg['price_source']}); the month's first 1 TiB is free if "
        f"the daily reads have not used it, so the bill is at most the figure above")
    if bad:
        return planned, "; ".join(bad)
    return planned, cap_refusal(st, planned, cfg)


# ----- one chunk, billed ------------------------------------------------------------------------------------------------
def eth_history_chunk(eth, client, bq: dict, st: dict, out, ctx: dict, d0, d1, hard_cap: int) -> dict | None:
    """Ethereum's chunk as NearBigQuery.history_chunk does NEAR's: no held day twice, every day with its native-ETH row
    or nothing kept."""
    e = st.setdefault("eth_p2p", {"days": {}, "prices": {}})
    e.setdefault("days", {})
    e.setdefault("prices", {})
    days = [str(d) for d in _days(_day(d0), _day(d1))]
    what = f"Ethereum p2p HISTORY {days[0]}..{days[-1]}"
    twice = sorted(set(days) & eth.held_days(e))
    if twice:
        out.skipped("eth_settlement", "Ethereum", f"{what}: {len(twice)} day(s) already held - NOT RUN", 1)
        return None
    sql, params = _sql_params({"chain": "Ethereum", "d0": _day(d0), "d1": _day(d1)}, st, ctx)
    rows = eth._run(client, what, sql, params, bq, st, out, "Ethereum", hard_cap=int(hard_cap), billed=True)
    if rows is None:
        return None
    got: dict = {}
    for r in rows:
        got.setdefault(str(_day(r["day"])), {})[str(r["token"])] = str(r["amount"])
    outside = sorted(k for k in got if k not in set(days))
    short = [d for d in days if "ETH" not in got.get(d, {})]
    if outside or short:
        out.fail("eth_settlement", "Ethereum", f"{what}: NOTHING KEPT - {len(short)} day(s) without a native-ETH row "
                 f"({', '.join(short[:5])}), {len(outside)} row day(s) outside the chunk - kept whole or not at all", 1)
        return None
    for d in days:
        e["days"][d] = got[d]
        e["prices"].pop(d, None)
    eth._save(st)
    return {"days": len(days), "d0": days[0], "d1": days[-1]}


def run_chunk(p: dict, st: dict, ctx: dict, nb, eth, clients: dict, out, today) -> dict:
    """One planned chunk, billed. The attempt is recorded BEFORE the query (pending, its maximum_bytes_billed) and
    after it (done / failed / not_run, the bytes billed), the state saved each time."""
    cap_total = int(float(ctx["cfg"]["total_cap_gb"]) * 1e9)
    hard = min(int(p["max_bytes"]), cap_total - spent_bytes(st))
    a = {"chain": p["chain"], "d0": str(p["d0"]), "d1": str(p["d1"]), "on": str(today), "status": "pending",
         "max_bytes": int(hard), "dry_bytes": int(p["dry_bytes"])}
    if hard <= 0:
        a.update(status="not_run", billed_bytes=0, why="the total cap has nothing left")
        attempts(st).append(a)
        nb._save(st)
        return a
    attempts(st).append(a)
    nb._save(st)
    r = nb if p["chain"] == "Near" else eth
    before = r._run_bytes
    try:
        if p["chain"] == "Near":
            res = nb.history_chunk(clients["Near"], ctx["near_spec"], st, out, "Near", str(p["d0"]), str(p["d1"]),
                                   hard, billed=True)
        else:
            res = eth_history_chunk(eth, clients["Ethereum"], eth._bq_spec(), st, out, ctx, p["d0"], p["d1"], hard)
    except BaseException:
        a["status"] = "interrupted"                         # counted at its maximum: what it billed is not known
        nb._save(st)
        raise
    billed = r._run_bytes - before
    a.update(billed_bytes=int(billed), status="done" if res else ("failed" if billed else "not_run"))
    nb._save(st)
    return a


# ----- commands -------------------------------------------------------------------------------------------------------
def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _clients(nb, eth, ctx: dict) -> tuple:
    c1, why = nb._client(ctx["near_spec"])
    if c1 is None:
        return None, why
    c2, why = eth._client(eth._bq_spec())
    if c2 is None:
        return None, why
    return {"Near": c1, "Ethereum": c2}, None


def cmd_plan(db: Path, rd=None) -> int:
    ctx = _ctx()
    cfg = ctx["cfg"]
    nb, eth = rd or readers()
    st = nb._load()
    chunks, notes = plan_chunks(st, ctx, eth)
    print("BIGQUERY ONE-SHOT - the plan (no network, nothing billed; GB are ESTIMATES, the dry run replaces them)")
    for n in notes:
        print("  " + n)
    tot = 0.0
    for c in chunks:
        sub = cfg["near" if c["chain"] == "Near" else "ethereum"]
        gb = ((c["d1"] - c["d0"]).days + 1) * float(sub["est_gb_per_day"])
        tot += gb
        print(f"  {c['chain']:<9} {c['d0']}..{c['d1']}  ~{gb:,.1f} GB")
    print(f"  TOTAL ~{money(tot * 1e9, cfg)} over {len(chunks)} chunk(s); cap {cfg['total_cap_gb']:,.0f} GB; "
          f"already billed by the one-shot {spent_bytes(st) / 1e9:,.1f} GB")
    why = flag_refusal(ctx["config"].BIGQUERY_BILLING, _today())
    print("  billing: " + ("flag set - `run` also asks the Cloud Billing API" if why is None else why))
    return 0


def cmd_dry_run(db: Path, rd=None) -> int:
    ctx = _ctx()
    nb, eth = rd or readers()
    st = nb._load()
    chunks, notes = plan_chunks(st, ctx, eth)
    for n in notes:
        print("  " + n)
    try:
        chunks, lines = held_back(chunks, ctx, db)
    except sqlite3.Error as e:
        print(f"  the store {db} could not be read ({e}): no chunk is held back for missing prices here")
        lines = []
    for line in lines:
        print("  " + line)
    clients, why = _clients(nb, eth, ctx)
    if clients is None:
        print(f"NOT RUN - {why}")
        return 2
    print(f"BIGQUERY ONE-SHOT - DRY RUNS (free, nothing billed): {len(chunks)} chunk(s)")
    _planned, refusal = dry_all(chunks, st, ctx, nb, eth, clients)
    print("  the cap: " + ("fits" if refusal is None else "REFUSED - " + refusal))
    return 0


def cmd_run(db: Path, rd=None, check=None, today=None) -> int:
    ctx = _ctx()
    now = _day(today) or _today()
    why = billing_refusal(ctx["config"].BIGQUERY_BILLING, now, check)
    if why:
        print(f"BIGQUERY ONE-SHOT REFUSED - {why}. Nothing billed; no BigQuery call was made. With billing off the "
              f"free-tier schedule (docs/r19_bigquery_plan.md, config.BIGQUERY_SCHEDULE) stands.")
        return EXIT_REFUSED
    import storelock
    from runlock import EXIT_LOCKED, RunLock, RunLocked
    try:
        with storelock.hold("bigquery_backfill run", path=storelock.lock_path(db)):
            try:
                with RunLock(db, "bigquery_backfill run"):
                    return _run_locked(db, ctx, rd, now)
            except RunLocked as e:
                print(e.code)
                return EXIT_LOCKED
    except storelock.StoreHeld as e:
        print(e.code)
        return storelock.EXIT_HELD


def _run_locked(db: Path, ctx: dict, rd, now) -> int:
    from fetch.base import FetchOutput
    nb, eth = rd or readers()
    st = nb._load()
    st.setdefault("ledger", {})
    st.setdefault("days", {})
    k = mark_interrupted(st)
    if k:
        nb._save(st)
        print(f"  {k} query(ies) of an earlier run never recorded their end: counted at their maximum_bytes_billed")
    chunks, notes = plan_chunks(st, ctx, eth)
    for n in notes:
        print("  " + n)
    try:
        chunks, lines = held_back(chunks, ctx, db)
    except sqlite3.Error as e:
        print(f"BIGQUERY ONE-SHOT NOT RUN - the store {db} could not be read ({e}): the chunks' prices cannot be "
              f"checked. Nothing billed.")
        return EXIT_REFUSED
    for line in lines:
        print("  " + line)
    if not chunks:
        print("BIGQUERY ONE-SHOT: nothing to read - both histories are held over their ranges. Nothing billed.")
        return 0
    clients, why = _clients(nb, eth, ctx)
    if clients is None:
        print(f"NOT RUN - {why}")
        return 2
    print(f"BIGQUERY ONE-SHOT - DRY RUNS FIRST (free): {len(chunks)} chunk(s)")
    planned, refusal = dry_all(chunks, st, ctx, nb, eth, clients)
    if refusal:
        print(f"BIGQUERY ONE-SHOT REFUSED - {refusal}. Nothing billed.")
        return EXIT_REFUSED
    print(f"BILLING CONFIRMED (config.BIGQUERY_BILLING and the Cloud Billing API); reading {len(planned)} chunk(s)")
    out = FetchOutput()
    done = []
    for i, p in enumerate(planned, 1):
        a = run_chunk(p, st, ctx, nb, eth, clients, out, now)
        done.append(a)
        print(f"  [{i}/{len(planned)}] {a['chain']} {a['d0']}..{a['d1']}: {a['status'].upper()}, "
              f"{int(a.get('billed_bytes') or 0) / 1e9:,.2f} GB billed (maximum_bytes_billed {a['max_bytes'] / 1e9:,.2f} GB)")
    for e in out.log:
        if e.status != "skipped":
            print(f"  {e.status}: {e.message}")
    billed = sum(int(a.get("billed_bytes") or 0) for a in done)
    bad = [a for a in done if a["status"] != "done"]
    print(f"BILLED this run {money(billed, ctx['cfg'])}; the one-shot so far {spent_bytes(st) / 1e9:,.1f} GB of "
          f"{ctx['cfg']['total_cap_gb']:,.0f} GB")
    if bad:
        print(f"{len(bad)} chunk(s) not kept - run `{ctx['cfg']['command']}` again: it reads only the days not held")
    print("next (values and stores the days, under the store lock): `python token_metrics.py --project Near` then "
          "`python token_metrics.py --project Ethereum`")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("plan", "dry-run", "run"))
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    a = ap.parse_args(argv)
    db = Path(a.db)
    if a.mode == "plan":
        return cmd_plan(db)
    if a.mode == "dry-run":
        return cmd_dry_run(db)
    return cmd_run(db)


if __name__ == "__main__":
    import progress
    progress.line_buffered()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
