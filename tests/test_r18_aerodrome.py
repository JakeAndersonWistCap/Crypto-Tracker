"""Round 18, item 1 (Jake 2026-11-24) — Aerodrome across the merger:
  1  before 2026-10-22 Aerodrome only: the merged-basis restatement is off, no Velodrome history in any figure
  2  the AERO minted for VELO conversions is a one-off merger issuance on its own line — in the supply, in no dilution
     rate, and left out of the net supply change
  3  revenue before 2026-10-22 is Aerodrome's alone (the stored parent less Velodrome's own listings); combined from it
  4  the 10,000,000 minted 2026-11-19 and burned 2026-11-20 is the Gauge Caps' surplus (Voter.spendSurplus), not the
     merger and not a buyback; every burn on chain reconciles to the token
"""
import pandas as pd
import pytest

import build_workbook as bw
import config
from fetch.base import FetchOutput
from fetch.llama import DefiLlama

A = "Aerodrome"


def _rows(metric, days, values, source, tier=2):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": A, "metric": metric,
                         "value": [float(v) for v in values], "tier": tier, "source": source})


@pytest.fixture
def _aero(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[A]])


def _supply_groups():
    days = list(pd.date_range("2026-10-15", "2026-10-21"))
    return {
        (A, "total_supply"): pd.concat([
            _rows("total_supply", days, [2_002_079_493.92 + 50_000 * i for i in range(7)], "chain:base:token:archive"),
            _rows("total_supply", ["2026-10-22"], [2_119_379_492.92], "aero_v3:supply_once")], ignore_index=True),
        (A, "locked_tokens"): _rows("locked_tokens", days, [1_000_000_000.0] * 7, "chain:base:ve"),
        (A, "gross_issuance_tokens"): _rows("gross_issuance_tokens", ["2026-10-15"], [4_204_996.94],
                                            "chain:base:minter[tail@21bps]"),
        (A, "velo_total_supply_tokens"): _rows("velo_total_supply_tokens", days, [2_640_000_000.0 + 1e6 * i for i in range(7)],
                                               "velo_history:VELO.totalSupply:archive"),
        (A, "velo_locked_tokens"): _rows("velo_locked_tokens", days, [1_300_000_000.0] * 7,
                                         "velo_history:VELO.balanceOf(VotingEscrow):archive"),
        (A, "velo_tail_emission_rate_bps"): _rows("velo_tail_emission_rate_bps", days, [30.0] * 7, "velo_history:tail"),
        (A, "velo_minter_active_period"): _rows("velo_minter_active_period", days,
                                                [1_791_417_600.0] * 1 + [1_792_022_400.0] * 6, "velo_history:period"),
        (A, "velo_minter_weekly_tokens"): _rows("velo_minter_weekly_tokens", days, [5_950_167.0] * 7, "velo_history:weekly")}


# ----- 1. Aerodrome only before the merger -------------------------------------------------------------------------
def test_1_before_the_merger_every_series_is_aerodromes_alone(_aero):
    """A store that HOLDS Velodrome's history (Jake's does: 465 days of twelve series): nothing of it reaches a figure."""
    assert config.VELO_MERGED[A]["restate"] is False
    g = _supply_groups()
    before = {k: v.copy() for k, v in g.items()}
    bw._velo_merged_views(g)
    assert set(g) == set(before)                              # no merged emission, no *_aero_only copy, no VELO line
    for k in before:
        pd.testing.assert_frame_equal(g[k], before[k])
    assert not any("velo_merged" in str(s) for v in g.values() for s in v["source"])
    assert (A, "velo_gauge_emissions_tokens") not in g and (A, config.VELO_MERGED[A]["issuance"]) not in g
    assert A not in bw.VELO_FLIPS and not bw.VELO_MERGED_NOTE


def test_1_the_merged_basis_would_have_added_velo_at_the_ratio(_aero, monkeypatch):
    """What was reversed, by hand: 2,646,000,000 VELO x 0.044 = 116,424,000 AERO on 2026-10-21."""
    monkeypatch.setitem(config.VELO_MERGED[A], "restate", True)
    g = _supply_groups()
    own = float(g[(A, "total_supply")].set_index("date")["value"][pd.Timestamp("2026-10-21")])
    bw._velo_merged_views(g)
    merged = float(g[(A, "total_supply")].set_index("date")["value"][pd.Timestamp("2026-10-21")])
    assert merged - own == pytest.approx(2_646_000_000.0 * 0.044) == pytest.approx(116_424_000.0)


def test_1_the_statement_has_no_velodrome_line_and_dilution_reads_no_velo_series():
    legs = [x[1] for x in config.RULE_IN[A]["flow_in"]]
    assert legs == ["emissions_tokens", "issuer_mint_tokens", "aero_merger_issuance_tokens"]
    assert "velo_merged_issuance_tokens" not in config.RULE_IN[A]["_flow_events"]
    cats = config.OUTFLOW_CATEGORIES[A]
    used = [m for k in "abcdef" for m in (cats.get(k) or ())]
    assert not [m for m in used if "velo" in m or "merger" in m]
    assert not [m for m in config.ABSORPTION_EMISSIONS[A] if "velo" in m or "merger" in m]


# ----- 2. the merger issuance --------------------------------------------------------------------------------------
def test_2_the_merger_issuance_is_the_whole_allocation_on_its_own_line(_aero):
    """The step of the total across the handover on the Aerodrome-only basis: 2,119,379,492.92 − 2,002,379,493.92 =
    116,999,999.00 (the 117,000,000 the new token minted for VELO, less 1 AERO of the day's movement), dated 10-22."""
    g = _supply_groups()
    bw._velo_merged_views(g)
    bw._handover_step_views(g)
    step = g[(A, "aero_merger_issuance_tokens")]
    assert len(step) == 1 and step["date"].iloc[0] == pd.Timestamp("2026-10-22")
    assert float(step["value"].iloc[0]) == pytest.approx(2_119_379_492.92 - 2_002_379_493.92) == pytest.approx(116_999_999.0)
    line = next(x for x in config.RULE_IN[A]["flow_in"] if x[1] == "aero_merger_issuance_tokens")
    assert line[0].startswith("+ merger issuance") and line[2] == "event"
    m = config.AERO_MERGER_ISSUANCE
    assert m["allocation"] == 117_000_000.0 and m["paid"] + m["remaining"] == pytest.approx(m["allocation"], abs=0.01)
    assert all(v <= m["paid"] for v in m["paid_by_day"].values())
    assert list(m["paid_by_day"].values()) == sorted(m["paid_by_day"].values())


def _circ(days, values):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": A, "metric": "circulating_supply_used",
                         "value": values, "tier": 1, "source": "derived:circulating_used[total_supply]"})


def test_2_the_net_supply_change_leaves_the_merger_issuance_out(_aero):
    """By hand: circulating 2,002.0M on 10-20, +50,000 on 10-21, then the step on 10-22 (117.0M of merger issuance +
    300,000 of ordinary mints), +400,000 on 10-23. Closing − opening = 117,750,000; less the one-off = 750,000."""
    days = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    vals = [2_002_000_000.0, 2_002_050_000.0, 2_119_350_000.0, 2_119_750_000.0]
    g = {(A, "circulating_supply_used"): _circ(days, vals),
         (A, "aero_merger_issuance_tokens"): _rows("aero_merger_issuance_tokens", ["2026-10-22"], [117_000_000.0],
                                                   "derived:handover_step[total_supply]")}
    bw._circulating_change_views(g, pd.Timestamp("2026-10-23"))
    ch = g[(A, "circulating_change_tokens")].set_index("date")["value"]
    assert ch[pd.Timestamp("2026-10-22")] == pytest.approx(300_000.0)
    assert ch.sum() == pytest.approx(vals[-1] - vals[0] - 117_000_000.0) == pytest.approx(750_000.0)
    assert bw.ONE_OFF_ISSUANCE_OUT[A] == [(pd.Timestamp("2026-10-22"), 117_000_000.0)]
    assert "117,000,000" in bw._circ_seam_note(config.PROJECT_BY_NAME[A])
    # a yearly rate on it: 750,000 over 3 days, not 117,750,000 (which would read 676% a year of 2.12bn)
    assert ch.sum() * 365 / 3 / vals[-1] == pytest.approx(0.04305, abs=1e-4)


def test_2_a_multi_day_change_across_the_merger_leaves_it_out_too(_aero):
    g = {(A, "circulating_supply_used"): _circ(["2026-10-20", "2026-10-23"], [2_002_000_000.0, 2_119_750_000.0]),
         (A, "aero_merger_issuance_tokens"): _rows("aero_merger_issuance_tokens", ["2026-10-22"], [117_000_000.0], "x")}
    bw._circulating_change_views(g)
    assert g[(A, "circulating_change_tokens")]["value"].tolist() == [pytest.approx(750_000.0)]


def test_2_no_other_project_has_a_one_off_left_out(monkeypatch):
    """The change is Aerodrome's alone: every other project's day-on-day change is the plain difference."""
    assert {k[0] for k, v in config.HANDOVER_STEP.items() if v.get("net_supply_change")} == {A}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Uniswap"]])
    g = {("Uniswap", "circulating_supply_used"): _circ(["2026-10-21", "2026-10-22", "2026-10-23"],
                                                       [100.0, 117_000_100.0, 117_000_150.0]).assign(project="Uniswap"),
         (A, "aero_merger_issuance_tokens"): _rows("aero_merger_issuance_tokens", ["2026-10-22"], [117_000_000.0], "x")}
    bw._circulating_change_views(g)
    assert g[("Uniswap", "circulating_change_tokens")]["value"].tolist() == [117_000_000.0, 50.0]
    assert bw.ONE_OFF_ISSUANCE_OUT.get("Uniswap") == []


# ----- 3. revenue ----------------------------------------------------------------------------------------------------
def _rev_groups(with_velo=True):
    days = ["2023-08-20", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    g = {(A, "fees_usd"): _rows("fees_usd", days, [900.0, 282_297.0, 386_133.0, 323_870.0, 267_808.0, 369_585.0],
                                "defillama", 1),
         (A, "revenue_usd"): _rows("revenue_usd", days, [800.0, 197_279.0, 261_823.0, 229_335.0, 177_904.0, 278_600.0],
                                   "defillama", 1)}
    if with_velo:
        # 10-19 not read; the days from the merger on are stored too and must not be used
        vd = ["2023-08-20", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
        g[(A, "velo_fees_usd")] = _rows("velo_fees_usd", vd, [900.0, 19_827.0, 10_648.0, 4_048.0, 1_796.0],
                                        "defillama:sum(Velodrome services)", 1)
        g[(A, "velo_revenue_usd")] = _rows("velo_revenue_usd", vd, [800.0, 19_327.0, 10_285.0, 2_063.0, 1_036.0],
                                           "defillama:sum(Velodrome services)", 1)
    return g


def test_3_before_the_merger_revenue_is_aerodromes_alone_and_combined_from_it(_aero):
    """DefiLlama's own figures read 2026-11-23: on 2026-10-20 the parent's fees 386,133 hold Velodrome V1 7 + V2 4,994 +
    V3 14,826 = 19,827 -> Aerodrome 366,306; on 10-21 323,870 − (5 + 781 + 9,862) = 313,222. From 10-22 the combined
    figure stands (267,808; 369,585)."""
    g = _rev_groups()
    bw._revenue_before_merger_views(g)
    f = g[(A, "fees_usd")].set_index("date")
    assert f.loc[pd.Timestamp("2026-10-20"), "value"] == pytest.approx(386_133.0 - 19_827.0) == pytest.approx(366_306.0)
    assert f.loc[pd.Timestamp("2026-10-21"), "value"] == pytest.approx(323_870.0 - 10_648.0) == pytest.approx(313_222.0)
    assert f.loc[pd.Timestamp("2026-10-22"), "value"] == 267_808.0 and f.loc[pd.Timestamp("2026-10-23"), "value"] == 369_585.0
    assert f.loc[pd.Timestamp("2026-10-22"), "source"] == "defillama"
    assert "Aerodrome alone before 2026-10-22" in f.loc[pd.Timestamp("2026-10-20"), "source"]
    # the measuring point is unchanged (an annotation): no handover is raised between the two legs
    from fetch.base import _measuring_point
    assert {_measuring_point(s) for s in f["source"]} == {"defillama"}
    # a day inside the span without Velodrome's reading holds no value (never the combined figure); a day before
    # Aerodrome existed is dropped
    assert pd.Timestamp("2026-10-19") not in f.index and pd.Timestamp("2023-08-20") not in f.index
    r = g[(A, "revenue_usd")].set_index("date")
    assert r.loc[pd.Timestamp("2026-10-20"), "value"] == pytest.approx(261_823.0 - 19_327.0)
    assert r.loc[pd.Timestamp("2026-10-22"), "value"] == 177_904.0
    assert "2 day(s) before 2026-10-22" in bw.REVENUE_BEFORE_MERGER_NOTE[(A, "fees_usd")]


def test_3_nothing_moves_on_a_store_without_velodromes_listing(_aero):
    g = _rev_groups(with_velo=False)
    before = {k: v.copy() for k, v in g.items()}
    bw._revenue_before_merger_views(g)
    for k in before:
        pd.testing.assert_frame_equal(g[k], before[k])
    assert not bw.REVENUE_BEFORE_MERGER_NOTE


def test_3_only_the_merged_parents_rows_are_reduced_and_never_below_zero(_aero):
    g = _rev_groups()
    g[(A, "fees_usd")] = pd.concat([
        g[(A, "fees_usd")],
        _rows("fees_usd", ["2026-10-18"], [5_000.0], "own_read:aerodrome_pools", 3)], ignore_index=True)
    g[(A, "velo_fees_usd")] = pd.concat([
        g[(A, "velo_fees_usd")], _rows("velo_fees_usd", ["2026-10-18"], [9_999.0], "defillama:sum(Velodrome services)", 1)],
        ignore_index=True)
    g[(A, "velo_fees_usd")].loc[g[(A, "velo_fees_usd")]["date"] == pd.Timestamp("2026-10-21"), "value"] = 400_000.0
    bw._revenue_before_merger_views(g)
    f = g[(A, "fees_usd")].set_index("date")
    assert f.loc[pd.Timestamp("2026-10-18"), "value"] == 5_000.0                 # another route's row: untouched
    assert f.loc[pd.Timestamp("2026-10-21"), "value"] == 0.0                     # floored, never negative


def test_3_only_aerodrome_declares_it_and_the_three_legs_are_the_fee_family():
    assert set(config.REVENUE_BEFORE_MERGER) == {A}
    spec = config.REVENUE_BEFORE_MERGER[A]
    assert spec["until"] == "2026-10-22" and set(spec["legs"]) == {"fees_usd", "revenue_usd", "holders_revenue_usd"}
    assert set(spec["legs"]) <= set(config.DEFILLAMA_REREAD_METRICS)
    for m in spec["legs"].values():
        assert (A, m) in config.FULL_HISTORY_SERIES and config.METRICS[m]["only_projects"] == (A,)
        assert m in config.metrics_for_project(config.PROJECT_BY_NAME[A])
        assert m not in config.metrics_for_project(config.PROJECT_BY_NAME["Uniswap"])


def test_3_the_fetch_stores_velodromes_three_listings_summed_over_the_whole_history():
    """The route the next run takes (fetch/llama._sum_slugs, config defillama_sum_slugs): Velodrome V1 + V2 + V3 under
    parent#aero-protocol, the other children left out; the whole history is kept whatever the run's window."""
    d = lambda n: pd.Timestamp("2022-06-01") + pd.Timedelta(days=n)  # noqa: E731
    charts = {"velodrome-v1": [(d(i), 5.0) for i in range(1000)],
              "velodrome-v2": [(d(i), 700.0) for i in range(400, 1000)],
              "velodrome-v3": [(d(i), 9_000.0) for i in range(600, 1000)],
              "aerodrome-slipstream": [(d(i), 300_000.0) for i in range(1000)]}
    listing = [{"name": "Velodrome V1", "slug": "velodrome-v1", "parentProtocol": "parent#aero-protocol"},
               {"name": "Velodrome V2", "slug": "velodrome-v2", "parentProtocol": "parent#aero-protocol"},
               {"name": "Velodrome V3", "slug": "velodrome-v3", "parentProtocol": "parent#aero-protocol"},
               {"name": "Aerodrome Slipstream", "slug": "aerodrome-slipstream", "parentProtocol": "parent#aero-protocol"},
               {"name": "Aero Basic", "slug": "aero-basic", "parentProtocol": "parent#aero-protocol"}]
    tried = []

    def chart(slug, dt):
        tried.append((slug, dt))
        return charts[slug]
    ll = DefiLlama.__new__(DefiLlama)
    ll.stored_long = None
    ll._fees_listing = lambda: listing
    ll._summary_chart = chart
    out = FetchOutput()
    specs = config.PROJECT_BY_NAME[A]["defillama_sum_slugs"]
    assert [s["metric"] for s in specs] == ["velo_fees_usd", "velo_revenue_usd", "velo_holders_revenue_usd"]
    assert [s["data_type"] for s in specs] == ["dailyFees", "dailyRevenue", "dailyHoldersRevenue"]
    ll._sum_slugs(config.PROJECT_BY_NAME[A], specs[0], 30, out)
    f = out.frame().set_index("date")
    assert {s for s, _ in tried} == {"velodrome-v1", "velodrome-v2", "velodrome-v3"}
    assert len(f) == 1000 and set(f["metric"]) == {"velo_fees_usd"}             # every day, not the last 30
    assert f.loc[d(10), "value"] == 5.0 and f.loc[d(500), "value"] == 705.0 and f.loc[d(999), "value"] == 9_705.0


# ----- 4. the 10M and the burns --------------------------------------------------------------------------------------
def test_4_the_ten_million_is_the_gauge_caps_surplus_not_the_merger_and_not_a_buyback():
    ev = config.AERO_MINT_THEN_BURN_2026_11
    mech = ev["mechanism"]
    assert mech["merger_related"] is False and mech["is_buyback"] is False
    assert sum(x[2] for x in mech["events"]) == ev["mint"]["to_buyer_safe"] == ev["burn"]["burned"] == 10_000_000.0
    assert mech["stake"]["amount"] == ev["burn"]["burned"]                      # the stake burned is the mint, whole
    spent = (mech["surplus_before"]["base"] + mech["surplus_before"]["chain0"]
             - mech["surplus_after"]["base"] - mech["surplus_after"]["chain0"])
    # the pots fell by the 10M less one day's accrual into them (9,817,521.90 between the two first blocks)
    assert spent == pytest.approx(9_817_521.90, abs=0.01) and 9_000_000 < spent < 10_000_000
    assert ev["mint"]["to_team_splitter"] == pytest.approx(0.05 * ev["mint"]["to_buyer_safe"])
    assert "surplus" in ev["purpose"] and "not stated" in ev["purpose"]


def test_4_every_burn_on_chain_reconciles_to_the_token():
    b = config.AERO_BURNS_ONCHAIN
    assert len(b["burns"]) == 3
    assert sum(x["aero"] for x in b["burns"]) == pytest.approx(b["total"], abs=0.01) == pytest.approx(10_407_815.83, abs=0.01)
    i = b["identity"]
    # genesis + everything the Minter minted (+5% team on top, floored per mint) − every burn = totalSupply
    assert i["genesis"] + i["emissions_minted"] * (1 + i["team_rate"]) - b["total"] == pytest.approx(i["total_supply"], abs=1.0)
    # the two announced burns match to the token; the third matches no announcement
    assert [x["announced"] for x in b["burns"]] == ["174k burned", "233k burned", None]
    leg = config.AERODROME_BURN_LEG["burns"]
    assert [round(x["aero"], 2) for x in leg] == [round(x["aero"], 2) for x in b["burns"][:2]]
    assert [x["burn_tx"] for x in leg] == [x["tx"] for x in b["burns"][:2]]
    assert b["burns"][2]["tx"] == config.AERO_MINT_THEN_BURN_2026_11["burn"]["tx"]
    # aero.xyz's "bought and then burned" 911,157 = the two burns + the 11-18 purchase, which no burn holds
    assert 174_600.64 + 233_215.20 + 503_340.91 == pytest.approx(911_157, abs=1.0)
    assert len(b["not_matching"]) == 2
    # the purchases of 11-04 and 11-11 sit liquid in the Fund's wallet as legacy AERO: 243,612.23 + 294,003.10
    st = b["announced_staked"]
    assert st["matches"] is False and 243_612.23 + 294_003.10 == pytest.approx(st["fund_legacy_balance"][61_224_885], abs=0.01)


def test_4_the_cap_on_the_burned_buyback_stands():
    """R18: Jake kept the 8.18M cap (1,818,679.81 of the 10M a burned buyback, 8,181,320.19 destroyed) until the 10M was
    explained. R20 (Jake 2026-11-24): NONE of the 10M is a buyback — burned buyback of it 0 (was 1,818,679.81), the
    destroyed line 10,000,000 (was 8,181,320.19); the cap itself still applies to every other burn
    (tests/test_r20_decisions.py)."""
    assert config.RULE_IN[A]["flow_destroyed"][0][1] == "foundation_aero_burned_other_tokens"
    assert config.RULE_IN[A]["flow_burn"] == "buyback_burned_tokens"
    assert config.mint_funded_burns(A) == {"2026-11-20": 10_000_000.0}


def test_4_gauge_caps_the_cap_rate_and_what_it_let_through():
    c = config.AERO_GAUGE_CAPS
    assert c["per_day"] == pytest.approx(c["rate_per_second"] * 86_400, abs=0.01)
    assert c["per_week"] == pytest.approx(c["rate_per_second"] * 604_800, abs=0.01)
    assert config.AERO_EMISSIONS_ACCRUAL[A]["rate_metric"] == "emission_rate_current"      # R16 stands: the cap rate
    a = c["at"]
    assert a["minted"] + a["surplus_base"] + a["surplus_chain0"] < a["cap_accrued"]         # the cap is a ceiling
    assert (a["surplus_base"] + a["surplus_chain0"]) / a["cap_accrued"] == pytest.approx(0.3327, abs=1e-3)
    # the cap accrued for 28 days less a few seconds at the base rate
    assert a["cap_accrued"] / c["per_day"] == pytest.approx(28.0, abs=0.01)


# ----- 5. the frozen baseline ----------------------------------------------------------------------------------------
def test_5_the_fixture_dump_holds_nothing_these_views_read():
    """The frozen baseline is built on the 2026-10-13 dump — before the merger, with no Velodrome series and no v3
    total: the restatement switch, the revenue view and the one-off all find nothing there, so no frozen cell moves."""
    import json
    import pathlib
    f = pathlib.Path(config.__file__).resolve().parent / "fixtures" / "real_aerodrome.json"
    if not f.exists():
        pytest.skip("fixtures/real_aerodrome.json not on file")
    d = json.loads(f.read_text(encoding="utf-8"))
    assert str(d["meta"]["asof"])[:10] < config.AERODROME_MIGRATION["cutover_utc"][:10]
    assert not [r for r in d["metrics"] if str(r["metric"]).startswith("velo_")]
    assert not [r for r in d["metrics"] if r["metric"] == "total_supply" and "supply_once" in str(r["source"])]
