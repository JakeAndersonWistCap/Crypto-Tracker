"""
fetch/staked_eth.py — tier 1: ETH STAKED on the beacon chain, per day (Jake's run 2026-10-05 21:00).

WHY. beacon_chain_eth was deposit-contract balance + ethsupply2 Eth2Staking − WithdrawnTotal, which read
88,441,790 ETH. The deposit contract's balance is every deposit EVER made (ETH never leaves it; withdrawals
are minted on the execution layer), and WithdrawnTotal is ~7.6M against ~47-55M actually withdrawn, so the
formula cannot be repaired from those fields. Staked ETH is read instead from:

    GET https://raw.githubusercontent.com/etheralpha/validatorqueue-com/main/historical_data.json
    -> [{"date": "YYYY-MM-DD", "staked_amount": <whole ETH>, "supply", "staked_percent", "apr", ...}, ...]

validatorqueue.com's own repo (MIT), one row per day since 2023-05-21, committed by its GitHub Action every
3 hours. staked_amount = beaconcha.in /api/v1/epoch/finalized `votedether` / 1e9 (its build.py L139): the
ACTIVE stake that attested in the finalized epoch — the base the issuance curve pays on. Checked 2026-10-05:
43,657,647 ETH (35.76% of 122,080,989), the figure published for early October 2026 (~43.5-43.6M,
cryptoticker / validatorqueue.com).

A THIRD PARTY'S COPY of beaconcha.in's number, keyless: if the file stops updating, the newest row ages and
the run log says how old it is; nothing is carried forward. One request a day (DailyChecks); the first read
stores a year, later reads a week.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.staked_eth")

SOURCE = "validatorqueue"
TIER = 1
URL = "https://raw.githubusercontent.com/etheralpha/validatorqueue-com/main/historical_data.json"


class StakedEth:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("staked_eth")
            if not spec:
                continue
            name, metric = p["name"], spec["metric"]
            day = str(today().date())
            key = "staked_eth:validatorqueue"
            if not self.daily.due(key, day):
                out.skipped(SOURCE, name, f"{metric}: read today already", TIER)
                continue
            try:
                body = self.http.get(URL)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{metric}: raw.githubusercontent.com validatorqueue history — {e}", TIER)
                continue
            rows = self.parse(body)
            if isinstance(rows, str):
                out.fail(SOURCE, name, f"{metric}: {rows} — NOTHING STORED", TIER)
                continue
            days = int(spec.get("first_days", 365)) if not self.daily.ever(key) else 7
            cut = today().normalize() - pd.Timedelta(days=days)
            keep = [(d, v) for d, v in rows if d > cut]
            if not keep:
                out.fail(SOURCE, name, f"{metric}: newest row {rows[-1][0].date()} is older than {days} day(s) — "
                                       f"the file has stopped updating; NOTHING STORED", TIER)
                continue
            age = (today().normalize() - keep[-1][0]).days
            frame = tidy(keep, name, metric, f"{SOURCE}:staked_amount", TIER)
            out.add(frame, SOURCE, name,
                    f"{metric}: {len(keep)} day(s) {keep[0][0].date()}..{keep[-1][0].date()}, latest "
                    f"{keep[-1][1]:,.0f} ETH (beaconcha.in finalized-epoch votedether via validatorqueue.com)"
                    + (f" — newest row is {age} day(s) old" if age > 1 else ""), TIER)
            self.daily.done(key, day)

    @staticmethod
    def parse(body) -> list[tuple] | str:
        """[(day, staked ETH)] sorted, or why the answer is refused."""
        if not isinstance(body, list) or not body:
            return f"not a list of daily rows ({type(body).__name__})"
        out = {}
        for r in body:
            if not isinstance(r, dict):
                return f"a row that is not an object: {str(r)[:80]}"
            try:
                d, v = pd.Timestamp(r["date"]).normalize(), float(r["staked_amount"])
            except (KeyError, TypeError, ValueError) as e:
                return f"a row without date / staked_amount ({e}): {str(r)[:120]}"
            sup, pct = r.get("supply"), r.get("staked_percent")
            # SELF-CHECK on the row's own fields: staked / supply must equal its staked_percent.
            if sup and pct is not None and abs(v / float(sup) * 100 - float(pct)) > 0.05:
                return (f"{d.date()}: staked {v:,.0f} / supply {float(sup):,.0f} = {v / float(sup):.2%}, but the row "
                        f"says {float(pct):.2f}% — the units changed")
            out[d] = v
        return sorted(out.items())
