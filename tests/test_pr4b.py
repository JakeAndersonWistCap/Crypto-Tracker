"""Project round 4B — circulating corrections for Fluid and Maple, Morpho's last checks (Jake 2026-11-12). No network."""
import pandas as pd

import build_workbook as bw
import config
import done_check


def _rows(project, metric, days, values, source="chain:x"):
    return pd.DataFrame({"date": days, "project": project, "metric": metric, "value": values, "source": source,
                         "tier": 2})


# ======================================================================================== 1: Fluid — nothing is released
def test_1_fluids_igp137_and_team_multisig_stay_out_with_igp137s_own_words():
    spec = config.CIRCULATING_ONCHAIN["Fluid"]
    assert spec["subtract"] == ("treasury_holding_tokens", "noncirculating_holding_tokens", "noncirculating_igp137_tokens")
    assert "subtract_when_complete" not in spec and "handover" not in spec          # circulating does not move
    c = config.PROJECT_BY_NAME["Fluid"]["contracts"]
    lab = config.METRICS["noncirculating_igp137_tokens"]["label"]
    for t in (lab, c["noncirc_igp137_lock"]["purpose"]):
        assert "legally locked" in t and "2030" in t and "custodial partners" in t, t
        assert "not a custodian or time-lock" not in t
    assert c["noncirc_igp137_lock"]["source_url"].startswith("https://github.com/Instadapp/fluid-governance")
    assert "rewards funding" in c["noncirc_team_multisig"]["purpose"]
    assert "issuer" not in config.CIRCULATING_POLICY["Fluid"]["definition"]


# ======================================================================================= 2: Maple — the Safe added back
def test_2_maples_safe_is_added_back_only_when_held_on_every_day():
    swc = dict(config.CIRCULATING_ONCHAIN["Maple"]["subtract_when_complete"])
    assert (swc["metric"], swc["sign"], swc["whole_series"]) == ("maple_ssf_safe_tokens", 1, True)
    days = pd.date_range("2026-01-01", periods=6)
    s = pd.Series(1_176.0, index=days)
    part = {("Maple", "maple_ssf_safe_tokens"): _rows("Maple", "maple_ssf_safe_tokens", days[2:], 48.0)}
    out, tag = bw._subtract_when_complete(part, "Maple", s, "t", swc)
    assert swc["in_force"] is False and out.equals(s) and "PENDING" in swc["state"]
    full = {("Maple", "maple_ssf_safe_tokens"): _rows("Maple", "maple_ssf_safe_tokens", days, 48.0)}
    out, tag = bw._subtract_when_complete(full, "Maple", s, "t", swc)
    assert swc["in_force"] is True and (out == 1_224.0).all() and tag == "t[+ maple_ssf_safe_tokens]"
    assert "SYRUP added back" in swc["state"]


def test_2_the_float_keeps_the_funds_holdings_and_the_safes_falls_are_no_release(monkeypatch):
    swc = config.CIRCULATING_ONCHAIN["Maple"]["subtract_when_complete"]
    monkeypatch.setitem(swc, "in_force", False)
    assert config.held_buyback_less_applies("Maple") is True                       # pending: chart − Safe, as before
    assert config.unlock_stock_metrics("Maple") == ("treasury_holding_tokens_chain", "noncirculating_holding_tokens")
    monkeypatch.setitem(swc, "in_force", True)
    assert config.held_buyback_less_applies("Maple") is False                      # in force: the whole chart
    assert config.unlock_stock_metrics("Maple")[-1] == "-maple_ssf_safe_tokens"
    # the Safe sells 5 out of the set on day 3, the DAO releases 2 on day 4: only the DAO's 2 is a release
    days = pd.date_range("2026-01-01", periods=4)
    groups = {("Maple", "treasury_holding_tokens_chain"): _rows("Maple", "treasury_holding_tokens_chain", days,
                                                                [42.0, 42.0, 42.0, 40.0]),
              ("Maple", "noncirculating_holding_tokens"): _rows("Maple", "noncirculating_holding_tokens", days,
                                                               [60.0, 60.0, 55.0, 55.0]),
              ("Maple", "maple_ssf_safe_tokens"): _rows("Maple", "maple_ssf_safe_tokens", days, [50.0, 50.0, 45.0, 45.0])}
    bw._unlock_release_view(groups, "Maple", "unlock_release_tokens", config.unlock_stock_metrics("Maple"))
    rel = groups[("Maple", "unlock_release_tokens")].set_index("date")["value"]
    assert list(rel.round(6)) == [0.0, 0.0, 2.0]
    # no other project's held-buyback float changes
    assert config.held_buyback_less_applies("Near") is False and not config.HELD_BUYBACK_FLOAT["Near"].get("less")


# ================================================================================= 3: Morpho — vesting and the reserve
def test_3_morphos_contributor_reserve_is_not_released_and_the_float_leg_is_its_own_series():
    und = config.current_set_point("Morpho", "morpho_undistributed_tokens")
    assert {"ethereum:undist_contributor_grants_safe", "ethereum:undist_contributor_grants_safe_2",
            "ethereum:undist_contributor_grants_2_legacy", "ethereum:undist_rewards_multisig",
            "base:undist_merkl_distributor_base"} <= und and len(und) == 9
    rel = config.current_set_point("Morpho", "morpho_released_held_tokens")
    assert rel == {"ethereum:rel_association_master", "ethereum:rel_association_master_legacy",
                   "ethereum:rel_association_ops", "ethereum:rel_operative_1", "ethereum:rel_operative_2",
                   "ethereum:rel_operative_2_legacy"}
    swc = config.circulating_swc("Morpho")
    assert swc["also_need"] == ("morpho_released_held_tokens",) and swc["current_set_only"] is True
    assert config.FLOAT_RELEASED_HELD["Morpho"]["in_force_metric"] == "morpho_released_held_tokens"
    # PR4C.1: the limitation became the modelled relock (the note keeps its date and the 112.2M)
    assert "RELOCK MODELLED (PR4C.1, read 2026-11-12)" in config.MORPHO_VESTING_LIMITATION
    assert "112.2M" in config.MORPHO_VESTING_LIMITATION


def test_3_a_day_read_on_an_earlier_wallet_set_is_not_held(monkeypatch):
    swc = dict(config.circulating_swc("Morpho"))
    days = pd.date_range("2026-01-01", periods=3)
    now = "chain:sum(" + "+".join(sorted(config.current_set_point("Morpho", "morpho_undistributed_tokens"))) + ")"
    old = "chain:sum(ethereum:undist_rewards_multisig+ethereum:undist_urd)"
    rel_src = "chain:sum(" + "+".join(sorted(config.current_set_point("Morpho", "morpho_released_held_tokens"))) + ")"
    s = pd.Series(700.0, index=days)
    groups = {("Morpho", "morpho_undistributed_tokens"): pd.concat([
                  _rows("Morpho", "morpho_undistributed_tokens", days[:1], 3.0, old),
                  _rows("Morpho", "morpho_undistributed_tokens", days[1:], 55.0, now)]),
              ("Morpho", "morpho_released_held_tokens"): _rows("Morpho", "morpho_released_held_tokens", days, 90.0,
                                                               rel_src + ":archive")}
    bw._subtract_when_complete(groups, "Morpho", s, "t", swc)
    assert swc["in_force"] is False and "held on 2 of 3" in swc["state"]
    groups[("Morpho", "morpho_undistributed_tokens")] = _rows("Morpho", "morpho_undistributed_tokens", days, 55.0, now)
    out, _ = bw._subtract_when_complete(groups, "Morpho", s, "t", swc)
    assert swc["in_force"] is True and (out == 645.0).all()
    # in force, the float's leg is the released entities' own series
    groups[("Morpho", "noncirculating_entities_tokens")] = _rows("Morpho", "noncirculating_entities_tokens", days, 145.0)
    bw._released_held_view(groups, "Morpho", swc, groups[("Morpho", "morpho_released_held_tokens")].columns)
    held = groups[("Morpho", "issuer_held_released_tokens")]
    assert (held["value"] == 90.0).all() and held["source"].iloc[0] == "derived:released_held[morpho_released_held_tokens]"
    # pending, it is the entities' whole stock (circulating still excludes them all)
    bw._released_held_view(groups, "Morpho", {"in_force": False}, held.columns)
    assert (groups[("Morpho", "issuer_held_released_tokens")]["value"] == 145.0).all()


def test_3_a_release_missing_its_claims_leg_says_blocked(monkeypatch):
    swc = config.CIRCULATING_ONCHAIN["Morpho"]["subtract_when_complete"]
    monkeypatch.setitem(swc, "in_force", True)
    days = pd.date_range("2026-01-01", periods=3)
    groups = {("Morpho", "noncirculating_holding_tokens"): _rows("Morpho", "noncirculating_holding_tokens", days, 10.0),
              ("Morpho", "morpho_undistributed_tokens"): _rows("Morpho", "morpho_undistributed_tokens", days, 5.0),
              # PR4C.1: the relocked stock is a leg of the release as well
              ("Morpho", "morpho_relocked_tokens"): _rows("Morpho", "morpho_relocked_tokens", days, 100.0)}
    bw._VIEW_BLOCKS.pop(("Morpho", "unlock_release_tokens"), None)
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", config.unlock_stock_metrics("Morpho"))
    assert bw._VIEW_BLOCKS.pop(("Morpho", "unlock_release_tokens")).startswith(
        "BLOCKED — emissions_tokens_claims_ethereum stored nothing")


# ================================================================================================ 4: Morpho UNIFORMITY
def test_4_no_morpho_staking_exists_is_declared_with_the_filing():
    t = config.declared_na_text("Morpho", "UNIFORMITY = whole-token yield ÷ staker yield")
    assert t.startswith("n/a — no staker yield exists: no MORPHO staking exists") and "Q3(c)" in t
    e = config.declared_na_reason("Morpho", t)
    assert e["read_on"] == "2026-11-12" and e["source"].startswith("https://blockworks.com/token-transparency/filing/morpho")
    hits = [{"project": "Morpho", "tab": "audit:CP Comparability", "header": "UNIFORMITY", "code": "n/a", "reason": t}]
    assert done_check.judge_suspects(hits, "Morpho", []) == []


# =========================================================================================== 5: the wording is removed
def test_5_no_issuer_rule_or_decision_1_wording_left_on_fluid_maple_morpho():
    texts = []
    for n in ("Fluid", "Maple", "Morpho"):
        texts += [str(v) for v in (config.CIRCULATING_ONCHAIN.get(n) or {}).values()]
        texts += [str(v) for v in (config.CIRCULATING_POLICY.get(n) or {}).values()]
        texts += [str(c.get(k)) for c in config.PROJECT_BY_NAME[n]["contracts"].values() for k in ("purpose", "provenance")]
    texts += [str(config.HELD_BUYBACK_FLOAT["Maple"]), str(config.DISCRETIONARY_SUPPLY["Morpho"]),
              config.METRICS["noncirculating_entities_tokens"]["label"]]
    for t in texts:
        assert "decision 1" not in t, t[:200]
        assert "issuer rule" not in t.lower() or "never made" in t or "superseded" in t, t[:200]
    src = open("SPEC.md", encoding="utf-8").read()
    assert "| PR4B.5 |" in src and "OPEN, for their own rounds" in src
