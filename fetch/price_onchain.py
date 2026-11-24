"""
fetch/price_onchain.py — THE PRICE CROSS-CHECK'S SECOND SOURCE, ON CHAIN (R21, Jake 2026-11-24: "coins.llama.fi: remove
it from the price cross-check; it asks for 50-minute waits. Use CoinGecko or an on-chain pool price instead").

CoinGecko IS the price (price_usd), so a CoinGecko re-read could only ever be FRESH-only; the second source is a market
price read from chain state, per project (config.PRICE_ONCHAIN):

  uniswap_v3   the pool's slot0().sqrtPriceX96 at THE BLOCK OF THE INSTANT (the last block at or before it):
               price(token1 per token0) = (sqrtPriceX96 / 2^96)^2 x 10^(dec0 - dec1), inverted when the token is token1
  uniswap_v2   getReserves() at that block: reserve(quote) / reserve(token), decimals applied
  hypercore_spot  Hyperliquid's own spot order book (HYPE/USDC, "@107"): the OPEN of the candle that starts at the
               instant (1d at 00:00 UTC, 1m at a spot's minute) — an exchange price, as Coinbase's is elsewhere

A pool quoted in WETH is converted with the chain's declared USDC/WETH pool AT THE SAME BLOCK (on chain too). A
stablecoin quote (USDC, USDT, USDS) is taken at $1 — declared on each pool (a depeg would show as a CHECK, never be
hidden). Which pool: the deepest Uniswap pool of the token against WETH / USDC / USDT / USDS on its chain, found with the
factory's getPool / getPair and the quote token's balance in the pool, read on chain 2026-11-24 (config).

STORED (CREDIBILITY reference only; never a headline):
  price_usd_onchain     00:00 UTC of each completed day of the last AT_DAYS (FIRST_DAYS on a first read) the store does
                        not hold yet — the instant CoinGecko's daily point is stamped
  price_usd_onchain_at  at the instant of each price_usd row of the last AT_DAYS read DURING its own day (the run's
                        spot), stamped "@<UTC instant of the block / candle>" (credibility.same_instant)
A read that fails is named and asked again next run; unknown is never 0. Keyed RPC URLs are never logged (host only).
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config
from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.price_onchain")

SOURCE = "xref_onchain"
TIER = 1
AT_DAYS = 7
FIRST_DAYS = 30
SOFT_BUDGET_S = 120.0            # the tier's budget is 150 s (fetch.TIER_BUDGET_S): no new read starts after this
SEL = {"slot0": "3850c7bd", "token0": "0dfe1681", "getReserves": "0902f1ac"}
HL_INFO = "https://api.hyperliquid.xyz/info"
Q96 = 2 ** 96


def v3_price(sqrt_price_x96: int, dec0: int, dec1: int, token_is_token0: bool) -> float:
    """The token's price in the quote from a v3 slot0 sqrtPriceX96."""
    p01 = (int(sqrt_price_x96) / Q96) ** 2 * 10 ** (int(dec0) - int(dec1))      # token1 per token0
    if p01 <= 0:
        raise ValueError("a zero pool price")
    return p01 if token_is_token0 else 1.0 / p01


def v2_price(r_token: int, r_quote: int, dec_token: int, dec_quote: int) -> float:
    """The token's price in the quote from a v2 pair's reserves."""
    if int(r_token) <= 0 or int(r_quote) <= 0:
        raise ValueError("an empty pair")
    return (int(r_quote) / 10 ** int(dec_quote)) / (int(r_token) / 10 ** int(dec_token))


class OnchainPrices:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, stored_long=None, rpc_post=None, clock=None, env_rpcs=None,
                 **_ignored):
        self.http = http or Http(min_interval=0.1, retries=2)
        self.stored_long = stored_long
        self._rpc_post = rpc_post            # tests: (url, payload) -> JSON-RPC response
        self.clock = clock or time.monotonic
        self._env_rpcs = env_rpcs            # tests: chain -> [] ; None = fetch.chain.rpc_endpoints' keyed URLs
        self._blocks: dict = {}              # (chain, unix ts) -> (block, block ts): shared by every project
        self._t0 = None

    # ------------------------------------------------------------------ plumbing
    def _left(self) -> float:
        return SOFT_BUDGET_S - (self.clock() - self._t0)

    def endpoints(self, chain: str) -> list[str]:
        keyed = []
        if self._env_rpcs is not None:
            keyed = list(self._env_rpcs.get(chain) or [])
        else:
            try:
                from .chain import rpc_endpoints                    # noqa: PLC0415
                keyed = [u for u in rpc_endpoints(chain) if u not in (config.DEFAULT_RPC.get(chain) or [])]
            except Exception:  # noqa: BLE001
                keyed = []
        return list(dict.fromkeys(keyed + list((config.PRICE_ONCHAIN_RPC.get(chain) or ()))))

    def rpc(self, chain: str, method: str, params: list):
        errs = []
        for u in self.endpoints(chain):
            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            try:
                j = self._rpc_post(u, payload) if self._rpc_post is not None else self.http.post(u, json_body=payload)
                if not isinstance(j, dict) or j.get("error") or "result" not in j:
                    raise RuntimeError(str((j or {}).get("error") if isinstance(j, dict) else j)[:120])
                return j["result"]
            except Exception as e:  # noqa: BLE001 — the next endpoint
                errs.append(f"{_host(u)}: {str(e)[:100]}")
        raise RuntimeError(f"{chain} {method}: no endpoint answered ({'; '.join(errs[-2:]) or 'none declared'})")

    def block_ts(self, chain: str, n: int) -> int:
        b = self.rpc(chain, "eth_getBlockByNumber", [hex(int(n)), False])
        if not b:
            raise RuntimeError(f"{chain}: block {n} not served")
        return int(b["timestamp"], 16)

    def block_at(self, chain: str, ts: int) -> tuple[int, int]:
        """(block, its timestamp): the LAST block with timestamp <= ts — chain state as of the instant. Interpolated
        from the head at the chain's measured block time, then bisected; cached per (chain, ts)."""
        key = (chain, int(ts))
        if key in self._blocks:
            return self._blocks[key]
        head = int(self.rpc(chain, "eth_blockNumber", []), 16)
        th = self.block_ts(chain, head)
        if th <= ts:
            raise RuntimeError(f"{chain}: the head is not past {pd.Timestamp(ts, unit='s')} yet")
        ref = max(head - 10_000, 1)
        bt = max((th - self.block_ts(chain, ref)) / max(head - ref, 1), 0.05)
        lo = max(int(head - (th - ts) / bt) - 64, 1)
        tl = self.block_ts(chain, lo)
        step = 256
        while tl > ts:                                      # widen down until below the instant
            lo, step = max(lo - step, 1), step * 4
            tl = self.block_ts(chain, lo)
        hi, step = min(lo + 128, head), 128
        thi = self.block_ts(chain, hi)
        while thi <= ts and hi < head:                     # widen up until past it
            lo, tl = hi, thi
            hi, step = min(hi + step * 2, head), step * 2
            thi = self.block_ts(chain, hi)
        for _ in range(60):
            if hi - lo <= 1:
                break
            mid = (lo + hi) // 2
            tm = self.block_ts(chain, mid)
            if tm <= ts:
                lo, tl = mid, tm
            else:
                hi = mid
        self._blocks[key] = (lo, tl)
        return lo, tl

    def eth_call(self, chain: str, to: str, sel: str, block: int) -> str:
        res = self.rpc(chain, "eth_call", [{"to": to, "data": "0x" + sel}, hex(int(block))])
        if not res or res == "0x":
            raise RuntimeError(f"{chain} {to[:10]}…: an empty answer to 0x{sel} at block {block:,}")
        return res[2:] if str(res).startswith("0x") else str(res)

    # ------------------------------------------------------------------ prices
    def pool_price(self, chain: str, pool: dict, block: int) -> float:
        """The pool's price of its token in its quote at `block` (quote units, not yet $)."""
        if pool["kind"] == "uniswap_v3":
            sqrtp = int(self.eth_call(chain, pool["pool"], SEL["slot0"], block)[:64], 16)
            t0 = "0x" + self.eth_call(chain, pool["pool"], SEL["token0"], block)[-40:]
            is0 = t0.lower() == pool["token"].lower()
            d0, d1 = (pool["token_decimals"], pool["quote_decimals"]) if is0 else \
                (pool["quote_decimals"], pool["token_decimals"])
            return v3_price(sqrtp, d0, d1, is0)
        if pool["kind"] == "uniswap_v2":
            body = self.eth_call(chain, pool["pool"], SEL["getReserves"], block)
            r0, r1 = int(body[0:64], 16), int(body[64:128], 16)
            t0 = "0x" + self.eth_call(chain, pool["pool"], SEL["token0"], block)[-40:]
            rt, rq = (r0, r1) if t0.lower() == pool["token"].lower() else (r1, r0)
            return v2_price(rt, rq, pool["token_decimals"], pool["quote_decimals"])
        raise ValueError(f"unknown pool kind {pool['kind']}")

    def usd_at(self, spec: dict, ts: int) -> tuple[float, str, int]:
        """(price $, source text, the instant measured) of the project's declared on-chain market at instant ts."""
        if spec["kind"] == "hypercore_spot":
            minute = ts - ts % 60
            daily = ts % 86400 == 0
            req = {"coin": spec["pair"], "interval": "1d" if daily else "1m", "startTime": minute * 1000,
                   "endTime": (minute + (86400 if daily else 60)) * 1000 - 1}
            body = self.http.post(HL_INFO, json_body={"type": "candleSnapshot", "req": req})
            hit = [c for c in (body or []) if isinstance(c, dict) and int(c.get("t", -1)) == minute * 1000]
            if not hit:
                raise RuntimeError(f"no {req['interval']} candle of {spec['pair']} starting "
                                   f"{pd.Timestamp(minute, unit='s')} UTC")
            return (float(hit[0]["o"]), f"hypercore_spot:{spec['pair']}:{req['interval']}_open", minute)
        chain = spec["chain"]
        block, bts = self.block_at(chain, ts)
        px = self.pool_price(chain, spec, block)
        if spec["quote"] == "WETH":
            eth = self.pool_price(chain, config.PRICE_ONCHAIN_ETH_USD[chain], block)
            px, via = px * eth, f" x ETH/USD {config.PRICE_ONCHAIN_ETH_USD[chain]['pool']}"
        else:
            via = f" ({spec['quote']} at $1)"
        return px, f"{spec['kind']}:{chain}:{spec['pool']}{via} block {block}", bts

    # ------------------------------------------------------------------ what to read
    def _stored(self, name: str, metric: str) -> set:
        sl = self.stored_long
        if sl is None or getattr(sl, "empty", True):
            return set()
        g = sl[(sl["project"] == name) & (sl["metric"] == metric)]
        return {str(d)[:10] for d in g["date"]}

    def days_wanted(self, name: str) -> list[pd.Timestamp]:
        """00:00 UTC of the completed days of the last AT_DAYS (FIRST_DAYS when nothing is stored) the store lacks."""
        have = self._stored(name, "price_usd_onchain")
        n = AT_DAYS if have else FIRST_DAYS
        end = today().normalize()
        return [d for d in (end - pd.Timedelta(days=k) for k in range(n, 0, -1)) if str(d.date()) not in have]

    def spot_instants(self, name: str) -> list[tuple]:
        """[(day, instant)] of our price_usd rows of the last AT_DAYS read during their own day with no _at row yet."""
        sl = self.stored_long
        if sl is None or getattr(sl, "empty", True) or "fetched_at" not in sl:
            return []
        g = sl[(sl["project"] == name) & (sl["metric"] == "price_usd")]
        done = self._stored(name, "price_usd_onchain_at")
        lo = today().normalize() - pd.Timedelta(days=AT_DAYS)
        out = []
        for d, f in zip(pd.to_datetime(g["date"]).dt.normalize(),
                        pd.to_datetime(g["fetched_at"], errors="coerce", utc=True).dt.tz_localize(None)):
            if f is pd.NaT or f != f or f.normalize() != d or d < lo or str(d.date()) in done:
                continue
            out.append((d, f))
        return sorted(out)

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out):
        self._t0 = self.clock()
        names = set(config.CREDIBILITY_PROJECTS)
        for p in projects:
            name = p["name"]
            spec = (config.PRICE_ONCHAIN or {}).get(name)
            if name not in names or not spec:
                continue
            rows, fails = [], []
            for d in self.days_wanted(name):
                if self._left() <= 0:
                    fails.append(f"{d.date()}: time budget reached — read next run")
                    break
                try:
                    px, src, _inst = self.usd_at(spec, int(d.timestamp()))
                    rows.append((d, px, src))
                except Exception as e:  # noqa: BLE001 — named; asked again next run
                    fails.append(f"{d.date()}: {str(e)[:160]}")
            if rows:
                frame = pd.concat([tidy([(d, v)], name, "price_usd_onchain", f"onchain:{s}", TIER) for d, v, s in rows],
                                  ignore_index=True)
                out.add(frame, SOURCE, name, f"price_usd_onchain: {len(rows)} day(s) at 00:00 UTC from "
                                             f"{spec['kind']} {spec.get('pool') or spec.get('pair')}", TIER)
            at_rows = []
            for d, inst in self.spot_instants(name):
                if self._left() <= 0:
                    fails.append(f"spot {inst}: time budget reached — read next run")
                    break
                try:
                    px, src, when = self.usd_at(spec, int(pd.Timestamp(inst).timestamp()))
                    stamp = pd.Timestamp(when, unit="s").strftime("%Y-%m-%dT%H:%M:%SZ")
                    at_rows.append((d, px, f"onchain:{src}@{stamp}"))
                except Exception as e:  # noqa: BLE001
                    fails.append(f"spot {inst}: {str(e)[:160]}")
            if at_rows:
                frame = pd.concat([tidy([(d, v)], name, "price_usd_onchain_at", s, TIER) for d, v, s in at_rows],
                                  ignore_index=True)
                out.add(frame, SOURCE, name, f"price_usd_onchain_at: {len(at_rows)} spot instant(s)", TIER)
            if fails:
                out.fail(SOURCE, name, "price_usd_onchain: not read (asked again next run): " + "; ".join(fails[:4])
                         + (f"; and {len(fails) - 4} more" if len(fails) > 4 else ""), TIER)


def _host(url: str) -> str:
    try:
        from urllib.parse import urlsplit                              # noqa: PLC0415
        return urlsplit(str(url)).netloc or str(url)[:30]
    except Exception:  # noqa: BLE001
        return "?"
