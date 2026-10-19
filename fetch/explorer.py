"""
fetch/explorer.py — event logs from block-explorer APIs, paged by RECORD COUNT.

** WHY THIS EXISTS. ** Every Ethereum event scan in this tool was blocked by one RPC-tier
restriction: Alchemy's free plan caps eth_getLogs at 10 blocks per request, and publicnode
refuses the method outright. That is a limit on the RPC product, not on the data. A block
explorer indexes logs server-side and pages by NUMBER OF RECORDS, so a five-million-block history
with a few hundred matching events is a single request instead of half a million.

THREE BACKENDS, ROUTED PER CHAIN FROM config.EXPLORER_LOG_ROUTES:
  etherscan   Etherscan V2 — one key, a chainid parameter, ~60 chains. Free tier serves logs on
              some chains and not others; the table in config says which, with its date.
  blockscout  Etherscan-compatible request shape, one host per chain. Covers what Etherscan's
              free tier excludes.
  alchemy     (Round G 2.7, 2026-10-18) Alchemy's Transfers API, alchemy_getAssetTransfers, on the keyed
              Alchemy URL of the chain (fetch.chain.alchemy_endpoint: <CHAIN>_RPC_URL, or the key of any
              other Alchemy *_RPC_URL). It indexes ERC-20 TRANSFERS server-side and pages by record count
              (pageKey), so it serves exactly one query shape: Transfer(from, to, value) of one token, from
              and/or to one address. Anything else is refused with that reason — never approximated.
              Each transfer is returned as the log it came from (topics [Transfer, from, to], data =
              rawContract.value, logIndex from uniqueId "<tx>:log:<n>").
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
import re
import time
import urllib.parse

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


# Tries for a "server too busy" answer (2+4+8+16 s of backoff) before the fallback explorer is asked.
BUSY_TRIES = 5

_ALCHEMY_LOG_INDEX = re.compile(r":log:(0x[0-9a-fA-F]+|\d+)$")


_ZERO_WORD = "0x" + "0" * 64


def transfer_shaped(address, topics) -> bool:
    """A Transfer query the Transfers API answers exactly: one token, from and/or to one NON-ZERO address. The zero
    address is refused — it is the API's own default for an unset fromAddress / toAddress, so a mint or burn filter
    would be read as no filter at all."""
    t = [str(x).lower() if x else None for x in (list(topics or []) + [None] * 3)]
    sides = [x for x in t[1:3] if x]
    return (bool(address) and t[0] == TRANSFER_TOPIC and bool(sides) and not any(t[3:])
            and all(x != _ZERO_WORD and int(x, 16) != 0 for x in sides))


def alchemy_transfer_log(t: dict, token: str) -> dict:
    """One alchemy_getAssetTransfers ERC-20 transfer as the Transfer log it was indexed from. Raises ExplorerRefused on a
    record without the raw value or the log index — a transfer that cannot be placed is not guessed at."""
    raw = (t.get("rawContract") or {}).get("value")
    m = _ALCHEMY_LOG_INDEX.search(str(t.get("uniqueId") or ""))
    if raw is None or not m or not t.get("blockNum") or not t.get("hash"):
        raise ExplorerRefused(f"alchemy: a transfer without rawContract.value / uniqueId log index / block / hash: "
                              f"{str({k: t.get(k) for k in ('uniqueId', 'blockNum', 'hash')})[:160]}")
    ts = (t.get("metadata") or {}).get("blockTimestamp")
    when = 0
    if ts:
        import pandas as _pd
        try:
            when = int(_pd.Timestamp(ts).timestamp())
        except (TypeError, ValueError):
            when = 0
    return {"address": str((t.get("rawContract") or {}).get("address") or token).lower(),
            "topics": [TRANSFER_TOPIC, pad_address(str(t.get("from") or "0x0")), pad_address(str(t.get("to") or "0x0"))],
            "data": str(raw), "blockNumber": _int(t["blockNum"]), "timeStamp": when,
            "transactionHash": str(t["hash"]).lower(), "logIndex": _int(m.group(1))}


class ExplorerLogs:
    """getLogs against the explorers configured for a chain, paging by record count."""

    def __init__(self, http: Http | None = None, pace_sleep=None):
        # 5 req/s is the free-tier ceiling on both; 0.25s keeps a margin under it.
        # 30 s a read (G4 item 1): an explorer page answers in seconds; a 60 s wait, retried, held a tier for minutes
        self.http = http or Http(min_interval=float(config.EXPLORER_MIN_INTERVAL_S), retries=2,
                                 ratelimit_reset_ms=True, timeout=float(config.EXPLORER_READ_TIMEOUT_S))
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
    def key(name: str, chain_id: int | None = None) -> str:
        """The explorer's key; for alchemy, the chain's keyed Alchemy URL (never logged)."""
        if config.EXPLORERS[name].get("kind") == "alchemy_transfers":
            if chain_id is None:
                return ""
            from .chain import alchemy_endpoint
            chain = next((c for c, i in config.CHAIN_IDS.items() if int(i) == int(chain_id)), None)
            return alchemy_endpoint(chain) if chain else ""
        return os.environ.get(config.EXPLORERS[name]["key_env"], "").strip()

    def configured(self, chain_id: int, transfer: bool = False) -> list[str]:
        """The explorers that would be tried for this chain, in order: routed AND keyed. `transfer`: the caller's
        queries are all transfer-shaped, so the chain's opened TRANSFER routes count too."""
        order = config.explorer_order(chain_id) + (config.transfer_route(chain_id) if transfer else [])
        return [n for n in order if self.key(n, chain_id)]

    def _scrub(self, text) -> str:
        s = str(text)
        for name in config.EXPLORERS:
            if config.EXPLORERS[name].get("kind") == "alchemy_transfers":
                continue
            k = self.key(name)
            if k:
                s = s.replace(k, "***")
        from .chain import alchemy_keys, redact_urls
        for k in alchemy_keys():
            s = s.replace(k, "***")
        return redact_urls(s)

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
        for attempt in range(1, BUSY_TRIES + 1):
            self._check_budget()
            pace.wait(urllib.parse.urlsplit(url).netloc)
            from .base import set_phase
            set_phase(f"{name} getLogs on chain {chain_id}: request {self.requests.get(name, 0) + 1} "
                      f"({params.get('fromBlock')}..{params.get('toBlock')})")
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
            # THE DOCUMENTED TRANSIENTS. A rate limit is retried once after a second. "Server too busy"
            # (Etherscan on Polygon, Jake's seed 2026-10-05) is retried with backoff 2/4/8/16 s — it went
            # to the fallback at once, and Blockscout's fallback for Polygon is a paid plan (HTTP 402).
            # Anything else is the explorer's definite answer and goes to the fallback.
            said = f"{msg} {text}".lower()
            wait = (1.1 if attempt == 1 and "rate limit" in said else
                    float(2 ** attempt) if "busy" in said and attempt < BUSY_TRIES else None)
            if wait is not None:
                if self._deadline is not None and time.monotonic() + wait >= self._deadline:
                    self._errors.append(f"{name}: {'busy' if 'busy' in said else 'rate limited'}")
                    raise self._timed_out()
                log.info("explorer: %s answered %r — retry %d in %.1fs", name, self._scrub(text)[:80],
                         attempt, wait)
                sleep(wait, f"{name} explorer {'busy' if 'busy' in said else 'rate-limit'} pause")
                continue
            raise ExplorerRefused(f"{name}: {self._scrub(msg)} — {self._scrub(text)[:200]}")
        raise ExplorerRefused(f"{name}: still rate limited / too busy after {BUSY_TRIES} tries")

    # ------------------------------------------------------------------ alchemy (Transfers API)
    def _alchemy_paged(self, name: str, chain_id: int, address: str, topics: list, from_block: int,
                       to_block) -> tuple[list[dict], int]:
        """Transfer logs of one token from/to one address through alchemy_getAssetTransfers, paged by pageKey."""
        if not transfer_shaped(address, topics):
            raise ExplorerRefused(f"{name}: serves ERC-20 Transfer queries of one token from / to an address only "
                                  f"(alchemy_getAssetTransfers) — not topics {[x for x in topics if x]} on "
                                  f"{address or 'every contract'}")
        t = list(topics or []) + [None] * 3
        url = self.key(name, chain_id)
        if not url:
            raise ExplorerRefused(f"{name}: no Alchemy key for chain {chain_id} (an Alchemy URL in "
                                  f"<CHAIN>_RPC_URL; {config.EXPLORERS[name]['key_env']})")
        spec = config.EXPLORERS[name]
        max_n, cap = int(spec["max_records"]), int(config.EXPLORER_MAX_REQUESTS_PER_SCAN)
        params = {"fromBlock": hex(int(from_block)),
                  "toBlock": to_block if isinstance(to_block, str) else hex(int(to_block)),
                  "contractAddresses": [address], "category": ["erc20"], "excludeZeroValue": False,
                  "order": "asc", "maxCount": hex(max_n)}
        if t[1]:
            params["fromAddress"] = topic_address(t[1])
        if t[2]:
            params["toAddress"] = topic_address(t[2])
        if int(chain_id) in set(spec.get("metadata_chains") or ()):
            params["withMetadata"] = True
        pace = host_pace(url, float(spec.get("rate_per_s") or 2.0), sleep=self._pace_sleep)
        out, seen, n, page_key = [], set(), 0, None

        def timed_out(e: ExplorerTimeout) -> ExplorerTimeout:
            # WHAT WAS READ BEFORE THE BUDGET RAN OUT, for a resumable seed (Round G5 item 2: this route raised its
            # timeout bare, so a history longer than one run's share — Morpho's Base claims, Pendle's Berachain gauge —
            # started from block 0 every run and never stored). Transfers come in block order: only blocks BELOW the
            # last one read are whole, so the next run resumes AT that block.
            if out:
                last = max(x["blockNumber"] for x in out)
                e.partial = sorted((x for x in out if x["blockNumber"] < last),
                                   key=lambda x: (x["blockNumber"], x["logIndex"]))
                e.resume_from = last
            return e
        while True:
            if n >= cap:
                raise ExplorerRefused(f"{name}: stopped at {cap} requests without exhausting the range — a "
                                      f"truncated history is not returned as a complete one")
            try:
                self._check_budget()
            except ExplorerTimeout as e:
                raise timed_out(e) from None
            pace.wait(urllib.parse.urlsplit(url).netloc)
            from .base import set_phase
            set_phase(f"{name} Transfers API on chain {chain_id}: page {n + 1}, {len(out):,} transfer(s) so far")
            body = {"jsonrpc": "2.0", "id": n + 1, "method": "alchemy_getAssetTransfers",
                    "params": [{**params, **({"pageKey": page_key} if page_key else {})}]}
            try:
                res = self.http.post(url, json_body=body, deadline=self._deadline)
            except BudgetExhausted as e:
                self._errors.append(f"{name}: {self._scrub(e)}")
                raise timed_out(self._timed_out()) from None
            except Exception as e:  # noqa: BLE001 — reported as a refusal, never swallowed
                self._errors.append(f"{name}: {self._scrub(e)[:160]}")
                raise ExplorerRefused(f"{name}: {self._scrub(e)}") from None
            n += 1
            self.requests[name] = self.requests.get(name, 0) + 1
            if not isinstance(res, dict):
                raise ExplorerRefused(f"{name}: non-JSON-object answer ({type(res).__name__})")
            if res.get("error"):
                err = res["error"]
                msg = err.get("message") if isinstance(err, dict) else err
                raise ExplorerRefused(f"{name}: {self._scrub(msg)[:240]}")
            result = res.get("result") or {}
            if not isinstance(result, dict) or not isinstance(result.get("transfers"), list):
                raise ExplorerRefused(f"{name}: result carries no transfers list: {self._scrub(str(result))[:160]}")
            for tr in result["transfers"]:
                e = alchemy_transfer_log(tr, address)
                k = (e["transactionHash"], e["logIndex"])
                if k not in seen:
                    seen.add(k)
                    out.append(e)
            page_key = result.get("pageKey") or None
            if not page_key:
                break
        out.sort(key=lambda e: (e["blockNumber"], e["logIndex"]))
        return out, n

    # ------------------------------------------------------------------ the scan
    def _paged(self, name: str, chain_id: int, address: str, topics: list, from_block: int,
               to_block) -> tuple[list[dict], int]:
        if config.EXPLORERS[name].get("kind") == "alchemy_transfers":
            return self._alchemy_paged(name, chain_id, address, topics, from_block, to_block)
        max_n = int(config.EXPLORERS[name]["max_records"])
        cap = int(config.EXPLORER_MAX_REQUESTS_PER_SCAN)
        params = {"module": "logs", "action": "getLogs", "toBlock": to_block, "offset": max_n}
        if address:                     # None: a topic-only query (every contract), e.g. all tokens a wallet sent
            params["address"] = address
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
            if config.EXPLORERS[name].get("kind") == "alchemy_transfers":
                refused.append(f"{name}: serves transfers only, not block-by-time")
                continue
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
        # A TRANSFER-SHAPED QUERY also goes to the chain's opened transfer route (Round G 2.7: Alchemy's Transfers API,
        # opened per chain by Jake's probe — config.EXPLORER_TRANSFER_ROUTES), after the log explorers.
        if transfer_shaped(address, topics):
            tr = [n for n in config.transfer_route(chain_id) if n not in order]
            # G5 item 8c: on config.TRANSFER_FIRST_CHAINS the opened Transfers route goes FIRST
            order = (tr + order) if int(chain_id) in config.TRANSFER_FIRST_CHAINS else (order + tr)
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
