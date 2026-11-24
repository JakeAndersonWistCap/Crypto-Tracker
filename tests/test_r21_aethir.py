"""Round 21 — Aethir (Jake 2026-11-25; SPEC "Round 21 — Aethir"). Behaviour and numbers, never wording.

1. The burn's 0 rows on 24 Nov: the day's marks now name the series they vouch for (kept only when it reaches the
   store), the burn is read FIRST, a stray non-burn transfer is named and excluded and never refuses the read; one run
   stores every complete day since 2026-11-02.
2. Unlocks (decision 7): counted on the published schedule; the measured claims a memo beside them and the on-chain
   check (claimed <= scheduled, 0%).
3. Staking pools: emissions-funded on chain; A3 stays off.
4. Buyback: a direct burn of fees — the draft declaration is NOT applied.
No network: every read is a stub (the R20 stub world)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import token_metrics  # noqa: E402
from fetch import aethir_chain as ac  # noqa: E402
from fetch import base  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402
from test_r20_aethir import ATH, SPEC, WEI, _Daily, _g, _run, _World  # noqa: E402

BURN = SPEC["burn"]
K_BURN, K_ETH = "aethir_chain:Aethir:gross_burn_tokens", "aethir_chain:Aethir:ath_issuer_held_tokens"


# ============================================================================================ 1. the burn's 0 rows
def test_the_marks_name_the_series_so_a_mark_without_its_rows_is_dropped(monkeypatch):
    got, out, daily = _run(_World(), monkeypatch)
    assert {k for k, _d in daily.marks} == {K_BURN, K_ETH}
    verify_ok = token_metrics._mark_stored(pd.concat(
        [f for f in out.frames], ignore_index=True))
    assert verify_ok(K_BURN) and verify_ok(K_ETH)
    # the rows did NOT reach the store (any reason): the mark is not kept, so the next run reads again
    no_burn = token_metrics._mark_stored(pd.concat([f for f in out.frames if f["metric"].iloc[0] != "gross_burn_tokens"],
                                                   ignore_index=True))
    assert not no_burn(K_BURN) and no_burn(K_ETH)
    # the R20 keys named no series: _mark_stored kept them whatever was stored — the "read today already", 0 rows
    assert no_burn("aethir_chain:burn")


def test_a_second_run_the_same_day_says_read_today_already_with_no_rows(monkeypatch):
    world, out, daily = _World(), FetchOutput(), _Daily()
    monkeypatch.delenv("ETHEREUM_RPC_URL", raising=False)
    monkeypatch.delenv("ARBITRUM_RPC_URL", raising=False)
    daily.done(K_BURN, str(base.today().date()))
    ac.AethirChain(session=world, daily=daily).run([ATH], 1, out)
    assert "gross_burn_tokens" not in {f["metric"].iloc[0] for f in out.frames}
    assert ("Aethir", "gross_burn_tokens") in out.current                     # said, never a silent gap


def test_the_burn_is_read_first(monkeypatch):
    world = _World()
    _run(world, monkeypatch)
    assert world.calls[0][1] == "arb"
    first_eth = next(i for i, c in enumerate(world.calls) if c[1] == "eth")
    assert all(c[1] == "arb" for c in world.calls[:first_eth])


def test_the_ethereum_part_failing_still_stores_every_complete_burn_day(monkeypatch):
    got, out, daily = _run(_World(fail_address=SPEC["wallets"][0]["address"]), monkeypatch)
    b = got["gross_burn_tokens"]
    # ONE run: every complete UTC day from the launch (2026-11-02) to the day before the read (11-22 in the stub)
    assert list(b.index) == list(pd.date_range("2026-11-02", "2026-11-22"))
    assert b.sum() == pytest.approx(11_955_182.0938 - 635_257.2326, abs=0.01)
    assert {k for k, _d in daily.marks} == {K_BURN}


class _StrayWorld(_World):
    """The R20 world plus the stray transfer of 2026-10-21 (0.0159 ATH from 0xd4627ecb…) to the dead address."""

    def __init__(self, **kw):
        super().__init__(**kw)
        stray = config.AETHIR_R21_BURN_READ["stray"]
        self.burns.insert(0, {"blockNumber": hex(516_922_099), "blockTimestamp": hex(int(pd.Timestamp(
            "2026-10-21 13:11:39").timestamp())), "transactionHash": stray["tx"], "logIndex": "0x9",
            "topics": [ac.TRANSFER, "0x" + ac._pad(stray["from"]), "0x" + ac._pad(BURN["dead"])],
            "data": hex(int(stray["tokens"] * WEI))})


def test_a_stray_transfer_is_named_and_excluded_and_never_refuses_the_read(monkeypatch):
    got, out, daily = _run(_StrayWorld(), monkeypatch)
    b = got["gross_burn_tokens"]
    assert b.sum() == pytest.approx(11_955_182.0938 - 635_257.2326, abs=0.01)     # the stray is not the burn
    assert got["burn_address_balance"].iloc[-1] == pytest.approx(11_955_182.0938 + 0.015936806889, abs=0.001)
    src = next(f for f in out.frames if f["metric"].iloc[0] == "gross_burn_tokens")["source"].iloc[0]
    assert "0xd4627ecb" in src and "2026-10-21" in src
    assert K_BURN in {k for k, _d in daily.marks}
    strays = ac.stray_transfers([{"ts": 1_792_000_000, "from": "0x" + "a" * 40, "value": 5 * WEI, "tx": "0x1"},
                                 {"ts": 1_793_000_000, "from": BURN["burner"].lower(), "value": 7 * WEI}], BURN["burner"])
    assert [(x["from"], x["tokens"]) for x in strays] == [("0x" + "a" * 40, 5.0)]


def test_a_balance_the_logs_do_not_reach_still_refuses(monkeypatch):
    got, out, daily = _run(_World(dead_extra=1000.0), monkeypatch)           # the proof is unchanged (no loosening)
    assert "gross_burn_tokens" not in got and K_BURN not in {k for k, _d in daily.marks}


def test_the_recorded_live_read_reconciles_to_the_wei():
    r = config.AETHIR_R21_BURN_READ
    assert r["burned"] + r["stray"]["tokens"] == pytest.approx(r["dead_balance"], abs=1e-6)
    assert r["burned"] - config.PROJECT_BY_NAME["Aethir"]["aethir_chain"]["burn"]["read"]["total"] == pytest.approx(
        631_062.88723137, abs=1e-6)


def test_the_tier_budget_fits_the_measured_read():
    import fetch
    assert fetch.TIER_BUDGET_S["aethir_chain"] == 300                  # was 180 (R20); 81 s measured alone 2026-11-24


# ============================================================================================ 2. unlocks
def _sched_days(asof):
    e = config.EVIDENCE_LADDER[("Aethir", "insider_unlock_tokens")]
    days = {}
    for per in e["periods"]:
        span = pd.date_range(per["from"], per["to"])
        for d in span:
            days[d] = days.get(d, 0.0) + per["value"] / len(span)
    s = pd.Series(days).sort_index()
    return s[s.index <= asof]


def test_the_published_schedule_is_about_1_04bn_a_quarter():
    s = _sched_days(pd.Timestamp("2026-11-24"))
    assert s[pd.Timestamp("2026-11-24")] == pytest.approx(11_406_584, abs=1)
    q0 = s[(s.index > pd.Timestamp("2026-08-26")) & (s.index <= pd.Timestamp("2026-11-24"))].sum()
    assert q0 == pytest.approx(1_026_592_591, abs=1)                         # a 90-day block
    assert s[s.index <= pd.Timestamp("2026-11-24")].sum() == pytest.approx(5_180_299_570, abs=1)


def test_the_check_claimed_never_above_scheduled_per_block():
    asof = pd.Timestamp("2026-11-24")
    sched = _sched_days(asof)
    # claims as read live 2026-11-24, spread over each block (a per-block sum is what the check compares)
    blocks = [(0, 514_104_186.0), (1, 512_300_919.0), (2, 448_824_532.0), (3, 656_024_648.0), (4, 1_451_709_321.0)]
    claims = {}
    for k, tot in blocks:
        a, b = bw._flow_window(asof, k)
        for d in pd.date_range(a + pd.Timedelta(days=1), b):
            claims[d] = tot / 90
    verdict, rows, bad = bw.unlock_schedule_check(sched, pd.Series(claims).sort_index(), asof, 0.0)
    assert bad == ["Q-4"] and verdict.startswith("DISAGREES")
    assert rows[0][1] == pytest.approx(1_026_592_591, abs=1) and rows[0][2] == pytest.approx(514_104_186.0, abs=1)
    # a block the claims do not span is not judged
    v2, rows2, _ = bw.unlock_schedule_check(sched, pd.Series(claims).sort_index().loc["2026-05-29":], asof, 0.0)
    assert len(rows2) == 2 and v2 == "agrees"


def test_the_unlock_is_the_schedule_and_the_claims_are_the_memo(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ATH])
    monkeypatch.setitem(bw._WINDOWS, "q0", ("2026-08-26", "2026-11-24"))
    src = ac.POINT_VESTERS + "[x]"
    days = pd.date_range("2026-08-20", "2026-11-24")
    stock = [6_700_000_000.0 - 5_000_000.0 * i for i in range(len(days))]          # 5M claimed a day
    groups = {("Aethir", "ath_vester_balance_tokens"): _g("ath_vester_balance_tokens",
                                                          [(d, v, src) for d, v in zip(days, stock)])}
    bw._unlock_views(groups)
    bw.EVIDENCE_RESULTS.clear()
    bw._evidence_views(groups, usd=False)
    bw._unlock_on_schedule_views(groups)
    rel, memo = bw._daily(groups[("Aethir", "unlock_release_tokens")]), bw._daily(groups[("Aethir", "unlock_claims_tokens")])
    assert rel[pd.Timestamp("2026-11-24")] == pytest.approx(11_406_584, abs=1)
    assert memo[pd.Timestamp("2026-11-24")] == pytest.approx(5_000_000.0)
    q0 = rel[(rel.index > pd.Timestamp("2026-08-26")) & (rel.index <= pd.Timestamp("2026-11-24"))].sum()
    assert q0 == pytest.approx(1_026_592_591, abs=1)
    chk = bw.UNLOCK_SCHEDULE_CHECK["Aethir"]
    assert chk["verdict"] == "agrees" and chk["blocks"][0][2] == pytest.approx(450_000_000.0)
    assert "unlock_on_schedule" in groups[("Aethir", "unlock_release_tokens")]["source"].iloc[0]
    assert ("Aethir", "unlock_release_tokens") in bw.EVIDENCE_RESULTS


def test_no_schedule_view_means_no_unlock_never_the_claims(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ATH])
    monkeypatch.setitem(bw._WINDOWS, "q0", ("2026-08-26", "2026-11-24"))
    groups = {("Aethir", "unlock_release_tokens"): _g("unlock_release_tokens", [("2026-11-24", 5.0)])}
    bw._unlock_on_schedule_views(groups)
    assert ("Aethir", "unlock_release_tokens") not in groups and ("Aethir", "unlock_claims_tokens") in groups


def test_the_memo_column_sits_beside_the_unlock_on_cp():
    cols = [d for d in config.detail_columns("CP") if "Aethir" in d["cells"]]
    assert any(d["after"] == "Unlocks Q0" and d["cells"]["Aethir"] == ("unlock_claims_tokens", "q0") for d in cols)
    assert config.unlock_stock_metrics("Aethir") == ("ath_vester_balance_tokens",)      # the claims are still measured


# ============================================================================================ 3. pools, 4. buyback
def test_no_pool_is_fee_funded_on_chain_and_a3_stays_off():
    rf = config.AETHIR_STAKING_POOLS["reward_funding"]
    safe = config.PROJECT_BY_NAME["Aethir"]["aethir_chain"]["vesting_wallet"]["beneficiary"].lower()
    for pool in ("ai_pool", "gaming_pool"):
        assert set(rf[pool]["faucet_funded_by"]) == {safe}                 # the issuer's Safe, nobody else
        assert rf[pool]["weekly"] == 1_000_000.0
    assert rf["fee_address_seen"] is False
    assert config.CONDITIONAL_ARCHETYPES["Aethir"]["on"] is False


def test_the_buyback_draft_is_not_applied():
    # the burn is a direct burn of fees (ServiceFeeBurner); the declaration waits for Jake: R20's by-design code stands
    # R21 BURNS (Jake: "Uniswap and Aethir read 'buying: by design'. Fix all three"): buying is a declared 0 now;
    # the draft declaration of the no-buyback line is still not applied (was: Aethir in BUYING_PRESSURE_BY_DESIGN)
    assert "Aethir" not in config.BUYING_PRESSURE_BY_DESIGN and "Aethir" in config.BUYING_ZERO_DECLARED
    assert "ServiceFeeBurner" not in str(config.DECLARED_NA_REASONS.get("Aethir", ""))
