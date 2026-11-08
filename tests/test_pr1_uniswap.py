"""PROJECT ROUND 1, group `uniswap` (Jake 2026-11-05): U3, U4, U5, U8, U9.

U3: Uniswap's 100M treasury burn (2025-12-27/28) counts in the daily series, Q-3, Y1 and every trailing figure, and the
    Monthly tab, Data windows and Flows lines that hold it say "of which one-off" as the rates do.
U4: the market-share headline is the 90-day mean (the Q0 basis), its 30- and 60-day means beside; r44 answered.
U5: total supply = UNI.totalSupply() − the 0x…dEaD balance (TOTAL_SUPPLY_NET, a declared handover); the circulating
    cross-check against CoinGecko is made on CoinGecko's documented basis and the difference is explained on the cell.
U8: the distributor release stays in dilution and the A4 key, its one-off share shown (never "0%" for a small share).
U9: Uniswap's two open Review Queue rows answered with their numbers.
No test touches the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

UNI = config.PROJECT_BY_NAME["Uniswap"]


def _rows(project, metric, days, values, source, tier=2):
    days = pd.to_datetime(list(days))
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": days, "project": project, "metric": metric, "value": [float(v) for v in vals],
                         "source": source, "tier": tier})


# ============================================================================================================ U5
def test_u5_uniswap_total_supply_is_ours_net_of_the_burn_with_a_declared_handover():
    spec = config.TOTAL_SUPPLY_NET["Uniswap"]
    assert spec["total"] == "total_supply_gross" and spec["less"] == ("burn_address_balance",)
    assert spec["read_live"]["block"] == 26_348_366
    assert spec["read_live"]["net"] == pytest.approx(spec["read_live"]["total_supply"]
                                                     - spec["read_live"]["dead_balance"], abs=1e-3)
    assert "Uniswap" in config.TOTAL_SUPPLY_NET_STANDARD["on"]
    assert config.TOTAL_SUPPLY_ONCHAIN_FIRST["Uniswap"]["view"] is True
    h = UNI["series_handover"]["total_supply"]
    assert h["ordered_points"] == ("coingecko", "derived:total_supply_gross-burn_address_balance")
    assert "Uniswap" in config.METRICS["total_supply_coingecko"]["only_projects"]
    # A3's rebuild of CoinGecko's own total now lives where CoinGecko's total is kept (GEODNET's arrangement)
    assert config.supply_rebuild_metrics("Uniswap") == {"total_supply_coingecko": "total"}


def test_u5_the_view_nets_the_dead_balance_and_moves_coingecko_beside_it(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    d = ["2026-10-11", "2026-10-12", "2026-10-13"]
    groups = {("Uniswap", "total_supply_gross"): _rows("Uniswap", "total_supply_gross", d, 1e9, "chain:ethereum:token"),
              ("Uniswap", "burn_address_balance"): _rows("Uniswap", "burn_address_balance", d,
                                                         [112_787_600.0, 112_819_600.0, 112_857_600.0],
                                                         "chain:ethereum:burn_dead"),
              ("Uniswap", "total_supply"): _rows("Uniswap", "total_supply", d[1:], [887_184_418.0, 887_146_418.0],
                                                 "coingecko", tier=1)}
    bw._total_supply_net_views(groups)
    tot = bw._daily(groups[("Uniswap", "total_supply")])
    assert tot.tolist() == [887_212_400.0, 887_180_400.0, 887_142_400.0]
    assert set(groups[("Uniswap", "total_supply")]["source"]) == {"derived:total_supply_gross-burn_address_balance"}
    assert bw._daily(groups[("Uniswap", "total_supply_coingecko")]).tolist() == [887_184_418.0, 887_146_418.0]


def _circ_frame(name, rows, day="2026-11-05"):
    return pd.DataFrame([{"date": pd.Timestamp(day), "project": name, "metric": m, "value": float(v)}
                         for m, v in rows.items()])


# the live reads of 2026-11-05 (block 26,348,366) and CoinGecko's figures at 11:47:55 UTC
LIVE = {"total_supply_gross": 1_000_000_000.0, "burn_address_balance": 113_781_581.213803,
        "treasury_holding_tokens": 262_247_996.305835, "noncirculating_holding_tokens": 219_165.087661,
        "total_supply_coingecko": 886_234_418.786197}


def test_u5_the_coingecko_check_is_made_on_its_documented_basis_never_netting_the_burn_twice():
    from fetch import circulating
    # CoinGecko back on its own basis (its total − the Timelock): no row
    on_basis = LIVE["total_supply_coingecko"] - LIVE["treasury_holding_tokens"]
    out = FetchOutput()
    circulating.check(out, _circ_frame("Uniswap", {**LIVE, "circulating_supply": on_basis}), [UNI])
    assert not [r for r in out.review if r["reason"] == "onchain_vs_coingecko"], \
        "like-for-like is CoinGecko's own total − the Timelock; the 0x…dEaD balance is not taken out a second time"
    # CoinGecko's figure of 2026-11-05: +2.34% beyond the unchanged 2% tolerance — still raised, with the numbers
    out = FetchOutput()
    circulating.check(out, _circ_frame("Uniswap", {**LIVE, "circulating_supply": 609_723_895.879829}), [UNI])
    hit = [r for r in out.review if r["reason"] == "onchain_vs_coingecko"]
    assert hit and "+2.34%" in hit[0]["basis"]
    assert hit[0]["value"] == pytest.approx(on_basis, abs=1)
    assert config.CIRCULATING_TOLERANCE == 0.02


def test_u5_the_basis_note_explains_the_difference_on_the_cells_and_the_credibility_row():
    note = config.coingecko_circ_basis_note("Uniswap")
    assert note.startswith("COINGECKO'S BASIS (PR1.U5")
    for s in ("UNI Timelock", "272,134,858.48", "supply_breakdown", "14,262,526.60", "+2.34%", "26,348,366"):
        assert s in note, s
    assert config.coingecko_circ_basis_note("Sky") == ""
    b = config.COINGECKO_CIRC_BASIS["Uniswap"]["live"]
    assert b["ours"] == pytest.approx(b["net_total"] - b["timelock"] - b["distributor"] - b["vesting"], abs=1e-3)
    assert b["coingecko_total"] - b["coingecko_circulating"] - b["timelock"] == pytest.approx(14_262_526.60, abs=0.01)
    src = Path(bw.__file__).read_text(encoding="utf-8")
    assert src.count('"note_fn": lambda p: config.coingecko_circ_basis_note(p["name"])') == 2   # Master + CP
    import credibility
    spec = credibility.circulating_input("Uniswap")
    assert "COINGECKO'S BASIS" in spec["ref"]["note"]


def test_u5_issuance_still_derives_from_the_contract_with_no_total_supply_row_in_the_run():
    """total_supply is a read-time view now — no fetch writes it — so the gross route runs on its own."""
    from fetch import _derive_issuance
    out = FetchOutput()
    out.frames = [pd.DataFrame([
        {"date": pd.Timestamp("2026-11-05"), "project": "Uniswap", "metric": "total_supply_gross",
         "value": 1_000_000_000.0, "source": "chain:ethereum:token", "tier": 2},
        {"date": pd.Timestamp("2026-11-05"), "project": "Uniswap", "metric": "gross_burn_tokens",
         "value": 8_000.0, "source": "chain:ethereum:burn_dead:delta", "tier": 2},
        {"date": pd.Timestamp("2026-11-05"), "project": "Uniswap", "metric": "total_supply_coingecko",
         "value": 886_234_418.79, "source": "coingecko", "tier": 1}])]
    _derive_issuance(out, [UNI], {("Uniswap", "total_supply_gross"): 1_000_000_000.0},
                     {("Uniswap", "total_supply_gross"): "2026-11-04"})
    df = out.frame()
    got = df[df.metric == "gross_issuance_tokens"]
    assert len(got) == 1 and float(got.value.iloc[0]) == 0.0
    assert got.source.iloc[0] == "derived:d_supply_gross"
    # the same day read twice is not a delta
    out = FetchOutput()
    out.frames = [pd.DataFrame([{"date": pd.Timestamp("2026-11-05"), "project": "Uniswap",
                                 "metric": "total_supply_gross", "value": 1e9, "source": "chain", "tier": 2},
                                {"date": pd.Timestamp("2026-11-05"), "project": "Uniswap",
                                 "metric": "gross_burn_tokens", "value": 1.0, "source": "chain", "tier": 2}])]
    _derive_issuance(out, [UNI], {("Uniswap", "total_supply_gross"): 1e9}, {("Uniswap", "total_supply_gross"): "2026-11-05"})
    assert out.frame()[lambda f: f.metric == "gross_issuance_tokens"].empty
    assert any("no earlier-dated reading" in g["reason"] for g in out.gaps)


def test_u5_dd_moves_coingeckos_stored_totals_and_is_a_runnable_section():
    import run_sql
    secs = run_sql.parse_sections((Path(bw.__file__).parent / "orphan_cleanup.sql").read_text(encoding="utf-8"))
    dd = secs["DD"]["text"]
    assert "PR1.U5" in secs["DD"]["title"]
    assert "-- UPDATE OR IGNORE metrics\n--    SET metric = 'total_supply_coingecko'" in dd
    assert "project = 'Uniswap'" in dd


# ============================================================================================================ U4
def test_u4_the_30_and_60_day_means_are_the_daily_shares_held_in_the_window(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    days = pd.date_range("2026-08-01", "2026-11-02")
    vals = [0.30] * (len(days) - 1) + [0.2536]
    groups = {("Uniswap", "market_share_pct"): _rows("Uniswap", "market_share_pct", days, vals, "defillama", 1),
              ("Uniswap", "market_share_category_usd"): _rows("Uniswap", "market_share_category_usd", days,
                                                              [4.1e9] * (len(days) - 1) + [7_693_669_883.0],
                                                              "defillama", 1)}
    bw._market_share_mean_views(groups)
    m30 = bw._daily(groups[("Uniswap", "market_share_mean30_pct")])
    m60 = bw._daily(groups[("Uniswap", "market_share_mean60_pct")])
    assert m30.iloc[-1] == pytest.approx((29 * 0.30 + 0.2536) / 30)
    assert m60.iloc[-1] == pytest.approx((59 * 0.30 + 0.2536) / 60)
    assert m30.index.max() == pd.Timestamp("2026-11-02")
    last, share, cat, means = bw.MARKET_SHARE_MEAN_NOTE["Uniswap"]
    assert (last, share, cat) == (pd.Timestamp("2026-11-02"), 0.2536, 7_693_669_883.0) and set(means) == {30, 60}
    suffix, note = bw._market_share_mean_flag(UNI)
    assert suffix == " 90d mean" and "Q0 basis" in note and "25.36%" in note and "+87.0%" in note
    assert bw._market_share_mean_flag(config.PROJECT_BY_NAME["Aerodrome"]) is None


def test_u4_a_hole_is_never_a_zero_in_the_mean(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    days = ["2026-10-01", "2026-10-03"]                       # 10-02 not stored
    groups = {("Uniswap", "market_share_pct"): _rows("Uniswap", "market_share_pct", days, [0.2, 0.4], "x", 1)}
    bw._market_share_mean_views(groups)
    assert bw._daily(groups[("Uniswap", "market_share_mean30_pct")]).iloc[-1] == pytest.approx(0.3)


def test_u4_the_headline_reads_the_90_day_mean_for_uniswap_only_and_the_means_sit_beside():
    assert config.market_share_window("Uniswap") == "q0"
    assert config.market_share_window("Aerodrome") == "now"
    R = bw.Refs(50, 5, ["2026-10"])
    cols = {c[0]: c for c in bw._cp_cols(R, {})}
    head = cols[bw.MARKET_SHARE_HEAD]
    f_uni = head[1](7, UNI)
    f_aero = head[1](8, config.PROJECT_BY_NAME["Aerodrome"])
    assert f'Data!${bw.DC["q0"]}$' in f_uni and "market_share_pct" in f_uni
    assert f'Data!${bw.DC["now"]}$' in f_aero
    assert head[5]["flag_fn"] is bw._market_share_mean_flag
    names = list(cols)
    i = names.index(bw.MARKET_SHARE_HEAD)
    assert names[i + 1].startswith("Market share, 30-day mean") and names[i + 2].startswith("Market share, 60-day mean")
    assert "market_share_mean30_pct" in cols[names[i + 1]][1](7, UNI)
    assert cols[names[i + 1]][1](8, config.PROJECT_BY_NAME["Aerodrome"]) == ""


# ============================================================================================================ U9
def test_u9_the_two_open_rows_are_answered_with_their_numbers():
    r44 = {"project": "Uniswap", "metric": "market_share_category_usd", "reason": "change_threshold",
           "date": "2026-11-02"}
    a = config.review_answered(r44, on="2026-11-05")
    assert a and a["decision"] == "ACCEPT" and "+87.0%" in a["why"] and "7,693,669,883" in a["why"]
    assert config.review_answered({**r44, "date": "2026-11-09"}, on="2026-11-09") is None, "another day stays open"
    circ = {"project": "Uniswap", "metric": "circulating_supply", "reason": "onchain_vs_coingecko",
            "date": "2026-11-04"}
    a = config.review_answered(circ, on="2026-11-05")
    assert a and a["decision"] == "ACCEPT" and "14,262,527" in a["why"]
    assert config.review_answered(circ, on="2026-12-01") is None, "past its re-check date the row is open again"


# ============================================================================================================ U3 / U8
def _with_shares(monkeypatch, asof="2026-01-10"):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    days = ["2025-12-26", "2025-12-27", "2025-12-28", "2025-12-29"]
    groups = {("Uniswap", "gross_burn_tokens"): _rows("Uniswap", "gross_burn_tokens", days,
                                                      [5.0, 6.0, 100_000_007.0, 8.0], "chain:burn_dead:delta")}
    bw._ONE_OFF_HV_RECORDED.clear()
    bw._one_off_views(groups)
    bw._one_off_shares(groups, pd.Timestamp(asof))
    return groups


def test_u3_the_monthly_tab_says_the_one_off_share_of_its_month(monkeypatch):
    _with_shares(monkeypatch)
    assert bw.ONE_OFF_MONTHS[("Uniswap", "gross_burn_tokens", "2025-12")][0] == 100_000_000.0
    monthly = pd.DataFrame([{"key": "Uniswap|gross_burn_tokens", "label": "x", "kind": "flow", "unit": "tokens",
                             "2025-11": 1.0, "2025-12": 100_000_026.0}])
    wb = openpyxl.Workbook()
    bw.write_monthly(wb.active, ["2025-11", "2025-12"], monthly)
    assert wb.active.freeze_panes == "E2"
    dec, nov = wb.active.cell(2, 6), wb.active.cell(2, 5)
    assert dec.value == 100_000_026.0, "the month keeps the one-off, like the daily series and Q-3"
    # PR1.U10: 100,000,000 of 100,000,026 reads 100.00% (two decimals above 99%: not all, never rounded to "100%")
    assert dec.comment and "of which one-off: 100.00%" in dec.comment.text and "UNIfication" in dec.comment.text
    assert nov.comment is None


def test_u3_a_flows_line_says_where_the_one_off_is(monkeypatch):
    _with_shares(monkeypatch)
    burned = {"Project": "Uniswap", "Line": "− burned", "Series": "gross_burn_tokens", "Unit": "tokens",
              "Q0": 26.0 - 100_000_000.0 + 100_000_000.0 - 100_000_000.0, "Q-1": 0.0}
    note = bw._flows_one_off_note(burned)
    assert "OUT of this line" in note and "Q0" in note, "the identity keeps an issuer-side burn out of '− burned'"
    carried = {**burned, "Line": "→ retired", "Q0": 100_000_026.0}
    assert "of which one-off 100.00%" in bw._flows_one_off_note(carried)          # PR1.U10: two decimals above 99%
    assert bw._flows_one_off_note({**burned, "Series": "fees_usd"}) == ""


def test_u3_a_data_window_holding_the_one_off_carries_its_share(monkeypatch):
    _with_shares(monkeypatch)
    data = pd.DataFrame([{c: None for c in bw.DATA_COLS}])
    data.loc[0, ["key", "project", "metric", "kind", "unit", "status"]] = [
        "Uniswap|gross_burn_tokens", "Uniswap", "gross_burn_tokens", "flow", "tokens", "ok"]
    data.loc[0, "q0"] = 100_000_026.0
    data.loc[0, "q1"] = 3.0
    wb = openpyxl.Workbook()
    bw.write_data(wb.active, data, pd.Timestamp("2026-01-10"))
    q0 = wb.active.cell(2, bw.DATA_COLS.index("q0") + 1)
    q1 = wb.active.cell(2, bw.DATA_COLS.index("q1") + 1)
    assert q0.comment and "of which one-off: 100.00%" in q0.comment.text         # PR1.U10: two decimals above 99%
    assert q1.comment is None


def test_u8_a_small_one_off_share_is_never_shown_as_zero():
    assert bw._share_pct(13_303 / 5_057_505) == "0.26%"
    assert bw._share_pct(0.23) == "23%" and bw._share_pct(1.0) == "100%"


def test_u8_the_distributor_release_stays_in_dilution_and_the_a4_key():
    spec = config.ONE_OFF_DILUTION_NOTES["Uniswap"]
    assert spec["carriers"] == ("outflow_e_tokens", "unlock_release_vesting_tokens", "unlock_release_tokens")
    assert config.A4_RELEASE_ISSUANCE["Uniswap"]["metric"] == "unlock_release_tokens", \
        "A4's key divides the burn by the release, which holds the claims"
    assert config.one_offs_in_rates()
