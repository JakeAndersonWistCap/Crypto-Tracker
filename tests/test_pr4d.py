"""Project round 4D — fix the 4C fetch regression, finish Morpho (Jake 2026-11-12). No network."""
import sqlite3
import threading
import time

import config


# ===================================================================================== 1: the connection-slot deadlock
def test_1_a_nested_slot_on_the_same_host_never_deadlocks():
    """4C wrapped every RPC post in a host slot, and requests' patched send took a second one inside it: with every slot
    held by an outer acquire, the inner ones waited forever (Jake's 18:50 run: 3 calls answered, 184 queued)."""
    import fetch.base as b
    done = []

    def work():
        with b.connection_slot("deadlock.test"):
            with b.connection_slot("deadlock.test"):
                time.sleep(0.02)
        done.append(1)
    ts = [threading.Thread(target=work, daemon=True) for _ in range(3 * b.MAX_CONN_PER_HOST)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(done) == len(ts)
    src = open("fetch/chain.py", encoding="utf-8").read()
    assert "with connection_slot(rpc_host(url)):" not in src                # the outer slot is gone


def test_2_a_timeout_says_the_time_of_every_phase():
    import fetch.base as b
    t = threading.Thread(target=lambda: (b.set_phase("phase one"), time.sleep(0.05), b.set_phase("phase two")),
                         name="phase.test")
    t.start()
    t.join()
    got = b.phase_times("phase.test")
    assert got.startswith("phase one 0s; phase two") and got.endswith("(still in the last)")
    # PR4D.2: the per-holder balanceOf at the pinned block stays — it is the proof (a balance moved without a Transfer
    # event is refused: test_adapters' reconcile tests); summing is already incremental from the last proven block
    src = open("fetch/logscan.py", encoding="utf-8").read()
    assert "no balanceOf read" not in src and "incremental from block" in src


# ========================================================================== 3: a switch leg on an earlier wallet set
def test_3_rows_on_an_earlier_wallet_set_are_removed_before_the_catch_up(tmp_path, monkeypatch):
    import store as sm
    import token_metrics as tm
    m = "morpho_undistributed_tokens"
    now = "chain:sum(" + "+".join(sorted(config.current_set_point("Morpho", m))) + ")"
    old = "chain:sum(ethereum:undist_rewards_multisig+ethereum:undist_urd)"
    assert config.on_current_set("Morpho", m, now + ":archive") and not config.on_current_set("Morpho", m, old)
    assert {c["metric"] for c in config.RUN_CLEANUPS if c["id"] == "PR4D.3"} == {
        "morpho_undistributed_tokens", "morpho_released_held_tokens"}
    st = sm.Store(str(tmp_path / "m.db"))
    try:
        cols = "date, project, metric, value, source, tier, fetched_at"
        st.conn.executemany(f"INSERT INTO metrics ({cols}) VALUES (?,?,?,?,?,?,?)", [
            ("2026-11-11", "Morpho", m, 3.0, now + ":archive", 2, "t"),
            ("2026-11-12", "Morpho", m, 2.7, old, 2, "t")])
        st.conn.commit()

        class _DC:
            def forget_series(self, *a):
                return []
        monkeypatch.setattr("fetch.logcache.DailyChecks", _DC)

        class _Log:
            def __getattr__(self, k):
                return lambda *a, **kw: None
        lines = tm.apply_run_cleanups(st, "run", _Log())
        left = st.conn.execute("SELECT substr(date,1,10) FROM metrics WHERE metric=?", (m,)).fetchall()
        assert left == [("2026-11-11",)] and any("CLEAN-UP PR4D.3" in x for x in lines)
    finally:
        st.close()


def test_3_circ_legs_names_the_days_missing_and_on_another_set(tmp_path):
    import circ_legs
    from fetch.archive import catch_up_bounds
    lo, y = catch_up_bounds()
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT)")
    d1, d2 = str(y.date()), str((y - __import__("pandas").Timedelta(days=1)).date())
    now = "chain:sum(" + "+".join(sorted(config.current_set_point("Morpho", "morpho_undistributed_tokens"))) + ")"
    for d in (d1, d2):
        for mtr in ("total_supply", "noncirculating_holding_tokens"):
            conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (d, "Morpho", mtr, 1.0, "chain"))
    conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (d1, "Morpho", "morpho_undistributed_tokens", 1.0, now))
    conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (d2, "Morpho", "morpho_undistributed_tokens", 1.0,
                                                            "chain:sum(ethereum:undist_urd)"))
    conn.commit()
    out = "\n".join(circ_legs.report(conn, "Morpho"))
    assert "morpho_undistributed_tokens [switch (subtracted when held)]: 1 of 2 day(s) held" in out
    assert f"ON ANOTHER WALLET SET 1: {d2}" in out
    assert "morpho_released_held_tokens [switch also needs]: 0 of 2 day(s) held; MISSING 2" in out
    assert "morpho_relocked_tokens [subtract]: built at read time (no stored rows) — MODELLED" in out


# =============================================================================== 4: partly modelled, never VERIFIED
def test_4_a_circulating_built_on_the_modelled_relock_is_never_green():
    import pandas as pd

    import build_workbook as bw
    assert config.is_modelled("modelled:relock_schedule[2025-11-21..2027-11-21]")
    pm = config.partly_modelled("Morpho", "circulating_supply_used")
    assert pm.startswith("PARTLY MODELLED: circulating subtracts morpho_relocked_tokens") and "read 2026-11-12" in pm
    assert config.partly_modelled("Morpho", "market_cap_usd") and not config.partly_modelled("Morpho", "fees_usd")
    assert config.partly_modelled("Maple", "circulating_supply_used") == ""            # no modelled leg there
    row = {"status": "ok", "n_points": 400, "source": "derived:circulating_used[circulating_supply_onchain]",
           "note": ""}
    band, why = bw.confidence_for("Morpho", "circulating_supply_used", row, pd.Timestamp("2026-10-13"))
    assert band == "AMBER" and "PARTLY MODELLED" in why


# ================================================================================================= 5: the probe pin
def test_5_the_pin_takes_sonic_and_hyperevm_and_matches_jakes_file():
    assert {146, 999} <= config.frozen_probe_chains()
    st = {"served": [5000, 146, 999, 9745], "alchemy": {"served": [10, 56, 8453, 80094],
                                                         "refused": {"5000": "x", "146": "y"}}}
    assert config.probe_report(st).splitlines()[-1].startswith("PIN MATCH")
