"""
fetch/tokenjar.py — UNISWAP'S REVENUE FROM THE TOKENJARS (PR1B.9, Jake 2026-11-09: "daily inflows to each TokenJar on
every chain where the fee switch is live … This is Uniswap's REVENUE line (primary)").

WHAT IS READ, PER CHAIN AND UTC DAY (config.TOKENJAR_INFLOWS["Uniswap"]["chains"]: the jar of each chain, from the day
its fee switch executed — proposals #93 / #94 / #95 / #96 / #99 / #102, decoded in config.UNISWAP_FEE_SWITCH_CHAINS):
  * the day's block range: the first block whose timestamp is >= 00:00 UTC, to the block before the next day's first
    (bisection on eth_getBlockByNumber, through the chain's state RPCs — <CHAIN>_RPC_URL first where Jake set one);
  * every ERC-20 Transfer INTO the jar: topic0 Transfer, topic2 = the jar, exactly three topics (an ERC-721 Transfer
    has four and is left out), from a log route — a keyless public RPC's eth_getLogs (chunked to the span it serves),
    or a keyless Blockscout instance's getLogs (paged by record count), or Alchemy's Transfers API when an Alchemy key
    is in .env (fetch.chain.alchemy_endpoint; never logged). The first route that answers the whole day is used, and
    which one is kept on the day;
  * NATIVE COIN INTO the jar (v4 pools in ETH / BNB / POL pay their protocol fee in the native coin, through the
    PoolManager — an internal transaction, no log): Blockscout txlistinternal + txlist (to = the jar, isError 0). A
    chain with no route that answers has its native leg NOT READ — the day is marked PARTIAL and the chain named,
    never valued at 0. (A same-transaction collect-and-release — PoolManager -> jar -> searcher, seen on mainnet — is
    why a balance difference cannot stand in for it.)

VALUE = Σ amount × 10^-decimals × the day's price (DefiLlama coins historical at 12:00 UTC, searchWidth 12h, confidence
>= min_confidence) — a PRICE SOURCE only; the quantities are on chain. UNI-V2 LP tokens (the v2 fee is minted to the jar
as LP tokens — a token whose factory() is that chain's Uniswap v2 factory) are valued at their reserves ÷ totalSupply ×
the two reserve tokens' day prices, read through Multicall3 at the day's closing block. UNKNOWN IS NEVER 0: a token
with no price (or an LP token with a reserve token unpriced) is COUNTED, NAMED (symbol, address, transfers) on the day
and excluded; the share of the day's transfers it carries is stored (tokenjar_unpriced_transfers over
tokenjar_inflow_transfers) and said on the cell.

STORED (Uniswap; source chain:tokenjars:tokenjar_inflow[<chain> $x; …]):
  tokenjar_inflow_usd          the day's inflow, every chain live that day — only once EVERY such chain's day is read
                               and priced (a chain with no route at all is named in the source and the row is PARTIAL;
                               X Layer's is declared with its evidence, config)
  tokenjar_inflow_transfers    transfers into the jars that day (ERC-20 + native)
  tokenjar_unpriced_transfers  of which, unpriced (named in the row's source)
  tokenjar_balance_usd         the jars' UNDISTRIBUTED balance at a day's closing block (balanceOf of every token seen
                               arriving + the native coin, priced the same way) — read for yesterday and for the day
                               before the 90-day window opens, every run (a STOCK; the window's start and end)

INCREMENTAL, RESUMABLE (state spec state_path, default .cache/tokenjar/state.json, saved after every chain-day): days
are read NEWEST FIRST from yesterday (the incomplete as-of day is never read) down to each chain's switch day, inside
spec budget_s — or with no limit as `token_metrics.py --seed tokenjar`. A read day keeps only its summary (value, counts,
named unpriced tokens), the tokens it saw (for the balance reads) and its block range.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

import config
from .base import Http, LogEntry, tidy, today   # noqa: F401 — LogEntry kept for parity with the other adapters
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.tokenjar")

SOURCE = "tokenjar"
TIER = 2
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
NATIVE = "native"
ROOT = Path(__file__).resolve().parent.parent
SRC = "chain:tokenjars:tokenjar_inflow"          # the stored measuring point — keep it stable (config series_handover)
SRC_BAL = "chain:tokenjars:tokenjar_balance"
COINS_PER_CALL = 60
NATIVE_TRIES = 6           # runs a day's native leg is asked before it stays NOT READ (named, PARTIAL)
MULTICALL_CHUNK = 300
SEL = {"factory": "c45a0155", "token0": "0dfe1681", "token1": "d21220a7", "getReserves": "0902f1ac",
       "totalSupply": "18160ddd", "symbol": "95d89b41", "balanceOf": "70a08231", "aggregate3": "82ad56cb"}
DEFAULTS = {"state_path": ".cache/tokenjar/state.json", "prices_path": ".cache/tokenjar/prices.json",
            "budget_s": 240, "min_confidence": 0.9, "price_hour_utc": 12, "search_width": "12h",
            "price_api": "https://coins.llama.fi", "balance_window_days": 90}


class RouteFailed(RuntimeError):
    """A route did not answer the whole range — the next route is tried; nothing partial is kept from it."""


# ===================================================================================================== pure helpers
def pad(addr: str) -> str:
    return "0x" + "0" * 24 + str(addr).lower()[2:]


def word_addr(word: str) -> str:
    return "0x" + str(word)[-40:].lower()


def day_ts(day: str, hour: int = 0) -> int:
    return int(pd.Timestamp(day).tz_localize("UTC").timestamp()) + 3600 * int(hour)


def shift(day: str, n: int) -> str:
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=n)).isoformat()


def inflows_from_logs(logs: list, jar: str, skip=()) -> dict:
    """{token: [raw sum, transfers]} of the ERC-20 Transfers INTO `jar` among `logs` (eth_getLogs / Blockscout shape;
    topics may carry a trailing null). Exactly three topics (ERC-20; ERC-721 has four), topic2 = the jar, from ≠ the
    jar; duplicates by (transaction, log index) counted once. `skip`: tokens whose moves the native leg already counts
    (a chain's native coin with an ERC-20 face, config native_erc20)."""
    jar_w, out, seen = pad(jar), defaultdict(lambda: [0, 0]), set()
    skip = {str(s).lower() for s in skip}
    for lg in logs:
        topics = [t for t in (lg.get("topics") or []) if t]
        if len(topics) != 3 or str(topics[0]).lower() != TRANSFER or str(topics[2]).lower() != jar_w:
            continue
        if word_addr(topics[1]) == jar.lower():
            continue
        key = (str(lg.get("transactionHash")).lower(), str(lg.get("logIndex")))
        if key in seen:
            continue
        seen.add(key)
        tok = str(lg.get("address")).lower()
        if tok in skip:
            continue
        data = str(lg.get("data") or "0x")
        raw = int(data, 16) if data not in ("0x", "") else 0
        if raw <= 0:
            continue
        out[tok][0] += raw
        out[tok][1] += 1
    return {k: v for k, v in out.items()}


def native_from_txs(rows: list, jar: str) -> tuple[int, int]:
    """(raw sum, transfers) of native coin INTO `jar` among Blockscout txlist / txlistinternal rows (isError 0, value > 0,
    from ≠ the jar); duplicates by (hash, index / trace id) counted once."""
    total, n, seen = 0, 0, set()
    for r in rows:
        if str(r.get("to") or "").lower() != jar.lower() or str(r.get("from") or "").lower() == jar.lower():
            continue
        if str(r.get("isError", "0")) not in ("0", "", "None") or str(r.get("txreceipt_status", "1")) == "0":
            continue
        v = int(str(r.get("value") or "0"))
        if v <= 0:
            continue
        key = (str(r.get("hash") or r.get("transactionHash")).lower(), str(r.get("index") or r.get("traceId") or ""),
               str(r.get("type") or ""))
        if key in seen:
            continue
        seen.add(key)
        total += v
        n += 1
    return total, n


def lp_unit_value(meta: dict, price_of) -> float | None:
    """$ per LP token (1e18 base units) from {t0, t1, r0, r1, supply} at the closing block and price_of(token) ->
    (price, decimals) | None. None when either reserve token is unpriced or the supply is 0 (never half a pool)."""
    p0, p1 = price_of(meta["t0"]), price_of(meta["t1"])
    sup = int(meta.get("supply") or 0)
    if p0 is None or p1 is None or sup <= 0:
        return None
    tvl = int(meta["r0"]) / 10 ** int(p0[1]) * float(p0[0]) + int(meta["r1"]) / 10 ** int(p1[1]) * float(p1[0])
    return tvl / (sup / 1e18)


def lp_priced_side(meta: dict, price_of, raw: int) -> float:
    """The claim on the PRICED reserve alone of an LP token whose other reserve token has no price ($, a diagnostic —
    NOT counted: the unpriced side is not valued at the pool's own price)."""
    sup = int(meta.get("supply") or 0)
    if sup <= 0:
        return 0.0
    share, v = raw / sup, 0.0
    for t, r in (("t0", "r0"), ("t1", "r1")):
        p = price_of(meta[t])
        if p is not None:
            v += int(meta[r]) / 10 ** int(p[1]) * float(p[0]) * share
    return v


def value_tokens(tokens: dict, price_of, lp: dict, symbols: dict | None = None) -> dict:
    """The day's verdict: {usd, lp_usd, transfers, unpriced_transfers, unpriced [[addr, symbol, transfers]], by_token}.
    tokens {addr: [raw, n]}; price_of(addr) -> (price, decimals) | None; lp {addr: meta} for the UNI-V2 LP tokens."""
    usd = lp_usd = lp_one_side = 0.0
    n_all = n_un = 0
    unpriced, by_token = [], {}
    for addr, (raw, n) in tokens.items():
        n_all += int(n)
        p = price_of(addr)
        if p is not None:
            v = int(raw) / 10 ** int(p[1]) * float(p[0])
        elif addr in lp:
            u = lp_unit_value(lp[addr], price_of)
            v = None if u is None else int(raw) / 1e18 * u
            if v is not None:
                lp_usd += v
            else:
                lp_one_side += lp_priced_side(lp[addr], price_of, int(raw))
        else:
            v = None
        if v is None:
            n_un += int(n)
            unpriced.append([addr, (symbols or {}).get(addr) or ("UNI-V2" if addr in lp else ""), int(n)])
            continue
        usd += v
        by_token[addr] = v
    unpriced.sort(key=lambda x: -x[2])
    return {"usd": usd, "lp_usd": lp_usd, "transfers": n_all, "unpriced_transfers": n_un, "unpriced": unpriced,
            "by_token": by_token, "lp_one_side_usd": lp_one_side}


def _decode_string(hexdata: str) -> str:
    from eth_abi import decode                                         # noqa: PLC0415
    b = bytes.fromhex(str(hexdata)[2:] if str(hexdata).startswith("0x") else str(hexdata))
    if not b:
        return ""
    try:
        return str(decode(["string"], b)[0])[:24]
    except Exception:  # noqa: BLE001 — a bytes32 symbol (MKR-style)
        return b[:32].rstrip(b"\x00").decode("utf-8", "ignore")[:24]


# ===================================================================================================== the fetcher
class TokenJarInflows:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, rpc_post=None, clock=None, root=None, env_rpcs=None, **_ignored):
        self.http = http or Http(min_interval=0.2, retries=2, ratelimit_reset_ms=True)
        # the keyless Blockscout instances answer 429 to bursts (eth.blockscout.com, 2026-11-09): spaced 1 s, retried
        self.bs_http = self.http if http is not None else Http(min_interval=1.0, retries=4, ratelimit_reset_ms=True)
        self._rpc_post = rpc_post            # tests: (url, payload) -> JSON-RPC response
        self.clock = clock or time.monotonic
        self.root = Path(root) if root else ROOT
        self._env_rpcs = env_rpcs            # tests: chain -> [] (no keyed endpoint); None = fetch.chain.rpc_endpoints

    # ------------------------------------------------------------------ plumbing
    def _path(self, p: str) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    def _load(self, p: str) -> dict:
        try:
            return json.loads(self._path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        atomic_write_text(self._path(self.spec["state_path"]), json.dumps(self.st, separators=(",", ":")))
        atomic_write_text(self._path(self.spec["prices_path"]), json.dumps(self.prices, separators=(",", ":")))

    def _left(self) -> float:
        return self._deadline - self.clock()

    def _post(self, url: str, payload):
        if self._rpc_post is not None:
            return self._rpc_post(url, payload)
        return self.http.post(url, json_body=payload)

    def _get(self, url: str, params: dict):
        h = self.bs_http if "/api" in url and "module" in params else self.http
        return h.get(url, params=params)

    def rpc(self, url: str, method: str, params: list):
        j = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(j, dict):
            raise RouteFailed(f"{method}: a non-JSON-RPC answer")
        if j.get("error"):
            raise RouteFailed(f"{method}: {str(j['error'])[:200]}")
        return j.get("result")

    def state_rpcs(self, chain: str, cs: dict) -> list[str]:
        """The chain's endpoints for state reads (headers, archive eth_call / eth_getBalance): a keyed <CHAIN>_RPC_URL /
        Alchemy URL first where .env has one (fetch.chain.rpc_endpoints' prepend), then the chain's declared public
        ones. Never logged (a keyed URL carries its key)."""
        keyed = []
        if self._env_rpcs is not None:
            keyed = list(self._env_rpcs.get(chain) or [])
        else:
            try:
                from .chain import rpc_endpoints                    # noqa: PLC0415
                keyed = [u for u in rpc_endpoints(cs.get("rpc_chain") or chain)
                         if u not in (config.DEFAULT_RPC.get(cs.get("rpc_chain") or chain) or [])]
            except Exception:  # noqa: BLE001
                keyed = []
        return list(dict.fromkeys(keyed + list(cs.get("state_rpc") or [])))

    def srpc(self, chain: str, cs: dict, method: str, params: list):
        """A state read through the first endpoint that answers (errors joined, keys never shown)."""
        errs = []
        for u in self.state_rpcs(chain, cs):
            try:
                return self.rpc(u, method, params)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{_host(u)}: {str(e)[:120]}")
        raise RouteFailed(f"{chain} {method}: no state endpoint answered ({'; '.join(errs[-3:])})")

    # ------------------------------------------------------------------ blocks
    def block_ts(self, chain: str, cs: dict, n: int) -> int:
        cache = self.cst(chain).setdefault("ts", {})
        k = str(n)
        if k not in cache:
            b = self.srpc(chain, cs, "eth_getBlockByNumber", [hex(n), False])
            if not b:
                raise RouteFailed(f"{chain}: block {n} not served")
            cache[k] = int(b["timestamp"], 16)
        return cache[k]

    def _ts_or_old(self, chain: str, cs: dict, n: int) -> int | None:
        """A block's timestamp, or None where the endpoint serves no such block (pruned / pre-bedrock history: older
        than anything a jar day needs, so it is treated as before the target)."""
        try:
            return self.block_ts(chain, cs, n)
        except RouteFailed:
            return None

    def first_block_at(self, chain: str, cs: dict, ts: int) -> int:
        """The first block with timestamp >= ts: bracketed from the cached boundaries (or stepped back from the head at
        the chain's measured block time), then interpolation with a bisection every third step."""
        known = sorted((int(b), t) for b, t in self.cst(chain).get("ts", {}).items())
        above = [b for b, t in known if t >= ts]
        below = [b for b, t in known if t < ts]
        hi = min(above) if above else int(self.srpc(chain, cs, "eth_blockNumber", []), 16)
        th = self.block_ts(chain, cs, hi)
        if th < ts:
            raise RouteFailed(f"{chain}: the head block is before {pd.Timestamp(ts, unit='s')}")
        floor = max(int(cs.get("from_block") or 1) - 1, 1)
        lo = max(below) if below else None
        if lo is None:
            ref = max(hi - 10_000, floor)
            tr = self._ts_or_old(chain, cs, ref)
            bt = max((th - tr) / max(hi - ref, 1), 1e-3) if tr is not None and th > tr else 1.0
            step = int((th - ts) / bt * 1.02) + 16
            while lo is None:
                cand = max(hi - step, floor)
                tc = self._ts_or_old(chain, cs, cand)
                if tc is None or tc < ts or cand == floor:
                    lo = cand
                else:
                    hi, th, step = cand, tc, step * 2
        it = 0
        while hi - lo > 1:
            tl = self._ts_or_old(chain, cs, lo)
            if tl is None or it % 3 == 2:
                mid = (lo + hi) // 2
            else:
                th = self.block_ts(chain, cs, hi)
                mid = lo + int((hi - lo) * (ts - tl) / max(th - tl, 1)) if th > tl else (lo + hi) // 2
                mid = min(max(mid, lo + 1), hi - 1)
            tm = self._ts_or_old(chain, cs, mid)
            if tm is None or tm < ts:
                lo = mid
            else:
                hi = mid
            it += 1
        return hi

    def day_range(self, chain: str, cs: dict, day: str) -> tuple[int, int]:
        """[first, last] block of the UTC day on `chain` (cached in the state)."""
        cache = self.cst(chain).setdefault("day_block", {})
        for d in (day, shift(day, 1)):
            if d not in cache:
                cache[d] = self.first_block_at(chain, cs, day_ts(d))
        return int(cache[day]), int(cache[shift(day, 1)]) - 1

    # ------------------------------------------------------------------ logs
    def logs(self, chain: str, cs: dict, b0: int, b1: int, address: str | None = None,
             topics: list | None = None) -> tuple[list, str]:
        """(logs, route) for [b0, b1] from the chain's first route that answers the WHOLE range."""
        topics = topics or [TRANSFER, None, pad(cs["jar"])]
        errs = []
        for r in cs.get("logs") or ():
            try:
                if r["kind"] == "rpc":
                    ck = None
                    if address is None:              # a day's jar read: its progress is kept, so a stop resumes
                        ck = self.cst(chain).setdefault("open", {})
                        if ck.get("key") != [b0, b1, r["url"]]:
                            ck.clear()
                            ck.update({"key": [b0, b1, r["url"]], "next": b0, "logs": []})
                    got = self._rpc_logs(r, b0, b1, address, topics, ck)
                    if ck is not None:
                        ck.clear()
                    return got, _host(r["url"])
                if r["kind"] == "blockscout":
                    return self._bs_logs(r, b0, b1, address, topics), _host(r["url"])
                if r["kind"] == "alchemy_transfers" and address is None:
                    from .chain import alchemy_endpoint                # noqa: PLC0415
                    url = alchemy_endpoint(r.get("alchemy_chain") or chain)
                    if not url:
                        errs.append("alchemy: no key in .env")
                        continue
                    return self._alchemy_logs(url, cs["jar"], b0, b1), "alchemy Transfers API"
            except Exception as e:  # noqa: BLE001 — the next route
                errs.append(f"{r.get('kind')} {_host(r.get('url', ''))}: {str(e)[:160]}")
        raise RouteFailed(f"{chain}: no log route answered blocks {b0:,}..{b1:,} ({'; '.join(errs[-3:]) or 'none declared'})")

    def _rpc_logs(self, r: dict, b0: int, b1: int, address, topics, ck: dict | None = None) -> list:
        """eth_getLogs over [b0, b1] in spans the endpoint serves. `ck`: the day's checkpoint ({next, logs}) — the
        blocks read so far are kept in the state when the budget stops the read, and the next run continues there
        (Base's tenderly route serves 1,000 blocks a call: ~430 calls a day)."""
        span, out, lo = int(r.get("span") or 10_000), [], b0
        if ck is not None and ck.get("logs") is not None:
            out, lo = list(ck["logs"]), int(ck.get("next") or b0)
        cap = int(r.get("result_cap") or 10_000)
        while lo <= b1:
            if self._left() <= 0:
                if ck is not None:
                    ck["next"], ck["logs"] = lo, [_slim(x) for x in out]
                raise RouteFailed(f"time budget reached inside the day (block {lo:,} of {b1:,}; kept, resumes)")
            hi = min(lo + span - 1, b1)
            flt = {"fromBlock": hex(lo), "toBlock": hex(hi), "topics": topics}
            if address:
                flt["address"] = address
            try:
                res = self.rpc(r["url"], "eth_getLogs", [flt])
            except RouteFailed as e:
                low = str(e).lower()
                if span > 1 and any(w in low for w in ("range", "too many", "limit", "exceed", "10000", "results")):
                    span = max(span // 2, 1)
                    continue
                raise
            res = res or []
            if len(res) >= cap and hi > lo:          # a capped answer may be truncated: read the range in halves
                span = max((hi - lo + 1) // 2, 1)
                continue
            out.extend(res)
            lo = hi + 1
        return out

    def _bs_logs(self, r: dict, b0: int, b1: int, address, topics) -> list:
        out, seen, lo = [], set(), b0
        for _ in range(10_000):
            if self._left() <= 0:
                raise RouteFailed("time budget reached inside the day")
            params = {"module": "logs", "action": "getLogs", "fromBlock": lo, "toBlock": b1}
            if address:
                params["address"] = address
            for i, t in enumerate(topics):
                if t:
                    params[f"topic{i}"] = t
            ks = [i for i, t in enumerate(topics) if t]
            for a, b in zip(ks, ks[1:]):
                params[f"topic{a}_{b}_opr"] = "and"
            j = self._get(r["url"], params)
            if not isinstance(j, dict):
                raise RouteFailed("a non-JSON answer")
            res = j.get("result")
            if str(j.get("status")) != "1":
                if "no logs found" in f"{j.get('message')} {res}".lower() or res == []:
                    return out
                raise RouteFailed(f"{j.get('message')}: {str(res)[:120]}")
            page = [x for x in res if (str(x.get("transactionHash")), str(x.get("logIndex"))) not in seen]
            for x in page:
                seen.add((str(x.get("transactionHash")), str(x.get("logIndex"))))
                x["blockNumber"] = int(str(x["blockNumber"]), 16) if str(x["blockNumber"]).startswith("0x") \
                    else int(x["blockNumber"])
            out.extend(page)
            if len(res) < int(r.get("page", 1000)):
                return out
            last = max(int(str(x["blockNumber"]), 16) if str(x["blockNumber"]).startswith("0x")
                       else int(x["blockNumber"]) for x in res)
            if last <= lo and not page:
                raise RouteFailed(f"a full page inside block {lo} cannot advance the cursor")
            lo = last
        raise RouteFailed("page limit reached")

    def _alchemy_logs(self, url: str, jar: str, b0: int, b1: int) -> list:
        """alchemy_getAssetTransfers toAddress = the jar, category erc20 — each transfer as the log it came from."""
        out, key = [], None
        for _ in range(10_000):
            if self._left() <= 0:
                raise RouteFailed("time budget reached inside the day")
            p = {"fromBlock": hex(b0), "toBlock": hex(b1), "toAddress": jar, "category": ["erc20"],
                 "excludeZeroValue": True, "maxCount": hex(1000), "order": "asc"}
            if key:
                p["pageKey"] = key
            res = self.rpc(url, "alchemy_getAssetTransfers", [p]) or {}
            for t in res.get("transfers") or []:
                raw = (t.get("rawContract") or {}).get("value")
                uid = str(t.get("uniqueId") or "")
                if raw is None or ":log:" not in uid:
                    raise RouteFailed(f"a transfer without rawContract.value / log index: {uid[:80]}")
                out.append({"address": (t.get("rawContract") or {}).get("address"),
                            "topics": [TRANSFER, pad(t.get("from") or "0x0"), pad(jar)], "data": raw,
                            "transactionHash": t.get("hash"), "logIndex": uid.rsplit(":", 1)[-1],
                            "blockNumber": int(str(t.get("blockNum")), 16)})
            key = res.get("pageKey")
            if not key:
                return out
        raise RouteFailed("page limit reached")

    # ------------------------------------------------------------------ native coin
    def native(self, chain: str, cs: dict, b0: int, b1: int) -> tuple[tuple[int, int] | None, str]:
        """((raw, transfers) | None, route or why not read)."""
        if cs.get("native_not_read"):
            return None, cs["native_not_read"]
        errs = []
        for r in self._bs_routes(cs, cs.get("native") or ()):
            try:
                if r["kind"] == "blockscout":
                    rows = []
                    for action in ("txlistinternal", "txlist"):
                        rows += self._bs_txs(r, action, cs["jar"], b0, b1)
                    return native_from_txs(rows, cs["jar"]), _host(r["url"]) + (" (keyed)" if r.get("key") else "")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{_host(r.get('url', ''))}: {_scrub(str(e))[:120]}")
        return None, ("no native-coin route answered (" + ("; ".join(errs[-2:]) or "none declared") + ")")

    @staticmethod
    def _bs_routes(cs: dict, routes) -> list:
        """The declared Blockscout routes, each preceded by Blockscout's PRO API with BLOCKSCOUT_API_KEY when .env has
        it and the chain id is declared (config.EXPLORERS['blockscout'], the repo's key handling; the keyless
        instances allow ~10 calls per ~8 minutes — eth.blockscout.com's x-ratelimit headers, 2026-11-09). The key
        travels as a parameter and never reaches a message (_scrub)."""
        import os                                                       # noqa: PLC0415
        key = os.environ.get(config.EXPLORERS["blockscout"]["key_env"], "").strip()
        pro = config.EXPLORERS["blockscout"].get("pro_api")
        out = []
        for r in routes:
            if r.get("kind") == "blockscout" and key and pro and cs.get("chain_id") and not cs.get("pro_paid_only"):
                out.append({**r, "url": pro.format(chain_id=int(cs["chain_id"])), "key": True})
            out.append(r)
        return out

    def _bs_txs(self, r: dict, action: str, jar: str, b0: int, b1: int) -> list:
        out, size = [], int(r.get("page", 1000))
        for page in range(1, 1000):
            if self._left() <= 0:
                raise RouteFailed("time budget reached inside the day")
            q = {"module": "account", "action": action, "address": jar, "startblock": b0,
                 "endblock": b1, "page": page, "offset": size, "sort": "asc"}
            if r.get("key"):
                import os                                               # noqa: PLC0415
                q["apikey"] = os.environ.get(config.EXPLORERS["blockscout"]["key_env"], "").strip()
            j = self._get(r["url"], q)
            if not isinstance(j, dict):
                raise RouteFailed("a non-JSON answer")
            res = j.get("result")
            if str(j.get("status")) != "1":
                if "no transactions found" in f"{j.get('message')} {res}".lower() or res == []:
                    return out
                raise RouteFailed(f"{action}: {j.get('message')}: {str(res)[:120]}")
            out.extend(res)
            if len(res) < size:
                return out
        raise RouteFailed(f"{action}: page limit reached")

    # ------------------------------------------------------------------ multicall
    def multicall(self, chain: str, cs: dict, calls: list, block: int) -> list:
        """[(target, selector hex, arg address | None)] -> [bytes hex | None] via Multicall3.aggregate3 (allowFailure)."""
        from eth_abi import decode, encode                              # noqa: PLC0415
        out = []
        for i in range(0, len(calls), MULTICALL_CHUNK):
            chunk = calls[i:i + MULTICALL_CHUNK]
            enc = [(t, True, bytes.fromhex(sel + (pad(arg)[2:] if arg else ""))) for t, sel, arg in chunk]
            data = "0x" + SEL["aggregate3"] + encode(["(address,bool,bytes)[]"], [enc]).hex()
            res = self.srpc(chain, cs, "eth_call", [{"to": MULTICALL3, "data": data}, hex(block)])
            dec = decode(["(bool,bytes)[]"], bytes.fromhex(str(res)[2:]))[0]
            out += ["0x" + b.hex() if ok and b else None for ok, b in dec]
        return out

    def token_meta(self, chain: str, cs: dict, tokens: list, block: int) -> tuple[dict, dict]:
        """({lp addr: {t0, t1, r0, r1, supply}}, {addr: symbol}) for `tokens` at `block`: an LP token is one whose
        factory() is the chain's Uniswap v2 factory (config v2_factory)."""
        if not tokens:
            return {}, {}
        f = self.multicall(chain, cs, [(t, s, None) for t in tokens for s in (SEL["factory"], SEL["symbol"])], block)
        syms = {t: _decode_string(f[2 * i + 1]) if f[2 * i + 1] else "" for i, t in enumerate(tokens)}
        v2 = str(cs.get("v2_factory") or "").lower()
        pairs = [t for i, t in enumerate(tokens) if v2 and f[2 * i] and word_addr(f[2 * i]) == v2]
        if not pairs:
            return {}, syms
        g = self.multicall(chain, cs, [(t, SEL[s], None) for t in pairs
                                       for s in ("token0", "token1", "getReserves", "totalSupply")], block)
        lp = {}
        for i, t in enumerate(pairs):
            t0, t1, rs, sup = g[4 * i:4 * i + 4]
            if not (t0 and t1 and rs and sup):
                continue
            body = rs[2:]
            lp[t] = {"t0": word_addr(t0), "t1": word_addr(t1), "r0": str(int(body[0:64], 16)),
                     "r1": str(int(body[64:128], 16)), "supply": str(int(sup, 16))}
        return lp, syms

    # ------------------------------------------------------------------ prices
    def coin(self, chain: str, cs: dict, addr: str) -> str:
        return cs["native_coin"] if addr == NATIVE else f"{cs['llama']}:{addr}"

    def ask_prices(self, day: str, coins: list) -> None:
        """DefiLlama coins historical at the day's price hour for every coin not yet answered that day (cached;
        an answered 'no price' is remembered as null)."""
        have = self.prices.setdefault(day, {})
        want = [c for c in dict.fromkeys(coins) if c not in have]
        ts = day_ts(day, int(self.spec["price_hour_utc"]))
        for i in range(0, len(want), COINS_PER_CALL):
            chunk = want[i:i + COINS_PER_CALL]
            j = self._get(f"{self.spec['price_api']}/prices/historical/{ts}/{','.join(chunk)}",
                          {"searchWidth": self.spec["search_width"]})
            got = (j or {}).get("coins") or {}
            for c in chunk:
                g = got.get(c) or got.get(c.lower())
                # a coingecko: key (a chain's native coin) carries no decimals — the native coin's are 18 (price_fn)
                dec = g.get("decimals") if g else None
                if dec is None and g and c.startswith("coingecko:"):
                    dec = 18
                ok = (g and g.get("price") is not None and dec is not None
                      and float(g.get("confidence", 1.0)) >= float(self.spec["min_confidence"]))
                have[c] = [float(g["price"]), int(dec), str(g.get("symbol") or "")] if ok else None

    def price_fn(self, day: str, chain: str, cs: dict):
        have = self.prices.get(day, {})

        def price_of(addr: str):
            p = have.get(self.coin(chain, cs, addr))
            if p is None and addr == NATIVE:
                return None
            return None if p is None else (p[0], 18 if addr == NATIVE else p[1])
        return price_of

    # ------------------------------------------------------------------ one chain-day
    def cst(self, chain: str) -> dict:
        return self.st.setdefault("chains", {}).setdefault(chain, {"days": {}, "seen": []})

    def read_day(self, chain: str, cs: dict, day: str) -> dict:
        """Read, value and summarise one chain-day; raises RouteFailed (the day stays unread)."""
        b0, b1 = self.day_range(chain, cs, day)
        lg, route = self.logs(chain, cs, b0, b1)
        toks = inflows_from_logs(lg, cs["jar"], skip=[cs["native_erc20"]] if cs.get("native_erc20") else ())
        if cs.get("native_from") and day >= cs["native_from"]:
            nat, nroute = self.native(chain, cs, b0, b1)
        else:
            nat, nroute = (0, 0), (f"not needed: no fee is paid to this jar in the native coin before "
                                   f"{cs.get('native_from') or 'any v4 fee switch on this chain'} (v2 / v3 pay in ERC-20)")
        tokens = dict(toks)
        if nat and nat[1]:
            tokens[NATIVE] = [nat[0], nat[1]]
        self.ask_prices(day, [self.coin(chain, cs, a) for a in tokens])
        price_of = self.price_fn(day, chain, cs)
        unpriced = [a for a in tokens if a != NATIVE and price_of(a) is None]
        lp, syms = self.token_meta(chain, cs, unpriced, b1)
        if lp:
            self.ask_prices(day, [self.coin(chain, cs, m[k]) for m in lp.values() for k in ("t0", "t1")])
        v = value_tokens({a: (int(r), n) for a, (r, n) in tokens.items()}, price_of, lp, syms)
        seen = self.cst(chain)["seen"]
        add = sorted(set(toks) - set(seen))
        seen.extend(add)
        lps = self.cst(chain).setdefault("lp_seen", [])
        lps.extend(sorted(set(lp) - set(lps)))
        return {"stage": "read", "b0": b0, "b1": b1, "route": route, "usd": v["usd"], "lp_usd": v["lp_usd"],
                "lp_one_side_usd": v["lp_one_side_usd"],
                "transfers": v["transfers"], "unpriced_transfers": v["unpriced_transfers"],
                "unpriced": v["unpriced"][:12], "n_unpriced_kinds": len(v["unpriced"]), "kinds": len(tokens),
                "native": None if nat is None else [str(nat[0]), nat[1]], "native_route": nroute,
                "native_usd": v["by_token"].get(NATIVE, 0.0)}

    def retry_native(self, chain: str, cs: dict, day: str, rec: dict) -> None:
        """Ask a day's native leg again (it did not answer when the day was read): on an answer the coin is priced and
        added to the day — its stored row is replaced, PARTIAL lifted — else the try is counted."""
        nat, nroute = self.native(chain, cs, int(rec["b0"]), int(rec["b1"]))
        rec["native_tries"] = int(rec.get("native_tries") or 1) + 1
        if nat is None:
            rec["native_route"] = nroute
            return
        rec["native"], rec["native_route"] = [str(nat[0]), nat[1]], nroute
        if nat[1]:
            self.ask_prices(day, [self.coin(chain, cs, NATIVE)])
            p = self.price_fn(day, chain, cs)(NATIVE)
            rec["transfers"] = int(rec["transfers"]) + nat[1]
            if p is None:
                rec["unpriced_transfers"] = int(rec["unpriced_transfers"]) + nat[1]
                rec["unpriced"] = (rec.get("unpriced") or []) + [[NATIVE, "native", nat[1]]]
            else:
                v = nat[0] / 1e18 * float(p[0])
                rec["usd"], rec["native_usd"] = float(rec["usd"]) + v, v

    # ------------------------------------------------------------------ balances (the stock)
    def read_balance(self, chain: str, cs: dict, day: str) -> dict:
        """The jar's holdings at the day's closing block: balanceOf of every token SEEN ARRIVING in the days read, plus
        the native coin, priced at the day's price (UNI-V2 LP tokens at their reserves)."""
        _b0, b1 = self.day_range(chain, cs, day)
        seen = list(self.cst(chain).get("seen") or [])
        bal = {}
        if seen:
            res = self.multicall(chain, cs, [(t, SEL["balanceOf"], cs["jar"]) for t in seen], b1)
            bal = {t: int(r, 16) for t, r in zip(seen, res) if r and int(r, 16) > 0}
        nb = int(self.srpc(chain, cs, "eth_getBalance", [cs["jar"], hex(b1)]), 16)
        if nb > 0 and not cs.get("native_erc20"):
            bal[NATIVE] = nb
        self.ask_prices(day, [self.coin(chain, cs, a) for a in bal])
        price_of = self.price_fn(day, chain, cs)
        un = [a for a in bal if a != NATIVE and price_of(a) is None]
        lp, syms = self.token_meta(chain, cs, un, b1)
        if lp:
            self.ask_prices(day, [self.coin(chain, cs, m[k]) for m in lp.values() for k in ("t0", "t1")])
        v = value_tokens({a: (r, 1) for a, r in bal.items()}, price_of, lp, syms)
        days_read = sorted(d for d, r in self.cst(chain)["days"].items() if r.get("stage") == "read")
        return {"usd": v["usd"], "block": b1, "held_kinds": len(bal), "unpriced": v["unpriced"][:8],
                "n_unpriced": len(v["unpriced"]), "seen_kinds": len(seen),
                "seen_from": days_read[0] if days_read else None}

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        for p in projects:
            spec = (getattr(config, "TOKENJAR_INFLOWS", {}) or {}).get(p["name"])
            if spec:
                self._project(p["name"], {**DEFAULTS, **spec}, out, unbounded)

    def live_chains(self, day: str) -> list[str]:
        return [c for c, cs in self.spec["chains"].items() if cs["from_day"] <= day]

    def _project(self, name: str, spec: dict, out, unbounded: bool) -> None:
        self.spec = spec
        if unbounded and self.bs_http is not self.http:
            self.bs_http.max_retry_wait = 600.0       # a seed waits out a keyless Blockscout window (x-ratelimit-reset)
        self._deadline = self.clock() + (float("inf") if unbounded else float(spec["budget_s"]))
        st = self._load(spec["state_path"])
        self.st = st if st.get("version") == 1 else {"version": 1, "chains": {}, "balances": {}}
        self.prices = self._load(spec["prices_path"])
        yesterday = str((today() - pd.Timedelta(days=1)).date())
        notes, failed = [], {}
        first = min(cs["from_day"] for cs in spec["chains"].values())
        day = yesterday
        try:
            while day >= first and self._left() > 0:
                for chain in self.live_chains(day):
                    cs = spec["chains"][chain]
                    if cs.get("not_read") or not cs.get("logs"):
                        continue
                    rec = self.cst(chain)["days"].get(day)
                    if rec and rec.get("stage") == "read":
                        if rec.get("native") is None and cs.get("native") and \
                                int(rec.get("native_tries") or 1) < NATIVE_TRIES and chain not in failed and self._left() > 0:
                            self.retry_native(chain, cs, day, rec)
                            self._save()
                        continue
                    if chain in failed or self._left() <= 0:
                        continue
                    try:
                        self.cst(chain)["days"][day] = self.read_day(chain, cs, day)
                    except Exception as e:  # noqa: BLE001 — the day stays unread; this chain waits for the next run
                        failed[chain] = f"{day}: {str(e)[:200]}"
                    self._save()
                day = shift(day, -1)
            if self._left() <= 0:
                notes.append(f"time budget reached at {day} — the next run resumes")
            self._balances(yesterday, spec, notes)
        finally:
            self._save()
        for c, why in failed.items():
            notes.append(f"{c} not read past {why}")
        self._emit(name, out, notes)
        if failed:
            out.warn(SOURCE, name, "TokenJar chain-day(s) not read this run (retried next run): "
                     + "; ".join(f"{c} {why}" for c, why in failed.items()), TIER)

    def _balances(self, yesterday: str, spec: dict, notes: list) -> None:
        """The stock at yesterday's close and at the close of the day before the balance window opens."""
        n = int(spec["balance_window_days"])
        for day in (yesterday, shift(yesterday, -n)):
            if self._left() <= 0 and day != yesterday:
                break
            rec = self.st.setdefault("balances", {}).get(day)
            if rec and rec.get("complete"):
                continue
            per, why = {}, {}
            for chain in self.live_chains(day):
                cs = spec["chains"][chain]
                if cs.get("not_read"):
                    continue
                try:
                    per[chain] = self.read_balance(chain, cs, day)
                except Exception as e:  # noqa: BLE001
                    why[chain] = str(e)[:160]
            self.st["balances"][day] = {"per": per, "not_read": why,
                                        "complete": not why and all(
                                            (self.cst(c)["days"].get(spec["chains"][c]["from_day"]) or {}).get("stage")
                                            == "read" for c in per)}
            self._save()

    # ------------------------------------------------------------------ emit
    def day_total(self, day: str) -> dict | None:
        """The day's total over every chain live that day, or None while a chain with a route has not read it."""
        per, missing, declared, partial_native = {}, [], [], []
        for chain in self.live_chains(day):
            cs = self.spec["chains"][chain]
            if cs.get("not_read") or not cs.get("logs"):
                (declared if cs.get("evidence_nil") else missing).append(chain)
                continue
            rec = self.cst(chain)["days"].get(day)
            if not rec or rec.get("stage") != "read":
                return None
            per[chain] = rec
            if rec.get("native") is None:
                partial_native.append(chain)
        usd = sum(r["usd"] for r in per.values())
        one_side = sum(float(r.get("lp_one_side_usd") or 0.0) for r in per.values())
        n = sum(int(r["transfers"]) for r in per.values())
        un = sum(int(r["unpriced_transfers"]) for r in per.values())
        top = sorted(((k, c, sym or a[:10]) for c, r in per.items() for a, sym, k in r.get("unpriced") or ()),
                     reverse=True)
        names = [f"{c}:{sym}×{k:,}" for k, c, sym in top]
        parts = "; ".join(f"{c} ${r['usd']:,.0f}" + (f" (LP ${r['lp_usd']:,.0f})" if r.get("lp_usd") else "")
                          for c, r in per.items() if r["usd"] or r["transfers"])
        quiet = [c for c, r in per.items() if not (r["usd"] or r["transfers"])]
        bits = [parts or "nothing arrived"]
        if quiet:
            bits.append("read, nothing arrived: " + ", ".join(quiet))
        if un:
            bits.append(f"UNPRICED {un:,} of {n:,} transfers ({un / n:.1%}), excluded: " + ", ".join(names[:6])
                        + (f"; LP tokens with one reserve unpriced: their priced reserve alone ${one_side:,.0f}, not "
                           f"counted" if one_side else ""))
        if partial_native:
            bits.append("native coin NOT READ on " + ", ".join(partial_native))
        if missing:
            bits.append("NOT READ (no route): " + ", ".join(missing))
        if declared:
            bits.append("not read, declared nil: " + ", ".join(declared))
        src = f"{SRC}[{_clean(' | '.join(bits))[:850]}]"
        if missing or partial_native:
            src = config.mark_source(src, "PARTIAL")
        return {"usd": usd, "transfers": n, "unpriced": un, "source": src, "lp_one_side": one_side}

    def _emit(self, name: str, out, notes: list) -> None:
        rows, cnt, unp, one = [], [], [], []
        days = sorted({d for c in self.spec["chains"] for d in self.cst(c)["days"]})
        for day in days:
            t = self.day_total(day)
            if t is None:
                continue
            rows.append((day, t["usd"], t["source"]))
            cnt.append((day, float(t["transfers"])))
            unp.append((day, float(t["unpriced"])))
            one.append((day, float(t["lp_one_side"])))
        frames = []
        if rows:
            frames.append(pd.concat([tidy([(d, v)], name, self.spec["metric"], s_, TIER) for d, v, s_ in rows],
                                    ignore_index=True))
            frames.append(tidy(cnt, name, self.spec["count_metric"], f"{SRC}[transfers in]", TIER))
            frames.append(tidy(unp, name, self.spec["unpriced_metric"], f"{SRC}[transfers in with no price]", TIER))
            if self.spec.get("one_side_metric"):
                frames.append(tidy(one, name, self.spec["one_side_metric"],
                                   f"{SRC}[UNI-V2 LP tokens in with one reserve unpriced: the priced reserve's share "
                                   f"alone — a size, NOT counted in the inflow]", TIER))
        brow = []
        for day, rec in sorted((self.st.get("balances") or {}).items()):
            per = rec.get("per") or {}
            if not per:
                continue
            usd = sum(float(v["usd"]) for v in per.values())
            txt = "; ".join(f"{c} ${v['usd']:,.0f} ({v['held_kinds']} held of {v['seen_kinds']} seen)" for c, v in per.items())
            inner = f"at each day's closing block: {txt}" + (
                f"; NOT READ: {', '.join(rec['not_read'])}" if rec.get("not_read") else "") + (
                "" if rec.get("complete") else "; tokens seen only since the first day read")
            src = f"{SRC_BAL}[{_clean(inner)[:850]}]"
            if not rec.get("complete"):
                src = config.mark_source(src, "PARTIAL")
            brow.append((day, usd, src))
        if brow:
            frames.append(pd.concat([tidy([(d, v)], name, self.spec["balance_metric"], s_, TIER) for d, v, s_ in brow],
                                    ignore_index=True))
        msg = (f"{self.spec['metric']}: {len(rows)} day(s)" + (f" {rows[0][0]}..{rows[-1][0]}" if rows else "")
               + f"; balances {len(brow)} day(s)" + ("; " + "; ".join(notes) if notes else ""))
        if frames:
            out.add(pd.concat(frames, ignore_index=True), SOURCE, name, msg, TIER)
        else:
            out.warn(SOURCE, name, "nothing stored — " + msg, TIER)


def _slim(lg: dict) -> dict:
    """A log reduced to what inflows_from_logs reads (the checkpoint stays small)."""
    return {k: lg.get(k) for k in ("address", "topics", "data", "transactionHash", "logIndex")}


def _scrub(text: str) -> str:
    """A message with the Blockscout key masked (it travels as a query parameter)."""
    import os                                                           # noqa: PLC0415
    k = os.environ.get(config.EXPLORERS["blockscout"]["key_env"], "").strip()
    return str(text).replace(k, "***") if k else str(text)


def _clean(text: str) -> str:
    """A source bracket's text: no bracket inside it (it would end the annotation early) and no colon-free surprise."""
    return str(text).replace("[", "(").replace("]", ")")


def _host(url: str) -> str:
    """The host only — a keyed URL's path never reaches a log."""
    try:
        from urllib.parse import urlsplit                              # noqa: PLC0415
        return urlsplit(str(url)).netloc or str(url)[:30]
    except Exception:  # noqa: BLE001
        return "?"
