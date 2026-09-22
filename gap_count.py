#!/usr/bin/env python3
"""gap_count.py — gaps in the portfolio, by priority. The progress metric across rounds.

WHY THIS EXISTS AS A TOOL RATHER THAN A PASTED NUMBER
-----------------------------------------------------
Round after round the question is "how many gaps, and did that go down". Answering it by hand
means re-deriving the scope, re-running the detector and hoping the same filters were applied
as last time — and the one round where they were not is the round the number stops meaning
anything. The comparison only works if it is computed the same way every time.

It reads the STORE, so it answers for real data rather than for the config's opinion of what
ought to exist. Applicable (project, metric) pairs are a different measure and a weaker one:
they say what the library thinks could be filled, not what is.

    python gap_count.py                    the current run's gaps, by priority
    python gap_count.py --all              every project, not just portfolio.txt
    python gap_count.py --by-metric        which metrics account for the P6 pile
    python gap_count.py --save before.json record a baseline
    python gap_count.py --against before.json   the before/after table
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config          # noqa: E402
import store as store_mod  # noqa: E402
from fetch import gaps as gap_detect  # noqa: E402

ORDER = ["P1 critical", "P2 decision", "P3 fixable", "P4 fixable", "P5 by design", "P6 uncovered"]


def scope(use_all: bool) -> list[dict]:
    if use_all:
        return list(config.PROJECTS)
    names, unknown = config.read_portfolio(HERE / config.PORTFOLIO_FILE)
    if unknown:
        # SAME RULE AS THE RUN ITSELF: a typo WIDENS rather than narrows, and says so. A count
        # that silently dropped a held asset would report progress that is a missing project.
        print(f"  {config.PORTFOLIO_FILE}: {len(unknown)} unrecognised name(s) IGNORED — "
              f"{', '.join(unknown)}. Counting every project rather than risk dropping one.")
        return list(config.PROJECTS)
    return [config.PROJECT_BY_NAME[n] for n in names] if names else list(config.PROJECTS)


def count(db: pathlib.Path, use_all: bool) -> dict:
    st = store_mod.Store(db)
    try:
        projects = scope(use_all)
        rows = gap_detect.detect(projects, st.load_long(), set(), {}, [])
    finally:
        st.close()
    by_pri = collections.Counter(r.get("priority_label") or "?" for r in rows)
    by_metric = collections.Counter(r["metric"] for r in rows)
    return {"total": len(rows), "projects": len(projects),
            "by_priority": dict(by_pri), "by_metric": dict(by_metric)}


def _table(now: dict, before: dict | None) -> None:
    print(f"\n  GAPS — {now['projects']} project(s)")
    print(f"  {'priority':16} {'now':>7}" + (f" {'before':>8} {'change':>8}" if before else ""))
    print(f"  {'-' * 16} {'-' * 7}" + (f" {'-' * 8} {'-' * 8}" if before else ""))
    keys = ORDER + sorted(set(now["by_priority"]) - set(ORDER))
    for k in keys:
        n = now["by_priority"].get(k, 0)
        if not n and not (before or {}).get("by_priority", {}).get(k):
            continue
        line = f"  {k:16} {n:>7}"
        if before:
            b = before["by_priority"].get(k, 0)
            line += f" {b:>8} {n - b:>+8}"
        print(line)
    line = f"  {'TOTAL':16} {now['total']:>7}"
    if before:
        line += f" {before['total']:>8} {now['total'] - before['total']:>+8}"
    print(line)
    if before and before.get("projects") != now.get("projects"):
        # A COUNT OVER A DIFFERENT SCOPE IS NOT A COMPARISON. Saying so beats a tidy table that
        # reports a scope change as progress.
        print(f"\n  ** SCOPES DIFFER — {before['projects']} project(s) before against "
              f"{now['projects']} now. The change above is not comparable. **")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(HERE / "metrics.db"))
    ap.add_argument("--all", action="store_true", help="every project, not just portfolio.txt")
    ap.add_argument("--by-metric", action="store_true", help="which metrics account for the pile")
    ap.add_argument("--save", metavar="FILE", help="write this count as a baseline")
    ap.add_argument("--against", metavar="FILE", help="compare against a saved baseline")
    args = ap.parse_args(argv)

    db = pathlib.Path(args.db)
    if not db.exists():
        print(f"  no such store: {db}\n  Run token_metrics.py first, or pass --db.")
        return 2
    now = count(db, args.all)
    before = json.loads(pathlib.Path(args.against).read_text()) if args.against else None
    _table(now, before)

    if args.by_metric:
        print(f"\n  BY METRIC (top 20 of {len(now['by_metric'])}):")
        for m, n in sorted(now["by_metric"].items(), key=lambda kv: -kv[1])[:20]:
            delta = ""
            if before:
                b = before["by_metric"].get(m, 0)
                delta = f"  ({n - b:+d})" if n != b else ""
            print(f"    {n:>4}  {m}{delta}")
        if before:
            closed = {m: b for m, b in before["by_metric"].items() if m not in now["by_metric"]}
            if closed:
                print(f"\n  CLOSED ENTIRELY since the baseline: "
                      + ", ".join(f"{m} (was {b})" for m, b in sorted(closed.items())))

    if args.save:
        pathlib.Path(args.save).write_text(json.dumps(now, indent=1))
        print(f"\n  baseline written to {args.save}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
