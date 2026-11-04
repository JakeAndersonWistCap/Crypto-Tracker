"""P5 round 1, items 2-5 and 10 (Jake 2026-11-04): Hyperliquid.

2  a [span=Nd] row counts N days in the observed/model ratio (span_aware_ratio), as Ethereum's issuance check does;
3  the days between two stored futureEmissions reads are MEASURED from those reads (_flow_from_stored_stock_views);
4  a modelled day is never presented as measured: locked_tokens has no modelled leg, every modelled value rests on
   measured inputs only (hl_model_rewards), Data splits covered days into measured and modelled, the audit lists them;
5  option C: k x sqrt(stake) calibrated on half the measured rows, tested on the other half (hl_option_c); A4 reads
   option C's issuance and the interim allowance lapses only when it passes;
10 tokenDetails / validatorSummaries holes are routed not recoverable (fetch.archive).
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

HL = "Hyperliquid"
COLS = ["date", "project", "metric", "value", "source", "tier"]
FE_SRC = "hypercore_info:tokenDetails.futureEmissions"
DELTA_SRC = FE_SRC + ":delta"
MODEL_SRC = "modelled:hl_reference_yield[k=1.000, test]"


def _frame(metric, series: dict, source) -> pd.DataFrame:
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": HL, "metric": metric, "value": float(v),
                          "source": source(d) if callable(source) else source, "tier": 1} for d, v in series.items()],
                        columns=COLS)


@pytest.fixture
def hl_only(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME[HL]])
    monkeypatch.setattr(config, "OPTION_C_STATE", {})
    yield


# ---------- item 2: span-aware ratio ----------

def test_a_span_row_is_compared_with_the_model_over_its_own_span():
    model = pd.Series(100.0, index=pd.date_range("2026-10-01", "2026-10-10"))
    obs = pd.DataFrame({"date": pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07"]),
                        "value": [400.0, 100.0, 100.0],
                        "source": [DELTA_SRC + "[span=4d]", DELTA_SRC, DELTA_SRC]})
    ratio, used, days = bw.span_aware_ratio(obs, model)
    assert ratio == pytest.approx(1.0), "the 4-day row is 4 days of the model, not one"
    assert len(used) == 3 and days == 6
    # the bug: the 4-day row against ONE model day read 600 / 300 = 2.0
    assert ratio != pytest.approx(2.0)


def test_a_span_row_without_a_model_value_on_every_day_is_not_used():
    model = pd.Series(100.0, index=pd.date_range("2026-10-03", "2026-10-10"))
    obs = pd.DataFrame({"date": pd.to_datetime(["2026-10-05", "2026-10-06"]), "value": [500.0, 110.0],
                        "source": [DELTA_SRC + "[span=4d]", DELTA_SRC]})
    ratio, used, days = bw.span_aware_ratio(obs, model)
    assert used == [pd.Timestamp("2026-10-06")] and days == 1 and ratio == pytest.approx(1.1)
    assert bw.span_aware_ratio(obs.iloc[0:0], model) == (None, [], 0)


# ---------- item 3: the days between two stored reads, measured ----------

def test_the_fixture_s_09_25_to_09_28_reads_give_one_measured_3_day_row(hl_only):
    pl = json.loads((ROOT / "fixtures" / "real_hyperliquid.json").read_text(encoding="utf-8"))
    rows = [r for r in pl["metrics"] if r["metric"] in ("future_emissions_tokens", "emissions_tokens")]
    df = pd.DataFrame([{**r, "project": HL, "date": pd.Timestamp(r["date"])} for r in rows])[COLS]
    groups = {(HL, m): g.reset_index(drop=True) for m, g in df.groupby("metric")}
    first_flow = groups[(HL, "emissions_tokens")]["date"].min()
    fe = bw._daily(groups[(HL, "future_emissions_tokens")])
    bw._flow_from_stored_stock_views(groups)
    g = groups[(HL, "emissions_tokens")]
    new = g[g["source"].str.contains("P5-1.3", regex=False)]
    assert len(new) >= 1 and (new["date"] < first_flow).all(), "nothing at or after the first stored flow row"
    r = new[new["date"] == pd.Timestamp("2026-09-28")]
    assert len(r) == 1, new
    assert "[span=3d]" in r["source"].iloc[0]
    assert r["value"].iloc[0] == pytest.approx(fe[pd.Timestamp("2026-09-25")] - fe[pd.Timestamp("2026-09-28")])
    assert not any(config.is_modelled(s) for s in new["source"]), "measured from the stored reads, never MODELLED"
    from fetch.base import _measuring_point
    live = g[~g["source"].str.contains("P5-1.3", regex=False)]["source"]
    assert {_measuring_point(s) for s in new["source"]} == {_measuring_point(s) for s in live}, \
        "the live rows' own measuring point"
    from fetch.validate import _span_days
    assert _span_days(r["source"].iloc[0]) == 3


def test_a_rise_in_the_stock_is_never_a_negative_flow(hl_only):
    fe = _frame("future_emissions_tokens", {"2026-09-01": 1000, "2026-09-02": 990, "2026-09-03": 995,
                                            "2026-09-05": 975}, FE_SRC)
    groups = {(HL, "future_emissions_tokens"): fe}
    bw._flow_from_stored_stock_views(groups)
    g = groups[(HL, "emissions_tokens")].sort_values("date")
    assert list(g["date"].dt.strftime("%m-%d")) == ["09-02", "09-05"], "09-03 rose: refused, not stored as -5"
    assert list(g["value"]) == [10.0, 20.0]
    assert "[span=2d]" in g["source"].iloc[1]


# ---------- item 5: option C ----------

def _synthetic(days=24, k=2000.0, scale=1.0, noise=None):
    idx = pd.date_range("2026-09-01", periods=days)
    stake = pd.Series([4.0e8 + 1.0e6 * i for i in range(days)], index=idx)
    a = k / stake ** 0.5                                    # the gross rate the Grants wallet earns
    rew = k * stake ** 0.5 / 365.0
    vals = {d: rew[d] * (scale if i >= days // 2 else 1.0) * (1 + (noise[i] if noise else 0.0))
            for i, d in enumerate(idx)}
    return a, stake, _frame("emissions_tokens", vals, DELTA_SRC)


def test_option_c_passes_when_the_model_holds_on_the_held_out_half():
    a, stake, em = _synthetic(noise=[0.01 * ((-1) ** i) for i in range(24)])
    oc = bw.hl_option_c(a, stake, em)
    assert oc["passed"], oc["why"]
    assert oc["k"] == pytest.approx(2000.0, rel=0.01)
    assert oc["calibrated_on"][2] == 12 and oc["tested_on"][2] == 12
    assert oc["calibrated_on"][1] < oc["tested_on"][0], "calibrated on the earlier half, tested on the later"
    for form in ("sqrt_form", "a_form"):
        st = oc[form]
        assert st["mae"] <= 0.05 and st["worst"] <= 0.15 and abs(st["bias"]) <= 0.02


def test_option_c_fails_on_any_of_the_three_marks_and_says_which():
    a, stake, em = _synthetic(scale=1.10)                   # the test half is 10% above the model
    oc = bw.hl_option_c(a, stake, em)
    assert not oc["passed"] and "mean abs error 9.1%" in oc["why"] and "bias -9.1%" in oc["why"]
    a, stake, em = _synthetic(noise=[0.0] * 23 + [0.25])   # one test row 25% off: mean is fine, the worst is not
    oc = bw.hl_option_c(a, stake, em)
    assert not oc["passed"] and oc["sqrt_form"]["worst"] > 0.15


def test_option_c_needs_measured_rows_with_a_stake_read_on_every_span_day():
    a, stake, em = _synthetic(days=6)
    em.loc[em.index[:3], "source"] = MODEL_SRC              # modelled rows never calibrate or test
    oc = bw.hl_option_c(a, stake, em)
    assert not oc["passed"] and oc["rows"] == 3 and "at least 4" in oc["why"]
    a, stake, em = _synthetic(days=10)
    oc = bw.hl_option_c(a.iloc[0:0], stake, em)              # no reference yield: the a-form cannot be tested
    assert not oc["passed"] and "cannot be tested" in oc["why"]


def test_a_span_row_calibrates_against_its_whole_span():
    a, stake, em = _synthetic(days=12)
    em = em.sort_values("date").reset_index(drop=True)
    merged = em.iloc[1:4]["value"].sum()                    # three days read as one [span=3d] row
    em = pd.concat([em.drop(index=[1, 2]), ], ignore_index=True)
    em.loc[em["date"] == pd.Timestamp("2026-09-04"), ["value", "source"]] = [merged, DELTA_SRC + "[span=3d]"]
    oc = bw.hl_option_c(a, stake, em)
    assert oc["passed"] and oc["k"] == pytest.approx(2000.0, rel=1e-9)


def test_option_c_switches_a4_and_lapses_the_allowance_only_when_it_passed(monkeypatch):
    monkeypatch.setattr(config, "OPTION_C_STATE", {})
    before = config.crossover_issuance_metric(HL)
    assert before != "issuance_option_c_tokens"
    day = pd.Timestamp("2026-11-04")
    assert config.regression_allowance(HL, "A4 | BURN ÷ ISSUANCE: lost", on=day) is not None
    config.OPTION_C_STATE[HL] = True
    assert config.crossover_issuance_metric(HL) == "issuance_option_c_tokens"
    assert config.regression_allowance(HL, "A4 | BURN ÷ ISSUANCE: lost", on=day) is None
    config.OPTION_C_STATE[HL] = False
    assert config.crossover_issuance_metric(HL) == before


# ---------- item 4: every modelled value from measured inputs ----------

def test_modelled_rewards_use_the_stake_read_else_the_rate_and_never_a_model():
    idx = pd.date_range("2026-09-01", periods=4)
    a = pd.Series([0.1, 0.1, 0.1, 0.1], index=idx)
    st = pd.Series([4.0e8], index=[idx[1]])
    r = bw.hl_model_rewards({"k": 2000.0, "a": a, "stake": st}, list(idx) + [pd.Timestamp("2026-09-10")])
    assert r[idx[1]] == pytest.approx(2000.0 * 4.0e8 ** 0.5 / 365.0)
    assert r[idx[0]] == pytest.approx(2000.0 ** 2 / (365.0 * 0.1))
    assert pd.Timestamp("2026-09-10") not in r.index, "no measured input: no value"


def _model_groups():
    idx = pd.date_range("2026-09-01", periods=20)
    k = 2000.0
    stake = pd.Series([4.0e8 + 1.0e6 * i for i in range(20)], index=idx)
    a = k / stake ** 0.5
    meas = idx[10:]
    return {
        (HL, "hl_reference_yield_gross"): _frame("hl_reference_yield_gross", a.to_dict(), "hypercore_info:ledger_replay"),
        (HL, "locked_tokens"): _frame("locked_tokens", stake[idx[5:]].to_dict(), "hypercore_info:validatorSummaries"),
        (HL, "emissions_tokens"): _frame("emissions_tokens", (k * stake[meas] ** 0.5 / 365.0).to_dict(), DELTA_SRC),
        (HL, "future_emissions_tokens"): _frame("future_emissions_tokens", {d: 1e8 - 1e4 * i for i, d in
                                                                            enumerate(meas)}, FE_SRC),
        (HL, "staked_noncirculating_tokens"): _frame("staked_noncirculating_tokens", {d: 1.0e8 for d in idx},
                                                     "hypercore_info:ledger_replay"),
    }, k, stake, a, idx


def test_locked_tokens_has_no_modelled_leg_and_the_modelled_rewards_rest_on_measured_inputs(hl_only, monkeypatch):
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    groups, k, stake, a, idx = _model_groups()
    locked_before = groups[(HL, "locked_tokens")].copy()
    bw._hl_model_views(groups)
    assert bw.OPTION_C[HL]["passed"], bw.OPTION_C[HL]["why"]
    pd.testing.assert_frame_equal(groups[(HL, "locked_tokens")], locked_before), "locked_tokens stays measured only"
    assert (HL, "locked_tokens_modelled") in groups, "the stake model is its own row"
    e = groups[(HL, "emissions_tokens")]
    lead = e[e["source"].map(config.is_modelled)]
    assert set(lead["date"]) == set(idx[:10]), "modelled only before the first measured row"
    for d, v in zip(lead["date"], lead["value"]):
        exp = bw.OPTION_C[HL]["k"] * (stake[d] ** 0.5 if d in idx[5:] else (k / a[d])) / 365.0
        assert v == pytest.approx(exp, rel=1e-9)
    # FE history: the first read + the modelled rewards paid after each day, computed directly
    fem = bw._daily(groups[(HL, "future_emissions_modelled_tokens")])
    rew = bw.hl_model_rewards(bw.HL_MODEL_PARAMS[HL], [d for d in idx if d <= idx[10]])
    d0 = idx[3]
    assert fem[d0] == pytest.approx(1e8 + rew[rew.index > d0].sum())


def test_the_outflow_a_on_a_modelled_day_comes_from_measured_inputs(hl_only, monkeypatch):
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    groups, k, stake, a, idx = _model_groups()
    bw._hl_model_views(groups)
    bw._stake_prorata_views(groups)
    g = groups[(HL, "emissions_circulating_stake_tokens")]
    kk = bw.HL_MODEL_PARAMS[HL]["k"]
    by = g.set_index("date")
    d_stake, d_rate = idx[6], idx[2]                         # modelled days with and without a stake read
    assert by.loc[d_stake, "value"] == pytest.approx(kk * stake[d_stake] ** 0.5 / 365 * (stake[d_stake] - 1e8)
                                                     / stake[d_stake])
    assert by.loc[d_rate, "value"] == pytest.approx(kk * kk / (365 * a[d_rate]) - 1e8 * a[d_rate] / 365)
    assert config.is_modelled(by.loc[d_rate, "source"]) and "P5-1.4" in by.loc[d_rate, "source"]
    assert not config.is_modelled(by.loc[idx[12], "source"]), "a measured day stays the measured product"


def test_option_c_issuance_is_the_two_legs_and_keeps_the_modelled_label(hl_only, monkeypatch):
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    groups, k, stake, a, idx = _model_groups()
    groups[(HL, "unlock_release_tokens")] = _frame("unlock_release_tokens", {d: 50.0 for d in idx},
                                                   "hypercore_info:ledger_replay")
    bw._hl_model_views(groups)
    bw._option_c_issuance_views(groups)
    g = groups[(HL, "issuance_option_c_tokens")].set_index("date")
    e = bw._daily(groups[(HL, "emissions_tokens")])
    assert g.loc[idx[12], "value"] == pytest.approx(e[idx[12]] + 50.0)
    assert config.is_modelled(g.loc[idx[2], "source"]) and not config.is_modelled(g.loc[idx[12], "source"])
    config.OPTION_C_STATE[HL] = False                         # not passed: no option C series at all
    groups.pop((HL, "issuance_option_c_tokens"))
    bw._option_c_issuance_views(groups)
    assert (HL, "issuance_option_c_tokens") not in groups


def test_covered_days_split_into_measured_and_modelled():
    g = pd.concat([_frame("emissions_tokens", {"2026-09-01": 1, "2026-09-02": 1, "2026-09-03": 1}, MODEL_SRC),
                   _frame("emissions_tokens", {"2026-09-05": 4}, DELTA_SRC + "[span=3d]"),
                   _frame("emissions_tokens", {"2026-09-06": 1}, DELTA_SRC)])
    mc = bw._day_classes(g)
    assert sorted(d.day for d in mc["modelled"]) == [1, 2], "09-03 is inside the measured span row"
    assert sorted(d.day for d in mc["measured"]) == [3, 4, 5, 6]


def test_data_has_the_two_count_columns_and_the_audit_lists_the_days(tmp_path, monkeypatch):
    for col in ("q0_measured_days", "q0_modelled_days"):
        assert col in bw.DATA_COLS and col in bw.NUMERIC_APPENDED
    assert len(bw.DATA_COLS) == len(bw.DATA_HEAD)
    monkeypatch.setattr(bw, "MEASURED_VS_MODELLED", {
        (HL, "emissions_tokens"): {"measured": set(pd.date_range("2026-09-29", "2026-10-02")),
                                   "modelled": set(pd.date_range("2026-09-20", "2026-09-22"))}})
    rows = bw.measured_vs_modelled_rows()
    assert rows == [[HL, "emissions_tokens", "MEASURED", 4, "2026-09-29", "2026-10-02", "2026-09-29..10-02"],
                    [HL, "emissions_tokens", "MODELLED", 3, "2026-09-20", "2026-09-22", "2026-09-20..09-22"]]
    import openpyxl
    wb = openpyxl.Workbook()
    bw.write_measured_vs_modelled(wb.create_sheet("Measured vs modelled"), tmp_path / "mvm.csv")
    assert (tmp_path / "mvm.csv").read_text(encoding="utf-8").splitlines()[0] == ",".join(bw.MVM_HEAD)
    assert wb["Measured vs modelled"].cell(4, 3).value == "MEASURED"


# ---------- item 10: holes that cannot be read back ----------

def test_tokendetails_and_validator_summaries_holes_are_not_recoverable():
    from fetch import archive
    for src in ("hypercore_info:tokenDetails", "hypercore_info:tokenDetails.futureEmissions",
                DELTA_SRC + "[span=3d]", "hypercore_info:validatorSummaries"):
        assert archive.hole_route(HL, "m", src, set()) == "not_recoverable", src
        assert "permanently missing" in archive.not_recoverable(src)
    assert archive.not_recoverable("hypercore_info:ledger_replay") == ""
    assert archive.not_recoverable("scrape:x") == "scrape keeps no history"


def test_the_read_interval_figures_are_reported_beside_the_pass_test_and_never_decide_it():
    """P5-1.5 finding: a daily row is the fall between two reads made at different hours. The read-to-read fit is shown
    for Jake's decision; the pass test stays on calendar days."""
    a, stake, em = _synthetic(days=12)
    em = em.sort_values("date").reset_index(drop=True)
    hours = [12 + (6 if i % 2 else -6) for i in range(13)]       # reads alternate 06:00 / 18:00
    days = pd.date_range("2026-08-31", periods=13)
    fe = _frame("future_emissions_tokens", {d: 1e8 - i for i, d in enumerate(days)}, FE_SRC)
    fe["fetched_at"] = [(d + pd.Timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ") for d, h in zip(days, hours)]
    # each row's true value follows the read interval (0.5 or 1.5 days), so calendar days miss by up to 50%
    for i in range(12):
        em.loc[i, "value"] *= (1.5 if i % 2 == 0 else 0.5)
    oc = bw.hl_option_c(a, stake, em, fe)
    assert not oc["passed"], "the calendar-day test decides"
    assert oc["read_interval_form"]["mae"] < 0.001 and "FOR JAKE, not the pass test" in oc["why"]
    assert "read_interval_form" not in bw.hl_option_c(a, stake, em)
    ri = oc["read_interval_form"]
    assert ri["meets_pass_marks"] and ri["interval_hours"] == (12.0, 36.0)
    assert ri["calibrated_on"][2] == 6 and ri["tested_on"][2] == 6, "first half calibrates, second half tests"
    assert ri["calibrated_on"][1] < ri["tested_on"][0]
    assert "MEETS the pass marks on this basis" in oc["why"] and "verdict unchanged until Jake confirms" in oc["why"]
    assert config.OPTION_C_PASS["mae"] == 0.05 and config.OPTION_C_PASS["worst"] == 0.15 \
        and config.OPTION_C_PASS["bias"] == 0.02, "the same thresholds on both bases"


def test_the_catch_up_line_for_an_endpoint_hole_names_the_endpoint():
    from fetch import archive
    assert "tokenDetails answers today's value only" in archive._no_history_why("hypercore_info:tokenDetails")
    assert archive._no_history_why("scrape:x").startswith("scrape keeps no history: the protocol's page")
