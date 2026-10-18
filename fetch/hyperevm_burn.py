"""
fetch/hyperevm_burn.py — the HyperEVM burn's DAILY HISTORY (Round G2, D2 — Jake 2026-10-18: "Make it a dated series that
updates as more is burned, not a one-off figure").

hyperevm_zero_address_balance is read live every run (fetch/hypercore._evm_native_balance, from 2026-10-14). Its days
before that are read here from the archive: eth_getBalance(0x0) at each UTC day's FIRST HyperEVM block (the first block
whose timestamp is >= the day's 00:00 UTC, found by bisection on eth_getBlockByNumber) through an endpoint that serves
past state (rpc.purroofgroup.com; rpc.hyperliquid.xyz/evm answers 'latest' whatever block is asked — F4). Only days with
no stored row, from the first day a Hyperliquid supply series needs it (total_supply_gross / circulating_supply_first_party)
to yesterday; the day-on-day change is the burn (hyperevm_priority_burn_tokens) for consecutive days read here.
`token_metrics.py --seed hyperevm_burn` runs it. Source = the live read's measuring point with the `archive` marker.
"""
from __future__ import annotations

import logging

import pandas as pd

import config

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.hyperevm_burn")

SOURCE = "hypercore_info"
TIER = 1


def block_at(rpc, ts: int, cache: dict | None = None) -> int:
    """The first block with timestamp >= ts. rpc(method, params) -> result."""
    cache = {} if cache is None else cache

    def t(n: int) -> int:
        if n not in cache:
            cache[n] = int(rpc("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)
        return cache[n]
    lo, hi = 1, int(rpc("eth_blockNumber", []), 16)
    if t(hi) < ts:
        raise ValueError(f"the head block is before {pd.Timestamp(ts, unit='s')}")
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if t(mid) < ts else (lo, mid)
    return hi


def history_rows(name: str, read: dict, days: list, rpc) -> tuple[list, list]:
    """([(day, balance)], [(day, why not read)]) — the zero address's balance at each day's first block."""
    got, failed, cache = [], [], {}
    for d in sorted(days):
        try:
            b = block_at(rpc, int(pd.Timestamp(d).tz_localize("UTC").timestamp()), cache)
            got.append((pd.Timestamp(d), int(rpc("eth_getBalance", [read["address"], hex(b)]), 16) / 10 ** 18))
        except Exception as e:  # noqa: BLE001 — one day failing leaves the others
            failed.append((pd.Timestamp(d), str(e)[:120]))
    return got, failed


def frames(name: str, read: dict, got: list) -> list[pd.DataFrame]:
    """The stock rows, plus the burn per day where the day before was read here or is held."""
    src = config.mark_source(f"hyperevm:eth_getBalance({read['address'][:6]}…)", "archive")
    out = [tidy(got, name, read["metric"], src, TIER)] if got else []
    by_day = dict(got)
    flows = [(d, v - by_day[d - pd.Timedelta(days=1)]) for d, v in sorted(by_day.items())
             if d - pd.Timedelta(days=1) in by_day]
    if flows and read.get("derive_flow_metric"):
        out.append(tidy(flows, name, read["derive_flow_metric"], config.mark_source(src, "delta"), TIER))
    return out


def days_needed(long: pd.DataFrame, name: str, metric: str) -> list:
    """Days from the first stored row of a Hyperliquid series that subtracts the burn to yesterday, with no `metric` row."""
    if long is None or long.empty:
        return []
    s = long[long["project"] == name]
    need_from = pd.to_datetime(s[s["metric"].isin(["total_supply_gross", "circulating_supply_first_party"])]["date"])
    if need_from.empty:
        return []
    have = set(pd.to_datetime(s[s["metric"] == metric]["date"]).dt.normalize())
    return [d for d in pd.date_range(need_from.min().normalize(), today() - pd.Timedelta(days=1)) if d not in have]
