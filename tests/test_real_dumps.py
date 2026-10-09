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
    read at 0, stays unpublished until 10-13. 1a/1c: with no per-epoch APR from Pendle and no stake of ours before
    2026-09-29, the headline is not formed — a CHECK naming why, never a number over a subset."""
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
    tab = cred.epoch_apr_table(s("pendle_distributed_tokens"), s("pendle_epoch_apr_published"),
                               {m: s(m) for m in ("locked_tokens_shares", "locked_tokens_virtual")}, lo, hi, 14, 7, lag)
    assert [r["source"] for r in tab if r["date"] == pd.Timestamp("2026-09-22")] == ["unpublished"]
    got = cred.epoch_headline(tab)
    head = o["a3_protocol_yield"]
    if got is None:
        assert not isinstance(head["ours"], (int, float)), head
        assert o["in_epochs_first_party"]["verdict"].startswith("CHECK"), o["in_epochs_first_party"]
    else:
        assert abs(float(head["ours"]) - got[0]) < 1e-9, (head, got)


def test_real_maple_stays_signed_off(tmp_path, monkeypatch):
    """6: the wording changed, the verdicts must not: no CHECK on Maple."""
    o = evaluate_real(tmp_path, monkeypatch, "Maple")
    bad = {k: v["verdict"] for k, v in o.items() if str(v["verdict"]).startswith("CHECK")}
    assert not bad, bad
