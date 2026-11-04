"""ROUND P5-1.11 (Jake, 2026-11-04): THE CATCH-UP FILLS EVERY HOLE THAT HAS A PAST-DAY SOURCE.

Jake's live run: 5,471 holes over 250 series found and 0 filled; today's tiers read back 4 of 460 owed days. The causes,
each tested here on fakes (no network):
  - a RETIRED query's series (Ether.fi's Dune 8683038, retired 2026-09-24) was scanned as owed for ever — counted apart;
  - CoinGecko's circulating supply had no past-day route: market cap ÷ price (market_chart) now fills it; FDV, total and
    max supply have no free history (CoinGecko's API reference) — named not recoverable, never carried;
  - contract point reads (Chainlink's RewardVault, Ether.fi's accountant and share price) had no past-day route: the
    contract is read again at the owed day's first block through an archive endpoint;
  - the business / market-share reads kept their own 30-day span and the RWA read today's value only, so an owed day
    older than that was never served; Coinbase candles and DefiLlama coin prices asked 7 days; the Ethereum P2P BigQuery
    leg waited for its weekly turn — each now reads the owed days;
  - the summary counts holes per source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

TODAY = pd.Timestamp("2026-11-04")
Y = TODAY - pd.Timedelta(days=1)


def _rows(project, metric, days, value, source, tier=1):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(value(d)) if callable(value) else float(value) for d in days], "tier": tier,
                         "source": source})


def _days(n, end=Y):
    return [end - pd.Timedelta(days=i) for i in range(n - 1, -1, -1)]


@pytest.fixture
def st(tmp_path, monkeypatch):
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    s = sm.Store(tmp_path / "metrics.db")
    yield s
    s.close()


def _val(st, p, m, d):
    r = st.conn.execute("SELECT value, source FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)=?",
                        (p, m, d)).fetchone()
    return None if r is None else (r[0], r[1])


# ============================================================================================ retired series
def test_a_retired_query_s_series_is_not_a_hole_and_is_counted_apart(st):
    from fetch import archive as ar
    assert "Dune 8683038 retired 2026-09-24 by Jake" in ar.retired_why("Ether.fi", "lock_rate_pct", "dune:8683038")
    assert ar.retired_why("Ether.fi", "lock_rate_pct", "chain:ethereum:sethfi") == ""      # another source: not retired
    assert ar.retired_why("Maple", "ssf_net_outflow_tokens").startswith("BLOCKED")        # config.RETIRED_SERIES
    st.upsert(_rows("Ether.fi", "lock_rate_pct", _days(20, Y - pd.Timedelta(days=40)), 0.17, "dune:8683038", 2))
    assert ar.hole_route("Ether.fi", "lock_rate_pct", "dune:8683038", set()) == "retired"
    scan = ar.scan_holes(st, [config.PROJECT_BY_NAME["Ether.fi"]], set())
    assert scan[("Ether.fi", "lock_rate_pct")]["route"] == "retired"
    res = ar.catch_up(st, [config.PROJECT_BY_NAME["Ether.fi"]], budget_s=5, max_days=3, valuers=[], api=_NoCG(),
                      derive=False, contracts={}, tiers=True)
    assert ("Ether.fi", "lock_rate_pct") in res["retired"] and ("Ether.fi", "lock_rate_pct") not in res["tier_owed"]
    assert "40 day(s) of 1 RETIRED series not counted (no adapter reads them)" in res["lines"][-1][1]
    assert any("CATCH-UP Ether.fi/lock_rate_pct: RETIRED, not counted as holes" in t for _, t in res["lines"])


class _NoCG:
    def market_chart(self, *a):
        raise AssertionError("no CoinGecko series here")


# ============================================================================================ CoinGecko
def test_coingecko_routes_circulating_by_market_cap_over_price_and_names_the_rest_not_recoverable():
    from fetch import archive as ar
    p = config.PROJECT_BY_NAME["Uniswap"]
    assert ar.hole_route("Uniswap", "circulating_supply", "coingecko", set(), p) == "market_chart"
    for m in ("fdv_usd", "total_supply_coingecko", "max_supply", "total_supply"):
        # P5-1C.A3: Uniswap's CoinGecko total (net of the 0xdead balance) is now rebuilt from the chain; the rest stay
        assert ar.hole_route("Uniswap", m, "coingecko", set(), p) == (
            "supply_rebuild" if m == "total_supply" else "not_recoverable"), m
        assert "Enterprise-only" in ar.not_recoverable("coingecko", m)
    assert ar.not_recoverable("coingecko", "price_usd") == ""
    assert "circulating_supply" in ar.api_series(p)
    from fetch.base import _measuring_point
    assert _measuring_point(ar.CG_CIRCULATING_PAST_SOURCE) == "coingecko", "CoinGecko's own measuring point"


def test_a_missed_circulating_day_is_filled_from_market_cap_over_price(st, monkeypatch):
    from fetch import archive as ar
    from fetch.coingecko import CoinGecko
    n = "Uniswap"
    hole = [str((Y - pd.Timedelta(days=i)).date()) for i in (3, 4)]
    full = _days(20)
    held = [d for d in full if str(d.date()) not in hole]
    st.upsert(pd.concat([_rows(n, "circulating_supply", held, 6.0e8, "coingecko"),
                         _rows(n, "price_usd", full, 7.0, "coingecko"),
                         _rows(n, "market_cap_usd", full, 4.2e9, "coingecko"),
                         _rows(n, "volume_usd", full, 1e8, "coingecko"),
                         _rows(n, "circulating_supply_implied", full, 6.0e8, "coingecko:mcap/price")]))

    class Http:
        min_interval = 0.0

        def get(self, url, params=None, headers=None):
            pts = [TODAY - pd.Timedelta(days=i) for i in range(int(params["days"]), 0, -1)]
            ms = [int(d.tz_localize("UTC").timestamp() * 1000) for d in pts]
            return {"prices": [[t, 8.0] for t in ms], "market_caps": [[t, 8.0 * 6.1e8] for t in ms],
                    "total_volumes": [[t, 1e8] for t in ms]}
    cg = CoinGecko()
    cg.http = Http()
    res = ar.api_catch_up(st, [config.PROJECT_BY_NAME[n]], api=cg, max_days=10)
    assert res["filled"][(n, "circulating_supply")] == sorted(hole, reverse=True)
    for d in hole:
        v, src = _val(st, n, "circulating_supply", d)
        assert v == pytest.approx(6.1e8) and src == ar.CG_CIRCULATING_PAST_SOURCE
    assert _val(st, n, "circulating_supply", str(Y.date()))[0] == 6.0e8, "never an overwrite"


# ============================================================================================ contracts at a past block
class _W3:
    class eth:
        block_number = 1000


class _Blocks:
    def at(self, day, head):
        return int((pd.Timestamp(day) - pd.Timestamp("2026-01-01")).days)


def test_a_contract_point_read_is_read_again_at_the_owed_day_s_first_block(st):
    from fetch import archive as ar
    n = "Chainlink"
    hole = [str((Y - pd.Timedelta(days=i)).date()) for i in (1, 2)]
    full = _days(10)
    src = "rewardvault:getRewardBuckets"
    st.upsert(_rows(n, "reward_rate_ends_unix", [d for d in full if str(d.date()) not in hole], 1.79e9, src, 2))
    assert ar.hole_route(n, "reward_rate_ends_unix", src, set()) == "contract_archive"
    seen = []

    def rows_at(p, w3, block, day):
        seen.append((block, str(day.date())))
        return [("reward_rate_ends_unix", 1.79e9 + block, config.mark_source(src, "archive")),
                ("reward_emission_rate_annual", 1.0, config.mark_source(src, "archive"))]   # not owed: not written
    scan = ar.scan_holes(st, [config.PROJECT_BY_NAME[n]], set())
    res = ar.contract_catch_up(st, [config.PROJECT_BY_NAME[n]], scan, None,
                               readers={"rewardvault": (lambda p: "ethereum", rows_at)},
                               resolve=lambda chain: (_W3(), "archive.example"), day_blocks=lambda w3, c: _Blocks())
    assert sorted(d for _b, d in seen) == sorted(hole)
    for d in hole:
        v, s = _val(st, n, "reward_rate_ends_unix", d)
        assert v == 1.79e9 + _Blocks().at(pd.Timestamp(d), 0) and s == f"{src}:archive"
    assert _val(st, n, "reward_emission_rate_annual", hole[0]) is None, "only the owed keys are written"
    assert res["filled"][(n, "reward_rate_ends_unix")] == sorted(hole, reverse=True)
    assert "the rewardvault contract read again at each day's first block via archive.example" in res["lines"][0][1]


def test_no_archive_endpoint_leaves_the_days_owed_and_says_why(st):
    from fetch import archive as ar
    n = "Ether.fi"
    full = _days(10)
    st.upsert(_rows(n, "sethfi_accountant_rate", [d for d in full if d != full[-3]], 1.2,
                    "share_vault:accountant.getRate", 2))
    scan = ar.scan_holes(st, [config.PROJECT_BY_NAME[n]], set())
    assert scan[(n, "sethfi_accountant_rate")]["route"] == "contract_archive"
    res = ar.contract_catch_up(st, [config.PROJECT_BY_NAME[n]], scan, None,
                               readers={"share_vault": (lambda p: "ethereum", lambda *a: [])},
                               resolve=lambda chain: (None, None))
    assert res["left"][(n, "sethfi_accountant_rate")] == [str(full[-3].date())]
    assert "no endpoint serves ethereum state" in res["lines"][0][1] and "never 0" in res["lines"][0][1]


def test_the_vault_s_rows_at_a_block_are_the_live_reads(monkeypatch):
    from fetch import reward_vault as rv
    p = config.PROJECT_BY_NAME["Chainlink"]
    ts = 1_800_000_000
    buckets = {"operatorBase": (10 ** 15, ts + 100, 0), "communityBase": (2 * 10 ** 15, ts + 50, 0),
               "operatorDelegated": (0, ts - 1, 0)}
    monkeypatch.setattr(rv, "read_at", lambda w3, a, b: (buckets, ts))
    monkeypatch.setattr(rv, "decode_string", lambda h: "CommunityStakingPool 1.0.0")

    class Eth:
        def call(self, tx, block):
            class R:
                def hex(self_):
                    return hex(40_000_000 * 10 ** 18)
            return R()

    class W3:
        eth = Eth()

        @staticmethod
        def to_checksum_address(a):
            return a
    got = {m: (v, s) for m, v, s in rv.rows_at(p, W3(), 123, pd.Timestamp("2026-10-01"))}
    annual = 3 * 10 ** 15 * rv.SECONDS_PER_YEAR / rv.JUELS
    assert got["reward_emission_rate_annual"][0] == pytest.approx(annual)
    assert got["emissions_tokens"] == (pytest.approx(annual / 365.25), "rewardvault:getRewardBuckets:rate_x_time:archive")
    assert got["reward_rate_ends_unix"][0] == ts + 50, "the earliest active bucket's end, as run() reads it"
    assert got["staker_yield_community_pool"][0] == pytest.approx(
        rv.community_rate(buckets, 40_000_000 * 10 ** 18, ts))


# ============================================================================================ the tiers' read-back
def test_the_business_read_keeps_the_owed_days_beside_its_30_day_span(monkeypatch):
    from fetch import base, business
    monkeypatch.setattr(business, "today", lambda: TODAY)
    b = business.Business.__new__(business.Business)

    class D:
        def ever(self, k):
            return True
    b.daily = D()
    s = {d: 1.0 for d in pd.date_range(TODAY - pd.Timedelta(days=120), TODAY)}
    old = TODAY - pd.Timedelta(days=90)
    base.set_catch_up_owed({("Ether.fi", "stake_tvl_usd"): {str(old.date()), str(TODAY.date())}})
    try:
        kept = {d for d, _ in b._keep(s, "business:Ether.fi:stake_tvl_usd")}
    finally:
        base.set_catch_up_owed({})
    assert old in kept, "an owed day older than the 30-day span is read back"
    assert TODAY not in kept, "never past the last complete day"
    assert min(d for d in kept if d != old) == TODAY - pd.Timedelta(days=30)


def test_the_rwa_read_rebuilds_the_history_of_a_chain_with_owed_days(monkeypatch):
    from fetch import base, llama
    d = llama.DefiLlama.__new__(llama.DefiLlama)
    d._today = str(TODAY.date())
    d._rwa_by_chain = None

    class Daily:
        def due(self, k, day):
            return True

        def done(self, k, day):
            pass
    d.daily = Daily()
    d._stored = lambda n, m: True
    built = []

    def build(wanted):
        built.append(set(wanted))
        return pd.DataFrame({"chain": "Ethereum", "date": pd.date_range(TODAY - pd.Timedelta(days=60), Y),
                             "value": 1.0})
    d._build_rwa_by_chain = build

    class Http:
        def get(self, url):
            return [{"category": "RWA", "chains": ["Ethereum"], "chainTvls": {"Ethereum": 5.0}}]
    d.http = Http()

    class Out:
        rows = []

        def add(self, f, *a):
            if f is not None:
                self.rows.append(f)

        def fail(self, *a):
            raise AssertionError(a)
    p = dict(config.PROJECT_BY_NAME["Ethereum"])
    monkeypatch.setattr(llama, "window", lambda f, w: f)
    o = Out()
    d.rwa([p], 30, o)
    assert built == [], "no owed day: today's one-call sum only"
    base.set_catch_up_owed({("Ethereum", "rwa_defillama_usd"): {str((TODAY - pd.Timedelta(days=50)).date())}})
    try:
        d.rwa([p], 30, Out())
    finally:
        base.set_catch_up_owed({})
    assert built == [{p["defillama_chain"]}]


def test_coinbase_widens_its_read_to_the_owed_days_and_keeps_only_those_and_the_routine_week(monkeypatch):
    from fetch import base, xref
    monkeypatch.setattr(xref, "today", lambda: TODAY)
    c = xref.CrossRefs.__new__(xref.CrossRefs)
    asked = {}

    class Daily:
        def due(self, k, day):
            return True

        def ever(self, k):
            return True

        def get(self, k):
            return None

        def done(self, k, day):
            pass

        def set(self, k, v):
            pass
    c.daily = Daily()
    c.robots = lambda url: (True, "")

    class Http:
        def get(self, url, params=None):
            asked.update(params)
            s, e = pd.Timestamp(params["start"]), pd.Timestamp(params["end"])
            return [[int(d.tz_localize("UTC").timestamp()), 1, 2, 1.5, 1.6, 10] for d in pd.date_range(s, e)
                    if d < TODAY]
    c.http = Http()
    added = []

    class Out:
        def add(self, f, *a):
            added.append(f)

        def fail(self, *a):
            raise AssertionError(a)

        def skipped(self, *a):
            pass

        def mark_current(self, *a):
            pass
    p = config.PROJECT_BY_NAME["Uniswap"]
    spec = {"coinbase_product": {"Uniswap": "UNI"}}
    owed_day = TODAY - pd.Timedelta(days=40)
    base.set_catch_up_owed({("Uniswap", "price_usd_coinbase"): {str(owed_day.date())}})
    try:
        c._coinbase(p, spec, str(TODAY.date()), Out())
    finally:
        base.set_catch_up_owed({})
    days = set(pd.to_datetime(added[0]["date"]).dt.normalize())
    assert pd.Timestamp(asked["start"]).tz_localize(None) <= owed_day
    assert owed_day in days and TODAY - pd.Timedelta(days=20) not in days, "the owed day and the routine week only"
    assert TODAY - pd.Timedelta(days=3) in days


def test_defillama_coin_prices_widen_to_the_owed_days_and_keep_only_those_and_the_routine_week(monkeypatch):
    from fetch import base, xref
    monkeypatch.setattr(xref, "today", lambda: TODAY)
    c = xref.CrossRefs.__new__(xref.CrossRefs)

    class Daily:
        def due(self, k, day):
            return True

        def ever(self, k):
            return True

        def done(self, k, day):
            pass
    c.daily = Daily()
    c.robots = lambda url: (True, "")
    asked = {}

    class Http:
        def get(self, url, params=None):
            asked.update(params)
            keys = url.rsplit("/", 1)[1].split(",")
            pts = [{"timestamp": int(d.tz_localize("UTC").timestamp()), "price": 2.0}
                   for d in pd.date_range(pd.Timestamp(params["start"], unit="s"), Y)]
            return {"coins": {k: {"prices": pts} for k in keys}}
    c.http = Http()
    added = []

    class Out:
        def add(self, f, *a):
            added.append(f)

        def fail(self, *a):
            raise AssertionError(a)
    p = config.PROJECT_BY_NAME["Uniswap"]
    owed_day = TODAY - pd.Timedelta(days=60)
    base.set_catch_up_owed({("Uniswap", "price_usd_llama"): {str(owed_day.date())}})
    try:
        c._llama([p], {}, str(TODAY.date()), Out())
    finally:
        base.set_catch_up_owed({})
    assert asked["span"] >= 61
    days = set(pd.to_datetime(added[0]["date"]).dt.normalize())
    assert owed_day in days and TODAY - pd.Timedelta(days=30) not in days and Y in days


def test_the_p2p_leg_reads_the_owed_days_in_one_dated_query_per_run_of_days():
    from fetch.eth_settlement import EthP2PBigQuery
    now = pd.Timestamp("2026-11-04")
    got = EthP2PBigQuery.owed_pieces({"2026-11-01"}, {"2026-11-02", "2026-11-03", "2026-10-30", "2026-11-01",
                                                      "2026-11-04"}, now)
    assert got == [(pd.Timestamp("2026-10-30"), pd.Timestamp("2026-10-30")),
                   (pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-03"))], "held and today's days are not asked"


def test_the_p2p_turn_reads_owed_pieces_without_the_weekly_turn(monkeypatch, tmp_path):
    from fetch import eth_settlement as es
    (tmp_path / "x.sql").write_text("SELECT 1")
    monkeypatch.setattr(es, "SQL", tmp_path)
    q = es.EthP2PBigQuery.__new__(es.EthP2PBigQuery)
    q._dry = lambda *a, **k: 1e9
    ran = []

    def run(client, what, sql, params, bq, st, out, name, backfill):
        ran.append((what, params["d0"], params["d1"]))
        return [{"day": params["d0"], "token": "ETH", "amount": "1"}]
    q._run = run
    q._save = lambda st: None

    class Out:
        def skipped(self, *a):
            pass

        def fail(self, *a):
            raise AssertionError(a)
    p2p ={"sql": "x.sql", "tokens": [("WETH", "0xw", 18)], "validation_from": "2025-07-27", "approved": True,
           "validation_extend_from": "2025-07-27"}
    monkeypatch.setattr(es, "_spec", lambda: {"validation": {"to": "2025-08-25"}, "p2p": p2p})
    e = {"days": {}, "prices": {}, "validation_done": True}
    owed = [(pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-03"))]
    assert q._turn(None, {"monthly_budget_bytes": 1e12}, p2p, {}, e, Out(), "Ethereum", pd.Timestamp("2026-11-04"),
                   weekly=False, owed=owed)
    assert ran == [("p2p owed 2026-11-02..2026-11-03", pd.Timestamp("2026-11-02").date(),
                    pd.Timestamp("2026-11-03").date())]
    assert "through" not in e, "an owed piece is not the weekly cursor"


# ============================================================================================ named holes and the tally
def test_etherscan_supply_fields_are_named_not_recoverable():
    from fetch import archive as ar
    for src in ("etherscan:ethsupply2.supply", "etherscan:ethsupply2.BurntFees", "etherscan:ethsupply2.Eth2Staking"):
        assert ar.hole_route("Ethereum", "total_supply_protocol", src, set()) == "not_recoverable"
        assert "native ETH supply is not contract state" in ar.not_recoverable(src)


def test_the_new_sources_go_to_the_tiers_that_now_read_their_owed_days():
    from fetch import archive as ar
    for src in ("coinbase_exchange:UNI-USD:open", "defillama_coins:ethereum:0x1f98", "eth_settlement:bigquery:p2p"):
        assert ar.hole_route("Uniswap", "m", src, set()) == "tiers", src


def test_the_summary_counts_holes_per_source():
    from fetch import archive as ar
    line = ar.by_source_line({"coingecko": {"series": 2, "found": 20, "filled": 10, "left": 0, "not_recoverable": 10,
                                            "routes": {"market_chart", "not_recoverable"}},
                              "dune": {"series": 1, "found": 3, "filled": 0, "left": 3, "not_recoverable": 0,
                                       "routes": {"tiers"}}})
    assert line == ("CATCH-UP BY SOURCE (holes found / filled / left / not recoverable): coingecko 2 series: 20 found, "
                    "10 filled, 0 left, 10 not recoverable [market_chart, not_recoverable]; dune 1 series: 3 found, "
                    "0 filled, 3 left [tiers]")
