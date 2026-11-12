"""Project round 4C — finish Morpho, and the timeouts (Jake 2026-11-12). No network."""
import json

import pandas as pd
import pytest

import build_workbook as bw
import config
import project_freeze as pf


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "pr4c")
    assert "error" not in rec["stats"], rec["stats"]
    return rec


# ======================================================================================== 1: Morpho's relock, modelled
def test_1_the_relocked_balance_follows_the_published_schedule():
    r = config.MORPHO_RELOCK
    assert (r["start"], r["end"]) == ("2025-11-21", "2027-11-21")
    assert sum(a for _, a in r["tranches"]) == 219_000_000
    assert config.relocked_on("2025-09-08") == 219_000_000            # before the cliff: all of it
    assert config.relocked_on("2025-11-21") == 219_000_000
    assert config.relocked_on("2026-11-12") == pytest.approx(219_000_000 * 374 / 730)    # 112.2M
    assert config.relocked_on("2027-11-21") == 0 and config.relocked_on("2028-01-01") == 0
    assert config.CIRCULATING_ONCHAIN["Morpho"]["subtract"] == ("noncirculating_holding_tokens", "morpho_relocked_tokens")
    # each day's vesting is an unlock (e): the modelled stock is a leg of the release
    assert "morpho_relocked_tokens" in config.unlock_stock_metrics("Morpho")
    assert config.METRICS["morpho_relocked_tokens"]["label"].count("MODELLED") == 1
    assert "6.23%" in r["uncertainty"]


def test_1_the_view_is_labelled_modelled_and_vesting_reaches_the_release():
    days = pd.date_range("2026-11-10", periods=3)
    tot = pd.DataFrame({"date": days, "project": "Morpho", "metric": "total_supply", "value": 1e9, "source": "chain",
                        "tier": 2})
    groups = {("Morpho", "total_supply"): tot}
    bw._modelled_lock_views(groups)
    v = groups[("Morpho", "morpho_relocked_tokens")]
    assert v["source"].iloc[0] == "modelled:relock_schedule[2025-11-21..2027-11-21]"
    assert list(v["value"].diff().dropna().round(2)) == [-300_000.0, -300_000.0]   # 219M / 730 a day
    hold = pd.DataFrame({"date": days, "project": "Morpho", "metric": "noncirculating_holding_tokens", "value": 2.8e8,
                         "source": "chain:sum(x)", "tier": 2})
    groups[("Morpho", "noncirculating_holding_tokens")] = hold
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", config.unlock_stock_metrics("Morpho"))
    assert list(groups[("Morpho", "unlock_release_tokens")]["value"].round(2)) == [300_000.0, 300_000.0]


def test_1_on_the_dumps_the_schedule_takes_121_2m_out_and_unlocks_27m(built):
    m = pf.cells(built["audit"], built["reader"], "Morpho")
    cp = "audit|CP Comparability|"
    circ = next(v for k, v in m.items() if k.startswith(cp + "Circulating supply (tokens)"))
    assert circ == pytest.approx(696_001_146.74 - config.relocked_on("2026-10-13"), abs=1)
    unl = next(v for k, v in m.items() if k.startswith(cp + "Unlocks Q0"))
    assert unl == pytest.approx(219_000_000 * 90 / 730, abs=1)


# ================================================================================================ 2: the timeouts
def test_2_the_chain_tier_is_one_tier_per_project_each_with_the_chain_budget():
    import fetch
    from fetch import gaps
    from fetch.chain import Chain
    names = [t[0] for t in fetch.TIER_ORDER]
    chain = [n for n in names if n.split("@")[0] == "chain"]
    assert "chain" not in names and "chain@Morpho" in chain and "chain@Maple" in chain
    assert len(chain) == sum(1 for p in config.PROJECTS if p.get("contracts"))
    assert all(fetch._budget(n) == fetch.TIER_BUDGET_S["chain"] for n in chain)
    maple, morpho = config.PROJECT_BY_NAME["Maple"], config.PROJECT_BY_NAME["Morpho"]
    assert "maple_ssf_safe_tokens" in gaps.served_by("chain@Maple", maple)
    assert gaps.served_by("chain@Maple", morpho) == set()              # a tier's timeout names its own project only
    assert Chain(only="Maple").only == "Maple"
    out = []

    class _Out:
        def __getattr__(self, k):
            return lambda *a, **kw: out.append(k)
    Chain(only="Nobody").run([maple, morpho], None, _Out())
    assert out == []                                                    # nothing of another project is read
    src = open(fetch.__file__, encoding="utf-8").read()
    assert 'name.split("@", 1)[0] == "chain" and "chain" in sources' in src   # --source chain still selects them
    csrc = open("fetch/chain.py", encoding="utf-8").read()
    assert "with connection_slot(rpc_host(url)):" in csrc                # the RPC posts take the run-wide slots


def test_2_morphos_base_claims_read_only_the_new_days():
    p = config.PROJECT_BY_NAME["Morpho"]
    saved = list(p.get("log_scans") or ())
    try:
        config.wire_probed_scans({"alchemy": {"served": [8453]}}, projects={"Morpho"}, open_routes=False)
        base = next(s for s in p["log_scans"] if s["key"] == "merkl_morpho_out_base")
        assert base["incremental_after_seed"] is True and base["window_days"] == 100
    finally:
        p["log_scans"] = saved


def test_2_a_switch_leg_is_owed_from_the_window_start_and_the_switch_span_is_the_window():
    from fetch.archive import catch_up_window_days, owed_days
    assert config.switch_legs() == {("Maple", "maple_ssf_safe_tokens"), ("Morpho", "morpho_undistributed_tokens"),
                                    ("Morpho", "morpho_released_held_tokens")}
    rows = [("2026-11-10", "chain:ethereum:noncirc_ssf_safe")]
    assert owed_days("Maple", "maple_ssf_safe_tokens", rows, "2026-11-01", "2026-11-11") == ["2026-11-11"]
    full = owed_days("Maple", "maple_ssf_safe_tokens", rows, "2026-11-01", "2026-11-11", from_window=True)
    assert len(full) == 10 and full[-1] == "2026-11-01"
    # the switch needs exactly the catch-up window, so the daily catch-up can hold every day it needs
    W = catch_up_window_days()
    days = pd.date_range("2025-01-01", periods=W + 30)
    s = pd.Series(1.0, index=days)
    leg = pd.DataFrame({"date": days[-W:], "project": "Maple", "metric": "leg", "value": 1.0, "source": "x", "tier": 2})
    swc = {"metric": "leg", "whole_series": True, "sign": 1}
    out, _ = bw._subtract_when_complete({("Maple", "leg"): leg}, "Maple", s, "t", swc)
    assert swc["in_force"] is True and len(out) == W


# ================================================================================================= 3: the probe file
def test_3_the_pin_has_mantle_and_the_report_prints_no_key():
    assert 5000 in config.frozen_probe_chains()
    st = {"probed_on": "2026-10-14", "served": [5000, 9745],
          "refused": {"56": "status '0' https://api.etherscan.io/v2/api?apikey=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"},
          "alchemy": {"probed_on": "2026-10-18", "served": [10, 56, 8453, 80094],
                      "refused": {"5000": "mantle-mainnet.g.alchemy.com: EAPIs not enabled", "146": "sonic: x"},
                      "not_tried": {}}}
    rep = config.probe_report(st)
    assert "ABCDEFGHIJ" not in rep and "/v2/api" not in rep and "https://" not in rep
    assert "PIN MATCH" in rep and "Pendle:gauge_pendle_out_mantle" in rep
    st["served"] = [5000, 146, 9745]
    assert "PIN DIFFERS" in config.probe_report(st) and "only in the file [146]" in config.probe_report(st)


# ================================================================================================= 5: Morpho frozen
def test_5_morpho_is_frozen_with_round_4_4b_and_4cs_moves_declared(built):
    assert config.FROZEN_PROJECTS["Morpho"]["baseline"] == "tests/fixtures/frozen_morpho.json"
    base = json.loads(pf.baseline_path("Morpho").read_text(encoding="utf-8"))
    moves = config.FROZEN_MOVES["Morpho"]
    assert len(base) > 250 and moves
    assert all(d["why"].startswith(("PR4.", "PR4B.", "PR4C.")) for d in moves.values())
    assert all(base[k] == pf._norm(d["to"]) for k, d in moves.items() if k in base)
    now = pf.cells(built["audit"], built["reader"], "Morpho")
    assert pf.undeclared(base, now, moves) == []
