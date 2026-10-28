"""P2c-6 (Jake 2026-10-28): Pendle's Fees Δ6m / Δ9m read "n/a — SERIES BREAK" across the D4-5 handover's stitch
(DefiLlama -> Pendle's epoch spread -> DefiLlama for the epoch in progress). A Δ comparison crossing a stitch of a
declared composition-change handover is made on the series' declared ONE basis (config.COMPARISON_BASIS: DefiLlama's
pendle-v2 daily fees, stored under fees_usd_defillama for every day DefiLlama serves) for BOTH windows, through the same
coverage rules; the comment names the source and says the basis differs from the headline's. Where that series does
not hold both windows on its one basis: n/a with the reason. Fakes only, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

WINDOWS = {"q0": ("2026-07-30", "2026-10-27"), "q1": ("2026-05-01", "2026-07-29"),
           "q2": ("2026-01-31", "2026-04-30"), "q3": ("2025-11-02", "2026-01-30")}
FIRST_EPOCH, IN_PROGRESS = "2026-04-21", "2026-10-13"      # Jake's store: the first non-DefiLlama day is 2026-04-21


def _g(metric, days, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": "Pendle", "metric": metric, "value": 1000.0,
                         "source": source, "tier": 1})


def _groups(llama_days=None):
    """The D4-5 view of fees_usd (three legs) and the stored DefiLlama cross-check."""
    head = pd.concat([
        _g("fees_usd", pd.date_range("2025-10-01", "2026-04-20"), "defillama"),
        _g("fees_usd", pd.date_range(FIRST_EPOCH, "2026-10-12"),
           "pendle_api:epoch_spread[epoch 2026-04-21: pendle_epoch_market_fees_usd 42,000, spread evenly over its 14 "
           "days]"),
        _g("fees_usd", pd.date_range(IN_PROGRESS, "2026-10-27"),
           "defillama:in_progress_epoch[DefiLlama pendle-v2 (fees_usd_defillama) for the epoch from 2026-10-13]")],
        ignore_index=True)
    days = pd.date_range("2025-10-01", "2026-10-27") if llama_days is None else llama_days
    return {("Pendle", "fees_usd"): head, ("Pendle", "fees_usd_defillama"): _g("fees_usd_defillama", days, "defillama")}


@pytest.fixture
def bw(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_WINDOWS", dict(WINDOWS))
    monkeypatch.setattr(bw, "_LAST_GROUPS", _groups())
    monkeypatch.setitem(bw.GLOBALS, "short_days", 30)
    return bw


def _cells(bw, metric="fees_usd", label="Fees"):
    R = bw.Refs(50, 10, [])
    p = config.PROJECT_BY_NAME["Pendle"]
    out = {}
    for spec in bw._trajectory(R, metric, label):
        with bw._project_row("Pendle"):
            out[spec[0]] = (spec[1](5, p), spec[5]["note_fn"](p))
    return out


def test_the_comparison_basis_is_a_declared_per_series_entry():
    cb = config.comparison_basis("Pendle", "fees_usd")
    assert cb["metric"] == "fees_usd_defillama" and cb["basis"] == "defillama"
    assert cb["source"] == "DefiLlama pendle-v2 daily fees" and cb["reason"] and cb["headline_basis"]
    # the D4-5 handover it answers is declared with a composition change; the headline definition is untouched
    decl = config.declared_handover("Pendle", "fees_usd")
    assert not str(decl["composition_change"]).lower().startswith("none")
    assert config.EPOCH_PRIMARY["Pendle"]["series"]["fees_usd"]["daily"] == cb["metric"]
    assert config.comparison_basis("Pendle", "revenue_usd") is None          # declared per series, nothing implied


def test_a_stitch_inside_the_window_with_defillama_in_both_compares_on_defillama_only(bw):
    c = _cells(bw)
    for h, w in (("Fees Δ6m", "q2"), ("Fees Δ9m", "q3")):
        f, note = c[h]
        assert f.startswith("=IF(OR(") and '|fees_usd_defillama"' in f and '|fees_usd"' not in f, h
        assert "BLOCKED — unequal coverage" in f and f"{w}_covered_days" not in f    # the guard reads Data columns
        assert "Compared on ONE basis: DefiLlama pendle-v2 daily fees, both windows" in note
        assert "differs from the headline fees_usd figure" in note and "Pendle's own per-epoch fees" in note
        assert "2026-04-21" in note
    # the comparison is the per-covered-day rate on each side, the same rule as every flow Δ
    f6 = c["Fees Δ6m"][0]
    assert f6.count('|fees_usd_defillama"') >= 6
    # the stitch into the epoch in progress (2026-10-13) sits in Q0 and the 'now' window: Δ30d and Δ3m cross it too
    for h in ("Fees Δ30d", "Fees Δ3m"):
        f, note = c[h]
        assert '|fees_usd_defillama"' in f and '|fees_usd"' not in f and "2026-10-13" in note, h


def test_defillama_missing_in_one_window_keeps_na_with_the_reason(bw, monkeypatch):
    days = pd.date_range("2026-05-01", "2026-10-27").append(pd.date_range("2025-10-01", "2026-01-30"))  # no Q2
    monkeypatch.setattr(bw, "_LAST_GROUPS", _groups(days))
    c = _cells(bw)
    v, note = c["Fees Δ6m"]
    assert v.startswith("n/a — SERIES BREAK 2026-04-21") and "inside this comparison" in v
    assert "not formed on DefiLlama pendle-v2 daily fees: no fees_usd_defillama row in Q2" in v
    assert not v.startswith("=") and len(v) <= 250
    assert note.startswith("Not compared:") and "no fees_usd_defillama row in Q2" in note
    # Q3 is held: Δ9m still compares on DefiLlama
    assert '|fees_usd_defillama"' in c["Fees Δ9m"][0]


def test_a_window_not_on_the_one_basis_is_not_compared(bw, monkeypatch):
    g = _groups()
    ll = g[("Pendle", "fees_usd_defillama")]
    ll.loc[ll["date"] == pd.Timestamp("2026-02-10"), "source"] = "probe:fees_usd_defillama"
    monkeypatch.setattr(bw, "_LAST_GROUPS", g)
    v, _ = _cells(bw)["Fees Δ6m"]
    assert v.startswith("n/a — SERIES BREAK") and "not one basis (defillama, probe:fees_usd_defillama" in v


def test_without_a_declaration_the_stitch_stays_na(bw, monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_BASIS", {})
    c = _cells(bw)
    assert c["Fees Δ6m"][0].startswith("n/a — SERIES BREAK 2026-04-21") and c["Fees Δ6m"][1] == ""
    assert c["Fees Δ9m"][0].startswith("n/a — SERIES BREAK 2026-04-21")
    # Δ3m does not cross the first stitch: the headline series, as before
    assert '|fees_usd"' in c["Fees Δ3m"][0]


def test_a_series_with_no_stitch_in_the_window_is_unchanged(bw, monkeypatch):
    g = _groups()
    g[("Pendle", "fees_usd")] = _g("fees_usd", pd.date_range("2025-10-01", "2026-10-27"), "defillama")
    monkeypatch.setattr(bw, "_LAST_GROUPS", g)
    for h, (f, note) in _cells(bw).items():
        assert '|fees_usd"' in f and "fees_usd_defillama" not in f and note == "", h


def _master_fees(tmp_path, pl, rows) -> dict:
    """{header: (value via xlcalc, comment)} of Pendle's Master fee columns, built on a store of `rows` only."""
    import openpyxl
    import build_workbook as bw
    import store as sm
    import xlcalc
    from real_dump import to_store
    sys.modules.setdefault("recalc", None)
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "metrics.db"
    to_store(dict(pl, metrics=rows), db)
    was = sm.DB_PATH
    sm.DB_PATH = str(db)
    st = sm.Store(str(db))
    try:
        bw.build_workbook(st, tmp_path / "wb.xlsx", asof=pd.Timestamp(pl["meta"]["asof"]), only=["Pendle"],
                          reader_path=tmp_path / "reader.xlsx")
    finally:
        st.close()
        sm.DB_PATH = was
    wb = openpyxl.load_workbook(tmp_path / "wb.xlsx")
    X = xlcalc.Workbook(wb)
    ws = wb["Master"]
    hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
    col = {ws.cell(hr, c).value: c for c in range(1, ws.max_column + 1)}
    row = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == "Pendle")
    return {h: (X.value("Master", row, col[h]), (ws.cell(row, col[h]).comment or None) and
                ws.cell(row, col[h]).comment.text)
            for h in ("Fees Δ30d", "Fees Δ3m", "Fees Δ6m", "Fees Δ9m")}


def test_on_the_real_dump_the_master_delta_is_a_number_on_defillama(tmp_path):
    """The 10-13 Pendle dump as Jake's store holds it after D3-5 / D4-5: DefiLlama's daily fees under
    fees_usd_defillama, epochs from 2026-04-07 (inside Q-2). Before P2c-6 Δ6m / Δ9m read n/a — SERIES BREAK; now each is
    the per-covered-day DefiLlama comparison — equal to the figure the same rows gave as the headline series before
    D4-5 (DefiLlama only, no epoch)."""
    from real_dump import load
    pl = load("Pendle")
    rows = [dict(r, metric="fees_usd_defillama") if r["metric"] == "fees_usd" else r for r in pl["metrics"]]
    asof = pd.Timestamp(pl["meta"]["asof"])
    for st in pd.date_range("2026-04-07", asof - pd.Timedelta(days=14), freq="14D"):
        rows.append({"date": str(st.date()), "metric": "pendle_epoch_market_fees_usd", "value": 300_000.0,
                     "source": "pendle_api:statistics/market-fees", "tier": 3, "fetched_at": "2026-10-13T00:00:00Z"})
    after = _master_fees(tmp_path / "d45", pl, rows)
    llama_only = _master_fees(tmp_path / "llama", pl, pl["metrics"])
    for h in ("Fees Δ6m", "Fees Δ9m", "Fees Δ30d", "Fees Δ3m"):
        v, note = after[h]
        assert isinstance(v, float), (h, v)
        assert v == pytest.approx(llama_only[h][0], rel=1e-9), h
        assert note.startswith("Compared on ONE basis: DefiLlama pendle-v2 daily fees, both windows"), h


def test_the_guard_reads_these_master_columns_by_the_snapshot_key():
    """No ACCEPTED LOSS is recorded for these headlines: they are formed. Were one needed, the key the live guard and
    compare match is the snapshot's own "Master | <header>" (export_snapshot.snapshot_rows), and no allowance covers it
    today, so an unformed cell on Jake's store reports HEADLINE LOST rather than passing silently."""
    import inspect
    import export_snapshot as es
    assert 'put(f"Master | {h}"' in inspect.getsource(es.snapshot_rows)
    for h in ("Master | Fees Δ6m", "Master | Fees Δ9m"):
        assert es._headline_key(h) and not es.is_attribute(h)
        assert config.regression_allowance("Pendle", h, on="2026-10-28") is None
