"""P4-Q7 interim (Jake 2026-11-03), SPEC row "P4-Q7 (decision, Jake 2026-11-03)".

(1) Hyperliquid's A4 BURN ÷ ISSUANCE allowance is INTERIM for the rest of P4: it expires 2026-12-15 (was 2027-01-31).
(2) Every tokenDetails component is captured daily as its own stored series: the Hyper Foundation and Community Grants
wallets each on its own (beside their sum, excluded_wallets_tokens), and every address listed in
nonCirculatingUserBalances other than the team wallet (the Assistance Fund as listed, 0x0, 0xdEaD). An unnamed listed
address is logged "listed but not stored"; a named one missing from the list is a FAIL with nothing stored (a hole, never
0). They are evidence series: no existing stored value changes. Fakes only — no network.
"""
from __future__ import annotations

import copy
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402
from fetch.hypercore import HyperCoreInfo  # noqa: E402

HL = "A4 | BURN ÷ ISSUANCE (x) — crossover, on the basis to the left"
DAY = pd.Timestamp("2026-11-03")
TEAM = "0x43e9abea1910387c4292bca4b94de81462f8a251"
AF = "0xfefefefefefefefefefefefefefefefefefefefe"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
FOUNDATION = "0xd57ecca444a9acb7208d286be439de12dd09de5d"
GRANTS = "0xa20fcfa0507fe762011962cc581b95bbbc3bbdba"
NEW = ("foundation_wallet_tokens", "grants_wallet_tokens", "af_noncirc_listed_tokens", "zero_address_noncirc_tokens",
       "dead_address_noncirc_tokens")


class _Http:
    """HyperCore info + HyperEVM answers for every wallet the tokenDetails read touches."""
    SPOT = {FOUNDATION: "50000.00000044", GRANTS: "105501.0", TEAM: "0.0"}
    DELEG = {FOUNDATION: "60501988.6402254328", GRANTS: "2868230.9850819199", TEAM: "237930044.48"}

    def post(self, endpoint, json_body=None):
        b = json_body or {}
        if b.get("method") == "eth_getBalance":
            return {"jsonrpc": "2.0", "id": 1, "result": hex(308_538_170_000_000_000_000_000)}
        u = b.get("user")
        if b.get("type") == "spotClearinghouseState":
            return {"balances": [{"coin": "HYPE", "total": self.SPOT[u]}, {"coin": "USDC", "total": "1"}]}
        return {"delegated": self.DELEG[u], "undelegated": "0.0", "totalPendingWithdrawal": "0.0"}


def _read():
    api = config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]
    return api, next(r for r in api["extra_reads"] if r.get("shape") == "token_details")


def _payload(listed=((TEAM, "237930044.48"), (AF, "48007021.50"), (ZERO, "1673.79"), (DEAD, "2.72"))):
    return {"totalSupply": "998868230.0", "circulatingSupply": "302167698.0", "maxSupply": "1000000000.0",
            "futureEmissions": "379000000.0", "nonCirculatingUserBalances": [list(x) for x in listed]}


def _run(read=None, payload=None):
    api, rd = _read()
    a = HyperCoreInfo(prior_values={("Hyperliquid", "total_supply_gross"): 998_870_000.0,
                                    ("Hyperliquid", "future_emissions_tokens"): 379_010_000.0},
                      prior_dates={("Hyperliquid", "total_supply_gross"): pd.Timestamp("2026-11-02"),
                                   ("Hyperliquid", "future_emissions_tokens"): pd.Timestamp("2026-11-02")})
    a.http = _Http()
    out = FetchOutput()
    a._token_details(config.PROJECT_BY_NAME["Hyperliquid"], api, read or rd, payload or _payload(), out, DAY)
    return out


def _vals(out) -> dict:
    f = out.frame()
    return {(r.metric, str(pd.Timestamp(r.date).date())): (r.value, r.source) for r in f.itertuples()}


# ===================================================================================== (1) the interim allowance
def test_the_hyperliquid_a4_allowance_is_interim_to_2026_12_15():
    (a,) = [x for x in config.REGRESSION_ALLOWLIST if x["project"] == "Hyperliquid"]
    assert a["match"] == ("A4 | BURN ÷ ISSUANCE",) and a["until"] == "2026-12-15" and a["accepted"] == "2026-10-28"
    assert "INTERIM" in a["reason"] and "P5" in a["reason"] and "not accepted as the answer" in a["reason"]
    assert config.regression_allowance("Hyperliquid", HL, on="2026-12-15") is a
    assert config.regression_allowance("Hyperliquid", HL, on="2026-12-16") is None


def test_after_the_expiry_the_guard_counts_the_loss():
    import measuring_guard as mg
    accepted: list = []
    bad = mg.compare({"headlines": {"Hyperliquid": {HL: 1.5}}, "bases": {}}, {"headlines": {}, "bases": {}}, accepted,
                     on="2026-12-16")
    assert bad == [f"HEADLINE LOST Hyperliquid {HL}: 1.5 before, no value after"] and accepted == []
    accepted = []
    assert mg.compare({"headlines": {"Hyperliquid": {HL: 1.5}}, "bases": {}}, {"headlines": {}, "bases": {}}, accepted,
                      on="2026-12-15") == [] and len(accepted) == 1


# ===================================================================================== (2) the daily capture
def test_every_new_series_is_stored_from_a_fake_tokendetails_payload(tmp_path):
    out = _run()
    v = _vals(out)
    d = "2026-11-03"
    assert v[("af_noncirc_listed_tokens", d)][0] == pytest.approx(48_007_021.50)
    assert v[("zero_address_noncirc_tokens", d)][0] == pytest.approx(1_673.79)
    assert v[("dead_address_noncirc_tokens", d)][0] == pytest.approx(2.72)
    assert v[("foundation_wallet_tokens", d)][0] == pytest.approx(60_551_988.64, abs=0.01)
    assert v[("grants_wallet_tokens", d)][0] == pytest.approx(2_973_731.99, abs=0.01)
    for m in ("af_noncirc_listed_tokens", "zero_address_noncirc_tokens", "dead_address_noncirc_tokens"):
        assert v[(m, d)][1] == "hypercore_info:tokenDetails.nonCirculatingUserBalances"
    # each is a declared Hyperliquid-only stock in tokens, inside its sanity band
    for m in NEW:
        spec = config.METRICS[m]
        assert spec["kind"] == "stock" and spec["unit"] == "tokens" and spec["only_projects"] == ("Hyperliquid",)
        assert spec["sanity_min"] <= v[(m, d)][0] <= spec["sanity_max"], m
    # and it reaches the store
    import store as sm
    st = sm.Store(str(tmp_path / "m.db"))
    st.upsert(out.frame())
    got = dict(st.conn.execute("SELECT metric, value FROM metrics WHERE project='Hyperliquid' AND date='2026-11-03'"))
    assert set(NEW) <= set(got)
    assert not [e for e in out.log if e.status == "failed" and any(m in e.message for m in NEW)]


def test_an_unmapped_listed_address_is_logged_not_dropped():
    other = "0x1111111111111111111111111111111111111111"
    out = _run(payload=_payload(((TEAM, "237930044.48"), (AF, "48007021.50"), (ZERO, "1673.79"), (DEAD, "2.72"),
                                 (other, "12.5"))))
    w = [e for e in out.log if "listed but not stored" in e.message]
    assert len(w) == 1 and other in w[0].message and "12.50" in w[0].message and w[0].status == "warn"
    # the team wallet (unlock_wallets) and the mapped three are never "listed but not stored"
    assert not any(a in w[0].message for a in (TEAM, AF, ZERO, DEAD))
    assert other not in {m for m, _d in _vals(out)}


def test_a_mapped_address_missing_from_the_list_is_a_fail_with_nothing_stored():
    out = _run(payload=_payload(((TEAM, "237930044.48"), (AF, "48007021.50"), (ZERO, "1673.79"))))
    v = _vals(out)
    assert ("dead_address_noncirc_tokens", "2026-11-03") not in v           # a hole, never 0
    fails = [e for e in out.log if e.status == "failed" and "dead_address_noncirc_tokens" in e.message]
    assert len(fails) == 1 and "NOT STORED" in fails[0].message and DEAD in fails[0].message.lower()
    # the others are still stored
    assert ("af_noncirc_listed_tokens", "2026-11-03") in v and ("zero_address_noncirc_tokens", "2026-11-03") in v


def test_no_list_at_all_fails_every_mapped_address():
    p = _payload()
    del p["nonCirculatingUserBalances"]
    out = _run(payload=p)
    v = {m for m, _d in _vals(out)}
    for m in ("af_noncirc_listed_tokens", "zero_address_noncirc_tokens", "dead_address_noncirc_tokens"):
        assert m not in v
        assert any(e.status == "failed" and m in e.message and "NOT STORED" in e.message for e in out.log), m


def test_foundation_and_grants_are_the_parts_of_the_existing_sum():
    v = _vals(_run())
    d = "2026-11-03"
    f, g, s = (v[(m, d)][0] for m in ("foundation_wallet_tokens", "grants_wallet_tokens", "excluded_wallets_tokens"))
    assert f + g == pytest.approx(s, abs=1e-6)
    assert s == pytest.approx(63_525_720.63, abs=0.01)
    _api, rd = _read()
    assert [w["metric"] for w in rd["exclude_wallets"]] == ["foundation_wallet_tokens", "grants_wallet_tokens"]
    assert [w["address"] for w in rd["exclude_wallets"]] == [FOUNDATION, GRANTS]


def test_no_existing_stored_value_changes():
    """A fake run on the read as it was before P4-Q7 (no address map, no per-wallet metric) against the read now: every
    point the old read stored is stored again with the same value and source; the only additions are the new series."""
    _api, rd = _read()
    old = copy.deepcopy(rd)
    old.pop("noncirc_listed_metrics")
    for w in old["exclude_wallets"]:
        w.pop("metric")
    before, after = _vals(_run(read=old)), _vals(_run(read=rd))
    assert {m for m, _d in before} >= {"total_supply_gross", "future_emissions_tokens", "emissions_tokens",
                                       "core_burn_tokens", "circulating_supply_tokendetails",
                                       "circulating_supply_first_party", "team_wallet_tokens",
                                       "excluded_wallets_tokens", "staked_noncirculating_tokens"}
    assert {k: after[k] for k in before} == before
    assert {m for m, _d in set(after) - set(before)} == set(NEW)


def test_the_new_series_are_declared_evidence_not_unwired():
    """The G12 wiring audit's class 4 (config.NOT_SHOWN): a stored series no cell reads is UNWIRED unless declared."""
    import wiring
    for m in NEW:
        assert "P4-Q7 evidence series" in config.not_shown_reason("Hyperliquid", m), m
        assert not config.METRICS[m].get("view_only")
    wiring.reset()
    rows = [{"project": "Hyperliquid", "metric": m, "now": 1.0, "label": m} for m in NEW]
    got = wiring.audit(rows)
    assert {got[("Hyperliquid", m)]["class"] for m in NEW} == {"NOT SHOWN"}


# ===================================================================================== (3) the RUNBOOK check
def _runbook_check() -> str:
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = text[text.index("## 11ds. P4-Q7 interim"):]
    sec = sec[:sec.index("\n## ", 5)]
    (cmd,) = re.findall(r"^\s*(python -c \".*\")\s*$", sec, re.M)
    return cmd


def test_the_runbook_section_is_at_the_top_of_the_11d_run():
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    heads = re.findall(r"^## (11d\w*)\.", text, re.M)
    assert heads[0] == "11ds"
    sec = text[text.index("## 11ds."):text.index("## 11dr.")]
    assert "2026-12-15" in sec and "mode=ro" in sec and "hole" in sec
    for m in NEW:
        assert m in sec


def test_the_runbook_live_store_check_is_read_only_and_counts_missing_days(tmp_path):
    cmd = _runbook_check()
    assert "mode=ro" in cmd and "uri=True" in cmd
    for m in NEW + ("total_supply_gross", "future_emissions_tokens", "team_wallet_tokens", "excluded_wallets_tokens"):
        assert m in cmd
    import store as sm
    st = sm.Store(str(tmp_path / "metrics.db"))
    y = (pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)).tz_localize(None)
    days = [y - pd.Timedelta(days=i) for i in range(60) if i != 5]                 # one day missing
    st.upsert(pd.DataFrame({"date": days, "project": "Hyperliquid", "metric": "total_supply_gross", "value": 1.0,
                            "source": "x", "tier": 1}))
    st.conn.close()
    before = (tmp_path / "metrics.db").read_bytes()
    code = cmd[len('python -c "'):-1]
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    lines = {ln.split(":")[0]: ln for ln in r.stdout.splitlines()}
    gap = str((y - pd.Timedelta(days=5)).date())
    assert lines["total_supply_gross"].startswith("total_supply_gross: 59 stored days; missing 1 of the last 60:")
    assert lines["total_supply_gross"].endswith(gap)
    assert "0 stored days; missing 60 of the last 60" in lines["foundation_wallet_tokens"]
    assert (tmp_path / "metrics.db").read_bytes() == before
    # read-only: it never creates a store where there is none
    empty = tmp_path / "none"
    empty.mkdir()
    r = subprocess.run([sys.executable, "-c", code], cwd=empty, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and not (empty / "metrics.db").exists()
