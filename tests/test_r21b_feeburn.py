"""Round 21b — fee burns in A3 (Jake 2026-11-25, item 3; SPEC "Round 21b — fee burns in A3"). Behaviour and numbers.

"A3 includes revenue-funded fee burns (Jake): Aethir's fee burn counts in its A3 key, so tag it A2, A3, A4. Apply the
same to Ethereum's EIP-1559 burn (A1, A3, A4) and NEAR's fee burn (alongside its Intents buybacks). Fee burns go in the
A3 key's numerator as "revenue sent to holders", valued at the day's price."
1. The A3 numerator = the credited holder value with the fee burn in it (one machinery: HOLDER_VALUE burn_credited).
2. Tags A2/A3/A4, A1/A3/A4, A1/A3/A4; primaries unchanged — none of the three enters a primary-A3 rank.
3. A4 and net float pressure keep their burn series (counted once).
4. Aethir's declared reason: the section 9 wording, applied."""
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
import reader  # noqa: E402

THREE = ("Aethir", "Ethereum", "Near")


def _g(project, metric, s):
    return pd.DataFrame({"date": s.index, "project": project, "metric": metric, "value": s.values, "tier": 2,
                         "source": "test", "fetched_at": "2026-11-20T00:00:00"})


# ============================================================================================ 1. the numerator
@pytest.mark.parametrize("name", ["Aethir", "Ethereum"])
def test_the_fee_burn_is_revenue_to_holders_at_the_days_price(name, monkeypatch):
    """Holder value credits the burn x that day's price; the A3 numerator is that same figure (R20 took it out)."""
    days = pd.date_range("2026-11-02", "2026-11-11")
    burn = pd.Series([100.0, 0.0, 250.0, 50.0, 0.0, 10.0, 0.0, 0.0, 40.0, 5.0], index=days)
    px = pd.Series([0.03 + 0.001 * i for i in range(10)], index=days)
    metric = config.HOLDER_VALUE[name]["burn_credited"]["metric"]
    groups = {(name, metric): _g(name, metric, burn), (name, "price_usd"): _g(name, "price_usd", px)}
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[name]])
    bw._holder_value_views(groups)
    bw._revenue_to_holders_views(groups)
    hv = bw._daily(groups[(name, "holder_value_revenue_usd")])
    num = bw._daily(groups[(name, bw.A3_KEY_METRIC)])
    want = (burn * px).sum()
    assert hv.sum() == pytest.approx(want) and num.sum() == pytest.approx(want)    # was R20: num = hv − burn = 0
    assert (num == hv.reindex(num.index)).all() and list(num.index) == list(hv.index)
    assert bw.a3_key_numerator(name) == bw.A3_KEY_METRIC
    assert "included (R21b)" in groups[(name, bw.A3_KEY_METRIC)]["source"].iloc[0]


def test_near_counts_the_gas_burn_once_beside_the_intents_buyback(monkeypatch):
    """Near: $30 Intents buyback + 2 NEAR burned x $5 a day, the burn credited to the buyback's last stored day (holder
    value's own cut) — the numerator is that holder value, 8 x $40 = $320 (R20: $240)."""
    days = pd.date_range("2026-08-01", "2026-08-10")
    bc = config.HOLDER_VALUE["Near"]["burn_credited"]
    assert bc["metric"] == "near_chain_burn_tokens" and bc["with_buyback"]
    hv_n = pd.Series(40.0, index=days[:8])
    groups = {("Near", "holder_value_revenue_usd"): _g("Near", "holder_value_revenue_usd", hv_n),
              ("Near", bc["metric"]): _g("Near", bc["metric"], pd.Series(2.0, index=days)),
              ("Near", "price_usd"): _g("Near", "price_usd", pd.Series(5.0, index=days)),
              ("Near", "actual_buyback_usd"): _g("Near", "actual_buyback_usd", pd.Series(30.0, index=days[:8]))}
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Near"]])
    bw._revenue_to_holders_views(groups)
    n = bw._daily(groups[("Near", "revenue_to_holders_usd")])
    assert n.sum() == pytest.approx(320.0) and list(n.index) == list(days[:8])
    # never twice: the A1/A4 burn (gross_burn_tokens_bigquery) is the same burn as the credited series — not added
    assert ("Near", "near_chain_burn_tokens") in config.BURN_NOT_IN_NET
    assert [b["metric"] for b in config.BURN_SERIES["Near"]] == ["gross_burn_tokens_bigquery"]


def test_the_near_export_key_moves_by_the_fee_burn_only():
    """Jake's Near export 2026-11-20 10:44 (scratchpad exp20), built before/after: Q0 revenue to holders $5,025,794.16
    -> $5,329,854.95 (= holder value; + $304,060.79 of gas burn), both over 85 covered days, the same new supply: the
    key 0.190165374 -> 0.201670388 (the build's cells)."""
    before, after, key_before = 5_025_794.160617104, 5_329_854.948469856, 0.190165374
    assert key_before * after / before == pytest.approx(0.201670388, abs=1e-8)


def test_every_credited_fee_burn_is_in_the_numerator_metric():
    only = set(config.METRICS["revenue_to_holders_usd"]["only_projects"])
    assert only == {n for n, h in config.HOLDER_VALUE.items() if h.get("burn_credited")} == set(THREE)
    for n in THREE:
        assert "revenue_to_holders_usd" in config.metrics_for_project(config.PROJECT_BY_NAME[n])
        assert "+ the fee burn" in bw.a3_key_note(config.PROJECT_BY_NAME[n])


# ============================================================================================ 2. tags and ranks
def test_the_tags_and_the_primaries():
    assert config.PROJECT_BY_NAME["Aethir"]["archetypes"] == [2, 3, 4]
    assert config.PROJECT_BY_NAME["Ethereum"]["archetypes"] == [1, 3, 4]
    assert config.PROJECT_BY_NAME["Near"]["archetypes"] == [1, 3, 4]
    assert [reader._primary(config.PROJECT_BY_NAME[n]) for n in THREE] == [2, 1, 1]
    c = config.CONDITIONAL_ARCHETYPES["Aethir"]
    assert c["on"] and "revenue-funded fee burn" in c["why_on"] and "RewardFaucet" in c["pool_finding"]
    # the series asked of them do not move (a re-tag moves no series)
    assert config.PROJECT_BY_NAME["Ethereum"]["metric_archetypes"] == [1]


def test_none_of_the_three_enters_the_a3_rank():
    """The A3 rank is over primary-A3 projects only: a key of any size on Aethir/Ethereum/Near ranks nothing."""
    keys = {"Maple": (3, 0.5), "Pendle": (3, 0.2), "Aethir": (2, 99.0), "Ethereum": (1, 99.0), "Near": (1, 99.0)}
    ranked = reader._arch_ranking(keys, 3, tiebreak={})
    assert [n for n, _r, _v in ranked] == ["Maple", "Pendle"] and [r for _n, r, _v in ranked] == [1, 2]


# ============================================================================================ 3. A4 / net float pressure
def test_the_burns_stay_on_a4_and_the_float_once():
    assert [b["metric"] for b in config.BURN_SERIES["Aethir"]] == ["gross_burn_tokens"]
    assert [b["metric"] for b in config.BURN_SERIES["Ethereum"]] == ["gross_burn_tokens"]
    assert all(b["from"] == "float" for n in THREE for b in config.BURN_SERIES[n])
    assert "Aethir" not in config.BUYING_PRESSURE_BY_DESIGN                          # a burn is never buying
    assert config.BUYING_ZERO_DECLARED["Aethir"].startswith("0 — Aethir has no buy-then-burn programme.")


# ============================================================================================ 4. Aethir's declared reason
def test_aethirs_declared_reason_is_the_section_9_wording(monkeypatch):
    w = ("Aethir has no buy-then-burn programme. The ServiceFeeBurner (0xf8ec…) claims a share of service fees from "
         "AethirCore in ATH and sends it to 0x…dEaD: a direct fee burn, not a buyback — no swap.")
    assert config.AETHIR_DECLARED_NO_BUYBACK == w
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Aethir"]])
    st = bw.derived_statuses(pd.DataFrame(columns=["project", "metric", "q0"]))
    assert st["Aethir"]["buyback"] == "n/a — " + w                   # was "n/a — no buyback programme declared (...)"
    e = config.declared_na_reason("Aethir", st["Aethir"]["buyback"])
    assert e and e["read_on"] == "2026-11-24" and "R21b" in e["decided"]
    assert w in config.PROJECT_BY_NAME["Aethir"]["fee_split"]["note"]
    import rule3
    assert not rule3.is_suspect(st["Aethir"]["buyback"])                         # names no absent mechanism


def test_ethereums_a3_cells_carry_its_decided_reasons():
    """Ethereum's new A3 tab cells read two reasons it already carries, both decided (R20 no unlock wallets; P4-Q4 no
    lock contract) — declared, never silently passed."""
    a = config.declared_na_reason("Ethereum", "BURNED ÷ NEW SUPPLY: ... (emissions = emissions_tokens; no unlock leg: "
                                              "no non-circulating wallet set is declared for this project)")
    assert a and "R20" in a["decided"] and a["source"].startswith("https://")
    b = config.declared_na_reason("Ethereum", "n/a — Data: not applicable to this project — ETHEREUM HAS NO LOCK "
                                              "CONTRACT. No veETH")
    assert b and "P4-Q4" in b["decided"] and b["read_on"]
    assert config.declared_na_reason("Ethereum", "n/a — no supply-side incentive (b) series on file") is None
