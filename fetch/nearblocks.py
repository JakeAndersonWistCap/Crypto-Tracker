"""
fetch/nearblocks.py — NEAR daily transactions and active accounts, from NearBlocks' API v3.
Also: an account's inflow history via v1 (near_account_flows) — a differently-shaped read, same
module, same key.

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
near_account_flows — GET /v1/account/{account}/txns, ACTUAL INFLOW, NOT A BALANCE. Added 2026-09-24.
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

import logging
import os

import pandas as pd

from .base import Http, tidy, today, window

log = logging.getLogger("token_metrics.fetch.nearblocks")

SOURCE = "nearblocks"
TIER = 1
FLOW_PAGE_CAP = 20   # hard stop on pagination — polite, and this wallet's volume is nowhere near it


class NearBlocks:
    """Daily chain activity for any project declaring a `nearblocks` block."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)

    @staticmethod
    def _key(spec: dict) -> str:
        return os.environ.get(spec["key_env"], "").strip()

    def _scrub(self, spec: dict, text) -> str:
        s, k = str(text), self._key(spec)
        return s.replace(k, "***") if k else s

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("nearblocks")
            if spec:
                self._project(p["name"], spec, window_days, out)
            for flow in p.get("near_account_flows") or []:
                self._account_flow(p["name"], flow, window_days, out)

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

        for metric, m in spec["metrics"].items():
            url = spec["base_url"].rstrip("/") + m["path"]
            allowed, why = robots_verdict(url)
            if not allowed:
                out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
                out.gap(name, metric, reason=f"robots.txt disallows {url} — {why}",
                        tiers_attempted="1", suggestion="Not worked around. Manual entry, or another source.")
                continue
            try:
                body = self.http.get(url, params={"limit": int(spec["limit"])},
                                     headers={"Authorization": f"Bearer {key}"})
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                msg = self._scrub(spec, e)
                out.fail(SOURCE, name, f"{metric}: {m['path']}: {msg}", TIER)
                out.gap(name, metric, reason=f"NearBlocks {m['path']} did not answer: {msg}",
                        tiers_attempted="1", suggestion="Read the status above; a 401 is the key.")
                continue
            self._store(name, metric, m, body, window_days, out)

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

    def _account_flow(self, name: str, spec: dict, window_days, out):
        """near_account_flows — v1 account txns, ACTUAL INFLOW (see the module docstring)."""
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
        url = spec["base_url"].rstrip("/") + f"/v1/account/{account}/txns"
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

        by_day: dict = {}
        cursor, total_rows, excluded_hits, sample = None, 0, 0, None
        for _page in range(FLOW_PAGE_CAP):
            params = {"action": action, "after_date": after, "before_date": before,
                     "per_page": 50, "order": "asc"}
            if cursor:
                params["cursor"] = cursor
            try:
                body = self.http.get(url, params=params, headers={"Authorization": f"Bearer {key}"})
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                msg = self._scrub(spec, e)
                out.fail(SOURCE, name, f"{metric}: {url}: {msg}", TIER)
                out.gap(name, metric, reason=f"NearBlocks {url} did not answer: {msg}",
                        tiers_attempted="1", suggestion="Read the status above; a 401 is the key.")
                return
            rows = body.get("txns") if isinstance(body, dict) else None
            if rows is None:
                shape = sorted(body)[:12] if isinstance(body, dict) else type(body).__name__
                out.fail(SOURCE, name, f"{metric}: {url} carried no `txns` key — top level "
                                       f"{shape}. NOTHING STORED.", TIER)
                out.gap(name, metric, reason=f"NearBlocks {url} answered without a `txns` list "
                                             f"(top level: {shape}); the source-read shape does "
                                             f"not hold live", tiers_attempted="1",
                        suggestion="Read the keys above and correct `near_account_flows` in config.py.")
                return
            if rows and sample is None:
                first = rows[0]
                required = {"predecessor_account_id", "block_timestamp", "actions"}
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
            for r in rows:
                total_rows += 1
                sender = str(r.get("predecessor_account_id") or "").lower()
                if sender in exclude:
                    excluded_hits += 1
                    continue
                try:
                    day = pd.Timestamp(int(r.get("block_timestamp")), unit="ns").normalize()
                except (TypeError, ValueError):
                    continue
                deposit = 0.0
                for a in r.get("actions") or []:
                    if isinstance(a, dict) and a.get("action") == action:
                        try:
                            deposit += float(a.get("deposit") or 0) / scale
                        except (TypeError, ValueError):
                            continue
                if deposit:
                    by_day[day] = by_day.get(day, 0.0) + deposit
            cursor = body.get("cursor")
            if not cursor or not rows:
                break
        else:
            out.skipped(SOURCE, name, f"{metric}: stopped at the {FLOW_PAGE_CAP}-page cap with "
                                      f"more data available (cursor still set) — this run's "
                                      f"figure covers a partial window.", TIER)

        if not by_day:
            out.skipped(SOURCE, name, f"{metric}: {total_rows} {action} txn(s) seen, "
                                      f"{excluded_hits} excluded as internal hops, none left "
                                      f"after exclusion — 0 stored for {after}..{before}.", TIER)
            return
        frame = window(tidy(sorted(by_day.items()), name, metric, SOURCE, TIER), window_days)
        out.add(frame, SOURCE, name,
                f"{metric} = NearBlocks {url} `actions[].deposit` for action={action!r} into "
                f"{account}, excluding {sorted(exclude) or 'nothing'} as senders; "
                f"{len(by_day)} day(s) {min(by_day).date()}..{max(by_day).date()}, "
                f"{total_rows} txn(s) seen, {excluded_hits} excluded as internal hops; live row "
                f"shape {sample}. NATIVE NEAR ONLY — wrap.near ft_transfer inflow not counted, "
                f"not wei-reconciled against a balance (see the module docstring).", TIER)
