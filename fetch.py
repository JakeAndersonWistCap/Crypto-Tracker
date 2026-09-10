"""
fetch.py — data-source adapters.

Every adapter returns a tidy long-format DataFrame: date, project, metric, value, source.
A failed source never kills the run: the failure is returned as a log entry, the store keeps
the last known values, and the workbook marks the cell stale.

Sources
  defillama       fees / revenue / holders revenue / protocol TVL / chain TVL / stablecoins / RWA category
  coingecko       price, market cap, volume, circulating/total/max supply, FDV
  dune            protocol-specific series from query ids in config (DUNE_API_KEY in .env)
  schedule:config deterministic issuance from config.issuance_schedule (Bitcoin, Zcash, Bittensor, Venice)
  rwa.xyz         no public API on our tier — manual override only (logged as "unconfigured")
  tokenterminal   no API on our tier — manual override only

Backfill: window_days=None means the entire available history (first run). Subsequent runs
pass a trailing window (30 days) to catch source revisions; the DefiLlama endpoints return
the full series in one call, so we simply keep the trailing window client-side.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import config

log = logging.getLogger("token_metrics.fetch")

LONG_COLUMNS = ["date", "project", "metric", "value", "source"]
USER_AGENT = "token-metrics/1.0 (+https://github.com/JakeAndersonWistCap/Crypto-Tracker)"

DEFILLAMA_API = "https://api.llama.fi"
DEFILLAMA_STABLES = "https://stablecoins.llama.fi"
COINGECKO_API = "https://api.coingecko.com/api/v3"
DUNE_API = "https://api.dune.com/api/v1"


@dataclass
class LogEntry:
    source: str
    project: str | None
    rows: int
    status: str            # ok | failed | skipped | unconfigured
    message: str = ""


@dataclass
class FetchOutput:
    frames: list = field(default_factory=list)
    log: list = field(default_factory=list)

    def add(self, df: pd.DataFrame | None, source: str, project: str | None, message: str = ""):
        n = 0 if df is None else len(df)
        if df is not None and n:
            self.frames.append(df[LONG_COLUMNS])
        self.log.append(LogEntry(source, project, n, "ok", message))

    def fail(self, source: str, project: str | None, err: Exception | str):
        msg = str(err)
        log.warning("FAILED %s / %s: %s", source, project, msg)
        self.log.append(LogEntry(source, project, 0, "failed", msg))

    def unconfigured(self, source: str, project: str | None, message: str):
        self.log.append(LogEntry(source, project, 0, "unconfigured", message))

    def frame(self) -> pd.DataFrame:
        if not self.frames:
            return pd.DataFrame(columns=LONG_COLUMNS)
        return pd.concat(self.frames, ignore_index=True)


# ---------------------------------------------------------------------------------------
# HTTP helper with retry/backoff (429 and 5xx), never disables TLS verification.
# ---------------------------------------------------------------------------------------
class Http:
    def __init__(self, min_interval: float = 0.0, retries: int = 4, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.min_interval = float(os.environ.get("TOKEN_METRICS_MIN_INTERVAL", min_interval))
        self.retries = int(os.environ.get("TOKEN_METRICS_RETRIES", retries))   # env override for tests / offline runs
        self.timeout = timeout
        self._last = 0.0

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        wait = time.monotonic() - self._last
        if wait < self.min_interval:
            time.sleep(self.min_interval - wait)
        backoff = 2.0
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._last = time.monotonic()
            try:
                r = self.s.get(url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {r.status_code} from {url}")
                    if attempt < self.retries:
                        time.sleep(max(float(r.headers.get("Retry-After", backoff)), backoff))
                        backoff *= 2
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(backoff)
                    backoff *= 2
        raise RuntimeError(f"gave up after {self.retries + 1} attempts: {last_err}")


def _tidy(rows, project: str, metric: str, source: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"]).drop_duplicates(subset=["date"], keep="last")
    df["project"] = project
    df["metric"] = metric
    df["source"] = source
    return df[LONG_COLUMNS]


def _window(df: pd.DataFrame, window_days: int | None) -> pd.DataFrame:
    if window_days is None or df.empty:
        return df
    cutoff = pd.Timestamp.now('UTC').tz_localize(None).normalize() - pd.Timedelta(days=window_days)
    return df[df["date"] >= cutoff]


# ---------------------------------------------------------------------------------------
# DefiLlama
# ---------------------------------------------------------------------------------------
class DefiLlama:
    SOURCE = "defillama"

    def __init__(self):
        self.http = Http(min_interval=0.25)
        self._rwa_by_chain: pd.DataFrame | None = None

    def _summary_chart(self, slug: str, data_type: str):
        j = self.http.get(f"{DEFILLAMA_API}/summary/fees/{slug}", params={"dataType": data_type})
        chart = j.get("totalDataChart") or []
        return [(datetime.fromtimestamp(ts, tz=timezone.utc), v) for ts, v in chart]

    def fees(self, project: dict, window_days, out: FetchOutput):
        slug = project.get("defillama_fees_slug")
        name = project["name"]
        if not slug:
            out.unconfigured(self.SOURCE, name, "no defillama_fees_slug")
            return
        for data_type, metric in (("dailyFees", "fees_usd"), ("dailyRevenue", "revenue_usd"), ("dailyHoldersRevenue", "holders_revenue_usd")):
            try:
                rows = self._summary_chart(slug, data_type)
                out.add(_window(_tidy(rows, name, metric, self.SOURCE), window_days), self.SOURCE, name, f"{slug}:{data_type}")
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(self.SOURCE, name, f"{slug}:{data_type}: {e}")

    def protocol_tvl(self, project: dict, window_days, out: FetchOutput):
        slug = project.get("defillama_protocol")
        name = project["name"]
        if not slug:
            return
        try:
            j = self.http.get(f"{DEFILLAMA_API}/protocol/{slug}")
            rows = [(datetime.fromtimestamp(p["date"], tz=timezone.utc), p["totalLiquidityUSD"]) for p in j.get("tvl", [])]
            out.add(_window(_tidy(rows, name, "protocol_tvl_usd", self.SOURCE), window_days), self.SOURCE, name, f"{slug}:tvl")
        except Exception as e:  # noqa: BLE001
            out.fail(self.SOURCE, name, f"{slug}:protocol tvl: {e}")

    def chain_tvl(self, project: dict, window_days, out: FetchOutput):
        chain = project.get("defillama_chain")
        name = project["name"]
        if not chain:
            return
        try:
            j = self.http.get(f"{DEFILLAMA_API}/v2/historicalChainTvl/{chain}")
            rows = [(datetime.fromtimestamp(p["date"], tz=timezone.utc), p["tvl"]) for p in j]
            out.add(_window(_tidy(rows, name, "tvl_usd", self.SOURCE), window_days), self.SOURCE, name, f"{chain}:chain tvl")
        except Exception as e:  # noqa: BLE001
            out.fail(self.SOURCE, name, f"{chain}:chain tvl: {e}")

    def stablecoins(self, project: dict, window_days, out: FetchOutput):
        chain = project.get("defillama_chain")
        name = project["name"]
        if not chain:
            return
        try:
            j = self.http.get(f"{DEFILLAMA_STABLES}/stablecoincharts/{chain}")
            rows = []
            for p in j:
                circ = p.get("totalCirculatingUSD") or {}
                rows.append((datetime.fromtimestamp(int(p["date"]), tz=timezone.utc), sum(v for v in circ.values() if v)))
            out.add(_window(_tidy(rows, name, "stablecoin_supply_usd", self.SOURCE), window_days), self.SOURCE, name, f"{chain}:stablecoins")
        except Exception as e:  # noqa: BLE001
            out.fail(self.SOURCE, name, f"{chain}:stablecoins: {e}")

    def _build_rwa_by_chain(self, wanted_chains: set[str]) -> pd.DataFrame:
        """Sum the chain-level TVL history of every protocol in DefiLlama's RWA category.
        One /protocol/{slug} call per RWA protocol; result cached for the run."""
        protocols = self.http.get(f"{DEFILLAMA_API}/protocols")
        rwa = [p for p in protocols if str(p.get("category", "")).lower() in ("rwa", "rwa lending")]
        frames = []
        for p in rwa:
            chains = set(p.get("chains") or [])
            if not chains & wanted_chains:
                continue
            try:
                j = self.http.get(f"{DEFILLAMA_API}/protocol/{p['slug']}")
            except Exception as e:  # noqa: BLE001
                log.warning("RWA protocol %s failed: %s", p["slug"], e)
                continue
            for chain, block in (j.get("chainTvls") or {}).items():
                if chain not in wanted_chains:
                    continue
                pts = block.get("tvl") or []
                if not pts:
                    continue
                df = pd.DataFrame(pts)
                df["date"] = pd.to_datetime(df["date"], unit="s").dt.normalize()
                df = df.rename(columns={"totalLiquidityUSD": "value"})[["date", "value"]]
                df["chain"] = chain
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=["date", "chain", "value"])
        allf = pd.concat(frames)
        return allf.groupby(["chain", "date"], as_index=False)["value"].sum()

    def rwa(self, projects: list[dict], window_days, out: FetchOutput):
        wanted = {p["defillama_chain"]: p["name"] for p in projects if p.get("defillama_chain") and 1 in p["archetypes"]}
        if not wanted:
            return
        try:
            if self._rwa_by_chain is None:
                self._rwa_by_chain = self._build_rwa_by_chain(set(wanted))
        except Exception as e:  # noqa: BLE001
            out.fail(self.SOURCE, None, f"RWA category: {e}")
            return
        for chain, name in wanted.items():
            sub = self._rwa_by_chain[self._rwa_by_chain["chain"] == chain]
            if sub.empty:
                out.add(None, self.SOURCE, name, f"{chain}:rwa category (no RWA protocols on chain)")
                continue
            out.add(_window(_tidy(list(zip(sub["date"], sub["value"])), name, "rwa_defillama_usd", self.SOURCE), window_days),
                    self.SOURCE, name, f"{chain}:rwa category")

    def run(self, projects: list[dict], window_days, out: FetchOutput):
        for p in projects:
            self.fees(p, window_days, out)
            self.protocol_tvl(p, window_days, out)
            self.chain_tvl(p, window_days, out)
            self.stablecoins(p, window_days, out)
        self.rwa(projects, window_days, out)


# ---------------------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------------------
class CoinGecko:
    SOURCE = "coingecko"

    def __init__(self):
        key = os.environ.get("COINGECKO_API_KEY", "").strip()
        self.headers = {"x-cg-demo-api-key": key} if key else {}
        # public API: ~30 calls/min; demo key: ~30 calls/min too but more reliable
        self.http = Http(min_interval=2.2)

    def run(self, projects: list[dict], window_days, out: FetchOutput):
        # The public/demo tier limits market_chart history to 365 days. That covers the
        # 30-day and 3/6/9-month trajectory columns; the store accumulates beyond it.
        days = "365" if window_days is None else str(window_days)
        for p in projects:
            cid, name = p.get("coingecko_id"), p["name"]
            if not cid:
                out.unconfigured(self.SOURCE, name, "no coingecko_id")
                continue
            try:
                j = self.http.get(f"{COINGECKO_API}/coins/{cid}/market_chart",
                                  params={"vs_currency": "usd", "days": days, "interval": "daily"}, headers=self.headers)
                prices = _tidy([(datetime.fromtimestamp(ts / 1000, tz=timezone.utc), v) for ts, v in j.get("prices", [])], name, "price_usd", self.SOURCE)
                mcaps = _tidy([(datetime.fromtimestamp(ts / 1000, tz=timezone.utc), v) for ts, v in j.get("market_caps", [])], name, "market_cap_usd", self.SOURCE)
                vols = _tidy([(datetime.fromtimestamp(ts / 1000, tz=timezone.utc), v) for ts, v in j.get("total_volumes", [])], name, "volume_usd", self.SOURCE)
                out.add(prices, self.SOURCE, name, f"{cid}:price")
                out.add(mcaps, self.SOURCE, name, f"{cid}:market cap")
                out.add(vols, self.SOURCE, name, f"{cid}:volume")
                # circulating supply implied by market cap / price — a documented derivation,
                # labelled as such in its source string so it is never mistaken for reported supply.
                m = prices.merge(mcaps, on="date", suffixes=("_p", "_m"))
                m = m[m["value_p"] > 0]
                implied = pd.DataFrame({
                    "date": m["date"], "project": name, "metric": "circulating_supply_implied",
                    "value": m["value_m"] / m["value_p"], "source": "coingecko:mcap/price",
                })
                out.add(implied, "coingecko:mcap/price", name, f"{cid}:implied supply")
            except Exception as e:  # noqa: BLE001
                out.fail(self.SOURCE, name, f"{cid}:market_chart: {e}")
            try:
                j = self.http.get(f"{COINGECKO_API}/coins/{cid}",
                                  params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false", "sparkline": "false"},
                                  headers=self.headers)
                md = j.get("market_data") or {}
                today = pd.Timestamp.now('UTC').tz_localize(None).normalize()
                rows = []
                for metric, key in (("circulating_supply", "circulating_supply"), ("total_supply", "total_supply"), ("max_supply", "max_supply")):
                    v = md.get(key)
                    if v is not None:
                        rows.append({"date": today, "project": name, "metric": metric, "value": float(v), "source": self.SOURCE})
                fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
                if fdv is not None:
                    rows.append({"date": today, "project": name, "metric": "fdv_usd", "value": float(fdv), "source": self.SOURCE})
                out.add(pd.DataFrame(rows, columns=LONG_COLUMNS), self.SOURCE, name, f"{cid}:supply/fdv")
            except Exception as e:  # noqa: BLE001
                out.fail(self.SOURCE, name, f"{cid}:coin detail: {e}")


# ---------------------------------------------------------------------------------------
# Dune
# ---------------------------------------------------------------------------------------
class Dune:
    SOURCE = "dune"

    def __init__(self):
        self.key = os.environ.get("DUNE_API_KEY", "").strip()
        self.http = Http(min_interval=0.5)

    def _results(self, query_id: int) -> list[dict]:
        rows, offset, limit = [], 0, 5000
        while True:
            j = self.http.get(f"{DUNE_API}/query/{query_id}/results",
                              params={"limit": limit, "offset": offset}, headers={"X-Dune-API-Key": self.key})
            batch = (j.get("result") or {}).get("rows") or []
            rows.extend(batch)
            if len(batch) < limit:
                return rows
            offset += limit

    def run(self, projects: list[dict], window_days, out: FetchOutput):
        for p in projects:
            name = p["name"]
            queries = p.get("dune_queries") or {}
            for metric, q in queries.items():
                qid = q.get("query_id")
                if not qid:
                    out.unconfigured(self.SOURCE, name, f"{metric}: no query_id in config")
                    continue
                if not self.key:
                    out.fail(self.SOURCE, name, f"{metric}: DUNE_API_KEY not set in .env")
                    continue
                try:
                    rows = self._results(int(qid))
                    dcol, vcol = q.get("date_col", "day"), q.get("value_col", "value")
                    pairs = [(r[dcol], r[vcol]) for r in rows if r.get(dcol) is not None]
                    df = _tidy(pairs, name, metric, f"dune:{qid}")
                    out.add(_window(df, window_days), self.SOURCE, name, f"{metric}: query {qid}")
                except Exception as e:  # noqa: BLE001
                    out.fail(self.SOURCE, name, f"{metric}: query {qid}: {e}")


# ---------------------------------------------------------------------------------------
# Deterministic issuance schedules from config (Bitcoin, Zcash, Bittensor, Venice)
# ---------------------------------------------------------------------------------------
class Schedule:
    SOURCE = "schedule:config"

    def run(self, projects: list[dict], window_days, out: FetchOutput):
        today = pd.Timestamp.now('UTC').tz_localize(None).normalize()
        for p in projects:
            sched = p.get("issuance_schedule")
            if not sched or not sched.get("steps"):
                continue
            steps = sorted(sched["steps"], key=lambda s: s["from"])
            start = pd.Timestamp(steps[0]["from"])
            if window_days is not None:
                start = max(start, today - pd.Timedelta(days=window_days))
            dates = pd.date_range(start, today, freq="D")
            per_day = pd.Series(index=dates, dtype=float)
            for s in steps:
                per_day[per_day.index >= pd.Timestamp(s["from"])] = float(s["tokens_per_day"])
            df = pd.DataFrame({"date": dates, "project": p["name"], "metric": "gross_issuance_tokens",
                               "value": per_day.values, "source": self.SOURCE})
            out.add(df, self.SOURCE, p["name"], "issuance schedule")


# ---------------------------------------------------------------------------------------
# Sources with no API on our tier: manual override only. Never fabricate an endpoint.
# ---------------------------------------------------------------------------------------
class ManualOnly:
    def __init__(self, source: str, metrics: list[str], archetype: int | None = None):
        self.source, self.metrics, self.archetype = source, metrics, archetype

    def run(self, projects: list[dict], window_days, out: FetchOutput):
        for p in projects:
            if self.archetype is not None and self.archetype not in p["archetypes"]:
                continue
            for m in self.metrics:
                out.unconfigured(self.source, p["name"], f"{m}: no API on our tier — manual_overrides.csv only")


# ---------------------------------------------------------------------------------------
def fetch_all(projects: list[dict], window_days: int | None, sources: list[str] | None = None) -> FetchOutput:
    out = FetchOutput()
    adapters = {
        "schedule:config": Schedule(),
        "defillama": DefiLlama(),
        "coingecko": CoinGecko(),
        "dune": Dune(),
        "rwa.xyz": ManualOnly("rwa.xyz", ["rwa_xyz_usd"], archetype=1),
        "tokenterminal": ManualOnly("tokenterminal", [], None),
    }
    for name, adapter in adapters.items():
        if sources and name not in sources:
            continue
        log.info("fetching %s (window=%s)", name, window_days or "full history")
        try:
            adapter.run(projects, window_days, out)
        except Exception as e:  # noqa: BLE001 — never let one source kill the run
            out.fail(name, None, f"adapter crashed: {e}")
    return out


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
