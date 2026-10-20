"""
tests/test_overnight_ab5f2a9.py — Jake's overnight review of ab5f2a9 (2026-10-12 night): corrections 17-19 and the
Phase 3 / Phase 4 datasets. Judged on the real dumps (fixtures/real_*.json) wherever one holds the shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from test_trajectory import _built  # noqa: E402


def _row(wb, X, sheet, project):
    ws = wb[sheet]
    hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
    r = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return {str(ws.cell(hr, c).value): X.value(sheet, r, c) for c in range(1, ws.max_column + 1)
            if ws.cell(hr, c).value}


def _get(row, prefix):
    return next(v for h, v in row.items() if h.startswith(prefix))


def _data(wb, X, key, col):
    import build_workbook as bw
    ws = wb["Data"]
    r = next(i for i in range(2, ws.max_row + 1) if ws.cell(i, 1).value == key)
    return X.value("Data", r, bw.DATA_COLS.index(col) + 1)


# ------------------------------------------------------------------------------------------- 17 Total Yield
def test_chainlink_total_yield_subtracts_the_reward_vault_payouts_on_the_real_dump(tmp_path, monkeypatch, dumps_20261009):
    """Item 17: supply is fixed, but staking rewards come out of the issuer-funded Reward Vault (outside the 27
    non-circulating wallets, whose own releases are the net figure's basis). Total Yield subtracts them."""
    import xlcalc
    wb, _ = _built(tmp_path, monkeypatch, "Chainlink")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, "A1 Infrastructure", "Chainlink")
    vy, ty, th = _get(o, "VALIDATOR STAKING YIELD"), _get(o, "TOTAL YIELD — stakers"), _get(o, "Total yield — non-stakers")
    net = _get(o, "NET ISSUANCE AFTER BURN, annualised")
    em_q0, cov = _data(wb, X, "Chainlink|emissions_tokens", "q0"), _data(wb, X, "Chainlink|emissions_tokens",
                                                                          "q0_covered_days")
    circ = _data(wb, X, "Chainlink|circulating_supply_used", "now")
    rel = em_q0 * 365 / (cov if isinstance(cov, (int, float)) and cov and cov != 90 else 90) / circ
    assert th == pytest.approx(-(net + rel), rel=1e-6) and rel > 0
    assert ty == pytest.approx(vy - (net + rel), rel=1e-6) and ty < vy
    assert config.TOTAL_YIELD_RELEASES["Chainlink"]["add"] == ("emissions_tokens",)


def test_plume_total_yield_is_na_while_its_reward_payouts_are_unmeasured(tmp_path, monkeypatch):
    import xlcalc
    wb, _ = _built(tmp_path, monkeypatch, "Plume")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, "A1 Infrastructure", "Plume")
    assert str(_get(o, "TOTAL YIELD — stakers")).startswith("n/a — staking rewards are paid out of the PRE-MINTED")
    assert str(_get(o, "Total yield — non-stakers")).startswith("n/a — staking rewards")
    assert isinstance(_get(o, "VALIDATOR STAKING YIELD"), float)


def test_hyperliquid_adds_only_the_foundation_and_grants_releases():
    """The team wallet's falls already move tokenDetails' circulating (the net figure's basis); the Foundation / Grants
    wallets are counted AS circulating by it, so only their releases are added — a release series of that subset."""
    import build_workbook as bw
    assert config.UNLOCK_RELEASE_SUBSETS["Hyperliquid"] == {"unlock_release_counted_tokens": ("excluded_wallets_tokens",)}
    days = pd.date_range("2026-09-01", "2026-09-05")

    def g(m, vals):
        return pd.DataFrame({"date": days, "project": "Hyperliquid", "metric": m, "value": vals, "source": f"x:{m}",
                             "tier": 2, "is_manual": False, "entered_on": ""})
    groups = {("Hyperliquid", "excluded_wallets_tokens"): g("excluded_wallets_tokens", [100.0, 90.0, 90.0, 80.0, 85.0]),
              ("Hyperliquid", "team_wallet_tokens"): g("team_wallet_tokens", [50.0, 40.0, 30.0, 20.0, 10.0])}
    import types
    old = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Hyperliquid"]]
    try:
        bw._unlock_views(groups)
    finally:
        bw._SCOPE = old
    sub = groups[("Hyperliquid", "unlock_release_counted_tokens")].set_index("date")["value"]
    full = groups[("Hyperliquid", "unlock_release_tokens")].set_index("date")["value"]
    # the subset alone: 10 + 10 (its rise on the last day is not a negative release); the whole set's daily sum is clipped
    # as a sum (existing behaviour): 20 + 10 + 20 + (−5 + 10)
    assert sub.sum() == pytest.approx(20.0) and full.sum() == pytest.approx(55.0)
    assert types is not None


# ------------------------------------------------------------------------------------------- 19 GEODNET
def test_the_geodnet_diagnosis_names_the_case_and_the_next_command(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store as sm
    from real_dump import to_store
    src = "chain:sum(polygon:burn_polygon+solana:burn_solana_token_account)"
    rows, cum = [], 10_000_000.0
    for d in pd.date_range("2025-07-14", "2026-10-11"):
        cum += 50_000
        rows.append({"date": str(d.date()), "metric": "burn_address_balance", "value": cum, "source": src, "tier": 2,
                     "fetched_at": "x"})
        if d > pd.Timestamp("2025-07-14"):
            v = 9_000_000 if d == pd.Timestamp("2026-08-20") else 50_000
            rows.append({"date": str(d.date()), "metric": "gross_burn_tokens", "value": v, "source": src + ":delta",
                         "tier": 2, "fetched_at": "x"})
    db = tmp_path / "geod.db"
    to_store({"meta": {"project": "GEODNET", "asof": "2026-10-12"}, "metrics": rows, "review_queue": [],
              "manual_overrides": [], "run_log": [], "gap_report": [], "manual_references": []}, db)
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", bw._SCOPE)
    coi.diagnose_geodnet_burn()
    out = capsys.readouterr().out
    assert "gross_burn_tokens: status implausible_delta" in out
    assert "2026-08-20" in out and "NEXT: one flow row is most of the cumulative" in out
    assert "python rederive.py GEODNET gross_burn_tokens" in out
    assert coi.diagnose_geodnet_burn in coi.CHECKS


# ------------------------------------------------------------------------------------------- Phase 3: the T-bill
_TREASURY_CSV = ("Date,4 WEEKS BANK DISCOUNT,4 WEEKS COUPON EQUIVALENT,13 WEEKS BANK DISCOUNT,13 WEEKS COUPON EQUIVALENT\n"
                 "10/08/2026,4.10,4.18,4.05,4.15\n10/07/2026,4.11,4.19,4.06,4.16\n01/02/2025,4.30,4.39,4.25,4.36\n")
_FRED_CSV = "observation_date,DTB3\n2026-10-07,4.06\n2026-10-08,4.05\n2026-10-09,.\n"


class _Out:
    def __init__(self):
        self.frames, self.notes, self.fails = [], [], []

    def add(self, df, source, project, detail, tier):
        self.frames.append(df)
        self.notes.append(detail)

    def fail(self, source, project, msg, tier=None):
        self.fails.append(msg)

    def skipped(self, source, project, msg, tier=None):
        self.notes.append(msg)

    def mark_current(self, source, project, metric, msg, tier=None):
        self.notes.append(msg)
        self.current = getattr(self, "current", set()) | {(project, metric)}


class _Daily:
    def __init__(self):
        self.d = {}

    def due(self, k, day):
        return self.d.get(k) != day

    def ever(self, k):
        return k in self.d

    def done(self, k, day):
        self.d[k] = day


def test_treasury_parses_the_coupon_equivalent_and_fred_the_discount_rate():
    from fetch import treasury as t
    rows = t.parse_treasury(_TREASURY_CSV)
    assert rows[-1] == (pd.Timestamp("2026-10-08"), pytest.approx(0.0415))
    assert t.parse_fred(_FRED_CSV) == [(pd.Timestamp("2026-10-07"), pytest.approx(0.0406)),
                                       (pd.Timestamp("2026-10-08"), pytest.approx(0.0405))]
    assert isinstance(t.parse_treasury("Date,Other\n10/08/2026,1\n"), str)       # refused, with the reason
    assert isinstance(t.parse_fred("<html>"), str)


def test_treasury_adapter_stores_both_series_under_macro_once_a_day(monkeypatch):
    from fetch import treasury as t
    monkeypatch.setattr(t, "today", lambda: pd.Timestamp("2026-10-12"))
    urls = []

    def get(url):
        urls.append(url)
        return _FRED_CSV if "fred" in url else _TREASURY_CSV
    daily, out = _Daily(), _Out()
    t.TreasuryBills(get_text=get, daily=daily).run([], None, out)
    df = pd.concat(out.frames)
    assert set(df["project"]) == {config.MACRO_PROJECT}
    y = df[df["metric"] == "tbill_3m_yield"]
    assert y.sort_values("date")["value"].iloc[-1] == pytest.approx(0.0415)
    # the first read covers the trajectory's 455 days: both 2025 and 2026 files, the Jan-2025 row outside the window
    assert any("/2025/" in u for u in urls) and pd.Timestamp("2025-01-02") not in set(y["date"])
    assert sorted(df[df["metric"] == "tbill_3m_dtb3"]["value"]) == pytest.approx([0.0405, 0.0406])
    out2 = _Out()
    t.TreasuryBills(get_text=get, daily=daily).run([], None, out2)
    assert not out2.frames and "already" in out2.notes[0]
    # a failed read stores nothing, says why and stays due
    daily3, out3 = _Daily(), _Out()
    t.TreasuryBills(get_text=lambda u: "<html>blocked</html>", daily=daily3).run([], None, out3)
    assert not out3.frames and "NOTHING STORED" in out3.fails[0] and daily3.due("macro:tbill_3m", "2026-10-12")


def _with_tbill(monkeypatch, value=0.0415):
    """The real dump plus a stored T-bill: the dumps predate Phase 3, so the benchmark series is added beside them."""
    import real_dump
    orig = real_dump.to_store

    def to_store(payload, db):
        orig(payload, db)
        import store as sm
        st = sm.Store(str(db))
        days = pd.date_range("2025-07-01", "2026-10-08", freq="B")
        st.upsert(pd.DataFrame({"date": days, "project": config.MACRO_PROJECT, "metric": "tbill_3m_yield",
                                "value": value, "source": "treasury_bills:test", "tier": 1}))
        st.close()
    monkeypatch.setattr(real_dump, "to_store", to_store)


def test_the_tbill_sits_beside_every_yield_on_the_real_chainlink_dump(tmp_path, monkeypatch):
    import xlcalc
    _with_tbill(monkeypatch)
    wb, rows = _built(tmp_path, monkeypatch, "Chainlink")
    X = xlcalc.Workbook(wb)
    a1 = _row(wb, X, "A1 Infrastructure", "Chainlink")
    assert _get(a1, "3-MONTH T-BILL") == pytest.approx(0.0415)
    assert _get(a1, "Spread over the 3-month T-bill: Total yield — stakers") == pytest.approx(_get(a1, "TOTAL YIELD — stakers") - 0.0415)
    cp = _row(wb, X, "CP Comparability", "Chainlink")
    assert _get(cp, "Spread over the 3-month T-bill: Holder value yield") == pytest.approx(_get(cp, "HOLDER VALUE YIELD") - 0.0415)
    assert _data(wb, X, "Macro|tbill_3m_yield", "q1") == pytest.approx(0.0415)
    # the trajectory benchmark now has its target
    tb = [r for r in rows if r.get("hid") == "a1_total_yield_stakers"]
    assert tb and all(r["target"] == pytest.approx(0.0415) and "no tbill_3m_yield stored" not in r["why"] for r in tb)


def test_no_tbill_stored_reads_na_never_zero(tmp_path, monkeypatch):
    import xlcalc
    wb, _ = _built(tmp_path, monkeypatch, "Chainlink")
    X = xlcalc.Workbook(wb)
    a1 = _row(wb, X, "A1 Infrastructure", "Chainlink")
    # never a zero, and never a bare n/a (alignment round, SPEC rule 2): the reason names the missing series
    assert str(_get(a1, "3-MONTH T-BILL")).startswith("n/a — Data: no rows stored")
    assert _get(a1, "Spread over the 3-month T-bill: Total yield — stakers") == "n/a — tbill_3m_yield (now): no rows stored"


# ------------------------------------------------------------------------------------------- Phase 3: the SSR
def _ssr_events():
    import json
    raw = json.loads(Path(__file__).resolve().parents[1].joinpath("fixtures/onchain_sky_ssr_events.json").read_text())
    return raw["events"], int(raw["_meta"]["ssr_now"])


def test_sky_ssr_history_is_the_contracts_own_steps_reconciled_to_ssr_now(monkeypatch):
    """The 20 real File('ssr') events (read 2026-10-12): each day carries the rate in force at its end, and the
    latest event equals ssr() now to the unit."""
    from fetch import sky_ssr as s
    events, now = _ssr_events()
    assert len(events) == 20 and s.apy(now) == pytest.approx(0.038, abs=1e-6)
    monkeypatch.setattr(s, "today", lambda: pd.Timestamp("2026-10-12"))
    daily, out = _Daily(), _Out()
    s.SkySSR(logs_fn=lambda: events, ssr_now_fn=lambda: now, daily=daily).run([], None, out)
    df = pd.concat(out.frames).set_index("date")["value"]
    assert len(df) == 456 and set(pd.concat(out.frames)["project"]) == {config.MACRO_PROJECT}
    # 4.00% from 2025-12-16 15:02 UTC: the 16th ends at 4.00%, the 15th at 4.25%
    assert df[pd.Timestamp("2025-12-15")] == pytest.approx(0.0425, abs=1e-6)
    assert df[pd.Timestamp("2025-12-16")] == pytest.approx(0.04, abs=1e-6)
    assert df[pd.Timestamp("2026-10-06")] == pytest.approx(0.036, abs=1e-6)
    assert df[pd.Timestamp("2026-10-07")] == pytest.approx(0.038, abs=1e-6)
    assert daily.ever("macro:sky_ssr:history")
    # a later read is 14 days
    out2, d2 = _Out(), _Daily()
    d2.d["macro:sky_ssr:history"] = "2026-10-11"
    s.SkySSR(logs_fn=lambda: events, ssr_now_fn=lambda: now, daily=d2).run([], None, out2)
    assert len(pd.concat(out2.frames)) == 15


def test_sky_ssr_refuses_history_that_does_not_reconcile_and_keeps_it_owed(monkeypatch):
    from fetch import sky_ssr as s
    events, now = _ssr_events()
    monkeypatch.setattr(s, "today", lambda: pd.Timestamp("2026-10-12"))
    daily, out = _Daily(), _Out()
    s.SkySSR(logs_fn=lambda: events[:-1], ssr_now_fn=lambda: now, daily=daily).run([], None, out)
    assert len(pd.concat(out.frames)) == 1 and "history REFUSED" in out.notes[0]
    assert not daily.ever("macro:sky_ssr:history")


def test_ssr_sits_beside_the_tbill(tmp_path, monkeypatch):
    import xlcalc
    import real_dump
    _with_tbill(monkeypatch)
    orig = real_dump.to_store

    def to_store(payload, db):
        orig(payload, db)
        import store as sm
        st = sm.Store(str(db))
        st.upsert(pd.DataFrame({"date": pd.date_range("2025-07-01", "2026-10-09"), "project": config.MACRO_PROJECT,
                                "metric": "sky_savings_rate", "value": 0.038, "source": "sky_ssr:test", "tier": 2}))
        st.close()
    monkeypatch.setattr(real_dump, "to_store", to_store)
    wb, _ = _built(tmp_path, monkeypatch, "Chainlink")
    X = xlcalc.Workbook(wb)
    assert _get(_row(wb, X, "A1 Infrastructure", "Chainlink"), "Sky Savings Rate") == pytest.approx(0.038)


# ------------------------------------------------------------------------------------------- Phase 4 + market share
def _with_business(monkeypatch):
    """The real dump plus the real business reads of 2026-10-12 (fixtures/business_reads_2026-10-12.json)."""
    import json
    import real_dump
    rows = json.loads(Path(__file__).resolve().parents[1].joinpath("fixtures/business_reads_2026-10-12.json")
                      .read_text())["rows"]
    orig = real_dump.to_store

    def to_store(payload, db):
        orig(payload, db)
        import store as sm
        st = sm.Store(str(db))
        # only what had been read by the dump's own as-of date
        mine = [r for r in rows if r["project"] == payload["meta"]["project"] and r["date"] <= payload["meta"]["asof"][:10]]
        if mine:
            st.upsert(pd.DataFrame(mine).assign(date=lambda f: pd.to_datetime(f["date"])))
        st.close()
    monkeypatch.setattr(real_dump, "to_store", to_store)
    return rows


def test_business_and_market_share_headlines_on_the_real_aerodrome_dump(tmp_path, monkeypatch, dumps_20261009):
    import xlcalc
    rows = _with_business(monkeypatch)
    wb, traj = _built(tmp_path, monkeypatch, "Aerodrome")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, "CP Comparability", "Aerodrome")
    q0, cov = (_data(wb, X, "Aerodrome|protocol_dex_volume_usd", c) for c in ("q0", "q0_covered_days"))
    vol = [r["value"] for r in rows if r["project"] == "Aerodrome" and r["metric"] == "protocol_dex_volume_usd"]
    assert cov == 90 and vol and q0 > 0
    assert _get(o, "BUSINESS 1 — core usage") == pytest.approx(q0 / 90 * 365, rel=1e-9)
    assert "protocol_dex_volume_usd" in _get(o, "Business 1 — series") and "on-chain replacement" in \
        _get(o, "Business 1 — series")
    share = [r for r in rows if r["project"] == "Aerodrome" and r["metric"] == "market_share_pct"
             and r["date"] <= "2026-10-09"][-1]
    assert _get(o, "MARKET SHARE vs its category") == pytest.approx(share["value"])
    assert "Dexs" in _get(o, "Market share — basis")
    ids = {r["hid"] for r in traj if r["project"] == "Aerodrome"} if traj and "project" in traj[0] else \
        {r["hid"] for r in traj}
    assert {"cp_business_1", "cp_market_share"} <= ids
    assert all(r["verdict"].startswith("PASS") for r in traj if r["hid"] in ("cp_business_1", "cp_market_share"))


def test_business_and_market_share_are_na_with_the_reason_where_unsourced(tmp_path, monkeypatch):
    import xlcalc
    _with_business(monkeypatch)
    wb, _ = _built(tmp_path, monkeypatch, "Plume")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, "CP Comparability", "Plume")
    assert str(_get(o, "MARKET SHARE vs its category")).startswith("n/a — share of DefiLlama RWA")


def test_market_share_drops_a_day_a_launched_peer_did_not_report():
    from fetch.business import strict_sum
    d = pd.Timestamp
    s = strict_sum({"own": {d("2026-01-01"): 1.0, d("2026-01-02"): 1.0, d("2026-01-03"): 1.0},
                    "peer": {d("2026-01-02"): 3.0}})          # launched on the 2nd, silent on the 3rd
    assert s == {d("2026-01-01"): 1.0, d("2026-01-02"): 4.0}


def test_business_adapter_refuses_a_renamed_protocol(monkeypatch):
    from fetch import business as b

    class _Http:
        def get(self, url, params=None):
            return {"totalDataChartBreakdown": [[1791504000, {"Aerodrome V1": 5.0}]],
                    "protocols": [{"name": "Aerodrome V1", "category": "Dexs", "parentProtocol": "parent#aerodrome"}]}
    monkeypatch.setattr(b, "today", lambda: pd.Timestamp("2026-10-12"))
    monkeypatch.setitem(config.BUSINESS_SERIES, "Aerodrome", [config.BUSINESS_SERIES["Aerodrome"][0]])
    out = _Out()
    b.Business(http=_Http(), daily=_Daily()).run([config.PROJECT_BY_NAME["Aerodrome"]], None, out)
    # G9 A2: children are matched by DefiLlama id (a name is not an identity — "Aero Lite" was renamed "Aero"); a
    # listing that carries none of the declared ids stores nothing and names every one
    assert any("lists none of" in f and "Aerodrome V1 (id 3450)" in f and "Aero Lite (id 8691)" in f
               for f in out.fails)


def test_etf_and_treasury_holdings_are_dated_issuer_readings_on_cp(tmp_path, monkeypatch):
    import json
    import xlcalc
    raw = json.loads(Path(__file__).resolve().parents[1].joinpath("etf_dat_holdings.json").read_text())
    assert raw["_meta"]["entered_by"] == "Claude Code, pending Jake's review"
    for name, rec in raw["projects"].items():
        for kind in ("etf_etp", "dat"):
            for e in rec[kind]:
                assert e["source_url"] and len(e["as_of"]) == 10 and e["as_of"] <= "2026-10-12", (name, e)
        assert rec["etf_etp"] or rec["dat"] or rec.get("na"), name
    wb, _ = _built(tmp_path, monkeypatch, "Chainlink")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, "CP Comparability", "Chainlink")
    etf = sum(e["tokens"] for e in raw["projects"]["Chainlink"]["etf_etp"])
    assert _get(o, "ETF / ETP holdings (tokens)") == pytest.approx(etf)
    circ = _get(o, "Circulating supply (tokens)")
    assert _get(o, "ETF / ETP holdings ÷ circulating") == pytest.approx(etf / circ)
    assert "Grayscale Chainlink Trust ETF (GLNK)" in _get(o, "ETF / ETP and DAT holders")
    (tmp_path / "p").mkdir()
    wb2, _ = _built(tmp_path / "p", monkeypatch, "Plume")
    o2 = _row(wb2, xlcalc.Workbook(wb2), "CP Comparability", "Plume")
    assert str(_get(o2, "ETF / ETP holdings (tokens)")).startswith("n/a — no ETF / ETP")
