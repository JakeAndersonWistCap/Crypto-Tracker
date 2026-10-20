"""
tests/test_round_g9.py — Round G9 (Jake 2026-10-20), SPEC §19 "G9 — Round G9, old → new". One test (or more) per item,
each named for its row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402


# ======================================================================================== A1 Pendle, every gauge chain
def test_a1_pendle_samples_every_chain_whose_gauge_emissions_tokens_sums():
    from fetch import subsidy_targeting as st
    gauges = {c["chain"] for c in st.pendle_gauge_chains()}
    scans = {s["chain"] for s in config.PROJECT_BY_NAME["Pendle"]["log_scans"]
             if str(s.get("key", "")).startswith("gauge_pendle_out")}
    scans |= {c for _i, p, c, k, *_r in config.EXPLORER_PROBED_SCANS if p == "Pendle" and k.startswith("gauge_pendle")}
    assert gauges == scans and len(gauges) == 9
    spec = config.PROJECT_BY_NAME["Pendle"]["subsidy_targeting"]
    assert "chain_id" not in spec and "rpc" not in spec, "no single-chain spec left"
    import fetch
    assert fetch._budget("subsidy_targeting") >= spec["budget_s"] + config.PROJECT_BY_NAME["Aerodrome"][
        "subsidy_targeting"]["budget_s"]


def test_a1_a_day_with_no_sample_is_excluded_and_named_never_extrapolated():
    from fetch import subsidy_targeting as st
    s0 = int(pd.Timestamp("2026-10-20").timestamp())
    D = 86_400
    pts = [(s0 + 6 * 3600, 10 ** 15, s0 + 60 * D), (s0 + 2 * D + 6 * 3600, 10 ** 15, s0 + 60 * D)]
    wei, have, miss = st.pendle_emissions_by_day(pts, s0, s0 + 4 * D)
    assert have == ["2026-10-20", "2026-10-22"] and miss == ["2026-10-21", "2026-10-23"]
    assert wei == 2 * D * 10 ** 15, "two sampled days, never the two between"
    assert st._day_ranges(["2026-10-21", "2026-10-22", "2026-10-25"]) == "2026-10-21..2026-10-22, 2026-10-25"


def test_a1_pendle_samples_run_before_aerodrome():
    from fetch import subsidy_targeting as st
    order = []
    t = st.SubsidyTargeting(http=object())
    t._aerodrome = lambda *a, **k: order.append("aero")
    t._pendle = lambda *a, **k: order.append("pendle")
    t.run([{"name": "Aerodrome", "subsidy_targeting": {"route": "aerodrome_sugar"}},
           {"name": "Pendle", "subsidy_targeting": {"route": "pendle_gauge"}}], None, FetchOutput())
    assert order == ["pendle", "aero"]


# ======================================================================================== A2 Aerodrome children
def _llama(listing, tvl):
    from fetch.llama import DefiLlama
    t = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731

    class H:
        def get(self, url, params=None):
            if url.endswith("/protocols"):
                return listing
            slug = url.rsplit("/", 1)[1]
            return {"tvl": [{"date": t(d), "totalLiquidityUSD": v} for d, v in tvl[slug]]}
    a = DefiLlama()
    a.http = H()
    return a


def _listing(*rows):
    return [{"id": i, "name": n, "slug": s, "parentProtocol": "parent#aero-protocol"} for i, n, s in rows]


DAYS = [str(d.date()) for d in pd.date_range("2026-09-01", "2026-09-30")]


def test_a2_children_by_id_live_names_quoted_and_an_absent_one_named_partial():
    listing = _listing(("3450", "Aerodrome V1", "v1"), ("8691", "Aero", "aero"), ("3302", "Velodrome V2", "velo"))
    tvl = {"v1": [(d, 10.0) for d in DAYS], "aero": [(d, 1.0) for d in DAYS]}
    a = _llama(listing, tvl)
    out = FetchOutput()
    a.protocol_tvl(dict(config.PROJECT_BY_NAME["Aerodrome"]), None, out)
    fr = out.frame()
    tvl_rows = fr[fr.metric == "protocol_tvl_usd"]
    assert set(tvl_rows["value"]) == {11.0}
    src = tvl_rows["source"].iloc[0]
    assert src.startswith("defillama:PARTIAL[") and "Aerodrome Slipstream (id 4524)" in src
    msg = " ".join(e.message for e in out.log)
    assert "Aero (was Aero Lite)" in msg and "absent: Aerodrome Slipstream (id 4524)" in msg
    import trajectory
    assert trajectory.basis_of("protocol_tvl_usd", src) == trajectory.basis_of("protocol_tvl_usd", "defillama")


def test_a2_a_new_child_is_not_summed_without_a_declared_handover():
    listing = _listing(("3450", "Aerodrome V1", "v1"), ("4524", "Aerodrome Slipstream", "slip"),
                       ("8691", "Aero", "aero"), ("9999", "Aero V3", "aero-v3"))
    tvl = {"v1": [(d, 10.0) for d in DAYS], "slip": [(d, 20.0) for d in DAYS], "aero": [(d, 1.0) for d in DAYS],
           "aero-v3": [(d, 500.0) for d in DAYS]}
    out = FetchOutput()
    _llama(listing, tvl).protocol_tvl(dict(config.PROJECT_BY_NAME["Aerodrome"]), None, out)
    assert set(out.frame().query("metric == 'protocol_tvl_usd'")["value"]) == {31.0}
    assert any("NEW child" in e.message and "Aero V3 (id 9999)" in e.message for e in out.log)


def test_a2_a_declared_handover_takes_effect_only_after_its_overlap_check():
    p = dict(config.PROJECT_BY_NAME["Aerodrome"])
    days = [str(d.date()) for d in pd.date_range("2026-10-01", "2026-10-31")]
    base = {"v1": [(d, 10.0) for d in days], "slip": [(d, 20.0) for d in days], "aero": [(d, 1.0) for d in days]}
    listing = _listing(("3450", "Aerodrome V1", "v1"), ("4524", "Aerodrome Slipstream", "slip"),
                       ("8691", "Aero", "aero"), ("9999", "Aero V3", "aero-v3"))
    ho = {"on": "2026-10-22", "add": {9999: "Aero V3"}, "min_days": 14, "max_median_diff": 0.005}
    p["defillama_protocol_parts"] = dict(p["defillama_protocol_parts"], handover=ho)
    # a real migration: the new child holds ~0 before the switch — the check passes, the new set counts from 10-22
    mig = dict(base, **{"aero-v3": [(d, 0.0 if d < "2026-10-22" else 25.0) for d in days]})
    out = FetchOutput()
    _llama(listing, mig).protocol_tvl(p, None, out)
    v = out.frame().query("metric == 'protocol_tvl_usd'").set_index("date")["value"]
    assert v[pd.Timestamp("2026-10-21")] == 31.0 and v[pd.Timestamp("2026-10-22")] == 56.0
    assert "overlap check passed" in " ".join(e.message for e in out.log)
    # unrelated TVL before the switch: the check fails, the old set stands
    bad = dict(base, **{"aero-v3": [(d, 500.0) for d in days]})
    out2 = FetchOutput()
    _llama(listing, bad).protocol_tvl(p, None, out2)
    assert set(out2.frame().query("metric == 'protocol_tvl_usd'")["value"]) == {31.0}
    assert "overlap check NOT passed" in " ".join(e.message for e in out2.log)


def test_a2_market_share_and_dex_volume_match_children_by_id():
    from fetch import business as b
    meta = {"Aerodrome V1": {"defillamaId": "3450"}, "Aero": {"defillamaId": "8691"}}
    present, absent = b.resolve_ids(meta, config.MARKET_SHARE["Aerodrome"]["own_ids"])
    assert present == {"Aerodrome V1": "Aerodrome V1", "Aero Lite": "Aero"}
    assert absent == ["Aerodrome Slipstream (id 4524)"]
    assert config.BUSINESS_SERIES["Aerodrome"][0]["ids"] == config.MARKET_SHARE["Aerodrome"]["own_ids"]
