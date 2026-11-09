"""Project round 1B (Jake 2026-11-09), group `burn`: items 2 (burn day-stamping by block time), 3 (one pricing basis for
$ burn), 6 (no holder value credited before the fee switch's execution) and 7 (the fee switch's dates). No network."""
from __future__ import annotations

import pandas as pd
import pytest

import config
import references


def _frame(days, vals, source="derived:one_off"):
    return pd.DataFrame({"date": pd.to_datetime(days), "value": vals, "source": source})


# ================================================================================================== item 7
def test_7_series_launch_is_the_fee_switch_execution_on_chain():
    la = config.SERIES_LAUNCH["Uniswap"]
    assert la["date"] == "2025-12-27" and la["what"] == "fee switch executed"
    assert la["block"] == 24_106_378 and la["executed_utc"] == "2025-12-27 20:33:11"
    assert la["defillama_first_revenue"] == "2025-12-29"
    assert config.series_launch("Uniswap", "revenue_usd") == {"date": "2025-12-27", "what": "fee switch executed"}
    uni = config.PROJECT_BY_NAME["Uniswap"]
    assert uni["fee_switch_executions"]["#93 UNIfication (mainnet v2/v3, Firepit, 100M burn)"].startswith("2025-12-27")
    assert uni["fee_switch_dates"]["ethereum"] == "2025-12-27"


def test_7_the_launch_label_reads_fee_switch_executed():
    """The Trajectory's label is built from SERIES_LAUNCH: "NEW since Q-k (fee switch executed 2025-12-27)"."""
    import trajectory as tj
    la = config.series_launch("Uniswap", "gross_burn_tokens")
    k, part = tj.launch_quarter(la["date"], "2026-11-09", 90, 5)
    assert (k, part) == (3, True)                       # Q-3 = 2025-11-14 .. 2026-02-11 on Jake's as-of
    assert f"NEW since {tj.LABELS[k]} ({la['what']} {la['date']})" == "NEW since Q-3 (fee switch executed 2025-12-27)"
    k2, _ = tj.launch_quarter(la["date"], "2026-10-13", 90, 5)
    assert k2 == 3                                      # the dumps' as-of: the same quarter


def test_7_the_100m_reference_reads_the_event_whichever_day_it_is_stamped():
    """Jake's store: one_off_burn_tokens ends 2025-12-27 (the tx's block day) and sum_period refused "ends before the
    period's end 2025-12-28". Read-date stamping puts the differenced row on 12-28, block-time stamping (item 2) on
    12-27: both read 100,000,000."""
    route = references.ROUTES[("Uniswap", "g6_retro_burn_100m")]
    assert route[0] == "event_period"
    at = pd.Timestamp("2025-12-27")
    for days in (["2025-12-27"], ["2025-12-28"]):
        g = _frame(days, [100_000_000.0])
        v, where, why = references._our_value(route, {("Uniswap", "one_off_burn_tokens"): g}, "Uniswap", at,
                                              100_000_000.0)
        assert v == 100_000_000.0 and not why, (days, v, where, why)
        assert days[0] in where
    # the old route refused the block-day stamping (the failure Jake saw)
    old = ("sum_period", "one_off_burn_tokens", "2025-12-27", "2025-12-28")
    v, _w, why = references._our_value(old, {("Uniswap", "one_off_burn_tokens"): _frame(["2025-12-27"], [1e8])},
                                       "Uniswap", at, 1e8)
    assert v is None and "before the period's end" in why
    # nothing in the window is never a zero
    v, _w, why = references._our_value(route, {("Uniswap", "one_off_burn_tokens"): _frame(["2026-01-05"], [1e8])},
                                       "Uniswap", at, 1e8)
    assert v is None and "not stored" in why


# ================================================================================================== item 6
def _stored(days, vals, metric, source="test", project="Uniswap"):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric, "value": vals,
                         "source": source, "tier": 2})


def test_6_no_holder_value_is_credited_before_the_fee_switch_executed():
    """A 0.0014 UNI burn on 2025-10-22 / 11-03 (stray transfers to 0x…dEaD, two months before the fee switch) was
    credited as holder value with no revenue (Flows Q-4 CHECK). Before 2025-12-27 a burn is no holder value: 0 credited,
    and not recorded as treasury / emissions-funded either; from the execution day it is credited as before."""
    import build_workbook as bw
    assert config.holder_funding("Uniswap", "2025-12-26")[0] == "none"
    assert config.holder_funding("Uniswap", "2025-12-27")[0] == "revenue"
    assert config.holder_funding("Uniswap", "2026-08-01")[0] == "revenue"
    assert config.holder_funding("GEODNET", "2020-01-01")[0] == "revenue"            # no declaration: unchanged
    days = ["2025-10-22", "2025-11-03", "2026-01-05"]
    groups = {("Uniswap", "actual_buyback_usd"): _stored(days, [0.0089, 0.0007, 52_000.0], "actual_buyback_usd"),
              ("Uniswap", "actual_buyback_tokens"): _stored(days, [0.0013, 0.0001, 8_000.0], "actual_buyback_tokens")}
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    bw._ONE_OFF_IN_SERIES.clear()
    try:
        bw._holder_value_views(groups)
    finally:
        bw._SCOPE = keep
    cr = groups[("Uniswap", "holder_value_revenue_usd")].set_index("date")["value"]
    assert cr[pd.Timestamp("2025-10-22")] == 0.0 and cr[pd.Timestamp("2025-11-03")] == 0.0
    assert cr[pd.Timestamp("2026-01-05")] == 52_000.0
    assert ("Uniswap", "holder_value_other_usd") not in groups                    # not recorded as another funding


def test_6_flows_q4_reads_ok_on_a_live_shaped_store():
    """Jake's as-of 2026-11-09: Q-4 (to 2025-11-14) holds the two stray pre-switch burns and no revenue — its value-flow
    status read "CHECK — holder value credited with no revenue in the quarter"; now OK (0 credited)."""
    import build_workbook as bw
    asof = pd.Timestamp("2026-11-09")
    days = pd.date_range("2025-08-01", "2026-11-08")
    b = ["2025-10-22", "2025-11-03", "2026-01-05"]
    groups = {("Uniswap", "revenue_usd"): _stored(days, [0.0 if d < pd.Timestamp("2025-12-29") else 1000.0
                                                         for d in days], "revenue_usd"),
              ("Uniswap", "fees_usd"): _stored(days, [5000.0] * len(days), "fees_usd"),
              ("Uniswap", "actual_buyback_usd"): _stored(b, [0.0089, 0.0007, 900.0], "actual_buyback_usd"),
              ("Uniswap", "actual_buyback_tokens"): _stored(b, [0.0013, 0.0001, 100.0], "actual_buyback_tokens")}
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    bw._ONE_OFF_IN_SERIES.clear()
    try:
        bw._holder_value_views(groups)
        rows = bw.flow_statements(groups, asof)
    finally:
        bw._SCOPE = keep
    val = {r["Line"]: r for r in rows if r["Statement"] != "Token flow"}
    assert val["Revenue (every stream)"]["Q-4"] == 0.0 and val["Holder value CREDITED (revenue-funded)"]["Q-4"] == 0.0
    assert val["Status"]["Q-4"].startswith("OK"), val["Status"]["Q-4"]


def _uni_groups(days, burn, price, revenue=None):
    g = {("Uniswap", "gross_burn_tokens"): _stored(days, burn, "gross_burn_tokens",
                                                   source="chain:ethereum:burn_dead:archive:delta"),
         ("Uniswap", "price_usd"): _stored(days, price, "price_usd", source="coingecko")}
    if revenue is not None:
        g[("Uniswap", "revenue_usd")] = _stored(days, revenue, "revenue_usd", source="defillama")
    return g


def _views(groups, *fns):
    import build_workbook as bw
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    bw._ONE_OFF_IN_SERIES.clear()
    try:
        for f in fns:
            getattr(bw, f)(groups)
    finally:
        bw._SCOPE = keep
    return groups


# ================================================================================================== item 3
def test_3_burn_usd_is_each_days_burn_at_that_days_price_and_equals_holder_value():
    """Jake's store: A4 $36.90M (window tokens x the 90-day average price) vs holder revenue $35.17M (each day at its own
    price). One basis: Σ(day's burn x day's price) — gross_burn_day_priced_usd — equal to holder value on the same days."""
    days = pd.date_range("2026-08-12", "2026-08-21").strftime("%Y-%m-%d").tolist()
    burn = [12_000.0, 18_000.0, 0.0, 40_000.0, 4_000.0, 22_000.0, 8_000.0, 16_000.0, 30_000.0, 10_000.0]
    price = [6.0, 6.5, 7.0, 8.0, 7.5, 6.8, 6.2, 6.9, 7.3, 7.1]
    revenue = [80_000.0] * len(days)
    g = _views(_uni_groups(days, burn, price, revenue), "_relabel_views", "_holder_value_views", "_gross_burn_day_priced_usd_views")
    usd = g[("Uniswap", "gross_burn_day_priced_usd")].set_index("date")["value"]
    want = sum(b * p for b, p in zip(burn, price))
    assert abs(float(usd.sum()) - want) < 1e-6
    avg = sum(burn) * (sum(price) / len(price))
    assert abs(avg - want) > 1.0                                          # the old basis is a different figure
    hv = g[("Uniswap", "holder_value_revenue_usd")].set_index("date")["value"]
    assert abs(float(hv.sum()) - want) < 1e-6                             # one figure per concept
    base = g[("Uniswap", "fees_base_day_priced_tokens")].set_index("date")["value"]   # Uniswap's base: revenue_usd (burn_split)
    assert abs(float(base.sum()) - sum(r / p for r, p in zip(revenue, price))) < 1e-6
    assert str(g[("Uniswap", "gross_burn_day_priced_usd")]["source"].iloc[0]) == "derived:gross_burn_tokens*price_usd[that day's price]"


def test_3_a_day_without_a_price_is_left_out_never_valued_at_another_days():
    days = ["2026-08-12", "2026-08-13", "2026-08-14"]
    g = _uni_groups(days, [10.0, 20.0, 30.0], [5.0, 6.0, 7.0])
    g[("Uniswap", "price_usd")] = g[("Uniswap", "price_usd")].iloc[[0, 2]]
    g = _views(g, "_gross_burn_day_priced_usd_views")
    usd = g[("Uniswap", "gross_burn_day_priced_usd")].set_index("date")["value"]
    assert list(usd.index.strftime("%Y-%m-%d")) == ["2026-08-12", "2026-08-14"] and float(usd.sum()) == 260.0


def test_3_every_a4_dollar_burn_cell_reads_the_day_priced_series():
    """The A4 gross burn $, burn share of fees, implied burn and actual − implied read gross_burn_day_priced_usd / fees_base_day_priced_tokens;
    no burn is multiplied (or a fee base divided) by the 90-day average price any more."""
    import inspect
    import build_workbook as bw
    src = inspect.getsource(bw.write_a4)
    assert "(\"Gross burn Q0 ($, each day's burn × that day's price)\"" in src
    assert "R.D(r, \"gross_burn_day_priced_usd\", \"q0\")" in src and "_flow_rate(R, r, 'gross_burn_day_priced_usd', p=p)" in src
    assert src.count("fees_base_day_priced_tokens") >= 3
    assert "{burn(r, p)}*{price(r)}" not in src and "Share of fees burned')}/{price(r)}" not in src
    assert config.events_per_year("Sky", "gross_burn_day_priced_usd") == config.events_per_year("Sky", "sky_stage2_burn_tokens")
    import reader
    assert reader.short_header("Gross burn Q0 ($, each day's burn × that day's price)") == "Gross burn, 90 days ($)"


def test_3_issuance_and_net_dollars_are_day_priced_and_net_uses_the_burn_dollars_figure():
    """Lead 2026-11-09: 'Gross issuance Q* ($ ...)' (A1, A4) and A4's 'Net supply change ($ ...)' were tokens x the 90-day
    average price; now each day's issuance x that day's price, and net $ = day-priced issuance $ − the SAME day-priced burn
    $ as the gross burn $ column — one $ burn figure."""
    import inspect
    import build_workbook as bw
    import reader
    col = bw._issuance_usd_col(type("R", (), {"D": staticmethod(lambda r, m, w: f"D[{m}|{w}]")})())
    assert col[0] == "Gross issuance Q0 ($, each day's issuance × that day's price)"
    assert "gross_issuance_day_priced_usd" in col[1](5, {"name": "Sky"})
    a4, a1 = inspect.getsource(bw.write_a4), inspect.getsource(bw.write_a1)
    assert "_issuance_usd_col(R)" in a4 and "_issuance_usd_col(R)" in a1
    assert "{iss(r)}*{price(r)}" not in a4 + a1 and "_price_net(" not in a4
    assert '"Net supply change ($, each day\'s issuance − burn × that day\'s price)"' in a4
    assert reader.short_header("Gross issuance Q0 ($, each day's issuance × that day's price)") == "Gross issuance, 90 days ($)"
    assert reader.short_header("Net supply change ($, each day's issuance − burn × that day's price)") == "Issuance − burn ($)"
    # the net's legs: issuance basis and every burn, each day-priced; the burn leg IS the gross burn $ series
    assert bw.day_priced_metric("Uniswap", config.a1_burn_metric("Uniswap")) == "gross_burn_day_priced_usd"
    assert bw.day_priced_metric("Uniswap", config.issuance_basis("Uniswap")) == "gross_issuance_day_priced_usd"
    assert bw.day_priced_metric("GEODNET", config.issuance_basis("GEODNET")) == "pool_release_day_priced_usd"
    assert bw.day_priced_metric("Hyperliquid", config.a1_burn_metric("Hyperliquid")) == "total_burn_day_priced_usd"
    for m in ("gross_issuance_day_priced_usd", "pool_release_day_priced_usd", "total_burn_day_priced_usd",
              "net_mint_monthly_day_priced_usd"):
        assert config.METRICS[m]["view_only"]


class _R:
    @staticmethod
    def D(r, m, w):
        return f"D[{m}]"

    @staticmethod
    def C(r, h):
        return f"C[{h}]"


def test_3_the_net_dollar_formula_differences_the_day_priced_legs(monkeypatch):
    import build_workbook as bw
    seen = {}
    monkeypatch.setattr(bw, "_coverage_guard", lambda R, r, legs, expr, **kw: seen.setdefault("x", (legs, expr)) and
                        "=" + expr)
    f = bw._net_usd_day_priced(_R, 7, config.PROJECT_BY_NAME["Uniswap"])
    assert f == "=D[gross_issuance_day_priced_usd]-D[gross_burn_day_priced_usd]", f
    seen.clear()
    g = bw._net_usd_day_priced(_R, 7, config.PROJECT_BY_NAME["GEODNET"])          # pre-minted: − burn $ only
    assert g == "=-D[gross_burn_day_priced_usd]", g


def test_3_a_day_without_a_price_is_left_out_and_named_in_the_cell():
    import build_workbook as bw
    days = ["2026-08-12", "2026-08-13", "2026-08-14"]
    g = _uni_groups(days, [10.0, 20.0, 30.0], [5.0, 6.0, 7.0])
    g[("Uniswap", "gross_issuance_tokens")] = _stored(days, [1.0, 2.0, 3.0], "gross_issuance_tokens")
    g[("Uniswap", "price_usd")] = g[("Uniswap", "price_usd")].iloc[[0, 2]]
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    try:
        bw._gross_burn_day_priced_usd_views(g, pd.Timestamp("2026-08-20"))
    finally:
        bw._SCOPE = keep
    iss = g[("Uniswap", "gross_issuance_day_priced_usd")].set_index("date")["value"]
    assert float(iss.sum()) == 1.0 * 5 + 3.0 * 7
    note = bw.day_price_note("Uniswap", "gross_issuance_day_priced_usd")
    assert note.startswith("LEFT OUT (PR1B.3): 1 day(s)") and "2026-08-13" in note and "never valued" in note
    assert "2026-08-13" in bw.day_price_note("Uniswap", "gross_issuance_day_priced_usd", "gross_burn_day_priced_usd")


def test_3_a_monthly_row_takes_its_own_months_prices():
    import build_workbook as bw
    px = pd.Series([2.0, 4.0], index=pd.to_datetime(["2026-07-10", "2026-07-20"]))
    g = _stored(["2026-07-01", "2026-08-01"], [100.0, 50.0], "gross_burn_tokens", source="dune:8683175")
    vals, srcs, left = bw._day_priced(g, px)
    assert float(vals.iloc[0]) == 300.0 and "2 of 31 day(s) of its month" in srcs[0]
    assert left == [pd.Timestamp("2026-08-01")]                                      # no price in August: named


# ================================================================================================== item 2
# A LIVE-SHAPED FIXTURE (Jake's store, 2026-11-09): hourly blocks; 0x…dEaD holds X + the burns landed by each block.
# Burns: 10,000 at 12:00 every day to 11-06; 12,000 at 11-07 15:00; 18,000 at 11-08 10:00; 18,000 at 11-09 05:00.
# The live read runs at 07:50 (the latest block is 07:00's). Read-date stamping stored 11-08 = 12,000 (the 11-07 burn)
# and 11-09 = 36,000 (11-08's 18,000 + 11-09's first 18,000).
UNI, BAL, BURN, TSG = "Uniswap", "burn_address_balance", "gross_burn_tokens", "total_supply_gross"
TODAY2 = pd.Timestamp("2026-11-09")
T0 = int((TODAY2 - pd.Timedelta(days=40)).tz_localize("UTC").timestamp())
X = 113_000_000.0
BURNS = [(pd.Timestamp(d) + pd.Timedelta(hours=12), 10_000.0) for d in pd.date_range("2026-10-15", "2026-11-06")] + [
    (pd.Timestamp("2026-11-07 15:00"), 12_000.0), (pd.Timestamp("2026-11-08 10:00"), 18_000.0),
    (pd.Timestamp("2026-11-09 05:00"), 18_000.0)]
LIVE_BLOCK = int((TODAY2 + pd.Timedelta(hours=7)).tz_localize("UTC").timestamp() - T0) // 3600


def _blk_time(n) -> pd.Timestamp:
    return pd.Timestamp(T0 + int(n) * 3600, unit="s")


def _first_block(day) -> int:
    return int((pd.Timestamp(day).tz_localize("UTC").timestamp() - T0) // 3600)


def _dead(t: pd.Timestamp) -> float:
    return X + sum(v for when, v in BURNS if when <= t)


class _Eth:
    block_number = LIVE_BLOCK

    def get_block(self, n):
        return {"timestamp": T0 + int(n) * 3600}

    def get_balance(self, a, block_identifier=None):
        return 0

    def get_code(self, a, block_identifier=None):
        return b"\x60"


class _Reader:
    """The fake Ethereum: UNI totalSupply 1bn; 0x…dEaD's balance at a block is X + every burn landed by it."""

    def __init__(self):
        self.at_block, self.reads = {}, []

    def web3(self, chain):
        return type("W", (), {"eth": _Eth()})()

    def checksum(self, a):
        return a

    def symbol_matches(self, chain, address, expected):
        return True, expected

    def has_code(self, chain, a):
        return True

    def scaled(self, chain, address, call, *args, **kw):
        blk = self.at_block.get(chain, LIVE_BLOCK)
        self.reads.append((str(_blk_time(blk)), call))
        if not args:
            return 1e9
        return _dead(_blk_time(blk))


def _rows2(metric, days, value, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": UNI, "metric": metric,
                         "value": [float(value(pd.Timestamp(d))) for d in days], "tier": 2, "source": source})


@pytest.fixture
def jake(tmp_path, monkeypatch):
    """Jake's store on 2026-11-09 07:50, read-date stamped: mid-day reads 10-20..11-06 (12:30), first-block reads 11-07 and
    11-08 (the catch-up's), the live read 11-09 07:50; the deltas between consecutive readings dated at the later."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY2)
    p = dict(config.PROJECT_BY_NAME[UNI])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k in ("token", "burn_dead")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, UNI, p)
    mid = list(pd.date_range("2026-10-20", "2026-11-06"))
    reads = ([(d, _dead(d + pd.Timedelta(hours=12, minutes=30)), "chain:ethereum:burn_dead") for d in mid]
             + [(pd.Timestamp(d), _dead(pd.Timestamp(d)), "chain:ethereum:burn_dead:archive")
                for d in ("2026-11-07", "2026-11-08")]
             + [(TODAY2, _dead(TODAY2 + pd.Timedelta(hours=7, minutes=50)), "chain:ethereum:burn_dead")])
    stock = pd.DataFrame({"date": [d for d, _, _ in reads], "project": UNI, "metric": BAL,
                          "value": [v for _, v, _ in reads], "tier": 2, "source": [s for _, _, s in reads]})
    flows = pd.DataFrame({"date": [d for d, _, _ in reads[1:]], "project": UNI, "metric": BURN,
                          "value": [b[1] - a[1] for a, b in zip(reads, reads[1:])], "tier": 2,
                          "source": [s + ":delta" for _, _, s in reads[1:]]})
    st = sm.Store(tmp_path / "metrics.db")
    st.upsert(pd.concat([stock, flows]))
    return st, p


def _held2(st, metric):
    return dict(st.conn.execute("SELECT substr(date,1,10), value FROM metrics WHERE project=? AND metric=? ORDER BY date",
                                (UNI, metric)).fetchall())


def test_2_the_fixture_reproduces_jakes_read_date_rows(jake):
    st, _p = jake
    held = _held2(st, BURN)
    assert held["2026-11-08"] == 12_000.0 and held["2026-11-09"] == 36_000.0          # Jake's two stored days


def test_2_the_route_lists_every_series_and_the_flow_is_a_new_declared_point():
    assert config.day_boundary_stock("Uniswap", BAL) and config.day_boundary_stock("GEODNET", BAL)
    assert config.day_boundary_stock("PancakeSwap", BAL) and config.day_boundary_stock("Venice AI", BAL)
    assert not config.day_boundary_stock("Sky", BAL) and not config.day_boundary_stock("Hyperliquid", BAL)
    assert config.day_boundary_flow_metrics("Venice AI") == ("gross_burn_tokens", "burn_revenue_funded")
    from fetch.base import _measuring_point
    assert _measuring_point("chain:ethereum:burn_dead:archive:delta:utc-day[span=2d]") == "chain:ethereum:burn_dead:utc-day"
    assert _measuring_point("chain:ethereum:burn_dead:archive:delta") == "chain:ethereum:burn_dead"
    new_src = "chain:ethereum:burn_dead:archive:delta:utc-day[span=2d]"
    assert config.withdrawn_contract_keys("Uniswap", BURN, new_src) == []           # never read as a contract key
    assert config.orphaned_contract_keys("Uniswap", new_src) == []
    assert config.withdrawn_contract_keys("GEODNET", BURN, config.GEODNET_BURN_SUM_POINT + ":archive:delta:utc-day") == []
    h = config.declared_handover("Uniswap", BURN)
    assert h["ordered_points"] == ("chain:ethereum:burn_dead", "chain:ethereum:burn_dead:utc-day")
    g = config.declared_handover("GEODNET", BURN)["ordered_points"]
    assert g[-1] == config.GEODNET_BURN_SUM_POINT + ":utc-day" and g[:3] == (
        "dune:8683175", "chain:polygon:burn_polygon", config.GEODNET_BURN_SUM_POINT)
    # GEODNET's full-composition leg is the sum, whatever its day boundary (its monthly-window rule is unchanged)
    assert config.handover_monthly_leg("GEODNET", BURN) == ("dune:8683175", config.GEODNET_BURN_SUM_POINT)
    assert config.declared_handover("Uniswap", "actual_buyback_tokens")["ordered_points"][-1].endswith(":utc-day")


def test_2_derive_day_flow_dates_the_burn_by_block_time_and_refuses_as_before():
    from fetch.base import derive_day_flow
    s = "chain:ethereum:burn_dead:archive"
    f = derive_day_flow(100.0, "2026-11-07", s, 112.0, "2026-11-08", s, UNI, BURN)
    assert str(f["date"].iloc[0])[:10] == "2026-11-07" and f["value"].iloc[0] == 12.0
    assert f["source"].iloc[0] == "chain:ethereum:burn_dead:archive:delta:utc-day"
    f = derive_day_flow(100.0, "2026-11-05", s, 130.0, "2026-11-08", s, UNI, BURN)
    assert str(f["date"].iloc[0])[:10] == "2026-11-07" and f["source"].iloc[0].endswith(":utc-day[span=3d]")
    assert derive_day_flow(100.0, "2026-11-07", s, 90.0, "2026-11-08", s, UNI, BURN).empty          # a fall
    assert derive_day_flow(100.0, "2026-11-07", "chain:ethereum:fire_pit", 112.0, "2026-11-08", s, UNI, BURN).empty
    assert derive_day_flow(100.0, "2026-11-08", s, 112.0, "2026-11-08", s, UNI, BURN).empty         # no interval


def test_2_the_restatement_puts_jakes_store_on_block_time(jake):
    """rederive.py --day-boundary: every mid-day reading is read again at its day's first block; the flow is re-derived
    from first block to first block, dated the day the burns happened — 11-07 = 12,000, 11-08 = 18,000, and the as-of day
    11-09 neither stored nor counted."""
    st, _p = jake
    from fetch import archive as ar
    r = _Reader()
    plan = ar.day_boundary_restatement(st, UNI, BAL, reader=r, solana={}, archive_chains={"ethereum"})
    assert not plan["blocked"] and not plan["unread"]
    assert set(plan["restated"]) == {str(d.date()) for d in pd.date_range("2026-10-20", "2026-11-06")} | {"2026-11-09"}
    assert plan["restated"]["2026-11-09"][2] == _dead(TODAY2) == X + sum(v for w, v in BURNS if w < TODAY2)
    assert {s for *_x, s in plan["restated"].values()} == {"chain:ethereum:burn_dead:archive"}
    assert len(plan["old"]) == 20 and not st.conn.execute(               # the plan only reads the store
        "SELECT 1 FROM metrics WHERE source LIKE '%utc-day%'").fetchall()
    n, deleted, written = ar.apply_day_boundary_restatement(st, UNI, BAL, plan)
    assert (n, deleted) == (19, 20)
    held = _held2(st, BURN)
    assert held["2026-11-07"] == 12_000.0 and held["2026-11-08"] == 18_000.0 and "2026-11-09" not in held
    assert all(held[str(d.date())] == 10_000.0 for d in pd.date_range("2026-10-20", "2026-11-06"))
    assert {s for (s,) in st.conn.execute("SELECT DISTINCT source FROM metrics WHERE metric=?", (BURN,))} == {
        "chain:ethereum:burn_dead:archive:delta:utc-day"}
    # every stock reading is now the first block of its day, and the flows telescope onto them exactly
    import build_workbook as bw
    long = pd.read_sql("SELECT date, project, metric, value, source FROM metrics", st.conn)
    long["date"] = pd.to_datetime(long["date"])
    g = {m: x.sort_values("date") for m, x in long.groupby("metric")}
    t = bw.telescoping(UNI, BURN, BAL, g[BURN], g[BAL])
    assert t["flow_recon_blocked"] is None and abs(t["flow_residual"]) < 1e-6
    assert t["flow_span"] == ("2026-10-20", "2026-11-09")
    # a second plan finds nothing to restate and nothing new
    plan2 = ar.day_boundary_restatement(st, UNI, BAL, reader=_Reader(), solana={}, archive_chains={"ethereum"})
    assert not plan2["restated"] and not plan2["old"] and plan2["new"].empty


def test_2_rederive_previews_the_plan_and_refuses_a_read_date_rebuild(jake, capsys):
    st, _p = jake
    import sqlite3
    import rederive
    from fetch import archive as ar
    plan = ar.day_boundary_restatement(st, UNI, BAL, reader=_Reader(), solana={}, archive_chains={"ethereum"})
    rederive.print_day_boundary_plan(UNI, BAL, plan, ("2026-10-21", "2026-11-08"))
    out = capsys.readouterr().out
    assert "19 mid-day reading(s) restated" in out and "2026-11-09" in out
    # read-date rows dated 10-21..11-08: 17 x 10,000 + 0 (11-07: first block − mid-day 11-06 12:30) + 12,000 (the 11-07
    # burn, on 11-08); utc-day: 17 x 10,000 + 12,000 (11-07) + 18,000 (11-08, which read-date put on 11-09)
    assert "window 2026-10-21..2026-11-08: read-date 182,000.0000 (19 rows) -> utc-day 200,000.0000 (19 rows)" in out, out
    conn = sqlite3.connect(st.path)
    p2 = rederive.rederive_flow_from_stock(conn, UNI, BURN)
    assert p2.blocked and "--day-boundary" in p2.blocked
    conn.close()


def test_2_the_revenue_window_sum_is_the_first_block_difference():
    """Jake's expected Uniswap burn on the revenue window 2026-08-12..11-08 (5,430,001 UNI) is the 0x…dEaD balance at the
    first block of 11-09 minus that at the first block of 08-12: read 2026-11-09 from eth.drpc.org (archive eth_call,
    UNI.balanceOf(0x…dEaD)); first blocks from eth.blockscout.com getblocknobytime, each checked (block − 1 < 00:00 UTC)."""
    reads = config.DAY_BOUNDARY_EVIDENCE["Uniswap"]["first_block_reads"]
    assert reads["2026-11-09"]["block"] == 26_373_603 and reads["2026-08-12"]["block"] == 25_735_201
    burned = reads["2026-11-09"]["balance"] - reads["2026-08-12"]["balance"]
    assert round(burned) == 5_430_001
    assert reads["2026-11-08"]["balance"] - reads["2026-11-07"]["balance"] == 12_000.0            # the 11-07 burn
    assert reads["2026-11-09"]["balance"] - reads["2026-11-08"]["balance"] == 18_000.0            # the 11-08 burn


def test_2_the_catch_up_reads_todays_first_block_and_stores_yesterday_never_today(tmp_path, monkeypatch):
    """Steady state after PR1B.2: first-block readings to 11-08 and their flows to 11-07; on 11-09 the catch-up reads
    today's first block (the only series that reads today) and differences 11-08 = 18,000; 11-09 is not stored."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY2)
    p = dict(config.PROJECT_BY_NAME[UNI])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k in ("token", "burn_dead")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, UNI, p)
    days = list(pd.date_range("2026-10-30", "2026-11-08"))
    st = sm.Store(tmp_path / "metrics.db")
    st.upsert(pd.concat([
        _rows2(BAL, days, _dead, "chain:ethereum:burn_dead:archive"),
        _rows2(BURN, days[:-1], lambda d: _dead(d + pd.Timedelta(days=1)) - _dead(d),
               "chain:ethereum:burn_dead:archive:delta:utc-day"),
        _rows2(TSG, days + [TODAY2], lambda d: 1e9, "chain:ethereum:token")]))
    gaps, _x, _c = ar.catch_up_gaps(st, [p])
    assert gaps == {(UNI, BAL): ["2026-11-09"]}                 # today, for the day-boundary stock only
    r = _Reader()
    ar.catch_up(st, [p], reader=r, archive_chains={"ethereum"}, budget_s=120, max_days=7, valuers=[])
    assert {(d[:10], c) for d, c in r.reads} == {("2026-11-09", "balanceOf")}
    assert _held2(st, BAL)["2026-11-09"] == _dead(TODAY2)
    held = _held2(st, BURN)
    assert held["2026-11-08"] == 18_000.0 and held["2026-11-07"] == 12_000.0 and "2026-11-09" not in held


def test_2_the_live_tier_stores_neither_the_mid_day_reading_nor_a_delta(monkeypatch):
    from fetch.base import FetchOutput
    from fetch.chain import Chain
    p = dict(config.PROJECT_BY_NAME[UNI])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k in ("token", "burn_dead")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, UNI, p)
    ch = Chain(prior_values={(UNI, BAL): X}, prior_dates={(UNI, BAL): "2026-11-08"},
               prior_sources={(UNI, BAL): "chain:ethereum:burn_dead:archive"}, prior_delta={(UNI, BAL): X})
    ch.reader = _Reader()
    out = FetchOutput()
    ch.run([p], None, out)
    f = out.frame()
    assert BAL not in set(f["metric"]) and BURN not in set(f["metric"])
    assert any("NOT STORED (PR1B.2)" in str(getattr(e, "message", "")) for e in out.log)


def test_2_the_handover_is_accepted_in_order_and_refused_when_the_legs_overlap():
    import build_workbook as bw
    old, new = "chain:ethereum:burn_dead", "chain:ethereum:burn_dead:utc-day"
    spans = {old: (pd.Timestamp("2026-08-01"), pd.Timestamp("2026-11-09")),
             new: (pd.Timestamp("2026-11-10"), pd.Timestamp("2026-11-20"))}
    assert bw.handover_refusal(UNI, BURN, (old, new), spans) is None
    spans[new] = (pd.Timestamp("2026-11-08"), pd.Timestamp("2026-11-20"))
    assert "OVERLAP" in bw.handover_refusal(UNI, BURN, (old, new), spans)


def test_6_the_metrics_doc_names_the_credit_start():
    txt = open(config.__file__.replace("config.py", "METRICS.md"), encoding="utf-8").read()
    assert "Funding: none before 2025-12-27 (0 credited:" in txt
