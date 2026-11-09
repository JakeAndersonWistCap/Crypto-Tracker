"""ROUND P3-3 (Jake 2026-11-02): (a) Chainlink's DefiLlama parent and its children; (b) Uniswap's one-off burn as a
declared static event; (c) the TVL change at start-of-window prices. Fakes only — no network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import reader  # noqa: E402
from fetch import llama  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

D0 = pd.Timestamp("2026-09-01")


def _days(n, start=D0):
    return [start + pd.Timedelta(days=i) for i in range(n)]


def _ts(d):
    return int(pd.Timestamp(d).tz_localize("UTC").timestamp())


# ============================================================================================ (a) parent and children
def _family(n=60, lump_every=7):
    """requests: a daily line; staking: one lump a week (Chainlink's shape); parent = their sum, every day."""
    days = _days(n)
    req = {d: 5_000.0 + i for i, d in enumerate(days)}
    stk = {d: (1_070_000.0 if i % lump_every == 3 else 0.0) for i, d in enumerate(days)}
    return days, req, stk, {d: req[d] + stk[d] for d in days}


def test_a_parent_that_carries_its_children_is_kept():
    _days_, req, stk, parent = _family()
    r = llama.children_reconcile(parent, {"chainlink-requests": req, "chainlink-staking": stk})
    assert r["verdict"] == "carries" and r["carries"]
    assert r["overlap"]["days"] == 60 and r["overlap"]["pct_diff"] == 0.0
    assert r["overlap"]["parent_sum"] == pytest.approx(r["overlap"]["children_sum"])
    assert "0.000%" in llama.reconcile_text(r)


def test_a_children_are_summed_per_day_and_a_missing_child_day_is_refused():
    days, req, stk, parent = _family(20)
    del stk[days[10]]                                  # a live child misses a day: refused, never a 0
    r = llama.children_reconcile(parent, {"a": req, "b": stk})
    got = dict(r["children_rows"])
    assert days[10] not in got and got[days[3]] == req[days[3]] + stk[days[3]]
    assert any(str(days[10].date()) in x for x in r["refused"])


def test_a_overlap_within_2pct_restates_over_2pct_is_a_break():
    days, req, stk, parent = _family(60)
    split = days[40]
    # the parent stops carrying staking at the split (a residual), and before it reads 1.5% above the children
    near = {d: (v * 1.015 if d < split else req[d]) for d, v in parent.items()}
    r = llama.children_reconcile(near, {"r": req, "s": stk}, split_on=split)
    assert r["verdict"] == "restate" and r["overlap"]["days"] == 40
    assert r["overlap"]["pct_diff"] == pytest.approx(0.015 / 1.015, rel=1e-6)
    far = {d: (v * 1.03 if d < split else req[d]) for d, v in parent.items()}
    r2 = llama.children_reconcile(far, {"r": req, "s": stk}, split_on=split)
    assert r2["verdict"] == "break" and r2["overlap"]["pct_diff"] > 0.02
    # undated: the parent no longer carries them over the recent days -> "split" (a human dates it)
    r3 = llama.children_reconcile({d: req[d] for d in days}, {"r": req, "s": stk})
    assert r3["verdict"] == "split" and not r3["carries"]


class _Daily:
    def due(self, *_a):
        return True

    def done(self, *_a):
        return None


def _fake_llama(charts: dict, totals: dict | None = None, kids=()):
    """A DefiLlama with no Http: _summary answers from `charts` ({slug: {day: value}})."""
    d = llama.DefiLlama.__new__(llama.DefiLlama)
    d._memo, d.known_absent, d.daily, d._today, d.stored_long = {}, set(), _Daily(), "2026-11-02", None
    d._history_days = lambda: 455

    def summary(slug, data_type):
        if slug not in charts:
            raise RuntimeError(f"404 {slug}")
        ch = sorted(charts[slug].items())
        return {"totalDataChart": [[_ts(k), v] for k, v in ch],
                "total30d": (totals or {}).get(slug, sum(v for _k, v in ch[-30:])),
                "childProtocols": [{"name": k} for k in kids] if slug == "chainlink" else []}
    d._summary = summary
    return d


def _route_project(split_on=None):
    p = dict(config.PROJECT_BY_NAME["Chainlink"])
    p["defillama_children_route"] = dict(p["defillama_children_route"], split_on=split_on)
    return p


def test_a_route_carries_restates_or_breaks_through_the_adapter():
    days, req, stk, parent = _family(60)
    kids = {"chainlink-requests": req, "chainlink-staking": stk}
    rows = [(d.tz_localize("UTC").to_pydatetime(), v) for d, v in sorted(parent.items())]
    # carries: the parent, as before, under `defillama`
    out = FetchOutput()
    parts = _fake_llama(kids)._children_route(_route_project(), "chainlink", "fees_usd", rows, 30, out)
    assert len(parts) == 1 and parts[0][1] == "defillama" and not out.gaps
    assert any("carries its children" in e.message for e in out.log)
    # a dated split whose overlap reconciles: the children's sum over the WHOLE history, one measuring point
    split = days[40]
    resid = [(d.tz_localize("UTC").to_pydatetime(), (v if d < split else req[d])) for d, v in sorted(parent.items())]
    out = FetchOutput()
    parts = _fake_llama(kids)._children_route(_route_project(str(split.date())), "chainlink", "fees_usd", resid, 30, out)
    assert len(parts) == 1 and parts[0][1] == "defillama:sum(chainlink-requests+chainlink-staking)"
    assert parts[0][2] is None and len(parts[0][0]) == 60 and "RESTATED" in parts[0][3]
    # a dated split whose overlap does NOT reconcile: the parent's history kept, the children's sum from the split,
    # and a P2 [data] row naming the series break
    off = [(d.tz_localize("UTC").to_pydatetime(), (v * 1.05 if d < split else req[d])) for d, v in sorted(parent.items())]
    out = FetchOutput()
    parts = _fake_llama(kids)._children_route(_route_project(str(split.date())), "chainlink", "fees_usd", off, 30, out)
    assert [p[1] for p in parts] == ["defillama", "defillama:sum(chainlink-requests+chainlink-staking)"]
    assert all(d < split for d, _v in parts[0][0]) and all(d >= split for d, _v in parts[1][0])
    assert out.gaps and "SERIES BREAK" in out.gaps[0]["metric"] and out.gaps[0]["metric"].startswith("[data]")
    # a child that does not answer: the parent as before, the check said not made
    out = FetchOutput()
    parts = _fake_llama({"chainlink-requests": req})._children_route(_route_project(), "chainlink", "fees_usd", rows,
                                                                     30, out)
    assert parts[0][1] == "defillama" and any("NOT MADE" in e.message for e in out.log)


def test_a_generic_restructure_check_is_not_fooled_by_a_weekly_lump():
    """Jake's Gap Report P2: on a day whose chart ends before that week's staking lump is indexed, the last 7 days hold
    no lump. The parent still equals Σ children every day, so it is no restructure."""
    days, req, stk, parent = _family(60)
    # the chart ends ON a lump day, with the lump not yet indexed (0) on both the child and the parent
    end = [d for d in days if stk[d] > 0][-1]
    keep = [d for d in days if d <= end]
    stk2 = {d: (0.0 if d == end else stk[d]) for d in keep}
    req2 = {d: req[d] for d in keep}
    par2 = {d: req2[d] + stk2[d] for d in keep}
    totals = {"chainlink": sum(par2[d] for d in keep[-30:]),
              "chainlink-requests": sum(req2[d] for d in keep[-30:]),
              "chainlink-staking": sum(stk2[d] for d in keep[-30:])}
    recent = sum(v for d, v in par2.items() if (end - d).days < 7)
    assert recent * llama.RESTRUCTURE_FACTOR < totals["chainlink"] / 30 * 7      # the old discriminator fires
    p = {"name": "Chainlink", "defillama_fees_slug": "chainlink"}
    out = FetchOutput()
    _fake_llama({"chainlink": par2, "chainlink-requests": req2, "chainlink-staking": stk2}, totals,
                kids=("Chainlink Requests", "Chainlink Staking")).check_restructure(p, out)
    assert not out.gaps and not out.review
    # a parent whose daily chart really stopped carrying a child is still reported
    par3 = {d: req2[d] for d in keep}
    out = FetchOutput()
    full = {d: stk[d] for d in keep}                    # the child reports its lump; the parent does not carry it
    _fake_llama({"chainlink": par3, "chainlink-requests": req2, "chainlink-staking": full},
                {"chainlink": sum(req2[d] + full[d] for d in keep[-30:]),
                 "chainlink-requests": totals["chainlink-requests"],
                 "chainlink-staking": sum(full[d] for d in keep[-30:])},
                kids=("Chainlink Requests", "Chainlink Staking")).check_restructure(p, out)
    assert out.gaps and "restructured" in out.gaps[0]["metric"]


def test_a_chainlink_route_declared_with_its_live_finding():
    r = config.PROJECT_BY_NAME["Chainlink"]["defillama_children_route"]
    assert r["metric"] == "fees_usd" and r["children"] == ("chainlink-requests", "chainlink-staking")
    assert r["tolerance"] == 0.02 and r["split_on"] is None and r["checked_on"] == "2026-11-02"
    assert "0.000%" in r["live_finding"]


# ============================================================================================ (b) Uniswap's one-off
TX = "0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e"


def test_b_one_off_is_a_declared_static_event_with_its_source():
    n = config.one_off_event_note("Uniswap", f"tx:{TX} (block 24106378, UNIfication retroactive treasury burn)")
    assert n.startswith("DECLARED STATIC EVENT") and TX in n and "24,106,378" in n
    assert "vote.uniswapfoundation.org/proposals/93" in n and "never stale" in n
    view = config.one_off_event_note(
        "Uniswap", "one_off:UNIfication retroactive treasury burn (100,000,000 UNI) "
                   "(https://vote.uniswapfoundation.org/proposals/93)")
    assert view and "--seed uni_retro_burn" in view and TX in view
    assert config.one_off_event_note("Uniswap", "chain:ethereum:burn_dead") is None
    assert config.one_off_event_note("Sky", f"tx:{TX}") is None


@pytest.mark.parametrize("source", [f"tx:{TX} (block 24106378, UNIfication retroactive treasury burn (100,000,000 UNI))",
                                    "one_off:UNIfication retroactive treasury burn (100,000,000 UNI) "
                                    "(https://vote.uniswapfoundation.org/proposals/93)"])
def test_b_the_data_row_reads_the_value_with_its_source_not_never(monkeypatch, source):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Uniswap"]])
    long = pd.DataFrame([{"date": pd.Timestamp("2025-12-27"), "project": "Uniswap", "metric": "one_off_burn_tokens",
                          "value": 100_000_000.0, "source": source, "tier": 1, "fetched_at": "2026-11-02T00:00:00",
                          "is_manual": 0, "entered_on": None, "source_note": None}])
    data = bw.aggregate(long, pd.DataFrame(), pd.Timestamp("2026-11-02"))
    row = data[data["key"] == "Uniswap|one_off_burn_tokens"].iloc[0]
    assert row["status"] == "ok", (row["status"], row["note"])
    assert "fetch never" not in str(row["note"]) and str(row["note"]).startswith("DECLARED STATIC EVENT")
    assert TX in str(row["note"])


# ============================================================================================ (c) start-of-window prices
def _breakdown(days, amounts: dict, prices: dict) -> dict:
    """DefiLlama's /protocol shape: tokens (amounts) and tokensInUsd per day; prices[t][i] None = no price that day."""
    tok, usd = [], []
    for i, d in enumerate(days):
        ts = _ts(d)
        a = {t: v[i] for t, v in amounts.items() if v[i] is not None}
        u = {t: a[t] * prices[t][i] for t in a if prices[t][i] is not None}
        tok.append({"date": ts, "tokens": a})
        usd.append({"date": ts, "tokens": u})
    return {"tokens": tok, "tokensInUsd": usd}


def _series(rows):
    return pd.Series({pd.Timestamp(d).tz_convert(None).normalize(): v for d, v in rows})


def test_c_price_moves_cancel_and_amount_moves_count():
    days = _days(31)
    # constant amounts, prices doubling: the nominal TVL doubles, the constant-price change is 0
    j = _breakdown(days, {"ETH": [10.0] * 31, "USDC": [1000.0] * 31},
                   {"ETH": [100.0 * (1 + i / 30) for i in range(31)], "USDC": [1.0] * 31})
    ratio, cover, _note = llama.window_start_price_tvl(j)
    r, c = _series(ratio), _series(cover)
    v, cv, _why = reader.window_start_change(r, c, 30)
    assert v == pytest.approx(0.0) and cv == pytest.approx(1.0)
    # amounts +10% on ETH only, prices moving: the change is the amount move at START prices
    j = _breakdown(days, {"ETH": [10.0 + i / 30 for i in range(31)], "USDC": [1000.0] * 31},
                   {"ETH": [100.0 * (1 + i / 30) for i in range(31)], "USDC": [1.0] * 31})
    r, c = (_series(x) for x in llama.window_start_price_tvl(j)[:2])
    v, _cv, _why = reader.window_start_change(r, c, 30)
    assert v == pytest.approx((11 * 100 + 1000) / (10 * 100 + 1000) - 1)          # 5.0%: amounts at day-0 prices
    v7, _c7, _w = reader.window_start_change(r, c, 7)
    p0 = 100.0 * (1 + 23 / 30)
    assert v7 == pytest.approx((11 * p0 + 1000) / ((10 + 23 / 30) * p0 + 1000) - 1)


def test_c_an_unpriced_token_is_excluded_and_the_coverage_floor_gives_a_code():
    days = _days(31)
    # NEW holds 5% of the latest TVL and had no price 30 days ago: excluded, coverage 95% (above the floor)
    j = _breakdown(days, {"ETH": [10.0] * 31, "NEW": [0.0] * 20 + [50.0] * 11},
                   {"ETH": [100.0] * 31, "NEW": [None] * 20 + [1.0] * 11})
    r, c = (_series(x) for x in llama.window_start_price_tvl(j)[:2])
    v, cv, _why = reader.window_start_change(r, c, 30)
    assert v == pytest.approx(0.0) and cv == pytest.approx(1000 / 1050)
    # NEW at 20% of the latest TVL: under the 90% floor -> n/a with the coverage in the reason
    j = _breakdown(days, {"ETH": [10.0] * 31, "NEW": [0.0] * 20 + [250.0] * 11},
                   {"ETH": [100.0] * 31, "NEW": [None] * 20 + [1.0] * 11})
    r, c = (_series(x) for x in llama.window_start_price_tvl(j)[:2])
    v, cv, why = reader.window_start_change(r, c, 30)
    assert v is None and cv == pytest.approx(0.8) and "80.0%" in why and "floor 90%" in why
    assert reader.TVL_CP_COVERAGE_FLOOR == 0.90
    # a day with no priced TVL has no row at all (unknown, never 0)
    j = _breakdown(days, {"ETH": [10.0] * 31}, {"ETH": [None] * 5 + [100.0] * 26})
    ratio, _cover, _n = llama.window_start_price_tvl(j)
    assert len(ratio) == 26 and all(v > 0 for _d, v in ratio)


def test_c_an_intraday_row_is_one_day_with_the_midnight_row():
    days = _days(3)
    j = _breakdown(days, {"ETH": [10.0, 11.0, 12.0]}, {"ETH": [100.0, 100.0, 100.0]})
    j["tokens"].append({"date": _ts(days[-1]) + 29_000, "tokens": {"ETH": 13.0}})
    j["tokensInUsd"].append({"date": _ts(days[-1]) + 29_000, "tokens": {"ETH": 1300.0}})
    ratio, _c, _n = llama.window_start_price_tvl(j)
    assert len(ratio) == 3 and ratio[0][1] == pytest.approx(13 / 10)


def test_c_business_column_header_and_formula_on_series(tmp_path):
    """The new column sits beside 'TVL change, selected period', ≤35 characters, a formula on _series with the floor."""
    assert len(reader.TVL_SW_HEADER) <= reader.MAX_HEADER
    import openpyxl

    from types import SimpleNamespace
    wb = openpyxl.Workbook()
    ser = reader.SeriesSheet(wb.create_sheet("_series"), pd.Timestamp("2026-11-02"))
    days = _days(31, pd.Timestamp("2026-10-02"))
    j = _breakdown(days, {"ETH": [10.0 + i / 30 for i in range(31)]}, {"ETH": [100.0] * 31})
    ratio, cover, _n = llama.window_start_price_tvl(j)
    src = f"defillama:tokens_at_window_start[basket {days[-1].date()}]"
    groups = {("Fluid", m): pd.DataFrame({"date": [pd.Timestamp(d).tz_convert(None).normalize() for d, _v in rows],
                                          "value": [v for _d, v in rows], "source": src})
              for m, rows in zip(reader.TVL_SW_METRICS, (ratio, cover))}
    col = reader.Col(reader.TVL_SW_HEADER, "", reader.FMT_PCT, letter="B")
    sh = reader.Sheet(wb.active)
    sh.cols = [reader.Col("Project", ""), col]
    A = SimpleNamespace(data_row=lambda k: None)
    ctx = SimpleNamespace(A=A, ser=ser, groups=groups, config=config)
    coord = reader._window_start_tvl_cell(sh, ctx, 5, col, config.PROJECT_BY_NAME["Fluid"])
    f = sh.ws[coord].value
    assert f.startswith("=IFERROR(") and "_series!" in f and f"<{reader.TVL_CP_COVERAGE_FLOOR}" in f
    assert "30D: +10.0%" in sh.ws[coord].comment.text
    assert "under 90% of the latest TVL" in sh.ws[coord].comment.text
    # a chain reads "by design"; Uniswap, whose breakdown DefiLlama does not publish, n/a with the dated reason
    # (PR1B.11, config.TVL_BREAKDOWN_UNPUBLISHED; was "not wired")
    c1 = reader._window_start_tvl_cell(sh, ctx, 6, col, config.PROJECT_BY_NAME["Ethereum"])
    c2 = reader._window_start_tvl_cell(sh, ctx, 7, col, config.PROJECT_BY_NAME["Uniswap"])
    assert sh.ws[c1].value == "by design" and sh.ws[c2].value == "n/a" and "read 2026-11-09" in sh.ws[c2].comment.text


def test_c_metrics_declared_for_the_four_protocols_not_the_parked_one():
    for m in reader.TVL_SW_METRICS:
        assert config.METRICS[m]["only_projects"] == ("Maple", "Morpho", "Pendle", "Fluid")
    assert "protocol_tvl_cp_window_ratio" not in config.metrics_for_project(config.PROJECT_BY_NAME["Aerodrome"])
