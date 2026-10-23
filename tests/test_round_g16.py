"""ROUND G16 (Jake 2026-10-23, run on 19ef3a9) — two clean-ups. Nothing new is fetched.

1. the provisional latest day: a per-series list built from the morning-read evidence, not a 16:00 UTC clock;
2. Ether.fi's total supply Credibility row: "CoinGecko total = its circulating" only when ours and CoinGecko's disagree.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import store  # noqa: E402

LLAMA = "defillama:fees:fluid"


# ================================================================================================== 1 the list
def test_1_d_a_listed_series_is_provisional_at_1400_utc_an_unlisted_one_is_not(monkeypatch):
    monkeypatch.setattr(config, "PROVISIONAL_FROM_STORE", {})
    pv = config.provisional
    assert ("Fluid", "fees_usd") in config.provisional_series() and ("Morpho", "fees_usd") not in config.provisional_series()
    # at fetch time (no run history passed): yesterday of a listed series is provisional at 14:00 UTC — and at 23:00
    assert pv("Fluid", "fees_usd", LLAMA, "2026-10-22", "2026-10-23T14:00:00Z")
    assert pv("Fluid", "fees_usd", LLAMA, "2026-10-22", "2026-10-23T23:00:00Z")
    # an unlisted series from the same provider is not, whatever the hour
    assert not pv("Morpho", "fees_usd", "defillama", "2026-10-22", "2026-10-23T14:00:00Z")
    assert not pv("Morpho", "fees_usd", "defillama", "2026-10-22", "2026-10-23T07:00:00Z")
    # in the build: held until a second run after the day closed has read it
    one = [pd.Timestamp("2026-10-23T14:00:00")]
    two = one + [pd.Timestamp("2026-10-24T06:30:00")]
    assert pv("Fluid", "fees_usd", LLAMA, "2026-10-22", "2026-10-23T14:02:00Z", one)
    assert not pv("Fluid", "fees_usd", LLAMA, "2026-10-22", "2026-10-24T06:33:00Z", two)
    # a read made before the day closed is not a re-read of it
    assert pv("Fluid", "fees_usd", LLAMA, "2026-10-22", "2026-10-23T14:02:00Z",
              [pd.Timestamp("2026-10-22T20:00:00")] + one)


def _store(tmp_path, flags, runs=()):
    """A store with Review Queue flags [(run_id, project, metric, day, read)] and final values [(project, metric, day,
    final)] and run_log rows [(run_id, source, project)]."""
    st = store.Store(str(tmp_path / "metrics.db"))
    rq, fin = flags
    st.conn.executemany("INSERT INTO review_queue(run_id, ts, project, metric, date, value, reason, action, source) "
                        "VALUES (?, '', ?, ?, ?, ?, 'change_threshold', 'stored_flagged', 'defillama')", rq)
    st.conn.executemany("INSERT INTO metrics(date, project, metric, value, source, tier, fetched_at) "
                        "VALUES (?, ?, ?, ?, 'defillama', 1, '2026-10-23T18:00:00Z')",
                        [(d, p, m, v) for p, m, d, v in fin])
    st.conn.executemany("INSERT INTO run_log(run_id, ts, source, project, status) VALUES (?, '', ?, ?, 'ok')", runs)
    st.conn.commit()
    return st


def test_1_a_a_series_low_on_two_days_is_listed_one_low_once_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVISIONAL_FROM_STORE", {})
    rq = [("20261011T080000Z-a", "Near", "intents_revenue_usd", "2026-10-10", 5.0),     # 5% of final
          ("20261014T080000Z-b", "Near", "intents_revenue_usd", "2026-10-13", 8.0),     # a second day
          ("20261014T090000Z-c", "Near", "intents_revenue_usd", "2026-10-13", 9.0),     # same day again: still 2 days
          ("20261007T080000Z-d", "Near", "dex_volume_usd", "2026-10-06", 14.0),         # once: a one-off
          ("20261008T080000Z-e", "Morpho", "fees_usd", "2026-10-07", 95.0)]             # 95%: not low
    fin = [("Near", "intents_revenue_usd", "2026-10-10", 100.0), ("Near", "intents_revenue_usd", "2026-10-13", 100.0),
           ("Near", "dex_volume_usd", "2026-10-06", 100.0), ("Morpho", "fees_usd", "2026-10-07", 100.0)]
    st = _store(tmp_path, (rq, fin))
    days = store.low_read_days(st.conn, config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD)
    assert days == {("Near", "intents_revenue_usd"): 2, ("Near", "dex_volume_usd"): 1}
    config.set_provisional_from_store(days)
    listed = config.provisional_series()
    assert ("Near", "intents_revenue_usd") in listed and "2 day(s)" in listed[("Near", "intents_revenue_usd")]
    assert ("Near", "dex_volume_usd") not in listed and ("Morpho", "fees_usd") not in listed
    assert ("Fluid", "fees_usd") in listed and listed[("Fluid", "fees_usd")].startswith("declared")
    st.close()


def test_1_c_morning_reads_prints_the_list_and_each_series_count(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    monkeypatch.setattr(config, "PROVISIONAL_FROM_STORE", {})
    rq = [("20261011T080000Z-a", "Near", "intents_revenue_usd", "2026-10-10", 5.0),
          ("20261014T170000Z-b", "Near", "intents_revenue_usd", "2026-10-13", 8.0),
          ("20261007T080000Z-d", "Near", "dex_volume_usd", "2026-10-06", 14.0)]
    fin = [("Near", "intents_revenue_usd", "2026-10-10", 100.0), ("Near", "intents_revenue_usd", "2026-10-13", 100.0),
           ("Near", "dex_volume_usd", "2026-10-06", 100.0)]
    _store(tmp_path, (rq, fin)).close()
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "metrics.db"))
    coi.morning_reads()
    out = capsys.readouterr().out
    assert "THE PROVISIONAL LIST" in out
    assert "LISTED      Near / intents_revenue_usd: low on 2 day(s), 2 flag(s), hours 08,17" in out
    assert "not listed  Near / dex_volume_usd: low on 1 day(s), 1 flag(s), hours 08 — fewer than 2 days" in out
    assert "LISTED      Fluid / fees_usd: low on 0 day(s)" in out and "declared" in out


def test_1_the_build_releases_a_listed_day_once_a_second_run_has_read_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVISIONAL_FROM_STORE", {})
    st = _store(tmp_path, ([], []), runs=[("20261023T063000Z-a", "defillama", "Fluid"),
                                          ("20261023T104700Z-b", "defillama", "Fluid"),
                                          ("20261023T104700Z-b", "coingecko", "Fluid"),
                                          ("20261023T110000Z-c", "chain", "Fluid")])
    runs = store.provider_runs(st.conn, config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD)
    assert runs[("defillama", "Fluid")] == [pd.Timestamp("2026-10-23T06:30:00"), pd.Timestamp("2026-10-23T10:47:00")]
    assert ("chain", "Fluid") not in runs
    st.close()
    days = pd.date_range("2026-10-15", "2026-10-22")
    g = pd.DataFrame({"date": days, "project": "Fluid", "metric": "fees_usd", "value": 100.0, "tier": 1,
                      "source": "defillama", "is_manual": False, "entered_on": "",
                      "fetched_at": ["2026-10-22T06:33:00Z"] * 7 + ["2026-10-23T06:33:00Z"]})
    monkeypatch.setattr(bw, "PROVIDER_RUNS", {("defillama", "Fluid"): runs[("defillama", "Fluid")][:1]})
    groups = {("Fluid", "fees_usd"): g.copy()}
    bw._provisional_views(groups)
    assert bw.PROVISIONAL[("Fluid", "fees_usd")] == [("2026-10-22", "2026-10-23T06:33:00Z")]
    # the 10:47 run re-read it: released
    monkeypatch.setattr(bw, "PROVIDER_RUNS", {("defillama", "Fluid"): runs[("defillama", "Fluid")]})
    groups = {("Fluid", "fees_usd"): g.assign(fetched_at=["2026-10-22T06:33:00Z"] * 7 + ["2026-10-23T10:50:00Z"])}
    bw._provisional_views(groups)
    assert ("Fluid", "fees_usd") not in bw.PROVISIONAL and len(groups[("Fluid", "fees_usd")]) == 8


def test_1_an_unlisted_series_latest_day_is_used_whatever_the_hour(monkeypatch):
    monkeypatch.setattr(config, "PROVISIONAL_FROM_STORE", {})
    monkeypatch.setattr(bw, "PROVIDER_RUNS", {})
    g = pd.DataFrame({"date": pd.date_range("2026-10-20", "2026-10-22"), "project": "Morpho", "metric": "fees_usd",
                      "value": 1.0, "tier": 1, "source": "defillama", "is_manual": False, "entered_on": "",
                      "fetched_at": "2026-10-23T07:00:00Z"})
    groups = {("Morpho", "fees_usd"): g}
    bw._provisional_views(groups)
    assert len(groups[("Morpho", "fees_usd")]) == 3 and not bw.PROVISIONAL


# ================================================================================================== 2 Ether.fi
def test_2_coingecko_total_equal_to_ours_is_judged_not_labelled():
    import credibility
    rows = {"Ether.fi|total_supply": {"now": 998_535_999.0},
            "Ether.fi|total_supply_coingecko": {"now": 998_535_999.0},
            "Ether.fi|circulating_supply": {"now": 998_535_999.0, "source": "coingecko"}}
    spec = credibility.wiring_inputs("Ether.fi", rows)["in_total_supply_coingecko"]
    assert "force_verdict" not in spec["ref"] and spec["ref"]["tol"] == 1.0


def test_2_coingecko_total_disagreeing_and_equal_to_its_circulating_keeps_the_label():
    import credibility
    rows = {"Ether.fi|total_supply": {"now": 998_535_999.0},
            "Ether.fi|total_supply_coingecko": {"now": 965_350_000.0},
            "Ether.fi|circulating_supply": {"now": 965_350_000.0, "source": "coingecko"}}
    spec = credibility.wiring_inputs("Ether.fi", rows)["in_total_supply_coingecko"]
    assert spec["ref"]["force_verdict"] == "UNVERIFIABLE — reference unreliable: CoinGecko total = its circulating"
    # within 1% of ours: judged (0.5% apart)
    rows["Ether.fi|total_supply_coingecko"]["now"] = rows["Ether.fi|circulating_supply"]["now"] = 998_535_999.0 * 0.995
    assert "force_verdict" not in credibility.wiring_inputs("Ether.fi", rows)["in_total_supply_coingecko"]["ref"]
