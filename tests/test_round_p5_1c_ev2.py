"""Round P5-1C, group ev2 (Jake 2026-11-04).

C5 Morpho: the numerator of every figure built on Morpho's customer revenue is labelled "gross borrower interest"
(config.NUMERATOR_LABEL, on the audit cell and on the reader's copy); the Comparison tab's subsidy cover says why it is
not Summary's A2 key (17.60 vs 16.95); morpho_claims.py reads the Merkl / URD Claimed events from BigQuery - a DRY RUN by
default, --apply to read - and splits them by reward date, printing the A2 key on the claimed, programmed and split
bases (the key's basis in the workbook is unchanged). C4 (Ether.fi vesting) is a report only - nothing changed.
No network: BigQuery is a stub.
"""
import json
import sqlite3
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import morpho_claims as mc  # noqa: E402
import reader  # noqa: E402


# ------------------------------------------------------------------------------------------------ the label (C5)
def test_numerator_label_names_gross_borrower_interest_for_morphos_customer_revenue_figures_only():
    fig = config.numerator_label("Morpho", "Customer revenue Q0 ($)")
    assert fig is not None and fig[0] == " gross borrower interest"
    assert "GROSS BORROWER INTEREST" in fig[1] and "DefiLlama" in fig[1]
    for head in ("Customer revenue ÷ emissions value (x)", "CUSTOMER REVENUE PER TOKEN EMITTED ($/token) — key ratio",
                 bw.CP_SUBSIDY_HEAD, bw.CP_SUBSIDY_BC_HEAD, "FREE FLOAT ÷ ARR (x) = free float market cap ÷ ARR",
                 "Rev ÷ emission at Q1 (−3m, $/token)", "LEAKAGE (ii) — incentive spend not returned as revenue",
                 "Customer revenue Δ30d"):
        assert config.numerator_label("Morpho", head)[0] == " (revenue = gross borrower interest)", head
    assert config.numerator_label("Morpho", "Fees Q0 ($)") is None
    assert config.numerator_label("Morpho", "Emissions to suppliers Q0 (tokens)") is None
    assert config.numerator_label("Aethir", "Customer revenue Q0 ($)") is None, "declared for Morpho only"
    # the metric the label describes is the one Morpho's customer revenue restates (fees_usd: borrower interest)
    assert config.NUMERATOR_LABEL["Morpho"]["metric"] == "customer_revenue_usd"
    assert config.PROJECT_BY_NAME["Morpho"]["metric_restatement"]["customer_revenue_usd"]["equals"] == "fees_usd"


def test_every_subsidy_and_a2_header_the_label_names_exists_in_the_builder():
    """A header prefix that matches nothing would label nothing: each must start a real A2 / CP / reader header."""
    import inspect
    src = inspect.getsource(bw) + inspect.getsource(reader)
    formed = {"Customer revenue Δ": '_trajectory(R, "customer_revenue_usd", "Customer revenue")'}  # f"{label} Δ30d"
    assert 'f"{label} Δ30d"' in src
    for h in config.NUMERATOR_LABEL_HEADS:
        assert (formed[h] if h in formed else h) in src, h


def _table(projects, specs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Z"
    bw._write_table(ws, None, projects, specs, {}, [])
    return ws


def test_the_audit_cell_carries_the_label_in_its_format_and_comment_and_the_value_stays_numeric():
    specs = [("Project", lambda r, p: p["name"], bw.FMT_TEXT, "text"),
             ("Customer revenue Q0 ($)", lambda r, p: 63_200_000.0, bw.FMT_USD, "pull"),
             ("Customer revenue ÷ emissions value (x)", lambda r, p: 16.95, bw.FMT_X, "calc", True),
             ("Fees Q0 ($)", lambda r, p: 1.0, bw.FMT_USD, "pull")]
    ws = _table([config.PROJECT_BY_NAME["Morpho"], config.PROJECT_BY_NAME["Aethir"]], specs)
    rev, key, fees = ws.cell(5, 2), ws.cell(5, 3), ws.cell(5, 4)
    assert rev.value == 63_200_000.0 and key.value == 16.95, "the number itself is unchanged"
    assert all(sec.endswith('" gross borrower interest"') for sec in rev.number_format.split(";"))
    assert all(sec.endswith('" (revenue = gross borrower interest)"') for sec in key.number_format.split(";"))
    assert "GROSS BORROWER INTEREST" in rev.comment.text and "GROSS BORROWER INTEREST" in key.comment.text
    assert "gross borrower" not in fees.number_format and fees.comment is None
    other = [ws.cell(6, c) for c in (2, 3)]
    assert all("gross borrower" not in c.number_format and c.comment is None for c in other), "Aethir unlabelled"


class _Sh:
    def __init__(self):
        self.ws = openpyxl.Workbook().active


def test_the_readers_copy_carries_the_label_beside_the_number():
    sh = _Sh()
    sh.ws["B5"] = 16.95
    sh.ws["B5"].number_format = reader.FMT_X
    reader.numerator_label_cell(sh, "B5", "Morpho", "Customer revenue ÷ emissions value (x)")
    assert sh.ws["B5"].number_format == '0.0"x"" (revenue = gross borrower interest)"'
    sh.ws["C5"] = 1.0
    sh.ws["C5"].number_format = reader.FMT_X
    reader.numerator_label_cell(sh, "C5", "Aethir", "Customer revenue ÷ emissions value (x)")
    reader.numerator_label_cell(sh, "C5", "Morpho", "Price — spot ($)")
    assert sh.ws["C5"].number_format == reader.FMT_X


# ------------------------------------------------------------------- Comparison: 17.60 is not the A2 key 16.95 (C5)
def _src(v, head="h"):
    return reader.Src("CP Comparability", "A1", v, v, "" if v is None else str(v), None, "", head)


def test_comparison_says_why_subsidy_cover_17_60_is_not_the_a2_key_16_95():
    assert config.outflow_metrics("Morpho", "b") == ("emissions_tokens",)
    s = reader.subsidy_vs_key_note(config, "Morpho", _src(17.60), _src(16.95))
    assert "NOT THE A2 RANK KEY" in s and "16.95x" in s and "17.60x" in s
    # PR1B.3 (lead 2026-11-09): the key's emissions $ is at each day's price too — what is left is the dating of a row
    assert "both at each day's price (PR1B.3)" in s and "THAT PERIOD's mean price" in s and "= 1.038" in s
    assert "90-day" not in s and "AVERAGE price" not in s


def test_comparison_note_names_different_tokens_and_is_silent_without_two_numbers():
    b = config.outflow_metrics("Aethir", "b")
    assert b and b != ("emissions_tokens",)
    s = reader.subsidy_vs_key_note(config, "Aethir", _src(8.0), _src(9.0))
    assert "Different tokens" in s and b[0] in s and "90-day" not in s
    assert reader.subsidy_vs_key_note(config, "Morpho", _src(None), _src(16.95)) == ""
    assert reader.subsidy_vs_key_note(config, "Morpho", _src(17.6), None) == ""
    assert reader.subsidy_vs_key_note(config, "Morpho", _src(17.6), _src(0.0)) == ""
    defn = next(c for c in reader.COMPARISON_COLS if c[0] == "subsidy")[4]
    assert "NOT Summary's rank key" in defn


# ------------------------------------------------------------------------------------- morpho_claims.py (C5)
CFG = config.MORPHO_CLAIMS_SPLIT
MERKL, URD = (a.lower() for a in (CFG["distributors"]["merkl"], CFG["distributors"]["urd"]))
CLAIMED, ROOTSET = CFG["topics"]["Claimed"], CFG["topics"]["RootSet"]


def _row(day, block, who, topic, amount=0.0):
    return {"day": day, "block_number": block, "distributor": who, "topic0": topic,
            "data": "0x" + format(int(round(amount * 1e18)), "064x") if topic == CLAIMED else "0x"}


def test_the_recorded_read_and_the_sql_are_what_the_research_found():
    assert CFG["claimed"] == {"merkl": 57_563.49, "urd": 1_000_459.94}
    assert CFG["urd_last_root"] == {"day": "2025-08-11", "block": 23_116_386}
    assert CFG["programmed"]["ethereum"] == 44_397.6 and CFG["programmed"]["ethereum_computed_on_ethereum"] == 17_092.9
    from eth_hash.auto import keccak
    assert CLAIMED == "0x" + keccak(b"Claimed(address,address,uint256)").hex()
    assert ROOTSET == "0x" + keccak(b"RootSet(bytes32,bytes32)").hex()
    sql = (ROOT / "sql" / "ethereum" / CFG["sql"]).read_text()
    from fetch.near_bigquery import NearBigQuery
    allowed = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]["allowed_dataset"]
    NearBigQuery._guard_sql(sql, {"allowed_dataset": allowed})          # names only the public Ethereum dataset
    with pytest.raises(PermissionError):
        NearBigQuery._guard_sql(sql.replace("bigquery-public-data.crypto_ethereum", "other.x"),
                                {"allowed_dataset": allowed})
    for p in ("@d0", "@d1", "@distributors", "@topics", "@token_topics"):
        assert p in sql
    q = mc.query_params(CFG, "2026-08-07", "2026-11-04")
    assert q["topics"][0] == CLAIMED, "the SQL keeps a Claimed row only for the token: Claimed must be first"
    assert q["token_topics"] == ["0x" + "0" * 24 + CFG["token"].lower()[2:]]
    assert set(q["distributors"]) == {MERKL, URD} and str(q["d0"]) == "2026-08-07"


def test_split_dates_urd_claims_by_the_root_and_bounds_merkl_by_the_programmed_amount():
    rows = [_row("2026-08-10", 10, MERKL, CLAIMED, 50.0), _row("2026-09-01", 20, URD, CLAIMED, 1000.0),
            _row("2026-10-13", 30, MERKL, CLAIMED, 7.5), _row("2026-10-20", 40, URD, CLAIMED, 200.0),
            _row("2026-10-01", 25, "0xother", CLAIMED, 99.0)]
    ev = mc.parse_rows(rows, CFG)
    assert [e["who"] for e in ev] == ["merkl", "urd", "merkl", "urd"], "another address is not read"
    sp = mc.split(ev, programmed_eth=40.0)
    assert sp["merkl_claimed"] == pytest.approx(57.5) and sp["urd_claimed"] == pytest.approx(1200.0)
    assert sp["urd_earlier_rewards"] == pytest.approx(1200.0) and sp["urd_undetermined"] == 0
    assert sp["merkl_window_rewards_max"] == pytest.approx(40.0)
    assert sp["merkl_earlier_rewards_min"] == pytest.approx(17.5)
    # a RootSet inside the window: the URD's later claims are UNDETERMINED, never counted as the window's own
    sp2 = mc.split(mc.parse_rows(rows + [_row("2026-10-01", 26, URD, ROOTSET)], CFG), programmed_eth=40.0)
    assert sp2["urd_earlier_rewards"] == pytest.approx(1000.0) and sp2["urd_undetermined"] == pytest.approx(200.0)
    assert sp2["urd_roots_in_window"] == ["2026-10-01"]
    # no programmed amount for the window: Merkl is not bounded (earlier rewards at least 0, never guessed)
    sp3 = mc.split(ev, programmed_eth=None)
    assert sp3["merkl_window_rewards_max"] == pytest.approx(57.5) and sp3["merkl_earlier_rewards_min"] == 0


def test_the_a2_key_on_each_basis_and_unknown_never_zero():
    assert mc.a2_key(63.2e6, 90, 1_582_040, 90, 2.3568) == pytest.approx(16.95, abs=0.01)
    assert mc.a2_key(63.2e6, 90, 0, 90, 2.0) is None and mc.a2_key(None, 90, 1.0, 90, 2.0) is None
    assert mc.a2_key(63.2e6, 90, 1.0, 0, 2.0) is None
    sp = mc.split([{"day": "d", "block": 1, "who": "merkl", "event": "Claimed", "amount": 57_563.49},
                   {"day": "d", "block": 2, "who": "urd", "event": "Claimed", "amount": 1_000_459.94}], 44_397.6)
    store = {"rev_usd": 63.2e6, "rev_days": 90, "emissions_tokens": 1_582_040.0, "emis_days": 90, "price": 2.3568,
             "window_days": 90}
    k = mc.key_bases(store, sp, 758_736.5)
    assert k["claimed"][1] == pytest.approx(16.95, abs=0.01)
    assert k["programmed"][1] == pytest.approx(35.34, abs=0.01)
    assert k["split_high"][0] == pytest.approx(1_582_040 - 1_000_459.94 - 13_165.89, abs=0.01)
    assert k["split_high"][1] == pytest.approx(47.18, abs=0.01)
    assert k["split_low"][1] == pytest.approx(51.17, abs=0.01)
    # a store holding fewer claims than the read: the split is unknown, never a negative or zero denominator
    k2 = mc.key_bases(dict(store, emissions_tokens=500.0), sp, 758_736.5)
    assert k2["split_high"] == (None, None) and k2["split_low"] == (None, None)
    assert mc.key_bases(dict(store, emissions_tokens=None), sp, None)["programmed"] == (None, None)
    # Merkl's schedule covers every day of the window, the claims the days the store holds them
    k3 = mc.key_bases(dict(store, emis_days=45), sp, 758_736.5)
    assert k3["programmed"][1] == pytest.approx(35.34, abs=0.01) and k3["claimed"][1] == pytest.approx(8.475, abs=0.01)


def _store(path: Path, legs: dict, rev: float = 700_000.0, price: float = 2.0, days=("2026-08-07", "2026-08-08")):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metrics (date TEXT NOT NULL, project TEXT NOT NULL, metric TEXT NOT NULL, value REAL, "
                "source TEXT NOT NULL, tier INTEGER, fetched_at TEXT NOT NULL, PRIMARY KEY (date, project, metric))")
    for d in days:
        rows = [("customer_revenue_usd", rev), ("price_usd", price)] + list(legs.items())
        for m, v in rows:
            con.execute("INSERT INTO metrics VALUES (?,?,?,?,?,?,?)", (d, "Morpho", m, v, "test", 1, d))
    con.commit()
    con.close()
    return path


def test_store_inputs_sums_the_four_claim_legs_read_only_and_a_missing_leg_is_unknown(tmp_path):
    legs = config.PROJECT_BY_NAME["Morpho"]["emissions_from_metric"]
    assert len(legs) == 5                                           # PR4F.2: + the legacy URD leg
    db = _store(tmp_path / "m.db", {m: 10.0 for m in legs})
    s = mc.store_inputs(db, "2026-08-07", "2026-08-08")
    assert s["rev_usd"] == 1_400_000.0 and s["rev_days"] == 2 and s["price"] == 2.0 and s["window_days"] == 2
    assert s["emissions_tokens"] == 100.0 and s["emis_days"] == 2
    db2 = _store(tmp_path / "m2.db", {legs[0]: 10.0})
    assert mc.store_inputs(db2, "2026-08-07", "2026-08-08")["emissions_tokens"] is None, "unknown, never a partial sum"
    db3 = _store(tmp_path / "m3.db", {"emissions_tokens": 5.0})
    assert mc.store_inputs(db3, "2026-08-07", "2026-08-08")["emissions_tokens"] == 10.0, "an older store's series"
    assert mc.store_inputs(tmp_path / "none.db", "2026-08-07", "2026-08-08") == {"window_days": 2}
    assert not (tmp_path / "none.db").exists(), "never creates a store"


class _StubBQ:
    calls: list = []
    est = 123_400_000_000

    def __init__(self):
        type(self).calls = []

    def _bq_spec(self):
        return {"allowed_dataset": "bigquery-public-data.crypto_ethereum."}

    def _client(self, spec):
        return object(), None

    def _dry(self, client, sql, params, spec):
        type(self).calls.append(("dry", params))
        return type(self).est

    def _load(self):
        return {"ledger": {}}

    def _run(self, client, what, sql, params, spec, st, out, name, backfill=False):
        type(self).calls.append(("run", backfill))
        return [_row("2026-08-10", 10, MERKL, CLAIMED, 57_563.49), _row("2026-10-13", 20, URD, CLAIMED, 1_000_459.94)]


def test_the_script_dry_runs_by_default_and_reads_nothing(monkeypatch, tmp_path, capsys):
    import fetch.eth_settlement as es
    monkeypatch.setattr(es, "EthP2PBigQuery", _StubBQ)
    monkeypatch.setattr(mc, "OUT_DIR", tmp_path / "out")
    assert mc.main(["--db", str(tmp_path / "none.db")]) == 0
    out = capsys.readouterr().out
    assert "dry run 123.4 GB (no data read, no quota)" in out and "nothing read (add --apply" in out
    assert [c[0] for c in _StubBQ.calls] == ["dry"], "no query without --apply"
    assert not (tmp_path / "out").exists()


def test_apply_refuses_above_the_cap_then_reads_splits_and_writes(monkeypatch, tmp_path, capsys):
    import fetch.eth_settlement as es
    monkeypatch.setattr(es, "EthP2PBigQuery", _StubBQ)
    monkeypatch.setattr(mc, "OUT_DIR", tmp_path / "out")
    assert mc.main(["--apply", "--max-gb", "100", "--db", str(tmp_path / "none.db")]) == 3
    assert "REFUSED" in capsys.readouterr().out and [c[0] for c in _StubBQ.calls] == ["dry"]
    legs = config.PROJECT_BY_NAME["Morpho"]["emissions_from_metric"]
    days = [f"2026-{m:02d}-{d:02d}" for m, d in [(8, x) for x in range(7, 32)] + [(9, x) for x in range(1, 31)]
            + [(10, x) for x in range(1, 32)] + [(11, x) for x in range(1, 5)]]
    assert len(days) == 90
    db = _store(tmp_path / "m.db", {legs[0]: 1_582_040.0 / 90 - 3, legs[1]: 1.0, legs[2]: 1.0, legs[3]: 1.0,
                                      legs[4]: 0.0},                    # PR4F.2: the legacy leg, stored
                rev=63.2e6 / 90, price=2.3568, days=days)
    assert mc.main(["--apply", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert [c for c in _StubBQ.calls] == [("dry", _StubBQ.calls[0][1]), ("run", True)], "budgeted as a backfill"
    assert "earlier rewards 1,000,459.94; undetermined 0.00 (no RootSet inside the window)" in out
    assert "gross borrower interest" in out
    res = json.loads((tmp_path / "out" / "claims_split_2026-08-07_2026-11-04.json").read_text())
    assert res["split"]["claimed"] == pytest.approx(1_058_023.43) and res["dry_run_bytes"] == _StubBQ.est
    assert res["keys"]["claimed"][1] == pytest.approx(16.95, abs=0.01)
    assert res["keys"]["programmed"][1] == pytest.approx(35.34, abs=0.01)
    assert res["keys"]["split_high"][1] == pytest.approx(47.18, abs=0.01)


def test_another_window_has_no_programmed_amount_and_recorded_mode_needs_no_bigquery(monkeypatch, tmp_path, capsys):
    import fetch.eth_settlement as es
    monkeypatch.setattr(es, "EthP2PBigQuery", _StubBQ)
    monkeypatch.setattr(mc, "OUT_DIR", tmp_path / "out")
    assert mc.main(["--apply", "--from", "2026-08-01", "--to", "2026-08-31", "--db", str(tmp_path / "x.db")]) == 0
    out = capsys.readouterr().out
    assert "programmed on Ethereum not read for this window" in out
    res = json.loads((tmp_path / "out" / "claims_split_2026-08-01_2026-08-31.json").read_text())
    assert res["split"]["merkl_earlier_rewards_min"] == 0
    monkeypatch.setattr(es, "EthP2PBigQuery", None)                     # recorded mode never touches BigQuery
    assert mc.main(["--recorded", "--db", str(tmp_path / "x.db")]) == 0
    out = capsys.readouterr().out
    assert "3,131 claims, 1,058,023.43 MORPHO" in out and "earlier rewards at least 13,165.89" in out
    assert "A2 KEY: no metrics.db rows" in out
