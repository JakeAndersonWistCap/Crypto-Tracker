"""
tests/test_review_3500042.py — Jake's run on 3500042 (2026-10-13): items 20-26 and decision 5. Real dumps
(fixtures/real_*.json) where one holds the shape; the on-chain figures quoted were read 2026-10-13.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from test_overnight_ab5f2a9 import _Daily, _Out, _row, _get  # noqa: E402
from test_trajectory import _built  # noqa: E402


# ------------------------------------------------------------------------------------------- 21 Fluid
def test_sql_section_cp_removes_only_the_fluid_rows_that_carry_the_igp137_lock(tmp_path):
    import sqlite3
    import run_sql
    text = Path(__file__).resolve().parents[1].joinpath("orphan_cleanup.sql").read_text(encoding="utf-8")
    assert "-- CP. FLUID: THE SIX NON-CIRCULATING ROWS" in text
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT, tier INT)")
    old = "chain:ethereum:noncirc_timelock+noncirc_merkle_distributor_eth_usdc_lp+noncirc_igp137_lock"
    new = "chain:ethereum:noncirc_timelock+noncirc_merkle_distributor_eth_usdc_lp"
    rows = [(f"2026-10-{d:02d}", "Fluid", "noncirculating_holding_tokens", 1.0, old, 2) for d in range(7, 13)]
    rows += [("2026-10-06", "Fluid", "noncirculating_holding_tokens", 1.0, new, 2),
             ("2026-10-13", "Fluid", "noncirculating_holding_tokens", 1.0, new, 2),
             ("2026-10-13", "Fluid", "noncirculating_igp137_tokens", 5e6, "chain:ethereum:noncirc_igp137_lock", 2)]
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    sec = None
    delete = [ln[3:] if ln.startswith("-- ") else ln for ln in text.split("-- CP2. THE DELETE.")[1].splitlines()[1:3]]
    con.execute("\n".join(delete).rstrip(";"))
    con.commit()
    left = con.execute("SELECT date, metric FROM metrics ORDER BY date, metric").fetchall()
    assert left == [("2026-10-06", "noncirculating_holding_tokens"), ("2026-10-13", "noncirculating_holding_tokens"),
                    ("2026-10-13", "noncirculating_igp137_tokens")], (left, sec)
    assert run_sql is not None


# ------------------------------------------------------------------------------------------- 22 Maple
def test_maple_multisig_floor_admits_the_real_2025_07_14_balance():
    lo, hi = config.sanity_bounds("Maple", "treasury_holding_tokens_chain", "2025-07-14")
    assert lo <= 16_897.46 <= hi and lo > 0          # a wrong-address zero is still refused


# ------------------------------------------------------------------------------------------- 23 Hyperliquid
def test_hyperliquid_stake_outside_circulating_is_read_and_taken_out_of_the_lock():
    from fetch import hypercore as h
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["hypercore"]["reads"]
                if r.get("staked_noncirculating")) if config.PROJECT_BY_NAME["Hyperliquid"].get("hypercore") else None
    staked = {"0x43e9abea1910387c4292bca4b94de81462f8a251": "237930044.4762020707",
              "0xd57ecca444a9acb7208d286be439de12dd09de5d": "60509177.7263667136",
              "0xa20fcfa0507fe762011962cc581b95bbbc3bbdba": "2868571.5134560098"}

    class _Http:
        def post(self, url, json_body=None):
            return {"delegated": staked[json_body["user"]], "undelegated": "0.0", "totalPendingWithdrawal": "0.0"}
    x = h.HyperCoreInfo()
    x.http = _Http()
    spec = read or {"exclude_wallets": [{"address": a, "label": a[:6]} for a in list(staked)[1:]],
                    "unlock_wallets": [{"address": list(staked)[0], "label": "team"}]}
    from fetch.base import FetchOutput
    out = FetchOutput()
    x._staked_outside_circulating("Hyperliquid", "http://x", spec, out, pd.Timestamp("2026-10-13"))
    f = out.frame()
    assert float(f.loc[f.metric == "staked_noncirculating_tokens", "value"].iloc[0]) == pytest.approx(301_307_793.72,
                                                                                                    abs=0.01)
    assert config.locked_excluded_metrics("Hyperliquid") == ("staked_noncirculating_tokens",)
    import build_workbook as bw

    class R:
        def D(self, r, m, w):
            return f"[{m}]"
    expr = bw._ff_lock(R(), 5, config.PROJECT_BY_NAME["Hyperliquid"])
    assert expr == "MAX(0,[locked_tokens]-[staked_noncirculating_tokens])"
    # the 2026-10-13 figures: 238.6M circulating − (440.07M − 301.31M) is a positive float
    assert 238_602_000 - (440_065_811 - 301_307_794) == pytest.approx(99_844_000, abs=1_000)


# ------------------------------------------------------------------------------------------- 24 NHY
def test_net_holder_yield_names_the_starved_outflow_leg_on_the_real_maple_dump(tmp_path, monkeypatch):
    import xlcalc
    wb, _ = _built(tmp_path, monkeypatch, "Maple")
    o = _row(wb, xlcalc.Workbook(wb), "CP Comparability", "Maple")
    nhy = _get(o, "NET HOLDER YIELD")
    assert nhy == "n/a — outflow (a) = emissions_tokens has no value in this window: no rows stored", nhy


def test_a_blocked_or_empty_outflow_leg_blocks_the_category_with_its_reason(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Near"]])
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {("Near", "gross_issuance_tokens"): "BLOCKED — declared vs observed"})
    d = pd.date_range("2026-07-01", periods=5)
    df = pd.DataFrame({"date": d, "project": "Near", "metric": "unlock_release_tokens", "value": 1.0, "source": "x",
                       "tier": 1})
    groups = {("Near", "unlock_release_tokens"): df,
              ("Near", "price_usd"): df.assign(metric="price_usd"),
              ("Near", "gross_issuance_tokens"): df.assign(metric="gross_issuance_tokens")}
    bw._outflow_views(groups)
    assert ("Near", "outflow_a_usd") not in groups
    assert bw._VIEW_BLOCKS[("Near", "outflow_a_usd")].startswith(
        "BLOCKED — outflow (a) is not formed: gross_issuance_tokens: BLOCKED — declared vs observed")
    assert ("Near", "outflow_e_usd") in groups


# ------------------------------------------------------------------------------------------- 25 Aerodrome
def _tail_case(day: str, bps: int):
    from fetch.base import FetchOutput
    from fetch.chain import Chain
    p = config.PROJECT_BY_NAME["Aerodrome"]
    spec = p["contracts"]["minter"]
    ch = Chain(backfill={"day": pd.Timestamp(day), "metrics": {}, "solana": {}})

    class _Reader:
        def raw(self, chain, address, call):
            return bps

        def scaled(self, chain, address, call):
            return 1_980_000_000.0
    ch.reader = _Reader()
    return ch._tail_aware_emission("base", spec["address"], p["contracts"], spec["emission_tail"], 1.0, "minter",
                                   FetchOutput(), "Aerodrome", "gross_issuance_tokens")


def test_aerodrome_tail_band_is_dated_by_the_contracts_own_nudge_history():
    # 2025-07-18: 35 bps (18.2% a year) was the real rate, read at that epoch's block on Base
    v, note = _tail_case("2025-07-18", 35)
    assert v == pytest.approx(1_980_000_000 * 35 / 10_000) and note == "tail@35bps"
    # today the documented band stands: an un-nudged 67 bps (34.8%) still reads as a finding
    v, why = _tail_case("2026-10-09", 67)
    assert v is None and "outside the documented 5%-15% band" in why
    v, _ = _tail_case("2026-10-09", 21)
    assert v == pytest.approx(1_980_000_000 * 21 / 10_000)


def test_archive_not_written_line_carries_a_refusal_reason():
    src = Path(__file__).resolve().parents[1].joinpath("fetch/archive.py").read_text(encoding="utf-8")
    assert 'msgs += [g["reason"] for g in out.gaps if g.get("project") == name and g.get("metric") == m]' in src


# ------------------------------------------------------------------------------------------- 26 sanity
def test_sanity_exit_status_ignores_parked_projects(monkeypatch, capsys):
    import sanity
    import build_workbook as bw
    monkeypatch.setattr(sanity, "check_workbook", lambda p: [("World Mobile", "x", "parked hit")])
    assert sanity.main(["--workbook", "w.xlsx"]) == 0
    out = capsys.readouterr().out
    assert "0 hit(s) on live projects" in out and "PARKED PROJECTS — 1 hit(s)" in out and "parked hit" in out
    monkeypatch.setattr(sanity, "check_workbook", lambda p: [("World Mobile", "x", "a"), ("Maple", "y", "b")])
    assert sanity.main(["--workbook", "w.xlsx"]) == 1
    assert bw is not None


# ------------------------------------------------------------------------------------------- decision 5
def test_chain_fee_share_needs_every_chain_and_sits_beside_the_tvl_share(monkeypatch):
    from fetch import business as b
    days = [1791504000 + 86400 * i for i in range(3)]

    class _Http:
        def get(self, url, params=None):
            slug = url.rsplit("/", 1)[1]
            name = dict(config._FEE_CHAINS)[slug]
            chart = [[t, 10.0] for t in days]
            if slug == "sui":
                chart = chart[:1] + chart[2:]          # Sui silent on the middle day
            return {"name": name, "totalDataChart": chart}
    monkeypatch.setattr(b, "today", lambda: pd.Timestamp("2026-10-13"))   # stores through 2026-10-11
    out = _Out()
    biz = b.Business(http=_Http(), daily=_Daily())
    biz._share("Ethereum", config.MARKET_SHARE_2["Ethereum"], "2026-10-12", out, metric="market_share_2_pct",
               denominator="market_share_2_category_usd")
    f = pd.concat(out.frames)
    sh = f[f.metric == "market_share_2_pct"]
    assert len(sh) == 2 and sh["value"].tolist() == pytest.approx([1 / 13, 1 / 13])
    assert config.MARKET_SHARE["Ethereum"]["how"] == "chain_tvl" and set(config.MARKET_SHARE_2) == {"Ethereum", "Near"}


# ------------------------------------------------------------------------------------------- 20 GEODNET
def test_the_geodnet_diagnosis_locates_the_span_of_a_missing_100000_burn(tmp_path, monkeypatch, capsys):
    """Item 20: 52,018,504.0020 recorded vs 52,118,504.0020 of stock movement. The diagnosis lists the stock pair
    whose 100,000 move no differenced flow covers, and says rederive.py fills it (same measuring point)."""
    import check_offline_items as coi
    import store as sm
    from real_dump import to_store
    src = "chain:sum(polygon:burn_polygon+solana:burn_solana_token_account)"
    rows, cum = [], 10_000_000.0
    for d in pd.date_range("2025-08-05", "2026-10-11"):
        cum += 100_000
        rows.append({"date": str(d.date()), "metric": "burn_address_balance", "value": cum, "source": src, "tier": 2,
                     "fetched_at": "x"})
        if d > pd.Timestamp("2025-08-05") and d != pd.Timestamp("2025-08-25"):
            rows.append({"date": str(d.date()), "metric": "gross_burn_tokens", "value": 100_000,
                         "source": src + ":delta", "tier": 2, "fetched_at": "x"})
    db = tmp_path / "geod.db"
    to_store({"meta": {"project": "GEODNET", "asof": "2026-10-12"}, "metrics": rows, "review_queue": [],
              "manual_overrides": [], "run_log": [], "gap_report": [], "manual_references": []}, db)
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", bw._SCOPE)
    coi.diagnose_geodnet_burn()
    out = capsys.readouterr().out
    assert "(residual 100,000.00)" in out
    assert ("2025-08-24..2025-08-25: stock 100,000.0000 vs flows 0.0000 (missing 100,000.0000) — no differenced flow "
            "covers it — rederive.py fills it") in out
