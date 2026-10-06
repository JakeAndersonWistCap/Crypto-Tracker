#!/usr/bin/env python3
"""
manual_form.py — the MANUAL READINGS form (Jake, 2026-10-06).

    python manual_form.py make              -> manual_readings_form.csv: every open Credibility row whose root fix is a
                                               manual reading, with the URL, the exact tile/label to read, OUR current
                                               value, and how many headline rows the reading would clear. Monthly rows
                                               (buybacks, revenue per month) get one line per month to fill.
    python manual_form.py load FILE.csv     -> reads the filled form in one pass: every line with a `value` is checked
                                               (a number, a read_on date, a period for monthly lines), previewed, and on
                                               one typed "yes" written to manual_references.csv, which Credibility reads.

Fill `value` (in the stated unit), `read_on` (YYYY-MM-DD) and, if you like, `note`. Leave `value` empty to skip a
line. Re-loading a line for the same project/row/period replaces the earlier reading. Readings carry read_by (Jake
by default) and the page they came from, so every manual reference says who read what, where and when.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date

import manual_refs as mr

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH = re.compile(r"^\d{4}-\d{2}$")


def _recent_months(n: int = 3) -> list[str]:
    """The last n COMPLETE calendar months, oldest first."""
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return out[::-1]


def make(args) -> int:
    import credibility
    from credibility_report import evaluate, fmt
    rows, tab = evaluate(project=args.project)
    if rows is None:
        return 1
    roots = credibility.root_causes(rows, [t[10] for t in tab])
    have = mr.by_row()
    lines = []
    for d in roots:
        project, rid = d["root"].split(" | ")[:2]
        # a manual-reading fix, or a row whose page is registered (manual_refs.PAGES): a reading clears it
        if not (d["fix"].startswith("manual reading") or (project, rid) in mr.PAGES):
            continue
        pg = mr.page_for(project, rid)
        i = next(k for k, r in enumerate(rows) if r["project"] == project and r["id"] == rid)
        base = {"project": project, "row": rid, "unit": pg.get("unit", ""), "url": pg["url"],
                "tile": pg.get("tile", ""), "tol_pct": pg.get("tol_pct", 10.0), "read_by": "Jake",
                "our_value": fmt(tab[i][4]), "clears_headline_rows": d["headline_rows"],
                "what": rows[i]["what"], "resolve": d["fix"].split(":", 1)[-1].strip()}
        periods = _recent_months() if pg.get("monthly_metric") else [""]
        for per in periods:
            done = [r for r in have.get((project, rid), []) if (r.get("period") or "") == per]
            lines.append({**base, "period": per, "value": done[0]["value"] if done else "",
                          "read_on": done[0].get("read_on", "") if done else ""})
    cols = ["project", "row", "what", "period", "url", "tile", "unit", "our_value", "clears_headline_rows",
            "value", "read_on", "read_by", "tol_pct", "note", "resolve"]
    out = args.out or str(mr.FORM)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(lines)
    n_rows = len({(x["project"], x["row"]) for x in lines})
    print(f"wrote {out}: {len(lines)} line(s) for {n_rows} open row(s); together they clear up to "
          f"{sum(int(x['clears_headline_rows']) for x in {(x['project'], x['row']): x for x in lines}.values())} "
          f"headline row(s). "
          f"Fill value + read_on, then: python manual_form.py load {out}")
    return 0


def validate(lines: list[dict]) -> tuple[list[dict], list[str]]:
    """(readings to store, problems). A line without a value is skipped silently; a bad one is a problem."""
    good, bad = [], []
    for n, r in enumerate(lines, start=2):
        v = (r.get("value") or "").strip().replace(",", "")
        if not v:
            continue
        where = f"line {n} ({r.get('project')} {r.get('row')} {r.get('period') or ''})".rstrip()
        try:
            float(v)
        except ValueError:
            bad.append(f"{where}: value {v!r} is not a number")
            continue
        if not DATE.match((r.get("read_on") or "").strip()):
            bad.append(f"{where}: read_on must be YYYY-MM-DD")
            continue
        if (r.get("read_on") or "") > date.today().isoformat():
            bad.append(f"{where}: read_on {r['read_on']} is in the future")
            continue
        per = (r.get("period") or "").strip()
        if mr.page_for(r["project"], r["row"]).get("monthly_metric") and not MONTH.match(per):
            bad.append(f"{where}: a monthly line needs period YYYY-MM")
            continue
        good.append({k: (r.get(k) or "").strip() for k in mr.FIELDS} | {"value": v, "period": per,
                                                                           "read_by": (r.get("read_by") or "Jake").strip()})
    return good, bad


def load(args) -> int:
    with open(args.file, newline="", encoding="utf-8") as fh:
        lines = list(csv.DictReader(fh))
    good, bad = validate(lines)
    for b in bad:
        print(f"REFUSED {b}")
    if not good:
        print("nothing to load" + (" (fix the refused lines and load again)" if bad else ""))
        return 1 if bad else 0
    print(f"{len(good)} reading(s) to store:")
    for r in good:
        print(f"  {r['project']:<10} {r['row']:<22} {r['period'] or '':<8} {r['value']:>18} {r['unit']:<10} "
              f"read {r['read_on']} by {r['read_by']} — {r['url']}")
    if not args.yes and input('type "yes" to store them: ').strip().lower() != "yes":
        print("not stored")
        return 1
    keep = {(r["project"], r["row"], r.get("period") or ""): r for r in mr.load()}
    for r in good:
        keep[(r["project"], r["row"], r["period"])] = r
    mr.save(list(keep.values()))
    print(f"stored in {mr.STORE} ({len(keep)} reading(s) in all). Run credibility_report.py to see the rows judged.")
    return 0 if not bad else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--project")
    m.add_argument("--out")
    lo = sub.add_parser("load")
    lo.add_argument("file")
    lo.add_argument("--yes", action="store_true", help="store without the typed confirmation")
    args = ap.parse_args(argv)
    return make(args) if args.cmd == "make" else load(args)


if __name__ == "__main__":
    sys.exit(main())
