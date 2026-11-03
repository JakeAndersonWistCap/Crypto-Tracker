"""P4-Q2 (Jake 2026-11-03): World Mobile is parked — its two lost headlines (Master FDV; Fees ÷ FDV) are ACCEPTED
losses in config.REGRESSION_ALLOWLIST with `while_parked`, the same form as Aerodrome's, so the live guard reports
0 violations for them. `while_parked` now asks config.project_parked (Aerodrome: its migration park reason; World
Mobile: config.CREDIBILITY_PARKED). Plain asserts, no network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import measuring_guard as mg  # noqa: E402

DAY = "2026-11-03"
PREV = {"World Mobile": {"Master | FDV ($)": 123_456_789.0, "Master | Fees ÷ FDV (annualised)": 0.0123,
                         "Master | Price — spot ($)": 0.05}}


def test_both_world_mobile_losses_are_accepted_while_parked():
    bad, accepted = mg.lost_against_previous(PREV, {"World Mobile": {"Master | Price — spot ($)": 0.05}}, None,
                                             "2026-11-02", on=DAY)
    assert bad == [], bad
    assert len(accepted) == 2 and all(a.startswith("ACCEPTED: World Mobile") and "parked (P4-Q2)" in a
                                      for a in accepted), accepted


def test_any_other_world_mobile_loss_is_still_a_violation():
    prev = {"World Mobile": dict(PREV["World Mobile"], **{"Master | Market cap ($)": 9.0})}
    bad, _acc = mg.lost_against_previous(prev, {}, None, "2026-11-02", on=DAY)
    assert len(bad) == 2 and all("World Mobile" in b for b in bad)            # price + market cap, not the two FDV
    assert not any("FDV" in b for b in bad)


def test_the_allowance_ends_when_world_mobile_is_reopened(monkeypatch):
    monkeypatch.setattr(config, "CREDIBILITY_PARKED", {})
    assert not config.project_parked("World Mobile")
    bad, accepted = mg.lost_against_previous(PREV, {"World Mobile": {"Master | Price — spot ($)": 0.05}}, None,
                                             "2026-11-02", on=DAY)
    assert accepted == [] and len(bad) == 2


def test_the_same_form_as_aerodrome_and_no_other_project_parked():
    wm = [a for a in config.REGRESSION_ALLOWLIST if a["project"] == "World Mobile"]
    assert len(wm) == 2 and all(a.get("while_parked") is True and a.get("accepted") and a.get("reason")
                                and not a.get("until") for a in wm)
    assert config.project_parked("World Mobile")
    assert not config.project_parked("Uniswap") and not config.project_parked("Hyperliquid")
    # Aerodrome's allowances still follow its migration park reason
    assert config.project_parked("Aerodrome") == bool(config.aerodrome_park_reason())
