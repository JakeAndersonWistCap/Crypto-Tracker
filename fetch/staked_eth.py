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
import re

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
# ===== P4-Q4 (Jake 2026-11-03, SPEC "P4-Q4"): THE ROUTE IS beaconcha.in's v2 EPOCH ENDPOINT. =====
# ETH staked is read from beaconcha.in's v2 API and stored as its own series (config Ethereum["staked_eth_primary"]
# ["metric"], beacon_chain_eth_primary) beside the stored validatorqueue one; the validator count beside it
# (["count_metric"]). The switch is a DECLARED HANDOVER (config series_handover["beacon_chain_eth"], rule 10):
# beacon_chain_eth reads the new series only after the OVERLAP CHECK on validatorqueue's last week (2026-10-19..10-25)
# passes (build_workbook._handover_continuation_views, requires_overlap).
#
#     POST https://beaconcha.in/api/v2/ethereum/epoch          Authorization: Bearer <BEACONCHAIN_API_KEY>
#     body {"chain": "mainnet", "epoch": {"number": <the day's first epoch>}}
#     -> {"data": {"epoch", "timestamp", "validators_count", "total_validator_balance" (wei, a decimal string),
#                  "finality": "finalized" | "not_finalized", ...}}
#
# Read from docs.beaconcha.in (api-reference/ethereum/epoch/overview, OpenAPI "External Service API" 1.0.3) on
# 2026-11-03: total_validator_balance = "Sum of the actual balances (in wei) of all validators at this epoch";
# validators_count = "Number of active validators participating in consensus during this epoch"; the epoch is chosen
# by {"number": n} or {"view": "latest" | "finalized"}; the key is an HTTP bearer token. Keyless, the endpoint answers
# 401 to a well-formed body (checked 2026-11-03), so no live keyed answer was read from here.
#
# ONE DAY = THE DAY'S FIRST EPOCH: the first epoch starting at or after 00:00 UTC, ceil((day_start - genesis) / 384).
# The whole window the cells read (history_days, five quarters) is backfilled by epoch number, NEWEST FIRST, under the
# tier's time budget; the store is the state — a day already holding both series is not asked again, so a run stopped by
# the budget resumes where it stopped. STRICT: a missing or malformed field, another epoch than the one asked, or an
# epoch not yet finalized is a REFUSAL for that day (said, nothing stored) — never a 0, never a carried value.
#
# THE KEYLESS BEACON-NODE ROUTE (P3-A3) IS WITHDRAWN: it read Σ EFFECTIVE balance of ACTIVE validators
# (current_epoch_active_gwei), a different quantity from total_validator_balance (Σ ACTUAL balances of all validators);
# storing it under the same series would mix two definitions (rule 1). Its stored rows are removed by the run
# (config.RUN_CLEANUPS "P4-Q4"). Without the key nothing is stored and the run says so.
GENESIS_UNIX = 1606824023          # beacon chain genesis (mainnet), seconds
EPOCH_SECONDS = 32 * 12
POINT = "beaconchain:v2_epoch.total_validator_balance"
COUNT_POINT = "beaconchain:v2_epoch.validators_count"
STOP_BEFORE_S = 10.0               # stop this long before the tier's budget, so what was read is stored
_WEI = re.compile(r"^(0|[1-9][0-9]*)$")


def epoch_at(day: pd.Timestamp) -> int:
    """The first epoch starting at or after 00:00 UTC of `day` (its first epoch): ceil((day start − genesis) / 384)."""
    ts = int(pd.Timestamp(day).normalize().timestamp())
    return max(0, -(-(ts - GENESIS_UNIX) // EPOCH_SECONDS))


def day_of_epoch(epoch: int) -> pd.Timestamp:
    return pd.Timestamp(GENESIS_UNIX + int(epoch) * EPOCH_SECONDS, unit="s").normalize()


def wei_to_eth(raw) -> float | None:
    """A wei amount (the API's decimal string, or an int) in ETH; None for anything else (a float, a negative, a
    blank) — never 0 for an unreadable figure."""
    if isinstance(raw, bool) or raw is None:
        return None
    s = str(raw) if isinstance(raw, (int, str)) else None
    if s is None or not _WEI.match(s):
        return None
    wei = int(s)
    return wei / 1e18 if wei > 0 else None


def parse_epoch(body, epoch: int) -> tuple[float, int] | str:
    """(staked ETH, validators_count) from a v2 epoch answer for `epoch`, or why the answer is refused."""
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        return f"no data object in the answer ({type(body).__name__})"
    d = body["data"]
    got = d.get("epoch")
    if isinstance(got, bool) or not isinstance(got, int) or got != int(epoch):
        return f"answered epoch {got!r} for epoch {epoch}"
    if d.get("finality") != "finalized":
        return f"epoch {epoch} is not finalized yet (finality {d.get('finality')!r}) — read on a later run"
    ts = d.get("timestamp")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts != GENESIS_UNIX + int(epoch) * EPOCH_SECONDS:
        return f"epoch {epoch}: timestamp {ts!r} is not the epoch's start"
    if "total_validator_balance" not in d:
        return f"epoch {epoch}: no total_validator_balance in the answer"
    eth = wei_to_eth(d["total_validator_balance"])
    if eth is None:
        return f"epoch {epoch}: total_validator_balance {str(d['total_validator_balance'])[:40]!r} is not a wei amount"
    n = d.get("validators_count")
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return f"epoch {epoch}: validators_count {n!r} is missing or not a positive integer"
    return eth, n


class BeaconStakedEth:
    """ETH staked and the validator count, one row a day at the day's first epoch, from beaconcha.in v2 (P4-Q4)."""
    SOURCE = "beacon_api"
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, key: str | None = None, stored_long=None,
                 deadline_s: float | None = None, **_ignored):
        import os
        # 1 request a second: beaconcha.in's Free Trial / Hobbyist rate (docs.beaconcha.in/api/overview, 2026-11-03)
        self.http = http or Http(min_interval=1.0, retries=1)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.key = (os.environ.get("BEACONCHAIN_API_KEY", "") if key is None else key).strip()
        self.stored = stored_long
        self.deadline_s = deadline_s          # None: the tier's budget less STOP_BEFORE_S

    def _scrub(self, text) -> str:
        s = str(text)
        return s.replace(self.key, "***") if self.key else s

    def stored_days(self, name: str, metric: str, count_metric: str) -> set:
        """The days already holding BOTH series from this route (the backfill's state is the store)."""
        df = self.stored
        if df is None or getattr(df, "empty", True):
            return set()
        from .base import _measuring_point
        df = df[(df["project"] == name) & df["metric"].isin([metric, count_metric])]
        if df.empty:
            return set()
        mp = df["source"].astype(str).map(_measuring_point)
        df = df[mp.isin([_measuring_point(POINT), _measuring_point(COUNT_POINT)])]
        days = df.assign(d=pd.to_datetime(df["date"]).dt.normalize()).groupby("d")["metric"].nunique()
        return set(days[days >= 2].index)

    def window(self, spec: dict) -> list[pd.Timestamp]:
        """Every day of the window the cells read, newest first (today included: its first epoch is minutes old)."""
        end = today().normalize()
        return [end - pd.Timedelta(days=k) for k in range(int(spec.get("history_days", 455)))]

    def run(self, projects: list[dict], window_days, out):
        import time as _time
        for p in projects:
            spec = p.get("staked_eth_primary")
            if not spec:
                continue
            name, metric, cmetric = p["name"], spec["metric"], spec["count_metric"]
            day = str(today().date())
            dkey = "staked_eth:beaconchain_v2"
            if not self.daily.due(dkey, day):
                out.mark_current(self.SOURCE, name, metric, f"{metric}: every day of the window read today already",
                                 TIER)
                continue
            if not self.key:
                out.fail(self.SOURCE, name, f"{metric}, {cmetric}: NOTHING STORED — no BEACONCHAIN_API_KEY in the "
                                            f"environment; beaconcha.in's v2 epoch endpoint needs it (keyless it "
                                            f"answers 401). The handover from validatorqueue waits for these days",
                         TIER)
                continue
            have = self.stored_days(name, metric, cmetric)
            todo = [d for d in self.window(spec) if d not in have]
            if not todo:
                out.mark_current(self.SOURCE, name, metric, f"{metric}: every day of the {spec.get('history_days', 455)}"
                                                            f"-day window is held", TIER)
                self.daily.done(dkey, day)
                continue
            if self.deadline_s is not None:
                deadline = _time.monotonic() + self.deadline_s
            else:
                from . import _budget                                  # noqa: PLC0415
                deadline = _time.monotonic() + _budget(self.SOURCE) - STOP_BEFORE_S
            staked, counts, refused, stopped = [], [], [], ""
            for d in todo:
                if _time.monotonic() >= deadline:
                    stopped = "the tier's time budget"
                    break
                ep = epoch_at(d)
                try:
                    body = self.request(spec, ep)
                except Exception as e:  # noqa: BLE001 — a refused key, a plan, a rate limit: stop, never hammer
                    st = getattr(e, "status", None)
                    stopped = self._scrub(f"epoch {ep} ({d.date()}): {type(e).__name__}"
                                          + (f" HTTP {st}" if st else "") + f": {str(e)[:160]}")
                    break
                got = parse_epoch(body, ep)
                if isinstance(got, str):
                    refused.append(f"{d.date()}: {self._scrub(got)}")
                    continue
                if day_of_epoch(ep) != d:
                    refused.append(f"{d.date()}: epoch {ep} starts on {day_of_epoch(ep).date()}")
                    continue
                staked.append((d, got[0]))
                counts.append((d, got[1]))
            left = len(todo) - len(staked) - len(refused)
            tail = (f"; {len(refused)} day(s) REFUSED (nothing stored for them): " + "; ".join(refused[:4])
                    if refused else "") + (f"; STOPPED at {stopped} — {left} day(s) left, the next run resumes"
                                           if stopped else "")
            if staked:
                staked.sort()
                counts.sort()
                out.add(tidy(staked, name, metric, POINT, TIER), self.SOURCE, name,
                        f"{metric}: {len(staked)} day(s) {staked[0][0].date()}..{staked[-1][0].date()}, latest "
                        f"{staked[-1][1]:,.0f} ETH — beaconcha.in v2 epoch total_validator_balance (wei → ETH) at "
                        f"each day's first epoch; {len(have)} day(s) already held" + tail, TIER)
                out.add(tidy(counts, name, cmetric, COUNT_POINT, TIER), self.SOURCE, name,
                        f"{cmetric}: {len(counts)} day(s), latest {counts[-1][1]:,} validators", TIER)
            else:
                out.fail(self.SOURCE, name, f"{metric}, {cmetric}: NOTHING STORED this run" + (tail or ""), TIER)
            if not stopped:
                self.daily.done(dkey, day)

    def request(self, spec: dict, epoch: int):
        """One POST for one epoch. The key goes in the Authorization header only — never in the URL, body or log."""
        bc = spec["beaconchain"]
        return self.http.post(bc["url"], json_body={"chain": bc.get("chain", "mainnet"), "epoch": {"number": int(epoch)}},
                              headers={"Authorization": f"Bearer {self.key}"})
