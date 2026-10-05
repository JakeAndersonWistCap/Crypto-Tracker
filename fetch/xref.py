"""
fetch/xref.py — INDEPENDENT REFERENCES for the Credibility tab (Jake, 2026-10-05).

Figures from sources OTHER than the one each headline is read from, stored under their own metrics
so build_workbook.write_credibility can set them beside our figure. None of them feeds a headline.

  price_usd_coinbase   Coinbase Exchange public market data, one daily candle per UTC day:
                         GET https://api.exchange.coinbase.com/products/{SYMBOL}-USD/candles
                             ?granularity=86400&start=<ISO>&end=<ISO>
                         -> [[time, low, high, open, close, volume], ...]   (time = the day's start, unix s)
                       The OPEN of day D is the price at D 00:00 UTC — the instant CoinGecko's daily
                       market_chart point for D is stamped — so the two compare on the same instant.
                       An exchange price: independent of CoinGecko's aggregate. A product that does not
                       exist answers HTTP 404; it is remembered as NOT LISTED and re-tried weekly.
  staking_apr_lido     Lido's stETH APR, 7-day simple moving average, as a FRACTION:
                         GET https://eth-api.lido.fi/v1/protocol/steth/apr/sma
                         -> {"data": {"aprs": [{"timeUnix", "apr"}, ...], "smaApr": <percent>}, "meta": {...}}
                       NET of Lido's 10% protocol fee — labelled as Lido's, and grossed up (/0.9) only
                       on the Credibility row that compares it with the network's staking yield.

POLITE: robots.txt per host (the pipeline's RFC 9309 reader), once a day per reference (DailyChecks),
an honest User-Agent, ~1 request a second. The first read takes `days` of candles, later runs a week.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.xref")

SOURCE = "xref"
TIER = 1
COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"
LIDO_SMA = "https://eth-api.lido.fi/v1/protocol/steth/apr/sma"


class CrossRefs:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, robots=None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        if robots is None:
            from .scrape import robots_verdict
            robots = robots_verdict
        self.robots = robots

    def run(self, projects: list[dict], window_days, out):
        spec = config.CREDIBILITY_XREF
        day = str(today().date())
        names = set(config.CREDIBILITY_PROJECTS)
        for p in projects:
            if p["name"] not in names:
                continue
            if p.get("symbol"):
                self._coinbase(p, spec, day, out)
            if p["name"] in spec.get("lido_apr_projects", ()):
                self._lido(p, day, out)

    # ------------------------------------------------------------------ Coinbase candles
    def _coinbase(self, p: dict, spec: dict, day: str, out) -> None:
        name = p["name"]
        product = (spec.get("coinbase_product") or {}).get(name) or f"{p['symbol']}-USD"
        key = f"xref:coinbase:{product}"
        absent = self.daily.get(f"{key}:absent")
        if absent and (pd.Timestamp(day) - pd.Timestamp(absent)).days < 7:
            out.skipped(SOURCE, name, f"price_usd_coinbase: {product} not listed on Coinbase Exchange "
                                      f"(HTTP 404 on {absent}; re-tried weekly)", TIER)
            return
        if not self.daily.due(key, day):
            out.skipped(SOURCE, name, f"price_usd_coinbase: {product} read today already", TIER)
            return
        url = COINBASE.format(product=product)
        ok, why = self.robots(url)
        if not ok:
            out.fail(SOURCE, name, f"price_usd_coinbase: robots.txt disallows {url} ({why})", TIER)
            return
        days = int(spec.get("days", 30)) if not self.daily.ever(key) else 7
        end = today().normalize() + pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=days)
        try:
            body = self.http.get(url, params={"granularity": 86400, "start": start.isoformat(),
                                              "end": end.isoformat()})
        except Exception as e:  # noqa: BLE001 — a 404 is an answer: not listed
            if "404" in str(e) or "NotFound" in str(e):
                self.daily.set(f"{key}:absent", day)
                out.skipped(SOURCE, name, f"price_usd_coinbase: {product} not listed on Coinbase Exchange "
                                          f"(HTTP 404) — the price row uses its fallback reference", TIER)
                return
            out.fail(SOURCE, name, f"price_usd_coinbase: {product}: {e}", TIER)
            return
        rows = self.parse_candles(body)
        if isinstance(rows, str):
            out.fail(SOURCE, name, f"price_usd_coinbase: {product}: {rows}", TIER)
            return
        frame = tidy(rows, name, "price_usd_coinbase", f"coinbase_exchange:{product}:open", TIER)
        out.add(frame, SOURCE, name, f"price_usd_coinbase: {len(rows)} day(s) of {product} daily OPEN", TIER)
        self.daily.done(key, day)

    @staticmethod
    def parse_candles(body) -> list[tuple] | str:
        """[(day, open)] from Coinbase Exchange daily candles, or why they are refused."""
        if not isinstance(body, list):
            return f"not a list of candles ({type(body).__name__}: {str(body)[:120]})"
        rows = []
        for c in body:
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                return f"a candle that is not [time, low, high, open, close, volume]: {str(c)[:80]}"
            t, opn = int(c[0]), float(c[3])
            if t % 86400:
                return f"a candle that does not start at 00:00 UTC (time {t})"
            rows.append((pd.Timestamp(t, unit="s"), opn))
        return sorted(rows)

    # ------------------------------------------------------------------ Lido APR
    def _lido(self, p: dict, day: str, out) -> None:
        name, key = p["name"], "xref:lido_apr"
        if not self.daily.due(key, day):
            out.skipped(SOURCE, name, "staking_apr_lido: read today already", TIER)
            return
        ok, why = self.robots(LIDO_SMA)
        if not ok:
            out.fail(SOURCE, name, f"staking_apr_lido: robots.txt disallows {LIDO_SMA} ({why})", TIER)
            return
        try:
            body = self.http.get(LIDO_SMA)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"staking_apr_lido: {e}", TIER)
            return
        sma = ((body or {}).get("data") or {}).get("smaApr") if isinstance(body, dict) else None
        try:
            frac = float(sma) / 100.0
        except (TypeError, ValueError):
            out.fail(SOURCE, name, f"staking_apr_lido: no data.smaApr in the answer "
                                   f"(keys {sorted((body or {}).get('data') or {})[:8] if isinstance(body, dict) else '?'})",
                     TIER)
            return
        if not 0 < frac < 0.2:
            out.fail(SOURCE, name, f"staking_apr_lido: {sma}% is outside 0-20% — refused (a unit change?)", TIER)
            return
        frame = tidy([(today(), frac)], name, "staking_apr_lido", "lido_api:steth/apr/sma", TIER)
        out.add(frame, SOURCE, name, f"staking_apr_lido: {frac:.4%} (Lido stETH 7-day SMA, net of Lido's fee)", TIER)
        self.daily.done(key, day)
