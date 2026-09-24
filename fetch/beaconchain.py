"""
fetch/beaconchain.py — Ethereum's own consensus-layer issuance, from beaconcha.in's ETH.Store API.

    GET https://beaconcha.in/api/v1/ethstore/latest      header: apikey: <key>

Returns {"status": "OK", "data": [{...one ETH.Store daily aggregate...}]}, one record per
"beaconchain-day" (~24h since genesis, NOT aligned to UTC midnight — see day_start/day_end below).

** WHY THIS EXISTS. ** Ethereum's gross_issuance_tokens already has a route — d(total_supply_gross)
+ gross_burn_tokens (fetch/__init__._derive_issuance, rule "add_burn" for protocol_level_destruction)
— but that derivation needs gross_burn_tokens (ultrasound.money, tier 3) to answer in the SAME run,
and is a derived figure rather than a first-party one. ETH.Store publishes the network-wide reward
sum directly, so it is a measured route rather than a derivation, and this module registers AHEAD
of the derivation in TIER_ORDER (tier 1 vs the derivation's post-tier-4 pass): on any day it answers,
_derive_issuance sees (project, "gross_issuance_tokens") already in this run's frame and stands
down. The derivation remains the fallback for any day this source fails.

** THE FIELD NAMES ARE READ FROM BEACONCHA.IN'S OWN PUBLISHED OpenAPI SPEC, AND CHECKED AGAINST
EVERY LIVE ROW ANYWAY. ** beaconcha.in's API docs site itself was not fetched from the build
environment (egress-blocked, same as every RPC and explorer here); its OpenAPI spec is published
in its own backend repository and was read from there:

  gobitfly/eth2-beaconchain-explorer, static/openapi/bundled.yaml
    security: apiKey, `in: query` name `apikey` OR `in: header` name `apikey` — NOT an
              `Authorization: Bearer` header, despite a stray line of prose beside the scheme
              that says so; the machine-readable `type: apiKey` / `name: apikey` definition is
              what is followed here. Sent as a header so the key never lands in a URL that then
              appears in an exception message or a cached request log.
    GET /api/v1/ethstore/{day}   day = integer beaconchain-day index, or the keyword `latest`
    types.APIEthStoreResponse    day, day_start, day_end (RFC 3339), apr, cl_apr, el_apr,
                                 consensus_rewards_sum_wei, tx_fees_sum_wei, and their 7d/31d
                                 trailing averages — all documented as returned in WEI (a plain
                                 JSON number, not a string).

A published spec is still not a live response. So the adapter verifies the shape on every call —
the same growthepie/NearBlocks discipline — and stores nothing if the row does not carry the
keys the spec promises.

** CONSENSUS-LAYER REWARDS ONLY. THE EXECUTION-LAYER FIELD IS DELIBERATELY NOT READ. **
consensus_rewards_sum_wei is the protocol's own attestation/proposal/sync-committee reward
issuance for the day — NEW ETH, created by the protocol, the same quantity this project's
issuance_rate_observed already describes qualitatively (~2,800 ETH/day, diminishing-marginal-rate
function of total stake). tx_fees_sum_wei (and el_apr) is priority fees plus MEV tips paid BY
transaction senders TO the block proposer — existing circulating ETH changing hands, a transfer,
not issuance. Reading the combined (cl+el) APR or fee sum into gross_issuance_tokens would count
part of ordinary transaction activity as new supply. Only the cl_* fields are used.

A NEGATIVE consensus_rewards_sum_wei (aggregate slashing penalties exceeding rewards network-wide
— not observed, but not provably impossible) is rejected by validate_frame's existing
sanity_min=0 on gross_issuance_tokens; nothing bespoke is added here for it.

TODAY IS NOT STORED, and neither is a day whose own day_end has not yet elapsed — the same
discipline as every other daily adapter, applied to a period boundary that (unlike a calendar
day) beaconcha.in defines and reports itself; nothing here assumes it lines up with UTC midnight.
The row is dated to day_start, normalised to midnight by tidy() like every other daily series.

** THE KEY NEVER REACHES A LOG. ** It travels only in the `apikey` header, and every message this
module produces is scrubbed of it anyway.

** RATE LIMIT: 10 REQUESTS/MINUTE PER IP, FREE TIER. ** Confirmed from beaconcha.in's own OpenAPI
spec (gobitfly/eth2-beaconchain-explorer, static/openapi/bundled.yaml, read 2026-09-24): "The API
is free to use under a fair use policy, with rate limits of 10 requests per minute per IP." The
spec does not document a higher limit for a free-tier API key — only "higher usage plans" (paid)
get one. Production cadence here is nowhere near that ceiling: exactly ONE project declares a
`beaconchain` block (Ethereum, one metric), so `run()` makes exactly one call per scheduled run,
and this tool runs at most once a day. `Http(min_interval=1.0, retries=2)` already retries 429
with exponential backoff (fetch/base.py, honours Retry-After) for the rare case something else on
the same IP has spent the quota. The 429 Jake hit was in check_offline_items.py's manual check —
a raw requests.get with no Http wrapper and an unauthenticated baseline call immediately before
the real one — not in this production path, which was never the source of that failure.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from .base import Http, point, today

log = logging.getLogger("token_metrics.fetch.beaconchain")

SOURCE = "beaconchain"
TIER = 1


class BeaconChain:
    """Ethereum's own consensus-layer issuance, for any project declaring a `beaconchain` block."""

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
            spec = p.get("beaconchain")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out):
        key = self._key(spec)
        if not key:
            for metric in spec["metrics"]:
                out.unconfigured(SOURCE, name, f"{metric}: no {spec['key_env']} in .env", TIER)
                out.gap(name, metric, reason=f"beaconcha.in needs an API key and {spec['key_env']} "
                                             f"is not set", tiers_attempted="1",
                        suggestion=f"Set {spec['key_env']} in .env (see .env.example).")
            return
        from .scrape import robots_verdict

        # ONE CALL PER PATH, however many metrics read from it: gross_issuance_tokens and
        # staking_yield_pct both come from /ethstore/latest, and the free tier allows 10/min.
        bodies: dict[str, object] = {}
        for metric, m in spec["metrics"].items():
            url = spec["base_url"].rstrip("/") + m["path"]
            if url not in bodies:
                allowed, why = robots_verdict(url)
                if not allowed:
                    bodies[url] = ("robots", why)
                else:
                    try:
                        bodies[url] = ("ok", self.http.get(url, headers={"apikey": key}))
                    except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                        bodies[url] = ("error", self._scrub(spec, e))
            state, body = bodies[url]
            if state == "robots":
                out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {body}", TIER)
                out.gap(name, metric, reason=f"robots.txt disallows {url} — {body}",
                        tiers_attempted="1", suggestion="Not worked around. Manual entry, or another source.")
                continue
            if state == "error":
                out.fail(SOURCE, name, f"{metric}: {m['path']}: {body}", TIER)
                out.gap(name, metric, reason=f"beaconcha.in {m['path']} did not answer: {body}",
                        tiers_attempted="1", suggestion="Read the status above; a 401 is the key.")
                continue
            self._store(name, metric, m, body, out)

    def _store(self, name: str, metric: str, m: dict, body, out):
        field = m["field"]
        if not isinstance(body, dict) or body.get("status") != "OK":
            shape = sorted(body)[:12] if isinstance(body, dict) else type(body).__name__
            status = body.get("status") if isinstance(body, dict) else None
            out.fail(SOURCE, name, f"{metric}: {m['path']} did not answer status 'OK' (got "
                                   f"{status!r}) — top level {shape}. NOTHING STORED.", TIER)
            out.gap(name, metric, reason=f"beaconcha.in {m['path']} answered without status "
                                         f"'OK' (got {status!r}; top level {shape}); the "
                                         f"source-read shape does not hold live", tiers_attempted="1",
                    suggestion="Read the keys above and correct `beaconchain` in config.py.")
            return
        rows = body.get("data")
        if not isinstance(rows, list) or not rows:
            out.fail(SOURCE, name, f"{metric}: {m['path']} carried no `data` row. NOTHING STORED.", TIER)
            out.gap(name, metric, reason=f"beaconcha.in {m['path']} returned status 'OK' but an "
                                         f"empty or missing `data` list", tiers_attempted="1",
                    suggestion="Re-check the day/tag requested against beaconcha.in's own spec.")
            return
        rec = rows[0]
        if not isinstance(rec, dict) or "day_start" not in rec or "day_end" not in rec or field not in rec:
            keys = sorted(rec) if isinstance(rec, dict) else type(rec).__name__
            out.fail(SOURCE, name, f"{metric}: {m['path']} row carries {keys}, not "
                                   f"('day_start', 'day_end', {field!r}). NOTHING STORED.", TIER)
            out.gap(name, metric, reason=f"beaconcha.in {m['path']} rows carry {keys}; config "
                                         f"expects 'day_start'/'day_end' and {field!r} (read from "
                                         f"beaconcha.in's own OpenAPI spec). The live shape "
                                         f"differs, so nothing was stored", tiers_attempted="1",
                    suggestion="Correct the field name in config.py from the keys above.")
            return
        try:
            start = pd.Timestamp(rec["day_start"])
            end = pd.Timestamp(rec["day_end"])
            value = float(rec[field]) / float(m.get("scale", 1))
        except (TypeError, ValueError) as e:
            out.fail(SOURCE, name, f"{metric}: could not parse day_start/day_end/{field} from "
                                   f"{rec}: {e}. NOTHING STORED.", TIER)
            out.gap(name, metric, reason=f"beaconcha.in's row did not parse as expected: {e}",
                    tiers_attempted="1", suggestion="Inspect the raw row printed above.")
            return
        # RFC 3339 timestamps arrive tz-aware (a 'Z' or offset); normalised to naive UTC so they
        # compare against today() — itself naive UTC (fetch.base.today()) — without pandas
        # raising on a tz-aware/tz-naive comparison. A naive parse (no offset in the string) is
        # left as-is: RFC 3339 requires an offset, so that would be the source deviating from
        # its own spec, not something to silently coerce.
        if start.tzinfo is not None:
            start = start.tz_convert("UTC").tz_localize(None)
        if end.tzinfo is not None:
            end = end.tz_convert("UTC").tz_localize(None)
        now = pd.Timestamp.now("UTC").tz_localize(None)
        if end > now:
            out.skipped(SOURCE, name, f"{metric}: the latest beaconchain-day ({start.date()}..."
                                      f"{end.date()}) has not finished ({end} is still in the "
                                      f"future) — not stored as complete.", TIER)
            return
        day = start.normalize()
        if day >= today():
            out.skipped(SOURCE, name, f"{metric}: the latest beaconchain-day starts {day.date()}, "
                                      f"which is today — not stored until it is a complete prior "
                                      f"day.", TIER)
            return
        out.add(point(name, metric, value, SOURCE, TIER, day), SOURCE, name,
                f"{metric} = beaconcha.in ETH.Store `{field}` for the beaconchain-day "
                f"{day.date()} ({rec['day_start']}..{rec['day_end']}) = {value:,.4f} — "
                f"{m.get('log_note', '')}", TIER)
