"""ROUND 22, PLUME lane (Jake 2026-11-25, item 5) — Plume active again: off hold, no holder value (a declared measured
0), the ecosystem / foundation unlock leg of (e) on the Flows statement, its declared n/a reasons, its three revenue
lines, and the insider share of the stake marked "not verified". Config-level tests: no build is run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402
import rule3  # noqa: E402

LEG = "plume_ecosystem_foundation_unlock_tokens"


def _daily(key) -> pd.Series:
    """An EVIDENCE_LADDER entry spread over its days, exactly as build_workbook._evidence_views spreads it."""
    days: dict = {}
    for per in config.EVIDENCE_LADDER[key]["periods"]:
        span = pd.date_range(per["from"], per["to"])
        for d in span:
            days[d] = days.get(d, 0.0) + float(per["value"]) / len(span)
    return pd.Series(days).sort_index()


def test_plume_is_off_hold_and_judged():
    assert "Plume" not in config.ARCHETYPES_NO_CHANGE_R20
    assert config.ARCHETYPES_NO_CHANGE_R20.get("World Mobile") == "parked"
    assert reader.parked_reason("Plume") == "" and "Plume" not in config.CREDIBILITY_PARKED
    assert config.PROJECT_BY_NAME["Plume"]["archetypes"] == [1]          # token utility unchanged: A1 only
    assert "Plume" not in config.FROZEN_PROJECTS                         # not frozen this round


def test_no_holder_value_is_a_declared_measured_zero():
    why = config.holder_value_absent("Plume")
    assert why and "gas, governance, staking and ecosystem incentives" in why and "2026-11-23" in why
    z = config.DECLARED_ZERO[("Plume", "holder_value_revenue_usd")]
    assert z["since"] == "2000-01-01" and why in z["why"]
    assert config.buyback_route("Plume")["route"] == "none"
    assert config.PROJECT_BY_NAME["Plume"]["fee_split"]["share_to_buyback"] == 0.0


def test_the_ecosystem_foundation_leg_totals_match_the_dataset():
    s = _daily(("Plume", LEG))
    # the dataset's own end totals (2028-01-22): Community & Ecosystem 3.9bn, Foundation 1.3bn; the listing-day values
    # (cliff + that day's linear: 1,152,162,408.76 and 151,049,270.07) are the opening circulating at TGE
    assert abs(s.sum() + 1_152_162_408.76 + 151_049_270.07 - 5_200_000_000) < 1.0
    assert s.index.min() == pd.Timestamp("2025-01-22") and s.index.max() == pd.Timestamp("2028-01-21")
    assert abs(s[pd.Timestamp("2026-10-13")] - (2_162_408.759 + 1_049_270.073)) < 0.01


def test_the_four_legs_explain_plumes_q0_residual_on_the_fixture():
    # fixture (as of 2026-10-13) Q0: opening 5,969,793,823, closing 6,606,142,167 (circulating_supply_used, the Flows
    # tab of the before build); the window is build_workbook._flow_window(asof, 0)
    a, b = bw._flow_window(pd.Timestamp("2026-10-13"), 0)
    legs = sum(float(_daily(("Plume", m))[lambda x: (x.index > a) & (x.index <= b)].sum())
               for m in ("insider_unlock_tokens", LEG))
    opening, closing = 5_969_793_823, 6_606_142_167
    residual_before = closing - opening - 338_142_857                     # insider leg alone: +5.00% of opening
    assert abs(residual_before / opening - 0.05) < 0.001
    assert abs((closing - opening - legs) / opening) <= bw.FLOW_RESIDUAL_LIMIT


def test_the_leg_is_on_unlocks_e_and_on_the_flows_statement():
    assert config.outflow_metrics("Plume", "e") == ("insider_unlock_tokens", LEG)
    assert LEG in [m for _l, m in config.FLOW_RELEASE_LEGS["Plume"]]
    assert config.METRICS[LEG]["only_projects"] == ("Plume",) and config.METRICS[LEG]["view_only"]
    assert config.PLUME_TOKEN_FLOW_NOTE == ""
    e = config.EVIDENCE_LADDER[("Plume", LEG)]
    assert e["step"] == "AGGREGATOR" and e["read_on"] and e["url"].endswith("plume-mainnet")


def test_declared_reasons_cover_plumes_rule3_cells():
    dest = config.phase2_attribute_text("Plume", "destination")
    assert "0x6CBb" not in dest and "0x60be7a0c" in dest
    for text in (dest, "n/a — no buyback programme declared (config fee_split)",
                 "n/a — no non-circulating wallet set on this project (config.unlock_stock_metrics)"):
        assert rule3.is_suspect(text)                                     # a rule-3 wording ...
        e = config.declared_na_reason("Plume", text)                      # ... declared, with its source and date
        assert e and e["source"] and e["read_on"] == "2026-11-23"


def test_three_revenue_lines_are_split():
    s = config.revenue_split("Plume")
    assert (s["fees"]["metric"], s["revenue"]["metric"], s["holder_revenue"]["metric"]) == (
        "fees_usd", "revenue_usd", "holder_value_revenue_usd")
    assert config.REVENUE_FROM_FEES["Plume"]["revenue"] == "revenue_usd"
    lab = config.revenue_tier_label("Plume", "Revenue (S13), 90 days")      # a number's label, not 'not yet split'
    assert lab and lab[0] == config.REVENUE_TIER_SUFFIX["revenue"]
    assert config.revenue_tier("Plume")[0] == "revenue" and "RewardDistributor" in config.revenue_tier("Plume")[1]


def test_insider_share_of_stake_is_not_verified_after_a_search():
    nv = config.not_verified("Plume", "Insider share of stake")
    assert nv and nv["searched"] and nv["read_on"] == "2026-11-23"
    assert config.not_verified("Plume", "Net holder yield") is None
