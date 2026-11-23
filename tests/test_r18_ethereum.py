"""Round 18 — Ethereum (Jake 2026-11-24; SPEC "Round 18 — Ethereum"). Behaviour and numbers, never wording.

1. The three revenue lines: FEES all gas, REVENUE the base fee burned, HOLDER REVENUE the same burn.
2. The Ethereum Foundation's wallets: in circulating, off the float; published and INFERRED kept apart; its stake.
3. Settlement: Artemis's own series ends 2026-08-25 (the recorded read); the declared rebuild is untouched.
4. Staked ETH: a failed overlap test declares the change of quantity at the seam — the ±1% test itself is unchanged.
No BigQuery, no network: every read is a stub."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import staked_eth_rebuild as r  # noqa: E402
from fetch import base, ef_wallets as ef  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

ETH = config.PROJECT_BY_NAME["Ethereum"]
EF = ETH["ef_wallets"]
COLS = ["date", "project", "metric", "value", "source", "tier"]


def _g(metric, rows, source="x", project="Ethereum"):
    """rows: [(day, value)] or [(day, value, source)]"""
    return pd.DataFrame([{"date": pd.Timestamp(x[0]), "project": project, "metric": metric, "value": float(x[1]),
                          "source": x[2] if len(x) > 2 else source, "tier": 1} for x in rows], columns=COLS)


# ================================================================================================ 1. revenue lines
def test_the_three_lines_are_all_gas_the_burn_and_the_same_burn():
    s = config.revenue_split("Ethereum")
    assert s["fees"]["metric"] == "fees_usd_blockscout"                      # base + priority, every transaction
    assert s["revenue"]["metric"] == s["holder_revenue"]["metric"] == "holder_value_revenue_usd"
    # the burn credited as holder value IS the base-fee burn, all of it
    bc = config.HOLDER_VALUE["Ethereum"]["burn_credited"]
    assert bc["metric"] == "gross_burn_tokens" and bc["share_of_revenue"] == 1.0
    for role in ("fees", "revenue", "holder_revenue"):
        assert s[role]["metric"] in config.metrics_for_project(ETH), role
    assert config.not_yet_split_text("Ethereum")                              # still callable; no longer what a cell reads
    assert "Ethereum" in config.REVENUE_SPLIT


def test_fees_are_the_days_gas_in_eth_at_that_days_price(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    # two days of the dump: Blockscout txnsFee (ETH) and the day's price
    groups = {("Ethereum", "fees_native_tokens"): _g("fees_native_tokens", [("2026-09-30", 204.334638),
                                                                            ("2026-10-01", 289.305794)],
                                                     "blockscout_stats:txnsFee"),
              ("Ethereum", "price_usd"): _g("price_usd", [("2026-09-30", 2677.384504), ("2026-10-01", 2684.708553)])}
    bw._native_fee_usd_views(groups)
    fees = bw._daily(groups[("Ethereum", "fees_usd_blockscout")])
    assert fees[pd.Timestamp("2026-09-30")] == pytest.approx(204.334638 * 2677.384504)       # $547,082
    assert fees[pd.Timestamp("2026-10-01")] == pytest.approx(776_701.7, rel=1e-6)
    assert ("Ethereum", "fees_usd") not in groups                # DefiLlama's fees stay their own series


def test_the_holder_share_is_one_series_over_itself():
    s = config.revenue_split("Ethereum")
    burn, price = [230.751861, 68.362563, 233.119052], [2677.384504, 2684.708553, 2725.910538]
    rev = sum(b * p for b, p in zip(burn, price))
    hold = sum(b * p for b, p in zip(burn, price))
    assert s["revenue"]["metric"] == s["holder_revenue"]["metric"] and hold / rev == 1.0
    # the lines are not the DefiLlama series the Master cell reads (the cross-check)
    assert config.revenue_headline_metric("Ethereum") == "revenue_usd"


# ================================================================================================ 2. the Foundation
def test_the_wallet_list_every_address_with_its_source_and_tag():
    ws = EF["wallets"]
    assert len({w["address"].lower() for w in ws}) == len(ws) == 5
    pub = [w for w in ws if w["tag"] == "PUBLISHED"]
    inf = [w for w in ws if w["tag"] == "INFERRED"]
    assert len(pub) == 3 and len(inf) == 2
    for w in pub:               # a Foundation publication: its blog, or its co-executive director's announcement
        assert "blog.ethereum.org" in w["source"] or "x.com/hwwonx/status/1881413731780821405" in w["source"]
    for w in inf:
        assert w["source"].startswith("INFERRED") and "no Foundation publication" in w["source"]
    # the read of 2026-11-23, block 26,477,664 (ETH + WETH)
    assert sum(sum(w["read"]) for w in pub) == pytest.approx(95_118.0446)
    assert sum(sum(w["read"]) for w in inf) == pytest.approx(2_473.8858)
    assert len(set(EF["stake"]["validators"])) == 34 and EF["stake"]["read"] == pytest.approx(69_633.2443)
    assert EF["stake"]["treasury"].lower() == pub[0]["address"].lower()


def _reads(drop=None):
    return {w["address"].lower(): {"eth": w["read"][0], "weth": w["read"][1] if w["weth"] else None}
            for w in EF["wallets"] if w["key"] != drop}


def test_published_and_inferred_are_summed_apart_and_a_missing_wallet_is_no_sum():
    total, parts, missing = ef.sum_wallets(_reads(), EF["wallets"], "PUBLISHED")
    assert total == pytest.approx(95_118.0446) and len(parts) == 3 and not missing
    assert ef.sum_wallets(_reads(), EF["wallets"], "INFERRED")[0] == pytest.approx(2_473.8858)
    total, _parts, missing = ef.sum_wallets(_reads(drop="defi_safe"), EF["wallets"], "PUBLISHED")
    assert total is None and missing == ["defi_safe"]            # never the sum without one of its wallets
    assert ef.sum_wallets(_reads(drop="defi_safe"), EF["wallets"], "INFERRED")[0] == pytest.approx(2_473.8858)


WC = "0x020000000000000000000000c06145782f31030db1c40b203be6b0fd53410b6d"


def _vals(n=34, each=2_048_000_000_000, wc=WC):
    return [{"index": str(2_270_000 + i), "balance": str(each), "status": "active_ongoing",
             "validator": {"pubkey": k, "withdrawal_credentials": wc}} for i, k in enumerate(EF["stake"]["validators"][:n])]


def test_the_stake_is_every_declared_validator_on_the_treasurys_credentials():
    total, n, bad = ef.staked_sum(_vals(), EF["stake"]["validators"], EF["stake"]["treasury"])
    assert total == pytest.approx(34 * 2048.0) and n == 34 and not bad
    assert ef.staked_sum(_vals(33), EF["stake"]["validators"], EF["stake"]["treasury"])[0] is None      # one not answered
    moved = _vals()
    moved[5]["validator"]["withdrawal_credentials"] = "0x01" + "00" * 11 + "11" * 20
    assert ef.staked_sum(moved, EF["stake"]["validators"], EF["stake"]["treasury"])[0] is None          # credentials changed
    assert ef.credentials_address(WC) == EF["stake"]["treasury"].lower()
    assert ef.credentials_address("0x00" + "ab" * 31) == ""                                             # a BLS credential


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


class _Chain:
    """A stub execution RPC + beacon node: balances from the recorded read, one finalized block."""
    def __init__(self, fail_address=None, nonce=18, vals=None):
        self.fail, self.nonce, self.vals, self.calls = (fail_address or "").lower(), nonce, vals, []

    def post(self, url, json=None, timeout=None):
        if url.endswith("/validators"):
            assert sorted(json["ids"]) == sorted(EF["stake"]["validators"])
            return _Resp({"finalized": True, "data": self.vals if self.vals is not None else _vals()})
        m, p = json["method"], json["params"]
        self.calls.append(m)
        if m == "eth_getBlockByNumber":
            if p[0] != "finalized":                              # the history read: a past block, dated by its number
                self.block = int(p[0], 16)
                return _Resp({"result": {"number": p[0], "timestamp": hex(self.block)}})
            self.block = 26_477_664
            return _Resp({"result": {"number": hex(26_477_664), "timestamp": hex(1_795_434_839)}})
        assert p[-1] == hex(self.block)                          # every figure at the ONE block
        if m == "eth_getBalance":
            if p[0].lower() == self.fail:
                return _Resp({"error": {"message": "boom"}})
            return _Resp({"result": hex(int(round(_reads()[p[0].lower()]["eth"] * 1e4)) * 10 ** 14)})
        if p[0]["data"] == ef.SAFE_NONCE:
            return _Resp({"result": hex(self.nonce)})
        a = "0x" + p[0]["data"][-40:]
        return _Resp({"result": hex(int(round((_reads()[a]["weth"] or 0.0) * 1e4)) * 10 ** 14)})

    def get(self, url, timeout=None):
        return _Resp({"data": {"header": {"message": {"slot": "15717600"}}}})


class _Daily:
    def __init__(self):
        self.marks = set()

    def due(self, k, d):
        return (k, d) not in self.marks

    def done(self, k, d):
        self.marks.add((k, d))


def _run(chain, monkeypatch):
    monkeypatch.delenv("ETHEREUM_RPC_URL", raising=False)
    out, daily = FetchOutput(), _Daily()
    ef.EFWallets(session=chain, daily=daily).run([ETH], 1, out)
    return {f["metric"].iloc[0]: f for f in out.frames}, out, daily


def test_the_daily_read_stores_three_series_at_one_block(monkeypatch):
    got, out, daily = _run(_Chain(), monkeypatch)
    assert set(got) == {"ef_held_tokens", "ef_inferred_held_tokens", "ef_staked_tokens"} and daily.marks
    assert got["ef_held_tokens"]["value"].iloc[0] == pytest.approx(95_118.0446)
    assert got["ef_inferred_held_tokens"]["value"].iloc[0] == pytest.approx(2_473.8858)
    assert got["ef_staked_tokens"]["value"].iloc[0] == pytest.approx(34 * 2048.0)
    for f in got.values():                                       # dated the block's own UTC day: 2026-11-23 11:53:59
        assert pd.Timestamp(f["date"].iloc[0]) == pd.Timestamp("2026-11-23")
    assert base._measuring_point(got["ef_held_tokens"]["source"].iloc[0]) == ef.POINT_HELD
    assert "INFERRED" in got["ef_inferred_held_tokens"]["source"].iloc[0]
    assert "INFERRED" not in got["ef_held_tokens"]["source"].iloc[0]
    assert "unchanged" in got["ef_staked_tokens"]["source"].iloc[0]


def test_a_wallet_that_cannot_be_read_stores_nothing_and_the_day_is_asked_again(monkeypatch):
    got, out, daily = _run(_Chain(fail_address=EF["wallets"][1]["address"]), monkeypatch)
    assert got == {} and not daily.marks                          # every endpoint failed at that wallet: nothing stored
    assert [e.status for e in out.log] == ["failed"]


def test_a_safe_that_has_transacted_since_the_list_was_read_is_said_on_the_row(monkeypatch):
    got, _out, _daily = _run(_Chain(nonce=20), monkeypatch)
    src = got["ef_staked_tokens"]["source"].iloc[0]
    assert "2 transaction(s) since the list was read" in src
    assert base._measuring_point(src) == ef.POINT_STAKED          # the note does not change the measuring point


def test_a_validator_off_the_treasurys_credentials_refuses_the_stake_not_the_wallets(monkeypatch):
    bad = _vals()
    bad[0]["validator"]["withdrawal_credentials"] = "0x01" + "00" * 11 + "22" * 20
    got, _out, daily = _run(_Chain(vals=bad), monkeypatch)
    assert set(got) == {"ef_held_tokens", "ef_inferred_held_tokens"} and not daily.marks


def test_the_registry_scan_finds_a_validator_across_chunk_boundaries():
    import re
    want = EF["stake"]["treasury"].lower()[2:]
    pat = re.compile((r'\{"index":"(\d+)","balance":"(\d+)","status":"([a-z_]+)","validator":\{"pubkey":"(0x[0-9a-f]{96})",'
                      r'"withdrawal_credentials":"0x0[12]0{22}(' + want + r')"').encode())
    k = EF["stake"]["validators"]
    rows = [(1, "0x" + "11" * 48, "0x00" + "ab" * 31, 32), (2, k[0], WC, 2048), (3, "0x" + "22" * 48,
                                                                               "0x01" + "00" * 11 + "33" * 20, 32),
            (4, k[1], WC, 2047)]
    body = ('{"finalized":true,"data":[' + ",".join(
        '{"index":"%d","balance":"%d","status":"active_ongoing","validator":{"pubkey":"%s","withdrawal_credentials":"%s",'
        '"effective_balance":"1"}}' % (i, eth * 10 ** 9, pk, wc) for i, pk, wc, eth in rows) + "]}").encode()
    for size in (37, 200, 10_000):
        res = ef.scan_stream((body[i:i + size] for i in range(0, len(body), size)), pat)
        assert res["entries"] == 4 and [f[0] for f in res["found"]] == [2, 4], size
        assert sum(f[1] for f in res["found"]) == pytest.approx(4095.0)


def test_off_the_float_from_the_first_read_and_never_before(monkeypatch):
    rs = config.RULE_IN["Ethereum"]
    assert rs["legs"] == () and rs["off_float"] == ("ef_held_tokens", "ef_inferred_held_tokens")
    assert rs["flow_rule"] is False and rs["reference_leaves_out"] is False
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    monkeypatch.setattr(config, "RULE_IN", {"Ethereum": rs})
    # a store the read has not reached: no term — the float is what it was (never a float without a figure, never 0)
    groups = {("Ethereum", "circulating_supply"): _g("circulating_supply", [("2026-10-13", 120_700_000.0)])}
    bw._rule_in_views(groups)
    assert "Ethereum" in config.RULE_IN_PENDING and ("Ethereum", "rule_in_off_float_tokens") not in groups
    assert bw._rule_in_term(None, 5, ETH) is None
    # one leg read, the other not: still pending (a sum without a wallet set is not the sum)
    groups[("Ethereum", "ef_held_tokens")] = _g("ef_held_tokens", [("2026-11-23", 95_118.0446)])
    bw._rule_in_views(groups)
    assert "Ethereum" in config.RULE_IN_PENDING
    # both read: the sum on the days both hold, from the first read
    groups[("Ethereum", "ef_held_tokens")] = _g("ef_held_tokens", [("2026-11-23", 95_118.0446), ("2026-11-24", 95_120.0),
                                                                   ("2026-11-25", 95_121.0)])
    groups[("Ethereum", "ef_inferred_held_tokens")] = _g("ef_inferred_held_tokens", [("2026-11-23", 2_473.8858),
                                                                                     ("2026-11-25", 2_400.0)])
    bw._rule_in_views(groups)
    off = bw._daily(groups[("Ethereum", "rule_in_off_float_tokens")])
    assert "Ethereum" not in config.RULE_IN_PENDING
    assert list(off.index) == [pd.Timestamp("2026-11-23"), pd.Timestamp("2026-11-25")]
    assert off.iloc[0] == pytest.approx(97_591.9304) and off.iloc[1] == pytest.approx(97_521.0)


def test_a_row_dated_before_the_declared_first_read_is_not_the_float_leg(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    monkeypatch.setattr(config, "RULE_IN", {"Ethereum": config.RULE_IN["Ethereum"]})
    assert set(config.RULE_IN["Ethereum"]["off_float_from"].values()) == {"2025-01-20"}
    groups = {("Ethereum", "ef_held_tokens"): _g("ef_held_tokens", [("2025-01-10", 1.0)]),
              ("Ethereum", "ef_inferred_held_tokens"): _g("ef_inferred_held_tokens", [("2025-01-10", 1.0)])}
    bw._rule_in_views(groups)
    assert "Ethereum" in config.RULE_IN_PENDING and ("Ethereum", "rule_in_off_float_tokens") not in groups


def test_the_history_read_is_newest_first_only_owed_days_and_on_the_live_measuring_point():
    held = {pd.Timestamp("2026-11-21")}
    owed = ef.owed_history_days(held, pd.Timestamp("2026-11-19"), pd.Timestamp("2026-11-22"))
    assert owed == [pd.Timestamp("2026-11-22"), pd.Timestamp("2026-11-20"), pd.Timestamp("2026-11-19")]
    rows = ef.history_rows(EF, pd.Timestamp("2026-11-22"), 26_474_105, pd.Timestamp("2026-11-22 23:59:59"), _reads())
    got = {f["metric"].iloc[0]: f for f in rows}
    assert got["ef_held_tokens"]["value"].iloc[0] == pytest.approx(95_118.0446)
    assert got["ef_inferred_held_tokens"]["value"].iloc[0] == pytest.approx(2_473.8858)
    for f in rows:                              # the archive marker is not a change of measuring point
        assert f["source"].iloc[0].endswith(":archive") and base._measuring_point(f["source"].iloc[0]) == ef.POINT_HELD
    assert [f["metric"].iloc[0] for f in ef.history_rows(EF, "2026-11-22", 1, "2026-11-22", _reads(drop="defi_safe"))] \
        == ["ef_inferred_held_tokens"]          # a wallet not read: no row for its series, never a short sum


def test_the_backfill_writes_only_the_keys_the_store_lacks(tmp_path, monkeypatch):
    import store as sm
    monkeypatch.delenv("ETHEREUM_RPC_URL", raising=False)
    monkeypatch.setattr(ef, "today", lambda: pd.Timestamp("2026-11-23"))
    db = tmp_path / "metrics.db"
    st = sm.Store(db)
    st.upsert(pd.concat([_g("ef_held_tokens", [("2026-11-21", 1.0)], "kept"),
                         _g("ef_inferred_held_tokens", [("2026-11-21", 2.0)], "kept")]))
    st.close()
    asked = []

    def block_at(ts):                           # the stub chain dates a block by its own number: the day's last second
        asked.append(pd.Timestamp(ts, unit="s"))
        return int(ts)
    n = ef.backfill(db, 3, 600.0, adapter=ef.EFWallets(session=_Chain(), daily=object()), block_at=block_at)
    assert n == 4                               # 2 owed days x 2 series; 2026-11-21 is held and is not asked
    assert asked == [pd.Timestamp("2026-11-22 23:59:59"), pd.Timestamp("2026-11-20 23:59:59")]
    con = sqlite3.connect(db)
    rows = con.execute("SELECT date, metric, value, source FROM metrics ORDER BY metric, date").fetchall()
    con.close()
    assert [(d, m) for d, m, _v, _s in rows] == [(d, m) for m in ("ef_held_tokens", "ef_inferred_held_tokens")
                                                 for d in ("2026-11-20", "2026-11-21", "2026-11-22")]
    assert rows[1][2:] == (1.0, "kept") and rows[0][2] == pytest.approx(95_118.0446)
    assert ef.backfill(db, 3, 600.0, adapter=ef.EFWallets(session=_Chain(), daily=object()), block_at=block_at) == 0


def test_no_other_projects_rule_in_stock_can_be_pending():
    # the pending state exists only where EVERY leg joins from a declared date: Ethereum and (R20) Aethir, no frozen
    # project
    for name, spec in config.RULE_IN.items():
        legs = tuple(spec.get("off_float", spec["legs"]))
        all_from = bool(legs) and set(legs) <= set(spec.get("off_float_from") or {})
        assert all_from == (name in ("Ethereum", "Aethir")), name          # was: Ethereum alone (before R20)
    assert "Ethereum" not in config.FROZEN_PROJECTS and "Aethir" not in config.FROZEN_PROJECTS


def test_the_insider_share_of_stake_reads_the_foundations_validators():
    num, den = config.INSIDER_LOCK["Ethereum"][:2]
    assert (num, den) == ("ef_staked_tokens", "beacon_chain_eth")
    assert 69_633.2443 / 44_714_369 == pytest.approx(0.001557, abs=1e-6)       # 0.16% of the stake on 2026-11-23


# ================================================================================================ 3. settlement
def test_artemis_own_series_ends_2026_08_25_and_its_legs_add_up():
    st = config.ARTEMIS_SETTLEMENT["series_status"]
    assert st["last_day"] == "2026-08-25" and st["checked_on"] == "2026-11-23" and st["status"].startswith("ENDED")
    lg = st["overlap_legs_2026_05_28_to_08_25"]
    assert lg["artemis_p2p_usd"] + lg["artemis_dex_nft_usd"] == pytest.approx(lg["artemis_settlement_usd"], abs=2.0)
    assert lg["artemis_p2p_usd"] / lg["artemis_settlement_usd"] == pytest.approx(0.931, abs=0.0005)
    ours = lg["defillama_dex_usd"] + lg["defillama_nft_usd"]
    assert ours / lg["artemis_dex_nft_usd"] - 1 == pytest.approx(0.0046, abs=0.0001)          # the DEX + NFT legs agree
    assert (ours - lg["artemis_dex_nft_usd"]) / lg["artemis_settlement_usd"] == pytest.approx(0.0003, abs=0.0001)


def test_no_artemis_route_is_wired_and_the_declared_rebuild_stands():
    assert config.ARTEMIS_SETTLEMENT["api"]["retired"]["on"] == "2026-10-19"
    assert "data-svc.artemisxyz.com" not in config.SOURCE_REGISTER
    ho = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["handover"]
    assert ho["from"] == "2026-08-26" and ho["scaled"] is False
    assert config.SETTLEMENT_REBUILD["ethereum_rebuild"]["validation"]["weekly_tol"] == 0.10


# ================================================================================================ 4. staked ETH
SP = ETH["staked_eth_rebuild"]
D0, D1 = pd.Timestamp("2026-10-25"), pd.Timestamp("2026-11-05")
FAILED = {"passed": False, "compared": 455, "min_days": 455, "tolerance_pct": 1.0, "worst_gap": 1.31,
          "worst_day": "2026-03-02", "mean_gap": 0.78, "outside": ["2026-03-02", "2026-03-03"], "missing": []}
ANC = {"slot": 15_716_896, "time": pd.Timestamp("2026-11-23 09:39:35"), "balance_eth": 44_714_369.0,
       "pending_eth": 731_489.0, "entries": 2_400_000, "pending_entries": 8_866}


def test_the_overlap_test_itself_is_not_loosened():
    assert SP["tolerance_pct"] == 1.0 and SP["min_days"] == 455 and SP["overlap"] == ("2025-07-28", "2026-10-25")
    ov = ETH["series_handover"]["beacon_chain_eth"]["overlap"]
    assert ov["tolerance_pct"] == 1.0 and ov["min_days"] == 455
    assert config.HANDOVER_CONTINUATION[("Ethereum", "beacon_chain_eth")]["requires_overlap"] is True


def test_a_failed_test_declares_the_measured_step_at_the_seam():
    have = {"vq": {D0 - pd.Timedelta(days=1): 43_990_000.0, D0: 44_000_000.0},
            "node": {D1: 44_352_000.0, D1 + pd.Timedelta(days=1): 44_360_000.0}}
    row, what = r.seam_declaration(have, ANC, FAILED, {D0: 44_330_000.0})
    assert row["metric"].iloc[0] == "beacon_chain_eth_seam_step_pct" and pd.Timestamp(row["date"].iloc[0]) == D1
    assert row["value"].iloc[0] == pytest.approx(0.80)            # 44,352,000 ÷ 44,000,000 − 1
    src = row["source"].iloc[0]
    assert base._measuring_point(src) == config.STAKED_SEAM_POINT
    assert "11 day(s) apart" in src and "44,000,000" in src and "44,352,000" in src and "+0.75%" in src
    assert "+0.80%" in what


def test_no_node_row_yet_takes_the_runs_own_anchor_read():
    row, _what = r.seam_declaration({"vq": {D0: 44_000_000.0}, "node": {}}, ANC, FAILED)
    assert pd.Timestamp(row["date"].iloc[0]) == pd.Timestamp("2026-11-23")
    assert row["value"].iloc[0] == pytest.approx((44_714_369.0 / 44_000_000.0 - 1) * 100)      # +1.62% over 29 days


def test_nothing_is_declared_on_a_pass_without_validatorqueue_or_on_an_impossible_step():
    have = {"vq": {D0: 44_000_000.0}, "node": {D1: 44_352_000.0}}
    assert r.seam_declaration(have, ANC, dict(FAILED, passed=True))[0] is None
    assert r.seam_declaration({"vq": {}, "node": {D1: 44e6}}, ANC, FAILED)[0] is None
    wrong_unit = {"vq": {D0: 44_000_000.0}, "node": {D1: 44_000_000.0 * 1.06}}                  # +6%: not a seam
    row, why = r.seam_declaration(wrong_unit, ANC, FAILED)
    assert row is None and "outside ±5%" in why
    assert r.seam_declaration({"vq": {D0: 44e6}, "node": {D0: 44.3e6}}, dict(ANC, time=D0), FAILED)[0] is None


def _p5():
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("p5_1_12", ROOT / "tests" / "test_round_p5_1_12.py")
    m = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(m)
    return m


def _failing_store(tmp_path, node_day="2026-11-04"):
    p5 = _p5()
    t_a = pd.Timestamp("2026-11-04 10:05:11")
    rows, pend = p5._scenario(t_a, 44.43e6)
    anc = p5._Anchor(15_580_224, 44.43e6, pend)
    a = r.anchor(anc)
    flows, partial, _ = r.flows_from_rows(rows, a["time"])
    truth = r.rebuild(a, flows, partial)["balance"]
    # validatorqueue reads 1.2% BELOW the rebuild on every day: the definition gap — the ±1% test cannot pass
    db = p5._db(tmp_path, vq_value=lambda d: truth[d] / 1.012, node_day=node_day)
    return db, anc, rows, truth


def _count(db, metric):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM metrics WHERE metric=?", (metric,)).fetchone()[0]
    finally:
        con.close()


def test_the_script_declares_the_change_of_quantity_without_asking(tmp_path, capsys):
    db, anc, rows, truth = _failing_store(tmp_path)
    # test only: printed, nothing stored
    assert r.main(["--db", str(db)], reader=anc, bq_rows=lambda _a: rows) == 1
    out = capsys.readouterr().out
    assert "OVERLAP TEST NOT PASSED" in out and "DECLARED CHANGE OF QUANTITY" in out and "nothing stored" in out
    assert _count(db, "beacon_chain_eth_seam_step_pct") == 0
    # --apply: ONE marker row, no rebuilt day, exit code 3
    assert r.main(["--db", str(db), "--apply"], reader=anc, bq_rows=lambda _a: rows) == 3
    assert _count(db, "beacon_chain_eth_seam_step_pct") == 1
    assert _count(db, "beacon_chain_eth_primary") == 1            # the node row the store already held; nothing rebuilt
    con = sqlite3.connect(db)
    d, v, s = con.execute("SELECT date, value, source FROM metrics WHERE metric='beacon_chain_eth_seam_step_pct'").fetchone()
    con.close()
    vq_last = truth[pd.Timestamp("2026-10-25")] / 1.012
    assert d == "2026-11-04" and v == pytest.approx((44e6 / vq_last - 1) * 100)
    assert s.startswith(config.STAKED_SEAM_POINT + "[OVERLAP TEST NOT PASSED: 455 of 455 day(s) compared")
    # run again: the same row, not a second one
    assert r.main(["--db", str(db), "--apply"], reader=anc, bq_rows=lambda _a: rows) == 3
    assert _count(db, "beacon_chain_eth_seam_step_pct") == 1


def test_a_test_that_could_not_run_declares_nothing(tmp_path, capsys):
    db, anc, _rows, _truth = _failing_store(tmp_path)

    def refused(_a):
        raise RuntimeError("BigQuery not available: no credentials")
    assert r.main(["--db", str(db), "--apply"], reader=anc, bq_rows=refused) == 1
    assert "NOT REBUILT" in capsys.readouterr().out and _count(db, "beacon_chain_eth_seam_step_pct") == 0


def test_a_pass_still_stores_the_rebuild_and_no_declaration(tmp_path):
    p5 = _p5()
    t_a = pd.Timestamp("2026-11-04 10:05:11")
    rows, pend = p5._scenario(t_a, 44.43e6)
    anc = p5._Anchor(15_580_224, 44.43e6, pend)
    a = r.anchor(anc)
    flows, partial, _ = r.flows_from_rows(rows, a["time"])
    truth = r.rebuild(a, flows, partial)["balance"]
    db = p5._db(tmp_path, vq_value=lambda d: truth[d] * 0.998)
    assert r.main(["--db", str(db), "--apply"], reader=anc, bq_rows=lambda _a: rows) == 0
    assert _count(db, "beacon_chain_eth_seam_step_pct") == 0 and _count(db, "beacon_chain_eth_primary") == 455 + 9


VQ_DAYS = pd.date_range("2025-07-28", "2026-10-25")
NODE = config.BEACON_NODE_POINT + "[finalized slot 1]"
REBUILT = config.STAKED_REBUILD_POINT + "[anchor slot 1]"
MARK = (config.STAKED_SEAM_POINT + "[OVERLAP TEST NOT PASSED: 455 of 455 day(s) compared, worst +1.31% on 2026-03-02, "
        "2 outside ±1% — DECLARED CHANGE OF QUANTITY]")


def _build_groups(primary_rows, marker=True):
    groups = {("Ethereum", "beacon_chain_eth"): _g("beacon_chain_eth", [(d, 44_000_000.0) for d in VQ_DAYS],
                                                   "validatorqueue:staked_amount"),
              ("Ethereum", "beacon_chain_eth_primary"): _g("beacon_chain_eth_primary", primary_rows)}
    if marker:
        groups[("Ethereum", "beacon_chain_eth_seam_step_pct")] = _g("beacon_chain_eth_seam_step_pct",
                                                                    [("2026-11-05", 0.80, MARK)])
    return groups


NODE_ROWS = [("2026-11-05", 44_352_000.0, NODE), ("2026-11-06", 44_360_000.0, NODE)]
KEY = ("Ethereum", "beacon_chain_eth")
DECL = ETH["series_handover"]["beacon_chain_eth"]


def test_without_the_declaration_the_handover_stays_held(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    groups = _build_groups(NODE_ROWS, marker=False)
    bw._handover_continuation_views(groups)
    assert KEY in bw.HANDOVER_HELD and KEY not in bw.HANDOVER_CONTINUED
    assert bw._daily(groups[KEY]).index.max() == pd.Timestamp("2026-10-25")          # stale, as before
    assert "DECLARED CHANGE OF QUANTITY" not in DECL["composition_change"]


def test_with_the_declaration_the_series_reads_the_nodes_actual_balances_from_the_seam(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    # a stray rebuilt-point row after validatorqueue's last day must NOT be taken: the rebuild was not adopted
    groups = _build_groups([("2026-10-30", 44_100_000.0, REBUILT)] + NODE_ROWS)
    bw._handover_continuation_views(groups)
    s = bw._daily(groups[KEY])
    assert KEY in bw.HANDOVER_CONTINUED and KEY not in bw.HANDOVER_HELD
    assert s.index.max() == pd.Timestamp("2026-11-06") and s[pd.Timestamp("2026-11-05")] == 44_352_000.0
    assert pd.Timestamp("2026-10-30") not in s.index                                  # a hole, never the rebuilt figure
    assert len(s) == 455 + 2 and s[pd.Timestamp("2026-10-25")] == 44_000_000.0        # validatorqueue's days untouched
    text = bw.HANDOVER_CONTINUED[KEY]
    assert "DECLARED CHANGE OF QUANTITY from 2026-11-05" in text and "+0.80%" in text
    # the seam is a series break: the handover's composition text is the declared change, with the measured step
    assert "DECLARED CHANGE OF QUANTITY" in DECL["composition_change"] and "+0.80%" in DECL["composition_change"]
    assert str(DECL["composition_change"]).strip().lower() != "none"
    # the rows carry the continuation's declared source: the handover's ordered points still describe the series
    added = groups[KEY][pd.to_datetime(groups[KEY]["date"]) > pd.Timestamp("2026-10-25")]
    assert set(added["source"].map(base._measuring_point)) == {"derived:=beacon_chain_eth_primary"}
    # the next build without the marker restores the handover's own text
    bw._handover_continuation_views(_build_groups(NODE_ROWS, marker=False))
    assert "DECLARED CHANGE OF QUANTITY" not in DECL["composition_change"]


def test_a_later_pass_takes_the_ordinary_handover_even_with_the_marker_in_the_store(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    rebuilt = [(d, 44_000_000.0 * 1.004, REBUILT) for d in VQ_DAYS] + \
              [(d, 44_200_000.0, REBUILT) for d in pd.date_range("2026-10-26", "2026-11-04")]
    groups = _build_groups(rebuilt + NODE_ROWS)
    bw._handover_continuation_views(groups)
    s = bw._daily(groups[KEY])
    assert KEY in bw.HANDOVER_CONTINUED and "DECLARED CHANGE OF QUANTITY" not in bw.HANDOVER_CONTINUED[KEY]
    assert s[pd.Timestamp("2026-10-30")] == 44_200_000.0 and len(s) == 455 + 10 + 2    # the rebuilt days are used
    assert "DECLARED CHANGE OF QUANTITY" not in DECL["composition_change"]


def test_the_fallback_is_ethereums_alone():
    with_fb = [k for k, v in config.HANDOVER_CONTINUATION.items() if v.get("fallback")]
    assert with_fb == [("Ethereum", "beacon_chain_eth")]
    assert all(k[0] not in config.FROZEN_PROJECTS for k in with_fb)
