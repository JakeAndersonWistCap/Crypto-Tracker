"""ROUND G14 (Jake 2026-10-22, run on 1eb632d) — three store faults. Nothing new is fetched.

1. a fallback written into the primary's series (CoinGecko's total took 2026-10-22 when the chain tier timed out);
2. a flow reconciled against a stock that is missing its last day;
3. Ethereum's settlement rebuild: a day every leg holds is a rebuild day.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _g(project, metric, days, values, source="x"):
    return pd.DataFrame({"date": pd.to_datetime(list(days)), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": ""})


def _row(project, metric, day, value, source, tier=1):
    return {"date": pd.Timestamp(day), "project": project, "metric": metric, "value": float(value),
            "source": source, "tier": tier}


# ================================================================================================== 1 the fallback
def test_1_a_timed_out_primary_takes_no_row_from_another_source():
    import fetch
    from fetch.base import FetchOutput
    days = pd.date_range("2026-10-15", "2026-10-21")
    stored = pd.concat([_g("Chainlink", "total_supply", days, [1e9] * len(days), "chain:ethereum:token"),
                        _g("Ethereum", "total_supply", days, [1.2e8] * len(days), "ultrasound:supply-over-time")])
    out = FetchOutput()
    # the chain tier timed out: only CoinGecko answered for Chainlink on 10-22; Ethereum's declared later leg is
    # CoinGecko, so its row stands
    out.frames = [pd.DataFrame([_row("Chainlink", "total_supply", "2026-10-22", 1e9, "coingecko"),
                                _row("Chainlink", "total_supply_coingecko", "2026-10-22", 1e9, "coingecko"),
                                _row("Ethereum", "total_supply", "2026-10-22", 1.2e8, "coingecko")])]
    lines = fetch._refuse_fallback_into_primary(out, stored)
    kept = out.frame()
    assert not ((kept["project"] == "Chainlink") & (kept["metric"] == "total_supply")).any()
    assert ((kept["project"] == "Chainlink") & (kept["metric"] == "total_supply_coingecko")).any()
    assert ((kept["project"] == "Ethereum") & (kept["metric"] == "total_supply")).any()
    assert len(lines) == 1 and lines[0].startswith("FALLBACK REFUSED Chainlink / total_supply 2026-10-22 from coingecko")
    assert "keeps its last stored value (1e+09 on 2026-10-21)" in lines[0]
    assert any(e.status == "warn" and "FALLBACK REFUSED" in e.message for e in out.log)
    # the primary's own row on the day is never refused
    out = FetchOutput()
    out.frames = [pd.DataFrame([_row("Chainlink", "total_supply", "2026-10-22", 1e9, "chain:ethereum:token")])]
    assert fetch._refuse_fallback_into_primary(out, stored) == [] and len(out.frame()) == 1


def test_1_coingecko_never_writes_an_onchain_first_total():
    from fetch.base import FetchOutput
    from fetch.coingecko import CoinGecko

    class _Http:
        def get(self, url, params=None, headers=None):
            if url.endswith("/coins/markets"):
                return [{"id": "chainlink", "circulating_supply": 6.8e8, "total_supply": 1e9, "max_supply": 1e9,
                         "fully_diluted_valuation": 1.5e10}]
            return {"prices": [], "market_caps": [], "total_volumes": []}
    cg = CoinGecko()
    cg.http = _Http()
    out = FetchOutput()
    cg.run([config.PROJECT_BY_NAME["Chainlink"]], 30, out)
    f = out.frame()
    assert "Chainlink" in config.TOTAL_SUPPLY_ONCHAIN_FIRST
    assert not (f["metric"] == "total_supply").any()
    assert float(f.loc[f["metric"] == "total_supply_coingecko", "value"].iloc[0]) == 1e9


def test_1_the_declared_clean_up_removes_exactly_its_rows_and_logs_each(tmp_path):
    import logging

    import store as sm
    import token_metrics as tm
    st = sm.Store(str(tmp_path / "metrics.db"))
    rows = []
    for p in ("Chainlink", "Maple", "Morpho", "Sky", "Pendle", "Aerodrome", "Ether.fi", "Aethir", "Fluid"):
        rows += [_row(p, "total_supply", "2026-10-21", 1e9, "chain:ethereum:token"),
                 _row(p, "total_supply", "2026-10-22", 9e8, "coingecko"),
                 _row(p, "total_supply_coingecko", "2026-10-22", 9e8, "coingecko")]
    rows.append(_row("Ethereum", "total_supply", "2026-10-22", 1.2e8, "coingecko"))     # declared: stays
    st.upsert(pd.DataFrame(rows))
    lines = tm.apply_run_cleanups(st, "run-test", logging.getLogger("t"))
    assert len(lines) == 9 and all(x.startswith("CLEAN-UP G14-1: removed ") for x in lines)
    left = pd.read_sql("SELECT project, metric, date, source FROM metrics", st.conn)
    assert len(left) == len(rows) - 9
    assert not ((left["metric"] == "total_supply") & (left["source"] == "coingecko")
                & (left["project"] != "Ethereum")).any()
    assert (left["metric"] == "total_supply_coingecko").sum() == 9
    logged = pd.read_sql("SELECT source, message FROM run_log", st.conn)
    assert (logged["source"] == "cleanup:G14-1").sum() == 9
    assert tm.apply_run_cleanups(st, "run-2", logging.getLogger("t")) == []     # idempotent
    st.close()


def test_1_a_stock_not_read_keeps_its_value_marked_stale_with_the_date():
    groups = {("Chainlink", "total_supply"): _g("Chainlink", "total_supply", pd.date_range("2026-10-15", "2026-10-21"),
                                                [1e9] * 7, "chain:ethereum:token"),
              ("Chainlink", "price_usd"): _g("Chainlink", "price_usd", pd.date_range("2026-10-15", "2026-10-22"),
                                             [15.0] * 8, "coingecko")}
    why = bw.not_read_since(groups, "Chainlink", "total_supply")
    assert why.startswith("STALE — last read 2026-10-21, not read on 2026-10-22")
    band, text = bw.confidence_for("Chainlink", "total_supply", {"status": "ok", "n_points": 7, "source":
                                                                 "chain:ethereum:token", "not_read": why},
                                   pd.Timestamp("2026-10-22"))
    assert band == "AMBER" and "last read 2026-10-21" in text
    # read on the day: nothing said
    groups[("Chainlink", "price_usd")] = groups[("Chainlink", "price_usd")].iloc[:-1]
    assert bw.not_read_since(groups, "Chainlink", "total_supply") == ""


def test_1_etherfi_coingecko_total_equal_to_its_circulating_is_not_a_judge():
    import credibility
    rows = {"Ether.fi|total_supply": {"now": 998_535_999.0},
            "Ether.fi|total_supply_coingecko": {"now": 965_350_000.0},
            "Ether.fi|circulating_supply": {"now": 965_350_000.0, "source": "coingecko"}}
    spec = credibility.wiring_inputs("Ether.fi", rows)["in_total_supply_coingecko"]
    assert spec["ref"]["force_verdict"] == "UNVERIFIABLE — reference unreliable: CoinGecko total = its circulating"
    # a CoinGecko total that differs from its circulating is judged as before
    rows["Ether.fi|circulating_supply"]["now"] = 700_000_000.0
    assert "force_verdict" not in credibility.wiring_inputs("Ether.fi", rows)["in_total_supply_coingecko"]["ref"]


# ================================================================================================== 2 flow vs stock
def test_2_a_stock_missing_its_last_day_is_not_a_broken_flow():
    days = pd.date_range("2026-10-15", "2026-10-22")
    level = [1_000_000.0 + 15_000 * i for i in range(len(days))]
    stock = _g("GEODNET", "burn_address_balance", days[:-1], level[:-1], "chain:polygon:burn_polygon")
    flow = _g("GEODNET", "gross_burn_tokens", days[1:], [15_000.0] * (len(days) - 1),
              "chain:polygon:burn_polygon:delta")
    got = bw.telescoping("GEODNET", "gross_burn_tokens", "burn_address_balance", flow, stock)
    assert got["flow_recon_blocked"] is None
    assert abs(got["flow_residual"]) < 1e-6 and got["flow_span"] == ("2026-10-15", "2026-10-21")
    assert "1 gross_burn_tokens row(s) after 2026-10-21 wait for the burn_address_balance reading" in \
        got["flow_recon_waiting"]
    # the same shape before the fix reconciled 10-22's flow against the 10-21 stock: a residual of one day's burn
    assert bw.withheld_for("GEODNET", "gross_burn_tokens", got) is None or \
        bw.withheld_for("GEODNET", "gross_burn_tokens", got)[0] != "unreconciled_flow"
    # a flow that really fails to add up is still caught over the days both hold
    bad = flow.copy()
    bad.loc[bad.index[2], "value"] = 5_000.0
    got = bw.telescoping("GEODNET", "gross_burn_tokens", "burn_address_balance", bad, stock)
    assert abs(got["flow_residual"] - 10_000.0) < 1e-6


# ================================================================================================== 3 settlement
def test_3_three_legs_stored_on_a_day_make_a_rebuild_row_that_day(monkeypatch):
    days = pd.date_range("2026-09-15", "2026-09-30")
    groups = {("Ethereum", "dex_volume_usd"): _g("Ethereum", "dex_volume_usd", days, [1e9] * len(days),
                                                 "defillama:dexs:ethereum"),
              ("Ethereum", "nft_volume_usd"): _g("Ethereum", "nft_volume_usd", days, [2e7] * len(days),
                                                 "eth_settlement:defillama/overview/nft-volume/ethereum"),
              ("Ethereum", "p2p_transfer_volume_usd"): _g("Ethereum", "p2p_transfer_volume_usd", days,
                                                          [3e9] * len(days),
                                                          "eth_settlement:bigquery:p2p[Artemis method, rebuilt]")}
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Ethereum"]])
    bw._settlement_rebuild_views(groups)
    rb = bw._daily(groups[("Ethereum", "settlement_rebuilt_usd")])
    assert list(rb.index) == list(days)
    assert all(rb[pd.Timestamp(d)] == 1e9 + 2e7 + 3e9 for d in ("2026-09-20", "2026-09-21", "2026-09-22",
                                                                 "2026-09-23"))
    # a leg lacking a day: that day only is dropped, and the build says which leg
    groups[("Ethereum", "nft_volume_usd")] = groups[("Ethereum", "nft_volume_usd")][
        ~groups[("Ethereum", "nft_volume_usd")]["date"].isin(pd.to_datetime(["2026-09-21"]))]
    groups.pop(("Ethereum", "settlement_rebuilt_usd"))
    bw._settlement_rebuild_views(groups)
    rb = bw._daily(groups[("Ethereum", "settlement_rebuilt_usd")])
    assert pd.Timestamp("2026-09-21") not in rb.index and len(rb) == len(days) - 1
    assert bw.SETTLEMENT_VALIDATION["Ethereum"]["dropped_days"] == {"nft": ["2026-09-21"]}


def test_3_the_store_diagnostic_names_the_leg_and_leaves_the_build_scope_as_it_found_it(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store as sm
    assert coi.settlement_legs in coi.CHECKS
    st = sm.Store(str(tmp_path / "metrics.db"))
    days = pd.date_range("2026-09-15", "2026-09-30")
    rows = []
    for m, v in (("dex_volume_usd", 1e9), ("nft_volume_usd", 2e7), ("p2p_transfer_volume_usd", 3e9)):
        for d in days:
            if m == "nft_volume_usd" and str(d.date()) == "2026-09-21":
                continue
            rows.append(_row("Ethereum", m, d, v, "x"))
    st.upsert(pd.DataFrame(rows))
    st.close()
    monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "metrics.db"))
    before = list(bw._SCOPE)
    coi.settlement_legs()
    out = capsys.readouterr().out
    assert bw._SCOPE == before, "a check must not narrow the build's scope for whatever runs after it"
    assert "2026-09-21: NO REBUILD ROW — the store lacks nft" in out
    assert "2026-09-20: NO REBUILD ROW" not in out
