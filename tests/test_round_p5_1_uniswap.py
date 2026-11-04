"""P5 round 1, items 7 and 8 (Jake 2026-11-04).
7: Uniswap's airdrop-distributor claims stay counted as dilution (e); a one-off note on the NHY and Outflow (e) cells and
   in Project notes says the distributor's latest balance means the window's claims cannot repeat at that scale; net
   holder yield without the distributor is TEXT in Project notes only.
8: buying pressure on float reads a by-design code where the protocol buys nothing on the market."""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import done_check  # noqa: E402
import reader  # noqa: E402
import rule3  # noqa: E402

UNI = config.PROJECT_BY_NAME["Uniswap"]


def _g(metric, rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": "Uniswap", "metric": metric, "value": v, "source": "s"}
                         for d, v in rows])


# ================================================================================================== 7
def test_the_distributor_is_stored_on_its_own_beside_the_set():
    c = UNI["contracts"]["noncirc_merkle_distributor"]
    assert c["address"].lower() == "0x090d4613473dee047c3f2706764f49e0821d256e"
    assert c["component_metric"] == "noncirc_merkle_distributor_tokens"
    m = config.METRICS["noncirc_merkle_distributor_tokens"]
    assert m["kind"] == "stock" and m["only_projects"] == ("Uniswap",)
    spec = config.ONE_OFF_DILUTION_NOTES["Uniswap"]
    assert spec["category"] == "e" and "e" in config.OUTFLOW_CATEGORIES["Uniswap"]
    assert config.outflow_metrics("Uniswap", "e"), "the claims stay counted as dilution (e)"


def test_the_note_reads_the_distributors_own_latest_balance(monkeypatch):
    monkeypatch.setattr(bw, "_LAST_GROUPS", {
        ("Uniswap", "noncirc_merkle_distributor_tokens"): _g("x", [("2026-11-02", 260_000.0), ("2026-11-03", 241_005.3)]),
        ("Uniswap", "noncirculating_holding_tokens"): _g("y", [("2026-11-03", 241_005.3)])})
    n = bw.one_off_dilution_note(UNI)
    assert n.startswith("ONE-OFF (outflow (e)")
    assert "now holds 241,005 UNI (latest read 2026-11-03)" in n
    assert "cannot repeat at that scale" in n and "Net holder yield is unchanged" in n
    assert "30,202" in n


def test_without_its_own_series_the_note_says_so_and_gives_the_sets_read(monkeypatch):
    monkeypatch.setattr(bw, "_LAST_GROUPS", {
        ("Uniswap", "noncirculating_holding_tokens"): _g("y", [("2026-10-13", 12_484_662.6)])})
    n = bw.one_off_dilution_note(UNI)
    assert "its own balance is not stored yet" in n and "12,484,663 UNI (latest read 2026-10-13)" in n
    assert "held 0 UNI" in n, "the rest of the set is named with its read"
    monkeypatch.setattr(bw, "_LAST_GROUPS", {})
    assert "cannot be stated" in bw.one_off_dilution_note(UNI), "unknown is never a number"
    assert bw.one_off_dilution_note(config.PROJECT_BY_NAME["Pendle"]) == ""


def _ctx(nhy, eusd, mcap):
    vals = {"NET HOLDER YIELD": nhy, "Outflow (e)": eusd, "Market cap ($)": mcap}

    class A:
        def cell(self, sheet, header, project):
            assert sheet == reader.CP_TAB and project == "Uniswap"
            v = vals[header]
            return None if v is None else SimpleNamespace(num=v)
    return SimpleNamespace(projects=[UNI], config=config, bw=SimpleNamespace(one_off_dilution_note=lambda p: "NOTE"),
                           A=A(), p=lambda n: UNI)


def test_net_holder_yield_without_the_distributor_is_text_from_the_cp_cells():
    rows = reader._project_text_notes(_ctx(-0.01135816, 1_406_565.9, 4_648_938_898.0))["Uniswap"]
    (l1, t1, _d1), (l2, t2, _d2) = rows
    assert l1 == reader.TEXT_NOTE_ONE_OFF and t1 == "NOTE"
    assert l2 == reader.TEXT_NOTE_NHY_WITHOUT and isinstance(t2, str)
    assert t2.startswith("−1.11%"), t2                      # -1.136% + 1.41M / 4,649M = -1.106%
    assert "never" in _d2 and "ranked" in _d2


def test_a_missing_input_is_named_never_estimated():
    t = reader._project_text_notes(_ctx(None, 1_406_565.9, 4.6e9))["Uniswap"][1][1]
    assert t.startswith("Not worked out: net holder yield") and "%" not in t
    t = reader._project_text_notes(_ctx(-0.01, 1.0, 0.0))["Uniswap"][1][1]
    assert "a market cap above 0" in t


# ================================================================================================== 8
def _bp_cell(p):
    head, build, *_ = bw._buying_pressure_col(None)
    assert head == bw.BUYING_PRESSURE_HEAD
    return build(5, p)


def test_uniswap_buying_pressure_is_a_by_design_code():
    t = _bp_cell(UNI)
    assert t == config.BUYING_PRESSURE_BY_DESIGN["Uniswap"] and not t.startswith(("=", "n/a"))
    assert "buys no UNI on the market" in t and "Firepit" in t
    assert reader.code_of(t) == "by design"
    assert not rule3.is_suspect(t)
    assert done_check.classify(reader.code_of(t), t, date(2026, 11, 4)) == (True, "by design", "structural")


def test_only_uniswap_is_given_the_code():
    assert set(config.BUYING_PRESSURE_BY_DESIGN) == {"Uniswap"}
    eth = _bp_cell(config.PROJECT_BY_NAME["Ethereum"])
    assert eth.startswith("n/a — no market buyback is measured"), "every other project's reason is unchanged"
