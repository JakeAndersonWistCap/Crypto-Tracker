"""
tests/test_real_dumps.py — the 2026-10-09 items judged on JAKE'S OWN STORE (Jake's run 2026-10-09 ~10:15: "fixtures
passed, the real store did not"). Each test loads fixtures/real_<project>.json (python dump_fixture.py --project X,
committed) into a fresh store and runs credibility_report on it, as of the dump's date. Until a dump is committed its
test SKIPS — it never passes on a hand-built fixture.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from real_dump import evaluate_real, load, long_frame  # noqa: E402


def test_real_ethereum_net_change_is_judged_over_the_issuance_rows_of_the_common_days(tmp_path, monkeypatch):
    """1. a4_net_change: ours = our issuance rows of the common days (each row spans the days since the supply last
    moved), never the full Q0 sum."""
    o = evaluate_real(tmp_path, monkeypatch, "Ethereum")
    row = o["a4_net_change"]
    assert "OUR ISSUANCE ROWS" in row["note"], row["note"][:800]
    assert row["verdict"].startswith("PASS"), row
    # item 4 (Jake's run 2026-10-09 11:41): the 10-05 row spans 10-02..10-05 and so does its burn row — the judged figure
    # is the full covered period, the same as the A4 headline (28,722 over 10 days), not 6 single-day rows (17,022)
    assert "OURS, same 10 day(s)" in row["note"] and abs(float(row["ours"]) - 28_721.6) < 1, row


def test_real_chainlink_release_is_zero_and_a1_is_na(tmp_path, monkeypatch):
    """2a/b/c: the 27 wallets hold the same at both Q0 ends with no transfers — the release is 0 and PASSes; A1 is
    n/a by definition; a3_buyback_locked is recorded N/A."""
    o = evaluate_real(tmp_path, monkeypatch, "Chainlink")
    iss = o["in_issuance"]
    assert iss["ours"] is not None and abs(float(iss["ours"])) < 1.0, iss
    assert iss["verdict"].startswith("PASS"), iss
    assert o["a1_fees_issuance"]["verdict"].startswith("N/A"), o["a1_fees_issuance"]
    assert o["a3_buyback_locked"]["verdict"] == "N/A (recorded — judged by the exact-date row)"


def test_real_plume_circulating_has_a_figure(tmp_path, monkeypatch):
    """3: the confirmed anchor (supply.plume.org = 6,606,142,167, Jake 2026-10-09) — in_circ is judged, not
    'CHECK (no figure)'."""
    o = evaluate_real(tmp_path, monkeypatch, "Plume")
    assert o["in_circ"]["verdict"] != "CHECK (no figure)", o["in_circ"]


def test_real_aerodrome_price_finding_and_one_price_epoch_row(tmp_path, monkeypatch):
    """4a/b/d: AERO rose over Q0 -> the today's-price APR is a VERIFIED FINDING beside the headline; the per-epoch row
    uses one price on both sides; the epoch rows are PASS, or MATURING until 2026-10-10 while DefiLlama's last day is
    missing."""
    o = evaluate_real(tmp_path, monkeypatch, "Aerodrome")
    # Jake's price convention (2026-10-09): today's-price APR is recorded, not judged; the price itself is confirmed by
    # Coinbase on the 33 overlapping days (3a)
    assert o["in_apr_today_price"]["verdict"] == "N/A (recorded, not judged)", o["in_apr_today_price"]
    pc = o["in_price_confirmed"]
    assert pc["verdict"].startswith("PASS") and "33 of the" in pc["note"], pc
    # 3c: no on-chain epoch was stored (a lost row) — MATURING naming it, not "CHECK (no reference)"
    for k in ("in_voter_apr_epoch", "in_revenue"):
        assert o[k]["verdict"].startswith(("PASS", "MATURING")), o[k]
        if o[k]["verdict"].startswith("MATURING"):
            assert "2026-10-01" in o[k]["note"] and "on-chain" in o[k]["note"], o[k]["note"]


def test_real_pendle_headline_cell_equals_the_python(tmp_path, monkeypatch):
    """5a: the headline cell equals the Python computation of the same definition on the real rows. 1b: Pendle's
    observed publish lag is 7 days (2026-09-08, the latest complete epoch at our first read); 1d: the 2026-09-22 epoch,
    read at 0, stays unpublished until 10-13. 1c (Jake's decision 2026-10-09): Pendle's aprs are 0 for every epoch, so
    the epochs before the API's history take the CALIBRATED on-chain rebuild (active locked + 3 x vePENDLE, x the mean
    API/rebuild ratio over the overlap days) — the headline is formed over the five published epochs and is a
    DOCUMENTED LIMITATION (calibrated, not first-party)."""
    import credibility as cred
    o = evaluate_real(tmp_path, monkeypatch, "Pendle")
    payload = load("Pendle")
    long = long_frame(payload)
    s = lambda m: (long[long.metric == m].drop_duplicates("date", keep="last").set_index("date")["value"]  # noqa: E731
                   .astype(float).sort_index())
    asof = pd.Timestamp(payload["meta"]["asof"])
    lo, hi = cred._q0(asof)
    lag, _ = cred.epoch_publish_lag("Pendle", db=tmp_path / "metrics.db")
    assert lag == 7
    cal = cred.pendle_calibration("Pendle", long.assign(project="Pendle"))
    assert cal is not None and abs(cal["ratio"] - 1 / 1.0138) < 0.001, cal and cal["label"]
    assert not cal["drift"] and len(cal["days"]) >= 8, cal["label"]
    assert "calibrated on-chain rebuild" in cal["label"] and "range" in cal["label"]
    tab = cred.epoch_apr_table(s("pendle_distributed_tokens"), s("pendle_epoch_apr_published"),
                               {m: s(m) for m in ("locked_tokens_shares", "locked_tokens_virtual")}, lo, hi, 14, 7, lag,
                               fallback={"locked_tokens_virtual": (cal["calibrated"], cal["label"])})
    assert [r["source"] for r in tab if r["date"] == pd.Timestamp("2026-09-22")] == ["unpublished"]
    rebuilt = [r for r in tab if r["source"] == "calibrated on-chain rebuild"]
    assert len(rebuilt) == 5 and all(r["apr"] for r in rebuilt), tab
    got = cred.epoch_headline(tab)
    assert got is not None and got[1] == 5
    head = o["a3_protocol_yield"]
    assert abs(float(head["ours"]) - got[0]) < 1e-9, (head, got)
    assert head["verdict"] == "DOCUMENTED LIMITATION (inputs)", head
    fp = o["in_epochs_first_party"]
    assert fp["verdict"] == "DOCUMENTED LIMITATION" and float(fp["ours"]) == 5, fp
    assert "calibrated, not first-party" in fp["note"] and "CALIBRATION" in fp["note"], fp["note"]
    assert o["in_epoch_apr"]["verdict"].startswith("PASS"), o["in_epoch_apr"]


def test_real_maple_stays_signed_off(tmp_path, monkeypatch):
    """6: the wording changed, the verdicts must not: no CHECK on Maple."""
    o = evaluate_real(tmp_path, monkeypatch, "Maple")
    bad = {k: v["verdict"] for k, v in o.items() if str(v["verdict"]).startswith("CHECK")}
    assert not bad, bad


def _pendle_rebuild_inputs():
    import credibility as cred
    payload = load("Pendle")
    long = long_frame(payload).assign(project="Pendle")
    g = long[long.metric == "vependle_voting_supply_tokens"].copy()
    g["date"] = pd.to_datetime(g["date"]).dt.normalize()
    s = lambda m: (long[long.metric == m].assign(date=lambda d: pd.to_datetime(d["date"]).dt.normalize())  # noqa: E731
                   .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index())
    return cred, s, dict(zip(g["date"], g["source"])), dict(zip(g["date"], g["fetched_at"]))


def test_real_pendle_rebuild_takes_the_archive_to_live_fall_over_its_real_gap():
    """3 (Jake's run 2026-10-09 ~14:10): on the real rows the 10-08 read is archive (00:00) and the 10-09 read live
    (10:41) — 34.7h apart. The fall is scaled to 24h, active stays under all PENDLE locked, and no day drifts."""
    cred, s, src, at = _pendle_rebuild_inputs()
    cal = cred.virtual_rebuild(s("vependle_voting_supply_tokens"), src, s("locked_tokens_virtual"), 728, 0.005,
                               legacy=s("locked_tokens_legacy_vependle"), ve_at=at)
    d = pd.Timestamp("2026-10-09")
    assert d in cal["active"].index and cal["active"].loc[d] < s("locked_tokens_legacy_vependle").loc[d]
    assert abs(cal["active"].loc[d] / cal["active"].loc[pd.Timestamp("2026-10-08")] - 1) < 0.01, cal["active"].tail()
    assert not cal["drift"] and not cal["skipped"], (cal["drift"], cal["skipped"])


def test_real_pendle_rebuild_skips_a_day_whose_reads_are_under_20h_apart_or_exceed_all_locked():
    """3: the same real rows with the 10-08 read moved to a live read at 23:30 (under 20h before 10-09's) — 10-09 is skipped,
    not rebuilt and not judged; and a day whose active locked would exceed all PENDLE locked is skipped too."""
    cred, s, src, at = _pendle_rebuild_inputs()
    d8, d9 = pd.Timestamp("2026-10-08"), pd.Timestamp("2026-10-09")
    src2 = {**src, d8: "chain:ethereum:vependle_voting_supply"}
    at2 = {**at, d8: "2026-10-08T23:30:00Z"}
    cal = cred.virtual_rebuild(s("vependle_voting_supply_tokens"), src2, s("locked_tokens_virtual"), 728, 0.005,
                               legacy=s("locked_tokens_legacy_vependle"), ve_at=at2)
    assert d9 not in cal["rebuilt"].index and d9 not in [d for d, *_r in cal["days"]]
    assert any(d == d9 and "apart" in w and "under 20h" in w for d, w in cal["skipped"]), cal["skipped"]
    assert not any(d == d9 for d, _r in cal["drift"])
    leg = s("locked_tokens_legacy_vependle").copy()
    leg.loc[d9] = 50e6                                     # below that day's active locked (~61.4M)
    cal2 = cred.virtual_rebuild(s("vependle_voting_supply_tokens"), src, s("locked_tokens_virtual"), 728, 0.005,
                                legacy=leg, ve_at=at)
    assert d9 not in cal2["rebuilt"].index
    assert any(d == d9 and "impossible" in w for d, w in cal2["skipped"]), cal2["skipped"]
