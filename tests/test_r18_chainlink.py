"""Round 18 — Chainlink (Jake 2026-11-24): decisions 1-8 of docs/r15_chainlink_pack.md wired as recommended.

No network. Figures are the reads of 2026-11-23 (Ethereum block 26,477,750; Blockscout; supply.chain.link/supply;
metrics.chain.link/api/reserve/latest) and the rows of the fixture dump (fixtures/real_chainlink.json, 2026-10-13).
  1-3  circulating = total supply; the 33 wallets and the Reserve in circulating, off the float; the RewardVault and
       the three INFERRED path wallets off the float from their first stored row; the references' like-for-like figure
  4, 7 the three revenue lines: fees = customer revenue, revenue = holder revenue = the held Reserve inflow
  5    the insider share of stake: a measured 0 on the 42 issuer-side wallets, all or no row
  6    node-operator payments (63 recipients, INFERRED) are outflow (b), out of (e) FIFO per lump
  8    the exchange leg is a named float outflow; the financial-theatre flag and what it reads"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import credibility  # noqa: E402

CL = "Chainlink"
P = config.PROJECT_BY_NAME[CL]
NC33, RESERVE, VAULT = 233_150_029.58, 6_804_760.10, 3_093_999.14
PATH = 9_822_500.00 + 900_576.13 + 31_465.48
PRINCIPAL = 40_875_000.00 + 1_694_261.49


def _frame(metric, rows, source="chain:ethereum:x"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _ in rows], "project": CL, "metric": metric,
                         "value": [float(v) for _, v in rows], "tier": 2, "source": source})


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [P])
    return P


def _dump():
    return json.loads((ROOT / "fixtures" / "real_chainlink.json").read_text(encoding="utf-8"))


def _dump_series(metric):
    rows = [(r["date"][:10], r["value"]) for r in _dump()["metrics"] if r["metric"] == metric]
    return pd.Series({pd.Timestamp(d): float(v) for d, v in rows}).sort_index()


# ===================================================================== decisions 1-3: circulating under the rule
def test_1_circulating_is_the_total_supply():
    from fetch import circulating as circ
    assert config.rule_in_legs(CL) == ("buyback_fund_balance", "noncirculating_holding_tokens")
    days = ["2026-11-22", "2026-11-23"]
    h = pd.concat([_frame("total_supply", [(d, 1e9) for d in days]),
                   _frame("buyback_fund_balance", [(d, RESERVE) for d in days]),
                   _frame("noncirculating_holding_tokens", [(d, NC33) for d in days])])
    assert list(circ.series(h, CL).values) == [1e9, 1e9]                     # nothing subtracted
    # the figure before the rule (what the build showed): total − Reserve − the 33
    assert circ.series(h, CL, rule=False).iloc[-1] == pytest.approx(1e9 - RESERVE - NC33)
    spec = config.RULE_IN[CL]
    assert spec["circulating"] == (760_645_318, 1_000_000_000)
    for who in ("chainlink_api", "coingecko", "coinmarketcap"):
        ref, legs = spec["references"][who]
        assert ref + sum(legs.values()) == pytest.approx(1e9, abs=0.01), who  # each gap named to the token
    assert config.CIRCULATING_ONCHAIN[CL]["handover"]["on"] == "2026-11-24"


def test_1_on_the_dump_circulating_goes_from_760_6m_to_the_total():
    from fetch import circulating as circ
    m = _dump()["metrics"]
    h = pd.DataFrame([{"date": pd.Timestamp(r["date"][:10]), "project": CL, "metric": r["metric"], "value": r["value"]}
                      for r in m if r["metric"] in ("total_supply", "buyback_fund_balance",
                                                    "noncirculating_holding_tokens")])
    day = pd.Timestamp("2026-10-13")
    assert circ.series(h, CL).loc[day] == 1_000_000_000.0
    assert circ.series(h, CL, rule=False).loc[day] == pytest.approx(760_645_318, abs=1)   # the figure it replaces


def test_2_3_the_float_loses_the_issuer_stock_and_the_new_legs_join_at_their_first_row(scoped):
    spec = config.RULE_IN[CL]
    assert spec["off_float"] == ("buyback_fund_balance", "noncirculating_holding_tokens",
                                 "reward_vault_balance_tokens", "issuer_path_holding_tokens")
    assert set(spec["off_float_from"]) == {"reward_vault_balance_tokens", "issuer_path_holding_tokens"}
    days = pd.date_range("2026-11-20", "2026-11-23")
    g = {(CL, "buyback_fund_balance"): _frame("buyback_fund_balance", [(d, RESERVE) for d in days]),
         (CL, "noncirculating_holding_tokens"): _frame("noncirculating_holding_tokens", [(d, NC33) for d in days])}
    bw._rule_in_views(g)
    # the store before the first run with the new reads (the fixture's case): the float is what it was
    assert list(g[(CL, "rule_in_off_float_tokens")]["value"]) == pytest.approx([RESERVE + NC33] * 4)
    late = days[2:]
    g[(CL, "reward_vault_balance_tokens")] = _frame("reward_vault_balance_tokens", [(d, VAULT) for d in late])
    g[(CL, "issuer_path_holding_tokens")] = _frame("issuer_path_holding_tokens", [(d, PATH) for d in late])
    g.pop((CL, "rule_in_off_float_tokens"))
    bw._rule_in_views(g)
    off = g[(CL, "rule_in_off_float_tokens")].set_index("date")["value"]
    assert off.loc[days[0]] == pytest.approx(RESERVE + NC33)                 # before the seam: the two legs
    assert off.loc[days[-1]] == pytest.approx(253_803_330.43, abs=0.01)      # from it: + the vault + the path wallets
    assert off.loc[days[-1]] == pytest.approx(config.CHAINLINK_RULE_READ["off_float"], abs=0.01)
    # free float = total − staked principal − the off-float stock: 703.6M, 70.4% of supply (read 2026-11-23)
    flt = 1e9 - PRINCIPAL - off.loc[days[-1]]
    assert flt == pytest.approx(703_627_408.08, abs=0.01) == pytest.approx(config.CHAINLINK_RULE_READ["float"], abs=0.01)
    assert round(flt / 1e9, 3) == 0.704


def test_3_the_path_wallets_are_tagged_inferred_and_the_vault_published():
    keys = [k for k in P["contracts"] if k.startswith("issuer_path_")]
    assert len(keys) == 3
    for k in keys:
        c = P["contracts"][k]
        assert c["metric_override"] == "issuer_path_holding_tokens" and c["kind"] == "treasury_holding"
        assert "INFERRED" in c["provenance"] and "INFERRED" in c["purpose"], k
        assert c["required_component"] is True                              # all three or no row
    v = P["contracts"]["reward_vault_balance"]
    assert v["metric_override"] == "reward_vault_balance_tokens" and v["provenance"].startswith("PUBLISHED")
    assert v["address"].lower() == P["contracts"]["staking_reward_vault"]["address"].lower()
    assert sum(config.CHAINLINK_RULE_READ["path_wallets"].values()) == pytest.approx(PATH)


def test_1_the_references_are_compared_with_ours_less_the_33_wallets_only():
    # Chainlink's own API and CoinGecko leave out the 33 wallets and count the Reserve, the vault and the path wallets
    assert credibility._rule_minus(CL) == ("noncirculating_holding_tokens",)
    ci = credibility.circulating_input(CL)
    assert ci["ours"] == {"py": "now_combo", "args": {"metrics": ("circulating_supply_onchain",),
                                                      "minus": ("noncirculating_holding_tokens",)}}
    api = config.CHAINLINK_RULE_READ["chainlink_api"]["circulating"]
    assert 1e9 - NC33 == pytest.approx(api, abs=0.01) == pytest.approx(766_849_970.42, abs=0.01)
    assert config.CHAINLINK_RULE_READ["coingecko"] == api                   # 0.0% apart, to the token
    # every other project on the rule keeps the whole off-float sum (or nothing): the knob is read only where declared
    for n, s in config.RULE_IN.items():
        if n != CL:
            assert "reference_minus" not in s, n
            assert credibility._rule_minus(n) in ((), ("rule_in_off_float_tokens",)), n


def test_2_the_reserve_leaves_the_effective_float_once():
    """A3's effective float subtracted the Reserve by its destination AND would subtract it again through the rule's
    off-float stock: where the rule's off-float holds buyback_fund_balance the destination term is not added. Chainlink
    is the only project declared so — no other project's formula changes."""
    holds = [n for n, s in config.RULE_IN.items() if "buyback_fund_balance" in (s.get("off_float") or ())]
    assert holds == [CL]
    assert not set(holds) & set(config.FROZEN_PROJECTS)
    # on the dump: 1,000,000,000 − staked 42,525,190.83 − (Reserve 6,204,681.79 + the 33 233,150,000.10) = 718,120,127
    eff = 1e9 - _dump_series("locked_tokens").iloc[-1] - (_dump_series("buyback_fund_balance").iloc[-1]
                                                          + _dump_series("noncirculating_holding_tokens").iloc[-1])
    assert eff == pytest.approx(718_120_127, abs=1)                          # = the tradeable float; was 711,915,445


# ===================================================================== decisions 4 and 7: the three revenue lines
def test_4_7_the_three_revenue_lines():
    s = config.revenue_split(CL)
    assert s["fees"]["metric"] == "customer_revenue_usd"                    # 7 (b)
    assert s["revenue"]["metric"] == "actual_buyback_usd"                   # the Reserve inflow x the day's price
    assert s["holder_revenue"]["metric"] == "holder_value_revenue_usd"      # 4 (a): the held inflow
    assert "HELD BY THE ISSUER" in s["holder_revenue"]["route"] and "NOT DISTRIBUTED" in s["holder_revenue"]["route"]
    assert s["strict_holder_revenue_usd"] == 0.0 and "$0" in s["holder_revenue"]["route"]      # 4 (b) beside it
    assert "100% BY CONSTRUCTION" in s["holder_share"]["route"]
    assert bw.s13_metric(CL, "fees") == "customer_revenue_usd"
    for m in ("customer_revenue_usd", "actual_buyback_usd", "holder_value_revenue_usd"):
        assert m in config.metrics_for_project(P), m


def test_4_holder_revenue_is_the_reserve_inflow_row_for_row(scoped):
    """The credited holder value IS the Reserve inflow at each day's price (revenue-funded, held): the share is 1."""
    days = pd.date_range("2026-08-22", "2026-11-19")
    usd = [(d, 1_159_171.56 if d.dayofweek == 3 else 0.0) for d in days]    # weekly, Thursdays
    g = {(CL, "actual_buyback_usd"): _frame("actual_buyback_usd", usd, "derived:tokens*price"),
         (CL, "retired_buyback_usd"): _frame("retired_buyback_usd", usd, "derived:burn+hold"),
         (CL, "price_usd"): _frame("price_usd", [(d, 9.0) for d in days], "coingecko")}
    assert config.holder_funding(CL, "2026-11-19")[0] == "revenue"
    bw._holder_value_views(g)
    hv = g[(CL, "holder_value_revenue_usd")]["value"].sum()
    assert hv == pytest.approx(sum(v for _d, v in usd)) and hv > 0
    assert hv / g[(CL, "actual_buyback_usd")]["value"].sum() == pytest.approx(1.0)


def test_4_7_on_the_dump_fees_and_revenue_read_as_built():
    """Q0 of the fixture (2026-07-16..10-13), from its stored rows: revenue = holder revenue = $14,369,4xx."""
    ab = _dump_series("actual_buyback_usd")
    q0 = ab[(ab.index > pd.Timestamp("2026-10-13") - pd.Timedelta(days=90)) & (ab.index <= pd.Timestamp("2026-10-13"))]
    assert q0.sum() == pytest.approx(14_369_400, rel=2e-4)


# ===================================================================== decision 5: the insider share of stake
def test_5_insider_stake_is_every_wallet_in_every_pool_or_nothing():
    from eth_utils import keccak
    from fetch import reward_vault as rv
    assert rv.SEL_STAKER_PRINCIPAL == "0x" + keccak(text="getStakerPrincipal(address)").hex()[:8]
    ins = P["reward_vault_rates"]["insider_stake"]
    assert len(ins["wallets"]) == 42 and len(ins["pools"]) == 2
    assert {a.lower() for a in ins["pools"]} == {P["contracts"]["staking_community_principal"]["address"].lower(),
                                                 P["contracts"]["staking_node_operator_principal"]["address"].lower()}
    asked = []

    def call(to, data, block):
        asked.append((to, data[-40:], block))
        return "0x" + hex(7 * 10 ** 18)[2:].rjust(64, "0") if data.endswith(ins["wallets"][0].lower()[2:]) else "0x" + "0" * 64
    juels = rv.insider_principal(call, ins["pools"], ins["wallets"], 26_477_750)
    assert juels == 2 * 7 * 10 ** 18                                        # the one staking wallet, in both pools
    assert len(asked) == 84 and {b for _t, _w, b in asked} == {26_477_750}  # every wallet x every pool, one block
    assert {w for _t, w, _b in asked} == {a.lower()[2:] for a in ins["wallets"]}
    with pytest.raises(ValueError):                                         # one empty answer: no figure at all
        rv.insider_principal(lambda to, data, block: "0x", ins["pools"], ins["wallets"], 1)


def test_5_the_run_stores_the_measured_zero_and_no_row_when_a_read_fails():
    import types
    from fetch import reward_vault as rv
    from fetch.base import FetchOutput
    spec = P["reward_vault_rates"]
    now = 1_795_000_000
    buckets = "0x" + "".join(hex(v)[2:].rjust(64, "0") for v in (10 ** 15, now + 10 ** 6, 0) * 3)

    def tv(s):
        b = s.encode()
        return "0x" + hex(32)[2:].rjust(64, "0") + hex(len(b))[2:].rjust(64, "0") + b.hex().ljust(64, "0")

    class _Hex(str):
        def hex(self):
            return str(self)

    def make(fail):
        def call(tx, block=None):
            d, to = tx["data"], tx["to"].lower()
            if d == rv.SEL_TYPE_AND_VERSION:
                return _Hex(tv("RewardVault 1.0.0" if to == spec["address"].lower() else "CommunityStakingPool 1.0.0"))
            if d == rv.SEL_GET_REWARD_BUCKETS:
                return _Hex(buckets)
            if d == rv.SEL_TOTAL_PRINCIPAL:
                return _Hex("0x" + hex(40_875_000 * 10 ** 18)[2:].rjust(64, "0"))
            if d.startswith(rv.SEL_STAKER_PRINCIPAL):
                if fail:
                    raise RuntimeError("rpc refused")
                return _Hex("0x" + "0" * 64)
            raise AssertionError(d)
        eth = types.SimpleNamespace(call=call, get_block=lambda _b: {"number": 26_477_750, "timestamp": now})
        return types.SimpleNamespace(web3=lambda chain: types.SimpleNamespace(eth=eth), checksum=lambda a: a)
    out = FetchOutput()
    rv.RewardVaultRates(reader=make(False)).run([P], None, out)
    f = out.frame()
    row = f[f["metric"] == "insider_stake_principal_tokens"]
    assert len(row) == 1 and float(row["value"].iloc[0]) == 0.0             # a measured 0, stored
    assert "42 wallets x 2 pools" in str(row["source"].iloc[0])
    out2 = FetchOutput()
    rv.RewardVaultRates(reader=make(True)).run([P], None, out2)
    f2 = out2.frame()
    assert "insider_stake_principal_tokens" not in set(f2["metric"])        # a failed read: no row, never a 0
    assert "reward_emission_rate_annual" in set(f2["metric"])               # the rest of the vault read is unharmed


def test_5_the_share_is_a_measured_zero_floor_over_the_pools_principal():
    num, den, what = config.INSIDER_LOCK[CL]
    assert (num, den) == ("insider_stake_principal_tokens", "locked_tokens_principal")
    assert "FLOOR" in what and "MEASURED 0" in what
    assert 0.0 / PRINCIPAL == 0.0 and P["reward_vault_rates"]["insider_stake"]["read"]["principal"] == 0.0
    tags = [t for _a, t in config.CHAINLINK_INSIDER_WALLETS]
    assert sum(t.startswith("PUBLISHED") for t in tags) == 34 and sum(t.startswith("INFERRED") for t in tags) == 8
    assert num in config.metrics_for_project(P)


def test_5_leakage_i_is_zero_when_all_the_credited_value_was_retired():
    """Chainlink's credited holder value is held in the Reserve (retired), never distributed: Leakage (i) = insider
    share x distributed ÷ credited is 0 whatever the share — also before the share's first row is stored."""
    assert config.LEAKAGE_I_ALL_RETIRED_IS_ZERO == (CL,)
    cols = bw._cp_cols(bw.Refs(10, 10, ["2026-09"]), {})
    build = next(f for h, f, *_ in cols if h.startswith("Leakage (i)"))
    f = build(5, P)
    assert f.startswith("=IF(NOT(ISNUMBER(") and "<=0,0," in f
    assert "insider_stake_principal_tokens" in f and "locked_tokens_principal" in f      # a distributing quarter: the share
    # the arithmetic of the two branches: credited 14,369,400 all retired -> 0; 10% distributed at a 0 share -> 0
    cr, rt = 14_369_400.0, 14_369_400.0
    assert (0 if cr - rt <= 0 else None) == 0
    assert (0.0 / PRINCIPAL) * (cr - 0.9 * cr) / cr == 0.0


# ===================================================================== decision 6: node-operator payments are (b)
def test_6_node_operator_payments_are_outflow_b_and_the_recipients_are_inferred():
    # R20 (Jake 2026-11-24): (b) also holds the Safe 0x77dd1a9b's direct payments to the same recipients (was the
    # contract's alone); the exchange leg is category (f), netted out of (e) (was: it stays in (e)); the label reads
    # "path PUBLISHED ..., addresses not named" (was "node operators, INFERRED") - tests/test_r20_decisions.py
    assert config.outflow_metrics(CL, "b") == ("node_operator_payments_tokens", "node_operator_payments_safe_tokens")
    assert config.outflow_metrics(CL, "e") == ("unlock_release_tokens",)
    np_ = config.CHAINLINK_NODE_PAYMENTS
    assert np_["recipients"] == 63
    rd = np_["read"]
    assert sum(rd["by_quarter"].values()) == pytest.approx(rd["paid_all_time"], abs=3)      # 5,132,390.40 (rounded rows)
    assert sum(rd["funded_by"].values()) == pytest.approx(rd["paid_all_time"], abs=3)       # every LINK's payer named
    scan = next(s for s in P["log_scans"] if s["key"] == "node_payments_out")
    assert scan["holders"] == [np_["payer"]] and scan["direction"] == "out" and scan["store"]
    assert "0x9A709B7B69EA42D5eeb1ceBC48674C69E1569eC6" in scan["exclude_counterparties"]   # the Reserve leg is the buyback
    for m in ("outflow_b_tokens", "outflow_b_usd"):
        assert CL in config.METRICS[m]["only_projects"]


def _elog(block, idx, topics, value, ts):
    return {"address": "0xtoken", "topics": topics, "data": hex(int(value)), "blockNumber": hex(block),
            "timeStamp": hex(ts), "logIndex": hex(idx), "transactionHash": f"0x{block:x}{idx:x}"}


class _Http:
    def __init__(self, logs):
        self.logs = logs

    def get(self, url, params=None, headers=None, deadline=None):
        key = (params.get("address", "").lower(), params.get("topic1"), params.get("topic2"))
        top = params.get("toBlock")
        top = float("inf") if top in (None, "latest") else int(top)
        rows = [r for r in self.logs.get(key, []) if int(params["fromBlock"]) <= int(r["blockNumber"], 16) <= top]
        off, page = int(params["offset"]), int(params["page"])
        chunk = rows[(page - 1) * off: page * off]
        return ({"status": "1", "message": "OK", "result": chunk} if chunk
                else {"status": "0", "message": "No records found", "result": []})


class _Call:
    def __init__(self, v):
        self.v = v

    def call(self, block_identifier=None):
        return self.v


class _Reader:
    def __init__(self, balances):
        self.balances = {k.lower(): v for k, v in balances.items()}

    def web3(self, chain):
        import types
        return types.SimpleNamespace(eth=types.SimpleNamespace(block_number=1_000))

    def erc20(self, chain, token):
        import types
        b = self.balances
        return types.SimpleNamespace(functions=types.SimpleNamespace(balanceOf=lambda h: _Call(b[h.lower()]),
                                                                     decimals=lambda: _Call(18)))

    @staticmethod
    def checksum(a):
        return a


def _scan(monkeypatch, spec, logs, balances):
    from fetch.base import FetchOutput
    from fetch.explorer import ExplorerLogs
    from fetch.logscan import LogScan
    monkeypatch.setenv("ETHERSCAN_API_KEY", "K")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "K")
    monkeypatch.setitem(config.EXPLORERS["etherscan"], "max_records", 1000)
    monkeypatch.setitem(config.EXPLORERS["blockscout"], "max_records", 1000)
    out = FetchOutput()
    LogScan(explorer=ExplorerLogs(http=_Http(logs)), reader=_Reader(balances))._scan({"name": CL}, spec, None, out)
    return out.frame()


def test_6_the_scan_counts_what_the_contract_pays_to_anyone_but_the_reserve(monkeypatch):
    from fetch.explorer import TRANSFER_TOPIC, pad_address
    E, t0 = 10 ** 18, int(pd.Timestamp("2026-11-19").timestamp())
    real = next(s for s in P["log_scans"] if s["key"] == "node_payments_out")
    pal, reserve = real["holders"][0].lower(), "0x9a709b7b69ea42d5eeb1cebc48674c69e1569ec6"
    op1, op2, payer, conv = "0x" + "a1" * 20, "0x" + "a2" * 20, "0x" + "b1" * 20, "0x" + "c1" * 20
    hp = pad_address(pal)
    logs = {("0xtoken", None, hp): [_elog(10, 0, [TRANSFER_TOPIC, pad_address(conv), hp], 128_907 * E, t0),
                                    _elog(11, 0, [TRANSFER_TOPIC, pad_address(payer), hp], 100_000 * E, t0)],
            ("0xtoken", hp, None): [_elog(12, 1, [TRANSFER_TOPIC, hp, pad_address(reserve)], 128_907 * E, t0),
                                    _elog(13, 1, [TRANSFER_TOPIC, hp, pad_address(op1)], 60_000 * E, t0),
                                    _elog(14, 1, [TRANSFER_TOPIC, hp, pad_address(op2)], 40_000 * E, t0)]}
    spec = dict(real, token="0xtoken", exclude_counterparties=[a.lower() for a in real["exclude_counterparties"]])
    f = _scan(monkeypatch, spec, logs, {pal: 0})
    assert f["value"].sum() == 100_000.0, f            # the two operators; the Reserve leg (the buyback) is not (b)


def test_6_each_payment_leaves_the_release_on_its_lumps_day_fifo():
    """(e) loses exactly what (b) counts: payments draw FIFO on the lumps from 2025-03-14, each lump first less its
    staking part; taken out on the lump's day; a payment no lump covers is not attributed."""
    pl = config.RELEASE_PAID_LATER[CL]
    # R20: two payment paths (was "node_operator_payments_tokens" alone)
    assert pl["paid"] == ("node_operator_payments_tokens", "node_operator_payments_safe_tokens")
    assert pl["from"] == "2025-03-14"
    days = pd.date_range("2025-01-01", "2025-12-31")
    release = pd.Series(0.0, index=days)
    lumps = {"2025-01-10": 1_000_000.0, "2025-03-14": 4_125_000.0, "2025-06-20": 3_125_000.0}   # to 0xD50f074E…
    for d, v in lumps.items():
        release.loc[pd.Timestamp(d)] = v + 14_875_000.0                       # + the exchange leg that day
    g = {(CL, pl["lumps"]): _frame(pl["lumps"], list(lumps.items()), "explorer:staking_path_in"),
         (CL, pl["after"]): _frame(pl["after"], [("2025-03-14", 607_500.0)], "derived:fifo"),
         (CL, pl["paid"][0]): _frame(pl["paid"][0], [("2025-04-03", 3_000_000.0), ("2025-07-03", 1_000_000.0),
                                               ("2025-12-18", 5_000_000.0)], "explorer:node_payments_out")}
    total, src = bw._paid_later_split(g, CL, pl, release, g[(CL, pl["lumps"])].columns)
    out = g[(CL, pl["out"])].set_index("date")["value"]
    # lump 03-14 holds 4,125,000 − 607,500 staking = 3,517,500: 3,000,000 (04-03) + 517,500 of the 07-03 payment;
    # the other 482,500 of 07-03 and 2,642,500 of 12-18 draw on the 06-20 lump (3,125,000); 2,357,500 has no lump
    assert out.loc[pd.Timestamp("2025-03-14")] == pytest.approx(3_517_500.0)
    assert out.loc[pd.Timestamp("2025-06-20")] == pytest.approx(3_125_000.0)
    assert out.sum() == pytest.approx(6_642_500.0)                            # never more than the lumps held
    assert pd.Timestamp("2025-01-10") not in out.index                        # a lump before the declared start
    assert release.sum() - total.sum() == pytest.approx(out.sum())            # (e) loses exactly that
    assert total.loc[pd.Timestamp("2025-03-14")] == pytest.approx(4_125_000.0 + 14_875_000.0 - 3_517_500.0)
    assert (total >= 0).all() and "node_payments_release_tokens" in src
    # nothing stored yet (the fixture's case): the release reads as before
    t2, s2 = bw._paid_later_split({}, CL, pl, release, None)
    assert t2 is release and s2 == ""


def test_6_total_dilution_is_not_blocked_while_the_payments_scan_has_no_row(scoped):
    """No (b) row stored = nothing netted out of (e): the payments are still inside the release, so total dilution
    (a + e) is complete; the (b) cells themselves say the series is not stored."""
    days = pd.date_range("2026-07-16", "2026-10-13")
    g = {(CL, "price_usd"): _frame("price_usd", [(d, 12.0) for d in days], "coingecko"),
         (CL, "emissions_tokens"): _frame("emissions_tokens", [(d, 5_547.95) for d in days], "rewardvault"),
         (CL, "unlock_release_tokens"): _frame("unlock_release_tokens",
                                               [(d, 18_750_000.0 if d == pd.Timestamp("2026-10-09") else 0.0)
                                                for d in days], "derived:unlock_release")}
    bw._VIEW_BLOCKS.clear()
    bw._outflow_views(g)
    assert (CL, "b") in bw.PAID_LATER_NOT_READ and (CL, "outflow_b_tokens") not in g
    assert bw._cp_cats(P) == ["a", "e"]                                       # dilution sums what is stored and whole
    assert "no rows stored" in bw._VIEW_BLOCKS[(CL, "outflow_b_usd")]         # SUBSIDY COVER still says so
    assert g[(CL, "outflow_e_tokens")]["value"].sum() == 18_750_000.0
    # once the scan has stored the payments, (b) is summed
    g[(CL, "node_operator_payments_tokens")] = _frame("node_operator_payments_tokens",
                                                      [(d, 100_000.0 if d.dayofweek == 3 else 0.0) for d in days],
                                                      "explorer:node_payments_out")
    bw._VIEW_BLOCKS.clear()
    bw._outflow_views(g)
    # R20: the second payment path's scan stores its own series ((b) is formed only with both) and the exchange leg is
    # category (f), not read here - still wholly inside (e), so it blocks nothing (was: no entry, cats a, b, e)
    g[(CL, "node_operator_payments_safe_tokens")] = _frame("node_operator_payments_safe_tokens",
                                                           [(d, 0.0) for d in days], "explorer:node_payments_safe_out")
    bw._VIEW_BLOCKS.clear()
    bw._outflow_views(g)
    assert set(bw.PAID_LATER_NOT_READ) == {(CL, "f")} and bw._cp_cats(P) == ["a", "b", "e"]
    assert g[(CL, "outflow_b_usd")]["value"].sum() == pytest.approx(13 * 100_000.0 * 12.0)
    bw.PAID_LATER_NOT_READ.clear()
    bw._VIEW_BLOCKS.clear()


# ===================================================================== decision 8: the exchange leg and the flag
def test_8_the_flag_is_declared_on_the_33_wallets_and_the_binance_tagged_destinations():
    spec = config.THEATRE_FLAG[CL]
    assert len(spec["wallets"]) == 33
    assert {a.lower() for a in spec["destinations"]} == {"0x9b1c1752b6b6eaf62a8c491a8681b32c1f7ecde9",
                                                         "0x28c6c06298d514db089934071355e5743bf21d60"}
    for a, d in spec["destinations"].items():
        assert d["label_source"] and "2026-11-23" in d["label_source"] and "Binance" in d["tag"], a
    assert "evidence, not proof" in spec["label_caveat"]
    assert spec["window"]["from"] == "2025-08-07" and spec["scored"] == "as_written"
    scan = next(s for s in P["log_scans"] if s["key"] == "exchange_leg_out")
    assert scan["metric"] == spec["outflow_metric"] == "issuer_exchange_outflow_tokens"
    assert set(scan["count_to"]) == set(spec["destinations"]) and len(scan["holders"]) == 33
    assert scan["own_tier"] == "explorer_noncirc@Chainlink"
    # Ether.fi's flag, its series and its label are not touched
    assert config.THEATRE_FLAG["Ether.fi"]["outflow_metric"] == "theatre_exchange_outflow_tokens"
    assert config.METRICS["theatre_exchange_outflow_tokens"]["only_projects"] == ("Ether.fi",)


def test_8_the_research_read_adds_up():
    rr = config.THEATRE_FLAG[CL]["research_read"]
    assert sum(rr["by_day"].values()) == rr["sent"] == 77_600_000.0 == sum(rr["by_destination"].values())
    assert rr["sent"] / rr["retired_in_window"] == pytest.approx(rr["size"], abs=1e-4) == pytest.approx(11.4038, abs=1e-4)
    d = rr["at_dump_asof"]
    assert d["sent"] / d["retired"] == pytest.approx(d["size"], abs=1e-4) == pytest.approx(12.5067, abs=1e-4)
    # the five release days are the pack's table: the part of each release that went to 0x9B1c1752…
    assert list(rr["by_day"].values()) == [18_750_000.0, 9_225_000.0, 14_375_000.0, 18_375_000.0, 16_875_000.0]


def test_8_the_scan_counts_only_the_sends_to_the_tagged_addresses(monkeypatch):
    from fetch.explorer import TRANSFER_TOPIC, pad_address
    E, t0 = 10 ** 18, int(pd.Timestamp("2026-10-09").timestamp())
    real = next(s for s in P["log_scans"] if s["key"] == "exchange_leg_out")
    w1, w2 = (a.lower() for a in real["holders"][:2])
    fwd, hop, src = real["count_to"][0].lower(), "0xd50f074ec5c7997ce1daacb513ec194c480878af", "0x" + "cd" * 20
    h1, h2 = pad_address(w1), pad_address(w2)
    logs = {("0xtoken", None, h1): [_elog(10, 0, [TRANSFER_TOPIC, pad_address(src), h1], 30 * E, t0)],
            ("0xtoken", h1, None): [_elog(20, 1, [TRANSFER_TOPIC, h1, pad_address(fwd)], 16 * E, t0),
                                    _elog(21, 1, [TRANSFER_TOPIC, h1, pad_address(hop)], 2 * E, t0),
                                    _elog(22, 1, [TRANSFER_TOPIC, h1, h2], 1 * E, t0)],
            ("0xtoken", None, h2): [_elog(22, 0, [TRANSFER_TOPIC, h1, h2], 1 * E, t0)],
            ("0xtoken", h2, None): []}
    spec = dict(real, token="0xtoken", holders=[w1, w2], count_to=[a.lower() for a in real["count_to"]])
    f = _scan(monkeypatch, spec, logs, {w1: 11 * E, w2: 1 * E})
    assert f["value"].sum() == 16.0, f      # to the deposit address; the 0xD50f074E… lump and the internal hop are not


def _flag_groups(sent_days, retired, start, asof, retired_source="derived:burn+hold"):
    spec = config.THEATRE_FLAG[CL]
    days = pd.date_range("2022-08-26", asof)                                # a log scan: daily from the first transfer
    rdays = pd.date_range(start, asof)
    return {(CL, spec["outflow_metric"]): _frame(spec["outflow_metric"],
                                                 [(d, sent_days.get(str(d.date()), 0.0)) for d in days],
                                                 "explorer:exchange_leg_out"),
            (CL, spec["retired_metric"]): _frame(spec["retired_metric"], [(d, retired.get(d, 0.0)) for d in rdays],
                                                 retired_source),
            (CL, "actual_buyback_tokens"): _frame("actual_buyback_tokens", [(d, retired.get(d, 0.0)) for d in rdays],
                                                  "explorer:reserve_inflow")}


def test_8_the_flag_reads_raised_at_the_live_figures(scoped):
    """The store's case once the scan has run: 77.6M sent since 2025-08-07 against the LINK retired into the Reserve."""
    spec, rr = config.THEATRE_FLAG[CL], config.THEATRE_FLAG[CL]["research_read"]
    asof = pd.Timestamp("2026-11-23")
    thursdays = [d for d in pd.date_range("2025-08-07", "2026-11-19") if d.dayofweek == 3]
    retired = {d: rr["retired_in_window"] / len(thursdays) for d in thursdays}
    g = _flag_groups(dict(rr["by_day"], **{"2025-06-20": 17_875_000.0}), retired, "2025-08-07", asof)
    bw._theatre_flag_views(g, asof)
    r = bw.THEATRE_RESULTS[CL]
    assert r["sent"] == 77_600_000.0 == r["named_sent"]                     # the 2025-06-20 release is before the window
    assert r["retired"] == pytest.approx(6_804_737.24) == pytest.approx(r["bought"])
    assert r["as_written"]["state"] == "RAISED" == r["any_buyback"]["state"]
    assert r["as_written"]["size"] == pytest.approx(11.4038, abs=1e-4)      # 1,140% of the retired tokens
    assert bw.theatre_flag_size(CL) == f"={r['as_written']['size']!r}"
    txt = bw.theatre_flag_text(CL)
    # R20: the cell opens with the long wording (W1), its figures from these series; the scored line follows
    assert txt.startswith("RAISED — 11.4x. Since the Reserve began (2025-08-07)") and "77.6M LINK" in txt
    assert "SCORED — AS WRITTEN (tokens RETIRED by buyback): RAISED" in txt
    for must in ("Binance", "Blockscout", "evidence, not proof", "2025-08-07", "NAMED FLOAT OUTFLOW"):
        assert must in txt, must
    assert spec["window"]["from"] == str(r["window"][0])


def test_8_on_the_dump_the_flag_is_not_testable_and_never_not_raised(scoped):
    """The fixture dump holds no row of the new outflow series and starts 2025-09-08, after the window opens: NOT
    TESTABLE — with the live read shown as research, not as the store's figure."""
    spec = config.THEATRE_FLAG[CL]
    assert not [r for r in _dump()["metrics"] if r["metric"] == spec["outflow_metric"]]
    asof = pd.Timestamp("2026-10-13")
    ab = _dump_series("actual_buyback_tokens")
    assert ab.index.min() == pd.Timestamp("2025-09-08")
    g = {(CL, "actual_buyback_tokens"): _frame("actual_buyback_tokens", list(ab.items()), "explorer:reserve_inflow"),
         (CL, "retired_buyback_tokens"): _frame("retired_buyback_tokens", list(ab.items()), "derived:burn+hold")}
    bw._theatre_flag_views(g, asof)
    r = bw.THEATRE_RESULTS[CL]
    assert r["as_written"]["state"] == r["any_buyback"]["state"] == "NOT TESTABLE" and r["sent"] is None
    assert bw.theatre_flag_size(CL).startswith("n/a — NOT TESTABLE")
    assert "RESEARCH READ (not the store" in bw.theatre_flag_text(CL)
    # what the dump's rows DO hold over its own days (2025-09-08..2026-10-12), said as what it is:
    out_all = _dump_series("noncirc_outflow_scan_tokens")                   # the 33 wallets' outflow, EVERY destination
    assert out_all.sum() == pytest.approx(88_750_000.0) and ab.sum() == pytest.approx(5_967_667.64, abs=0.01)
    assert out_all.sum() / ab.sum() == pytest.approx(14.87, abs=0.01)       # an upper bound, not the flag
    # the exchange part of those five days is the live read's 77.6M (87.4%); the rest went to 0xD50f074E…
    assert 77_600_000.0 / ab.sum() == pytest.approx(13.00, abs=0.01)
    assert [str(d.date()) for d in out_all[out_all > 1].index] == list(spec["research_read"]["by_day"])


def test_8_the_named_float_outflow_is_a_memo_line_beside_the_identity(scoped):
    lab, m, note = next(x for x in config.FLOW_TOKEN_MEMO[CL] if x[1] == config.THEATRE_FLAG[CL]["outflow_metric"])
    assert lab.startswith("memo: named float outflow")
    assert "purpose not established" in lab and "evidence, not proof" in note
    asof = pd.Timestamp("2026-10-13")
    days = pd.date_range("2025-06-01", asof)
    g = {(CL, "total_supply"): _frame("total_supply", [(d, 1e9) for d in days]),
         (CL, "circulating_supply_used"): _frame("circulating_supply_used", [(d, 1e9) for d in days],
                                                 "derived:circulating_supply_used[circulating_supply_onchain]"),
         (CL, m): _frame(m, [(d, 16_875_000.0 if d == pd.Timestamp("2026-10-09") else 0.0) for d in days],
                         "explorer:exchange_leg_out")}
    rows = {r["Line"]: r for r in bw.flow_statements(g, asof) if r["Project"] == CL and r["Statement"] == "Token flow"}
    assert rows[lab]["Q0"] == 16_875_000.0
    assert rows["Opening circulating"]["Q0"] == 1e9 == rows["Closing circulating"]["Q0"]   # the rule: flat at the total
    assert rows["Residual ÷ opening"]["Q0"] == 0


def test_1_the_release_stays_on_the_statement_as_a_memo_and_the_reference_reads_it(scoped):
    """Under the rule the 18.75M release of 2026-10-09 is no circulating change; it is the statement's memo line, and the
    quarterly-release reference (18,750,000, independent_references.csv) is read against that line."""
    import references as rf
    assert rf.ROUTES[(CL, "quarterly_release")] == ("flows", config.CHAINLINK_RELEASE_MEMO, "Q0")
    asof = pd.Timestamp("2026-10-13")
    days = pd.date_range("2025-06-01", asof)
    g = {(CL, "total_supply"): _frame("total_supply", [(d, 1e9) for d in days]),
         (CL, "circulating_supply_used"): _frame("circulating_supply_used", [(d, 1e9) for d in days],
                                                 "derived:circulating_supply_used[circulating_supply_onchain]"),
         (CL, "pool_release_tokens"): _frame("pool_release_tokens",
                                             [(d, 18_750_000.0 if d == pd.Timestamp("2026-10-09") else 0.0)
                                              for d in days], "derived:release")}
    rows = {r["Line"]: r for r in bw.flow_statements(g, asof) if r["Project"] == CL and r["Statement"] == "Token flow"}
    assert rows[config.CHAINLINK_RELEASE_MEMO]["Q0"] == 18_750_000.0
    assert rows["Expected closing (opening + minted + released − returned − burned)"]["Q0"] == 1e9   # not in the closing


# ===================================================================== nothing frozen moves
def test_nothing_declared_here_touches_a_frozen_project():
    assert CL not in config.FROZEN_PROJECTS
    assert set(config.RELEASE_PAID_LATER) == {CL}
    for n in config.FROZEN_PROJECTS:
        assert "insider_stake" not in (config.PROJECT_BY_NAME[n].get("reward_vault_rates") or {})
    new = ("issuer_path_holding_tokens", "reward_vault_balance_tokens", "insider_stake_principal_tokens",
           "node_operator_payments_tokens", "node_payments_release_tokens", "issuer_exchange_outflow_tokens",
           "circulating_supply_chainlink_api")
    for m in new:
        assert config.METRICS[m]["only_projects"] == (CL,), m


def test_chainlinks_own_figure_is_read_as_a_reference():
    import yaml
    e = [x for x in yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
         if x.get("metric") == "circulating_supply_chainlink_api"]
    assert len(e) == 1 and e[0]["project"] == CL and e[0]["enabled"] is True
    assert e[0]["url"] == "https://supply.chain.link/supply" and e[0]["json_path"] == "circulatingSupply"
    assert e[0]["decimals"] == 18
    assert 766849970424883961240529443 / 10 ** e[0]["decimals"] == pytest.approx(766_849_970.42, abs=0.01)
    assert e[0]["sanity_min"] < 766_849_970.42 < e[0]["sanity_max"]
    assert "supply.chain.link" in config.SOURCE_REGISTER
