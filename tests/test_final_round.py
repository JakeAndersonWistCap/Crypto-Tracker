"""
tests/test_final_round.py — the final data round before the theory phase (Jake, 2026-10-11): decisions A1-A7 on
5b87610, the Aerodrome buyback basis (B), NEAR's still-unvested lockups (C), the theory-ready outputs (D) and Jake's
sanity hits on his store (E). Real dumps (fixtures/real_*.json) and real captured chain data wherever they exist; NEAR
has no dump, so its view is driven by its own rows' shape.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def _build(tmp_path, monkeypatch, project, extra_rows=(), asof=None):
    """Build the real dump (plus rows appended from a named real reading) and return (wb, value)."""
    import openpyxl
    import xlcalc
    import store as sm
    import build_workbook as bw
    from real_dump import load, to_store
    payload = load(project)
    payload["metrics"] = list(payload["metrics"]) + list(extra_rows)
    monkeypatch.setitem(sys.modules, "recalc", None)
    monkeypatch.setattr(bw, "_SCOPE", bw._SCOPE)          # a narrowed build must not leak its scope into other tests
    db = tmp_path / "metrics.db"
    to_store(payload, db)
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    out = tmp_path / "wb.xlsx"
    bw.build_workbook(sm.Store(str(db)), out, asof=pd.Timestamp(asof or payload["meta"]["asof"]),
                      only=[payload["meta"]["project"]])
    wb = openpyxl.load_workbook(out)
    X = xlcalc.Workbook(wb)
    return wb, (lambda s, r, c: xlcalc.display(X.value(s, r, c)))


def _cell(wb, value, tab, project, header_prefix):
    ws = next(w for w in wb.worksheets if w.title.startswith(tab))
    hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
    col = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(hr, c).value or "").startswith(header_prefix))
    row = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return value(ws.title, row, col)


# ----------------------------------------------------------------------------------------------- E: sanity hits
def test_zero_split_reads_none_not_active():
    """Jake's sanity run: Bitcoin, Zcash and Maple read burn 'active (declared; no burn series measured)' — a 0% split
    is no burn (decision A6 for Maple)."""
    import build_workbook as bw
    st = bw.derived_statuses(None)
    for n in ("Bitcoin", "Zcash", "Maple"):
        assert st[n]["burn"] == "none (declared: 0% of fees burned)", (n, st[n])


def test_documented_funding_for_near_and_uniswap():
    import config
    for n, host in (("Near", "revenue.near.org"), ("Uniswap", "docs.uniswap.org")):
        f = config.PROJECT_BY_NAME[n]["buyback_funding"]
        assert host in f["source_url"] and f["source_date"] and f["what"]


def test_pancakeswap_dead_address_inflow_is_not_a_buyback():
    import config
    r = config.buyback_route("PancakeSwap")
    assert r["route"] == "none" and "MasterChef" in r["reason"] and "NOT MEASURED" in r["reason"]
    assert config.unavailable_for("PancakeSwap", "gross_burn_tokens")["reopen_if"]
    sql = (ROOT / "orphan_cleanup.sql").read_text(encoding="utf-8")
    assert "-- CH1." in sql and "\n-- DELETE FROM metrics\n--  WHERE project = 'PancakeSwap'" in sql


def test_fdv_is_price_times_total_and_never_below_market_cap_on_real_dumps(tmp_path, monkeypatch):
    """Bitcoin / Ethereum / Canton read FDV < market cap (CoinGecko's FDV at its own price and time): FDV = our price x
    the total supply we use, the same day, for every project."""
    wb, value = _build(tmp_path, monkeypatch, "Pendle")
    fdv, mcap = _cell(wb, value, "Master", "Pendle", "FDV ($)"), _cell(wb, value, "Master", "Pendle", "Market cap ($)")
    px = _cell(wb, value, "Master", "Pendle", "Price — spot ($)")
    ws = wb["Data"]
    total = next(value("Data", r, 10) for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "Pendle|total_supply")
    assert fdv == pytest.approx(px * total, rel=1e-9) and fdv >= mcap


def test_annualised_net_is_blocked_with_the_total():
    """mixed_windows (Hyperliquid 43.656%, Venice AI 4.704% beside a BLOCKED net): the annualised % carries the same
    unequal-coverage condition as the total."""
    import build_workbook as bw

    class R:
        @staticmethod
        def D(r, m, w):
            return f"D[{m},{w}]"
    import config
    # alignment round B8: Hyperliquid is pre-minted — its net issuance is mint − burn = −burn, one leg, so the guard is
    # the burn's own coverage (no second leg to be unequal with); a minting project keeps the two-leg guard
    f = bw._net_annual_pct(R, 5, config.PROJECT_BY_NAME["Hyperliquid"], "CIRC")
    assert "BLOCKED" in f and "pool_release_tokens" not in f and "total_burn_tokens" in f
    f = bw._net_annual_pct(R, 5, config.PROJECT_BY_NAME["Venice AI"], "CIRC")
    assert "ABS(" in f and "BLOCKED" in f


# ----------------------------------------------------------------------------------------------- A1 Hyperliquid
class _HypeHttp:
    """The two wallets' answers as read live on 2026-10-11."""
    def __init__(self, fail=None):
        self.fail = fail

    def post(self, endpoint, json_body=None):
        u, t = (json_body or {}).get("user"), (json_body or {}).get("type")
        if u == self.fail:
            raise RuntimeError("HTTP 500")
        spot = {"0xd57ecca444a9acb7208d286be439de12dd09de5d": "50000.00000044",
                "0xa20fcfa0507fe762011962cc581b95bbbc3bbdba": "105501.0"}
        deleg = {"0xd57ecca444a9acb7208d286be439de12dd09de5d": "60501988.6402254328",
                 "0xa20fcfa0507fe762011962cc581b95bbbc3bbdba": "2868230.9850819199"}
        if t == "spotClearinghouseState":
            return {"balances": [{"coin": "HYPE", "total": spot[u]}, {"coin": "USDC", "total": "1"}]}
        return {"delegated": deleg[u], "undelegated": "0.0", "totalPendingWithdrawal": "0.0"}


def _hype_read():
    import config
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    api = p["node_api"]
    read = next(r for r in api["extra_reads"] if r.get("exclude_wallets"))
    return api, read


def test_hyperliquid_circulating_excludes_foundation_and_grants():
    """Decision A1: tokenDetails' 302,167,698 less the Foundation (60,551,988.64) and Grants (2,973,731.99) wallets."""
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo
    import config
    api, read = _hype_read()
    a = HyperCoreInfo()
    a.http = _HypeHttp()
    out = FetchOutput()
    a._circ_less_wallets("Hyperliquid", api, read, 302_167_698.0, read["exclude_wallets"], out, pd.Timestamp("2026-10-11"))
    f = out.frame().set_index("metric")
    assert f.loc["excluded_wallets_tokens", "value"] == pytest.approx(63_525_720.63, abs=0.01)
    assert f.loc["circulating_supply_first_party", "value"] == pytest.approx(238_641_977.37, abs=0.01)
    assert f.loc["circulating_supply_tokendetails", "value"] == 302_167_698.0
    h = config.declared_handover("Hyperliquid", "circulating_supply_first_party")
    from fetch.base import _measuring_point
    assert _measuring_point(f.loc["circulating_supply_first_party", "source"]) == h["ordered_points"][1]


def test_hyperliquid_unread_wallet_stores_no_circulating():
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo
    api, read = _hype_read()
    a = HyperCoreInfo()
    a.http = _HypeHttp(fail="0xa20fcfa0507fe762011962cc581b95bbbc3bbdba")
    out = FetchOutput()
    a._circ_less_wallets("Hyperliquid", api, read, 302_167_698.0, read["exclude_wallets"], out, pd.Timestamp("2026-10-11"))
    f = out.frame()
    assert "circulating_supply_first_party" not in set(f["metric"]) if not f.empty else True
    assert any("NOT STORED" in e.message for e in out.log)


# ----------------------------------------------------------------------------------------------- A4 / A5 / A7
def test_total_supply_on_chain_first_for_the_eight_and_aethir_since_14_oct():
    """Aethir joined on Jake's decision of 2026-10-14 (round F, F3): Ethereum's canonical totalSupply, PARTIAL for Solana."""
    import config
    assert set(config.TOTAL_SUPPLY_ONCHAIN_FIRST) == {"Ether.fi", "Aerodrome", "Chainlink", "Fluid", "Maple", "Morpho",
                                                      "Pendle", "Sky", "Aethir"}
    assert config.TOTAL_SUPPLY_ONCHAIN_FIRST["Aethir"]["decided_on"] == "2026-10-14"
    for n in config.TOTAL_SUPPLY_ONCHAIN_FIRST:
        assert config.declared_handover(n, "total_supply")["ordered_points"][0] == "coingecko"


def test_total_supply_collision_keeps_the_chain_read_on_the_real_pendle_dump():
    """Pendle's dump carries the daily collision (chain:ethereum:token dropped for CoinGecko): now the chain row is kept
    (logged, not raised)."""
    import fetch
    from fetch.base import FetchOutput
    from real_dump import load
    rq = [x for x in load("Pendle")["review_queue"] if x["metric"] == "total_supply" and x["reason"] == "tier_collision"]
    x = rq[-1]
    row = lambda v, src, t: {"date": pd.Timestamp(x["date"]), "project": "Pendle", "metric": "total_supply",  # noqa: E731
                             "value": v, "source": src, "tier": t}
    out = FetchOutput()
    out.frames = [pd.DataFrame([row(x["prior_value"], "coingecko", 1)]),
                  pd.DataFrame([row(x["value"], "chain:ethereum:token", 2)])]
    fetch._resolve_tier_collisions(out)
    f = out.frame()
    assert list(f["source"]) == ["chain:ethereum:token"] and f["value"].iloc[0] == x["value"]
    assert not [r for r in out.review if r["reason"] == "tier_collision"]


def test_morpho_dao_legacy_and_base_wired_ratios_on_the_issuer_rule():
    import config
    c = config.PROJECT_BY_NAME["Morpho"]["contracts"]
    leg, base = c["noncirc_dao_treasury_legacy"], c["noncirc_dao_treasury_base"]
    assert (leg["chain"], c[leg["underlying"]]["chain"]) == ("ethereum", "ethereum")
    assert (base["chain"], c[base["underlying"]]["chain"]) == ("base", "base")
    assert c["token_legacy"]["kind"] == c["token_base"]["kind"] == "bridged_representation"
    assert config._MORPHO_ORG.endswith("/learn/governance/organization/")
    # Jake 2026-10-12 (decision 1 on 6f8c86c): the issuer rule — the on-chain set, issuer-held entities out
    assert config.circulating_primary_metric("Morpho") == "circulating_supply_onchain"
    spec = config.circulating_onchain("Morpho")
    assert spec["subtract"] == ("noncirculating_holding_tokens", "noncirculating_entities_tokens")
    assert config.coingecko_counts_holdings("Morpho") == ("noncirculating_entities_tokens",)


class _MorphoHttp:
    """Liquidations shaped as blue-api answered on 2026-10-11 (two listed, one unlisted, one unpriced)."""
    def post(self, endpoint, json_body=None):
        return {"data": {"marketTransactions": {"pageInfo": {"countTotal": 4}, "items": [
            {"market": {"marketId": "0xa", "listed": True, "lltv": "860000000000000000",
                        "collateralAsset": {"decimals": 18, "priceUsd": 2525.2035}},
             "data": {"seizedAssets": "17513662030281839"}},
            {"market": {"marketId": "0xb", "listed": True, "lltv": "915000000000000000",
                        "collateralAsset": {"decimals": 18, "priceUsd": None}},
             "data": {"seizedAssets": "381729245213535400"}},
            {"market": {"marketId": "0xc", "listed": False, "lltv": "770000000000000000",
                        "collateralAsset": {"decimals": 18, "priceUsd": 1.07}},
             "data": {"seizedAssets": "342115356157564416"}},
            {"market": {"marketId": "0xd", "listed": True, "lltv": "945000000000000000",
                        "collateralAsset": {"decimals": 6, "priceUsd": 1.0}},
             "data": {"seizedAssets": "1000000000"}}]}}}


def test_morpho_liquidation_bonus_listed_only_lif_formula():
    """Decision A7: seized x (1 - 1/LIF), LIF = min(1.15, 1/(0.3 LLTV + 0.7)) — DefiLlama's fees/morpho formula."""
    import config
    from fetch.base import FetchOutput
    from fetch.llama import MorphoBlueApi
    a = MorphoBlueApi()
    a.http = _MorphoHttp()
    api = dict(config.PROJECT_BY_NAME["Morpho"]["lending_api"])
    api["liquidations"] = dict(api["liquidations"], days=1)
    out = FetchOutput()
    a._liquidations(api, [1, 8453], "Morpho", pd.Timestamp("2026-10-11"), out)
    v = out.frame()["value"].iloc[0]
    lif = lambda l: min(1.15, 1 / (0.3 * l + 0.7))  # noqa: E731
    want = 0.017513662030281839 * (1 - 1 / lif(0.86)) * 2525.2035 + 1000.0 * (1 - 1 / lif(0.945)) * 1.0
    assert v == pytest.approx(want, rel=1e-9)
    assert "1 with no collateral price left out" in out.log[-1].message


def test_window_vs_rate_adds_the_bonuses_only_on_a_full_window():
    import credibility
    days = pd.date_range("2026-10-04", "2026-10-10")
    long = pd.DataFrame([{"date": d, "project": "Morpho", "metric": "borrow_interest_usd_day_morpho_api",
                          "value": 700_000.0} for d in days] +
                        [{"date": d, "project": "Morpho", "metric": "liquidation_bonus_usd_day_morpho_api",
                          "value": v} for d, v in zip(days, [0.45, 294.3, 2.4, 39_226.8, 397_146.1, 40.7, 0.02])])
    asof = pd.Timestamp("2026-10-11")
    v, _d, how = credibility._window_vs_rate("Morpho", {}, long, asof, rate="borrow_interest_usd_day_morpho_api", days=7,
                                             side="ref", plus_flow="liquidation_bonus_usd_day_morpho_api")
    assert v == pytest.approx(7 * 700_000 + 436_710.77, abs=0.1) and "summed over" in how
    v2, _d, how2 = credibility._window_vs_rate("Morpho", {}, long[long["date"] != days[3]], asof,
                                               rate="borrow_interest_usd_day_morpho_api", days=7, side="ref",
                                               plus_flow="liquidation_bonus_usd_day_morpho_api")
    assert v2 == pytest.approx(7 * 700_000) and "NOT added" in how2


# ----------------------------------------------------------------------------------------------- A3 / A2 on real dumps
def test_aerodrome_absorption_subtracts_gauge_emissions_and_rebase_on_the_real_dump(tmp_path, monkeypatch, dumps_20261009):
    """Decision A3 on the 10-09 dump: gauges + rebase over Q0. Round 3 B4 dates each weekly row by the flip that PAID
    it: 53,622,724.80 to gauges over 13 paid flips (the epoch in progress, read 10-08, pays 10-15 — not yet) +
    6,157,059.65 rebase over 13 flips (12 before re-dating) = 59,779,784.45 (was 53,752,722 + 5,699,610 = 59,452,332)."""
    wb, value = _build(tmp_path, monkeypatch, "Aerodrome")
    assert _cell(wb, value, "A3", "Aerodrome", "Emissions Q0 (tokens)") == pytest.approx(59_779_784.46, abs=1)


def test_chainlink_unlock_is_the_2026_10_09_release(tmp_path, monkeypatch, dumps_20261009):
    """Decision A2's test case: the 27 wallets' 18,750,000 LINK release of 2026-10-09 (read after the dump's 10:41 run;
    appended as the next day's stock under the same wallet set)."""
    from real_dump import load
    last = max((r for r in load("Chainlink")["metrics"] if r["metric"] == "noncirculating_holding_tokens"),
               key=lambda r: r["date"])
    nxt = dict(last, date="2026-10-10", value=last["value"] - 18_750_000)
    wb, value = _build(tmp_path, monkeypatch, "Chainlink", [nxt], asof="2026-10-10")
    for tab in ("A2", "A3"):
        assert _cell(wb, value, tab, "Chainlink", "UNLOCK DILUTION Q0") == pytest.approx(18_750_000)
    assert _cell(wb, value, "A3", "Chainlink", "Net absorption incl. unlocks") < -17_000_000
    assert 0.10 < _cell(wb, value, "A2", "Chainlink", "TOTAL DILUTION") < 0.13


def test_pendle_unlocks_and_a_composition_change_is_never_an_unlock(tmp_path, monkeypatch, dumps_20261009):
    (tmp_path / "p").mkdir()
    (tmp_path / "a").mkdir()
    wb, value = _build(tmp_path / "p", monkeypatch, "Pendle")
    assert _cell(wb, value, "A3", "Pendle", "UNLOCK DILUTION Q0") == pytest.approx(1_675_300.33, abs=0.5)
    wb2, value2 = _build(tmp_path / "a", monkeypatch, "Aerodrome")
    # Aerodrome's set gained the filing wallets on 2026-10-07 (4.9M -> 36.2M): no release, and the % is BLOCKED
    assert _cell(wb2, value2, "A3", "Aerodrome", "UNLOCK DILUTION Q0") == 0
    assert str(_cell(wb2, value2, "A3", "Aerodrome", "Unlock dilution, annualised")).startswith("BLOCKED")


# ----------------------------------------------------------------------------------------------- B Aerodrome buyback
def _fake_base(monkeypatch, receipts, sig_by_block=None, move_blocks=None):
    import check_offline_items as coi
    rc = {int(k): v for k, v in receipts.items()}
    monkeypatch.setattr(coi, "_block_receipts", lambda chain, b: rc.get(b, []))
    monkeypatch.setattr(coi, "_base_move_blocks", lambda token, holder, b0, b1, **k: [
        b for b in (move_blocks or {}).get(holder.lower(), []) if b0 < b <= b1])
    monkeypatch.setattr(coi, "_is_safe", lambda a: False)

    def batch(chain, calls, chunk=200, block="latest"):
        out = []
        for to, data in calls:
            if data.startswith(coi._selx("balanceOf(address)")):
                out.append(hex(1 if sig_by_block else 0))
            else:
                out.append(hex(130786))
        return out
    monkeypatch.setattr(coi, "_batch_eth_calls", batch)
    if sig_by_block:
        monkeypatch.setattr(coi, "_foundation_nft_sig", lambda owners, ids, block: {t: sig_by_block(int(block, 16))
                                                                                    for t in ids})


def _receipts():
    return json.loads((ROOT / "tests" / "fixtures" / "aero_receipts_2026-10-11.json").read_text(encoding="utf-8"))


def test_aerodrome_buyer_purchase_counts_only_for_a_declared_buyer(monkeypatch):
    """The Safe 0x10b42e92… paid 111,247 USDC for 214,509 AERO (block 48,669,228) and forwarded it to the Public Goods
    Fund (48,669,271) — real receipts. Not a Foundation wallet on file: the forward is an inflow, not a purchase. Declared
    as a buyer: its purchase counts and the forward is internal."""
    import check_offline_items as coi
    import config
    pgf, buyer = "0x834c0da026d5f933c2c18fa9f8ba7f1f792fda52", "0x10b42e926c2b8ff0200f35ec466d50576c1efa30"
    _fake_base(monkeypatch, _receipts(), move_blocks={pgf: [48669271], buyer: [48669228, 48669271]})
    fb = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
    assert buyer in fb["buyer_wallets"]                                  # Jake 2026-10-12, decision 1 on e4e50b0
    monkeypatch.setitem(fb, "buyer_wallets", {})
    got = coi.aerodrome_buyback_window(48669200, 48669300, owners=[pgf])
    assert got["error"] is None and got["purchased"] == 0
    assert got["inflows"][buyer] == pytest.approx(214_508.93, abs=0.01)
    monkeypatch.setitem(fb, "buyer_wallets", {buyer: "test: declared buyer"})
    got = coi.aerodrome_buyback_window(48669200, 48669300, owners=[pgf])
    assert got["purchased"] == pytest.approx(214_508.93, abs=0.01) and buyer not in got["inflows"]
    assert got["purchases"][0]["paid_out"] == {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 111_247_000_000}


def test_aerodrome_lock_130786_is_from_balance_not_bought(monkeypatch):
    """The 2026-08-24 lock (real receipt, block 50,403,608): 2,746,705.44 AERO from the PGF's balance, nothing paid out
    in the transaction — lock_from_balance, not bought_same_tx."""
    import check_offline_items as coi
    pgf = "0x834c0da026d5f933c2c18fa9f8ba7f1f792fda52"
    _fake_base(monkeypatch, _receipts(),
               sig_by_block=lambda b: (pgf, 0, 0, 0.0 if b < 50403608 else 2_746_705.443139))
    got = coi.aerodrome_buyback_window(50403600, 50403610, owners=[pgf])
    assert got["error"] is None
    assert got["bought_same_tx"] == 0 and got["lock_from_balance"] == pytest.approx(2_746_705.44, abs=0.01)
    assert got["locks"][0]["nft"] == 130786


def test_aerodrome_agreed_basis_matches_locks_to_purchases_oldest_first():
    """buyback = bought in the lock's tx + balance-funded locks matched to the pool of purchases + compounded."""
    import build_workbook as bw
    import config
    fb = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
    wm = fb["window_metrics"]
    days = pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24"])
    vals = {"bought_same_tx": [0, 0, 0], "lock_from_balance": [0, 0, 2_746_705.44],
            "purchased": [1_000_000, 500_000, 0], "compounded_owner": [10.0, 20.0, 30.0], "rebase_owner": [1, 1, 1]}
    groups = {("Aerodrome", wm[k]): pd.DataFrame({"date": days, "project": "Aerodrome", "metric": wm[k],
                                                  "value": v, "source": "ve_managed:buyback_window", "tier": 2})
              for k, v in vals.items()}
    groups[("Aerodrome", fb["stock_metric"])] = groups[("Aerodrome", wm["purchased"])].assign(metric=fb["stock_metric"])
    bw._foundation_buyback_views(groups)
    s = groups[("Aerodrome", "actual_buyback_tokens")].set_index("date")["value"]
    assert list(s.values) == pytest.approx([10.0, 20.0, 1_500_030.0])    # the lock uses the 1.5M bought, no more


def test_aerodrome_route_names_the_agreed_basis():
    import config
    r = config.buyback_route("Aerodrome")
    assert r["metric"] == "foundation_compounded_owner_tokens" and "bought" in r["reason"]
    bw_ = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]["buyer_wallets"]
    assert list(bw_) == ["0x10b42e926c2b8ff0200f35ec466d50576c1efa30"] and "Jake 2026-10-12" in \
        bw_["0x10b42e926c2b8ff0200f35ec466d50576c1efa30"]["decided"]


def test_logs_are_asked_of_the_token_not_the_wallet(monkeypatch):
    """The lock split read 0 logs over 10 balance moves: a Transfer INTO a wallet is emitted by the token."""
    import check_offline_items as coi
    asked = []
    monkeypatch.setattr(coi, "eth_call", lambda to, sel, block, chain="base": (hex(int(block, 16) // 100), None))
    monkeypatch.setattr(coi, "eth_get_logs", lambda addr, *a, **k: (asked.append(addr) or [], None))
    coi.base_logs_where_balance_moves("0xTOKEN", "0x" + "11" * 20, ["0xt"], 1000, 1100, say=lambda *_: None,
                                      emitter="0xTOKEN")
    assert asked and set(asked) == {"0xTOKEN"}
    src = (ROOT / "fetch" / "validate.py").read_text(encoding="utf-8")
    assert 'emitter=o["token"]' in src


def test_seeded_bisection_finds_a_pass_through_wallet(monkeypatch):
    """A wallet at 0 at both ends (buy, then forward within the hour) reads 'no change' unseeded."""
    import check_offline_items as coi
    # held 1_003_000 .. 1_003_999: 0 at the window's ends, 5 at the seed 1_003_600
    monkeypatch.setattr(coi, "_rpc_retry", lambda chain, m, params: hex(5 if 1_003_000 <= int(params[1], 16) < 1_004_000
                                                                        else 0))
    assert coi._base_move_blocks("0xt", "0x" + "22" * 20, 1_000_000, 1_020_000) == [1_003_000, 1_004_000]
    assert coi._base_move_blocks("0xt", "0x" + "22" * 20, 1_000_000, 1_020_000, seed_span=10**9) == []


# ----------------------------------------------------------------------------------------------- C NEAR lockups
def test_near_circulating_under_the_lockup_rule(monkeypatch):
    """Jake 2026-10-12 (decision 8): everything NOT YET TRANSFERABLE is out — circulating = total − LOCKED from the latest
    weekly read <= 10 days old; without one, NEAR's own figure unchanged and said so. Jake's probe: LOCKED 21,568,722."""
    import build_workbook as bw
    import config
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    assert config.circulating_primary_metric("Near") == "circulating_supply_lockup_rule"
    # fix round 2, R10: (e) reads lockup.near + contributors.near daily (the v3 history, N2); the lockup contracts'
    # LOCKED amount is a declared subset (one weekly reading so far) — config.UNLOCK_RELEASE_SUBSETS
    assert config.unlock_stock_metrics("Near") == ("near_foundation_accounts_tokens",)
    assert config.UNLOCK_RELEASE_SUBSETS["Near"] == {"near_lockup_release_tokens": ("near_lockup_locked_tokens",)}
    d = pd.date_range("2026-10-01", "2026-10-25")
    fp = pd.DataFrame({"date": d, "project": "Near", "metric": "circulating_supply_first_party",
                       "value": 1_250_000_000.0, "source": "near_bigquery:circulating_supply", "tier": 1})
    tot = pd.DataFrame({"date": d, "project": "Near", "metric": "total_supply",
                        "value": 1_306_900_000.0, "source": "coingecko", "tier": 1})
    rd = pd.Timestamp("2026-10-11")
    lk = pd.DataFrame({"date": [rd], "project": "Near", "metric": "near_lockup_locked_tokens", "value": [21_568_722.0],
                       "source": "manual", "tier": 3})
    groups = {("Near", "circulating_supply_first_party"): fp, ("Near", "total_supply"): tot,
              ("Near", "near_lockup_locked_tokens"): lk}
    bw._near_lockup_views(groups)
    s = groups[("Near", "circulating_supply_lockup_rule")].set_index("date")
    # decision 3 on 6f8c86c: no lockup.near / contributors.near read -> never a rule figure missing that leg
    assert s.loc["2026-10-11", "value"] == 1_250_000_000 and "no lockup read" in s.loc["2026-10-11", "source"]
    # lockup.near + contributors.near at the same block (manual_overrides.csv, 2026-10-11: 171,059.131)
    fa = pd.DataFrame({"date": [rd], "project": "Near", "metric": "near_foundation_accounts_tokens",
                       "value": [171_059.131], "source": "manual", "tier": 3})
    groups[("Near", "near_foundation_accounts_tokens")] = fa
    bw._near_lockup_views(groups)
    s = groups[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.loc["2026-10-10", "value"] == 1_250_000_000 and "no lockup read" in s.loc["2026-10-10", "source"]
    assert s.loc["2026-10-11", "value"] == pytest.approx(1_306_900_000 - 21_568_722 - 171_059.131)
    assert "lockups read 2026-10-11" in s.loc["2026-10-11", "source"]
    assert "lockup.near − contributors.near" in s.loc["2026-10-11", "source"]
    assert s.loc["2026-10-22", "value"] == 1_250_000_000                         # 11 days past the read


def test_near_lockups_weekly_never_stores_a_partial_read(monkeypatch, tmp_path, capsys):
    import near_lockups_weekly as nl
    import check_offline_items as coi
    import store as sm
    monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "m.db"))
    sm.Store(str(tmp_path / "m.db")).close()
    monkeypatch.setattr(coi, "near_lockups", lambda: {"height": 1, "accounts": 1960, "read": 1958, "zero": 0,
                                                      "unreadable": 2, "unvested": 1.0, "locked": 2.0})
    assert nl.main(["--force"]) == 1 and "NOTHING STORED" in capsys.readouterr().out
    monkeypatch.setattr(coi, "near_lockups", lambda: {"height": 7, "accounts": 1960, "read": 1960, "zero": 0,
                                                      "unreadable": 0, "unvested": 20.0, "locked": 50.0})
    assert nl.main(["--force"]) == 0
    assert nl.last_read(str(tmp_path / "m.db")) is not None
    assert nl.main([]) == 0 and "nothing read" in capsys.readouterr().out


def test_nearblocks_supply_stats_cross_check():
    import config
    from fetch.base import FetchOutput
    from fetch.nearblocks import NearBlocks
    nb = NearBlocks()
    # NearBlocks v3 (Part E, 2026-10-13): /v3/stats answers {"data": {...}} — the same row v1 wrapped as {"stats": [...]}
    asked = []
    nb._get = lambda url, params, key: asked.append(url) or {"data": {
        "total_supply": "1308704598260070585499781848169781",
        "circulating_supply": "1249836992159308065117756741322730"}}
    nb._key = lambda spec: "k"
    import fetch.scrape as sc
    sc_orig = sc.robots_verdict
    sc.robots_verdict = lambda url: (True, "ok")
    try:
        out = FetchOutput()
        spec = config.PROJECT_BY_NAME["Near"]["nearblocks"]
        nb._supply_stats("Near", spec, spec["supply_stats_retired"], out)
    finally:
        sc.robots_verdict = sc_orig
    assert out.frame()["value"].iloc[0] == pytest.approx(58_867_606.1, abs=1)
    assert asked == ["https://api.nearblocks.io/v3/stats"]
    # a v1-shaped answer stores nothing (the envelope is checked, never guessed)
    nb._get = lambda url, params, key: {"stats": [{"total_supply": "1", "circulating_supply": "1"}]}
    out2 = FetchOutput()
    sc.robots_verdict = lambda url: (True, "ok")
    try:
        nb._supply_stats("Near", spec, spec["supply_stats_retired"], out2)
    finally:
        sc.robots_verdict = sc_orig
    assert out2.frame().empty and any("NOTHING STORED" in e.message for e in out2.log)


# ----------------------------------------------------------------------------------------------- D outputs
def test_snapshot_on_the_real_pendle_dump(tmp_path, monkeypatch):
    import build_workbook as bw
    import export_snapshot as es
    from credibility_report import read_tab
    wb, value = _build(tmp_path, monkeypatch, "Pendle")
    _s, tab = read_tab(tmp_path / "wb.xlsx", evaluate=True)
    rows = es.snapshot_rows(wb, value, list(bw.CREDIBILITY_ROWS), tab, list(bw._HEADLINE_CELLS), "2026-10-09")
    r = rows[0]
    assert r["project"] == "Pendle" and r["Master | FDV ($)"] > 0
    assert r["A3 | " + bw.UNLOCK_HEAD] == pytest.approx(1_675_300.33, abs=0.5)
    assert any(k.endswith("| verdict") and v for k, v in r.items())
    assert {k.rsplit(" | ", 1)[1] for k in r if k.count(" | ") >= 2 and not k.startswith("traj | ")} == \
        {"verdict", "near_limit", "coverage_days"}
    assert any(k.startswith("traj | A3 | ") for k in r)          # the trajectory columns (Phase 1, 2026-10-12)
    p = es.write_csv(rows, tmp_path / "snapshot_2026-10-09.csv")
    assert p.read_text(encoding="utf-8").startswith("project,asof,")


def test_metrics_doc_covers_every_headline_and_is_current():
    import generate_metrics_doc as g
    assert g.main(["--check"]) == 0


def test_daily_run_ends_with_the_snapshot_and_runs_the_weekly_lockups():
    ps = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    steps = [ln for ln in ps.splitlines() if ln.startswith("[void](Invoke-Step")]
    assert steps[-1] == '[void](Invoke-Step "export snapshot" @("export_snapshot.py"))'
    # Jake's Phase 1 review 2026-10-12, item 9: the script closes with ONE console line (ran / exit code / log path)
    # Round G 1.8: the body sits in try/finally — the closing line, then "daily run FINAL: complete", then the exit;
    # the finally block writes "daily run FINAL: stopped early" when the body ended any other way
    lines = [ln.strip() for ln in ps.splitlines()]
    i = lines.index("Write-Host $summary")
    assert lines[i + 1].startswith('Write-Log "daily run FINAL: complete') and lines[i + 3] == "exit $run[0]"
    assert ps.rstrip().endswith("}") and "} finally {" in ps and "daily run FINAL: stopped early" in ps
    assert "token_metrics exit $($run[0])" in ps and "log $Log" in ps and "$script:Steps += ,@($label, $code)" in ps
    assert '"near_lockups_weekly.py"' in ps


# ----------------------------------------------------------------------------------------------- Jake on e4e50b0
def test_aerodrome_implied_buyback_is_zero_in_every_cell_on_the_real_dump(tmp_path, monkeypatch, dumps_20261009):
    """Decision 2: no protocol share of fees goes to buybacks — implied net absorption = -emissions (-59,452,332), and no
    cell applies the 100% VOTER share as a buyback split."""
    wb, value = _build(tmp_path, monkeypatch, "Aerodrome")
    assert _cell(wb, value, "A3", "Aerodrome", "Net absorption, implied basis") == pytest.approx(-59_779_784.46, abs=1)
    for h in ("Implied buyback Q0 ($)", "Implied buyback Q0 (tokens)", "BUYBACK AS % OF SUPPLY", "Buyback % supply at Q1",
              "Buyback % supply at Q2", "Buyback % supply at Q3", "Share applied to this window",
              "Current documented share"):
        assert _cell(wb, value, "A3", "Aerodrome", h) == 0, h
    for h in ("Implied buyback Q0 ($)", "Buyback % of supply"):
        assert _cell(wb, value, "Master", "Aerodrome", h) == 0, h


def test_hyperliquid_team_wallet_is_an_unlock_stock_not_subtracted_again():
    """Decision 3: the team wallet (tokenDetails.nonCirculatingUserBalances, 237,915,860.47 on 2026-10-11) is stored as
    team_wallet_tokens and is an unlock leg; circulatingSupply already excludes it."""
    import config
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo
    api, read = _hype_read()
    a = HyperCoreInfo()
    a.http = _HypeHttp()
    payload = {"totalSupply": "998868230.0", "circulatingSupply": "302167698.0", "maxSupply": "1000000000.0",
               "nonCirculatingUserBalances": [["0x43e9abea1910387c4292bca4b94de81462f8a251", "237915860.4669794142"],
                                              ["0xfefefefefefefefefefefefefefefefefefefefe", "47955056.0"]]}
    out = FetchOutput()
    a._token_details(config.PROJECT_BY_NAME["Hyperliquid"], api, read, payload, out, pd.Timestamp("2026-10-11"))
    f = out.frame().set_index("metric")["value"]
    assert f["team_wallet_tokens"] == pytest.approx(237_915_860.47, abs=0.01)
    assert f["circulating_supply_first_party"] == pytest.approx(302_167_698.0 - 63_525_720.63, abs=0.01)
    assert config.unlock_stock_metrics("Hyperliquid") == ("excluded_wallets_tokens", "team_wallet_tokens")


def test_chainlink_unlock_row_is_judged_against_the_scan_on_the_real_dump(tmp_path, monkeypatch):
    """Decision 6: Chainlink's unlock row is judged (the reconciled outflow scan); no other project has one."""
    import config
    from real_dump import evaluate_real
    o = evaluate_real(tmp_path, monkeypatch, "Chainlink")
    assert str(o["in_unlocks"]["verdict"]).startswith("PASS")
    assert [n for n, rows in config.CREDIBILITY.items() if "in_unlocks" in rows] == ["Chainlink"]


def test_near_first_weekly_read_is_jakes_probe_and_counts_as_the_last_read():
    """Decision 8: Jake's 2026-10-11 15:50 totals (block 219,544,718) are the first read — manual_overrides.csv — so the
    weekly step does not re-read for a week; a lockup whose own getter fails is not a read failure."""
    import near_lockups_weekly as nl
    df = pd.read_csv(ROOT / "manual_overrides.csv", dtype=str, comment="#").fillna("")
    near = df[(df.project == "Near") & df.metric.isin(nl.METRICS)].set_index("metric")
    assert near.loc["near_lockup_unvested_tokens", "value"] == "1999721"
    # LOCKED with the buggy-code lockups recomputed: 21,568,722 − their RPC 19,742,787 + recomputed 232,587
    assert near.loc["near_lockup_locked_tokens", "value"] == str(21_568_722 - 19_742_787 + 232_587) == "2058522"
    assert "RECOMPUTED" in near.loc["near_lockup_locked_tokens", "source_note"]
    assert near["source_note"].str.contains("pending Jake's review").all()
    assert (near["date"] == "2026-10-11").all() and near["source_note"].str.contains("219,544,718").all()
    assert nl.last_read("/nonexistent.db", str(ROOT / "manual_overrides.csv")) == pd.Timestamp("2026-10-11")
    got = {"unreadable": 1, "errors": {"rpc.mainnet.fastnear.com: wasm execution failed with error: ": 1}}
    assert nl.read_failures(got) == 0
    assert nl.read_failures({"unreadable": 2, "errors": {"HTTP 429": 2}}) == 2


def test_etherfi_like_for_like_circulating_is_on_coingeckos_own_total():
    """Jake's run on e4e50b0 (2026-10-12 item 2): in_circ CHECK at 3.44% — exactly the chain total 998,535,999 against
    CoinGecko's 965,350,000, since Ether.fi's total is on-chain first. The like-for-like row now takes ours − our total
    + CoinGecko's own total (kept as total_supply_coingecko), so it compares like-for-like again."""
    import config
    import credibility as cred
    assert "total_supply_coingecko" in config.metrics_for_project(config.PROJECT_BY_NAME["Ether.fi"])
    day = "2026-10-12"
    # the figures of Jake's run: CoinGecko counts the filing wallets, so ours + those wallets = our chain total
    rows = {"Ether.fi|circulating_supply_onchain": {"now": 830_395_999.0, "latest_date": day},
            "Ether.fi|noncirculating_holding_tokens": {"now": 168_140_000.0, "latest_date": day},
            "Ether.fi|total_supply": {"now": 998_535_999.0, "latest_date": day},
            "Ether.fi|circulating_supply": {"now": 965_350_000.0, "latest_date": day}}
    long = pd.DataFrame({"date": pd.to_datetime([day] * 2), "project": "Ether.fi",
                         "metric": ["total_supply_coingecko", "circulating_supply"],
                         "value": [965_350_000.0, 965_350_000.0]})
    built = cred.build_rows([], rows, long, pd.Timestamp(day), projects=["Ether.fi"])
    row = next(r for r in built if r["id"] == "in_circ")
    assert row["ours"]["value"] == pytest.approx(965_350_000.0)
    assert row["value"] == pytest.approx(965_350_000.0) and row["mode"] == "independent"
    assert abs(row["ours"]["value"] / row["value"] - 1) * 100 <= row["tol"] == 2.0     # judged in the sheet: PASS
    # before the fix: our chain total against CoinGecko's circulating — the 3.44% of Jake's run
    assert (830_395_999.0 + 168_140_000.0) / 965_350_000.0 - 1 == pytest.approx(0.0344, abs=5e-5)


def test_pendle_buyback_is_judged_over_whole_epochs_so_the_window_edge_does_not_move_it(tmp_path, monkeypatch):
    """Jake's run on e4e50b0 (2026-10-12 item 3): in_buyback +14.8% on 10-11, +46.70% on 10-12. Nothing was re-read or
    re-dated: the trailing 90 days lost the epoch of 07-14 (144,388 PENDLE) whole on 10-12 while the daily side lost one
    day (the real dump reproduces it: +13.9% -> +44.9%). Both sides now cover the whole published epochs inside Q0, so
    the same data reads the same gap on each day; the 25% tolerance is unchanged."""
    import config
    from real_dump import evaluate_real
    spec = config.CREDIBILITY["Pendle"]["in_buyback"]
    assert spec["ref"]["tol"] == 25.0 and spec["ref"]["formula"] == "epoch_span_sum"
    got = {}
    for day in ("2026-10-09", "2026-10-11", "2026-10-12"):
        (tmp_path / day).mkdir()
        o = evaluate_real(tmp_path / day, monkeypatch, "Pendle", asof=day)["in_buyback"]
        got[day] = o
        assert str(o["verdict"]).startswith("PASS"), (day, o)
    # 10-11: the epochs 07-14..09-08 (09-22 still read 0 in the dump); 10-12: 07-28..09-08 — each gap a few %
    assert got["2026-10-11"]["ref"] == pytest.approx(659_876, abs=1)
    assert got["2026-10-12"]["ref"] == pytest.approx(515_488, abs=1)
    for o in got.values():
        assert abs(o["ours"] / o["ref"] - 1) < 0.06, o


def test_plume_circulating_live_and_cached_reads_are_one_measuring_point(tmp_path, monkeypatch):
    """Jake's run on e4e50b0 (2026-10-12 item 4): Plume in_circ "CHECK (no figure)" although the last good figure was a
    day old. The day's first run reads supply.plume.org LIVE ('scrape:supply.plume.org'); same-day re-runs re-serve the
    cache and stored 'scrape:cache'. Two measuring points, no handover -> the series was withheld
    (measuring_point_changed), and the stale-now fallback never stands in for a withheld figure. The real Plume dump
    (every day cached) plus Jake's store shape — 10-10/10-11 cached, 10-12 live, the value supply.plume.org returns —
    reproduces it; orphan_cleanup.sql CI relabels the cached rows, and a cached read is now stored with the page's host
    plus the 'cache' marker, so it is the live read's measuring point."""
    import builtins
    import sqlite3
    import config
    import run_sql
    from fetch.base import _measuring_point
    from real_dump import evaluate_real, load
    assert _measuring_point(config.mark_source("scrape:supply.plume.org", "cache")) == \
        _measuring_point("scrape:supply.plume.org")
    payload = load("Plume")
    v = 6_606_142_167.0
    payload["metrics"] = list(payload["metrics"]) + [
        {"date": d, "metric": "circulating_supply_first_party", "value": v, "source": src, "tier": 3,
         "fetched_at": f"{d}T{t}Z"}
        for d, src, t in (("2026-10-10", "scrape:cache", "18:00:00"), ("2026-10-11", "scrape:cache", "18:00:00"),
                          ("2026-10-12", "scrape:supply.plume.org", "07:00:00"))]
    (tmp_path / "before").mkdir()
    before = evaluate_real(tmp_path / "before", monkeypatch, "Plume", payload=payload, asof="2026-10-12")["in_circ"]
    # Jake's run reproduced CHECK (no figure) here until fix round 2: config.SAME_BASIS now reads a bare 'scrape:cache'
    # row as the page's own measuring point, so the figure stands even before CI relabels the store
    assert before["verdict"].startswith("PASS"), before
    # orphan_cleanup.sql CI, run as Jake runs it (python run_sql.py --delete CI)
    (tmp_path / "after").mkdir()
    db = tmp_path / "after" / "metrics.db"
    from real_dump import to_store
    to_store(payload, db)
    sec = run_sql.parse_sections(run_sql.SQL_FILE.read_text(encoding="utf-8"))["CI"]
    monkeypatch.setattr(builtins, "input", lambda *_: "DELETE CI")
    con = sqlite3.connect(db)
    try:
        assert run_sql.run_delete(con, sec["text"], "CI", db) == 0
        left = con.execute("SELECT COUNT(*) FROM metrics WHERE project = 'Plume' AND source LIKE 'scrape:cache%'").fetchone()
        assert left == (0,)
        stored = [r[0] for r in con.execute("SELECT DISTINCT source FROM metrics WHERE project = 'Plume' AND "
                                            "metric = 'circulating_supply_first_party' ORDER BY source")]
    finally:
        con.close()
    assert stored == ["scrape:supply.plume.org", "scrape:supply.plume.org:cache"]
    payload["metrics"] = [{**r, "source": ("scrape:supply.plume.org:cache" if r["source"] == "scrape:cache" else
                                           r["source"])} for r in payload["metrics"]]
    (tmp_path / "relabelled").mkdir()
    after = evaluate_real(tmp_path / "relabelled", monkeypatch, "Plume", payload=payload, asof="2026-10-12")["in_circ"]
    assert str(after["verdict"]).startswith("PASS") and after["ours"] == v, after


def test_metrics_doc_build_closes_every_connection_before_its_temp_dir_goes(monkeypatch):
    """Jake's run on e4e50b0 (2026-10-12 item 1): `generate_metrics_doc.py --check` died on Windows — PermissionError
    [WinError 32] deleting %TEMP%\\tmp...\\metrics.db in TemporaryDirectory cleanup, the Store handed to build_workbook
    still open. Windows' rule, imitated: the temporary directory refuses to go while any sqlite connection to a file
    inside it is open."""
    import sqlite3
    import tempfile
    import generate_metrics_doc as g
    opened = []
    real_connect = sqlite3.connect

    def tracking(*a, **k):
        c = real_connect(*a, **k)
        opened.append((c, str(a[0] if a else k.get("database", ""))))
        return c

    class WindowsTemporaryDirectory(tempfile.TemporaryDirectory):
        def __exit__(self, *exc):
            for c, path in opened:
                try:
                    c.total_changes                              # raises once the connection is closed
                except sqlite3.ProgrammingError:
                    continue
                if path.startswith(self.name):
                    raise PermissionError(32, "The process cannot access the file because it is being used by "
                                              "another process", path)
            return super().__exit__(*exc)

    monkeypatch.setattr(sqlite3, "connect", tracking)
    monkeypatch.setattr(g.tempfile, "TemporaryDirectory", WindowsTemporaryDirectory)
    cap = g.captured_columns()
    assert cap and opened


def test_near_buggy_recompute_counts_jakes_set_and_never_takes_an_error_for_a_lockup(monkeypatch, capsys):
    """Decision 8: the buggy-code lockups' locked is recomputed from their own state. 2026-10-12: the public node had
    garbage-collected Jake's block and every error reply read as a NON-buggy lockup (0 of 1,960). An unread code hash
    is an unread lockup (retried once), a past block is read from the archival nodes first, and a zero-balance lockup
    is skipped as near_lockups skips it (Jake's 1,685). Aggregates only."""
    import check_offline_items as coi
    from fetch.archive import near_archival_endpoints
    y = 10 ** 24
    monkeypatch.setattr(coi, "near_lockup_accounts", lambda: (["a", "b", "c", "d", "e"], "list"))
    monkeypatch.setattr(coi, "rpc", lambda url, m, p=None: {"result": {"header": {"height": 9, "timestamp": 5}}})
    tries = {}
    urls_seen = []

    def code(acc, h, urls=None):
        urls_seen.append(urls)
        tries[acc] = tries.get(acc, 0) + 1
        if acc == "e" and tries[acc] == 1:
            return None, "x: view_account GARBAGE_COLLECTED_BLOCK"      # read on the retry
        if acc == "d":
            return None, "x: view_account GARBAGE_COLLECTED_BLOCK"
        return ("3kVY9qcVRoW3B5498SMX6R3rtSLiCdmBzKs7zcnzDJ7Q" if acc != "a" else "other"), ""
    monkeypatch.setattr(coi, "_near_code_hash", code)
    bal = {"b": 10 * y, "c": 0, "e": 4 * y}
    monkeypatch.setattr(coi, "_near_view", lambda acc, m, args=None, block_id=None, pace=0.12, urls=None:
                        ((str(bal[acc]) if m == "get_balance" else str(3 * y)), ""))
    monkeypatch.setattr(coi, "_near_view_state", lambda acc, h, urls=None: (b"s", ""))
    monkeypatch.setattr(coi, "near_lockup_state", lambda raw: {})
    monkeypatch.setattr(coi, "near_locked_recomputed", lambda st, ts, has_bug: 1 * y)
    monkeypatch.setattr(coi, "near_unvested", lambda st, ts: 0)
    out = coi.near_lockups_buggy_recompute(block_id=9, workers=1)
    assert out["buggy"] == 2 and out["zero"] == 1 and out["unread"] == 1 and out["retried"] == 2
    assert out["rpc"] == 6 * y and out["recomputed"] == 2 * y
    assert urls_seen[0][:len(near_archival_endpoints())] == tuple(near_archival_endpoints())
    printed = capsys.readouterr().out
    assert "unreadable 1 after one retry of 2" in printed and " b " not in printed


# ----------------------------------------------------------------------------------------------- Aerodrome round
def _aero_rows(day, values: dict, source="ve_managed:window"):
    return [{"date": day, "metric": m, "value": v, "source": source, "tier": 2, "fetched_at": f"{day}T12:00:00Z"}
            for m, v in values.items()]


def test_aerodrome_traced_purchases_fill_q0_on_the_real_dump(tmp_path, monkeypatch, dumps_20261009):
    """Jake 2026-10-12 (Aerodrome round, item 1): `--seed aero_buyback` stored 0 rows (no keyed BASE_RPC_URL), so the
    Q0 buyback, retirement and net absorption were empty. The six traced Safe purchases (config foundation_buyback.
    trace) are tier-3 rows on their purchase days, tx hashes in the source: 1,906,696 AERO retired (held, locked veNFT)
    for 829,828 USDC; net absorption against the 59.45M of emissions ≈ −57.5M."""
    import config
    tr = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]["trace"]
    assert sum(r["aero"] for r in tr["rows"]) == pytest.approx(1_906_696.26, abs=0.01)
    assert sum(r["usdc"] for r in tr["rows"]) == 829_828
    assert all(len(r["buy_tx"]) == 66 and len(r["forward_tx"]) == 66 for r in tr["rows"])
    wb, value = _build(tmp_path, monkeypatch, "Aerodrome", asof="2026-10-12")
    assert _cell(wb, value, "A3", "Aerodrome", "Actual buyback Q0 ($)") == pytest.approx(829_828)
    assert _cell(wb, value, "A3", "Aerodrome", "Net absorption Q0 (tokens)") == pytest.approx(-57_873_088.20, abs=1)
    assert _cell(wb, value, "A3", "Aerodrome", "CIRCULATING RETIREMENT RATE") == pytest.approx(0.005155, abs=1e-6)
    ws = wb["Data"]
    import build_workbook as bw
    row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "Aerodrome|retired_buyback_tokens")
    assert ws.cell(row, bw.DATA_COLS.index("q0") + 1).value == pytest.approx(1_906_696.26, abs=0.01)
    src = next(ws.cell(r, bw.DATA_COLS.index("source") + 1).value for r in range(2, ws.max_row + 1)
               if ws.cell(r, 1).value == "Aerodrome|actual_buyback_tokens")
    assert "trace — matched to lock #130786" in src and "0x" in src


def test_aerodrome_chain_scan_replaces_the_trace_and_a_gap_is_flagged(tmp_path, monkeypatch):
    """Item 2: once the chain scan holds the lock day (2026-08-24) it replaces the trace — the agreed basis counts the
    matched AERO on that day, so the two are never added — and credibility in_buyback_trace judges them over the
    trace span at 2%. The scan rows below are the trace's own figures in the agreed basis's shape (purchases on their
    days, the lock from balance on 08-24): equal -> PASS; 3% short -> CHECK."""
    import config
    from real_dump import evaluate_real, load
    tr = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]["trace"]
    wm = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]["window_metrics"]

    def scan(scale):
        rows = []
        for d in pd.date_range("2026-07-14", "2026-10-08").strftime("%Y-%m-%d"):
            bought = sum(r["aero"] for r in tr["rows"] if r["date"] == d) * scale
            lock = tr["lock"]["aero"] if d == tr["lock"]["date"] else 0.0
            rows += _aero_rows(d, {wm["bought_same_tx"]: 0.0, wm["lock_from_balance"]: lock, wm["purchased"]: bought,
                                   wm["compounded_owner"]: 0.0, wm["rebase_owner"]: 0.0})
        return rows
    for scale, verdict in ((1.0, "PASS"), (0.97, "CHECK")):
        payload = load("Aerodrome")
        payload["metrics"] = list(payload["metrics"]) + scan(scale)
        d = tmp_path / f"s{scale}"
        d.mkdir()
        o = evaluate_real(d, monkeypatch, "Aerodrome", payload=payload, asof="2026-10-12")
        assert str(o["in_buyback_trace"]["verdict"]).startswith(verdict), (scale, o["in_buyback_trace"])
        assert o["in_buyback_trace"]["ours"] == pytest.approx(1_906_696.26 * scale, rel=1e-6)
        assert o["in_buyback"]["ours"] == pytest.approx(1_906_696.26 * scale, rel=1e-6), "replaced, never added"


def test_aerodrome_seed_says_why_it_stored_nothing(monkeypatch):
    """Item 3: `--seed aero_buyback` prints WHY 0 rows were stored — no key, no events in range, already stored."""
    from fetch import ve_managed as vm
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    import check_offline_items as coi
    monkeypatch.setattr(coi, "rpc_block_at", lambda *a, **k: 123)
    frames = vm.seed_foundation([config_aero()], days=3, stored=lambda *a: False,
                                reader=lambda owners, block: {"error": "HTTP 403 historical state not available"},
                                say=lambda m: None)
    assert frames == []
    why = vm.why_nothing_stored(vm.LAST_SEED, 0)
    assert "3 lock-stock read(s) failed (first: HTTP 403" in why and "NO KEYED BASE_RPC_URL" in why
    vm.seed_foundation([config_aero()], days=2, stored=lambda *a: True, say=lambda m: None)
    assert vm.why_nothing_stored(vm.LAST_SEED, 0).startswith("all 2 days already stored")


def config_aero():
    import config
    return config.PROJECT_BY_NAME["Aerodrome"]


def test_issuer_audit_labels_each_wallet_from_its_own_label():
    """Issuer-wallet audit (Jake 2026-10-12): issuer-side vs team/investor unvested vs burned vs legacy backing vs
    'other' (Jake's call) — from each wallet's config label. A treasury vesting contract or an ecosystem wallet is
    issuer-side; only a label naming team / investor AND vesting / unlocking is unvested."""
    import config
    import check_offline_items as coi
    cat = {(w["key"]): w["category"] for n in ("GEODNET", "Uniswap", "Sky", "Ether.fi", "Morpho")
           for w in coi._issuer_wallets(config.PROJECT_BY_NAME[n])}
    assert cat["noncirc_candidate_1"] == "team/investor unvested (convention)"         # GEODNET Team (unlocking)
    assert cat["ecosystem_polygon"] == "issuer-side"
    assert cat["noncirc_candidate_4"].startswith("other")                               # GEODNET public sale
    assert cat["noncirc_treasury_vesting_1"] == "issuer-side"                           # Uniswap treasury vesting
    assert cat["burn_dead"] == "burned"
    assert cat["noncirc_mkr_sky_converter"] == "pre-minted for unconverted legacy"
    assert cat["noncirc_custodian_origin_1"] == "team/investor unvested (convention)"
    assert cat["noncirc_association_master_safe"] == "issuer-side"
    assert coi.issuer_wallet_audit in coi.CHECKS
