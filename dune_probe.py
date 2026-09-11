#!/usr/bin/env python3
"""
dune_probe.py — read ONE Dune query, or read back what one Dune query put in the store.

Why this exists: discovering a query's shape used to cost a full run of every tier, and the
answer landed in a spreadsheet cell. Mapping a query is an iterative job — look at the columns,
try a mapping, look at the resulting series, adjust — and it should cost one round trip.

This tool NEVER writes to metrics.db and NEVER rebuilds the workbook. The only thing it writes
is the shape artefact under .cache/dune/, which is plain JSON you can grep.

    python dune_probe.py 8683038                      fetch it, print its shape, save the artefact
    python dune_probe.py Ether.fi/locked_tokens       same, resolving the id from config.py
    python dune_probe.py 8683038 --map day:staked_supply
                                                      dry-run a mapping: the series it WOULD store
    python dune_probe.py 8683038 --map day:tokens_burned,sol_tokens_burned --granularity monthly
                                                      several value columns are summed, as in config
    python dune_probe.py --show 8683038               print a saved artefact, no network, no key
    python dune_probe.py --stored GEODNET/gross_burn_tokens
                                                      what the store actually holds for that series
    python dune_probe.py 2986047 --diagnose            4xx? control-compare "missing" vs "no access"
    python dune_probe.py --config                     every Dune query in config and its map status

DUNE_API_KEY comes from the environment or from .env, exactly as the main run reads it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

import config
from fetch.base import HttpError
from fetch.dune import Dune, SHAPE_DIR, describe_rows, write_shape

ROOT = Path(__file__).resolve().parent

# Column names that mean "this row is dated" — used only to WARN, never to pick a column.
DATEISH = ("day", "date", "month", "week", "time", "period", "block_time", "dt", "ts", "hour")


def load_dotenv():
    """Same .env handling as the main entry point, so the probe needs no separate setup."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve(target: str) -> tuple[int, str, str]:
    """Accept a bare query id, or Project/metric, and return (query_id, project, metric)."""
    if target.isdigit():
        for p in config.PROJECTS:
            for metric, q in (p.get("dune_queries") or {}).items():
                if str(q.get("query_id") or "") == target:
                    return int(target), p["name"], metric
        return int(target), "(not in config)", "(not in config)"
    if "/" not in target:
        sys.exit(f"Give a query id, or Project/metric. Got: {target!r}")
    project, metric = target.split("/", 1)
    p = config.PROJECT_BY_NAME.get(project)
    if not p:
        sys.exit(f"No project named {project!r} in config.py")
    q = (p.get("dune_queries") or {}).get(metric)
    if not q:
        sys.exit(f"{project} has no dune_queries entry for {metric!r}")
    if not q.get("query_id"):
        sys.exit(f"{project}/{metric} has no query_id in config.py — nothing to probe.")
    return int(q["query_id"]), project, metric


def print_shape(shape: dict, artefact: Path | None):
    print(f"\nrows returned: {shape['row_count']}")
    print("\ncolumns:")
    width = max((len(c) for c in shape["columns"]), default=0)
    for col in shape["columns"]:
        print(f"  {col.ljust(width)}  {shape['types'].get(col, '?')}")
    if not shape["columns"]:
        print("  (none — the query returned no rows)")
    print("\nsample rows:")
    for row in shape["sample_rows"]:
        print("  " + json.dumps(row, default=str))
    dateish = [c for c in shape["columns"] if any(d in c.lower() for d in DATEISH)]
    print("\nlooks like a date column: " + (", ".join(dateish) if dateish else
                                            "NONE — if this query has no date, it is a point-in-time "
                                            "snapshot, not a history"))
    if artefact:
        print(f"\nshape written to: {artefact}")


def dry_run_map(rows: list[dict], mapping: str, granularity: str, drop_current: bool):
    """Show the series a mapping WOULD produce. Nothing is stored."""
    if ":" not in mapping:
        sys.exit("--map takes date_col:value_col[,value_col...], or :value_col for a snapshot query.")
    date_col, value_part = mapping.split(":", 1)
    value_cols = [c.strip() for c in value_part.split(",") if c.strip()]
    date_col = date_col.strip() or None

    have = {k for r in rows[:50] for k in r}
    missing = [c for c in ([date_col] if date_col else []) + value_cols if c not in have]
    if missing:
        print(f"\n!! the query does not return {missing}. Available: {', '.join(sorted(have))}")
        return

    pairs = []
    for r in rows:
        total, seen = 0.0, False
        for c in value_cols:
            v = r.get(c)
            if v is None:
                continue
            try:
                total += float(v)
                seen = True
            except (TypeError, ValueError):
                pass
        if not seen:
            continue
        pairs.append((r.get(date_col) if date_col else "(run date)", total))

    if not pairs:
        print("\n!! no numeric values came out of that mapping.")
        return

    if not date_col:
        print(f"\nsnapshot mapping — {len(pairs)} row(s), value {pairs[0][1]:,.6g}")
        if len(pairs) > 1:
            print("   !! more than one row: this is a time series, not a snapshot. Find its date column.")
        return

    df = pd.DataFrame(pairs, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_localize(None)
    bad = int(df["date"].isna().sum())
    df = df.dropna(subset=["date"]).sort_values("date")
    print(f"\nmapped series: {len(df)} dated rows"
          + (f" ({bad} row(s) had an unparseable date)" if bad else ""))
    if df.empty:
        return
    print(f"  range: {df['date'].min():%Y-%m-%d} .. {df['date'].max():%Y-%m-%d}")
    print(f"  total: {df['value'].sum():,.6g}   min: {df['value'].min():,.6g}   max: {df['value'].max():,.6g}")
    if drop_current:
        freq = {"monthly": "M", "weekly": "W", "daily": "D"}.get(granularity, "M")
        current = pd.Timestamp.now("UTC").tz_localize(None).to_period(freq)
        n = int((df["date"].dt.to_period(freq) >= current).sum())
        print(f"  drop_current_period would remove {n} incomplete row(s) at {granularity} granularity")
    print("\n  first 5:")
    for _, r in df.head(5).iterrows():
        print(f"    {r['date']:%Y-%m-%d}  {r['value']:,.6g}")
    print("  last 5:")
    for _, r in df.tail(5).iterrows():
        print(f"    {r['date']:%Y-%m-%d}  {r['value']:,.6g}")


def show_stored(target: str, before: str | None):
    """What the store actually holds. A silent success is not a verified one."""
    from store import Store
    qid, project, metric = resolve(target)
    st = Store()
    rows = st.conn.execute(
        "SELECT date, value, source, tier, fetched_at FROM metrics WHERE project=? AND metric=? ORDER BY date",
        (project, metric)).fetchall()
    print(f"\n{project} / {metric}  (Dune query {qid})")
    if not rows:
        print("  NOTHING STORED. The backfill did not land.")
        st.close()
        return
    by_source: dict[str, int] = {}
    for _, _, src, _, _ in rows:
        by_source[src] = by_source.get(src, 0) + 1
    print(f"  rows stored : {len(rows)}")
    print(f"  date range  : {rows[0][0][:10]} .. {rows[-1][0][:10]}")
    print(f"  by source   : " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    dune_rows = [r for r in rows if str(r[2]).startswith("dune:")]
    print(f"  from Dune   : {len(dune_rows)}"
          + (f" ({dune_rows[0][0][:10]} .. {dune_rows[-1][0][:10]})" if dune_rows else ""))

    if before:
        older = [r for r in dune_rows if r[0][:10] < before]
        print(f"  before {before}: {len(older)} row(s) from Dune"
              + ("  <-- the historical snapshot IS reachable" if older else
                 "  <-- NOTHING earlier. Either the snapshot table has gone, or the query never "
                 "covered that period."))

    current = pd.Timestamp.now("UTC").tz_localize(None).to_period("M")
    in_current = [r for r in dune_rows if pd.Timestamp(r[0][:10]).to_period("M") >= current]
    print(f"  current month ({current}) from Dune: {len(in_current)} row(s)"
          + ("  <-- drop_current_period FIRED (no incomplete month stored)" if not in_current
             else "  <-- an incomplete current month IS stored; check drop_current_period"))

    print("\n  first 6 / last 6:")
    for d, v, src, tier, _ in rows[:6]:
        print(f"    {d[:10]}  {v:>18,.6g}  {src}")
    if len(rows) > 12:
        print("    ...")
    for d, v, src, tier, _ in rows[-6:]:
        print(f"    {d[:10]}  {v:>18,.6g}  {src}")

    log = st.conn.execute(
        "SELECT ts, status, rows, message FROM run_log WHERE source='dune' AND project=? "
        "ORDER BY ts DESC LIMIT 6", (project,)).fetchall()
    if log:
        print("\n  most recent Dune run-log lines for this project:")
        for ts, status, n, msg in log:
            print(f"    {ts}  {status:<12} rows={n:<6} {msg}")
    st.close()


# A query id far above anything Dune has issued. Used as a CONTROL: whatever the API says for an
# id that certainly does not exist is the shape of its "does not exist" answer.
CONTROL_ID = 999999999


def diagnose(qid: int):
    """Is a 4xx on this query 'no such query' or 'exists, but not yours'? Two calls, no guessing.

    Asking Dune about one id tells you nothing on its own, because you have nothing to compare the
    answer against — which is exactly why retrying variations is wasted effort. Asking about a
    control id that certainly does not exist, with the same key, in the same minute, turns the
    answer into a comparison: same status and same message means the API does NOT distinguish the
    two cases; different means it does, and the difference is the answer.
    """
    d = Dune()

    def probe(i):
        try:
            rows = d._results(i)
            return ("ok", 200, f"{len(rows)} rows returned")
        except HttpError as e:
            return ("http", e.status, e.detail or "(no message body)")
        except Exception as e:  # noqa: BLE001
            return ("error", 0, str(e))

    print(f"control  query {CONTROL_ID} (certainly does not exist)")
    ck, cs, cd = probe(CONTROL_ID)
    print(f"  -> {cs}  {cd}")
    print(f"target   query {qid}")
    tk, ts_, td = probe(qid)
    print(f"  -> {ts_}  {td}")

    print("\nreading:")
    if tk == "ok":
        print("  the query IS readable with this key. Nothing to diagnose — map its columns.")
    elif cs == ts_ and cd.strip().lower() == td.strip().lower():
        print(f"  the API gives the SAME answer ({ts_}: {td}) for a query that certainly does not exist")
        print("  and for this one. It does NOT distinguish 'no such query' from 'exists but not")
        print("  readable by this key', so non-existence is NOT established — only unreadability is.")
        print("  The remedy is the same either way: open the query in the browser while signed in.")
        print("  If it renders, it exists and the API simply will not serve it to this key — fork it")
        print("  into your own account and use the fork's id. If it 404s in the browser too, it is gone.")
    else:
        print(f"  the answers DIFFER: control {cs} ({cd}) vs target {ts_} ({td}).")
        print("  The API does distinguish the two cases, and the target's answer above is the verdict:")
        print("  a 403-shaped message means it exists and this key cannot read it (fork it);")
        print("  a 404-shaped message matching the control means there is no such query.")


def show_config():
    print(f"{'project':<14} {'metric':<28} {'query':>9}  mapping")
    for p in config.PROJECTS:
        for metric, q in (p.get("dune_queries") or {}).items():
            qid = q.get("query_id")
            if not qid:
                continue
            cols = q.get("value_cols") or ([q["value_col"]] if q.get("value_col") else [])
            if q.get("snapshot") and cols:
                m = f"snapshot -> {'+'.join(cols)}"
            elif q.get("date_col") and cols:
                m = f"{q['date_col']} -> {'+'.join(cols)}"
            else:
                m = "UNMAPPED"
            print(f"{p['name']:<14} {metric:<28} {qid:>9}  {m}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="query id, or Project/metric")
    ap.add_argument("--map", dest="mapping", help="dry-run a mapping: date_col:value_col[,value_col]")
    ap.add_argument("--granularity", default="monthly", choices=["monthly", "weekly", "daily"])
    ap.add_argument("--drop-current", action="store_true", help="show what drop_current_period would remove")
    ap.add_argument("--show", metavar="QUERY_ID", help="print a saved shape artefact; no network")
    ap.add_argument("--stored", metavar="TARGET", help="read back what the store holds for a series")
    ap.add_argument("--before", metavar="YYYY-MM-DD", help="with --stored: count rows older than this")
    ap.add_argument("--config", action="store_true", help="list every Dune query in config and its map status")
    ap.add_argument("--diagnose", action="store_true",
                    help="on a 4xx: compare the target against a control id to see whether Dune "
                         "distinguishes 'does not exist' from 'exists but not readable'")
    ap.add_argument("--json", action="store_true", help="print the shape as raw JSON instead")
    args = ap.parse_args()

    if args.config:
        return show_config()
    if args.show:
        path = SHAPE_DIR / f"query-{args.show}.json"
        if not path.exists():
            sys.exit(f"No saved shape at {path}. Run `python dune_probe.py {args.show}` first.")
        shape = json.loads(path.read_text())
        print(f"query {shape['query_id']} — captured {shape.get('captured_at')} "
              f"for {shape.get('project')}/{shape.get('metric')}")
        return print_shape(shape, path)
    if args.stored:
        return show_stored(args.stored, args.before)
    if not args.target:
        ap.print_help()
        sys.exit(2)

    load_dotenv()
    qid, project, metric = resolve(args.target)
    if not os.environ.get("DUNE_API_KEY", "").strip():
        sys.exit("DUNE_API_KEY is not set (environment or .env). This probe needs it; --show does not.")

    print(f"query {qid}  ({project}/{metric})")
    if args.diagnose:
        return diagnose(qid)
    d = Dune()
    try:
        rows = d._results(qid)
    except HttpError as e:
        print(f"\nHTTP {e.status} from Dune: {e.detail or '(no message)'}")
        if e.status in (403, 404):
            print("A 4xx here is Dune's definite answer; its message above is what separates "
                  "'no such query' from 'exists, but not readable by this key'. A query you do not "
                  "own often cannot be read through the results endpoint at all — forking it into "
                  "your own account gives an id that can.")
        sys.exit(1)

    shape = describe_rows(rows, sample=5)
    if args.json:
        print(json.dumps(shape, indent=2, default=str))
    artefact = write_shape(qid, project, metric, rows)
    if not args.json:
        print_shape(shape, artefact)
    if args.mapping:
        dry_run_map(rows, args.mapping, args.granularity, args.drop_current)


if __name__ == "__main__":
    main()
