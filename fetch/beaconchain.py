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

import json
import logging
import os

import pandas as pd

from .base import Http, point, today

log = logging.getLogger("token_metrics.fetch.beaconchain")

SOURCE = "beaconchain"
TIER = 1


def _day_cache_file():
    from .logcache import LogCache
    return LogCache().root / "beaconchain-day.json"


def _now() -> pd.Timestamp:
    """UTC wall clock, naive. One place so a test can move it."""
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def _day_cache_read(url: str):
    """(state, body) stored THIS CALENDAR MONTH for url, else None. Failures are kept too: a
    refused call is not retried until the next month — OR, when the refusal named its own wait
    (a 429's Retry-After, kept as `retry_at`), until that wait is over, whichever comes first.

    ONE CALL A MONTH, NEVER DAILY (Jake, 2026-09-29): beaconcha.in's free quota is MONTHLY, and
    it is now only the cross-check of the all-in yield (consensus + execution INCLUDING MEV) —
    the headline's consensus part is d(Eth2Staking) from ethsupply2. The stored point is dated
    by its own beaconchain-day, so re-using the month's answer re-writes the same key.

    EACH ENDPOINT KEEPS ITS OWN WAIT (Jake's run, 2026-09-30): /ethstore/latest asked 150,198s
    (~41.7h) while the history call /ethstore/{day} asked 36,338s (~10.1h, the monthly reset).
    This cache is /ethstore/latest's; the history backfill keeps its own in beaconchain-history.json."""
    try:
        st = json.loads(_day_cache_file().read_text())
    except (OSError, ValueError):
        return None
    rec = st.get(url)
    if rec and str(rec.get("date", ""))[:7] == str(today().date())[:7]:
        state, body = rec["state"], rec["body"]
        if state != "ok" and rec.get("retry_at"):
            if _now() >= pd.Timestamp(rec["retry_at"]):
                return None                      # the wait this endpoint asked for is over
            return state, (f"{body} (cached {rec['date']}; this endpoint asked to wait until "
                           f"{rec['retry_at']} UTC — not retried before then)")
        if state != "ok":
            body = (f"{body} (the month's one attempt, {rec['date']}, cached — not retried until "
                    f"next month: the free quota is monthly)")
        return state, body
    return None


def _day_cache_write(url: str, result, retry_after: float | None = None) -> None:
    f = _day_cache_file()
    try:
        st = json.loads(f.read_text())
    except (OSError, ValueError):
        st = {}
    st[url] = {"date": str(today().date()), "state": result[0], "body": result[1]}
    if retry_after is not None:
        st[url]["retry_at"] = str((_now() + pd.Timedelta(seconds=float(retry_after))).floor("s"))
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(f)


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
                if spec.get("history") and self._key(spec):
                    self._history(p["name"], spec, out)

    # ===== ETH.STORE PER DAY, BACKFILLED UNDER THE SERVER'S OWN LIMIT (A2, Jake 2026-09-30). =====
    # /api/v1/ethstore/{day} for each beaconchain-day of the last `days` (day N starts at genesis
    # 2020-12-01T12:00:23Z + N days — gobitfly/eth.store README; the row carries day_start, which
    # is what dates it). ONE CALL PER DAY, cached for good in beaconchain-history.json: a past
    # day's aggregate does not change, so nothing is ever read twice.
    # THE CAP IS beaconcha.in's OWN COUNTER, not a guess. Every response carries x-ratelimit-*
    # headers; the backfill stops when the MONTHLY remaining falls to `reserve` (kept for the
    # monthly cross-check call) and paces to the per-minute limit it reports. Jake's probe of
    # 2026-09-30 read every counter 0 with Retry-After 36,644s — the month's window, resetting
    # 2026-10-01 00:00 UTC. The documented free plan is 30,000 calls/month, so 365 fits in one
    # run of `python ethstore_backfill.py`; inside the daily run it is capped at calls_per_run
    # to stay inside the source's time budget.
    # WHAT EACH DAY GIVES: `apr` -> staking_yield_pct, and consensus_rewards_sum_wei -> ETH minted
    # as consensus rewards that day (consensus_rewards_tokens) — the ISSUANCE HISTORY before the
    # Etherscan leg (declared handover), replacing ultrasound.money's series, which is frozen at
    # 2024-06-22 (Jake's probe, 2026-09-30).
    GENESIS_DAY0 = pd.Timestamp("2020-12-01")
    RATE_KEYS = ("x-ratelimit-remaining-month", "x-ratelimit-limit-month",
                 "x-ratelimit-remaining-minute", "x-ratelimit-limit-minute")

    @staticmethod
    def _hist_file():
        from .logcache import LogCache
        return LogCache().root / "beaconchain-history.json"

    @staticmethod
    def _header_int(headers: dict, key: str):
        for k, v in (headers or {}).items():
            if k.lower() == key:
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return None
        return None

    def _history(self, name: str, spec: dict, out, max_calls: int | None = None, http=None) -> dict:
        h = spec["history"]
        f = self._hist_file()
        try:
            st = json.loads(f.read_text())
        except (OSError, ValueError):
            st = {}
        days = st.setdefault("days", {})
        want = [today() - pd.Timedelta(days=i) for i in range(2, int(h.get("days", 365)) + 2)]
        missing = [d for d in want if str((d - self.GENESIS_DAY0).days) not in days]
        cap = int(h.get("calls_per_run", 30)) if max_calls is None else int(max_calls)
        reserve = int(h.get("reserve", 5))
        key, calls, refused, limits = self._key(spec), 0, None, {}
        http = http or Http(min_interval=float(h.get("min_interval_s", 3.1)), retries=0)
        # THIS ENDPOINT'S OWN WAIT, separate from /ethstore/latest's (_day_cache_read): the last
        # 429 on /ethstore/{day} named its Retry-After; no call is made before it has passed.
        todo = missing[:cap]
        wait_until = st.get("retry_at")
        if wait_until and _now() < pd.Timestamp(wait_until):
            refused = (f"/ethstore/{{day}} asked on {st.get('retry_asked_on')} to wait "
                       f"{float(st.get('retry_after_s', 0)):,.0f}s, until {wait_until} UTC — "
                       f"no call before then")
            todo = []
        for d in todo:
            n = (d - self.GENESIS_DAY0).days
            url = spec["base_url"].rstrip("/") + f"/api/v1/ethstore/{n}"
            try:
                body = http.get(url, headers={"apikey": key})
                calls += 1
            except Exception as e:  # noqa: BLE001 — stop at the first refusal, keep what was read
                refused = self._scrub(spec, e)
                calls += 1
                wait = getattr(e, "retry_after", None)
                if wait is not None:
                    st["retry_at"] = str((_now() + pd.Timedelta(seconds=float(wait))).floor("s"))
                    st["retry_after_s"] = float(wait)
                    st["retry_asked_on"] = str(_now().floor("s"))
                    lim = getattr(e, "headers", None)
                    if lim:
                        st["retry_headers"] = lim
                break
            rows = body.get("data") if isinstance(body, dict) and body.get("status") == "OK" else None
            rec = rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else None
            if not isinstance(rec, dict) or h["field"] not in rec or "day_start" not in rec:
                keys = sorted(rec) if isinstance(rec, dict) else type(body).__name__
                out.fail(SOURCE, name, f"{h['metric']} history: /ethstore/{n} carried {keys}, not "
                                       f"('day_start', {h['field']!r}). NOTHING STORED; backfill stopped.", TIER)
                break
            st.pop("retry_at", None)             # an answer means the wait is over
            days[str(n)] = {"day_start": str(rec["day_start"]), "value": rec[h["field"]],
                            "consensus_wei": rec.get("consensus_rewards_sum_wei")}
            hdr = getattr(http, "last_headers", None) or {}
            limits = {k: self._header_int(hdr, k) for k in self.RATE_KEYS}
            rem = limits.get("x-ratelimit-remaining-month")
            if rem is not None and rem <= reserve:
                refused = (f"the monthly quota is down to {rem} (limit "
                           f"{limits.get('x-ratelimit-limit-month')}); {reserve} kept in reserve")
                break
            per_min = limits.get("x-ratelimit-limit-minute")
            if per_min:
                http.min_interval = max(http.min_interval, 60.0 / per_min + 0.1)
        if calls or "retry_at" in st:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(f)
        from .base import tidy
        apr, cons = [], []
        for n, r in days.items():
            try:
                d = pd.Timestamp(r["day_start"])
                d = (d.tz_convert("UTC").tz_localize(None) if d.tzinfo else d).normalize()
            except (TypeError, ValueError):
                continue
            if d >= today():
                continue
            try:
                apr.append((d, float(r["value"]) / float(h.get("scale", 1))))
            except (TypeError, ValueError):
                pass
            if r.get("consensus_wei") is not None and h.get("consensus_metric"):
                try:
                    cons.append((d, float(r["consensus_wei"]) / 1e18))
                except (TypeError, ValueError):
                    pass
        still = max(0, len(missing) - calls)
        quota = (f"; beaconcha.in reports {limits.get('x-ratelimit-remaining-month')} of "
                 f"{limits.get('x-ratelimit-limit-month')} calls left this month" if limits else "")
        if apr:
            out.add(tidy(sorted(apr), name, h["metric"], SOURCE, TIER), SOURCE, name,
                    f"{h['metric']} history = ETH.Store `{h['field']}` per beaconchain-day: {len(apr)} "
                    f"day(s) held, {calls} call(s) this run, {still} day(s) still to read{quota}", TIER)
        if cons:
            out.add(tidy(sorted(cons), name, h["consensus_metric"], f"{SOURCE}:ethstore.consensus_rewards_sum_wei",
                         TIER), SOURCE, name,
                    f"{h['consensus_metric']} history = ETH.Store consensus_rewards_sum_wei / 1e18 per "
                    f"beaconchain-day: {len(cons)} day(s)", TIER)
        if refused:
            out.fail(SOURCE, name, f"{h['metric']} history: stopped after {calls} call(s) — {refused}. "
                                   f"What was read is kept; resumes next run.", TIER)
        return {"calls": calls, "still": still, "limits": limits, "retry_at": st.get("retry_at")}

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
                # AT MOST ONE CALL A MONTH (2026-09-29; was a day). Run 20260928T142424Z: the
                # MONTHLY quota was exhausted (ratelimit-window: month, remaining 0, reset
                # 207,324s — the October rollover). The month's answer — or its failure — is
                # kept and re-used; see _day_cache_read.
                cached = _day_cache_read(url)
                if cached is not None:
                    bodies[url] = tuple(cached)
                else:
                    allowed, why = robots_verdict(url)
                    if not allowed:
                        bodies[url] = ("robots", why)
                    else:
                        wait = None
                        try:
                            bodies[url] = ("ok", self.http.get(url, headers={"apikey": key}))
                        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                            bodies[url] = ("error", self._scrub(spec, e))
                            wait = getattr(e, "retry_after", None)
                        _day_cache_write(url, bodies[url], retry_after=wait)
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
