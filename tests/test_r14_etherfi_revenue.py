"""R14 (Jake 2026-11-19) — Ether.fi: the buyback read, the three revenue lines, the top-ups in dilution.

No network: synthetic logs through the real log scan, synthetic series through the real build views, and the config
records the round rests on. SPEC.md "Round 14 — Ether.fi"."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

EF = "Ether.fi"
E = 10 ** 18


def _frame(metric, vals, source="explorer:test"):
    return pd.DataFrame({"date": pd.to_datetime(list(vals)), "project": EF, "metric": metric,
                         "value": [float(v) for v in vals.values()], "tier": 2, "source": source})


# --------------------------------------------------------------------------------------- R14.2a an LP withdrawal
def test_an_lp_withdrawal_is_not_a_purchase(monkeypatch):
    """A fill from a declared venue no longer counts by itself: the holder must pay ANOTHER token out in the same
    transaction. The Uniswap v4 PoolManager inflow with nothing paid out (the 2026-09-02 Arrakis withdrawals) is not
    bought; the same venue with WETH paid out is; a CoW fill with USDC paid out is."""
    from fetch.explorer import TRANSFER_TOPIC, pad_address
    from test_adapters import _elog, _run_scan
    spec = dict(next(s for s in config.PROJECT_BY_NAME[EF]["log_scans"] if s["key"] == "buyback_wallet_inflow"))
    assert spec["purchase_requires_payment"] is True
    safe = "0x3fb6784e263643656f386a0371644931133d7b78"
    cow, v4 = "0x9008d19f58aabd9ed0d60971565aa8510560ab41", "0x000000000004444c5dc75cb358380d2e3de08a90"
    spec = dict(spec, holders=[safe], token="0xtoken")
    t = int(pd.Timestamp("2026-09-02").timestamp())
    hs = pad_address(safe)
    weth = _elog(210, 1, [TRANSFER_TOPIC, hs, pad_address(v4)], 1 * E, ts=t, tx="0xswapv4")
    weth["address"] = "0xweth"
    usdc = _elog(220, 1, [TRANSFER_TOPIC, hs, pad_address(cow)], 10 ** 6, ts=t + 86_400, tx="0xcow")
    usdc["address"] = "0xusdc"
    logs = {("0xtoken", None, hs): [_elog(200, 0, [TRANSFER_TOPIC, pad_address(v4), hs], 7 * E, ts=t, tx="0xlpw"),
                                    _elog(210, 0, [TRANSFER_TOPIC, pad_address(v4), hs], 2 * E, ts=t, tx="0xswapv4"),
                                    _elog(220, 0, [TRANSFER_TOPIC, pad_address(cow), hs], 3 * E, ts=t + 86_400,
                                          tx="0xcow")],
            ("0xtoken", hs, None): [], ("", hs, None): [weth, usdc]}
    out = _run_scan(monkeypatch, spec, logs, {safe: 12 * E}, name=EF)
    f = out.frame()
    got = f[f.metric == "actual_buyback_tokens"].set_index("date")["value"]
    assert got[pd.Timestamp("2026-09-02")] == 2.0          # the LP withdrawal (7) is out; the paid v4 swap (2) is in
    assert got[pd.Timestamp("2026-09-03")] == 3.0          # CoW, USDC paid
    assert got.sum() == 5.0
    line = next(e.message for e in out.log if "RECONCILED" in e.message)
    assert "a fill from a venue alone is not a purchase" in line


def test_the_four_withdrawals_are_evidence_not_the_rule():
    """The rule takes them out; the hashes are the evidence: four transactions, 1,047,757.47 ETHFI, the three of the
    dumps' Q0 (2026-09-02) summing to the 493,031.75 the repo read as bought."""
    w = config.ETHERFI_LP_WITHDRAWALS_2026_09_02
    assert len(w) == 4 and sum(w.values()) == pytest.approx(1_047_757.47, abs=0.01)
    q0 = sum(v for k, v in w.items() if not k.startswith("0x3b52e19b"))
    assert q0 == pytest.approx(config.ETHERFI_PURCHASE_RULE_READ["q0_was"]["tokens"], abs=0.01)
    r = config.ETHERFI_PURCHASE_RULE_READ
    assert r["counted_old_rule"]["0x3fb6784e…"] - r["counted_new_rule"]["0x3fb6784e…"] == pytest.approx(
        r["removed"], abs=0.01)
    for k in ("0x2f5301a3…", "0x43f4ca92…", "0xa7b1d3a0…"):     # the CoW buyers: unchanged to the token
        assert r["counted_old_rule"][k] == r["counted_new_rule"][k]
    prog = next(s for s in config.PROJECT_BY_NAME[EF]["log_scans"] if s["key"] == "programmatic_buyback_inflow")
    assert prog["purchase_requires_payment"] is True


# --------------------------------------------------------------------------------------- R14.2b destinations measured
def test_destinations_are_measured_per_purchase_day():
    """Was "100% distributed". Each purchase day reads its measured shares; a day whose purchase went to wallets not
    identified has none (None — left out of the split, never guessed); from 2026-09-03 the programme's purchases are
    distributed to card users."""
    bds = config.buyback_destination_shares
    assert bds(EF, "2025-08-15") == {**bds(EF, "2025-08-15"), "burn": 1.0, "hold": 0.0, "distribute": 0.0}
    assert bds(EF, "2025-11-15")["hold"] == 1.0
    assert bds(EF, "2025-08-04") is None and bds(EF, "2026-01-30") is None
    assert bds(EF, "2024-07-07") is None and bds(EF, "2024-07-08")["distribute"] == 0.0     # no purchase: 0 shares
    assert bds(EF, "2026-10-01")["from"] == "2026-02-09"         # the programme is carved out before the shares
    for d, sh in config.ETHERFI_BUYBACK_DESTINATIONS:
        if isinstance(sh, tuple):
            assert sum(sh) == pytest.approx(1.0, abs=1e-4) and min(sh) >= 0, d
    # the measured mix of the old Safe's purchases (FIFO, Blockscout 2026-11-19): 47.0% / 44.8% / 8.1% of 17,984,520.10
    assert 8_460_420.10 + 8_060_100.00 + 1_464_000.00 == pytest.approx(17_984_520.10, abs=0.01)
    assert config.BUYBACK_DESTINATION_SHARES_WAS[EF].startswith("100% distributed")


def test_the_split_view_leaves_an_unestablished_day_out(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[EF]])
    groups = {(EF, "actual_buyback_tokens"): _frame("actual_buyback_tokens",
                                                    {"2025-08-04": 100.0, "2025-08-15": 10.0, "2025-11-15": 4.0})}
    bw._buyback_split_views(groups)
    burned = bw._daily(groups[(EF, "buyback_burned_tokens")])
    held = bw._daily(groups[(EF, "buyback_held_tokens")])
    assert pd.Timestamp("2025-08-04") not in burned.index                     # not established: no share, no row
    assert burned[pd.Timestamp("2025-08-15")] == 10.0 and held[pd.Timestamp("2025-11-15")] == 4.0
    assert bw._daily(groups[(EF, "retired_buyback_tokens")]).sum() == 14.0


# --------------------------------------------------------------------------------------- R14.2c the programme
def _leg_groups(prog_days):
    return {(EF, "actual_buyback_tokens"): _frame("actual_buyback_tokens",
                                                  {"2026-09-01": 0.0, "2026-09-02": 0.0, "2026-09-03": 0.0,
                                                   "2026-09-04": 0.0, "2026-09-05": 0.0},
                                                  "explorer:buyback_wallet_inflow"),
            (EF, "actual_buyback_usd"): _frame("actual_buyback_usd",
                                               {"2026-09-01": 0.0, "2026-09-02": 0.0, "2026-09-03": 0.0,
                                                "2026-09-04": 0.0, "2026-09-05": 0.0}, "derived:tokens*price"),
            (EF, "programmatic_buyback_tokens"): _frame("programmatic_buyback_tokens", prog_days),
            (EF, "price_usd"): _frame("price_usd", {d: 0.5 for d in ("2026-09-01", "2026-09-02", "2026-09-03",
                                                                     "2026-09-04", "2026-09-05")})}


def test_the_programme_is_a_buyback_paid_to_customers_never_holder_value(monkeypatch):
    """From 2026-09-03 actual buyback = the wallet scan + the programme's purchases, on the days BOTH hold a row (a
    missing day is a gap, never 0); the programme's part is DISTRIBUTED to card users (its own view); holder value
    reads the wallet scan alone, so it credits nothing of it."""
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[EF]])
    # the programme's first row is 09-04 (09-03 before it: its measured 0); no row on 09-05 (a gap)
    g = _leg_groups({"2026-09-04": 100.0, "2026-09-06": 40.0})
    g[(EF, "actual_buyback_tokens")] = pd.concat([g[(EF, "actual_buyback_tokens")],
                                                  _frame("actual_buyback_tokens", {"2026-09-06": 0.0},
                                                         "explorer:buyback_wallet_inflow")])
    g[(EF, "actual_buyback_usd")] = pd.concat([g[(EF, "actual_buyback_usd")],
                                               _frame("actual_buyback_usd", {"2026-09-06": 0.0}, "derived:tokens*price")])
    g[(EF, "price_usd")] = pd.concat([g[(EF, "price_usd")], _frame("price_usd", {"2026-09-06": 0.5})])
    bw._buyback_leg_views(g)
    tok = bw._daily(g[(EF, "actual_buyback_tokens")])
    assert list(tok.index.strftime("%Y-%m-%d")) == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
                                                    "2026-09-06"]
    assert tok[pd.Timestamp("2026-09-03")] == 0.0 and tok[pd.Timestamp("2026-09-04")] == 100.0
    assert bw._daily(g[(EF, "actual_buyback_usd")])[pd.Timestamp("2026-09-04")] == 50.0
    assert not g[(EF, "actual_buyback_usd")]["is_manual"].astype(bool).any()        # never read as "manual"
    assert bw._daily(g[(EF, "buyback_to_customers_tokens")]).sum() == 140.0
    assert bw.BUYBACK_LEG_STATE[EF]["left_out"] == 1 and bw.BUYBACK_LEG_STATE[EF]["first_left_out"] == "2026-09-05"
    src = g[(EF, "actual_buyback_tokens")]
    assert set(src[src["date"] >= "2026-09-03"]["source"].str.split("[").str[0]) == {
        "derived:buyback_wallet_plus_programme"}
    bw._buyback_split_views(g)
    split = {m: bw._daily(g[(EF, m)]).sum() for m in ("buyback_burned_tokens", "buyback_held_tokens",
                                                      "buyback_distributed_tokens", "retired_buyback_tokens")}
    # the card cashback is carved out before the shares: DISTRIBUTED (to stakers) reads 0, not 140
    assert split == {"buyback_burned_tokens": 0.0, "buyback_held_tokens": 0.0, "buyback_distributed_tokens": 0.0,
                     "retired_buyback_tokens": 0.0}
    bw._holder_value_views(g)
    hv = bw._daily(g[(EF, "holder_value_revenue_usd")])
    assert hv.sum() == 0.0 and pd.Timestamp("2026-09-05") in hv.index      # holder value is not the leg's gap
    ho = config.declared_handover(EF, "actual_buyback_tokens")
    assert ho["ordered_points"] == ("explorer:buyback_wallet_inflow", "derived:buyback_wallet_plus_programme")


# --------------------------------------------------------------------------------------- R14.3 the three lines
def test_fees_are_defillama_fees_less_the_cash_cashback(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[EF]])
    g = {(EF, "fees_usd"): _frame("fees_usd", {"2026-09-01": 400.0, "2026-09-02": 500.0, "2026-09-03": 450.0}),
         (EF, "cash_cashback_usd_defillama"): _frame("cash_cashback_usd_defillama",
                                                     {"2026-09-01": 60.0, "2026-09-02": 55.0})}
    bw._revenue_total_views(g)
    net = bw._daily(g[(EF, "fees_net_cashback_usd")])
    assert net.to_dict() == {pd.Timestamp("2026-09-01"): 340.0, pd.Timestamp("2026-09-02"): 445.0}  # 09-03: a gap
    assert g[(EF, "fees_net_cashback_usd")]["source"].iloc[0] == \
        "derived:fees_net_cashback[fees_usd-cash_cashback_usd_defillama]"
    # the window read 2026-11-19 (DefiLlama): 41,442,301 − 5,414,890.47 = 36,027,410.53
    assert 41_442_301 - 5_414_890.47 == pytest.approx(36_027_411, abs=1)


def test_the_three_lines_are_wired_with_their_labels():
    s = config.revenue_split(EF)
    assert (s["fees"]["metric"], s["revenue"]["metric"], s["holder_revenue"]["metric"]) == (
        "fees_net_cashback_usd", "revenue_usd", "holder_value_revenue_usd")
    assert "ESTIMATE" in s["revenue"]["route"] and "1.38%" in s["revenue"]["route"]
    assert "USDC" in s["revenue"]["route"]                                    # the cross-check beside it
    assert "RECORDED, NOT CREDITED" in s["holder_revenue"]["route"] and "$0" in s["holder_revenue"]["route"]
    assert s["holder_revenue"]["primary"] is True and s["fees"]["primary"] is False
    assert config.S13_SAME_DAYS[EF] == ("fees", "revenue", "holder_revenue")
    suffix, note = config.revenue_tier_label(EF, "REVENUE Q0 ($) — S13")
    assert suffix == " · revenue" and "ESTIMATE" in note
    slugs = {(c["slug"], c["data_type"]): c["metric"] for c in config.PROJECT_BY_NAME[EF]["defillama_component_slugs"]}
    assert slugs[("etherfi-cash-liquid", "dailySupplySideRevenue")] == "cash_cashback_usd_defillama"
    assert slugs[("etherfi-cash-liquid", "dailyRevenue")] == "cash_revenue_usd_defillama"
    comp = config.S13_COMPONENTS[EF]
    assert ("s13x_ef_holder_topups_recorded_usd", "s13c_ef_holder_usd", "sethfi_issuer_topup_tokens", True) in \
        comp["crosschecks"]
    for m in ("fees_net_cashback_usd_s13d", "revenue_usd_s13d", "holder_value_revenue_usd_s13d"):
        assert EF in config.METRICS[m]["only_projects"]


def test_holder_revenue_is_zero_and_the_top_ups_are_recorded_not_credited(monkeypatch):
    """Decision 5, strict: no revenue-funded value reaches holders — every wallet buyback on record is
    treasury-funded, and the top-ups are paid from the Foundation's stock. Holder share = 0 ÷ revenue."""
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[EF]])
    g = {(EF, "actual_buyback_usd"): _frame("actual_buyback_usd", {"2026-03-20": 1000.0, "2026-09-14": 0.0}),
         (EF, "price_usd"): _frame("price_usd", {"2026-03-20": 0.6, "2026-09-14": 0.62})}
    bw._holder_value_views(g)
    assert bw._daily(g[(EF, "holder_value_revenue_usd")]).sum() == 0.0
    assert bw._daily(g[(EF, "holder_value_other_usd")]).sum() == 1000.0       # recorded (treasury-funded)
    t = config.ETHERFI_TOPUPS_READ
    assert sum(t["by_day"].values()) == t["tokens"] == 412_000 and t["usd"] == pytest.approx(260_723.78)
    assert "0%" in config.revenue_split(EF)["holder_share"]["route"]


# --------------------------------------------------------------------------------------- R14.A top-ups in dilution
def test_the_top_ups_are_read_as_their_own_series_into_outflow_e():
    """The Foundation's top-ups to stakers are emissions-funded yield: outflow (e) — a treasury release, as Sky's
    reserve-funded rewards. Their own scan: issuer wallets -> the vault, deposits (shares minted) out."""
    assert config.outflow_metrics(EF, "e") == ("unlock_release_tokens",)
    assert config.RELEASE_GROSS_UP[EF] == ("sethfi_issuer_topup_tokens",)
    # R15 (decision 5 (b)): only the named float outflow (f) is netted out of the release — never the top-ups
    # (was: EF not in config.RELEASE_LESS)
    assert "sethfi_issuer_topup_tokens" not in config.RELEASE_LESS.get(EF, ())
    assert "Sky" in config.OUTFLOW_CATEGORIES[EF]["basis"] and "top-ups" in config.OUTFLOW_CATEGORIES[EF]["basis"]
    scan = next(s for s in config.PROJECT_BY_NAME[EF]["log_scans"] if s["key"] == "sethfi_issuer_topups")
    senders = {a.lower() for a in scan["count_from"]}
    assert "0x3fb6784e263643656f386a0371644931133d7b78" in senders                       # the Operating Safe
    assert "0x01e42ad3acd58584ffc1d1982ecbbe758996d601" in senders
    assert "0x2f5301a3d59388c509c65f8698f521377d41fd0f" not in senders                   # bought, not stock
    assert scan["share_mint"]["token"].lower() == "0x86b5780b606940eb59a062aa85a07959518c0161"   # deposits out
    assert config.scan_feeds_headline(EF, scan)


def test_the_release_counts_each_day_at_least_its_top_ups_and_the_flow_still_balances(monkeypatch):
    """The release is the set's NET fall. On a quiet day a top-up out of the Operating Safe is already in it (counted
    once, unchanged); on a day the set ROSE (5M in from Binance while 273,069 was paid, 2026-08-13) the release read 0:
    it now reads the top-up, and the hidden inflow it was netted against goes on the returned line, so released −
    returned (the token-flow statement) is unchanged."""
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[EF]])
    days = ("2026-08-12", "2026-08-13", "2026-09-13", "2026-09-14")
    stock = {"2026-08-12": 10_000_000.0, "2026-08-13": 14_726_931.0, "2026-09-13": 14_726_931.0,
             "2026-09-14": 14_564_931.0}
    g0 = {(EF, "noncirculating_holding_tokens"): _frame("noncirculating_holding_tokens", stock, "chain:sum(a+b)")}
    bw._unlock_release_view(g0, EF, "unlock_release_tokens", ("noncirculating_holding_tokens",))
    g = dict(g0)
    g[(EF, "sethfi_issuer_topup_tokens")] = _frame("sethfi_issuer_topup_tokens",
                                                   dict(zip(days, (0.0, 273_069.0, 0.0, 162_000.0))))
    bw._unlock_release_view(g, EF, "unlock_release_tokens", ("noncirculating_holding_tokens",))
    before, after = (bw._daily(x[(EF, "unlock_release_tokens")]) for x in (g0, g))
    ret0, ret1 = (bw._daily(x[(EF, "issuer_return_tokens")]) for x in (g0, g))
    assert before[pd.Timestamp("2026-08-13")] == 0.0 and after[pd.Timestamp("2026-08-13")] == 273_069.0
    assert after[pd.Timestamp("2026-09-14")] == before[pd.Timestamp("2026-09-14")] == 162_000.0   # counted once
    assert (after - ret1).sum() == pytest.approx((before - ret0).sum())               # the flow statement balances
    assert ret1[pd.Timestamp("2026-08-13")] == ret0[pd.Timestamp("2026-08-13")] + 273_069.0
    assert "release holds sethfi_issuer_topup_tokens" in g[(EF, "unlock_release_tokens")]["source"].iloc[0]


# --------------------------------------------------------------------------------------- R14.5 the declared none
def test_supply_side_incentives_are_a_declared_none():
    text = ("n/a — no supply-side incentive (b) series on file; outflows held: (c) demand-side incentives, (e) unlocks "
            "— see the outflow basis column")
    e = config.declared_na_reason(EF, text)
    assert e is not None and e["source"] and e["read_on"] == "2026-11-19" and "decision 8" in e["decided"]
    assert "b" not in config.OUTFLOW_CATEGORIES[EF]


def test_the_expired_cashback_allowance_is_not_renewed():
    """Total dilution / net holder yield form from data (the cashback rows) or say BLOCKED with the leg named: the
    allowance that covered them ran to 2026-10-27 and accepts nothing after it."""
    entries = [e for e in config.REGRESSION_ALLOWLIST if e["project"] == EF]
    assert all(e["until"] <= "2026-10-27" for e in entries)
    assert config.regression_allowance(EF, "CP NET HOLDER YIELD cashback_unfunded_tokens", on="2026-11-19") is None
