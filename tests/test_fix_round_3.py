"""
tests/test_fix_round_3.py — fix round 3 (Jake 2026-10-13): each mechanism on REAL data — the dumps (fixtures/real_*.json)
plus the real API responses captured on 2026-10-13 for the series the dumps predate (fixtures/api_hl_ledgers.json,
api_sky_pnl.json, api_chainlink_staking_path.json, api_chainlink_reward_vault.json, api_merkl_morpho.json,
api_boros_market2.csv, api_etherfi_cashback.json, api_llama_pendle_tokens.json).

  B1   Hyperliquid's issuer-side wallets replayed from their own ledgers: end levels = live state; the team's releases;
       the build: (e) unlocks, NHY and Total Yield numeric, the modelled rewards history labelled MODELLED
  Q2   Sky's payout-type ratios on revenue BEFORE distributions; the implied buyback stays on NPS
  B7   the corrected reference rows grade VERIFIED
  B2   Chainlink: the staking part of each release, FIFO through 0xD50f074E…, out of (e); the community-pool rate
  B3   Morpho: Merkl campaigns PAID off Ethereum (the Arc campaign pays on Ethereum and is not counted twice)
  B4   Aerodrome: the weekly rows dated by the flip that paid them; the team emission on its own flip's rate
  B5   NEAR: the two bridged outflows named on their days
  B6   W1 TVL at one date's prices; W2 Boros $ volume; W3 the cashback its purchases did not cover; W4–W7 config
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from test_fix_round_2 import _groups  # noqa: E402

FX = ROOT / "fixtures"


def _fx(name: str):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def _rows(metric: str, pts, source: str, tier: int = 2) -> list[dict]:
    return [{"date": str(pd.Timestamp(d).date()), "metric": metric, "value": float(v), "source": source, "tier": tier,
             "fetched_at": "2026-10-13T20:00:00"} for d, v in pts]


# --------------------------------------------------------------------------------------------------------- B1 replay
def _hl_replays():
    from fetch import hl_ledger as h
    d = _fx("api_hl_ledgers.json")
    spec = config.PROJECT_BY_NAME["Hyperliquid"]["hl_ledger_replay"]
    pairs, internal = h.hype_pairs(d["spotMeta_hype"]), set(spec["wallets"].values())
    reps = {n: h.replay_wallet(w["address"], w["userNonFundingLedgerUpdates"], w["delegatorHistory"],
                               w["delegatorRewards"], w["fills"], pairs, internal) for n, w in d["wallets"].items()}
    return d, spec, reps


def test_hl_replay_end_levels_equal_the_live_state():
    d, _spec, reps = _hl_replays()
    for n, w in d["wallets"].items():
        spot = sum(float(b.get("total") or 0) for b in w["spotClearinghouseState"].get("balances") or ()
                   if b.get("coin") == "HYPE")
        ds = w["delegatorSummary"]
        live = spot + sum(float(ds.get(k) or 0) for k in ("delegated", "undelegated", "totalPendingWithdrawal"))
        assert abs(float(sum(reps[n]["end"])) - live) < 1e-6, n


def _hl_rows():
    from fetch import hl_ledger as h
    _d, spec, reps = _hl_replays()
    fr = h.daily(reps, dt.date(2025, 7, 10), dt.date(2026, 10, 13))
    out = []
    for m, pts in h.series(fr, spec).items():
        out += _rows(m, pts, h.SOURCE, 1)
    return out


def test_hl_team_releases_are_the_ledgers_own():
    rows = pd.DataFrame(_hl_rows())
    rel = rows[rows.metric == "issuer_release_replay_tokens"].set_index("date")["value"]
    assert rel["2026-10-07"] == pytest.approx(3_750_000, abs=1)
    assert rel["2025-11-29"] == pytest.approx(1_745_746, abs=1)
    team = rows[rows.metric == "team_wallet_tokens_replay"].set_index("date")["value"]
    assert team["2026-10-12"] == pytest.approx(237_915_860.467, abs=0.01)


def test_hl_build_unblocks_unlocks_and_labels_the_model():
    g = _groups("Hyperliquid", _hl_rows())
    unl = g[("Hyperliquid", "unlock_release_tokens")]
    q0 = unl[(pd.to_datetime(unl.date) > "2026-07-15") & (pd.to_datetime(unl.date) <= "2026-10-13")]
    assert q0.value.sum() > 4_000_000
    em = g[("Hyperliquid", "emissions_tokens")]
    assert em.source.astype(str).str.startswith("modelled:hl_reference_yield").any()
    assert config.is_modelled("modelled:hl_reference_yield")
    assert ("Hyperliquid", "future_emissions_modelled_tokens") in g


def test_hl_burn_total_keeps_the_funds_own_reads_and_covers_q0():
    """Round 3 review H1 (Jake 2026-10-14): the total's history leg kept only the DefiLlama rows (to 2026-09-11), so the
    Fund's own reads 2026-09-12..09-28 fell out — Q0 2,146,959 HYPE over 18 'covered' days read -18.25% net supply and
    put non-stakers' Total Yield at +9.0% against NHY -6.40%. The leg is now continuous and covers all 90 days."""
    import build_workbook as bw
    g = _groups("Hyperliquid", _hl_rows())
    tb = g[("Hyperliquid", "total_burn_tokens")].drop_duplicates("date", keep="last")
    s = tb.set_index(pd.to_datetime(tb.date))["value"].astype(float)
    asof = pd.Timestamp("2026-10-13")
    hole = s[(s.index >= "2026-09-12") & (s.index <= "2026-09-28")]
    assert len(hole) >= 10 and hole.sum() > 300_000         # the Fund's spotClearinghouseState reads, kept
    q0 = s[(s.index > asof - pd.Timedelta(days=90)) & (s.index <= asof)].sum()
    assert q0 == pytest.approx(2_467_817.2, abs=1)
    s_cov = bw._after_leg_gap(tb, s)
    assert bw._window_coverage(s_cov, asof - pd.Timedelta(days=90), asof)[0] == 90
    # the retirement above DefiLlama's revenue_usd it exposes (114%) is the Fund's documented intake, not a gap
    f = config.PROJECT_BY_NAME["Hyperliquid"]["buyback_funding"]
    assert "hyperliquid-docs/trading/fees" in f["source_url"] and f["source_date"] == "2026-10-14"


# ------------------------------------------------------------------------------------------------------------ Q2 Sky
def _sky_rows():
    from fetch.sky_accounting import nps_candidates
    d = _fx("api_sky_pnl.json")
    c = nps_candidates(d["pnl"]["data"], d["sm"]["data"])["revenue-expense"]
    return _rows("net_revenue_pre_distribution_usd",
                 [(pd.Period(m, "M").to_timestamp("M"), v) for m, v in c.items() if m < "2026-10"],
                 "sky_accounting:pnl.revenue-expense", 3)


def test_sky_ratio_base_is_before_distributions_and_the_implied_buyback_stays_on_nps():
    assert config.revenue_ratio_base_metric("Sky") == "net_revenue_pre_distribution_usd"
    assert config.revenue_base_metric("Sky") == "net_protocol_surplus_usd"
    assert config.revenue_ratio_base_metric("Near") == config.revenue_base_metric("Near") == "revenue_total_usd"
    sep = {r["date"]: r["value"] for r in _sky_rows()}["2026-09-30"]
    assert sep == pytest.approx(15_379_185, abs=1)          # revenue − expense; NPS (after distribution) 7,153,241


def test_sky_reference_reads_the_pre_distribution_series():
    import references as rf
    g = _groups("Sky", _sky_rows())
    r = {x["metric"]: x for x in rf.check(g)}["net_revenue_sep_2026"]
    assert r["ours"] == pytest.approx(15_379_185, abs=1)
    assert "TMF series does not sit on one Block Analitica basis" in r["detail"]


# ------------------------------------------------------------------------------------------------------- B7 references
def test_corrected_reference_rows_verify():
    import references as rf
    uni = {r["metric"]: r for r in rf.check(_groups("Uniswap"))}
    assert uni["franchiser_recall_inflow"]["status"] == "VERIFIED"
    cl = {r["metric"]: r for r in rf.check(_groups("Chainlink"))}
    assert cl["reserve_holdings_2026-04-15"]["status"] == cl["reserve_holdings_end_q1_2026"]["status"] == "VERIFIED"


# ------------------------------------------------------------------------------------------------------- B2 Chainlink
def _cl_path_rows():
    P = config.CL_STAKING_PATH
    t = _fx("api_chainlink_staking_path.json")["transfers"]
    lump, fwd = P["lump_wallet"].lower(), P["forwarder"].lower()
    hops = {h.lower() for h in P["staking_hops"]}
    nc = {c["address"].lower() for c in config.PROJECT_BY_NAME["Chainlink"]["contracts"].values()
          if c.get("metric_override") == "noncirculating_holding_tokens" and c.get("chain") == "ethereum"}
    acc = {m: {} for m in ("staking_path_in_tokens", "staking_path_out_tokens", "staking_funding_tokens")}

    def add(m, ts, v):
        acc[m][ts[:10]] = acc[m].get(ts[:10], 0.0) + v
    for x in t[P["lump_wallet"]]:
        v = int(x["value"]) / 1e18
        if x["to"] == lump and x["from"] in nc:
            add("staking_path_in_tokens", x["ts"], v)
        elif x["from"] == lump:
            add("staking_path_out_tokens", x["ts"], v)
    for x in t[P["forwarder"]]:
        if x["from"] == fwd and x["to"] in hops:
            add("staking_funding_tokens", x["ts"], int(x["value"]) / 1e18)
    out = []
    for m, a in acc.items():
        out += _rows(m, sorted(a.items()), "logscan:" + m)
    return out


def test_chainlink_staking_part_of_each_release_comes_out_of_e_fifo():
    rows = _cl_path_rows()
    fund = sum(r["value"] for r in rows if r["metric"] == "staking_funding_tokens")
    assert fund == pytest.approx(6_682_500 + 1_445_000, abs=1)       # the two staking hops, all time
    base, g = _groups("Chainlink"), _groups("Chainlink", rows)
    split = g[("Chainlink", "staking_funding_release_tokens")].set_index("date")["value"]
    assert split.sum() == pytest.approx(327_500, abs=1)               # FIFO: the 2025-12-19 lump, within the dump
    b, a = (x[("Chainlink", "unlock_release_tokens")].value.sum() for x in (base, g))
    assert b - a == pytest.approx(327_500, abs=1)
    assert "staking_funding_release_tokens" in str(g[("Chainlink", "unlock_release_tokens")].source.iloc[-1])
    # P1 (round 3 review): FIFO is labelled an attribution convention, ACCEPTED, on both series
    for m in ("unlock_release_tokens", "staking_funding_release_tokens"):
        assert "ATTRIBUTION CONVENTION: FIFO" in str(g[("Chainlink", m)].source.iloc[-1]) and "ACCEPTED" in str(
            g[("Chainlink", m)].source.iloc[-1])


def test_chainlink_community_pool_rate_from_the_vault():
    from fetch.reward_vault import community_rate, decode_buckets
    d = _fx("api_chainlink_reward_vault.json")
    assert d["getStakingPools"].lower().endswith(config.PROJECT_BY_NAME["Chainlink"]["reward_vault_rates"]
                                                 ["community_pool"].lower()[2:])
    rate = community_rate(decode_buckets(d["getRewardBuckets"]), int(d["community_getTotalPrincipal"], 16),
                          d["timestamp"])
    assert rate == pytest.approx(0.0432, abs=0.0005)


# ------------------------------------------------------------------------------------------------------ B3 Morpho
def _morpho_rows():
    from fetch import merkl_campaigns as mc
    fx = _fx("api_merkl_morpho.json")
    spec = config.PROJECT_BY_NAME["Morpho"]["merkl_campaigns"]
    tot, seen = {}, set()
    for cid, ch in spec["chains"].items():
        per, _ = mc.daily_amounts(fx["chains"][str(cid)], ch["token"], dt.date(2026, 10, 13), int(cid), seen)
        for d, v in per.items():
            tot[d] = tot.get(d, 0.0) + v
    d0, pts = min(tot), []
    while d0 < dt.date(2026, 10, 13):
        pts.append((d0, tot.get(d0, 0.0)))
        d0 += dt.timedelta(days=1)
    return _rows(spec["metric"], pts, "merkl_campaigns", 1)


def test_merkl_counts_where_a_campaign_pays_not_where_it_computes():
    from fetch import merkl_campaigns as mc
    fx = _fx("api_merkl_morpho.json")
    arc = fx["arc_not_declared"]
    assert arc and all(c["distributionChainId"] == 1 for c in arc)
    per, skipped = mc.daily_amounts(arc, "0x58D97B57BB95320F9a05dC918Aef65434969c2B2", dt.date(2026, 10, 13), 5042)
    assert not per and "distributed on chain 1" in skipped[0][1]


def test_morpho_b_against_the_merkl_reference():
    import references as rf
    g = _groups("Morpho", _morpho_rows())
    r = {x["metric"]: x for x in rf.check(g)}["merkl_rewards_rate"]
    assert r["status"] == "VERIFIED" and 6_500 < r["ours"] < 8_500


# --------------------------------------------------------------------------------------------------- B4 Aerodrome
def test_aerodrome_weekly_rows_sit_on_the_flip_that_paid_them():
    g = _groups("Aerodrome")
    gi = g[("Aerodrome", "gross_issuance_tokens")].set_index("date")["value"]
    gi.index = pd.to_datetime(gi.index)
    assert gi[pd.Timestamp("2026-10-08")] == pytest.approx(4_184_974.45, abs=1)   # the Voter transfer of 10-08
    assert pd.Timestamp("2026-10-15") not in gi.index                              # the epoch still in progress
    im = g[("Aerodrome", "issuer_mint_tokens")].set_index("date")["value"]
    im.index = pd.to_datetime(im.index)
    assert im[pd.Timestamp("2026-10-08")] == pytest.approx(220_689.5, rel=1e-4)    # on-chain team transfer
    assert im[pd.Timestamp("2025-10-09")] == 0                                     # teamRate 0 from the 10-09 flip
    assert "flip_offset_days" not in config.ISSUER_MINTS["Aerodrome"]


# ------------------------------------------------------------------------------------------------------------ B5 NEAR
def test_near_bridged_outflows_are_recorded_and_named():
    out = {o["date"]: o for o in config.NEAR_REVENUE_OUTFLOWS}
    assert out["2026-04-22"]["tokens"] == 45_010 and out["2026-07-10"]["tokens"] == 12_000
    assert all("not retired" in o["class"] for o in out.values())
    g = _groups("Near")
    b = g[("Near", "actual_buyback_tokens")]
    named = b[b.source.astype(str).str.contains("bridged out")]
    assert all(v < 0 for v in named.value)


# --------------------------------------------------------------------------------------------------------------- B6
def test_w1_tvl_at_one_dates_prices():
    from fetch.llama import constant_price_tvl
    j = _fx("api_llama_pendle_tokens.json")
    rows, note = constant_price_tvl(j)
    last_usd = sum(v for v in j["tokensInUsd"][-1]["tokens"].values() if v)
    assert rows[-1][1] == pytest.approx(last_usd, rel=1e-9)                         # the latest day is itself
    assert "priced at" in note
    assert "protocol_tvl_constant_price_usd" in config.BUSINESS_SLOTS["Pendle"]
    assert "eth_staked_etherfi_tokens" in config.BUSINESS_SLOTS["Ether.fi"]


def test_w2_boros_units_are_the_collateral():
    from fetch.boros import collateral_pricing
    d = _fx("api_boros_meta.json")
    assets = {int(a["tokenId"]): a for a in d["assets"] if a.get("tokenId") is not None}
    by_id = {m["marketId"]: m for m in d["markets"]}
    assert collateral_pricing(by_id[2], assets) == "row"        # WBTC collateral, BTC underlying: Boros's own price
    assert collateral_pricing(by_id[161], assets) == "par"      # USD₮0 collateral on a BTC market: units are dollars


def test_w2_boros_dollar_volume_is_yield_units_times_borors_own_price():
    from fetch.boros import daily_usd
    with open(FX / "api_boros_market2.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    per, unpriced = daily_usd(rows, dt.date(2025, 8, 13), dt.date(2025, 8, 13))
    assert per[dt.date(2025, 8, 13)] == pytest.approx(129.44898787684116 * 120134.09, rel=1e-9)
    assert unpriced == 0


def test_w3_only_the_cashback_purchases_did_not_cover_is_dilution():
    d = _fx("api_etherfi_cashback.json")
    paid = _rows("cashback_paid_tokens", d["paid_daily"].items(), "logscan:cashback_out_optimism")
    bought = _rows("programmatic_buyback_tokens", [(x["date"], x["ethfi_bought"]) for x in d["cow_buys_daily"]],
                   "logscan:programmatic_buyback_inflow")
    days = pd.date_range("2026-09-01", "2026-10-12")
    have = {r["date"] for r in bought}
    bought += _rows("programmatic_buyback_tokens", [(x, 0.0) for x in days if str(x.date()) not in have],
                    "logscan:programmatic_buyback_inflow")
    have = {r["date"] for r in paid}
    paid += _rows("cashback_paid_tokens", [(x, 0.0) for x in days if str(x.date()) not in have],
                  "logscan:cashback_out_optimism")
    g = _groups("Ether.fi", paid + bought)
    unf = g[("Ether.fi", "cashback_unfunded_tokens")]
    assert sum(r["value"] for r in paid) == pytest.approx(1_378_243.74, abs=1)
    assert 0 < unf.value.sum() < 1_000                                              # 404 ETHFI ahead of the buys
    assert ("Ether.fi", "outflow_c_tokens") in g


def test_w4_to_w7_are_wired():
    assert config.outflow_metrics("Maple", "b") == ("drips_rewards_tokens",)
    assert any(s["key"] == "drips_out" for s in config.PROJECT_BY_NAME["Maple"]["log_scans"])
    assert config.LEAKAGE_III["Sky"]["spend"] == "buyback_spending_usd_ba"
    for n in ("Ether.fi", "Maple", "Pendle"):
        assert n not in config.INSIDER_LOCK                      # review W6: no insider identified -> NOT WIRED
        assert config.INSIDER_SHARE_MONITOR[n]["numerator"] == "insider_stake_shares"
        assert any(k.startswith("insider_stake_") for k in config.PROJECT_BY_NAME[n]["contracts"])
    assert config.RELEASE_LESS["Pendle"] == ("issuer_to_gauges_tokens",)


def test_w7_pendle_gauge_transfers_come_out_of_e():
    rows = _rows("issuer_to_gauges_tokens", [("2025-12-23", 100_000), ("2026-02-06", 50_000)],
                 "logscan:issuer_to_gauges")
    base, g = _groups("Pendle"), _groups("Pendle", rows)
    b = base[("Pendle", "unlock_release_tokens")].set_index("date")["value"]
    a = g[("Pendle", "unlock_release_tokens")].set_index("date")["value"]
    # each transfer comes out of its day's release (or the next stored day's), never below 0: on 2025-12-23 the stock
    # fell 99,924.66 net of an inflow the same day, so 75.34 of the 100,000 had no release left to come out of
    assert 149_900 < b.sum() - a.sum() <= 150_000
    assert (a >= 0).all()
    assert "issuer_to_gauges_tokens" in str(g[("Pendle", "unlock_release_tokens")].source.iloc[-1])


# ------------------------------------------------------------------------------------- BUG (Jake's run): llama 6840 pts
def test_llama_price_history_is_asked_in_calls_under_the_500_point_cap(tmp_path):
    """The first read of the 456-day window used to ask every coin in one URL: 15 x 456 = 6,840 points, HTTP 400 for all
    15 projects (the price cross-check and the Q-4 price history were blind). Each call now stays at or under 500."""
    from fetch import xref
    from fetch.base import FetchOutput
    from fetch.logcache import DailyChecks
    calls = []

    class Fake:
        def get(self, url, params=None, **_):
            keys = url.rsplit("/", 1)[1].split(",")
            calls.append((keys, params["span"]))
            start = int(params["start"])
            return {"coins": {k: {"prices": [{"timestamp": start + 86_400 * i, "price": 1.0 + i}
                                             for i in range(params["span"])]} for k in keys}}
    cr = xref.CrossRefs(http=Fake(), daily=DailyChecks(tmp_path), robots=lambda u: (True, "ok"))
    out = FetchOutput()
    cr._llama(list(config.PROJECTS), {"days": 30}, "2026-10-13", out)
    assert calls and all(len(k) * span <= xref.LLAMA_MAX_POINTS for k, span in calls)
    assert sorted(k for ks, _ in calls for k in ks) == sorted({cr.llama_key(p)[0] for p in config.PROJECTS})
    got = pd.concat(out.frames)
    assert set(got.project) == {p["name"] for p in config.PROJECTS} and set(got.metric) == {"price_usd_llama"}
    assert not [e for e in out.log if e.status == "failed"]


def test_w4_maple_drips_form_outflow_b_on_the_real_series():
    d = _fx("api_maple_drips_daily.json")["daily"]
    paid = {r["date"]: r["syrup_out"] for r in d}
    days = pd.date_range("2025-07-01", "2026-10-12")
    rows = _rows("drips_rewards_tokens", [(x, paid.get(str(x.date()), 0.0)) for x in days], "logscan:drips_out")
    g = _groups("Maple", rows)
    b = g[("Maple", "outflow_b_tokens")]
    assert b.value.sum() == pytest.approx(4_097_369.65, abs=1)
    assert b[pd.to_datetime(b.date) > "2026-02-18"].value.sum() == 0          # Drips ended: the last payout 02-18


# ---------------------------------------------------------------------------------------------------- V2 the guard
def test_a_stated_zero_is_a_value_not_a_lost_headline():
    """Jake's round-2 run: Uniswap A4 and Plume A1 gross issuance Q0 flagged lost ("0 last run, now 0") — the cell
    reads "0 — fixed supply: …", which is a value with its reason."""
    import openpyxl
    import sanity
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A4 Permanent Burn"
    ws["A1"] = "0 — fixed supply: UNI mints nothing (the 2% inflation switch has never been used)"
    ws["A2"] = "n/a — gross_issuance_tokens: no rows stored"
    cells = [{"project": "Uniswap", "sheet": "A4 Permanent Burn", "header": "Gross issuance Q0", "cell": c}
             for c in ("A1", "A2")]
    prev = {"Uniswap": {"A4 | Gross issuance Q0": 0.0}}
    assert sanity.stated_number(ws["A1"].value) == 0.0 and sanity.stated_number("1,250 — x") == 1250.0
    assert sanity.lost_headlines(wb, prev, cells[:1], "2026-10-12") == []
    hits = sanity.lost_headlines(wb, prev, cells[1:], "2026-10-12")
    assert len(hits) == 1 and hits[0][1] == "headline_lost"


# ------------------------------------------------------------------------------------------------- V1 unequal coverage
@pytest.mark.parametrize("project", ["Fluid", "Pendle"])
def test_subsidy_cover_on_an_incomplete_denominator_is_blocked(tmp_path, monkeypatch, project):
    import xlcalc
    from test_overnight_ab5f2a9 import _get, _row
    from test_trajectory import _built
    wb, _ = _built(tmp_path, monkeypatch, project)
    o = _row(wb, xlcalc.Workbook(wb), "CP Comparability", project)
    cover = _get(o, "SUBSIDY COVER")
    assert str(cover).startswith("BLOCKED — unequal coverage: the incentive denominator is incomplete"), cover
    assert config.OUTFLOW_INCOMPLETE[(project, "b")][:20] in str(cover)


@pytest.mark.parametrize("project,sym,n", [("Ether.fi", "sETHFI", 8), ("Maple", "stSYRUP", 8), ("Pendle", "sPENDLE", 3)])
def test_w6_insider_share_is_not_wired_when_no_checked_wallet_holds_shares(tmp_path, monkeypatch, project, sym, n):
    """Round 3 review W6 (Jake 2026-10-14): 'If none are identified, the cell is NOT WIRED with the route, not 0.'"""
    import xlcalc
    from test_overnight_ab5f2a9 import _get, _row
    from test_trajectory import _built
    wb, _ = _built(tmp_path, monkeypatch, project)
    o = _row(wb, xlcalc.Workbook(wb), "CP Comparability", project)
    cell = str(_get(o, "Insider share of staked / locked supply"))
    assert cell.startswith(f"NOT WIRED — no insider wallet identified among {sym} holders"), cell
    assert len(cell) <= 255                                                   # an Excel string constant
    what = str(_get(o, "Insider share — what is divided"))
    assert f"Wallets checked ({n}, 0 {sym} each on 2026-10-13)" in what
    leak = _get(o, "Leakage (i)")
    assert leak in ("", None) or str(leak).startswith("NOT WIRED"), leak


def test_only_declared_gaps_block():
    assert config.outflow_incomplete("Morpho", ["b"]) == []         # B3: off-Ethereum campaigns now read
    assert config.outflow_incomplete("Aethir", ["b"]) and config.outflow_incomplete("Fluid", ["b", "c"])


# ------------------------------------------------------------------------------------- V3–V6 on Jake's 10-13 dumps
def test_v5_chainlink_quarterly_release_reads_the_flows_line(monkeypatch):
    import build_workbook as bw
    import references as rf
    from real_dump import load
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Chainlink"]])
    g = _groups("Chainlink")
    flows = bw.flow_statements(g, pd.Timestamp(load("Chainlink")["meta"]["asof"]))
    r = {x["metric"]: x for x in rf.check(g, flows=flows)}["quarterly_release"]
    assert r["ours"] == pytest.approx(18_750_000) and "Flows: Token flow" in r["route"]
    assert r["status"] == "LIVE-UNVERIFIED"                      # a press figure: a cross-check, never VERIFIED


def test_v3_maple_dao_safe_move_is_named_and_only_the_eoa_leg_is_a_release():
    import references as rf
    g = _groups("Maple")
    r = {x["metric"]: x for x in rf.check(g)}["dao_safe_balance"]
    assert r["status"] == "BROKEN" and "0xd628a262" in r["detail"] and "4,000,000" in r["detail"]
    rel = g[("Maple", "unlock_release_tokens")]
    assert float(rel[pd.to_datetime(rel.date) == pd.Timestamp("2026-10-13")].value.iloc[0]) == pytest.approx(4e6, abs=1)


def test_v4_eth_burn_gap_is_a_named_basis_difference():
    import references as rf
    r = {x["metric"]: x for x in rf.check(_groups("Ethereum"))}["burn_30d"]
    assert r["status"] == "CHECK" and "basis difference" in r["detail"] and "etherscan" in r["detail"]


def test_v6_a_short_leg_is_named_a_coverage_gap(monkeypatch):
    """GEODNET Q-4 on Jake's 10-13 run read +2.71%: the opening was stored (451,925,750.34, his workbook) while the
    mining release starts 2025-09-29 — 20 of 90 days. The status names a COVERAGE gap."""
    import build_workbook as bw
    from real_dump import load
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["GEODNET"]])
    g = dict(_groups("GEODNET"))
    asof = pd.Timestamp(load("GEODNET")["meta"]["asof"])
    start, _end = bw._flow_window(asof, 4)
    c = g[("GEODNET", "circulating_supply_used")]
    first = c.iloc[[0]].assign(date=start, value=451_925_750.3365579)
    g[("GEODNET", "circulating_supply_used")] = pd.concat([first, c], ignore_index=True)
    st = next(x for x in bw.flow_statements(g, asof) if x["Project"] == "GEODNET" and x["Line"] == "Status")
    assert "COVERAGE gap" in st["Q-4"] and "pool_release_tokens covers 20/90 days" in st["Q-4"], st["Q-4"]


# --------------------------------------------------------------------------- round 3 run results (Jake 2026-10-14)
def test_x3_near_probe_is_inconclusive_on_429_and_on_empty_reads(monkeypatch, capsys):
    """X3: 'A 429 or empty read must give INCONCLUSIVE, never a pass.' Two of three wallets answered HTTP 429 and the
    verdict said every outflow was on the list. The probe now waits a 429 out (paced by fetch.nearblocks' own pacer)
    and gives no verdict on a partial or empty read."""
    import check_offline_items as coi
    from fetch import nearblocks as nb
    clock = [0.0]
    monkeypatch.setattr(nb, "_clock", lambda: clock[0])
    monkeypatch.setattr(nb, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    calls = []

    class _Limited:
        status_code = 429

        def json(self):
            return {"message": "rate limited"}

    monkeypatch.setattr(coi.requests, "get", lambda url, **k: calls.append(url) or _Limited())
    coi.near_revenue_wallet_flows()
    out = capsys.readouterr().out
    assert "VERDICT: INCONCLUSIVE" in out and "every outflow of 1,000 NEAR or more is on" not in out
    assert len(calls) == 6 * 4                         # each of the 6 reads: the call + 3 retries after the minute
    assert clock[0] >= 3 * 6 * nb.RATE_LIMIT_WAIT      # it waited the limit out, it did not hammer

    ok = [("a receipts", "complete", 12), ("a ft-txns", "complete", None)]
    assert coi.near_flows_verdict(ok, [], []).startswith("every outflow")
    empty = ok + [("b receipts", "complete", 0)]
    assert coi.near_flows_verdict(empty, [], []).startswith("INCONCLUSIVE") and "b receipts: 0 rows" in \
        coi.near_flows_verdict(empty, [], [])
    assert coi.near_flows_verdict(ok + [("c receipts", "HTTP 429", 1000)], [], []).startswith("INCONCLUSIVE")


def test_x4_nearblocks_budget_covers_a_day_of_free_plan_pacing():
    """X4: the tier timed out at 60s on free-plan pacing (6 credits a minute) with 5 of its calls read. The budget now
    covers every pacing window the day's calls need, plus one window of slack."""
    import math

    import fetch
    from fetch import nearblocks as nb
    calls = fetch.nearblocks_daily_calls()
    assert calls >= 9
    windows = math.ceil(calls / nb.CREDITS_PER_MINUTE)
    assert fetch.TIER_BUDGET_S["nearblocks"] >= 60 * windows + 60       # the windows + one of slack


def test_x2_boros_first_read_resumes_across_runs_and_stores_only_when_complete(tmp_path, monkeypatch):
    """X2: the tier timed out at 120s with 0 rows. The adapter now keeps each market's days in .cache/boros/state.json,
    stops itself at its own budget, reads the stalest markets first and stores nothing until every market is read
    through a day. Real export rows (fixtures/api_boros_market2.csv) for every market."""
    import fetch.boros as fb
    from fetch.base import FetchOutput
    monkeypatch.setattr(fb, "today", lambda: pd.Timestamp("2026-10-13"))
    rows = list(csv.DictReader((FX / "api_boros_market2.csv").open(newline="", encoding="utf-8")))
    meta = _fx("api_boros_meta.json")
    real = [m for m in meta["markets"] if fb.collateral_pricing(m, {int(a["tokenId"]): a for a in meta["assets"]})]
    assert real
    mk = [dict(real[i % len(real)], marketId=100 + i) for i in range(5)]   # the real records, five market ids
    clock, calls = [0.0], []

    class _Http:
        def get(self, url, params=None, **k):
            if url.endswith("/v1/markets"):
                return {"results": mk}
            return {"results": meta["assets"]}

        def get_text(self, url, params=None, **k):
            calls.append(params["marketId"])
            clock[0] += 100.0                                # each export "takes" 100s of the budget
            return "\n".join([",".join(rows[0].keys())] + [",".join(str(r[c]) for c in rows[0]) for r in rows])

    class _Daily:
        def __init__(self):
            self.done_keys = set()

        def due(self, k, day):
            return k not in self.done_keys

        def done(self, k, day):
            self.done_keys.add(k)

    daily = _Daily()
    spec = config.PROJECT_BY_NAME["Pendle"]["boros_volume"]
    outs = []
    for _run in range(4):
        b = fb.BorosVolume(http=_Http(), daily_checks=daily, cache_dir=tmp_path, max_seconds=150.0,
                           clock=lambda: clock[0])
        out = FetchOutput()
        b._project("Pendle", spec, out)
        outs.append(out)
    # run 1 and 2: two markets each (cut by the budget), nothing stored; run 3: the fifth, then everything stored
    stored = [sum(len(f) for f in o.frames) if getattr(o, "frames", None) else 0 for o in outs]
    assert stored[0] == stored[1] == 0 and stored[2] > 0, stored
    assert len(calls) == 5 and len(set(calls)) == 5                         # each market read once, none twice
    assert ("boros_volume:Pendle" in daily.done_keys)                       # complete for yesterday: done for today
    assert stored[3] == 0                                                   # run 4: skipped as current
    st = json.loads((tmp_path / "state.json").read_text())
    assert all(v["through"] == "2026-10-12" for v in st["markets"].values())


def test_x1_an_explorer_hole_is_read_from_the_chain_not_tolerated(tmp_path, monkeypatch):
    """X1: Ether.fi's OP cashback window was 19.808964 ETHFI short of balanceOf. Cause: Blockscout has NO logs for OP
    block 158,047,200 (both hosts, every API), while the chain has the distributor's Transfer of exactly that amount.
    Real data: the RPC's Transfer logs in and out of the distributor around the hole and the real balanceOf at both
    ends (fixtures/api_etherfi_cashback_hole.json). The explorer fake serves them WITHOUT the hole block, as Blockscout
    does; the scan must find the block, read it from the chain and reconcile to the wei — no tolerance."""
    import fetch.logscan as ls
    from fetch.base import FetchOutput
    from fetch.logcache import LogCache
    fx = _fx("api_etherfi_cashback_hole.json")
    logs = [ls._norm_log(x) for x in fx["logs"]]
    hole, h = fx["explorer_missing_block"], fx["holder"].lower()
    bal0 = int(fx["balance_before"])
    monkeypatch.setattr(ls, "today", lambda: pd.Timestamp("2026-10-13"))

    def match(e, topics, lo, hi):
        return lo <= e["blockNumber"] <= hi and all(t is None or e["topics"][i] == t.lower()
                                                    for i, t in enumerate(topics))

    class _Explorer:
        def get_logs(self, chain_id, token, topics, lo, hi):
            return [e for e in logs if match(e, topics, lo, hi) and e["blockNumber"] != hole], {"explorer": "blockscout"}

        def block_at(self, chain_id, ts):
            return fx["from_block"]

    class _Reader:
        balance_reads = 0

        def erc20_balance_at(self, chain, token, holder, block):
            _Reader.balance_reads += 1
            sign = lambda e: (1 if ls.topic_address(e["topics"][2]) == h else -1)  # noqa: E731
            return bal0 + sum(sign(e) * ls._amount(e) for e in logs if e["blockNumber"] <= block)

        def scan_logs(self, chain, base, lo, hi):
            return [e for e in logs if match(e, base["topics"], lo, hi)], 1, 1

        def checksum(self, a):
            return a

        def block_timestamp(self, chain, b):
            # OP: 2 s blocks; block 158,047,200 is 2026-10-11 04:32:57 UTC (eth_getBlockByNumber, read 2026-10-14)
            return int(pd.Timestamp("2026-10-11 04:32:57", tz="UTC").timestamp()) + (b - hole) * 2

        def erc20(self, chain, token):
            class _F:
                class functions:
                    @staticmethod
                    def decimals():
                        class _C:
                            @staticmethod
                            def call():
                                return 18
                        return _C
            return _F

    scan = ls.LogScan(explorer=_Explorer(), reader=_Reader(), cache=LogCache(tmp_path))
    spec = {"key": "cashback_out_optimism", "metric": "cashback_paid_tokens", "window_days": 100}
    out = FetchOutput()
    assert scan._window_scan({"name": "Ether.fi"}, spec, "optimism", 10, fx["token"], [h], fx["to_block"], "test", out)
    msg = " ".join(e.message for e in out.log)
    assert f"block {hole:,} (1 Transfer(s) the explorer does not index)" in msg, msg[:400]
    assert "RECONCILED to the wei" in msg
    paid = sum(f["value"].sum() for f in out.frames)
    true_out = sum(ls._amount(e) for e in logs if ls.topic_address(e["topics"][1]) == h) / 1e18
    assert paid == pytest.approx(true_out, abs=1e-9)                     # the hole's 19.808964 ETHFI is in
    assert _Reader.balance_reads < 60                                    # one bisection, not a block-by-block walk
