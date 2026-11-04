"""P5-1.6 (Jake 2026-11-04): change_threshold compares per day on both sides and never a same-date prior; a Review
Queue row can be resolved, --rejudge resolves only proven artefacts, a resolved row is answered everywhere."""
import hashlib
import sys
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import review_resolve as rr  # noqa: E402
import store as sm  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402
from fetch.validate import validate_frame  # noqa: E402

P, M = "Hyperliquid", "emissions_tokens"
SRC = "hypercore_info:tokenDetails.futureEmissions"


def _check(value, prior, meta, date="2026-10-30"):
    out = FetchOutput()
    df = pd.DataFrame([{"date": date, "project": P, "metric": M, "value": value, "source": SRC, "tier": 1}])
    validate_frame(df, {(P, M): prior}, out, meta)
    return [r for r in out.review if r["reason"] == "change_threshold"]


def test_the_rule_applies_to_hyperliquid_emissions_as_a_daily_non_lumpy_flow_at_50_pct():
    assert config.METRICS[M]["kind"] == "flow" and not config.lumpy_flow(P, M)
    assert config.change_threshold_pct(P, M) == 50.0, "the threshold is unchanged"


def test_jakes_example_a_daily_19547_against_a_4_day_span_row_of_114570_is_not_flagged():
    meta = {(P, M): (114570.0, "2026-10-29", SRC + "[span=4d]")}
    assert _check(19547.0, 114570.0, meta) == [], "28,642.5 a day before, 19,547 now: -31.8%, under 50%"
    # the old comparison (no date or source for the prior) flagged it: the raw figures read -83%
    assert len(_check(19547.0, 114570.0, None)) == 1


def test_a_genuine_per_day_fall_beyond_the_threshold_is_still_flagged_with_both_sides_per_day():
    meta = {(P, M): (114570.0, "2026-10-29", SRC + "[span=4d]")}
    got = _check(5000.0, 114570.0, meta)
    assert len(got) == 1
    assert got[0]["value"] == 5000.0 and got[0]["prior_value"] == 28642.5
    assert "NORMALISED TO PER DAY" in got[0]["basis"] and str(got[0]["prior_date"])[:10] == "2026-10-29"
    # and a one-day prior with a one-day row: compared as before
    assert len(_check(5000.0, 20000.0, {(P, M): (20000.0, "2026-10-29", SRC)})) == 1


def test_a_same_day_re_run_is_not_compared():
    assert _check(19547.0, 5000.0, {(P, M): (5000.0, "2026-10-30", SRC)}) == []
    # a meta row that is not the prior value's row is not used (the prior still counts as one day, undated)
    assert len(_check(19547.0, 5000.0, {(P, M): (9999.0, "2026-10-30", SRC)})) == 1


def test_the_store_gives_the_newest_rows_date_and_source(tmp_path):
    st = sm.Store(tmp_path / "m.db")
    st.upsert(pd.DataFrame([
        {"date": "2026-10-25", "project": P, "metric": M, "value": 20000.0, "source": SRC, "tier": 1},
        {"date": "2026-10-29", "project": P, "metric": M, "value": 114570.0, "source": SRC + "[span=4d]", "tier": 1}]))
    assert st.latest_rows()[(P, M)] == (114570.0, "2026-10-29", SRC + "[span=4d]")
    assert st.latest_values()[(P, M)] == 114570.0, "the same row latest_values reads"
    st.close()


def _store_with_flags(path):
    st = sm.Store(path)
    st.upsert(pd.DataFrame([
        {"date": "2026-10-29", "project": P, "metric": M, "value": 114570.0, "source": SRC + "[span=4d]", "tier": 1},
        {"date": "2026-10-30", "project": P, "metric": M, "value": 19547.0, "source": SRC, "tier": 1},
        {"date": "2026-10-31", "project": P, "metric": M, "value": 5000.0, "source": SRC, "tier": 1},
        {"date": "2026-10-28", "project": "Uniswap", "metric": "fees_usd", "value": 1_000_000.0,
         "source": "defillama:fees", "tier": 1},
        {"date": "2026-10-29", "project": "Uniswap", "metric": "fees_usd", "value": 300_000.0,
         "source": "defillama:fees", "tier": 1}]))
    old = "the previous stored value of this series, whatever date it carries"
    st.record_review("20261031T090000Z", [
        # an artefact: 19,547 a day against a 4-day row read raw (-83%); 28,642.5 a day (-31.8%)
        {"project": P, "metric": M, "date": "2026-10-30", "value": 19547.0, "prior_value": 114570.0,
         "reason": "change_threshold", "action": "stored_flagged", "source": SRC, "tier": 1, "basis": old},
        # a real change: 5,000 against 19,547 the day before, both one day (-74%)
        {"project": P, "metric": M, "date": "2026-10-31", "value": 5000.0, "prior_value": 19547.0,
         "reason": "change_threshold", "action": "stored_flagged", "source": SRC, "tier": 1, "basis": old},
        # a same-date comparison the flag itself dated
        {"project": "Uniswap", "metric": "fees_usd", "date": "2026-10-29", "value": 300_000.0,
         "prior_value": 300_000.0 * 3, "prior_date": "2026-10-29", "reason": "change_threshold",
         "action": "stored_flagged", "source": "defillama:fees", "tier": 1, "basis": old},
        # a real -70% day-on-day fall in a one-day series
        {"project": "Uniswap", "metric": "fees_usd", "date": "2026-10-29", "value": 300_000.0,
         "prior_value": 1_000_000.0, "reason": "out_of_bounds", "action": "rejected", "source": "defillama:fees",
         "tier": 1, "basis": ""}])
    st.close()


def _digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_rejudge_is_a_dry_run_and_list_is_read_only(tmp_path, capsys):
    db = tmp_path / "m.db"
    _store_with_flags(db)
    before = _digest(db)
    assert rr.main(["--list", "--store", str(db)]) == 0
    assert rr.main(["--rejudge", "--store", str(db)]) == 0
    out = capsys.readouterr().out
    assert _digest(db) == before, "no write without --apply"
    assert "DRY RUN - nothing written" in out and "BEFORE: 4 open row(s)" in out and "AFTER (if applied): 2" in out


def test_rejudge_apply_resolves_only_the_artefacts_and_leaves_the_real_change_open(tmp_path, capsys):
    db = tmp_path / "m.db"
    _store_with_flags(db)
    assert rr.main(["--rejudge", "--apply", "--store", str(db)]) == 0
    capsys.readouterr()
    st = sm.Store(db)
    q = st.review_queue()
    st.close()
    res = {(r["project"], str(r["date"])[:10], r["reason"], r["value"]): r["resolution"] for r in q.to_dict("records")}
    assert res[(P, "2026-10-30", "change_threshold", 19547.0)] == "RESOLVED-ARTEFACT"
    assert res[(P, "2026-10-31", "change_threshold", 5000.0)] is None, "a real -74% day stays open"
    assert res[("Uniswap", "2026-10-29", "change_threshold", 300_000.0)] == "RESOLVED-ARTEFACT", "same date"
    assert res[("Uniswap", "2026-10-29", "out_of_bounds", 300_000.0)] is None, "only change_threshold is re-judged"
    note = q[(q["project"] == P) & (q["date"] == "2026-10-30")]["resolution_note"].iloc[0]
    assert "19,547.0000 over 1 day(s)" in note and "28,642.5000/day" in note and "-31.8% per day" in note


def test_a_resolved_row_is_answered_on_the_review_queue_tab_and_not_counted_open(tmp_path, capsys):
    import build_workbook as bw
    db = tmp_path / "m.db"
    _store_with_flags(db)
    assert rr.main(["--rejudge", "--apply", "--store", str(db)]) == 0
    assert rr.main(["--resolve", P, M, "2026-10-31", "--note", "checked the reserve: a real slowdown",
                    "--store", str(db)]) == 0
    capsys.readouterr()
    st = sm.Store(db)
    q = st.review_queue()
    st.close()
    wb = openpyxl.Workbook()
    bw.write_review_queue(wb.active, q, "20261031T090000Z")
    assert bw.BOARD_INPUTS["review_open"]["Uniswap"] == 1, "only the out_of_bounds row is open"
    assert bw.BOARD_INPUTS["review_open"][P] == 0
    cells = [str(c.value) for row in wb.active.iter_rows() for c in row if c.value is not None]
    assert "RESOLVED-ARTEFACT" in cells and "RESOLVED-ACCEPTED" in cells
    assert any("checked the reserve: a real slowdown" in c for c in cells)
    # aggregate()'s review flag: a resolved row no longer flags its series
    flagged = {(r["project"], r["metric"]) for r in q.to_dict("records")
               if (config.review_answered(r) or {}).get("decision") != "ACCEPT"}
    assert (P, M) not in flagged and ("Uniswap", "fees_usd") in flagged


def test_resolve_needs_a_note_and_an_existing_row(tmp_path, capsys):
    db = tmp_path / "m.db"
    _store_with_flags(db)
    assert rr.main(["--resolve", P, M, "2026-10-31", "--store", str(db)]) == 2
    assert rr.main(["--resolve", P, M, "2026-01-01", "--note", "x", "--store", str(db)]) == 1
    st = sm.Store(db)
    try:
        import pytest
        with pytest.raises(ValueError):
            st.resolve_review(P, M, "2026-10-31", "change_threshold", "ACCEPT-EVERYTHING", "x", "test")
    finally:
        st.close()
