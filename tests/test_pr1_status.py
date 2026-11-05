"""PROJECT ROUND 1, group `status` (Jake 2026-11-05).

S6:  Coverage and Credibility give ONE verdict per cell: a headline is VERIFIED only when its band is green AND the
     Credibility tab passes it (PASS / VERIFIED FINDING); a Credibility CHECK is a Coverage CHECK; a figure read only
     from an aggregator (DefiLlama, CoinGecko, Token Terminal, Artemis, growthepie) is never VERIFIED.
S7:  the price cross-checks (Coinbase, DefiLlama) compare at the instant of the price the workbook uses.
S9:  a code that contradicts a number fails the gate (contradictions.py), run on the reader built from the 15 dumps.
S11: Summary's key column in each key's own unit (A1 a change, %, A2-A4 ratios, x); the payout ratio in x.
S12: the Flows reconciliation blocks: a Q0 token-flow residual beyond tolerance, unexplained, takes the project's
     supply cells off green and out of the ranks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import reader  # noqa: E402
from shared_build import plain_build  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return plain_build(tmp_path_factory, "pr1_status")


def _col(ws, header, row=4):
    return next(c for c in range(1, ws.max_column + 1) if ws.cell(row, c).value == header)


def _rows(ws):
    return {ws.cell(r, 1).value: r for r in range(5, ws.max_row + 1) if isinstance(ws.cell(r, 1).value, str)}


# ============================================================================================================ S11
def test_s11_each_key_has_its_own_unit():
    assert reader.key_format(1) == reader.FMT_PCT, "A1's key is a 1-year change: -0.135 reads -13.5%, never -0.1x"
    for a in (2, 3, 4):
        assert reader.key_format(a) == reader.FMT_X


def test_s11_the_payout_ratio_is_in_x_everywhere():
    cols = {k: fmt for k, _h, _s, fmt, _d, _f in reader.COMPARISON_COLS}
    assert cols["payout"] == reader.FMT_X
    import inspect
    src = inspect.getsource(bw)
    i = src.index('("Payout ratio — gross = credited holder value ÷ fees"')
    assert "FMT_X" in src[i:i + 400] and "FMT_PCT" not in src[i:i + 400]


def test_s11_summary_formats_on_the_built_reader(built):
    wb = openpyxl.load_workbook(built["reader"])
    ws = wb["Summary"]
    kc, pc = _col(ws, "Archetype key"), _col(ws, "Payout ratio")
    arch_of = {}
    for name, r in _rows(ws).items():
        a = str(ws.cell(r, 3).value or "")
        if a.startswith("A") and a[1:2].isdigit():
            arch_of[name] = int(a[1])
    seen = set()
    for name, r in _rows(ws).items():
        c = ws.cell(r, kc)
        if isinstance(c.value, str) and c.value.startswith("=") and name in arch_of:
            want = reader.key_format(arch_of[name])
            assert c.number_format.split(";")[0] == want.split(";")[0], (name, c.number_format)
            seen.add(arch_of[name])
        p = ws.cell(r, pc)
        if isinstance(p.value, str) and p.value.startswith("="):
            assert '"x"' in p.number_format, (name, p.number_format)
    assert seen, "no key on Summary was a number"
