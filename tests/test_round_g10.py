"""
tests/test_round_g10.py — Round G10, "fix what we already have" (Jake 2026-10-21), SPEC §19 "G10". Every fix uses series
already stored: stored_but_unshown (1), status and label fixes (2), class C logic (3), Ethereum's settlement handover
(4), run reliability (5), the Review Queue triage (6).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import check_offline_items as coi  # noqa: E402
import config  # noqa: E402
import coverage_audit as ca  # noqa: E402


def _frame(project, metric, days, values, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric, "value": values,
                         "tier": 1, "source": source})


# ============================================================================================ 1 stored but not shown
def _store(path: Path, rows):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT, tier INTEGER)")
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,1)", rows)
    con.commit()
    con.close()


def _csv(path: Path, rows):
    keys = ["Tab", "Column", "Key", "Project", "Value now", "Status", "Reason / route / error", "Formula as built"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})


def test_1_stored_but_unshown_finds_the_declared_basis_and_skips_cross_checks(tmp_path, monkeypatch, capsys):
    days = [d.date().isoformat() for d in pd.date_range("2026-07-10", "2026-10-13")]
    _store(tmp_path / "metrics.db",
           [(d, "GEODNET", "pool_release_tokens", 1.0, "balance_flow:x") for d in days]
           + [(d, "Sky", "locked_tokens", 1.0, "chain:x") for d in days]
           + [(d, "Hyperliquid", "fees_usd", 1.0, "defillama") for d in days])
    _csv(tmp_path / "coverage_audit.csv", [
        # GEODNET: the cell reads gross_issuance_tokens; the store holds its declared basis — a HIT
        {"Tab": "A4 Permanent Burn", "Column": "Issuance Δ3m", "Project": "GEODNET", "Status": "MISSING",
         "Value now": "n/a — gross_issuance_tokens (q0): gap", "Reason / route / error": "MISSING [D — ROUTE NOT FILLED] "
         "— gross_issuance_tokens — supply_units (miner count) is sourced, at which point issuance can be modelled"},
        # a cross-check against the series it checks is never a stand-in
        {"Tab": "A3 Revenue Buyback", "Column": "Tokens locked — cross-check (protocol dashboard)", "Project": "Sky",
         "Status": "MISSING", "Reason / route / error": "MISSING [D — ROUTE NOT FILLED] — locked_tokens_dashboard — x"},
        # one input stored and the other absent: waiting on the absent one, not a hit
        {"Tab": "A1 Infrastructure", "Column": "Fees ÷ issuance at Q1 (−3m window)", "Project": "Hyperliquid",
         "Status": "MISSING", "Value now": "BLOCKED — unequal coverage: fees (fees_usd, q1) covers 90 days of a 90-day "
         "window; issuance (gross_issuance_tokens, q1) covers 0 days of a 90-day window",
         "Reason / route / error": "MISSING [C — NEEDS HISTORY] — x"},
    ])
    monkeypatch.setenv("TOKEN_METRICS_COVERAGE_CSV", str(tmp_path / "coverage_audit.csv"))
    import store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "metrics.db"))
    coi.stored_but_unshown()
    out = capsys.readouterr().out
    assert "1 hit(s)" in out and "GEODNET" in out and "STORED as pool_release_tokens" in out
    assert "locked_tokens_dashboard" not in out and "Hyperliquid (" not in out


def test_1_concept_keys_never_take_a_dashboard_or_a_constant_price_basis():
    stored = {"protocol_tvl_usd", "buyback_fund_balance", "customer_revenue_usd_defillama"}
    assert coi._concept_keys("Maple", "protocol_tvl_constant_price_usd", stored) == []
    assert coi._concept_keys("Chainlink", "buyback_fund_balance_dashboard", stored) == []
    assert coi._concept_keys("Aethir", "customer_revenue_usd", stored) == []


def test_1_geodnet_a4_reads_the_measured_release_and_near_emissions_come_from_the_mint():
    assert bw._a4_iss_metric("GEODNET") == "pool_release_tokens"
    assert bw._a4_iss_metric("Uniswap") == "gross_issuance_tokens"
    assert config.TREASURY_SHARE_SPLIT["Near"].get("emissions") is True


def test_1_near_emissions_view_is_line_a(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Near"]])
    days = pd.date_range("2026-10-01", periods=3)
    groups = {("Near", "gross_issuance_tokens"): _frame("Near", "gross_issuance_tokens", days, [100.0, 100.0, 100.0],
                                                        "near_rpc:x"),
              ("Near", "near_treasury_mint_tokens"): _frame("Near", "near_treasury_mint_tokens", days[:1], [10.0],
                                                            "near_rpc:t")}
    bw._treasury_split_views(groups)
    em = bw._daily(groups[("Near", "emissions_tokens")])
    assert list(em.values) == [90.0, 90.0, 90.0]


def test_1_plume_revenue_is_applicable_and_labelled():
    assert "revenue_usd" in config.metrics_for_project(config.PROJECT_BY_NAME["Plume"])
    why = config.REVENUE_FROM_FEES["Plume"]["why"]
    assert "protocol captures 100% of gas" in why and "0x07DC8589" in why and "12.5% bridged" in why


# ============================================================================================ 2 status and labels
def test_2b_settled_answer_is_class_e():
    cls, _ev = ca.missing_class({"Reason / route / error": "x", "Project": "World Mobile"},
                                {"reason": "NOT TRACKED BY DEFILLAMA — checked 2026-09-30", "suggestion":
                                 "Nothing to add here. ... otherwise this metric has no source and that is the settled "
                                 "answer."})
    assert cls == ca.MISSING_CLASSES[4]


def test_2c_no_such_flow_cells_are_not_listed_in_missing_triage():
    src = Path(coi.__file__).read_text(encoding="utf-8")
    body = src[src.index("def missing_triage"):src.index("_WIN_FROM = ")]
    assert 'why.startswith("0 — no such flow")' not in body


def test_2d_a_failed_validation_is_amber_with_its_result():
    row = {"source": "derived:dex+nft+p2p[rebuild; validation FAILED: 12 complete week(s): 1 week(s) outside]",
           "status": "ok", "n_points": 30, "measuring_points": ("derived:dex+nft+p2p",)}
    band, why = bw.confidence_for("Ethereum", "settlement_rebuilt_usd", row, pd.Timestamp("2026-10-13"))
    assert band == "AMBER" and "VALIDATION FAILED: 12 complete week(s)" in why


def test_2e_a_first_epoch_not_closed_is_pending_amber():
    band, why = bw.confidence_for("Pendle", "subsidy_fee_cover_pools",
                                  {"status": "pending", "note": "first epoch closes 2026-11-03 — x | gap"},
                                  pd.Timestamp("2026-10-13"))
    assert band == "AMBER" and why.startswith("first epoch closes 2026-11-03")
    assert ca.DISHONEST_NA.search("n/a — subsidy_fee_cover_pools (now): PENDING — first epoch closes 2026-11-03")


def test_2f_a_cell_reading_a_modelled_cell_is_modelled():
    rows = [{"Tab": "A1 Infrastructure", "Column": "Yield", "Project": "Hyperliquid", "Status": "MODELLED",
             "Reason / route / error": "MODELLED — x", "_raw": "=1", "_addr": "A1 Infrastructure!D7"},
            {"Tab": "CP Comparability", "Column": "Staker yield (A1)", "Project": "Hyperliquid", "Status": "VERIFIED",
             "Reason / route / error": "", "_raw": "='A1 Infrastructure'!$D$7*2", "_addr": "CP Comparability!E9"},
            {"Tab": "Master", "Column": "x", "Project": "Hyperliquid", "Status": "LIVE-UNVERIFIED",
             "Reason / route / error": "", "_raw": "='CP Comparability'!E9", "_addr": "Master!B3"}]
    ca.mark_modelled(rows)
    assert [r["Status"] for r in rows] == ["MODELLED"] * 3
    assert "reads A1 Infrastructure 'Yield', a MODELLED cell" in rows[1]["Reason / route / error"]


# ============================================================================================ 3 class C logic
def test_3c_a_short_leg_is_dated_from_its_own_window():
    groups = {("Aethir", "customer_revenue_usd"): _frame("Aethir", "customer_revenue_usd",
                                                         pd.date_range("2026-06-15", "2026-10-05", freq="7D"), 1.0, "x")}
    why = ("BLOCKED — unequal coverage: Q0 (customer_revenue_usd, q0) covers 90 days of a 90-day window; Q2 "
           "(customer_revenue_usd, q2) covers 0 days of a 90-day window")
    cls, ev = ca._short_legs(why, "Aethir", groups, pd.Timestamp("2026-10-13"))
    assert cls == ca.MISSING_CLASSES[2] and "valid from 2027-03-11" in ev and "q2 window" in ev


def test_3c_a_declared_incomplete_denominator_is_not_history():
    rows = [{"Tab": "CP Comparability", "Column": "SUBSIDY COVER", "Project": "Fluid", "Status": "N/A BY DESIGN",
             "Reason / route / error": "BLOCKED — unequal coverage: the incentive denominator is incomplete — (b) "
                                       "FLUID rewards are read from the Ethereum Merkle distributors only",
             "Formula as built": ""}]
    ca.honest_statuses(rows, [], {}, pd.Timestamp("2026-10-13"))
    assert rows[0]["Status"] == "MISSING" and rows[0]["Reason / route / error"].startswith(
        f"MISSING [{ca.MISSING_CLASSES[3]}]")


def test_3a_sky_q0_share_is_applied_day_by_day(monkeypatch):
    months = pd.to_datetime(["2026-07-31", "2026-08-31", "2026-09-30"])
    monkeypatch.setitem(bw._LAST_GROUPS, ("Sky", "net_protocol_surplus_usd"),
                        _frame("Sky", "net_protocol_surplus_usd", months, [31.0, 31.0, 30.0], "sky_accounting:x"))
    eff, why = bw.day_by_day_share("Sky", pd.Timestamp("2026-07-15"), pd.Timestamp("2026-10-13"), 0,
                                   pd.Timestamp("2026-10-13"))
    # July 31 days at 7.5%, August 16 days at 7.5% + 15 at 27.5%, September 30 days at 27.5%: one unit a day
    expect = (31 * 0.075 + 16 * 0.075 + 15 * 0.275 + 30 * 0.275) / 92
    assert eff == pytest.approx(expect)
    assert "7.5% 2026-07-01..2026-08-16" in why and "27.5% 2026-08-17..2026-09-30" in why
    # a window wholly inside one split is not applied day by day
    assert bw.day_by_day_share("Sky", pd.Timestamp("2026-08-20"), pd.Timestamp("2026-09-30"), 0,
                               pd.Timestamp("2026-10-13")) is None


def test_3b_stage2_burn_is_zero_before_its_first_row_only(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Sky"]])
    monkeypatch.setattr(config, "DECLARED_ZERO", {k: v for k, v in config.DECLARED_ZERO.items() if k[0] == "Sky"})
    g = _frame("Sky", "sky_stage2_burn_tokens", pd.to_datetime(["2026-09-10", "2026-09-14"]), [0.0, 2_860_943.76],
               "chain:x")
    groups = {("Sky", "sky_stage2_burn_tokens"): g}
    bw._declared_zero_views(groups, pd.Timestamp("2026-10-13"))
    s = bw._daily(groups[("Sky", "sky_stage2_burn_tokens")])
    assert s[pd.Timestamp("2026-09-09")] == 0.0 and s[pd.Timestamp("2026-09-14")] == 2_860_943.76
    assert pd.Timestamp("2026-09-11") not in s.index           # never after the first stored row
    src = groups[("Sky", "sky_stage2_burn_tokens")]
    assert "first burn 2026-09-14" in str(src[src["date"] == pd.Timestamp("2026-09-09")]["source"].iloc[0])


# ============================================================================================ 4 Ethereum handover
def test_4_rebuild_hands_over_from_08_26_unscaled_whatever_the_validation(monkeypatch):
    days = pd.date_range("2026-05-28", "2026-10-12")
    legs = {m: _frame("Ethereum", m, days, 1.0e9, f"src:{m}")
            for m in ("dex_volume_usd", "nft_volume_usd", "p2p_transfer_volume_usd")}
    csv_days = days[days <= "2026-08-25"]
    # the CSV 10% below the rebuild every other week: the ±10% weekly gate fails
    csvv = [3.0e9 / (1.2 if (d.dayofyear // 7) % 2 else 1.0) for d in csv_days]
    groups = {("Ethereum", k): v for k, v in legs.items()}
    groups[("Ethereum", "settlement_volume_usd")] = _frame("Ethereum", "settlement_volume_usd", csv_days, csvv,
                                                           "artemis:settlement_volume (CSV export, Jake, 2026-09-30)")
    bw._settlement_rebuild_views(groups)
    assert bw.SETTLEMENT_VALIDATION["Ethereum"]["status"] == "FAILED"
    g = groups[("Ethereum", "settlement_volume_usd")]
    ext = g[g["date"] > "2026-08-25"]
    assert ext["date"].min() == pd.Timestamp("2026-08-26") and (ext["value"] == 3.0e9).all()   # never scaled
    assert "DECLARED HANDOVER from 2026-08-26" in ext["source"].iloc[0]
    assert "rebuild +6.8% vs Artemis over 2026-05-28..08-25; 11/12 weeks within ±10%" in ext["source"].iloc[0]
    pts = {bw._measuring_point(s) for s in g["source"].astype(str)}
    spans = {p: (g[g["source"].map(bw._measuring_point) == p]["date"].min(),
                 g[g["source"].map(bw._measuring_point) == p]["date"].max()) for p in pts}
    assert bw.handover_refusal("Ethereum", "settlement_volume_usd", pts, spans) is None


# ============================================================================================ 5 run reliability
class _NoHttp:
    def get(self, *a, **k):
        raise AssertionError("the CCIP API was called for days ccip_value already holds")


class _Daily:
    def ever(self, _k):
        return True

    def due(self, _k, _d):
        return True

    def done(self, _k, _d):
        pass


def test_5a_ccip_messages_read_ccip_values_walk(tmp_path, monkeypatch):
    from fetch import business as bz
    monkeypatch.setattr(bz, "today", lambda: pd.Timestamp("2026-10-20"))
    state = {"days": {"2026-10-19": {"stage": "closed", "n_msgs": 1234}, "2026-10-18": {"stage": "walked", "n_msgs": 999},
                      "2026-10-17": {"stage": "gap"}}}
    p = tmp_path / ".cache" / "ccip"
    p.mkdir(parents=True)
    (p / "state.json").write_text(json.dumps(state))
    b = bz.Business(http=_NoHttp(), daily=_Daily(), ccip_root=str(tmp_path))
    rows, src, detail = b._ccip("business:Chainlink:ccip_messages")
    assert dict(rows) == {pd.Timestamp("2026-10-18"): 999.0, pd.Timestamp("2026-10-19"): 1234.0}
    assert src == "ccip_api:v2/messages[mainnet, count by sendTimestamp day]" and "ccip_value's walk state" in detail


def test_5a_each_business_source_has_its_own_budget():
    from fetch import business as bz
    b = bz.Business(http=_NoHttp(), daily=_Daily())
    b._spent["ccip"] = bz.SOURCE_BUDGET_S["ccip"]
    with pytest.raises(RuntimeError, match="time budget exhausted — source ccip"):
        with b._budget("ccip"):
            pass
    with b._budget("growthepie"):
        pass
    assert sum(bz.SOURCE_BUDGET_S.values()) <= 900


def test_5b_chainlink_noncirc_scans_have_their_own_tier():
    import fetch
    tiers = {s["key"]: s["own_tier"] for s in config.PROJECT_BY_NAME["Chainlink"]["log_scans"]}
    assert tiers["noncirc_out"] == tiers["noncirc_in"] == "explorer_noncirc@Chainlink"
    assert "explorer_noncirc@Chainlink" in [t[0] for t in fetch.TIER_ORDER]
    assert fetch.TIER_BUDGET_S["explorer_noncirc@Chainlink"] >= 300


def test_5d_measuring_guard_newer_prints_a_verdict():
    src = (ROOT / "measuring_guard.py").read_text(encoding="utf-8")
    assert "def live_check" in src and "VERDICT: " in src and "shutil.copyfile(db_path, db)" in src


def test_6_review_triage_is_registered():
    assert coi.review_triage in coi.CHECKS


def test_5d_a_declared_zero_is_not_an_undeclared_basis():
    import measuring_guard as mg
    before = {"bases": {"Sky|sky_stage2_burn_tokens": ["chain:ethereum:burn_logs"]}, "headlines": {}}
    after = {"bases": {"Sky|sky_stage2_burn_tokens": ["chain:ethereum:burn_logs", "declared:zero"]}, "headlines": {}}
    assert mg.compare(before, after) == []
    after["bases"]["Sky|sky_stage2_burn_tokens"].append("probe:x")
    assert mg.compare(before, after)                        # anything else still fails
