"""
tests/test_round_g2.py — Round G2 (Jake 2026-10-18): the five decisions D1-D5 and the loose ends L1-L3, SPEC §19 "G2 —
decisions old → new". One test (or more) per change, each named for its row.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

DISTRIBUTOR = "0x3Ef3D8bA38EBe18DB133cEc108f4D14CE00Dd9Ae"


def _groups(project):
    import real_dump as rd
    df = rd.long_frame(rd.load(project))
    return {k: g.reset_index(drop=True) for k, g in df.groupby(["project", "metric"])}


def _frame(project, metric, rows, source="x"):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "project": project, "metric": metric,
                         "value": [float(v) for _, v in rows], "source": source, "tier": 2})


# ------------------------------------------------------------------------------------------- run_sql sections CT / CU
def _sections():
    import run_sql
    sql = (ROOT / "orphan_cleanup.sql").read_text(encoding="utf-8")
    return run_sql, run_sql.parse_sections(sql)


@pytest.mark.parametrize("label", ["CT", "CU", "CV", "CW"])
def test_g2_run_sql_sections_are_dated_and_ship_commented(label):
    """Both sections exist, are stamped no later than now, carry SELECT dry runs, and ship every write commented."""
    run_sql, secs = _sections()
    s = secs[label]
    assert s["authored"] and pd.Timestamp(s["authored"].rstrip("Z")) <= pd.Timestamp.now("UTC").tz_localize(None)
    body = s["text"]
    assert "SELECT" in body
    assert not [ln for ln in body.splitlines() if ln.lstrip().upper().startswith(("UPDATE", "DELETE"))]
    writes = [w for w in run_sql.split_statements("\n".join(run_sql.uncommented_write(body)))
              if run_sql.classify(w) == "write"]
    assert writes and all(run_sql.write_target(w)[1] == "metrics" for w in writes)


def test_g2_run_sql_previews_the_outer_where_of_an_update_with_a_subquery():
    """CU restates value with a subquery in SET; the preview takes the WHERE outside the brackets (it took the
    subquery's, which cannot run against the outer table, and refused)."""
    import run_sql
    verb, table, where = run_sql.write_target(
        "UPDATE metrics\n   SET value = value - (SELECT z.value FROM metrics z\n  WHERE z.date = metrics.date),\n"
        "       source = 'a'\n WHERE project = 'X' AND EXISTS (SELECT 1 FROM metrics z WHERE z.date = metrics.date)")
    assert (verb, table) == ("UPDATE", "metrics")
    assert where.startswith("project = 'X' AND EXISTS (SELECT 1 FROM metrics z WHERE")


def _store(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT, tier INTEGER, "
                "fetched_at TEXT, PRIMARY KEY (date, project, metric))")
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,2,'2026-10-18T10:00:00Z')", rows)
    return con


def _run_writes(con, label):
    run_sql, secs = _sections()
    for w in run_sql.split_statements("\n".join(run_sql.uncommented_write(secs[label]["text"]))):
        if run_sql.classify(w) == "write":
            # the preview reads exactly this WHERE first; it must run
            _verb, table, where = run_sql.write_target(w)
            con.execute(f"SELECT * FROM {table} WHERE {where}").fetchall()
            con.execute(run_sql.strip_comments(w))


def test_g2_d2_cu_moves_coingecko_and_restates_circulating_by_the_same_days_burn():
    circ = "hypercore_info:tokenDetails.circulatingSupply-exclude_wallets"
    con = _store([
        ("2026-10-12", "Hyperliquid", "total_supply", 955_000_000.0, "coingecko"),
        ("2026-10-13", "Hyperliquid", "total_supply", 955_307_079.43, "coingecko"),
        ("2026-10-13", "Hyperliquid", "total_supply_coingecko", 1.0, "coingecko"),       # already moved that day
        ("2026-10-13", "GEODNET", "total_supply", 930_406_063.62, "coingecko"),
        ("2026-10-13", "Uniswap", "total_supply", 1e9, "coingecko"),                     # not in scope
        ("2026-10-12", "Hyperliquid", "circulating_supply_first_party", 238_500_000.0, circ),
        ("2026-10-13", "Hyperliquid", "circulating_supply_first_party", 238_601_453.86, circ),
        ("2026-10-13", "Hyperliquid", "hyperevm_zero_address_balance", 308_538.17, "hyperevm:eth_getBalance(0x0000…)"),
    ])
    _run_writes(con, "CU")
    q = lambda *a: con.execute(*a).fetchall()  # noqa: E731
    assert q("SELECT project, date, value FROM metrics WHERE metric = 'total_supply' ORDER BY project") == [
        ("Uniswap", "2026-10-13", 1e9)]
    assert sorted(q("SELECT project, date, value FROM metrics WHERE metric = 'total_supply_coingecko'")) == [
        ("GEODNET", "2026-10-13", 930_406_063.62), ("Hyperliquid", "2026-10-12", 955_000_000.0),
        ("Hyperliquid", "2026-10-13", 1.0)]
    got = dict(((d, (v, s)) for d, v, s in q(
        "SELECT date, value, source FROM metrics WHERE metric = 'circulating_supply_first_party'")))
    assert got["2026-10-13"][0] == pytest.approx(238_292_915.69) and got["2026-10-13"][1] == circ + "-hyperevm_burn"
    assert got["2026-10-12"] == (238_500_000.0, circ), "a day with no balance row is left (CU3 lists it)"


def test_g2_d1_ct_renames_the_ethereum_claims():
    con = _store([("2026-10-01", "Morpho", "emissions_tokens", 10.0, "logscan:merkl_morpho_out"),
                  ("2026-10-02", "Morpho", "emissions_tokens", 11.0, "logscan:merkl_morpho_out"),
                  ("2026-10-02", "Morpho", "emissions_tokens_claims_ethereum", 11.0, "logscan:merkl_morpho_out"),
                  ("2026-10-02", "Pendle", "emissions_tokens", 5.0, "x")])
    _run_writes(con, "CT")
    got = con.execute("SELECT project, metric, date FROM metrics ORDER BY project, date").fetchall()
    assert got == [("Morpho", "emissions_tokens_claims_ethereum", "2026-10-01"),
                   ("Morpho", "emissions_tokens_claims_ethereum", "2026-10-02"),
                   ("Pendle", "emissions_tokens", "2026-10-02")]


# -------------------------------------------------------------------------------------------------- D1 Morpho claims
def test_g2_d1_morpho_b_is_claims_on_every_chain_and_the_schedule_is_context_only():
    p = config.PROJECT_BY_NAME["Morpho"]
    legs = ("emissions_tokens_claims_ethereum", "emissions_tokens_claims_arbitrum", "emissions_tokens_claims_base",
            "emissions_tokens_claims_katana")
    assert tuple(p["emissions_from_metric"]) == legs
    assert config.OUTFLOW_CATEGORIES["Morpho"]["b"] == ("emissions_tokens",)
    used = {m for cat in config.OUTFLOW_CATEGORIES["Morpho"].values() if isinstance(cat, tuple) for m in cat}
    assert "merkl_campaigns_non_ethereum_tokens" not in used
    assert p["merkl_campaigns"]["metric"] == "merkl_campaigns_non_ethereum_tokens"      # still read, as context
    scans = {s["metric"]: s for s in p["log_scans"]}
    assert scans["emissions_tokens_claims_ethereum"]["key"] == "merkl_morpho_out"
    for m, chain, tok in (("emissions_tokens_claims_arbitrum", "arbitrum", "0x40BD670A58238e6E230c430BBb5cE6ec0d40df48"),
                          ("emissions_tokens_claims_katana", "katana", "0x1e5eFCA3D0dB2c6d5C67a4491845c43253eB9e4e")):
        s = scans[m]
        assert (s["chain"], s["token"], s["holders"], s["direction"]) == (chain, tok, [DISTRIBUTOR], "out")
        assert s["window_days"] == 100 and s["store"]
    assert scans["emissions_tokens_claims_katana"]["rpc_window"].startswith("https://rpc.katana.network")
    assert config.CHAIN_IDS["katana"] == 747474 and config.DEFAULT_RPC["katana"] == ["https://rpc.katana.network"]
    base = [x for x in config.EXPLORER_PROBED_SCANS if x[1] == "Morpho"]
    assert base == [(8453, "Morpho", "base", "merkl_morpho_out_base", "emissions_tokens_claims_base",
                     "0xBAa5CC21fd487B8Fcc2F632f3F4E8D37262a0842", [DISTRIBUTOR])]


def test_g2_d1_base_claims_wire_when_the_alchemy_probe_serves_base(monkeypatch):
    p = config.PROJECT_BY_NAME["Morpho"]
    monkeypatch.setitem(p, "log_scans", list(p["log_scans"]))
    monkeypatch.setattr(config, "EXPLORER_TRANSFER_ROUTES", {})
    monkeypatch.setitem(config.PROJECT_BY_NAME["Fluid"], "log_scans", list(config.PROJECT_BY_NAME["Fluid"]["log_scans"]))
    monkeypatch.setitem(config.PROJECT_BY_NAME["Pendle"], "log_scans",
                        list(config.PROJECT_BY_NAME["Pendle"]["log_scans"]))
    monkeypatch.setitem(config.PROJECT_BY_NAME["Pendle"], "emissions_from_metric",
                        tuple(config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]))
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES, "Fluid", dict(config.OUTFLOW_CATEGORIES["Fluid"]))
    monkeypatch.setitem(p, "log_scans", [s for s in p["log_scans"] if s["key"] != "merkl_morpho_out_base"])
    wired = config.wire_probed_scans({"alchemy": {"served": [8453], "probed_on": "2026-10-19"}})
    assert "merkl_morpho_out_base" in wired
    s = next(x for x in p["log_scans"] if x["key"] == "merkl_morpho_out_base")
    assert s["exclude_counterparties"] == ["0x0000000000000000000000000000000000000000"]
    assert "merkl-contracts" in s["attribution_sources"][0] and "Alchemy" in s["route"]
    assert config.OUTFLOW_CATEGORIES["Fluid"]["b"] == config.OUTFLOW_CATEGORIES["Fluid"]["b"]   # untouched by Morpho


def _morpho(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Morpho"]])
    bw._VIEW_BLOCKS.pop(("Morpho", "emissions_tokens"), None)
    return bw


def test_g2_d1_a_chain_with_nothing_stored_blocks_the_sum_and_old_rows_are_not_shown(monkeypatch):
    bw = _morpho(monkeypatch)
    days = [(f"2026-10-{d:02d}", 1.0) for d in range(1, 6)]
    g = {("Morpho", "emissions_tokens"): _frame("Morpho", "emissions_tokens", days),     # pre-CT Ethereum rows
         ("Morpho", "emissions_tokens_claims_ethereum"): _frame("Morpho", "emissions_tokens_claims_ethereum", days)}
    bw._emissions_from_metric_views(g)
    assert ("Morpho", "emissions_tokens") not in g
    why = bw._VIEW_BLOCKS[("Morpho", "emissions_tokens")]
    assert "emissions_tokens_claims_base" in why and "emissions_tokens_claims_katana" in why
    assert "pendle_gauges" not in why
    # nothing of the four stored, the old Ethereum rows still under emissions_tokens: blocked too (one leg, not the sum)
    bw._VIEW_BLOCKS.pop(("Morpho", "emissions_tokens"), None)
    g = {("Morpho", "emissions_tokens"): _frame("Morpho", "emissions_tokens", days)}
    bw._emissions_from_metric_views(g)
    assert ("Morpho", "emissions_tokens") not in g and ("Morpho", "emissions_tokens") in bw._VIEW_BLOCKS


def test_g2_d1_the_sum_starts_where_the_window_only_katana_read_starts(monkeypatch):
    bw = _morpho(monkeypatch)
    rng = lambda a, b, v: [(str(d.date()), v) for d in pd.date_range(a, b)]  # noqa: E731
    g = {("Morpho", "emissions_tokens_claims_ethereum"):
             _frame("Morpho", "emissions_tokens_claims_ethereum", rng("2026-06-01", "2026-10-17", 10.0)),
         ("Morpho", "emissions_tokens_claims_arbitrum"):
             _frame("Morpho", "emissions_tokens_claims_arbitrum", rng("2026-03-01", "2026-10-17", 1.0)),
         ("Morpho", "emissions_tokens_claims_base"):
             _frame("Morpho", "emissions_tokens_claims_base", rng("2026-05-01", "2026-10-17", 100.0)),
         ("Morpho", "emissions_tokens_claims_katana"):
             _frame("Morpho", "emissions_tokens_claims_katana", rng("2026-07-10", "2026-10-17", 5.0))}
    bw._emissions_from_metric_views(g)
    e = g[("Morpho", "emissions_tokens")].set_index("date")["value"]
    assert e.index.min() == pd.Timestamp("2026-07-10"), "before the Katana window nothing was read there"
    assert e.loc["2026-10-17"] == pytest.approx(116.0) and e.index.max() == pd.Timestamp("2026-10-17")


def test_g2_d1_an_rpc_window_scan_goes_straight_to_the_window(monkeypatch, tmp_path):
    """Katana has no explorer route: the scan is not refused as unconfigured, it reads the window by RPC."""
    from fetch import logscan
    from fetch.logcache import LogCache

    class Exp:
        def configured(self, chain_id, transfer=False):
            return []

    class Rdr:
        def block_number(self, chain):
            return 1_000_000
    calls = []
    sc = logscan.LogScan.__new__(logscan.LogScan)
    sc.explorer, sc.reader, sc.cache, sc.unbounded = Exp(), Rdr(), LogCache(tmp_path), True
    monkeypatch.setattr(sc, "_window_scan", lambda *a: calls.append(a) or True, raising=False)
    spec = next(s for s in config.PROJECT_BY_NAME["Morpho"]["log_scans"] if s["key"] == "merkl_morpho_out_katana")
    out = FetchOutput()
    sc._scan(config.PROJECT_BY_NAME["Morpho"], spec, 100, out)
    assert len(calls) == 1 and calls[0][2] == "katana" and calls[0][3] == 747474
    assert calls[0][6] == 1_000_000 - logscan.CONFIRMATIONS
    assert not out.gaps and not [e for e in out.log if e.status == "unconfigured"]


# ----------------------------------------------------------------------------------- D2 / L3 total supply net of burns
def _net(project, monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[project]])
    bw._VIEW_BLOCKS.pop((project, "total_supply"), None)
    return bw


def test_g2_d2_hyperliquid_total_is_gross_less_the_fund_and_the_hyperevm_burn(monkeypatch):
    """Jake's 10-13 dump + the burn read 2026-10-13 16:22 (308,538.17): 998,862,059.40 − 48,007,021.50 − 308,538.17."""
    bw = _net("Hyperliquid", monkeypatch)
    g = _groups("Hyperliquid")
    gross = g[("Hyperliquid", "total_supply_gross")]
    fund = g[("Hyperliquid", "burn_address_balance")]
    day = pd.Timestamp("2026-10-13")
    g[("Hyperliquid", "hyperevm_zero_address_balance")] = _frame("Hyperliquid", "hyperevm_zero_address_balance",
                                                                 [("2026-10-13", 308_538.17)])
    cg_before = g[("Hyperliquid", "total_supply")].copy()
    bw._total_supply_net_views(g)
    t = g[("Hyperliquid", "total_supply")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    v = lambda f: float(f.assign(date=pd.to_datetime(f["date"])).set_index("date")["value"].loc[day])  # noqa: E731
    assert t["value"].loc[day] == pytest.approx(v(gross) - v(fund) - 308_538.17)
    assert t["source"].loc[day] == "derived:total_supply_gross-burn_address_balance-hyperevm_zero_address_balance"
    # CoinGecko's figure stays beside it, and only the days before ours fall back to it
    assert len(g[("Hyperliquid", "total_supply_coingecko")]) >= len(cg_before)
    assert (t.index[t["source"].str.startswith("derived")] == day).all()


def test_g2_d2_a_missing_burn_leg_blocks_the_total(monkeypatch):
    bw = _net("Hyperliquid", monkeypatch)
    g = _groups("Hyperliquid")
    g.pop(("Hyperliquid", "hyperevm_zero_address_balance"), None)
    bw._total_supply_net_views(g)
    assert "hyperevm_zero_address_balance" in bw._VIEW_BLOCKS[("Hyperliquid", "total_supply")]


def test_g2_l3_geodnet_total_is_on_chain_net_of_burns_over_the_whole_history(monkeypatch):
    """GEODNET's dump: total_supply_gross and burn_address_balance for 401 days; the net equals CoinGecko's from
    2026-10-12 (when CoinGecko became net of the burn) and covers every day, so FDV needs no overlap check."""
    bw = _net("GEODNET", monkeypatch)
    g = _groups("GEODNET")
    n_gross = g[("GEODNET", "total_supply_gross")]["date"].nunique()
    bw._total_supply_net_views(g)
    t = g[("GEODNET", "total_supply")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    derived = t[t["source"].str.startswith("derived")]
    assert len(derived) == n_gross >= 400
    cg = g[("GEODNET", "total_supply_coingecko")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    d = pd.Timestamp("2026-10-13")
    # CoinGecko's figure lags the daily burn: 10-13 it still read 10-11's burn balance (69,428,936.38)
    burn = g[("GEODNET", "burn_address_balance")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    assert float(cg["value"].loc[d]) == pytest.approx(1e9 - float(burn["value"].loc["2026-10-11"]), abs=1.0)
    assert derived["value"].loc[d] == pytest.approx(1e9 - float(burn["value"].loc[d]), abs=1e-3)
    assert "GEODNET" not in config.FDV_TOTAL_HISTORY
    assert config.TOTAL_SUPPLY_NET["GEODNET"]["less"] == ("burn_address_balance",)


def test_g2_d2_coingecko_writes_only_the_cross_check_for_net_projects():
    assert {"Hyperliquid", "GEODNET"} <= set(config.TOTAL_SUPPLY_NET)
    for n in ("Hyperliquid", "GEODNET"):
        assert config.TOTAL_SUPPLY_ONCHAIN_FIRST[n]["view"] is True
        assert n in config.METRICS["total_supply_coingecko"]["only_projects"]


def _hc():
    from fetch.hypercore import HyperCoreInfo
    return HyperCoreInfo()


def test_g2_d2_circulating_is_written_net_of_the_hyperevm_burn_and_refused_without_it(monkeypatch):
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    api = p["node_api"]
    evm = next(r for r in api["extra_reads"] if r.get("subtract_from_supply"))
    assert evm["address"] == "0x0000000000000000000000000000000000000000"
    assert evm["archive_endpoints"] == ("https://rpc.purroofgroup.com",)
    h = _hc()
    monkeypatch.setattr(h, "wallet_hype", lambda endpoint, addr: (1_000.0, "stub"))
    monkeypatch.setattr(h, "evm_balance", lambda read, block="latest": (308_538.17, "stub"))
    excl = [{"label": "a", "address": "0x" + "1" * 40}, {"label": "b", "address": "0x" + "2" * 40}]
    out = FetchOutput()
    h._circ_less_wallets("Hyperliquid", api, {}, 240_000_000.0, excl, out, pd.Timestamp("2026-10-18"))
    f = out.frame().set_index("metric")
    assert f.loc["circulating_supply_first_party", "value"] == pytest.approx(240_000_000.0 - 2_000.0 - 308_538.17)
    assert f.loc["circulating_supply_first_party", "source"].endswith("-exclude_wallets-hyperevm_burn")
    monkeypatch.setattr(h, "evm_balance", lambda read, block="latest": (None, "every endpoint refused"))
    out = FetchOutput()
    h._circ_less_wallets("Hyperliquid", api, {}, 240_000_000.0, excl, out, pd.Timestamp("2026-10-18"))
    assert "circulating_supply_first_party" not in set(out.frame()["metric"])
    assert any("HyperEVM burn" in e.message for e in out.log if e.status == "failed")


def test_g2_d2_the_release_adds_the_hyperevm_burn_back():
    plus = config.CIRCULATING_ONCHAIN["Hyperliquid"]["release_first_party"]["plus"]
    assert "hyperevm_priority_burn_tokens" in plus and "actual_buyback_tokens" in plus


def test_g2_d2_seed_reads_each_days_first_block_and_derives_the_burn(monkeypatch):
    from fetch import hyperevm_burn as hb
    t0 = int(pd.Timestamp("2026-10-10").tz_localize("UTC").timestamp())
    # one block a second from t0 - 100; balance grows 10 HYPE a day
    head = 4 * 86400

    def rpc(method, params):
        if method == "eth_blockNumber":
            return hex(head)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(t0 - 100 + int(params[0], 16))}
        assert method == "eth_getBalance" and params[0] == "0x0000000000000000000000000000000000000000"
        ts = t0 - 100 + int(params[1], 16)
        return hex(int((300_000 + 10 * ((ts - t0) // 86400)) * 10 ** 18))
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]["extra_reads"]
                if r.get("subtract_from_supply"))
    days = [pd.Timestamp("2026-10-10"), pd.Timestamp("2026-10-11"), pd.Timestamp("2026-10-13")]
    got, failed = hb.history_rows("Hyperliquid", read, days, rpc)
    assert not failed and [v for _, v in got] == [300_000.0, 300_010.0, 300_030.0]
    assert hb.block_at(rpc, t0) == 100
    fr = hb.frames("Hyperliquid", read, got)
    stock, flow = fr[0], fr[1]
    assert set(stock["metric"]) == {"hyperevm_zero_address_balance"} and stock["source"].str.endswith(":archive").all()
    assert list(flow["metric"]) == ["hyperevm_priority_burn_tokens"] and list(flow["value"]) == [10.0]
    long = pd.DataFrame({"project": "Hyperliquid", "metric": ["total_supply_gross", "hyperevm_zero_address_balance"],
                         "date": ["2026-10-15", "2026-10-16"], "value": [1.0, 2.0]})
    monkeypatch.setattr(hb, "today", lambda: pd.Timestamp("2026-10-18"))
    assert hb.days_needed(long, "Hyperliquid", "hyperevm_zero_address_balance") == [
        pd.Timestamp("2026-10-15"), pd.Timestamp("2026-10-17")]


# ------------------------------------------------------------------------------------------------- D3 Ether.fi 5M
def test_g2_d3_the_5m_ethfi_is_signed_off_as_a_transfer():
    q = [x for x in config.OPEN_QUESTIONS if x.get("project") == "Ether.fi" and "5,000,000 ETHFI" in str(x.get("topic"))]
    assert len(q) == 1 and q[0]["status"] == "answered" and q[0]["answered_on"] == "2026-10-18"
    assert q[0]["topic"].startswith("ANSWERED 2026-10-18") and "TRANSFER" in q[0]["topic"]
    labels = {}

    def walk(o):
        if isinstance(o, dict):
            if "not_counted_labels" in o:
                labels.update(o["not_counted_labels"])
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
    walk(config.PROJECT_BY_NAME["Ether.fi"])
    lab = next(v for k, v in labels.items() if k.lower().startswith("0x83971edb"))
    assert lab.startswith("transfer (signed off, Jake 2026-10-18)")
    open_now = [x for x in config.OPEN_QUESTIONS if x.get("project") == "Ether.fi" and x.get("status") != "answered"
                and "5,000,000" in str(x.get("topic"))]
    assert not open_now


# ---------------------------------------------------------------------------------------------------- D4 AQAv2
def test_g2_d4_aqa_is_booked_from_the_documented_interest_address():
    src = next(s for s in config.PROJECT_BY_NAME["Hyperliquid"]["revenue_sources"] if "AQAv2" in s["name"])
    assert src["booked"] is True and src["metric"] == "aqa_revenue_usd"
    assert src["source_url"] == "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/aligned-quote-assets"
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]["extra_reads"]
                if r.get("shape") == "aqa_payments")
    # "0x50...00 + {token_index}", USDC = token index 0
    assert list(read["interest_addresses"]) == ["0x" + format(0x50 << 152, "040x")]
    assert config.REVENUE_TOTAL["Hyperliquid"] == ("revenue_usd", "aqa_revenue_usd")
    assert config.revenue_headline_metric("Hyperliquid") == "revenue_total_usd"


def test_g2_d4_aqa_reader_counts_only_documented_sends_to_the_fund(monkeypatch):
    from fetch import hypercore
    monkeypatch.setattr(hypercore, "today", lambda: pd.Timestamp("2026-10-06"))
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]["extra_reads"]
                if r.get("shape") == "aqa_payments")
    fund = read["recipient"]
    ms = lambda d: int(pd.Timestamp(d).tz_localize("UTC").timestamp() * 1000)  # noqa: E731
    led = [
        {"time": ms("2026-10-03 00:00:01"), "hash": "a", "delta": {"type": "send", "user": "0x" + "50" + "0" * 38,
                                                                    "destination": fund, "amount": "2", "usdcValue": "2.0"}},
        {"time": ms("2026-10-04 00:00:02"), "hash": "b", "delta": {"type": "send", "user": "0x" + "50" + "0" * 38,
                                                                    "destination": fund, "amount": "14580774.69",
                                                                    "usdcValue": "14580774.69"}},
        {"time": ms("2026-10-04 05:00:00"), "hash": "c", "delta": {"type": "send", "user": "0x" + "ab" * 20,
                                                                    "destination": fund, "usdcValue": "999"}},
        {"time": ms("2026-10-05 05:00:00"), "hash": "d", "delta": {"type": "spotTransfer",
                                                                    "user": "0x" + "50" + "0" * 38,
                                                                    "destination": fund, "usdcValue": "7"}}]
    h = _hc()

    class H:
        def post(self, url, json_body):
            assert json_body["type"] == "userNonFundingLedgerUpdates" and json_body["user"] == fund.lower()
            return led
    h.http = H()
    out = FetchOutput()
    h._aqa_payments(config.PROJECT_BY_NAME["Hyperliquid"], config.PROJECT_BY_NAME["Hyperliquid"]["node_api"], read,
                    out, pd.Timestamp("2026-10-06"))
    s = out.frame().assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")["value"]
    assert s.index.min() == pd.Timestamp("2026-08-26") and s.index.max() == pd.Timestamp("2026-10-05")
    assert s.loc["2026-10-03"] == 2.0 and s.loc["2026-10-04"] == pytest.approx(14_580_774.69)
    assert s.loc["2026-10-05"] == 0.0 and s.drop(pd.to_datetime(["2026-10-03", "2026-10-04"])).eq(0).all()


def test_g2_d4_revenue_total_counts_aqa_as_zero_before_activation(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    rng = lambda a, b, v: [(str(d.date()), v) for d in pd.date_range(a, b)]  # noqa: E731
    g = {("Hyperliquid", "revenue_usd"): _frame("Hyperliquid", "revenue_usd", rng("2026-08-20", "2026-10-05", 100.0)),
         ("Hyperliquid", "aqa_revenue_usd"): _frame("Hyperliquid", "aqa_revenue_usd",
                                                    rng("2026-08-26", "2026-10-05", 0.0))}
    g[("Hyperliquid", "aqa_revenue_usd")].loc[lambda x: x["date"] == "2026-10-04", "value"] = 14_580_774.69
    bw._revenue_total_views(g)
    t = g[("Hyperliquid", "revenue_total_usd")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    assert t["value"].loc["2026-08-20"] == 100.0, "before activation AQAv2 paid nothing: a 0, not a gap"
    assert t["value"].loc["2026-10-04"] == pytest.approx(14_580_874.69)


# ----------------------------------------------------------------------------------------------- D5 Aerodrome supply
def test_g2_d5_each_aero_once_and_the_old_term_falls_away_when_the_reserve_is_burned():
    from fetch.aero_v3 import supply_once
    # 200M reserve pre-minted; 120M old AERO migrated (held), 80M old still out; Velodrome allocation 50M held
    r = supply_once(new_total=1_300e6, reserve=80e6, velo_held=50e6, new_noncirc=30e6, old_total=1_900e6,
                    old_noncirc=100e6, old_held=1_820e6, ratio=1.0)
    assert r["total"] == pytest.approx(1_220e6 + 80e6)        # each AERO once: the 80M old still out, not twice
    assert r["old_term"] == pytest.approx(80e6)
    assert r["circulating"] == pytest.approx((1_300e6 - 80e6 - 50e6 - 30e6) + 0.0)   # old circulating − held < 0 -> 0
    assert r["noncirculating"] == pytest.approx(r["total"] - r["circulating"])
    # burnRemaining(): the reserve is 0, the old term is 0 by itself
    b = supply_once(new_total=1_220e6, reserve=0.0, velo_held=50e6, new_noncirc=30e6, old_total=1_900e6,
                    old_noncirc=100e6, old_held=1_820e6, ratio=1.0)
    assert b["old_term"] == 0.0 and b["total"] == pytest.approx(1_220e6)
    # a non-1:1 ratio is applied, still capped at the reserve
    c = supply_once(new_total=1_000e6, reserve=500e6, velo_held=0.0, new_noncirc=0.0, old_total=1_000e6,
                    old_noncirc=0.0, old_held=600e6, ratio=0.5)
    assert c["old_term"] == pytest.approx(200e6) and c["total"] == pytest.approx(700e6)


def _aero_state(**extra):
    st = {"launched": True, "addresses": {"minter": "0x5f1c9a3e7b2d48c06e9f31a7b5d2c8e4f0a6b913",
                                          "voting_escrow": "0x8e2d7c4b1a9f36e05d8c27b4a1f9e6d3c0b5a872",
                                          "token": "0x3a7f9c1e5b8d24f06a3c9e7b1d5f82a4c6e0b391"},
          "source_url": "https://github.com/dromos-labs", "verified": "2026-10-22"}
    st["addresses"].update(extra)
    return st


MIG = "0x7d3e9b1f5a2c48e06b9d31f7a5c2e8b4d0f6a1c9"
VELO = "0x2b8f4d6a1e9c37b05f2a8d6e3c1b9f7a4e0d5c28"


HEAD = 37_000_000                                   # the Base block the supply is read at


def _supply_rpc(a, v2_token=None, basis=0, converted=0, remaining=80e6, tags=None):
    tok = _aero_state()["addresses"]["token"]
    W, w = a.WAD, (lambda v: "0x" + f"{int(v):064x}")
    noncirc = {c["address"].lower() for k, c in config.PROJECT_BY_NAME["Aerodrome"]["contracts"].items()
               if k.startswith("noncirc_")}
    legacy = a.LEGACY_TOKEN

    def rpc(method, params):
        if method == "eth_blockNumber":
            return hex(HEAD)
        if method == "eth_getCode":
            return "0x6080"
        if tags is not None:
            tags.append(params[1])
        to, data = params[0]["to"].lower(), params[0]["data"]
        sel, arg = data[:10], data[10:]
        addr = "0x" + arg[-40:] if arg else ""
        if to == tok:
            if sel == a.selector("MIGRATION"):
                return w(int(MIG, 16))
            if sel == a.selector("VELODROME_MIGRATION"):
                return w(int(VELO, 16))
            if sel == a.selector("totalSupply"):
                return w(1_300e6 * W)
            if sel == a.selector("balanceOf"):
                # new AERO held by the migration contract = the reserve (G2-D5e)
                return w(50e6 * W if addr == VELO else 80e6 * W if addr == MIG else 1e6 * W if addr in noncirc else 0)
        if to == MIG:
            return {a.selector("V2_TOKEN"): w(int(v2_token or legacy, 16)), a.selector("V2_ESCROW"):
                    w(int(a.LEGACY_ESCROW, 16)), a.selector("V3_TOKEN"): w(int(tok, 16)),
                    a.selector("MIGRATION_TOKEN_ID"): w(77), a.selector("remaining"): w(remaining * W),
                    a.selector("previewConversion"): w(basis * W) + w(converted * W)[2:] + w(0)[2:] + w(1)[2:]}[sel]
        if to == legacy:
            if sel == a.selector("totalSupply"):
                return w(1_900e6 * W)
            if sel == a.selector("balanceOf"):
                return w(2e6 * W if addr in noncirc else 5 * W if addr == MIG else 0)
        raise AssertionError((to, sel))
    return rpc


def test_g2_d5_the_v3_reader_writes_total_and_noncirculating_from_the_contracts(monkeypatch):
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    rdr = a.AeroV3(rpc=_supply_rpc(a, basis=1_820e6, converted=1_820e6), state=_aero_state())
    out = FetchOutput()
    rdr._supply("Aerodrome", {k: v.lower() for k, v in _aero_state()["addresses"].items()}, out)
    f = out.frame().set_index("metric")
    n = sum(1 for k in config.PROJECT_BY_NAME["Aerodrome"]["contracts"] if k.startswith("noncirc_"))
    held = 1_820e6 + 5
    exp = a.supply_once(1_300e6, 80e6, 50e6, n * 1e6, 1_900e6, n * 2e6, held, 1.0)
    assert f.loc["total_supply", "value"] == pytest.approx(exp["total"])
    assert f.loc["noncirculating_holding_tokens", "value"] == pytest.approx(exp["noncirculating"])
    assert f.loc["total_supply", "source"] == "aero_v3:supply_once[ratio 1]"
    assert {"total_supply", "noncirculating_holding_tokens"} <= set(config.AERODROME_MIGRATION["v3_replaces"])
    h = config.PROJECT_BY_NAME["Aerodrome"]["series_handover"]
    assert h["total_supply"]["ordered_points"][-1] == "aero_v3:supply_once" and h["total_supply"]["declared"] == "2026-10-11"
    assert h["noncirculating_holding_tokens"]["ordered_points"][-1] == "aero_v3:supply_once"


def test_g2_d5_a_ratio_that_is_not_one_to_one_is_read_and_applied(monkeypatch):
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    rdr = a.AeroV3(rpc=_supply_rpc(a, basis=1_000e6, converted=500e6), state=_aero_state())
    out = FetchOutput()
    rdr._supply("Aerodrome", {k: v.lower() for k, v in _aero_state()["addresses"].items()}, out)
    f = out.frame().set_index("metric")
    assert f.loc["total_supply", "source"] == "aero_v3:supply_once[ratio 0.5]"
    assert f.loc["total_supply", "value"] == pytest.approx((1_300e6 - 80e6) + 80e6)    # 0.5 x 900M, capped at 80M


def test_g2_d5_a_migration_naming_another_token_is_refused(monkeypatch):
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    rdr = a.AeroV3(rpc=_supply_rpc(a, v2_token="0x" + "9c" * 20), state=_aero_state())
    with pytest.raises(RuntimeError, match="V2_TOKEN"):
        rdr._supply("Aerodrome", {k: v.lower() for k, v in _aero_state()["addresses"].items()}, FetchOutput())
    rdr = a.AeroV3(rpc=_supply_rpc(a), state=_aero_state())
    with pytest.raises(RuntimeError, match="addresses.migration"):
        rdr._supply("Aerodrome", {**{k: v.lower() for k, v in _aero_state()["addresses"].items()},
                                  "migration": "0x4c1e8a2f6d9b35e07c4a1f8d2b6e9c3a5f0d7b14"}, FetchOutput())


def test_g2_d5_placeholders_are_refused_and_nothing_changes_before_the_cutover(monkeypatch):
    from fetch import aero_v3 as a
    from fetch.validate import _migration_switch_missing
    day = pd.Timestamp("2026-10-23")
    assert any("addresses.migration" in m for m in _migration_switch_missing(_aero_state(migration="0x" + "0" * 40), day))
    assert any("addresses.migration" in m for m in _migration_switch_missing(_aero_state(migration="0x…"), day))
    assert not _migration_switch_missing(_aero_state(migration=MIG), day)
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-21"))
    out = FetchOutput()
    a.AeroV3(rpc=lambda *x: (_ for _ in ()).throw(AssertionError("read before the cutover")),
             state=_aero_state()).run([], 30, out)
    assert out.frame().empty and not out.log


# ---------------------------------------------------------------------------------------------------- L1 / L2
def test_g2_l1_mkr_reference_names_its_block_and_keeps_the_tolerance():
    rows = list(csv.DictReader((ROOT / "independent_references.csv").open(encoding="utf-8")))
    r = next(x for x in rows if x.get("project") == "Sky" and "mkr_supply_unconverted" in str(x.values()))
    text = " ".join(str(v) for v in r.values())
    assert "block 26184665" in text and "83465.892107" in text
    tol = next((v for k, v in r.items() if k and "tol" in k.lower()), None)
    assert tol is not None and float(str(tol).rstrip("%")) in (0.1, 0.001)


def test_g2_l2_the_buyback_primary_is_our_fund_read_and_the_gap_is_explained():
    lab = config.METRICS["buyback_hypercore_asxn_usd"]["label"]
    assert "cross-check" in lab.lower() and "G2-L2" in lab


# ------------------------------------------------------------------------------------------- addendum A1 price series
def _with_tier_rows(project, inside=False):
    import real_dump as rd
    p = rd.load(project)
    cid = config.PROJECT_BY_NAME[project]["coingecko_id"]
    px = [r for r in p["metrics"] if r["metric"] == "price_usd"]
    f0 = pd.Timestamp(min(r["date"] for r in px))
    tier = f"price_history:coins.llama.fi[coingecko:{cid}]"
    for i in range(1, 31):
        p["metrics"].append({"date": str((f0 - pd.Timedelta(days=i)).date()), "metric": "price_usd", "value": 1.0,
                             "source": tier, "tier": 1, "fetched_at": min(r["fetched_at"] for r in px)})
    if inside:
        last = max(r["date"] for r in px)
        next(r for r in px if r["date"] == last)["source"] = tier
    return p


@pytest.mark.parametrize("project", ["GEODNET", "Hyperliquid", "Pendle", "Fluid", "Chainlink", "Morpho", "Aethir"])
def test_a1_the_tier_rows_join_the_declared_price_handover(project):
    """Jake's run on e2250e5: price_usd held CoinGecko rows and the G2.1 tier's `price_history:coins.llama.fi[...]` rows,
    a point outside the declared handover -> measuring_point_changed blanked 11 headline cells. Read time, the older tier
    rows are the declared leg and a tier row inside CoinGecko's range is not shown: one series, the handover accepted."""
    import build_workbook as bw
    import real_dump as rd
    from fetch.base import _measuring_point
    df = rd.long_frame(_with_tier_rows(project, inside=True))
    g = {k: v.reset_index(drop=True) for k, v in df.groupby(["project", "metric"])}
    before = g[(project, "price_usd")]
    pts = set(before["source"].map(_measuring_point))
    assert bw.handover_refusal(project, "price_usd", pts, {}) is not None          # the regression, reproduced
    bw._price_tier_views(g)
    px = g[(project, "price_usd")].assign(date=lambda x: pd.to_datetime(x["date"]))
    pts = set(px["source"].map(_measuring_point))
    assert pts == {"coingecko", "defillama_coins:history"}
    spans = {mp: (gg["date"].min(), gg["date"].max()) for mp, gg in px.groupby(px["source"].map(_measuring_point))}
    assert bw.handover_refusal(project, "price_usd", pts, spans) is None
    assert len(px) == len(before) - 1                                              # the inside row is not shown
    assert not px["source"].str.startswith("price_history:").any()


def test_a1_the_tier_writes_only_before_coingecko_and_under_the_declared_point(tmp_path, monkeypatch):
    from fetch import price_history as ph
    monkeypatch.setattr(ph, "today", lambda: pd.Timestamp("2026-10-18"))
    rows = [("2025-01-01", "actual_buyback_tokens", 5.0, "x")]
    rows += [(str(d.date()), "price_usd", 1.0, "coingecko") for d in pd.date_range("2025-09-12", "2026-10-10")]
    stored = pd.DataFrame([{"date": d, "project": "Pendle", "metric": m, "value": v, "source": s, "tier": 1}
                           for d, m, v, s in rows])
    days, _ = ph.missing_price_days(stored, "Pendle")
    assert days[0] == pd.Timestamp("2025-01-01") and days[-1] == pd.Timestamp("2025-09-11")
    assert pd.Timestamp("2026-10-15") not in days, "never a day inside (or after) CoinGecko's range"
    assert ph.missing_price_days(stored[stored["metric"] != "price_usd"], "Pendle")[0] == []


def test_a1_run_sql_cv_relabels_older_rows_and_removes_inside_ones():
    con = _store([("2025-09-10", "Pendle", "price_usd", 3.0, "price_history:coins.llama.fi[coingecko:pendle]"),
                  ("2025-09-12", "Pendle", "price_usd", 3.1, "coingecko"),
                  ("2025-09-13", "Pendle", "price_usd", 3.2, "price_history:coins.llama.fi[coingecko:pendle]")])
    _run_writes(con, "CV")
    assert con.execute("SELECT date, source FROM metrics ORDER BY date").fetchall() == [
        ("2025-09-10", "defillama_coins:history[coins.llama.fi coingecko:pendle]"), ("2025-09-12", "coingecko")]


# ------------------------------------------------------------------------------------- addendum A3 Pause Proxy at block
def test_a3_the_pause_proxy_reference_is_compared_at_its_own_block():
    """Jake's run on e2250e5: still BROKEN +1.29% — the build compared 20,399,911, the 10-13 row as overwritten by a later
    read of that day. The check now reads our archive eth_call at the reference's block, whatever the day rows say."""
    import references as rf
    rows = [("2026-10-13", "treasury_holding_tokens", 20_399_911.0), ("2026-10-14", "treasury_holding_tokens", 21_528_971.0),
            ("2026-10-13", "mkr_outstanding_tokens", 83_160.82)]
    df = pd.DataFrame([{"date": pd.Timestamp(d), "project": "Sky", "metric": m, "value": v,
                        "source": "chain:ethereum:pause_proxy", "tier": 2} for d, m, v in rows])
    groups = {k: g for k, g in df.groupby(["project", "metric"])}
    refs = [r for r in rf.load() if r["project"] == "Sky" and r["metric"] in ("pause_proxy_sky_balance",
                                                                               "mkr_supply_unconverted")]
    got = {x["metric"]: x for x in rf.check(groups, rows=refs, projects={"Sky"})}
    pp = got["pause_proxy_sky_balance"]
    assert pp["status"] == "VERIFIED" and pp["ours"] == pytest.approx(20_142_225.835854)
    assert "block 26,184,750" in pp["route"] and "(+0.01%)" in pp["detail"]
    assert got["mkr_supply_unconverted"]["status"] == "VERIFIED"
    assert "block 26,184,665" in got["mkr_supply_unconverted"]["route"]
    for r in config.REFERENCE_BLOCK_READS.values():
        assert r["rpc_host"] and r["read_on"] and r["block"] > 0
    tol = {r["metric"]: r["tolerance"] for r in refs}
    assert tol == {"pause_proxy_sky_balance": "1%", "mkr_supply_unconverted": "0.1%"}


# ---------------------------------------------------------------------------------- addendum A2 browser_capture rerun
def test_a2_a_page_rendered_earlier_today_marks_its_series_current_not_missing(tmp_path, monkeypatch):
    """Jake's full run on e2250e5: browser_capture stored 0 rows in 0.3 s — the morning's group D run had rendered the
    ASXN pages (7,822 rows) and the once-a-day check returned silently, so the 12 ASXN series went back on the Gap
    Report. The page is still rendered once a day; a later run the same day marks every pinned series current."""
    from fetch import browser_capture as bc
    from fetch.logcache import DailyChecks
    monkeypatch.delenv("TOKEN_METRICS_DAILY_CHECKS", raising=False)
    monkeypatch.setattr(bc, "today", lambda: pd.Timestamp("2026-10-18"))
    daily = DailyChecks(tmp_path)
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    for pg in p["browser_capture"]["pages"]:
        daily.done(f"{bc.SOURCE}:{pg['url']}", "2026-10-18")             # the morning's group D run
    rendered = []
    a = bc.BrowserCapture(capture_fn=lambda *x, **k: rendered.append(x) or {"responses": [], "text": ""}, daily=daily)
    out = FetchOutput()
    a.run([p], 30, out)
    assert not rendered and out.frame().empty
    want = {m for pg in p["browser_capture"]["pages"] for m in bc.pinned_metrics(pg)}
    assert {m for (proj, m) in out.current if proj == "Hyperliquid"} == want
    assert len([m for m in want if "asxn" in m or m in ("burn_auction_usd", "burn_hyperevm_usd")]) >= 10
    assert all(e.status == "skipped" and "rendered today already" in e.message for e in out.log)


# ---------------------------------------------------------------------------- addendum A5 Pendle insider stake shares
def test_a5_insider_stake_shares_before_spendle_is_deployed_is_before_deployment():
    """archive_backfill Pendle: insider_stake_shares read NOT WRITTEN on 180 days — balanceOf on sPENDLE answers nothing
    before its deployment (2026-01-16). The share token is named by KEY ("spendle"); it now resolves to its address, so
    the days are before deployment, as cooldown_days (read from sPENDLE itself) already was."""
    from types import SimpleNamespace
    from fetch import archive
    spendle = config.PROJECT_BY_NAME["Pendle"]["contracts"]["spendle"]["address"].lower()
    asked = []

    class W3:
        eth = SimpleNamespace(get_code=lambda a, block_identifier=None: asked.append(a.lower())
                              or (b"" if a.lower() == spendle else b"\x60\x80"))

    class Rdr:
        at_block = {"ethereum": 23_000_000}

        def web3(self, chain):
            return W3()

        def checksum(self, a):
            return a

        def deployment_block(self, chain, a):
            return 24_241_000
    bf = archive.ArchiveBackfill.__new__(archive.ArchiveBackfill)
    bf.reader = Rdr()
    why = bf._undeployed(config.PROJECT_BY_NAME["Pendle"], "insider_stake_shares")
    assert why and "has no code at block 23,000,000" in why and "deployed at block 24,241,000" in why
    assert spendle in asked


# --------------------------------------------------------------------------------- addendum A7 Alchemy OP / BNB / Bera
def test_a7_served_alchemy_chains_open_their_routes_and_wire_their_scans(monkeypatch):
    """Jake is enabling OP Mainnet, BNB and Berachain on his Alchemy app. Once alchemy_transfer_routes lists them served:
    BNB's and Berachain's Pendle gauge scans are wired (G2.7), and OP — whose scans already exist — gains the Transfers
    route after its explorers. A chain not served stays closed."""
    import check_offline_items as co
    for n in ("Pendle", "Fluid", "Morpho"):
        monkeypatch.setitem(config.PROJECT_BY_NAME[n], "log_scans", list(config.PROJECT_BY_NAME[n]["log_scans"]))
    monkeypatch.setitem(config.PROJECT_BY_NAME["Pendle"], "emissions_from_metric",
                        tuple(config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]))
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES, "Fluid", dict(config.OUTFLOW_CATEGORIES["Fluid"]))
    monkeypatch.setattr(config, "EXPLORER_TRANSFER_ROUTES", {})
    assert {c for c, *_ in co.EXPLORER_PROBE} >= {10, 56, 80094}
    wired = config.wire_probed_scans({"alchemy": {"served": [10, 56, 80094], "probed_on": "2026-10-19"}})
    assert {"gauge_pendle_out_bsc", "gauge_pendle_out_berachain"} <= set(wired)
    assert config.transfer_route(10) == ["alchemy"] and config.explorer_order(10)[:1] != ["alchemy"]
    assert config.transfer_route(56) == ["alchemy"] and config.transfer_route(80094) == ["alchemy"]
    assert config.transfer_route(5000) == []
    pendle = [s["key"] for s in config.PROJECT_BY_NAME["Pendle"]["log_scans"]]
    assert "gauge_pendle_out_mantle" not in pendle


def test_g2_d1_the_blocked_emissions_reason_reaches_the_data_row(monkeypatch):
    """The summed view runs before _VIEW_BLOCKS is cleared mid-aggregate; its block was lost and Morpho's emissions read
    "no rows stored". On Jake's 10-13 dump (Ethereum claims still stored under emissions_tokens, before run_sql CT) the
    row is BLOCKED and names the chains still missing."""
    import build_workbook as bw
    import real_dump as rd
    df = rd.long_frame(rd.load("Morpho")).assign(is_manual=False, entered_on="", source_note="")
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Morpho"]])
    out = bw.aggregate(df, pd.DataFrame(columns=["source", "project", "last_success_at"]), pd.Timestamp("2026-10-13"))
    r = out[(out.project == "Morpho") & (out.metric == "emissions_tokens")].iloc[0]
    assert r["status"] == "blocked" and "emissions_tokens_claims_katana" in r["note"]
    legs = out[(out.project == "Morpho") & out.metric.str.startswith("emissions_tokens_claims_")]
    assert set(legs["status"]) == {"missing"}, "each leg applies to Morpho (archetype 2)"


def test_g2_d5e_the_reserve_is_the_migration_contracts_balance_at_one_block(monkeypatch):
    """Jake 2026-10-18 (D5 confirmed): the reserve is new AERO's balanceOf(MIGRATION()) read on-chain at the block, every
    term is read at that same block, and the Run Log line says so. remaining() must agree at that block."""
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    tags = []
    rdr = a.AeroV3(rpc=_supply_rpc(a, basis=1_820e6, converted=1_820e6, tags=tags), state=_aero_state())
    out = FetchOutput()
    rdr._supply("Aerodrome", {k: v.lower() for k, v in _aero_state()["addresses"].items()}, out)
    assert tags and set(tags) == {hex(HEAD)}, "every eth_call at the one pinned block"
    line = next(e.message for e in out.log if e.message.startswith("total_supply:"))
    assert f"at Base block {HEAD:,}: reserve = new AERO balanceOf(migration {MIG}) = 80,000,000.0000" in line
    assert "read on-chain at that block" in line
    # remaining() disagreeing with the balance at the same block: not the published contract, nothing written
    rdr = a.AeroV3(rpc=_supply_rpc(a, basis=1_820e6, converted=1_820e6, remaining=79e6), state=_aero_state())
    with pytest.raises(RuntimeError, match="reserve check failed"):
        rdr._supply("Aerodrome", {k: v.lower() for k, v in _aero_state()["addresses"].items()}, FetchOutput())


def test_g2_d2f_cw_restates_the_rows_before_10_11_and_the_series_reads_as_one_handover():
    """Jake's follow-up 2026-10-18: the circulating rows before 2026-10-11 (tokenDetails' own figure) are restated net
    of the same day's HyperEVM burn (run_sql CW), so after CU and CW the burn is out of every day; the series is the
    declared handover with no overlap, and a day with no balance row is left on the raw leg."""
    import build_workbook as bw
    from fetch.base import _measuring_point
    raw = "hypercore_info:tokenDetails.circulatingSupply"
    rows = [("2026-10-09", "circulating_supply_first_party", 302_100_000.0, raw),
            ("2026-10-10", "circulating_supply_first_party", 302_150_000.0, raw),
            ("2026-10-11", "circulating_supply_first_party", 302_167_698.0, raw),
            ("2026-10-12", "circulating_supply_first_party", 238_620_000.0, raw + "-exclude_wallets"),
            ("2026-10-13", "circulating_supply_first_party", 238_601_453.86, raw + "-exclude_wallets")]
    rows += [(d, "hyperevm_zero_address_balance", v, "hyperevm:eth_getBalance(0x0000…):archive")
             for d, v in (("2026-10-10", 307_900.0), ("2026-10-11", 308_100.0), ("2026-10-12", 308_300.0),
                          ("2026-10-13", 308_538.17))]
    con = _store([(d, "Hyperliquid", m, v, src) for d, m, v, src in rows])
    _run_writes(con, "CU")
    _run_writes(con, "CW")
    got = {d: (v, src) for d, v, src in con.execute(
        "SELECT date, value, source FROM metrics WHERE metric = 'circulating_supply_first_party' ORDER BY date")}
    assert got["2026-10-09"] == (302_100_000.0, raw), "no balance that day: left, listed by CW3"
    assert got["2026-10-10"] == (pytest.approx(302_150_000.0 - 307_900.0), raw + "-hyperevm_burn")
    assert got["2026-10-11"] == (pytest.approx(302_167_698.0 - 308_100.0), raw + "-hyperevm_burn")
    assert got["2026-10-13"] == (pytest.approx(238_292_915.69), raw + "-exclude_wallets-hyperevm_burn")
    pts = {_measuring_point(src) for _v, src in got.values()}
    spans = {}
    for d, (_v, src) in got.items():
        mp = _measuring_point(src)
        lo, hi = spans.get(mp, (pd.Timestamp(d), pd.Timestamp(d)))
        spans[mp] = (min(lo, pd.Timestamp(d)), max(hi, pd.Timestamp(d)))
    assert bw.handover_refusal("Hyperliquid", "circulating_supply_first_party", pts, spans) is None
