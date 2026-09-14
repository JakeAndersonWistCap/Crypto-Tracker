#!/usr/bin/env python3
"""
run_triage.py — read back what a run actually did, and what CHANGED since the last one.

A run summary line ("41 review items") is a number without a cause. This breaks it down by
reason code, and — the part that matters after a round of new checks — DIFFS it against an
earlier run, so a jump separates into "the new checks firing, as designed" and "something new
went wrong". Those two need different responses and the summary line cannot tell them apart.

Reads only. It never writes to the store and never touches the workbook.

    python run_triage.py                          the latest run
    python run_triage.py --compare-previous       latest vs the run before it  <-- start here
    python run_triage.py --run <id> --compare <older-id>
    python run_triage.py --runs                   list recent run ids
    python run_triage.py --metric Uniswap/burn_address_balance
                                                  everything about one figure in this run
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from store import Store

# When each review reason started firing. A jump in the total is only surprising if it is NOT
# one of these — so the triage says which, instead of leaving it to be worked out by hand.
INTRODUCED = {
    "burn_mechanism_assumed": ("b33317c", "burn-mechanism audit — fires once per project whose "
                                          "dead-address model is assumed rather than documented"),
    "burn_address_never_received": ("a503956", "burn address holding exactly zero — evidence about "
                                               "the address, not the burn"),
    "unattributable_zero": ("29955b1", "a zero burn flow differenced out of a balance read"),
    "period_overlap": ("e23b249", "a monthly backfill and a daily delta covering the same month"),
    "supply_partial": ("f0feb8b", "a figure summed over known components only"),
    "tier_collision": ("f63eadc", "two tiers wrote the same figure"),
    "address_unverified": ("8bdaedc", "an address read without being verified against protocol docs"),
    "anchor_unconfirmed": ("0660b98", "a scrape anchor whose first value needs eyeballing"),
    "out_of_bounds": ("8bdaedc", "outside sanity bounds — REJECTED, not stored"),
    "change_threshold": ("8bdaedc", "moved more than its threshold — stored, flagged"),
    "disagrees_with_published_reference": ("8bdaedc", "contradicts a figure the protocol published"),
    "cross_check_divergence": ("8bdaedc", "primary and cross-check disagree beyond tolerance"),
}

RECENT_ROUNDS = {"29955b1", "a503956", "b33317c", "88d4736", "e23b249", "d6fd3ad"}


def runs(st: Store, limit: int = 12) -> list[tuple[str, str]]:
    return st.conn.execute(
        "SELECT run_id, MIN(ts) FROM run_log GROUP BY run_id ORDER BY MIN(ts) DESC LIMIT ?",
        (limit,)).fetchall()


def latest_run(st: Store) -> str | None:
    r = runs(st, 1)
    return r[0][0] if r else None


def review_counts(st: Store, run_id: str) -> Counter:
    return Counter(r[0] for r in st.conn.execute(
        "SELECT reason FROM review_queue WHERE run_id=?", (run_id,)).fetchall())


def section(title: str):
    print(f"\n{title}\n{'-' * len(title)}")


def show_run(st: Store, run_id: str):
    counts = review_counts(st, run_id)
    total = sum(counts.values())
    section(f"REVIEW QUEUE — {total} item(s)")
    if not counts:
        print("  none")
    for reason, n in counts.most_common():
        commit, why = INTRODUCED.get(reason, ("?", "no description on file"))
        tag = "NEW CHECK" if commit in RECENT_ROUNDS else "pre-existing"
        print(f"  {n:>4}  {reason:<36} [{tag}, {commit}]")
        print(f"        {why}")
        rows = st.conn.execute(
            "SELECT project, metric, value, action FROM review_queue WHERE run_id=? AND reason=? "
            "ORDER BY project", (run_id, reason)).fetchall()
        for project, metric, value, action in rows[:8]:
            v = "n/a" if value is None else f"{value:,.6g}"
            print(f"          {project:<14} {metric:<28} {v:>20}  {action}")
        if len(rows) > 8:
            print(f"          ... and {len(rows) - 8} more")

    section("RUN LOG")
    for status in ("failed", "skipped"):
        rows = st.conn.execute(
            "SELECT source, project, message FROM run_log WHERE run_id=? AND status=? ORDER BY source",
            (run_id, status)).fetchall()
        print(f"  {status.upper()}: {len(rows)}")
        for source, project, message in rows:
            print(f"    {source:<16} {project or '-':<14} {message[:96]}")

    section("GAP REPORT by priority")
    for label, n in st.conn.execute(
            "SELECT priority_label, COUNT(*) FROM gap_report WHERE run_id=? GROUP BY priority_label "
            "ORDER BY MIN(COALESCE(priority,5))", (run_id,)).fetchall():
        print(f"  {n:>4}  {label}")


def compare(st: Store, run_id: str, base_id: str):
    now, was = review_counts(st, run_id), review_counts(st, base_id)
    section(f"REVIEW QUEUE DELTA — {base_id} -> {run_id}")
    print(f"  total {sum(was.values())} -> {sum(now.values())} "
          f"({sum(now.values()) - sum(was.values()):+d})\n")
    expected, unexpected = [], []
    for reason in sorted(set(now) | set(was)):
        delta = now.get(reason, 0) - was.get(reason, 0)
        if delta == 0:
            continue
        commit, why = INTRODUCED.get(reason, ("?", "no description on file"))
        line = (f"  {delta:+4d}  {reason:<36} {was.get(reason, 0)} -> {now.get(reason, 0)}", commit, why)
        (expected if commit in RECENT_ROUNDS else unexpected).append(line)

    print("  EXPECTED — a check this round's work introduced:")
    for line, commit, why in expected or []:
        print(f"{line}  [{commit}]")
        print(f"        {why}")
    if not expected:
        print("    none")
    print("\n  NOT EXPLAINED BY THIS ROUND'S NEW CHECKS — look at these:")
    for line, commit, why in unexpected or []:
        print(f"{line}  [{commit}]")
        print(f"        {why}")
    if not unexpected:
        print("    none — the entire change is the new checks firing as designed")


def show_metric(st: Store, run_id: str, target: str):
    project, metric = target.split("/", 1)
    section(f"{project} / {metric}")
    row = st.conn.execute(
        "SELECT date, value, source, tier FROM metrics WHERE project=? AND metric=? "
        "ORDER BY date DESC LIMIT 5", (project, metric)).fetchall()
    print("  STORED:" + ("" if row else "  NOTHING — it was not stored this run"))
    for date, value, source, tier in row:
        print(f"    {date[:10]}  {value:>22,.6g}  tier {tier}  {source}")

    print("\n  REVIEW QUEUE (this run):")
    rows = st.conn.execute(
        "SELECT reason, action, value, prior_value, source FROM review_queue "
        "WHERE run_id=? AND project=? AND metric=?", (run_id, project, metric)).fetchall()
    for reason, action, value, prior, source in rows or []:
        v = "n/a" if value is None else f"{value:,.6g}"
        print(f"    {reason:<32} {action:<16} value={v}")
        print(f"      {source}")
    if not rows:
        print("    nothing flagged")

    print("\n  WHAT THE ADAPTER SAID (run log):")
    for source, message in st.conn.execute(
            "SELECT source, message FROM run_log WHERE run_id=? AND project=? AND message LIKE ?",
            (run_id, project, f"%{metric}%")).fetchall():
        print(f"    {source}: {message[:150]}")

    print("\n  GAP REPORT:")
    for m, reason in st.conn.execute(
            "SELECT metric, reason FROM gap_report WHERE run_id=? AND project=? AND metric LIKE ?",
            (run_id, project, f"%{metric}%")).fetchall():
        print(f"    {m}: {reason[:200]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run id (default: the latest)")
    ap.add_argument("--compare", metavar="RUN_ID", help="diff review counts against this earlier run")
    ap.add_argument("--compare-previous", action="store_true", help="diff against the run before this one")
    ap.add_argument("--runs", action="store_true", help="list recent run ids and exit")
    ap.add_argument("--metric", metavar="Project/metric", help="everything about one figure")
    args = ap.parse_args()

    st = Store()
    try:
        recent = runs(st)
        if args.runs:
            for rid, ts in recent:
                print(f"  {rid}  {ts}")
            return
        run_id = args.run or latest_run(st)
        if not run_id:
            sys.exit("No runs in the store. Run token_metrics.py first.")
        print(f"run {run_id}")

        if args.metric:
            return show_metric(st, run_id, args.metric)

        show_run(st, run_id)
        base = args.compare
        if args.compare_previous and not base:
            others = [r for r, _ in recent if r != run_id]
            base = others[0] if others else None
            if not base:
                print("\n(no earlier run to compare against)")
        if base:
            compare(st, run_id, base)
    finally:
        st.close()


if __name__ == "__main__":
    main()
