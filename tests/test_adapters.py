"""
tests/test_adapters.py — adapter parsing against payloads shaped like the real APIs.

    python tests/test_adapters.py

The HTTP layer is stubbed, so this runs offline. It checks that each adapter turns the
documented response shape into tidy long rows with the right metric keys and source labels,
and that a failing endpoint is logged rather than raised.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DUNE_API_KEY"] = "test-key"

import fetch  # noqa: E402

TS1, TS2 = 1756684800, 1756771200   # 2025-09-01, 2025-09-02 UTC


class StubHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        for key, payload in self.routes.items():
            if key in url and (not isinstance(payload, tuple) or payload[0] is None or payload[0] == (params or {}).get("dataType")):
                body = payload[1] if isinstance(payload, tuple) else payload
                if isinstance(body, Exception):
                    raise body
                return body
        raise RuntimeError(f"HTTP 404 from {url}")


def test_defillama():
    dl = fetch.DefiLlama()
    dl.http = StubHttp({
        "/summary/fees/aave": (None, {"totalDataChart": [[TS1, 100.0], [TS2, 150.0]]}),
        "/protocol/aave": {"tvl": [{"date": TS1, "totalLiquidityUSD": 5e9}, {"date": TS2, "totalLiquidityUSD": 5.1e9}]},
        "/v2/historicalChainTvl/Ethereum": [{"date": TS1, "tvl": 6e10}, {"date": TS2, "tvl": 6.1e10}],
        "/stablecoincharts/Ethereum": [{"date": str(TS1), "totalCirculatingUSD": {"peggedUSD": 1.2e11, "peggedEUR": 1e8}}],
        "/protocols": [{"slug": "ondo", "category": "RWA", "chains": ["Ethereum"]}, {"slug": "uni", "category": "Dexes", "chains": ["Ethereum"]}],
        "/protocol/ondo": {"tvl": [], "chainTvls": {"Ethereum": {"tvl": [{"date": TS1, "totalLiquidityUSD": 1e9}]}}},
    })
    out = fetch.FetchOutput()
    aave = {"name": "Aave", "defillama_fees_slug": "aave", "defillama_protocol": "aave", "defillama_chain": None, "archetypes": [3]}
    eth = {"name": "Ethereum", "defillama_fees_slug": "broken", "defillama_protocol": None, "defillama_chain": "Ethereum", "archetypes": [1]}
    dl.run([aave, eth], None, out)
    df = out.frame()
    got = set(zip(df["project"], df["metric"]))
    assert ("Aave", "fees_usd") in got and ("Aave", "revenue_usd") in got and ("Aave", "holders_revenue_usd") in got, got
    assert ("Aave", "protocol_tvl_usd") in got
    assert ("Ethereum", "tvl_usd") in got and ("Ethereum", "stablecoin_supply_usd") in got and ("Ethereum", "rwa_defillama_usd") in got
    stab = df[(df.project == "Ethereum") & (df.metric == "stablecoin_supply_usd")]
    assert abs(stab.value.iloc[0] - 1.201e11) < 1, stab
    assert set(df["source"]) == {"defillama"}
    assert df["date"].dt.tz is None and df["date"].iloc[0] == pd.Timestamp("2025-09-01")
    fails = [e for e in out.log if e.status == "failed"]
    assert len(fails) == 3 and all(e.project == "Ethereum" and "broken" in e.message for e in fails), fails
    print("defillama ok:", len(df), "rows,", len(fails), "logged failures for the broken slug")


def test_coingecko():
    cg = fetch.CoinGecko()
    cg.http = StubHttp({
        "/coins/aave/market_chart": {"prices": [[TS1 * 1000, 200.0], [TS2 * 1000, 210.0]], "market_caps": [[TS1 * 1000, 3e9], [TS2 * 1000, 3.15e9]], "total_volumes": [[TS1 * 1000, 1e8], [TS2 * 1000, 1.1e8]]},
        "/coins/aave": {"market_data": {"circulating_supply": 15e6, "total_supply": 16e6, "max_supply": 16e6, "fully_diluted_valuation": {"usd": 3.36e9}}},
    })
    out = fetch.FetchOutput()
    cg.run([{"name": "Aave", "coingecko_id": "aave"}, {"name": "Nope", "coingecko_id": None}], None, out)
    df = out.frame()
    metrics = set(df["metric"])
    assert {"price_usd", "market_cap_usd", "volume_usd", "circulating_supply_implied", "circulating_supply", "total_supply", "max_supply", "fdv_usd"} <= metrics, metrics
    implied = df[df.metric == "circulating_supply_implied"]
    assert abs(implied.value.iloc[0] - 1.5e7) < 1 and implied.source.iloc[0] == "coingecko:mcap/price"
    assert cg.http.calls[0][1]["days"] == "365"
    assert any(e.status == "unconfigured" and e.project == "Nope" for e in out.log)
    print("coingecko ok:", sorted(metrics))


def test_dune():
    du = fetch.Dune()
    du.http = StubHttp({
        "/query/123/results": {"result": {"rows": [{"day": "2025-09-01 00:00:00.000 UTC", "burned": 12.5}, {"day": "2025-09-02 00:00:00.000 UTC", "burned": 13.0}]}},
    })
    out = fetch.FetchOutput()
    p = {"name": "Ethereum", "dune_queries": {"gross_burn_tokens": {"query_id": 123, "date_col": "day", "value_col": "burned"},
                                              "tx_count": {"query_id": None}}}
    du.run([p], 30 * 365, out)
    df = out.frame()
    assert len(df) == 2 and df.source.iloc[0] == "dune:123" and df.metric.iloc[0] == "gross_burn_tokens"
    assert du.http.calls[0][2] == {"X-Dune-API-Key": "test-key"}
    assert any(e.status == "unconfigured" and "tx_count" in e.message for e in out.log)
    print("dune ok")


def test_window():
    df = pd.DataFrame({"date": pd.to_datetime(["2020-01-01", pd.Timestamp.now("UTC").tz_localize(None).normalize()]),
                       "project": "x", "metric": "m", "value": [1.0, 2.0], "source": "s"})
    assert len(fetch._window(df, None)) == 2 and len(fetch._window(df, 30)) == 1
    print("window ok")


if __name__ == "__main__":
    test_defillama()
    test_coingecko()
    test_dune()
    test_window()
    print("ALL ADAPTER TESTS PASSED")
