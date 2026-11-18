"""Round 7C (Jake 2026-11-16): gate, then sign off Sky and Hyperliquid. Behaviour and values within tolerance. No network.

A1  decision 1b: Sky's and Hyperliquid's dumps refreshed from Jake's store exports (dump_fixture.py --from-export), cut
    at the dumps' common as-of so no frozen project moves; the two lost headlines hold values again.
B1  Sky's insider share of circulating = the endowments ÷ circulating (config.INSIDER_INSIDE_CIRCULATING).
C3  the USDH reserve yield: the Fund's ledger evidence, named in HOLDER SHARE's gap, 0 in Q0.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402
import xlcalc  # noqa: E402
from shared_build import plain_build  # noqa: E402

CP, A4, A1 = "CP Comparability", "A4 Permanent Burn", "A1 Infrastructure"


# ================================================================================== A1: the dumps from the exports
def _export(tmp_path, project, rows, rq=(), mo=()):
    p = tmp_path / f"{project.lower()}-x.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        json.dump({"meta": {"project": project, "dumped_at": "2026-11-16T18:00:00Z", "git_commit": "abc"},
                   "metrics": rows, "review_queue": list(rq), "manual_overrides": list(mo), "run_log": [{"ts": "x"}],
                   "gap_report": [{"ts": "x"}]}, fh)
    return p


def test_a1_from_export_cuts_at_the_old_dumps_asof_and_keeps_its_run_log(tmp_path):
    import dump_fixture
    old = tmp_path / "real_sky.json"
    old.write_text(json.dumps({"meta": {"project": "Sky", "asof": "2026-10-13", "days": 400},
                               "metrics": [], "run_log": [{"ts": "2026-10-13T16:00:00Z"}],
                               "gap_report": [{"ts": "2026-10-13"}], "manual_references": [{"r": 1}]}), encoding="utf-8")
    row = lambda d, v: {"date": d, "metric": "m", "value": v, "source": "chain", "tier": 2,  # noqa: E731
                        "fetched_at": "2026-11-16T00:00:00Z"}
    ex = _export(tmp_path, "Sky", [row("2025-07-01", 1.0), row("2026-10-13", 2.0), row("2026-10-14", 3.0)],
                 rq=[{"ts": "2026-10-12T00:00:00Z"}, {"ts": "2026-11-01T00:00:00Z"}],
                 mo=[{"date": "2026-01-31"}, {"date": "2026-11-01"}])
    out = json.loads(dump_fixture.from_export(ex, old, tmp_path / "new.json").read_text(encoding="utf-8"))
    assert [r["value"] for r in out["metrics"]] == [1.0, 2.0]               # nothing after the as-of; history kept
    assert out["meta"]["asof"] == "2026-10-13" and len(out["review_queue"]) == 1 and len(out["manual_overrides"]) == 1
    assert out["run_log"] == [{"ts": "2026-10-13T16:00:00Z"}] and out["gap_report"] == [{"ts": "2026-10-13"}]
    assert out["meta"]["refreshed_from_export"]["decided"].startswith("Jake 2026-11-16")
    with pytest.raises(SystemExit):
        dump_fixture.from_export(_export(tmp_path, "Hyperliquid", []), old, tmp_path / "bad.json")


def test_a1_a_frozen_projects_dump_is_never_refreshed(tmp_path, capsys):
    import dump_fixture
    for p in config.FROZEN_PROJECTS:
        assert dump_fixture.main(["--project", p, "--from-export", str(tmp_path / "x.json.gz")]) == 2
        assert "frozen" in capsys.readouterr().out


def test_a1_only_sky_and_hyperliquid_are_refreshed_all_at_the_common_asof():
    import real_dump
    asofs = {}
    for f in sorted((ROOT / "fixtures").glob("real_*.json")):
        meta = json.loads(f.read_text(encoding="utf-8"))["meta"]
        asofs[meta["project"]] = str(meta["asof"])[:10]
        if meta["project"] in config.FROZEN_BEFORE_R7D:      # frozen when the two dumps were refreshed
            assert "refreshed_from_export" not in meta, meta["project"]
    for p in ("Sky", "Hyperliquid"):
        m = real_dump.load(p)["meta"]
        assert m["refreshed_from_export"]["cut_at_asof"] == m["asof"] == max(asofs.values())
    # the legs the lost headlines need now hold history before Q0 (the 10-13 dumps held 1-2 days)
    hl = real_dump.load("Hyperliquid")["metrics"]
    for leg in ("excluded_wallets_tokens_replay", "team_wallet_tokens_replay", "staked_noncirculating_tokens_replay"):
        assert sum(r["metric"] == leg for r in hl) > 90, leg
    sky = real_dump.load("Sky")["metrics"]
    assert sum(r["metric"] == "sky_endowment_locked_tokens" for r in sky) > 90


def test_a1_the_rank_moves_are_declared_with_their_cause():
    for p, to in (("Fluid", 4.0), ("Maple", 3.0)):
        for k in ("reader|A3 Revenue Buyback|Rank in A3", "reader|Summary|Rank in archetype"):
            d = config.FROZEN_MOVES[p][k]["then"][-1]         # R7D.A1: appended; the earlier declaration stays
            assert d["to"] == to and "R7C.A1" in d["why"] and "values are unchanged" in d["why"]


# ======================================================================================= on the 15 dumps (one build)
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    rec = plain_build(tmp_path_factory, "r7c")
    a = openpyxl.load_workbook(rec["audit"])
    return {"audit": a, "XA": xlcalc.Workbook(a)}


def _rows(ws, first=5) -> dict:
    """Each project's FIRST row (a tab can repeat the names in a lower block, e.g. A1's monthly ratios)."""
    out: dict = {}
    for r in range(first, ws.max_row + 1):
        if ws.cell(r, 1).value:
            out.setdefault(ws.cell(r, 1).value, r)
    return out


def _col(ws, pred) -> int:
    return next(c for c in range(1, ws.max_column + 1) if pred(str(ws.cell(4, c).value or "")))


def _val(built, tab, project, pred):
    ws = built["audit"][tab]
    return built["XA"].value(tab, _rows(ws)[project], _col(ws, pred))


def test_a1_the_two_lost_headlines_hold_values_and_hyperliquid_balances(built):
    pool = _val(built, A4, "Hyperliquid", lambda h: h.startswith("Pool release Q0 (tokens)"))
    # R9.2 (the rule): the team wallet is circulating, so its payouts are no release INTO circulation (they stay the
    # unlock leg, R9.3) — what enters is the staking rewards: 214,122 on the dump's stored days (was 3.8166e6)
    assert isinstance(pool, float) and pool == pytest.approx(214_122.0, rel=0.05)
    nrr = _val(built, A1, "Hyperliquid", lambda h: h.startswith("NRR ON TRADING THROUGHPUT"))
    assert isinstance(nrr, float) and 0 < nrr < 0.05
    assert "Hyperliquid" not in reader.flow_blocks_of(built["audit"], bw)


def test_a1_sky_float_forms_on_its_refreshed_dump(built):
    f = _val(built, CP, "Sky", lambda h: h == bw.CP_FLOAT_HEAD)
    c = _val(built, CP, "Sky", lambda h: h.startswith("Circulating supply (tokens)"))
    assert isinstance(f, float) and 0 < f < c


# ============================================================================== B1: Sky's insider share of circulating
def test_b1_sky_insider_share_is_the_endowments_over_circulating(built):
    spec = config.INSIDER_INSIDE_CIRCULATING["Sky"]
    # R9.2: + the converter's accrued fee and the SKY backing the Pause Proxy's MKR (in circulating by the rule)
    assert spec["held"][0] == "sky_endowment_tokens" and "R7B.B1" in spec["decided"]
    share = _val(built, CP, "Sky", lambda h: h.startswith("Insider share of circulating"))
    c = _val(built, CP, "Sky", lambda h: h.startswith("Circulating supply (tokens)"))
    import real_dump
    endow = max((r for r in real_dump.load("Sky")["metrics"] if r["metric"] == "sky_endowment_tokens"),
                key=lambda r: r["date"])["value"]
    # R9.2 (the rule): + the converter's accrued fee and the SKY backing the Pause Proxy's MKR (about 40M, 0.17% of
    # circulating) — issuer-controlled and now inside circulating; the endowments are the rest
    assert isinstance(share, float) and endow / c < share < endow / c + 0.002
    assert 0.10 < share < 0.15                                   # ~12.7%: 2.97B of ~23.4B
    # a frozen project's cell is untouched: none is in the map
    assert not set(config.INSIDER_INSIDE_CIRCULATING) & set(config.FROZEN_BEFORE_R7D)


# ======================================================================================== C3: the USDH reserve yield
def test_c3_usdh_yield_is_named_with_its_ledger_evidence_and_not_booked():
    ev = config.HL_USDH_YIELD_EVIDENCE
    assert ev["last"] == "2026-05-26" and ev["sender_documented"] is False and ev["total_usdh"] > 0
    assert "USDH" in config.HL_HOLDER_SHARE_GAP and "2026-05-26" in config.HL_HOLDER_SHARE_GAP
    assert "50% to the Fund from 2026-09-17" not in config.HL_HOLDER_SHARE_GAP
    route = config.REVENUE_SPLIT["Hyperliquid"]["revenue"]["route"]
    assert "0 in Q0" in route and "not booked" in route
    # booked only from a documented sender (G2-D4): the AQAv2 read never lists the USDH sender
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]["extra_reads"]
                if r.get("shape") == "aqa_payments")
    assert ev["sender"] not in {a.lower() for a in read["interest_addresses"]}


# =============================================================== B2: Sky's MKR detail rows — a declared handover
def test_b2_mkr_detail_rows_hand_over_from_the_archive_point_and_the_live_read_wins(monkeypatch):
    import pandas as pd
    from fetch.base import _measuring_point
    sky = config.PROJECT_BY_NAME["Sky"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [sky])
    arch = "sky_mkr:(mkr.totalSupply-pause_proxy)xratex(1-fee):archive"
    for m, live in config.SKY_MKR_DETAIL_POINTS.items():
        d = config.declared_handover("Sky", m)
        assert d["cut"] and d["ordered_points"] == (arch, live) and "don't hide it" in d["decided"]
        a_days, l_days = pd.date_range("2026-10-01", "2026-10-15"), pd.date_range("2026-10-12", "2026-10-16")
        g = pd.concat([pd.DataFrame({"date": a_days, "project": "Sky", "metric": m, "value": 1.0, "source": arch,
                                     "tier": 2}),
                       pd.DataFrame({"date": l_days, "project": "Sky", "metric": m, "value": 1.0, "source": live,
                                     "tier": 2})], ignore_index=True)
        groups = {("Sky", m): g}
        bw._handover_cut_views(groups)
        out = groups[("Sky", m)]
        assert out.loc[out["source"] == arch, "date"].max() == pd.Timestamp("2026-10-11")   # the archive rows after
        assert len(out) == 11 + 5                                                             # the live start dropped
        mp = out["source"].map(_measuring_point)
        spans = {p: (out.loc[mp == p, "date"].min(), out.loc[mp == p, "date"].max()) for p in set(mp)}
        assert bw.handover_refusal("Sky", m, set(mp), spans) is None, m


def test_b2_the_backfill_labels_each_detail_series_with_its_own_call(monkeypatch):
    import pandas as pd
    import trajectory
    from fetch import sky_mkr
    monkeypatch.setattr(sky_mkr, "today", lambda: pd.Timestamp("2026-11-16"))
    spec = config.PROJECT_BY_NAME["Sky"]["mkr_backing"]
    word = lambda v: "0x" + hex(int(v))[2:].rjust(64, "0")  # noqa: E731

    def call(to, data, block):
        if to == spec["converter"]:
            return word(24_000) if data.startswith("0x2c4e722e") else word(5e16)          # rate() else fee()
        return word(82_664.63e18) if data.startswith("0x18160ddd") else word(49.56e18)

    class Blocks:
        def at(self, day, head):
            return 26_000_000
    rows, why = sky_mkr.backing_history(config.PROJECT_BY_NAME["Sky"], None, Blocks(), 2, set(), 26_000_000, call=call)
    assert why == "reached the start of the window"
    by = {}
    for r in rows:
        by.setdefault(r["metric"], set()).add(r["source"])
    for m, live in config.SKY_MKR_DETAIL_POINTS.items():
        (src,) = by[m]
        assert src.endswith(":archive") and trajectory.basis_of(m, src) == trajectory.basis_of(m, live), m
    (bsrc,) = by["sky_mkr_nonissuer_backing_tokens"]
    assert "xratex(1-fee)" in bsrc


# ================================================================== the export bug (Jake's addendum, 2026-11-16)
def test_store_export_names_are_unique_per_run_and_never_overwrite(tmp_path, monkeypatch):
    import re
    import sqlite3
    import store as sm
    import store_export
    db = tmp_path / "metrics.db"
    sm.Store(db).close()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO metrics (date, project, metric, value, source, tier, fetched_at) VALUES "
                "('2026-11-15', 'Sky', 'm', 1.0, 'chain', 2, '2026-11-16T00:00:00Z')")
    con.commit()
    con.close()
    p1 = store_export.export("Sky", str(db), tmp_path / "exports")
    assert re.fullmatch(r"sky-\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-z]+\.json\.gz", p1.name), p1.name
    # a file already tracked on the data branch under the old fixed name can no longer be the same path
    assert p1.name != f"sky-{p1.name[4:14]}.json.gz"
    import datetime as _dt

    class Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.strptime(p1.name.split("-")[4], "%H%M%S").replace(
                year=int(p1.name[4:8]), month=int(p1.name[9:11]), day=int(p1.name[12:14]), tzinfo=tz)
    monkeypatch.setattr(store_export, "datetime", Frozen)
    with pytest.raises(SystemExit):                       # the same second and commit: refused, never overwritten
        store_export.export("Sky", str(db), tmp_path / "exports")


def test_the_runbook_export_steps_are_safe_against_checkout():
    rb = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = rb[rb.index("## 11eu."):]
    sec = sec[:sec.index("\n## ", 5)]
    assert "store_export.py --project Sky --out-dir ..\\tm-exports" in sec
    i_switch, i_copy = sec.index("git switch data/store-exports"), sec.index("Copy-Item ..\\tm-exports\\*.json.gz exports\\")
    assert i_switch < i_copy                              # copied in only AFTER the switch: checkout cannot overwrite it
