"""
fetch/alchemy_usage.py — WHAT THIS RUN ASKED OF ALCHEMY (Round G3 item 10, Jake 2026-10-18: "Log Alchemy usage per run
and warn before the free-tier monthly allowance gets close").

Every request to a *.g.alchemy.com host — web3 (fetch.chain._GuardedProvider) and the plain HTTP client (fetch.base.Http,
which carries the Transfers API and the hand-rolled JSON-RPC readers) — is counted here by method. The compute units are
an ESTIMATE from Alchemy's published table, not Alchemy's own meter: alchemy.com/docs/reference/compute-unit-costs, read
2026-10-18 (eth_call 26, eth_getLogs 60, eth_blockNumber 10, eth_getBalance 20, eth_getBlockByNumber 20, eth_getCode 20,
eth_chainId 0, alchemy_getAssetTransfers 120); a method not in the table is counted at eth_call's 26. The Free plan is
"30M CU per month" (alchemy.com/pricing, read 2026-10-18).

The month's running total is kept in .cache/alchemy_usage.json (machine-local, like the log cache): {"YYYY-MM": {"cu": n,
"requests": n}}. Only the host and the method are recorded — never a URL, so never a key.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("token_metrics.fetch.alchemy_usage")

CU_COST = {"eth_call": 26, "eth_getLogs": 60, "eth_blockNumber": 10, "eth_getBalance": 20,
           "eth_getBlockByNumber": 20, "eth_getCode": 20, "eth_chainId": 0, "alchemy_getAssetTransfers": 120}
CU_DEFAULT = 26
FREE_TIER_CU_MONTH = 30_000_000
# THROUGHPUT (Round G6 item 0c): the Free plan's documented limit is 300 compute units per second — alchemy.com/pricing
# ("300 CU/s (15)" for Free, read 2026-10-19) and alchemy.com/docs/reference/throughput ("a Free tier customer with a
# 300 CU/s limit can consume up to 3,000 CUs over any 10-second period"). Every request to an Alchemy host is paced to
# it run-wide (all tiers, all Alchemy chains: one app, one key), so concurrent tiers queue here instead of drawing 429s.
FREE_TIER_CU_PER_S = 300
WARN_AT, FAIL_AT = 0.80, 0.95

_lock = threading.Lock()
_run: dict[str, int] = {}


def is_alchemy(url: str) -> bool:
    try:
        return (urlsplit(str(url)).hostname or "").endswith(".g.alchemy.com")
    except Exception:  # noqa: BLE001 — a counter must never break the read it counts
        return False


def record(url: str, method: str | None) -> None:
    """Count one request to `url` (ignored unless it is an Alchemy host)."""
    if not is_alchemy(url):
        return
    with _lock:
        m = str(method or "?")
        _run[m] = _run.get(m, 0) + 1


_pace_lock = threading.Lock()
_next_free = 0.0
_paced = {"requests": 0, "waited_s": 0.0}


def pace(url: str, method: str | None, sleep=None) -> float:
    """Hold an Alchemy request until the run's CU budget per second allows it; returns the seconds waited."""
    if not is_alchemy(url):
        return 0.0
    import time
    global _next_free
    cost = CU_COST.get(str(method or ""), CU_DEFAULT) / float(FREE_TIER_CU_PER_S)
    with _pace_lock:
        now = time.monotonic()
        start = max(now, _next_free)
        _next_free = start + cost
        wait = start - now
        _paced["requests"] += 1
        _paced["waited_s"] += wait
    if wait > 0:
        (sleep or time.sleep)(wait)
    return wait


def paced() -> dict:
    with _pace_lock:
        return dict(_paced)


def method_of(body) -> str | None:
    """The JSON-RPC method of a request body (dict, list for a batch, or bytes)."""
    try:
        if isinstance(body, (bytes, bytearray)):
            body = json.loads(body)
        if isinstance(body, list):
            body = body[0] if body else {}
        return (body or {}).get("method")
    except Exception:  # noqa: BLE001
        return None


def run_counts() -> dict[str, int]:
    with _lock:
        return dict(_run)


def cu(counts: dict[str, int]) -> int:
    return sum(CU_COST.get(m, CU_DEFAULT) * n for m, n in counts.items())


def reset() -> None:
    with _lock:
        _run.clear()


def _file() -> Path:
    return Path(os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan")).parent / "alchemy_usage.json"


def commit(month: str) -> tuple[dict, dict]:
    """Add this run's counts to `month`'s total on disk, reset the run; returns (this run, the month's total)."""
    run = run_counts()
    f = _file()
    try:
        state = json.loads(f.read_text())
    except (OSError, ValueError):
        state = {}
    tot = state.setdefault(month, {"cu": 0, "requests": 0})
    tot["cu"] = int(tot.get("cu", 0)) + cu(run)
    tot["requests"] = int(tot.get("requests", 0)) + sum(run.values())
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, sort_keys=True))
    except OSError as e:
        log.info("alchemy usage: could not save %s (%s) — the month's total is this run's only", f, e)
    reset()
    return run, tot


def report(month: str) -> tuple[str, str | None]:
    """(the run's usage line, a WARN / FAIL level or None) — and the month's total saved."""
    run, tot = commit(month)
    share = tot["cu"] / FREE_TIER_CU_MONTH
    by = ", ".join(f"{m} {n}" for m, n in sorted(run.items(), key=lambda x: -x[1])) or "none"
    line = (f"ALCHEMY USAGE this run: {sum(run.values())} request(s), ~{cu(run):,} CU ({by}); month {month}: "
            f"~{tot['cu']:,} CU of the Free plan's {FREE_TIER_CU_MONTH:,} ({share:.1%}) — CU estimated from Alchemy's "
            f"published table, not its meter")
    level = "FAIL" if share >= FAIL_AT else "WARN" if share >= WARN_AT else None
    return line, level
