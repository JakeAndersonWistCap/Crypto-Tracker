"""Round 15 (Jake 2026-11-20): his decisions on the R14 report, for Ether.fi —
  1 (b) the theatre flag SCORES the wider reading (any buyback); 2 (b) its state and size from 2026-09-08, the named
  outflow from 2026-07-01; 3 (b) fees net of the cashback everywhere; 4 (b) a wallet purchase after the destinations
  were read is unknown until measured; 5 (b) the named outflow is its own dilution category (f), taken out of (e);
  6 (a) the team leg stays in (e) — and no "a year" figure annualises a schedule that ends inside the year.
"""
import pandas as pd
import pytest

import build_workbook as bw
import config

EF = "Ether.fi"
COLS = ["date", "project", "metric", "value", "tier", "source"]


def _frame(metric, rows, project=EF, source="derived:test"):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "project": project, "metric": metric,
                         "value": [float(v) for _, v in rows], "tier": 2, "source": source})[COLS]


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[EF], config.PROJECT_BY_NAME["Morpho"]])


# ----- 1, 2 ----------------------------------------------------------------------------------------------------------
def test_1_2_the_flag_scores_any_buyback_from_the_programme_start_and_counts_each_purchase_once(scoped):
    spec = config.THEATRE_FLAG[EF]
    assert spec["scored"] == "any_buyback" and spec["size_window"]["from"] == "2026-09-08"
    assert spec["window"]["from"] == "2026-07-01"                     # the named outflow keeps its window
    asof = pd.Timestamp("2026-11-19")
    days = pd.date_range("2026-06-01", asof)
    sent = {pd.Timestamp("2026-07-20"): 7_100_020.0, pd.Timestamp("2026-10-12"): 1_200_010.0}
    prog = {d: 100.0 for d in days if d >= pd.Timestamp("2026-09-08")}
    leg_point = config.BUYBACK_LEGS[EF]["points"][0]
    g = {(EF, spec["outflow_metric"]): _frame(spec["outflow_metric"], [(d, sent.get(d, 0.0)) for d in days]),
         (EF, spec["retired_metric"]): _frame(spec["retired_metric"], [(d, 0.0) for d in days]),
         # the programme has ALREADY joined actual_buyback (R14 BUYBACK_LEGS): its own series is not added again
         (EF, "actual_buyback_tokens"): _frame("actual_buyback_tokens", [(d, prog.get(d, 0.0)) for d in days],
                                               source=leg_point),
         (EF, "programmatic_buyback_tokens"): _frame("programmatic_buyback_tokens",
                                                     [(d, v) for d, v in prog.items()], source="explorer:programmatic")}
    bw._theatre_flag_views(g, asof)
    r = bw.THEATRE_RESULTS[EF]
    assert r["named_sent"] == 8_300_030.0 and r["sent"] == 1_200_010.0
    assert r["bought"] == pytest.approx(100.0 * len(prog))              # once, not twice
    assert r["any_buyback"]["state"] == "RAISED" and r["as_written"]["state"] == "NOT RAISED"
    assert bw.theatre_flag_size(EF) == f"={1_200_010.0 / r['bought']!r}"
    assert bw.theatre_flag_text(EF).startswith("SCORED — IF ANY BUYBACK COUNTS: RAISED")


# ----- 3 -------------------------------------------------------------------------------------------------------------
def test_3_every_fee_cell_of_ether_fi_reads_the_net_figure():
    assert config.metric_alias(EF, "fees_usd") == "fees_net_cashback_usd"
    assert config.MASTER_FEES_OVERRIDE[EF][0] == "fees_net_cashback_usd"
    assert config.metric_alias("Uniswap", "fees_usd") == "fees_usd"            # no other project's fees move
    R = bw.Refs(10, 0, [])
    bw._PROJECT["name"] = EF
    try:
        assert "fees_net_cashback_usd" in R.D(5, "fees_usd", "q0")
    finally:
        bw._PROJECT["name"] = None


# ----- 4 -------------------------------------------------------------------------------------------------------------
def test_4_a_purchase_after_the_read_is_unknown_and_history_keeps_its_split():
    assert config.buyback_destination_shares(EF, "2026-11-20") is None
    assert config.buyback_destination_shares(EF, "2027-01-15") is None
    last = config.buyback_destination_shares(EF, "2026-11-19")
    assert last is not None and last["distribute"] == 1.0                      # the 2026-02-09 measured row
    assert config.buyback_destination_shares(EF, "2025-12-03")["hold"] == 1.0  # a measured day: unchanged


# ----- 5 -------------------------------------------------------------------------------------------------------------
def test_5_the_named_outflow_is_category_f_taken_out_of_e_from_the_stock_senders_only():
    assert config.OUTFLOW_CATEGORY_NAMES["f"] == "named float outflow"
    assert config.outflow_metrics(EF, "f") == ("etherfi_named_float_outflow_tokens",)
    assert config.release_less(EF)[0] == ("etherfi_named_float_outflow_tokens",)
    assert "f" in bw.CP_DILUTION_CATS and "f" in bw._cp_cats(config.PROJECT_BY_NAME[EF])
    assert all(not config.outflow_metrics(p["name"], "f") for p in config.PROJECTS if p["name"] != EF)
    scan = next(s for s in config.PROJECT_BY_NAME[EF]["log_scans"] if s["key"] == "named_float_outflow")
    holders = {h.lower() for h in scan["holders"]}
    assert "0x9eac7114d1a1eabc4732a886795cfd9e6e35843f" not in holders        # the deployer EOA: already in the float
    assert "0x3fb6784e263643656f386a0371644931133d7b78" in holders            # the Operating Safe: in the stock
    assert scan["count_to"] == next(s for s in config.PROJECT_BY_NAME[EF]["log_scans"]
                                    if s["key"] == "theatre_exchange_out")["count_to"]
    assert scan["own_tier"].endswith("@" + EF)


# ----- 6 -------------------------------------------------------------------------------------------------------------
def test_6_the_ending_schedule_counts_only_what_the_next_365_days_release(scoped):
    asof = pd.Timestamp("2026-11-19")
    days = pd.date_range("2026-08-01", asof)
    leg = config.SCHEDULE_LOCK_LEGS[EF]
    g = {(EF, leg["metric"]): _frame(leg["metric"], [(d, leg["fn"](d)) for d in days]),
         (EF, "price_usd"): _frame("price_usd", [(d, 0.5) for d in days])}
    bw._ending_schedule_views(g)
    rel = g[(EF, "schedule_release_tokens")].set_index("date")["value"]
    assert rel[pd.Timestamp("2026-10-18")] == pytest.approx(214_700_000 / 36)  # the monthly fall, on its day
    assert rel[pd.Timestamp("2026-10-19")] == 0.0
    fwd = g[(EF, "schedule_forward_365_tokens")].set_index("date")["value"][asof]
    assert fwd == pytest.approx(leg["fn"](asof)) and fwd == pytest.approx(23_855_555.6, abs=1)   # all that is left
    assert bw.ENDING_SCHEDULE_ROWS.get(EF)
    # the formula: the leg's trailing falls are replaced by MIN(them, the 365 days ahead)
    R = bw.Refs(10, 0, [])
    f = bw._annualise(R, 5, config.PROJECT_BY_NAME[EF], "outflow_e_tokens", "X")
    assert "schedule_release_tokens" in f and "schedule_forward_365_tokens" in f and "MIN(" in f
    usd = bw._annualise(R, 5, config.PROJECT_BY_NAME[EF], "outflow_e_usd", "X")
    assert "schedule_release_usd" in usd and "price_usd" in usd
    assert "MIN(" not in bw._annualise(R, 5, config.PROJECT_BY_NAME[EF], "revenue_usd", "X")   # other series as before


def test_6_every_ending_schedule_is_listed_and_a_full_year_ahead_moves_nothing(scoped):
    assert set(config.ENDING_SCHEDULES) == {EF, "Morpho"}
    for name, es in config.ENDING_SCHEDULES.items():
        assert es["leg"] in config.unlock_stock_metrics(name), name          # its falls are releases
    # Morpho on 2026-10-13 (the dumps): the relock runs past a year ahead — forward = trailing, the MIN keeps it
    m = config.ENDING_SCHEDULES["Morpho"]
    d = pd.Timestamp("2026-10-13")
    daily = m["fn"](d - pd.Timedelta(days=1)) - m["fn"](d)
    assert m["fn"](d) - m["fn"](d + pd.Timedelta(days=365)) == pytest.approx(daily * 365, rel=1e-3)
    # from 2026-11-22 the year ahead reaches past its end (2027-11-21): what remains is the cap
    late = pd.Timestamp("2027-06-01")
    assert m["fn"](late) - m["fn"](late + pd.Timedelta(days=365)) == pytest.approx(m["fn"](late))


# ----- add 1: the top-ups are holder value RECORDED, not credited -------------------------------------------------------
def test_add1_the_top_ups_show_as_recorded_holder_value_never_credited(scoped):
    days = pd.date_range("2026-10-01", "2026-10-10")
    g = {(EF, "sethfi_issuer_topup_tokens"): _frame("sethfi_issuer_topup_tokens",
                                                    [(d, 1000.0 if d.day == 5 else 0.0) for d in days]),
         (EF, "price_usd"): _frame("price_usd", [(d, 0.6) for d in days])}
    bw._holder_value_views(g)
    other = g[(EF, "holder_value_other_usd")].set_index("date")["value"]
    credit = g[(EF, "holder_value_revenue_usd")].set_index("date")["value"]
    assert other[pd.Timestamp("2026-10-05")] == pytest.approx(600.0) and other.sum() == pytest.approx(600.0)
    assert credit.sum() == 0.0                                                 # never credited
    assert EF in config.METRICS["holder_value_other_usd"]["only_projects"]


# ----- add 5: Aerodrome's subsidy targeting after the migration ----------------------------------------------------------
def test_add5_an_ended_route_stores_no_zero_and_the_cells_read_stale(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    st = config.PROJECT_BY_NAME["Aerodrome"]["subsidy_targeting"]
    assert st["route_ends"] == "2026-10-22"
    eps = ["2026-10-01", "2026-10-08", "2026-10-22", "2026-10-29"]
    g = {("Aerodrome", "subsidy_fee_cover_pools"): _frame("subsidy_fee_cover_pools", [(d, 0.5) for d in eps[:2]],
                                                          project="Aerodrome"),
         ("Aerodrome", "subsidy_emissions_usd"): _frame("subsidy_emissions_usd",
                                                        [(eps[0], 3e6), (eps[1], 3.7e6), (eps[2], 0.0), (eps[3], 0.0)],
                                                        project="Aerodrome")}
    bw._subsidy_route_end_views(g, pd.Timestamp("2026-11-20"))
    kept = pd.to_datetime(g[("Aerodrome", "subsidy_emissions_usd")]["date"])
    assert kept.max() == pd.Timestamp("2026-10-08")                            # the false zeros are refused
    txt = bw.SUBSIDY_ROUTE_STALE["Aerodrome"]
    assert txt.startswith("n/a — STALE") and "2026-10-08" in txt and "2026-10-22" in txt
    # on the dumps (as of 2026-10-13) the route has not ended: nothing is stale, nothing moves
    bw._subsidy_route_end_views(g, pd.Timestamp("2026-10-13"))
    assert "Aerodrome" not in bw.SUBSIDY_ROUTE_STALE


def test_add5_the_reader_does_not_read_or_store_an_epoch_after_the_route_ended(monkeypatch, tmp_path):
    import fetch.subsidy_targeting as stg
    from fetch.base import FetchOutput
    spec = dict(config.PROJECT_BY_NAME["Aerodrome"]["subsidy_targeting"], dir=str(tmp_path))
    end = int(pd.Timestamp(spec["route_ends"]).tz_localize("UTC").timestamp())

    class _Rpc:
        def head(self):
            return 1, end + 3 * 7 * 86400 + 3600                                  # three epochs past the end
    f = stg.SubsidyTargeting.__new__(stg.SubsidyTargeting)
    f.rpc_factory = lambda url, deadline: _Rpc()
    f.clock = lambda: 0.0
    called = []
    monkeypatch.setattr(f, "_restore", lambda out, name, spec_, epochs, src: called.append(list(epochs)) or [],
                        raising=False)
    out = FetchOutput()
    f._aerodrome("Aerodrome", dict(spec, epochs_back=4), out, 1e9)
    assert called and all(ep < end for ep in called[0])                          # only epochs before the end
    assert any("ROUTE ENDED 2026-10-22" in e.message for e in out.log)
