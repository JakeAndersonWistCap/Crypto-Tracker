"""ROUND D3 (Jake 2026-10-26, head 483d926) — Jake's decisions on D2.

1 NEAR's lockup rule takes a read up to 30 days old, AMBER (its age and the LOCKED bound) after 10, BLOCKED after 30;
2 the lockup read on its own weekly task, one read at a time, daily_run's step 2b the 8-day fallback; 3 the daily task
capped at 6 h by default; 5 Pendle's fees / revenue read from DefiLlama's V2 child (Boros out, once-only clean-up) and
Pendle's own epoch legs stored for the revenue cross-check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


# ================================================================================================== 1
def _views(monkeypatch, read_on):
    from test_round_g18 import _near_groups
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    g = _near_groups(read_on=read_on)
    bw._near_lockup_views(g)
    return g


@pytest.mark.parametrize("read_on,age,amber,blocked", [("2026-10-20", 6, False, False),
                                                        ("2026-10-16", 10, False, False),
                                                        ("2026-10-15", 11, True, False),
                                                        ("2026-10-11", 15, True, False),
                                                        ("2026-09-26", 30, True, False),
                                                        ("2026-09-20", 36, False, True)])
def test_1_a_read_up_to_30_days_forms_the_rule_amber_after_10_blocked_after_30(monkeypatch, read_on, age, amber,
                                                                                 blocked):
    g = _views(monkeypatch, read_on)
    assert bw.NEAR_LOCKUP_MAX_AGE_DAYS == 30 and bw.NEAR_LOCKUP_FRESH_DAYS == 10
    s = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    if blocked:
        assert s.index.max() == pd.Timestamp(read_on) + pd.Timedelta(days=30)
        assert bw._VIEW_BLOCKS[("Near", "circulating_supply_used")].startswith("BLOCKED")
        assert ("Near", "circulating_supply_used") not in bw.LOCKUP_AGE_AMBER
        return
    assert s.index.max() == pd.Timestamp("2026-10-26") and f"lockups read {read_on}" in s.iloc[-1]["source"]
    assert ("Near", "circulating_supply_used") not in bw._VIEW_BLOCKS
    for m in ("circulating_supply_lockup_rule", "circulating_supply_used", "market_cap_usd"):
        assert ((("Near", m) in bw.LOCKUP_AGE_AMBER) == amber), m
    if amber:
        why = bw.LOCKUP_AGE_AMBER[("Near", "market_cap_usd")]
        assert f"lockups read {read_on}, {age} days old on 2026-10-26" in why
        # THE BOUND, NOT A RATE (Jake: the ~38K/day rate came from a partial read): at most LOCKED, as a share of supply
        assert "at most LOCKED itself (2,058,522 NEAR, 0.16% of total supply)" in why
        assert "/day" not in why and "a day" not in why


def test_1_b_the_amber_reason_reaches_the_cell_band(monkeypatch):
    _views(monkeypatch, "2026-10-11")
    row = {"status": "ok", "n_points": 30, "source": "derived:circulating_used[circulating_supply_lockup_rule]"}
    band, why = bw.confidence_for("Near", "circulating_supply_used", row, pd.Timestamp("2026-10-26"))
    assert band == "AMBER" and "15 days old" in why and "0.16% of total supply" in why
    _views(monkeypatch, "2026-10-20")
    band, why = bw.confidence_for("Near", "circulating_supply_used", row, pd.Timestamp("2026-10-26"))
    assert "days old" not in why


# ================================================================================================== 2
def test_2_a_the_lockup_task_is_weekly_sunday_0215_4h_wake_and_its_own_log():
    reg = (ROOT / "windows" / "register_lockup_task.ps1").read_text(encoding="utf-8")
    assert '[string]$TaskName = "Crypto-Tracker NEAR lockups"' in reg
    assert '[string]$At = "02:15"' in reg and '[string]$Day = "Sunday"' in reg
    assert "New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At" in reg
    assert "-WakeToRun -StartWhenAvailable" in reg and "[int]$LimitHours = 4" in reg
    assert "-ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances IgnoreNew" in reg
    assert "near_lockups_run.ps1" in reg and "-LogonType Interactive" in reg
    run = (ROOT / "windows" / "near_lockups_run.ps1").read_text(encoding="utf-8")
    assert '"near_lockups_{0}.log"' in run and '@("near_lockups_weekly.py")' in run
    assert "[System.IO.FileMode]::Append" in run and "$w.AutoFlush = $true" in run      # streamed, line by line
    assert 'near lockups FINAL: exit $code' in run and '$env:PYTHONUNBUFFERED = "1"' in run
    for f in ("register_lockup_task.ps1", "near_lockups_run.ps1", "register_task.ps1", "daily_run.ps1"):
        txt = (ROOT / "windows" / f).read_text(encoding="utf-8")
        new = [ln for ln in txt.splitlines() if "D3" in ln or f != "daily_run.ps1"]
        assert not any(ord(ch) > 127 for ln in new for ch in ln), f + ": PowerShell 5.1 reads it as ANSI"


def test_2_b_daily_run_keeps_step_2b_as_the_8_day_fallback():
    ps = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    assert '[void](Invoke-Step "NEAR lockups (weekly)" @("near_lockups_weekly.py", "--every-days", "8"))' in ps


def test_2_c_every_days_gates_the_read(monkeypatch, tmp_path, capsys):
    import near_lockups_weekly as nl
    import check_offline_items as coi
    import store as sm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "lc"))
    monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "m.db"))
    sm.Store(str(tmp_path / "m.db")).close()
    day = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None) - pd.Timedelta(days=7)
    monkeypatch.setattr(nl, "last_read", lambda db, csv=None: day)
    calls = []
    monkeypatch.setattr(coi, "near_lockups", lambda: calls.append(1) or None)
    assert nl.main(["--every-days", "8"]) == 0 and calls == []          # 7 days old: the fallback waits
    assert "next due" in capsys.readouterr().out
    assert nl.main([]) == 1 and calls == [1]                            # the Sunday task (7): it reads


def test_2_d_one_read_at_a_time(monkeypatch, tmp_path):
    import json
    import os
    import near_lockups_weekly as nl
    lock = tmp_path / "near-lockups.lock"
    now = pd.Timestamp("2026-11-01 03:00")
    assert nl.take_lock(lock, now) == ""
    assert json.loads(lock.read_text())["pid"] == os.getpid()
    other = os.getppid()                                                # a live process that is not us
    lock.write_text(json.dumps({"pid": other, "since": "2026-11-01 02:15:00"}))
    held = nl.take_lock(lock, now)
    assert held.startswith(f"another lockup read (PID {other})")
    assert nl.take_lock(lock, pd.Timestamp("2026-11-01 08:16")) == ""     # older than LOCK_MAX_H: taken over
    lock.write_text(json.dumps({"pid": 2 ** 22 + 12345, "since": "2026-11-01 02:15:00"}))
    assert nl.take_lock(lock, now) == ""                                  # its process is gone: taken over
    nl.release_lock(lock)
    assert not lock.exists()


def test_2_e_a_second_copy_exits_0_without_reading(monkeypatch, tmp_path, capsys):
    import json
    import os
    import near_lockups_weekly as nl
    import check_offline_items as coi
    import store as sm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "m.db"))
    sm.Store(str(tmp_path / "m.db")).close()
    (tmp_path / "near-lockups.lock").write_text(json.dumps({"pid": os.getppid(),
                                                            "since": str(pd.Timestamp.now(tz="UTC").tz_localize(None))}))
    monkeypatch.setattr(coi, "near_lockups", lambda: (_ for _ in ()).throw(AssertionError("read while locked")))
    assert nl.main(["--force"]) == 0 and "nothing read by this copy" in capsys.readouterr().out


# ================================================================================================== 3
def test_3_the_daily_task_defaults_to_a_6h_cap():
    reg = (ROOT / "windows" / "register_task.ps1").read_text(encoding="utf-8")
    assert "[int]$LimitHours = 6" in reg and "0 = no limit (not the default" in reg
    assert "-ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours)" in reg
    assert "register_lockup_task.ps1" in reg


# ================================================================================================== 5
def test_5a_pendle_reads_defillamas_v2_child_and_boros_stays_on_its_own_line():
    p = config.PROJECT_BY_NAME["Pendle"]
    assert p["defillama_fees_slug"] == "pendle-v2" and p["defillama_protocol"] == "pendle"
    assert p["defillama_component_slugs"] == [{"slug": "boros", "data_type": "dailyFees", "metric": "boros_fees_usd"}]


def test_5a_the_clean_up_runs_once_and_never_touches_the_re_read_rows(tmp_path, monkeypatch):
    import json
    import logging
    import store as sm
    import token_metrics as tm
    from fetch.logcache import DailyChecks
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] == "D3-5"))
    assert {c["metric"] for c in config.RUN_CLEANUPS} == {"fees_usd", "revenue_usd", "holders_revenue_usd"}
    assert all(c.get("once") and c["source"] == "defillama" for c in config.RUN_CLEANUPS)
    dc = DailyChecks()
    dc.f.parent.mkdir(parents=True, exist_ok=True)
    dc.f.write_text(json.dumps({"defillama:project:Pendle": "2026-10-27",
                                "defillama:project:Pendle:metrics": "fees_usd,holders_revenue_usd,revenue_usd"}))
    st = sm.Store(str(tmp_path / "metrics.db"))
    parent = [{"date": d, "project": "Pendle", "metric": m, "value": v, "source": "defillama", "tier": 1}
              for d in ("2025-09-08", "2026-10-01") for m, v in (("fees_usd", 9_043.0), ("revenue_usd", 8_765.0),
                                                                  ("holders_revenue_usd", 6_576.0))]
    keep = [{"date": "2026-10-01", "project": "Pendle", "metric": "boros_fees_usd", "value": 546.0,
             "source": "defillama:boros", "tier": 1},
            {"date": "2026-10-01", "project": "Aave", "metric": "revenue_usd", "value": 1.0, "source": "defillama",
             "tier": 1}]
    st.upsert(pd.DataFrame(parent + keep))
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    left = pd.read_sql("SELECT project, metric FROM metrics", st.conn)
    assert sorted(map(tuple, left.values.tolist())) == [("Aave", "revenue_usd"), ("Pendle", "boros_fees_usd")]
    assert sum("applied once — 2 row(s) of Pendle / revenue_usd from defillama removed" in x for x in lines) == 1
    assert sum(x.startswith("CLEAN-UP D3-5: removed Pendle / ") for x in lines) == 6
    assert dc.get("defillama:project:Pendle") is None               # D2-7: the DefiLlama tier reads Pendle this run
    # THE SAME RUN RE-READS THE SERIES FROM THE CHILD, UNDER THE SAME LABEL: never removed again
    st.upsert(pd.DataFrame([{"date": "2026-10-01", "project": "Pendle", "metric": "revenue_usd", "value": 8_219.44,
                             "source": "defillama", "tier": 1}]))
    assert tm.apply_run_cleanups(st, "r2", logging.getLogger("t")) == []
    assert pd.read_sql("SELECT value FROM metrics WHERE metric='revenue_usd' AND project='Pendle'",
                       st.conn)["value"].tolist() == [8_219.44]
    marks = pd.read_sql("SELECT source FROM fetch_status WHERE project='Pendle'", st.conn)["source"].tolist()
    assert {"cleanup:D3-5:fees_usd", "cleanup:D3-5:revenue_usd", "cleanup:D3-5:holders_revenue_usd"} <= set(marks)
    st.close()


def test_5a_a_store_without_the_rows_records_the_marker_and_returns_nothing(tmp_path, monkeypatch):
    import logging
    import store as sm
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] == "D3-5"))
    st = sm.Store(str(tmp_path / "metrics.db"))
    assert tm.apply_run_cleanups(st, "r", logging.getLogger("t")) == []
    st.upsert(pd.DataFrame([{"date": "2026-10-01", "project": "Pendle", "metric": "fees_usd", "value": 1.0,
                             "source": "defillama", "tier": 1}]))
    assert tm.apply_run_cleanups(st, "r2", logging.getLogger("t")) == []         # once: a later row is the child's
    assert len(pd.read_sql("SELECT * FROM metrics", st.conn)) == 1
    st.close()


def _sp(ts, **cols):
    return {"sPendleHistoricalData": {"timestamps": ts, **cols}}


def test_5b_pt_looping_a_fee_above_0_is_a_reading_a_0_only_beside_a_volume():
    from fetch.base import FetchOutput
    from fetch.pendle_epochs import PendleEpochs
    spec = config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]
    T = [int(pd.Timestamp(d).timestamp()) for d in ("2026-07-28", "2026-08-11", "2026-08-25", "2026-09-08",
                                                    "2026-10-20")]
    payload = _sp(T, ptLoopingFees=[0, 11_789.38, 0, 1_285.76, 2_810.0],
                  ptLoopingVolumes=[None, None, 1_000.0, 2_360_411.6, 12_830_865.8])
    out = FetchOutput()
    PendleEpochs._pt_looping("Pendle", spec, payload, pd.Timestamp("2026-10-26"), out)
    f = pd.concat(out.frames, ignore_index=True)
    got = dict(zip(pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d"), f["value"]))
    # 07-28: 0 without a volume — not tracked yet, not stored; 08-25: a measured 0; 10-20: in progress
    assert got == {"2026-08-11": pytest.approx(11_789.38), "2026-08-25": 0.0, "2026-09-08": pytest.approx(1_285.76)}
    assert "1 earlier epoch(s) before tracking, not stored" in out.log[-1].message


def test_5b_market_revenue_is_protocol_plus_realized_yt_for_closed_epochs_only():
    from fetch.base import FetchOutput
    from fetch.pendle_epochs import PendleEpochs
    spec = config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]
    assert spec["market_fees_url"].startswith("https://api-v2.pendle.finance/core/v1/statistics/market-fees")
    tot = lambda p, y: {"protocolFeeUsd": p, "realizedYtFeeUsd": y, "poolFeeUsd": 9e9}   # noqa: E731
    mf = {"epochs": [
        {"epochStart": "2026-10-20T00:00:00.000Z", "isOngoing": True, "totals": tot(1.0, 1.0), "markets": []},
        {"epochStart": "2026-10-06T00:00:00.000Z", "isOngoing": False, "totals": tot(63_225.0, 186_396.0),
         "markets": [{}] * 330},
        {"epochStart": "2026-09-22T00:00:00.000Z", "isOngoing": False, "totals": {"protocolFeeUsd": 1.0},
         "markets": []}]}
    out = FetchOutput()
    PendleEpochs(get=lambda url: mf)._market_revenue("Pendle", spec, pd.Timestamp("2026-10-26"), out)
    f = pd.concat(out.frames, ignore_index=True)
    assert f["date"].tolist() == [pd.Timestamp("2026-10-06")] and f["value"].tolist() == [249_621.0]
    assert "NOT STORED (a field missing): 2026-09-22" in out.log[-1].message


def _long(rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": "Pendle", "metric": m, "value": float(v),
                          "source": "x"} for d, m, v in rows])


def test_5b_the_cross_check_names_its_legs_and_its_verdict_basis_is_unchanged():
    import credibility as cr
    rows = []
    for e, (rev, mk, lp, ours) in {"2026-09-08": (272_997, 248_158, 1_286, 220_100),
                                   "2026-09-22": (191_655, 174_706, 2_977, 178_853)}.items():
        rows += [(e, "pendle_epoch_revenue_usd", rev), (e, "pendle_epoch_market_revenue_usd", mk),
                 (e, "pendle_pt_looping_fees_usd", lp)]
        rows += [(d, "revenue_usd", ours / 14) for d in pd.date_range(e, periods=14)]
    long = _long(rows)
    args = config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["args"]
    assert args["markets"] == "pendle_epoch_market_revenue_usd" and args["looping"] == "pendle_pt_looping_fees_usd"
    assert config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["tol"] == 10.0              # the tolerance is unchanged
    ours = cr._pendle_epoch_revenue("Pendle", None, long, pd.Timestamp("2026-10-10"), **{**args, "side": "ours"})
    ref = cr._pendle_epoch_revenue("Pendle", None, long, pd.Timestamp("2026-10-10"), **args)
    assert ours[0] == pytest.approx(398_953) and ref[0] == pytest.approx(464_652)        # the same comparison as D2-5
    how = ref[2]
    assert "Pendle 464,652 = per-market protocol + realized YT fee 422,864 + PT-looping fee 4,263" in how
    assert "an epoch-level remainder attributed to no market 37,525" in how
    assert "Ours 398,953 against the per-market leg alone: -5.7%" in how
    # an epoch without a leg is named, never split with a 0
    long2 = long[~((long["metric"] == "pendle_pt_looping_fees_usd") & (long["date"] == pd.Timestamp("2026-09-08")))]
    how2 = cr._pendle_epoch_revenue("Pendle", None, long2, pd.Timestamp("2026-10-10"), **args)[2]
    assert "over the 1 epoch(s) with every leg stored, 2026-09-22..2026-09-22" in how2
    assert "Legs not split for 2026-09-08" in how2
    note = config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["note"]
    assert "DefiLlama does count airdrops" in note and "Boros out since D3-5a" in note
