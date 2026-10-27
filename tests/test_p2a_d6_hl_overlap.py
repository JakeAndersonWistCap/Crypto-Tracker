"""
tests/test_p2a_d6_hl_overlap.py — Round P2a D6 (Jake's run: Hyperliquid's declared 2026-10-21 circulating handover failed
its overlap check, +7.04% on 10-27, worst +35.72% on 10-07). check_offline_items.hl_circulating_overlap prints both
series side by side from metrics.db (read-only), the wallets of the exclusion with their balances, and attributes the
gap wallet by wallet. A fake store; no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FOUNDATION = "0xd57ecca444a9acb7208d286be439de12dd09de5d"
GRANTS = "0xa20fcfa0507fe762011962cc581b95bbbc3bbdba"
CG = 222_445_714.07              # CoinGecko's HYPE circulating, unchanged on every day of the window


class _Http:
    """The two wallet reads fetch/hypercore makes, plus the HyperEVM burn — fixed answers, no network."""
    def post(self, url, json_body=None, **_):
        t = json_body.get("type")
        u = json_body.get("user")
        if json_body.get("method") == "eth_getBalance":
            return {"result": hex(310_444 * 10 ** 18)}
        if t == "spotClearinghouseState":
            return {"balances": [{"coin": "HYPE", "total": {FOUNDATION: "50000.0", GRANTS: "105501.0"}[u]}]}
        if t == "delegatorSummary":
            return {"delegated": {FOUNDATION: "60559447.70", GRANTS: "2870952.87"}[u], "undelegated": "0.0",
                    "totalPendingWithdrawal": "0.0"}
        raise AssertionError(json_body)


def _hl_read():
    import config
    api = config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]
    return api, next(r for r in api["extra_reads"] if r.get("exclude_wallets"))


def _the_run_log_line() -> str:
    """The excluded_wallets_tokens line exactly as fetch/hypercore writes it."""
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo
    api, read = _hl_read()
    a = HyperCoreInfo()
    a.http = _Http()
    out = FetchOutput()
    a._circ_less_wallets("Hyperliquid", api, read, 301_994_093.32, read["exclude_wallets"], out,
                         pd.Timestamp("2026-10-27"))
    return next(e.message for e in out.log if e.message.startswith("excluded_wallets_tokens="))


def _fake_store(path: Path, with_parts: bool = True):
    import store
    st = store.Store(str(path))
    rows = []
    for i, d in enumerate(pd.date_range("2026-09-20", "2026-10-27")):
        gross, burn, excl = 301_000_000.0 + i * 30_000, 300_000.0 + i * 300, 63_500_000.0 + i * 3_700
        net = d >= pd.Timestamp("2026-10-11")
        src = ("hypercore_info:tokenDetails.circulatingSupply-exclude_wallets-hyperevm_burn" if net
               else "hypercore_info:tokenDetails.circulatingSupply-hyperevm_burn")
        rows += [(d, "circulating_supply", CG, "coingecko"),
                 (d, "circulating_supply_first_party", gross - burn - (excl if net else 0.0), src),
                 (d, "circulating_supply_tokendetails", gross, "hypercore_info:tokenDetails.circulatingSupply"),
                 (d, "hyperevm_zero_address_balance", burn, "hyperevm")]
    rows.append((pd.Timestamp("2026-10-27"), "price_usd", 80.0, "coingecko"))
    st.upsert(pd.DataFrame(rows, columns=["date", "metric", "value", "source"]).assign(project="Hyperliquid", tier=1))
    if with_parts:
        st.record_fetch("r1", "hypercore_info", "Hyperliquid", 1, "ok", _the_run_log_line(), 1)
        st.conn.execute("UPDATE run_log SET ts = '2026-10-27T09:00:00Z'")
        st.conn.commit()
    st.close()


def test_d6_the_run_log_line_parses_into_each_wallet():
    import check_offline_items as coi
    _, read = _hl_read()
    labels = [w["label"] for w in read["exclude_wallets"]]
    got = coi.hl_wallet_parts(_the_run_log_line(), labels)
    assert got[labels[0]] == pytest.approx((60_609_447.70, 50_000.0, 60_559_447.70))
    assert got[labels[1]] == pytest.approx((2_976_453.87, 105_501.0, 2_870_952.87))
    assert coi.hl_wallet_parts("excluded_wallets_tokens=1.00: something else", labels) == {}


def test_d6_attribution_names_each_wallet_and_never_takes_unknown_as_zero():
    import check_offline_items as coi
    att = dict(coi.hl_attribution(301_994_093.32, CG, {"F": 60_609_447.70, "G": 2_976_453.87}, 310_444.53))
    assert att["F"] == 60_609_447.70 and att["G"] == 2_976_453.87
    rest = next(v for k, v in att.items() if k.startswith("UNATTRIBUTED"))
    assert rest == pytest.approx(301_994_093.32 - CG - 60_609_447.70 - 2_976_453.87 - 310_444.53)
    att = dict(coi.hl_attribution(301_994_093.32, CG, {"F": None, "G": 2_976_453.87}, 310_444.53))
    assert next(v for k, v in att.items() if k.startswith("UNATTRIBUTED")) is None
    assert coi.hl_attribution(None, CG, {}, 1.0) == []


def test_d6_both_series_side_by_side_with_the_wallets_and_the_build_verdict(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store
    db = tmp_path / "m.db"
    _fake_store(db)
    monkeypatch.setattr(store, "DB_PATH", db)
    assert coi.hl_circulating_overlap(days=30, live=False) == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("  2026-")]
    assert len(lines) == 30 and lines[0].startswith("  2026-09-28") and lines[-1].startswith("  2026-10-27")
    last = lines[-1]
    assert "222,445,714" in last and "net-burn" in last and "+7." in last
    assert "60,609,448" in last and "2,976,454" in last, "the day's wallets come from the run log line"
    assert "unread" in next(ln for ln in lines if ln.startswith("  2026-10-26")), "no line that day: unread, not 0"
    assert "gross-burn" in next(ln for ln in lines if ln.startswith("  2026-10-07"))
    assert FOUNDATION in out and GRANTS in out
    assert "OVERLAP CHECK NOT PASSED" in out and "1 distinct value" in out
    assert "Days before 2026-10-11 hold b on its gross leg" in out


def test_d6_the_live_section_attributes_today_wallet_by_wallet(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store
    db = tmp_path / "m.db"
    _fake_store(db, with_parts=False)
    monkeypatch.setattr(store, "DB_PATH", db)
    h = _Http()

    def live(url, body):
        if body.get("type") == "spotMeta":
            return {"tokens": [{"name": "HYPE", "tokenId": "0xtok"}]}
        if body.get("type") == "tokenDetails":
            return {"totalSupply": "998824067.02", "circulatingSupply": "301994093.32", "futureEmissions": "410409606.0",
                    "nonCirculatingUserBalances": [["0x43e9abea1910387c4292bca4b94de81462f8a251", "238128411.11"],
                                                   ["0xfefefefefefefefefefefefefefefefefefefefe", "48290280.07"]]}
        return h.post(url, json_body=body)
    monkeypatch.setattr(coi, "_hl_live", live)
    coi.hl_circulating_overlap(days=30, live=True)
    out = capsys.readouterr().out
    assert "0xfefefefefefefefefefefefefefefefefefefefe  48,290,280.07" in out
    assert "60,609,447.70 (spot 50,000.00 + staked 60,559,447.70)" in out
    assert "b / a - 1 = +7.04%" in out
    assert "UNATTRIBUTED" in out and "15,652" in out


def test_d6_no_store_and_no_network_says_so(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "absent.db")

    def dead(*_a, **_k):
        raise RuntimeError("no network in tests")
    monkeypatch.setattr(coi, "_hl_live", dead)
    assert coi.hl_circulating_overlap(live=True) == 0
    out = capsys.readouterr().out
    assert "no store at" in out and "tokenDetails unread" in out and "unread" in out
    assert coi.hl_circulating_overlap in coi.CHECKS
