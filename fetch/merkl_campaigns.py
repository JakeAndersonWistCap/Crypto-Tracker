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
live campaign's amount × (its seconds inside that UTC day) ÷ its duration. That is the programme's own schedule, not a
model and not claims (claims lag and are lumpy; Ethereum keeps its claims series, so Ethereum is NOT read here — the
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
TIER = 1
PAGE = 100
DAY = 86_400


CANCELLED = "INVALID"                # Merkl's type for a cancelled campaign (docs: "overrides … to an invalid type")


def daily_amounts(campaigns: list[dict], token_address: str, end_day: _dt.date, chain_id: int | None = None,
                  seen: set | None = None, refunds: dict | None = None) -> tuple[dict, list]:
    """({date: tokens}, [skipped (campaignId, reason)]) for the campaigns paying `token_address` ON `chain_id` (their
    distributionChainId), complete days < end; a campaignId already in `seen` is skipped (and added when counted).

    D1-2 (Morpho, reconciled 2026-10-26): a SUB-CAMPAIGN (parentCampaignId set) is the engine's forwarder share of its
    parent — no new money (Merkl docs; the child id is no campaign in DistributionCreator) — and is never counted: the
    parent's amount already holds it (1.0M MORPHO double-counted 2025-10..2026-10). A CANCELLED campaign (type INVALID)
    counts its amount less what went back to its creator (`refunds` {campaignId: raw amount}, Merkl's own
    /users/{creator}/rewards/campaign/{id}); without that read it is not counted and says so."""
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
        if str(c.get("type") or "") == CANCELLED:
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


class MerklCampaigns:
    """Reads every project declaring `merkl_campaigns` once a day."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily_checks=None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily_checks is None:
            from .logcache import DailyChecks
            daily_checks = DailyChecks()
        self.daily = daily_checks

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

    def _refunds(self, url: str, camps: list, chain_id: int, token: str) -> dict:
        """{campaignId: raw amount returned to its creator} for every CANCELLED root campaign paying `token` on chain_id
        (D1-2) — Merkl's own GET /v4/users/{creator}/rewards/campaign/{id}?chainId=. A failed read raises: nothing is
        stored rather than a cancelled campaign counted at its full amount."""
        base = url.rsplit("/campaigns", 1)[0]
        out = {}
        for c in camps:
            if (str(c.get("type") or "") != CANCELLED or c.get("parentCampaignId")
                    or str((c.get("rewardToken") or {}).get("address") or "").lower() != token.lower()):
                continue
            creator = c.get("creatorAddress") or (c.get("creator") or {}).get("address")
            got = self.http.get(f"{base}/users/{creator}/rewards/campaign/{c['campaignId']}",
                                params={"chainId": chain_id})
            if not isinstance(got, dict) or "amount" not in got:
                raise ValueError(f"chain {chain_id}: cancelled campaign {c['campaignId'][:10]}…: no refund amount")
            out[c["campaignId"]] = int(got["amount"])
        return out

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
        notes, seen = [], set()
        try:
            for chain_id, chain in spec["chains"].items():
                camps = self._campaigns(spec["url"], spec.get("creator"), int(chain_id), chain["token"])
                refunds = self._refunds(spec["url"], camps, int(chain_id), chain["token"])
                per_day, skipped = daily_amounts(camps, chain["token"], today().date(), int(chain_id), seen, refunds)
                for d, v in per_day.items():
                    total[d] += v
                notes.append(f"{chain['name']}: {len(camps)} campaign(s), {len(skipped)} not counted"
                             + (f" ({'; '.join(r for _, r in skipped[:3])})" if skipped else ""))
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run; nothing partial is stored
            out.fail(SOURCE, name, f"{m}: {e} — NOTHING STORED (a chain missing would understate the sum)", TIER)
            return
        self.daily.done(f"merkl_campaigns:{name}", day)
        # EVERY DAY FROM THE FIRST CAMPAIGN TO YESTERDAY, a day with no live campaign as 0: the (b) category sums its
        # legs only on days every leg has a row, so a day left out would drop the Ethereum claims with it
        pts = []
        if total:
            d, last = min(total), today().date() - _dt.timedelta(days=1)
            while d <= last:
                pts.append((_dt.datetime(d.year, d.month, d.day), total.get(d, 0.0)))
                d += _dt.timedelta(days=1)
        if pts:
            out.add(tidy(pts, name, m, SOURCE, TIER), SOURCE, name,
                    f"{m}: {len(pts)} day(s) {pts[0][0].date()}..{pts[-1][0].date()} — {'; '.join(notes)}", TIER)
