"""Round 17 (Jake 2026-11-23) — finish the 15, no new metric or rule:
  A1  Aerodrome: the six days before the first v3 read are backfilled from the same Minter counter, and a Foundation
      wallet's destruction that is no burned buyback is its own flow line
  A2  Ether.fi: a wallet the rule counts in circulating is not added a second time to the CoinGecko cross-check
  A3  holder value yield divides by the window's average market cap, as net holder yield
  B   a cell nothing can evidence is shown grey as "not verified" and is not a blocker (never a broken or blank cell)
  D   the Leaderboard: the reader's first sheet, frozen projects ranked inside their archetype, the others provisional
"""
import types

import openpyxl
import pandas as pd
import pytest

import build_workbook as bw
import config
import leaderboard as lb


class _R(bw.Refs):
    def D(self, r, metric, col="q0"):
        if bw._PROJECT["name"]:
            metric = config.metric_alias(bw._PROJECT["name"], metric)
        return f"D({metric},{col})"


def _col(cols, head):
    return next(c for c in cols if c[0].startswith(head))


# ----- A3 ------------------------------------------------------------------------------------------------------------
def test_a3_holder_value_yield_divides_by_the_windows_average_market_cap():
    cols = bw._cp_cols(_R(10, 0, []), {})
    for name in ("Maple", "Aerodrome", "Uniswap"):
        P = config.PROJECT_BY_NAME[name]
        hvy = _col(cols, "HOLDER VALUE YIELD")
        f = hvy[1](9, P)
        assert "÷ average market cap over the window" in hvy[0]
        assert "D(market_cap_usd,q0)" in f and "D(market_cap_usd,now)" not in f, name
        nhy = _col(cols, "NET HOLDER YIELD")[1](9, P)
        assert "D(market_cap_usd,q0)" in nhy and "D(market_cap_usd,now)" not in nhy   # one denominator for the pair
    # by hand: $36.5m paid over the year; the cap averaged $1,000m over the window and stands at $500m today
    paid, avg, today = 36.5e6, 1_000e6, 500e6
    assert paid / avg == pytest.approx(0.0365) and paid / today == pytest.approx(0.073)   # the old figure doubled it
    ren = [r for r in config.HEADLINE_RENAMES if r.get("ref") == "R17.A3"]
    assert len(ren) == 1 and ren[0]["kind"] == "change" and "÷ market cap —" in ren[0]["from"]


# ----- A2 ------------------------------------------------------------------------------------------------------------
def test_a2_a_wallet_the_rule_counts_is_not_added_twice_to_the_reference_check(monkeypatch):
    import credibility
    legs = set(config.rule_in_legs("Ether.fi"))
    assert legs and not config.RULE_IN["Ether.fi"].get("reference_leaves_out", True)
    seen = {}
    real = config.coingecko_counts_holdings

    def spy(name):
        seen[name] = real(name)
        return seen[name]
    monkeypatch.setattr(config, "coingecko_counts_holdings", spy)
    got = credibility.circulating_input("Ether.fi")
    text = str(got)
    assert seen.get("Ether.fi") is not None
    assert not [x for x in legs if x in text]                                  # none of the rule's wallets is added on top
    # a project whose reference leaves the wallets out keeps the add-back (the rule is unchanged for it)
    others = [n for n, r in config.RULE_IN.items() if r.get("reference_leaves_out", True)
              and set(config.rule_in_legs(n)) & set(real(n) or ())]
    for n in others[:2]:
        assert [x for x in config.rule_in_legs(n) if x in str(credibility.circulating_input(n))], n


# ----- A1 ------------------------------------------------------------------------------------------------------------
class _Minter:
    """emissionsMinted grows 100 a day from the cutover; teamRate 5%."""
    def __init__(self, cut, fail_on=None):
        self.cut, self.fail_on = cut, fail_on

    def _block_at(self, ts):
        return int(ts)

    def call(self, addr, fn, block=None):
        day = pd.Timestamp(block, unit="s").normalize()
        if self.fail_on is not None and day == pd.Timestamp(self.fail_on):
            raise RuntimeError("archive node refused")
        if fn == "teamRate":
            return (50_000,)
        return (int((day - self.cut).days * 100 * 10 ** 18),)


def _hist(first_day, cum, have=(), fail_on=None):
    from fetch import aero_v3
    cut = pd.Timestamp(config.AERODROME_MIGRATION["cutover_utc"]).normalize()
    st = {"addresses": {"minter": "0x" + "11" * 20}}
    first = cut + pd.Timedelta(days=first_day)
    rows, why = aero_v3.minted_opening_history(config.PROJECT_BY_NAME["Aerodrome"], (first, cum), set(have),
                                               reader=_Minter(cut, fail_on), state=st)
    df = pd.DataFrame(rows)
    return cut, df, why


def test_a1_the_days_before_the_first_read_sum_to_the_first_reads_counter():
    cut, df, why = _hist(5, 560.0)
    g = df[df["metric"] == "gross_issuance_tokens"].assign(date=lambda d: pd.to_datetime(d["date"]).dt.normalize()
                                                           ).set_index("date")["value"]
    # dated as the daily reader dates a delta - the day of the later read: the mints of day D are on D+1
    assert list(g.index) == [cut + pd.Timedelta(days=i) for i in range(1, 6)]
    assert cut not in g.index                                                  # the legacy Minter's last row holds that day
    assert g.iloc[:4].tolist() == [100.0] * 4 and g.iloc[4] == pytest.approx(160.0)   # the first read's day: the rest
    assert g.sum() == pytest.approx(560.0)                                     # exactly the first read's cumulative
    t = df[df["metric"] == "issuer_mint_tokens"]["value"]
    assert t.sum() == pytest.approx(560.0 * 0.05)
    assert df["source"].str.contains("emissionsMinted").all() and "first v3 read" in why


def test_a1_a_held_day_is_never_rewritten_and_an_unreadable_day_is_named_not_zero():
    cut, _, _ = _hist(5, 560.0)
    _, df, _ = _hist(5, 560.0, have={str((cut + pd.Timedelta(days=2)).date())})
    assert len(df[df["metric"] == "gross_issuance_tokens"]) == 4               # the day already held is left alone
    _, df, why = _hist(5, 560.0, fail_on=cut + pd.Timedelta(days=3))
    assert "stopped at" in why and str((cut + pd.Timedelta(days=3)).date()) in why
    days = set(pd.to_datetime(df["date"]).dt.normalize()) if len(df) else set()
    assert cut + pd.Timedelta(days=3) not in days and cut + pd.Timedelta(days=5) not in days   # unknown, never 0


def test_a1_a_destruction_that_is_no_burned_buyback_is_its_own_flow_line_and_no_explained_residual():
    fd = config.RULE_IN["Aerodrome"]["flow_destroyed"]
    assert [m for _, m in fd] == ["foundation_aero_burned_other_tokens"]
    ev = config.AERO_MINT_THEN_BURN_2026_11
    assert len(ev["mint"]["tx"]) == 66 and len(ev["burn"]["tx"]) == 66 and ev["purpose"]
    assert ev["burn"]["burned"] == ev["mint"]["to_buyer_safe"] == 10_000_000.0 and ev["burn"]["block"] > ev["mint"]["block"]
    assert "Aerodrome" not in getattr(config, "FLOW_RESIDUAL_EXPLAINED", {})    # the residual is measured, not excused
    # the buyback's burn is capped at what was BOUGHT since the purchase-day basis: 10 destroyed, 4 bought -> 6 other
    burned, bought = 10.0, 4.0
    assert burned - min(burned, bought) == 6.0


# ----- B -------------------------------------------------------------------------------------------------------------
def test_b_not_verified_needs_a_search_and_a_date_and_never_covers_a_broken_cell(monkeypatch):
    import done_check
    monkeypatch.setitem(config.NOT_VERIFIED, "Plume", (
        {"cols": ("Some column",), "searched": "explorer, docs, DefiLlama", "read_on": "2026-11-23"},
        {"cols": ("No search",), "searched": "", "read_on": "2026-11-23"},
        {"cols": ("No date",), "searched": "x", "read_on": ""}))
    assert config.not_verified("Plume", "Some column, 90 days")["read_on"] == "2026-11-23"
    assert config.not_verified("Plume", "No search") is None                   # an entry that names no search is void
    assert config.not_verified("Plume", "No date") is None
    assert config.not_verified("Aethir", "Some column") is None                # per project, never shared
    assert {"BROKEN", "STALE", "error", "blank"} <= set(done_check.NEVER_NOT_VERIFIED)
    import reader
    assert reader.CODES[config.NOT_VERIFIED_CODE] and reader.FILL_NOT_VERIFIED.fgColor.rgb.endswith("D9D9D9")


def test_b_no_frozen_project_has_a_not_verified_cell():
    assert not set(config.NOT_VERIFIED) & set(config.FROZEN_PROJECTS)          # a frozen figure is evidenced or it moves


# ----- D -------------------------------------------------------------------------------------------------------------
def _summary(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    heads = ["Project", "Archetype key", "Rank in archetype", "Holder value yield", "Total dilution, % of float",
             "Net holder yield", "Confidence (share green)"]
    for i, h in enumerate(heads, 1):
        ws.cell(4, i, h)
    for r, row in enumerate(rows, 5):
        for i, v in enumerate(row, 1):
            ws.cell(r, i, v)
    return wb


def _ctx(names_arch):
    return types.SimpleNamespace(projects=[{"name": n, "archetypes": [a]} for n, a in names_arch],
                                 asof=pd.Timestamp("2026-11-20"), groups={})


def test_d_frozen_projects_are_ranked_in_their_archetype_and_the_rest_are_provisional(monkeypatch):
    monkeypatch.setattr(config, "FROZEN_PROJECTS", {"Aa": {"since": "2026-11-01"}, "Bb": {"since": "2026-11-02"},
                                                    "Cc": {"since": "2026-11-03"}, "Dd": {"since": "2026-11-04"}})
    wb = _summary([["Aa", 3.0, 4, 0.05, 0.02, 0.03, 0.9],        # the Summary ranks ALL projects of the archetype
                   ["Bb", 9.0, 2, 0.10, 0.20, -0.10, 0.8],
                   ["Zz", 9.9, 1, 0.01, 0.01, 0.0, 0.5],         # not frozen, though it leads the Summary
                   ["Cc", "n/a", "unranked", "n/a", 0.04, "blocked", "n/a"],
                   ["Dd", 1.0, 1, 0.07, 0.01, 0.06, 1.0]])
    res = lb.add_leaderboard(wb, _ctx([("Aa", 3), ("Bb", 3), ("Zz", 3), ("Cc", 3), ("Dd", 2)]))
    assert wb.sheetnames[0] == "Leaderboard"
    assert res["ranked"] == {2: [(1, "Dd")], 3: [(1, "Bb"), (2, "Aa"), ("unranked", "Cc")]}
    assert res["provisional"] == ["Zz"]
    ws = wb["Leaderboard"]
    assert [c.value for c in ws[4]] == list(lb.HEADERS)
    body = {ws.cell(r, 2).value: r for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value}
    assert ws.cell(body["Bb"], 1).value == 1 and ws.cell(body["Zz"], 1).value == "provisional"
    assert ws.cell(body["Zz"], 8).value == "not frozen" and ws.cell(body["Aa"], 8).value == "2026-11-01"
    assert body["Zz"] > max(body[n] for n in ("Aa", "Bb", "Cc", "Dd"))          # provisional rows are below the ranked
    # every figure is the Summary's own cell: a formula, never a copied number
    for c in range(3, 8):
        assert str(ws.cell(body["Aa"], c).value).startswith("='Summary'!")
    assert ws.cell(body["Aa"], 4).value == "='Summary'!D5"
    with pytest.raises(ValueError):
        lb.add_leaderboard(wb, _ctx([("Aa", 3)]))                              # one Leaderboard a workbook


def test_d_the_line_is_generated_from_the_figures_and_an_unmeasured_figure_is_never_read_as_zero():
    vals = dict(zip(lb.HEADERS[2:7], [9.0, 0.10, 0.20, -0.10, 0.8]))
    s = lb.line_for("Bb", vals, frozen=True, rank=1, key_label="Payout ratio")
    assert "10.0%" in s and "20.0%" in s and "-10.0%" in s and "dilutes holders more than it pays them" in s
    assert "rank 1" in s and "80%" in s
    pos = lb.line_for("Aa", {**vals, "Net holder yield": 0.03}, frozen=True, rank=2, key_label="k")
    assert "+3.0%" in pos and "pays holders more than it dilutes them" in pos
    gap = lb.line_for("Cc", dict(zip(lb.HEADERS[2:7], ["n/a", "n/a", 0.04, "blocked", "n/a"])), frozen=True,
                      rank="unranked", key_label="k")
    assert "not measured (n/a)" in gap and "not measured (blocked)" in gap and "0.0%" not in gap
    prov = lb.line_for("Zz", vals, frozen=False, rank="provisional", key_label="k")
    assert "provisional" in prov and "rank 1" not in prov


def test_d_the_leaderboard_is_first_in_the_look_and_no_tab_the_freeze_reads():
    import project_freeze as pf
    import reader_look
    assert reader_look.TAB_ORDER[:2] == ("Leaderboard", "Highlights")
    assert not lb.SHEET.startswith(tuple(pf.READER_TABS))                      # a frozen baseline holds none of its cells


# ----- C -------------------------------------------------------------------------------------------------------------
def test_c_an_input_is_cross_checked_only_where_a_second_source_was_read():
    import headline_check as hc
    assert hc.classify("PASS", 1.0, "DefiLlama") == "CROSS-CHECKED"
    assert hc.classify("CHECK", 998_535_999, "CoinGecko") == "CROSS-CHECKED"      # a disagreement is still a cross-check
    assert hc.classify("CHECK (no reference)", None, "CoinGecko") == "SINGLE-SOURCE"   # named, but nothing was read
    assert hc.classify("FRESH-only", 1.0, "our own source") == "SINGLE-SOURCE"   # a re-read of ours is no second source
    assert hc.classify("DOCUMENTED LIMITATION", None, "—") == "SINGLE-SOURCE"
    assert hc.classify("N/A (recorded)", 5.0, "DefiLlama") == "RECORDED"         # shown beside ours, never the judge
    assert hc.classify("PASS (inputs)", None, "") == "DERIVED"
    assert hc.our_metrics("Data actual_buyback_tokens (q0)") == ["actual_buyback_tokens"]
    assert hc.our_metrics("computed: now_sum {'metrics': ('staked_tokens', 'locked_tokens')}") == [
        "locked_tokens", "staked_tokens"]


def test_c_a_single_source_is_named_from_the_store_never_guessed():
    import sqlite3

    import headline_check as hc
    db = sqlite3.connect(":memory:")
    db.execute("create table metrics (date text, project text, metric text, value real, source text)")
    db.executemany("insert into metrics values (?, ?, ?, ?, ?)",
                   [("2026-11-01", "Maple", "m", 1.0, "old_source"), ("2026-11-20", "Maple", "m", 2.0, "maple_page")])
    assert hc.stored_source(db, "Maple", "m") == ("maple_page", "2026-11-20")     # the newest row's own source
    assert hc.stored_source(db, "Maple", "absent") is None                        # no row: said so, no name invented
    assert hc.stored_source(None, "Maple", "m") is None
