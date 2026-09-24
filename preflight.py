#!/usr/bin/env python3
"""
preflight.py — know what the run will do BEFORE it does it.

    python preflight.py              # the plan. Reads config only, touches NO network.
    python preflight.py --check      # connectivity check: hits every dependency, short timeouts.
    python preflight.py --check --timeout 5

The plan answers: which metrics will be attempted on the first run, grouped by tier and named
with the source they will hit; which will NOT be attempted and why; every external dependency;
every credential; and a realistic estimate of how long the first backfill takes.

The check answers one question fast: is anything unreachable? It makes one cheap call per
dependency so an unreachable RPC surfaces in seconds rather than forty minutes into a backfill.
Neither mode writes to the store or the workbook.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch.chain import (METHOD_REQUIRED_KINDS, READABLE_BURN_METHODS, REFERENCE_ONLY_KINDS,  # noqa: E402
                         allow_unverified, rpc_endpoints)
from fetch.scrape import entry_ready, load_registry  # noqa: E402

RULE = "=" * 100
THIN = "-" * 100


# ---------------------------------------------------------------------------------------
# What each adapter will attempt
# ---------------------------------------------------------------------------------------
def tier1_plan() -> list[tuple[str, str, str]]:
    """(project, metric, source) for everything the free APIs will attempt."""
    out = []
    for p in config.PROJECTS:
        name = p["name"]
        if p.get("coingecko_id"):
            for m in ("price_usd", "market_cap_usd", "volume_usd", "circulating_supply_implied",
                      "circulating_supply", "total_supply", "max_supply", "fdv_usd"):
                out.append((name, m, f"CoinGecko /coins/{p['coingecko_id']}"))
        if p.get("defillama_fees_slug"):
            for m in ("fees_usd", "revenue_usd", "holders_revenue_usd"):
                out.append((name, m, f"DefiLlama /summary/fees/{p['defillama_fees_slug']}"))
        if p.get("defillama_protocol"):
            out.append((name, "protocol_tvl_usd", f"DefiLlama /protocol/{p['defillama_protocol']}"))
        if p.get("defillama_chain"):
            out.append((name, "tvl_usd", f"DefiLlama /v2/historicalChainTvl/{p['defillama_chain']}"))
            out.append((name, "stablecoin_supply_usd", f"DefiLlama /stablecoincharts/{p['defillama_chain']}"))
            if 1 in p["archetypes"]:
                out.append((name, "rwa_defillama_usd", "DefiLlama RWA category, aggregated per chain"))
        sched = p.get("issuance_schedule") or {}
        if sched.get("steps"):
            out.append((name, "gross_issuance_tokens", "schedule:config (no network)"))
            if sched.get("also_emissions"):
                out.append((name, "emissions_tokens", "schedule:config (no network)"))
        # A RESTATED COLUMN FILLS FROM ITS SOURCE COLUMN, so it is planned wherever that is.
        planned = {m for n, m, _s in out if n == name}
        for metric, spec in config.metric_restatements(name).items():
            if spec["equals"] in planned:
                out.append((name, metric, f"restated from {spec['equals']} (no network)"))
    return out


def tier2_plan() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(attempted, skipped) for contract reads, applying every gate the adapter applies."""
    attempted, skipped = [], []
    for p in config.PROJECTS:
        name = p["name"]
        for key, c in (p.get("contracts") or {}).items():
            metric = {"erc20_total_supply": "total_supply", "burn_address_balance": "burn_address_balance",
                      "ve_total_supply": "locked_tokens", "buyback_fund_balance": "buyback_fund_balance",
                      "treasury_holding": "treasury_holding_tokens",
                      "spl_mint": "total_supply", "spl_token_account": "burn_address_balance"}.get(c["kind"], key)
            where = f"{key} on {c['chain']}"
            # Reference-only entries are kept in config for the mechanism (and for eth_getCode),
            # and no metric is read from them. The plan must say that, or a Firepit reads as a
            # totalSupply call that never happens.
            if c["kind"] in REFERENCE_ONLY_KINDS:
                skipped.append((name, c["kind"], f"{where}: reference only, no metric read from it"))
                continue
            if c.get("ambiguous"):
                skipped.append((name, metric, f"{where}: address AMBIGUOUS, none chosen"))
                continue
            if c["kind"] == "burn_address_balance" and config.burn_mechanism(p).get("status") == "refuted":
                skipped.append((name, metric, f"{where}: burn MECHANISM refuted — no address balance models it"))
                continue
            if c["kind"] == "burn_address_balance" and p.get("burn_read_method") not in READABLE_BURN_METHODS:
                skipped.append((name, metric, f"{where}: burn is {p.get('burn_read_method')}, not a transfer"))
                continue
            if c["chain"] not in config.EVM_CHAINS:
                skipped.append((name, metric, f"{where}: chain not covered by the EVM adapter"))
                continue
            if c["kind"] in METHOD_REQUIRED_KINDS and not c.get("read_method"):
                skipped.append((name, metric, f"{where}: lock read method not established"))
                continue
            if not c.get("verified") and not allow_unverified():
                skipped.append((name, metric, f"{where}: address UNVERIFIED (set TOKEN_METRICS_ALLOW_UNVERIFIED=1 to read anyway)"))
                continue
            if not c.get("address"):
                skipped.append((name, metric, f"{where}: no address on file"))
                continue
            how = ("underlying.balanceOf(escrow)" if c.get("read_method") == "escrow_balance_of"
                   else "balanceOf(holder)" if c["kind"] in ("burn_address_balance", "buyback_fund_balance",
                                                             "treasury_holding")
                   else "totalSupply()")
            attempted.append((name, metric, f"{where} via {how}"))
    return attempted, skipped


def protocol_api_plan() -> list[tuple[str, str, str]]:
    """Protocol HTTP APIs: a chain node (Tron) or a protocol info endpoint (Hyperliquid).

    Neither uses a chain RPC and neither needs a key, so they are listed separately from the
    contract reads rather than being lumped in with tier 2.
    """
    out = []
    for p in config.PROJECTS:
        api = p.get("node_api") or {}
        if not api:
            continue
        base = (api.get("endpoints") or [api.get("endpoint", "?")])[0]
        out.append((p["name"], api.get("metric", "?"),
                    f"{api['kind']} -> POST {base}{api.get('path', '')}"))
    return out


def explorer_plan() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(attempted, skipped) for Transfer-event scans through block-explorer APIs.

    Attempted only where a routed explorer has its key in .env — the same test fetch/logscan.py
    applies. The key itself is never printed, only which variable is missing.
    """
    from fetch.explorer import ExplorerLogs
    ex = ExplorerLogs()
    attempted, skipped = [], []
    for p in config.PROJECTS:
        for sc in p.get("log_scans") or []:
            cid = config.CHAIN_IDS.get(sc["chain"])
            order = config.explorer_order(cid) if cid is not None else []
            usable = ex.configured(cid) if cid is not None else []
            what = (f"{sc['key']}: Transfer {'IN to' if sc['direction'] == 'in' else 'OUT of'} "
                    f"{len(sc['holders'])} holder(s) on {sc['chain']}"
                    + ("" if sc.get("store") else " — runs and reconciles, STORES NOTHING (attribution held)"))
            if usable:
                attempted.append((p["name"], sc["metric"], f"{what} via {' -> '.join(usable)}"))
            elif not order:
                skipped.append((p["name"], sc["metric"], f"{what}: no explorer serves logs free on this chain"))
            else:
                skipped.append((p["name"], sc["metric"],
                                f"{what}: no key in .env ({', '.join(config.EXPLORERS[n]['key_env'] for n in order)})"))
    return attempted, skipped


def tier3_plan() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    attempted, skipped = [], []
    for e in load_registry():
        ok, why = entry_ready(e)
        proj, metric = e.get("project", "?"), e.get("metric", "?")
        if ok:
            attempted.append((proj, metric, f"tier {e.get('tier')} {e['method']} {e['url']}"))
        else:
            restated = config.metric_restatements(proj).get(metric)
            if restated:
                why = f"{why} — NOT the route: restated from {restated['equals']} (see tier 1)"
            skipped.append((proj, metric, why))
    return attempted, skipped


def tier4_plan() -> tuple[list, list]:
    attempted, skipped = [], []
    for p in config.PROJECTS:
        for metric, q in (p.get("dune_queries") or {}).items():
            if q.get("query_id"):
                attempted.append((p["name"], metric, f"Dune query {q['query_id']}"))
            else:
                skipped.append((p["name"], metric, "no query_id in config"))
    return attempted, skipped


# ---------------------------------------------------------------------------------------
# Dependencies, credentials, timing
# ---------------------------------------------------------------------------------------
def dependencies() -> dict:
    chains = defaultdict(list)
    for name, _metric, where in tier2_plan()[0]:
        for chain in config.EVM_CHAINS:
            if f"on {chain}" in where:
                chains[chain].append(name)
    domains = sorted({e["url"].split("/")[2] for e in load_registry() if entry_ready(e)[0]})
    # two shapes: a chain node with a fallback endpoint list (Tron), and a protocol info endpoint
    # with a POST body (Hyperliquid). Neither needs a chain or an RPC.
    node_apis = []
    for proj in config.PROJECTS:
        api = proj.get("node_api")
        if not api:
            continue
        endpoints = api.get("endpoints") or ([api["endpoint"]] if api.get("endpoint") else [])
        node_apis.append((proj["name"], endpoints, api.get("kind")))
    return {"chains": dict(chains), "scrape_domains": domains, "node_apis": node_apis,
            "apis": ["api.llama.fi", "stablecoins.llama.fi", "api.coingecko.com"],
            "dune_needed": bool(tier4_plan()[0])}


def credentials() -> list[tuple[str, str, str, str]]:
    """(variable, required for run one?, where to get it, what happens if missing)."""
    return [
        ("DUNE_API_KEY", "No — nothing needs it on run one",
         "dune.com account, Settings -> API",
         "Tier 4 is skipped entirely. No Dune query ids are configured, so nothing is lost today. "
         "Skips with a gap row, never fails the run."),
        ("COINGECKO_API_KEY", "No — optional",
         "coingecko.com/en/developers/dashboard (free demo key)",
         "Falls back to the public tier, which is slower and rate-limited harder. "
         "A 429 is retried with backoff, then logged and gapped."),
        ("RPC_ETHEREUM / RPC_BSC / RPC_BASE / RPC_POLYGON / RPC_UNICHAIN", "No — defaults are built in",
         "Any provider, or leave blank to use the public endpoints in config.DEFAULT_RPC",
         "Uses the public fallback list. If every endpoint for a chain fails, that chain's reads "
         "are logged as failures and gapped; other chains and tiers continue."),
        ("TOKEN_METRICS_ALLOW_UNVERIFIED", "No — leave unset",
         "n/a, set to 1 only deliberately",
         "Unverified contract addresses stay refused with a gap row. Setting it to 1 reads them "
         "anyway and flags every resulting value in the Review Queue."),
        ("TOKEN_METRICS_SOURCES / TOKEN_METRICS_DB / TOKEN_METRICS_CACHE", "No — defaults are fine",
         "n/a",
         "Defaults to sources.yaml, metrics.db and .cache/scrape in the repo root."),
    ]


def timing_estimate() -> list[tuple[str, str, str]]:
    """(stage, estimate, what drives it). Derived from the actual call counts and rate limits."""
    cg_projects = sum(1 for p in config.PROJECTS if p.get("coingecko_id"))
    cg_calls = cg_projects * 2
    cg_seconds = cg_calls * 2.2

    dl_calls = 0
    for p in config.PROJECTS:
        dl_calls += 3 if p.get("defillama_fees_slug") else 0
        dl_calls += 1 if p.get("defillama_protocol") else 0
        dl_calls += 2 if p.get("defillama_chain") else 0
    dl_calls += 1                      # /protocols for the RWA category
    rwa_protocol_calls = 25            # one per RWA-category protocol on a chain we track; order of magnitude
    dl_seconds = (dl_calls + rwa_protocol_calls) * 0.25

    chain_reads = len(tier2_plan()[0])
    chain_seconds = chain_reads * 3 * 0.4   # ~3 RPC calls each (symbol, decimals, the read)

    scrape_entries = len(tier3_plan()[0])
    scrape_low, scrape_high = scrape_entries * 10, scrape_entries * 45

    total_low = cg_seconds + dl_seconds + chain_seconds + scrape_low
    total_high = cg_seconds * 1.5 + dl_seconds * 2 + chain_seconds * 2 + scrape_high

    def fmt(sec):
        return f"{sec/60:.0f} min" if sec >= 90 else f"{sec:.0f} s"

    return [
        ("Tier 1 CoinGecko", fmt(cg_seconds),
         f"{cg_calls} calls at a 2.2s floor between them ({cg_projects} projects x 2). This is the single "
         f"biggest fixed cost and is pure rate limiting, not bandwidth."),
        ("Tier 1 DefiLlama", fmt(dl_seconds),
         f"~{dl_calls + rwa_protocol_calls} calls at a 0.25s floor. The RWA category walks one call per "
         f"RWA protocol, so this is the least predictable part of tier 1."),
        ("Tier 2 contract reads", fmt(chain_seconds),
         f"{chain_reads} reads, ~3 RPC calls each. Fast unless a public endpoint is slow, in which case "
         f"the fallback list is tried in order."),
        ("Tier 3 page scrapes", f"{fmt(scrape_low)} to {fmt(scrape_high)}",
         f"{scrape_entries} pages, each a full headless Chromium load waiting for networkidle. The widest "
         f"variance in the run. Cached per day, so a same-day re-run skips them entirely."),
        ("Tier 4 Dune", "0 s", "No query ids configured, so tier 4 is skipped."),
        ("Workbook build + recalc", "~5 s", "Local only, no network."),
        ("TOTAL, first run", f"{fmt(total_low)} to {fmt(total_high)}",
         "Full history on run one. Later runs re-fetch only a trailing 30-day window and skip cached "
         "scrapes, so they are substantially quicker."),
    ]


# ---------------------------------------------------------------------------------------
def print_plan() -> None:
    t1 = tier1_plan()
    t2_go, t2_no = tier2_plan()
    tapi = protocol_api_plan()
    t3_go, t3_no = tier3_plan()
    t4_go, t4_no = tier4_plan()
    tx_go, tx_no = explorer_plan()

    print(RULE)
    print("PRE-FLIGHT PLAN — what the first run will attempt. No network calls were made.")
    print(RULE)
    print(f"Universe: {len(config.PROJECTS)} projects   |   "
          f"Unverified addresses are {'READ (flag is set)' if allow_unverified() else 'REFUSED (default)'}")

    print()
    print(RULE)
    print("(a) WILL BE ATTEMPTED")
    print(RULE)
    for label, rows in (("TIER 1 — free APIs and config schedules", t1),
                        ("TIER 2 — contract reads", t2_go),
                        ("PROTOCOL HTTP APIs — no chain RPC, no key", tapi),
                        ("EXPLORER APIs — Transfer-event scans, reconciled to balanceOf", tx_go),
                        ("TIER 3/5 — protocol pages", t3_go),
                        ("TIER 4 — Dune backfill", t4_go)):
        print(f"\n{label}: {len(rows)} metric reads")
        print(THIN)
        if not rows:
            print("  (nothing)")
            continue
        by_project = defaultdict(list)
        for proj, metric, src in rows:
            by_project[proj].append((metric, src))
        for proj in sorted(by_project):
            items = by_project[proj]
            if label.startswith("TIER 1"):
                srcs = sorted({s.split(" ")[0] for _m, s in items})
                print(f"  {proj:<14} {len(items):>2} metrics   via {', '.join(srcs)}")
            else:
                for metric, src in items:
                    print(f"  {proj:<14} {metric:<26} {src}")

    print()
    print(RULE)
    print("(b) WILL NOT BE ATTEMPTED, AND WHY")
    print(RULE)
    for label, rows in (("TIER 2 — contract reads skipped", t2_no),
                        ("EXPLORER APIs — scans not run", tx_no),
                        ("TIER 3/5 — registry entries not usable", t3_no),
                        ("TIER 4 — Dune", t4_no)):
        print(f"\n{label}: {len(rows)}")
        print(THIN)
        if label.startswith("TIER 4"):
            print(f"  All {len(rows)} slots have no query_id. Tier 4 is skipped entirely on run one.")
            print("  Nothing is lost today: these are backfill slots for history that tiers 2 and 3 cannot give.")
            continue
        for proj, metric, why in rows:
            print(f"  {proj:<14} {metric:<26} {why}")

    deps = dependencies()
    print()
    print(RULE)
    print("(c) EXTERNAL DEPENDENCIES")
    print(RULE)
    print("\nHTTPS APIs (no credentials needed):")
    for d in deps["apis"]:
        print(f"  {d}")
    print("\nRPC endpoints, by chain (the fallback list is tried in order; the first that connects wins):")
    for chain, projects in sorted(deps["chains"].items()):
        eps = rpc_endpoints(chain)
        print(f"  {chain:<10} {len(eps)} endpoint(s), needed by: {', '.join(sorted(set(projects)))}")
        for e in eps:
            print(f"             {e}")
    print("\nProtocol HTTP APIs (no chain RPC, no key):")
    for name, eps, kind in deps["node_apis"]:
        print(f"  {name:<12} {kind:<16} {', '.join(eps)}")
    print("\nDomains the scraper will load with headless Chromium:")
    for d in deps["scrape_domains"]:
        print(f"  {d}")
    print(f"\nDune API required for run one? {'YES' if deps['dune_needed'] else 'NO — no query ids are configured'}")

    print()
    print(RULE)
    print("(d) CREDENTIALS AND ENVIRONMENT")
    print(RULE)
    for var, required, where, missing in credentials():
        print(f"\n  {var}")
        print(f"    required on run one : {required}")
        print(f"    where to get it     : {where}")
        print(f"    if missing          : {missing}")
    print("\n  NOTHING fails the run loudly. Every missing credential or unreachable source is logged to")
    print("  the Run Log tab and written to the Gap Report. The run always produces a workbook.")

    print()
    print(RULE)
    print("(e) HOW LONG THE FIRST BACKFILL TAKES")
    print(RULE)
    print(f"\n  {'STAGE':<26} {'ESTIMATE':<20} DRIVEN BY")
    print("  " + THIN)
    for stage, est, why in timing_estimate():
        print(f"  {stage:<26} {est:<20} {why[:60]}")
        for extra in [why[60:][i:i + 60] for i in range(0, len(why[60:]), 60)]:
            print(f"  {'':<26} {'':<20} {extra}")
    print("\n  These are wall-clock estimates dominated by deliberate rate limiting, not by data volume.")
    print("  A stalled run looks different: no new log line for more than ~60s. See RUNBOOK.md step 8.")


# ---------------------------------------------------------------------------------------
def print_check(timeout: int) -> int:
    """One cheap call per dependency. Returns a non-zero exit code if anything critical is down."""
    import requests
    from fetch.base import USER_AGENT

    print(RULE)
    print(f"CONNECTIVITY CHECK — one call per dependency, {timeout}s timeout each. No data is stored.")
    print(RULE)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    results, critical_down = [], 0

    def probe(label, fn, critical=True):
        nonlocal critical_down
        started = time.monotonic()
        try:
            detail = fn()
            ok, note = True, detail
        except Exception as e:  # noqa: BLE001
            ok, note = False, str(e)[:90]
            if critical:
                critical_down += 1
        elapsed = time.monotonic() - started
        results.append((label, ok, elapsed, note, critical))
        print(f"  {'OK ' if ok else 'DOWN'}  {label:<46} {elapsed:5.2f}s  {note[:44]}")

    print("\nFree APIs (tier 1) — these carry most of the run:")
    probe("DefiLlama api.llama.fi", lambda: f"{len(session.get('https://api.llama.fi/protocols', timeout=timeout).json())} protocols")
    probe("DefiLlama stablecoins.llama.fi",
          lambda: f"HTTP {session.get('https://stablecoins.llama.fi/stablecoincharts/Ethereum', timeout=timeout).status_code}")
    probe("CoinGecko api.coingecko.com",
          lambda: f"HTTP {session.get('https://api.coingecko.com/api/v3/ping', timeout=timeout).status_code}")

    print("\nRPC endpoints (tier 2) — first working endpoint per chain:")
    for chain in sorted(dependencies()["chains"]):
        endpoints = rpc_endpoints(chain)

        def probe_chain(eps=endpoints):
            errors = []
            for url in eps:
                try:
                    r = session.post(url, json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                                     timeout=timeout)
                    block = int(r.json()["result"], 16)
                    return f"block {block:,} via {url.split('//')[1][:28]}"
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{url.split('//')[1][:20]}: {type(e).__name__}")
            raise RuntimeError("; ".join(errors[:2]))

        probe(f"chain {chain}", probe_chain)

    print("\nProtocol HTTP APIs (no chain RPC, no key):")
    for name, eps, kind in dependencies()["node_apis"]:
        api = config.PROJECT_BY_NAME[name]["node_api"]

        def probe_node(base=eps[0], api=api):
            url = base.rstrip("/") + api.get("path", "")
            body = api.get("request") or {}
            r = session.post(url, json=body, timeout=timeout)
            payload = r.json()
            if api.get("balances_path"):      # a protocol info endpoint returning a balance list
                from fetch.base import json_path_get
                items = json_path_get(payload, api["balances_path"])
                coins = [str(i.get(api.get("coin_key", "coin"))) for i in items] if isinstance(items, list) else []
                hit = api.get("coin") in coins
                return f"HTTP {r.status_code}, {api.get('coin')} {'present' if hit else 'ABSENT'} in {len(coins)} balances"
            keys = api.get("response_keys") or []
            found = next((k for k in keys if k in payload), None)
            return f"HTTP {r.status_code}, key {found!r}" if found else f"HTTP {r.status_code}, none of {keys} present"

        probe(f"{name} {kind}", probe_node, critical=False)

    print("\nScraper targets (tier 3) — reachability only, not extraction:")
    for domain in dependencies()["scrape_domains"]:
        probe(f"https://{domain}",
              lambda d=domain: f"HTTP {session.get(f'https://{d}', timeout=timeout).status_code}",
              critical=False)

    print("\nLocal prerequisites:")

    def probe_playwright():
        """Launch exactly the way scrape.py does, including its executable_path fallback.

        Probing with a bare chromium.launch() reports DOWN on an image where the browser sits at
        an explicit path, which is a false alarm about a scraper that would actually work.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch(headless=True)
            except Exception:  # noqa: BLE001 — same fallback the adapter uses
                b = pw.chromium.launch(headless=True, executable_path="/opt/pw-browsers/chromium")
            v = b.version
            b.close()
        return f"Chromium {v}"

    probe("Playwright headless Chromium", probe_playwright, critical=False)
    probe("LibreOffice (recalc.py)",
          lambda: __import__("subprocess").run(["soffice", "--version"], capture_output=True, text=True,
                                               timeout=timeout).stdout.strip()[:40] or "present",
          critical=False)

    if os.environ.get("DUNE_API_KEY"):
        probe("Dune api.dune.com", lambda: f"HTTP {session.get('https://api.dune.com/api/v1/', timeout=timeout).status_code}",
              critical=False)
    else:
        print("  SKIP  Dune                                           no DUNE_API_KEY set, and none is needed on run one")

    print()
    print(RULE)
    down = [r for r in results if not r[1]]
    critical = [r for r in down if r[4]]
    optional = [r for r in down if not r[4]]
    if not down:
        print("ALL DEPENDENCIES REACHABLE. Safe to run: python token_metrics.py")
    else:
        print(f"{len(down)} unreachable: {len(critical)} critical, {len(optional)} optional.")
        for label, _ok, _el, note, crit in down:
            print(f"  {'CRITICAL' if crit else 'optional'}  {label}: {note}")
        if critical:
            print("\nCritical sources carry most of the run. Fix these before the first backfill;")
            print("see RUNBOOK.md step 12 for the usual causes.")
        else:
            print("\nNothing critical is down. The run will proceed and gap the optional sources.")
    print(RULE)
    return 1 if critical_down else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-flight plan and connectivity check.")
    ap.add_argument("--check", action="store_true", help="probe every dependency (makes network calls)")
    ap.add_argument("--timeout", type=int, default=10, help="per-probe timeout in seconds (default 10)")
    args = ap.parse_args()
    if args.check:
        return print_check(args.timeout)
    print_plan()
    print("\nRun `python preflight.py --check` to probe every dependency before the real run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
