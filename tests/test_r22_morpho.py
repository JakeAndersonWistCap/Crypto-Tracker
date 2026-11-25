"""ROUND 22 — MORPHO: EVERY VESTING CONTRACT (Jake 2026-11-25, item 3): "the same rule applies to every vesting contract.
A claim of tokens that have already vested is not new supply. Vesting counts as dilution on its schedule. [...] List every
vesting contract on chain with its end date and the amount still unvested. Show what's left of Morpho's dilution."

1. The contracts on chain (config.MORPHO_VESTING_CONTRACTS, read 2026-11-25 at block 26,492,109): the DAO Vester (ended
   2025-06-11, 0 unvested), the Labs vester (to 2028-10-02, 3,039,455.91 unvested), the Association vester (never funded).
2. Vesting on its schedule is dilution: morpho_vesting_unvested_tokens (each award's DssVest accrual) is a release leg —
   its fall enters (e) on the day it vests.
3. A claim of vested tokens is never a release, for every award of every contract — netted only out of a release leg
   that holds the czar's wallet.
4. The founders' relock tranche is out of the modelled relock (their MIP1 awards vested on chain 2025-06-11): Cohort 3's
   67M is the only modelled schedule left.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import free_float  # noqa: E402

C = config.MORPHO_VESTING_CONTRACTS
SWC = config.CIRCULATING_ONCHAIN["Morpho"]["subtract_when_complete"]


def _rows(metric, days, values, source="chain:sum(x)"):
    return pd.DataFrame({"date": list(days), "project": "Morpho", "metric": metric, "value": values, "source": source,
                         "tier": 2, "fetched_at": "2026-11-25T00:00:00Z"})


def _scope(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Morpho"]])


# ============================================================================== 1. every vesting contract on chain
def test_1_three_contracts_their_end_and_what_is_still_unvested():
    assert set(C) == {"dao", "labs", "association"}
    dao, labs, assoc = C["dao"], C["labs"], C["association"]
    assert len(dao["awards"]) == 23 and sum(a[5] for a in dao["awards"]) == pytest.approx(279_882_199.73, abs=0.05)
    assert sum(a[6] for a in dao["awards"]) == pytest.approx(229_525_631.72, abs=0.05)
    assert max(a[4] for a in dao["awards"])[:10] == dao["end"] == "2025-06-11" and dao["unvested"] == 0.0
    assert dao["vested_unclaimed"] == pytest.approx(279_882_199.73 - 229_525_631.72, abs=0.01)
    assert len(labs["awards"]) == 62 and max(a[4] for a in labs["awards"])[:10] == labs["end"] == "2028-10-02"
    assert sum(a[5] for a in labs["awards"]) == pytest.approx(21_227_398.12, abs=0.05)
    # the model reproduces the contract's own accrual: 3,039,455.91 unvested at the read (block 26,492,109, ~12:00 UTC);
    # 00:00 UTC of the day is ~half a day of vesting earlier
    assert config.morpho_vesting_unvested_on("2026-11-25", ("labs",)) == pytest.approx(labs["unvested"], abs=5_000)
    assert config.morpho_vesting_unvested_on("2026-11-26", ("labs",)) < labs["unvested"]
    # the Association vester was never funded: nothing vests through it, it is in no leg
    assert assoc["allowance"] == 0.0 and assoc["claimed"] == 0.0 and assoc["leg"] is None and assoc["awards"] == ()
    # the six restricted awards = the founders' 15.2% (R21b), all vested
    assert sum(a[5] for a in dao["awards"] if a[0] in (10, 13, 14, 15, 17, 18)) == 152_000_000.0


# ============================================================================== 2. vesting on its schedule is (e)
def test_2_the_dao_vester_vests_nothing_after_its_end_the_labs_vester_vests_daily():
    f = config.morpho_vesting_unvested_on
    assert f("2025-06-12", ("dao",)) == 0.0 and f("2026-11-20", ("dao",)) == 0.0
    assert f("2025-06-11", ("dao",)) > 0          # the last half-day before fin 13:00 UTC
    # the 90 days to the 11-20 export: 613,973.95 vested on the Labs vester's schedule
    assert f("2026-08-22") - f("2026-11-20") == pytest.approx(613_973.95, abs=0.05)
    assert f("2028-10-03") == 0.0
    assert "morpho_vesting_unvested_tokens" in config.unlock_stock_metrics("Morpho")
    assert "morpho_vesting_unvested_tokens" not in config.CIRCULATING_ONCHAIN["Morpho"]["subtract"]   # IN circulating
    assert config.SCHEDULE_LOCK_LEGS["Morpho"]["metric"] == "morpho_vesting_unvested_tokens"


def test_2_the_days_vesting_enters_the_release_on_its_day(monkeypatch):
    _scope(monkeypatch)
    monkeypatch.setitem(SWC, "in_force", False)
    days = pd.date_range("2026-11-10", periods=4)
    groups = {("Morpho", "total_supply"): _rows("total_supply", days, 1e9, "chain"),
              ("Morpho", "noncirculating_holding_tokens"): _rows("noncirculating_holding_tokens", days, 2.8e8)}
    bw._modelled_lock_views(groups)
    bw._schedule_lock_leg_views(groups)
    v = groups[("Morpho", "morpho_vesting_unvested_tokens")]
    assert v["source"].iloc[0] == "declared:vester_schedule[2022-12-24..2028-10-02]"
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", config.unlock_stock_metrics("Morpho"))
    rel = bw._daily(groups[("Morpho", "unlock_release_tokens")])
    f = config.morpho_vesting_unvested_on
    for d in days[1:]:
        want = 67_000_000 / 730 + (f(d - pd.Timedelta(days=1)) - f(d))      # Cohort 3 + the Labs vester's vesting
        assert rel[d] == pytest.approx(want, rel=1e-9)


# ============================================================================== 3. a claim is never a release
def test_3_every_award_s_claims_and_only_out_of_a_leg_that_holds_the_czar(monkeypatch):
    claims = config.MORPHO_VESTING_CLAIMS["claims"]
    keys = {aw for _d, aw, _t, _x in claims}
    assert {"dao#10", "dao#13", "dao#15", "dao#17", "dao#18"} <= keys          # R21b's founders
    assert {"dao#1", "dao#2", "dao#3", "dao#5", "dao#6", "dao#7", "dao#11", "dao#21"} <= keys   # contributors (R22)
    assert any(k.startswith("labs#") for k in keys)
    monkeypatch.setitem(SWC, "in_force", False)
    held = config.morpho_vest_claims_by_leg()
    assert set(held) == {"noncirculating_holding_tokens"}       # the Labs czar's leg is not a release leg: not netted
    assert config.morpho_founder_claims_by_day()["2025-12-05"] == pytest.approx(2_635_340.12, abs=0.01)
    monkeypatch.setitem(SWC, "in_force", True)
    held = config.morpho_vest_claims_by_leg()
    assert set(held) == {"noncirculating_holding_tokens", "morpho_undistributed_tokens"}
    q0 = sum(v for d, v in held["morpho_undistributed_tokens"].items() if "2026-08-22" < d <= "2026-11-20")
    assert q0 == pytest.approx(124_057.39, abs=0.01)
    assert config.release_less("Morpho")[0][0] == "morpho_vest_claims_tokens"
    assert ("Morpho", "morpho_vest_claims_tokens") in config.RELEASE_LESS_OPTIONAL
    assert config.FLOAT_NAMED_MOVES["Morpho"]["series"] == "morpho_vest_claims_tokens"


def test_3_a_labs_claim_is_netted_from_the_set_it_leaves_and_a_dao_claim_from_the_dao(monkeypatch):
    """Switch in force. 2025-12-05: the DAO Vester paid 2,635,340.12 (#5, #6, #21: the DAO's stock falls on 12-06, an
    archive read) and the Labs vester paid 1,000 on a declared day; the set also paid 500 that is no claim. The release
    holds the 500 only (was 2,635,840.12 + the claim: every contributor's claim read as an unlock)."""
    _scope(monkeypatch)
    monkeypatch.setitem(SWC, "in_force", True)
    spec = dict(config.MORPHO_VESTING_CLAIMS, claims=(
        ("2025-12-05", "dao#5", 1_345_694.5216049382, "x"), ("2025-12-05", "dao#6", 224_290.9079218107, "x"),
        ("2025-12-05", "dao#21", 1_065_354.6907150205, "x"), ("2025-12-05", "labs#54", 1_000.0, "x")))
    monkeypatch.setattr(config, "MORPHO_FOUNDER_VESTER", spec)
    days = pd.date_range("2025-12-03", periods=5)
    dao = [3e8, 3e8, 3e8, 3e8 - 2_635_340.1202417696, 3e8 - 2_635_340.1202417696]
    und = [5e7, 5e7, 5e7 - 1_000.0 - 500.0, 5e7 - 1_500.0, 5e7 - 1_500.0]
    groups = {("Morpho", "total_supply"): _rows("total_supply", days, 1e9, "chain"),
              ("Morpho", "noncirculating_holding_tokens"): _rows("noncirculating_holding_tokens", days, dao),
              ("Morpho", "morpho_undistributed_tokens"): _rows("morpho_undistributed_tokens", days, und)}
    for m in ("emissions_tokens_claims_ethereum", "emissions_tokens_claims_ethereum_legacy",
              "emissions_tokens_claims_base", "emissions_tokens_claims_arbitrum", "emissions_tokens_claims_katana"):
        groups[("Morpho", m)] = _rows(m, days, 0.0, "x")
    monkeypatch.setitem(config.UNLOCK_STOCKS, "Morpho", ("noncirculating_holding_tokens",))
    bw._founder_vest_claim_views(groups)
    cl = bw._daily(groups[("Morpho", "morpho_vest_claims_tokens")])
    assert cl[pd.Timestamp("2025-12-06")] == pytest.approx(2_635_340.12, abs=0.01)    # dated on the DAO's fall
    assert cl[pd.Timestamp("2025-12-05")] == pytest.approx(1_000.0)                   # the set's fall that day
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", config.unlock_stock_metrics("Morpho"))
    rel = bw._daily(groups[("Morpho", "unlock_release_tokens")])
    assert rel.sum() == pytest.approx(500.0, abs=1e-6)


def test_3_the_names_say_contract_award_and_amount(monkeypatch):
    monkeypatch.setitem(SWC, "in_force", True)
    names = config.morpho_founder_claim_names("2025-12-01", "2025-12-31")
    assert any(x.startswith("2025-12-05: vest() claim 1,345,694.52 MORPHO, the DAO Vester 0x229A…0bfc award #5")
               for x in names)
    assert any("the Labs vester 0xe206…4407 award #" in x for x in names)
    monkeypatch.setitem(SWC, "in_force", False)
    assert not any("Labs" in x for x in config.morpho_founder_claim_names("2025-12-01", "2025-12-31"))


# ============================================================================== 4. the founders' relock tranche is out
def test_4_the_relock_keeps_cohort_3_only():
    r = config.MORPHO_RELOCK
    assert [n for n, _a in r["tranches"]] == ["Strategic Partners Cohort 3 / Investors Round 3 (6.7%)"]
    assert config.relocked_on("2025-09-08") == 67_000_000                       # was 219,000,000
    assert config.relocked_on("2026-11-20") == pytest.approx(67_000_000 * 366 / 730)   # was 109,800,000
    # what circulating gains on the 11-20 export: the founders' leg the model held (152M x 366 / 730)
    assert 152_000_000 * 366 / 730 == pytest.approx(76_208_219.18, abs=0.01)
    assert "R22" in r["r22"] and "Founders" not in config.METRICS["morpho_relocked_tokens"]["label"][:60]


# ============================================================================== the numbers on a store (circ_legs)
def test_5_circ_legs_states_the_release_with_the_vesting_and_every_claim(tmp_path, monkeypatch):
    """The 11-20 export's Q0 rebuilt in a small store: DAO falls = the founders' 11-06 / 11-11 claims; the set pays
    the Labs vester's Q0 claims (124,057.39) and nothing else. Unlocks Q0 = Cohort 3's relock (8,260,273.97) + the Labs
    vester's vesting (613,973.95). Was (R21b): 27,000,000 of relock (the founders' tranche too) + the Labs claims."""
    import circ_legs
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT)")
    now = "chain:sum(" + "+".join(sorted(config.current_set_point("Morpho", "morpho_undistributed_tokens"))) + ")"
    labs = config.morpho_vest_claims_by_leg(legs=("morpho_undistributed_tokens",))["morpho_undistributed_tokens"]
    und = 5e7
    for d in pd.date_range("2026-08-01", "2026-11-20"):
        ds = str(d.date())
        und -= labs.get(ds, 0.0)
        hold = 3e8 - (3_264_486.30401235 if d >= pd.Timestamp("2026-11-06") else 0.0) \
            - (17_015_150.97736626 if d >= pd.Timestamp("2026-11-11") else 0.0)
        for m, v, s in (("total_supply", 1e9, "chain"), ("noncirculating_holding_tokens", hold, "c"),
                        ("morpho_undistributed_tokens", und, now)):
            conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (ds, "Morpho", m, v, s))
    conn.commit()
    out = "\n".join(circ_legs.release_parts(conn, "Morpho"))
    assert "(2026-08-22, 2026-11-20]" in out
    assert "morpho_relocked_tokens: fall 8,260,273.97 (MODELLED" in out                  # was 27,000,000.00
    assert "morpho_vesting_unvested_tokens: fall 613,973.95 (the vesting on schedule" in out
    assert "morpho_undistributed_tokens: fall 124,057.39" in out
    assert "less morpho_vest_claims_tokens: 20,403,694.67 claimed; 20,403,694.67 taken" in out
    assert "Unlocks Q0 (per day: released − claims taken) = 8,874,247.92" in out


def test_5_the_float_statement_balances_with_the_vesting_in_unlocks(monkeypatch):
    """The vesting is (e) on its day, but the tokens stay in the czar's Safe (off the float) until claimed: the float
    shows + unlocks and the same amount back on the ± issuer line — the day balances."""
    name = "Morpho"
    _scope(monkeypatch)
    monkeypatch.setattr(config, "RULE_IN_PENDING", {})
    sp = free_float.parts(config.PROJECT_BY_NAME[name])
    assert not isinstance(sp, str)
    n = 4
    d0 = pd.Timestamp("2026-11-01")
    dd = [d0 + pd.Timedelta(days=i) for i in range(n)]
    series = {"circulating_supply_used": [800.0] * n, "rule_in_off_float_tokens": [300.0] * n,
              "unlock_release_tokens": [7.0] * n, "unlock_release_outside_tokens": [0.0] * n,
              "morpho_vest_claims_tokens": [0.0] * n}
    for m in (*sp["issuer"], *sp["lock"]["add"], *sp["lock"]["less"], *sp["held"]["add"], *sp["held"]["less"]):
        series.setdefault(m, [0.0] * n)
    groups = {(name, m): _rows(m, dd, v, "test") for m, v in series.items()}
    free_float.views(groups, dd[-1])
    day = free_float.MOVES[name].loc[dd[2]]
    assert day["unlocks"] == pytest.approx(7.0) and day["issuer"] == pytest.approx(-7.0)
    assert abs(day["residual"]) < 1e-9
