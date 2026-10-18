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
