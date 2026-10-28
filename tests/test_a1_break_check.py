"""P2f-5b: a1_break_check.py — would a change of measuring point inside A1's one-year key span block the key?

The script is read-only and behaviour-neutral (it implements nothing in reader.py). Tested on fakes (no build): one
project with a change inside the span reads WOULD BLOCK, one outside OK, one with a DECLARED handover OK and says so; and
once on the committed Ethereum dump (store opened read-only, byte-identical afterwards, no network).
"""
from __future__ import annotations

import hashlib
import socket
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import a1_break_check as abc  # noqa: E402
import config  # noqa: E402

END = pd.Timestamp("2026-10-12")
SETTLE, CIRC = "settlement_volume_365d_usd", "circulating_supply_used"


def _groups(settle_src=None, circ_src=None, days: int = 365 + 30):
    """Fake groups for project X: `settle_src` / `circ_src` map a first date to the source from that date on."""
    idx = pd.date_range(end=END, periods=days)

    def frame(srcs, default):
        srcs = srcs or {}
        src = [default] * len(idx)
        for since, s in sorted(srcs.items()):
            src = [s if d >= pd.Timestamp(since) else x for d, x in zip(idx, src)]
        return pd.DataFrame({"date": idx, "value": [1e9 + i for i in range(len(idx))], "source": src})
    return {("X", SETTLE): frame(settle_src, "artemis:x"), ("X", CIRC): frame(circ_src, "chain:x")}


def test_a_change_inside_the_span_would_block_and_names_date_and_sources():
    g = _groups(settle_src={"2026-06-01": "artemis:y"})
    r = abc.check_project(g, "X")
    assert r["verdict"] == "WOULD BLOCK"
    assert r["then"] == END - pd.Timedelta(days=365) and r["end"] == END
    [b] = r["breaks"]
    assert b["date"] == pd.Timestamp("2026-06-01") and b["metric"] == SETTLE
    assert b["before"] == "artemis:x" and b["after"] == "artemis:y"
    assert "artemis:x" in b["stored_before"] and "2026-05-31" in b["stored_before"]
    assert "artemis:y" in b["stored_on"] and "2026-06-01" in b["stored_on"]
    text = "\n".join(abc.render(r))
    assert "WOULD BLOCK" in text and "2026-06-01" in text and "artemis:x" in text and "artemis:y" in text
    assert "no declared handover" in text


def test_a_change_of_the_circulating_input_counts_too():
    g = _groups(circ_src={"2026-03-15": "chain:z"})
    r = abc.check_project(g, "X")
    assert r["verdict"] == "WOULD BLOCK" and r["breaks"][0]["metric"] == CIRC


def test_a_change_outside_the_span_is_ok():
    g = _groups(settle_src={"2025-09-20": "artemis:y"}, days=365 + 90)   # span starts 2025-10-12; change is a month earlier
    r = abc.check_project(g, "X")
    assert r["verdict"] == "OK" and not r["breaks"]
    assert "no change of measuring point inside the span" in "\n".join(abc.render(r))


def test_the_day_the_earlier_value_is_read_is_outside_the_span():
    then = END - pd.Timedelta(days=365)
    on = abc.check_project(_groups(settle_src={str(then.date()): "artemis:y"}), "X")
    after = abc.check_project(_groups(settle_src={str((then + pd.Timedelta(days=1)).date()): "artemis:y"}), "X")
    assert on["verdict"] == "OK"                    # both values are on the new basis
    assert after["verdict"] == "WOULD BLOCK"        # the earlier value is on the old one


def test_a_declared_handover_is_ok_and_says_so(monkeypatch):
    monkeypatch.setitem(config.PROJECT_BY_NAME, "X", {"name": "X", "series_handover": {
        SETTLE: {"ordered_points": ("artemis:x", "artemis:y"), "why": "test", "composition_change": "none: same quantity",
                 "declared": "2026-10-29"}}})
    r = abc.check_project(_groups(settle_src={"2026-06-01": "artemis:y"}), "X")
    assert r["verdict"] == "OK" and not r["breaks"]
    [d] = r["declared_ok"]
    assert d["date"] == pd.Timestamp("2026-06-01") and d["metric"] == SETTLE
    text = "\n".join(abc.render(r))
    assert "DECLARED handover" in text and "recognised, not a break" in text and "artemis:y" in text


def test_a_declared_handover_that_steps_still_blocks_and_says_why(monkeypatch):
    monkeypatch.setitem(config.PROJECT_BY_NAME, "X", {"name": "X", "series_handover": {
        SETTLE: {"ordered_points": ("artemis:x", "artemis:y"), "why": "test",
                 "composition_change": "a DIFFERENT MEASURING POINT from 2026-06-01"}}})
    r = abc.check_project(_groups(settle_src={"2026-06-01": "artemis:y"}), "X")
    assert r["verdict"] == "WOULD BLOCK"
    assert "declares a STEP" in "\n".join(abc.render(r))


def test_a_mismatched_declaration_is_named_not_recognised(monkeypatch):
    monkeypatch.setitem(config.PROJECT_BY_NAME, "X", {"name": "X", "series_handover": {
        SETTLE: {"ordered_points": ("artemis:x[note]", "artemis:y"), "why": "test", "composition_change": "none"}}})
    r = abc.check_project(_groups(settle_src={"2026-06-01": "artemis:y"}), "X")
    assert r["verdict"] == "WOULD BLOCK" and "NOT RECOGNISED" in "\n".join(abc.render(r))


def test_no_key_series_is_ok_nothing_to_block():
    r = abc.check_project({}, "X")
    assert r["verdict"] == "OK" and "nothing to block" in "\n".join(abc.render(r))


# ---- the committed Ethereum dump, through the script's own entry point -------------------------------------------
def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_script_on_the_ethereum_dump_is_read_only_offline_and_reports_the_2026_09_29_break(tmp_path, monkeypatch, capsys):
    from real_dump import load, to_store
    db = tmp_path / "metrics.db"
    to_store(load("Ethereum"), db)
    before, names = _digest(db), sorted(p.name for p in tmp_path.iterdir())

    def no_network(*a, **k):
        raise AssertionError("a1_break_check opened a socket")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    rc = abc.main(["--store", str(db), "--project", "Ethereum"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _digest(db) == before and sorted(p.name for p in tmp_path.iterdir()) == names      # nothing written
    assert "=== Ethereum (primary A1) ===" in out
    assert "verdict (proposed rule): WOULD BLOCK" in out
    assert "2026-09-29  total_supply_protocol" in out
    assert "ultrasound:supply-over-time[next-day 00:00Z]" in out and "etherscan:ethsupply2.supply" in out


def test_a_missing_store_is_said_and_exits_0(tmp_path, capsys):
    assert abc.main(["--store", str(tmp_path / "nope.db")]) == 0
    assert "cannot open" in capsys.readouterr().out
    assert not (tmp_path / "nope.db").exists()                       # not created by a read-only open
