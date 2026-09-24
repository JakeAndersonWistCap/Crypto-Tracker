"""
fetch/nearblocks.py — NEAR daily transactions and active accounts, from NearBlocks' API v3.

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
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from .base import Http, tidy, today, window

log = logging.getLogger("token_metrics.fetch.nearblocks")

SOURCE = "nearblocks"
TIER = 1


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
