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

ORDER = ["P1 critical", "P2 decision", "P3 fixable", "P4 fixable", "P6 uncovered", "P5 answered"]
# P5 IS LISTED, NEVER COUNTED AS OPEN (2026-09-24). Its rows carry their own conclusion — checked
# and absent, refused by robots.txt, deliberately disabled, or pointing at the row that holds the
# figure — see fetch/gaps.py ANSWERED_SIGNALS. "P5 by design" is the same class's old label, read
# as P5 answered in an older baseline.
ANSWERED = "P5 answered"
OLD_LABELS = {"P5 by design": ANSWERED}


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
        from fetch import registry_reasons        # noqa: PLC0415
        rows = gap_detect.detect(projects, st.load_long(), set(), registry_reasons(), [])
    finally:
        st.close()
    by_pri = collections.Counter(r.get("priority_label") or "?" for r in rows)
    open_rows = [r for r in rows if r.get("priority_label") != ANSWERED]
    by_metric = collections.Counter(r["metric"] for r in open_rows)
    by_project = collections.Counter(r["project"] for r in open_rows)
    return {"total": len(open_rows), "answered": len(rows) - len(open_rows),
            "projects": len(projects), "by_priority": dict(by_pri),
            "by_metric": dict(by_metric), "by_project": dict(by_project)}


def _normalise(baseline: dict) -> dict:
    """An older baseline, relabelled and re-totalled so it compares with this one: before
    2026-09-24 the total counted P5 as open."""
    b = dict(baseline)
    pri = {}
    for k, v in (b.get("by_priority") or {}).items():
        pri[OLD_LABELS.get(k, k)] = pri.get(OLD_LABELS.get(k, k), 0) + v
    b["by_priority"] = pri
    if "answered" not in b:
        b["answered"] = pri.get(ANSWERED, 0)
        b["total"] = b.get("total", 0) - b["answered"]
    return b


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
    line = f"  {'OPEN (excl. P5)':16} {now['total']:>7}"
    if before:
        line += f" {before['total']:>8} {now['total'] - before['total']:>+8}"
    print(line)
    if now.get("by_project"):
        print("\n  OPEN BY PROJECT")
        for p in sorted(set(now["by_project"]) | set((before or {}).get("by_project") or {})):
            n = now["by_project"].get(p, 0)
            line = f"  {p:16} {n:>7}"
            if before and before.get("by_project"):
                b = before["by_project"].get(p, 0)
                line += f" {b:>8} {n - b:>+8}"
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
    before = _normalise(json.loads(pathlib.Path(args.against).read_text())) if args.against else None
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
