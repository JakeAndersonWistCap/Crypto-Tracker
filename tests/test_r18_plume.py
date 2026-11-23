"""Round 18 (Jake 2026-11-24) — Plume's RWA figure follows a declared issuer list (SPEC "Round 18 — Plume").
Behaviour and numbers, not wording."""
from __future__ import annotations

import pandas as pd
import pytest

import config
from fetch import llama
from fetch.base import FetchOutput
from fetch.llama import DefiLlama
from fetch.logcache import DailyChecks

CHAIN = "Plume Mainnet"
TODAY = "2026-11-23"
D0 = pd.Timestamp("2026-11-18")


def _iso(day: pd.Timestamp, hh: int = 23) -> str:
    return f"{day:%Y-%m-%d}T{hh:02d}:01:00.000Z"


def _chart(first: pd.Timestamp, values, skip=(), twice=()):
    """One 23:01 snapshot a day from `first`; `twice` days also carry an earlier one; plus today's 11:01 snapshot."""
    pts = []
    for i, v in enumerate(values):
        d = first + pd.Timedelta(days=i)
        if str(d.date()) in skip:
            continue
        if str(d.date()) in twice:
            pts.append({"timestamp": _iso(d, 9), "tvlUsd": v + 5_000_000})
        pts.append({"timestamp": _iso(d), "tvlUsd": v})
    pts.append({"timestamp": _iso(pd.Timestamp(TODAY), 11), "tvlUsd": 999})
    return {"status": "success", "data": pts}


def _ptvl(first: pd.Timestamp, values, skip=()):
    """/protocol chainTvls list: a 00:00 point a day, and a 'current' point on the last day."""
    pts = [{"date": int((first + pd.Timedelta(days=i)).timestamp()), "totalLiquidityUSD": v}
           for i, v in enumerate(values) if str((first + pd.Timedelta(days=i)).date()) not in skip]
    pts.append({"date": pts[-1]["date"] + 40_000, "totalLiquidityUSD": 777})
    return {"chainTvls": {CHAIN: {"tvl": pts}}}


# A SYNTHETIC TWO-CATEGORY SET: issuer A moved to DefiLlama's RWA product (category RWA, no TVL on /protocols, two pools on
# the chain and one on another chain); issuer B is filed under Yield and still on /protocols; C is an RWA-category
# protocol that is NOT on the list; D is a Lending protocol on the chain.
SPEC = {"chain": CHAIN, "from": "2026-11-18", "declared": "2026-11-24", "issuers": (
    {"issuer": "A", "slug": "vaults-a", "read": "yields", "pools": ("pool-a1",), "evidence": "x"},
    {"issuer": "B", "slug": "issuer-b", "read": "protocol", "evidence": "x"})}
PROTOCOLS = [
    {"slug": "vaults-a", "category": "RWA", "module": "dummy.js", "chains": [], "chainTvls": {}, "tvl": None},
    {"slug": "issuer-b", "category": "Yield", "chains": [CHAIN], "chainTvls": {CHAIN: 6_000_000.0}},
    {"slug": "small-c", "category": "RWA", "chains": [CHAIN], "chainTvls": {CHAIN: 990_000.0}},
    {"slug": "lender-d", "category": "Lending", "chains": [CHAIN], "chainTvls": {CHAIN: 550_000.0}},
]
POOLS = [
    {"chain": CHAIN, "project": "vaults-a", "pool": "pool-a1", "tvlUsd": 40_000_000},
    {"chain": CHAIN, "project": "vaults-a", "pool": "pool-a2", "tvlUsd": 2_500_000},
    {"chain": "Ethereum", "project": "vaults-a", "pool": "pool-a-eth", "tvlUsd": 36_000_000},
    {"chain": CHAIN, "project": "someone-else", "pool": "pool-x", "tvlUsd": 16_000_000},
]
CHARTS = {"pool-a1": _chart(D0, [41e6, 41.5e6, 42e6, 40.5e6, 40.2e6], twice=("2026-11-20",)),
          "pool-a2": _chart(D0 + pd.Timedelta(days=2), [2.0e6, 2.2e6, 2.4e6])}       # listed from 2026-11-20
PROT = {"issuer-b": _ptvl(D0, [6.1e6, 6.2e6, 6.3e6, 6.4e6, 6.5e6])}


class _Http:
    def __init__(self, protocols=None, pools=None, charts=None, prot=None, fail=()):
        self.calls, self.fail = [], set(fail)
        self.protocols, self.pools = protocols or PROTOCOLS, POOLS if pools is None else pools
        self.charts, self.prot = charts or CHARTS, prot or PROT

    def get(self, url, params=None, **_):
        self.calls.append(url)
        key = url.rsplit("/", 1)[-1]
        if key in self.fail:
            raise RuntimeError("timeout")
        if url == f"{llama.API}/protocols":
            return self.protocols
        if url == f"{llama.YIELDS}/pools":
            return {"status": "success", "data": self.pools}
        if url.startswith(f"{llama.YIELDS}/chart/"):
            return self.charts[key]
        return self.prot[key]


def _run(tmp_path, monkeypatch, http=None, stored=True, spec=SPEC, daily=None):
    monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "")
    monkeypatch.setattr(config, "RWA_ISSUERS", {"Plume": spec})
    st = (pd.DataFrame([{"project": "Plume", "metric": "rwa_defillama_usd", "date": pd.Timestamp("2026-10-01"),
                         "value": 1.0}]) if stored else None)
    d = DefiLlama(stored_long=st, part="tvl")
    d.daily = daily or DailyChecks(tmp_path)
    d.http = http or _Http()
    d._today = TODAY
    out = FetchOutput()
    d.rwa([config.PROJECT_BY_NAME["Plume"]], 30, out)
    f = out.frame()
    by = {} if f is None or f.empty else {
        (r.metric, str(pd.Timestamp(r.date).date())): r.value for r in f.itertuples()}
    return d, out, by


def test_the_sum_follows_the_issuer_list_across_two_categories(tmp_path, monkeypatch):
    _d, _out, by = _run(tmp_path, monkeypatch)
    # today: A's two pools ON THE CHAIN (not its Ethereum pool, not another project's pool) + B's chain figure
    assert by[("rwa_defillama_usd", TODAY)] == pytest.approx(40_000_000 + 2_500_000 + 6_000_000)
    # the category figure (the one RWA-category protocol still on /protocols) is the cross-check row, not the series
    assert by[("rwa_defillama_category_usd", TODAY)] == pytest.approx(990_000)
    # a past day: A's pools + B, C and D never in it
    assert by[("rwa_defillama_usd", "2026-11-21")] == pytest.approx(40.5e6 + 2.2e6 + 6.4e6)
    # a pool not listed yet adds nothing before its first day (the issuer's other pool is the issuer that day)
    assert by[("rwa_defillama_usd", "2026-11-18")] == pytest.approx(41e6 + 6.1e6)
    assert by[("rwa_defillama_usd", "2026-11-19")] == pytest.approx(41.5e6 + 6.2e6)


def test_one_value_per_pool_and_day_and_today_is_not_in_the_history(tmp_path, monkeypatch):
    daily = DefiLlama._daily_pool_tvl(CHARTS["pool-a1"]["data"])
    assert daily[pd.Timestamp("2026-11-20")] == 42e6                 # the day's LAST snapshot, not 42M + 47M
    assert len(daily) == 6 and daily[pd.Timestamp(TODAY)] == 999
    assert pd.Timestamp(TODAY) not in DefiLlama._daily_pool_tvl(CHARTS["pool-a1"]["data"], before=pd.Timestamp(TODAY))
    assert DefiLlama._daily_pool_tvl([{"timestamp": _iso(D0), "tvlUsd": None}]) == {}     # no figure is not 0
    _d, _out, by = _run(tmp_path, monkeypatch)
    assert by[("rwa_defillama_usd", "2026-11-20")] == pytest.approx(42e6 + 2.0e6 + 6.3e6)
    # today's row is the one-call figure (/pools + /protocols), not the chart's part-day snapshot (999) or B's 777
    assert by[("rwa_defillama_usd", TODAY)] == pytest.approx(48_500_000)
    # B's /protocol list: its 00:00 point of the day, the 'current' point of the same day not added (R17's rule)
    assert by[("rwa_defillama_usd", "2026-11-22")] == pytest.approx(40.2e6 + 2.4e6 + 6.5e6)


@pytest.mark.parametrize("case", ["no_pool", "declared_pool_gone", "no_chain_figure", "pool_without_tvl"])
def test_an_issuer_that_cannot_be_read_stores_nothing_for_the_day(tmp_path, monkeypatch, case):
    pools, protocols = [dict(q) for q in POOLS], [dict(q) for q in PROTOCOLS]
    if case == "no_pool":
        pools = [q for q in pools if q["project"] != "vaults-a" or q["chain"] != CHAIN]
    elif case == "declared_pool_gone":
        pools = [q for q in pools if q["pool"] != "pool-a1"]          # 2.5M would be a short sum
    elif case == "no_chain_figure":
        protocols[1]["chainTvls"] = {}
    else:
        pools[1]["tvlUsd"] = None
    _d, out, by = _run(tmp_path, monkeypatch, http=_Http(protocols=protocols, pools=pools))
    assert not [k for k in by if k[0] == "rwa_defillama_usd"]        # not today's row, not a past day
    assert any(e.status != "ok" and "NOTHING STORED" in str(e.message) for e in out.log)


def test_a_history_that_cannot_be_read_stores_no_past_day_and_is_read_again(tmp_path, monkeypatch):
    daily = DailyChecks(tmp_path)
    d, out, by = _run(tmp_path, monkeypatch, http=_Http(fail=("pool-a2",)), daily=daily)
    assert {k[1] for k in by if k[0] == "rwa_defillama_usd"} == {TODAY}      # today's complete one-call sum only
    assert any(e.status != "ok" and "pool-a2" not in str(e.message) and "vaults-a could not be read" in str(e.message)
               for e in out.log)
    # neither mark is set: the next run makes the read again, and then stores the whole span
    assert daily.due("defillama:rwa", TODAY) and daily.due("defillama:rwa:issuers:Plume", "once")
    _d2, _o2, by2 = _run(tmp_path, monkeypatch, daily=daily)
    assert ("rwa_defillama_usd", "2026-11-18") in by2


def test_a_hole_inside_a_pools_life_leaves_the_day_out_never_a_short_sum(tmp_path, monkeypatch):
    charts = dict(CHARTS, **{"pool-a2": _chart(D0 + pd.Timedelta(days=2), [2.0e6, 2.2e6, 2.4e6], skip=("2026-11-21",))})
    _d, _out, by = _run(tmp_path, monkeypatch, http=_Http(charts=charts))
    days = {k[1] for k in by if k[0] == "rwa_defillama_usd"}
    assert "2026-11-21" not in days and {"2026-11-20", "2026-11-22"} <= days
    # a /protocol issuer with no point on a day: the same
    prot = {"issuer-b": _ptvl(D0, [6.1e6, 6.2e6, 6.3e6, 6.4e6, 6.5e6], skip=("2026-11-19",))}
    _d, _out, by = _run(tmp_path / "b", monkeypatch, http=_Http(prot=prot))
    assert ("rwa_defillama_usd", "2026-11-19") not in by and ("rwa_defillama_usd", "2026-11-20") in by


def test_the_series_starts_where_every_issuer_is_served_and_not_before_the_declared_day(tmp_path, monkeypatch):
    # B is served from 2026-11-20 only: 11-18 and 11-19 would be A alone — not stored
    prot = {"issuer-b": _ptvl(D0 + pd.Timedelta(days=2), [6.3e6, 6.4e6, 6.5e6])}
    _d, _out, by = _run(tmp_path, monkeypatch, http=_Http(prot=prot))
    assert min(k[1] for k in by if k[0] == "rwa_defillama_usd") == "2026-11-20"
    # the declared handover day bounds it too
    _d, _out, by = _run(tmp_path / "b", monkeypatch, spec=dict(SPEC, **{"from": "2026-11-21"}))
    assert min(k[1] for k in by if k[0] == "rwa_defillama_usd") == "2026-11-21"


def test_the_span_is_rebuilt_once_then_two_list_calls_a_day(tmp_path, monkeypatch):
    daily = DailyChecks(tmp_path)
    d, _out, by = _run(tmp_path, monkeypatch, daily=daily)
    assert sorted(k[1] for k in by if k[0] == "rwa_defillama_usd") == [
        "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", TODAY]
    assert len(d.http.calls) == 2 + 2 + 1                             # /protocols, /pools, two charts, one /protocol
    daily.done("defillama:rwa", "2026-11-22")                         # another day: the daily read is due again
    d2, _o2, by2 = _run(tmp_path, monkeypatch, daily=daily)
    assert sorted(c.rsplit("/", 1)[-1] for c in d2.http.calls) == ["pools", "protocols"]
    assert {k[1] for k in by2 if k[0] == "rwa_defillama_usd"} == {TODAY}


def test_a_run_made_after_todays_read_still_makes_the_one_rebuild(tmp_path, monkeypatch):
    """The run's clean-up removes the earlier leg's rows before the fetch: the rebuild must not wait for tomorrow because
    an earlier run today already read the category."""
    daily = DailyChecks(tmp_path)
    daily.done("defillama:rwa", TODAY)                                 # this morning's run, before the declaration
    _d, _out, by = _run(tmp_path, monkeypatch, daily=daily)
    assert by[("rwa_defillama_usd", "2026-11-18")] == pytest.approx(41e6 + 6.1e6)
    d2, _o2, by2 = _run(tmp_path, monkeypatch, daily=daily)            # and then it is once a day again
    assert d2.http.calls == [] and not by2


def test_a_chain_without_an_issuer_list_is_summed_by_category_as_before(tmp_path, monkeypatch):
    """The shared read: with Plume's list on file, another chain's figure is the category sum, to the dollar."""
    monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "")
    eth = config.PROJECT_BY_NAME["Ethereum"]
    ch = eth["defillama_chain"]
    protocols = [{"slug": "r1", "category": "RWA", "chains": [ch], "chainTvls": {ch: 1_000.0}},
                 {"slug": "r2", "category": "RWA Lending", "chains": [ch, CHAIN], "chainTvls": {ch: 250.0, CHAIN: 7.0}},
                 {"slug": "y1", "category": "Yield", "chains": [ch], "chainTvls": {ch: 9_999.0}}]

    def run(issuers):
        monkeypatch.setattr(config, "RWA_ISSUERS", issuers)
        st = pd.DataFrame([{"project": "Ethereum", "metric": "rwa_defillama_usd", "date": pd.Timestamp("2026-10-01"),
                            "value": 1.0}])
        d = DefiLlama(stored_long=st, part="tvl")
        d.daily = DailyChecks(tmp_path / str(len(issuers)))
        d.http = _Http(protocols=protocols)
        d._today = TODAY
        out = FetchOutput()
        d.rwa([eth], 30, out)
        f = out.frame()
        return {(r.project, r.metric, r.source): r.value for r in f.itertuples()}, d.http.calls
    with_list, calls = run({"Plume": SPEC})
    without, calls0 = run({})
    assert with_list == without == {("Ethereum", "rwa_defillama_usd", "defillama"): 1_250.0}
    assert calls == calls0 == [f"{llama.API}/protocols"]


def test_the_list_is_declared_dated_and_evidenced_and_the_handover_is_a_series_break():
    spec = config.RWA_ISSUERS["Plume"]
    assert spec["chain"] == config.PROJECT_BY_NAME["Plume"]["defillama_chain"] == CHAIN
    assert spec["from"] == "2026-05-21" and spec["declared"] == "2026-11-24"
    slugs = [it["slug"] for it in spec["issuers"]]
    assert slugs == ["plume-vaults", "centrifuge-protocol", "invesco-ustb", "realtyx", "harvest-flow", "spice-protocol",
                     "fractit-fleet"]
    assert all(it.get("evidence") and it["read"] in ("yields", "protocol") for it in spec["issuers"])
    assert all(it.get("pools") for it in spec["issuers"] if it["read"] == "yields")
    # holders of the issuers' tokens and what was never in the series are named with a figure, never summed
    assert not set(spec["not_listed"]) & set(slugs)
    assert {"grove-finance", "nucleus", "bitwise-uscc", "sierra-protocol"} <= set(spec["not_listed"])
    assert all(v["usd"] > 0 and v["why"] for v in spec["not_listed"].values())
    # the handover is declared (two points, in order, the quantity steps): one series with a break, never blanked
    decl = config.declared_handover("Plume", "rwa_defillama_usd")
    assert decl["ordered_points"] == ("defillama", "defillama_rwa_issuers") and decl["declared"] == spec["from"]
    assert not decl["composition_change"].lower().startswith("none")
    import trajectory
    g = pd.DataFrame({"date": pd.to_datetime(["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]),
                      "value": [606e6, 602e6, 90e6, 90e6],
                      "source": ["defillama", "defillama", "defillama_rwa_issuers", "defillama_rwa_issuers"]})
    br = trajectory._own_breaks("Plume", "rwa_defillama_usd", {("Plume", "rwa_defillama_usd"): g}, pd.Timestamp("2026-01-01"))
    assert [str(b[0].date()) for b in br] == ["2026-05-21"]
    m = config.METRICS["rwa_defillama_category_usd"]
    assert m["only_projects"] == ("Plume",)
    assert "rwa_defillama_usd" in config.BUSINESS_SLOTS["Plume"]


def test_the_numbers_read_on_2026_11_23():
    spec = config.RWA_ISSUERS["Plume"]
    # Grove's ALMProxy holds 14,963,131 of the 15,061,147 ACRDX on Plume: grove-finance's Plume TVL is that fund again
    assert 14_963_131 / 15_061_147 == pytest.approx(0.9935, abs=1e-4)
    assert spec["not_listed"]["grove-finance"]["usd"] / 15_386_584 == pytest.approx(0.9936, abs=2e-3)
    # the list against the stored figure on the days both exist: never above it, 64% to 89%
    ratios = {d: b / a for d, (a, b) in spec["overlap"].items()}
    assert ratios["2026-06-01"] == pytest.approx(0.890, abs=2e-3) and ratios["2026-09-17"] == pytest.approx(0.817, abs=2e-3)
    assert ratios["2026-10-07"] == pytest.approx(0.642, abs=2e-3) and max(ratios.values()) < 1
    # USTB and JTRSY on Plume sit inside nTBILL (3,748,314): 3,275,000 + 556,000 of it
    st = spec["stacking"]
    assert st["USTB in nTBILL"]["usd"] + st["JTRSY in nTBILL"]["usd"] == pytest.approx(3_748_314, rel=0.03)
    assert sum(v["usd"] for v in st.values()) == 7_303_000


def test_the_rows_are_stored_under_their_own_measuring_point(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "")
    monkeypatch.setattr(config, "RWA_ISSUERS", {"Plume": SPEC})
    d = DefiLlama(stored_long=None, part="tvl")
    d.daily, d.http, d._today = DailyChecks(tmp_path), _Http(), TODAY
    out = FetchOutput()
    d.rwa([config.PROJECT_BY_NAME["Plume"]], 30, out)
    f = out.frame()
    assert set(f[f["metric"] == "rwa_defillama_usd"]["source"]) == {"defillama_rwa_issuers"}
    assert set(f[f["metric"] == "rwa_defillama_category_usd"]["source"]) == {"defillama"}


def test_the_earlier_legs_rows_after_the_handover_day_go_and_the_lists_rows_stay(tmp_path):
    """The run's declared clean-up: a category-sum row dated on or after 2026-05-21 is removed (the ~$1M rows, the old
    basis inside the new leg, a day the list cannot form); rows before the handover and the list's own rows stay."""
    import logging
    import store as sm
    import token_metrics as tm

    def row(metric, day, value, source):
        return {"date": pd.Timestamp(day), "project": "Plume", "metric": metric, "value": float(value),
                "source": source, "tier": 1}
    st = sm.Store(str(tmp_path / "metrics.db"))
    st.upsert(pd.DataFrame([
        row("rwa_defillama_usd", "2026-05-20", 602_240_000, "defillama"),              # before the handover: stays
        row("rwa_defillama_usd", "2026-05-21", 607_351_612, "defillama"),              # old basis on the handover day
        row("rwa_defillama_usd", "2026-07-27", 150_860_000, "defillama"),              # a day the list cannot form
        row("rwa_defillama_usd", "2026-11-20", 989_156, "defillama"),                  # the ~$1M category row
        row("rwa_defillama_usd", "2026-11-22", 62_839_521, "defillama_rwa_issuers"),   # the list's row: stays
        row("rwa_defillama_category_usd", "2026-11-23", 986_609, "defillama"),         # the cross-check: stays
        row("tvl_usd", "2026-11-20", 1_038_028, "defillama")]))                        # another series: stays
    lines = [x for x in tm.apply_run_cleanups(st, "r", logging.getLogger("t")) if "R18-plume-rwa" in x]
    assert len(lines) == 3
    left = pd.read_sql("SELECT metric, date, value, source FROM metrics WHERE project='Plume'", st.conn)
    kept = {(r.metric, str(r.date)[:10]): r.value for r in left.itertuples()}
    assert kept == {("rwa_defillama_usd", "2026-05-20"): 602_240_000.0,
                    ("rwa_defillama_usd", "2026-11-22"): 62_839_521.0,
                    ("rwa_defillama_category_usd", "2026-11-23"): 986_609.0,
                    ("tvl_usd", "2026-11-20"): 1_038_028.0}
    assert [x for x in tm.apply_run_cleanups(st, "r2", logging.getLogger("t")) if "R18-plume-rwa" in x] == []
    st.close()


def test_the_live_reads_are_checks_jake_can_run():
    import check_offline_items as coi
    for fn in (coi.plume_rwa_issuers, coi.plume_rwa_archive):
        assert fn in coi.CHECKS
