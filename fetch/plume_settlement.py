"""
fetch/plume_settlement.py — Plume's settlement volume rebuilt by Artemis's published method. Jake, 2026-10-01.

ARTEMIS METHOD, UNVALIDATED (config.SETTLEMENT_REBUILD): settlement = DEX volume + NFT trading volume
+ P2P transfer volume (Artemis dbt, public mirror rbreejen/dbt@a404d28f ez_ethereum_metrics.sql:125).
The method has not been validated on Ethereum against Artemis's own figure (that needs a P2P value
series no free source carries), so every row says UNVALIDATED.

  P2P, ERC-20   GET {base}/api/v2/token-transfers?type=ERC-20 — newest first, 50 a page, keyset
                next_page_params {block_number, index}. Kept: type == "token_transfer" (mints, burns,
                spawns out), NEITHER from nor to is a contract (`is_contract` on each side), not a
                self-transfer. Amount = total.value / 10^total.decimals.
  P2P, native   GET {base}/api/v2/advanced-filters?transaction_types=COIN_TRANSFER&age_from&age_to,
                one UTC day at a time, 50 a page. Kept: type == "coin_transfer" (the endpoint relabels
                a value transfer INTO a contract as contract_interaction), neither side a contract, not a
                self-transfer. Amount = value / 1e18 PLUME.
  Prices        DefiLlama's historical price for each token on that day (coins.llama.fi, plume_mainnet:
                <address>; native PLUME as coingecko:plume). THE LIQUIDITY FILTER, ADAPTED: Artemis keeps
                a token only if it is in the chain's top 250 DEX pairs by volume; Plume has a handful
                of DEX pairs, so "top 250" is every traded token. A token counts here only if DefiLlama
                prices it that day (long-tail prices come from DEX liquidity). Unpriced value is
                logged, never stored.
  DEX           DefiLlama's chain DEX volume (dex_volume_usd, fetch/llama.py), added at read time.
  NFT           ~0 on Plume — not read, and the row says so.

A DAY IS STORED ONLY WHEN BOTH LEGS COVER IT COMPLETELY. Progress is cached
(<logcache>/plume-settlement.json), so a run cut short resumes: the first year is
`token_metrics.py --seed plume_settlement` (~21,755 ERC-20 pages at Jake's measured ~2,980
transfers/day, plus the native pages), then each run tops up the newest blocks and days.
Blockscout's default limit is 300 requests/min per IP; requests are paced under it.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.plume_settlement")

SOURCE = "plume_settlement"
TIER = 1
LABEL = "Artemis method, UNVALIDATED"


def _ts(item) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(item["timestamp"]).tz_convert(None) if pd.Timestamp(item["timestamp"]).tzinfo \
            else pd.Timestamp(item["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def p2p_side_ok(item) -> bool:
    """Neither side a contract, and not a self-transfer."""
    f, t = item.get("from") or {}, item.get("to") or {}
    if f.get("is_contract") or t.get("is_contract"):
        return False
    return bool(f.get("hash")) and bool(t.get("hash")) and str(f["hash"]).lower() != str(t["hash"]).lower()


class PlumeSettlement:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, prices=None, cache_file: Path | None = None,
                 max_seconds: float | None = 240, clock=time.monotonic, **_ignored):
        self.http = http or Http(min_interval=0.25, retries=3)
        self._prices = prices                # tests inject {(day, coin): price}
        self.cache_file, self.max_seconds, self.clock = cache_file, max_seconds, clock

    # --- state ------------------------------------------------------------------------------
    def _path(self) -> Path:
        if self.cache_file:
            return Path(self.cache_file)
        from .logcache import LogCache
        return LogCache().root / "plume-settlement.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._path().read_text())
        except (OSError, ValueError):
            return {"erc20": {"newest": None, "oldest": None, "cursor": None, "done_back": False, "days": {}},
                    "native": {"days": {}}}

    def _save(self, st: dict) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st))

    # --- the read -----------------------------------------------------------------------------
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        for p in projects:
            spec = p.get("settlement_rebuild")
            if spec and spec.get("engine", "blockscout") == "blockscout":   # NEAR's is fetch/near_bigquery
                self._project(p["name"], spec, out, unbounded)

    def _over(self, t0, unbounded) -> bool:
        return not unbounded and self.max_seconds is not None and self.clock() - t0 > self.max_seconds

    def _add_erc20(self, st: dict, item, floor: pd.Timestamp) -> None:
        d = _ts(item)
        if d is None or d < floor or item.get("type") != "token_transfer" or not p2p_side_ok(item):
            return
        tot, tok = item.get("total") or {}, item.get("token") or {}
        try:
            amt = float(tot["value"]) / 10 ** int(tot["decimals"])
        except (KeyError, TypeError, ValueError):
            return
        addr = str(tok.get("address_hash") or tok.get("address") or "").lower()
        if not addr:
            return
        day = st["days"].setdefault(str(d.normalize().date()), {})
        day[addr] = day.get(addr, 0.0) + amt

    def _erc20(self, spec, st, floor, t0, unbounded, out, name) -> int:
        """FIRST RUN: newest first back to the floor, page by page, the cursor saved after each page
        (a cut run resumes from it). LATER RUNS: a TOP-UP from the newest block down to the newest
        already covered — collected aside and merged only when it reaches that block, so a cut top-up
        is simply redone — then the saved backfill continues if it never reached the floor.
        Returns pages read."""
        url, pages = f"{spec['base'].rstrip('/')}/api/v2/token-transfers", 0
        e = st["erc20"]
        first = e["newest"] is None
        params, top, fresh = {"type": "ERC-20"}, None, {"days": {}}
        while True:
            if self._over(t0, unbounded):
                break
            j = self.http.get(url, params=params)
            pages += 1
            items = j.get("items") or []
            if top is None and items:
                top = (int(items[0].get("block_number") or 0), _ts(items[0]))
            reached = False
            for it in items:
                if not first and int(it.get("block_number") or 0) <= e["newest"]:
                    reached = True
                    break
                self._add_erc20(e if first else fresh, it, floor)
            nxt = j.get("next_page_params")
            if first:
                last = _ts(items[-1]) if items else None
                if last is not None:
                    e["oldest"] = str(last)
                e["cursor"] = nxt
                if not nxt or last is None or last < floor:
                    e["done_back"] = True
                    break
            elif reached or not nxt:
                for d, toks in fresh["days"].items():          # the top-up is complete: merge it
                    day = e["days"].setdefault(d, {})
                    for t, a in toks.items():
                        day[t] = day.get(t, 0.0) + a
                e["newest"], e["newest_ts"] = top[0], str(top[1])
                top = None
                break
            params = {"type": "ERC-20", **nxt}
        if first and top is not None:
            e["newest"], e["newest_ts"] = top[0], str(top[1])
        # BACKFILL: from the saved cursor towards the floor (a first run cut short)
        while not first and not e["done_back"] and e["cursor"]:
            if self._over(t0, unbounded):
                break
            j = self.http.get(url, params={"type": "ERC-20", **e["cursor"]})
            pages += 1
            items = j.get("items") or []
            for it in items:
                self._add_erc20(e, it, floor)
            last = _ts(items[-1]) if items else None
            if last is not None:
                e["oldest"] = str(last)
            e["cursor"] = j.get("next_page_params")
            if not e["cursor"] or last is None or last < floor:
                e["done_back"] = True
        return pages

    def _native_day(self, spec, day: pd.Timestamp) -> tuple[float | None, int, str]:
        """(PLUME moved P2P that day, pages, why-None)."""
        url = f"{spec['base'].rstrip('/')}/api/v2/advanced-filters"
        params = {"transaction_types": "COIN_TRANSFER", "age_from": f"{day.date()}T00:00:00Z",
                  "age_to": f"{day.date()}T23:59:59Z"}
        total, pages = 0.0, 0
        while True:
            j = self.http.get(url, params=params)
            pages += 1
            for it in j.get("items") or []:
                if it.get("type") == "coin_transfer" and p2p_side_ok(it) and str(it.get("status", "ok")) in ("ok", "success", "None"):
                    try:
                        total += float(it.get("value") or 0) / 1e18
                    except (TypeError, ValueError):
                        continue
            if not j.get("next_page_params"):
                return total, pages, ""
            if pages >= int(spec["native_max_pages_per_day"]):
                return None, pages, (f"more than {pages * 50:,} native transfers on {day.date()} — over the "
                                     f"declared cap of {spec['native_max_pages_per_day']} pages a day")
            params = {**params, **j["next_page_params"]}

    def _price(self, spec, day: pd.Timestamp, coins: list[str]) -> dict:
        if self._prices is not None:
            return {c: self._prices.get((str(day.date()), c)) for c in coins}
        got = {}
        ts = int((day + pd.Timedelta(hours=12)).timestamp())
        for i in range(0, len(coins), 50):
            chunk = coins[i:i + 50]
            j = self.http.get(f"{spec['price_api'].rstrip('/')}/prices/historical/{ts}/{','.join(chunk)}")
            for c, v in ((j or {}).get("coins") or {}).items():
                got[c] = float(v["price"]) if v.get("price") is not None else None
        return got

    def _project(self, name: str, spec: dict, out, unbounded: bool) -> None:
        t0 = self.clock()
        st = self._load()
        floor = (today() - pd.Timedelta(days=int(spec["days"]))).normalize()
        try:
            pages = self._erc20(spec, st, floor, t0, unbounded, out, name)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            self._save(st)
            out.fail(SOURCE, name, f"token-transfers: {str(e).split('?')[0]} — resumes next run", TIER)
            return
        e = st["erc20"]
        # ERC-20 coverage: whole days strictly inside [oldest, newest)
        lo = pd.Timestamp(e["oldest"]).normalize() + pd.Timedelta(days=1) if e.get("oldest") else None
        if e.get("done_back"):
            lo = floor
        hi = (pd.Timestamp(e["newest_ts"]).normalize() - pd.Timedelta(days=1)) if e.get("newest_ts") else None
        yday = (today() - pd.Timedelta(days=1)).normalize()
        hi = min(hi, yday) if hi is not None else None
        stored, native_pages, notes = [], 0, []
        if lo is not None and hi is not None and lo <= hi:
            for day in pd.date_range(hi, lo, freq="-1D"):           # newest first
                key = str(day.date())
                nd = st["native"]["days"].get(key)
                if nd is None:
                    if self._over(t0, unbounded):
                        break
                    try:
                        amt, n, why = self._native_day(spec, day)
                    except Exception as ex:  # noqa: BLE001
                        notes.append(f"native {key}: {str(ex).split('?')[0]}")
                        break
                    native_pages += n
                    if amt is None:
                        notes.append(why)
                        continue
                    st["native"]["days"][key] = nd = amt
        self._save(st)
        # value every day both legs cover
        days = sorted(d for d in st["native"]["days"] if lo is not None and hi is not None
                      and str(lo.date()) <= d <= str(hi.date()))
        unpriced_total = 0.0
        for key in days:
            day = pd.Timestamp(key)
            toks = e["days"].get(key, {})
            coins = [f"{spec['chain_key']}:{a}" for a in toks] + [spec["native_coin"]]
            px = st.setdefault("prices", {}).get(key)
            if px is None:
                if self._over(t0, unbounded):
                    break
                try:
                    px = self._price(spec, day, coins)
                except Exception as ex:  # noqa: BLE001
                    notes.append(f"prices {key}: {str(ex).split('?')[0]}")
                    break
                st["prices"][key] = px
            if px.get(spec["native_coin"]) is None:
                notes.append(f"{key}: no PLUME price — day not stored")
                continue
            native_usd = st["native"]["days"][key] * px[spec["native_coin"]]
            tok_usd = sum(a * px[f"{spec['chain_key']}:{t}"] for t, a in toks.items()
                          if px.get(f"{spec['chain_key']}:{t}") is not None)
            unpriced = [t for t in toks if px.get(f"{spec['chain_key']}:{t}") is None]
            unpriced_total += len(unpriced)
            stored.append((day, native_usd + tok_usd))
        self._save(st)
        if not stored:
            out.skipped(SOURCE, name, f"{spec['p2p_metric']}: no day complete yet ({pages} ERC-20 page(s), "
                                      f"{native_pages} native page(s) read this run; resumes)"
                        + (f"; {'; '.join(notes[:3])}" if notes else ""), TIER)
            return
        frame = tidy(stored, name, spec["p2p_metric"], f"{SOURCE}:p2p[{LABEL}]", TIER)
        out.add(frame, SOURCE, name,
                f"{spec['p2p_metric']} = P2P transfers between non-contract accounts, ERC-20 + native PLUME, "
                f"same-day DefiLlama prices ({LABEL}); {len(frame)} day(s) {stored[0][0].date()}.."
                f"{stored[-1][0].date()}; {pages} ERC-20 + {native_pages} native page(s) this run; "
                f"{unpriced_total} token-day(s) unpriced and left out (the liquidity filter); NFT ~0, not read"
                + (f"; {'; '.join(notes[:3])}" if notes else ""), TIER)
