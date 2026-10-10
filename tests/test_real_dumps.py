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
        # in_revenue: a VERIFIED FINDING once the fee leg is stored (Jake's run 2026-10-09 18:13)
        assert o[k]["verdict"].startswith(("PASS", "MATURING", "VERIFIED FINDING")), o[k]
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


def test_real_aerodrome_illiquid_cap_fee_leg_finding_and_tolerances(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 18:13 on the evening dump (every Q0 epoch stored by --seed aero_epochs):
    - the illiquid-token rule excludes $6,950,759 for 09-03 (LAPTOP $7,546,432 quoted vs $595,818 depth) and $434,141
      for 09-24 (XDP); the headline falls from 17.4% to ~12.1% and the labelled line carries the exclusion;
    - the like-for-like rows are judged at +/-10% (the tolerance was lost: CHECK at -5.9% / -5.6% with a blank column);
    - in_revenue is a VERIFIED FINDING: DefiLlama's fee leg sums below on-chain, the on-chain figure is the reference;
    - after the rule no epoch is an outlier."""
    o = evaluate_real(tmp_path, monkeypatch, "Aerodrome")
    long = long_frame(load("Aerodrome"))
    ib = long[long.metric == "voter_rewards_illiquid_bribes_usd"].set_index("date")["value"]
    assert round(float(ib[pd.Timestamp("2026-09-03")])) == 6_950_614 and round(float(ib[pd.Timestamp("2026-09-24")])) == 433_484
    hr = long[long.metric == "holders_revenue_usd"].set_index("date")["value"]
    assert float(hr[pd.Timestamp("2026-09-09")]) == 7_922_809.0          # DefiLlama's hand-priced LAPTOP day
    head = o["a3_protocol_yield"]
    assert 0.115 < float(head["ours"]) < 0.125, head
    ep = o["in_voter_apr_epoch"]
    assert ep["verdict"].startswith("PASS"), ep
    assert "illiquid-token rewards excluded: $" in ep["note"] and "before the exclusion" in ep["note"], ep["note"][:600]
    rv = o["in_revenue"]
    assert rv["verdict"] == "VERIFIED FINDING", rv
    assert "THE ON-CHAIN FIGURE IS THE REFERENCE" in rv["note"] and "FEE LEG over" in rv["note"], rv["note"][:600]
    assert "2026-10-01 -37.4%" in rv["note"] and "2026-08-20 -21.5%" in rv["note"], rv["note"]
    assert o["in_bribe_outliers"]["verdict"] == "N/A (recorded, not judged)", o["in_bribe_outliers"]
    # today's-price APR on the SAME capped flow (was 12.2% beside "the headline 17.55%")
    tp = o["in_apr_today_price"]
    assert abs(float(tp["ours"]) - 0.0852) < 0.0005 and f"headline at payment-day prices: {float(head['ours']):.2%}" in \
        tp["note"], tp


def test_real_every_judged_row_has_a_tolerance(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 18:13: two Aerodrome rows read CHECK at -5.9% / -5.6% with a BLANK tolerance — a row inside a
    `verdict_when` took the outer spec's tol. On every real dump, every row judged against a reference carries one."""
    import shutil
    from build_workbook import CREDIBILITY_ROWS
    bad = []
    for p in ("Aerodrome", "Pendle", "Ethereum", "Chainlink", "Maple", "Plume"):
        d = tmp_path / p
        d.mkdir()
        evaluate_real(d, monkeypatch, p)
        bad += [f"{p}/{r['id']}" for r in CREDIBILITY_ROWS if r["project"] == p and r.get("verdict") is None
                and r.get("mode") in ("independent", "same_source") and r.get("tol") is None]
        shutil.rmtree(d)
    assert not bad, bad


def test_real_pendle_optimism_zero_counts_with_its_evidence(tmp_path, monkeypatch):
    """Jake's check 2026-10-09 17:05: the Optimism gauge's last token transfer was 743 days ago — Optimism = 0, stored
    as manual rows with that evidence (manual_overrides.csv). On the real Pendle dump with those rows, Optimism is no
    longer named missing on either side of in_emissions; with Arbitrum reconciled (evening dump) the row passes.""" 
    import csv
    payload = load("Pendle")
    with open(Path(__file__).resolve().parents[1] / "manual_overrides.csv", newline="") as fh:
        rows = [r for r in csv.DictReader(line for line in fh if not line.startswith("#"))
                if r["project"] == "Pendle" and "optimism" in r["metric"]]
    assert {r["metric"] for r in rows} == {"emissions_tokens_gauge_optimism", "emissions_tokens_gauge_optimism_direct"}
    assert all(float(r["value"]) == 0 and "read_by Jake" in r["source_note"] and "pending Jake's review" in
               r["source_note"] and "optimistic.etherscan.io" in r["source_note"] for r in rows)
    have = {(r["date"][:10], r["metric"]) for r in payload.get("manual_overrides") or []}
    payload = {**payload, "manual_overrides": list(payload.get("manual_overrides") or [])
               + [r for r in rows if (r["date"][:10], r["metric"]) not in have]}
    o = evaluate_real(tmp_path, monkeypatch, "Pendle", payload=payload)
    em = o["in_emissions"]
    # the evening dump (Jake's run 2026-10-09 18:13): Arbitrum reconciled too, and the sum is formed over all three
    assert "NONE STORED" not in em["note"] and "Optimism 0.00" in em["note"], em["note"][:600]
    assert em["verdict"].startswith("PASS") and "= 83,731.84 PENDLE" in em["note"], em


def test_real_aerodrome_buyback_is_the_foundation_lock_and_implied_stays_zero(tmp_path, monkeypatch):
    """External audit 2026-10-09 item 1 + Jake's retirement rule (2026-10-10), on the real dump: the implied (fee-funded)
    buyback stays N/A by design (fees buy nothing), while the actual buyback is the Foundation's buy-and-lock — a
    DOCUMENTED LIMITATION (state read; Base logs are paid), no longer a declared zero — and the retirement rates judge
    it rather than reading a structural 0."""
    o = evaluate_real(tmp_path, monkeypatch, "Aerodrome")
    assert o["a3_implied_buyback_pct"]["verdict"] == "N/A", o["a3_implied_buyback_pct"]
    assert o["in_buyback"]["verdict"].startswith("DOCUMENTED LIMITATION"), o["in_buyback"]
    for hid in ("a3_circ_retirement", "a3_actual_buyback_pct", "a3_net_absorption"):
        assert not o[hid]["verdict"].startswith("N/A"), (hid, o[hid])
        assert "in_buyback" in o[hid]["note"], (hid, o[hid])
    assert "foundation_locked_aero_tokens" in o["a3_buyback_locked"]["note"], o["a3_buyback_locked"]
