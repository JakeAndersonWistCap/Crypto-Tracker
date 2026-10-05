"""
fetch/explorer.py — event logs from block-explorer APIs, paged by RECORD COUNT.

** WHY THIS EXISTS. ** Every Ethereum event scan in this tool was blocked by one RPC-tier
restriction: Alchemy's free plan caps eth_getLogs at 10 blocks per request, and publicnode
refuses the method outright. That is a limit on the RPC product, not on the data. A block
explorer indexes logs server-side and pages by NUMBER OF RECORDS, so a five-million-block history
with a few hundred matching events is a single request instead of half a million.

TWO EXPLORERS, ROUTED PER CHAIN FROM config.EXPLORER_LOG_ROUTES:
  etherscan   Etherscan V2 — one key, a chainid parameter, ~60 chains. Free tier serves logs on
              some chains and not others; the table in config says which, with its date.
  blockscout  Etherscan-compatible request shape, one host per chain. Covers what Etherscan's
              free tier excludes.
The first explorer in a chain's order is tried; the other is the fallback. WHICH ONE SERVED IS
RETURNED WITH EVERY SCAN, so a run log always says where a figure came from.

PAGINATION IS ON RECORDS, NOT BLOCKS. Each request asks for up to `max_records` logs from a block
cursor. A full page moves the cursor to the LAST block in it — not past it, because that block
may hold more matching logs than fitted — and duplicates are dropped by (transaction, logIndex).
A page that is entirely one block cannot advance the cursor, so the next request pages within
that block instead. Nothing is inferred from a short answer except that the range is exhausted.

** THE KEY NEVER REACHES A LOG. ** It travels as a query parameter, and a requests exception
carries the full URL. Every message leaving this module is scrubbed of it.
"""
from __future__ import annotations

import logging
import os
import time

import config
from .base import BudgetExhausted, Http, host_pace, sleep

log = logging.getLogger("token_metrics.fetch.explorer")

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class ExplorerRefused(Exception):
    """No explorer served the request. The message says what each one answered."""



class ExplorerTimeout(ExplorerRefused):
    """The scan's TOTAL time budget ran out. Raised straight through get_logs — never handed to
    the next provider, which is how one GEODNET scan once spent 2,508s retrying two explorers.
    Carries `partial` (the whole blocks read so far) and `resume_from` when raised mid-page."""
    partial: list | None = None
    resume_from: int | None = None

def pad_address(address: str) -> str:
    """An address as a 32-byte topic word, lower-case, 0x-prefixed."""
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def topic_address(word: str) -> str:
    """The address in the low 20 bytes of a topic word."""
    return "0x" + str(word).lower()[-40:]


def _int(value) -> int:
    """Explorer numbers arrive as hex strings ('0x1b4'), decimal strings or ints."""
    if isinstance(value, int):
        return value
    s = str(value or "").strip()
    if not s or s == "0x":
        return 0
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def normalise(entry: dict) -> dict:
    """One log in the shape the rest of this codebase reads: ints for block and index."""
    topics = [t for t in (entry.get("topics") or []) if t]
    return {
        "address": str(entry.get("address") or "").lower(),
        "topics": [t if str(t).startswith("0x") else "0x" + str(t) for t in topics],
        "data": entry.get("data") or "0x",
        "blockNumber": _int(entry.get("blockNumber")),
        "timeStamp": _int(entry.get("timeStamp")),
        "transactionHash": str(entry.get("transactionHash") or "").lower(),
        "logIndex": _int(entry.get("logIndex")),
    }


class ExplorerLogs:
    """getLogs against the explorers configured for a chain, paging by record count."""

    def __init__(self, http: Http | None = None, pace_sleep=None):
        # 5 req/s is the free-tier ceiling on both; 0.25s keeps a margin under it.
        self.http = http or Http(min_interval=float(config.EXPLORER_MIN_INTERVAL_S), retries=2,
                                 ratelimit_reset_ms=True)
        # ONE PACER PER HOST FOR THE WHOLE RUN (Jake, 2026-10-05): each adapter used to hold its own
        # ExplorerLogs and its own spacing, so chainlink_fees, logscan and balance_flow together drew
        # HTTP 429 from base.blockscout.com. Every call now waits on fetch.base.host_pace for its host —
        # api.etherscan.io is ONE host (one key, one limit) across every chain id. A test that injects
        # its own http paces on its own clock (pace_sleep), or not at all.
        self._pace_sleep = pace_sleep or (time.sleep if http is None else (lambda _s: None))
        self.requests: dict[str, int] = {}
        self._deadline: float | None = None
        self._budget_s: float | None = None
        self._errors: list[str] = []

    # ------------------------------------------------------------------ the time budget
    def start_budget(self, seconds: float) -> None:
        """ONE budget for a whole scan — every holder, both directions, BOTH providers."""
        self._budget_s, self._deadline, self._errors = float(seconds), time.monotonic() + float(seconds), []

    def clear_budget(self) -> None:
        self._budget_s = self._deadline = None

    def _timed_out(self, why: str = "") -> ExplorerTimeout:
        errs = "; ".join(self._errors[-6:]) or why or "none recorded"
        return ExplorerTimeout(f"explorer scan timed out after {self._budget_s:.0f}s, provider errors: {errs}")

    def _check_budget(self) -> None:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise self._timed_out()

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def key(name: str) -> str:
        return os.environ.get(config.EXPLORERS[name]["key_env"], "").strip()

    def configured(self, chain_id: int) -> list[str]:
        """The explorers that would be tried for this chain, in order: routed AND keyed."""
        return [n for n in config.explorer_order(chain_id) if self.key(n)]

    def _scrub(self, text) -> str:
        s = str(text)
        for name in config.EXPLORERS:
            k = self.key(name)
            if k:
                s = s.replace(k, "***")
        return s

    def _endpoint(self, name: str, chain_id: int) -> tuple[str, dict]:
        spec = config.EXPLORERS[name]
        if name == "etherscan":
            return spec["base_url"], {"chainid": chain_id}
        # A KEY GOES TO THE PRO API (per-instance keys are deprecated): one host for every chain.
        if spec.get("pro_api") and self.key(name):
            return spec["pro_api"].format(chain_id=int(chain_id)), {}
        host = (spec.get("hosts") or {}).get(chain_id)
        if not host:
            raise ExplorerRefused(f"{name}: no host configured for chain {chain_id}")
        return host, {}

    def _call(self, name: str, chain_id: int, params: dict):
        key = self.key(name)
        if not key:
            raise ExplorerRefused(f"{name}: no {config.EXPLORERS[name]['key_env']} in .env")
        url, base = self._endpoint(name, chain_id)
        query = {**base, **params, "apikey": key}
        pace = host_pace(url, float(config.EXPLORERS[name].get("rate_per_s") or 1.0 / config.EXPLORER_MIN_INTERVAL_S),
                         sleep=self._pace_sleep)
        for attempt in (1, 2):
            self._check_budget()
            pace.wait()
            try:
                body = (self.http.get(url, params=query, deadline=self._deadline)
                        if self._deadline is not None else self.http.get(url, params=query))
            except BudgetExhausted as e:
                self._errors.append(f"{name}: {self._scrub(e)}")
                raise self._timed_out() from None
            except Exception as e:  # noqa: BLE001 — reported as a refusal, never swallowed
                if "429" in str(e) and pace.gap:
                    # STILL LIMITED AFTER THE WAITS: halve this host's rate for the rest of the run, for
                    # every adapter that shares it (Jake's run 2026-10-05: Base/OP 429 at 4 req/s).
                    pace.slow_to(0.5 / pace.gap)
                    log.info("explorer: %s answered 429 — pace for its host now %.2f req/s", name, 1 / pace.gap)
                self._errors.append(f"{name}: {self._scrub(e)[:160]}")
                raise ExplorerRefused(f"{name}: {self._scrub(e)}") from None
            self.requests[name] = self.requests.get(name, 0) + 1
            if not isinstance(body, dict):
                raise ExplorerRefused(f"{name}: non-JSON-object answer ({type(body).__name__})")
            status, msg, res = str(body.get("status", "")), str(body.get("message", "")), body.get("result")
            if status == "1":
                return res
            text = res if isinstance(res, str) else msg
            # "No records found" is an ANSWER, not a refusal: the range holds nothing.
            if "no records found" in f"{msg} {text}".lower() or (status == "0" and res == []):
                return []
            # THE ONE DOCUMENTED TRANSIENT, retried once after a second. Anything else is the
            # explorer's definite answer and goes to the fallback, not round the loop again.
            if attempt == 1 and "rate limit" in str(text).lower():
                if self._deadline is not None and time.monotonic() + 1.1 >= self._deadline:
                    self._errors.append(f"{name}: rate limited")
                    raise self._timed_out()
                sleep(1.1, f"{name} explorer rate-limit pause")
                continue
            raise ExplorerRefused(f"{name}: {self._scrub(msg)} — {self._scrub(text)[:200]}")
        raise ExplorerRefused(f"{name}: rate limited twice")

    # ------------------------------------------------------------------ the scan
    def _paged(self, name: str, chain_id: int, address: str, topics: list, from_block: int,
               to_block) -> tuple[list[dict], int]:
        max_n = int(config.EXPLORERS[name]["max_records"])
        cap = int(config.EXPLORER_MAX_REQUESTS_PER_SCAN)
        params = {"module": "logs", "action": "getLogs", "address": address, "toBlock": to_block,
                  "offset": max_n}
        set_topics = [i for i, t in enumerate(topics) if t]
        for i in set_topics:
            params[f"topic{i}"] = topics[i]
        # Etherscan-shaped APIs need an explicit operator between every pair of topics given.
        for a in set_topics:
            for b in set_topics:
                if a < b:
                    params[f"topic{a}_{b}_opr"] = "and"
        cursor, page, n, seen, out = int(from_block), 1, 0, set(), []
        while True:
            if n >= cap:
                raise ExplorerRefused(
                    f"{name}: stopped at {cap} requests without exhausting the range (cursor "
                    f"block {cursor:,}). Raise EXPLORER_MAX_REQUESTS_PER_SCAN deliberately, or "
                    f"narrow the scan — a truncated history is not returned as a complete one.")
            try:
                res = self._call(name, chain_id, {**params, "fromBlock": cursor, "page": page})
            except ExplorerTimeout as e:
                # WHAT WAS READ BEFORE THE BUDGET RAN OUT, for a resumable seed (fetch/logcache.py).
                # Only blocks BELOW the cursor are whole: the cursor block itself may hold more
                # matching logs than the last page fitted.
                e.partial = sorted((x for x in out if x["blockNumber"] < cursor),
                                   key=lambda x: (x["blockNumber"], x["logIndex"]))
                e.resume_from = cursor
                raise
            n += 1
            if not isinstance(res, list):
                raise ExplorerRefused(f"{name}: result is {type(res).__name__}, not a list")
            batch = [normalise(r) for r in res]
            for e in batch:
                k = (e["transactionHash"], e["logIndex"])
                if k not in seen:
                    seen.add(k)
                    out.append(e)
            if len(res) < max_n:
                break
            last = max(e["blockNumber"] for e in batch)
            if last > cursor:
                cursor, page = last, 1          # re-read `last`: it may hold more than fitted
            else:
                page += 1                        # a whole page in one block — page within it
        out.sort(key=lambda e: (e["blockNumber"], e["logIndex"]))
        return out, n

    def block_at(self, chain_id: int, timestamp: int) -> int:
        """The first block at or after `timestamp`, from the chain's explorer (getblocknobytime).
        Etherscan answers the number as a string; Blockscout as {"blockNumber": ...}."""
        refused = []
        for name in config.explorer_order(chain_id):
            try:
                res = self._call(name, chain_id, {"module": "block", "action": "getblocknobytime",
                                                  "timestamp": int(timestamp), "closest": "after"})
            except ExplorerRefused as e:
                refused.append(str(e))
                continue
            if isinstance(res, dict):
                res = res.get("blockNumber")
            try:
                return int(str(res))
            except (TypeError, ValueError):
                refused.append(f"{name}: not a block number: {str(res)[:80]}")
        raise ExplorerRefused("; ".join(refused) or f"no explorer is routed for chain {chain_id}")

    def get_logs(self, chain_id: int, address: str, topics: list, from_block: int = 0,
                 to_block="latest") -> tuple[list[dict], dict]:
        """Every log matching (address, topics) in [from_block, to_block].

        Returns (logs, meta) where meta = {explorer, requests, refused}. Raises ExplorerRefused
        when no explorer serves it, naming what each said — never an empty list in its place.
        """
        order = config.explorer_order(chain_id)
        if not order:
            raise ExplorerRefused(f"no explorer is routed for chain {chain_id} in "
                                  f"config.EXPLORER_LOG_ROUTES")
        refused = []
        for name in order:
            try:
                logs, n = self._paged(name, chain_id, address, topics, from_block, to_block)
            except ExplorerTimeout:
                raise                            # out of time: do NOT start the next provider
            except ExplorerRefused as e:
                refused.append(str(e))
                log.info("explorer: %s", e)
                continue
            return logs, {"explorer": name, "requests": n, "refused": refused}
        raise ExplorerRefused("; ".join(refused))
