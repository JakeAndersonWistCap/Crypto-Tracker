"""
fetch/merkl_campaigns.py — tier 1: a creator's Merkl campaigns per chain, as MORPHO paid per day (fix round 3, B3; Jake
2026-10-13). Merkl's public API, no key: GET https://api.merkl.xyz/v4/campaigns?creatorAddress=…&chainId=…&items=100
&page=N (robots.txt: 404, nothing disallowed; read once a day).

WHY. Morpho's (b) read Ethereum only (MORPHO claimed out of Merkl's distributor + the URD: log_scans.merkl_morpho_out)
— 682 MORPHO/day against Merkl's ~7.5K/day for the DAO rewards multisig 0xF057afee… (independent_references.csv). The
multisig's MORPHO campaigns now run mostly OFF Ethereum: read 2026-10-13, over 13 Sep..12 Oct, Base 6,646/day.

WHERE A CAMPAIGN PAYS, NOT WHERE IT COMPUTES. The API's chainId is the COMPUTE chain; the Arc campaign (20,602 MORPHO,
23 Sep..16 Oct) and the Tempo and Unichain ones carry distributionChainId 1 — they are claimed from Ethereum's Merkl
distributor, which the Ethereum claims scan already counts. So only a campaign whose distributionChainId is the declared
chain is counted here, and each campaignId once.

WHAT A DAY IS. Merkl pays a campaign's `amount` evenly over [startTimestamp, endTimestamp): the day's MORPHO is each
live campaign's amount × (its seconds inside that UTC day) ÷ its duration. D2-3 (Jake 2026-10-26): the amount counted is
what Merkl ALLOCATED TO USERS under the campaign — its allocated total less what it credited back to the creator
(end-of-campaign leftovers, reallocations, cancellation refunds); allocated-but-unclaimed stays in. The per-day shape
stays the schedule's (Merkl publishes no per-day allocation). Not a model and not claims (claims lag and are lumpy; Ethereum keeps its claims series, so Ethereum is NOT read here — the
two would count the same MORPHO twice). Only the reward token declared for the chain is counted (address match, never
the symbol alone); a campaign paying another token, or MORPHO at an undeclared address, is listed in the log, not
counted. Complete UTC days only; the whole history is restated each run from the campaign list.
"""
from __future__ import annotations

import datetime as _dt
import logging
from collections import defaultdict

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.merkl_campaigns")

SOURCE = "merkl_campaigns"
# D4-3: the rows carry their basis — what Merkl ALLOCATED TO USERS (D2-3) — so a series still holding the earlier schedule
# basis (rows labelled plain "merkl_campaigns") is told apart (references.REFERENCE_BASIS)
ROW_SOURCE = "merkl_campaigns:allocated"
TIER = 1
PAGE = 100
DAY = 86_400
# D4-2b (Jake's run on D2, 2026-10-26: 131 calls in the 120 s budget, timed out): two reads per root campaign (~216 roots,
# ~432 calls; the first fill measured 234 s on 2026-10-27) cannot fit. A campaign that
# ENDED at least FINAL_AFTER_DAYS before a read is cached (LogCache root merkl-allocations.json) and not re-read — except
# each cached entry is re-verified every REVERIFY_DAYS + (its id mod REVERIFY_SPREAD) days, so the re-reads spread over the
# month and a late change (a reallocation) is still seen; a change found is logged. Running and recently ended campaigns
# are read every run. MEASURED 2026-10-27 (live, fill_cache): the first fill read all 216 roots in 234 s; the next pass
# found 208 cached and re-read 8 in 26 s. The reader stops itself after READ_BUDGET_S, keeps every campaign it read in
# the cache and stores NOTHING (a sum missing campaigns would understate); the next run continues — a first fill takes
# three daily runs, or one `python -m fetch.merkl_campaigns --fill`.
FINAL_AFTER_DAYS = 30
REVERIFY_DAYS, REVERIFY_SPREAD = 28, 14
READ_BUDGET_S = 100.0


class ReadIncomplete(RuntimeError):
    """The self-imposed read budget ran out before every campaign was read (the cache keeps what was read)."""


def _now() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


CANCELLED = "INVALID"                # Merkl's type for a cancelled campaign (docs: "overrides … to an invalid type")


def daily_amounts(campaigns: list[dict], token_address: str, end_day: _dt.date, chain_id: int | None = None,
                  seen: set | None = None, refunds: dict | None = None,
                  allocated: dict | None = None) -> tuple[dict, list]:
    """({date: tokens}, [skipped (campaignId, reason)]) for the campaigns paying `token_address` ON `chain_id` (their
    distributionChainId), complete days < end; a campaignId already in `seen` is skipped (and added when counted).

    D1-2 (Morpho, reconciled 2026-10-26): a SUB-CAMPAIGN (parentCampaignId set) is the engine's forwarder share of its
    parent — no new money (Merkl docs; the child id is no campaign in DistributionCreator) — and is never counted: the
    parent's amount already holds it (1.0M MORPHO double-counted 2025-10..2026-10). A CANCELLED campaign (type INVALID)
    counts its amount less what went back to its creator (`refunds` {campaignId: raw amount}, Merkl's own
    /users/{creator}/rewards/campaign/{id}); without that read it is not counted and says so.

    D2-3 (Jake 2026-10-26: the series is what Merkl ALLOCATED TO USERS): with `allocated` {campaignId: (raw tokens,
    until)} a root campaign counts what Merkl's engine allocated under it less what it credited back to its creator
    (Merkl's own /v4/rewards/total and /v4/users/{creator}/rewards/campaign/{id}), spread evenly over [start, until) —
    until = its end, or, while it runs, the engine's computedUntil (nothing is known past it). A root missing from
    `allocated` is not counted and says so. Without `allocated` the amount is the schedule (as before D2)."""
    tok = token_address.lower()
    per_day: dict = defaultdict(float)
    skipped = []
    end_ts = int(_dt.datetime(end_day.year, end_day.month, end_day.day, tzinfo=_dt.timezone.utc).timestamp())
    for c in campaigns:
        rt = c.get("rewardToken") or {}
        dist = c.get("distributionChainId")
        if chain_id is not None and dist is not None and int(dist) != int(chain_id):
            skipped.append((c.get("campaignId"), f"distributed on chain {dist}"))
            continue
        if c.get("parentCampaignId"):
            skipped.append((c.get("campaignId"), "sub-campaign (forwarder share of its parent: no new money)"))
            continue
        if seen is not None and c.get("campaignId") in seen:
            skipped.append((c.get("campaignId"), "already counted"))
            continue
        if str(rt.get("address") or "").lower() != tok:
            skipped.append((c.get("campaignId"), f"pays {rt.get('symbol')} {rt.get('address')}"))
            continue
        start, end = int(c["startTimestamp"]), int(c["endTimestamp"])
        if end <= start:
            skipped.append((c.get("campaignId"), "zero duration"))
            continue
        if seen is not None:
            seen.add(c.get("campaignId"))
        raw = int(c["amount"])
        if allocated is not None:
            got = allocated.get(c.get("campaignId"))
            if got is None:
                skipped.append((c.get("campaignId"), "its allocation to users was not read"))
                continue
            raw, end = int(got[0]), min(end, int(got[1]))
            if end <= start:
                skipped.append((c.get("campaignId"), "not yet computed by Merkl's engine"))
                continue
        elif str(c.get("type") or "") == CANCELLED:
            back = (refunds or {}).get(c.get("campaignId"))
            if back is None:
                skipped.append((c.get("campaignId"), "cancelled: its refund to the creator was not read"))
                continue
            raw = max(0, raw - int(back))
        amount = raw / 10 ** int(rt.get("decimals") or 18)
        t = start - start % DAY
        while t < min(end, end_ts):
            inside = min(end, t + DAY) - max(start, t)
            if inside > 0:
                per_day[_dt.datetime.fromtimestamp(t, _dt.timezone.utc).date()] += amount * inside / (end - start)
            t += DAY
    return dict(per_day), skipped


# D2-3: what Merkl credits BACK to a campaign's creator, by the reason its users endpoint gives — end_campaign
# (docs: "unallocated points at the end of the campaign are credited to the creator address"; "unspent budget returns
# to the creator"), reallocation (docs: the manager reallocates unclaimed rewards, here to itself), Invalid (docs: a
# cancellation "triggers the return of undistributed rewards to the admin address"), no_recipient (undocumented; the
# name: rewards with no recipient, credited to the creator). Any other reason is the creator EARNING as a participant
# (e.g. "ERC20_0xc0Df…" on Plume, 8 WPLUME) and stays allocated; read 2026-10-26, the only others are dust
# ("checkpoint", "Contract creation adjustment": ≤ 0.0015 MORPHO each).
RETURN_REASONS = ("end_campaign", "reallocation", "Invalid", "no_recipient")


def returned_to_creator(user_rewards: dict) -> int | None:
    """Raw tokens credited back to the creator under one campaign (its RETURN_REASONS breakdowns); None when the
    creator holds something but Merkl gives no breakdown to tell a return from an earning."""
    bd = user_rewards.get("breakdowns")
    if not bd:
        return 0 if int(user_rewards.get("amount") or 0) == 0 else None
    return sum(int(b.get("amount") or 0) for b in bd if b.get("reason") in RETURN_REASONS)


class MerklCampaigns:
    """Reads every project declaring `merkl_campaigns` once a day."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily_checks=None, cache_path=None, budget_s: float | None = None,
                 clock=None, **_ignored):
        import time as _time
        self.http = http or Http(min_interval=0.5, retries=2)   # D2-3: two reads per campaign
        if daily_checks is None:
            from .logcache import DailyChecks
            daily_checks = DailyChecks()
        self.daily = daily_checks
        self._cache_path = cache_path
        self._cache: dict | None = None
        self.budget_s = READ_BUDGET_S if budget_s is None else float(budget_s)
        self.clock = clock or _time.monotonic
        self.deadline = None
        self.stats = {"cached": 0, "read": 0, "reverified": 0, "changed": []}

    # ----------------------------------------------------------------------------------------- D4-2b: the cache
    def _cache_file(self):
        if self._cache_path is not None:
            from pathlib import Path
            return Path(self._cache_path)
        from .logcache import LogCache
        return LogCache().root / "merkl-allocations.json"

    def _load_cache(self) -> dict:
        if self._cache is None:
            import json
            try:
                self._cache = json.loads(self._cache_file().read_text())
            except (OSError, ValueError):
                self._cache = {}
        return self._cache

    def _save_cache(self) -> None:
        import json
        from .logcache import atomic_write_text
        f = self._cache_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(f, json.dumps(self._cache or {}, separators=(",", ":")))

    @staticmethod
    def _reverify_days(cid: str) -> int:
        try:
            return REVERIFY_DAYS + int(str(cid)[2:10], 16) % REVERIFY_SPREAD
        except ValueError:
            return REVERIFY_DAYS

    def _cached(self, key: str, cid: str, end: int, now: int) -> dict | None:
        """The cached (allocated, returned) of a FINAL campaign still within its re-verification period, else None."""
        e = self._load_cache().get(key)
        if not e or int(e.get("end", 0)) != int(end):
            return None
        final = int(end) <= now - FINAL_AFTER_DAYS * DAY and int(e.get("read_at", 0)) >= int(end) + FINAL_AFTER_DAYS * DAY
        if not final or now - int(e.get("read_at", 0)) >= self._reverify_days(cid) * DAY:
            return None
        return e

    def _campaigns(self, url: str, creator: str | None, chain_id: int, token: str | None = None) -> list:
        """Every campaign computed on chain_id — by `creator` (Morpho), or, with no creator (Plume, G9 N: WPLUME is
        funded by at least nine creators), every campaign paying `token` (tokenAddress filter; matched by address)."""
        rows = []
        for page in range(100):
            params = {"chainId": chain_id, "items": PAGE, "page": page}
            params.update({"creatorAddress": creator} if creator else {"tokenAddress": token})
            got = self.http.get(url, params=params)
            if not isinstance(got, list):
                raise ValueError(f"chain {chain_id}: not a list ({type(got).__name__})")
            rows += got
            if len(got) < PAGE:
                return rows
        raise ValueError(f"chain {chain_id}: more than {100 * PAGE} campaigns — not read in full")

    def _allocations(self, url: str, camps: list, chain_id: int, token: str, now: int) -> tuple[dict, int | None]:
        """({campaignId: (raw tokens allocated to users, until)}, the earliest `until` of a campaign still running) for
        every ROOT campaign paying `token`, distributed on chain_id, that has started (D2-3). Allocated to users =
        Merkl's GET /v4/rewards/total (what the engine allocated under the campaign) less GET /v4/users/{creator}/
        rewards/campaign/{id} (what it credited back to the creator: end_campaign leftovers, reallocations, cancelled
        refunds); allocated-but-unclaimed stays in. A running campaign's figure covers [start, computedUntil). Any read
        that fails raises: nothing is stored rather than a campaign counted at its schedule."""
        base = url.rsplit("/campaigns", 1)[0]
        out, running_until = {}, None
        for c in camps:
            dist = c.get("distributionChainId")
            if (c.get("parentCampaignId") or (dist is not None and int(dist) != int(chain_id))
                    or str((c.get("rewardToken") or {}).get("address") or "").lower() != token.lower()
                    or int(c["startTimestamp"]) >= now):
                continue
            cid = c["campaignId"]
            key = f"{chain_id}:{cid}"
            hit = self._cached(key, cid, int(c["endTimestamp"]), now)
            if hit is not None:
                self.stats["cached"] += 1
                out[cid] = (max(0, int(hit["allocated"]) - int(hit["returned"])), int(c["endTimestamp"]))
                continue
            if self.deadline is not None and self.clock() >= self.deadline:
                raise ReadIncomplete(f"the read budget ({self.budget_s:.0f}s) ran out with {self.stats['read']} "
                                     f"campaign(s) read this run and {self.stats['cached']} cached")
            creator = c.get("creatorAddress") or (c.get("creator") or {}).get("address")
            back = self.http.get(f"{base}/users/{creator}/rewards/campaign/{cid}", params={"chainId": chain_id})
            if not isinstance(back, dict) or "amount" not in back:
                raise ValueError(f"chain {chain_id}: campaign {cid[:10]}…: no refund amount (its return to the "
                                 f"creator) read")
            returned = returned_to_creator(back)
            if returned is None:
                raise ValueError(f"chain {chain_id}: campaign {cid[:10]}…: the creator holds {back['amount']} with no "
                                 f"breakdown by reason — no refund amount (its return to the creator) read")
            tot = self.http.get(f"{base}/rewards/total", params={"chainId": chain_id, "campaignId": cid})
            if not isinstance(tot, dict) or "amount" not in tot:
                raise ValueError(f"chain {chain_id}: campaign {cid[:10]}…: no allocated total read")
            until = int(c["endTimestamp"])
            if until > now:                                  # still running: known only to the engine's last pass
                cu = (c.get("campaignStatus") or {}).get("computedUntil")
                if cu is None:
                    raise ValueError(f"chain {chain_id}: running campaign {cid[:10]}…: no computedUntil")
                until = min(until, int(cu))
                running_until = until if running_until is None else min(running_until, until)
            out[cid] = (max(0, int(tot["amount"]) - returned), until)
            # D4-2b: every read kept, so a run cut short is continued next time; a FINAL campaign's re-read compared
            prev = self._load_cache().get(key)
            if prev and int(prev.get("end", 0)) == int(c["endTimestamp"]):
                if (str(prev.get("allocated")), str(prev.get("returned"))) != (str(int(tot["amount"])), str(returned)):
                    self.stats["changed"].append(f"{cid[:10]}… allocated {prev.get('allocated')} -> {int(tot['amount'])}"
                                                 f", returned {prev.get('returned')} -> {returned}")
                self.stats["reverified"] += 1
            self._cache[key] = {"allocated": str(int(tot["amount"])), "returned": str(int(returned)),
                                "end": int(c["endTimestamp"]), "read_at": int(now)}
            self.stats["read"] += 1
            if self.stats["read"] % 10 == 0:
                self._save_cache()
        return out, running_until

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("merkl_campaigns")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        m, day = spec["metric"], str(today().date())
        if not self.daily.due(f"merkl_campaigns:{name}", day):
            out.mark_current(SOURCE, name, m, f"{m}: the campaigns were read today ({day})", TIER)
            return
        total: dict = defaultdict(float)
        notes, seen, known_until = [], set(), None
        now = _now()
        if self.deadline is None:
            self.deadline = self.clock() + self.budget_s
        self.stats = {"cached": 0, "read": 0, "reverified": 0, "changed": []}
        t0, calls0 = self.clock(), int(self.http.stats.get("calls", 0)) if hasattr(self.http, "stats") else 0
        self._load_cache()
        try:
            for chain_id, chain in spec["chains"].items():
                camps = self._campaigns(spec["url"], spec.get("creator"), int(chain_id), chain["token"])
                alloc, until = self._allocations(spec["url"], camps, int(chain_id), chain["token"], now)
                if until is not None:
                    known_until = until if known_until is None else min(known_until, until)
                per_day, skipped = daily_amounts(camps, chain["token"], today().date(), int(chain_id), seen,
                                                 allocated=alloc)
                for d, v in per_day.items():
                    total[d] += v
                notes.append(f"{chain['name']}: {len(camps)} campaign(s), {len(skipped)} not counted"
                             + (f" ({'; '.join(r for _, r in skipped[:3])})" if skipped else ""))
        except ReadIncomplete as e:
            self._save_cache()
            out.fail(SOURCE, name, f"{m}: NOT YET COMPLETE — {e}; every campaign read is kept (merkl-allocations.json) "
                                   f"and the next run continues — NOTHING STORED (a sum missing campaigns would "
                                   f"understate)", TIER)
            return
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run; nothing partial is stored
            self._save_cache()
            out.fail(SOURCE, name, f"{m}: {e} — NOTHING STORED (a chain missing would understate the sum)", TIER)
            return
        self._save_cache()
        calls = (int(self.http.stats.get("calls", 0)) if hasattr(self.http, "stats") else 0) - calls0
        notes.append(f"campaign allocations: {self.stats['cached']} cached (ended {FINAL_AFTER_DAYS}+ days before "
                     f"their read), {self.stats['read']} read ({self.stats['reverified']} re-verified); {calls} call(s) "
                     f"in {self.clock() - t0:.0f}s")
        if self.stats["changed"]:
            notes.append("CHANGED SINCE THE CACHED READ: " + "; ".join(self.stats["changed"][:5]))
        self.daily.done(f"merkl_campaigns:{name}", day)
        # EVERY DAY FROM THE FIRST CAMPAIGN TO YESTERDAY, a day with no live campaign as 0: the (b) category sums its
        # legs only on days every leg has a row, so a day left out would drop the Ethereum claims with it
        # D2-3: complete days only — a running campaign's allocation is known up to the engine's computedUntil, so the
        # series stops the day before the earliest one (the next read carries it on)
        pts, last = [], today().date() - _dt.timedelta(days=1)
        if known_until is not None:
            ku = _dt.datetime.fromtimestamp(known_until, _dt.timezone.utc).date() - _dt.timedelta(days=1)
            if ku < last:
                notes.append(f"ends {ku}: a running campaign is computed by Merkl only to "
                             f"{_dt.datetime.fromtimestamp(known_until, _dt.timezone.utc):%Y-%m-%d %H:%M} UTC")
                last = ku
        if total:
            d = min(total)
            while d <= last:
                pts.append((_dt.datetime(d.year, d.month, d.day), total.get(d, 0.0)))
                d += _dt.timedelta(days=1)
        if pts:
            out.add(tidy(pts, name, m, ROW_SOURCE, TIER), SOURCE, name,
                    f"{m}: {len(pts)} day(s) {pts[0][0].date()}..{pts[-1][0].date()} — {'; '.join(notes)}", TIER)


def fill_cache(projects: list[dict] | None = None, http: Http | None = None, cache_path=None) -> dict:
    """D4-2b: the first fill of the allocation cache in one sitting (216 roots in 234 s, measured 2026-10-27), outside the
    daily run's budget. Reads every campaign of every project declaring `merkl_campaigns` with NO budget, writes the
    cache, and stores NOTHING in the store (the next daily run reads the final campaigns from the cache and stores the
    series). {project: (cached, read)}; a read that fails raises."""
    import config
    m = MerklCampaigns(http=http, daily_checks=object(), cache_path=cache_path, budget_s=float("inf"))
    now, got = _now(), {}
    for p in projects if projects is not None else config.PROJECTS:
        spec = p.get("merkl_campaigns")
        if not spec:
            continue
        m.stats = {"cached": 0, "read": 0, "reverified": 0, "changed": []}
        for chain_id, chain in spec["chains"].items():
            camps = m._campaigns(spec["url"], spec.get("creator"), int(chain_id), chain["token"])
            m._allocations(spec["url"], camps, int(chain_id), chain["token"], now)
            m._save_cache()
        got[p["name"]] = (m.stats["cached"], m.stats["read"])
        print(f"{p['name']}: {m.stats['cached']} campaign(s) already cached, {m.stats['read']} read", flush=True)
    return got


if __name__ == "__main__":
    import progress
    progress.line_buffered()
    import sys as _sys
    if "--fill" not in _sys.argv[1:]:
        _sys.exit("usage: python -m fetch.merkl_campaigns --fill   (fills merkl-allocations.json; stores nothing)")
    fill_cache()
    print("merkl-allocations.json filled; the next daily run stores the series", flush=True)
