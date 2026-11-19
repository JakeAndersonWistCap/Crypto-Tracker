"""ROUND 11 — NEAR (Jake 2026-11-19). SPEC.md "Round 11 — Near".

3 each S13 line split into gas and NEAR Intents (parts that sum to the line, cross-checks on the same days), the 3.7x
explained, the two stale references; 4 the NEAR Foundation account set and the insider share of stake; 5 the House of
Stake payouts as Near's supply-side incentive (b): SUBSIDY COVER over the days every leg covers.
"""
from __future__ import annotations

import base64
import csv
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

NEAR = config.PROJECT_BY_NAME["Near"]


def _g(metric, days, values, source="x", project="Near"):
    days = list(days)
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in vals], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    monkeypatch.setattr(bw, "S13_STATE", {})


def _day(groups, metric, project="Near"):
    return bw._daily(groups.get((project, metric)))


# ============================================================================================ 3a the parts of a line
def _s13_groups(buyback=900.0):
    d = pd.date_range("2026-08-01", "2026-11-10")
    g = {("Near", "price_usd"): _g("price_usd", d, 2.0, "coingecko"),
         ("Near", "gross_burn_tokens_bigquery"): _g("gross_burn_tokens_bigquery", d, 100.0,
                                                    "near_bigquery:blocks.total_supply"),
         ("Near", "fees_usd"): _g("fees_usd", d, 260.0, "defillama"),
         ("Near", "revenue_usd"): _g("revenue_usd", d, 182.0, "defillama"),
         ("Near", "holders_revenue_usd"): _g("holders_revenue_usd", d, 182.0, "defillama"),
         # NearBlocks' fee total is stored for part of the window only
         ("Near", "fees_native_tokens"): _g("fees_native_tokens", pd.date_range("2026-10-01", "2026-11-10"), 128.0,
                                            "nearblocks"),
         ("Near", "intents_fees_usd"): _g("intents_fees_usd", pd.date_range("2026-08-01", "2026-11-09"), 5_000.0,
                                          "defillama:near-intents"),
         ("Near", "intents_revenue_usd"): _g("intents_revenue_usd", d, 1_000.0, "defillama:near-intents")}
    bd = pd.date_range("2026-08-01", "2026-11-08")
    g[("Near", "actual_buyback_usd")] = _g("actual_buyback_usd", bd, buyback, "derived:actual_buyback_tokens*price_usd")
    g[("Near", "actual_buyback_tokens")] = _g("actual_buyback_tokens", bd, buyback / 2.0, "nearblocks:stats/balance:delta")
    return g


def _build(g, asof="2026-11-10"):
    bw._chain_burn_revenue_views(g)
    bw._revenue_total_views(g)
    bw._holder_value_views(g)
    bw._s13_views(g)
    bw._s13_component_views(g, pd.Timestamp(asof))
    return g


def test_3a_the_parts_of_each_line_sum_to_the_line_on_every_covered_day(scope):
    g = _build(_s13_groups())
    comp = config.S13_COMPONENTS["Near"]["lines"]
    assert set(comp) == {"fees", "revenue", "holder_revenue"}
    for role, parts in comp.items():
        line = _day(g, bw.s13_metric("Near", role))
        views = [_day(g, view) for _lab, _src, view in parts]
        assert len(views) == 2 and all(not v.empty for v in views), role
        total = sum(v.reindex(line.index).fillna(0.0) for v in views)
        assert (total - line).abs().max() < 1e-9, role                       # to the cent, every day
        # a part never reaches past the line's own last day (the last day ALL three lines hold: the buyback's)
        assert max(v.index.max() for v in views) == line.index.max() == pd.Timestamp("2026-11-08"), role
    day = pd.Timestamp("2026-10-20")
    assert _day(g, "s13c_fees_gas_usd")[day] == 260.0 and _day(g, "s13c_fees_intents_usd")[day] == 5_000.0
    assert _day(g, "s13c_revenue_gas_usd")[day] == 200.0 and _day(g, "s13c_revenue_intents_usd")[day] == 1_000.0
    # HOLDER REVENUE: the same gas burn, and the buyback as the line less it (the wallets' intake, not DefiLlama's)
    assert _day(g, "s13c_holder_gas_usd")[day] == 200.0 and _day(g, "s13c_holder_intents_usd")[day] == 900.0


def test_3a_the_holder_buyback_part_follows_the_wallets_intake_not_the_revenue_leg(scope):
    g = _build(_s13_groups(buyback=1_500.0))          # the wallets took in more than DefiLlama's captured revenue
    day = pd.Timestamp("2026-10-20")
    assert _day(g, "s13c_holder_intents_usd")[day] == 1_500.0 and _day(g, "s13c_revenue_intents_usd")[day] == 1_000.0
    q0 = bw.S13_STATE["Near"]["components"]
    for role, c in q0.items():                        # the note's Q0 figures are the parts of the cell's own figure
        assert sum(v for _lab, v in c["parts"]) == pytest.approx(c["line"], abs=0.01), role
    note = bw.s13_note(NEAR)
    assert f"{q0['fees']['line']:,.0f}" in note and f"{q0['holder_revenue']['parts'][1][1]:,.0f}" in note


def test_3a_a_crosscheck_is_on_its_parts_own_days_and_priced_where_it_is_in_tokens(scope):
    g = _build(_s13_groups())
    nb = _day(g, "s13x_fees_gas_nearblocks_usd")
    assert nb.index.min() == pd.Timestamp("2026-10-01") and nb.index.max() == pd.Timestamp("2026-11-08")
    assert nb.iloc[0] == 128.0 * 2.0                                          # NEAR x that day's price
    dl = _day(g, "s13x_revenue_gas_defillama_usd")
    assert dl.index.equals(_day(g, "s13c_revenue_gas_usd").index) and dl.iloc[-1] == 182.0
    assert _day(g, "s13x_holder_gas_defillama_usd").index.equals(_day(g, "s13c_holder_gas_usd").index)
    # a cross-check day outside the part's days is not carried (11-09 and 11-10 are after the lines' last common day)
    assert pd.Timestamp("2026-11-09") not in dl.index


def test_3a_only_near_has_components_and_no_existing_series_is_touched(scope):
    assert set(config.S13_COMPONENTS) == {"Near"} and set(config.VALUE_FLOW_COMPONENTS) == {"Near"}
    assert not set(config.S13_COMPONENTS) & set(config.FROZEN_PROJECTS)
    g = _s13_groups()
    for m in ("fees_usd", "revenue_usd"):                                     # another project's rows sit beside Near's
        g[("Hyperliquid", m)] = _g(m, pd.date_range("2026-08-01", "2026-11-10"), 7.0, "defillama", "Hyperliquid")
    bw._chain_burn_revenue_views(g)
    bw._revenue_total_views(g)
    bw._holder_value_views(g)
    bw._s13_views(g)
    before = {k: v.copy() for k, v in g.items()}
    bw._s13_component_views(g, pd.Timestamp("2026-11-10"))
    new = set(g) - set(before)
    assert new and all(n == "Near" and (m.startswith("s13c_") or m.startswith("s13x_")) for n, m in new)
    for k, v in before.items():
        pd.testing.assert_frame_equal(g[k], v)
    # every Flows line reads a declared, Near-only, view-only series
    for _lab, m, _why in config.VALUE_FLOW_COMPONENTS["Near"]:
        assert config.METRICS[m]["only_projects"] == ("Near",) and config.METRICS[m]["view_only"]


def test_3b_the_export_figures_add_up_and_explain_the_ratio():
    x = config.NEAR_S13_EXPLAINED
    for line in ("fees", "revenue", "holder_revenue"):
        assert x[line]["gas"] + x[line]["intents"] == pytest.approx(x[line]["total"], abs=1)
    assert x["fees"]["total"] / x["revenue"]["total"] == pytest.approx(3.67, abs=0.01)
    assert x["revenue"]["intents"] / x["fees"]["intents"] == pytest.approx(x["intents_capture"], abs=1e-4)
    assert x["revenue"]["gas"] / x["fees"]["gas"] == pytest.approx(x["gas_burn_share"], abs=1e-4)
    # what never becomes revenue is the whole of fees − revenue, and nearly all of it is the Intents partners' share
    never = x["never_revenue"]
    assert sum(never.values()) == pytest.approx(x["fees"]["total"] - x["revenue"]["total"], abs=1)
    assert never["intents_partners_and_solvers"] / sum(never.values()) > 0.99
    # DefiLlama on the same days: its own supply-side figure is that share, within its restatement since the export
    dl = x["defillama_same_days_read_2026_11_19"]["near-intents"]
    assert dl["supply_side"] == pytest.approx(never["intents_partners_and_solvers"], rel=0.001)
    assert dl["fees"] == pytest.approx(x["fees"]["intents"], rel=0.001)
    assert dl["revenue"] == pytest.approx(x["revenue"]["intents"], rel=0.001)


# ============================================================================================ 3c the two references
def test_3c_the_net_revenue_reference_is_read_against_the_30_days_before_its_own_read():
    spec = config.CREDIBILITY["Near"]["in_buyback"]
    man = spec["ref"]["manual"]
    args = spec["ours"]["args"]
    assert man["value"] == 1_900_000 and man["read_on"] == "2026-11-19"       # was 2_080_000 read 2026-10-07
    assert pd.Timestamp(args["end"]) == pd.Timestamp(man["read_on"]) - pd.Timedelta(days=1) and args["days"] == 30
    assert spec["ref"]["tol"] == 15.0                                         # the row's own tolerance, unchanged
    import credibility as cred
    days = pd.date_range("2026-10-01", "2026-11-19")
    long = pd.concat([pd.DataFrame({"date": days, "project": "Near", "metric": "actual_buyback_tokens", "value": 10.0}),
                      pd.DataFrame({"date": days, "project": "Near", "metric": "price_usd", "value": 3.0})],
                     ignore_index=True)
    v, d, _how = cred.FORMULAS["window_sum"]("Near", {}, long, pd.Timestamp("2026-11-25"),
                                             **{k: a for k, a in args.items() if k != "use_views"})
    assert v == 30 * 30.0 and d == "2026-11-18"                               # 2026-10-20..2026-11-18, never to as-of


def test_3c_the_pool_apr_is_a_measurement_of_a_named_pool_net_of_its_fee():
    with open(ROOT / "manual_references.csv", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["project"] == "Near" and r["row"] == "a1_validator_yield"]
    assert len(rows) == 1
    r, m = rows[0], config.NEAR_POOL_APR_READ
    assert r["read_on"] == "2026-11-19" and m["pool"] in r["url"]             # was 0.0456, 2026-10-07, an unnamed pool
    assert float(r["tol_pct"]) == 15.0                                        # unchanged
    apr = (m["growth"] - 1.0) * 365.0 / m["days"]
    assert apr == pytest.approx(m["net_apr"], abs=5e-5) and float(r["value"]) == pytest.approx(apr, abs=5e-5)
    # the second pool read the same way agrees once each is grossed up by its own fee (one network rate)
    other = m["also"]["bitwise_1.poolv1.near"]
    gross = [a / (1 - f[0] / f[1]) for a, f in ((m["net_apr"], m["fee"]), (other["net_apr"], other["fee"]))]
    assert gross[0] == pytest.approx(gross[1], rel=0.05)


# ============================================================================================ 4 the Foundation set
def test_4_the_set_is_tagged_adds_up_and_its_share_is_the_stake_over_all_stake():
    s = config.NEAR_FOUNDATION_SET
    for name, (tag, n, liquid, staked, evidence) in s["groups"].items():
        assert tag in ("PUBLISHED", "INFERRED") and n >= 1 and liquid >= 0 and staked >= 0 and evidence.strip(), name
    assert sum(g[3] for g in s["groups"].values()) == pytest.approx(s["totals"]["staked"], abs=0.01)
    assert s["totals"]["staked"] / s["totals"]["validators_stake"] == pytest.approx(s["totals"]["share_of_stake"],
                                                                                   abs=1e-7)
    # no PUBLISHED wallet stakes: every staked NEAR of the set is in a wallet tagged INFERRED, and the cell says so
    assert all(g[3] == 0 for g in s["groups"].values() if g[0] == "PUBLISHED")
    assert "INFERRED" in config.INSIDER_LOCK["Near"][2] and "INFERRED" in config.METRICS[
        "near_foundation_staked_tokens"]["label"]
    pairs = config.NEAR_FOUNDATION_STAKE_PAIRS
    assert len(set(pairs)) == len(pairs) == 20
    for owner, who, pool in pairs:
        assert owner.startswith(("nfendowment", "nf-payments")) and ".pool" in pool
        assert who == owner or who.endswith(".lockup.near")
        if who != owner:                                                      # a lockup's address is derived from its owner
            import hashlib
            assert who == hashlib.sha256(owner.encode()).hexdigest()[:40] + ".lockup.near"
    # a wallet left out for thin evidence is in no pair
    assert not {o for o, _w, _p in pairs} & {"nf-finance.near", "near"}


def _pool_node(answers):
    from fetch.near import NearNode
    n = NearNode()
    calls = []

    def call(api, body=None):
        prm = body["params"]
        who = json.loads(base64.b64decode(prm["args_base64"]))["account_id"]
        calls.append((who, prm["account_id"], prm["method_name"]))
        a = answers.get((who, prm["account_id"]))
        if a is None:
            return None, "no answer"
        return {"result": {"result": list(json.dumps(a).encode())}}, "fake"
    n._call = call
    return n, calls


def test_4_the_stake_read_sums_staked_balance_over_every_pair_or_stores_nothing():
    from fetch.base import FetchOutput
    read = NEAR["node_api"]["pool_stake"]
    assert read["metric"] == "near_foundation_staked_tokens" and read["pairs"] is config.NEAR_FOUNDATION_STAKE_PAIRS
    pairs = read["pairs"]
    ans = {(who, pool): {"account_id": who, "staked_balance": str(1_000 * 10 ** 24), "unstaked_balance": "0",
                         "can_withdraw": True} for _o, who, pool in pairs}
    # NEAR left UNSTAKED in a pool is not staked: not counted
    ans[(pairs[1][1], pairs[1][2])] = {"staked_balance": "0", "unstaked_balance": str(30_000 * 10 ** 24)}
    node, calls = _pool_node(ans)
    out = FetchOutput()
    node._pool_stake(NEAR, NEAR["node_api"], read, 1.3e9, out, pd.Timestamp("2026-11-19"))
    f = out.frame()
    assert list(f["metric"]) == ["near_foundation_staked_tokens"] and f["value"].iloc[0] == pytest.approx(19_000.0)
    assert len(calls) == len(pairs) and {c[2] for c in calls} == {"get_account"}
    assert f["source"].iloc[0] == "near_rpc:pool_stake"
    # one pair without an answer: nothing stored (a sum missing a pair is a wrong figure)
    node, _ = _pool_node({k: v for k, v in ans.items() if k != (pairs[5][1], pairs[5][2])})
    out = FetchOutput()
    node._pool_stake(NEAR, NEAR["node_api"], read, 1.3e9, out, pd.Timestamp("2026-11-19"))
    assert out.frame().empty and out.log[-1].status != "ok"
    # a figure above the total supply fails its bound
    node, _ = _pool_node(ans)
    out = FetchOutput()
    node._pool_stake(NEAR, NEAR["node_api"], read, 10_000.0, out, pd.Timestamp("2026-11-19"))
    assert out.frame().empty


def test_4_the_insider_share_cell_is_the_ratio_and_leakage_i_keeps_the_burn_retired():
    assert config.INSIDER_LOCK["Near"][:2] == ("near_foundation_staked_tokens", "locked_tokens")
    assert "Near" not in config.INSIDER_SHARE_ROUTES            # was MISSING E "no NEAR Foundation account list on file"
    cols = bw._cp_cols(bw.Refs(10, 10, ["2026-09"]), {})
    insider = next(f for h, f, *_ in cols if h.startswith("Insider share of staked"))
    cell = insider(9, NEAR)
    assert cell.startswith("=") and '"|near_foundation_staked_tokens"' in cell and '"|locked_tokens"' in cell
    leak = next(f for h, f, *_ in cols if h.startswith("Leakage (i)"))(9, NEAR)
    # nothing distributed (credited − the held buyback − the burn <= 0) is tested BEFORE the share is read: a store
    # without the new series still reads 0 where nothing was distributed, as before the share was wired
    burn = '"|' + config.HOLDER_VALUE["Near"]["burn_credited"]["usd_metric"] + '"'
    assert burn in leak and leak.index("<=0,0,") < leak.index('"|near_foundation_staked_tokens"')
    # another project's cells are built as before: Sky's insider share and Hyperliquid's are their own series
    sky = insider(9, config.PROJECT_BY_NAME["Sky"])
    assert '"|sky_endowment_locked_tokens"' in sky and "near_foundation" not in sky


# ============================================================================================ 5 the payout ledger
H1, H2, H3, H4, HT = ("1" * 43 + "A", "2" * 43 + "B", "3" * 43 + "C", "4" * 43 + "D", "5" * 43 + "E")
PAYER, OTHER = "nf-payments8.near", "someone-else.near"
THREAD = f"""
**Date of Issuance:** October 5, 2026
| Operator | Amount | Hash |
| Near One | 4,600.00 | [`a…b`](https://nearblocks.io/txns/{H1}) |
| Aurora | 4,600.00 | {H2} |
| Testnet op | 100.00 | [x](https://testnet.nearblocks.io/txns/{HT}) |
**Date of Issuance:** November 5, 2026
| Stakin | 2,146.67 | `{H3}` |
| Near One | 4,600.00 | [again](https://nearblocks.io/txns/{H1}) |
"""


def _ledger_node(monkeypatch, tmp_path, text=THREAD, txs=None, today="2026-11-19"):
    import fetch.near as fn
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    monkeypatch.setattr(fn, "today", lambda: pd.Timestamp(today))
    yocto = 10 ** 24
    txs = txs if txs is not None else {
        H1: (PAYER, "2026-09-15", [("n1-treasury.sputnik-dao.near", 4_600)]),
        # routed through the payment forwarder: the NEAR lands on the payee in a later receipt
        H2: (PAYER, "2026-09-16", [("requestnetwork.near", 4_600), ("aurorafinance.sputnik-dao.near", 4_600)]),
        H3: ("nf-payments2.near", "2026-10-21", [("stakin-payee.near", 2_146.67)])}
    posts = []

    class Http:
        def get_text(self, url, params=None, **_):
            return text if (params or {}).get("page") == 1 else ""

        def post(self, url, json_body=None, **_):
            posts.append(json_body["method"])
            prm = json_body["params"]
            if json_body["method"] == "block":
                day = prm["block_id"]
                return {"result": {"header": {"timestamp": int(pd.Timestamp(day + " 12:00").value)}}}
            h = prm["tx_hash"]
            if h not in txs:
                return {"error": {"name": "UNKNOWN_TRANSACTION"}}
            signer, day, moves = txs[h]
            return {"result": {"status": {"SuccessValue": ""}, "transaction": {"signer_id": signer, "hash": h},
                               "transaction_outcome": {"block_hash": day},
                               "receipts": [{"receiver_id": to, "receipt": {"Action": {"actions": [
                                   {"Transfer": {"deposit": str(int(v * yocto))}}]}}} for to, v in moves]}}
    n = fn.NearNode()
    n.http = Http()
    return n, posts


def test_5_the_ledgers_mainnet_hashes_are_read_once_each():
    from fetch.near import NearNode
    assert NearNode.ledger_hashes(THREAD) == [H1, H2, H3]            # link or bare cell; the testnet link is left out


def test_5_each_published_payout_is_read_on_chain_and_days_without_one_are_zero_inside_the_ledger(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    spec = dict(NEAR["node_api"]["payout_ledger"], **{"from": "2026-09-10"})
    assert spec["metric"] == "near_mpc_operator_payouts_tokens" and PAYER in spec["payers"]
    node, posts = _ledger_node(monkeypatch, tmp_path)
    out = FetchOutput()
    node._payout_ledger(NEAR, spec, out)
    f = out.frame()
    s = f.assign(date=pd.to_datetime(f["date"])).set_index("date")["value"]
    assert s.sum() == pytest.approx(4_600 + 4_600 + 2_146.67)
    assert s[pd.Timestamp("2026-09-16")] == pytest.approx(4_600)     # the forwarded payment, once (not 9,200)
    assert s[pd.Timestamp("2026-09-20")] == 0.0                      # inside the ledger's span: a measured zero
    # from the declared start to the last payout report's date — no row after it (unknown until the next report)
    assert s.index.min() == pd.Timestamp("2026-09-10") and s.index.max() == pd.Timestamp("2026-11-05")
    assert len(s) == len(pd.date_range("2026-09-10", "2026-11-05"))
    assert set(f["source"]) == {"near_rpc:payout_ledger"}
    # a resolved transaction is cached: the next run asks the chain for none of them again
    node2, posts2 = _ledger_node(monkeypatch, tmp_path)
    out2 = FetchOutput()
    node2._payout_ledger(NEAR, spec, out2)
    assert not posts2 and out2.frame()["value"].sum() == pytest.approx(s.sum())


def test_5_a_hash_not_signed_by_a_declared_payer_is_named_and_not_counted(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    spec = dict(NEAR["node_api"]["payout_ledger"], **{"from": "2026-09-10"})
    txs = {H1: (PAYER, "2026-09-15", [("n1-treasury.sputnik-dao.near", 4_600)]),
           H2: (OTHER, "2026-09-16", [("aurorafinance.sputnik-dao.near", 4_600)]),
           H3: ("nf-payments2.near", "2026-10-21", [("stakin-payee.near", 2_146.67)])}
    node, _ = _ledger_node(monkeypatch, tmp_path, txs=txs)
    out = FetchOutput()
    node._payout_ledger(NEAR, spec, out)
    assert out.frame()["value"].sum() == pytest.approx(4_600 + 2_146.67)
    assert OTHER in out.log[-1].message and "NOT COUNTED" in out.log[-1].message


def test_5_a_ledger_with_an_unread_hash_stores_nothing(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    spec = dict(NEAR["node_api"]["payout_ledger"], **{"from": "2026-09-10"})
    txs = {H1: (PAYER, "2026-09-15", [("n1-treasury.sputnik-dao.near", 4_600)])}       # H2, H3: the node has no answer
    node, _ = _ledger_node(monkeypatch, tmp_path, txs=txs)
    out = FetchOutput()
    node._payout_ledger(NEAR, spec, out)
    assert out.frame().empty and out.log[-1].status != "ok" and "2 of the ledger's 3" in out.log[-1].message
    # a thread without a report date or without a hash stores nothing either
    node, _ = _ledger_node(monkeypatch, tmp_path, text="no ledger here")
    out = FetchOutput()
    node._payout_ledger(NEAR, spec, out)
    assert out.frame().empty and out.log[-1].status != "ok"


def _b_groups(ledger_to="2026-11-05"):
    d = pd.date_range("2026-07-01", "2026-11-16")
    led = pd.date_range("2026-04-23", ledger_to)
    pay = [4_600.0 if x.day == 15 else 0.0 for x in led]
    return {("Near", "price_usd"): _g("price_usd", d, 2.0, "coingecko"),
            ("Near", "near_mpc_operator_payouts_tokens"): _g("near_mpc_operator_payouts_tokens", led, pay,
                                                            "near_rpc:payout_ledger"),
            ("Near", "revenue_total_usd"): _g("revenue_total_usd", d, 1_000.0, "derived:revenue_total"),
            ("Near", "fees_usd"): _g("fees_usd", d, 50.0, "defillama")}


def test_5_subsidy_cover_is_revenue_over_b_on_the_days_both_hold(scope):
    assert config.outflow_metrics("Near", "b") == ("near_mpc_operator_payouts_tokens",)     # was: no (b)
    assert ("Near", "Incentives paid beside: supply side (b)") not in config.FLOW_LINE_EVIDENCE
    assert bw._cp_revenue_metric(NEAR) == "revenue_total_usd"
    # every other project keeps the generic basis
    assert bw._cp_revenue_metric(config.PROJECT_BY_NAME["Ethereum"]) == "fees_usd"
    assert bw._cp_revenue_metric(config.PROJECT_BY_NAME["Aethir"]) == "customer_revenue_usd"
    g = _b_groups()
    bw._outflow_views(g)
    asof = pd.Timestamp("2026-11-17")
    bw._subsidy_common_views(g, asof)
    info = bw.SUBSIDY_COMMON["Near"]
    # Q0 is 2026-08-20..2026-11-17; the ledger stops at its last report (11-05): the days after it are in NEITHER side
    assert (info["first"], info["last"], info["days"]) == ("2026-08-20", "2026-11-05", 78)
    num, den = (float(g[("Near", m)]["value"].sum()) for m in ("subsidy_cover_num_usd", "subsidy_cover_den_usd"))
    assert num == 78 * 1_000.0 and den == 2 * 4_600.0 * 2.0                   # the payouts of 09-15 and 10-15 x price
    assert num / den == pytest.approx(4.239, abs=0.001)
    # fewer common days than a per-day ratio needs: no series (the cell says NEEDS HISTORY)
    g = _b_groups(ledger_to="2026-08-23")
    bw._outflow_views(g)
    bw._subsidy_common_views(g, asof)
    assert bw.SUBSIDY_COMMON["Near"]["days"] == 4 and ("Near", "subsidy_cover_num_usd") not in g


def test_5_b_is_not_summed_into_dilution_while_it_is_published_with_a_lag(scope):
    # R12.1 (Jake 2026-11-19): the operator payouts ARE dilution, counted as published (tests/test_r12_near.py).
    # Was: ("Near", "b") in config.DILUTION_LEAVES_OUT, _cp_cats(NEAR) == ["a", "e"] and
    # set(config.DILUTION_LEAVES_OUT) == {("Near", "b")}
    assert ("Near", "b") not in config.DILUTION_LEAVES_OUT and ("Near", "b") in config.OUTFLOW_INCOMPLETE
    assert bw._cp_cats(NEAR) == ["a", "b", "e"]
    assert bw._cp_cats(NEAR, ("b", "c")) == ["b"]                             # its own cells still see it
    assert set(config.DILUTION_LEAVES_OUT) == set()
    assert "b" in bw._cp_cats(config.PROJECT_BY_NAME["Aethir"])
    # the ledger is monthly: stale by that cadence, the daily series beside it by the default
    assert config.stale_after_days("Near", "outflow_b_usd", 7) == 64
    assert config.stale_after_days("Near", "outflow_a_usd", 7) == 7
    assert config.stale_after_days("Aethir", "outflow_b_usd", 7) == 7


def test_5_the_house_of_stake_wallets_are_recorded_and_are_not_the_payers():
    h = config.NEAR_HOUSE_OF_STAKE
    assert set(h["payers_of_hsp_007"]) == set(NEAR["node_api"]["payout_ledger"]["payers"])
    assert not set(h["wallets"]) & set(h["payers_of_hsp_007"])
    assert sum(h["payers_of_hsp_007"].values()) == pytest.approx(h["ledger"]["near"], abs=0.01)
    # R12.5 (Jake 2026-11-19): approved on his dry run of 289,127,592 bytes, under the 1 GB condition
    # (was: ["approved"] is False — "Jake pastes the dry run back")
    assert NEAR["near_bigquery"]["supply_flows"]["history"]["approved"] is True


def test_5_the_cover_cell_reads_the_common_day_views_and_a_store_without_the_series_says_so(scope, monkeypatch):
    cols = bw._cp_cols(bw.Refs(10, 10, ["2026-09"]), {})
    cover = next(f for h, f, *_ in cols if h == bw.CP_SUBSIDY_HEAD)
    leak = next(f for h, f, *_ in cols if h.startswith("LEAKAGE (ii)"))
    monkeypatch.setattr(bw, "SUBSIDY_COMMON", {})                       # the series is not in the store (an old export)
    text = cover(9, NEAR)
    assert not text.startswith("=") and "near_mpc_operator_payouts_tokens" in text and "no rows stored" in text
    monkeypatch.setattr(bw, "SUBSIDY_COMMON", {"Near": {"from": "2026-04-23", "days": 78, "first": "2026-08-20",
                                                        "last": "2026-11-05"}})
    cell = cover(9, NEAR)
    assert cell.startswith("=") and '"|subsidy_cover_num_usd"' in cell and '"|subsidy_cover_den_usd"' in cell
    assert "MAX(0,1-" in leak(9, NEAR)                                  # leakage (ii) = max(0, 1 − that cover)
    assert "2026-08-20..2026-11-05 (78 common days" in bw._subsidy_dates_fmt("Near")
    # too few common days: NEEDS HISTORY with the date, as for any project on this mechanism
    monkeypatch.setattr(bw, "SUBSIDY_COMMON", {"Near": {"from": "2026-04-23", "days": 3, "first": None, "last": None}})
    assert "NEEDS HISTORY" in cover(9, NEAR)
