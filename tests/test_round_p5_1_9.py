"""P5-1.9 (Jake 2026-11-04): Ethereum settlement without Artemis. First message: extend the rebuild, validate it
against the Artemis CSV on 2026-05-28..08-25, remove Artemis only after that. Second message: the BigQuery definition IS
the series (no +-1% test; validate is a reference); extend back to 2024-11-04; size the exchange double count first
(cex-sweep, not subtracted); no BigQuery query, Artemis removal, purge or fallback until Jake confirms.
settlement_extend.py. No network: BigQuery is stubbed."""
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import settlement_extend as se  # noqa: E402

DAYS = pd.date_range("2026-05-28", "2026-08-25")


def _legs(scale=1.0, drop=()):
    dex = pd.Series(2e9, index=DAYS)
    nft = pd.Series(1e7, index=DAYS)
    p2p = pd.Series(3e9 * scale, index=DAYS).drop([pd.Timestamp(d) for d in drop])
    csv = pd.Series(5.01e9, index=DAYS)
    return dex, nft, p2p, csv


def test_the_overlap_is_a_reference_that_decides_nothing():
    ref = se.overlap_check(*_legs(scale=1.0227))                 # +6.8%-style, as Jake's G9 run measured
    assert "status" not in ref and "decides nothing" in ref["reference"]
    assert ref["days_with_both"] == 90 and ref["ratio_minus_1"] == pytest.approx(0.0227 * 3 / 5.01, rel=1e-6)
    assert "rebuild / CSV - 1 = +" in ref["summary"]
    assert not hasattr(se, "TOL"), "no +-1% test (Jake 2026-11-04)"


def test_a_missing_overlap_day_is_named():
    res = se.overlap_check(*_legs(drop=("2026-06-10",)))
    assert res["summary"].startswith("89 of 90 days hold both")
    assert res["missing_days"] == {"p2p": ["2026-06-10"]}


def _db(tmp_path, rows):
    import store as sm
    db = tmp_path / "metrics.db"
    sm.Store(str(db)).close()
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO metrics (date, project, metric, value, source, tier, fetched_at) VALUES "
                    "(?, 'Ethereum', ?, ?, ?, 1, 'x')", rows)
    con.commit()
    con.close()
    return db


def test_validate_reads_the_store_read_only_and_writes_its_verdict(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(se, "RESULT", tmp_path / "data" / "ethereum" / "settlement_overlap.json")
    monkeypatch.setattr(se, "ROOT", tmp_path)
    rows = []
    for d in DAYS:
        ds = str(d.date())
        rows += [(ds, "dex_volume_usd", 2e9, "defillama:dexs:ethereum"), (ds, "nft_volume_usd", 1e7, "defillama:nft"),
                 (ds, "p2p_transfer_volume_usd", 3e9, "bigquery:p2p"),
                 (ds, "settlement_volume_usd", 5.01e9, "artemis:settlement_volume (CSV export, Jake, 2026-09-30)")]
    db = _db(tmp_path, rows)
    before = db.read_bytes()
    assert se.cmd_validate(db) == 0
    assert db.read_bytes() == before, "read-only"
    out = capsys.readouterr().out
    assert "SETTLEMENT OVERLAP 2026-05-28..2026-08-25 (REFERENCE ONLY - decides nothing): 90 of 90 days" in out
    assert "decides nothing" in json.loads(se.RESULT.read_text())["reference"]


def test_the_backfill_refuses_until_jakes_budget_decision_is_declared(monkeypatch, capsys):
    assert config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]["extend_approved"] is None
    assert se.cmd_backfill("2024-11-04", apply=True) == 3
    assert "REFUSED - the extension waits on Jake's BigQuery budget decision" in capsys.readouterr().out
    monkeypatch.setitem(config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"], "extend_approved", {"by": "test"})

    class R:
        def _client(self, bq):
            return None, "no credentials (test)"
    monkeypatch.setattr(se, "_p2p_reader", lambda: (R(), {}, {}, None, pd.Timestamp("2026-11-04")))
    monkeypatch.setattr(se, "_pieces", lambda r, spec, frm, now: ([], {}, {}))
    assert se.cmd_backfill("2024-11-04", apply=True) == 2, "past the gate; no client here"
    assert "REFUSED" not in capsys.readouterr().out


def test_the_extension_reaches_back_to_2024_11_04():
    assert se.EXTEND_FROM == "2024-11-04"


def test_the_dry_run_prices_every_weekly_piece_before_the_stored_history_and_reads_nothing(monkeypatch, capsys):
    from fetch import eth_settlement as es
    asked, ran = [], []

    class R(es.EthP2PBigQuery):
        def _client(self, spec):
            return object(), None

        def _load(self):
            return {"ledger": {}, "eth_p2p": {"days": {}}}

        def _dry(self, client, sql, params, spec=None):
            asked.append((params["d0"], params["d1"]))
            return 13_000_000_000

        def _run(self, *a, **k):
            ran.append(a)
            raise AssertionError("a dry run never runs a query")

        def _quota_line(self, bq, st):
            return "quota: test"
    monkeypatch.setattr(es, "EthP2PBigQuery", R)
    monkeypatch.setattr(se, "_p2p_reader", lambda: (R(), es._spec(), R()._bq_spec(), None,
                                                   pd.Timestamp("2026-11-04")))
    assert se.cmd_dry_run("2024-11-04") == 0
    out = capsys.readouterr().out
    assert asked[0][0] == pd.Timestamp("2024-11-04").date(), "starts on the date asked"
    assert all(pd.Timestamp(a) < pd.Timestamp("2026-05-28") for a, _b in asked), "only weeks with a day not stored yet"
    assert len(asked) == 82 and not ran
    assert f"TOTAL {len(asked) * 13:,.1f} GB" in out


def test_the_dex_and_nft_legs_keep_their_whole_history():
    for m in ("dex_volume_usd", "nft_volume_usd"):
        assert ("Ethereum", m) in config.FULL_HISTORY_SERIES
    from fetch import base
    df = pd.DataFrame({"date": pd.to_datetime(["2025-05-01", "2026-11-03"]), "project": "Ethereum",
                       "metric": "dex_volume_usd", "value": [1.0, 2.0], "source": "defillama:dexs:ethereum"})
    assert len(base.window(df, 30)) == 2


@pytest.mark.parametrize("mode", ["validate", "dry-run", "cex-sweep", "backfill"])
def test_the_cli_takes_the_three_modes(mode):
    import argparse
    with pytest.raises(SystemExit):
        se.main([mode, "--help"])
    assert argparse


def test_the_runbook_gives_the_dry_runs_and_holds_every_query():
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = text[text.index("## 11ea."):text.index("## 11dz.")]
    i, j, k = (sec.index(c) for c in ("settlement_extend.py validate", "settlement_extend.py dry-run --from 2024-11-04",
                                      "settlement_extend.py cex-sweep"))
    assert i < j < k and "Nothing Artemis is removed yet" in sec and "PASTE BACK" in sec
    assert "settlement_extend.py cex-sweep --apply" in sec and "do not run" in sec.lower()
    assert "2025-05-01" not in sec


def test_no_fallback_until_jake_chooses_the_test():
    """Jake 2026-11-04 (after the round-1 report): no fallback to 2026-05-28 yet; validate decides nothing until the
    +6.8% is explained. The SPEC row and RUNBOOK 11ea say so; the A1 key's 730-day need is on the row."""
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    row = next(ln for ln in spec.splitlines() if ln.startswith("| P5-1.9 |"))
    assert "NO FALLBACK to 2026-05-28 yet" in row and "2024-11-04" in row and "NOT MEASURED" in row
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = text[text.index("## 11ea."):text.index("## 11dz.")]
    assert "no purge script and no fallback until Jake confirms" in sec
    assert "there is no ±1% test" in sec and "UNDOCUMENTED" in row and "same-CEX-label filter" not in row


# ---------- the exchange double count, sized (Jake 2026-11-04, decision 1) ----------

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def _lists(tmp_path, monkeypatch):
    lists = {"A": {"page_url": "https://a.example/por", "data_url": "https://a.example/por.json", "read_on": "2026-11-04",
                   "eth_addresses": ["0xAAA1", "0xaaa2", "0xdup"]},
             "B": {"page_url": "https://b.example/por", "data_url": "https://b.example/por.csv", "read_on": "2026-11-04",
                   "eth_addresses": ["0xbbb1", "0xDUP"]},
             "C": {"page_url": "https://c.example/por", "data_url": None, "read_on": "2026-11-04", "eth_addresses": [],
                   "why_none": "Merkle-tree PoR, no wallet list"}}
    p = tmp_path / "por.json"
    p.write_text(json.dumps(lists))
    monkeypatch.setattr(se, "POR_LISTS", p)
    return lists


def test_the_lists_are_lower_cased_once_and_a_shared_address_is_dropped_and_named(tmp_path, monkeypatch):
    _lists(tmp_path, monkeypatch)
    addrs, names, _l, dropped = se.por_wallets()
    assert dict(zip(addrs, names)) == {"0xaaa1": "A", "0xaaa2": "A", "0xbbb1": "B"}
    assert dropped == ["0xdup"], "listed by two exchanges: given to neither"


def test_the_week_is_the_newest_with_all_seven_p2p_days_stored():
    held = pd.Series(1.0, index=pd.date_range("2026-10-19", "2026-11-02").drop(pd.Timestamp("2026-10-29")))
    assert se.sweep_week(held, None) == (pd.Timestamp("2026-10-19"), pd.Timestamp("2026-10-25"))
    assert se.sweep_week(held, "2026-10-26")[1] == pd.Timestamp("2026-11-01")
    with pytest.raises(ValueError, match="not a Monday"):
        se.sweep_week(held, "2026-10-27")


def test_rows_are_valued_as_the_p2p_leg_and_an_unpriced_row_is_named_never_zero():
    tokens = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]["tokens"]
    eth = pd.Series({pd.Timestamp("2026-10-20"): 4000.0})
    rows = [{"day": "2026-10-20", "token": "ETH", "exchange": "A", "sender": "unlisted", "amount": str(2 * 10 ** 18)},
            {"day": "2026-10-20", "token": WETH, "exchange": "A", "sender": "same_exchange", "amount": str(10 ** 18)},
            {"day": "2026-10-20", "token": USDC, "exchange": "B", "sender": "other_exchange", "amount": str(5 * 10 ** 6)},
            {"day": "2026-10-21", "token": "ETH", "exchange": "B", "sender": "unlisted", "amount": "1"}]
    prices = {"2026-10-20": {f"ethereum:{USDC}": [1.0, 6]}}
    valued, why = se.value_sweep(rows, eth, prices, tokens)
    assert [round(r["usd"], 6) for r in valued] == [8000.0, 4000.0, 5.0]
    assert why == ["2026-10-21 ETH: no stored Ethereum price_usd"]
    s = se.sweep_summary(valued, 1_000_000.0)
    assert s["into_listed_wallets_usd"] == pytest.approx(12005.0)
    assert s["by_sender"]["unlisted"]["share_of_p2p"] == pytest.approx(0.008)
    assert s["by_sender"]["same_exchange"]["usd"] == pytest.approx(4000.0)
    assert s["by_exchange"]["A / unlisted"] == pytest.approx(8000.0)
    assert s["double_count_bounds"]["low_usd"] == pytest.approx(4000.0)
    assert s["double_count_bounds"]["high_share"] == pytest.approx(0.012)


def _sweep_store(tmp_path, artemis=False):
    rows = []
    for d in pd.date_range("2026-10-19", "2026-10-25"):
        ds = str(d.date())
        rows += [(ds, "p2p_transfer_volume_usd", 1e6, "bigquery:p2p"), (ds, "price_usd", 4000.0, "coingecko"),
                 (ds, "dex_volume_usd", 2e5, "defillama:dexs:ethereum"), (ds, "nft_volume_usd", 1e3, "defillama:nft")]
        if artemis:
            rows.append((ds, "settlement_volume_usd", 7e6, "artemis:settlement_volume (CSV export)"))
    return _db(tmp_path, rows)


ETH18 = 10 ** 18


class _BQ:
    asked, ran, gb = [], [], 14.0

    def _client(self, spec):
        return object(), None

    def _load(self):
        return {"ledger": {}, "eth_p2p": {"days": {}, "prices": {}}}

    def _dry(self, client, sql, params, spec=None):
        assert "@cex_addr" in sql and "contract_call_multi_token" in sql
        _BQ.asked.append(params)
        return int(_BQ.gb * 1e9)

    def _run(self, client, what, sql, params, spec, st, out, name, backfill=False, topup_days=0):
        _BQ.ran.append(backfill)
        r = lambda c, x, s, eth: {"day": "2026-10-20", "token": "ETH", "call_class": c, "to_exchange": x, "sender": s,
                                  "amount": str(eth * ETH18), "n": 1}                       # noqa: E731
        # $4,000 an ETH: 1,500 ETH = $6.0M in all - direct $4.0M (of which $0.1M into A from unlisted, $0.04M A->A),
        # single-token calls $0.4M, multi-token calls $1.6M
        return [r("direct", "", "", 965), r("direct", "A", "unlisted", 25), r("direct", "A", "same_exchange", 10),
                r("contract_call_single_token", "", "", 100), r("contract_call_multi_token", "", "", 400)]

    def _price(self, spec, day, coins):
        raise AssertionError("no stablecoin row, no price call")


def _patch_bq(monkeypatch, tmp_path):
    from fetch import eth_settlement as es
    _lists(tmp_path, monkeypatch)
    _BQ.asked, _BQ.ran, _BQ.gb = [], [], 14.0
    monkeypatch.setattr(se, "_p2p_reader", lambda: (_BQ(), es._spec(), {}, None, pd.Timestamp("2026-11-04")))
    monkeypatch.setattr(se, "SWEEP_DIR", tmp_path / "out")


def test_the_week_split_only_dry_runs_unless_applied_and_reads_the_store_read_only(tmp_path, monkeypatch, capsys):
    _patch_bq(monkeypatch, tmp_path)
    db = _sweep_store(tmp_path)
    before = db.read_bytes()
    assert se.cmd_cex_sweep(None, apply=False, db=db) == 0
    out = capsys.readouterr().out
    assert "P2P WEEK SPLIT 2026-10-19..2026-10-25 (DEX hypothesis (a) + exchange double count): 3 published " \
           "proof-of-reserves wallet(s) of 2 exchange(s) (A, B)" in out
    assert "dry run: 14.0 GB" in out and "nothing read" in out and not _BQ.ran
    p = _BQ.asked[0]
    assert dict(zip(p["cex_addr"], p["cex_name"])) == {"0xaaa1": "A", "0xaaa2": "A", "0xbbb1": "B"}
    assert (p["d0"], p["d1"]) == (pd.Timestamp("2026-10-19").date(), pd.Timestamp("2026-10-25").date())
    assert db.read_bytes() == before, "metrics.db is read-only"


def test_the_week_split_measures_hypothesis_a_the_double_count_and_every_correction(tmp_path, monkeypatch, capsys):
    _patch_bq(monkeypatch, tmp_path)
    db = _sweep_store(tmp_path)
    assert se.cmd_cex_sweep(None, apply=True, db=db) == 0
    out = capsys.readouterr().out
    assert _BQ.ran == [True], "one query, under the backfill budget"
    res = json.loads((tmp_path / "out" / "p2p_week_split_2026-10-19.json").read_text())
    assert res["p2p_query_usd"] == pytest.approx(6.0e6) and res["p2p_query_vs_stored"] == pytest.approx(6.0 / 7 - 1)
    cc = res["p2p_by_call_class_usd"]
    assert cc == pytest.approx({"direct": 4.0e6, "contract_call_single_token": 0.4e6, "contract_call_multi_token": 1.6e6})
    assert res["multi_token_vs_dex"] == pytest.approx(1.6e6 / 1.4e6)
    assert res["double_count_bounds"]["low_usd"] == pytest.approx(40_000) \
        and res["double_count_bounds"]["high_usd"] == pytest.approx(140_000)
    rb = res["rebuild_week_usd"]
    base = 1.4e6 + 7e3 + 7e6
    assert rb["as built (DEX + NFT + P2P)"] == pytest.approx(base)
    assert rb["(a) without the multi-token contract-call P2P"] == pytest.approx(base - 1.6e6)
    assert rb["(b) without the DEX leg (P2P + NFT)"] == pytest.approx(7e3 + 7e6)
    assert rb["(a) + exchange high"] == pytest.approx(base - 1.6e6 - 140_000)
    assert "vs_artemis" not in res, "the newest week is past the CSV"
    assert "contract_call_multi_token" in out and "THE WEEK'S REBUILD, WITH AND WITHOUT EACH CORRECTION" in out
    assert res["subtracted"] is False


def test_a_week_inside_the_csv_span_is_also_compared_with_artemis(tmp_path, monkeypatch, capsys):
    _patch_bq(monkeypatch, tmp_path)
    db = _sweep_store(tmp_path, artemis=True)
    assert se.cmd_cex_sweep("2026-10-19", apply=True, db=db) == 0
    res = json.loads((tmp_path / "out" / "p2p_week_split_2026-10-19.json").read_text())
    assert res["vs_artemis"]["(b) without the DEX leg (P2P + NFT)"] == pytest.approx((7e3 + 7e6) / 49e6 - 1)


def test_apply_refuses_a_dry_run_above_the_cap(tmp_path, monkeypatch, capsys):
    _patch_bq(monkeypatch, tmp_path)
    _BQ.gb = 31.0
    assert se.cmd_cex_sweep(None, apply=True, db=_sweep_store(tmp_path)) == 3
    assert "REFUSED - the dry run is 31.0 GB, above the 25 GB" in capsys.readouterr().out and not _BQ.ran


def test_explain_reads_the_store_and_the_cache_only(tmp_path, capsys):
    rows = []
    for i, d in enumerate(pd.date_range(*se.OVERLAP)):
        ds = str(d.date())
        dexv = 1e9 + 1e7 * (i % 7)
        rows += [(ds, "dex_volume_usd", dexv, "defillama:dexs:ethereum"), (ds, "nft_volume_usd", 1e6, "defillama:nft"),
                 (ds, "p2p_transfer_volume_usd", 16e9, "bigquery:p2p"), (ds, "price_usd", 4000.0, "coingecko"),
                 (ds, "settlement_volume_usd", 16e9 + dexv, "artemis:settlement_volume (CSV export)")]
    db = _db(tmp_path, rows)
    before = db.read_bytes()
    st = {"eth_p2p": {"days": {str(d.date()): {"ETH": str(10 ** 6 * ETH18), USDC: str(12 * 10 ** 15)}
                               for d in pd.date_range(*se.OVERLAP)},
                      "prices": {str(d.date()): {f"ethereum:{USDC}": [1.0, 6]} for d in pd.date_range(*se.OVERLAP)}}}
    assert se.cmd_explain(db, st=st) == 0
    out = capsys.readouterr().out
    assert "90 of 90 days hold all four" in out
    assert "daily (Artemis - P2P) on DEX: slope 1.00, R^2 1.00" in out, out
    assert "P2P mix:   ETH  USDC" in out and "25.0% 75.0%" in out
    assert db.read_bytes() == before


def test_the_sweep_sql_applies_the_daily_p2p_rules_exactly():
    daily = (ROOT / "sql" / "ethereum" / "bigquery_p2p_daily.sql").read_text()
    week = (ROOT / "sql" / "ethereum" / se.CEX_SQL).read_text()
    for rule in ("AND t.value > 0 AND t.receipt_status = 1 AND t.to_address IS NOT NULL",
                 "AND LOWER(t.from_address) != LOWER(t.to_address)", "AND c.address IS NULL",
                 "AND LOWER(tt.token_address) IN UNNEST(@tokens)", "AND LOWER(tt.from_address) != LOWER(tt.to_address)",
                 "AND LOWER(tt.from_address) != '0x0000000000000000000000000000000000000000'",
                 "'0x000000000000000000000000000000000000dead')", "AND c1.address IS NULL AND c2.address IS NULL"):
        assert rule in daily and rule in week, rule
    for leg in ("'same_exchange'", "'other_exchange'", "'unlisted'", "'direct'", "'contract_call_single_token'",
                "'contract_call_multi_token'"):
        assert leg in week


def test_the_committed_lists_are_each_cited_and_only_the_exchanges_own():
    lists = json.loads(se.POR_LISTS.read_text(encoding="utf-8"))
    assert "Third-party" in lists["_about"]
    addrs, names, got, dropped = se.por_wallets()
    with_list = {k for k, v in got.items() if v["eth_addresses"]}
    assert with_list == {"binance", "okx", "coinex", "bitfinex"}
    for k, v in got.items():
        assert v["page_url"] and v["read_on"] == "2026-11-04" and v["what_the_list_says_they_are"], k
        if v["eth_addresses"]:
            assert v["data_url"] and v["list_kind"], k
            assert all(len(a) == 42 and a == a.lower() and a.startswith("0x") for a in v["eth_addresses"]), k
    assert len(addrs) == 50 + 17383 + 7 + 4 and not dropped
    assert "UNLABELLED" in got["okx"]["list_kind"], "why the double count is given as bounds"


def test_the_dry_run_prints_total_and_quota_with_the_real_quota_line(monkeypatch, capsys, tmp_path):
    """Jake 2026-11-04: `dry-run` crashed - AttributeError: no _run_bytes in _quota_line (the counters were only set
    inside run()). The real _quota_line now runs; only BigQuery itself is stubbed."""
    from fetch import eth_settlement as es

    class R(es.EthP2PBigQuery):
        def _client(self, spec):
            return object(), None

        def _load(self):
            return {"ledger": {"2026-11": 123_000_000_000}, "eth_p2p": {"days": {}}}

        def _dry(self, client, sql, params, spec=None):
            return 9_500_000_000
    r = R(cache_file=tmp_path / "bq.json")
    assert r._run_bytes == 0 and r._backfill_bytes == 0, "set in the constructor"
    monkeypatch.setattr(es, "today", lambda: pd.Timestamp("2026-11-04"))
    import fetch.near_bigquery as nb
    monkeypatch.setattr(nb, "today", lambda: pd.Timestamp("2026-11-04"))
    monkeypatch.setattr(se, "_p2p_reader", lambda: (r, es._spec(), r._bq_spec(), None, pd.Timestamp("2026-11-04")))
    assert se.cmd_dry_run("2024-11-04") == 0
    out = capsys.readouterr().out
    assert "TOTAL 779.0 GB over 82 piece(s); QUOTA 2026-11: 0.00 GB billed this run (backfill 0.00 GB); month used " \
           "123.0 GB" in out
