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

THE TOKEN-SCOPED BACKFILL (Jake, 2026-10-01: the chain-wide seed ran 1h+ without finishing). Only
tokens DefiLlama prices can ever count (the liquidity filter above), so the year's ERC-20 backfill can
read THOSE TOKENS ONLY, through Blockscout's Etherscan-compatible API — `erc20_route: "tokentx"`:

  scope     tokens seen so far + Blockscout's ERC-20 list (most holders first, `scope_pages` pages),
            kept where DefiLlama has ever priced them (coins.llama.fi /prices/first). Fixed once chosen.
  read      GET {base}/api?module=account&action=tokentx&contractaddress=<token>&startblock&endblock
            &sort=asc&offset=10000 (Blockscout's cap: page x offset <= 10,000). A full answer drops its
            last block (it may be cut) and the next call starts there, so no row is read twice; each
            token's block range is split into `segments` independent cursors, run `workers` at a time
            within one shared request rate.
  is_contract  tokentx carries no is_contract, so each address is checked ONCE with eth_getCode on
            {rpc} (JSON-RPC batches), cached in the state file: empty code = not a contract (an
            EIP-7702 delegation designator 0xef0100.. counts as an account, not a contract).
  kept      the v2 rule: neither side a contract, not a self-transfer, not a mint or burn (0x0 side).
  resume    the cursors, day totals and code cache are checkpointed together. A v2 scan already
            under way is KEPT: its fully covered days stand, its partial oldest day is dropped, and the
            token route covers floor .. the block before that day ends — nothing read twice.
Native PLUME has no Transfer event: it stays on advanced-filters, one day per call chain, now run
`native_workers` days at a time within the same rate. Daily top-ups stay on v2 (one day is ~60 pages).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import pandas as pd

from .base import Http, Progress, tidy, today

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


ZERO = "0x" + "0" * 40


def code_is_account(code) -> bool:
    """eth_getCode says no contract: empty, or an EIP-7702 delegation designator (an account)."""
    c = str(code or "0x").lower()
    return c in ("0x", "0x0", "") or (c.startswith("0xef0100") and len(c) == 2 + 46)


class _Pace:
    """Thread-safe spacing of calls to ONE host at `per_s` requests a second (all workers share it)."""

    def __init__(self, per_s: float, sleep=time.sleep, clock=time.monotonic):
        self.gap = 1.0 / float(per_s) if per_s else 0.0
        self.sleep, self.clock, self._next, self._lock = sleep, clock, 0.0, threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            at = max(now, self._next)
            self._next = at + self.gap
        if at > now:
            self.sleep(at - now)


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
                 max_seconds: float | None = 240, clock=time.monotonic, sleep=time.sleep, **_ignored):
        self._injected = http is not None
        self.http = http or Http(min_interval=0.25, retries=3)
        self._prices = prices                # tests inject {(day, coin): price}
        self.cache_file, self.max_seconds, self.clock, self._sleep = cache_file, max_seconds, clock, sleep
        self._tls = threading.local()
        self._code_lock = threading.Lock()
        self._paces: dict = {}

    def _client(self):
        """One Http per worker thread (a requests.Session is not shared across threads); the spacing
        is _Pace's, shared, so `min_interval` is 0 here."""
        if self._injected:
            return self.http
        if not hasattr(self._tls, "h"):
            self._tls.h = Http(min_interval=0.0, retries=3)
        return self._tls.h

    def _pace(self, host: str, per_s: float) -> _Pace:
        if host not in self._paces:
            self._paces[host] = _Pace(per_s, sleep=self._sleep)
        return self._paces[host]

    @staticmethod
    def _route(spec) -> str:
        """`erc20_route` from config; PLUME_SETTLEMENT_ROUTE overrides it for one run."""
        return os.environ.get("PLUME_SETTLEMENT_ROUTE") or spec.get("erc20_route", "v2")

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
        self._kept = getattr(self, "_kept", 0) + 1

    def _erc20(self, spec, st, floor, t0, unbounded, out, name) -> int:
        """FIRST RUN: newest first back to the floor, page by page, the cursor saved after each page
        (a cut run resumes from it). LATER RUNS: a TOP-UP from the newest block down to the newest
        already covered — collected aside and merged only when it reaches that block, so a cut top-up
        is simply redone — then the saved backfill continues if it never reached the floor.
        Returns pages read."""
        url, pages = f"{spec['base'].rstrip('/')}/api/v2/token-transfers", 0
        e = st["erc20"]
        first = e["newest"] is None
        tokentx = self._route(spec) == "tokentx" and not e.get("done_back")
        if tokentx and first:
            # the TOKEN ROUTE does the year: v2 is read for ONE page, to fix the newest block covered
            j = self.http.get(url, params={"type": "ERC-20"})
            items = j.get("items") or []
            if not items:
                return 1
            e["newest"], e["newest_ts"] = int(items[0].get("block_number") or 0), str(_ts(items[0]))
            self._save(st)
            return 1 + self._tokentx(spec, st, floor, t0, unbounded, name)
        params, top, fresh = {"type": "ERC-20"}, None, {"days": {}}
        self._kept = 0

        def covered() -> float | None:
            """How far back towards the floor the scan has reached (0..1)."""
            if not e.get("oldest") or not e.get("newest_ts"):
                return None
            hi, lo = pd.Timestamp(e["newest_ts"]), pd.Timestamp(e["oldest"])
            span = (hi - floor).total_seconds()
            return (hi - lo).total_seconds() / span if span > 0 else None

        # CHECKPOINTED (Jake, 2026-10-01): the cursor and the day totals are saved together every
        # PROGRESS_EVERY_UNITS pages or seconds, so an interrupted seed resumes from the line it printed.
        prog = Progress(f"plume_settlement ERC-20 {name}", unit="pages", fraction=covered,
                        checkpoint=lambda: self._save(st),
                        status=lambda: (f"reached back to {str(e.get('oldest') or '?')[:10]} (floor "
                                        f"{floor.date()}); {len(e['days'])} day(s) with transfers; "
                                        f"{self._kept:,} P2P transfer(s) kept this run"))
        while True:
            if self._over(t0, unbounded):
                break
            j = self.http.get(url, params=params)
            pages += 1
            items = j.get("items") or []
            if top is None and items:
                top = (int(items[0].get("block_number") or 0), _ts(items[0]))
                if first:
                    # RECORDED AT ONCE, so a first run cut short resumes as a top-up plus the saved
                    # backfill cursor — before 2026-10-01 it was written only at the end, and a cut
                    # first run started again from the top.
                    e["newest"], e["newest_ts"] = top[0], str(top[1])
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
            prog.tick()
            params = {"type": "ERC-20", **nxt}
        if first and top is not None:
            e["newest"], e["newest_ts"] = top[0], str(top[1])
        if tokentx:
            if pages:
                prog.flush(final=False)
            return pages + self._tokentx(spec, st, floor, t0, unbounded, name)
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
            prog.tick()
        if pages:
            prog.flush(final=bool(e.get("done_back")))
        return pages

    # --- THE TOKEN-SCOPED BACKFILL (module docstring) -------------------------------------------
    def _api(self, spec, params: dict):
        tt = spec.get("tokentx") or {}
        self._pace(spec["base"], float(tt.get("rate_per_s", 4))).wait()
        return self._client().get(f"{spec['base'].rstrip('/')}/api", params=params)

    def _block_at(self, spec, ts: pd.Timestamp, closest: str) -> int:
        j = self._api(spec, {"module": "block", "action": "getblocknobytime",
                             "timestamp": int(pd.Timestamp(ts).timestamp()), "closest": closest})
        r = (j or {}).get("result")
        try:
            return int(r["blockNumber"] if isinstance(r, dict) else r)
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(f"getblocknobytime {pd.Timestamp(ts)} ({closest}) answered {str(j)[:200]}") from None

    def _scope(self, spec, st) -> list[str]:
        """The tokens DefiLlama has ever priced, among those seen so far and Blockscout's ERC-20 list.
        Chosen ONCE and kept in the state, so a resumed seed reads the same set."""
        tt = st["tokentx"]
        if tt.get("scope"):
            return tt["scope"]["tokens"]
        cfg = spec.get("tokentx") or {}
        cands = {t for toks in st["erc20"]["days"].values() for t in toks}
        params = {"type": "ERC-20"}
        for _ in range(int(cfg.get("scope_pages", 20))):
            self._pace(spec["base"], float(cfg.get("rate_per_s", 4))).wait()
            j = self._client().get(f"{spec['base'].rstrip('/')}/api/v2/tokens", params=params)
            for it in j.get("items") or []:
                a = str(it.get("address_hash") or it.get("address") or "").lower()
                if a:
                    cands.add(a)
            nxt = j.get("next_page_params")
            if not nxt:
                break
            params = {"type": "ERC-20", **nxt}
        cands = sorted(cands)
        if self._prices is not None:
            priced = sorted({c.split(":", 1)[1] for (_, c) in self._prices
                             if c.startswith(spec["chain_key"] + ":") and c.split(":", 1)[1] in cands})
        else:
            priced = []
            for i in range(0, len(cands), 50):
                coins = [f"{spec['chain_key']}:{a}" for a in cands[i:i + 50]]
                j = self._client().get(f"{spec['price_api'].rstrip('/')}/prices/first/{','.join(coins)}")
                priced += [c.split(":", 1)[1].lower() for c in ((j or {}).get("coins") or {})]
            priced = sorted(set(priced))
        tt["scope"] = {"tokens": priced, "candidates": len(cands), "chosen_on": str(today().date())}
        return priced

    def _plan(self, spec, st, floor) -> dict:
        """The block range the token route covers, and its cursors — fixed once (resumable)."""
        tt, e = st["tokentx"], st["erc20"]
        if tt.get("plan"):
            return tt["plan"]
        cfg = spec.get("tokentx") or {}
        lo = self._block_at(spec, floor, "after")
        if e.get("cursor") and e.get("oldest"):
            # a v2 scan under way: its days from the one after its oldest stand; the partial oldest
            # day (and anything before it) is dropped and re-read by the token route
            first_full = pd.Timestamp(e["oldest"]).normalize() + pd.Timedelta(days=1)
            hi = self._block_at(spec, first_full, "after") - 1
            for d in [d for d in e["days"] if d < str(first_full.date())]:
                del e["days"][d]
            kept_from = str(first_full.date())
        else:
            hi, kept_from = int(e["newest"]), None
        segs = max(int(cfg.get("segments", 8)), 1)
        bounds = [lo + (hi - lo + 1) * k // segs for k in range(segs + 1)]
        tasks = [{"token": t, "next": a, "end": b - 1, "done": False}
                 for t in self._scope(spec, st) for a, b in zip(bounds, bounds[1:]) if b > a]
        tt["plan"] = {"from_block": lo, "to_block": hi, "floor": str(floor.date()), "v2_days_kept_from": kept_from,
                      "segments": segs, "planned_on": str(today().date())}
        tt["tasks"] = tasks
        self._save(st)
        return tt["plan"]

    def _codes(self, spec, addrs: list[str]) -> None:
        """eth_getCode for addresses not yet cached — JSON-RPC batches, ONE check per address ever."""
        cfg = spec.get("tokentx") or {}
        code = self._state_codes
        with self._code_lock:
            todo = sorted({a for a in addrs if a not in code})
        batch = int(cfg.get("code_batch", 100))
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            self._pace(cfg.get("rpc", "rpc"), float(cfg.get("rpc_rate_per_s", 10))).wait()
            got = self._client().post(cfg["rpc"], json_body=[
                {"jsonrpc": "2.0", "id": k, "method": "eth_getCode", "params": [a, "latest"]}
                for k, a in enumerate(chunk)])
            if not isinstance(got, list):
                raise RuntimeError(f"eth_getCode batch of {len(chunk)} answered {str(got)[:200]}")
            res = {int(r.get("id")): r for r in got if isinstance(r, dict)}
            vals = {}
            for k, a in enumerate(chunk):
                r = res.get(k) or {}
                if "result" not in r:
                    raise RuntimeError(f"eth_getCode {a}: {str(r.get('error') or r)[:200]}")
                vals[a] = 0 if code_is_account(r["result"]) else 1
            with self._code_lock:
                code.update(vals)

    def _tokentx_call(self, spec, task: dict):
        """ONE window of one token: (rows [(day, token, amount, from, to)], next block, finished)."""
        cfg = spec.get("tokentx") or {}
        n = int(cfg.get("offset", 10000))
        j = self._api(spec, {"module": "account", "action": "tokentx", "contractaddress": task["token"],
                             "startblock": task["next"], "endblock": task["end"], "sort": "asc",
                             "page": 1, "offset": n})
        res = (j or {}).get("result")
        if not isinstance(res, list):
            if str((j or {}).get("message", "")).lower().startswith("no token transfers"):
                res = []
            else:
                raise RuntimeError(f"tokentx {task['token']} {task['next']}..{task['end']}: {str(j)[:200]}")
        finished, nxt = len(res) < n, task["end"] + 1
        if not finished:
            last = int(res[-1]["blockNumber"])
            if last <= task["next"]:
                raise RuntimeError(f"tokentx {task['token']}: block {last} alone holds {n:,}+ transfers — "
                                   f"lower tokentx.offset is no help; read it with v2")
            res = [r for r in res if int(r["blockNumber"]) < last]     # the last block may be cut
            nxt = last
        rows = []
        for r in res:
            try:
                rows.append((str(pd.Timestamp(int(r["timeStamp"]), unit="s").date()), task["token"],
                             float(r["value"]) / 10 ** int(r.get("tokenDecimal") or 0),
                             str(r["from"]).lower(), str(r["to"]).lower()))
            except (KeyError, TypeError, ValueError):
                continue
        self._codes(spec, [a for row in rows for a in row[3:] if a != ZERO])
        return rows, nxt, finished

    def _tokentx(self, spec, st, floor, t0, unbounded, name) -> int:
        """Run the token route to completion or the budget. Returns calls made."""
        tt = st.setdefault("tokentx", {})
        self._state_codes = tt.setdefault("code", {})
        plan = self._plan(spec, st, floor)
        e, cfg = st["erc20"], spec.get("tokentx") or {}
        tasks = [t for t in tt["tasks"] if not t["done"]]
        span = max(plan["to_block"] - plan["from_block"] + 1, 1) * max(len(tt["scope"]["tokens"]), 1)
        kept = [0]

        def covered():
            left = sum(max(t["end"] - t["next"] + 1, 0) for t in tt["tasks"] if not t["done"])
            return 1 - left / span

        prog = Progress(f"plume_settlement tokentx {name}", unit="calls", fraction=covered,
                        checkpoint=lambda: self._save(st),
                        status=lambda: (f"{len(tt['scope']['tokens'])} token(s) in scope, blocks "
                                        f"{plan['from_block']:,}..{plan['to_block']:,}; "
                                        f"{sum(t['done'] for t in tt['tasks'])}/{len(tt['tasks'])} segment(s) done; "
                                        f"{kept[0]:,} P2P transfer(s) kept this run; "
                                        f"{len(self._state_codes):,} address(es) checked"))
        calls, err = 0, None
        workers = max(int(cfg.get("workers", 4)), 1)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            queue, inflight = list(tasks), {}
            while queue or inflight:
                while queue and len(inflight) < workers and err is None and not self._over(t0, unbounded):
                    t = queue.pop(0)
                    inflight[ex.submit(self._tokentx_call, spec, t)] = t
                if not inflight:
                    break
                done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
                for f in done:
                    t = inflight.pop(f)
                    try:
                        rows, nxt, finished = f.result()
                    except Exception as ex_:  # noqa: BLE001 — the rest drain; state stays consistent
                        err = err or ex_
                        continue
                    calls += 1
                    for day, tok, amt, a, b in rows:
                        if a == ZERO or b == ZERO or a == b:
                            continue
                        if self._state_codes.get(a) == 0 and self._state_codes.get(b) == 0:
                            dd = e["days"].setdefault(day, {})
                            dd[tok] = dd.get(tok, 0.0) + amt
                            kept[0] += 1
                    t["next"], t["done"] = nxt, finished
                    prog.tick()
                    if not finished and err is None and not self._over(t0, unbounded):
                        queue.insert(0, t)
        done_all = all(t["done"] for t in tt["tasks"])
        if done_all:
            e["oldest"], e["done_back"], e["cursor"] = str(floor), True, None
            tt["completed_on"] = str(today().date())
        prog.flush(final=done_all)
        if err is not None:
            raise err
        return calls

    def _native_day(self, spec, day: pd.Timestamp) -> tuple[float | None, int, str]:
        """(PLUME moved P2P that day, pages, why-None)."""
        url = f"{spec['base'].rstrip('/')}/api/v2/advanced-filters"
        params = {"transaction_types": "COIN_TRANSFER", "age_from": f"{day.date()}T00:00:00Z",
                  "age_to": f"{day.date()}T23:59:59Z"}
        total, pages = 0.0, 0
        concurrent = int(spec.get("native_workers", 1)) > 1
        while True:
            if concurrent:                     # days run side by side: shared pacing, a client per thread
                self._pace(spec["base"], float((spec.get("tokentx") or {}).get("rate_per_s", 4))).wait()
                j = self._client().get(url, params=params)
            else:
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
        nw = int(spec.get("native_workers", 1))
        if nw > 1:
            # side by side, the native leg covers the WHOLE window (yesterday back to the floor) without
            # waiting for the ERC-20 leg to reach a day — a day is still stored only when both cover it
            n_hi = min(hi, yday) if hi is not None else yday
            todo_native = [d for d in pd.date_range(n_hi, floor, freq="-1D") if str(d.date()) not in st["native"]["days"]]
        else:
            todo_native = ([d for d in pd.date_range(hi, lo, freq="-1D") if str(d.date()) not in st["native"]["days"]]
                           if lo is not None and hi is not None and lo <= hi else [])
        nprog = Progress(f"plume_settlement native PLUME {name}", total=len(todo_native) or None, unit="days",
                         every_units=25, checkpoint=lambda: self._save(st),
                         status=lambda: f"{native_pages:,} page(s) read; {len(st['native']['days'])} day(s) held")
        if nw > 1 and todo_native:
            # NATIVE DAYS SIDE BY SIDE (Jake, 2026-10-01): each day is its own call chain, so `nw` run at
            # once within the shared request rate; results are applied here, one thread, newest first
            with ThreadPoolExecutor(max_workers=nw) as ex:
                queue, inflight, stop = list(todo_native), {}, False
                while queue or inflight:
                    while queue and len(inflight) < nw and not stop and not self._over(t0, unbounded):
                        d = queue.pop(0)
                        inflight[ex.submit(self._native_day, spec, d)] = d
                    if not inflight:
                        break
                    done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
                    for f in done:
                        d = inflight.pop(f)
                        try:
                            amt, n, why = f.result()
                        except Exception as ex_:  # noqa: BLE001
                            notes.append(f"native {d.date()}: {str(ex_).split('?')[0]}")
                            stop = True
                            continue
                        native_pages += n
                        nprog.tick()
                        if amt is None:
                            notes.append(why)
                        else:
                            st["native"]["days"][str(d.date())] = amt
        elif lo is not None and hi is not None and lo <= hi:
            for day in pd.date_range(hi, lo, freq="-1D"):           # newest first
                key = str(day.date())
                nd = st["native"]["days"].get(key)
                if nd is None:
                    nprog.tick()
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
        if nprog.done:
            nprog.flush(final=True)
        self._save(st)
        # value every day both legs cover
        days = sorted(d for d in st["native"]["days"] if lo is not None and hi is not None
                      and str(lo.date()) <= d <= str(hi.date()))
        unpriced_total = 0.0
        pprog = Progress(f"plume_settlement prices {name}", total=len(days) or None, unit="days",
                         every_units=50, checkpoint=lambda: self._save(st),
                         status=lambda: f"{len(stored)} day(s) valued")
        for key in days:
            pprog.tick()
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
