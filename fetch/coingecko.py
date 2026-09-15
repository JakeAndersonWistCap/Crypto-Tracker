"""
fetch/coingecko.py — tier 1: CoinGecko free API.

Price, market cap, volume, circulating/total/max supply and FDV. The public tier serves 365
days of daily history, which covers the 30-day and 3/6/9-month trajectory columns; the store
accumulates beyond it from run to run.

SUPPLY IS A SNAPSHOT, AND THERE IS NO BACKFILL. market_chart gives 365 days of price, market cap
and volume; the supply figures come from /coins/{id}, which returns TODAY only. /coins/{id}/history
does not close the gap — confirmed on a live call (2026-09-14), its market_data carries
current_price, market_cap and total_volume and no supply field at all. So every supply series
starts the day the tool first runs, and issuance derived from a supply delta is FORWARD-ONLY.
Historical issuance exists only where a live endpoint or a declared schedule provides it. This is
settled, not an open question.

circulating_supply_implied is market cap / price. It is a derivation, not a reported figure,
so it carries the source coingecko:mcap/price and is used only as a cross-check against the
reported supply on the archetype 4 tab.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import pandas as pd

from .base import LONG_COLUMNS, Http, tidy, today

SOURCE = "coingecko"
TIER = 1
log = logging.getLogger("token_metrics.fetch.coingecko")
API = "https://api.coingecko.com/api/v3"


class CoinGecko:
    # Inter-call floor, and it is NOT the same question as "how fast may we go".
    #
    # The run makes 2 calls per project, 60 in all. At the old hardcoded 2.2s that is 132 seconds
    # of deliberate waiting — nowhere near the 16 minutes observed. The other ~14 minutes were
    # 429 backoff: 2.2s is 27 calls/min, which the Demo tier allows and the KEYLESS public tier
    # does not, so without a key the run spent most of its time being rate-limited and retrying.
    #
    # Which makes the polite setting also the fast one. Going slower without a key finishes
    # sooner than going fast and being throttled, because a 429 costs a documented Retry-After
    # plus exponential backoff, and it costs it on every call rather than once.
    WITH_KEY = 2.0      # Demo plan: 30 calls/min documented. 60 calls -> ~2 minutes.
    NO_KEY = 6.0        # public tier is variable and lower (roughly 5-15/min); 10/min sits inside
                        # that band. 60 calls -> ~6 minutes, and no 429 storm on top.

    def __init__(self):
        key = os.environ.get("COINGECKO_API_KEY", "").strip()
        self.headers = {"x-cg-demo-api-key": key} if key else {}
        self.http = Http(min_interval=self.WITH_KEY if key else self.NO_KEY)
        # The floor is chosen from key PRESENCE, but the throttling is decided by key ACCEPTANCE,
        # and those are not the same question. A Pro key sent under the Demo header name, to the
        # Demo host, is silently ignored: CoinGecko serves the request as anonymous and rate-limits
        # it accordingly, while this adapter — seeing a non-empty variable — picks the FAST floor.
        # That combination is the worst case, and it is invisible unless the two are printed apart.
        log.info(
            "coingecko: COINGECKO_API_KEY %s (len %d) | header %s | host %s | min_interval %.1fs | "
            "floor chosen from key PRESENCE, not from the server accepting it",
            "DETECTED" if key else "NOT SET", len(key),
            next(iter(self.headers), "(none — anonymous)"), API.split("//")[1].split("/")[0],
            self.http.min_interval,
        )

    @staticmethod
    def _ms_rows(pairs):
        return [(datetime.fromtimestamp(ts / 1000, tz=timezone.utc), v) for ts, v in pairs]

    def run(self, projects: list[dict], window_days, out):
        days = "365" if window_days is None else str(window_days)
        for p in projects:
            cid, name = p.get("coingecko_id"), p["name"]
            if not cid:
                out.unconfigured(SOURCE, name, "no coingecko_id", TIER)
                continue
            try:
                j = self.http.get(f"{API}/coins/{cid}/market_chart",
                                  params={"vs_currency": "usd", "days": days, "interval": "daily"},
                                  headers=self.headers)
                prices = tidy(self._ms_rows(j.get("prices", [])), name, "price_usd", SOURCE, TIER)
                mcaps = tidy(self._ms_rows(j.get("market_caps", [])), name, "market_cap_usd", SOURCE, TIER)
                vols = tidy(self._ms_rows(j.get("total_volumes", [])), name, "volume_usd", SOURCE, TIER)
                out.add(prices, SOURCE, name, f"{cid}:price", TIER)
                out.add(mcaps, SOURCE, name, f"{cid}:market cap", TIER)
                out.add(vols, SOURCE, name, f"{cid}:volume", TIER)
                m = prices.merge(mcaps, on="date", suffixes=("_p", "_m"))
                m = m[m["value_p"] > 0]
                if not m.empty:
                    implied = pd.DataFrame({"date": m["date"], "project": name,
                                            "metric": "circulating_supply_implied",
                                            "value": m["value_m"] / m["value_p"],
                                            "source": "coingecko:mcap/price", "tier": TIER})
                    out.add(implied[LONG_COLUMNS], SOURCE, name, f"{cid}:implied supply", TIER)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{cid}:market_chart: {e}", TIER)
            try:
                j = self.http.get(f"{API}/coins/{cid}",
                                  params={"localization": "false", "tickers": "false", "community_data": "false",
                                          "developer_data": "false", "sparkline": "false"},
                                  headers=self.headers)
                md = j.get("market_data") or {}
                when, rows = today(), []
                for metric, key in (("circulating_supply", "circulating_supply"),
                                    ("total_supply", "total_supply"), ("max_supply", "max_supply")):
                    v = md.get(key)
                    if v is not None:
                        rows.append({"date": when, "project": name, "metric": metric,
                                     "value": float(v), "source": SOURCE, "tier": TIER})
                fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
                if fdv is not None:
                    rows.append({"date": when, "project": name, "metric": "fdv_usd",
                                 "value": float(fdv), "source": SOURCE, "tier": TIER})
                out.add(pd.DataFrame(rows, columns=LONG_COLUMNS), SOURCE, name, f"{cid}:supply/fdv", TIER)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{cid}:coin detail: {e}", TIER)
