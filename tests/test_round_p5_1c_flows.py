"""ROUND 1C, group `flows` (Jake 2026-11-04).

B1: one-off releases AND one-off burns stay in the run rates and count toward every supply/demand figure and trend, the
    same as recurring flows (config.ONE_OFFS_IN_RATES); a treasury-funded one is still recorded, never credited, as
    holder value; the circulating flow statement still keeps an issuer-side burn out of "− burned"; every rate whose
    window holds a one-off says "of which one-off: x%" on its cell.
B2: a pre-minted supply's NET SUPPLY CHANGE is mint − burn = −burn — RELABELLED "burn only" on the cell, and the A4
    release-basis cells (derived net, Q1–Q3) "release − burn"; no number moves.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402

UNI = config.PROJECT_BY_NAME["Uniswap"]


def _rows(project, metric, days, values, source, tier=2):
    days = pd.to_datetime(list(days))
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": days, "project": project, "metric": metric, "value": [float(v) for v in vals],
                         "source": source, "tier": tier})


DAYS = ["2025-12-26", "2025-12-27", "2025-12-28", "2025-12-29"]


def _burn_groups():
    gross = _rows("Uniswap", "gross_burn_tokens", DAYS, [5.0, 6.0, 100_000_007.0, 8.0], "chain:burn_dead:delta")
    return {("Uniswap", "gross_burn_tokens"): gross}


# ============================================================================================================ B1
def test_b1_the_one_off_stays_in_the_series_and_is_only_dated(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    assert config.one_offs_in_rates() is True
    groups = _burn_groups()
    bw._one_off_views(groups)
    assert groups[("Uniswap", "gross_burn_tokens")]["value"].tolist() == [5.0, 6.0, 100_000_007.0, 8.0], \
        "the one-off is counted like a recurring burn"
    assert not groups[("Uniswap", "gross_burn_tokens")]["source"].str.contains("one-off-removed").any()
    one = groups[("Uniswap", "one_off_burn_tokens")]
    assert list(one["date"]) == [pd.Timestamp("2025-12-28")] and one["value"].iloc[0] == 100_000_000.0
    assert bw._ONE_OFF_IN_SERIES[("Uniswap", "gross_burn_tokens")][0][:3] == (
        pd.Timestamp("2025-12-28"), 100_000_000.0, "treasury")
    assert not bw._ONE_OFF_REMOVED and not bw._ONE_OFF_BLOCKS


def test_b1_two_candidate_rows_block_nothing_and_the_nearest_to_landing_dates_it(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    groups = {("Uniswap", "gross_burn_tokens"): _rows(
        "Uniswap", "gross_burn_tokens", ["2025-12-10", "2025-12-28"], [150_000_000.0, 100_000_001.0], "chain:x:delta")}
    bw._one_off_views(groups)
    assert not bw._ONE_OFF_BLOCKS, "nothing is taken out, so nothing is blocked"
    assert bw._ONE_OFF_IN_SERIES[("Uniswap", "gross_burn_tokens")][0][0] == pd.Timestamp("2025-12-28")


def test_b1_switch_off_restores_the_old_exclusion(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    monkeypatch.setitem(config.ONE_OFFS_IN_RATES, "on", False)
    groups = _burn_groups()
    bw._one_off_views(groups)
    assert groups[("Uniswap", "gross_burn_tokens")]["value"].tolist() == [5.0, 6.0, 7.0, 8.0]
    assert bw._ONE_OFF_REMOVED[("Uniswap", "gross_burn_tokens")] == 100_000_000.0
    assert not bw._ONE_OFF_IN_SERIES


def test_b1_a_treasury_funded_one_off_in_the_buyback_is_recorded_never_credited(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    groups = _burn_groups()
    bw._one_off_views(groups)
    tok = groups[("Uniswap", "gross_burn_tokens")].assign(metric="actual_buyback_tokens")
    usd = tok.assign(metric="actual_buyback_usd", value=tok["value"] * 6.0)     # $6 a UNI
    groups[("Uniswap", "actual_buyback_tokens")], groups[("Uniswap", "actual_buyback_usd")] = tok, usd
    groups[("Uniswap", "price_usd")] = _rows("Uniswap", "price_usd", DAYS, 6.0, "coingecko")
    bw._holder_value_views(groups)
    cred = bw._daily(groups[("Uniswap", "holder_value_revenue_usd")])
    other = bw._daily(groups[("Uniswap", "holder_value_other_usd")])
    day = pd.Timestamp("2025-12-28")
    assert cred[day] == pytest.approx(7.0 * 6.0), "the Firepit part of the day stays credited"
    assert other[day] == pytest.approx(100_000_000.0 * 6.0), "the treasury one-off is recorded, not credited"
    assert cred[pd.Timestamp("2025-12-27")] == pytest.approx(36.0)
    assert "Uniswap" in config.METRICS["holder_value_other_usd"]["only_projects"]


def test_b1_the_share_note_names_the_one_off_beside_the_rate(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    groups = _burn_groups()
    bw._one_off_views(groups)
    bw._one_off_shares(groups, pd.Timestamp("2026-01-10"))
    part, tot, what = bw.ONE_OFF_SHARES[("Uniswap", "gross_burn_tokens", "q0")]
    assert part == 100_000_000.0 and tot == pytest.approx(100_000_026.0) and "UNIfication" in what
    assert ("Uniswap", "gross_burn_tokens", "q1") not in bw.ONE_OFF_SHARES, "a window without it carries no note"
    f = (f'=IFERROR(INDEX(Data!${bw.DC["q0"]}$2:${bw.DC["q0"]}$900,MATCH($A5&"|gross_burn_tokens",Data!$A$2:$A$900,0))'
         f'/INDEX(Data!${bw.DC["q1"]}$2:${bw.DC["q1"]}$900,MATCH($A5&"|gross_burn_tokens",Data!$A$2:$A$900,0)),"n/a")')
    note = bw._one_off_share_note("Uniswap", f)
    # PR1.U10: a share above 99% and short of all reads two decimals (100,000,000 of 100,000,026 is not "100%")
    assert note.startswith("ONE-OFF IN THIS RATE") and "of which one-off: 100.00% — gross_burn_tokens Q0" in note
    assert "Q1" not in note
    assert bw._one_off_share_note("Sky", f) == "" and bw._one_off_share_note("Uniswap", "=1+1") == ""


def test_b1_the_release_one_off_share_reads_the_distributor_or_its_outflow(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    bw._ONE_OFF_IN_SERIES.clear()
    bw._ONE_OFF_HV_RECORDED.clear()
    d = ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"]
    groups = {("Uniswap", "outflow_e_tokens"): _rows("Uniswap", "outflow_e_tokens", d, [1.0, 2.0, 300.0, 400.0, 0.0],
                                                      "derived:outflow"),
              ("Uniswap", "outflow_e_usd"): _rows("Uniswap", "outflow_e_usd", d, [6.0, 12.0, 1800.0, 2400.0, 0.0],
                                                   "derived:outflow"),
              ("Uniswap", "unlock_release_tokens"): _rows("Uniswap", "unlock_release_tokens", d,
                                                          [1.0, 1002.0, 300.0, 1400.0, 0.0], "derived:unlock")}
    bw._one_off_shares(groups, pd.Timestamp("2026-10-13"))
    part, tot, what = bw.ONE_OFF_SHARES[("Uniswap", "outflow_e_tokens", "q0")]
    assert (part, tot) == (700.0, 703.0) and "merkle distributor" in what
    assert bw.ONE_OFF_SHARES[("Uniswap", "outflow_e_usd", "q0")][:2] == (4200.0, 4218.0)
    assert bw.ONE_OFF_SHARES[("Uniswap", "unlock_release_tokens", "q0")][:2] == (700.0, 2703.0)
    # its own per-wallet series, where stored, is the measure (falls only)
    groups[("Uniswap", "noncirc_merkle_distributor_tokens")] = _rows(
        "Uniswap", "noncirc_merkle_distributor_tokens", d, [1000.0, 1000.0, 750.0, 400.0, 400.0], "chain:x")
    bw._one_off_shares(groups, pd.Timestamp("2026-10-13"))
    assert bw.ONE_OFF_SHARES[("Uniswap", "outflow_e_tokens", "q0")][0] == 600.0   # 250 + 350
    spec = config.ONE_OFF_DILUTION_NOTES["Uniswap"]
    assert (spec["from"], spec["to"], spec["tokens"]) == ("2026-10-11", "2026-11-02", 12_257_360)


def test_b1_labels_say_counted_not_excluded():
    assert "counted in every rate and trend" in config.one_off_events_text("Uniswap")
    assert "a record, no series" in config.one_off_events_text("Ether.fi")
    n = config.one_off_event_note("Uniswap", "one_off:UNIfication retroactive treasury burn (100,000,000 UNI) (x)")
    assert "counted in every rate and trend like a recurring burn" in n and "out of every rate" not in n
    assert "EXCLUDED" not in config.METRICS["one_off_burn_tokens"]["label"]
    import headline_diff
    assert headline_diff.BEFORE["p5_1c_b1_one_offs"]() == [(config.ONE_OFFS_IN_RATES, "on", False)]


# ============================================================================================================ B2
def test_b2_premint_net_cells_are_relabelled_burn_only():
    geod, hl, maple = (config.PROJECT_BY_NAME[n] for n in ("GEODNET", "Hyperliquid", "Maple"))
    for p in (geod, hl, maple, config.PROJECT_BY_NAME["Chainlink"], config.PROJECT_BY_NAME["Aethir"], UNI):
        suffix, note = bw._premint_net_flag(p)
        # PR1.U10: the note says what the figure is NOT in S8's words — the net supply change (the circulating change)
        assert suffix == " burn only" and "NOT the net supply change, which is the change in circulating supply" in note, \
            p["name"]
    assert "Derived net supply change" in bw._premint_net_flag(geod)[1]
    assert "ISSUER_MINTS" in bw._premint_net_flag(maple)[1], "Maple's mint to its treasury is named"
    for n in ("Ethereum", "Near", "Sky", "Aerodrome"):
        assert bw._premint_net_flag(config.PROJECT_BY_NAME[n]) is None, n
    assert bw._premint_circ_flag(geod)[0] == " release − burn"
    assert bw._premint_circ_flag(UNI) is None and bw._premint_circ_flag(config.PROJECT_BY_NAME["Sky"]) is None


def test_b2_the_reader_says_burn_only_on_a_nonzero_premint_net():
    import dataclasses
    src = reader.Src(sheet="Master", coord="X5", value=-9_805_000.0, num=-9_805_000.0,
                     header="NET SUPPLY CHANGE Q0 (tokens) = issuance − burn")
    geod = config.PROJECT_BY_NAME["GEODNET"]
    assert reader._premint_note(geod, src).startswith("BURN ONLY")
    assert "issuance − burn is 0" in reader._premint_note(geod, dataclasses.replace(src, value=0.0, num=0.0))
    assert reader._premint_note(config.PROJECT_BY_NAME["Ethereum"], src) == ""


# ==================================================================================== on the 15 real dumps (build)
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "p5_1c_flows")
    assert "error" not in rec["stats"], rec["stats"]
    return rec


def _cell(ws, project, head_prefix):
    hr = next(r for r in range(1, 13) if str(ws.cell(r, 1).value or "").strip().lower() == "project")
    col = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(hr, c).value or "").startswith(head_prefix))
    row = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return ws.cell(row, col)


def test_dumps_geodnet_net_is_burn_only_and_the_derived_is_release_minus_burn(built):
    wb = openpyxl.load_workbook(built["audit"])
    a4 = wb["A4 Permanent Burn"]
    net = _cell(a4, "GEODNET", "NET SUPPLY CHANGE Q0")
    assert net.number_format.endswith('" burn only"') and "BURN ONLY" in net.comment.text
    der = _cell(a4, "GEODNET", "Derived net supply change")
    assert der.number_format.endswith('" release − burn"') and "CIRCULATING" in der.comment.text
    assert _cell(a4, "GEODNET", "Net supply change Q1").number_format.endswith('" release − burn"')
    # PR1.S8: Master's NET SUPPLY CHANGE is now closing − opening circulating; the mint − burn is its component column
    m = _cell(wb["Master"], "GEODNET", "Issuance − burn Q0")
    assert m.number_format.endswith('" burn only"')
    assert "burn only" not in _cell(a4, "Sky", "NET SUPPLY CHANGE Q0").number_format


def test_dumps_uniswap_one_off_is_in_y1_with_its_share_and_the_flow_statement_balances(built):
    wb = openpyxl.load_workbook(built["audit"])
    data = wb["Data"]
    hdr = [c.value for c in data[1]]
    key_c, y1_c = hdr.index("Key (project|metric)") + 1, next(i for i, h in enumerate(hdr) if str(h).startswith("Y1")) + 1
    rows = {data.cell(r, key_c).value: r for r in range(2, data.max_row + 1)}
    y1 = data.cell(rows["Uniswap|gross_burn_tokens"], y1_c).value
    assert y1 > 100_000_000, "the 100M is in the trailing year"
    # the share is on the cells that read the year (A4's burn trend reads Y1 nowhere; the Q-3 trajectory does)
    a4 = wb["A4 Permanent Burn"]
    notes = [a4.cell(r, c).comment.text for r in range(1, a4.max_row + 1) if a4.cell(r, 1).value == "Uniswap"
             for c in range(2, a4.max_column + 1) if a4.cell(r, c).comment]
    assert any("of which one-off:" in n and "gross_burn_tokens Q3" in n for n in notes)
    flows = wb["Flows"]
    burned = next(r for r in range(1, flows.max_row + 1) if flows.cell(r, 1).value == "Uniswap"
                  and flows.cell(r, 3).value == "− burned")
    assert all((flows.cell(burned, c).value or 0) < 100_000_000 for c in range(4, 9)), \
        "the issuer-side one-off stays out of the circulating statement's burned leg"
    status = [flows.cell(r, c).value for r in range(1, flows.max_row + 1) if flows.cell(r, 1).value == "Uniswap"
              and flows.cell(r, 3).value == "Status" for c in range(4, 9)]
    assert not any(str(s).startswith("BROKEN") for s in status), status
