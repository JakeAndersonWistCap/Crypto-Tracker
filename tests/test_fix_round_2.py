"""
tests/test_fix_round_2.py — fix round 2 (Jake 2026-10-13): the mechanisms added this round, each on the REAL dumps
(fixtures/real_*.json) where a dump carries the series, never on a hand-built fixture.

  T2/T4  independent_references.csv loads; tolerance kinds; primary vs cross-check grading
  G1/G2  GEODNET: the mining wallets (b) and the unlock wallets (e) are disjoint, and match the issuer filing
  H1     Aethir's circulating history from its published table (no SERIES BREAK), Plume's mirror from 2026-01-01
  H1/H3  Ethereum's supply history from ultrasound's supply-over-time; gross issuance on those days
  S1     Sky's rewards split by funding period
  S3/S5  issuer mints: Aerodrome's team emission from the Minter formula; Maple's dated mints
  S5     the Aero migration cutover refuses legacy rows loudly
  S6     Hyperliquid FDV on the API total less the Assistance Fund
  T1     an unnamed residual beyond 0.5% is BROKEN on Coverage
  T3     a lost headline is BROKEN unless allow-listed
  §14    the 4Q slope's standard error
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402


_CACHE: dict = {}


def _groups(project: str, extra_rows: list | None = None) -> dict:
    """The build's groups (every read-time view applied: build_workbook._LAST_GROUPS) for one real dump, built the way
    tests/test_alignment.py builds it; cached per (project, extra rows)."""
    import tempfile

    import build_workbook as bw
    import store as sm
    from real_dump import load, to_store
    import real_dump
    key = (project, repr(extra_rows), real_dump.VINTAGE)
    if key in _CACHE:
        return _CACHE[key]
    payload = load(project)
    if extra_rows:
        payload = {**payload, "metrics": payload["metrics"] + extra_rows}
    d = Path(tempfile.mkdtemp())
    mp = pytest.MonkeyPatch()
    try:
        mp.setitem(sys.modules, "recalc", None)
        mp.setattr(bw, "_SCOPE", bw._SCOPE)
        db = d / "metrics.db"
        to_store(payload, db)
        mp.setattr(sm, "DB_PATH", str(db))
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "wb.xlsx", asof=pd.Timestamp(payload["meta"]["asof"]), only=[project])
        finally:
            st.close()
        _CACHE[key] = dict(bw._LAST_GROUPS)
    finally:
        mp.undo()
    return _CACHE[key]


# --------------------------------------------------------------------------------------------- T2 / T4 references
def test_the_reference_table_loads_and_grades():
    import references as rf
    rows = rf.load()
    assert len(rows) == 64 and {   # round 3 B7: + the end-Q1 on-chain Chainlink Reserve row
        "project", "metric", "value", "tolerance", "source_type"} <= set(rows[0])
    assert rf.tolerance("0.5%", "1") == ("rel", 0.005)
    assert rf.tolerance("1pp", "0.5") == ("abs", 0.01)
    assert rf.tolerance("exact", "2860943.76") == ("exact", pytest.approx(0.005))
    assert rf.role("primary (eth_call)") == "primary"
    for st in ("secondary (ultrasound)", "primary-ish (revenue.near.org)", "primary-derived (consensus spec)",
               "derived from contract parameters"):
        assert rf.role(st) == "cross-check", st
    # every row is routed (or says why not)
    assert all((rf.project_of(r), r["metric"]) in rf.ROUTES for r in rows)


def test_a_cross_check_is_never_verified_and_a_primary_miss_is_broken():
    import references as rf
    g = {("X", "m"): pd.DataFrame({"date": pd.to_datetime(["2026-10-12", "2026-10-13"]), "value": [100.0, 100.0],
                                   "source": ["s", "s"]})}
    rf.ROUTES[("X", "a")] = ("last", "m")
    try:
        base = {"project": "X", "metric": "a", "unit": "t", "as_of": "2026-10-13", "source": "", "note": ""}
        ok_sec = rf.check(g, rows=[base | {"value": "100", "source_type": "secondary", "tolerance": "1%"}])[0]
        bad_sec = rf.check(g, rows=[base | {"value": "120", "source_type": "secondary", "tolerance": "1%"}])[0]
        ok_pri = rf.check(g, rows=[base | {"value": "100.5", "source_type": "primary (x)", "tolerance": "1%"}])[0]
        bad_pri = rf.check(g, rows=[base | {"value": "120", "source_type": "primary (x)", "tolerance": "1%"}])[0]
    finally:
        rf.ROUTES.pop(("X", "a"))
    assert ok_sec["status"] == "LIVE-UNVERIFIED" and bad_sec["status"] == "CHECK"
    assert ok_pri["status"] == "VERIFIED" and bad_pri["status"] == "BROKEN"
    assert "ours 100" in bad_pri["detail"] and "reference 120" in bad_pri["detail"]


def test_references_on_the_real_dumps():
    import references as rf
    hl = {r["metric"]: r for r in rf.check(_groups("Hyperliquid"))}
    for m in ("total_staked", "issuer_side_stake", "assistance_fund_balance", "api_total_supply",
              "api_circulating_supply", "future_emissions_remaining"):
        assert hl[m]["status"] == "VERIFIED", hl[m]
    uni = {r["metric"]: r for r in rf.check(_groups("Uniswap"))}
    assert uni["growth_budget_paid_2026"]["status"] == "VERIFIED"
    assert uni["franchiser_recall_inflow"]["status"] == "VERIFIED"      # round 3 B7: the reference is 12,500,001.19
    geod = {r["metric"]: r for r in rf.check(_groups("GEODNET"))}
    assert geod["mining_pool_wallets"]["status"] == geod["team_investor_ecosystem_vendor_publicsale_wallets"]["status"] \
        == "VERIFIED"


# --------------------------------------------------------------------------------------------- G1 / G2 / F2
def test_geodnet_mining_and_unlock_wallets_are_disjoint_and_match_the_filing():
    mining = {a.lower() for a in config.mining_wallets("GEODNET")}
    disc = {a.lower() for a in config.discretionary_wallets("GEODNET")}
    assert mining == {"0xfa5fed5cc2b6dd8f370651d17242c52ed711b14f", "0x8fb9dd00b9a3d893da96d444817d0b77330d5478"}
    assert disc == {"0xca3e874bc4e830796d822f529c29df30302324b2", "0x486559899e96981dfe55c4e6ebf5101a76bfadfa",
                    "0x3a6906e4239f9860c81035c54198df58d892653b", "0x82146cf0f350c241757660fd803c73313b06d75c",
                    "0xcecccb3ee2c208fb58a5a02499e97d4bf041ff6f"}
    for p in config.PROJECTS:
        m = {a.lower() for a in config.mining_wallets(p["name"])}
        d = {a.lower() for a in config.discretionary_wallets(p["name"])}
        assert not (m & d), (p["name"], m & d)
    # the unlock leg no longer reads the treasury sum that holds the mining wallets
    assert "treasury_holding_tokens" not in config.unlock_stock_metrics("GEODNET")


# --------------------------------------------------------------------------------------------- H1
def test_aethir_circulating_history_is_the_published_table_with_no_break():
    import trajectory
    g = _groups("Aethir")
    fp = g[("Aethir", "circulating_supply_first_party")]
    lead = fp[fp["source"] == config.FIRST_PARTY_HISTORY["Aethir"]["source"]]
    assert not lead.empty
    row = lead[pd.to_datetime(lead["date"]) == pd.Timestamp("2026-05-15")]
    assert float(row["value"].iloc[0]) == config.circulating_schedule_value("Aethir", "2026-05")
    assert not trajectory._own_breaks("Aethir", "circulating_supply_first_party", g, pd.Timestamp("2025-01-01"))
    rel = g[("Aethir", "scheduled_release_tokens")]
    on = rel[pd.to_datetime(rel["date"]) == pd.Timestamp("2026-10-01")]
    assert float(on["value"].iloc[0]) == pytest.approx(24_053_550_151 - 23_308_238_268)


def test_plume_mirror_starts_2026_01_01():
    g = _groups("Plume")
    fp = g[("Plume", "circulating_supply_first_party")]
    lead = fp[fp["source"] == config.FIRST_PARTY_HISTORY["Plume"]["source"]]
    assert pd.to_datetime(lead["date"]).min() >= pd.Timestamp("2026-01-01")


def test_ethereum_supply_history_from_ultrasound_on_the_real_dump():
    """The dump predates the route: two ultrasound rows (values as served 2026-10-13) are added the way the run stores
    them; the day before Etherscan's first read takes the NEXT day's 00:00Z point, and its issuance is d(supply) +
    burn under the first-party prefix."""
    extra = [{"date": d, "metric": "total_supply_ultrasound", "value": v, "source": "ultrasound:supply-over-time",
              "tier": 3, "fetched_at": "2026-10-13T19:00:00"}
             for d, v in (("2026-09-27", 122_077_300.0), ("2026-09-28", 122_080_200.0), ("2026-09-29", 122_083_143.33))]
    g = _groups("Ethereum", extra)
    s = g[("Ethereum", "total_supply_protocol")]
    d28 = s[pd.to_datetime(s["date"]) == pd.Timestamp("2026-09-28")]
    assert float(d28["value"].iloc[0]) == 122_083_143.33
    assert str(d28["source"].iloc[0]) == config.SUPPLY_HISTORY["Ethereum"]["source"]
    iss = g[("Ethereum", "gross_issuance_tokens")]
    d = iss[pd.to_datetime(iss["date"]) == pd.Timestamp("2026-09-28")]
    burn = g[("Ethereum", "gross_burn_tokens")]
    b = float(burn[pd.to_datetime(burn["date"]) == pd.Timestamp("2026-09-28")]["value"].iloc[0])
    assert float(d["value"].iloc[0]) == pytest.approx(122_083_143.33 - 122_080_200.0 + b)
    assert str(d["source"].iloc[0]).startswith("derived:d_total_supply_protocol")


# --------------------------------------------------------------------------------------------- S1
def test_sky_rewards_split_by_funding_period():
    assert config.reward_funding("Sky", "2026-08-16")[0] == "treasury"
    assert config.reward_funding("Sky", "2026-08-17")[0] == "revenue"
    assert config.reward_funding("Sky", "2025-05-01")[0] == "mint"
    g = _groups("Sky")
    tot = g[("Sky", "emissions_tokens")]
    parts = sum(float(g[("Sky", m)]["value"].sum()) for m in config.REWARD_FUNDING["Sky"]["to"].values())
    assert parts == pytest.approx(float(tot.drop_duplicates("date", keep="last")["value"].sum()))
    assert config.outflow_metrics("Sky", "c") == ("emissions_mint_funded_tokens",)
    assert config.outflow_metrics("Sky", "e") == ("emissions_treasury_funded_tokens",)


# --------------------------------------------------------------------------------------------- S3 / S5 mints
def test_aerodrome_team_emission_from_the_minter_formula():
    g = _groups("Aerodrome")
    tm = g[("Aerodrome", "issuer_mint_tokens")]
    # the 2026-10-08 flip's rebase (stored dated 2026-10-01, re-dated to its flip in round 3 B4): 228 x (489,618.34 +
    # 8,969,149.54) / 9,772
    row = tm[pd.to_datetime(tm["date"]) == pd.Timestamp("2026-10-08")]
    assert float(row["value"].iloc[0]) == pytest.approx(220_690, rel=1e-4)     # the on-chain transfer: 220,689.5
    zero = tm[pd.to_datetime(tm["date"]) == pd.Timestamp("2025-12-04")]
    assert float(zero["value"].iloc[0]) == 0.0                                  # teamRate 0 (2025-10-09..2026-02-26)


def test_maple_mints_are_their_own_line_and_not_a_return():
    g = _groups("Maple")
    m = g[("Maple", "issuer_mint_tokens")]
    assert float(m["value"].sum()) == pytest.approx(78_241_999.94)
    assert config.fixed_supply_reason("Maple") is None


# --------------------------------------------------------------------------------------------- S5 cutover
def test_the_aero_cutover_refuses_legacy_rows_loudly():
    from fetch.base import FetchOutput
    from fetch.validate import check_migration_cutover
    out = FetchOutput()
    out.frames.append(pd.DataFrame({"date": pd.to_datetime(["2026-10-21", "2026-10-22"]), "project": "Aerodrome",
                                    "metric": "gross_issuance_tokens", "value": [4.2e6, 0.0], "source": "chain",
                                    "tier": 2}))
    check_migration_cutover(out, today="2026-10-22", state={})
    f = out.frame()
    assert list(pd.to_datetime(f["date"])) == [pd.Timestamp("2026-10-21")]
    assert any(e.status == "failed" and "AERODROME MIGRATION CUTOVER" in e.message for e in out.log)
    out2 = FetchOutput()
    out2.frames.append(f.copy())
    check_migration_cutover(out2, today="2026-10-20", state={})
    assert not out2.log
    check_migration_cutover(out2, today="2026-10-23", state={"launched": True})
    assert not out2.log


# --------------------------------------------------------------------------------------------- S6
def test_hyperliquid_fdv_excludes_the_assistance_fund():
    g = _groups("Hyperliquid")
    f = g[("Hyperliquid", "fdv_usd")]
    last = f.assign(d=pd.to_datetime(f["date"])).sort_values("d").iloc[-1]
    px = g[("Hyperliquid", "price_usd")]
    p = float(px[pd.to_datetime(px["date"]) == last["d"]]["value"].iloc[-1])
    assert float(last["value"]) == pytest.approx(p * (998_862_059.4027 - 48_007_021.5047), rel=1e-6)
    assert "total_supply_gross-burn_address_balance" in str(last["source"])


# --------------------------------------------------------------------------------------------- T1 / T3 / §14
def test_an_unnamed_residual_is_broken_on_coverage():
    import coverage_audit as ca
    fr = {"Project": "X", "Statement": "Token flow", "Line": "Status", "Q0": "OK — balances within ±0.5%",
          "Q-1": "BROKEN — residual +2.03% of opening is unexplained by the series; no named cause",
          "Q-2": "OK", "Q-3": "OK", "Q-4": "n/a — x", "Series": ""}

    class WB:
        sheetnames = []
    rows = ca.audit(WB(), [], [fr], X=object())
    st = next(r for r in rows if r["Tab"] == "Flows")
    assert st["Status"] == "BROKEN" and "Q-1" in st["Reason / route / error"]


def test_a_lost_headline_is_broken_unless_allow_listed():
    import coverage_audit as ca
    hits = [("Chainlink", "headline_lost", "A3 Unlock dilution: 0.01 in the previous run (2026-10-12), now n/a"),
            ("GEODNET", "headline_lost", "CP Dilution (e) unlocks (annualised): 0.1 in the previous run, now n/a — "
                                         "outflow_e_tokens (q0): no rows stored"),
            ("Chainlink", "fdv_below_mcap", "not a regression")]
    rows = ca.regression_rows(hits)
    assert [r["Status"] for r in rows] == ["BROKEN", "N/A BY DESIGN"]
    assert "allow-listed" in rows[1]["Reason / route / error"]


def test_the_slope_standard_error():
    import trajectory
    se, why = trajectory.loglinear_growth_se([133.1, 121.0, 110.0, 100.0])
    assert se == pytest.approx(0.0, abs=1e-6) and not why
    se2, _ = trajectory.loglinear_growth_se([100.0, 130.0, 90.0, 100.0])
    assert se2 > 0.1
    assert trajectory.loglinear_growth_se([1.0, 2.0])[0] is None
    assert "4Q growth — standard error (annual; SPEC §12 item 14)" in trajectory.HEAD
