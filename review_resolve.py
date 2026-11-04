"""review_resolve.py - resolve Review Queue rows (P5-1.6, Jake 2026-11-04).

    python review_resolve.py --list                        # the open rows of the latest review run (read-only)
    python review_resolve.py --rejudge                     # DRY RUN: re-judge the open change_threshold rows
    python review_resolve.py --rejudge --apply             # ... and record the artefacts as resolved
    python review_resolve.py --resolve Hyperliquid emissions_tokens 2026-10-30 --note "checked: ..."   # a person's call
    options: --store PATH (default metrics.db), --all-runs (every run's rows, not only the latest run's)

THE NEW RULE (fetch/validate.py, P5-1.6): change_threshold compares PER DAY when the two rows cover different numbers of
days ([span=Nd]), and never compares a row with a prior row for the SAME date (a same-day re-run re-reads one day).
--rejudge applies that rule to rows the OLD rule already wrote, and resolves (RESOLVED-ARTEFACT, with the numbers in
the note) ONLY a row it proves was an artefact:
    - its prior row is for the same date (a re-read), or
    - the two rows cover different spans and the per-day move is within the series' threshold.
Every other row stays OPEN: a window or same-weekday comparison, a prior it cannot find in the store, equal spans, a
comparison already made per day, or a per-day move still beyond the threshold. Never a bulk accept.

--list and a --rejudge without --apply open the store READ-ONLY and change nothing.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REJUDGE_BY = "review_resolve.py --rejudge (P5-1.6 rule)"
RESOLVE_BY = "review_resolve.py --resolve (a person's decision)"


def _read_only(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)


def review_rows(con: sqlite3.Connection, all_runs: bool = False) -> pd.DataFrame:
    """The review rows the workbook shows (the latest review run), or every run's, with their resolutions."""
    import store as sm
    if all_runs:
        rows = pd.read_sql_query("SELECT * FROM review_queue ORDER BY ts, rowid", con)
    else:
        run = con.execute("SELECT run_id FROM review_queue ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        rows = (pd.read_sql_query("SELECT * FROM review_queue WHERE run_id=? ORDER BY project, metric", con,
                                  params=(run[0],)) if run else
                pd.read_sql_query("SELECT * FROM review_queue WHERE 0", con))
    return sm.attach_resolutions(rows, con)


def open_rows(rows: pd.DataFrame) -> pd.DataFrame:
    import config
    if rows.empty:
        return rows
    return rows[[config.review_answered(r) is None for r in rows.to_dict("records")]]


def open_counts(rows: pd.DataFrame) -> Counter:
    return Counter((r["project"], r["metric"], r["reason"]) for r in open_rows(rows).to_dict("records"))


def _prior_row(con: sqlite3.Connection, r: dict):
    """(date, value, source) of the stored row the flag compared against, or None when it cannot be found."""
    d = str(r.get("date") or "")[:10]
    pd_ = r.get("prior_date")
    if pd_ is not None and not (isinstance(pd_, float) and pd.isna(pd_)) and str(pd_).strip():
        got = con.execute("SELECT date, value, source FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)=?",
                          (r["project"], r["metric"], str(r["prior_date"])[:10])).fetchone()
        return got
    pv = r.get("prior_value")
    if pv is None or pd.isna(pv):
        return None
    for date, value, source in con.execute(
            "SELECT date, value, source FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)<=? "
            "ORDER BY date DESC", (r["project"], r["metric"], d)):
        if value is not None and abs(float(value) - float(pv)) <= 1e-9 * max(1.0, abs(float(pv))):
            return date, value, source
    return None


def rejudge(con: sqlite3.Connection, r: dict) -> tuple[bool, str]:
    """(artefact?, why) for one OPEN change_threshold row, under the P5-1.6 rule."""
    import config
    from fetch.validate import _span_days
    basis = str(r.get("basis") or "")
    if "TRAILING 7-DAY SUM" in basis or "SAME WEEKDAY" in basis:
        return False, "a window or same-weekday comparison: not a span or same-date artefact; stays open"
    if "NORMALISED TO PER DAY" in basis:
        return False, "already compared per day when it was flagged: the move is real on that basis; stays open"
    prior = _prior_row(con, r)
    if prior is None:
        return False, ("the prior value is not in the store any more, so this cannot be proven an artefact (if it was "
                       "a same-day re-run, the morning's row for the same date was overwritten by the later run); stays "
                       "open - a person can close it with --resolve")
    p_date, p_value, p_source = prior
    d = str(r.get("date") or "")[:10]
    if str(p_date)[:10] == d:
        return True, (f"same-date re-read: the flag compared {float(r['value']):,.4f} with {float(p_value):,.4f}, "
                      f"a row for the SAME date {d} (a second run re-reads the day; P5-1.6 no longer compares it)")
    if config.METRICS.get(r["metric"], {}).get("kind") != "flow":
        return False, "a stock compared with an earlier date: no span to normalise; stays open"
    cur_span, prior_span = _span_days(r.get("source")), _span_days(p_source)
    if cur_span == prior_span:
        return False, f"both rows cover {cur_span} day(s): the comparison stands; stays open"
    v, p = float(r["value"]) / cur_span, float(p_value) / prior_span
    if p == 0:
        return False, "the prior is 0 per day: no ratio; stays open"
    thr = config.change_threshold_pct(r["project"], r["metric"]) / 100.0
    move = (v - p) / abs(p)
    numbers = (f"{float(r['value']):,.4f} over {cur_span} day(s) = {v:,.4f}/day against {float(p_value):,.4f} over "
               f"{prior_span} day(s) ({str(p_date)[:10]}) = {p:,.4f}/day: {move:+.1%} per day (threshold "
               f"±{thr:.0%}); the raw figures read {float(r['value']) / float(p_value) - 1:+.1%}")
    if abs(move) <= thr:
        return True, f"span artefact: {numbers}"
    return False, f"still beyond the threshold per day: {numbers}; stays open"


def _print_counts(title: str, c: Counter) -> None:
    print(f"{title}: {sum(c.values())} open row(s)")
    for (p, m, reason), n in sorted(c.items()):
        print(f"    {n:4d}  {p} | {m} | {reason}")


def main(argv=None) -> int:
    import store as sm
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--rejudge", action="store_true")
    mode.add_argument("--resolve", nargs=3, metavar=("PROJECT", "METRIC", "DATE"))
    ap.add_argument("--apply", action="store_true", help="with --rejudge: record the resolutions")
    ap.add_argument("--note", default="", help="with --resolve: what was decided and why (required)")
    ap.add_argument("--reason", default=None, help="with --resolve: only rows of this reason")
    ap.add_argument("--all-runs", action="store_true", help="every run's rows, not only the latest review run's")
    ap.add_argument("--store", default=str(sm.DB_PATH))
    a = ap.parse_args(argv)
    if not Path(a.store).exists():
        print(f"review_resolve: no store at {a.store}")
        return 2

    if a.list:
        con = _read_only(a.store)
        rows = open_rows(review_rows(con, a.all_runs))
        print(f"OPEN Review Queue rows ({'every run' if a.all_runs else 'latest review run'}): {len(rows)}")
        def _s(v):
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
        for r in rows.to_dict("records"):
            print(f"  {r['project']} | {r['metric']} | {_s(r.get('date'))[:10]} | {r['reason']} | "
                  f"{_s(r.get('value'))} vs {_s(r.get('prior_value'))} ({_s(r.get('prior_date'))[:10]}) | "
                  f"{_s(r.get('basis'))[:120]}")
        _print_counts("by series", open_counts(review_rows(con, a.all_runs)))
        return 0

    if a.rejudge:
        con = _read_only(a.store)
        rows = review_rows(con, a.all_runs)
        before = open_counts(rows)
        decisions = []
        for r in open_rows(rows).to_dict("records"):
            if r["reason"] != "change_threshold":
                continue
            artefact, why = rejudge(con, r)
            decisions.append((r, artefact, why))
        con.close()
        print(f"RE-JUDGED {len(decisions)} open change_threshold row(s) under the P5-1.6 rule "
              f"({'APPLY' if a.apply else 'DRY RUN - nothing written'}):")
        for r, artefact, why in decisions:
            print(f"  {'RESOLVED-ARTEFACT' if artefact else 'OPEN             '}  {r['project']} | {r['metric']} | "
                  f"{str(r.get('date') or '')[:10]} | {why}")
        resolved = [(r, why) for r, artefact, why in decisions if artefact]
        after = before.copy()
        for r, _why in resolved:
            after[(r["project"], r["metric"], r["reason"])] -= 1
        after = +after
        _print_counts("BEFORE", before)
        _print_counts("AFTER" + ("" if a.apply else " (if applied)"), after)
        if a.apply and resolved:
            st = sm.Store(a.store)
            try:
                for r, why in resolved:
                    st.resolve_review(r["project"], r["metric"], r.get("date"), r["reason"], "RESOLVED-ARTEFACT", why,
                                      REJUDGE_BY, run_id=r["run_id"])
            finally:
                st.close()
            print(f"recorded {len(resolved)} resolution(s) in review_resolutions")
        return 0

    project, metric, date = a.resolve
    if not a.note.strip():
        print("review_resolve: --resolve needs --note \"what was decided and why\"")
        return 2
    con = _read_only(a.store)
    rows = pd.read_sql_query("SELECT DISTINCT project, metric, substr(date,1,10) AS d, reason FROM review_queue "
                             "WHERE project=? AND metric=? AND substr(date,1,10)=?", con,
                             params=(project, metric, str(date)[:10]))
    con.close()
    if a.reason:
        rows = rows[rows["reason"] == a.reason]
    if rows.empty:
        print(f"review_resolve: no Review Queue row for {project} | {metric} | {date}"
              + (f" | {a.reason}" if a.reason else ""))
        return 1
    st = sm.Store(a.store)
    try:
        for r in rows.to_dict("records"):
            st.resolve_review(project, metric, r["d"], r["reason"], "RESOLVED-ACCEPTED", a.note.strip(), RESOLVE_BY)
            print(f"resolved: {project} | {metric} | {r['d']} | {r['reason']} - RESOLVED-ACCEPTED: {a.note.strip()}")
    finally:
        st.close()
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped
    progress.line_buffered()
    sys.exit(main())
