#!/usr/bin/env python3
"""
daily_summary.py — the plain summary the daily log ends with (Jake, overnight 2026-10-06, E2).

    python daily_summary.py --log logs\\run_2026-10-07.log          (windows/daily_run.ps1 runs it last)

Prints, in plain words:
  1. Credibility counts by verdict, and the change against the previous day's run;
  2. NETWORK-WIDE TROUBLE — the run summary's line(s), or "none";
  3. ACTION NEEDED — the run's ACTION NEEDED lines and every desktop notification the daily run raised, or "none";
  4. NEW CHECKs — rows that are CHECK today and were not CHECK in the previous run (with what they check).

The previous day's verdicts are kept in logs/credibility_state.json (one entry per run date; the latest EARLIER
date is the comparison, so a second run on the same day still compares with yesterday). Reads metrics.db; writes
only that state file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

VERDICTS = ("PASS", "CHECK", "FRESH-only", "UNVERIFIABLE", "N/A")
STATE = Path(__file__).resolve().parent / "logs" / "credibility_state.json"


def bucket(verdict: str) -> str:
    v = str(verdict or "").strip()
    for name in VERDICTS:
        if v.upper().startswith(name.upper().split("-")[0]):
            return name
    return "other" if v else "blank"


def counts(verdicts: dict) -> dict:
    out = {k: 0 for k in VERDICTS}
    for v in verdicts.values():
        b = bucket(v)
        out[b] = out.get(b, 0) + 1
    return out


def summarise(now: dict, prev: dict | None, prev_date: str | None, log_text: str, what: dict | None = None) -> list[str]:
    """The summary lines. `now` / `prev`: {"Project|row": verdict}; `what`: {"Project|row": label}."""
    what = what or {}
    c_now = counts(now)
    lines = ["=" * 78, f"DAILY SUMMARY {date.today().isoformat()}", "",
             "1. CREDIBILITY COUNTS" + (f" (change vs {prev_date})" if prev is not None else " (no previous run on file)")]
    c_prev = counts(prev) if prev is not None else None
    for k in VERDICTS:
        d = "" if c_prev is None else f"  ({c_now.get(k, 0) - c_prev.get(k, 0):+d})"
        lines.append(f"   {k:<13}{c_now.get(k, 0):>5}{d}")
    lines.append(f"   {'rows':<13}{len(now):>5}" + ("" if prev is None else f"  ({len(now) - len(prev):+d})"))
    net = [ln.strip() for ln in log_text.splitlines() if "NETWORK-WIDE TROUBLE" in ln and "rerunning" not in ln]
    lines += ["", "2. NETWORK-WIDE TROUBLE"] + ([f"   {ln[:300]}" for ln in dict.fromkeys(net)] or ["   none"])
    act = [ln.strip() for ln in log_text.splitlines() if "ACTION NEEDED" in ln or "NOTIFIED:" in ln]
    lines += ["", "3. ACTION NEEDED"] + ([f"   {ln[:300]}" for ln in dict.fromkeys(act)] or ["   none"])
    new = sorted(k for k, v in now.items() if bucket(v) == "CHECK" and (prev is None or bucket(prev.get(k)) != "CHECK"))
    lines += ["", f"4. NEW CHECKs ({len(new)})" + ("" if prev is not None else " — every CHECK, no previous run to compare")]
    lines += [f"   {k}" + (f" — {what[k][:90]}" if what.get(k) else "") + (f" (was {prev.get(k) or 'absent'})"
                                                                         if prev is not None else "") for k in new] \
        or ["   none"]
    lines.append("=" * 78)
    return lines


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def previous(state: dict, today: str) -> tuple[str | None, dict | None]:
    earlier = sorted(d for d in (state.get("days") or {}) if d < today)
    return (earlier[-1], state["days"][earlier[-1]]) if earlier else (None, None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", help="today's run log, scanned for NETWORK-WIDE TROUBLE / ACTION NEEDED / NOTIFIED")
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args(argv)
    from credibility_report import evaluate
    rows, tab = evaluate()
    if rows is None:
        return 1
    now = {f"{r['project']}|{r['id']}": str(t[10] or "") for r, t in zip(rows, tab)}
    what = {f"{r['project']}|{r['id']}": r.get("what", "") for r in rows}
    log_text = ""
    if args.log:
        try:
            log_text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log_text = f"(log not readable: {e})"
    state_path = Path(args.state)
    state = load_state(state_path)
    today = date.today().isoformat()
    prev_date, prev = previous(state, today)
    for ln in summarise(now, prev, prev_date, log_text, what):
        print(ln.encode("ascii", "replace").decode("ascii"))
    days = state.setdefault("days", {})
    days[today] = now
    for d in sorted(days)[:-30]:                   # a month of history is plenty
        del days[d]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=0, sort_keys=True), encoding="utf-8")
    tmp.replace(state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
