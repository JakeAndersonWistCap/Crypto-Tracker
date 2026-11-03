"""P4-Q1 (Jake 2026-11-03): Chainlink's Direct Requests are counted ONCE. fees_usd is the DefiLlama `chainlink`
parent, which already holds `chainlink-requests` (P3-3a), so customer revenue = core + CCIP legacy + VRF + Automation;
Requests are shown as a part of the core, never summed on top. Plain asserts, no network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

SPEC = config.PROJECT_BY_NAME["Chainlink"]["customer_revenue_components"]


def _groups(rows):
    df = pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source"])
    df["date"] = pd.to_datetime(df["date"])
    df["tier"] = 1
    return {k: g.reset_index(drop=True) for k, g in df.groupby(["project", "metric"])}


def test_requests_are_inside_the_core_not_a_plus_line():
    assert "customer_revenue_requests_usd" not in SPEC["plus"]
    assert tuple(SPEC["core_includes"]) == ("customer_revenue_requests_usd",)
    assert SPEC["core"] == "fees_usd"
    assert bw._summed_revenue_legs(SPEC) == ("fees_usd", "customer_revenue_ccip_legacy_usd",
                                             "customer_revenue_vrf_usd", "customer_revenue_automation_premium_usd")


def test_the_sum_counts_requests_once():
    """Fixture day 2026-10-08: fees_usd 1,078,886 = requests 3,544 + staking 1,075,342. With lines of 10 each the
    sum is the parent + 30 — the 3,544 is not added again."""
    rows = [("2026-10-08", "Chainlink", "fees_usd", 1_078_886.0, "defillama:chainlink"),
            ("2026-10-08", "Chainlink", "customer_revenue_requests_usd", 3_544.0, "defillama:chainlink-requests")]
    rows += [("2026-10-08", "Chainlink", m, 10.0, "chainlink_fees:x") for m in SPEC["plus"]]
    g = _groups(rows)
    bw._customer_revenue_views(g)
    v = g[("Chainlink", "customer_revenue_usd")]
    assert v["value"].tolist() == [1_078_916.0]
    assert "customer_revenue_requests_usd" not in v["source"].iloc[0]


def test_a_missing_requests_row_no_longer_refuses_the_day():
    """Requests are not a summed leg, so a day without its row is still summed (it is inside the parent)."""
    rows = [("2026-10-08", "Chainlink", "fees_usd", 100.0, "defillama:chainlink")]
    rows += [("2026-10-08", "Chainlink", m, 1.0, "chainlink_fees:x") for m in SPEC["plus"]]
    g = _groups(rows)
    bw._customer_revenue_views(g)
    assert g[("Chainlink", "customer_revenue_usd")]["value"].tolist() == [103.0]


def test_the_split_still_shows_requests_beside_the_core():
    legs = bw._revenue_legs(config.PROJECT_BY_NAME["Chainlink"])
    assert legs[:2] == ["fees_usd", "customer_revenue_requests_usd"] and len(legs) == len(bw._REVENUE_LEG_LABELS)
    assert "Direct Requests included" in bw._REVENUE_LEG_LABELS[0]
    assert "not added again" in bw._REVENUE_LEG_LABELS[1]
    # the reader maps these audit headers by prefix — both prefixes still match
    assert bw._REVENUE_LEG_LABELS[0].startswith("payments collected at the fee aggregator")
    assert bw._REVENUE_LEG_LABELS[1].startswith("Direct Requests")
