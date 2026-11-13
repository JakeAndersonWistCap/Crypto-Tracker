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
    # PR4F.1-2: the legacy URD's claims and Merkl's Arbitrum / Katana claims are out of distributors inside the set too
    legacy = rng.uniform(1_000, 9_000, n)
    arb = rng.uniform(0, 300, n)
    kat = rng.uniform(0, 600, n)
    undist = 300_000_000.0 - np.cumsum(eth + base + legacy + arb + kat)
    hold = 120_000_000.0 - np.cumsum(dao_rel) + np.cumsum(dao_ret)
    groups = {("Morpho", "total_supply"): _rows("total_supply", days, 1e9),
              ("Morpho", "noncirculating_holding_tokens"): _rows("noncirculating_holding_tokens", days, hold),
              ("Morpho", "morpho_undistributed_tokens"): _rows("morpho_undistributed_tokens", days, undist),
              ("Morpho", "emissions_tokens_claims_ethereum"): _rows("emissions_tokens_claims_ethereum", days, eth),
              ("Morpho", "emissions_tokens_claims_base"): _rows("emissions_tokens_claims_base", days, base),
              ("Morpho", "emissions_tokens_claims_ethereum_legacy"): _rows("emissions_tokens_claims_ethereum_legacy",
                                                                           days, legacy),
              ("Morpho", "emissions_tokens_claims_arbitrum"): _rows("emissions_tokens_claims_arbitrum", days, arb),
              ("Morpho", "emissions_tokens_claims_katana"): _rows("emissions_tokens_claims_katana", days, kat)}
    bw._modelled_lock_views(groups)
    reloc = groups[("Morpho", "morpho_relocked_tokens")].set_index("date")["value"].reindex(days).values
    circ = 1e9 - hold - reloc - undist
    groups[("Morpho", "circulating_supply_used")] = _rows(
        "circulating_supply_used", days, circ, "derived:circulating_used[circulating_supply_onchain]")
    return groups, days, eth + base + legacy + arb + kat


def _token_flow(groups, drop_claims_line=False):
    bw._unlock_views(groups)
    if drop_claims_line:                                         # the statement as it stood on ca9a077
        bw.UNLOCK_LESS_TAKEN_LAST = bw.UNLOCK_LESS_TAKEN[("Morpho", "unlock_release_tokens")].copy()
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
        taken = bw.UNLOCK_LESS_TAKEN[("Morpho", "unlock_release_tokens")]
        # the line is what was netted out of (e) in the quarter: the stored claims, a claim taken on the next day when
        # its own day had no release left (PR4F: five claims legs now share each day's release)
        assert line[lab] == pytest.approx(float(taken[(taken.index > start) & (taken.index <= end)].sum()), rel=1e-12)
        assert line[lab] == pytest.approx(want, rel=1e-3)
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
    taken = bw.UNLOCK_LESS_TAKEN_LAST                                  # kept for this check before the line was dropped
    for k, lab in enumerate(bw.trajectory.LABELS[:4]):
        start, end = bw._flow_window(pd.Timestamp("2026-11-12"), k)
        assert tf["Residual (closing − expected)"][lab] == pytest.approx(
            float(taken[(taken.index > start) & (taken.index <= end)].sum()), rel=1e-9)
        assert tf["Residual (closing − expected)"][lab] > 0                 # positive in every quarter, as Jake saw


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
    assert "noncirculating_holding_tokens: fall 20,279,637.28; rise 0.00" in out
    assert "morpho_relocked_tokens: fall 27,000,000.00 (MODELLED" in out
    assert "morpho_undistributed_tokens: fall 900,000.00" in out
    assert "less emissions_tokens_claims_ethereum: 540,000.00 claimed; 540,000.00 taken" in out
    assert "less emissions_tokens_claims_base: 360,000.00 claimed; 360,000.00 taken" in out
    assert "falls − claims = 47,279,637.28" in out
    assert "Unlocks Q0 (per day: released − claims taken) = 47,279,637.28" in out
    # a return on a day the set fell is netted that day: the workbook's figure is below the per-leg sum by exactly it
    conn.execute("UPDATE metrics SET value = value + 1290 WHERE metric='noncirculating_holding_tokens' AND date>='2026-10-20'")
    conn.commit()
    out = "\n".join(circ_legs.release_parts(conn, "Morpho"))
    assert "noncirculating_holding_tokens: fall 20,279,637.28; rise 1,290.00" in out
    assert "Unlocks Q0 (per day: released − claims taken) = 47,278,347.28" in out
    assert "the difference 1,290.00 = rises netted on days the set fell 1,290.00" in out


# ==================================================================================== PR4F.5: the store export (4F)
def test_pr4f_5_store_export_holds_one_project_no_key_and_loads_back(tmp_path, monkeypatch):
    import gzip
    import json
    import sqlite3

    import store as sm
    import store_export
    db = tmp_path / "metrics.db"
    sm.Store(str(db)).close()
    con = sqlite3.connect(db)
    cols = "date, project, metric, value, source, tier, fetched_at"
    con.executemany(f"INSERT INTO metrics ({cols}) VALUES (?,?,?,?,?,?,?)", [
        ("2024-01-02", "Morpho", "total_supply", 1e9, "chain:ethereum:token", 2, "t"),
        ("2026-11-12", "Morpho", "total_supply", 1e9, "chain:ethereum:token", 2, "t"),
        ("2026-11-12", "Pendle", "total_supply", 2e8, "chain:ethereum:token", 2, "t")])
    con.execute("INSERT INTO run_log VALUES (?,?,?,?,?,?,?,?)",
                ("r", "2099-01-01T00:00:00", "explorer", 2, "Morpho", 1, "ok",
                 "GET https://api.etherscan.io/v2/api?chainid=1&apikey=ABCDEFGHIJKLMNOP1234 ok"))
    con.commit()
    con.close()
    before = db.read_bytes()
    path = store_export.export("Morpho", str(db), tmp_path / "exports")
    assert path.name.startswith("morpho-") and path.name.endswith(".json.gz") and path.parent.name == "exports"
    assert db.read_bytes() == before                                    # read-only
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        text = fh.read()
    payload = json.loads(text)
    assert {r["date"] for r in payload["metrics"]} == {"2024-01-02", "2026-11-12"}   # every date, Morpho's only
    assert "Pendle" not in text and "ABCDEFGHIJKLMNOP1234" not in text and "/v2/api" not in text
    assert "explorer_routes" not in json.dumps(payload["metrics"])
    assert payload["meta"]["catch_up_window"][1] >= payload["meta"]["catch_up_window"][0]
    import real_dump
    real_dump.to_store(payload, tmp_path / "back.db")                   # the tests' loader takes it as it stands
    back = sqlite3.connect(tmp_path / "back.db")
    assert back.execute("SELECT count(*) FROM metrics WHERE project='Morpho'").fetchone()[0] == 2
    back.close()
    assert "exports/" in open(".gitignore", encoding="utf-8").read()


# ============================================================================== PR4F.1-3: the set and the claims (4F)
def test_pr4f_1_the_rewards_safe_on_every_chain_and_the_distributors_are_in_the_set():
    und = config.current_set_point("Morpho", "morpho_undistributed_tokens")
    assert {"base:undist_rewards_safe_base", "base:undist_rewards_multisig_base", "arbitrum:undist_rewards_safe_arbitrum",
            "arbitrum:undist_merkl_distributor_arbitrum", "katana:undist_rewards_safe_katana",
            "katana:undist_merkl_distributor_katana", "ethereum:undist_campaign_funder",
            "base:undist_campaign_funder_base"} <= und and len(und) == 17
    c = config.PROJECT_BY_NAME["Morpho"]["contracts"]
    assert c["undist_rewards_safe_base"]["address"] == "0xF057afeEc22E220f47AD4220871364e9E828b2e9"
    assert c["undist_rewards_multisig_base"]["address"] == "0x5Eb982bb1E620cC3927E5CF8A5D207e667643297"
    assert (c["token_arbitrum"]["kind"], c["token_katana"]["kind"]) == ("bridged_representation",) * 2
    # Polygon / OP: no MORPHO token on file — nothing read there, and said
    assert not any(v["chain"] in ("polygon", "optimism") for v in c.values())
    assert "Polygon and OP Mainnet have no MORPHO token" in config.MORPHO_UNDISTRIBUTED_NOT_READ
    # the Safe whose owner is not established stays a release, named
    assert not any(v["address"].lower() == "0x92e4629a4510af5819d7d1601464c233599ff5ec" for v in c.values())
    assert "0x92e4629a" in config.CIRCULATING_ONCHAIN["Morpho"]["missing"]


def test_pr4f_2_legacy_urd_claims_are_emissions_and_netted_out_of_the_release_once():
    p = config.PROJECT_BY_NAME["Morpho"]
    leg = next(s for s in p["log_scans"] if s["key"] == "urd_morpho_legacy_out")
    assert leg["token"] == "0x9994E35Db50125E0DF82e4c2dde62496CE330999" and leg["holders"] == [
        "0x330eefa8a787552DC5cAd3C3cA644844B1E61Ddb"]
    assert "emissions_tokens_claims_ethereum_legacy" in p["emissions_from_metric"]
    swc = config.circulating_swc("Morpho")
    assert set(swc["release_less"]) == set(p["emissions_from_metric"])  # every claims leg (b) is netted out of (e)
    # a transfer to another wallet of the set is never a claim, on any chain
    rs = "0xf057afeec22e220f47ad4220871364e9e828b2e9"
    for s in p["log_scans"]:
        if s["key"].startswith(("merkl_morpho_out", "urd_morpho")):
            assert rs in {a.lower() for a in s["exclude_counterparties"]}, s["key"]
    saved = list(p["log_scans"])
    try:
        config.wire_probed_scans({"alchemy": {"served": [8453]}}, projects={"Morpho"}, open_routes=False)
        base = next(s for s in p["log_scans"] if s["key"] == "merkl_morpho_out_base")
        assert rs in {a.lower() for a in base["exclude_counterparties"]}
    finally:
        p["log_scans"] = saved
    # the urd's legacy balance stays in the set
    assert "ethereum:undist_urd_legacy" in config.current_set_point("Morpho", "morpho_undistributed_tokens")
