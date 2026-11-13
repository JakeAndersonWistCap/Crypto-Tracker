"""Project round 4E — Morpho's token-flow residual (Jake 2026-11-13). No network."""
import numpy as np
import pandas as pd
import pytest

import build_workbook as bw
import config


def _rows(metric, days, values, source="chain:x"):
    return pd.DataFrame({"date": days, "project": "Morpho", "metric": metric, "value": values, "source": source,
                         "tier": 2})


def _morpho_in_force(monkeypatch):
    """A Morpho store shaped like Jake's 11-13 run, switch in force: the DAO releases from noncirculating holdings, the
    relock vests on its schedule, the distributors inside the set pay claims out (Ethereum and Base) and are refilled
    from the Rewards Multisig (inside the set too: no change), and a few tokens come back to the DAO."""
    monkeypatch.setitem(config.CIRCULATING_ONCHAIN["Morpho"]["subtract_when_complete"], "in_force", True)
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Morpho"]])
    days = pd.date_range("2025-08-01", "2026-11-12")
    n = len(days)
    rng = np.random.default_rng(4)
    eth = rng.uniform(5_000, 30_000, n)                          # claims out of Merkl Ethereum + the URDs
    base = rng.uniform(1_000, 8_000, n)                          # claims out of Merkl Base
    dao_rel = np.where(days.day == 11, 3_000_000.0, 0.0)         # the DAO's monthly release
    dao_ret = np.where(days.day == 20, 1_290.0, 0.0)             # tokens returned to the DAO
    undist = 300_000_000.0 - np.cumsum(eth + base)
    hold = 120_000_000.0 - np.cumsum(dao_rel) + np.cumsum(dao_ret)
    groups = {("Morpho", "total_supply"): _rows("total_supply", days, 1e9),
              ("Morpho", "noncirculating_holding_tokens"): _rows("noncirculating_holding_tokens", days, hold),
              ("Morpho", "morpho_undistributed_tokens"): _rows("morpho_undistributed_tokens", days, undist),
              ("Morpho", "emissions_tokens_claims_ethereum"): _rows("emissions_tokens_claims_ethereum", days, eth),
              ("Morpho", "emissions_tokens_claims_base"): _rows("emissions_tokens_claims_base", days, base)}
    bw._modelled_lock_views(groups)
    reloc = groups[("Morpho", "morpho_relocked_tokens")].set_index("date")["value"].reindex(days).values
    circ = 1e9 - hold - reloc - undist
    groups[("Morpho", "circulating_supply_used")] = _rows(
        "circulating_supply_used", days, circ, "derived:circulating_used[circulating_supply_onchain]")
    return groups, days, eth + base


def _token_flow(groups, drop_claims_line=False):
    bw._unlock_views(groups)
    if drop_claims_line:                                         # the statement as it stood on ca9a077
        bw.UNLOCK_LESS_TAKEN.clear()
    rows = [r for r in bw.flow_statements(groups, pd.Timestamp("2026-11-12"))
            if r["Project"] == "Morpho" and r["Statement"] == "Token flow"]
    return {r["Line"]: r for r in rows}


def test_1_the_residual_was_the_claims_and_the_claims_line_balances_every_quarter(monkeypatch):
    groups, days, claims = _morpho_in_force(monkeypatch)
    tf = _token_flow(groups)
    line = tf[bw.FLOW_CLAIMS_LABEL]
    assert line["Series"] == bw.FLOW_CLAIMS_LEG
    asof = pd.Timestamp("2026-11-12")
    cl = pd.Series(claims, index=days)
    for k, lab in enumerate(bw.trajectory.LABELS[:4]):
        start, end = bw._flow_window(asof, k)
        want = float(cl[(cl.index > start) & (cl.index <= end)].sum())
        assert line[lab] == pytest.approx(want, rel=1e-9)               # the stored claims series, every token of it
        assert abs(tf["Residual (closing − expected)"][lab]) < 1e-3, lab
        assert tf["Status"][lab].startswith("OK — balances within ±0.5%"), tf["Status"][lab]
    # the release (e) stays net of the claims: they are (b), counted once (PR4.1)
    rel = tf["+ released from issuer wallets"]
    assert rel["Q0"] > 0 and rel["Q0"] < 3 * 3_000_000 + 219_000_000 * 90 / 730 + 1


def test_1_without_the_line_the_residual_is_exactly_the_claims_netted_out(monkeypatch):
    """The hypothesis, reproduced: the residual Jake saw (positive every quarter) is the claims netted out of (e)."""
    groups, days, claims = _morpho_in_force(monkeypatch)
    tf = _token_flow(groups, drop_claims_line=True)
    assert bw.FLOW_CLAIMS_LABEL not in tf
    cl = pd.Series(claims, index=days)
    for k, lab in enumerate(bw.trajectory.LABELS[:4]):
        start, end = bw._flow_window(pd.Timestamp("2026-11-12"), k)
        assert tf["Residual (closing − expected)"][lab] == pytest.approx(
            float(cl[(cl.index > start) & (cl.index <= end)].sum()), rel=1e-9)


def test_1_pending_and_the_other_projects_carry_no_claims_line(monkeypatch):
    groups, _days, _c = _morpho_in_force(monkeypatch)
    monkeypatch.setitem(config.CIRCULATING_ONCHAIN["Morpho"]["subtract_when_complete"], "in_force", False)
    tf = _token_flow(groups)
    assert bw.FLOW_CLAIMS_LABEL not in tf and not bw.UNLOCK_LESS_TAKEN
    # Pendle's RELEASE_LESS is not a required (in-force) leg: its release and its Flows are untouched
    assert config.release_less("Pendle") == (("issuer_to_gauges_tokens",), False)
    assert bw.FLOW_RESIDUAL_LIMIT == 0.005                              # the tolerance is not widened


# ========================================================================================= 2: what makes up Unlocks Q0
def test_2_circ_legs_release_lists_each_leg_and_the_claims_taken_out(tmp_path):
    import sqlite3

    import circ_legs
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT)")
    now = "chain:sum(" + "+".join(sorted(config.current_set_point("Morpho", "morpho_undistributed_tokens"))) + ")"
    days = pd.date_range("2026-08-01", "2026-11-12")
    for i, d in enumerate(days):
        ds = str(d.date())
        conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (ds, "Morpho", "total_supply", 1e9, "chain"))
        hold = 300e6 - (20_279_637.28 if d >= pd.Timestamp("2026-11-12") else 0.0)    # the DAO's two releases
        conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (ds, "Morpho", "noncirculating_holding_tokens", hold, "c"))
        conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (ds, "Morpho", "morpho_undistributed_tokens",
                                                                 5e6 - 10_000.0 * i, now))
        conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (ds, "Morpho", "emissions_tokens_claims_ethereum",
                                                                 6_000.0, "x"))
        conn.execute("INSERT INTO metrics VALUES (?,?,?,?,?)", (ds, "Morpho", "emissions_tokens_claims_base",
                                                                 4_000.0, "x"))
    conn.commit()
    out = "\n".join(circ_legs.release_parts(conn, "Morpho"))
    assert "(2026-08-14, 2026-11-12]" in out
    assert "noncirculating_holding_tokens: fall 20,279,637.28" in out
    assert "morpho_relocked_tokens: fall 27,000,000.00 (MODELLED" in out
    assert "morpho_undistributed_tokens: fall 900,000.00" in out
    assert "less emissions_tokens_claims_ethereum: 540,000.00" in out and "less emissions_tokens_claims_base: 360,000.00" in out
    assert "falls − claims = 47,279,637.28" in out
