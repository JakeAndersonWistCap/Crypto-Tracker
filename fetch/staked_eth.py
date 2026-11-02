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
                out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
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


# ===== P3-A3 (Jake's 2026-11-02 run): validatorqueue's history stopped at 2026-10-25. =====
# ETH staked is re-routed to the BEACON CHAIN ITSELF — staked ETH = Σ effective balance of the ACTIVE validators — and
# stored as its own series (config Ethereum["staked_eth_primary"]["metric"]) beside the stored validatorqueue one. The
# switch is a DECLARED HANDOVER (config series_handover["beacon_chain_eth"], rule 10): beacon_chain_eth reads the new
# series only after the 7-day OVERLAP CHECK against the stored validatorqueue days passes
# (build_workbook._handover_continuation_views, requires_overlap). Two routes, one quantity:
#   1. beaconcha.in /api/v1/epoch/{epoch} `eligibleether` (gwei) — with BEACONCHAIN_API_KEY only; reads each day's first
#      epoch back over `history_days`, so the overlap days (validatorqueue's last 7) can be read.
#   2. a public beacon node (Lighthouse) /lighthouse/validator_inclusion/{epoch}/global `current_epoch_active_gwei` at the
#      finalized epoch — keyless, but the node keeps no past state: TODAY only, so it cannot make the overlap by itself.
# Never a carried value, never a 0 for an unread day; a refused answer stores nothing and says why.
GENESIS_UNIX = 1606824023          # beacon chain genesis (mainnet), seconds
EPOCH_SECONDS = 32 * 12


def epoch_at(day: pd.Timestamp) -> int:
    """The first epoch starting at or after 00:00 UTC of `day` (its first epoch)."""
    ts = int(pd.Timestamp(day).normalize().timestamp())
    return max(0, -(-(ts - GENESIS_UNIX) // EPOCH_SECONDS))


def day_of_epoch(epoch: int) -> pd.Timestamp:
    return pd.Timestamp(GENESIS_UNIX + int(epoch) * EPOCH_SECONDS, unit="s").normalize()


def gwei_to_eth(v) -> float | None:
    try:
        x = float(v) / 1e9
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


class BeaconStakedEth:
    """ETH staked from a PRIMARY source (P3-A3): beaconcha.in with the key, else the public beacon API (today only)."""
    SOURCE = "beacon_api"
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, key: str | None = None, **_ignored):
        import os
        self.http = http or Http(min_interval=2.0, retries=1)     # public endpoints: paced, one retry
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.key = (os.environ.get("BEACONCHAIN_API_KEY", "") if key is None else key).strip()

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("staked_eth_primary")
            if not spec:
                continue
            name, metric = p["name"], spec["metric"]
            day = str(today().date())
            dkey = "staked_eth:beacon_primary"
            if not self.daily.due(dkey, day):
                out.mark_current(self.SOURCE, name, metric, f"{metric}: read today already", TIER)
                continue
            first = not self.daily.ever(dkey)
            rows, why = ([], "")
            if self.key:
                rows, why = self.beaconchain_days(spec, int(spec.get("history_days", 21)) if first else 3)
            if not rows:
                one, why2 = self.beacon_api_today(spec)
                rows, why = (one, why) if one else ([], "; ".join(x for x in (why, why2) if x))
            if not rows:
                out.fail(self.SOURCE, name, f"{metric}: NOTHING STORED — {why}"
                         + ("" if self.key else " (no BEACONCHAIN_API_KEY: the overlap days cannot be read)"), TIER)
                continue
            for src, items in _by_source(rows).items():
                out.add(tidy(items, name, metric, src, TIER), self.SOURCE, name,
                        f"{metric}: {len(items)} day(s) {items[0][0].date()}..{items[-1][0].date()}, latest "
                        f"{items[-1][1]:,.0f} ETH — Σ effective balance of active validators ({src.split(':')[0]})"
                        + (f"; {why}" if why else ""), TIER)
            self.daily.done(dkey, day)

    def beaconchain_days(self, spec: dict, days: int) -> tuple[list, str]:
        """[(day, ETH, source)] from beaconcha.in's epoch endpoint at each day's first epoch, oldest first, the last
        `days` complete days and today's finalized epoch excluded. The key goes in a header, never into a URL or log."""
        bc = spec["beaconchain"]
        end = today().normalize()
        out, errs = [], []
        for k in range(days, 0, -1):
            d = end - pd.Timedelta(days=k)
            ep = epoch_at(d)
            try:
                body = self.http.get(bc["base"] + bc["path"].format(epoch=ep), headers={"apikey": self.key})
            except Exception as e:  # noqa: BLE001
                errs.append(f"beaconcha.in epoch {ep}: {type(e).__name__}")
                break                                   # a refused key or a rate limit: stop, never hammer
            v = gwei_to_eth(((body or {}).get("data") or {}).get(bc["field"])) if isinstance(body, dict) else None
            if v is None:
                errs.append(f"beaconcha.in epoch {ep}: no {bc['field']} in the answer")
                continue
            out.append((d, v, f"beaconchain:epoch.{bc['field']}"))
        return out, "; ".join(errs[:3])

    def beacon_api_today(self, spec: dict) -> tuple[list, str]:
        """[(day, ETH, source)] for the finalized epoch's day from a public beacon node (Lighthouse), or why not."""
        ba = spec["beacon_api"]
        try:
            hdr = self.http.get(ba["base"] + ba["finalized_header"])
            slot = int(hdr["data"]["header"]["message"]["slot"])
        except Exception as e:  # noqa: BLE001
            return [], f"beacon API finalized header: {type(e).__name__}: {str(e)[:120]}"
        ep = slot // 32
        try:
            body = self.http.get(ba["base"] + ba["path"].format(epoch=ep))
        except Exception as e:  # noqa: BLE001
            return [], f"beacon API validator_inclusion epoch {ep}: {type(e).__name__}: {str(e)[:120]}"
        v = gwei_to_eth(((body or {}).get("data") or {}).get(ba["field"])) if isinstance(body, dict) else None
        if v is None:
            return [], f"beacon API epoch {ep}: no {ba['field']} in the answer"
        return [(day_of_epoch(ep), v, f"beacon_api:validator_inclusion.{ba['field']}")], ""


def _by_source(rows: list) -> dict:
    out: dict = {}
    for d, v, src in sorted(rows):
        out.setdefault(src, []).append((d, v))
    return out
