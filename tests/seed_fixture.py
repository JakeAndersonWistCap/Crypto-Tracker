"""
tests/seed_fixture.py — build the workbook from SYNTHETIC data, for validating formulas.

    python tests/seed_fixture.py

Writes tests/_scratch/metrics.db and tests/_scratch/token_metrics.xlsx, then runs recalc.py.
Every source string is prefixed "FIXTURE:" — this is never real data and token_metrics.py
never uses it. Includes a manual override (World Mobile), a stale series (Fluid fees ends
20 days ago) and a missing series (peaq has no Dune data) so the flags can be eyeballed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "tests" / "_scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)
DB = SCRATCH / "metrics.db"
XLSX = SCRATCH / "token_metrics.xlsx"
os.environ["TOKEN_METRICS_DB"] = str(DB)
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import fetch  # noqa: E402
import store as store_mod  # noqa: E402
from build_workbook import build_workbook  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402
from fetch.schedule import Schedule  # noqa: E402

rng = np.random.default_rng(7)
TODAY = pd.Timestamp.now('UTC').tz_localize(None).normalize()
DAYS = 400
DATES = pd.date_range(TODAY - pd.Timedelta(days=DAYS - 1), TODAY, freq="D")


def series(level: float, drift: float = 0.0, noise: float = 0.05, n: int = DAYS) -> np.ndarray:
    steps = rng.normal(drift / n, noise / np.sqrt(n), n).cumsum()
    return level * np.exp(steps)


def frame(project: str, metric: str, values, source: str, dates=DATES, tier: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"date": dates[: len(values)], "project": project, "metric": metric,
                         "value": values, "source": f"FIXTURE:{source}", "tier": tier})


def main() -> int:
    if DB.exists():
        DB.unlink()
    st = store_mod.Store(DB)
    run_id = fetch.new_run_id()
    frames = []
    for i, p in enumerate(config.PROJECTS):
        name = p["name"]
        arch = p["archetypes"]
        price0 = float(rng.choice([0.05, 0.4, 2.5, 30, 180, 3200, 65000]))
        price = series(price0, drift=rng.normal(0, 0.4), noise=0.5)
        supply0 = float(rng.choice([21e6, 120e6, 500e6, 1e9, 10e9]))
        frames.append(frame(name, "price_usd", price, "coingecko", tier=1))
        frames.append(frame(name, "market_cap_usd", price * supply0 * (1 + np.linspace(0, 0.04, DAYS)), "coingecko", tier=1))
        frames.append(frame(name, "circulating_supply_implied", supply0 * (1 + np.linspace(0, 0.04, DAYS)), "coingecko:mcap/price", tier=1))
        frames.append(frame(name, "volume_usd", series(price0 * supply0 * 0.02, noise=0.6), "coingecko", tier=1))
        today_only = pd.DatetimeIndex([TODAY])
        frames.append(frame(name, "circulating_supply", [supply0 * 1.04], "coingecko", today_only, tier=1))
        frames.append(frame(name, "total_supply", [supply0 * 1.3], "coingecko", today_only, tier=1))
        frames.append(frame(name, "fdv_usd", [supply0 * 1.3 * price[-1]], "coingecko", today_only, tier=1))
        if name != "World Mobile":  # manual-only project
            fees = series(price0 * supply0 * 0.0004, drift=rng.normal(0.2, 0.5), noise=0.4)
            if name == "Fluid":      # stale case: series ends 20 days ago
                frames.append(frame(name, "fees_usd", fees[:-20], "defillama", tier=1))
            else:
                frames.append(frame(name, "fees_usd", fees, "defillama", tier=1))
            frames.append(frame(name, "revenue_usd", fees * 0.6, "defillama", tier=1))
            frames.append(frame(name, "holders_revenue_usd", fees * 0.3, "defillama", tier=1))
        if 1 in arch:
            frames.append(frame(name, "tvl_usd", series(price0 * supply0 * 0.3, noise=0.3), "defillama", tier=1))
            frames.append(frame(name, "stablecoin_supply_usd", series(price0 * supply0 * 0.1, noise=0.2), "defillama", tier=1))
            frames.append(frame(name, "rwa_defillama_usd", series(price0 * supply0 * 0.01, drift=0.5, noise=0.2), "defillama", tier=1))
            frames.append(frame(name, "tx_count", series(2e6, noise=0.3), "dune:1", tier=4))
            frames.append(frame(name, "active_addresses", series(3e5, noise=0.3), "dune:2", tier=4))
            frames.append(frame(name, "staked_tokens", series(supply0 * 0.5, noise=0.05), "dune:3", tier=4))
        if p.get("defillama_protocol"):
            frames.append(frame(name, "protocol_tvl_usd", series(price0 * supply0 * 0.5, noise=0.3), "defillama", tier=1))
        if 4 in arch:
            frames.append(frame(name, "gross_burn_tokens", series(supply0 * 0.0001, drift=rng.normal(0.3, 0.5), noise=0.5), "dune:4", tier=4))
        if 4 in arch or 1 in arch:
            if not (p.get("issuance_schedule") or {}).get("steps"):
                frames.append(frame(name, "gross_issuance_tokens", series(supply0 * 0.00012, drift=-0.1, noise=0.1), "dune:5", tier=4))
        if 2 in arch and name != "peaq":   # peaq left missing on purpose
            frames.append(frame(name, "supply_units", series(rng.choice([1500, 12000, 40000]), drift=0.3, noise=0.1), "dune:6", tier=4))
            frames.append(frame(name, "utilisation_pct", np.clip(series(0.35, noise=0.3), 0, 1), "dune:7", tier=4))
            frames.append(frame(name, "customer_revenue_usd", series(price0 * supply0 * 0.00005, drift=rng.normal(0.4, 0.5), noise=0.5), "dune:8", tier=4))
            frames.append(frame(name, "emissions_tokens", series(supply0 * 0.0002, drift=-0.2, noise=0.1), "dune:9", tier=4))
        if 3 in arch:
            frames.append(frame(name, "actual_buyback_usd", series(price0 * supply0 * 0.0002, noise=0.5), "dune:10", tier=4))
            frames.append(frame(name, "actual_buyback_tokens", series(supply0 * 0.0002 / 1.0, noise=0.5), "dune:11", tier=4))
            frames.append(frame(name, "emissions_tokens", series(supply0 * 0.00015, drift=-0.2, noise=0.1), "dune:9", tier=4))
            if name == "Sky":
                frames.append(frame(name, "locked_tokens", series(supply0 * 0.30, noise=0.04), "chain:lssky", tier=2))
            if name in ("Aerodrome", "Pendle"):
                frames.append(frame(name, "locked_tokens", series(supply0 * 0.45, noise=0.05), "chain:ve", tier=2))
                frames.append(frame(name, "locked_tokens_dashboard", series(supply0 * 0.45, noise=0.05) * 1.008, "scrape:dashboard", tier=3))
                frames.append(frame(name, "avg_lock_duration_days", series(900, noise=0.1), "dune:13", tier=4))
        if name == "Chainlink":
            frames.append(frame(name, "buyback_fund_balance", series(4.8e6, drift=0.6, noise=0.1), "chain:reserve", tier=2))
            frames.append(frame(name, "buyback_fund_balance_dashboard", series(4.8e6, drift=0.6, noise=0.1) * 1.004, "scrape:metrics.chain.link", tier=3))
        if name == "OriginTrail":
            frames.append(frame(name, "publisher_conviction_usd", series(2e6, drift=0.5, noise=0.2), "dune:14", tier=4))
        # tier 2 (contract read) and tier 3 (protocol dashboard) point-in-time snapshots
        if p.get("contracts"):
            frames.append(frame(name, "total_supply", [supply0 * 1.3], "chain:erc20", today_only, tier=2))
        if 4 in arch:
            frames.append(frame(name, "burn_address_balance", series(supply0 * 0.01, drift=0.2, noise=0.05), "chain:burn", tier=2))
        if p.get("self_reported_net_mint"):   # only protocols that actually publish it
            frames.append(frame(name, "net_mint_monthly", series(supply0 * 0.0002, noise=0.4) * -1, "scrape:dashboard", tier=3))
        if name == "Aave":
            frames.append(frame(name, "umbrella_staked_usd", series(3.2e9, noise=0.1), "scrape:app.aave.com", tier=3))
        if 3 in arch and name in ("Aerodrome", "Pendle"):
            frames.append(frame(name, "locked_tokens", series(supply0 * 0.45, noise=0.05), "chain:ve", tier=2))

    # deterministic issuance schedules from config, exactly as the real run produces them
    out = FetchOutput()
    Schedule().run(config.PROJECTS, None, out)
    frames.append(out.frame())
    allf = pd.concat(frames, ignore_index=True)
    n = st.upsert(allf)
    for p in config.PROJECTS:
        for src in ("defillama", "coingecko", "dune", "schedule:config"):
            ok = not (p["name"] == "Fluid" and src == "defillama")
            st.record_fetch(run_id, src, p["name"], 100, "ok" if ok else "failed", "" if ok else "HTTP 503 from FIXTURE (simulated outage)")
    st.record_fetch(run_id, "rwa.xyz", "Plume", 0, "unconfigured", "rwa_xyz_usd: no API on our tier — manual_overrides.csv only")
    st.record_fetch(run_id, "dune", "peaq", 0, "unconfigured", "supply_units: no query_id in config")

    csv = SCRATCH / "manual_overrides.csv"
    csv.write_text(
        "date,project,metric,value,source_note,entered_on\n"
        f"{TODAY.date()},World Mobile,revenue_usd,1250000,World Mobile investor update (FIXTURE),{TODAY.date()}\n"
        f"{TODAY.date()},World Mobile,actual_buyback_usd,410000,World Mobile investor update (FIXTURE),{TODAY.date()}\n"
        f"{TODAY.date()},World Mobile,supply_units,9800,World Mobile AirNode dashboard (FIXTURE),{TODAY.date()}\n"
        f"{TODAY.date()},Plume,rwa_xyz_usd,2100000000,rwa.xyz Plume page (FIXTURE),{TODAY.date()}\n"
        f"{TODAY.date()},Ethereum,rwa_xyz_usd,9800000000,rwa.xyz Ethereum page (FIXTURE),{TODAY.date()}\n"
    )
    m = st.load_overrides_csv(csv)
    st.record_fetch(run_id, "manual", None, m, "ok", f"{m} overrides loaded")

    # a review row of each kind, and the real gap detection against the seeded frame
    st.record_review(run_id, [
        {"project": "Solana", "metric": "gross_burn_tokens", "date": str(TODAY.date()), "value": 9.9e12,
         "prior_value": 1.2e6, "reason": "out_of_bounds", "action": "rejected", "source": "FIXTURE:scrape", "tier": 3},
        {"project": "Uniswap", "metric": "gross_burn_tokens", "date": str(TODAY.date()), "value": 1.0e8,
         "prior_value": 4.5e6, "reason": "change_threshold", "action": "stored_flagged", "source": "FIXTURE:scrape", "tier": 3},
        {"project": "PancakeSwap", "metric": "total_supply", "date": str(TODAY.date()), "value": None,
         "prior_value": None, "reason": "address_unverified", "action": "stored_flagged", "source": "FIXTURE:chain", "tier": 2},
    ])
    manual_keys = set(st.conn.execute("SELECT DISTINCT project, metric FROM manual_overrides").fetchall())
    from fetch.gaps import detect as detect_gaps
    from fetch.scrape import entry_ready, load_registry
    registry_reasons = {}
    for e in load_registry():
        ok, why = entry_ready(e)
        if not ok and e.get("project") and e.get("metric"):
            registry_reasons[(e["project"], e["metric"])] = why
    gaps = detect_gaps(config.PROJECTS, allf, manual_keys, registry_reasons, [])
    st.record_gaps(run_id, gaps)
    print(f"seeded {n} rows + {m} overrides + {len(gaps)} gaps into {DB}")

    build_workbook(st, XLSX, run_id=run_id)
    st.close()
    print(f"wrote {XLSX}")
    res = subprocess.run([sys.executable, str(ROOT / "recalc.py"), str(XLSX), "90"], capture_output=True, text=True)
    print(res.stdout.strip() or res.stderr.strip())
    try:
        j = json.loads(res.stdout.strip())
    except Exception:  # noqa: BLE001
        print("recalc produced no JSON"); return 1
    if j.get("status") != "success":
        print("RECALC ERRORS:", json.dumps(j.get("error_summary"), indent=1)[:4000])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
