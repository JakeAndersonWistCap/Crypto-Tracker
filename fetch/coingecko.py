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

    def __init__(self, known_absent: set | None = None, last_dates: dict | None = None):
        # {(project, metric): last stored date} — the price history is asked for only since then
        # plus OVERLAP_DAYS (A, Jake 2026-10-05: history once, then incremental).
        self.last_dates = last_dates or {}
        # (source, project) pairs whose endpoint 404'd and never worked — see store.known_absent.
        # A coin id that CoinGecko does not have 404s on every call, every run, for ever.
        self.known_absent = known_absent or set()
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

    def _markets(self, projects: list[dict]) -> dict:
        """Supply, max supply and FDV for EVERY coin in ONE call. Added 2026-09-28.

        /coins/{id} was called once per project only for these four fields — 30 calls, ~60s at the
        Demo plan's 30/min. /coins/markets carries the same market_data fields for up to 250 ids
        per call (circulating_supply, total_supply, max_supply, fully_diluted_valuation). Any
        coin it does not return falls back to /coins/{id} below."""
        ids = sorted({p["coingecko_id"] for p in projects
                      if p.get("coingecko_id") and (SOURCE, p["name"]) not in self.known_absent})
        found = {}
        for i in range(0, len(ids), 250):
            try:
                rows = self.http.get(f"{API}/coins/markets",
                                     params={"vs_currency": "usd", "ids": ",".join(ids[i:i + 250]),
                                             "per_page": 250, "page": 1, "sparkline": "false"},
                                     headers=self.headers)
            except Exception as e:  # noqa: BLE001 — fall back to per-coin detail calls
                log.info("coingecko: /coins/markets failed (%s) — falling back to /coins/{id}", e)
                continue
            for r in rows if isinstance(rows, list) else []:
                if isinstance(r, dict) and r.get("id"):
                    found[r["id"]] = r
        return found

    def run(self, projects: list[dict], window_days, out):
        from .base import BACKFILL, BACKFILL_DAYS, OVERLAP_DAYS
        markets = self._markets(projects)
        for p in projects:
            cid, name = p.get("coingecko_id"), p["name"]
            # A project whose price/market history is short asks for the full year (2026-09-28).
            short = any((name, m) in BACKFILL for m in ("price_usd", "market_cap_usd", "volume_usd",
                                                        "circulating_supply_implied"))
            days = ("365" if window_days is None
                    else str(BACKFILL_DAYS if short else window_days))
            # SINCE THE LAST STORED DAY (A, 2026-10-05): a routine run asks only for the days after
            # the newest stored price, plus OVERLAP_DAYS for the change checks (a Monday compares
            # with Friday) — never more than the window.
            last = self.last_dates.get((name, "price_usd"))
            if window_days is not None and not short and last is not None and not pd.isna(last):
                since = (today().normalize() - pd.Timestamp(last).normalize()).days
                days = str(max(1, min(int(window_days), since + OVERLAP_DAYS)))
            if not cid:
                out.unconfigured(SOURCE, name, "no coingecko_id", TIER)
                continue
            if (SOURCE, name) in self.known_absent:
                # skipped, not failed: no call was made, so there is no error — and not
                # unconfigured either, because the coingecko_id IS configured and the coin is
                # what is missing. Retried automatically once 14 days pass without an attempt.
                out.skipped(SOURCE, name,
                            f"{cid}: KNOWN ABSENT — this coin id 404'd and has never returned "
                            f"anything. Not called. Retried after 14 days without an attempt "
                            f"(store.ABSENT_RECHECK_DAYS); any success clears it permanently.",
                            TIER)
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
                if cid in markets:
                    mk = markets[cid]
                    md = {k: mk.get(k) for k in ("circulating_supply", "total_supply", "max_supply")}
                    md["fully_diluted_valuation"] = {"usd": mk.get("fully_diluted_valuation")}
                else:
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
                out.add(pd.DataFrame(rows, columns=LONG_COLUMNS), SOURCE, name,
                        f"{cid}:supply/fdv" + (" (batched /coins/markets)" if cid in markets else ""), TIER)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{cid}:coin detail: {e}", TIER)
