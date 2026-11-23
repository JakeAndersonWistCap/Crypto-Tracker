"""Round 20, the DECISIONS lane (Jake 2026-11-24, Part 3 and Part 4.6):
  AERODROME  none of the 10M minted 11-19 and burned 11-20 is a burned buyback (burned buybacks this quarter 407,816);
             the Gauge Caps' unminted surplus is stored and netted out of accrued emissions; holder value from the
             merger is one all-chain staker-revenue series (on chain once hole-free, DefiLlama's holders revenue until
             then), the separate bribe add-on gone; the merger issuance stays on its own line
  CHAINLINK  the flag's long wording filled from the series; the Safe 0x77dd1a9b…'s payments to the 63 are (b), its
             others stay in (e); the exchange leg is category (f); the labels
  GEODNET    FEES = REVENUE = burn ÷ 0.8, HOLDER REVENUE = burn; uniformity declared n/a
  TOKENJAR   the tier has the time one day needs; no call after the hard stop; held balance days are not read again
  PENDLE     Berachain's history repair no longer takes a pruned node's empty answer as the chain's record
"""
import pandas as pd
import pytest

import build_workbook as bw
import config
import fetch
from fetch import aero_leaf, aero_v3, tokenjar
from fetch.base import FetchOutput

A = "Aerodrome"
TS = pd.Timestamp


def _rows(project, metric, days, values, source, tier=2):
    return pd.DataFrame({"date": pd.to_datetime(list(days)), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": tier, "source": source})


# ============================================================ AERODROME 1: the burn split ============================
PURCHASES = {"2026-08-26": 777_723.57, "2026-10-28": 174_600.64, "2026-10-30": 233_215.20,
             "2026-11-04": 243_612.23, "2026-11-11": 294_003.10, "2026-11-18": 503_340.91}
BURNS = {"2026-10-28": 174_600.64, "2026-10-30": 233_215.20, "2026-11-20": 10_000_000.0}


def _series(d):
    return pd.Series({TS(k): float(v) for k, v in d.items()}).sort_index()


def test_aero_none_of_the_10m_is_a_burned_buyback():
    """Jake: "none of the 19-20 Nov 10M surplus mint-then-burn counts as a buyback (burned buybacks this quarter =
    407,816)". Was 2,226,495.65 burned (407,815.84 + 1,818,679.81 of the 10M) and 8,181,320.19 destroyed."""
    bought, raw = _series(PURCHASES), _series(BURNS)
    # the old rule, kept as the number it gave: the cap at the purchases since the basis
    ok_old, extra_old = bw.foundation_burn_split(bought, raw)
    assert ok_old.sum() == pytest.approx(2_226_495.65, abs=0.01) and extra_old.sum() == pytest.approx(8_181_320.19, abs=0.01)
    left, minted = bw.foundation_minted_burns(raw, config.mint_funded_burns(A))
    ok, extra = bw.foundation_burn_split(bought, left)
    extra = extra.add(minted, fill_value=0.0)
    assert ok.sum() == pytest.approx(407_815.84, abs=0.01)               # 174,600.64 + 233,215.20
    assert ok[TS("2026-11-20")] == 0.0 and extra[TS("2026-11-20")] == pytest.approx(10_000_000.0)
    assert extra.sum() == pytest.approx(10_000_000.0)
    # nothing is lost: every destroyed token is on one of the two lines
    assert ok.sum() + extra.sum() == pytest.approx(raw.sum())
    # the purchases stay held: bought − burned buybacks = 1,818,679.81 (503,340.91 + 537,615.33 legacy + 777,723.57)
    assert bought.sum() - ok.sum() == pytest.approx(1_818_679.81, abs=0.01)
    assert 503_340.91 + 243_612.23 + 294_003.10 + 777_723.57 == pytest.approx(1_818_679.81, abs=0.01)


def test_aero_mint_funded_burn_is_by_evidence_not_by_amount(monkeypatch):
    rec = config.AERO_MINT_THEN_BURN_2026_11
    assert config.mint_funded_burns(A) == {"2026-11-20": 10_000_000.0}
    assert rec["mechanism"]["is_buyback"] is False and rec["mechanism"]["stake"]["amount"] == rec["burn"]["burned"]
    assert sum(e[2] for e in rec["mechanism"]["events"]) == rec["mint"]["to_buyer_safe"]
    # a record whose stake is not the burn (purchased AERO could be in it) declares nothing
    bad = {**rec, "mechanism": {**rec["mechanism"], "stake": {**rec["mechanism"]["stake"], "amount": 9_000_000.0}}}
    monkeypatch.setitem(config.MINT_FUNDED_BURNS, A, (bad,))
    assert config.mint_funded_burns(A) == {}
    # nor one whose mint events do not sum to what the wallet received, nor one not declared "no buyback"
    monkeypatch.setitem(config.MINT_FUNDED_BURNS, A, ({**rec, "mint": {**rec["mint"], "to_buyer_safe": 11_000_000.0}},))
    assert config.mint_funded_burns(A) == {}
    monkeypatch.setitem(config.MINT_FUNDED_BURNS, A, ({**rec, "mechanism": {**rec["mechanism"], "is_buyback": True}},))
    assert config.mint_funded_burns(A) == {}
    assert config.mint_funded_burns("Uniswap") == {}


def test_aero_a_declared_burn_the_chain_does_not_show_takes_nothing():
    raw = _series({"2026-11-20": 4_000_000.0, "2026-11-21": 50.0})
    left, minted = bw.foundation_minted_burns(raw, {"2026-11-20": 10_000_000.0, "2026-11-25": 7.0})
    assert left[TS("2026-11-20")] == 0.0 and minted[TS("2026-11-20")] == 4_000_000.0     # never more than was burned
    assert left[TS("2026-11-21")] == 50.0 and minted.sum() == 4_000_000.0                # a day not burned: nothing
    same, none = bw.foundation_minted_burns(raw, {})
    assert same.equals(raw.astype(float)) and none.sum() == 0.0


def test_aero_burn_view_end_to_end(monkeypatch):
    """The build's own view: burned buyback 407,815.84, the destroyed line 10,000,000, held not reduced by the 10M."""
    p = config.PROJECT_BY_NAME[A]
    fb = p["foundation_buyback"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [p])
    days = pd.date_range("2026-08-25", "2026-11-20")
    bought = _series(PURCHASES).reindex(days).fillna(0.0)
    burned = _series(BURNS).reindex(days[days >= TS("2026-10-22")]).fillna(0.0)
    held = bought.copy()
    g = {(A, fb["flow"]): _rows(A, fb["flow"], days, bought.values, "derived:foundation_purchases[x]"),
         (A, "buyback_held_tokens"): _rows(A, "buyback_held_tokens", days, held.values, "derived:split"),
         (A, fb["burn_leg"]["metric"]): _rows(A, fb["burn_leg"]["metric"], burned.index, burned.values,
                                              "ve_managed:buyback_window_v3"),
         (A, fb["burn_leg"]["other_metric"]): _rows(A, fb["burn_leg"]["other_metric"], burned.index,
                                                    [0.0] * len(burned), "ve_managed:buyback_window_v3")}
    monkeypatch.setitem(fb, "held_check", None)
    bw._foundation_burn_views(g)
    b = g[(A, "buyback_burned_tokens")].set_index("date")["value"]
    o = g[(A, fb["burn_leg"]["other_metric"])].set_index("date")["value"]
    h = g[(A, "buyback_held_tokens")].set_index("date")["value"]
    assert b[b.index >= TS("2026-10-22")].sum() == pytest.approx(407_815.84, abs=0.01)   # was 2,226,495.65
    assert o.sum() == pytest.approx(10_000_000.0)                                       # was 8,181,320.19
    assert h[TS("2026-11-20")] == 0.0 and h.sum() == pytest.approx(bought.sum() - 407_815.84, abs=0.01)
    assert h[TS("2026-11-18")] == pytest.approx(503_340.91)                             # bought and HELD
    assert bw.FOUNDATION_BURN[A]["mint_funded"] == pytest.approx(10_000_000.0)


def test_aero_merger_issuance_line_still_stands(monkeypatch):
    """Jake: "The 117M merger issuance stays on its own line" — in the supply, in no dilution category, and left out of
    the net supply change (R18.A2), unchanged by this round."""
    key = (A, "aero_merger_issuance_tokens")
    assert config.HANDOVER_STEP[key]["net_supply_change"] == "left out"
    line = next(x for x in config.RULE_IN[A]["flow_in"] if x[1] == "aero_merger_issuance_tokens")
    assert line[0].startswith("+ merger issuance")
    assert config.AERO_MERGER_ISSUANCE["allocation"] == 117_000_000.0
    assert not any("aero_merger_issuance_tokens" in (config.outflow_metrics(A, c) or ())
                   for c in config.OUTFLOW_CATEGORY_NAMES)
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[A]])
    days = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    vals = [2_002_000_000.0, 2_002_050_000.0, 2_119_350_000.0, 2_119_750_000.0]
    g = {(A, "circulating_supply_used"): _rows(A, "circulating_supply_used", days, vals,
                                               "derived:circulating_used[total_supply]", 1),
         (A, "aero_merger_issuance_tokens"): _rows(A, "aero_merger_issuance_tokens", ["2026-10-22"], [117_000_000.0],
                                                   "derived:handover_step[total_supply]")}
    bw._circulating_change_views(g, pd.Timestamp("2026-10-23"))
    assert g[(A, "circulating_change_tokens")]["value"].sum() == pytest.approx(750_000.0)


# ============================================================ AERODROME 2: the surplus ===============================
CAP_DAY = 13.489345 * 86_400          # 1,165,479.41


def _acc(days):
    return pd.Series(CAP_DAY, index=pd.DatetimeIndex(days))


def test_aero_accrual_less_surplus_counts_only_what_mints():
    days = pd.date_range("2026-10-22", "2026-10-27")
    before = pd.Series({TS("2026-10-15"): 4_204_996.94})
    acc = pd.concat([before, _acc(days)])
    surplus = pd.Series({TS("2026-10-22"): 0.0, TS("2026-10-23"): 780_487.60, TS("2026-10-24"): 1_500_000.0,
                         TS("2026-10-25"): 2_300_000.0, TS("2026-10-26"): 3_100_000.0, TS("2026-10-27"): 3_900_000.0})
    out, frm = bw.accrual_less_surplus(acc, surplus, "2026-10-22")
    assert frm == TS("2026-10-22") and out[TS("2026-10-15")] == 4_204_996.94          # the legacy mints untouched
    assert out[TS("2026-10-22")] == pytest.approx(CAP_DAY)
    assert out[TS("2026-10-23")] == pytest.approx(CAP_DAY - 780_487.60)
    # the identity: Σ accrual − the surplus now
    assert out[out.index >= TS("2026-10-22")].sum() == pytest.approx(6 * CAP_DAY - 3_900_000.0)


def test_aero_a_surplus_spent_is_a_mint_and_counts_when_it_mints():
    """The 10M of 11-19: the pots fell from 10,855,924.28 to 1,038,402.38 between the first blocks of 11-19 and
    11-20 — the accrual of the day the fall is read gains it (it minted); it is never netted away."""
    days = pd.date_range("2026-10-22", "2026-11-21")
    rise = pd.date_range("2026-10-22", "2026-11-19")
    surplus = pd.Series([10_855_924.28 * i / (len(rise) - 1) for i in range(len(rise))], index=rise)
    surplus[TS("2026-11-20")], surplus[TS("2026-11-21")] = 1_038_402.38, 1_167_567.0
    out, _ = bw.accrual_less_surplus(_acc(days), surplus, "2026-10-22")
    assert out[TS("2026-11-20")] == pytest.approx(CAP_DAY + (10_855_924.28 - 1_038_402.38))
    assert out[TS("2026-11-20")] > 10_000_000.0
    assert out[TS("2026-11-19")] == pytest.approx(CAP_DAY - 10_855_924.28 / 28)
    assert out.sum() == pytest.approx(31 * CAP_DAY - 1_167_567.0)        # Σ accrual − the surplus still unminted


def test_aero_accrual_never_negative_and_the_sum_is_exact():
    days = pd.date_range("2026-11-01", "2026-11-06")
    surplus = pd.Series({TS("2026-11-01"): 0.0, TS("2026-11-02"): 3_000_000.0, TS("2026-11-06"): 3_100_000.0})
    out, _ = bw.accrual_less_surplus(_acc(days), surplus, "2026-11-01")
    assert (out >= 0).all() and out[TS("2026-11-02")] == 0.0 and out[TS("2026-11-03")] == 0.0   # the rise carried forward
    assert out[TS("2026-11-04")] == pytest.approx(3 * CAP_DAY - 3_000_000.0)
    assert out.sum() == pytest.approx(6 * CAP_DAY - 3_100_000.0)
    # a day with no reading inside the span keeps its accrual; the change lands on the next reading
    assert out[TS("2026-11-05")] == pytest.approx(CAP_DAY)


def test_aero_no_surplus_row_nothing_netted_and_no_value_after_the_last_reading():
    days = pd.date_range("2026-11-01", "2026-11-05")
    same, frm = bw.accrual_less_surplus(_acc(days), pd.Series(dtype=float), "2026-11-01")
    assert frm is None and same.equals(_acc(days))                                   # R16's figure, unchanged
    surplus = pd.Series({TS("2026-11-03"): 100.0})
    out, frm = bw.accrual_less_surplus(_acc(days), surplus, "2026-11-01")
    assert frm == TS("2026-11-03") and out.index.max() == TS("2026-11-03")           # 11-04, 11-05: unknown, no row
    assert out[TS("2026-11-01")] == pytest.approx(CAP_DAY) and out[TS("2026-11-03")] == pytest.approx(CAP_DAY - 100.0)


def test_aero_accrual_view_reads_the_surplus(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[A]])
    spec = config.AERO_EMISSIONS_ACCRUAL[A]
    assert spec["surplus_metric"] == "gauge_caps_surplus_tokens" == aero_v3.SURPLUS_METRIC
    assert config.METRICS["gauge_caps_surplus_tokens"]["kind"] == "stock"
    days = pd.date_range("2026-10-27", "2026-10-30")
    g = {(A, "gross_issuance_tokens"): _rows(A, "gross_issuance_tokens", ["2026-10-15"], [4_204_996.94], "chain:base:minter"),
         (A, "emission_rate_current"): _rows(A, "emission_rate_current", days, [CAP_DAY * 7] * 4, "aero_v3:minter.emissionRate")}
    bw._emissions_accrual_views(g)
    cap = g[(A, "gross_issuance_accrued_tokens")].set_index("date")["value"]
    assert cap[cap.index >= TS("2026-10-22")].sum() == pytest.approx(9 * CAP_DAY)     # 10-22..10-30 at the cap (R16)
    assert bw.ACCRUAL_SURPLUS[A]["netted_from"] is None
    sdays = pd.date_range("2026-10-22", "2026-10-30")
    g[(A, "gauge_caps_surplus_tokens")] = _rows(A, "gauge_caps_surplus_tokens", sdays, [500_000.0 * i for i in range(9)],
                                                "aero_v3:voter.spendableSurplus:archive[9 pots]")
    bw._emissions_accrual_views(g)
    net = g[(A, "gross_issuance_accrued_tokens")].set_index("date")["value"]
    assert net[net.index >= TS("2026-10-22")].sum() == pytest.approx(9 * CAP_DAY - 4_000_000.0)
    assert config.outflow_metrics(A, "b") == ("gross_issuance_accrued_tokens",)


class _VoterRpc:
    """A fake Base endpoint for the Voter's surplus reads."""

    def __init__(self, pots: dict, fail: int | None = None, chains=None):
        self.pots, self.fail, self.calls = pots, fail, []
        self.chains = list(pots) if chains is None else chains

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "eth_blockNumber":
            return hex(61_000_000)
        if method == "eth_getBlockByNumber":
            n = int(params[0], 16)
            return {"timestamp": hex(1_792_627_200 + (n - 52_918_927) * 2)}          # 2 s blocks from the cutover
        data = params[0]["data"]
        k = aero_v3._keccak
        if data.startswith(k("VOTER()")[:10]):
            return "0x" + "0" * 24 + "49d7d4f17ec56ef8fa04e464af5d1a4942cc31e6"
        if data.startswith(k("chains()")[:10]):
            ids = [c for c in self.chains if c != 0]
            return "0x" + f"{32:064x}" + f"{len(ids):064x}" + "".join(f"{c:064x}" for c in ids)
        if data.startswith(k("CHAIN0()")[:10]):
            return "0x" + f"{0:064x}"
        if data.startswith(k("spendableSurplus(uint256)")[:10]):
            cid = int(data[10:], 16)
            if cid == self.fail:
                raise RuntimeError("execution reverted")
            return "0x" + f"{int(self.pots[cid] * 10 ** 18):064x}"
        raise AssertionError(data[:10])


POTS = {0: 269_643.0, 8453: 1_338_661.0, 10: 204_383.0, 1: 903_030.0, 42161: 238_533.0, 57073: 53_939.0,
        4663: 474_906.0, 5042: 73_495.0, 99901: 45_277.0}
ADDR = {"voting_escrow": "0xbf7977e1521e9a3032bf7243ee4712f311f13915"}


def test_aero_surplus_is_every_pot_or_no_row():
    """Nine pots on 2026-11-23: CHAIN0 + the eight chains Voter.chains() lists (R18 read two of them)."""
    r = aero_v3.AeroV3(rpc=_VoterRpc(POTS), state={})
    total, pots = r.surplus_at(ADDR)
    assert len(pots) == 9 and total == pytest.approx(3_601_867.0) and set(pots) == set(POTS)
    out = FetchOutput()
    r._surplus(A, ADDR, out)
    f = out.frame()
    assert list(f["metric"]) == ["gauge_caps_surplus_tokens"] and f["value"].iloc[0] == pytest.approx(3_601_867.0)
    assert "9 pots" in f["source"].iloc[0]
    with pytest.raises(RuntimeError):                                    # one pot unread: no sum (never a smaller one)
        aero_v3.AeroV3(rpc=_VoterRpc(POTS, fail=4663), state={}).surplus_at(ADDR)
    assert config.AERO_GAUGE_CAPS["surplus_pots"]["total"] == pytest.approx(
        sum(config.AERO_GAUGE_CAPS["surplus_pots"]["tokens"].values()), abs=0.05)


def test_aero_surplus_history_fills_the_missing_days_oldest_first(monkeypatch):
    monkeypatch.setattr(aero_v3, "today", lambda: pd.Timestamp("2026-10-26"))
    p = config.PROJECT_BY_NAME[A]
    wrote = []
    r = aero_v3.AeroV3(rpc=_VoterRpc(POTS), state={})
    rows, why = aero_v3.surplus_history(p, {"2026-10-23"}, reader=r, state={"addresses": ADDR}, write=wrote.extend)
    assert [str(x["date"])[:10] for x in rows] == ["2026-10-22", "2026-10-24", "2026-10-25"]      # 10-23 is held
    assert len(wrote) == 3 and "reached yesterday" in why
    assert all(x["metric"] == "gauge_caps_surplus_tokens" and ":archive" in x["source"] for x in rows)
    # a day that cannot be read stops the pass and is named — no row, never 0
    bad = aero_v3.AeroV3(rpc=_VoterRpc(POTS, fail=10), state={})
    rows, why = aero_v3.surplus_history(p, set(), reader=bad, state={"addresses": ADDR})
    assert rows == [] and why.startswith("stopped at 2026-10-22")
    # the budget stops it between days
    t = iter([0.0, 5.0, 50.0, 500.0, 5000.0])
    rows, why = aero_v3.surplus_history(p, set(), deadline=10.0, reader=aero_v3.AeroV3(rpc=_VoterRpc(POTS), state={}),
                                        state={"addresses": ADDR}, clock=lambda: next(t))
    assert len(rows) == 2 and "time budget spent at 2026-10-24" in why


# ============================================================ AERODROME 3: holder value ==============================
EP = [TS("2026-10-22"), TS("2026-10-29"), TS("2026-11-05"), TS("2026-11-12")]
BASE = pd.Series([1_159_296.0, 1_426_324.0, 1_463_746.0, 1_641_087.0], index=EP)
LEAF_1105 = config.AERO_LEAF_CHAINS["scratch_read"]["epochs"]["2026-11-05"]
LEAF_1112 = config.AERO_LEAF_CHAINS["scratch_read"]["epochs"]["2026-11-12"]
CHAINS = tuple(config.AERO_LEAF_CHAINS["chains"])


def _leaf(epochs, skip=()):
    out = {}
    for c in CHAINS:
        idx = [e for e in epochs if (c, e) not in skip and not (c == "elysium" and e < TS("2026-11-05"))]
        vals = [float({TS("2026-11-05"): LEAF_1105, TS("2026-11-12"): LEAF_1112}.get(e, LEAF_1105)[c]) for e in idx]
        out[c] = (pd.Series(vals, index=idx), pd.Series(0.0, index=idx))
    return out


LEAF_FROM = {c: v.get("from_epoch") for c, v in config.AERO_LEAF_CHAINS["chains"].items()}


def test_aero_all_chain_series_only_when_every_chain_holds_every_epoch():
    assert len(CHAINS) == 7 and "elysium" in CHAINS and LEAF_FROM["elysium"] == "2026-11-05"
    ep, ready, why = bw.allchain_epochs(BASE, _leaf(EP), "2026-10-22", LEAF_FROM)
    assert ready and len(ep) == 4
    # week 11-12: Base 1,641,087 + the seven chains 347,760 = 1,988,847 (docs/r19: −3.7% against aero.xyz)
    assert ep[TS("2026-11-12")] == pytest.approx(1_641_087 + 347_760) == pytest.approx(1_988_847)
    assert ep[TS("2026-11-05")] == pytest.approx(1_463_746 + 320_970)
    assert sum(LEAF_1112[c] for c in CHAINS) == LEAF_1112["seven"]
    # Elysium is owed from its own first epoch only: 10-22 and 10-29 form without it
    assert ep[TS("2026-10-22")] == pytest.approx(1_159_296 + sum(LEAF_1105[c] for c in CHAINS if c != "elysium"))
    # ONE chain short on ONE epoch: not ready, that epoch forms no figure, and the hole is named
    ep2, ready2, why2 = bw.allchain_epochs(BASE, _leaf(EP, skip={("ink", TS("2026-10-29"))}), "2026-10-22", LEAF_FROM)
    assert not ready2 and TS("2026-10-29") not in ep2 and "ink 2026-10-29" in why2 and len(ep2) == 3
    # nothing of the other chains stored (Jake's store today): not ready
    none = {c: (None, None) for c in CHAINS}
    ep3, ready3, _ = bw.allchain_epochs(BASE, none, "2026-10-22", LEAF_FROM)
    assert not ready3 and ep3 == {}
    # a hole in the root chain's own epochs is a hole too
    _, ready4, why4 = bw.allchain_epochs(BASE.drop(TS("2026-11-05")), _leaf(EP), "2026-10-22", LEAF_FROM)
    assert not ready4 and "base 2026-11-05" in why4


def _hv_groups(with_leaf=False):
    days = pd.date_range("2026-10-08", "2026-11-18")
    g = {(A, "price_usd"): _rows(A, "price_usd", days, [0.5] * len(days), "coingecko", 1),
         (A, "revenue_usd"): _rows(A, "revenue_usd", days, [300_000.0] * len(days), "defillama", 1),
         (A, "holders_revenue_usd"): _rows(A, "holders_revenue_usd", days, [280_000.0] * len(days), "defillama", 1),
         (A, "voter_rewards_onchain_bribes_usd"): _rows(
             A, "voter_rewards_onchain_bribes_usd", [TS("2026-10-08"), TS("2026-10-15")] + EP,
             [220_189.0, 9_237.0, 230_944.0, 60_331.0, 24_010.0, 16_564.0], "aero_v3:epoch[317 managers]"),
         (A, "voter_rewards_onchain_usd"): _rows(A, "voter_rewards_onchain_usd", EP, BASE.values,
                                                "aero_v3:epoch[317 managers]")}
    if with_leaf:
        for c, (f, i) in _leaf(EP).items():
            g[(A, f"aero_leaf_fees_usd_{c}")] = _rows(A, f"aero_leaf_fees_usd_{c}", f.index, f.values, f"aero_leaf:{c}:epoch")
            g[(A, f"aero_leaf_incentives_usd_{c}")] = _rows(A, f"aero_leaf_incentives_usd_{c}", i.index, i.values,
                                                            f"aero_leaf:{c}:epoch")
    return g


def test_aero_holder_value_reads_defillama_holders_revenue_and_no_bribe_add_on(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[A]])
    g = _hv_groups()
    bw._holder_value_views(g)
    hv = g[(A, "holder_value_revenue_usd")].set_index("date")
    v, src = hv["value"], hv["source"]
    # before the merger: unchanged — DefiLlama revenue + the liquid bribes of the epoch
    assert v[TS("2026-10-08")] == pytest.approx(300_000.0 + 220_189.0) and v[TS("2026-10-09")] == pytest.approx(300_000.0)
    assert src[TS("2026-10-08")].startswith("derived:holder_value_with_liquid_bribes")
    # from 2026-10-22: DefiLlama's holders revenue, NO add-on (was 300,000 + 230,944 on the epoch day)
    assert v[TS("2026-10-22")] == pytest.approx(280_000.0) and v[TS("2026-10-29")] == pytest.approx(280_000.0)
    assert src[TS("2026-10-22")].startswith("derived:holder_value_all_chains_defillama")
    assert bw.HV_SWITCH_STATE[A]["ready"] is False
    week = v[(v.index >= TS("2026-11-12")) & (v.index <= TS("2026-11-18"))].sum()
    assert week == pytest.approx(7 * 280_000.0)                                       # was 7 x 300,000 + 16,564
    # the handover is declared, the four points in order
    ho = config.PROJECT_BY_NAME[A]["series_handover"]["holder_value_revenue_usd"]
    assert ho["declared"] == "2026-10-22" and len(ho["ordered_points"]) == 4
    assert [p for p in ho["ordered_points"][2:]] == list(config.HOLDER_VALUE[A]["extra_switch"]["points"])


def test_aero_holder_value_switches_to_the_on_chain_all_chain_series_when_hole_free(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[A]])
    g = _hv_groups(with_leaf=True)
    bw._holder_value_views(g)
    hv = g[(A, "holder_value_revenue_usd")].set_index("date")
    v, src = hv["value"], hv["source"]
    assert bw.HV_SWITCH_STATE[A]["ready"] is True and bw.HV_SWITCH_STATE[A]["epochs"] == 4
    assert src[TS("2026-10-22")].startswith("derived:holder_value_all_chains_onchain")
    wk = v[(v.index >= TS("2026-11-12")) & (v.index <= TS("2026-11-18"))]
    assert len(wk) == 7 and wk.sum() == pytest.approx(1_988_847.0)                    # the epoch, spread over its days
    assert v[TS("2026-10-21")] == pytest.approx(300_000.0)                            # before the merger: untouched
    al = g[(A, "voter_rewards_allchain_usd")].set_index("date")["value"]
    assert al.sum() == pytest.approx(BASE.sum() + sum(s[0].sum() for s in _leaf(EP).values()))
    # one chain-epoch missing: back on the stand-in, nothing mixed
    g2 = _hv_groups(with_leaf=True)
    k = (A, "aero_leaf_fees_usd_arc")
    g2[k] = g2[k][g2[k]["date"] != TS("2026-10-29")]
    bw._holder_value_views(g2)
    hv2 = g2[(A, "holder_value_revenue_usd")].set_index("date")
    assert bw.HV_SWITCH_STATE[A]["ready"] is False and hv2["value"][TS("2026-11-12")] == pytest.approx(280_000.0)
    assert not hv2["source"].astype(str).str.contains("all_chains_onchain").any()


# ----- the leaf reader ------------------------------------------------------------------------------------------------
T0 = aero_v3.topic0("NotifyFeesAmount")
TOK = "0x" + "aa" * 20
MGR = "0x" + "11" * 20
OTHER = "0x" + "22" * 20


class _LeafRpc:
    """One leaf chain: a registry with one factory, two gauges -> one manager; 1 s blocks from `t0`."""

    def __init__(self, t0, logs, programs=(), cap=None):
        self.t0, self.logs, self.programs, self.cap, self.calls = t0, logs, list(programs), cap, 0

    def __call__(self, chain, method, params):
        self.calls += 1
        k = aero_v3._keccak
        word = lambda a: "0" * 24 + a[2:]                                             # noqa: E731
        arr = lambda xs: "0x" + f"{32:064x}" + f"{len(xs):064x}" + "".join(word(x) for x in xs)   # noqa: E731
        if method == "eth_blockNumber":
            return hex(3_000_000)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(self.t0 + int(params[0], 16))}
        if method == "eth_getLogs":
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            if self.cap and hi - lo + 1 > self.cap:
                raise RuntimeError("block range too large")
            return [lg for lg in self.logs if lo <= lg["blockNumber"] <= hi]
        d = params[0]["data"]
        if d.startswith(k("factoryRegistry()")[:10]):
            return "0x" + word("0x" + "99" * 20)
        if d.startswith(k("gaugeFactories()")[:10]):
            return arr(["0x" + "77" * 20])
        if d.startswith(k("factoryToGauges(address)")[:10]):
            return arr(["0x" + "55" * 20, "0x" + "66" * 20])
        if d.startswith(k("gaugeToRewards(address)")[:10]):
            return "0x" + word(MGR)
        if d.startswith(k("incentiveCount()")[:10]):
            return "0x" + f"{len(self.programs):064x}"
        if d.startswith(k(aero_v3.CALLS["incentive"][0])[:10]):
            tok, amount, rate, s, e, creator = self.programs[int(d[10:], 16) - 1]
            return "0x" + word(tok) + f"{amount:064x}" + f"{rate:064x}" + f"{s:064x}" + f"{e:064x}" + word(creator)
        raise AssertionError(d[:10])


def _fee_log(block, raw, emitter=MGR, idx=0):
    return {"address": emitter, "blockNumber": block, "topics": [T0, "0x" + "0" * 64, "0x" + "0" * 24 + TOK[2:]],
            "data": hex(raw), "transactionHash": f"0x{block:064x}", "logIndex": hex(idx)}


def _leaf_spec(monkeypatch, start_ts):
    spec = {"locator": "0x" + "88" * 20, "from_epoch": str(pd.Timestamp(start_ts, unit="s").date()), "budget_s": 100,
            "epochs_back": 8, "chains": {"ink": {"chain_id": 57073, "llama": "ink", "span": 200_000, "start_block": 0,
                                                 "rpc": ("https://x",)}}}
    monkeypatch.setattr(config, "AERO_LEAF_CHAINS", spec)
    return spec


W = aero_v3.WEEK
E0 = 1_793_836_800 // W * W            # a Thursday 00:00 UTC


def test_leaf_epoch_is_fees_plus_prorated_incentives_all_or_no_row(monkeypatch):
    _leaf_spec(monkeypatch, E0)
    logs = [_fee_log(10, 2 * 10 ** 18), _fee_log(W - 5, 3 * 10 ** 18), _fee_log(W + 5, 9 * 10 ** 18),     # next epoch
            _fee_log(50, 7 * 10 ** 18, emitter=OTHER)]                                                   # not a manager
    # one programme of 1 token a second over the epoch's second half
    progs = [(TOK, 0, 10 ** 36, E0 + W // 2, E0 + 2 * W, OTHER)]              # the rate is per second x 1e18
    rpc = _LeafRpc(E0, logs, progs, cap=150_000)
    r = aero_leaf.AeroLeafEpochs(rpc=rpc, prices=lambda c, toks, ts: {TOK: (2.0, 18)}, now=E0 + W + 3600, workers=1,
                                 state={"launched": True})
    monkeypatch.setattr(aero_leaf, "today", lambda: pd.Timestamp("2026-11-13"))
    out = FetchOutput()
    r.run([config.PROJECT_BY_NAME[A]], None, out)
    f = out.frame().set_index("metric")
    assert set(f.index) == set(aero_leaf.metrics_of("ink")) and (pd.to_datetime(f["date"]) == TS(E0, unit="s")).all()
    assert f.loc["aero_leaf_fees_usd_ink", "value"] == pytest.approx(5.0 * 2.0)              # 2 + 3 tokens at $2
    assert f.loc["aero_leaf_incentives_usd_ink", "value"] == pytest.approx((W // 2) * 2.0)   # rate x overlap
    assert f.loc["aero_leaf_unpriced_count_ink", "value"] == 0
    assert f["source"].iloc[0].startswith("aero_leaf:ink:epoch")
    # the stored epoch is never read again
    stored = out.frame()
    rpc2 = _LeafRpc(E0, logs, progs)
    out2 = FetchOutput()
    aero_leaf.AeroLeafEpochs(stored_long=stored, rpc=rpc2, prices=lambda *a: {}, now=E0 + W + 3600, workers=1,
                             state={"launched": True}).run([config.PROJECT_BY_NAME[A]], None, out2)
    assert rpc2.calls == 0 and out2.frame().empty
    # an unpriced token is counted, never valued; a failed chain stores nothing
    out3 = FetchOutput()
    aero_leaf.AeroLeafEpochs(rpc=_LeafRpc(E0, logs, progs), prices=lambda *a: {}, now=E0 + W + 3600, workers=1,
                             state={"launched": True}).run([config.PROJECT_BY_NAME[A]], None, out3)
    f3 = out3.frame().set_index("metric")
    assert f3.loc["aero_leaf_unpriced_count_ink", "value"] == 1 and f3.loc["aero_leaf_fees_usd_ink", "value"] == 0

    def boom(chain, method, params):
        raise RuntimeError("endpoint down")
    out4 = FetchOutput()
    aero_leaf.AeroLeafEpochs(rpc=boom, now=E0 + W + 3600, workers=1, state={"launched": True}).run(
        [config.PROJECT_BY_NAME[A]], None, out4)
    assert out4.frame().empty and any(e.status == "failed" and "ink" in e.message for e in out4.log)


def test_leaf_budget_stops_between_chain_epochs_and_the_next_run_continues(monkeypatch):
    spec = _leaf_spec(monkeypatch, E0 - 2 * W)
    assert aero_leaf.epochs_due(E0 + W + 5, spec, "ink", set()) == [(E0, E0 + W), (E0 - W, E0), (E0 - 2 * W, E0 - W)]
    assert aero_leaf.epochs_due(E0 + W + 5, spec, "ink", {str(TS(E0, unit="s").date())}) == [(E0 - W, E0), (E0 - 2 * W, E0 - W)]
    monkeypatch.setattr(aero_leaf, "today", lambda: pd.Timestamp("2026-11-13"))
    clock = iter([0.0] + [1.0] * 6 + [1e9] * 50)                 # the budget is spent after the first epoch's reads
    rpc = _LeafRpc(E0 - 2 * W, [_fee_log(2 * W + 10, 10 ** 18)])
    out = FetchOutput()
    aero_leaf.AeroLeafEpochs(rpc=rpc, prices=lambda c, toks, ts: {TOK: (1.0, 18)}, now=E0 + W + 5, workers=1,
                             clock=lambda: next(clock), state={"launched": True}).run(
        [config.PROJECT_BY_NAME[A]], None, out)
    f = out.frame()
    assert f["date"].nunique() == 1 and pd.to_datetime(f["date"]).iloc[0] == TS(E0, unit="s")   # newest first, whole
    assert any(e.status == "warn" and "still owed" in e.message for e in out.log)
    assert fetch.TIER_BUDGET_S["aero_leaf"] == config.AERO_LEAF_TIER_BUDGET_S == 600 > 540        # the tier outlives its own stop
    assert any(t[0] == "aero_leaf" for t in fetch.TIER_ORDER)


# ============================================================ CHAINLINK ==============================================
def test_chainlink_flag_wording_is_filled_from_the_series():
    spec = config.THEATRE_FLAG["Chainlink"]
    r = bw.theatre_flag_state(77_600_000.0, 6_804_737.24, 6_804_737.24)
    head = bw.theatre_flag_headline(spec, r, r["as_written"])
    assert head.startswith("RAISED — 11.4x. Since the Reserve began (2025-08-07) Chainlink's published non-circulating "
                           "wallets sent 77.6M LINK to a Binance deposit address; the Reserve bought 6.8M (9% of that).")
    assert "scheduled 7%-a-year release" in head and "has not said what the exchange leg is for" in head
    assert r["as_written"]["size"] == pytest.approx(11.4038, abs=1e-4)
    # other figures give other words: nothing is typed
    r2 = bw.theatre_flag_state(58_850_000.0, 5_920_000.0, 5_920_000.0)
    assert bw.theatre_flag_headline(spec, r2, r2["as_written"]).startswith("RAISED — 9.9x.") and "58.9M LINK" in \
        bw.theatre_flag_headline(spec, r2, r2["as_written"])
    # not stored (the fixture dump): NOT TESTABLE, no headline
    r3 = bw.theatre_flag_state(None, None, None)
    assert bw.theatre_flag_headline(spec, r3, r3["as_written"]) == ""
    bw.THEATRE_RESULTS.pop("Chainlink", None)
    assert bw.theatre_flag_text("Chainlink").startswith("SCORED — AS WRITTEN (tokens RETIRED by buyback): NOT TESTABLE")
    # a project without the wording reads as before
    assert bw.theatre_flag_headline(config.THEATRE_FLAG["Ether.fi"], r, r["as_written"]) == ""


def test_chainlink_second_payment_path_split_rule():
    """Of the Safe 0x77dd1a9b…'s 2,082,892 direct LINK: 1,351,928 to recipients of the Reserves contract is node
    payments (b); 730,964 to 532 other addresses stays in unlocks (e)."""
    sp = config.CHAINLINK_NODE_PAYMENTS["second_path"]
    assert sp["read"]["to_the_63"] + sp["read"]["to_others"] == pytest.approx(2_082_892.0)
    assert sp["read"]["to_the_63"] == 1_351_928.0 and sp["read"]["to_others"] == 730_964.0
    assert sum(sp["read"]["by_quarter_to_the_63"].values()) == pytest.approx(1_351_928, abs=2)
    scan = next(s for s in config.PROJECT_BY_NAME["Chainlink"]["log_scans"] if s["key"] == "node_payments_safe_out")
    assert scan["holders"] == [sp["safe"]] and scan["direction"] == "out" and scan["store"] is True
    rec = {a.lower() for a in scan["count_to"]}
    assert len(rec) == 63 == len(config.CHAINLINK_NODE_RECIPIENTS)
    # the rule the scan applies (fetch.logscan count_to): only a transfer TO one of the 63 counts
    transfers = [("0x001e0d294383d5b4136476648acc8d04a6461ae3", 2_617.0),          # one of the 63
                 ("0xfdc770353dc0bfce80a17ab8a6a2e7d80590f1ba", 1_000.0),          # one of the 63
                 ("0x" + "ab" * 20, 40.0),                                          # another address: stays in (e)
                 (config._CL_PAL.lower(), 500.0)]                                   # the Reserves contract: its own scan
    counted = sum(v for to, v in transfers if to in rec)
    assert counted == pytest.approx(3_617.0)
    assert config.outflow_metrics("Chainlink", "b") == ("node_operator_payments_tokens",
                                                        "node_operator_payments_safe_tokens")
    assert config.RELEASE_PAID_LATER["Chainlink"]["paid"] == config.outflow_metrics("Chainlink", "b")
    assert "INFERRED" not in config.CHAINLINK_NODE_PAYMENTS["recipients_label"]
    assert "path PUBLISHED (Payment Abstraction post 2025-03-31), addresses not named" in \
        config.CHAINLINK_NODE_PAYMENTS["recipients_label"]


def test_chainlink_both_payment_paths_leave_the_release_fifo():
    """The lump of 1,875,000 to 0xD50f074E… pays 300 through the contract and 200 through the Safe: (e) loses 500 on
    the lump's day; a path with no row yet takes nothing."""
    C = "Chainlink"
    days = pd.date_range("2026-10-08", "2026-10-20")
    total = pd.Series(0.0, index=days)
    total[TS("2026-10-09")] = 1_875_000.0
    g = {(C, "staking_path_in_tokens"): _rows(C, "staking_path_in_tokens", ["2026-10-09"], [1_875_000.0], "explorer:x"),
         (C, "node_operator_payments_tokens"): _rows(C, "node_operator_payments_tokens", ["2026-10-15"], [300.0], "explorer:a"),
         (C, "node_operator_payments_safe_tokens"): _rows(C, "node_operator_payments_safe_tokens", ["2026-10-16"],
                                                          [200.0], "explorer:b")}
    pl = dict(config.RELEASE_PAID_LATER[C], after=None)
    out, src = bw._paid_later_split(dict(g), C, pl, total, g[(C, "staking_path_in_tokens")].columns)
    assert out[TS("2026-10-09")] == pytest.approx(1_875_000.0 - 500.0) and "node_payments_release_tokens" in src
    g1 = {k: v for k, v in g.items() if k[1] != "node_operator_payments_safe_tokens"}
    out1, _ = bw._paid_later_split(g1, C, pl, total, g[(C, "staking_path_in_tokens")].columns)
    assert out1[TS("2026-10-09")] == pytest.approx(1_875_000.0 - 300.0)               # was the only path (R18)


def test_chainlink_exchange_leg_is_category_f_and_total_dilution_is_unchanged(monkeypatch):
    C = "Chainlink"
    assert config.outflow_metrics(C, "f") == ("issuer_exchange_outflow_tokens",)
    assert config.release_less(C) == (("issuer_exchange_outflow_tokens",), False)
    assert config.outflow_metrics("Ether.fi", "f") == ("etherfi_named_float_outflow_tokens",)       # as Ether.fi's
    assert "Chainlink" in config.METRICS["outflow_f_tokens"]["only_projects"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[C]])
    days = pd.date_range("2026-10-08", "2026-10-12")
    # the release day: the 33 wallets fall by 18,750,000; 16,875,000 of it goes to the Binance-tagged deposit address
    rel = [0.0, 18_750_000.0, 0.0, 0.0, 0.0]
    ex = [0.0, 16_875_000.0, 0.0, 0.0, 0.0]

    def groups(with_f):
        e = [r - x for r, x in zip(rel, ex)] if with_f else rel
        g = {(C, "price_usd"): _rows(C, "price_usd", days, [12.7] * 5, "coingecko", 1),
             (C, "emissions_tokens"): _rows(C, "emissions_tokens", days, [5_500.0] * 5, "reward_vault"),
             (C, "unlock_release_tokens"): _rows(C, "unlock_release_tokens", days, e, "derived:unlock_release")}
        if with_f:
            g[(C, "issuer_exchange_outflow_tokens")] = _rows(C, "issuer_exchange_outflow_tokens", days, ex, "explorer:x")
        return g
    g0, g1 = groups(False), groups(True)
    bw._outflow_views(g0)
    # not stored (the fixture dump, Jake's store before the run): (f) blocks nothing — still wholly inside (e)
    assert (C, "f") in bw.PAID_LATER_NOT_READ and (C, "b") in bw.PAID_LATER_NOT_READ
    assert (C, "outflow_f_tokens") not in g0
    e0 = g0[(C, "outflow_e_tokens")]["value"].sum()
    bw._outflow_views(g1)
    assert (C, "f") not in bw.PAID_LATER_NOT_READ
    e1, f1 = g1[(C, "outflow_e_tokens")]["value"].sum(), g1[(C, "outflow_f_tokens")]["value"].sum()
    assert f1 == pytest.approx(16_875_000.0) and e1 == pytest.approx(1_875_000.0)
    assert e1 + f1 == pytest.approx(e0) == pytest.approx(18_750_000.0)               # (e) falls by what (f) holds
    assert g1[(C, "outflow_f_usd")]["value"].sum() == pytest.approx(16_875_000.0 * 12.7)


def test_chainlink_labels():
    d = config.THEATRE_FLAG["Chainlink"]["destinations"][config._CL_FWD_EXCHANGE]
    assert "gas paid by Binance's deposit funder; receives only from the 33" in d["tag"]
    assert d["senders"] == {"count": 19, "all_among_the_33": True, "received_all_time": 253_800_000.0}
    assert sum(v for _d, v in d["gas_payer"]["payments"]) == pytest.approx(0.06)
    memo = config.FLOW_TOKEN_MEMO["Chainlink"]
    assert not any("stays inside unlocks (e)" in why for _l, _m, why in memo)


# ============================================================ GEODNET ================================================
def test_geodnet_fees_equal_revenue_equal_burn_over_08(monkeypatch):
    G = "GEODNET"
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[G]])
    f = config.S13_INFERRED[G]
    assert f["s13_fees_inferred_usd"] == ("actual_buyback_usd", pytest.approx(1.25))          # was 2.5 (burn ÷ 0.4)
    assert f["s13_revenue_inferred_usd"] == ("actual_buyback_usd", pytest.approx(1.25))
    days = pd.date_range("2026-07-16", "2026-10-13")
    burn = 2_210_124.0 / len(days)
    g = {(G, "actual_buyback_usd"): _rows(G, "actual_buyback_usd", days, [burn] * len(days), "derived:tokens*price")}
    bw._s13_inferred_views(g)
    fees, rev = (g[(G, m)]["value"].sum() for m in ("s13_fees_inferred_usd", "s13_revenue_inferred_usd"))
    # the dump's 90 days to 2026-10-13: holder revenue 2,210,124; revenue 2,762,655; fees 2,762,655 (was 5,525,310)
    assert rev == pytest.approx(2_762_655.0) and fees == pytest.approx(2_762_655.0)
    assert 2_210_124.0 / rev == pytest.approx(0.80) and 2_210_124.0 / fees == pytest.approx(0.80)   # was 0.40 of fees
    rs = config.REVENUE_SPLIT[G]
    assert rs["fees"]["inferred"] and rs["revenue"]["inferred"] and rs["holder_revenue"]["primary"]
    for line in ("fees", "revenue"):
        assert "INFERRED" in rs[line]["route"] and "Blockworks" in rs[line]["route"]
        assert "$4M" in rs[line]["route"] and "3,978,525" in rs[line]["route"]
    fp = rs["first_party"]
    assert fp["burn_usd_month"] * 12 / 0.8 == pytest.approx(fp["implied_revenue_annualised"], abs=1)
    assert fp["implied_revenue_annualised"] / fp["figure_usd_annualised"] - 1 == pytest.approx(-0.0054, abs=5e-4)
    assert fp["burn_month"] == "2025-05"


def test_geodnet_uniformity_is_declared_na():
    e = next(x for x in config.DECLARED_NA_REASONS["GEODNET"] if "UNIFORMITY" in x["cols"])
    assert e["match"] and e["match"] in e["text"] and e["source"] and e["read_on"] and "Jake 2026-11-24" in e["decided"]
    assert "+20% GEOD" in e["text"]                                       # the fact stays on the cell
    text = config.declared_na_text("GEODNET", "UNIFORMITY — staker yield")
    assert config.declared_na_reason("GEODNET", text) is e                # done_check accepts it


# ============================================================ TOKENJAR ===============================================
def test_tokenjar_has_the_time_one_day_needs():
    spec = {**tokenjar.DEFAULTS, **config.TOKENJAR_INFLOWS["Uniswap"]}
    m = config.TOKENJAR_MEASURED
    assert m["calls"] == 870 and m["seconds"] == pytest.approx(231.1)     # one day, twelve chains, keyless (measured)
    assert spec["budget_s"] == 780 and spec["balance_budget_s"] == 60     # was 240, and no slice for the balances
    assert spec["budget_s"] >= 3 * m["seconds"]                           # three days owed fit
    tier = fetch._budget("tokenjar")
    assert tier == 900 == config.TOKENJAR_TIER_BUDGET_S                   # was 300
    assert spec["budget_s"] + spec["balance_budget_s"] + 60 <= tier       # the rows are emitted before the tier's limit


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _jar(tmp_path, clock, post):
    r = tokenjar.TokenJarInflows(rpc_post=post, clock=clock, root=tmp_path, env_rpcs={})
    r.spec = {**tokenjar.DEFAULTS, "state_path": "state.json", "prices_path": "prices.json", "budget_s": 100,
              "balance_budget_s": 20, "balance_window_days": 3,
              "chains": {"a": {"jar": "0x" + "0a" * 20, "from_day": "2026-11-01", "logs": [{"kind": "rpc"}],
                               "state_rpc": ["https://a"], "llama": "a", "native_coin": "coingecko:ethereum"},
                         "b": {"jar": "0x" + "0b" * 20, "from_day": "2026-11-01", "logs": [{"kind": "rpc"}],
                               "state_rpc": ["https://b"], "llama": "b", "native_coin": "coingecko:ethereum"}}}
    r.st = {"version": 1, "chains": {}, "balances": {}}
    r.prices = {}
    r._deadline = clock() + 100
    r._hard = r._deadline + 20
    return r


def test_tokenjar_no_call_starts_after_the_hard_stop(tmp_path):
    clock, asked = _Clock(), []

    def post(url, payload):
        asked.append(payload["method"])
        return {"result": "0x10"}
    r = _jar(tmp_path, clock, post)
    assert r.rpc("https://a", "eth_blockNumber", []) == "0x10" and r._left() == 100 and r._hard_left() == 120
    clock.t = 110.0                                   # past the soft budget, inside the balance slice: still asked
    assert r._left() < 0 < r._hard_left() and r.rpc("https://a", "eth_blockNumber", []) == "0x10"
    clock.t = 120.0                                   # the hard stop: nothing more is asked
    with pytest.raises(tokenjar.RouteFailed, match="hard stop"):
        r.rpc("https://a", "eth_blockNumber", [])
    with pytest.raises(tokenjar.RouteFailed, match="hard stop"):
        r._get("https://coins.llama.fi/prices/historical/1/x", {})
    assert len(asked) == 2


def test_tokenjar_http_waits_are_cut_to_what_is_left(tmp_path):
    clock = _Clock()
    r = tokenjar.TokenJarInflows(clock=clock, root=tmp_path, env_rpcs={})
    r._deadline, r._hard = 100.0, 120.0
    t0, w0 = r.http.timeout, r.http.max_retry_wait
    r._guard(r.http)
    assert (r.http.timeout, r.http.max_retry_wait) == (min(t0, 120.0), min(w0, 120.0))
    clock.t = 113.0                                   # 7 s left: no read may wait longer, no retry sleeps past it
    r._guard(r.http)
    assert r.http.timeout == pytest.approx(7.0) and r.http.max_retry_wait == pytest.approx(7.0)
    # a seed has no limit: nothing is cut
    r2 = tokenjar.TokenJarInflows(clock=clock, root=tmp_path, env_rpcs={})
    r2._deadline = r2._hard = float("inf")
    r2._guard(r2.http)
    assert (r2.http.timeout, r2.http.max_retry_wait) == (t0, w0)


def test_tokenjar_a_held_balance_day_is_not_read_again(tmp_path, monkeypatch):
    clock = _Clock()
    r = _jar(tmp_path, clock, lambda u, p: {"result": "0x0"})
    for c in ("a", "b"):
        r.cst(c)["days"]["2026-11-01"] = {"stage": "partial"}            # the history is not complete yet
    r.cst("a")["seen"], r.cst("b")["seen"] = ["0x1", "0x2"], ["0x3"]
    reads = []

    def read_balance(chain, cs, day):
        reads.append((chain, day))
        return {"usd": 1.0, "block": 1, "held_kinds": 1, "unpriced": [], "n_unpriced": 0,
                "seen_kinds": len(r.cst(chain)["seen"]), "seen_from": None}
    monkeypatch.setattr(r, "read_balance", read_balance)
    r._balances("2026-11-10", r.spec, [])
    assert sorted(reads) == [("a", "2026-11-07"), ("a", "2026-11-10"), ("b", "2026-11-07"), ("b", "2026-11-10")]
    reads.clear()
    r._balances("2026-11-10", r.spec, [])             # the same run again: nothing new to ask, nothing read
    assert reads == []                                # was: all four again, on every run until the history was read
    r.cst("b")["seen"].append("0x4")                  # an older day read since saw a new token on b
    r._balances("2026-11-10", r.spec, [])
    assert sorted(reads) == [("b", "2026-11-07"), ("b", "2026-11-10")]
    # past the hard stop a chain still owed is named and its held read is kept
    reads.clear()
    r.cst("a")["seen"].append("0x5")
    clock.t = 500.0
    r._balances("2026-11-10", r.spec, [])
    rec = r.st["balances"]["2026-11-10"]
    assert reads == [] and "a" in rec["not_read"] and rec["per"]["a"]["seen_kinds"] == 2 and not rec["complete"]


def test_tokenjar_soft_stop_starts_no_new_chain_day_and_what_was_read_is_emitted(tmp_path, monkeypatch):
    clock = _Clock()
    r = tokenjar.TokenJarInflows(rpc_post=lambda u, p: {"result": "0x0"}, clock=clock, root=tmp_path, env_rpcs={})
    spec = {**tokenjar.DEFAULTS, "state_path": "state.json", "prices_path": "prices.json", "budget_s": 100,
            "balance_budget_s": 20, "balance_window_days": 3, "metric": "tokenjar_inflow_usd",
            "count_metric": "tokenjar_inflow_transfers", "unpriced_metric": "tokenjar_unpriced_transfers",
            "balance_metric": "tokenjar_balance_usd",
            "chains": {"a": {"jar": "0x" + "0a" * 20, "from_day": "2026-11-01", "logs": [{"kind": "rpc"}],
                             "state_rpc": ["https://a"], "llama": "a", "native_coin": "coingecko:ethereum"}}}
    monkeypatch.setattr(tokenjar, "today", lambda: pd.Timestamp("2026-11-11"))
    days_read = []

    def read_day(chain, cs, day):
        days_read.append(day)
        clock.t += 60.0                               # a chain-day costs 60 s
        return {"stage": "read", "b0": 1, "b1": 2, "route": "x", "usd": 5.0, "lp_usd": 0.0, "lp_one_side_usd": 0.0,
                "transfers": 1, "unpriced_transfers": 0, "unpriced": [], "n_unpriced_kinds": 0, "kinds": 1,
                "native": ["0", 0], "native_route": "x", "native_usd": 0.0}
    monkeypatch.setattr(r, "read_day", read_day)
    monkeypatch.setattr(r, "read_balance", lambda chain, cs, day: {
        "usd": 1.0, "block": 1, "held_kinds": 0, "unpriced": [], "n_unpriced": 0, "seen_kinds": 0, "seen_from": None})
    out = FetchOutput()
    r._project("Uniswap", spec, out, unbounded=False)
    assert days_read == ["2026-11-10", "2026-11-09"]                      # the third would start past the soft stop
    f = out.frame()
    got = f[f["metric"] == "tokenjar_inflow_usd"]
    assert sorted(pd.to_datetime(got["date"]).dt.strftime("%Y-%m-%d")) == ["2026-11-09", "2026-11-10"]
    # the next run resumes where this one stopped: the two days are held, the third is read
    clock.t = 0.0
    days_read.clear()
    out2 = FetchOutput()
    r2 = tokenjar.TokenJarInflows(rpc_post=lambda u, p: {"result": "0x0"}, clock=clock, root=tmp_path, env_rpcs={})
    monkeypatch.setattr(r2, "read_day", read_day)
    monkeypatch.setattr(r2, "read_balance", r.read_balance)
    r2._project("Uniswap", spec, out2, unbounded=False)
    assert days_read == ["2026-11-08", "2026-11-07"]


# ============================================================ PENDLE: Berachain ======================================
def test_berachain_chain_record_reconciles_to_the_wei():
    """Read 2026-11-23 on rpc.berachain.com: every Transfer in and out of the gaugeController closes to balanceOf."""
    r = config.PENDLE_BERACHAIN_RECONCILED
    assert r["in_tokens"] - r["out_tokens"] == pytest.approx(r["balance_of"], abs=1e-9) and r["difference_wei"] == 0
    assert r["out_events"] == 742_058 and r["in_events"] == 1
    assert config.REPAIR_CHUNK_BLOCKS["berachain"] == 10_000


class _W3:
    def __init__(self, fn):
        self.eth = self
        self._fn = fn

    def get_logs(self, flt):
        return self._fn(flt)


def _reader(monkeypatch, answers: dict):
    """A ChainReader whose endpoints answer eth_getLogs as `answers[host](filter)` says."""
    from fetch import chain as ch
    r = ch.ChainReader.__new__(ch.ChainReader)
    r.__dict__.update({"_w3_url": {}, "_w3_by_url": {}})
    urls = [f"https://{h}" for h in answers]
    monkeypatch.setattr(ch, "rpc_endpoints", lambda chain: list(urls))
    monkeypatch.setattr(ch.ChainReader, "make_web3", lambda self, chain, u, t=60: _W3(answers[u.split("/")[2]]))
    monkeypatch.setattr(ch.ChainReader, "checksum", staticmethod(lambda a: a), raising=False)
    monkeypatch.setattr(ch.ChainReader, "getlogs_cap", staticmethod(lambda u: None))
    return r


def _logs(flt):
    return [{"blockNumber": b, "blockTimestamp": hex(1_750_000_000 + b), "logIndex": 0, "transactionHash": "0x" + f"{b:064x}",
             "topics": [], "data": "0x1"} for b in range(flt["fromBlock"], flt["toBlock"] + 1) if b % 1_000 == 0]


def test_a_pruned_nodes_empty_answer_is_not_the_chains_record(monkeypatch):
    """Reproduced live before the fix: dated_logs(9,000,000..9,039,999) returned 0 logs — publicnode's [] — where the
    chain holds 5,532 payouts. rpc.berachain.com refuses a range over 10,000 blocks with HTTP 413."""
    asked = []

    def primary(flt):
        asked.append(("primary", flt["toBlock"] - flt["fromBlock"] + 1))
        if flt["toBlock"] - flt["fromBlock"] + 1 > 10_000:
            raise RuntimeError("413 Client Error: Request Entity Too Large for url: https://rpc.berachain.com/")
        return _logs(flt)

    def pruned(flt):
        asked.append(("pruned", flt["toBlock"] - flt["fromBlock"] + 1))
        return []
    r = _reader(monkeypatch, {"rpc.berachain.com": primary, "berachain-rpc.publicnode.com": pruned})
    trace, got, cur = [], [], 9_000_000
    while cur <= 9_039_999:
        part, upper = r.dated_logs("berachain", "0xtoken", ["0xt"], cur, 9_039_999, 2_000_000, trace)
        got += part
        cur = upper + 1
    assert len(got) == 40 and not any(who == "pruned" for who, _n in asked)       # was 0 logs, from the pruned node
    assert "berachain-rpc.publicnode.com" in config.RPC_PRUNED_LOGS
    assert all("rpc.berachain.com" in t for t in trace)


def test_an_empty_answer_does_not_end_the_search_while_another_endpoint_is_to_be_asked(monkeypatch):
    monkeypatch.setattr(config, "RPC_PRUNED_LOGS", {})                 # an UNDECLARED pruned node, asked first
    r = _reader(monkeypatch, {"pruned.example": lambda flt: [], "full.example": _logs})
    trace = []
    got, upper = r.dated_logs("berachain", "0xtoken", ["0xt"], 1_000, 5_999, 5_000, trace)
    assert len(got) == 5 and upper == 5_999 and "full.example" in trace[-1]
    # a range every endpoint calls empty IS empty
    r2 = _reader(monkeypatch, {"a.example": lambda flt: [], "b.example": lambda flt: []})
    got2, upper2 = r2.dated_logs("berachain", "0xtoken", ["0xt"], 1_000, 5_999, 5_000, [])
    assert got2 == [] and upper2 == 5_999
    from fetch.chain import ChainReader
    assert "request entity too large" in ChainReader.LOGS_RANGE_TOO_WIDE
