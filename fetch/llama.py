"""
fetch/llama.py — tier 1: DefiLlama free endpoints.

Free and unauthenticated: fees, revenue, holders revenue, protocol TVL, chain TVL, stablecoin
supply by chain, and the RWA category aggregated per chain. That is the demand side.

NOT available free, and deliberately not attempted here:
  * emissions / unlocks         — Pro tier, separate API plan
  * per-protocol-version fees   — Pro tier (Uniswap needs fees split by version)
Both are written to the Gap Report rather than approximated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from .base import Http, tidy, window

log = logging.getLogger("token_metrics.fetch.llama")

SOURCE = "defillama"
TIER = 1
API = "https://api.llama.fi"
STABLES = "https://stablecoins.llama.fi"

PRO_ONLY = {
    "emissions_tokens": "DefiLlama emissions/unlocks is Pro tier (separate API plan) — not fetched",
    "fees_by_version": "DefiLlama per-version fee breakdown is Pro tier — Uniswap implied burn needs it",
}


class DefiLlama:
    def __init__(self):
        self.http = Http(min_interval=0.25)
        self._rwa_by_chain: pd.DataFrame | None = None

    # ------------------------------------------------------------------ fees & revenue
    def _summary_chart(self, slug: str, data_type: str):
        j = self.http.get(f"{API}/summary/fees/{slug}", params={"dataType": data_type})
        return [(datetime.fromtimestamp(ts, tz=timezone.utc), v) for ts, v in (j.get("totalDataChart") or [])]

    def fees(self, project: dict, window_days, out):
        slug, name = project.get("defillama_fees_slug"), project["name"]
        if not slug:
            out.unconfigured(SOURCE, name, "no defillama_fees_slug — not tracked by DefiLlama", TIER)
            return
        for data_type, metric in (("dailyFees", "fees_usd"), ("dailyRevenue", "revenue_usd"),
                                  ("dailyHoldersRevenue", "holders_revenue_usd")):
            try:
                rows = self._summary_chart(slug, data_type)
                out.add(window(tidy(rows, name, metric, SOURCE, TIER), window_days), SOURCE, name,
                        f"{slug}:{data_type}", TIER)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{slug}:{data_type}: {e}", TIER)

    # ------------------------------------------------------------------ TVL
    def protocol_tvl(self, project: dict, window_days, out):
        slug, name = project.get("defillama_protocol"), project["name"]
        if not slug:
            return
        try:
            j = self.http.get(f"{API}/protocol/{slug}")
            rows = [(datetime.fromtimestamp(p["date"], tz=timezone.utc), p["totalLiquidityUSD"]) for p in j.get("tvl", [])]
            out.add(window(tidy(rows, name, "protocol_tvl_usd", SOURCE, TIER), window_days), SOURCE, name, f"{slug}:tvl", TIER)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{slug}:protocol tvl: {e}", TIER)

    def chain_tvl(self, project: dict, window_days, out):
        chain, name = project.get("defillama_chain"), project["name"]
        if not chain:
            return
        try:
            j = self.http.get(f"{API}/v2/historicalChainTvl/{chain}")
            rows = [(datetime.fromtimestamp(p["date"], tz=timezone.utc), p["tvl"]) for p in j]
            out.add(window(tidy(rows, name, "tvl_usd", SOURCE, TIER), window_days), SOURCE, name, f"{chain}:chain tvl", TIER)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{chain}:chain tvl: {e}", TIER)

    def stablecoins(self, project: dict, window_days, out):
        chain, name = project.get("defillama_chain"), project["name"]
        if not chain:
            return
        try:
            j = self.http.get(f"{STABLES}/stablecoincharts/{chain}")
            rows = []
            for p in j:
                circ = p.get("totalCirculatingUSD") or {}
                rows.append((datetime.fromtimestamp(int(p["date"]), tz=timezone.utc),
                             sum(v for v in circ.values() if v)))
            out.add(window(tidy(rows, name, "stablecoin_supply_usd", SOURCE, TIER), window_days), SOURCE, name,
                    f"{chain}:stablecoins", TIER)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{chain}:stablecoins: {e}", TIER)

    # ------------------------------------------------------------------ RWA category by chain
    def _build_rwa_by_chain(self, wanted: set[str]) -> pd.DataFrame:
        protocols = self.http.get(f"{API}/protocols")
        rwa = [p for p in protocols if str(p.get("category", "")).lower() in ("rwa", "rwa lending")]
        frames = []
        for p in rwa:
            if not (set(p.get("chains") or []) & wanted):
                continue
            try:
                j = self.http.get(f"{API}/protocol/{p['slug']}")
            except Exception as e:  # noqa: BLE001
                log.warning("RWA protocol %s failed: %s", p["slug"], e)
                continue
            for chain, block in (j.get("chainTvls") or {}).items():
                if chain not in wanted:
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
        return pd.concat(frames).groupby(["chain", "date"], as_index=False)["value"].sum()

    def rwa(self, projects: list[dict], window_days, out):
        wanted = {p["defillama_chain"]: p["name"] for p in projects
                  if p.get("defillama_chain") and 1 in p["archetypes"]}
        if not wanted:
            return
        try:
            if self._rwa_by_chain is None:
                self._rwa_by_chain = self._build_rwa_by_chain(set(wanted))
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, None, f"RWA category: {e}", TIER)
            return
        for chain, name in wanted.items():
            sub = self._rwa_by_chain[self._rwa_by_chain["chain"] == chain]
            if sub.empty:
                out.add(None, SOURCE, name, f"{chain}: no RWA-category protocols on chain", TIER)
                continue
            out.add(window(tidy(zip(sub["date"], sub["value"]), name, "rwa_defillama_usd", SOURCE, TIER), window_days),
                    SOURCE, name, f"{chain}:rwa category", TIER)

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            self.fees(p, window_days, out)
            self.protocol_tvl(p, window_days, out)
            self.chain_tvl(p, window_days, out)
            self.stablecoins(p, window_days, out)
        self.rwa(projects, window_days, out)
