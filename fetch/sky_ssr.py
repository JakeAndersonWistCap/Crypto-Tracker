"""
fetch/sky_ssr.py — tier 2: the SKY SAVINGS RATE read from the sUSDS contract itself, not a dashboard (Jake's overnight
review of ab5f2a9, Phase 3, 2026-10-12): the on-chain dollar "risk-free" rate beside the T-bill.

    sUSDS 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD (Ethereum) — the address Sky's on-chain chainlog
    0xdA0Ab1e0017DEbCd72Be8599041a2aa3bA7e740F returns for getAddress("SUSDS"); symbol() reads "sUSDS".
    Both read 2026-10-12 (config.SKY_SSR).

ssr() is a per-second accumulation factor in RAY (1e27); the rate a saver earns over a year is
(ssr / 1e27) ** 31_536_000 − 1 (1000000001182641576793209935 -> 3.80%, read 2026-10-12).

HISTORY FROM THE CONTRACT'S OWN EVENTS: every change of the rate is a File(bytes32 indexed what, uint256 data) event
with what = "ssr" (20 since 2024-09-17). The rate in force at the END of each day is the data of the last such event
before midnight UTC — a step function, exact, with no sampling. The scan RECONCILES before anything is stored: the
latest event's data must equal ssr() read now, to the unit (no tolerance); a mismatch stores today's read only and
says so. Today's row is the live ssr().

Stored under the pseudo-project "Macro" (config.MACRO_PROJECT) as sky_savings_rate (fraction). Once a day; the
first read stores config.TRAJECTORY history_days (455) of days, later reads 14.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.sky_ssr")

SOURCE = "sky_ssr"
TIER = 2
RAY = 10 ** 27
YEAR_S = 31_536_000
FILE_TOPIC = "0xe986e40cc8c151830d4f61050f4fb2e4add8567caad2d5f5496f9158e91fe4c7"   # keccak("File(bytes32,uint256)")
SSR_WHAT = "0x" + b"ssr".ljust(32, b"\0").hex()


def apy(ssr_raw: int) -> float:
    """The annual rate a saver earns at a per-second RAY factor."""
    return (int(ssr_raw) / RAY) ** YEAR_S - 1.0


def daily_rates(events: list[dict], since: pd.Timestamp, until: pd.Timestamp) -> list[tuple]:
    """[(day, raw ssr in force at the end of that day)] for since..until inclusive, from File('ssr') events
    ({'timeStamp': unix, 'data': hex}). Days before the first event are left out (no rate was set)."""
    steps = sorted((pd.Timestamp(int(e["timeStamp"]), unit="s"), int(e["data"], 16)) for e in events)
    out, i, cur = [], 0, None
    for day in pd.date_range(since.normalize(), until.normalize(), freq="D"):
        end = day + pd.Timedelta(days=1)
        while i < len(steps) and steps[i][0] < end:
            cur = steps[i][1]
            i += 1
        if cur is not None:
            out.append((day, cur))
    return out


class SkySSR:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, logs_fn=None, ssr_now_fn=None, daily=None, **_ignored):
        self._logs_fn = logs_fn            # () -> [File('ssr') events]; injectable for tests
        self._ssr_now_fn = ssr_now_fn      # () -> raw ssr() now
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _logs(self) -> list[dict]:
        if self._logs_fn:
            return self._logs_fn()
        from .explorer import ExplorerLogs
        spec = config.SKY_SSR
        logs, _meta = ExplorerLogs().get_logs(spec["chain_id"], spec["address"], [FILE_TOPIC, SSR_WHAT])
        return logs

    def _ssr_now(self) -> int:
        if self._ssr_now_fn:
            return int(self._ssr_now_fn())
        from .chain import ChainReader
        spec = config.SKY_SSR
        rd = ChainReader()
        ok, why = rd.symbol_matches("ethereum", spec["address"], spec["expected_symbol"])
        if not ok:
            raise RuntimeError(f"sUSDS symbol check failed: {why}")
        return int(rd.raw("ethereum", spec["address"], "ssr"))

    def run(self, projects: list[dict], window_days, out):
        name, metric = config.MACRO_PROJECT, "sky_savings_rate"
        day = str(today().date())
        key = "macro:sky_ssr"
        if not self.daily.due(key, day):
            out.skipped(SOURCE, name, f"{metric}: read today already", TIER)
            return
        try:
            now_raw = self._ssr_now()
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{metric}: ssr() NOT READ — {type(e).__name__}: {str(e)[:160]}", TIER)
            return
        t0 = today().normalize()
        rows = [(t0, apy(now_raw))]
        detail = f"{metric}: ssr() now {apy(now_raw):.4%}"
        # the 455-day history is owed until one read of it has reconciled (a failed first scan must not shrink later
        # reads to 14 days)
        hkey = key + ":history"
        span = int(config.TRAJECTORY["history_days"]) if not self.daily.ever(hkey) else 14
        history_ok = False
        try:
            events = self._logs()
        except Exception as e:  # noqa: BLE001
            events, detail = None, detail + f"; history NOT READ ({type(e).__name__}: {str(e)[:120]})"
        if events is not None:
            last = max(events, key=lambda e: (int(e["blockNumber"]), int(e["logIndex"]))) if events else None
            if last is None or int(last["data"], 16) != now_raw:
                detail += ("; history REFUSED — the latest File('ssr') event "
                           f"({int(last['data'], 16) if last else 'none'}) is not ssr() now ({now_raw}); "
                           "only today's read is stored")
            else:
                hist = daily_rates(events, t0 - pd.Timedelta(days=span), t0 - pd.Timedelta(days=1))
                rows = [(d, apy(v)) for d, v in hist] + rows
                history_ok = True
                detail += (f"; {len(hist)} day(s) of history from {len(events)} File('ssr') events, reconciled to "
                           f"ssr() now")
        src = f"{SOURCE}:chain:ethereum:sUSDS.ssr()"
        out.add(tidy(rows, name, metric, src, TIER), SOURCE, name, detail, TIER)
        self.daily.done(key, day)
        if history_ok:
            self.daily.done(hkey, day)
