"""
fetch/nearblocks.py — NEAR daily transactions and active accounts, from NearBlocks' API v3.
Also: an account's inflow history (near_account_flows) — a differently-shaped read, same module,
same key. Every path is v3 since 2026-10-13 (NearBlocks retires v1 on 2026-10-31).

    GET https://api.nearblocks.io/v3/txn-stats?limit=N       Authorization: Bearer <key>
    GET https://api.nearblocks.io/v3/address-stats?limit=N

Both return {"data": [...], "meta": ...}, one row per UTC day, newest first.

** THE FIELD NAMES ARE READ FROM NEARBLOCKS' OWN SOURCE, AND CHECKED AGAINST EVERY LIVE ROW. **
NearBlocks' API docs were unreachable from the build environment on 2026-09-24; its API is
open source, so the names come from the SQL that produces the response rather than from a guess:

  apps/api/src/sql/queries/stats/txn.sql      date ('YYYY-MM-DD'), txns (INT), receipts, txn_fee,
                                              txn_volume, meta_txns — from transaction_stats
  apps/api/src/sql/queries/stats/address.sql  date, active_accounts (INT), active_contracts,
                                              meta_accounts, meta_relayers — from action_stats
  apps/api/src/routes/v3/stats.ts             the two routes, bearerAuth, `limit` (each 25 rows
                                              costs one credit) and an optional single `date`
  apps/api/docs/v3-migration.md               v1 /charts is legacy; v3 splits it by metric

That is a source reading, not a live one. So the adapter verifies the shape on EVERY call: the
body must be an object with a `data` list whose rows carry `date` and the declared field. If the
first row does not, NOTHING is stored and the keys actually found are reported — the growthepie
discipline, applied mechanically instead of by a status flag, because here the expected names
are known from the producer rather than inferred.

TODAY IS NOT STORED: the current UTC day is still accumulating.

** THE KEY NEVER REACHES A LOG. ** It travels only in the Authorization header, and every message
from this module is scrubbed of it anyway.

===============================================================================================
near_account_flows — GET /v3/accounts/{account}/txns (v1 /v1/account/{account}/txns until 2026-10-13),
ACTUAL INFLOW, NOT A BALANCE. Added 2026-09-24.
===============================================================================================
Wired for NEAR's buyback wallet, buybacks.multisignature.near — actual_buyback_tokens was
gapped "WALLET KNOWN, INFLOW NOT READABLE HERE" because NEAR is not EVM and log_scans
(fetch/logscan.py) is built on eth_getLogs-shaped explorer APIs. This endpoint is NEAR's own
equivalent: an account's transaction history, filtered by action kind and date, confirmed from
NearBlocks' own source (its API docs site was unreachable, same as everywhere else in this file):

  apps/api/src/routes/account.ts               GET /:account/txns — query: from, to, action,
                                                method, after_date, before_date, cursor, page,
                                                per_page, order. bearerAuth (Bearer, same as v3).
  apps/api/src/services/account/txn.ts          predecessor_account_id (sender), receiver_
                                                account_id, block_timestamp (ns since epoch),
                                                actions: JSON_AGG(JSON_BUILD_OBJECT('action',
                                                action_kind, 'method', ..., 'deposit',
                                                args->>'deposit' (yoctoNEAR), 'fee', ..., 'args',
                                                ...)). Response: {"cursor": <next|null>, "txns": [...]}.

read 2026-09-24, both from GitHub source, same discipline as v3 above: verified on every live
call, nothing stored if the shape does not match.

** action='TRANSFER' FILTERS SERVER-SIDE, THE EXCLUDED SENDERS DO NOT. ** The endpoint's `from`
param is an exact match on ONE sender, not a NOT-IN — so it cannot exclude the OTHER near-intents
wallets' internal hops server-side (see config's intents_revenue_wallets: `moves` excludes
transfers BETWEEN the three wallets). Every TRANSFER into the account is fetched and the excluded
senders are dropped client-side before summing, one call's cost for a correct exclusion.

** NATIVE NEAR ONLY, SAME PARTIAL ALREADY DECLARED ON THE BALANCE READ. ** action='TRANSFER' is
the native NEAR action; wrap.near ft_transfer inflows (an FT, not a native action, a separate
FUNCTION_CALL) are not counted here — the same limitation already on record for the view_account
balance read of these wallets (node_api.extra_reads[0].partial). Understates by whatever arrives
as wrapped NEAR rather than native.

** NO WEI-EXACT RECONCILIATION, UNLIKE fetch/logscan.py. ** LogScan proves its EVM scans complete
by requiring sum(in) - sum(out) == balanceOf at a pinned block — an identity that holds because an
ERC-20 balance moves only by Transfer events. NEAR balances also move on gas paid, storage
staking and every other action a wallet takes, so no such identity holds here without walking the
account's entire outflow history too, which this read does not attempt. This is a measured inflow
figure, not a balance-reconciled one — a real improvement over "not readable" without claiming
the stronger guarantee LogScan gives on EVM chains.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from urllib.parse import urlparse
from collections import deque

import pandas as pd

from .base import Http, sleep as base_sleep, tidy, today, window, Progress
from .logcache import LogCache
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.nearblocks")

SOURCE = "nearblocks"
# NEARBLOCKS API v1 RETIRES 2026-10-31 (Jake 2026-10-13, Part E): every call is a /v3 path. tests/test_nearblocks_v3.py
# fails the build on any /v1/ or /v2/ NearBlocks path in the code.
V3_ACCOUNT_TXNS = "/v3/accounts/{account}/txns"
FLOW_SOURCE_V3 = f"{SOURCE}:v3-account-txns"
# THE v1 GUARD (Part E4): a /v1/ or /v2/ NearBlocks path is refused before any call is made — tests/test_nearblocks_v3.py
# also fails the build on one written anywhere in the code
_RETIRED_PATH = re.compile(r"^/v[12]/")
# A DEPRECATION / SUNSET ANSWER (Part E4): the RFC 8594 / 9745 headers, HTTP 410 Gone, or a message saying so. Logged as
# "ACTION NEEDED: NEARBLOCKS API SUNSET …" (daily_summary.py's section 3 lists it) and a FAILED line on the Run Log;
# windows/daily_run.ps1's closing console line names NearBlocks
SUNSET_MARKER = "NEARBLOCKS API SUNSET"
_SUNSET_HEADERS = ("deprecation", "sunset")
_SUNSET_WORDS = re.compile(r"deprecat|sunset|retired|no longer (?:available|supported)", re.I)


class RetiredPath(ValueError):
    """A NearBlocks v1 / v2 path — retired 2026-10-31; never called."""
TIER = 1
FLOW_PAGE_CAP = 20   # hard stop on pagination — polite, and this wallet's volume is nowhere near it

# ===== FREE-PLAN PACING. Read 2026-09-28 from NearBlocks' own source (the docs site is still =====
# unreachable from here): apps/api/src/services/rateLimiter.ts at Nearblocks/nearblocks@d8cebb2,
#   https://github.com/Nearblocks/nearblocks/blob/d8cebb2/apps/api/src/services/rateLimiter.ts
# FREE_PLAN = limit_per_minute 6, limit_per_day 333, limit_per_month 10000, and each call is
# charged ceil(rows / 25) credits (stats.ts `limit`, account.ts `per_page`) — so a v3 stats call at
# limit=100 costs 4 and a flow page at per_page=50 costs 2. Live run 20260928T090446Z made two
# v3 calls (8 credits) and then the buyback page inside the same minute: 429.
# The limiter sends NO Retry-After and no X-RateLimit-* headers — a 429 carries only a JSON
# message — so when Retry-After is absent the wait is one full minute window, not Http's 2s/4s.
CREDITS_PER_MINUTE = 6
CREDITS_PER_DAY = 333
ROWS_PER_CREDIT = 25
RATE_LIMIT_WAIT = 60.0
# v3 pages are `limit` 1..100; NearBlocks bills ceil(per_page / 25) on `per_page` only (rateLimiter.ts L132 @08a8c850),
# so a v3 page of 100 costs ONE credit — the largest page, the fewest calls
FLOW_PER_PAGE = 100
# stats/balance answers at most 365 rows: `limit` above that is refused with HTTP 422 ("Expected <=365", Jake's run
# 2026-10-07 15:51), and the refusal of one wallet skipped all three. Capped here whatever config asks; history
# older than a year would need paging by year (none of NEAR's three wallets has any: activity starts 2025-11).
STATS_BALANCE_MAX_ROWS = 365

_clock = time.monotonic   # module-level so tests can swap in a fake clock
def _sleep(seconds: float) -> None:
    base_sleep(seconds, f"NearBlocks free-plan pacing, {seconds:.0f}s")


def credits_for(rows: int) -> int:
    return max(1, -(-int(rows) // ROWS_PER_CREDIT))


class _Pacer:
    """Sliding 60s window: never more than CREDITS_PER_MINUTE credits in any 60 seconds.

    A sliding window that holds is also within any fixed calendar-minute window, whichever the
    server uses. Also refuses to spend past CREDITS_PER_DAY in one run — this counts only this
    process's spending; other users of the same key are invisible to it.
    """

    def __init__(self):
        self.spent: deque = deque()   # (monotonic time, credits)
        self.total = 0

    def wait(self, credits: int) -> float:
        if self.total + credits > CREDITS_PER_DAY:
            raise RuntimeError(f"NearBlocks free-plan daily budget: {self.total} credits spent "
                               f"this run, {credits} more would pass {CREDITS_PER_DAY}/day")
        waited = 0.0
        while True:
            now = _clock()
            while self.spent and now - self.spent[0][0] >= 60.0:
                self.spent.popleft()
            used = sum(c for _, c in self.spent)
            if used + credits <= CREDITS_PER_MINUTE:
                return waited
            pause = 60.0 - (now - self.spent[0][0]) + 0.5
            _sleep(pause)
            waited += pause

    def charge(self, credits: int) -> None:
        # stamped at completion (after any retry inside Http) — the later stamp is the safe one
        self.spent.append((_clock(), credits))
        self.total += credits


def _load_state(f, after: str) -> dict:
    empty = {"newest_ts": None, "newest_ids": [], "by_day": {}, "after": after}
    try:
        st = json.loads(f.read_text())
        return {**empty, **st} if st.get("version") == 1 else empty
    except (OSError, ValueError):
        return empty


def _save_raw(f, state: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, json.dumps({**state, "version": 1}))


def _save_state(f, after: str, newest_ts, newest_ids, by_day: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, json.dumps({"version": 1, "after": after, "newest_ts": newest_ts,
                               "newest_ids": sorted(newest_ids),
                               "by_day": {str(d.date()): v for d, v in sorted(by_day.items())}}))


def signer_participants(http, spec: dict) -> set:
    """The MPC signer's operator accounts from the contract itself (D2-4f): view `state` on `contract` over NEAR RPC,
    the participants list wherever the state nests it (Running / Resharing / Initializing). Raises if none."""
    import base64

    body = http.post(spec["rpc"], json_body={
        "jsonrpc": "2.0", "id": 1, "method": "query",
        "params": {"request_type": "call_function", "finality": "final", "account_id": spec["contract"],
                   "method_name": spec["method"], "args_base64": base64.b64encode(b"{}").decode()}})
    raw = ((body or {}).get("result") or {}).get("result")
    if not raw:
        raise ValueError(f"{spec['contract']}.{spec['method']}: no result ({str(body)[:160]})")
    found: set = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "participants" and isinstance(v, list):
                    found.update(str(e[0]) for e in v if isinstance(e, list) and e and isinstance(e[0], str))
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(json.loads(bytes(raw).decode()))
    if not found:
        raise ValueError(f"{spec['contract']}.{spec['method']}: no participants list in the state")
    return found


class NearBlocks:
    """Daily chain activity for any project declaring a `nearblocks` block."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, last_dates: dict | None = None,
                 page_cap: int = FLOW_PAGE_CAP, **_ignored):
        # page_cap: the seed (token_metrics --seed nearblocks) lifts it to read a year at once.
        self.page_cap = page_cap
        self.http = http or Http(min_interval=1.0, retries=2, rate_limit_wait=RATE_LIMIT_WAIT)
        self.pacer = _Pacer()
        # (project, metric) -> newest stored date, for the once-a-day skip of the v3 stats.
        self.last_dates = last_dates or {}
        self.sunsets: dict = {}       # path -> what the deprecation / sunset answer said (Part E4)

    def _get(self, url: str, params: dict, key: str):
        # BILLED AS NEARBLOCKS BILLS IT: ceil(per_page / 25), per_page defaulting to 25
        # (apps/api/src/middlewares/rateLimiter.ts @ d8cebb2: `req.query.per_page || 25`). The v3
        # stats calls send `limit`, not per_page, so they cost ONE credit — this charged them 4
        # (limit=100), which is where the 1s, 2s and last ~59s waits of 2026-09-28 came from.
        path = urlparse(url).path
        if _RETIRED_PATH.match(path):
            raise RetiredPath(f"NearBlocks {path.split('/')[1]} path {path} is retired (v1 sunset 2026-10-31) — "
                              f"use its /v3 equivalent (NearBlocks apps/api/docs/v3-migration.md); not called")
        credits = credits_for(int(params.get("per_page") or ROWS_PER_CREDIT))
        waited = self.pacer.wait(credits)
        if waited:
            log.info("nearblocks: paced %.0fs before a %d-credit call to %s (free plan: %d "
                     "credits/min)", waited, credits, url.split("?")[0], CREDITS_PER_MINUTE)
        try:
            body = self.http.get(url, params=params, headers={"Authorization": f"Bearer {key}"})
        except Exception as e:  # noqa: BLE001 — re-raised; a 410 or a sunset message is noted first
            status = getattr(e, "status", None)
            if status == 410 or _SUNSET_WORDS.search(str(e)):
                self._sunset(path, f"HTTP {status or '?'}: {str(e).replace(key, '***')[:160]}")
            raise
        finally:
            self.pacer.charge(credits)
        hdrs = {k.lower(): v for k, v in (getattr(self.http, "last_headers", None) or {}).items()}
        seen = {h: hdrs[h] for h in _SUNSET_HEADERS if h in hdrs}
        msg = body.get("message") if isinstance(body, dict) and isinstance(body.get("message"), str) else ""
        if seen or (msg and _SUNSET_WORDS.search(msg)):
            self._sunset(path, f"headers {seen}" if seen else f"message {msg[:160]!r}")
        return body

    def _sunset(self, path: str, what: str) -> None:
        """Record a deprecation / sunset answer once per path (Part E4)."""
        if path not in self.sunsets:
            self.sunsets[path] = what
            log.warning("ACTION NEEDED: %s — %s answered %s. Every NearBlocks path is /v3 since 2026-10-13; read "
                        "NearBlocks' v3-migration.md for this path's replacement.", SUNSET_MARKER, path, what)

    @staticmethod
    def _key(spec: dict) -> str:
        return os.environ.get(spec["key_env"], "").strip()

    def _scrub(self, spec: dict, text) -> str:
        s, k = str(text), self._key(spec)
        return s.replace(k, "***") if k else s

    def run(self, projects: list[dict], window_days, out):
        # THE DAILY STATS FIRST, THEN THE BUYBACK READ (reversed 2026-09-28). The buyback went
        # first while the pacer overcharged the stats 4 credits each; now they cost 1, run once a
        # day, and are done in seconds. With the buyback first, a first read that ran to the
        # budget left the stats never called, and tx_count / active_addresses fell through to
        # "no sources.yaml entry" (run 20260928T150426Z). The buyback read resumes across runs,
        # so going second costs it nothing.
        for p in projects:
            spec = p.get("nearblocks")
            if spec:
                self._project(p["name"], spec, window_days, out)
                if spec.get("balance_history"):
                    self._balance_history(p["name"], spec, spec["balance_history"], out)
                if spec.get("foundation_history"):
                    self._balance_history(p["name"], spec, spec["foundation_history"], out)
                if spec.get("validators_info"):
                    self._validators_info(p["name"], spec, spec["validators_info"], out)
                if spec.get("supply_stats"):
                    self._supply_stats(p["name"], spec, spec["supply_stats"], out)
                if spec.get("supply_total"):
                    self._supply_total(p["name"], spec, spec["supply_total"], out)
                if spec.get("payer_out"):
                    self._payer_out(p["name"], spec, spec["payer_out"], out)
        for p in projects:
            for flow in p.get("near_account_flows") or []:
                self._account_flow(p["name"], flow, window_days, out)
        # A DEPRECATION / SUNSET ANSWER IS A FAILED LINE ON THE RUN LOG (Part E4), named, once per path
        who = next((p["name"] for p in projects if "nearblocks" in p or p.get("near_account_flows")), None)
        for path, what in self.sunsets.items():
            if who:
                out.fail(SOURCE, who, f"{SUNSET_MARKER}: {path} answered {what} — move it to its v3 replacement", TIER)

    def _project(self, name: str, spec: dict, window_days, out):
        key = self._key(spec)
        if not key:
            for metric in spec["metrics"]:
                out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
                out.gap(name, metric, reason=f"NearBlocks needs an API key and {spec['key_env']} "
                                             f"is not set", tiers_attempted="1",
                        suggestion=f"Set {spec['key_env']} in .env (see .env.example).")
            return
        from .scrape import robots_verdict

        yesterday = today() - pd.Timedelta(days=1)
        bodies: dict = {}             # ONE CALL PER PATH: txn-stats serves txns AND txn_fee (2026-10-06)
        for metric, m in spec["metrics"].items():
            url = spec["base_url"].rstrip("/") + m["path"]
            # ONCE A DAY (2026-09-28). txn-stats and address-stats are DAILY aggregates and today
            # is never stored, so once yesterday is in the store there is nothing newer to get.
            last = self.last_dates.get((name, metric))
            if last is not None and pd.Timestamp(last).normalize() >= yesterday:
                out.mark_current(SOURCE, name, metric,
                                 f"{metric}: NOT re-fetched — yesterday ({yesterday.date()}) is "
                                 f"already stored and {m['path']} is a daily aggregate; the next "
                                 f"complete day arrives tomorrow.", TIER)
                continue
            if m["path"] in bodies:
                self._store(name, metric, m, bodies[m["path"]], window_days, out)
                continue
            allowed, why = robots_verdict(url)
            if not allowed:
                out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
                out.gap(name, metric, reason=f"robots.txt disallows {url} — {why}",
                        tiers_attempted="1", suggestion="Not worked around. Manual entry, or another source.")
                continue
            try:
                body = self._get(url, {"limit": int(spec["limit"])}, key)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                msg = self._scrub(spec, e)
                out.fail(SOURCE, name, f"{metric}: {m['path']}: {msg}", TIER)
                out.gap(name, metric, reason=f"NearBlocks {m['path']} did not answer: {msg}",
                        tiers_attempted="1", suggestion="Read the status above; a 401 is the key.")
                continue
            bodies[m["path"]] = body
            self._store(name, metric, m, body, window_days, out)

    def _validators_info(self, name: str, spec: dict, vi: dict, out):
        """THE NETWORK'S CURRENT TOTAL STAKE (Jake's run 2026-10-08 11:27) — /v3/validators/info, one call a day:
        data.total_stake in yoctoNEAR (the sum of current_epoch_stake, Nearblocks/nearblocks @e9e74695). Stored for
        today; a second run the same day does not call again."""
        metric, key = vi["metric"], self._key(spec)
        if not key:
            out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
            return
        last = self.last_dates.get((name, metric))
        if last is not None and pd.Timestamp(last).normalize() >= today().normalize():
            out.mark_current(SOURCE, name, metric, f"{metric}: NOT re-fetched — today's total_stake is stored", TIER)
            return
        from .scrape import robots_verdict
        url = spec["base_url"].rstrip("/") + vi["path"]
        allowed, why = robots_verdict(url)
        if not allowed:
            out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
            return
        try:
            body = self._get(url, {}, key)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{metric}: {vi['path']}: {self._scrub(spec, e)}", TIER)
            out.gap(name, metric, reason=f"NearBlocks {vi['path']} did not answer: {self._scrub(spec, e)}",
                    tiers_attempted="1", suggestion="Read the status above; a 401 is the key.")
            return
        data = body.get("data") if isinstance(body, dict) else None
        raw = data.get(vi["field"]) if isinstance(data, dict) else None
        try:
            value = int(str(raw)) / 10 ** int(vi.get("yocto_exponent", 24))
        except (TypeError, ValueError):
            shape = sorted(data)[:12] if isinstance(data, dict) else type(body).__name__
            out.fail(SOURCE, name, f"{metric}: {vi['path']} carried {vi['field']}={raw!r} (keys {shape}) — "
                                   f"NOTHING STORED", TIER)
            return
        out.add(tidy([(today(), value)], name, metric, f"{SOURCE}:validators/info", TIER), SOURCE, name,
                f"{metric}={value:,.0f} NEAR (total_stake {raw} yocto)", TIER)

    def _supply_stats(self, name: str, spec: dict, ss: dict, out):
        """NEARBLOCKS' NON-CIRCULATING NEAR (Jake 2026-10-11, C: the cross-check for the still-unvested lockup leg) —
        /v3/stats, one call a day: total_supply − circulating_supply, both yoctoNEAR. Today only; a second run the same
        day does not call again. v1 /v1/stats until 2026-10-13: v3 reads the SAME single row of NearBlocks' `stats`
        table (apps/api/src/services/stats.ts `SELECT * FROM stats LIMIT 1` vs sql/queries/stats/stats.sql, @08a8c850)
        — one basis, only the envelope changes ({"stats": [{…}]} -> {"data": {…}})."""
        metric, key = ss["metric"], self._key(spec)
        if not key:
            out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
            return
        last = self.last_dates.get((name, metric))
        if last is not None and pd.Timestamp(last).normalize() >= today().normalize():
            out.mark_current(SOURCE, name, metric, f"{metric}: NOT re-fetched — today's figure is stored", TIER)
            return
        from .scrape import robots_verdict
        url = spec["base_url"].rstrip("/") + ss["path"]
        allowed, why = robots_verdict(url)
        if not allowed:
            out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
            return
        try:
            body = self._get(url, {}, key)
            st = body.get("data") if isinstance(body, dict) else None
            if not isinstance(st, dict):
                raise ValueError(f"no `data` object in the v3 answer (top level {sorted(body)[:8] if isinstance(body, dict) else type(body).__name__})")
            scale = 10 ** int(ss.get("yocto_exponent", 24))
            total, circ = int(str(st["total_supply"])) / scale, int(str(st["circulating_supply"])) / scale
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{metric}: {ss['path']}: {self._scrub(spec, e)} — NOTHING STORED", TIER)
            return
        # v3 REPORTS CIRCULATING == TOTAL (fix round 2, N1): no non-circulating figure — never stored as a 0
        if abs(total - circ) < 1.0:
            out.fail(SOURCE, name, f"{metric}: {ss['path']} answered circulating_supply == total_supply "
                                   f"({total:,.0f}) — NearBlocks v3 does not net anything out; NOTHING STORED", TIER)
            return
        out.add(tidy([(today(), total - circ)], name, metric, f"{SOURCE}:stats", TIER), SOURCE, name,
                f"{metric}={total - circ:,.0f} NEAR (NearBlocks total_supply {total:,.0f} − circulating_supply "
                f"{circ:,.0f})", TIER)

    def _supply_total(self, name: str, spec: dict, ss: dict, out):
        """NEAR'S TOTAL SUPPLY AS THE CHAIN'S INDEXER HOLDS IT (G17-1a) — /v3/stats `total_supply` (yoctoNEAR), the
        net-supply cross-check sources.yaml's disabled total_supply_dashboard entry wanted. One call a day, today only;
        the same robots check and key as every NearBlocks read. Its circulating figure is not used (v3 reports
        circulating == total; the non-circulating route stays retired)."""
        metric, key = ss["metric"], self._key(spec)
        if not key:
            out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
            return
        last = self.last_dates.get((name, metric))
        if last is not None and pd.Timestamp(last).normalize() >= today().normalize():
            out.mark_current(SOURCE, name, metric, f"{metric}: NOT re-fetched — today's figure is stored", TIER)
            return
        from .scrape import robots_verdict
        url = spec["base_url"].rstrip("/") + ss["path"]
        allowed, why = robots_verdict(url)
        if not allowed:
            out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
            return
        try:
            body = self._get(url, {}, key)
            st = body.get("data") if isinstance(body, dict) else None
            if not isinstance(st, dict):
                raise ValueError(f"no `data` object in the v3 answer (top level {sorted(body)[:8] if isinstance(body, dict) else type(body).__name__})")
            total = int(str(st["total_supply"])) / 10 ** int(ss.get("yocto_exponent", 24))
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{metric}: {ss['path']}: {self._scrub(spec, e)} — NOTHING STORED", TIER)
            return
        if total <= 0:
            out.fail(SOURCE, name, f"{metric}: {ss['path']} answered total_supply {total!r} — NOTHING STORED", TIER)
            return
        out.add(tidy([(today(), total)], name, metric, f"{SOURCE}:stats.total_supply", TIER), SOURCE, name,
                f"{metric}={total:,.0f} NEAR (NearBlocks /v3/stats total_supply) — the cross-check on total_supply",
                TIER)

    def _balance_history(self, name: str, spec: dict, bh: dict, out):
        """THE THREE WALLETS' COMBINED DAILY CLOSE (Jake, 2026-10-07 14:17) — one stats/balance call per account.
        Each account's close is carried forward over days it did not move; a day is stored only from the first day
        every account has a balance, so the sum is never a part of the set. Today is not stored (still moving)."""
        metric, key = bh["metric"], self._key(spec)
        if not key:
            out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
            return
        from .scrape import robots_verdict
        scale = 10 ** int(bh.get("yocto_exponent", 24))
        per: dict = {}
        for acct in bh["accounts"]:
            url = spec["base_url"].rstrip("/") + bh["path"].format(account=acct)
            allowed, why = robots_verdict(url)
            if not allowed:
                out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}; NOTHING STORED", TIER)
                return
            try:
                body = self._get(url, {"limit": min(int(bh.get("limit", STATS_BALANCE_MAX_ROWS)),
                                                    STATS_BALANCE_MAX_ROWS)}, key)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{metric}: {acct}: {self._scrub(spec, e)} — NOTHING STORED (all-or-nothing)", TIER)
                return
            rows = body.get("data") if isinstance(body, dict) else None
            if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) and "date" in r and "amount" in r
                                                                 for r in rows[:1]):
                shape = sorted(rows[0]) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else \
                    (sorted(body)[:10] if isinstance(body, dict) else type(body).__name__)
                out.fail(SOURCE, name, f"{metric}: {acct}: stats/balance rows carry {shape}, not (date, amount) — "
                                       f"NOTHING STORED", TIER)
                return
            sr = {}
            fields = tuple(bh.get("fields") or ("amount",))
            for r in rows:
                try:
                    sr[pd.Timestamp(r["date"]).normalize()] = sum(int(r.get(f) or 0) for f in fields) / scale
                except (KeyError, TypeError, ValueError):
                    continue
            per[acct] = pd.Series(sr, dtype=float).sort_index()
        start = max(s_.index.min() for s_ in per.values())
        end = today().normalize() - pd.Timedelta(days=1)
        # A HISTORY BACKFILL STOPS BEFORE THE LIVE READ (fix round 2, N2: lockup.near + contributors.near are read
        # live by near_rpc view_account from `store_before`; the store keeps one row a day, so the history never
        # overwrites a live reading)
        if bh.get("store_before"):
            end = min(end, pd.Timestamp(bh["store_before"]) - pd.Timedelta(days=1))
        if start > end:
            out.fail(SOURCE, name, f"{metric}: no complete day on which all {len(per)} wallets have a balance", TIER)
            return
        days = pd.date_range(start, end, freq="D")
        total = sum(s_.reindex(s_.index.union(days)).ffill().reindex(days) for s_ in per.values())
        pairs = [(d, float(v)) for d, v in total.items() if v == v]
        out.add(tidy(pairs, name, metric, f"{SOURCE}:stats/balance[{'+'.join(per)}]", TIER), SOURCE, name,
                f"{metric}: {len(pairs)} day(s) {pairs[0][0]:%Y-%m-%d}..{pairs[-1][0]:%Y-%m-%d}, latest "
                f"{pairs[-1][1]:,.1f} NEAR = " + " + ".join(f"{a.split('.')[0]} {s_.iloc[-1]:,.1f}"
                                                            for a, s_ in per.items())
                + f"; {bh.get('flow', 'the release')} is its daily change (read time). {bh.get('partial', '')}", TIER)

    def _store(self, name: str, metric: str, m: dict, body, window_days, out):
        field = m["field"]
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list) or not rows:
            shape = sorted(body)[:12] if isinstance(body, dict) else type(body).__name__
            out.fail(SOURCE, name, f"{metric}: {m['path']} returned no `data` rows — top level "
                                   f"{shape}. NOTHING STORED.", TIER)
            out.gap(name, metric, reason=f"NearBlocks {m['path']} answered without a `data` list "
                                         f"(top level: {shape}); the source-read shape does not "
                                         f"hold live", tiers_attempted="1",
                    suggestion="Read the keys above and correct `nearblocks` in config.py.")
            return
        first = rows[0]
        if not isinstance(first, dict) or "date" not in first or field not in first:
            keys = sorted(first) if isinstance(first, dict) else type(first).__name__
            out.fail(SOURCE, name, f"{metric}: {m['path']} rows carry {keys}, not "
                                   f"('date', {field!r}). NOTHING STORED.", TIER)
            out.gap(name, metric, reason=f"NearBlocks {m['path']} rows carry {keys}; config expects "
                                         f"'date' and {field!r} (read from NearBlocks' SQL). The "
                                         f"live shape differs, so nothing was stored",
                    tiers_attempted="1", suggestion="Correct the field name in config.py from the keys above.")
            return
        cutoff = today()
        pairs, unusable = [], 0
        for r in rows:
            try:
                d, v = pd.Timestamp(r["date"]).normalize(), r.get(field)
                if v is None:
                    raise ValueError("null")
                v = float(v)
            except (KeyError, TypeError, ValueError):
                unusable += 1
                continue
            if d < cutoff:
                pairs.append((d, v))
        if unusable:
            out.skipped(SOURCE, name, f"{metric}: {unusable} of {len(rows)} row(s) had no usable "
                                      f"date/{field}; the other {len(pairs)} are stored.", TIER)
        if not pairs:
            out.fail(SOURCE, name, f"{metric}: no complete day in {len(rows)} row(s)", TIER)
            return
        frame = window(tidy(sorted(pairs), name, metric, SOURCE, TIER), window_days)
        sample = {k: str(first[k])[:24] for k in list(first)[:6]}
        out.add(frame, SOURCE, name,
                f"{metric} = NearBlocks {m['path']} `{field}`, {len(pairs)} complete day(s) "
                f"{min(pairs)[0].date()}..{max(pairs)[0].date()}; live row shape {sample}", TIER)

    def _payer_out(self, name: str, spec: dict, po: dict, out):
        """D2-4f, THE FOUR-STEP RULE AS A READ: every receipt the payer (HSP-007's treasury.near) has SENT since `from`,
        each receiver classified — an MPC signer operator (the contract's own participant list) counts in `metric`, any
        other receiver is named in the Run Log line, never dropped. NEAR's v3 /accounts/{account}/receipts (newest
        first, `next` cursors); a page cap reached before `from` stores nothing (a partial scan would read low)."""
        metric, account = po["metric"], po["account"]
        key = self._key(spec)
        if not key:
            out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
            return
        try:
            ops = signer_participants(self.http, po["recipients"])
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{metric}: the operator list ({po['recipients']['contract']}) did not read: "
                                   f"{self._scrub(spec, e)} — NOTHING STORED", TIER)
            return
        url = spec["base_url"].rstrip("/") + po["path"].format(account=account)
        since = pd.Timestamp(po["from"])
        rows, nxt, complete = [], None, False
        for _page in range(self.page_cap):
            params = {"limit": 100}
            if nxt:
                params["next"] = nxt
            try:
                body = self._get(url, params, key)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{metric}: {url}: {self._scrub(spec, e)} — NOTHING STORED", TIER)
                return
            data = body.get("data") if isinstance(body, dict) else None
            need = {"predecessor_account_id", "receiver_account_id", "actions_agg", "block", "outcome"}
            if not isinstance(data, list) or (data and not need.issubset(data[0])):
                got = sorted(data[0]) if isinstance(data, list) and data else type(body).__name__
                out.fail(SOURCE, name, f"{metric}: {url} answered {got}, not rows carrying {sorted(need)} — "
                                       f"NOTHING STORED", TIER)
                return
            rows += data
            nxt = (body.get("meta") or {}).get("next_page")
            oldest = min((int(r["block"]["block_timestamp"]) for r in data), default=None)
            if not nxt or oldest is None or pd.Timestamp(oldest, unit="ns") < since:
                complete = True
                break
        if not complete:
            out.fail(SOURCE, name, f"{metric}: {self.page_cap} pages of {account}'s receipts did not reach "
                                   f"{since.date()} — NOTHING STORED (a partial scan reads low)", TIER)
            return
        scale = 10 ** int(po.get("yocto_exponent", 24))
        to_ops, others = {}, {}
        for r in rows:
            dep = int((r.get("actions_agg") or {}).get("deposit") or 0)
            ts = pd.Timestamp(int(r["block"]["block_timestamp"]), unit="ns")
            if r["predecessor_account_id"] != account or dep <= 0 or ts < since \
                    or not (r.get("outcome") or {}).get("status"):
                continue
            who = r["receiver_account_id"]
            if who in ops:
                d = ts.normalize()
                to_ops[d] = to_ops.get(d, 0.0) + dep / scale
            else:
                others[who] = others.get(who, 0.0) + dep / scale
        last = today() - pd.Timedelta(days=1)                       # complete days only
        pts = [(d, to_ops.get(d, 0.0)) for d in pd.date_range(since, last)]
        named = "; ".join(f"{w[:24]} {v:,.0f}" for w, v in sorted(others.items(), key=lambda x: -x[1]))
        out.add(tidy(pts, name, metric, SOURCE, TIER), SOURCE, name,
                f"{metric}: {sum(to_ops.values()):,.2f} NEAR from {account} to the {len(ops)} signer operators "
                f"{since.date()}..{last.date()}; to other receivers (named, not counted): {named or 'none'}", TIER)

    def _account_flow(self, name: str, spec: dict, window_days, out):
        """near_account_flows — v3 account txns, ACTUAL INFLOW (see the module docstring)."""
        key = self._key(spec)
        metric = spec["metric"]
        if not key:
            out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
            out.gap(name, metric, reason=f"NearBlocks needs an API key and {spec['key_env']} "
                                         f"is not set", tiers_attempted="1",
                    suggestion=f"Set {spec['key_env']} in .env (see .env.example).")
            return
        from .scrape import robots_verdict

        account = spec["account"]
        url = spec["base_url"].rstrip("/") + V3_ACCOUNT_TXNS.format(account=account)
        allowed, why = robots_verdict(url)
        if not allowed:
            out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
            out.gap(name, metric, reason=f"robots.txt disallows {url} — {why}",
                    tiers_attempted="1", suggestion="Not worked around. Manual entry, or another source.")
            return

        action = spec.get("action", "TRANSFER")
        exclude = {s.lower() for s in spec.get("exclude_senders") or []}
        scale = 10 ** int(spec.get("yocto_exponent", 24))
        after = (today() - pd.Timedelta(days=int(window_days or 35))).strftime("%Y-%m-%d")
        before = (today() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")   # today is not complete
        # v3 has no after-date filter and its `before_ts` is in nanoseconds: rows strictly before today's 00:00 UTC
        # (today is not complete); the read stops at the first row older than `after` (newest-first, the default)
        after_ns = int(pd.Timestamp(after).value)

        # ===== INCREMENTAL, NEWEST FIRST. 2026-09-28. =====
        # Run of 2026-09-28: this paged the wallet's whole window oldest-first every run — 7 calls
        # at 2 credits, 6 minutes at the free plan's pace. Now: order=desc, and paging stops at
        # the first transaction already seen (the newest one, persisted with the per-day totals
        # in .cache/logscan/). A routine run makes ONE call. The totals are kept so the window
        # is re-emitted every run without re-fetching it. The kept totals only change when a
        # read reaches what was already seen or the end of the range; until then progress is
        # held separately as `pending` (below), so a partial read never passes for a complete one.
        state_f = LogCache().root / f"nearblocks-{account}.json"
        state = _load_state(state_f, after)
        # ===== A WIDER WINDOW THAN THE KEPT HISTORY RE-READS FROM THE NEWEST. 2026-09-28. =====
        # The first seed kept 28 days (the routine 30-day window); a read only ever looks back to
        # the newest transaction already seen, so the kept history could never grow backwards.
        # When a read asks from further back than the state reaches (the seed asks for 365 days)
        # and nothing is pending, the state starts over and the whole window is read once.
        if (not state.get("pending") and state.get("newest_ts") is not None
                and str(state.get("after") or after) > after):
            log.info("%s/%s: WINDOW EXTENDED — kept history starts %s, this read asks from %s; "
                     "re-reading the window from the newest transaction", name, metric,
                     state.get("after"), after)
            state = {"newest_ts": None, "newest_ids": [], "by_day": {}, "after": after}
        newest_ts, seen_ids = state["newest_ts"], set(state["newest_ids"])
        by_day: dict = {pd.Timestamp(d): v for d, v in state["by_day"].items()}
        new_by_day: dict = {}
        top_ts, top_ids = newest_ts, set(seen_ids)
        cursor, total_rows, excluded_hits, sample, calls = None, 0, 0, None, 0
        mixed_skipped = 0
        complete, prev_ids = False, None
        # ===== A READ THAT OUTLASTS ONE RUN RESUMES. 2026-09-28. =====
        # Run 20260928T142424Z: the first newest-first read of the window ran past the 240s budget
        # and, since state was only saved on completion, nothing persisted and every run started
        # over. Now each page's progress is saved as `pending` — the cursor, the newest
        # transaction this read began from, the partial per-day totals and a running count — and
        # the next run carries on from that cursor with the same date bounds. Only a COMPLETE read
        # folds `pending` into the kept totals.
        pend = state.get("pending") or None
        # ===== A NARROWER PARTIAL READ NEVER CAPS A WIDER ONE. 2026-09-29 (Jake: "asked twice"). =====
        # The seed kept stopping at 2026-08-30: a routine run (after = today - 30) hit its 20-page
        # cap and saved `pending` with after=2026-08-30; the seed then RESUMED that pending read —
        # resuming took precedence over the wider window it asked for — finished the 30 days, and
        # stored them as the whole history. A pending read that starts later than this read asks
        # is discarded, and the whole requested window is read from the newest transaction.
        # A v1 PARTIAL READ IS NOT RESUMED ON v3 (2026-10-13): its cursor was a raw v1 value, meaningless to v3
        if pend and pend.get("api") != "v3":
            log.info("%s/%s: DISCARDING a partial read saved under NearBlocks v1 — re-reading on v3", name, metric)
            state["pending"], pend = None, None
        if pend and str(pend.get("after") or "") > after:
            log.info("%s/%s: DISCARDING a partial read from %s — this read asks from %s; re-reading "
                     "the whole window from the newest transaction", name, metric, pend.get("after"), after)
            state = {"newest_ts": None, "newest_ids": [], "by_day": {}, "after": after}
            newest_ts, seen_ids, by_day = None, set(), {}
            top_ts, top_ids = None, set()
            pend = None
        if pend:
            cursor, after, before = pend["cursor"], pend["after"], pend["before"]
            top_ts, top_ids = pend["top_ts"], set(pend["top_ids"])
            new_by_day = {pd.Timestamp(d): v for d, v in pend["by_day"].items()}
            total_rows, excluded_hits = int(pend.get("rows", 0)), int(pend.get("excluded", 0))
            log.info("%s/%s: resuming a partial read at cursor %s (%d txn(s) read so far)",
                     name, metric, cursor, total_rows)
        # PROGRESS (Jake, 2026-10-01): every page is already saved as `pending`; the line says so.
        prog = Progress(f"nearblocks {name}/{metric} {account}", unit="pages", every_units=100,
                        status=lambda: f"{total_rows:,} txn(s) read; {len(new_by_day)} day(s) with inflows; "
                                       f"cursor saved after each page")
        for _page in range(self.page_cap):
            prog.tick()
            # v3 (NearBlocks v3-migration.md): `limit` + opaque `next` cursors, `receiver` replaces `to`, no
            # server-side action filter (kept client-side below), newest first
            params = {"receiver": account, "limit": FLOW_PER_PAGE,
                      "before_ts": str(int((pd.Timestamp(before) + pd.Timedelta(days=1)).value))}
            if cursor:
                params["next"] = cursor
            try:
                calls += 1
                body = self._get(url, params, key)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                msg = self._scrub(spec, e)
                out.fail(SOURCE, name, f"{metric}: {url}: {msg}", TIER)
                out.gap(name, metric, reason=f"NearBlocks {url} did not answer: {msg}",
                        tiers_attempted="1", suggestion="Read the status above; a 401 is the key.")
                return
            rows = body.get("data") if isinstance(body, dict) else None
            if not isinstance(rows, list):
                shape = sorted(body)[:12] if isinstance(body, dict) else type(body).__name__
                out.fail(SOURCE, name, f"{metric}: {url} carried no `data` list — top level "
                                       f"{shape}. NOTHING STORED.", TIER)
                out.gap(name, metric, reason=f"NearBlocks {url} answered without a `data` list "
                                             f"(top level: {shape}); the source-read shape does "
                                             f"not hold live", tiers_attempted="1",
                        suggestion="Read the keys above and correct `near_account_flows` in config.py.")
                return
            if rows and sample is None:
                first = rows[0]
                required = {"signer_account_id", "block_timestamp", "actions", "actions_agg"}
                if not isinstance(first, dict) or not required.issubset(first):
                    keys = sorted(first) if isinstance(first, dict) else type(first).__name__
                    out.fail(SOURCE, name, f"{metric}: {url} rows carry {keys}, not "
                                           f"{sorted(required)}. NOTHING STORED.", TIER)
                    out.gap(name, metric, reason=f"NearBlocks {url} rows carry {keys}; config "
                                                 f"expects {sorted(required)} (read from "
                                                 f"NearBlocks' SQL). The live shape differs, so "
                                                 f"nothing was stored", tiers_attempted="1",
                            suggestion="Correct the field names in config.py from the keys above.")
                    return
                sample = {k: str(first[k])[:24] for k in list(first)[:6]}
            # PROGRESS, EVERY PAGE (2026-09-28): the cursor, the running count and the page's time
            # span, so a read that advances is visibly different from one that repeats.
            page_ids = {json.dumps(r, sort_keys=True, default=str) for r in rows}
            if rows and page_ids == prev_ids:
                out.fail(SOURCE, name, f"{metric}: page {calls} repeated the previous page at "
                                       f"cursor {params.get('next')} — the cursor is not "
                                       f"advancing. Partial read kept; NOT stored.", TIER)
                return
            prev_ids = page_ids
            stamps = [int(r["block_timestamp"]) for r in rows
                      if str(r.get("block_timestamp") or "").isdigit()]
            span = (f"{pd.Timestamp(max(stamps), unit='ns').date()}..."
                    f"{pd.Timestamp(min(stamps), unit='ns').date()}" if stamps else "empty")
            log.info("%s/%s: page %d at cursor %s — %d row(s) %s; %d txn(s) read so far",
                     name, metric, calls, (params.get("next") or "(start)")[:16], len(rows), span,
                     total_rows + len(rows))
            reached_seen = False
            for r in rows:
                try:
                    ts = int(r.get("block_timestamp"))
                except (TypeError, ValueError):
                    continue
                tid = str(r.get("transaction_hash") or r.get("id") or "")
                if newest_ts is not None and (ts < newest_ts or (ts == newest_ts and tid in seen_ids)):
                    reached_seen = True
                    break
                if ts < after_ns:                  # older than the window: the newest-first read is done
                    reached_seen = True
                    break
                if top_ts is None or ts > top_ts:
                    top_ts, top_ids = ts, {tid}
                elif ts == top_ts:
                    top_ids.add(tid)
                total_rows += 1
                sender = str(r.get("signer_account_id") or r.get("predecessor_account_id") or "").lower()
                if sender in exclude:
                    excluded_hits += 1
                    continue
                if str(r.get("receiver_account_id") or account).lower() != account.lower():
                    continue
                day = pd.Timestamp(ts, unit="ns").normalize()
                # v3 LIST ROWS CARRY ONE AGGREGATED DEPOSIT (actions_agg.deposit), NOT ONE PER ACTION: a transaction is
                # counted only when EVERY action is the configured kind (a native TRANSFER), so a FUNCTION_CALL's
                # attached deposit is never read as a transfer. A mixed transaction is left out and counted in the log
                # line — under v1 its TRANSFER action's own deposit was summed (the basis note for the v3 switch).
                kinds = [a.get("action") for a in r.get("actions") or [] if isinstance(a, dict)]
                if not kinds or any(k != action for k in kinds):
                    if action in kinds:
                        mixed_skipped += 1
                    continue
                try:
                    deposit = float((r.get("actions_agg") or {}).get("deposit") or 0) / scale
                except (TypeError, ValueError):
                    deposit = 0.0
                if deposit:
                    new_by_day[day] = new_by_day.get(day, 0.0) + deposit
            cursor = (body.get("meta") or {}).get("next_page") if isinstance(body, dict) else None
            if reached_seen or not cursor or not rows:
                complete = True
                break
            state["pending"] = {"api": "v3", "cursor": cursor, "after": after, "before": before,
                                "top_ts": top_ts, "top_ids": sorted(top_ids),
                                "by_day": {str(d.date()): v for d, v in new_by_day.items()},
                                "rows": total_rows, "excluded": excluded_hits}
            _save_raw(state_f, state)
        else:
            out.skipped(SOURCE, name, f"{metric}: stopped at the {self.page_cap}-page cap with "
                                      f"more data available (cursor still set) — saved as a "
                                      f"partial read; the next run resumes at that cursor.", TIER)
        if not complete:
            # the partial read is saved as `pending`; nothing is stored until it completes
            out.skipped(SOURCE, name, f"{metric}: partial read saved ({total_rows} txn(s) so far, "
                                      f"cursor {cursor}); the next run resumes there", TIER)
            return
        for d, v in new_by_day.items():
            by_day[d] = by_day.get(d, 0.0) + v
        _save_state(state_f, after if not state["by_day"] and state["newest_ts"] is None
                    else state["after"], top_ts, top_ids, by_day)
        log.info("%s/%s: %d call(s), %d new txn(s) (%s)", name, metric, calls, total_rows,
                 "incremental — stopped at the newest already seen" if newest_ts is not None
                 else "first read of the window, newest first")

        if not by_day:
            out.skipped(SOURCE, name, f"{metric}: {total_rows} {action} txn(s) seen, "
                                      f"{excluded_hits} excluded as internal hops, none left "
                                      f"after exclusion — 0 stored for {after}..{before}.", TIER)
            return
        # A DISTINCT SOURCE FOR THE v3 BASIS (Part E2, 2026-10-13): v3 list rows give one aggregated deposit, so a mixed-
        # action transaction is left out where v1 summed its TRANSFER action — a stored series that mixes v1 rows
        # ("nearblocks") with these reads as a change of measuring point (SERIES BREAK), never silently
        frame = window(tidy(sorted(by_day.items()), name, metric, FLOW_SOURCE_V3, TIER), window_days)
        # the stored window can be older than today's window start after a long gap between runs
        frame = frame[frame["date"] >= pd.Timestamp(after)] if not frame.empty else frame
        out.add(frame, SOURCE, name,
                f"{metric} = NearBlocks {url} `actions_agg.deposit` of {action}-only txns into "
                f"{account}, excluding {sorted(exclude) or 'nothing'} as senders; "
                f"{len(by_day)} day(s) {min(by_day).date()}..{max(by_day).date()}, "
                f"{total_rows} txn(s) seen, {excluded_hits} excluded as internal hops, {mixed_skipped} mixed-action "
                f"txn(s) left out; live row "
                f"shape {sample}. NATIVE NEAR ONLY — wrap.near ft_transfer inflow not counted, "
                f"not wei-reconciled against a balance (see the module docstring).", TIER)
