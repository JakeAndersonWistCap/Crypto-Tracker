#!/usr/bin/env python3
"""ethstore_backfill.py — Ethereum's staking-yield and consensus-reward history from ETH.Store.
Jake's probe, 2026-09-30: "Raise the ETH.STORE backfill cap to the real limit (365 in one run)".

    python ethstore_backfill.py              read every missing day of the last 365 in one run
    python ethstore_backfill.py --max 30     at most 30 calls
    python ethstore_backfill.py --dry-run    what is held and missing; no network call

One call per beaconchain-day (/api/v1/ethstore/{day}), cached for good in
beaconchain-history.json, so a re-run reads only what is still missing. THE CAP IS
beaconcha.in's OWN COUNTER: every answer's x-ratelimit-remaining-month is read and the run stops
with `reserve` calls left; pacing follows x-ratelimit-limit-minute. A 429 stops the run at once;
its Retry-After is kept for /ethstore/{day} only (/ethstore/latest keeps its own wait), and the
two limits are printed.

Writes staking_yield_pct (source "beaconchain") and consensus_rewards_ethstore_tokens (source
"beaconchain:ethstore.consensus_rewards_sum_wei") through Store.upsert. The issuance history is
a read-time leg of the second (config.history_legs) — nothing is written under
gross_issuance_tokens. Run it AFTER the monthly quota resets (2026-10-01 00:00 UTC).

The key is read from .env (BEACONCHAIN_API_KEY) and never printed: every line is redacted.
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

import config
from fetch.base import FetchOutput, today


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--max", type=int, default=None,
                    help="at most this many calls (default: every missing day; the server's counter still caps it)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from fetch.archive import load_env
    load_env()
    from fetch.base import install_redaction
    install_redaction()
    from fetch.beaconchain import BeaconChain, _now
    p = config.PROJECT_BY_NAME["Ethereum"]
    spec = p["beaconchain"]
    h = spec["history"]
    bc = BeaconChain()
    try:
        held = json.loads(bc._hist_file().read_text())
    except (OSError, ValueError):
        held = {}
    days = held.get("days", {})
    want = [today() - pd.Timedelta(days=i) for i in range(2, int(h.get("days", 365)) + 2)]
    missing = [d for d in want if str((d - bc.GENESIS_DAY0).days) not in days]
    print(f"ETH.Store history: {len(want) - len(missing)} of {len(want)} day(s) held, "
          f"{len(missing)} missing")
    if held.get("retry_at"):
        print(f"  /ethstore/{{day}} last asked to wait {float(held.get('retry_after_s', 0)):,.0f}s "
              f"(on {held.get('retry_asked_on')}), until {held['retry_at']} UTC; now {_now().floor('s')}")
    if a.dry_run or not missing:
        return 0
    if not bc._key(spec):
        print(f"{spec['key_env']} is not set in .env — nothing read.")
        return 2
    out = FetchOutput()
    res = bc._history("Ethereum", spec, out, max_calls=a.max if a.max is not None else len(missing))
    import store as store_mod
    st = store_mod.Store(a.db)
    n = sum(st.upsert(f) for f in out.frames)
    for e in out.log:
        print(f"  [{e.status}] {e.message}")
    lim = res.get("limits") or {}
    print(f"{res['calls']} call(s); {n} row(s) upserted; {res['still']} day(s) still missing")
    if lim:
        print("  beaconcha.in limits after the last answer: " +
              ", ".join(f"{k}={v}" for k, v in lim.items() if v is not None))
    cap = lim.get("x-ratelimit-limit-month")
    if cap is not None and cap < len(want) + int(h.get("reserve", 5)):
        print(f"  ** the key's monthly limit is {cap}, below the {len(want)} days this history needs: "
              f"a full year takes {-(-len(want) // max(cap - int(h.get('reserve', 5)), 1))} month(s) "
              f"on this plan — a paid month is Jake's call.")
    if res.get("retry_at"):
        print(f"  stopped by a 429: /ethstore/{{day}} asks to wait until {res['retry_at']} UTC")
    return 0 if not res["still"] else 1


if __name__ == "__main__":
    sys.exit(main())
