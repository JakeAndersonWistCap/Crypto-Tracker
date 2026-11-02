#!/usr/bin/env python3
"""add_project.py — the onboarding kit's command (P3-7, Jake 2026-11-02). Reads projects/<slug>.yaml.

    python add_project.py aave --probe          READ-ONLY: each source answers, each contract read works, each wallet
                                                has a balance; then a COVERAGE FORECAST per reader column (number, or
                                                code + reason). Opens nothing writable — never the store.
    python add_project.py aave --backfill       history into the store for an ACTIVE project: the normal run's own
                                                backfill (fetch/backfill.py widens the window for short series) and the
                                                archive reads (archive_backfill), under the run lock (runlock.RunLock),
                                                within --budget-min; re-running continues where it stopped.

THE FORECAST reuses the build's own column knowledge: reader_column_map.csv (reader column -> audit column, written by
every build) and the audit workbook's formulas (each audit column's Data inputs, the INDEX/MATCH the build writes —
build_workbook._REF). Each Data input is then answered or not by this probe (project_intake.FIELD_METRICS: which YAML
field feeds which series). Without a built audit file + column map beside the store the forecast is per series.

Network: public, unauthenticated endpoints only (api.coingecko.com, api.llama.fi, the PUBLIC RPC lists in
config.DEFAULT_RPC — never the .env overrides, which may carry keys), paced (PACE_S between calls).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import project_intake as intake

ROOT = Path(__file__).resolve().parent
PACE_S = 0.5
TIMEOUT_S = 25
COINGECKO = "https://api.coingecko.com/api/v3"
LLAMA = "https://api.llama.fi"
SEL = {"decimals": "0x313ce567", "totalSupply": "0x18160ddd", "balanceOf": "0x70a08231"}
STATE_DIR = ROOT / ".cache" / "add_project"
A1_HISTORY_DAYS = 730            # "2 years where A1 needs it" (P3-7); other archetypes: the run's own history_days


# ============================================================================================== network (read-only)
class Net:
    """GET JSON and eth_call, paced. Both injectable for tests (`get`, `post`)."""

    def __init__(self, get=None, post=None, pace=PACE_S, sleep=time.sleep):
        self._get, self._post, self.pace, self.sleep, self._last = get, post, pace, sleep, 0.0

    def _wait(self):
        dt = time.monotonic() - self._last
        if dt < self.pace:
            self.sleep(self.pace - dt)
        self._last = time.monotonic()

    def get(self, url: str, params: dict | None = None):
        self._wait()
        if self._get:
            return self._get(url, params)
        import requests                                    # noqa: PLC0415
        r = requests.get(url, params=params, timeout=TIMEOUT_S, headers={"accept": "application/json"})
        r.raise_for_status()
        return r.json()

    def rpc(self, url: str, method: str, params: list):
        self._wait()
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        if self._post:
            return self._post(url, body)
        import requests                                    # noqa: PLC0415
        r = requests.post(url, json=body, timeout=TIMEOUT_S)
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise RuntimeError(str(j["error"])[:160])
        return j.get("result")


def public_rpcs(chain: str) -> list[str]:
    import config                                          # noqa: PLC0415
    return list(config.DEFAULT_RPC.get(chain) or [])


def eth_call(net: Net, chain: str, to: str, data: str) -> tuple[int | None, str]:
    """(int result, host) from the first public endpoint that answers, or (None, why)."""
    why = []
    for url in public_rpcs(chain):
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        try:
            res = net.rpc(url, "eth_call", [{"to": to, "data": data}, "latest"])
            if res in (None, "0x", ""):
                why.append(f"{host}: empty result")
                continue
            return int(res, 16), host
        except Exception as e:  # noqa: BLE001 — every endpoint is tried; the reasons are reported
            why.append(f"{host}: {type(e).__name__}: {str(e)[:80]}")
    return None, "; ".join(why) or f"no public RPC for chain {chain!r} in config.DEFAULT_RPC"


def _pad(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


# ============================================================================================== the probe
def probe(doc: dict, net: Net) -> list[dict]:
    """One row per check: {check, field, ok, detail, metrics}. TODO fields are listed as not asked."""
    rows = []

    def add(check, field, ok, detail):
        rows.append({"check": check, "field": field, "ok": ok, "detail": detail,
                     "metrics": intake.FIELD_METRICS.get(intake._top(field), ())})

    for path, why in intake.todos(doc):
        add("TODO", path, False, f"TODO: {why}")
    cg = intake.value(doc, "coingecko_id")
    if cg:
        try:
            j = net.get(f"{COINGECKO}/coins/{cg}", {"localization": "false", "tickers": "false",
                                                    "community_data": "false", "developer_data": "false"})
            md = j.get("market_data") or {}
            px = (md.get("current_price") or {}).get("usd")
            add("coingecko", "coingecko_id", px is not None,
                f"/coins/{cg}: price ${px:,.2f}, circulating {md.get('circulating_supply') or 0:,.0f}"
                if px is not None else f"/coins/{cg}: no market_data")
        except Exception as e:  # noqa: BLE001
            add("coingecko", "coingecko_id", False, f"/coins/{cg}: {type(e).__name__}: {str(e)[:100]}")
    for key, dtype in (("fees_slug", "dailyFees"), ("revenue_slug", "dailyRevenue"),
                       ("holders_revenue_slug", "dailyHoldersRevenue")):
        slug = intake.value(doc, f"defillama.{key}")
        if not slug:
            continue
        try:
            j = net.get(f"{LLAMA}/summary/fees/{slug}", {"dataType": dtype})
            chart = j.get("totalDataChart") or []
            add("defillama", f"defillama.{key}", bool(chart),
                f"summary/fees/{slug} {dtype}: {len(chart)} days" + (f", last {chart[-1][1]:,.0f} $/day" if chart else ""))
        except Exception as e:  # noqa: BLE001
            add("defillama", f"defillama.{key}", False, f"summary/fees/{slug} {dtype}: {type(e).__name__}: {str(e)[:100]}")
    slug = intake.value(doc, "defillama.tvl_slug")
    if slug:
        try:
            j = net.get(f"{LLAMA}/protocol/{slug}")
            tvl = j.get("tvl") or []
            add("defillama", "defillama.tvl_slug", bool(tvl),
                f"protocol/{slug}: {len(tvl)} TVL days" + (f", last ${tvl[-1].get('totalLiquidityUSD', 0):,.0f}"
                                                            if tvl else ""))
        except Exception as e:  # noqa: BLE001
            add("defillama", "defillama.tvl_slug", False, f"protocol/{slug}: {type(e).__name__}: {str(e)[:100]}")
    tokens = {}
    for i, _ in enumerate(doc.get("chains") or []):
        chain, tok = intake.value(doc, f"chains[{i}].chain"), intake.value(doc, f"chains[{i}].token_contract")
        dec = intake.value(doc, f"chains[{i}].decimals")
        if not (chain and tok):
            continue
        got, host = eth_call(net, chain, tok, SEL["decimals"])
        ok = got is not None and (dec is None or got == dec)
        add("contract", f"chains[{i}].decimals", ok,
            f"{chain} {tok[:10]}… decimals() = {got} via {host}" + ("" if ok or got is None else f" ≠ declared {dec}")
            if got is not None else f"{chain} {tok[:10]}… decimals(): {host}")
        sup, host = eth_call(net, chain, tok, SEL["totalSupply"])
        d = got if got is not None else (dec or 18)
        add("contract", f"chains[{i}].token_contract", sup is not None,
            f"{chain} totalSupply() = {sup / 10 ** d:,.2f} via {host}" if sup is not None else f"{chain} totalSupply(): {host}")
        tokens[chain] = (tok, d)
    for group in ("noncirculating_wallets", "insider_wallets"):
        for i, w in enumerate(intake.wallets(doc, group)):
            addr = intake.value(doc, f"{group}[{i}].address")
            if not addr or w.get("chain") not in tokens:
                continue
            tok, d = tokens[w["chain"]]
            bal, host = eth_call(net, w["chain"], tok, SEL["balanceOf"] + _pad(addr))
            add("wallet", f"{group}[{i}].address", bal is not None and bal > 0,
                f"{w['key']} {addr[:10]}… holds {bal / 10 ** d:,.2f} via {host}" if bal is not None else
                f"{w['key']} balanceOf: {host}")
    for sect, call in (("staking", "totalSupply"), ("buyback", "balanceOf")):
        addr = intake.value(doc, f"{sect}.{'contract' if sect == 'staking' else 'address'}")
        chain = (doc.get(sect) or {}).get("chain")
        if not addr or chain not in tokens:
            continue
        tok, d = tokens[chain]
        to, data = (addr, SEL["totalSupply"]) if call == "totalSupply" else (tok, SEL["balanceOf"] + _pad(addr))
        got, host = eth_call(net, chain, to, data)
        add("contract" if sect == "staking" else "wallet", f"{sect}.{'contract' if sect == 'staking' else 'address'}",
            got is not None, f"{sect} {addr[:10]}… {call}() = {got / 10 ** d:,.2f} via {host}" if got is not None else
            f"{sect} {call}(): {host}")
    # a SOURCED declaration with no network read of its own (max supply, the emission rate) answers from the file
    checked = {intake._top(r["field"]) for r in rows}
    for top in ("max_supply", "emission", "unlock_schedule"):
        if top not in checked and any(intake._top(p) == top and not intake.is_todo(f) for p, f in intake.fields(doc)):
            add("declared", top, True, f"declared in the YAML with its source ({top})")
    return rows


# ============================================================================================== the forecast
def answered_metrics(rows: list[dict]) -> dict:
    """metric -> (True, detail) when every check feeding it answered; (False, reason) otherwise (the first failure)."""
    out: dict = {}
    for r in rows:
        for m in r["metrics"]:
            prev = out.get(m)
            if r["ok"] and prev is None:
                out[m] = (True, r["detail"])
            elif not r["ok"] and (prev is None or prev[0]):
                out[m] = (False, f"{r['field']}: {r['detail']}")
    return out


def column_inputs(audit_path: Path, map_path: Path) -> dict:
    """(reader tab, reader header) -> the Data series its audit column reads (build_workbook._REF on the audit file's
    own formulas, every project row). The build's knowledge, read back — no second map."""
    import openpyxl                                        # noqa: PLC0415
    from openpyxl.utils import column_index_from_string     # noqa: PLC0415
    # build_workbook._REF verbatim — the grammar of every Data input the build writes (a test keeps the two equal; the
    # probe does not import the build)
    ref = re.compile(r'INDEX\(Data!\$([A-Z]+)\$\d+:\$[A-Z]+\$\d+,MATCH\((?:\$A\d+&"\|([a-z0-9_]+)"|"([A-Za-z.]+)\|([a-z0-9_]+)")')
    wb = openpyxl.load_workbook(audit_path, read_only=True)
    cache: dict = {}
    out: dict = {}
    with open(map_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not row.get("reader_tab") or not re.fullmatch(r"[A-Z]+", row.get("audit_column") or ""):
                continue
            sheet, col = row["audit_tab"], column_index_from_string(row["audit_column"])
            if (sheet, col) not in cache:
                ms: set = set()
                if sheet in wb.sheetnames:
                    for (v,) in wb[sheet].iter_rows(min_col=col, max_col=col, values_only=True):
                        if isinstance(v, str) and v.startswith("="):
                            for m in ref.findall(v):
                                if m[1]:                   # this project's own series (not a fixed "X|metric")
                                    ms.add(m[1])
                cache[(sheet, col)] = ms
            out.setdefault((row["reader_tab"], row["reader_header"]), set()).update(cache[(sheet, col)])
    wb.close()
    return out


def forecast(project: dict, rows: list[dict], audit_path: Path | None = None, map_path: Path | None = None) -> list:
    """[(where, verdict, reason)]: verdict 'number' or a code ('not wired' for a TODO or a failed input, 'n/a' for a
    series no intake field feeds)."""
    import config                                          # noqa: PLC0415
    have = answered_metrics(rows)
    applicable = set(config.metrics_for_project(project))

    def judge(ms: set) -> tuple[str, str]:
        ms = {m for m in ms if m in config.METRICS and not config.METRICS[m].get("view_only")}
        if not ms:
            return "derived", "computed from other columns / no Data input of its own"
        if not (ms & applicable):
            return "n/a", f"{', '.join(sorted(ms))}: not a series of this project's archetypes"
        for m in sorted(ms & applicable):
            ok, why = have.get(m, (False, "no intake field feeds it (a config.py-only route or a derived view)"))
            if not ok:
                return ("not wired" if "TODO" in why or m in have else "n/a"), f"{m}: {why}"
        return "number", ", ".join(f"{m} ✓" for m in sorted(ms & applicable))

    if audit_path and map_path and Path(audit_path).exists() and Path(map_path).exists():
        cols = column_inputs(Path(audit_path), Path(map_path))
        return [(f"{tab} | {head}", *judge(ms)) for (tab, head), ms in sorted(cols.items())]
    return [(f"series {m}", *judge({m})) for m in sorted(applicable)]


# ============================================================================================== backfill
def backfill(name: str, slug: str, archetypes: list, budget_s: float, *, steps=None, db_path=None,
             state_dir: Path | None = None, lock=None, clock=time.monotonic, say=print) -> int:
    """Run the backfill steps under the run lock, within budget_s; a finished step is recorded in a state file and
    skipped on the next call (resumable). Returns 0 when every step is done, 3 when the budget ran out first."""
    import store as store_mod                              # noqa: PLC0415
    from runlock import RunLock                            # noqa: PLC0415
    db_path = db_path or store_mod.DB_PATH
    state_file = (state_dir or STATE_DIR) / f"{slug}.json"
    steps = steps if steps is not None else default_steps(name, archetypes, db_path)
    t0 = clock()
    with (lock or RunLock)(db_path, f"add_project --backfill {name}", say=say):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {"done": []}
        for key, fn in steps:
            if key in state["done"]:
                say(f"  {key}: done on an earlier call — skipped")
                continue
            left = budget_s - (clock() - t0)
            if left <= 0:
                say(f"BUDGET USED ({budget_s / 60:.0f} min) before step {key!r} — run the same command again to "
                    f"continue")
                return 3
            say(f"  {key}: running (budget left {left / 60:.1f} min)")
            if fn(left) is False:
                say(f"  {key}: stopped inside its budget — run the same command again to continue")
                return 3
            state["done"].append(key)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(state), encoding="utf-8")
    say(f"BACKFILL COMPLETE for {name} (state {state_file})")
    return 0


def default_steps(name: str, archetypes: list, db_path) -> list:
    """The existing paths, unchanged: (1) the normal run scoped to the project — its backfill.plan asks every short
    series for the full history window; (2) archive_backfill for the state-based series (A1: 2 years)."""
    def run_fetch(_left):
        import logging                                     # noqa: PLC0415
        import token_metrics as tm                          # noqa: PLC0415
        return tm._main(tm.parse_args(["--project", name]), logging.getLogger("add_project")) == 0

    def run_archive(left):
        import archive_backfill                            # noqa: PLC0415
        from fetch import archive as ar                    # noqa: PLC0415
        was = ar.ARCHIVE_DAYS
        if 1 in archetypes:
            ar.ARCHIVE_DAYS = max(was, A1_HISTORY_DAYS)
        try:
            return archive_backfill._main(["--run", "--project", name, "--db", str(db_path),
                                           "--budget-min", f"{max(1.0, left / 60):.0f}"]) == 0
        finally:
            ar.ARCHIVE_DAYS = was
    return [("fetch", run_fetch), ("archive", run_archive)]


# ============================================================================================== main
def load_doc(slug: str) -> tuple[Path, dict]:
    path = intake.projects_dir() / f"{slug}.yaml"
    if not path.exists():
        raise SystemExit(f"no {path} — copy projects/_intake_template.yaml to it and fill it")
    return path, intake.read(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("slug")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--backfill", action="store_true")
    ap.add_argument("--budget-min", type=float, default=60.0)
    ap.add_argument("--audit", default=str(ROOT / "token_metrics_audit.xlsx"))
    ap.add_argument("--column-map", default=str(ROOT / "reader_column_map.csv"))
    args = ap.parse_args(argv)
    path, doc = load_doc(args.slug)
    errs = intake.validate(doc, path.name)
    print(f"{path.name}: status {doc.get('status')}, {len(intake.fields(doc))} fields, "
          f"{len(intake.todos(doc))} TODO, {len(errs)} problem(s)")
    for e in errs:
        print(f"  PROBLEM {e}")
    if errs:
        return 2
    import config                                          # noqa: PLC0415
    name = intake.value(doc, "name")
    clash = name in config.PROJECT_BY_NAME and not any(
        (p.get("intake") or {}).get("slug") == args.slug for p in config.PROJECTS)
    if clash:
        print(f"  NOTE: {name!r} is already declared in config.py — this YAML cannot be made active (it would be "
              "refused); it is probed as a draft")
    if args.backfill:
        p = config.PROJECT_BY_NAME.get(name)
        if doc.get("status") != "active" or p is None or clash:
            print(f"REFUSED: --backfill needs an ACTIVE YAML project loaded into config ({path.name} is "
                  f"{doc.get('status')}{', and the name is a config.py project' if clash else ''})")
            return 2
        return backfill(name, args.slug, p["archetypes"], args.budget_min * 60)
    rows = probe(doc, Net())
    print("\nCHECKS (read-only; nothing is written)")
    for r in rows:
        print(f"  {'OK  ' if r['ok'] else 'FAIL' if r['check'] != 'TODO' else 'TODO'} {r['check']:<9} {r['field']:<34} "
              f"{r['detail']}")
    project = intake.to_project(doc, args.slug, vars(config))
    print("\nCOVERAGE FORECAST (" + ("per reader column, from the build's column map and the audit formulas)"
                                    if Path(args.audit).exists() and Path(args.column_map).exists() else
                                    "per series — no built audit file / reader_column_map.csv beside the store)"))
    fc = forecast(project, rows, Path(args.audit), Path(args.column_map))
    for where, verdict, why in fc:
        print(f"  {where:<60} {verdict:<9} {why[:160]}")
    n = sum(1 for _, v, _ in fc if v == "number")
    print(f"\n{n} of {len(fc)} forecast as numbers; "
          f"{sum(1 for _, v, _ in fc if v == 'not wired')} not wired; {sum(1 for _, v, _ in fc if v == 'n/a')} n/a")
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
