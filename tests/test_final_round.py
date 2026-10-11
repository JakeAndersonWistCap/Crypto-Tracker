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
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    f = bw._net_annual_pct(R, 5, p, "CIRC")
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
def test_total_supply_on_chain_first_for_the_seven_and_not_aethir():
    import config
    assert set(config.TOTAL_SUPPLY_ONCHAIN_FIRST) == {"Ether.fi", "Aerodrome", "Chainlink", "Fluid", "Maple", "Morpho",
                                                      "Pendle", "Sky"}
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


def test_morpho_dao_legacy_and_base_wired_ratios_stay_coingecko():
    import config
    c = config.PROJECT_BY_NAME["Morpho"]["contracts"]
    leg, base = c["noncirc_dao_treasury_legacy"], c["noncirc_dao_treasury_base"]
    assert (leg["chain"], c[leg["underlying"]]["chain"]) == ("ethereum", "ethereum")
    assert (base["chain"], c[base["underlying"]]["chain"]) == ("base", "base")
    assert c["token_legacy"]["kind"] == c["token_base"]["kind"] == "bridged_representation"
    assert config._MORPHO_ORG.endswith("/learn/governance/organization/")
    assert config.circulating_primary_metric("Morpho") == "circulating_supply"      # 570.66M vs 700.45M: no switch


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
def test_aerodrome_absorption_subtracts_gauge_emissions_and_rebase_on_the_real_dump(tmp_path, monkeypatch):
    """Decision A3: 53,752,722 (Minter, to gauges) + 5,699,610 (rebase) = 59,452,332 over Q0 on the 10-09 dump."""
    wb, value = _build(tmp_path, monkeypatch, "Aerodrome")
    assert _cell(wb, value, "A3", "Aerodrome", "Emissions Q0 (tokens)") == pytest.approx(59_452_331.99, abs=1)


def test_chainlink_unlock_is_the_2026_10_09_release(tmp_path, monkeypatch):
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


def test_pendle_unlocks_and_a_composition_change_is_never_an_unlock(tmp_path, monkeypatch):
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
    got = coi.aerodrome_buyback_window(48669200, 48669300, owners=[pgf])
    assert got["error"] is None and got["purchased"] == 0
    assert got["inflows"][buyer] == pytest.approx(214_508.93, abs=0.01)
    fb = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
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
    assert config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]["buyer_wallets"] == {}


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
    """Only still-unvested leaves circulating: NEAR's figure + (locked - unvested) from the latest weekly read <= 10 days
    old; without one, NEAR's figure unchanged and said so."""
    import build_workbook as bw
    import config
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    assert config.circulating_primary_metric("Near") == "circulating_supply_lockup_rule"
    d = pd.date_range("2026-10-01", "2026-10-25")
    fp = pd.DataFrame({"date": d, "project": "Near", "metric": "circulating_supply_first_party",
                       "value": 1_250_000_000.0, "source": "near_bigquery:circulating_supply", "tier": 1})
    rd = pd.Timestamp("2026-10-12")
    mk = lambda m, v: pd.DataFrame({"date": [rd], "project": "Near", "metric": m, "value": [v],  # noqa: E731
                                    "source": "near_rpc:lockups@1", "tier": 2})
    groups = {("Near", "circulating_supply_first_party"): fp,
              ("Near", "near_lockup_unvested_tokens"): mk("near_lockup_unvested_tokens", 20_000_000.0),
              ("Near", "near_lockup_locked_tokens"): mk("near_lockup_locked_tokens", 50_000_000.0)}
    bw._near_lockup_views(groups)
    s = groups[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.loc["2026-10-11", "value"] == 1_250_000_000 and "no lockup read" in s.loc["2026-10-11", "source"]
    assert s.loc["2026-10-12", "value"] == 1_280_000_000 and "lockups read 2026-10-12" in s.loc["2026-10-12", "source"]
    assert s.loc["2026-10-23", "value"] == 1_250_000_000                         # 11 days past the read


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
    nb._get = lambda url, params, key: {"stats": [{"total_supply": "1308704598260070585499781848169781",
                                                   "circulating_supply": "1249836992159308065117756741322730"}]}
    nb._key = lambda spec: "k"
    import fetch.scrape as sc
    sc_orig = sc.robots_verdict
    sc.robots_verdict = lambda url: (True, "ok")
    try:
        out = FetchOutput()
        spec = config.PROJECT_BY_NAME["Near"]["nearblocks"]
        nb._supply_stats("Near", spec, spec["supply_stats"], out)
    finally:
        sc.robots_verdict = sc_orig
    assert out.frame()["value"].iloc[0] == pytest.approx(58_867_606.1, abs=1)


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
    assert {k.rsplit(" | ", 1)[1] for k in r if k.count(" | ") >= 2} == {"verdict", "near_limit", "coverage_days"}
    p = es.write_csv(rows, tmp_path / "snapshot_2026-10-09.csv")
    assert p.read_text(encoding="utf-8").startswith("project,asof,")


def test_metrics_doc_covers_every_headline_and_is_current():
    import generate_metrics_doc as g
    assert g.main(["--check"]) == 0


def test_daily_run_ends_with_the_snapshot_and_runs_the_weekly_lockups():
    ps = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    assert ps.rstrip().endswith('[void](Invoke-Step "export snapshot" @("export_snapshot.py"))')
    assert '"near_lockups_weekly.py"' in ps
