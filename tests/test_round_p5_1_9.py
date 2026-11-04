"""P5-1.9 (Jake 2026-11-04): Ethereum settlement without Artemis, in his order — (1) the rebuild extended back to at
least 2025-05-01, dry-run first; (2) validated against the Artemis CSV on 2026-05-28..08-25, used only within +-1%;
(3) only then Artemis removed; (4) else the fallback, its headlines listed and asked. These tests cover (1) and (2):
settlement_extend.py. No network: BigQuery is stubbed."""
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import settlement_extend as se  # noqa: E402

DAYS = pd.date_range("2026-05-28", "2026-08-25")


def _legs(scale=1.0, drop=()):
    dex = pd.Series(2e9, index=DAYS)
    nft = pd.Series(1e7, index=DAYS)
    p2p = pd.Series(3e9 * scale, index=DAYS).drop([pd.Timestamp(d) for d in drop])
    csv = pd.Series(5.01e9, index=DAYS)
    return dex, nft, p2p, csv


def test_the_rebuild_is_adopted_only_within_one_percent_on_every_overlap_day():
    ok = se.overlap_check(*_legs())
    assert ok["status"] == "PASS" and ok["days_with_both"] == 90 and abs(ok["ratio_minus_1"]) < 0.01
    off = se.overlap_check(*_legs(scale=1.0227))                 # +6.8%-style miss, as Jake's G9 run measured
    assert off["status"] == "NOT PASSED" and "outside +-1%" in off["why"]
    edge = se.overlap_check(*_legs(scale=1.0175))                # +1.05%
    assert edge["status"] == "NOT PASSED", "the bound is not loosened"


def test_a_missing_overlap_day_is_not_a_pass_and_is_named():
    res = se.overlap_check(*_legs(drop=("2026-06-10",)))
    assert res["status"] == "NOT PASSED" and "only 89 of 90 days" in res["why"]
    assert res["missing_days"] == {"p2p": ["2026-06-10"]}


def _db(tmp_path, rows):
    import store as sm
    db = tmp_path / "metrics.db"
    sm.Store(str(db)).close()
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO metrics (date, project, metric, value, source, tier, fetched_at) VALUES "
                    "(?, 'Ethereum', ?, ?, ?, 1, 'x')", rows)
    con.commit()
    con.close()
    return db


def test_validate_reads_the_store_read_only_and_writes_its_verdict(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(se, "RESULT", tmp_path / "data" / "ethereum" / "settlement_overlap.json")
    monkeypatch.setattr(se, "ROOT", tmp_path)
    rows = []
    for d in DAYS:
        ds = str(d.date())
        rows += [(ds, "dex_volume_usd", 2e9, "defillama:dexs:ethereum"), (ds, "nft_volume_usd", 1e7, "defillama:nft"),
                 (ds, "p2p_transfer_volume_usd", 3e9, "bigquery:p2p"),
                 (ds, "settlement_volume_usd", 5.01e9, "artemis:settlement_volume (CSV export, Jake, 2026-09-30)")]
    db = _db(tmp_path, rows)
    before = db.read_bytes()
    assert se.cmd_validate(db) == 0
    assert db.read_bytes() == before, "read-only"
    out = capsys.readouterr().out
    assert "SETTLEMENT OVERLAP 2026-05-28..2026-08-25: PASS" in out
    assert json.loads(se.RESULT.read_text())["status"] == "PASS"


def test_backfill_refuses_until_the_overlap_passed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(se, "RESULT", tmp_path / "settlement_overlap.json")
    monkeypatch.setattr(se, "ROOT", tmp_path)
    assert se.cmd_backfill("2025-05-01", apply=True) == 3
    assert "REFUSED" in capsys.readouterr().out
    se.RESULT.write_text(json.dumps({"status": "NOT PASSED", "why": "+6.80%"}))
    assert se.cmd_backfill("2025-05-01", apply=True) == 3


def test_the_dry_run_prices_every_weekly_piece_before_the_stored_history_and_reads_nothing(monkeypatch, capsys):
    from fetch import eth_settlement as es
    asked, ran = [], []

    class R(es.EthP2PBigQuery):
        def _client(self, spec):
            return object(), None

        def _load(self):
            return {"ledger": {}, "eth_p2p": {"days": {}}}

        def _dry(self, client, sql, params, spec=None):
            asked.append((params["d0"], params["d1"]))
            return 13_000_000_000

        def _run(self, *a, **k):
            ran.append(a)
            raise AssertionError("a dry run never runs a query")

        def _quota_line(self, bq, st):
            return "quota: test"
    monkeypatch.setattr(es, "EthP2PBigQuery", R)
    monkeypatch.setattr(se, "_p2p_reader", lambda: (R(), es._spec(), R()._bq_spec(), None,
                                                   pd.Timestamp("2026-11-04")))
    assert se.cmd_dry_run("2025-05-01") == 0
    out = capsys.readouterr().out
    assert asked[0][0] == pd.Timestamp("2025-05-01").date(), "starts on the date asked"
    assert all(pd.Timestamp(a) < pd.Timestamp("2026-05-28") for a, _b in asked), "only weeks with a day not stored yet"
    assert len(asked) >= 55 and not ran
    assert f"TOTAL {len(asked) * 13:,.1f} GB" in out


def test_the_dex_and_nft_legs_keep_their_whole_history():
    for m in ("dex_volume_usd", "nft_volume_usd"):
        assert ("Ethereum", m) in config.FULL_HISTORY_SERIES
    from fetch import base
    df = pd.DataFrame({"date": pd.to_datetime(["2025-05-01", "2026-11-03"]), "project": "Ethereum",
                       "metric": "dex_volume_usd", "value": [1.0, 2.0], "source": "defillama:dexs:ethereum"})
    assert len(base.window(df, 30)) == 2


@pytest.mark.parametrize("mode", ["validate", "dry-run", "backfill"])
def test_the_cli_takes_the_three_modes(mode):
    import argparse
    with pytest.raises(SystemExit):
        se.main([mode, "--help"])
    assert argparse


def test_the_runbook_orders_validate_before_the_dry_run_and_the_backfill():
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = text[text.index("## 11ea."):text.index("## 11dz.")]
    i, j, k = (sec.index(c) for c in ("settlement_extend.py validate", "settlement_extend.py dry-run --from 2025-05-01",
                                      "settlement_extend.py backfill --from 2025-05-01"))
    assert i < j < k and "Nothing Artemis is removed yet" in sec and "PASTE BACK" in sec
