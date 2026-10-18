#!/usr/bin/env python3
"""
history_gaps.py — which history the trajectory engine is missing, and the CHEAPEST way to get it (theory layer,
Phase 1; Jake 2026-10-12: "Jake does not want to wait months for history").

    python history_gaps.py                    build today's workbook from metrics.db (temp file), write
                                              logs/history_gaps.csv and print the summary — reads nothing from the
                                              network
    python history_gaps.py --run-fixes        then run every fix that needs NO new paid access (each with its own
                                              time budget; re-run to continue), and write the CSV again
    python history_gaps.py --workbook X       from an already built workbook

THE ROWS: every (project, metric) a trajectory row reads — the metric names in each Trajectory-tab Q0 formula (so the
list is exactly what the headlines use) — with: kind, latest source and tier, the earliest date held, Q0..Q-3 each
"full" / "partial (n days)" / "none", the blocking reason, the cheapest fix, what that fix costs, and whether
--run-fixes runs it. A series full for the four quarters Q0..Q-3 is listed as "ok" (Q-4 is a bonus column). Scope: the
15 portfolio projects (config.CREDIBILITY_PROJECTS); a series n/a by structure (no such mechanism, issuance declared
zero) is not listed (Jake's Phase 1 review 2026-10-12, item 8).

THE CHEAPEST FIX, in cost order: a free API that serves full history (DefiLlama — the routine run re-reads 455 days
for a short pair, fetch.backfill); a historical eth_call at past blocks (archive_backfill.py — free archive RPCs);
rebuilding from Transfer / mint / burn events (the source's own seed, `token_metrics.py --seed …`); the project's own
archive (a probe first); Dune / Artemis (keys on file, credits); none (the source starts there, or a decision is
needed). Never estimated: a series with no route says so.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

import config

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "logs" / "history_gaps.csv"
FULL_SHARE = 0.9               # a quarter "full" at >= 90% of its days held (81 of 90)
FIELDS = ["project", "metric", "kind", "source", "tier", "earliest", "Q0", "Q-1", "Q-2", "Q-3", "Q-4 (bonus)", "gap",
          "blocking_reason", "cheapest_fix", "fix_cost", "run_by_run_fixes"]
# JUDGED ON FOUR QUARTERS, Q0..Q-3 (Jake's Phase 1 review 2026-10-12, item 8); Q-4 is a bonus column (the rolling T12M
# a quarter earlier needs it) and never makes a gap on its own
QCOLS = {"Q0": ("q0_covered_days", 0), "Q-1": ("q1_covered_days", 1), "Q-2": ("q2_covered_days", 2),
         "Q-3": ("q3_covered_days", 3)}
BONUS = {"Q-4 (bonus)": ("q4_covered_days", 4)}

# A SOURCE'S OWN SEED (token_metrics.py --seed …): reads its full range with no time budget
SEEDS = {"chainlink_fees": "chainlink_fees", "plume_staking": "plume_staking", "plume_settlement": "plume_settlement",
         "mev_relays": "mev_relays", "aero_voter": "aero_epochs", "ve_managed": "aero_buyback",
         "nearblocks": "nearblocks", "geodnet": "geodnet", "hypercore_candles": "hl_candles",
         "pendle_gauges": "pendle_gauges"}
PROBE_FIRST = {"blockscout_stats", "growthepie", "validatorqueue", "lido_api", "rewardvault", "pendle_api",
               "aethir_pages", "hypercore_info", "maple_page", "scrape", "etherscan"}


def _metrics_in(formula) -> list[str]:
    return sorted(set(re.findall(r'&"\|([a-z0-9_]+)"', str(formula or ""))))


def _data_rows(wb) -> dict:
    ws = wb["Data"]
    import build_workbook as bw
    out = {}
    for r in range(2, ws.max_row + 1):
        k = ws.cell(r, 1).value
        if k and "|" in str(k):
            out[str(k)] = {col: ws.cell(r, j).value for j, col in enumerate(bw.DATA_COLS, start=1)}
    return out


def quarter_state(row: dict, k: int, asof: pd.Timestamp, period: int) -> tuple[str, bool]:
    """('full' | 'partial (n days)' | 'none', full?) for quarter Q-k of a Data row."""
    cov = row.get(f"q{k}_covered_days")
    if isinstance(cov, (int, float)):
        n = int(cov)
    else:                                   # a stock (or a series with no coverage column): from its first date
        f = pd.to_datetime(str(row.get("first_date") or "")[:10], errors="coerce")
        if pd.isna(f):
            return "none", False
        lo, hi = asof - pd.Timedelta(days=period * (k + 1)), asof - pd.Timedelta(days=period * k)
        n = 0 if f > hi else period if f <= lo + pd.Timedelta(days=1) else int((hi - f).days) + 1
    if n >= FULL_SHARE * period:
        return "full", True
    return ("none" if n <= 0 else f"partial ({n} days)"), False


def fix_for(p: dict, metric: str, row: dict) -> tuple[str, str, str, str]:
    """(blocking reason, cheapest fix, cost, run-fixes command or '')."""
    import history_audit as ha
    src = str(row.get("source") or "")
    base = src.split(":")[0].split("[")[0]
    first = row.get("first_date")
    fwd = (config.HISTORY_FORWARD_ONLY.get(p["name"]) or {})
    if metric in (fwd.get("metrics") or ()):
        return (f"FORWARD-ONLY source: {fwd.get('why', '')}"[:300], "none — the source keeps no history",
                "none", "")
    if (config.METRICS.get(metric) or {}).get("view_only") or base == "derived":
        return ("a read-time view: it reaches as far back as its inputs", "fill its inputs (their own rows here)",
                "as its inputs", "")
    if not src:
        return ("nothing stored", "the series' own route (Gap Report)", "see Gap Report", "")
    if base in ("defillama", "defillama_coins", "llama"):
        return ("stored from the store's first window (the routine run asks for 30 days)",
                "DefiLlama full history (free API): the next daily run re-reads 455 days for a short pair "
                "(fetch.backfill)", "free", "token_metrics.py")
    if base == "coingecko":
        if metric == "circulating_supply":
            return ("CoinGecko serves circulating as a current snapshot",
                    "CoinGecko market cap ÷ price = implied circulating, 365 days (free; stored as "
                    "circulating_supply_implied) — older: the project's own published history", "free", "token_metrics.py")
        if metric in ("price_usd", "market_cap_usd", "volume_usd"):
            return ("CoinGecko's free market_chart serves 365 days",
                    "Q-1..Q-3: CoinGecko 365 days (free, the daily run); before that: the price_history tier "
                    "(DefiLlama coingecko:<id> chart, Round G 2.1) back to the first token flow", "free",
                    "token_metrics.py")
        if metric == "total_supply" and p["name"] in config.TOTAL_SUPPLY_ONCHAIN_FIRST:
            # APPROVED (Jake 2026-10-12, decision 6 on 6f8c86c): the chain total at past blocks, labelled
            # "[chain backfill (pre-handover)]" — after orphan_cleanup.sql CJ moves CoinGecko's rows aside
            return ("CoinGecko serves this as a current snapshot only (no history)",
                    "orphan_cleanup.sql CJ, then archive_backfill.py --run: totalSupply() at past blocks (decision 6)",
                    "free (archive RPC) — after SQL CJ", "")
        return ("CoinGecko serves this as a current snapshot only (no history)",
                "historical totalSupply() at past blocks — wired only for the on-chain-first tokens "
                "(config.TOTAL_SUPPLY_ONCHAIN_FIRST); else rebuild from mint / burn Transfer events", "probe first", "")
    if base == "chain" or src.startswith("chain"):
        why = ha.why_short(p, metric, str(first)[:10] if first else None)
        if why.startswith("archive_backfill.py --run"):
            return ("point-in-time reads started late", "historical eth_call at past blocks — " + why,
                    "free (archive RPC)", "archive_backfill.py")
        return (why or "a chain read with no archive route",
                "rebuild from Transfer / mint / burn events (log scan from deployment)", "free (explorer / RPC logs)",
                "")
    if base in ("explorer", "etherscan_logs"):
        return ("an event-log scan", "re-scan the logs from deployment (free explorer / RPC logs)",
                "free", "token_metrics.py")
    if base in SEEDS:
        return ("routine runs read forward", f"the source's own seed: token_metrics.py --seed {SEEDS[base]}",
                "free", f"--seed {SEEDS[base]}")
    if base == "near_bigquery":
        return ("BigQuery reads", "token_metrics.py --seed near_bigquery (BigQuery quota — say the bytes first)",
                "BigQuery quota (Jake's call)", "")
    if base == "dune":
        return ("a Dune query", "re-run the Dune query over the longer range (DUNE_API_KEY on file — credits)",
                "Dune credits", "")
    if base == "artemis":
        return ("an Artemis export", "Artemis history (key on file)", "Artemis credits", "")
    if base == "manual" or row.get("status") == "manual":
        return ("hand-entered readings", "the project's own archive (reports, posts) — entered as manual rows",
                "manual", "")
    if base in PROBE_FIRST:
        return (f"{base}: does it serve history? (unknown until probed)",
                f"probe {base} for a history endpoint, then the project's own archive / Wayback snapshots",
                "probe first", "")
    return (f"{base}: no history route recorded", "probe the source for history", "probe first", "")


def structural_na(p: dict, metric: str, row: dict) -> str:
    """Why a series is n/a BY STRUCTURE — no history can fill it, so it is no gap (item 8): the Data row's status is
    'n/a' (no such mechanism for this project), or the issuance is declared zero (config.issuance_declared_zero)."""
    if str(row.get("status") or "").lower().startswith("n/a"):
        return "no such mechanism (Data status n/a)"
    if metric in ("gross_issuance_tokens", "emissions_tokens") and config.issuance_declared_zero(p["name"]):
        return "issuance declared zero"
    return ""


def gap_rows(wb, asof: pd.Timestamp) -> list[dict]:
    import trajectory
    period = int(config.TRAJECTORY["period_days"])
    data = _data_rows(wb)
    ws = wb[trajectory.SHEET]
    hr = next((r for r in range(1, 12) if ws.cell(r, 1).value == "Project"), None)
    wanted: dict = {}
    for r in range(hr + 1, ws.max_row + 1) if hr else ():
        p = ws.cell(r, 1).value
        if p:
            for m in _metrics_in(ws.cell(r, trajectory.COL["Q0"]).value):
                wanted.setdefault(str(p), set()).add(m)
    out = []
    for name in sorted(wanted):
        p = config.PROJECT_BY_NAME.get(name)
        # THE 15 PORTFOLIO PROJECTS ONLY (item 8): parked names are not on the to-do list
        if not p or name not in config.CREDIBILITY_PROJECTS:
            continue
        for m in sorted(wanted[name]):
            row = data.get(f"{name}|{m}") or {}
            if structural_na(p, m, row):
                continue
            states = {q: quarter_state(row, k, asof, period) for q, (_c, k) in QCOLS.items()}
            bonus = {q: quarter_state(row, k, asof, period) for q, (_c, k) in BONUS.items()}
            gap = not all(full for _s, full in states.values())
            reason, fix, cost, cmd = fix_for(p, m, row) if gap else ("", "", "", "")
            out.append({"project": name, "metric": m, "kind": row.get("kind") or "", "source": row.get("source") or "",
                        "tier": row.get("tier") or "",
                        "earliest": "" if pd.isna(pd.to_datetime(str(row.get("first_date") or "")[:10], errors="coerce"))
                        else str(row.get("first_date"))[:10],
                        **{q: s for q, (s, _f) in states.items()}, **{q: s for q, (s, _f) in bonus.items()},
                        "gap": "GAP" if gap else "ok",
                        "blocking_reason": reason, "cheapest_fix": fix, "fix_cost": cost,
                        "run_by_run_fixes": "yes" if cmd else "no"})
    return out


def write(rows: list[dict], path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def fix_commands(rows: list[dict]) -> list[list[str]]:
    """The free fixes the gaps call for, each once: the daily run (DefiLlama / CoinGecko re-reads, log scans), the
    archive backfill, then each source's seed."""
    cmds, seen = [], set()
    for r in rows:
        if r["gap"] != "GAP" or r["run_by_run_fixes"] != "yes":
            continue
        _reason, _fix, _cost, cmd = fix_for(config.PROJECT_BY_NAME[r["project"]], r["metric"],
                                            {"source": r["source"], "first_date": r["earliest"]})
        if cmd in seen or not cmd:
            continue
        seen.add(cmd)
        if cmd == "token_metrics.py":
            cmds.append([sys.executable, "token_metrics.py"])
        elif cmd == "archive_backfill.py":
            cmds.append([sys.executable, "archive_backfill.py", "--run", "--budget-min", "60"])
        elif cmd.startswith("--seed "):
            cmds.append([sys.executable, "token_metrics.py", "--seed", cmd.split()[1]])
    order = {"token_metrics.py": 0, "archive_backfill.py": 1}
    return sorted(cmds, key=lambda c: order.get(c[1], 2) if len(c) < 3 or c[2] != "--seed" else 2)


def _build(workbook: str | None):
    import openpyxl
    if workbook:
        wb = openpyxl.load_workbook(workbook)
        # the workbook's own date (write_data's comment: "... of the store as of YYYY-MM-DD"), never today's
        m = re.search(r"as of (\d{4}-\d{2}-\d{2})", str(getattr(wb["Data"]["A1"].comment, "text", "") or ""))
        return wb, (pd.Timestamp(m.group(1)) if m else pd.Timestamp.now("UTC").tz_localize(None).normalize())
    import build_workbook as bw
    import store as store_mod
    asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    tmp = Path(tempfile.mkdtemp()) / "history_gaps.xlsx"
    st = store_mod.Store(store_mod.DB_PATH)
    try:
        bw.build_workbook(st, tmp, asof=asof)
    finally:
        st.close()
    return openpyxl.load_workbook(tmp), asof


def summary(rows: list[dict]) -> list[str]:
    gaps = [r for r in rows if r["gap"] == "GAP"]
    by_cost: dict = {}
    for r in gaps:
        by_cost[r["fix_cost"]] = by_cost.get(r["fix_cost"], 0) + 1
    out = [f"HISTORY GAPS — {len(gaps)} of {len(rows)} (project, metric) series the trajectory reads are short of "
           f"four quarters (Q0..Q-3)", "  by cheapest fix: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_cost.items()))]
    for p in sorted({r["project"] for r in gaps}):
        mine = [r for r in gaps if r["project"] == p]
        out.append(f"  {p}: " + "; ".join(f"{r['metric']} (from {r['earliest'] or '—'}; {r['fix_cost']})"
                                          for r in mine[:8]) + (f" … +{len(mine) - 8}" if len(mine) > 8 else ""))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook")
    ap.add_argument("--run-fixes", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    wb, asof = _build(a.workbook)
    rows = gap_rows(wb, asof)
    print(f"wrote {write(rows, Path(a.out))}")
    for line in summary(rows):
        print(line)
    if not a.run_fixes:
        cmds = fix_commands(rows)
        if cmds:
            print("\n  free fixes --run-fixes would run, in order:")
            for c in cmds:
                print("    python " + " ".join(c[1:]))
        return 0
    import progress
    for i, c in enumerate(fix_commands(rows), 1):
        print(f"\n=== fix {i}: python {' '.join(c[1:])}", flush=True)
        t0 = time.time()
        with progress.Heartbeat(f"history fix {i}: {' '.join(c[1:])}"):
            rc = subprocess.call(c, cwd=str(ROOT))
        print(f"=== fix {i} exit {rc} ({time.time() - t0:,.0f}s)", flush=True)
    wb, asof = _build(a.workbook)
    rows = gap_rows(wb, asof)
    print(f"\nafter the fixes: wrote {write(rows, Path(a.out))}")
    for line in summary(rows):
        print(line)
    return 0


if __name__ == "__main__":
    import progress
    progress.line_buffered()
    sys.exit(main())
