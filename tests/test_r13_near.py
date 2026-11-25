"""ROUND 13 — NEAR (Jake's decisions of 2026-11-19 on Round 12). SPEC.md "Round 13 — Near".

1 BigQuery stage 2 never follows stage 1 by itself: a dated approval in config AND an explicit command; 2 the four
nf-payments accounts' liquid NEAR leaves the free float — a declared handover of the series' measuring point (196
accounts -> 200), liquid only; 4 the lockup read's messages state the final rule, and no figure moves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

NEAR = config.PROJECT_BY_NAME["Near"]
PAYERS = ("nf-payments.near", "nf-payments1.near", "nf-payments2.near", "nf-payments8.near")
LIQ = "near_foundation_liquid_tokens"


def _g(metric, days, values, source="x", project="Near"):
    days = list(days)
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in vals], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


# ============================================================================ 1 stage 2 never follows stage 1 by itself
class _FakeBQ:
    """Stands in for fetch.near_bigquery.NearBigQuery: records everything asked of it, bills nothing."""
    made: list = []
    rows = [{"day": "2026-10-01"}, {"day": "2026-10-03"}, {"day": "2026-10-03"}]

    def __init__(self):
        self.calls = []
        _FakeBQ.made.append(self)

    def _client(self, spec):
        self.calls.append(("client",))
        return object(), ""

    def _load(self):
        return {}

    def _run(self, client, label, sql, params, spec, st, out, project, backfill=False):
        self.calls.append(("run", label))
        return list(self.rows) if "stage 1" in label else []

    def _quota_line(self, spec, st):
        return "QUOTA (fake)"


@pytest.fixture
def stage(monkeypatch, tmp_path):
    """check_offline_items with its data folder in tmp_path, a recording BigQuery stand-in, and its SQL files."""
    import check_offline_items as coi
    import fetch.near_bigquery as nbq
    (tmp_path / "sql").symlink_to(ROOT / "sql")
    monkeypatch.setattr(coi, "__file__", str(tmp_path / "check_offline_items.py"))
    monkeypatch.setattr(nbq, "NearBigQuery", _FakeBQ)
    monkeypatch.setattr(_FakeBQ, "made", [])
    monkeypatch.setattr(coi, "_NEAR_STAGE2_COMMAND", False)
    monkeypatch.setattr(coi, "_RUN_BY_NAME", False)
    started = []
    real = coi.near_wallet_inflows_stage2
    monkeypatch.setattr(coi, "near_wallet_inflows_stage2",
                        lambda *a, **k: (started.append((a, k)), real(*a, **k))[1])
    day_list = tmp_path / "data" / "near" / "wallet_inflow_days.json"
    return coi, day_list, started


def _save_day_list(day_list, ran_on):
    day_list.parent.mkdir(parents=True, exist_ok=True)
    day_list.write_text(json.dumps({"ran_on": ran_on, "days": ["2026-10-01", "2026-10-03"]}))


def _today():
    return pd.Timestamp.now(tz="UTC").date()


def _approve(monkeypatch, on=None, by="Jake"):
    monkeypatch.setitem(config.NEAR_STAGE2_APPROVAL, "approved", True)
    monkeypatch.setitem(config.NEAR_STAGE2_APPROVAL, "approved_by", by)
    monkeypatch.setitem(config.NEAR_STAGE2_APPROVAL, "approved_on", str(on or _today()))


def test_1_the_default_is_not_approved_and_the_refusal_names_what_is_missing():
    a = config.NEAR_STAGE2_APPROVAL
    assert a["approved"] is False and a["approved_by"] is None and a["approved_on"] is None
    ok = {"approved": True, "approved_by": "Jake", "approved_on": "2026-12-02"}
    r = config.near_stage2_refusal
    assert r(True, "2026-12-02", "2026-12-01", ok) is None                     # the approval AND the command
    assert r(True, "2026-12-02", "2026-12-02", ok) is None
    assert r(False, "2026-12-02", "2026-12-01", ok) is not None                # the approval alone
    assert r(True, "2026-12-02", "2026-12-01") is not None                     # the command alone (config as shipped)
    for bad in ({"approved": "yes"}, {"approved": 1}, {"approved_by": ""}, {"approved_by": None},
                {"approved_on": None}, {"approved_on": "December"}, {"approved_on": "2026-12-03"},    # in the future
                {"approved_on": "2026-11-30"}):                                # before stage 1 saved its day list
        assert r(True, "2026-12-02", "2026-12-01", dict(ok, **bad)) is not None, bad
    assert r(True, "2026-12-02", None, ok) is not None                         # a day list that does not say when


def test_1_a_daily_run_that_finds_the_day_list_calls_nothing_and_says_so_in_one_line(stage, capsys):
    coi, day_list, started = stage
    _save_day_list(day_list, "2026-11-05")
    assert coi.near_wallet_inflow_days() == 0                # windows/daily_run.ps1 step 2c runs exactly this check
    out = capsys.readouterr().out
    assert started == [] and _FakeBQ.made == []              # no stage 2, no BigQuery client, nothing billed
    lines = [x for x in out.splitlines() if config.NEAR_STAGE2_WAITING in x]
    assert len(lines) == 1 and "Nothing run, nothing billed" in lines[0] and "not approved" in lines[0]
    assert not (day_list.parent / "wallet_inflows_alltime.json").exists()


def test_1_the_approval_alone_does_not_start_it_from_the_daily_run(stage, capsys, monkeypatch):
    coi, day_list, started = stage
    _save_day_list(day_list, str(_today()))
    _approve(monkeypatch)
    assert coi.near_wallet_inflow_days() == 0
    out = capsys.readouterr().out
    assert started == [] and _FakeBQ.made == []
    lines = [x for x in out.splitlines() if config.NEAR_STAGE2_WAITING in x]
    assert len(lines) == 1 and "waiting for the explicit command" in lines[0] and "nothing billed" in lines[0]
    # the same check typed by name (what the daily run's step does), every check at once, and a prefix of the
    # command's name are not the command either
    for argv in (["near_wallet_inflow_days"], ["near_stage"]):
        monkeypatch.setattr(sys, "argv", ["check_offline_items.py", *argv])
        coi.main()
        assert coi._NEAR_STAGE2_COMMAND is False and _FakeBQ.made == [], argv
    assert coi.near_stage2 in coi.CHECKS and coi.near_stage2() == 0              # as `python check_offline_items.py` runs it
    assert _FakeBQ.made == [] and not (day_list.parent / "wallet_inflows_alltime.json").exists()


def test_1_the_command_alone_does_not_start_it_and_both_together_do(stage, capsys, monkeypatch):
    coi, day_list, started = stage
    _save_day_list(day_list, str(_today()))
    monkeypatch.setattr(sys, "argv", ["check_offline_items.py", "near_stage2"])
    coi.main()                                                                  # the command, no approval
    out = capsys.readouterr().out
    assert coi._NEAR_STAGE2_COMMAND is True and _FakeBQ.made == []
    assert config.NEAR_STAGE2_WAITING in out and "not approved" in out
    _approve(monkeypatch, on=_today() - pd.Timedelta(days=1))                   # an approval older than the day list
    coi.main()
    assert _FakeBQ.made == [] and "before stage 1 saved its day list" in capsys.readouterr().out
    _approve(monkeypatch)
    coi.main()                                                                  # the dated approval AND the command
    out = capsys.readouterr().out
    assert len(_FakeBQ.made) == 1 and [c for c in _FakeBQ.made[0].calls if c[0] == "run"] == [
        ("run", "NEAR stage 2 wallet_inflows_alltime")]
    assert "APPROVED by Jake" in out and (day_list.parent / "wallet_inflows_alltime.json").exists()


def test_1_no_script_or_scheduled_step_names_the_stage_2_command():
    cmd = config.NEAR_STAGE2_APPROVAL["check"]
    for f in [*(ROOT / "windows").glob("*.ps1"), ROOT / "token_metrics.py", ROOT / "near_lockups_weekly.py"]:
        raw = f.read_bytes()
        code = [ln for ln in raw.decode("utf-8").splitlines() if not ln.lstrip().startswith("#")]
        assert not any(cmd in ln or "near_wallet_inflows_stage2" in ln for ln in code), f.name
        if f.suffix == ".ps1":
            assert all(b < 128 for b in raw), f"{f.name} must stay ASCII"


def test_1_stage_1_still_runs_by_hand_as_the_runbook_line_does_and_stage_2_does_not_follow(stage, capsys, monkeypatch):
    coi, day_list, started = stage
    # RUNBOOK 11fa step 5: config.NEAR_STAGE1_WINDOW.update({...}); check_offline_items.near_wallet_inflow_days()
    assert coi.near_wallet_inflow_days() == 0 and "NOT RUN: today" in capsys.readouterr().out      # the window as shipped
    assert _FakeBQ.made == []
    month = pd.Timestamp(_today())
    monkeypatch.setitem(config.NEAR_STAGE1_WINDOW, "from", str((month - pd.Timedelta(days=1)).date()))
    monkeypatch.setitem(config.NEAR_STAGE1_WINDOW, "to", str((month + pd.Timedelta(days=1)).date()))
    _approve(monkeypatch)                                    # even approved: stage 1's own run starts no stage 2
    assert coi.near_wallet_inflow_days() == 0
    out = capsys.readouterr().out
    assert json.loads(day_list.read_text()) == {"ran_on": str(_today()), "days": ["2026-10-01", "2026-10-03"]}
    assert [c for b in _FakeBQ.made for c in b.calls if c[0] == "run"] == [("run", "NEAR stage 1 wallet_inflow_days")]
    assert started == [] and "does NOT follow" in out


# ============================================================ 2 the four nf-payments accounts: off the float, declared
def _liquid_node(amounts):
    from fetch.near import NearNode
    n = NearNode()
    calls = []

    def call(api, body=None):
        prm = body["params"]
        calls.append((prm["request_type"], prm["account_id"]))
        return {"result": {"amount": str(amounts[prm["account_id"]]), "locked": str(9 * 10 ** 24)}}, "fake"
    n._call = call
    return n, calls


def test_2_the_set_is_200_accounts_with_the_four_payer_accounts_tagged_inferred_on_payer_evidence():
    accts = config.NEAR_FOUNDATION_LIQUID_ACCOUNTS
    assert len(accts) == len(set(accts)) == 200 and set(PAYERS) <= set(accts)
    assert NEAR["node_api"]["liquid_set"]["accounts"] is accts
    grp = {a: g for g, accs in config.NEAR_FOUNDATION_LIQUID_GROUPS.items() for a in accs}
    for a in PAYERS:
        assert grp[a] in config.NEAR_FOUNDATION_PAYER_GROUPS
        assert config.NEAR_FOUNDATION_SET["groups"][grp[a]][0] == "INFERRED"
        assert config.NEAR_FOUNDATION_KEYS_READ["payer_accounts"][a]["signer_keys"] == 0     # not by the signer keys
        assert "payer evidence" in config.NEAR_FOUNDATION_FLOAT["payer_evidence_off_float"][a]["tag"]
    # the 196 of R12 are all still there, in the same order, before the four
    assert accts[196:] == ("nf-payments1.near", "nf-payments2.near", "nf-payments8.near", "nf-payments.near")
    assert not set(accts[:196]) & set(PAYERS)
    # in circulating (no leg of the circulating rule), off the float (a leg of the off-float sum)
    assert LIQ in config.RULE_IN["Near"]["off_float"] and LIQ not in config.rule_in_legs("Near")
    fl = config.NEAR_FOUNDATION_FLOAT
    assert fl["accounts"] == 200 and fl["left_in_float"] == {} and fl["left_in_float_liquid"] == 0
    assert sum(v["liquid"] for v in fl["payer_evidence_off_float"].values()) == pytest.approx(
        fl["payer_evidence_liquid"], abs=0.01)
    assert sum(fl["liquid_by_group"].values()) == pytest.approx(fl["off_float_liquid"], abs=0.02)
    assert fl["off_float_liquid"] - fl["payer_evidence_liquid"] == pytest.approx(1_048_572.20, abs=0.02)   # R12's set


def test_2_the_read_carries_the_new_measuring_point_and_is_liquid_only():
    from fetch.base import FetchOutput, _measuring_point
    read = NEAR["node_api"]["liquid_set"]
    accts = read["accounts"]
    amounts = {a: 10 ** 24 for a in accts}
    amounts["nf-payments.near"] = 14_246 * 10 ** 24
    node, calls = _liquid_node(amounts)
    out = FetchOutput()
    node._liquid_set(NEAR, NEAR["node_api"], read, 1.3e9, out, pd.Timestamp("2026-11-20"))
    f = out.frame()
    # `amount` only: `locked` (9 NEAR on every account here) and anything deposited in a staking pool is not in it
    assert f["value"].iloc[0] == pytest.approx(199 + 14_246, abs=1e-6)
    assert calls == [("view_account", a) for a in accts]              # no staking-pool call: the stake is its own series
    src = f["source"].iloc[0]
    assert src == config.NEAR_FOUNDATION_LIQUID_POINT
    assert _measuring_point(src) != _measuring_point(config.NEAR_FOUNDATION_LIQUID_POINT_R12)
    assert "200 account(s)" in out.log[-1].message and "PAYER EVIDENCE" in out.log[-1].message
    # nf-payments.near's stake stays where it was: a pair of the stake series, never in the liquid sum
    assert ("nf-payments.near", "nf-payments.near", "trust-nodes.poolv1.near") in config.NEAR_FOUNDATION_STAKE_PAIRS
    assert "near_foundation_staked_tokens" not in config.RULE_IN["Near"]["off_float"]     # off the float as stake


def test_2_an_account_list_changed_without_a_new_point_reads_nothing():
    from fetch.base import FetchOutput
    read = NEAR["node_api"]["liquid_set"]
    for accts in (read["accounts"][:196], (*read["accounts"], "nf-finance.near")):
        node, calls = _liquid_node({a: 10 ** 24 for a in accts})
        out = FetchOutput()
        node._liquid_set(NEAR, NEAR["node_api"], dict(read, accounts=accts), 1.3e9, out, pd.Timestamp("2026-11-20"))
        assert out.frame().empty and calls == [] and out.log[-1].status != "ok"
        assert "NOTHING READ" in out.log[-1].message


def test_2_the_change_of_set_is_a_declared_handover_not_a_silent_one(monkeypatch):
    old, new = config.NEAR_FOUNDATION_LIQUID_POINT_R12, config.NEAR_FOUNDATION_LIQUID_POINT
    decl = config.declared_handover("Near", LIQ)
    assert decl["ordered_points"] == (old, new) and decl["declared"] == "2026-11-19"
    assert "payer evidence" in decl["composition_change"]
    t = pd.Timestamp
    seam = {old: (t("2026-11-19"), t("2026-11-19")), new: (t("2026-11-20"), t("2026-11-25"))}
    assert bw.handover_refusal("Near", LIQ, {old, new}, seam) is None
    # the two sets in use on the same day, or in the wrong order: still blanked
    both = {old: (t("2026-11-19"), t("2026-11-21")), new: (t("2026-11-20"), t("2026-11-25"))}
    assert "OVERLAP" in bw.handover_refusal("Near", LIQ, {old, new}, both)
    back = {new: (t("2026-11-19"), t("2026-11-19")), old: (t("2026-11-20"), t("2026-11-25"))}
    assert bw.handover_refusal("Near", LIQ, {old, new}, back) is not None
    # a third set is outside the declaration
    assert bw.handover_refusal("Near", LIQ, {old, new, "near_rpc:liquid_set_201"}, seam) is not None
    # and without the declaration the two points would be an undeclared change of measuring point
    monkeypatch.delitem(NEAR["series_handover"], LIQ)
    assert bw.handover_refusal("Near", LIQ, {old, new}, seam) == "no handover is declared for this series"


def test_2_a_row_on_the_old_set_never_lands_after_the_new_set_has_started():
    import fetch
    from fetch.base import FetchOutput, point
    stored = pd.concat([_g(LIQ, ["2026-11-19"], 1_048_572.2, config.NEAR_FOUNDATION_LIQUID_POINT_R12),
                        _g(LIQ, ["2026-11-20"], 1_116_105.0, config.NEAR_FOUNDATION_LIQUID_POINT)])
    out = FetchOutput()
    out.add(point("Near", LIQ, 1_048_000.0, config.NEAR_FOUNDATION_LIQUID_POINT_R12, 2, pd.Timestamp("2026-11-21")),
            "near_rpc", "Near", "an old-set read", 2)
    lines = fetch._refuse_fallback_into_primary(out, stored)
    assert len(lines) == 1 and out.frame().empty
    # the new set's own row is stored — on the handover day it replaces the old set's row (the store keys on the day)
    out = FetchOutput()
    out.add(point("Near", LIQ, 1_116_000.0, config.NEAR_FOUNDATION_LIQUID_POINT, 2, pd.Timestamp("2026-11-19")),
            "near_rpc", "Near", "the new set", 2)
    assert fetch._refuse_fallback_into_primary(out, stored.iloc[:1]) == [] and len(out.frame()) == 1


def test_2_the_float_steps_down_by_the_four_accounts_liquid_near_and_circulating_does_not_move(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    d = pd.date_range("2026-11-18", "2026-11-20")
    pay = config.NEAR_FOUNDATION_FLOAT["payer_evidence_liquid"]
    liq = pd.concat([_g(LIQ, d[:2], 1_048_572.20, config.NEAR_FOUNDATION_LIQUID_POINT_R12),
                     _g(LIQ, d[2:], 1_048_572.20 + pay, config.NEAR_FOUNDATION_LIQUID_POINT)])
    g = {("Near", "near_foundation_accounts_tokens"): _g("near_foundation_accounts_tokens", d, 171_059.13),
         ("Near", "near_treasury_tokens"): _g("near_treasury_tokens", d, 1_692_527.72),
         ("Near", LIQ): liq}
    bw._rule_in_views(g)
    off = bw._daily(g[("Near", "rule_in_off_float_tokens")])
    assert list(off.index) == list(d)
    assert off.iloc[2] - off.iloc[1] == pytest.approx(pay, abs=0.01) == pytest.approx(67_532.83, abs=0.01)
    assert off.iloc[1] == off.iloc[0]
    assert LIQ not in config.rule_in_legs("Near")            # nothing of it is subtracted from circulating


# ======================================================================== 4 the lockup messages: the final rule, text only
OLD_WORDING = ("NOT YET TRANSFERABLE", "clarified 2026-10-12", "comes out of circulating",
               "only tokens STILL UNVESTED leave circulating")


def test_4_the_old_rule_wording_is_gone_from_everything_the_lockup_read_prints(capsys):
    import check_offline_items as coi
    import near_lockups_weekly as nlw
    for f in ("near_lockups_weekly.py", "check_offline_items.py", "build_workbook.py", "metrics_dictionary.py",
              "windows/near_lockups_run.ps1", "windows/register_lockup_task.ps1"):
        text = (ROOT / f).read_text(encoding="utf-8")
        if f == "check_offline_items.py":                    # the lockup read itself (the rest of the file is not Near)
            text = text[text.index("def near_lockups("):text.index("def near_lockups_at_block(")]
            text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        for old in OLD_WORDING:
            assert old.lower() not in text.lower(), (f, old)
    # the help text (the module's own description) and the line the read prints state the FINAL rule
    with pytest.raises(SystemExit):
        nlw.main(["--help"])
    helped = " ".join(capsys.readouterr().out.split())
    rule = config.NEAR_LOCKUP_RULE_TEXT
    for said in (helped, " ".join(rule.split())):
        low = said.lower()
        assert "staked" in low and "earning" in low and "counts as circulating" in low and "off the free float" in low
        assert "only idle locked near" in low and "out of circulating" in low
        assert not any(old.lower() in low for old in OLD_WORDING)
    assert "?" not in coi.ascii_safe(rule)                   # prints whole on a cp1252 console
    body = (ROOT / "check_offline_items.py").read_text(encoding="utf-8")
    body = body[body.index("def near_lockups("):body.index("def near_lockups_at_block(")]
    assert "NEAR_LOCKUP_RULE_TEXT" in body


def test_4_the_figures_are_untouched_idle_locked_out_staked_locked_in(monkeypatch):
    import near_lockups_weekly as nlw
    read = config.NEAR_LOCKUP_RULE_READ_2026_11_19
    locked, staked = read["locked"], read["locked_staked"]
    assert locked - staked == pytest.approx(read["idle_out_of_circulating"], abs=0.005) == pytest.approx(788_417.85)
    # what one read stores: the same three figures, unrounded
    got = {"height": read["block"], "unvested": 1_823_913.65, "locked": locked, "locked_staked": staked}
    rows = nlw.rows_for(got, pd.Timestamp("2026-11-19")).set_index("metric")["value"]
    assert rows["near_lockup_locked_tokens"] == locked and rows["near_lockup_locked_staked_tokens"] == staked
    assert rows["near_lockup_unvested_tokens"] == 1_823_913.65 and len(rows) == 3
    # what the build makes of them — R22 (Jake 2026-11-25, item 1a): circulating = total − LOCKED, staked or not (was
    # R10.B3b: total − idle LOCKED, the staked part not subtracted)
    monkeypatch.setattr(bw, "_SCOPE", [NEAR])
    total = 1_312_113_606.0
    day = ["2026-11-19"]
    src = f"near_rpc:lockups@{read['block']}"
    g = {("Near", "total_supply"): _g("total_supply", day, total),
         ("Near", "near_lockup_locked_tokens"): _g("near_lockup_locked_tokens", day, locked, src),
         ("Near", "near_lockup_locked_staked_tokens"): _g("near_lockup_locked_staked_tokens", day, staked, src),
         ("Near", "near_foundation_accounts_tokens"): _g("near_foundation_accounts_tokens", day, 171_059.13)}
    bw._near_lockup_views(g)
    circ = g[("Near", "circulating_supply_lockup_rule")]
    assert float(circ["value"].iloc[-1]) == pytest.approx(total - locked, abs=0.01)    # was total − 788,417.85
    assert "R22" in circ["source"].iloc[-1]
    # the staked part leaves the FLOAT through the validators' stake, not through the off-float legs (never twice)
    assert config.RULE_IN["Near"]["locked_staked"] == "near_lockup_locked_staked_tokens"
    assert "near_lockup_locked_staked_tokens" not in config.RULE_IN["Near"]["off_float"]
    # a read without its staked part leaves all of LOCKED out (at most LOCKED too low), and says so
    del g[("Near", "near_lockup_locked_staked_tokens")], g[("Near", "circulating_supply_lockup_rule")]
    bw._near_lockup_views(g)
    circ = g[("Near", "circulating_supply_lockup_rule")]
    assert float(circ["value"].iloc[-1]) == pytest.approx(total - locked, abs=0.01)
    assert "R22" in circ["source"].iloc[-1]                     # R22 (was: "its staked part is not read yet")
