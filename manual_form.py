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
    python manual_form.py text "LINE" ...   -> the same, from readings given IN CHAT, without the CSV (Jake, 2026-10-07).
    python manual_form.py text --file F.txt    One reading per line:
                                                   Project row [YYYY-MM] = value (YYYY-MM-DD, source) optional note
                                               e.g.  Chainlink in_locked = 45,123,456 (2026-10-07, staking.chain.link)
                                                     Sky in_buyback [2026-09] = 12,400,000 (2026-10-07, forum.sky.money)
                                                     Aerodrome a3_protocol_yield = 24.5% (2026-10-07, aerodrome.finance/vote)
                                               A trailing % is divided by 100; commas and $ are dropped; nothing else is
                                               interpreted (1.2M is refused, not guessed). Blank and # lines are skipped.

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
                "our_value": fmt(tab[i][4]), "clears_headline_rows": d["headline_rows"], "clears_rows": d["rows"],
                "what": rows[i]["what"], "resolve": d["fix"].split(":", 1)[-1].strip()}
        periods = _recent_months() if pg.get("monthly_metric") else [""]
        for per in periods:
            done = [r for r in have.get((project, rid), []) if (r.get("period") or "") == per]
            lines.append({**base, "period": per, "value": done[0]["value"] if done else "",
                          "read_on": done[0].get("read_on", "") if done else ""})
    # SORTED BY WHAT A READING CLEARS (overnight 2026-10-06, D): headline rows first, then all rows, then the name
    lines.sort(key=lambda x: (-int(x["clears_headline_rows"]), -int(x["clears_rows"]), x["project"], x["row"],
                              x["period"]))
    cols = ["project", "row", "what", "period", "url", "tile", "unit", "our_value", "clears_headline_rows",
            "clears_rows", "value", "read_on", "read_by", "tol_pct", "note", "resolve"]
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


def validate(lines: list[dict], start: int = 2) -> tuple[list[dict], list[str]]:
    """(readings to store, problems). A line without a value is skipped silently; a bad one is a problem."""
    good, bad = [], []
    for n, r in enumerate(lines, start=start):
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


# One chat reading: "Project row [YYYY-MM] = value (YYYY-MM-DD, source) note". The row is a Credibility row id.
TEXT_LINE = re.compile(r"^\s*(?P<project>[A-Za-z][\w.\- ]*?)\s*(?:\||\s)\s*(?P<row>(?:a[1-4]|in)_[a-z0-9_]+)\s*"
                       r"(?:\[\s*(?P<period>[^\]]*?)\s*\])?\s*=\s*(?P<value>[^()=]+?)\s*"
                       r"\(\s*(?P<read_on>[^,()]+?)\s*,\s*(?P<source>[^()]+?)\s*\)\s*(?P<note>.*)$")


def parse_text(text: str) -> tuple[list[dict], list[str]]:
    """(form-shaped lines for validate(), problems) from readings typed in chat — one per line. The page's tile, unit
    and tolerance come from manual_refs.PAGES; the source given in the line is the page the reading came from."""
    import config
    by_lower = {n.lower(): n for n in config.PROJECT_BY_NAME}
    lines, bad = [], []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        m = TEXT_LINE.match(raw)
        if not m:
            bad.append(f"text line {n}: not 'Project row [YYYY-MM] = value (YYYY-MM-DD, source)': {raw.strip()[:120]}")
            continue
        project = by_lower.get(m["project"].strip().lower())
        if not project:
            bad.append(f"text line {n}: no project named {m['project'].strip()!r}")
            continue
        row = m["row"]
        known = (set((config.CREDIBILITY.get(project) or {})) | {r for (p, r) in mr.PAGES if p == project}
                 | set(getattr(config, "CREDIBILITY_COMMON_INPUTS", {}) or {}) | {"in_circ", "in_price"})
        if row not in known and not row.startswith(("a1_", "a2_", "a3_", "a4_")):
            bad.append(f"text line {n}: {project} has no Credibility row {row!r}")
            continue
        v = m["value"].strip().replace(",", "").replace("$", "").replace(" ", "")
        pg = mr.page_for(project, row)
        if v.endswith("%") and "fraction" in str(pg.get("unit", "")):   # only where the page's unit is a fraction
            try:
                v = f"{float(v[:-1]) / 100.0:.12g}"     # 12.48% -> 0.1248, never 0.12480000000000001
            except ValueError:
                pass                                     # validate() refuses it with the value shown
        note = ("given in chat; entered by Claude Code" + (f" — {m['note'].strip()}" if m["note"].strip() else ""))
        lines.append({"project": project, "row": row, "period": (m["period"] or "").strip(), "value": v,
                      "read_on": m["read_on"].strip(), "read_by": "Jake", "url": m["source"].strip(),
                      "tile": pg.get("tile", ""), "unit": pg.get("unit", ""), "tol_pct": str(pg.get("tol_pct", 10.0)),
                      "note": note, "_line": n})
    return lines, bad


def text(args) -> int:
    body = "\n".join(args.lines or [])
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            body += ("\n" if body else "") + fh.read()
    lines, bad = parse_text(body)
    good = []
    for ln in lines:                                     # one at a time, so a refusal names the line Jake typed
        g, b = validate([ln], start=ln["_line"])
        good += g
        bad += [x.replace("line ", "text line ", 1) for x in b]
    return _store(good, bad, args.yes)


def load(args) -> int:
    with open(args.file, newline="", encoding="utf-8") as fh:
        lines = list(csv.DictReader(fh))
    good, bad = validate(lines)
    return _store(good, bad, args.yes)


def _store(good: list[dict], bad: list[str], yes: bool) -> int:
    for b in bad:
        print(f"REFUSED {b}")
    if not good:
        print("nothing to load" + (" (fix the refused lines and load again)" if bad else ""))
        return 1 if bad else 0
    print(f"{len(good)} reading(s) to store:")
    for r in good:
        print(f"  {r['project']:<10} {r['row']:<22} {r['period'] or '':<8} {r['value']:>18} {r['unit']:<10} "
              f"read {r['read_on']} by {r['read_by']} — {r['url']}")
    if not yes and input('type "yes" to store them: ').strip().lower() != "yes":
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
    tx = sub.add_parser("text", help="readings typed in chat: 'Project row [YYYY-MM] = value (YYYY-MM-DD, source)'")
    tx.add_argument("lines", nargs="*")
    tx.add_argument("--file")
    tx.add_argument("--yes", action="store_true", help="store without the typed confirmation")
    args = ap.parse_args(argv)
    return {"make": make, "load": load, "text": text}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
