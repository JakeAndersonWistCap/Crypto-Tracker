"""ROUND D5 (Jake 2026-10-27, head 9b0bf84): data collection for the 14 projects.

0 a DefiLlama restatement older than the 30-day re-read reaches the store (and is logged) — a wrong stored number is
fixed first. 1a Aethir's monthly revenue, spread over each month's days, is the weekly series' history. 1d the Plume
settlement seed lifts the native-leg window only for that invocation. 2a Ethereum's consensus rewards before Etherscan's
first read are MEASURED (ultrasound supply + burn), the formula MODELLED only where nothing is measured. 3b Sky's 500M is a
gift, not a grant.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def _frame(project, metric, days, values, source="defillama"):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "source": source, "tier": 1})


# ================================================================================================== 0
def test_0_the_defillama_fee_family_keeps_every_day_the_call_returned(monkeypatch):
    import fetch.base as fb
    monkeypatch.setattr(fb, "today", lambda: pd.Timestamp("2026-10-27"))
    days = pd.date_range("2025-10-01", "2026-10-26")
    for m in ("revenue_usd", "fees_usd", "holders_revenue_usd", "revenue_usd_defillama"):
        out = fb.window(_frame("Fluid", m, days, range(len(days))), 30)
        assert len(out) == len(days), m
    # anything else keeps the routine trim: another metric, or the same metric from another source
    assert len(fb.window(_frame("Fluid", "tvl_usd", days, range(len(days))), 30)) == 30
    assert len(fb.window(_frame("Fluid", "revenue_usd", days, range(len(days)), source="defillama:x"), 30)) == 30


def test_0_a_restated_quarter_is_logged_before_the_store_takes_the_new_figure(tmp_path):
    import store as sm
    import token_metrics as tm
    st = sm.Store(str(tmp_path / "m.db"))
    q4 = pd.date_range("2025-10-01", "2025-12-31")
    q1 = pd.date_range("2026-01-01", "2026-03-31")
    st.upsert(_frame("Fluid", "revenue_usd", q4.append(q1), [100.0] * len(q4) + [50.0] * len(q1)))
    new = _frame("Fluid", "revenue_usd", q4.append(q1), [90.0] * len(q4) + [50.1] * len(q1))
    lines = tm.restatement_lines(st, new)
    assert len(lines) == 1                                  # Q1 moved 0.2%: under the 0.5% line
    assert lines[0][0] == "Fluid"
    assert lines[0][1].startswith("RESTATED BY THE SOURCE: Fluid revenue_usd 2025Q4: stored 9,200 → now 8,280 (-10.0%)")
    st.upsert(new)
    assert st.conn.execute("SELECT value FROM metrics WHERE project='Fluid' AND date='2025-11-15'").fetchone()[0] == 90.0
    # another source's rows are never compared or logged
    assert tm.restatement_lines(st, _frame("Fluid", "revenue_usd", q4, [1.0] * len(q4), source="other")) == []
    st.close()
