"""ROUND 12 — NEAR (Jake's decisions of 2026-11-19). SPEC.md "Round 12 — Near".

1 the operator payouts are dilution, counted as published (flagged, revisable for 45 days); 2 the Foundation set's
liquid NEAR leaves the free float; 3 every inferred account's signer keys re-read; 5 the header-burn history read is
approved under a byte condition.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

NEAR = config.PROJECT_BY_NAME["Near"]


def _g(metric, days, values, source="x", project="Near"):
    days = list(days)
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in vals], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    monkeypatch.setattr(bw, "AS_PUBLISHED", {})


# ===================================================================== 1 the payouts are dilution, as published
def _b_groups(ledger_to="2026-11-05"):
    d = pd.date_range("2026-07-01", "2026-11-17")
    led = pd.date_range("2026-04-23", ledger_to)
    pay = [4_600.0 if x.day == 15 else 0.0 for x in led]
    return {("Near", "price_usd"): _g("price_usd", d, 2.0, "coingecko"),
            ("Near", "near_mpc_operator_payouts_tokens"): _g("near_mpc_operator_payouts_tokens", led, pay,
                                                            "near_rpc:payout_ledger"),
            ("Near", "near_validator_rewards_tokens"): _g("near_validator_rewards_tokens", d, 80_000.0, "x")}


def test_1_the_payouts_are_summed_into_dilution_and_only_near_counts_a_category_as_published(scope):
    assert bw._cp_cats(NEAR) == ["a", "b", "e"]                                 # was ["a", "e"] (R11.5)
    assert set(config.OUTFLOW_AS_PUBLISHED) == {("Near", "b")}
    assert config.OUTFLOW_AS_PUBLISHED[("Near", "b")]["revisable_days"] == 45
    assert config.OUTFLOW_AS_PUBLISHED[("Near", "b")]["metric"] == config.outflow_metrics("Near", "b")[0]
    # no other project's categories moved: nothing is left out of anyone's dilution, nobody else is as published
    assert not config.DILUTION_LEAVES_OUT
    for p in config.PROJECTS:
        if p["name"] != "Near":
            # R18 (config.RELEASE_PAID_LATER): a category netted out of the release whose scan holds no row in the
            # LAST build of this process (bw.PAID_LATER_NOT_READ: Chainlink's (b) and (f)) is still inside (e) and is
            # not summed — a state of the build, not a moved category (the gate ran this test after a build)
            assert bw._cp_cats(p) == [c for c in bw.CP_DILUTION_CATS if config.outflow_metrics(p["name"], c)
                                      and (p["name"], c) not in bw.PAID_LATER_NOT_READ]


def test_1_the_days_after_the_last_report_are_not_yet_published_never_zero(scope):
    g = _b_groups()
    bw._outflow_views(g)
    b = bw._daily(g[("Near", "outflow_b_tokens")])
    # the series stops at the last report: no row (and so no 0) after it
    assert b.index.max() == pd.Timestamp("2026-11-05")
    bw._as_published_views(g, pd.Timestamp("2026-11-17"))
    st = bw.AS_PUBLISHED[("Near", "b")]
    assert (st["ok"], st["last"], st["lag"], st["days"]) == (True, "2026-11-05", 12, 78)
    assert st["max_lag"] == config.stale_after_days("Near", "outflow_b_usd", 7) == 64
    assert bw.as_published_cats(NEAR) == ["b"]
    # the figure is what is published so far: 78 covered days of the 90, two payouts — per covered day, annualised
    win = b[b.index > pd.Timestamp("2026-11-17") - pd.Timedelta(days=90)]
    assert len(win) == 78 and win.sum() == 2 * 4_600.0
    assert win.sum() / len(win) * 365 == pytest.approx(43_051.28, abs=0.01)


def test_1_a_ledger_older_than_its_cadence_is_not_counted_as_published(scope):
    g = _b_groups(ledger_to="2026-09-13")                                        # 65 days before the as-of
    bw._outflow_views(g)
    bw._as_published_views(g, pd.Timestamp("2026-11-17"))
    st = bw.AS_PUBLISHED[("Near", "b")]
    assert st["lag"] == 65 and st["ok"] is False and bw.as_published_cats(NEAR) == []
    assert bw.as_published_flag("Near") is None
    assert "NOT COUNTED AS PUBLISHED" in bw.as_published_note(NEAR)
    g = _b_groups(ledger_to="2026-09-14")                                        # 64 days: the cadence's own bound
    bw._outflow_views(g)
    bw._as_published_views(g, pd.Timestamp("2026-11-17"))
    assert bw.AS_PUBLISHED[("Near", "b")]["ok"] is True
    # a store without the series: nothing is as published (the cells name the missing series themselves)
    g = _b_groups()
    del g[("Near", "near_mpc_operator_payouts_tokens")]
    bw._outflow_views(g)
    bw._as_published_views(g, pd.Timestamp("2026-11-17"))
    assert bw.AS_PUBLISHED == {} and ("Near", "outflow_b_tokens") not in g


class _R:
    def D(self, r, m, f):
        return f"[{m}|{f}]"


def test_1_the_guard_keeps_the_minimum_days_but_not_the_same_span_test_for_an_as_published_leg():
    legs = [("holder value", "hv", "q0"), ("outflow a", "oa", "q0"), ("outflow b", "ob", "q0")]
    plain = bw._coverage_guard(_R(), 5, legs, "1")
    asp = bw._coverage_guard(_R(), 5, legs, "1", as_published=("ob",))
    # two same-span comparisons against the first leg before; one after — (b) is not set against holder value
    assert plain.count("ABS(") == 2 and asp.count("ABS(") == 1
    span = asp.split("IFERROR(ABS(")[1].split(">")[0]
    assert "[hv|" in span and "[oa|" in span and "[ob|" not in span
    # every leg — (b) included — must still cover the minimum days
    cov = bw._COV["q0"]
    for m in ("hv", "oa", "ob"):
        assert f"[{m}|{cov}]<{bw.RATE_MIN_DAYS}" in asp and f"[{m}|{cov}]<{bw.RATE_MIN_DAYS}" in plain
    # nothing changes for a guard that names no as-published leg
    assert bw._coverage_guard(_R(), 5, legs, "1", as_published=()) == plain


def test_1_net_holder_yield_reads_b_and_exempts_it_only_while_it_is_as_published(scope, monkeypatch):
    import free_float
    # R21 gate: the formulas as built with no float verdict yet — never the verdict the last build on this worker left
    # (on the dumps Near's float is not formed, so TOTAL DILUTION ÷ free float reads its n/a text there)
    monkeypatch.setattr(free_float, "MISSING", type(free_float.MISSING)())
    monkeypatch.setattr(free_float, "FORMED", type(free_float.FORMED)())
    cols = bw._cp_cols(bw.Refs(10, 10, ["2026-09"]), {})
    nhy = next(c for c in cols if c[0] == bw.CP_NHY_HEAD)
    monkeypatch.setattr(bw, "AS_PUBLISHED", {("Near", "b"): {"ok": True, "last": "2026-11-05", "lag": 12, "days": 78,
                                                             "max_lag": 64, "revisable_days": 45}})
    on = nhy[1](9, NEAR)
    monkeypatch.setattr(bw, "AS_PUBLISHED", {("Near", "b"): {"ok": False, "last": "2026-09-13", "lag": 65, "days": 25,
                                                             "max_lag": 64, "revisable_days": 45}})
    off = nhy[1](9, NEAR)
    assert '"|outflow_b_usd"' in on and '"|outflow_b_usd"' in off              # (b) is in the dilution either way
    assert off.count("ABS(") == on.count("ABS(") + 1                            # beyond its cadence: the full guard
    # the cell and both TOTAL DILUTION cells carry the flag and the note
    assert nhy[5]["fmt_fn"] is bw.as_published_flag
    dil = [c for c in cols if c[0].startswith("TOTAL DILUTION = outflows")]
    # R15: the TOTAL DILUTION note is _total_dilution_note (as published + the (f) note where a project holds (f));
    # for Near it reads exactly the as-published note (was: note_fn is bw.as_published_note)
    P = config.PROJECT_BY_NAME["Near"]
    assert len(dil) == 2 and all(c[5]["fmt_fn"] is bw.as_published_flag and
                                 c[5]["note_fn"](P) == bw.as_published_note(P) for c in dil)
    assert all("outflow_b_tokens" in c[1](9, NEAR) for c in dil)


def test_1_the_flag_is_on_the_number_names_the_last_published_day_and_reaches_the_reader(scope, monkeypatch):
    import openpyxl
    import reader
    monkeypatch.setattr(bw, "AS_PUBLISHED", {("Near", "b"): {"ok": True, "last": "2026-11-05", "lag": 12, "days": 78,
                                                             "max_lag": 64, "revisable_days": 45}})
    fmt = bw.as_published_flag("Near")
    secs = fmt.split(";")
    assert len(secs) == len(bw.FMT_PCT.split(";")) and len(fmt) <= 255          # Excel's number-format limit
    lit = config.AS_PUBLISHED_FLAG_PREFIX + "(b) to 2026-11-05, revisable for 45 days"
    assert all(s.endswith('"' + lit + '"') for s in secs)
    assert [s[:-len(lit) - 2] for s in secs] == bw.FMT_PCT.split(";")           # the number itself is formatted as before
    assert bw.as_published_flag("Ethereum") is None and bw.as_published_flag("Aethir") is None
    note = bw.as_published_note(NEAR)
    assert "2026-11-05" in note and "45" in note and "78" in note and bw.as_published_note(
        config.PROJECT_BY_NAME["Aethir"]) == ""
    # the reader's copy of the number keeps the flag (its own format would drop it)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CP"
    ws["B5"].value, ws["B5"].number_format = -0.0421, fmt
    ws["B6"].value, ws["B6"].number_format = -0.0421, bw.FMT_PCT
    monkeypatch.setitem(reader._AUDIT_WB, "wb", wb)
    assert reader.carried_suffix(reader.Src("CP", "B5")) == lit
    assert reader.carried_suffix(reader.Src("CP", "B6")) == ""


# ===================================================================== 2 the Foundation set's liquid NEAR, off the float
def _liquid_node(amounts, fail=()):
    from fetch.near import NearNode
    n = NearNode()
    calls = []

    def call(api, body=None):
        prm = body["params"]
        calls.append((prm["request_type"], prm["account_id"]))
        if prm["account_id"] in fail or prm["account_id"] not in amounts:
            return None, "no answer"
        return {"result": {"amount": str(amounts[prm["account_id"]]), "locked": str(7 * 10 ** 24)}}, "fake"
    n._call = call
    return n, calls


def test_2_the_liquid_read_sums_amount_over_every_account_or_stores_nothing():
    from fetch.base import FetchOutput
    read = NEAR["node_api"]["liquid_set"]
    accts = read["accounts"]
    assert read["metric"] == "near_foundation_liquid_tokens" and accts is config.NEAR_FOUNDATION_LIQUID_ACCOUNTS
    amounts = {a: 5 * 10 ** 24 for a in accts}
    amounts[accts[0]] = 1_000_000 * 10 ** 24 + 1                               # whole yocto: no float rounding in the sum
    node, calls = _liquid_node(amounts)
    pace = node.http.min_interval
    out = FetchOutput()
    node._liquid_set(NEAR, NEAR["node_api"], read, 1.3e9, out, pd.Timestamp("2026-11-19"))
    f = out.frame()
    assert list(f["metric"]) == ["near_foundation_liquid_tokens"]
    # `amount` only — `locked` (validator stake) is not liquid and is already off the float as stake
    assert f["value"].iloc[0] == pytest.approx(1_000_000 + 5 * (len(accts) - 1), abs=1e-6)
    # R13.2: the set is 200 accounts and its measuring point says so (was: "near_rpc:liquid_set", the 196-account set)
    assert f["source"].iloc[0] == "near_rpc:liquid_set_200"
    assert calls == [("view_account", a) for a in accts] and node.http.min_interval == pace
    # one account without an answer: NOTHING stored (unknown is never 0), and the gap names the account
    node, _ = _liquid_node(amounts, fail=(accts[57],))
    out = FetchOutput()
    node._liquid_set(NEAR, NEAR["node_api"], read, 1.3e9, out, pd.Timestamp("2026-11-19"))
    assert out.frame().empty and out.log[-1].status != "ok" and accts[57] in out.log[-1].message
    assert node.http.min_interval == pace
    # a read that runs past its own budget stores nothing either
    node, calls = _liquid_node(amounts)
    out = FetchOutput()
    node._liquid_set(NEAR, NEAR["node_api"], dict(read, budget_s=-1), 1.3e9, out, pd.Timestamp("2026-11-19"))
    assert out.frame().empty and not calls and "NOTHING STORED" in out.log[-1].message
    # a sum above the total supply fails its bound
    node, _ = _liquid_node(amounts)
    out = FetchOutput()
    node._liquid_set(NEAR, NEAR["node_api"], read, 10_000.0, out, pd.Timestamp("2026-11-19"))
    assert out.frame().empty


def test_2_the_accounts_are_asked_of_the_reads_own_nodes_in_turn_inside_the_tiers_budget():
    import fetch
    from fetch.base import FetchOutput
    from fetch.near import NearNode
    read = NEAR["node_api"]["liquid_set"]
    eps = list(read["endpoints"])
    assert len(eps) >= 2 and set(eps) != set(NEAR["node_api"]["endpoints"][:1])
    node, first = NearNode(), []

    def call(api, body=None):
        first.append(api["endpoints"][0])
        assert sorted(api["endpoints"]) == sorted(eps)                     # every node is still a fail-over for each call
        return {"result": {"amount": str(10 ** 24)}}, "fake"
    node._call = call
    out = FetchOutput()
    node._liquid_set(NEAR, NEAR["node_api"], read, 1.3e9, out, pd.Timestamp("2026-11-19"))
    assert first[:len(eps) * 2] == eps * 2 and out.frame()["value"].iloc[0] == len(read["accounts"])
    # the tier's budget holds this read's own budget, the payout ledger's and the reads before them
    assert fetch.TIER_BUDGET_S["near_rpc"] >= read["budget_s"] + NEAR["node_api"]["payout_ledger"]["budget_s"] + 60


def test_2_the_set_is_the_published_and_same_key_accounts_and_nothing_leaves_the_float_twice():
    accts = config.NEAR_FOUNDATION_LIQUID_ACCOUNTS
    assert len(set(accts)) == len(accts)
    # already off the float through their own series: never in this one
    own = {"treasury.near", "buybacks.multisignature.near", "contributors.near", "lockup.near",
           *NEAR["intents_revenue_wallets"]["wallets"]}
    for r in NEAR["node_api"]["extra_reads"]:
        own |= set(r.get("accounts") or ())
    assert not own & set(accts)
    # the payer-evidence accounts are not same-key: left IN the float, each named with its reason
    # R13.2 (Jake 2026-11-19): MOVED — the four nf-payments accounts are OFF the float, in the series, tagged INFERRED
    # (payer evidence). Was: left = NEAR_FOUNDATION_FLOAT["left_in_float"] holding the four, none of them in accts
    payers = {"nf-payments.near", "nf-payments1.near", "nf-payments2.near", "nf-payments8.near"}
    left = config.NEAR_FOUNDATION_FLOAT["left_in_float"]
    moved = config.NEAR_FOUNDATION_FLOAT["payer_evidence_off_float"]
    assert not left and set(moved) == payers and payers <= set(accts)
    assert all(str(v["why"]).strip() and v["liquid"] >= 0 and "payer evidence" in v["tag"] for v in moved.values())
    # every account in the series is PUBLISHED, carries the signer keys (R12.3's re-read), or is a payer account
    ev = config.NEAR_FOUNDATION_KEYS_READ
    grp = {a: g for g, accs in config.NEAR_FOUNDATION_LIQUID_GROUPS.items() for a in accs}
    for a in accts:
        tag = config.NEAR_FOUNDATION_SET["groups"][grp[a]][0]
        assert (tag == "PUBLISHED" or (grp[a] in ev["groups"] and a not in ev["removed"])
                or (tag == "INFERRED" and a in ev["payer_accounts"] and a in payers)), a
    assert config.RULE_IN["Near"]["off_float"][-1] == "near_foundation_liquid_tokens"
    assert "near_foundation_liquid_tokens" not in config.rule_in_legs("Near")      # in circulating: circulating does not move
    assert config.METRICS["near_foundation_liquid_tokens"]["only_projects"] == ("Near",)
    # the read's evidence adds up: the per-group liquid sums to the total, the float effect is the total
    fl = config.NEAR_FOUNDATION_FLOAT
    assert sum(fl["liquid_by_group"].values()) == pytest.approx(fl["off_float_liquid"], abs=0.02)
    assert set(fl["liquid_by_group"]) == set(config.NEAR_FOUNDATION_LIQUID_GROUPS)


def test_2_the_float_loses_the_liquid_set_and_a_day_without_it_has_no_float(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    d = pd.date_range("2026-11-10", "2026-11-19")
    g = {("Near", "near_foundation_accounts_tokens"): _g("near_foundation_accounts_tokens", d, 171_059.0),
         ("Near", "near_treasury_tokens"): _g("near_treasury_tokens", d, 1_692_528.0),
         ("Near", "near_foundation_liquid_tokens"): _g("near_foundation_liquid_tokens", d[-2:], [1_050_000.0, 1_048_000.0],
                                                       "near_rpc:liquid_set")}
    bw._rule_in_views(g)
    off = bw._daily(g[("Near", "rule_in_off_float_tokens")])
    # only the days every leg is held: the liquid set's first read on
    assert list(off.index) == list(d[-2:])
    assert off.iloc[-1] == pytest.approx(171_059.0 + 1_692_528.0 + 1_048_000.0)
    del g[("Near", "near_foundation_liquid_tokens")], g[("Near", "rule_in_off_float_tokens")]
    bw._rule_in_views(g)
    assert ("Near", "rule_in_off_float_tokens") not in g                         # n/a naming it — never the float without it


# ===================================================================== 3 the signer keys, every account
def test_3_every_same_key_account_was_read_and_carries_at_least_the_threshold():
    ev = config.NEAR_FOUNDATION_KEYS_READ
    assert len(ev["signer_keys"]) == 5 and ev["signer_keys"] == config.NEAR_FOUNDATION_SET["signer_keys"]
    assert ev["block"] > config.NEAR_FOUNDATION_SET["block"] and ev["read_on"] == "2026-11-19"
    thr = ev["threshold"]
    assert thr >= 3                                    # R11's claim: "each carries >= 3 of the signer keys"
    inferred_same_key = [g for g, v in config.NEAR_FOUNDATION_SET["groups"].items()
                         if v[0] == "INFERRED" and not g.startswith("nf-payments")]
    assert set(inferred_same_key) <= set(ev["groups"])
    read = 0
    for grp, e in ev["groups"].items():
        n = config.NEAR_FOUNDATION_SET["groups"][grp][1]
        assert sum(e["by_count"].values()) == e["accounts"] == n, grp          # every account of the group was read
        assert all(0 <= k <= 5 for k in e["by_count"])
        below = sum(c for k, c in e["by_count"].items() if k < thr)
        if grp in inferred_same_key:
            # every account carries the threshold, or is named as removed
            assert below == len([a for a in ev["removed"] if ev["removed"][a]["group"] == grp]), grp
            # and enough of them can confirm to meet the contract's own confirmations: the keys move the funds
            assert e["min_confirm_keys"] >= e["max_confirmations"] >= 1, grp
        read += e["accounts"]
    assert read == ev["accounts_read"] and read >= 190
    # an account that fails the evidence is in no float leg and no stake pair
    gone = set(ev["removed"])
    assert not gone & set(config.NEAR_FOUNDATION_LIQUID_ACCOUNTS)
    assert not gone & {o for o, _w, _p in config.NEAR_FOUNDATION_STAKE_PAIRS}
    # the payer accounts' count is recorded as read, whatever it is — they are not tagged by keys
    for a, e in ev["payer_accounts"].items():
        assert a.startswith("nf-payments") and 0 <= e["signer_keys"] <= 5


def test_3_the_stake_pairs_owners_are_same_key_accounts_or_named_payer_accounts():
    ev = config.NEAR_FOUNDATION_KEYS_READ
    same_key = set(config.NEAR_FOUNDATION_LIQUID_ACCOUNTS)
    for owner, who, _pool in config.NEAR_FOUNDATION_STAKE_PAIRS:
        assert owner in same_key or owner in ev["payer_accounts"], owner
    # the lockups' owners were re-read on chain with the keys
    assert ev["lockup_owners_read"] == sum(1 for o, w, _p in config.NEAR_FOUNDATION_STAKE_PAIRS if w != o)


# ===================================================================== 5 the header-burn history read is approved
class _Bq:
    class QueryJobConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.dry_run = kw.get("dry_run", False)
    ScalarQueryParameter = staticmethod(lambda k, t, v: (k, t, v))
    ArrayQueryParameter = staticmethod(lambda k, t, v: (k, t, tuple(v)))


class _Client:
    def __init__(self, est):
        self.est, self.dry, self.real, self.caps = est, [], [], []

    def query(self, sql, job_config=None):
        p = {k: v for k, _, v in job_config.query_parameters}
        win = (str(p["d0"]), str(p["d1"]))
        if job_config.dry_run:
            self.dry.append(win)
            return SimpleNamespace(total_bytes_processed=self.est)
        self.real.append(win)
        self.caps.append(job_config.maximum_bytes_billed)
        rows = [{"day": d, "burn_near": 800.0, "issuance_near": 100_000.0} for d in pd.date_range(*win)]
        return SimpleNamespace(result=lambda: rows, total_bytes_billed=self.est)


def _history(tmp_path, monkeypatch, est, spec=None, tag="c"):
    from fetch import near_bigquery as nbq
    from fetch.base import FetchOutput
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp("2026-11-20"))
    spec = spec or copy.deepcopy(NEAR["near_bigquery"])
    stored = _g("gross_burn_tokens_bigquery", pd.date_range("2025-10-07", "2026-11-19"), 900.0,
                "near_bigquery:blocks.total_supply")
    st = {"ledger": {}, "topup_bytes": {"bytes": 3e6, "on": "2026-11-20", "source": "t"}}
    a = nbq.NearBigQuery(client=_Client(est), bq=_Bq, stored_long=stored, cache_file=tmp_path / f"{tag}.json",
                         csv_dir=tmp_path / "none")
    out = FetchOutput()
    a._supply_flows_history(a._client_obj, spec, st, out, "Near")
    return a._client_obj, out, st


def test_5_the_history_read_is_approved_on_jakes_dry_run_and_runs_once_under_the_condition(tmp_path, monkeypatch):
    h = NEAR["near_bigquery"]["supply_flows"]["history"]
    assert h["approved"] is True and h["approved_by"] == "Jake" and h["approved_on"] == "2026-11-19"   # was False
    assert h["dry_run_bytes"] == 289_127_592 and h["dry_run_window"] == ("2025-07-15", "2025-10-06")
    # the condition it was approved under is the cap enforced at run time: at or above the dry run, and bounded
    assert h["dry_run_bytes"] <= h["max_bytes"] == 10 ** 9
    assert NEAR["near_bigquery"]["approved"]["supply_flows"] is True
    cl, out, st = _history(tmp_path, monkeypatch, 289_127_592)
    assert cl.dry == cl.real == [("2025-07-15", "2025-10-06")]                  # the window of Jake's dry run
    f = out.frame()
    assert len(f[f.metric == "gross_burn_tokens_bigquery"]) == 84 and st["supply_flows_history_from"] == "2025-07-15"
    # BigQuery itself is told the most it may bill: enough for the job, never above the condition
    assert 289_127_592 <= cl.caps[0] <= 10 ** 9
    assert any("84 row(s); 0.29 GB billed (dry run 0.29 GB)" in e.message for e in out.log)


def test_5_a_dry_run_over_the_condition_is_not_run_and_an_approval_without_one_is_not_run(tmp_path, monkeypatch):
    cl, out, st = _history(tmp_path, monkeypatch, 10 ** 9 + 1, tag="over")
    assert cl.real == [] and out.frame().empty and "supply_flows_history_from" not in st and st["ledger"] == {}
    assert "NOT RUN" in out.log[-1].message
    # exactly at the condition it runs, and the billing cap does not pass the condition
    cl, out, st = _history(tmp_path, monkeypatch, 10 ** 9, tag="at")
    assert len(cl.real) == 1 and cl.caps[0] == 10 ** 9
    spec = copy.deepcopy(NEAR["near_bigquery"])
    del spec["supply_flows"]["history"]["max_bytes"]
    cl, out, st = _history(tmp_path, monkeypatch, 289_127_592, spec=spec, tag="nocap")
    assert cl.real == [] and cl.dry == [] and out.frame().empty and "NOT RUN" in out.log[-1].message
    # other reads keep their own cap: the dry run x 1.05 + 10 MB (no condition is passed for them)
    from fetch.near_bigquery import NearBigQuery
    assert NearBigQuery.billing_cap(289_127_592) == int(289_127_592 * 1.05) + 10 * 1024 ** 2


# ===================================================================== 4 confirmed as built
def test_4_the_r11_readings_jake_confirmed_are_unchanged():
    assert config.NEAR_R11_CONFIRMED["by"] == "Jake" and config.NEAR_R11_CONFIRMED["on"] == "2026-11-19"
    assert config.SUBSIDY_REVENUE_METRIC["Near"] == "revenue_total_usd"
    assert config.stale_after_days("Near", "near_mpc_operator_payouts_tokens", 7) == 64
    assert "HSP-003 veNEAR Holder Rewards" in config.NEAR_HOUSE_OF_STAKE["not_in_b"]
    s = config.NEAR_FOUNDATION_SET["groups"]
    assert "weakest" in s["nf-payments.near"][4] and s["nf-payments.near"][0] == "INFERRED"
    assert all(s[g][0] == "PUBLISHED" for g in ("foundation.near", "treasury.near", "contributors.near",
                                               "nfvalidator1-4.near"))
