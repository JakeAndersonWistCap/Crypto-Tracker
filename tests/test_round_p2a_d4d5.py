"""ROUND P2a D4 / D5 (Jake 2026-10-27). No network.

D4 measuring-point handovers flagged BROKEN, declared under SPEC §1 rule 10 with their overlap check
   (build_workbook.handover_overlap; a_point / b_point restrict a side to one leg of a stitched series):
   (a) Near's lockups: manual -> near_rpc:lockups; (b) Chainlink's noncirculating_holding_tokens: 27 -> 33 wallets.
D5 Maple: (a) treasury_holding_tokens hands over to the DAO Safe on chain (treasury_holding_tokens_chain) — the page
   tile is gone; (b) the SSF Safe 0x58BE's monthly net Transfer flow, the buyback route's inflow on its own.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402


def _g(project, metric, days, values, sources):
    if isinstance(sources, str):
        sources = [sources] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": sources})


def _row(bw, g):
    """The fields aggregate() hands withheld_for about a series' measuring points."""
    g = g.sort_values("date")
    mps, change = bw.method_points(g)
    return {"source": g["source"].iloc[-1], "measuring_points": tuple(sorted(set(mps))), "method_change": change,
            "point_spans": {mp: (gg["date"].min(), gg["date"].max()) for mp, gg in g.groupby(mps)},
            "point_edges": {mp: (float(gg["value"].iloc[0]), float(gg["value"].iloc[-1])) for mp, gg in g.groupby(mps)}}


# ============================================================================================ D4(a) NEAR'S LOCKUPS
def test_d4a_near_lockups_handover_is_declared_and_no_longer_blanks_the_series():
    import build_workbook as bw
    import trajectory
    for m in ("near_lockup_locked_tokens", "near_lockup_unvested_tokens"):
        decl = config.declared_handover("Near", m)
        assert decl["ordered_points"] == ("manual", "near_rpc:lockups") and decl["valid_from"] == "2026-10-26"
        assert decl["overlap"]["tolerance_pct"] == 2.0 and decl["overlap"]["min_days"] == 3   # not loosened
        # the weekly read's block tag is WHEN, not WHAT: one measuring point
        assert trajectory.basis_of(m, "near_rpc:lockups@221748149") == "near_rpc:lockups"
        g = pd.concat([_g("Near", m, ["2026-10-11"], [2_058_522], "manual"),
                       _g("Near", m, ["2026-10-26"], [2_036_357], "near_rpc:lockups@221748149")])
        row = _row(bw, g)
        assert len(row["measuring_points"]) == 2
        assert bw.withheld_for("Near", m, row) is None, bw.withheld_for("Near", m, row)
        # the overlap: one manual row, weekly reads — never one day; the cell says so and names the command
        ho = bw.handover_overlap({("Near", m): g}, "Near", decl["overlap"])
        assert not ho["passed"] and ho["common_days"] == 0
        assert "UNCHECKED" in ho["text"] and "check_offline_items.py near_lockups_at_block" in ho["text"]
    # LOCKED: the same definition on both legs (declared "none: …") -> one series on the Trajectory; UNVESTED: the manual
    # row's cap differs (its own note) -> a step, so a SERIES BREAK stays
    g_l = pd.concat([_g("Near", "near_lockup_locked_tokens", ["2026-10-11"], [1], "manual"),
                     _g("Near", "near_lockup_locked_tokens", ["2026-10-26"], [1], "near_rpc:lockups@1")])
    g_u = pd.concat([_g("Near", "near_lockup_unvested_tokens", ["2026-10-11"], [1], "manual"),
                     _g("Near", "near_lockup_unvested_tokens", ["2026-10-26"], [1], "near_rpc:lockups@1")])
    since = pd.Timestamp("2026-01-01")
    assert trajectory._own_breaks("Near", "near_lockup_locked_tokens", {("Near", "near_lockup_locked_tokens"): g_l},
                                  since) == []
    assert len(trajectory._own_breaks("Near", "near_lockup_unvested_tokens",
                                      {("Near", "near_lockup_unvested_tokens"): g_u}, since)) == 1


def test_d4a_the_guard_reads_both_lockup_points_as_declared():
    import measuring_guard as mg
    for m in ("near_lockup_locked_tokens", "near_lockup_unvested_tokens"):
        assert {"manual", "near_rpc:lockups"} <= mg._declared("Near", m)


def test_d4a_overlap_at_the_manual_block_selects_the_lockups_alive_then(monkeypatch, tmp_path, capsys):
    """near_lockups_at_block: the lockups alive AT the block (NearBlocks' created / deleted heights) — one deleted since is
    read, one created since is not, one with no height is counted and left out; then both legs at that block."""
    import check_offline_items as coi
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    b = config.NEAR_LOCKUP_MANUAL_BLOCK
    (tmp_path / "near-lockups-heights.json").write_text(json.dumps({
        "alive.lockup.near": [100, 0], "deleted-since.lockup.near": [100, b + 5], "deleted-before.lockup.near": [100, b - 5],
        "created-since.lockup.near": [b + 1, 0], "unplaced.lockup.near": [100, -1]}))
    seen = {}

    def fake(limit=None, workers=4, block_id=None, accounts=None, cache_path=None, **_k):
        seen.update(block=block_id, accounts=sorted(accounts), urls=coi._NEAR_URLS_OVERRIDE)
        return {"accounts": len(accounts), "read": 2, "zero": 0, "unreadable": 0, "errors": {}, "faults": [],
                "locked": 2_058_522 * 1.001, "unvested": 1_999_721 * 0.9}
    monkeypatch.setattr(coi, "near_lockups", fake)
    assert coi.near_lockups_at_block() == 0
    out = capsys.readouterr().out
    assert seen["block"] == 219_544_718 and seen["accounts"] == ["alive.lockup.near", "deleted-since.lockup.near"]
    assert seen["urls"] and all("archival" in u for u in seen["urls"]) and coi._NEAR_URLS_OVERRIDE == ()
    assert "1 with no created / deleted height — NOT PLACED" in out
    assert "near_lockup_locked_tokens: manual (2026-10-11) 2,058,522 NEAR" in out and "+0.100% (WITHIN ±2%)" in out
    assert "-10.000% (OUTSIDE ±2%)" in out
    # a partial (limited) read is never compared
    assert coi.near_lockups_at_block(limit=2) == 0
    assert "a partial list — nothing compared" in capsys.readouterr().out


def test_d4a_a_buggy_lockup_read_by_the_state_route_does_not_crash_the_totals(monkeypatch, tmp_path, capsys):
    """The state route never reads the RPC's get_locked_amount, so a buggy-code lockup read that way has no rpc_locked —
    the totals crashed on it (KeyError, found re-reading at the manual block). Counted apart, never summed as 0."""
    import check_offline_items as coi
    cache = tmp_path / "pin.json"
    Y = 10 ** 24
    cache.write_text(json.dumps({"height": 5, "block_ts": 1, "pinned_at": "2026-10-27 00:00:00", "route": "state",
                                 "results": {
                                     "a.lockup.near": {"balance": str(10 * Y), "locked": str(4 * Y),
                                                       "unvested": str(3 * Y), "owners": str(6 * Y), "buggy": True,
                                                       "via": "state"},
                                     "b.lockup.near": {"balance": str(5 * Y), "locked": str(1 * Y),
                                                       "unvested": str(2 * Y), "owners": str(4 * Y), "buggy": True,
                                                       "rpc_locked": str(5 * Y), "via": "getters"}}}))
    monkeypatch.setattr(coi, "_lockup_throughput", lambda: {"calls_per_s": 1.0, "measured": "x", "how": "y"})
    got = coi.near_lockups(accounts=["a.lockup.near", "b.lockup.near"], block_id=5, cache_path=cache)
    out = capsys.readouterr().out
    assert got["locked"] == 5.0 and got["unvested"] == 4.0          # unvested capped at locked per lockup (3 + 1)
    assert got["buggy"] == 2 and got["buggy_rpc_locked"] is None       # one RPC figure unread: not a sum
    assert "1 read by the state route, where the RPC figure is not read" in out


# ============================================================================================ D4(b) CHAINLINK
def _cl_fixture_points():
    d = json.loads((ROOT / "fixtures" / "real_chainlink.json").read_text())
    return sorted({r["source"] for r in d["metrics"] if r["metric"] == "noncirculating_holding_tokens"})


def test_d4b_chainlink_wallet_set_growth_is_a_declared_nested_handover():
    import build_workbook as bw
    import measuring_guard as mg
    from fetch.base import _measuring_point
    decl = config.declared_handover("Chainlink", "noncirculating_holding_tokens")
    assert decl["nested_sums"] and decl["valid_from"] == "2026-10-26"
    p27, p33 = config.CHAINLINK_NONCIRC_POINTS["27"], config.CHAINLINK_NONCIRC_POINTS["33"]
    s27, s33 = bw.sum_components(p27), bw.sum_components(p33)
    assert len(s27) == 27 and len(s33) == 33 and s27 < s33          # the earlier set inside the later one
    assert all(k.startswith("ethereum:noncirc_cl_page_") for k in s33 - s27)
    # the declared 27-set IS the point the store holds (Jake's 10-13 dump: archive and live rows)
    for src in _cl_fixture_points():
        assert bw.sum_components(_measuring_point(src)) == s27, src[-120:]
    g = pd.concat([_g("Chainlink", "noncirculating_holding_tokens", ["2026-10-24", "2026-10-25"], [233_150_000.10] * 2,
                      [p27 + ":archive", p27]),
                   _g("Chainlink", "noncirculating_holding_tokens", ["2026-10-26", "2026-10-27"], [233_150_029.58] * 2,
                      p33)])
    row = _row(bw, g)
    assert len(row["measuring_points"]) == 2 and bw.withheld_for("Chainlink", "noncirculating_holding_tokens", row) is None
    assert mg._nested_growth("Chainlink", "noncirculating_holding_tokens", set(row["measuring_points"]))
    # a set that SWAPPED a wallet (not nested) is still refused — the declaration covers growth only
    swapped = "chain:sum(" + "+".join(sorted(s33 - {"ethereum:noncirc_blog_2022_400k"})) + ")"   # one wallet REMOVED
    g2 = pd.concat([g[g["date"] < "2026-10-26"], _g("Chainlink", "noncirculating_holding_tokens", ["2026-10-26"],
                                                    [1.0], swapped)])
    assert bw.withheld_for("Chainlink", "noncirculating_holding_tokens", _row(bw, g2))[0] == "measuring_point_changed"


def test_d4b_chainlink_overlap_is_unchecked_on_the_store_and_reads_the_sets_by_component_set():
    import build_workbook as bw
    decl = config.declared_handover("Chainlink", "noncirculating_holding_tokens")
    p27, p33 = config.CHAINLINK_NONCIRC_POINTS["27"], config.CHAINLINK_NONCIRC_POINTS["33"]
    g = pd.concat([_g("Chainlink", "noncirculating_holding_tokens", ["2026-10-25"], [233_150_000.10], p27),
                   _g("Chainlink", "noncirculating_holding_tokens", ["2026-10-26"], [233_150_029.58], p33)])
    ho = bw.handover_overlap({("Chainlink", "noncirculating_holding_tokens"): g}, "Chainlink", decl["overlap"])
    assert ho["common_days"] == 0 and "UNCHECKED" in ho["text"]
    assert "chainlink_noncirc_set_overlap" in ho["text"] and "27 wallets" in ho["text"]
    # the point filter matches a sum by its component SET (read order does not matter) — two metrics holding the legs
    # on common days compare as any declared overlap does
    rev = "chain:sum(" + "+".join(sorted(bw.sum_components(p27), reverse=True)) + ")"
    days = ["2026-10-20", "2026-10-21", "2026-10-22"]
    groups = {("Chainlink", "a_m"): _g("Chainlink", "a_m", days, [233_150_000.10] * 3, rev + ":archive"),
              ("Chainlink", "b_m"): _g("Chainlink", "b_m", days, [233_150_029.58] * 3, p33)}
    spec = dict(decl["overlap"], a="a_m", b="b_m")
    ho2 = bw.handover_overlap(groups, "Chainlink", spec)
    assert ho2["passed"] and ho2["common_days"] == 3, ho2


def test_d4b_set_overlap_check_reads_both_sets_at_one_block(monkeypatch, capsys):
    import check_offline_items as coi
    monkeypatch.setattr(coi, "_eth_block_at", lambda ts: 26_273_137)
    added = {c["address"].lower() for k, c in config.PROJECT_BY_NAME["Chainlink"]["contracts"].items()
             if k.startswith("noncirc_cl_page_")}

    def call(to, data, block="latest", chain="ethereum"):
        holder = "0x" + data[-40:]
        return (hex(5 * 10 ** 18) if holder in added else hex(10 ** 18)), "stub"
    monkeypatch.setattr(coi, "eth_call", call)
    assert coi.chainlink_noncirc_set_overlap(days=2) == 0
    out = capsys.readouterr().out
    assert "27 wallets 27.000000  + 6 added 30.000000  = 33 wallets 57.000000 LINK  (+111.111111%)" in out
    assert "OUTSIDE ±2%" in out


# ============================================================================================ D5(a) MAPLE TREASURY
def _maple_treasury_groups():
    th = pd.concat([
        _g("Maple", "treasury_holding_tokens", ["2026-09-21", "2026-09-23"], [23_092_603] * 2,
           "chain:ethereum:treasury:PARTIAL"),
        _g("Maple", "treasury_holding_tokens", ["2026-09-24", "2026-10-12", "2026-10-13"], [79.21e6, 78.04e6, 77.66e6],
           "maple_page"),
        _g("Maple", "treasury_holding_tokens", ["2026-09-26"], [23e6], "chain:ethereum:treasury:archive")])
    days = pd.date_range("2026-09-24", "2026-10-20")
    days = [d for d in days if d != pd.Timestamp("2026-10-17")]          # a day the chain read missed
    ch = _g("Maple", "treasury_holding_tokens_chain", days, [42_290_111.33] * len(days),
            "chain:ethereum:treasury:PARTIAL")
    return {("Maple", "treasury_holding_tokens"): th, ("Maple", "treasury_holding_tokens_chain"): ch}


def test_d5a_maple_treasury_hands_over_to_the_dao_safe_from_the_day_after_the_tile(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Maple"]])
    groups = _maple_treasury_groups()
    bw._handover_continuation_views(groups)
    bw._handover_cut_views(groups)
    g = groups[("Maple", "treasury_holding_tokens")].sort_values("date")
    after = g[g["date"] > "2026-10-13"]
    assert list(after["date"].dt.strftime("%m-%d")) == ["10-14", "10-15", "10-16", "10-18", "10-19", "10-20"]  # hole kept
    assert set(after["source"]) == {"derived:=treasury_holding_tokens_chain:PARTIAL"}
    assert not (g["source"] == "chain:ethereum:treasury:archive").any()         # the declared cut still holds
    assert "HANDED OVER from 2026-10-14" in bw.HANDOVER_CONTINUED[("Maple", "treasury_holding_tokens")]
    row = _row(bw, g)
    assert len(row["measuring_points"]) == 3
    assert bw.withheld_for("Maple", "treasury_holding_tokens", row) is None     # not BROKEN
    # never the "127.15M DAO + SSF" figure: the declared next leg is the DAO Safe's own series
    decl = config.declared_handover("Maple", "treasury_holding_tokens")
    assert decl["ordered_points"][-1] == "derived:=treasury_holding_tokens_chain"
    assert "127.15M" in decl["composition_change"] and "not used" in decl["composition_change"]


def test_d5a_the_overlap_compares_the_page_tile_with_the_dao_safe_and_says_they_differ(monkeypatch):
    import build_workbook as bw
    import trajectory
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Maple"]])
    groups = _maple_treasury_groups()
    bw._handover_continuation_views(groups)
    bw._handover_cut_views(groups)
    bw._handover_overlap_checks(groups)
    ho = bw.HANDOVER_OVERLAP[("Maple", "treasury_holding_tokens")]
    assert ho["common_days"] == 3 and not ho["passed"]          # only the page leg's days: 09-24, 10-12, 10-13
    assert ho["tolerance_pct"] == 2.0 and ho["min_days"] == 3
    assert "the page's SYRUP Holdings tile" in ho["text"] and "CHANGE OF QUANTITY" in ho["text"]
    # a different quantity: the Trajectory never reads across the seam
    br = trajectory._own_breaks("Maple", "treasury_holding_tokens", groups, pd.Timestamp("2026-01-01"))
    assert [str(d.date()) for d, *_ in br][-1] == "2026-10-14"


def test_d5a_no_continuation_without_the_page_leg_and_none_before_it_ends(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Maple"]])
    groups = _maple_treasury_groups()
    th = groups[("Maple", "treasury_holding_tokens")]
    groups[("Maple", "treasury_holding_tokens")] = th[th["source"] != "maple_page"]
    before = len(groups[("Maple", "treasury_holding_tokens")])
    bw._handover_continuation_views(groups)
    assert len(groups[("Maple", "treasury_holding_tokens")]) == before
    assert ("Maple", "treasury_holding_tokens") not in bw.HANDOVER_CONTINUED


def test_d5a_the_reader_no_longer_reads_the_tile(monkeypatch, tmp_path):
    from fetch import maple_transparency as mt
    from fetch import scrape
    from fetch.base import FetchOutput
    monkeypatch.setattr(scrape, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))
    tile = "<div>SYRUP Holdings</div><div>127.15M</div>"
    out = FetchOutput()
    mt.MapleTransparency(get=lambda url: tile).run([config.PROJECT_BY_NAME["Maple"]], 30, out)
    f = out.frame()
    assert f.empty or not (f.metric == "treasury_holding_tokens").any()
    msgs = " | ".join(e.message for e in out.log)
    assert "treasury_holding_tokens: not read (P2a D5a" in msgs and "different quantity, not used" in msgs
    assert not any(g["metric"] == "treasury_holding_tokens" for g in out.gaps)


# ============================================================================================ D5(b) MAPLE SSF ON CHAIN
SAFE = "0x58be00490d6a5d298d61f24226d2397983ee39a1"
DAO = "0xd6d4bcde6c816f17889f1dd3000af0261b03a196"
DELIVERY = "0x99f03ca02b232dc124b62b8cf40b2bfd21eae9ca"


def test_d5b_the_ssf_scans_read_the_safe_config_names():
    scans = {s["key"]: s for s in config.PROJECT_BY_NAME["Maple"]["log_scans"] if s["key"].startswith("ssf_safe")}
    assert set(scans) == {"ssf_safe_in", "ssf_safe_out", "ssf_safe_buyback_in"}
    syrup = config.PROJECT_BY_NAME["Maple"]["contracts"]["token"]["address"]
    ssf = config.PROJECT_BY_NAME["Maple"]["contracts"]["noncirc_ssf_safe"]["address"]
    for s in scans.values():
        assert [h.lower() for h in s["holders"]] == [ssf.lower()] == [SAFE] and s["token"] == syrup and s["store"]
        assert config.scanned_from_genesis("Maple", s["metric"])      # from block 0: its zeros are observed
    assert scans["ssf_safe_in"]["count_mints"] and scans["ssf_safe_out"]["direction"] == "out"
    assert [a.lower() for a in scans["ssf_safe_buyback_in"]["count_from"]] == [DELIVERY]
    assert "ssf_net_outflow_tokens" in str(config.RETIRED_SERIES)      # D5-3c stands: the chain route is a new series
    for m in ("ssf_safe_net_flow_monthly_tokens", "ssf_safe_buyback_inflow_monthly_tokens"):
        assert config.series_granularity("Maple", m) == "monthly" and config.METRICS[m]["view_only"]
    assert "NOT the SSF's" in config.METRICS["ssf_safe_net_flow_monthly_tokens"]["label"]


def _ssf_logs():
    """The Safe's whole SYRUP history as read live 2026-10-27 (eth.blockscout.com getLogs, block 26,285,646)."""
    from test_adapters import _flow_logs
    E = 10 ** 18
    ts = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731
    return _flow_logs(SAFE, [(23094229, DAO, 10 * E, ts("2025-08-08 05:22:59")),
                             (23134608, DAO, 76_124_977_585_739_366_617_198_200, ts("2025-08-13 20:46:35"))],
                      [(23684614, DELIVERY, 10 * E, ts("2025-10-29 17:43:11")),
                       (23684664, DELIVERY, 11_999_990 * E, ts("2025-10-29 17:53:23")),
                       (24032725, DELIVERY, 10_000_000 * E, ts("2025-12-17 13:46:47")),
                       (24327162, DELIVERY, 5_500_000 * E, ts("2026-01-27 15:47:35"))])


def test_d5b_the_scans_reconcile_and_the_monthly_view_shows_net_flow_and_buybacks_apart(monkeypatch, tmp_path):
    import build_workbook as bw
    from fetch.base import FetchOutput
    from fetch.explorer import pad_address
    from fetch.logcache import LogCache
    from fetch.logscan import LogScan
    from test_adapters import _FakeReader, _explorer
    logs = _ssf_logs()
    h = pad_address(SAFE)
    bal = sum(int(e["data"], 16) for e in logs[("0xtoken", None, h)]) - \
        sum(int(e["data"], 16) for e in logs[("0xtoken", h, None)])
    assert bal == 48_624_987_585_739_366_617_198_200            # the Safe's SYRUP today (D5-3c: 48,624,987.59)
    monkeypatch.setattr("fetch.logscan.today", lambda: pd.Timestamp("2026-10-27"))
    frames = []
    for s in config.PROJECT_BY_NAME["Maple"]["log_scans"]:
        if s["key"].startswith("ssf_safe"):
            ls = LogScan(explorer=_explorer(monkeypatch, logs, max_records=1000), cache=LogCache(tmp_path / s["key"]),
                         reader=_FakeReader({SAFE: bal}, head=26_285_666))
            out = FetchOutput()
            ls._scan({"name": "Maple"}, dict(s, token="0xtoken"), None, out)
            assert any("RECONCILED to the wei" in e.message for e in out.log), [e.message[:200] for e in out.log]
            frames.append(out.frame())
    f = pd.concat(frames)
    groups = {("Maple", m): g for m, g in f.groupby("metric")}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Maple"]])
    bw._monthly_flow_views(groups)
    net = groups[("Maple", "ssf_safe_net_flow_monthly_tokens")]
    by = {str(d.date()): round(v, 2) for d, v in zip(net["date"], net["value"])}
    assert by["2025-08-31"] == round(76_124_977.585739 + 10, 2)       # funded by the DAO Safe (both transfers)
    assert by["2025-09-30"] == 0.0 and by["2025-10-31"] == -12_000_000.0 and by["2025-12-31"] == -10_000_000.0
    assert by["2026-01-31"] == -5_500_000.0 and by["2026-09-30"] == 0.0 and "2026-10-31" not in by   # month not over
    bb = groups[("Maple", "ssf_safe_buyback_inflow_monthly_tokens")]
    assert set(bb["value"]) == {0.0} and len(bb) == len(net)          # no buyback ever entered the Safe — observed
    assert bw.MONTHLY_FLOW_FORMED[("Maple", "ssf_safe_net_flow_monthly_tokens")]["holes"] == []


def test_d5b_a_day_with_no_row_is_a_hole_and_its_month_is_not_formed(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Maple"]])
    days = [d for d in pd.date_range("2026-03-01", "2026-05-31") if d != pd.Timestamp("2026-04-10")]
    groups = {("Maple", "ssf_safe_inflow_tokens"): _g("Maple", "ssf_safe_inflow_tokens", days, [1.0] * len(days),
                                                      "explorer:ssf_safe_in"),
              ("Maple", "ssf_safe_outflow_tokens"): _g("Maple", "ssf_safe_outflow_tokens", days, [0.5] * len(days),
                                                       "explorer:ssf_safe_out")}
    bw._monthly_flow_views(groups)
    net = groups[("Maple", "ssf_safe_net_flow_monthly_tokens")]
    assert [str(d.date()) for d in net["date"]] == ["2026-03-31", "2026-05-31"]
    assert list(net["value"]) == [15.5, 15.5]
    assert bw.MONTHLY_FLOW_FORMED[("Maple", "ssf_safe_net_flow_monthly_tokens")]["holes"] == ["2026-04 (1 day(s) not held)"]
    assert ("Maple", "ssf_safe_buyback_inflow_monthly_tokens") not in groups      # its leg has no rows: nothing formed
