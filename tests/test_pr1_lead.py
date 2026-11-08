"""Project round 1 (Jake 2026-11-05): the lead's merge fixes. No network."""
from types import SimpleNamespace

import reader


class _A:
    def __init__(self, fields):
        self.fields = fields

    def data_field(self, key, header):
        return self.fields.get(header)


class _Data:
    def __init__(self):
        self.vals = {}

    def put(self, name, key, v):
        self.vals[key] = v
        return f"_data!{key}"


def _run(monkeypatch, fields):
    out = {}
    monkeypatch.setattr(reader, "put_code", lambda sh, r, c, code, why, *a, **k: out.update(code=code, why=why))
    monkeypatch.setattr(reader, "put_formula", lambda sh, r, c, f, *a, **k: out.update(formula=f, note=a[1]))
    ctx = SimpleNamespace(A=_A(fields), data=_Data())
    reader._thirty_day_change(None, ctx, 1, None, {"name": "Uniswap"}, "revenue_usd", "revenue")
    return out, ctx.data.vals


def test_thirty_day_change_is_a_rate_per_covered_day_with_the_read_lag_allowed(monkeypatch):
    """PR1.U1 x PR1.S1: the latest window covers 29 days and 1 day of read lag (the as-of day, not read); the change
    divides per-covered-day rates, so 29 days are not compared with 30 as if they were 30."""
    out, vals = _run(monkeypatch, {reader._DATA_NOW: 290.0, reader._DATA_M1: 300.0, reader._DATA_NOW_DAYS: 29,
                                   reader._DATA_M1_DAYS: 30, reader._DATA_LAG: 1})
    assert "formula" in out, out
    f = out["formula"]
    rate = (vals["revenue30|now"] / vals["revenue30|now_days"]) / (vals["revenue30|prior"] / vals["revenue30|prior_days"]) - 1
    assert abs(rate) < 1e-12          # 10 a day both windows: no change (the old 29-of-30 sum would read -3.3%)
    assert f.startswith("=(") and "now_days" in f and "prior_days" in f
    assert "read lag" in out["note"]


def test_thirty_day_change_refuses_a_hole_that_is_not_the_read_lag(monkeypatch):
    out, _ = _run(monkeypatch, {reader._DATA_NOW: 250.0, reader._DATA_M1: 300.0, reader._DATA_NOW_DAYS: 25,
                                reader._DATA_M1_DAYS: 30, reader._DATA_LAG: 1})
    assert out.get("code") == "n/a" and "never 0" in out["why"]


# ============================================================ PR1.U10: the Uniswap text pass (lead), unit level
import pandas as pd  # noqa: E402

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _frame(days, vals, source="defillama"):
    return pd.DataFrame({"date": pd.to_datetime(days), "value": vals, "source": source})


def test_one_off_share_is_of_everything_the_rate_sums(monkeypatch):
    """Uniswap total dilution read 'of which one-off: 23%' — 13,303 of the (e) leg's 57,505 — while the rate also adds
    the (d) leg's ~5.0M: the share of the RATE is 0.26%, said first; the leg's own share follows."""
    asof = pd.Timestamp("2026-10-13")
    q0 = (asof - pd.Timedelta(days=90), asof)
    groups = {("P", "outflow_d_tokens"): _frame(["2026-10-01"], [5_000_000.0]),
              ("P", "outflow_e_tokens"): _frame(["2026-10-11"], [57_505.0])}
    monkeypatch.setattr(bw, "ONE_OFF_SHARES", {("P", "outflow_e_tokens", "q0"): (13_303.0, 57_505.0, "airdrop claims")})
    monkeypatch.setattr(bw, "_ONE_OFF_CTX", {"groups": groups, "bounds": {"q0": q0}})
    L = bw.DC["q0"]
    f = (f'=(INDEX(Data!${L}$2:${L}$99,MATCH($A5&"|outflow_d_tokens",Data!$A$2:$A$99,0))'
         f'+INDEX(Data!${L}$2:${L}$99,MATCH($A5&"|outflow_e_tokens",Data!$A$2:$A$99,0)))/2')
    note = bw._one_off_share_note("P", f)
    assert "0.26% of the legs this rate sums" in note and "5,057,505" in note and "23% of outflow_e_tokens alone" in note
    # a ratio's other side is never a leg: burn ÷ release keeps the burn's own share
    groups[("P", "gross_burn_tokens")] = _frame(["2026-10-01"], [100.0])
    monkeypatch.setattr(bw, "ONE_OFF_SHARES", {("P", "gross_burn_tokens", "q0"): (90.0, 100.0, "treasury")})
    r = (f'=INDEX(Data!${L}$2:${L}$99,MATCH($A5&"|gross_burn_tokens",Data!$A$2:$A$99,0))'
         f'/INDEX(Data!${L}$2:${L}$99,MATCH($A5&"|outflow_e_tokens",Data!$A$2:$A$99,0))')
    assert "of which one-off: 90% — gross_burn_tokens Q0" in bw._one_off_share_note("P", r)


def test_one_off_share_on_a_single_leg_is_unchanged(monkeypatch):
    monkeypatch.setattr(bw, "ONE_OFF_SHARES", {("P", "gross_burn_tokens", "q3"): (100e6, 100_244_003.0, "treasury")})
    monkeypatch.setattr(bw, "_ONE_OFF_CTX", {"groups": {}, "bounds": {}})
    L = bw.DC["q3"]
    note = bw._one_off_share_note("P", f'=INDEX(Data!${L}$2:${L}$9,MATCH($A5&"|gross_burn_tokens",Data!$A$2:$A$9,0))')
    assert "of which one-off: 99.76% — gross_burn_tokens Q3" in note, note     # never "100%" when it is not all


def test_flows_coverage_of_a_differenced_leg_spans_its_gaps_and_allows_the_read_lag():
    """Uniswap Q0 read 'CHECK — gross_burn_tokens covers 87/90 days' with a residual of exactly 0: the burn is the
    0x…dEaD balance's day-on-day change, so the row after a missing day carries the gap."""
    start, end = pd.Timestamp("2026-07-15"), pd.Timestamp("2026-10-13")
    days = [d for d in pd.date_range("2026-07-01", "2026-10-12") if d not in (pd.Timestamp("2026-08-01"),
                                                                            pd.Timestamp("2026-09-01"))]
    g = _frame(days, [1.0] * len(days), source="chain:ethereum:burn_dead:delta")
    n, lag = bw._flow_days(g, bw._daily(g), start, end, 0)
    assert (n, lag) == (89, 1)                        # 07-15..10-12 spanned, the as-of day is read lag
    g2 = _frame(days, [1.0] * len(days), source="defillama")
    n2, lag2 = bw._flow_days(g2, bw._daily(g2), start, end, 0)
    assert (n2, lag2) == (87, 1)                      # a plain daily flow: a missing day is a hole
    assert bw._flow_days(g, bw._daily(g), start - pd.Timedelta(days=90), start, 1)[1] == 0   # no lag before Q0


def test_value_check_is_explained_only_when_the_token_gap_is_within_the_bridge(monkeypatch):
    days = pd.date_range("2025-12-30", "2026-10-12")
    burn = _frame(days, [45_000.0] * len(days), source="chain:ethereum:burn_dead:delta")
    px = _frame(days, [5.0 + i * 0.01 for i in range(len(days))])
    rev = _frame(days, [45_000.0 * (5.0 + i * 0.01) for i in range(len(days))])
    groups = {("Uniswap", "gross_burn_tokens"): burn, ("Uniswap", "price_usd"): px, ("Uniswap", "revenue_usd"): rev}
    monkeypatch.setattr(bw, "_ONE_OFF_IN_SERIES", {})
    start, end = pd.Timestamp("2026-07-15"), pd.Timestamp("2026-10-13")
    txt = bw._value_timing(groups, "Uniswap", start, end, 0.081)
    assert txt and txt.startswith("EXPLAINED — timing") and "config.VALUE_TIMING" in txt
    burn2 = _frame(days, [52_000.0] * len(days), source="chain:ethereum:burn_dead:delta")   # +15.6% more tokens
    assert bw._value_timing({**groups, ("Uniswap", "gross_burn_tokens"): burn2}, "Uniswap", start, end, 0.2) is None
    assert bw._value_timing(groups, "Aerodrome", start, end, 0.081) is None    # undeclared: the CHECK stands
    assert config.VALUE_TIMING["Uniswap"]["lag_days"] == 7


def test_reader_texts_follow_s8_and_the_declared_payout_reason():
    import reader
    src = reader.Src(sheet="A4 Permanent Burn", coord="L10", value=-5.0, num=-5.0, header="Issuance − burn Q0",
                     comment="BURN ONLY (P5-1C.B2 …) — already said by the audit cell")
    uni = config.PROJECT_BY_NAME["Uniswap"]
    assert reader._premint_note(uni, src) == ""                                     # never said twice
    net = reader.Src(sheet="CP Comparability", coord="BM27", value=1.07, num=1.07)
    gross = reader.Src(sheet="CP Comparability", coord="BL27", value=0.08, num=0.08)
    assert reader._payout_note(uni, net, gross) == ""                               # the declared timing reason only
    t = {"notes": ["", "", "", "x\n\nONE-OFF IN THIS RATE — of which one-off: 99.76% — gross_burn_tokens Q3"]}
    assert reader._one_off_of(t, 3).startswith("ONE-OFF IN THIS RATE") and reader._one_off_of(t, 0) == ""


def test_the_100m_burn_reference_reads_its_differenced_row():
    """The burn landed 2025-12-27 20:33 UTC; the 0x…dEaD delta that carries it is dated 2025-12-28 — the reference
    sums the two days (it read NOT WIRED 'starts 2025-12-28, after the reference date')."""
    import references
    route = references.ROUTES[("Uniswap", "g6_retro_burn_100m")] if hasattr(references, "ROUTES") else \
        next(v for k, v in vars(references).items() if isinstance(v, dict) and ("Uniswap", "g6_retro_burn_100m") in v)[
            ("Uniswap", "g6_retro_burn_100m")]
    g = _frame(["2025-12-28", "2026-01-02"], [100_000_000.0, 4_000.0], source="derived:one_off")
    v, where, why = references._our_value(route, {("Uniswap", "one_off_burn_tokens"): g}, "Uniswap",
                                          pd.Timestamp("2025-12-27"), 100_000_000.0)
    assert v == 100_000_000.0 and not why, (v, where, why)


def test_a_line_fed_only_by_declared_one_offs_is_zero_in_other_quarters(monkeypatch):
    assert not (config.HOLDER_VALUE.get("Uniswap") or {}).get("funding"), "Uniswap declares no other funding period"
    src = open(bw.__file__, encoding="utf-8").read()
    assert 'vrows["other"] = [0.0 if v is None else v for v in vrows["other"]]' in src
