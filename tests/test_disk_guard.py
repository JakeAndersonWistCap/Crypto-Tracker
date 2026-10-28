"""P2f-4: the run and the full suite stop early, in one line, when the disk is too full; the safe-to-delete list
never names the store, its backups, the scan cache, data/ or the newest snapshots. Fakes only."""
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import disk_guard as dg

GB = 1024 ** 3


def _free(monkeypatch, gb):
    monkeypatch.setattr(dg, "free_gb", lambda p: gb)


def test_the_message_is_one_line_with_both_numbers(monkeypatch):
    _free(monkeypatch, 1.7)
    msg = dg.check([dg.ROOT], 3.0, "full suite, 1870 tests")
    assert msg.startswith("only 1.7 GB free on ") and "need 3.0 GB (full suite, 1870 tests)" in msg
    assert "\n" not in msg and msg.endswith("python disk_guard.py --list")
    _free(monkeypatch, 3.0)
    assert dg.check([dg.ROOT], 3.0, "x") is None                         # exactly enough is enough


def test_the_fullest_drive_is_the_one_named(monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: 0.5 if str(p) == "D:/tmp" else 2.0)
    msg = dg.check(["C:/repo", "D:/tmp"], 3.0, "x")
    assert "only 0.5 GB free on D:/tmp" in msg


def test_the_run_need_is_one_gb_plus_twice_the_store(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_METRICS_MIN_FREE_GB", raising=False)
    db = tmp_path / "metrics.db"
    with open(db, "wb") as f:
        f.truncate(GB // 2)                                              # a sparse 0.5 GB store
    assert dg.run_need_gb(db) == pytest.approx(2.0)
    assert dg.run_need_gb(tmp_path / "none.db") == 1.0                   # no store yet
    monkeypatch.setenv("TOKEN_METRICS_MIN_FREE_GB", "7")
    assert dg.run_need_gb(db) == 7.0


def test_a_short_disk_stops_token_metrics_before_anything_starts(monkeypatch, caplog):
    import token_metrics as tm
    _free(monkeypatch, 0.2)
    started = []
    monkeypatch.setattr(tm, "_main", lambda *a, **k: started.append(1) or 0)
    assert tm.main(["--no-fetch"]) == dg.EXIT_DISK == 74
    assert not started
    lines = [r.getMessage() for r in caplog.records if "only 0.2 GB free" in r.getMessage()]
    assert len(lines) == 1 and "need " in lines[0]            # one line, on the run's own log (stdout in a real run)


def test_a_short_disk_stops_the_backfill_run_but_not_a_dry_look(monkeypatch, capsys, tmp_path):
    import archive_backfill as ab
    _free(monkeypatch, 0.2)
    assert ab.main(["--db", str(tmp_path / "m.db"), "--run"]) == 74
    assert capsys.readouterr().out.count("only 0.2 GB free") == 1


def test_the_suite_check_stops_a_full_collection_and_lets_a_small_one_through(monkeypatch):
    import conftest
    _free(monkeypatch, 1.0)
    full = SimpleNamespace(items=[object()] * 400)
    with pytest.raises(pytest.exit.Exception) as e:
        conftest.pytest_collection_finish(full)
    assert "only 1.0 GB free" in str(e.value) and "full suite, 400 tests" in str(e.value)
    conftest.pytest_collection_finish(SimpleNamespace(items=[object()] * 5))      # needs 0.3 GB: fine
    _free(monkeypatch, 5.0)
    conftest.pytest_collection_finish(full)


def _tree(root: Path):
    (root / "snapshots").mkdir()
    for d in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"):
        (root / "snapshots" / f"snapshot_{d}.csv").write_text("x" * 100)
    (root / "logs").mkdir()
    old, new = root / "logs" / "run_2026-08-01.log", root / "logs" / "run_2026-10-28.log"
    old.write_text("old")
    new.write_text("new")
    t = time.time() - 60 * 86400
    os.utime(old, (t, t))
    (root / "logs" / "daily_run_state.json").write_text("{}")
    (root / "tests" / "_scratch").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / "metrics.db").write_text("store")
    (root / "metrics.db.credibility.pkl").write_text("cache")
    (root / "metrics.db.bak-p2e").write_text("backup")
    (root / ".cache").mkdir()
    (root / "data").mkdir()
    (root / "manual_overrides.csv").write_text("m")
    (root / ".claude" / "worktrees" / "a" / "__pycache__").mkdir(parents=True)


def test_the_safe_set_keeps_the_store_the_cache_data_and_the_newest_three_snapshots(tmp_path):
    _tree(tmp_path)
    names = {str(p.relative_to(tmp_path)).replace("\\", "/") for p, _ in dg.safe_to_delete(tmp_path)}
    assert names == {"snapshots/snapshot_2026-10-20.csv", "snapshots/snapshot_2026-10-21.csv",
                     "logs/run_2026-08-01.log", "tests/_scratch", "__pycache__", "metrics.db.credibility.pkl"}
    mine = {str(p.relative_to(tmp_path)) for p, _ in dg.yours_to_decide(tmp_path)}
    assert mine == {"metrics.db.bak-p2e", ".cache"}                      # listed for Jake, never in the safe set


def test_clean_deletes_nothing_without_yes_and_exactly_the_safe_set_with_it(tmp_path, monkeypatch, capsys):
    _tree(tmp_path)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    assert dg.main(["--clean"]) == 2
    assert (tmp_path / "snapshots" / "snapshot_2026-10-20.csv").exists()
    dg.clean(tmp_path)
    left = sorted(p.name for p in (tmp_path / "snapshots").iterdir())
    assert left == ["snapshot_2026-10-22.csv", "snapshot_2026-10-23.csv", "snapshot_2026-10-24.csv"]
    for keep in ("metrics.db", "metrics.db.bak-p2e", ".cache", "data", "manual_overrides.csv",
                 "logs/run_2026-10-28.log", "logs/daily_run_state.json"):
        assert (tmp_path / keep).exists(), keep
    assert not (tmp_path / "logs" / "run_2026-08-01.log").exists() and not (tmp_path / "__pycache__").exists()
    capsys.readouterr()
    dg.list_report(tmp_path)
    out = capsys.readouterr().out
    assert "SAFE TO DELETE" in out and "YOURS TO DECIDE" in out and "metrics.db.bak-p2e" in out
    assert "python disk_guard.py --clean --yes" in out and "NEVER: metrics.db" in out
