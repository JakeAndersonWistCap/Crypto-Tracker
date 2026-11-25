"""ROUND 13 — NEAR ON JAKE'S STORE (Jake 2026-11-19, his run of Round 12). SPEC.md "Round 13 — Near on Jake's store".

(c) the step of a declared handover of NEAR's circulating figure is never a net supply change: the seam is measured on
both bases on one day; (a) the lockup contracts' net release is a line of the token-flow statement, formed on one basis;
(b) the regression guard on net holder yield is the unfinished payout ledger — no allow-list entry answers it.
No network: every series is made here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import coverage_audit  # noqa: E402

NEAR = config.PROJECT_BY_NAME["Near"]
REL = "near_lockup_net_release_tokens"
ASOF = pd.Timestamp("2026-11-19")
DAYS = pd.date_range("2026-02-01", ASOF)
FA = 171_059.0                     # lockup.near + contributors.near
MINT, BURN = 100_000.0, 20_000.0   # a day
CLIFF_DAY, CLIFF = pd.Timestamp("2026-06-12"), 12_000_000.0
# the weekly lockup reads: (day, LOCKED, of which staked by its lockup — None: the read does not store it)
READS = [("2026-10-11", 2_155_000.0, None), ("2026-10-26", 2_036_000.0, None), ("2026-11-05", 1_971_000.0, None),
         ("2026-11-14", 1_908_000.0, None), ("2026-11-19", 1_872_000.0, 1_084_000.0)]


def _g(metric, days, values, source="x"):
    days = list(days)
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": "Near", "metric": metric,
                         "value": [float(v) for v in vals], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


def _total(d) -> float:
    return 1_300_000_000.0 + (MINT - BURN) * (pd.Timestamp(d) - DAYS[0]).days


def _own_locked(d) -> float:
    """What NEAR's own table leaves out: 5,000 NEAR released a day, and one cliff."""
    d = pd.Timestamp(d)
    return 15_400_000.0 - 5_000.0 * (d - DAYS[0]).days - (CLIFF if d >= CLIFF_DAY else 0.0)


def _groups(reads=READS, own_until="2026-11-18"):
    own_days = [d for d in DAYS if d <= pd.Timestamp(own_until)]
    g = {("Near", "total_supply"): _g("total_supply", DAYS, [_total(d) for d in DAYS], "coingecko"),
         ("Near", "total_supply_protocol"): _g("total_supply_protocol", DAYS, [_total(d) for d in DAYS], "near_rpc:h"),
         ("Near", "circulating_supply_first_party"): _g(
             "circulating_supply_first_party", own_days, [_total(d) - _own_locked(d) - FA for d in own_days],
             "near_bigquery:circulating_supply"),
         ("Near", "near_foundation_accounts_tokens"): _g("near_foundation_accounts_tokens", DAYS, FA),
         ("Near", "near_lockup_locked_tokens"): _g("near_lockup_locked_tokens", [r[0] for r in reads],
                                                   [r[1] for r in reads], "near_rpc:lockups@1"),
         ("Near", "gross_issuance_tokens"): _g("gross_issuance_tokens", DAYS[1:], MINT),
         ("Near", "gross_burn_tokens_bigquery"): _g("gross_burn_tokens_bigquery", DAYS[1:], BURN),
         ("Near", "price_usd"): _g("price_usd", DAYS, 2.0)}
    st = [r for r in reads if r[2] is not None]
    if st:
        g[("Near", "near_lockup_locked_staked_tokens")] = _g("near_lockup_locked_staked_tokens", [r[0] for r in st],
                                                             [r[2] for r in st], "near_rpc:lockups@1")
    return g


@pytest.fixture
def built(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [NEAR])

    def build(**kw):
        g = _groups(**kw)
        bw._near_lockup_views(g)
        bw._market_cap_views(g)
        bw._circulating_change_views(g, ASOF)
        return g
    return build


def _s(g, metric):
    return bw._daily(g[("Near", metric)])


# ==================================================================== (c) a handover's step is not a net supply change
def test_c_the_figure_in_use_still_names_only_the_series_but_its_basis_is_the_point_of_that_series(built):
    g = built()
    used = g[("Near", "circulating_supply_used")]
    assert set(used["source"]) == {"derived:circulating_used[circulating_supply_lockup_rule]"}   # one source on every day
    days = list(pd.to_datetime(used["date"]))
    basis = bw._circ_used_basis(g, "Near", days, [str(x) for x in used["source"]])
    by_day = dict(zip(days, [bw._circ_basis_name(b) for b in basis]))
    names = config.NEAR_CIRC_SEAM["points"]
    assert by_day[pd.Timestamp("2026-10-10")] == names[list(names)[1]]       # NEAR's own table + the two accounts
    # R22 (Jake 2026-11-25, item 1a): every lockup read is one basis — all of LOCKED out, staked or not (was R13: the
    # 2026-10-11..11-18 reads on point 2, the staked read of 11-19 on point 3)
    assert by_day[pd.Timestamp("2026-10-11")] == by_day[pd.Timestamp("2026-11-18")] == names[list(names)[4]]
    assert by_day[pd.Timestamp("2026-11-19")] == names[list(names)[4]]
    assert len(set(names.values())) == 5 == len(config.NEAR_CIRC_RULE_POINTS)          # R22 (was 4)
    # a project that declares no such series keeps its row's own source as its basis
    assert bw._circ_used_basis(g, "Uniswap", days[:2], ["a", "b"]) == ["a", "b"]


def test_c_the_net_supply_change_leaves_both_steps_out_and_loses_no_day(built):
    g = built()
    circ, chg = _s(g, "circulating_supply_used"), _s(g, "circulating_change_tokens")
    lo = ASOF - pd.Timedelta(days=90)
    raw = circ[ASOF] - circ[lo]
    # the two seams, each measured on both bases on its own day
    steps = {d: st for d, _a, _b, st in bw.CIRC_SEAM_STEPS["Near"]}
    # R22: the staked part is never counted in, so 2026-11-19 is no seam (was R13: a step of +1,084,000 that day)
    assert set(steps) == {pd.Timestamp("2026-10-11")}
    assert steps[pd.Timestamp("2026-10-11")] == pytest.approx(_own_locked("2026-10-11") - 2_155_000.0)   # −15,000
    q0 = chg[(chg.index > lo) & (chg.index <= ASOF)]
    assert len(q0) == 90 and not bw.CIRC_CHANGE_GAPS["Near"]                 # every day dated: nothing is a gap
    assert q0.sum() == pytest.approx(raw - sum(steps.values()), abs=1.0)
    # = what was minted − burned + what left the lockups, on each basis: 255,000 (own table) + 283,000 (LOCKED)
    releases = (_own_locked(lo) - _own_locked("2026-10-11")) + (2_155_000.0 - 1_872_000.0)
    assert q0.sum() == pytest.approx(90 * (MINT - BURN) + releases, abs=1.0)
    # the seam days themselves: the day's change on the basis of the day before, never the step
    assert chg[pd.Timestamp("2026-11-19")] == pytest.approx((MINT - BURN) + (1_908_000.0 - 1_872_000.0))
    assert chg[pd.Timestamp("2026-10-11")] == pytest.approx((MINT - BURN) + 5_000.0)
    assert bw.CIRC_SEAM_STEPS_Q0["Near"] == bw.CIRC_SEAM_STEPS["Near"]


def test_c_with_the_link_switched_off_a_seam_day_holds_no_change_and_the_window_is_not_measured(built, monkeypatch):
    """The strict reading of PR1.S8 is one switch away (a decision for Jake): then each seam is a gap, never a step."""
    assert config.NEAR_CIRC_SEAM["link_net_supply_change"] is True
    monkeypatch.setitem(config.NEAR_CIRC_SEAM, "link_net_supply_change", False)
    g = built()
    chg = _s(g, "circulating_change_tokens")
    seams = [pd.Timestamp("2026-10-11")]                    # R22: one seam (was + 2026-11-19)
    assert not any(d in chg.index for d in seams)
    assert [d for d, _why in bw.CIRC_CHANGE_GAPS["Near"]] == seams and not bw.CIRC_SEAM_STEPS["Near"]
    assert chg[chg.index >= pd.Timestamp("2026-10-01")].abs().max() < 200_000.0   # no 1,084,000 step on any day


def test_c_before_the_first_staked_reading_nothing_is_restated_and_the_staked_part_is_never_0_under_the_rule(built):
    g = built()
    rule = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    # R22: every read leaves all of LOCKED out — with or without its staked part (was R13: the staked read of the as-of
    # day counted its staked part in, total − (1_872_000.0 − 1_084_000.0))
    for day, locked, _st in READS:
        row = rule.loc[pd.Timestamp(day)]
        assert row["value"] == pytest.approx(_total(day) - locked)
        assert "R22" in row["source"] and "idle LOCKED" not in row["source"]


def test_c_a_later_read_without_its_staked_part_is_a_gap_never_a_change(monkeypatch):
    """After a read WITH the staked part, a read without it cannot say what is idle: the day goes back to the
    all-LOCKED-out point, the idle basis cannot be formed on it (unknown, never 0) and no change is formed."""
    monkeypatch.setattr(bw, "_SCOPE", [NEAR])
    reads = [("2026-10-11", 2_060_000.0, None), ("2026-11-05", 1_971_000.0, 1_084_000.0), ("2026-11-14", 1_908_000.0, None)]
    g = _groups(reads=reads)
    asof = pd.Timestamp("2026-11-18")
    for k in list(g):
        g[k] = g[k][pd.to_datetime(g[k]["date"]) <= asof]
    bw._near_lockup_views(g)
    bw._market_cap_views(g)
    bw._circulating_change_views(g, asof)
    chg = _s(g, "circulating_change_tokens")
    # R22: a read with its staked part and one without are the same basis — no gap, the change is formed (was R13:
    # 2026-11-14 a gap, never a change)
    assert pd.Timestamp("2026-11-14") in chg.index and pd.Timestamp("2026-11-05") in chg.index
    assert pd.Timestamp("2026-11-14") not in [d for d, _why in bw.CIRC_CHANGE_GAPS["Near"]]
    assert _s(g, REL)[pd.Timestamp("2026-11-14")] == pytest.approx(1_971_000.0 - 1_908_000.0)


# ============================================================================ (a) the lockup contracts' net release
def test_a_the_release_is_the_fall_of_what_circulating_leaves_out_on_one_basis(built):
    g = built()
    rel = _s(g, REL)
    assert rel[pd.Timestamp("2026-06-11")] == pytest.approx(5_000.0)
    assert rel[CLIFF_DAY] == pytest.approx(CLIFF + 5_000.0)                  # the cliff is a release, on its day
    assert rel[pd.Timestamp("2026-10-11")] == pytest.approx(5_000.0)         # the seam day: on NEAR's own table still
    assert rel[pd.Timestamp("2026-10-26")] == pytest.approx(2_155_000.0 - 2_036_000.0)
    assert rel[pd.Timestamp("2026-10-27")] == 0.0                            # a carried read releases nothing
    assert rel[ASOF] == pytest.approx(1_908_000.0 - 1_872_000.0)             # NOT + the 1,084,000 counted in that day
    assert rel.sum() == pytest.approx((_own_locked(DAYS[0]) - _own_locked("2026-10-11")) + (2_155_000.0 - 1_872_000.0))
    assert all(str(x).startswith("derived:d_") for x in g[("Near", REL)]["source"])   # a differenced stock
    assert config.METRICS[REL]["view_only"] and config.METRICS[REL]["only_projects"] == ("Near",)
    assert config.METRICS[REL]["sanity_min"] < 0                             # signed: back behind a lock is negative


def test_a_near_going_back_behind_a_lock_is_a_negative_release(built):
    reads = [("2026-10-11", 2_060_000.0, None), ("2026-10-26", 2_100_000.0, None)]
    g = built(reads=reads)
    assert _s(g, REL)[pd.Timestamp("2026-10-26")] == pytest.approx(-40_000.0)


def _statement(g):
    rows = [r for r in bw.flow_statements(g, ASOF) if r["Project"] == "Near" and r["Statement"] == "Token flow"]
    by = {r["Line"]: r for r in rows}
    q = lambda line, k: next(v for key, v in by[line].items() if key.startswith(k))   # noqa: E731
    return by, q


def test_a_the_cliff_quarter_balances_with_the_leg_and_is_broken_without_it(built, monkeypatch):
    g = built()
    by, q = _statement(g)
    leg = next(k for k in by if "lockup contracts" in k)
    assert by[leg]["Series"] == REL
    assert q(leg, "Q-1") == pytest.approx(CLIFF + 90 * 5_000.0)
    opening = q("Opening circulating", "Q-1")
    assert abs(q("Residual (closing − expected)", "Q-1")) < 1.0 and str(q("Status", "Q-1")).startswith("OK")
    assert CLIFF / opening > bw.FLOW_RESIDUAL_LIMIT == 0.005                 # the tolerance is what it was
    # without the leg the same quarter is a residual beyond ±0.5% with no named cause — what Jake's Q-4 read
    monkeypatch.setitem(config.RULE_IN["Near"], "flow_outside", ())
    by, q = _statement(g)
    assert q("Residual (closing − expected)", "Q-1") == pytest.approx(CLIFF + 90 * 5_000.0, abs=1.0)
    assert str(q("Status", "Q-1")).startswith("BROKEN")


def test_a_the_quarter_of_the_handovers_reads_a_series_break_with_each_step_and_no_flow(built):
    g = built()
    by, q = _statement(g)
    leg = next(k for k in by if "lockup contracts" in k)
    assert q(leg, "Q0") == pytest.approx(255_000.0 + 283_000.0)              # no step in the release leg
    resid, status = q("Residual (closing − expected)", "Q0"), str(q("Status", "Q0"))
    # R22: one step (was R13: the two steps, 1_084_000.0 − 15_000.0)
    assert resid == pytest.approx(-15_000.0, abs=1.0)
    assert status.startswith("CHECK") and "SERIES BREAK" in status
    assert "-15,000" in status and "2026-10-11" in status and "+1,084,000" not in status
    # a quarter with no handover inside names none
    assert "SERIES BREAK" not in str(q("Status", "Q-1"))


def test_a_the_old_leg_is_superseded_and_a_release_from_the_two_accounts_is_still_no_line():
    rs = config.RULE_IN["Near"]
    assert [m for _lab, m in rs["flow_outside"]] == [REL] == [config.NEAR_CIRC_SEAM["release"]]
    assert "flow_outside_when_covered" not in rs and rs["flow_outside_was"] == "near_lockup_release_tokens"
    assert "near_foundation_accounts_tokens" in config.rule_in_legs("Near")  # inside circulating: no release line


# ============================================================================ (b) the regression guard, (0) the ledger
def test_b_the_guard_reads_broken_on_a_headline_that_lost_its_number_and_no_allowance_answers_it():
    detail = ("CP NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e a: -0.02044 in the previous run "
              "(2026-11-18), now n/a — outflow (b) = near_mpc_operator_payouts_tokens has no value in this window: "
              "BLOCKED — outflow (b) is not formed: near_mpc_operator_payouts_tokens: no rows stored")
    assert config.regression_allowance("Near", detail, on="2026-11-19") is None
    rows = coverage_audit.regression_rows([("Near", "headline_lost", detail)], on="2026-11-19")
    assert [r["Status"] for r in rows] == ["BROKEN"] and rows[0]["Tab"] == "Regression guard"
    # the leg is in the sum by decision (R12.1), and no entry of the allow-list is Near's after its 2026-10-20 one
    assert config.outflow_metrics("Near", "b") == ("near_mpc_operator_payouts_tokens",)
    live = [a for a in config.REGRESSION_ALLOWLIST if a["project"] == "Near"
            and pd.Timestamp(a["until"]) >= pd.Timestamp("2026-11-19")]
    assert live == []
    assert config.NEAR_R13_STORE["ledger"] == {"unread": 28, "of": 85, "stored": False}


def test_figures_of_jakes_store_add_up():
    st = config.NEAR_R13_STORE
    was, now = st["net_supply_change"]["was"], st["net_supply_change"]["now"]
    assert was[0] - sum(st["seam_steps"].values()) == pytest.approx(now[0], abs=1.0)
    assert now[1] == pytest.approx(was[1] * now[0] / was[0], rel=1e-6)       # one circulating divides both
    later = st["later_export"]["net_supply_change"]                          # the same two steps on his next export
    assert later["was"][0] - sum(st["seam_steps"].values()) == pytest.approx(later["now"][0], abs=1.0)
    assert st["later_export"]["ledger_rows"] == 197
    fall = st["q4_locked_fall"]
    assert fall["from"] - fall["to"] == st["q4_residual"]["release_leg"]
    assert st["q4_residual"]["was"][0] - st["q4_residual"]["release_leg"] == st["q4_residual"]["now"][0]
    assert sum(fall["cliffs"].values()) < fall["from"] - fall["to"]
