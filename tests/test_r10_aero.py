"""Round 10, Part A — Aerodrome (Jake 2026-11-18). No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
from fetch import aero_v3  # noqa: E402


def _g(metric, rows):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _v, _s in rows]), "project": "Aerodrome", "metric": metric,
                         "value": [float(v) for _d, v, _s in rows], "tier": 2, "source": [s for _d, _v, s in rows]})


# ================================================================= A1a: the 10-22 handover of the supply legs, cut
def test_a1a_a_legacy_row_after_the_new_reads_first_row_is_dropped_so_the_legs_meet_once(monkeypatch):
    """Jake's store: the legacy reader wrote total_supply on 10-23, 10-25 and 10-26, after the new read's 10-22 row."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    old, new = "chain:base:token", "aero_v3:supply_once[ratio 1]:archive"
    g = {("Aerodrome", "total_supply"): _g("total_supply", [
        ("2026-10-20", 1_850e6, old), ("2026-10-21", 1_851e6, old), ("2026-10-22", 2_118e6, new),
        ("2026-10-23", 1_852e6, old), ("2026-10-24", 2_120e6, new), ("2026-10-25", 1_853e6, old)])}
    bw._handover_cut_views(g)
    t = g[("Aerodrome", "total_supply")].set_index("date")["value"]
    assert list(t.index.strftime("%m-%d")) == ["10-20", "10-21", "10-22", "10-24"]     # 10-23 and 10-25: unknown
    assert t.is_monotonic_increasing                                                   # no fall back to the old token
    for m in ("total_supply", "noncirculating_holding_tokens"):
        h = config.PROJECT_BY_NAME["Aerodrome"]["series_handover"][m]
        assert h["cut"] is True and h["ordered_points"][-1] == "aero_v3:supply_once"


def test_a1a_the_backfill_reads_a_legacy_day_after_the_cutover_again_and_nothing_else():
    stored = [("2026-10-20", "chain:base:token"), ("2026-10-22", "aero_v3:supply_once[ratio 1]:archive"),
              ("2026-10-23", "chain:base:token"), ("2026-10-27", "aero_v3:supply_once[ratio 1]")]
    held = aero_v3.held_on_the_new_read(stored, config.AERODROME_MIGRATION["cutover_utc"])
    assert held == {"2026-10-20", "2026-10-22", "2026-10-27"}       # 10-23 (legacy, after the cutover) is not held


# ================================================================= A2: the epoch scan
def test_a2_an_endpoint_that_twice_refused_what_another_served_is_not_asked_again():
    r = aero_v3.AeroV3(rpc=None)
    asked = []

    def post(url):
        asked.append(url.split("/")[2])
        if "slow" in url:
            raise RuntimeError("HTTP 525")
        return {"result": "0x1"}
    urls = ["https://slow.example/x", "https://good.example/y"]
    for _ in range(4):
        assert r._first_answer("eth_getLogs", urls, post) == "0x1"
    assert asked.count("slow.example") == 2 and asked.count("good.example") == 4
    # another method starts afresh; and a request NO endpoint serves counts against none
    assert r._first_answer("eth_call", urls, post) == "0x1" and asked.count("slow.example") == 3
    r2 = aero_v3.AeroV3(rpc=None)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            r2._first_answer("eth_getLogs", ["https://a.example/1", "https://b.example/2"],
                             lambda url: (_ for _ in ()).throw(RuntimeError("range too wide")))
    assert not r2._refused


class _Cache:
    def __init__(self):
        self.d = {}

    def load(self, sid):
        return self.d.get(sid, {"events": [], "scanned_to": None})

    def save(self, sid, events, a, _b):
        self.d[sid] = {"events": list(events), "scanned_to": a}


def test_a2_a_log_filter_never_names_more_than_ten_addresses_and_loses_no_event():
    seen = []

    def rpc(method, params):
        assert method == "eth_getLogs"
        f = params[0]
        seen.append(len(f["address"]))
        return [{"address": a, "topics": ["0xt", "0x0", "0x" + "0" * 24 + "ab" * 20], "data": "0x1",
                 "blockNumber": hex(100 + int(a[-2:], 16)), "logIndex": "0x0", "transactionHash": "0x0"}
                for a in f["address"] if int(f["fromBlock"], 16) <= 100 + int(a[-2:], 16) <= int(f["toBlock"], 16)]
    r = aero_v3.AeroV3(rpc=rpc, cache=_Cache())
    mgrs = ["0x" + "0" * 38 + f"{i:02x}" for i in range(1, 24)]            # 23 managers
    got = r._logs(mgrs, ["0xt"], 100, 200)
    assert max(seen) <= aero_v3.LOG_ADDR_MAX == 10 and len(got) == 23
    assert [lg["blockNumber"] for lg in got] == sorted(lg["blockNumber"] for lg in got)


def test_a2_the_backfill_budget_replaces_the_daily_200_seconds_and_llamarpc_is_out_of_the_pool():
    assert aero_v3.AeroV3(rpc=lambda *a: None).epoch_budget_s == aero_v3.EPOCH_BUDGET_S == 200
    assert aero_v3.AeroV3(rpc=lambda *a: None, epoch_budget_s=1800).epoch_budget_s == 1800
    import check_offline_items as coi
    from fetch.chain import rpc_endpoints
    assert not any("llamarpc" in u for u in rpc_endpoints("base")) and not any("llamarpc" in u for u in coi._PUBLIC_BASE_RPCS)


def test_a2_the_declared_unreadable_filing_day_is_named_never_read_and_never_zero():
    class Rdr:
        def _block_at(self, ts):
            return 1

        def _filing_read(self, *a):
            raise AssertionError("the declared day must not be read")
    cut = pd.Timestamp(config.AERODROME_MIGRATION["cutover_utc"])
    legs = [m for m in aero_v3.SUPPLY_LEGS if m in config.METRICS]
    days = {str(d.date()) for d in pd.date_range(cut, pd.Timestamp.utcnow().tz_localize(None).normalize())}
    have = {m: set(days) for m in legs}
    have["filing_managed_lock_tokens"] = days - {"2026-10-22"}
    rows, why = aero_v3.supply_history(config.PROJECT_BY_NAME["Aerodrome"], have, reader=Rdr(),
                                       state={"addresses": {}, "launched": True})
    assert rows == [] and "2026-10-22 (DECLARED n/a" in why


# ================================================================= A1d: emissions across the migration
def _emis_groups():
    leg, v3 = "chain:base:minter[tail@21bps]", "aero_v3:minter.emissionsMinted:delta"
    return {("Aerodrome", "gross_issuance_tokens"): _g("gross_issuance_tokens", [
                ("2026-10-08", 4_195_056, leg), ("2026-10-15", 4_204_997, leg),
                ("2026-10-28", 332_205, v3), ("2026-10-29", 466_554, v3), ("2026-11-17", 386_465, v3),
                ("2026-11-18", 1_000_108, v3)]),
            ("Aerodrome", "aero_v3_emissions_minted_cumulative"): _g("aero_v3_emissions_minted_cumulative", [
                ("2026-10-27", 2_328_134, "aero_v3:minter.emissionsMinted"),
                ("2026-10-28", 2_660_338, "aero_v3:minter.emissionsMinted")])}


def test_a1d_only_the_legacy_weekly_rows_are_redated_and_the_latest_days_are_kept(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    g = _emis_groups()
    bw._epoch_paid_views(g, pd.Timestamp("2026-11-18"))
    f = g[("Aerodrome", "gross_issuance_tokens")].set_index("date")
    days = list(f.index.strftime("%m-%d"))
    assert days == ["10-15", "10-22", "10-28", "10-29", "11-17", "11-18"]      # legacy +7 days; the v3 rows where read
    assert f.loc["2026-11-18", "value"] == 1_000_108                           # was dropped as "paid after the as-of"
    from fetch.validate import _span_days
    assert _span_days(f.loc["2026-10-22", "source"]) == 7 and _span_days(f.loc["2026-10-28", "source"]) == 1


def test_a1d_the_first_reading_of_a_counter_that_was_zero_is_the_flow_since_the_cutover(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    g = _emis_groups()
    bw._flow_from_zero_views(g)
    bw._epoch_paid_views(g, pd.Timestamp("2026-11-18"))
    f = g[("Aerodrome", "gross_issuance_tokens")].set_index("date")
    from fetch.validate import _span_days
    assert f.loc["2026-10-27", "value"] == 2_328_134 and _span_days(f.loc["2026-10-27", "source"]) == 5   # 10-23..10-27
    # the legacy week ends 10-22 and the launch row holds 10-23..10-27: no leg gap, the series is one piece
    s = bw._daily(g[("Aerodrome", "gross_issuance_tokens")])
    assert len(bw._after_leg_gap(g[("Aerodrome", "gross_issuance_tokens")], s)) == len(s)
    # a second build adds nothing: the reader's row on that day is already there
    n = len(g[("Aerodrome", "gross_issuance_tokens")])
    bw._flow_from_zero_views(g)
    assert len(g[("Aerodrome", "gross_issuance_tokens")]) == n
    assert "0 at Base block 52,880,070" in config.FLOW_FROM_ZERO_AT[("Aerodrome", "gross_issuance_tokens")]["evidence"]


def test_a1d_a_real_gap_between_two_readers_still_cuts_to_the_current_leg():
    old, new = "chain:base:minter", "aero_v3:minter.emissionsMinted:delta"
    g = _g("gross_issuance_tokens", [("2026-10-01", 1, old), ("2026-10-02", 1, old), ("2026-10-20", 1, new),
                                     ("2026-10-21", 1, new)])
    assert list(bw._after_leg_gap(g, bw._daily(g)).index.strftime("%m-%d")) == ["10-20", "10-21"]


def test_a1d_the_merger_issuance_is_the_step_of_the_total_across_the_handover(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    old, new = "chain:base:token", "aero_v3:supply_once[ratio 1]:archive"
    g = {("Aerodrome", "total_supply"): _g("total_supply", [
        ("2026-10-20", 2_002.38e6, old), ("2026-10-21", 2_002.38e6, old), ("2026-10-22", 2_119.38e6, new),
        ("2026-10-23", 2_006.87e6, old), ("2026-10-24", 2_124.49e6, new)])}
    bw._handover_step_views(g)
    m = g[("Aerodrome", "aero_merger_issuance_tokens")]
    assert len(m) == 1 and m["value"].iloc[0] == pytest.approx(117.0e6) and str(m["date"].iloc[0].date()) == "2026-10-22"
    # readings more than a day apart: what moved in between is not known — no row, never a guess
    g2 = {("Aerodrome", "total_supply"): _g("total_supply", [("2026-10-18", 2_002e6, old), ("2026-10-22", 2_119e6, new)])}
    bw._handover_step_views(g2)
    assert ("Aerodrome", "aero_merger_issuance_tokens") not in g2


def test_a1d_the_statement_balances_across_the_migration_quarter_on_every_mint(monkeypatch):
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Aerodrome"]])
    bw._ONE_OFF_IN_SERIES.clear()
    asof = pd.Timestamp("2026-11-18")
    days = pd.date_range("2026-05-01", "2026-11-18")
    cut = pd.Timestamp("2026-10-22")
    mint, rebase, team = 600_000.0, 50_000.0, 30_000.0
    circ, c = [], 1_900e6
    for d in days:
        c += mint + rebase + team + (117e6 if d == cut else 0.0)
        circ.append(c)
    mk = lambda m, v, src="x:delta": pd.DataFrame({"date": days, "project": "Aerodrome", "metric": m, "value": v,  # noqa: E731
                                                  "tier": 2, "source": src})
    g = {("Aerodrome", "circulating_supply_used"): mk("circulating_supply_used", circ, "derived:circulating_onchain"),
         ("Aerodrome", "gross_issuance_tokens"): mk("gross_issuance_tokens", mint),
         ("Aerodrome", "emissions_tokens"): mk("emissions_tokens", rebase),
         ("Aerodrome", "issuer_mint_tokens"): mk("issuer_mint_tokens", team),
         ("Aerodrome", "aero_merger_issuance_tokens"): _g("aero_merger_issuance_tokens", [(cut, 117e6, "derived:handover_step[x]")])}
    tf = {r["Line"]: r for r in bw.flow_statements(g, asof) if r["Statement"] == "Token flow"}
    line = next(k for k in tf if "Velodrome merger" in k)
    assert tf[line]["Q0"] == 117e6 and tf[line]["Q-1"] == 0.0             # an event: 0 in a quarter without it
    for q in ("Q0", "Q-1"):
        assert abs(tf["Residual ÷ opening"][q]) < 1e-9 and tf["Status"][q].startswith("OK"), (q, tf["Status"][q])


def test_a1d_a_weekly_row_that_says_its_span_covers_its_week_on_the_statement():
    days = pd.date_range("2026-08-27", "2026-11-12", freq="7D")
    g = pd.DataFrame({"date": days, "project": "Aerodrome", "metric": "gross_issuance_tokens", "value": 4e6, "tier": 2,
                      "source": "chain:base:minter[tail@21bps][span=7d]"})
    n, _lag = bw._flow_days(g, bw._daily(g), pd.Timestamp("2026-08-20"), pd.Timestamp("2026-11-18"), 1)
    assert n == 84                                              # 12 weekly rows x 7 days (was 12)
    # a period-dated series (one row a week, no span tag: the rebase) covers its weeks too
    plain = g.assign(source="chain:base:rewards_distributor")
    assert bw._flow_days(plain, bw._daily(plain), pd.Timestamp("2026-08-20"), pd.Timestamp("2026-11-18"), 1)[0] == 84
    # a DAILY series with missing days still counts only the days it holds
    dd = pd.date_range("2026-11-01", "2026-11-18").delete([3, 4, 9])
    daily = pd.DataFrame({"date": dd, "project": "Aerodrome", "metric": "x", "value": 1.0, "tier": 2, "source": "defillama"})
    assert bw._flow_days(daily, bw._daily(daily), pd.Timestamp("2026-08-20"), pd.Timestamp("2026-11-18"), 1)[0] == 15


def test_a1d_with_no_burn_mechanism_the_burned_buyback_is_zero_on_every_day_the_total_is_held(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    days = pd.date_range("2026-08-20", "2026-11-18")
    g = {("Aerodrome", "total_supply"): pd.DataFrame({"date": days, "project": "Aerodrome", "metric": "total_supply",
                                                     "value": 2.1e9, "tier": 2, "source": "aero_v3:supply_once"}),
         ("Aerodrome", "buyback_burned_tokens"): _g("buyback_burned_tokens", [("2026-11-04", 0.0, "derived:x")]),
         ("Aerodrome", "buyback_held_tokens"): _g("buyback_held_tokens", [("2026-11-04", 243_612.0, "derived:x")])}
    bw._burned_declared_zero_views(g)
    b = g[("Aerodrome", "buyback_burned_tokens")]
    # R13 (Jake 2026-11-19): the zero is declared for the LEGACY system only — the days before the migration; from
    # 2026-10-22 the burned leg is measured (tests/test_r13_aero_buyback.py). Was: len(b) == len(days) (all 91 days)
    before = days[days < pd.Timestamp("2026-10-22")]
    assert len(b) == len(before) == 63 and b["date"].max() == pd.Timestamp("2026-10-21")
    assert (b["value"] == 0.0).all() and "declared_zero" in b["source"].iloc[0]
    assert g[("Aerodrome", "buyback_held_tokens")]["value"].sum() == 243_612.0          # what is held is untouched
    assert config.PROJECT_BY_NAME["Aerodrome"]["burn_mechanism"]["status"] == "confirmed"
    assert set(config.BURNED_DECLARED_ZERO) == {"Aerodrome"}                             # no other project's burn moves


# ================================================================= A4: Fluid's 100M and the bad-print guard
def test_a4_the_restated_100m_series_holds_no_bad_print_and_coingeckos_one_day_100m_prints_still_are():
    """Jake's store: the guard left out CoinGecko's circulating_supply = 100,000,000 on 2026-02-10, 03-12 and 06-27
    (one-day prints of the TOTAL in a ~78-84M series). Under the rule OUR circulating is the total on every day — a
    flat series: no day is off its neighbours, so the guard has nothing to drop."""
    days = pd.date_range("2026-02-01", "2026-02-20")
    ours = pd.Series(100_000_000.0, index=days)
    assert bw.bad_prints(ours) == []
    cg = pd.Series(78_400_000.0, index=days)
    cg[pd.Timestamp("2026-02-10")] = 100_000_000.0
    assert [str(d.date()) for d, *_ in bw.bad_prints(cg)] == ["2026-02-10"]
    # and the guard reads STORED stock series only: the restated figure is a read-time view of total_supply
    assert config.METRICS["circulating_supply_onchain"].get("kind") == "stock"
    assert config.rule_in_legs("Fluid") == config.CIRCULATING_ONCHAIN["Fluid"]["subtract"]      # nothing subtracted
