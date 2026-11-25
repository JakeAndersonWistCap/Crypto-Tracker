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
import math
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


# ===== P5-1.12 (Jake 2026-11-04): STOP RELYING ON beaconcha.in — A FREE PUBLIC BEACON NODE, ONCE A DAY. =====
# beaconcha.in's free-trial quota is spent (HTTP 429, a 2,300,523 s wait); it is in a declared cooldown
# (config.HOST_COOLDOWN, fetch.base.host_cooldown) and BeaconStakedEth above is no longer registered as a tier.
# beacon_chain_eth_primary is read from a public beacon node with the standard Beacon API, keyless:
#
#     GET <node>/eth/v1/beacon/headers/finalized                       -> the finalized slot (read before and after)
#     GET <node>/eth/v1/beacon/states/finalized/validator_balances      -> {"finalized": true, "data": [{index, balance}]}
#
# Σ balance (gwei -> ETH) = Σ ACTUAL balances of ALL validators at the finalized state: the quantity P4-Q4 chose
# (beaconcha.in's total_validator_balance), so the series keeps one definition. The answer is ~88 MB of JSON (~7 MB
# compressed); it is summed WHILE STREAMING, never held. The finalized slot is read before and after: if it moved, the
# sum may straddle two states — read once more, else refused. The row is dated the UTC day of the finalized slot and
# carries the slot. The node serves no past state (HTTP 403), so a missed day is not read back from here: the
# BigQuery rebuild (staked_eth_rebuild.py) fills the days before the first node row.
_BAL = re.compile(rb'"balance"\s*:\s*"(\d+)"')
_AMT = re.compile(rb'"amount"\s*:\s*"(\d+)"')


def sum_field_stream(chunks, field: bytes = b"balance") -> tuple[int, int, int]:
    """(Σ gwei, entries, non-zero entries) of every "<field>":"<digits>" in a JSON byte stream given in chunks of any
    size — a match split across two chunks is counted once (the tail of each chunk is carried into the next)."""
    pat = _BAL if field == b"balance" else _AMT if field == b"amount" else \
        re.compile(rb'"' + re.escape(field) + rb'"\s*:\s*"(\d+)"')
    total = n = nz = 0
    carry = b""
    for chunk in chunks:
        if not chunk:
            continue
        buf = carry + chunk
        cut = buf.rfind(b"}")              # every complete entry ends at a "}": match up to the last one
        if cut < 0:
            carry = buf
            continue
        for m in pat.finditer(buf, 0, cut + 1):
            v = int(m.group(1))
            total, n, nz = total + v, n + 1, nz + (v > 0)
        carry = buf[cut + 1:]
    for m in pat.finditer(carry):
        v = int(m.group(1))
        total, n, nz = total + v, n + 1, nz + (v > 0)
    return total, n, nz


def slot_time(slot: int) -> pd.Timestamp:
    return pd.Timestamp(GENESIS_UNIX + int(slot) * 12, unit="s")


def _utcnow() -> pd.Timestamp:
    """The read's own clock (UTC, naive) — module-level so a test can set it."""
    return pd.Timestamp.now("UTC").tz_localize(None)


# ===== R17 (Jake's store 2026-11-23): THE NODE READ IS CHECKED — A STALENESS RULE AND A SECOND SOURCE. =====
# STALENESS: finality trails the head by two epochs (~13 minutes). A "finalized" state older than the declared
# max_age_hours is a node that has stopped following the chain: REFUSED, nothing stored (the row would be dated a
# past day and read as that day's stake).
# THE SECOND SOURCE: ultrasound.money's own beacon node sums the same quantity,
#     GET https://ultrasound.money/api/v2/fees/eth-supply-parts -> {"beaconBalancesSum": "<gwei>", "slot": N, ...}
# (Σ balances of every validator at `slot`; its open-source backend, ultrasoundmoney/eth-analysis-rs, reads
# /eth/v1/beacon/states/{slot}/validator_balances — the same Beacon API call). Read live 2026-11-23: publicnode
# 44,714,369.44 ETH at finalized slot 15716896, ultrasound 44,714,251.11 ETH at slot 15716976 (80 slots later):
# −0.0003%. Within the declared slot gap the two must agree within the declared tolerance, else the node read is
# REFUSED (a truncated or wrong sum is never stored); when ultrasound does not answer, or its slot is too far from the
# node's, the node's sum is stored and the row's source says the cross-check was not made — never silently.
def cross_check_sum(node_gwei: int, node_slot: int, body, spec: dict) -> dict:
    """{"status": "agrees" | "DISAGREES" | "not comparable" | "not read", "text", "gap_pct"} for the node's Σ gwei at
    node_slot against a second source's answer `body` (spec: field, slot_field, tolerance_pct, max_slot_gap)."""
    what = spec.get("label", "second source")
    if not isinstance(body, dict):
        return {"status": "not read", "gap_pct": None, "text": f"cross-check NOT MADE: {what} did not answer"}
    raw, slot = body.get(spec["field"]), body.get(spec["slot_field"])
    if isinstance(slot, bool) or not isinstance(slot, int) or not isinstance(raw, (str, int)) \
            or isinstance(raw, bool) or not _WEI.match(str(raw)) or int(raw) <= 0:
        return {"status": "not read", "gap_pct": None,
                "text": f"cross-check NOT MADE: {what} carried no {spec['field']} / {spec['slot_field']}"}
    gap_slots = int(slot) - int(node_slot)
    if abs(gap_slots) > int(spec["max_slot_gap"]):
        return {"status": "not comparable", "gap_pct": None,
                "text": f"cross-check NOT MADE: {what} is at slot {slot}, {gap_slots:+,} slots from the node's "
                        f"(more than {int(spec['max_slot_gap']):,})"}
    gap = (int(raw) / node_gwei - 1.0) * 100.0
    ok = abs(gap) <= float(spec["tolerance_pct"])
    return {"status": "agrees" if ok else "DISAGREES", "gap_pct": gap,
            "text": f"cross-check {'agrees' if ok else 'DISAGREES'}: {what} {int(raw) / 1e9:,.0f} ETH at slot {slot} "
                    f"({gap_slots:+,} slots), {gap:+.4f}% (tolerance ±{float(spec['tolerance_pct']):g}%)"}


class BeaconNodeStakedEth:
    """ETH staked (Σ actual balances of all validators at the finalized state) from a public beacon node (P5-1.12)."""
    SOURCE = "beacon_api"
    TIER = TIER

    def __init__(self, session=None, daily=None, http: Http | None = None, **_ignored):
        self.session = session              # tests inject a stub; the run uses the Http client's session
        # R20 (Jake's store run 2026-11-23: "tier beacon_api done in 0s — no Http client", nothing stored): the read
        # ran on a bare requests.Session, so the tier's timing line had no client to report — no call count, no time,
        # no error — and a quick refusal read exactly like a day already done. It now owns an Http client (the
        # streamed sum goes through that client's session, each call counted in its stats).
        self.http = http or Http(min_interval=0.0, retries=1)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _sess(self):
        if self.session is None:
            self.session = self.http.s
            self.session.headers.update({"Accept-Encoding": "gzip, deflate"})
        return self.session

    def _get(self, url: str, stream: bool = False):
        from .base import CONNECT_TIMEOUT_S, READ_TIMEOUT_S, host_cooldown, mask_url, mask_v2
        import time as _time
        import urllib.parse
        parts = urllib.parse.urlsplit(url)
        cool = host_cooldown(parts.netloc)
        if cool:
            raise RuntimeError(cool)
        t0, st = _time.monotonic(), self.http.stats
        try:
            r = self._sess().get(url, stream=stream, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        finally:
            took = _time.monotonic() - t0
            st["calls"] += 1
            st["http_s"] += took
            st.setdefault("times", []).append((took, mask_v2(parts.path)[:60]))
            if took > st["slowest_s"]:
                st["slowest_s"], st["slowest"] = took, mask_v2(parts.path)[:80]
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from {mask_url(url)}")
        return r

    def finalized_slot(self, node: str) -> int:
        body = self._get(f"{node}/eth/v1/beacon/headers/finalized").json()
        slot = ((((body or {}).get("data") or {}).get("header") or {}).get("message") or {}).get("slot")
        if not (isinstance(slot, str) and slot.isdigit()):
            raise RuntimeError(f"no finalized slot in the header answer ({str(body)[:80]})")
        return int(slot)

    def read(self, node: str, state: str = "finalized", field: str = "balance",
             path: str = "validator_balances") -> tuple[int, int, int, int]:
        """(slot, Σ gwei, entries, non-zero) at the finalized state, the slot unchanged across the read (one retry)."""
        for _attempt in range(2):
            s0 = self.finalized_slot(node)
            r = self._get(f"{node}/eth/v1/beacon/states/{state}/{path}", stream=True)
            it = iter(r.iter_content(chunk_size=1 << 16))
            head = b""
            for chunk in it:                       # the first bytes carry "finalized": checked before summing
                head += chunk
                if len(head) >= 200:
                    break
            if b'"finalized":true' not in head[:400].replace(b" ", b""):
                raise RuntimeError(f"the {path} answer does not say finalized: {head[:80]!r}")
            total, n, nz = sum_field_stream(_chain([head], it), field.encode())
            if self.finalized_slot(node) == s0:
                return s0, total, n, nz
        raise RuntimeError("the finalized slot moved during the read twice — refused (the sum may straddle two states)")

    def cross_check(self, spec: dict, gwei: int, slot: int) -> dict | None:
        """The node's sum against the declared second source (R17); None when none is declared. A second source that
        does not answer is said in the row's source, never an error of the read."""
        cc = spec.get("cross_check")
        if not cc:
            return None
        try:
            from .scrape import robots_verdict
            allowed, why = robots_verdict(cc["url"])
            if not allowed:
                return {"status": "not read", "gap_pct": None,
                        "text": f"cross-check NOT MADE: robots.txt disallows {cc.get('label', 'the second source')} "
                                f"({str(why)[:60]})"}
            body = self._get(cc["url"]).json()
        except Exception as e:  # noqa: BLE001 — the cross-check's own failure is said, the node read stands
            log.info("staked_eth: cross-check %s did not answer: %s", cc.get("label"), str(e)[:160])
            body = None
        return cross_check_sum(gwei, slot, body, cc)

    def measure(self, spec: dict) -> tuple[dict | None, list[str]]:
        """ONE READING of the declared nodes, nothing stored (R20: the run stores it, the beaconcha.in tier sets its
        own figure against it): ({"node", "slot", "when", "eth", "n", "nz", "chk"}, []) from the first endpoint whose
        sum passes the band, the staleness rule and the second source (R17), else (None, why each endpoint failed)."""
        from .base import mask_url
        metric, errs = spec["metric"], []
        for node in spec["endpoints"]:
            try:
                slot, gwei, n, nz = self.read(node, spec.get("state", "finalized"))
            except Exception as e:  # noqa: BLE001 — one endpoint's failure is said, the next is tried
                errs.append(f"{mask_url(node)}: {type(e).__name__}: {str(e)[:160]}")
                continue
            eth = gwei / 1e9
            lo, hi = config.METRICS[metric]["sanity_min"], config.METRICS[metric]["sanity_max"]
            if n < int(spec.get("min_entries", 100_000)) or not (lo <= eth <= hi):
                errs.append(f"{mask_url(node)}: refused — {n:,} entries, Σ {eth:,.0f} ETH (band {lo:,.0f}.."
                            f"{hi:,.0f} ETH, at least {int(spec.get('min_entries', 100_000)):,} entries)")
                continue
            when = slot_time(slot)
            # R17: a finalized state older than max_age_hours is a node that stopped following the chain
            age_h = (_utcnow() - when).total_seconds() / 3600.0
            if spec.get("max_age_hours") is not None and age_h > float(spec["max_age_hours"]):
                errs.append(f"{mask_url(node)}: refused — its finalized slot {slot} is {when:%Y-%m-%d %H:%M} UTC, "
                            f"{age_h:,.1f} h old (finality trails the head by ~13 minutes; more than "
                            f"{float(spec['max_age_hours']):g} h is a node that has stopped following the chain)")
                continue
            chk = self.cross_check(spec, gwei, slot)
            if chk and chk["status"] == "DISAGREES":
                errs.append(f"{mask_url(node)}: refused — Σ {eth:,.0f} ETH at slot {slot}; {chk['text']}")
                continue
            return {"node": mask_url(node), "slot": slot, "when": when, "eth": eth, "n": n, "nz": nz, "chk": chk}, errs
        return None, errs

    def store(self, name: str, spec: dict, got: dict, out) -> None:
        """The reading as the day's row of the series (dated the UTC day of its finalized slot)."""
        metric, note = spec["metric"], (f"; {got['chk']['text']}" if got.get("chk") else "")
        src = (f"{spec['point']}[finalized slot {got['slot']}, {got['when']:%H:%M} UTC; {got['nz']:,} of "
               f"{got['n']:,} non-zero{note}]")
        out.add(tidy([(got["when"].normalize(), got["eth"])], name, metric, src, TIER), self.SOURCE, name,
                f"{metric}: {got['eth']:,.0f} ETH — Σ actual balances of all validators at finalized slot "
                f"{got['slot']} ({got['when']:%Y-%m-%d %H:%M} UTC), {got['n']:,} entries ({got['nz']:,} non-zero), "
                f"public beacon node {got['node']}{note}", TIER)

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("staked_eth_node")
            if not spec:
                continue
            name, metric = p["name"], spec["metric"]
            day = str(today().date())
            dkey = stake_mark(name, metric)
            if not self.daily.due(dkey, day):
                out.mark_current(self.SOURCE, name, metric, f"{metric}: read today already", TIER)
                continue
            got, errs = self.measure(spec)
            if got is None:
                out.fail(self.SOURCE, name, f"{metric}: NOTHING STORED — " + "; ".join(errs), TIER)
                continue
            self.store(name, spec, got, out)
            self.daily.done(dkey, day)


def stake_mark(name: str, metric: str) -> str:
    """The day's mark of a staked-ETH read, shaped <source>:<project>:<metric> so the run keeps it ONLY when the series
    reached the store (token_metrics._mark_stored). R20: the old key (staked_eth:beacon_node) was kept whatever
    happened to the row, so a read whose row was refused downstream said "read today already" for the rest of the
    day — a tier done in 0 s with nothing stored."""
    return f"beacon_api:{name}:{metric}"


# ===== R20 (Jake 2026-11-24): STAKED ETH, THE VALIDATOR COUNT AND THE STAKING YIELD FROM beaconcha.in's v2 API. =====
# Read from docs.beaconcha.in 2026-11-23 (the docs index llms.txt and the pages' own OpenAPI, "External Service API"
# 1.0.3; the knowledge-base MCP at docs.beaconcha.in/mcp answers JSON-RPC only — "Method not allowed" to a plain GET —
# and serves the same pages). Every v2 endpoint is a POST with a JSON body and `Authorization: Bearer <key>`; keyless
# it answers 401 ("a valid API key is required", checked 2026-11-23).
#
#   POST /api/v2/ethereum/epoch      {"chain": "mainnet", "epoch": {"number": N}}          (parse_epoch, above)
#        total_validator_balance  "Sum of the actual balances (in wei) of all validators at this epoch"   -> staked ETH
#        validators_count         "Number of active validators participating in consensus during this epoch"
#        (also total_active_balance = Σ effective balance eligible to attest, attesting_balance = Σ effective balance
#        that voted the target — validatorqueue's quantity; read by nobody here)
#        ANY PAST EPOCH BY NUMBER: daily history is served — one request a day of history.
#   POST /api/v2/ethereum/eth-store  {"chain": "mainnet", "range": {"evaluation_window": "24h"|"7d"|"30d"|"90d"|
#                                     "all_time"}, "page_size": <=10, "cursor": ""}
#        ETH.STORE: "the average return that validators on the Ethereum network achieved over each 24-hour period";
#        one datapoint per BEACONCHAIN-DAY (225 epochs from genesis, so a day runs 12:00:23 UTC to 12:00:23 UTC),
#        newest first, computed only after its day is finalized — immutable. apr.combined = (consensus + execution
#        rewards, priority fees and MEV included) ÷ Σ effective balance of the validators active the whole day x 365,
#        simple, a fraction (0.0342 = 3.42%). The trailing windows are served on every plan (the custom ranges are
#        Scale / Enterprise); 10 datapoints a page with a cursor: HISTORY IS SERVED.
#
# THE PLAN (docs.beaconcha.in/api/overview "Pricing", read 2026-11-23): there is NO standing free plan. "Free Trial
# 0 EUR (30 days)": 1 request a second, 1,000 requests, 1 key, "does not auto-renew; access ends after 30 days unless
# you upgrade"; then Hobbyist 59 EUR a month (1 a second, unlimited). So the reads are FEW: one epoch call a day of
# history owed (2026-10-19..today on the first run, ~40), one a day after; ETH.STORE one page a day (4-5 on the first
# run); at most max_requests_per_run in any run. ~45 on the first run, ~60 a month after.
#
# WHAT IS STORED, on which measuring point:
#   beacon_chain_eth_primary  beaconchain:v2_epoch.total_validator_balance — each UTC day's first epoch (00:00 UTC)
#   beacon_validators_count   beaconchain:v2_epoch.validators_count — the same answer
#   staking_yield_pct         beaconchain:v2_eth_store.apr.combined — dated the UTC day its beaconchain-day STARTS
# THE CROSS-CHECK: today's total_validator_balance against the public beacon node's Σ actual balances of all
# validators at its finalized state (R17's read, itself checked against ultrasound.money within ±0.1%) — the same
# quantity, hours apart, so the tolerance is the declared ±0.25% (config: R17's 0.1% + the most the sum moves in a
# day at the spec churn, 0.14%). Outside it today's beaconcha.in row is REFUSED. A past day cannot be cross-checked
# (the node serves no past state): said on the Run Log line, the days are stored.
# THE NODE'S OWN ROW is stored only while the series holds no beaconcha.in row (no key yet, or the declared cooldown):
# it is the earlier leg of the series' declared handover; once beaconcha.in's rows are stored it is the cross-check.
ETHSTORE_POINT = "beaconchain:v2_eth_store.apr.combined"
_WINDOWS = (("7d", 7), ("30d", 30), ("90d", 90))


def parse_ethstore(body) -> tuple[list[tuple], list[str], str] | str:
    """([(day, apr, note)], [why a datapoint was refused], next_cursor) from a v2 eth-store answer, or why the whole
    answer is refused. A datapoint is REFUSED (never a 0, never repaired) when its day is not a whole beaconchain-day
    from genesis, its apr is not a fraction in (0, 0.5], or its combined apr is not its two layers' sum."""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return f"no data list in the answer ({type(body).__name__})"
    rows, refused = [], []
    for d in body["data"]:
        if not isinstance(d, dict):
            return f"a datapoint that is not an object: {str(d)[:80]}"
        n, t0, t1 = d.get("day"), d.get("day_start"), d.get("day_end")
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (n, t0, t1)):
            refused.append(f"day {n!r}: day / day_start / day_end missing or not integers")
            continue
        if t1 - t0 != 86400 or t0 != GENESIS_UNIX + n * 86400:
            refused.append(f"day {n}: {t0}..{t1} is not the {n}th whole day from genesis")
            continue
        apr = d.get("apr") if isinstance(d.get("apr"), dict) else {}
        vals = [apr.get(k) for k in ("combined", "consensus_layer", "execution_layer")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vals):
            refused.append(f"day {n}: apr {str(d.get('apr'))[:60]} carries no combined / consensus_layer / "
                           f"execution_layer number")
            continue
        comb, cl, el = (float(v) for v in vals)
        if not (0.0 < comb <= 0.5) or abs(comb - (cl + el)) > 1e-4:
            refused.append(f"day {n}: apr combined {comb!r} is not a fraction in (0, 0.5] equal to consensus {cl!r} + "
                           f"execution {el!r} — the units changed")
            continue
        ma = d.get("moving_averages") if isinstance(d.get("moving_averages"), dict) else {}
        avg = "; ".join(f"{lab} mean {float(a):.4%}" for lab, a in (
            (lab, (((ma.get(k) or {}).get("apr") or {}).get("combined"))) for k, lab in
            (("window_7d", "7-day"), ("window_31d", "31-day"))) if isinstance(a, (int, float)) and not isinstance(a, bool))
        rows.append((pd.Timestamp(t0, unit="s").normalize(), comb,
                     f"beaconchain-day {n}; consensus {cl:.4%} + execution {el:.4%}" + (f"; {avg}" if avg else "")))
    paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
    return rows, refused, str(paging.get("next_cursor") or "")


def node_cross_check(eth: float, day: pd.Timestamp, node: dict | None, cc: dict) -> dict:
    """{"status": "agrees" | "DISAGREES" | "not made", "text", "gap_pct"}: beaconcha.in's total_validator_balance at
    `day`'s first epoch against the node's Σ actual balances at its finalized slot (cc: tolerance_pct, max_gap_hours)."""
    if not node:
        return {"status": "not made", "gap_pct": None, "text": "cross-check NOT MADE: the public beacon node gave no "
                                                               "reading this run"}
    hours = (node["when"] - pd.Timestamp(day).normalize()).total_seconds() / 3600.0
    if not (0.0 <= hours <= float(cc["max_gap_hours"])):
        return {"status": "not made", "gap_pct": None,
                "text": f"cross-check NOT MADE: the node's finalized slot {node['slot']} is {hours:+.1f} h from the "
                        f"day's first epoch (more than {float(cc['max_gap_hours']):g} h)"}
    gap = (node["eth"] / eth - 1.0) * 100.0
    ok = abs(gap) <= float(cc["tolerance_pct"])
    return {"status": "agrees" if ok else "DISAGREES", "gap_pct": gap,
            "text": f"cross-check {'agrees' if ok else 'DISAGREES'}: public beacon node Σ actual balances "
                    f"{node['eth']:,.0f} ETH at finalized slot {node['slot']} ({hours:.1f} h later), {gap:+.4f}% "
                    f"(tolerance ±{float(cc['tolerance_pct']):g}%)"}


class BeaconchainEth:
    """R20: staked ETH, the validator count and the ETH.STORE yield from beaconcha.in v2, the public beacon node as the
    cross-check (and as the series' row while beaconcha.in has stored none). config Ethereum["staked_eth_beaconchain"]."""
    SOURCE = "beacon_api"
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, key: str | None = None, stored_long=None,
                 deadline_s: float | None = None, node=None, **_ignored):
        import os
        self.http = http or Http(min_interval=1.1, retries=1)       # 1 request a second on every plan up to Hobbyist
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self._key_arg = key
        self._env = os.environ
        self.stored = stored_long
        self.deadline_s = deadline_s
        self.node = node or BeaconNodeStakedEth(daily=daily, http=self.http)
        self.requests = 0
        self._wrote = False                 # a beaconcha.in staked row was written this run

    # ---- the key: read from the environment only, sent in the Authorization header only, never logged
    def key(self, spec: dict) -> str:
        return (self._env.get(spec["key_env"], "") if self._key_arg is None else self._key_arg).strip()

    def _scrub(self, spec: dict, text) -> str:
        k, s = self.key(spec), str(text)
        return s.replace(k, "***") if k else s

    def blocked(self, spec: dict) -> tuple[str, str] | None:
        """("unconfigured" | "cooldown", why) when beaconcha.in is not asked this run, else None."""
        import urllib.parse
        if spec.get("not_standing"):
            # R21b (Jake 2026-11-25): the keyless public beacon node is the standing source — beaconcha.in is not asked
            # even with a key in .env (a change of source would be a declared handover, never a key's side effect)
            return "skipped", spec["not_standing"]
        if not self.key(spec):
            return "unconfigured", (f"no {spec['key_env']} in .env — beaconcha.in's v2 API answers 401 without a key; "
                                    f"NOTHING STORED from it")
        from .base import host_cooldown
        cool = host_cooldown(urllib.parse.urlsplit(spec["epoch_url"]).netloc)
        return ("cooldown", cool) if cool else None

    def _held(self, name: str, metric: str, point: str) -> set:
        """The days `metric` already holds on `point` in the store (the backfill's state is the store)."""
        df = self.stored
        if df is None or getattr(df, "empty", True):
            return set()
        from .base import _measuring_point
        df = df[(df["project"] == name) & (df["metric"] == metric)]
        if df.empty:
            return set()
        df = df[df["source"].astype(str).map(_measuring_point) == _measuring_point(point)]
        return set(pd.to_datetime(df["date"]).dt.normalize())

    def _post(self, spec: dict, url: str, body: dict):
        self.requests += 1
        return self.http.post(url, json_body=body, headers={"Authorization": f"Bearer {self.key(spec)}"})

    def _left(self, spec: dict, deadline: float) -> str:
        import time as _time
        if self.requests >= int(spec["max_requests_per_run"]):
            return f"the run's {int(spec['max_requests_per_run'])}-request cap (the plan's allowance is counted)"
        return "the tier's time budget" if _time.monotonic() >= deadline else ""

    def run(self, projects: list[dict], window_days, out):
        import time as _time
        for p in projects:
            spec = p.get("staked_eth_beaconchain")
            if not spec:
                continue
            name, metric, cmetric, ymetric = p["name"], spec["metric"], spec["count_metric"], spec["yield_metric"]
            day = str(today().date())
            k_stake, k_yield = stake_mark(name, metric), stake_mark(name, ymetric)
            stake_due, yield_due = self.daily.due(k_stake, day), self.daily.due(k_yield, day)
            why = self.blocked(spec)
            if why:
                (out.unconfigured if why[0] == "unconfigured" else out.skipped)(
                    self.SOURCE, name, f"{metric}, {cmetric}, {ymetric}: {why[1]}", TIER)
            if not stake_due and (why or not yield_due):
                out.mark_current(self.SOURCE, name, metric, f"{metric}: read today already", TIER)
                continue
            if self.deadline_s is not None:
                deadline = _time.monotonic() + self.deadline_s
            else:
                from . import _budget                                  # noqa: PLC0415
                deadline = _time.monotonic() + _budget(self.SOURCE) - STOP_BEFORE_S
            node, nerrs, nspec = None, [], p.get("staked_eth_node")
            if stake_due and nspec:
                node, nerrs = self.node.measure(nspec)
            stored_today, self._wrote = False, False
            if stake_due and not why:
                stored_today = self._epochs(name, spec, node, nerrs, deadline, out)
            if yield_due and not why:
                self._ethstore(name, spec, deadline, out)
            if stake_due and not stored_today and not self._wrote:
                # beaconcha.in gave no row this run (no key, the cooldown, a refusal): the node's reading is the day's
                # row ONLY while the series holds no beaconcha.in row (the earlier leg of its declared handover); once
                # it does, the node is the cross-check alone — a fallback is never written into the current leg
                held = self._held(name, metric, POINT)
                if node is None:
                    out.fail(self.SOURCE, name, f"{metric}: NOTHING STORED — the public beacon node: "
                                                + ("; ".join(nerrs) or "not declared"), TIER)
                elif held:
                    out.fail(self.SOURCE, name,
                             f"{metric}: NOTHING STORED for today — beaconcha.in gave no row "
                             f"({why[0] if why else 'see its line above'}) and the series is on its rows since "
                             f"{min(held).date()}; the public beacon node read {node['eth']:,.0f} ETH at finalized "
                             f"slot {node['slot']} (the cross-check, not stored: a fallback is never written into the "
                             f"series' current leg)", TIER)
                else:
                    self.node.store(name, nspec, node, out)
                    stored_today = True
            if stored_today:
                self.daily.done(k_stake, day)

    def _epochs(self, name: str, spec: dict, node, nerrs, deadline: float, out) -> bool:
        """The days owed (history_from..today, newest first) by epoch number; True when today's row is stored."""
        metric, cmetric = spec["metric"], spec["count_metric"]
        end = today().normalize()
        have = self._held(name, metric, POINT) & self._held(name, cmetric, COUNT_POINT)
        todo = [d for d in pd.date_range(pd.Timestamp(spec["history_from"]), end)[::-1] if d not in have]
        staked, counts, refused, stopped, check = [], [], [], "", None
        for d in todo:
            stopped = self._left(spec, deadline)
            if stopped:
                break
            ep = epoch_at(d)
            try:
                body = self._post(spec, spec["epoch_url"], {"chain": spec.get("chain", "mainnet"),
                                                            "epoch": {"number": int(ep)}})
            except Exception as e:  # noqa: BLE001 — a refused key, the plan, a rate limit: stop, never hammer
                st = getattr(e, "status", None)
                stopped = self._scrub(spec, f"epoch {ep} ({d.date()}): {type(e).__name__}"
                                            + (f" HTTP {st}" if st else "") + f": {str(e)[:160]}")
                break
            got = parse_epoch(body, ep)
            if isinstance(got, str):
                refused.append(f"{d.date()}: {self._scrub(spec, got)}")
                continue
            lo, hi = config.METRICS[metric]["sanity_min"], config.METRICS[metric]["sanity_max"]
            if not (lo <= got[0] <= hi):
                refused.append(f"{d.date()}: {got[0]:,.0f} ETH is outside the band {lo:,.0f}..{hi:,.0f}")
                continue
            if d == end:
                check = node_cross_check(got[0], d, node, spec["cross_check"])
                if check["status"] == "DISAGREES":
                    refused.append(f"{d.date()}: {got[0]:,.0f} ETH REFUSED — {check['text']}")
                    continue
            staked.append((d, got[0]))
            counts.append((d, got[1]))
        left = len(todo) - len(staked) - len(refused)
        tail = (f"; {len(refused)} day(s) REFUSED (nothing stored for them): " + "; ".join(refused[:4])
                if refused else "") + (f"; STOPPED at {stopped} — {left} day(s) left, the next run resumes"
                                       if stopped else "")
        if not staked:
            (out.fail if todo else out.mark_current)(
                *((self.SOURCE, name, f"{metric}, {cmetric}: NOTHING STORED this run" + tail, TIER) if todo else
                  (self.SOURCE, name, metric, f"{metric}: every day from {spec['history_from']} is held", TIER)))
            return not todo
        self._wrote = True
        staked.sort()
        counts.sort()
        note = (f"; {check['text']}" if check else
                "; no row of today in this read, so no cross-check (the node serves no past state)")
        if node is None and nerrs:
            note += f" [node: {'; '.join(nerrs)[:200]}]"
        last = staked[-1]
        out.add(tidy(staked, name, metric, POINT, TIER), self.SOURCE, name,
                f"{metric}: {len(staked)} day(s) {staked[0][0].date()}..{last[0].date()}, latest {last[1]:,.0f} ETH — "
                f"beaconcha.in v2 epoch total_validator_balance (Σ actual balances of all validators, wei → ETH) at "
                f"each day's first epoch; {len(have)} day(s) already held; {self.requests} request(s) this run"
                + note + tail, TIER)
        out.add(tidy(counts, name, cmetric, COUNT_POINT, TIER), self.SOURCE, name,
                f"{cmetric}: {len(counts)} day(s), latest {counts[-1][1]:,} active validators (beaconcha.in v2 epoch "
                f"validators_count)", TIER)
        return last[0] == end and not stopped

    def _ethstore(self, name: str, spec: dict, deadline: float, out) -> None:
        """ETH.STORE's daily apr.combined for the days owed since history_from: the smallest trailing window that
        reaches the oldest owed day, paged newest first until it is reached."""
        ymetric = spec["yield_metric"]
        end, first = today().normalize(), pd.Timestamp(spec["history_from"])
        have = self._held(name, ymetric, ETHSTORE_POINT)
        # the newest datapoint is the beaconchain-day that ENDED at the last 12:00:23 UTC (half an hour allowed for its
        # finality): the day dated yesterday after ~12:30 UTC, the day before until then
        newest = (_utcnow() - pd.Timedelta(hours=12, minutes=30)).normalize() - pd.Timedelta(days=1)
        owed = [d for d in pd.date_range(first, newest) if d not in have] if newest >= first else []
        if not owed:
            out.mark_current(self.SOURCE, name, ymetric, f"{ymetric}: every ETH.STORE day to {newest.date()} is held "
                                                         f"(the next one is complete after 12:00 UTC)", TIER)
            self.daily.done(stake_mark(name, ymetric), str(end.date()))
            return
        reach = (end - min(owed)).days + 1
        window = next((w for w, n in _WINDOWS if reach <= n), _WINDOWS[-1][0])
        rows, refused, cursor, stopped = {}, [], "", ""
        for _page in range(int(spec.get("ethstore_max_pages", 9))):
            stopped = self._left(spec, deadline)
            if stopped:
                break
            try:
                body = self._post(spec, spec["ethstore_url"], {
                    "chain": spec.get("chain", "mainnet"), "range": {"evaluation_window": window},
                    "page_size": 10, "cursor": cursor})
            except Exception as e:  # noqa: BLE001
                st = getattr(e, "status", None)
                stopped = self._scrub(spec, f"{type(e).__name__}" + (f" HTTP {st}" if st else "") + f": {str(e)[:160]}")
                break
            got = parse_ethstore(body)
            if isinstance(got, str):
                stopped = self._scrub(spec, got)
                break
            page, bad, cursor = got
            refused += bad
            for d, v, note in page:
                rows.setdefault(d, (v, note))
            if not cursor or not page or min(d for d, _v, _n in page) <= min(owed):
                break
        new = sorted((d, v, n) for d, (v, n) in rows.items() if d not in have and d >= first)
        tail = (f"; {len(refused)} datapoint(s) REFUSED: " + "; ".join(refused[:3]) if refused else "") + (
            f"; STOPPED at {stopped}" if stopped else "")
        if not new:
            (out.fail if (stopped or refused) else out.mark_current)(
                *((self.SOURCE, name, f"{ymetric}: NOTHING STORED this run" + tail, TIER) if (stopped or refused) else
                  (self.SOURCE, name, ymetric, f"{ymetric}: no ETH.STORE day newer than the store's", TIER)))
            return
        frames = [tidy([(d, v)], name, ymetric, f"{ETHSTORE_POINT}[{n}]", TIER) for d, v, n in new]
        out.add(pd.concat(frames, ignore_index=True), self.SOURCE, name,
                f"{ymetric}: {len(new)} day(s) {new[0][0].date()}..{new[-1][0].date()}, latest {new[-1][1]:.4%} — "
                f"ETH.STORE apr.combined (beaconcha.in v2 eth-store, window {window}): the day's consensus + execution "
                f"rewards ÷ Σ effective balance of the validators active all day x 365, each row dated the UTC day its "
                f"beaconchain-day starts ({new[-1][2]})" + tail, TIER)
        if not stopped:
            self.daily.done(stake_mark(name, ymetric), str(end.date()))


def _chain(*its):
    for it in its:
        yield from it


# ===== THE REBUILD (P5-1.12): the days before the first node row, from BigQuery's execution-layer data. =====
# L (the lock: Σ validator balances + pending deposits) is what the execution layer sees; rolled BACK from a live node
# read: L(start of day d) = L(start of day d+1) − deposits(d) + withdrawals(d) − rewards(d). B = L − Q, Q the deposit
# queue simulated forward from Electra at the spec churn. Pure functions — staked_eth_rebuild.py feeds them the
# query's rows and the anchor; the tests feed them stubs.
def cl_reward_eth(stake_eth: float, days: float = 1.0, base_reward_factor: int = 64, epochs_per_day: int = 225) -> float:
    """The consensus layer's maximum issuance over `days` at total active stake B (ETH): each epoch Σ base rewards =
    B x BASE_REWARD_FACTOR ÷ sqrt(B x 1e9) ETH (altair get_base_reward_per_increment x the increments; the weights sum to
    WEIGHT_DENOMINATOR, so full participation pays exactly this). An upper bound: missed duties are not paid."""
    if stake_eth <= 0:
        return 0.0
    return epochs_per_day * days * stake_eth * base_reward_factor / math.sqrt(stake_eth * 1e9)


def simulate_queue(deposits: dict, start_day, end_day, churn_per_day: float, q0: float = 0.0) -> dict:
    """{day: deposit queue (ETH) at the START of day} from start_day to end_day inclusive: each day the queue takes the
    day's deposits and releases at most churn_per_day (electra process_pending_deposits at
    MAX_PER_EPOCH_ACTIVATION_EXIT_CHURN_LIMIT a epoch). q0 at the start of start_day."""
    out, q = {}, float(q0)
    for d in pd.date_range(pd.Timestamp(start_day), pd.Timestamp(end_day)):
        out[d] = q
        q = max(0.0, q + float(deposits.get(d, 0.0)) - churn_per_day)
    return out


def roll_back(anchor_lock: float, anchor_time: pd.Timestamp, flows: dict, partial: tuple, first_day,
              base_reward_factor: int = 64, epochs_per_day: int = 225, queue: dict | None = None) -> dict:
    """{day: L at 00:00 UTC of day} for first_day..the anchor's day, rolled back from the anchor's L.
    flows = {day: (deposits ETH, withdrawals ETH)} for whole days before the anchor's day; partial = (deposits,
    withdrawals) from 00:00 of the anchor's day to the anchor. Rewards use the balance B = L − queue (queue: {day: Q})."""
    A = pd.Timestamp(anchor_time)
    day_a = A.normalize()
    frac = (A - day_a).total_seconds() / 86400.0
    q = queue or {}
    b_a = anchor_lock - q.get(day_a, 0.0)
    lock = anchor_lock - partial[0] + partial[1] - cl_reward_eth(b_a, frac, base_reward_factor, epochs_per_day)
    out = {day_a: lock}
    d = day_a - pd.Timedelta(days=1)
    while d >= pd.Timestamp(first_day):
        if d not in flows:
            raise KeyError(f"no flow row for {d.date()} — the rebuild needs every day (a hole is never 0)")
        dep, wd = flows[d]
        b = lock - q.get(d + pd.Timedelta(days=1), 0.0)
        lock = lock - dep + wd - cl_reward_eth(b, 1.0, base_reward_factor, epochs_per_day)
        out[d] = lock
        d -= pd.Timedelta(days=1)
    return out


def overlap_test(rebuilt: dict, stored: dict, first, last, tol_pct: float, min_days: int) -> dict:
    """Every stored day in [first, last] against the rebuild: passed only when at least min_days days are compared and
    EVERY one is within ±tol_pct (Jake 2026-11-04: "agrees within ±1% on the overlap"). Missing rebuild days fail."""
    days = [d for d in sorted(stored) if pd.Timestamp(first) <= d <= pd.Timestamp(last)]
    gaps = {d: (rebuilt[d] / stored[d] - 1.0) * 100.0 for d in days if d in rebuilt and stored[d]}
    missing = [d for d in days if d not in rebuilt]
    worst = max(gaps, key=lambda d: abs(gaps[d])) if gaps else None
    out = {"compared": len(gaps), "missing": [str(d.date()) for d in missing], "tolerance_pct": tol_pct,
           "min_days": min_days, "worst_day": str(worst.date()) if worst is not None else None,
           "worst_gap": gaps[worst] if worst is not None else None,
           "mean_gap": (sum(gaps.values()) / len(gaps)) if gaps else None,
           "outside": [str(d.date()) for d in gaps if abs(gaps[d]) > tol_pct]}
    out["passed"] = (not missing and len(gaps) >= min_days and not out["outside"])
    return out
