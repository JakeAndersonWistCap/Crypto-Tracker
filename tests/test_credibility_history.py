"""Item 2 of Jake's fine-tuning (2026-10-11): each run's verdict per Credibility row is kept, and a report gives per
row the flips in the last 7 / 14 / 28 days, mean and max |gap|, and the rows whose verdict changed since the previous
run. Read-only on the verdict logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import credibility_history as ch


def _run(path, run_id, when, verdicts):
    rows = [{"project": "Morpho", "id": "in_interest_day", "what": "borrower interest"},
            {"project": "Maple", "id": "in_circ", "what": "circulating"}]
    tab = [[None] * 4 + [100.0, None, 91.0, None, (100 - 91) / 91, 0.10, verdicts[0]],
           [None] * 4 + [95.6, None, 100.0, None, -0.044, 0.05, verdicts[1]]]
    return ch.record(rows, tab, run_id, ts=when.isoformat(timespec="seconds"), path=path)


def test_history_records_each_run_and_reports_flips_gaps_and_changes(tmp_path):
    db = tmp_path / "h.sqlite"
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    seq = [(25, "PASS", "PASS"), (10, "CHECK", "PASS (NEAR LIMIT)"), (5, "PASS", "PASS (NEAR LIMIT)"),
           (1, "PASS (NEAR LIMIT)", "CHECK")]
    for k, (ago, morpho, maple) in enumerate(seq):
        assert _run(db, f"run{k}", now - timedelta(days=ago), (morpho, maple)) == 2
    out = "\n".join(ch.report(now=now, path=db))
    morpho = next(ln for ln in out.splitlines() if "in_interest_day" in ln and "Morpho" in ln and "->" not in ln)
    # PASS->CHECK at -10d, CHECK->PASS at -5d; PASS -> PASS (NEAR LIMIT) at -1d is NOT a flip
    assert morpho.split()[2:5] == ["1", "2", "2"], morpho
    assert "9.89%" in morpho and "10%" in morpho, morpho                   # |gap| 9/91 mean = max
    maple = next(ln for ln in out.splitlines() if "in_circ" in ln and "->" not in ln)
    assert maple.split()[2:5] == ["1", "1", "1"], maple                     # only the last run's -> CHECK
    changed = out.split("CHANGED VERDICT SINCE THE PREVIOUS RUN")[1]
    assert "run2 -> run3): 1" in changed and "Maple" in changed and "-> CHECK" in changed and "Morpho" not in changed


def test_a_rerecorded_run_replaces_its_rows(tmp_path):
    db = tmp_path / "h.sqlite"
    t = datetime(2026, 10, 30, tzinfo=timezone.utc)
    _run(db, "r1", t, ("PASS", "PASS"))
    _run(db, "r1", t, ("CHECK", "PASS"))
    con = ch.connect(db)
    assert con.execute("SELECT count(*), max(verdict) FROM credibility_history WHERE row_key='in_interest_day'"
                       ).fetchone() == (1, "CHECK")


def test_record_flag_evaluates_and_records_the_latest_run(tmp_path, monkeypatch, capsys):
    import credibility_report
    rows = [{"project": "Sky", "id": "in_x", "what": "x"}]
    tab = [[None] * 4 + [1.0, None, 1.0, None, 0.0, 0.01, "PASS"]]
    monkeypatch.setattr(credibility_report, "evaluate", lambda *a, **k: (rows, tab))
    monkeypatch.setattr(ch, "latest_run_id", lambda: "20261011T060000Z")
    db = tmp_path / "h.sqlite"
    assert ch.main(["--record", "--db", str(db)]) == 0
    con = ch.connect(db)
    assert con.execute("SELECT run_id, project, row_key, gap, tolerance, verdict FROM credibility_history").fetchall() \
        == [("20261011T060000Z", "Sky", "in_x", 0.0, 0.01, "PASS")]
    assert "recorded run 20261011T060000Z: 1 row(s)" in capsys.readouterr().out
