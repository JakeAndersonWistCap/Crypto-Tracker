"""Round 16 (Jake 2026-11-20) — close-off: the $ ending-schedule cap on the token ratio (2); net holder yield over the
window's average market cap (3); Aerodrome's emissions on accrual for dilution (4); the headers name (f) (5)."""
import pandas as pd

import build_workbook as bw
import config


class _R(bw.Refs):
    """Data references as readable tokens: D(metric,col)."""
    def D(self, r, metric, col="q0"):
        if bw._PROJECT["name"]:
            metric = config.metric_alias(bw._PROJECT["name"], metric)
        return f"D({metric},{col})"


def _col(cols, head):
    return next(c for c in cols if c[0].startswith(head))


# ----- 2 -------------------------------------------------------------------------------------------------------------
def test_2_the_dollar_cap_scales_the_trailing_dollars_by_the_token_ratio(monkeypatch):
    monkeypatch.setitem(bw.ENDING_SCHEDULE_ROWS, "Morpho", True)
    P = config.PROJECT_BY_NAME["Morpho"]
    R = _R(10, 0, [])
    usd = bw._annualise(R, 5, P, "outflow_e_usd", "X")
    tok = bw._annualise(R, 5, P, "outflow_e_tokens", "X")
    assert "MIN(1," in usd and "D(schedule_forward_365_tokens,now)" in usd and "D(schedule_release_usd,q0)" in usd
    assert "price_usd" not in usd                                             # no other day's price is mixed in
    assert "MIN(" in tok and "D(schedule_release_tokens,q0)" in tok
    # the same arithmetic as the cell, by hand: trailing 100 tokens a year worth $50 a year, 40 tokens left
    trail_tok, trail_usd, fwd, base_tok, base_usd = 100.0, 50.0, 40.0, 300.0, 180.0
    assert base_usd - trail_usd + trail_usd * min(1, fwd / trail_tok) == 150.0  # $ falls by 60% of the leg's $ ...
    assert base_tok - trail_tok + min(trail_tok, fwd) == 240.0                  # ... as the tokens fall by 60% of the leg


# ----- 3 -------------------------------------------------------------------------------------------------------------
def test_3_net_holder_yield_divides_by_the_windows_average_market_cap():
    R = _R(10, 0, [])
    cols = bw._cp_cols(R, {})
    nhy = _col(cols, "NET HOLDER YIELD")
    assert "average market cap over the window" in nhy[0]
    P = config.PROJECT_BY_NAME["Maple"]
    f = nhy[1](9, P)
    assert "D(market_cap_usd,q0)" in f and "D(market_cap_usd,now)" not in f
    # the holder value yield column itself stays over today's market cap (unchanged)
    assert "D(market_cap_usd,now)" in _col(cols, "HOLDER VALUE YIELD")[1](9, P)


# ----- 4 -------------------------------------------------------------------------------------------------------------
def test_4_emissions_accrue_at_the_minters_rate_from_the_activation():
    days = pd.date_range("2026-10-15", "2026-10-21")
    before = pd.Series(1.0, index=days)
    rate = pd.Series({pd.Timestamp("2026-10-27"): 7_000.0, pd.Timestamp("2026-10-28"): 7_000.0,
                      pd.Timestamp("2026-11-02"): 14_000.0})
    acc = bw.emissions_accrual(before, rate, "2026-10-22", 7.0, 3)
    assert acc[pd.Timestamp("2026-10-21")] == 1.0                              # the mints before the activation
    assert acc[pd.Timestamp("2026-10-22")] == 1_000.0                          # the first read's rate, back to 10-22
    assert acc[pd.Timestamp("2026-10-31")] == 1_000.0                          # carried within 3 days
    assert pd.Timestamp("2026-11-01") not in acc.index                         # beyond 3 days: unknown, never 0
    assert acc[pd.Timestamp("2026-11-02")] == 2_000.0
    spec = config.AERO_EMISSIONS_ACCRUAL["Aerodrome"]
    assert config.outflow_metrics("Aerodrome", "b") == (spec["metric"],)
    assert config.ABSORPTION_EMISSIONS["Aerodrome"][0] == spec["metric"]
    # supply keeps the mints: the accrual is in no supply leg
    assert spec["metric"] not in str(config.CIRCULATING_ONCHAIN.get("Aerodrome"))


# ----- 5 -------------------------------------------------------------------------------------------------------------
def test_5_the_headers_name_f_and_the_allowance_follows():
    R = _R(10, 0, [])
    heads = [c[0] for c in bw._cp_cols(R, {})]
    assert any(h.startswith("TOTAL DILUTION = outflows a+b+c+d+e+f (annualised)") for h in heads)
    assert not any("outflows a+b+c+d+e (" in h or "outflows a+b+c+d+e at" in h for h in heads)
    new = "CP | " + bw.CP_NHY_HEAD
    assert any(new in m for a in config.REGRESSION_ALLOWLIST if a["project"] == "Aerodrome" for m in a["match"])


# ----- 1: Ether.fi frozen -----------------------------------------------------------------------------------------------
def test_1_ether_fi_is_frozen_with_its_own_baseline_and_no_earlier_record():
    import json
    import project_freeze as pf
    assert set(config.FROZEN_PROJECTS) == set(config.FROZEN_BEFORE_R16) | {"Ether.fi"}
    base = json.loads(pf.baseline_path("Ether.fi").read_text(encoding="utf-8"))
    assert len(base) > 250 and all(k.startswith(("audit|", "reader|")) for k in base)
    assert config.FROZEN_MOVES["Ether.fi"] == {}                               # never frozen before: nothing to carry
    assert "7a4b5b9" in config.FROZEN_PROJECTS["Ether.fi"]["why"]
