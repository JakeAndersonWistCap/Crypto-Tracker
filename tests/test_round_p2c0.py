"""P2c-0 (Jake 2026-10-28): the 10 violations of Jake's live guard run (`measuring_guard.py --newer metrics.db`,
2026-10-27 22:10), each answered — fed to the guard's own checks. A BASIS line must no longer be a violation (a declared
handover or restatement); a HEADLINE LOST line must be an ACCEPTED LOSS with its reason (config.REGRESSION_ALLOWLIST,
which the live check reads) or carry a value again (Pendle's Fees Δ — tests/test_round_p2c6.py, never allow-listed)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import config  # noqa: E402
import measuring_guard as mg  # noqa: E402
from test_round_p2c5 import P  # noqa: E402

BASELINE = json.loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text(encoding="utf-8"))
ON = "2026-10-28"


@pytest.mark.parametrize("key,bases", [
    ("Aerodrome|fdv_usd", ["aero_v3:supply_once", "chain:base:token"]),                # 1: declared handover (P2c-1)
    ("Ether.fi|noncirculating_holding_tokens", [P["ef8"], P["ef10"], P["ef12"]]),     # 2: restated (P2c-5a)
    ("Maple|insider_stake_shares", [P["mp8"], P["mp9"]]),                             # 3: restated (P2c-5b)
    ("Pendle|insider_stake_shares", [P["pd3"], P["pd6"]]),                            # 4: restated (P2c-5c)
])
def test_basis_lines_are_answered(key, bases):
    assert mg.live_basis_violations({key: bases}, BASELINE) == []


@pytest.mark.parametrize("project,head", [
    ("Hyperliquid", "A4 | BURN ÷ ISSUANCE (x) — crossover, on the basis to the left"),             # 5: P2c-9
    ("Aerodrome", "A3 | Net absorption Q0 (tokens) = RETIRED buyback (burned + held) − emissions"),  # 6: P2c-4
    ("Aerodrome", "A3 | Net absorption incl. unlocks Q0 (tokens) = RETIRED buyback − emissions − unlocks"),  # 7
    ("Aerodrome", "A3 | RETIREMENT ÷ DILUTION (x) = RETIRED buyback ÷ (emissions + unlocks), per covered day"),  # 8
])
def test_lost_headlines_are_accepted_losses_with_a_reason(project, head):
    a = config.regression_allowance(project, head, on=ON)
    assert a and a["reason"].strip(), (project, head)


@pytest.mark.parametrize("head", ["Master | Fees Δ6m", "Master | Fees Δ9m"])                    # 9, 10: P2c-6
def test_pendle_fees_deltas_are_not_hidden_by_an_allowance(head):
    # answered by a value on one basis (DefiLlama, both windows); where it cannot form, the guard says so
    assert config.regression_allowance("Pendle", head, on=ON) is None
    assert config.comparison_basis("Pendle", "fees_usd")["metric"] == "fees_usd_defillama"


def test_the_aerodrome_allowance_ends_when_aerodrome_is_unparked(monkeypatch):
    monkeypatch.setattr(config, "aerodrome_park_reason", lambda state=None, day=None: "")
    assert config.regression_allowance("Aerodrome", "A3 | Net absorption Q0 (tokens) = x", on=ON) is None
