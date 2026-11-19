"""ROUND 10, PART B — NEAR (Jake 2026-11-18). SPEC.md "Round 10 — Near".

B1 the v87 switch on 2026-11-05 and gas revenue = the chain's own burn x price (a declared handover, DefiLlama the
cross-check); B2 the three S13 lines; B3 circulating under the rule (lockup.near / contributors.near / treasury.near in
and off the float, the lockups' staked LOCKED in), the lockup list cached and slower; B5 HSP-029 pending and read by
no figure, the manual reference refreshed.
"""
from __future__ import annotations

import ast
import csv
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _g(metric, days, values, source="x", project="Near"):
    days = list(days)
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in vals], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})


def _day(groups, metric):
    return bw._daily(groups.get(("Near", metric)))


# ================================================================================================== B1a the switch
def test_b1a_the_rule_burn_is_70_percent_through_11_04_and_100_percent_from_11_05():
    """v87 went live mid-day on 2026-11-04; Jake: the switch day is 2026-11-05, the first whole day. A protocol-version
    reading of 87 on 11-04 no longer dates the switch — the hand-set day does; nothing is a PARTIAL lower bound."""
    from fetch import history_derive as hd
    from fetch.base import FetchOutput
    pb = config.chain_burn_from_revenue("Near")["protocol_burn"]
    assert pb["switch_on"] == config.NEAR_V87["switch_day"] == "2026-11-05"
    assert (pb["share_before"], pb["share_after"]) == (0.70, 1.00)
    days = pd.date_range("2026-10-25", "2026-11-08")
    rows = []
    for d in days:
        rows += [(d, "Near", "fees_usd", 1_000.0, "defillama"), (d, "Near", "price_usd", 2.0, "coingecko"),
                 # DefiLlama's own label stays at 0.70 x fees: it must not be what the burn follows
                 (d, "Near", "revenue_usd", 700.0, "defillama")]
    rows += [(pd.Timestamp(d), "Near", "near_protocol_version", float(v), "near_rpc:status.protocol_version")
             for d, v in (("2026-11-03", 86), ("2026-11-04", 87), ("2026-11-05", 87), ("2026-11-06", 88))]
    long = pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source"])
    out = FetchOutput()
    hd._chain_burn(out, hd._history(long, None), config.PROJECT_BY_NAME["Near"])
    f = out.frame().set_index("date")
    assert f.loc[pd.Timestamp("2026-11-03"), "value"] == pytest.approx(1_000 * 0.70 / 2.0)
    assert f.loc[pd.Timestamp("2026-11-04"), "value"] == pytest.approx(1_000 * 0.70 / 2.0)     # the mixed day: before
    assert f.loc[pd.Timestamp("2026-11-05"), "value"] == pytest.approx(1_000 * 1.00 / 2.0)
    assert f.loc[pd.Timestamp("2026-11-08"), "value"] == pytest.approx(1_000 * 1.00 / 2.0)
    assert set(f["source"]) == {hd.RULE_SOURCE}                                                  # no PARTIAL day
    # the documented share in force is 1.0 and nothing is left for contract developers
    bs = config.PROJECT_BY_NAME["Near"]["burn_split"]
    assert bs["share_of_fees_burned"] == 1.0 and bs["in_force_from"] == "2026-11-05"
    assert bs["before"]["share_of_fees_burned"] == 0.70 and bs["before"]["to"] == "2026-11-04"


# ================================================================================================== B1b gas revenue
def _burn_groups(header_from="2025-10-07", to="2026-11-10", drop_header_day=None, defillama_revenue=999.0):
    d = pd.date_range("2025-09-01", to)
    hd_days = [x for x in pd.date_range(header_from, to) if x != (pd.Timestamp(drop_header_day) if drop_header_day
                                                                   else None)]
    return {("Near", "price_usd"): _g("price_usd", d, 2.0, "coingecko"),
            ("Near", "gross_burn_tokens"): _g("gross_burn_tokens", d, 70.0,
                                              "derived:near_protocol_rule_x_defillama_fees/price"),
            ("Near", "gross_burn_tokens_bigquery"): _g("gross_burn_tokens_bigquery", hd_days, 100.0,
                                                       "near_bigquery:blocks.total_supply"),
            ("Near", "revenue_usd"): _g("revenue_usd", d, defillama_revenue, "defillama"),
            ("Near", "intents_revenue_usd"): _g("intents_revenue_usd", d, 1_000.0, "defillama:near-intents")}


def test_b1b_gas_revenue_is_the_chains_own_burn_times_price_with_one_declared_seam(scope):
    g = _burn_groups(drop_header_day="2026-03-03")
    bw._chain_burn_revenue_views(g)
    tok, usd = _day(g, "near_chain_burn_tokens"), _day(g, "near_gas_revenue_usd")
    # the WHOLE series is restated on the header burn: every day it is stored, not only the days from the switch
    assert tok[pd.Timestamp("2025-10-07")] == 100.0 and tok[pd.Timestamp("2026-11-10")] == 100.0
    assert tok[pd.Timestamp("2025-10-06")] == 70.0 and tok[pd.Timestamp("2025-09-01")] == 70.0   # the earlier leg
    assert usd[pd.Timestamp("2026-11-10")] == pytest.approx(200.0) and usd[pd.Timestamp("2025-10-06")] == pytest.approx(140.0)
    # one row a day; a day after the seam with no header row is NO VALUE — never the rule in its place
    assert tok.index.is_unique and pd.Timestamp("2026-03-03") not in tok.index and pd.Timestamp("2026-03-03") not in usd.index
    assert bw.CHAIN_BURN_REVENUE_STATE["Near"]["seam"] == "2025-10-07"
    # the two legs are the declared handover's two points, in its order, and never on one day
    from fetch.base import _measuring_point
    view = g[("Near", "near_chain_burn_tokens")].sort_values("date")
    pts = list(dict.fromkeys(_measuring_point(s) for s in view["source"]))
    assert tuple(pts) == tuple(config.declared_handover("Near", "near_chain_burn_tokens")["ordered_points"])
    # the headline revenue = gas (chain) + Intents, on the days both hold
    bw._revenue_total_views(g)
    rev = _day(g, "revenue_total_usd")
    assert rev[pd.Timestamp("2026-11-10")] == pytest.approx(200.0 + 1_000.0)
    assert pd.Timestamp("2026-03-03") not in rev.index


def test_b1b_defillamas_revenue_is_never_read_and_the_seam_follows_the_stored_header_history(scope):
    a, b = _burn_groups(defillama_revenue=999.0), _burn_groups(defillama_revenue=1.0)
    for g in (a, b):
        bw._chain_burn_revenue_views(g)
        bw._revenue_total_views(g)
    pd.testing.assert_series_equal(_day(a, "revenue_total_usd"), _day(b, "revenue_total_usd"))
    assert config.REVENUE_TOTAL["Near"] == ("near_gas_revenue_usd", "intents_revenue_usd")   # was ("revenue_usd", ...)
    # a header history extended back (the BigQuery backfill) moves the seam with it — no date is fixed in code
    c = _burn_groups(header_from="2025-09-10")
    bw._chain_burn_revenue_views(c)
    assert _day(c, "near_chain_burn_tokens")[pd.Timestamp("2025-09-10")] == 100.0
    assert _day(c, "near_chain_burn_tokens")[pd.Timestamp("2025-09-09")] == 70.0
    # no header burn at all: the views say what is missing rather than forming a figure from nothing
    e = {("Near", "price_usd"): _g("price_usd", pd.date_range("2026-01-01", "2026-01-05"), 2.0, "coingecko")}
    bw._chain_burn_revenue_views(e)
    assert ("Near", "near_gas_revenue_usd") not in e
    assert bw._VIEW_BLOCKS[("Near", "near_gas_revenue_usd")].startswith("BLOCKED")


def test_b1c_the_credibility_row_sets_the_chain_burn_beside_defillama_with_no_fixed_share():
    """Ours = the header burn x same-day price over the common days; the reference = DefiLlama's revenue on the same
    days, at the tolerance the row had (15%). A DefiLlama series 30% short reads 30% short — not patched, not scaled."""
    import credibility as cr
    row = config.CREDIBILITY["Near"]["in_revenue"]
    assert "scale" not in row["ref"] and row["ref"]["tol"] == 15.0                # was scale 0.7 on NearBlocks' fees
    d = pd.date_range("2026-10-10", "2026-11-16")
    long = pd.concat([_g("gross_burn_tokens_bigquery", d, 100.0), _g("price_usd", d, 3.0),
                      _g("revenue_usd", d, 210.0)], ignore_index=True)         # DefiLlama at 0.70 of 300
    asof = pd.Timestamp("2026-11-17")
    ours, _d, _how = cr._common_days_sum("Near", None, long, asof, **row["ours"]["args"])
    ref, _d2, _how2 = cr._common_days_sum("Near", None, long, asof, **row["ref"]["args"])
    assert ours == pytest.approx(30 * 300.0) and ref == pytest.approx(30 * 210.0)
    assert (ref / ours - 1) * 100 == pytest.approx(-30.0)
    short = config.NEAR_DEFILLAMA_REVENUE_SHORT
    assert short["days"] == ("2026-11-04", "2026-11-13") and short["defillama_equals_fees_from"] == "2026-11-14"


# ================================================================================================== B2 the S13 lines
def _s13_groups(buyback=900.0, burn_to="2026-11-10", buyback_to="2026-11-08", intents_fees_to="2026-11-09"):
    d = pd.date_range("2026-08-01", burn_to)
    g = {("Near", "price_usd"): _g("price_usd", d, 2.0, "coingecko"),
         ("Near", "gross_burn_tokens_bigquery"): _g("gross_burn_tokens_bigquery", d, 100.0,
                                                    "near_bigquery:blocks.total_supply"),
         ("Near", "fees_usd"): _g("fees_usd", d, 260.0, "defillama"),
         ("Near", "intents_fees_usd"): _g("intents_fees_usd", pd.date_range("2026-08-01", intents_fees_to), 5_000.0,
                                          "defillama:near-intents"),
         ("Near", "intents_revenue_usd"): _g("intents_revenue_usd", d, 1_000.0, "defillama:near-intents")}
    bd = pd.date_range("2026-08-01", buyback_to)
    g[("Near", "actual_buyback_usd")] = _g("actual_buyback_usd", bd, buyback, "derived:actual_buyback_tokens*price_usd")
    g[("Near", "actual_buyback_tokens")] = _g("actual_buyback_tokens", bd, buyback / 2.0, "nearblocks:stats/balance:delta")
    return g


def _s13(g):
    bw._chain_burn_revenue_views(g)
    bw._revenue_total_views(g)
    bw._holder_value_views(g)
    bw._s13_views(g)
    return {role: _day(g, bw.s13_metric("Near", role)) for role in ("fees", "revenue", "holder_revenue")}


def test_b2_the_three_lines_are_fees_revenue_and_burn_plus_held_buyback_on_the_same_days(scope):
    split = config.revenue_split("Near")
    assert (split["fees"]["metric"], split["revenue"]["metric"], split["holder_revenue"]["metric"]) == (
        "fees_total_usd", "revenue_total_usd", "holder_value_revenue_usd")
    lines = _s13(_s13_groups())
    last = {k: s.index.max() for k, s in lines.items()}
    assert set(last.values()) == {pd.Timestamp("2026-11-08")}             # the last day ALL three hold
    day = pd.Timestamp("2026-11-01")
    assert lines["fees"][day] == pytest.approx(260.0 + 5_000.0)
    assert lines["revenue"][day] == pytest.approx(100 * 2.0 + 1_000.0)     # the chain's burn x price + Intents revenue
    assert lines["holder_revenue"][day] == pytest.approx(100 * 2.0 + 900.0)  # the same burn + the held buyback
    # FEES >= REVENUE >= HOLDER REVENUE over the same covered days
    tot = {k: float(s.sum()) for k, s in lines.items()}
    assert tot["fees"] >= tot["revenue"] >= tot["holder_revenue"] > 0
    assert len(set(len(s) for s in lines.values())) == 1
    # the gas burn is the same figure in both lines: revenue − holder revenue is the Intents legs' difference only
    assert (lines["revenue"] - lines["holder_revenue"]).round(6).eq(100.0).all()


def test_b2_a_burn_day_after_the_last_buyback_day_is_not_credited_and_nothing_is_capped(scope):
    g = _s13_groups(buyback=1_500.0)                      # the wallets took in more than DefiLlama's captured revenue
    bw._chain_burn_revenue_views(g)
    bw._revenue_total_views(g)
    bw._holder_value_views(g)
    hv = _day(g, "holder_value_revenue_usd")
    assert hv.index.max() == pd.Timestamp("2026-11-08")   # burn days 11-09 and 11-10 wait for their buyback rows
    assert hv[pd.Timestamp("2026-11-08")] == pytest.approx(200.0 + 1_500.0)
    lines = _s13(g)
    # never capped: where holder revenue is above revenue on the same days, it reads so
    assert float(lines["holder_revenue"].sum()) > float(lines["revenue"].sum())
    # the burn is credited as RETIRED: leakage (i) nets it with the retired buyback (no "distributed" from a burn)
    hv_spec = config.HOLDER_VALUE["Near"]["burn_credited"]
    assert hv_spec["with_buyback"] and hv_spec["usd_metric"] == "near_gas_revenue_usd"
    assert hv_spec["metric"] == config.CHAIN_BURN_REVENUE["Near"]["tokens"]


# ================================================================================================== B3 circulating
def _circ_groups(staked=None, foundation=True, treasury=None, read_on="2026-11-14"):
    d = pd.date_range("2026-11-01", "2026-11-17")
    g = {("Near", "circulating_supply_first_party"): _g("circulating_supply_first_party", d, 1_309_800_000.0,
                                                        "near_bigquery:circulating_supply"),
         ("Near", "total_supply"): _g("total_supply", d, 1_311_900_000.0, "near_rpc:block.header.total_supply"),
         ("Near", "near_lockup_locked_tokens"): _g("near_lockup_locked_tokens", [read_on], 1_908_569.0,
                                                   "near_rpc:lockups@224481004")}
    if foundation:
        g[("Near", "near_foundation_accounts_tokens")] = _g("near_foundation_accounts_tokens", d, 171_059.0,
                                                            "near_rpc:view_account")
    if staked is not None:
        g[("Near", "near_lockup_locked_staked_tokens")] = _g("near_lockup_locked_staked_tokens", [read_on], staked,
                                                             "near_rpc:lockups@224481004")
    if treasury is not None:
        g[("Near", "near_treasury_tokens")] = _g("near_treasury_tokens", d, treasury, "near_rpc:view_account")
    return g


def test_b3a_the_foundation_accounts_are_in_circulating_and_only_idle_locked_is_out(scope):
    assert config.rule_in_legs("Near") == ("near_foundation_accounts_tokens",)
    total, locked = 1_311_900_000.0, 1_908_569.0
    # no staked part read yet: all of LOCKED stays out (said), the two accounts are no longer subtracted (was − 171,059)
    g = _circ_groups()
    bw._near_lockup_views(g)
    s = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.loc["2026-11-17", "value"] == pytest.approx(total - locked)
    assert "not read" in s.loc["2026-11-17", "source"]
    # the staked part of LOCKED earns: it is in; only the idle part is out
    g = _circ_groups(staked=1_200_000.0)
    bw._near_lockup_views(g)
    s = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.loc["2026-11-17", "value"] == pytest.approx(total - (locked - 1_200_000.0))
    # a staked figure above LOCKED can never push circulating above the total
    g = _circ_groups(staked=5_000_000.0)
    bw._near_lockup_views(g)
    assert g[("Near", "circulating_supply_lockup_rule")]["value"].max() == pytest.approx(total)
    # a staked row from ANOTHER read is not this read's: all of LOCKED stays out
    g = _circ_groups(staked=1_200_000.0)
    g[("Near", "near_lockup_locked_staked_tokens")] = _g("near_lockup_locked_staked_tokens", ["2026-11-05"], 1_200_000.0)
    bw._near_lockup_views(g)
    s = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.loc["2026-11-17", "value"] == pytest.approx(total - locked)
    # the rule forms without a foundation-accounts read (they are not subtracted any more)
    g = _circ_groups(foundation=False)
    bw._near_lockup_views(g)
    assert g[("Near", "circulating_supply_lockup_rule")].set_index("date").loc["2026-11-17", "value"] == \
        pytest.approx(total - locked)


def test_b3a_every_basis_the_circulating_view_writes_is_a_declared_handover_point(scope):
    """The change of basis is declared: whatever the view writes — before the first lockup read (with and without a
    foundation-accounts read), with LOCKED alone, with its staked part — the measuring-point guard finds it among
    the declared points, and the handover says the composition widens (never 'none')."""
    import measuring_guard as mg
    import trajectory
    m = "circulating_supply_lockup_rule"
    seen = set()
    for kw in ({}, {"staked": 1_200_000.0}, {"foundation": False}, {"read_on": "2026-11-10"},
               {"read_on": "2026-11-10", "foundation": False}):
        g = _circ_groups(**kw)
        bw._near_lockup_views(g)
        seen |= {trajectory.basis_of(m, s) for s in g[("Near", m)]["source"]}
    assert len(seen) == 4 and seen <= mg._declared("Near", m), seen - mg._declared("Near", m)
    decl = config.declared_handover("Near", m)
    assert decl["declared"] == "2026-11-18" and decl["composition_change"].startswith("WIDENS")


def test_b3a_treasury_and_foundation_accounts_leave_the_float_and_a_missing_leg_is_no_float(scope):
    spec = config.RULE_IN["Near"]
    # R12.2 (Jake 2026-11-19): + the Foundation set's liquid NEAR (near_foundation_liquid_tokens), a third leg
    # (was: ("near_foundation_accounts_tokens", "near_treasury_tokens"), and the sum 171,059 + 1,687,281)
    assert spec["off_float"] == ("near_foundation_accounts_tokens", "near_treasury_tokens",
                                 "near_foundation_liquid_tokens")
    g = _circ_groups(treasury=1_687_281.0)
    g[("Near", "near_foundation_liquid_tokens")] = _g("near_foundation_liquid_tokens",
                                                      pd.date_range("2026-11-01", "2026-11-17"), 1_000_000.0,
                                                      "near_rpc:liquid_set")
    bw._rule_in_views(g)
    off = _day(g, "rule_in_off_float_tokens")
    assert off[pd.Timestamp("2026-11-17")] == pytest.approx(171_059.0 + 1_687_281.0 + 1_000_000.0)
    # treasury.near not stored: no off-float series at all (the float is then n/a — never the float without it)
    g = _circ_groups(treasury=None)
    bw._rule_in_views(g)
    assert ("Near", "rule_in_off_float_tokens") not in g

    class R:
        def D(self, r, m, f):
            return f"{m}@{f}"
    near = config.PROJECT_BY_NAME["Near"]
    assert bw._rule_in_term(R(), 5, near) == "rule_in_off_float_tokens@now"
    # the held buyback keeps leaving the float through its own term, once
    assert config.held_buyback_float_metric("Near") == "buyback_fund_balance"
    assert "buyback_fund_balance" not in spec["off_float"]
    # the two reads that feed the treasury leg: the node read and NearBlocks' history before it, one basis
    reads = [r for r in near["node_api"]["extra_reads"] if r.get("metric") == "near_treasury_tokens"]
    assert len(reads) == 1 and reads[0]["accounts"] == ["treasury.near"] and tuple(reads[0]["fields"]) == ("amount", "locked")
    th = near["nearblocks"]["treasury_history"]
    assert th["metric"] == "near_treasury_tokens" and th["accounts"] == ["treasury.near"]
    assert config.SAME_BASIS[("Near", "near_treasury_tokens")]["canonical"] == "near_rpc:view_account"


def test_b3f_the_token_flow_statement_no_longer_counts_a_release_from_an_account_inside_circulating(scope):
    """lockup.near released 380,804 NEAR on 2025-11-04. With the account inside circulating that is no circulating
    change: the statement is opening + minted − burned and balances without it (it was a '+ released' line before)."""
    # was (R10.B3f): `not config.RULE_IN["Near"].get("flow_outside")` — no outside leg. R13 (Near on Jake's store): the
    # lockup CONTRACTS' net release is the one outside leg; a release from lockup.near / contributors.near still is none
    assert bw._rule_flow("Near")
    assert [m for _lab, m in config.RULE_IN["Near"]["flow_outside"]] == ["near_lockup_net_release_tokens"]
    d = pd.date_range("2025-08-01", "2026-01-31")
    circ = [1_250_000_000.0 + 80_000.0 * i for i in range(len(d))]          # +100,000 minted − 20,000 burned a day
    g = {("Near", "circulating_supply_used"): _g("circulating_supply_used", d, circ),
         ("Near", "gross_issuance_tokens"): _g("gross_issuance_tokens", d, 100_000.0),
         ("Near", "gross_burn_tokens_bigquery"): _g("gross_burn_tokens_bigquery", d, 20_000.0),
         ("Near", "unlock_release_tokens"): _g("unlock_release_tokens", ["2025-11-04"], 380_804.0),
         ("Near", "price_usd"): _g("price_usd", d, 2.0)}
    rows = [r for r in bw.flow_statements(g, pd.Timestamp("2026-01-31")) if r["Project"] == "Near"
            and r["Statement"] == "Token flow"]
    lines = {r["Line"]: r for r in rows}
    assert not any("released from issuer wallets" in k or "returned" in k.split("(")[0] for k in lines)
    resid = lines["Residual (closing − expected)"]
    q0 = next(v for k, v in resid.items() if k.startswith("Q0"))
    assert abs(float(q0)) <= 80_000.0 * 1.01                                 # a day's change at most, never 380,804


def test_b3a_treasury_history_is_nearblocks_daily_close_and_stops_before_the_live_read(monkeypatch):
    """treasury.near's history comes from NearBlocks' daily close (amount + amount_staked), carried over quiet days,
    through the day before the node's own read begins — the store keeps one row a day, so the history never
    overwrites a live reading."""
    import copy
    from fetch import nearblocks, scrape
    from fetch.base import FetchOutput
    monkeypatch.setenv("NEARBLOCKS_API_KEY", "nb-test-key")
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))
    monkeypatch.setattr(nearblocks, "today", lambda: pd.Timestamp("2026-11-25"))
    near = copy.deepcopy(config.PROJECT_BY_NAME["Near"])
    near["nearblocks"] = {k: v for k, v in near["nearblocks"].items()
                          if k not in ("balance_history", "foundation_history")}
    near["nearblocks"]["metrics"] = {}
    seen = []

    class H:
        def get(self, url, params=None, headers=None):
            seen.append(url)
            if url.endswith("/accounts/treasury.near/stats/balance"):
                return {"data": [{"date": "2026-11-20", "amount": str(9 * 10 ** 24), "amount_staked": "0"},
                                 {"date": "2026-11-16", "amount": str(1_671_459 * 10 ** 24), "amount_staked": str(10 ** 24)},
                                 {"date": "2026-11-14", "amount": str(1_653_341 * 10 ** 24), "amount_staked": "0"}]}
            return {"data": []}
    out = FetchOutput()
    nearblocks.NearBlocks(http=H()).run([near], 35, out)
    f = out.frame()
    t = f[f.metric == "near_treasury_tokens"].set_index("date")["value"]
    assert any(u.endswith("/accounts/treasury.near/stats/balance") for u in seen)
    live_from = pd.Timestamp(config.NEAR_TREASURY_LIVE_FROM)
    assert t.index.min() == pd.Timestamp("2026-11-14") and t.index.max() == live_from - pd.Timedelta(days=1)
    assert t[pd.Timestamp("2026-11-15")] == 1_653_341.0                      # a quiet day carries the last close
    assert t[pd.Timestamp("2026-11-16")] == 1_671_460.0                      # amount + amount_staked
    assert set(f[f.metric == "near_treasury_tokens"]["source"]) == {"nearblocks:stats/balance[treasury.near]"}
    from fetch.base import _measuring_point
    same = config.SAME_BASIS[("Near", "near_treasury_tokens")]
    assert _measuring_point("nearblocks:stats/balance[treasury.near]") in same["members"]


def test_b3d_the_reconciliation_names_every_gap_and_none_is_over_two_percent():
    r = config.NEAR_RULE_READ
    ours_min = r["total_supply"] - 1_908_569           # every NEAR but the lockups' LOCKED (the most that is out)
    for ref in ("coingecko", "coinmarketcap", "nearblocks_v3"):
        gap = ours_min / r["references"][ref] - 1
        assert abs(gap) < 0.02 and abs(gap) < 0.002, (ref, gap)
    wallets = r["accounts"]
    buyback = sum(wallets[a]["tokens"] for a in ("fefundsadmin.sputnik-dao.near", "buybacks.multisignature.near",
                                                 "1csfundsadmin.sputnik-dao.near"))
    assert buyback == pytest.approx(4_362_444.24, abs=0.01)
    assert all(not w["earns"] and w["can_move"] for w in wallets.values())


# ================================================================================================== B3c the lockup list
def _page(accounts, nxt):
    return {"data": [{"account_id": a, "created": {"block": {"block_height": "1", "block_timestamp": str(10 ** 18)}},
                      "deleted": {}} for a in accounts], "meta": {"next_page": nxt}}


def test_b3c_a_throttled_list_waits_retry_after_slows_down_and_still_finishes(tmp_path):
    import check_offline_items as coi
    script = [(429, None, "7"), (200, _page(["a.lockup.near"], "c1"), None), (429, None, None),
              (200, _page(["b.lockup.near"], None), None)]
    calls, slept = [], []

    def fake(params, headers):
        calls.append(dict(params))
        return script[len(calls) - 1]
    accts, how = coi.near_lockup_accounts(say=lambda *_: None, fetch=fake, sleep=slept.append, root=tmp_path)
    assert accts == ["a.lockup.near", "b.lockup.near"] and "2 live" in how
    assert slept[0] == 7.0                                  # the server's Retry-After
    assert slept[1] == 2.0                                  # the pace between pages doubled after the 429 (was 1.0)
    assert slept[2] == coi.lockup_list_wait(1, None) == 2.0  # no Retry-After: the backoff
    assert calls[1] == {"limit": 100} and calls[3] == {"limit": 100, "next": "c1"}   # the cursor is resumed, not restarted
    assert (tmp_path / "near-lockups.txt").read_text().split() == accts


def test_b3c_when_nearblocks_never_answers_the_cached_list_is_used_only_while_young(tmp_path):
    import check_offline_items as coi
    done = tmp_path / "near-lockups.txt"
    done.write_text("x.lockup.near\ny.lockup.near\n", encoding="utf-8")
    old = time.time() - 10 * 86400
    os.utime(done, (old, old))                              # 10 days old: past the 1-day reuse, inside the limit
    n = {"calls": 0}

    def always_429(params, headers):
        n["calls"] += 1
        return 429, None, "1"
    accts, how = coi.near_lockup_accounts(say=lambda *_: None, fetch=always_429, sleep=lambda s: None, root=tmp_path)
    assert accts == ["x.lockup.near", "y.lockup.near"]
    assert "CACHED LIST IS USED" in how and "10.0 day(s) old" in how
    assert n["calls"] == 7                                  # six retries, then the fallback — no endless loop
    assert (tmp_path / "near-lockups.partial.json").exists()   # the cursor is kept for the next run
    # older than the limit: no list — the weekly read stores nothing rather than reading a stale set
    too_old = time.time() - (config.NEAR_LOCKUP_LIST_MAX_AGE_DAYS + 1) * 86400
    os.utime(done, (too_old, too_old))
    accts, how = coi.near_lockup_accounts(say=lambda *_: None, fetch=always_429, sleep=lambda s: None, root=tmp_path)
    assert accts == [] and "STOPPED" in how and "CACHED" not in how


def test_b3b_the_staked_part_of_a_lockup_is_read_with_it_and_stored_only_when_whole():
    import check_offline_items as coi
    import near_lockups_weekly as nl
    li = {"lockup_amount": 1_000, "termination_withdrawn_tokens": 0, "lockup_duration": 0, "release_duration": None,
          "lockup_timestamp": None, "transfers": ("disabled", "transfer-poll.near")}
    state = {"owner": "o.near", "lockup": li, "vesting": ("none",), "whitelist": "w.near", "staking_pool": "p.near",
             "deposit": 600, "foundation": None}
    out = coi.near_lockup_from_state(state, account_amount=500, ts=1, buggy=False)
    assert out["locked"] == "1000" and out["staked"] == "600" and out["balance"] == "1100"
    got = {"height": 224_481_004, "unvested": 10.0, "locked": 100.0, "locked_staked": 60.0}
    rows = nl.rows_for(got, pd.Timestamp("2026-11-14"))
    assert dict(zip(rows["metric"], rows["value"])) == {"near_lockup_unvested_tokens": 10.0,
                                                        "near_lockup_locked_tokens": 100.0,
                                                        "near_lockup_locked_staked_tokens": 60.0}
    assert set(rows["source"]) == {"near_rpc:lockups@224481004"}          # one read, one block
    # a read where a lockup carried no staked figure: the staked row is NOT stored (unknown is never 0)
    assert list(nl.rows_for({**got, "locked_staked": None}, pd.Timestamp("2026-11-14"))["metric"]) == list(nl.METRICS)
    # and never a staked part above LOCKED
    assert list(nl.rows_for({**got, "locked_staked": 150.0}, pd.Timestamp("2026-11-14"))["metric"]) == list(nl.METRICS)


# ================================================================================================== B5 HSP-029
class _Poison(dict):
    """Any read of the pending proposal's record fails the test."""

    def _no(self, *a, **k):
        raise AssertionError("a figure read HSP-029 while it is pending")
    __getitem__ = get = items = values = keys = __iter__ = __contains__ = _no


def test_b5a_hsp_029_is_pending_and_no_figure_reads_it(scope, monkeypatch):
    h = config.NEAR_HSP_029
    assert h["in_force"] is False and h["used_by"] == () and h["status"].startswith("PENDING")
    assert h["chain_read"]["max_inflation_rate"] == (1, 40) and h["target_annual_rate_max"] == 0.016
    # the declared issuance ceiling every figure reads is still the one in force on chain
    assert config.PROJECT_BY_NAME["Near"]["issuance_rate_declared"]["annual_rate_max"] == 0.025

    def build():
        g = {**_s13_groups(), **_circ_groups(staked=1_200_000.0, treasury=1_687_281.0)}
        bw._near_lockup_views(g)
        bw._rule_in_views(g)
        lines = _s13(g)
        return {k: float(v.sum()) for k, v in lines.items()}, float(_day(g, "circulating_supply_lockup_rule").iloc[-1])
    before = build()
    monkeypatch.setattr(config, "NEAR_HSP_029", _Poison())
    assert build() == before
    # and nothing but its own definition names it: no module reads config.NEAR_HSP_029, config reads it nowhere
    mods = [ROOT / "config.py", *sorted(ROOT.glob("*.py")), *sorted((ROOT / "fetch").glob("*.py")),
            *sorted((ROOT / "adapters").glob("*.py"))]
    for f in dict.fromkeys(mods):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        reads = [n for n in ast.walk(tree)
                 if (isinstance(n, ast.Name) and n.id == "NEAR_HSP_029" and isinstance(n.ctx, ast.Load))
                 or (isinstance(n, ast.Attribute) and n.attr == "NEAR_HSP_029")]
        assert not reads, f"{f.name} reads NEAR_HSP_029 (line {reads[0].lineno})"


def test_b5a_the_chain_ceiling_is_read_each_run_as_a_ratio_or_not_at_all():
    from fetch.base import FetchOutput
    from fetch.near import NearNode
    near = config.PROJECT_BY_NAME["Near"]
    read = next(r for r in near["node_api"]["extra_reads"] if r.get("kind") == "near_protocol_config")
    assert config.METRICS[read["metric"]]["view_only"] and ("Near", read["metric"]) in config.NOT_SHOWN

    def node(payload):
        n = NearNode()
        n._call = lambda api, body=None: (payload, "fake")
        return n
    out = FetchOutput()
    node({"result": {"protocol_version": 88, "max_inflation_rate": [1, 40]}})._protocol_config(
        near, near["node_api"], read, out, pd.Timestamp("2026-11-18"))
    f = out.frame()
    assert list(f["metric"]) == ["near_max_inflation_rate"] and f["value"].iloc[0] == pytest.approx(0.025)
    # the ramp's end state would read 0.016 — the reading dates the step, no figure takes it
    out = FetchOutput()
    node({"result": {"protocol_version": 90, "max_inflation_rate": [2, 125]}})._protocol_config(
        near, near["node_api"], read, out, pd.Timestamp("2027-06-01"))
    assert out.frame()["value"].iloc[0] == pytest.approx(0.016)
    for bad in ({"result": {"max_inflation_rate": "1/40"}}, {"result": {"max_inflation_rate": [1, 0]}}, {"result": {}}):
        out = FetchOutput()
        node(bad)._protocol_config(near, near["node_api"], read, out, pd.Timestamp("2026-11-18"))
        assert out.frame().empty and out.log[-1].status != "ok"


def test_b5b_the_wallet_breakdown_reference_is_the_sum_of_the_three_wallets_read_on_2026_11_18():
    with open(ROOT / "manual_references.csv", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["project"] == "Near" and r["row"] == "in_buyback_wallets"]
    assert len(rows) == 1
    r = rows[0]
    assert r["read_on"] == "2026-11-18" and r["url"] == "https://revenue.near.org"
    parts = (2_028_316.66, 1_825_446.71, 555_348.27)        # fefundsadmin, buybacks.multisignature, 1csfundsadmin
    assert float(r["value"]) == pytest.approx(sum(parts), abs=0.02)       # was 3,786,229.6 (2026-10-07)
    assert float(r["tol_pct"]) == 5.0                                      # the tolerance is the row's own, unchanged


# ================================================================================================== B4 BigQuery
def test_b4_the_header_burn_history_is_dry_run_only_until_approved_then_read_once(tmp_path, monkeypatch):
    """The header burn before its first stored day: the supply-flows SQL over [from, first stored day − 1]. Until Jake
    approves it, every run logs the dry-run estimate and reads nothing; approved, it reads once, under the backfill
    budget rules, and never again."""
    import copy
    from types import SimpleNamespace
    from fetch import near_bigquery as nbq
    from fetch.base import FetchOutput
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp("2026-11-19"))

    class Bq:
        class QueryJobConfig:
            def __init__(self, **kw):
                self.__dict__.update(kw)
                self.dry_run = kw.get("dry_run", False)
        ScalarQueryParameter = staticmethod(lambda k, t, v: (k, t, v))
        ArrayQueryParameter = staticmethod(lambda k, t, v: (k, t, tuple(v)))

    class Client:
        def __init__(self):
            self.dry, self.real = [], []

        def query(self, sql, job_config=None):
            p = {k: v for k, _, v in job_config.query_parameters}
            win = (str(p["d0"]), str(p["d1"]))
            if job_config.dry_run:
                self.dry.append(win)
                return SimpleNamespace(total_bytes_processed=250_000_000)
            self.real.append(win)
            rows = [{"day": d, "burn_near": 800.0, "issuance_near": 100_000.0} for d in pd.date_range(*win)]
            return SimpleNamespace(result=lambda: rows, total_bytes_billed=250_000_000)
    spec = copy.deepcopy(config.PROJECT_BY_NAME["Near"]["near_bigquery"])
    # R12.5 (Jake 2026-11-19): approved on his dry run (289,127,592 bytes) under the 1 GB condition — the read itself
    # is tested from the not-approved state here, the approval in tests/test_r12_near.py
    # (was: spec["supply_flows"]["history"] == {"from": "2025-07-15", "approved": False})
    assert spec["supply_flows"]["history"]["from"] == "2025-07-15"
    assert spec["supply_flows"]["history"]["approved"] is True
    spec["supply_flows"]["history"]["approved"] = False
    stored = _g("gross_burn_tokens_bigquery", pd.date_range("2025-10-07", "2026-11-18"), 900.0,
                "near_bigquery:blocks.total_supply")
    st = {"ledger": {}, "topup_bytes": {"bytes": 3e6, "on": "2026-11-19", "source": "t"}}
    a = nbq.NearBigQuery(client=Client(), bq=Bq, stored_long=stored, cache_file=tmp_path / "c.json",
                         csv_dir=tmp_path / "none")
    out = FetchOutput()
    a._supply_flows_history(a._client_obj, spec, st, out, "Near")
    cl = a._client_obj
    assert cl.dry == [("2025-07-15", "2025-10-06")] and cl.real == [] and out.frame().empty   # not approved: dry run only
    assert "0.25 GB" in out.log[-1].message and "supply_flows_history_from" not in st and st["ledger"] == {}
    spec["supply_flows"]["history"]["approved"] = True
    out = FetchOutput()
    a._supply_flows_history(cl, spec, st, out, "Near")
    f = out.frame()
    burn = f[f.metric == "gross_burn_tokens_bigquery"]
    assert cl.real == [("2025-07-15", "2025-10-06")] and len(burn) == 84
    assert burn["date"].max() == pd.Timestamp("2025-10-06") and set(burn["value"]) == {800.0}
    assert st["supply_flows_history_from"] == "2025-07-15" and sum(st["ledger"].values()) == 250_000_000
    a._supply_flows_history(cl, spec, st, FetchOutput(), "Near")
    assert len(cl.real) == 1                                                                 # once
    # a query larger than the month's remaining budget is not run (the standing rule holds for the history too)
    st2 = {"ledger": {"2026-11": int(spec["monthly_budget_bytes"])}, "topup_bytes": st["topup_bytes"]}
    a2 = nbq.NearBigQuery(client=Client(), bq=Bq, stored_long=stored, cache_file=tmp_path / "d.json",
                          csv_dir=tmp_path / "none")
    out = FetchOutput()
    a2._supply_flows_history(a2._client_obj, spec, st2, out, "Near")
    assert a2._client_obj.real == [] and out.frame().empty and "supply_flows_history_from" not in st2
