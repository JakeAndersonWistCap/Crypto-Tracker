"""Round D7 (Jake, 2026-10-27, head 720148e; SPEC §19 D7): Pyth's STOCK reads stored only (1), rule 9's same span on
every ratio — BLOCKED with the holes named (2). Item 3 (CCIP R1/R2 not adopted) is a SPEC record, no code."""
from __future__ import annotations

import base64
import csv
import re
import struct
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


# ============================================================================================ 1 Pyth STOCK reads
def _spec_addresses() -> list[str]:
    s = config.PYTH_STOCKS
    out = [s["mint"], s["usdc_mint"], s["staking_program"], s["staking_config"], s["voting_target"],
           s["ois_pool_config"], s["ois_pool_data"], s["dca_program"], s["council_vault"]]
    for _m, addr, mint, owner, _w in s["token_accounts"]:
        out += [addr, mint, owner]
    return sorted(set(out))


def test_1_every_address_is_a_full_string_from_the_research_never_a_prefix():
    research = ((ROOT / "research" / "pyth" / "sources.csv").read_text(encoding="utf-8")
                + (ROOT / "research" / "pyth" / "dossier.md").read_text(encoding="utf-8"))
    circle_usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"      # Circle's address list, cited in config
    for a in _spec_addresses():
        assert B58.match(a), f"{a}: not a full base58 address"
        assert a in research or a == circle_usdc, f"{a}: not in research/pyth (full string) — a lookalike or a typo"
    # the stored lines are exactly the ones Jake listed
    metrics = {m for m, *_ in config.PYTH_STOCKS["token_accounts"]}
    assert metrics == {"pyth_treasury_pyth_tokens", "pyth_treasury_usdc", "pyth_council_pyth_tokens",
                       "pyth_council_usdc", "pyth_community_council_pyth_tokens", "pyth_douro_pyth_tokens",
                       "pyth_douro_usdc", "pyth_funder_cewsb_pyth_tokens", "pyth_funder_e6nab_pyth_tokens",
                       "pyth_ois_unclaimed_tokens"}
    assert config.PYTH_STOCKS["rpc"] == "https://api.mainnet-beta.solana.com"
    assert "Pyth" in config.STORE_ONLY_PROJECTS and "Pyth" not in config.PROJECT_BY_NAME


def test_1_the_sources_csv_stock_addresses_are_all_read():
    rows = list(csv.DictReader(open(ROOT / "research" / "pyth" / "sources.csv", encoding="utf-8")))
    wanted = {"AYBdhJU8Wao48SEDyKVRXgDRbo9SU8Thnytakof4HDDH", "5Unq3fgfSNdyeGjiq2Pu5XAQUJWo2rauKGErbUyxqUGe",
              "BTy44YBx9AVLHbKkSVLGaCBNKUpzw78W6hKy43vpeCHp", "HeMtL8F8PPsEWXxUzwBirNE2mYBtYpvZ4oq6erhjGr3n",
              "6njqtZeuLbSFU7Yo72GxPcdwhLQWahEPx1iN9GD6DRgV", "poo1zPoi5xrNzi4yk4i23oWcJrNNkDYAniBCewJY8kb",
              "BE8Xq1iHSQYKG8CZorZsrTnxyeHKQLzCWm4dYKdFkVEL", "FbSivnCf6iXfXns6osFih2MgX8icmSKx9iba3gyFpiCT",
              "6fLL4ZFTguNpiJNWzFHs3HmLonPPigok54rsi6ZVXb4g", "8iRsZ71SSe34neW644NBNB9qn2VMx84yZYKD3oLkr6ma",
              "2tEAnoFKyJJBLcpqcZwksQFM8dpUuo3GHvJdrvHGsgy7", "8UkJ2UYf1GeJUM5rUGuZqmGZushDvsYYMfxmoqK9gvtx",
              "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", "pytS9TjG1qyAZypk7n8rw8gfW9sUaqqYyMhJQ4E7JCQ"}
    stock_text = " ".join(r["endpoint_or_address"] for r in rows if r["kind"] == "STOCK")
    assert all(a in stock_text for a in wanted), [a for a in wanted if a not in stock_text]
    assert wanted <= set(_spec_addresses())


def test_1_decoders_read_the_dossiers_test_values():
    from fetch import pyth_stocks as ps
    # the voting target as read on-chain on 2026-10-27 (first 64 bytes)
    vt = bytes.fromhex("9d178b75b52cc582fe940b000000000000d845be048bf70300afa78e4495f50300411a1c62c4ffffff"
                       "0000000000000000000000000000000000000000000000")
    t = ps.decode_voting_target(vt)
    assert t["epoch"] == 2964 and t["locked"] == 1_114_446_379_263_919 and t["prev_epoch_locked"] == 1_116_601_382_225_368
    # OIS pool data: two pools, delegated and self stake
    data = bytearray(ps.OIS_SLICE)
    struct.pack_into("<QQ", data, 8, 2964, 84_834_191_564)
    data[ps.OIS_PUBLISHERS_AT] = 1
    data[ps.OIS_PUBLISHERS_AT + 32] = 1
    struct.pack_into("<Qq", data, ps.OIS_DEL_AT, 600_000_000_000_000, -5)
    struct.pack_into("<Qq", data, ps.OIS_DEL_AT + 16, 35_884_596_315_475, 0)
    struct.pack_into("<Qq", data, ps.OIS_SELF_AT + 16, 35_355_187_560_887, 0)
    p = ps.decode_ois_pool(bytes(data))
    assert p["publishers"] == 2 and p["delegated"] + p["self"] == 671_239_783_876_362 and p["claimable"] == 84_834_191_564
    cfg = bytearray(188)
    cfg[179] = 1
    struct.pack_into("<q", cfg, 180, 1_684_591_200)
    assert ps.decode_list_time(bytes(cfg)) == 1_684_591_200
    assert ps.anchor_discriminator("StakeAccountMetadataV2").hex() == "c033cb134cb18861"


def test_1_the_vesting_rule_is_the_staking_programs():
    from fetch import pyth_stocks as ps
    lt, yr = 1_684_591_200, 31_536_000
    oct27 = int(pd.Timestamp("2026-10-27 12:00").timestamp())
    assert ps.unvested(8_618_333_336, lt, yr, 4, oct27) == 2_154_583_334        # 3 of 4 periods passed: a quarter left
    assert ps.unvested(100, lt, yr, 4, lt - 1) == 100                            # before the start: all of it
    assert ps.unvested(100, lt, yr, 4, lt + 4 * yr) == 0                         # 2027-05-19 14:00 UTC: nothing left
    assert ps.unvested(100, lt, yr, 4, lt + 4 * yr - 1) == 25


class _FakeHttp:
    """Answers the JSON-RPC methods read() uses; `bad_owner` / `refuse_gpa` simulate a lookalike and a refusal."""

    def __init__(self, bad_owner: str | None = None, refuse_gpa: bool = False):
        self.stats = {"calls": 0, "throttled": 0}
        self.bad_owner, self.refuse_gpa, self.methods = bad_owner, refuse_gpa, []

    def post(self, url, body):
        self.stats["calls"] += 1
        m, prm = body["method"], body["params"]
        self.methods.append(m)
        ctx = {"slot": 462_000_000}
        s = config.PYTH_STOCKS
        if m == "getTokenSupply":
            return {"result": {"context": ctx, "value": {"amount": "9999958880261478", "decimals": 6}}}
        if m == "getAccountInfo" and prm[1]["encoding"] == "jsonParsed":
            acc = next(a for a in s["token_accounts"] if a[1] == prm[0])
            owner = "Gx4MpVlookalikeFyak11111111111111111111111" if prm[0] == self.bad_owner else acc[3]
            return {"result": {"context": ctx, "value": {"data": {"parsed": {"info": {
                "mint": acc[2], "owner": owner, "tokenAmount": {"amount": "41088354294652", "decimals": 6}}}}}}}
        if m == "getAccountInfo":
            off = prm[1].get("dataSlice", {}).get("offset", 0)
            if prm[0] == s["voting_target"]:
                raw = bytes(9) + struct.pack("<QQQq", 2964, 2, 1_114_446_379_263_919, 0)
            elif prm[0] == s["ois_pool_data"]:
                raw = bytearray(prm[1]["dataSlice"]["length"])
                struct.pack_into("<Qq", raw, 32_792, 671_239_783_876_362, 0)
                raw = bytes(raw)
            elif prm[0] == s["ois_pool_config"] and off == 104:
                raw = struct.pack("<Q", 0)
            else:                                             # staking config
                raw = bytearray(188)
                raw[179] = 1
                struct.pack_into("<q", raw, 180, 1_684_591_200)
                raw = bytes(raw)
            return {"result": {"context": ctx, "value": {"data": [base64.b64encode(raw).decode(), "base64"]}}}
        if m == "getProgramAccounts":
            if prm[0] == s["dca_program"]:
                return {"result": {"context": ctx, "value": [{"pubkey": "93suurAn3WiyEyrkgZiLujtaH3qiybTFBSvW35ccrQrT"}]}}
            if self.refuse_gpa:
                return {"error": {"code": -32010, "message": "getProgramAccounts is not available on this endpoint"}}
            tag = base64.b64decode(prm[1]["filters"][2]["memcmp"]["bytes"])[0]
            if tag == 2:
                b = bytes(33) + struct.pack("<QQQ", 4_000_000, 31_536_000, 4)
            else:
                b = bytes(33) + struct.pack("<QqQQ", 1_000_000, 1_790_000_000, 31_536_000, 1)
            bad = bytes(33) + struct.pack("<QqQQ", 5, 32_503_680_000, 0, 0)      # zero periods, year 3000: malformed
            v = [{"account": {"data": [base64.b64encode(b).decode(), "base64"]}}]
            if tag == 1:
                v.append({"account": {"data": [base64.b64encode(bad).decode(), "base64"]}})
            return {"result": {"context": ctx, "value": v}}
        if m == "getTokenAccountsByOwner":
            return {"result": {"context": ctx, "value": [{"account": {"data": {"parsed": {"info": {
                "tokenAmount": {"amount": "12516585434", "decimals": 6}}}}}}]}}
        raise AssertionError(m)


def test_1_a_full_read_stores_every_line_with_its_slot_and_counts_calls():
    from fetch import pyth_stocks as ps
    http = _FakeHttp()
    res = ps.read(http=http, now=int(pd.Timestamp("2026-10-27 12:00").timestamp()))
    got = {m: (v, s) for m, v, s in res.rows}
    assert not res.failed, res.failed
    # 1 supply + 10 token accounts + DCA (1 program read + 1 per open account) + voting target + OIS pool + y + staking
    # config + 2 vesting program reads = 19 with the fake's one open DCA (the live read on 2026-10-27: 2 open, 20 calls)
    assert len(got) == 17 and res.calls == http.stats["calls"] == 19
    assert all("slot 462000000" in s for _v, s in got.values())
    assert got["pyth_unvested_genesis_tokens"][0] == 1.0            # 4.0 initial, a quarter left
    assert got["pyth_unvested_other_tokens"][0] == 1.0              # before its start: all of it
    assert "1 malformed excluded" in got["pyth_unvested_other_tokens"][1]
    assert got["pyth_council_dca_open_usdc"][0] == 12_516.585434
    assert got["pyth_gov_staked_tokens"][0] == 1_114_446_379.263919 and "epoch 2964" in got["pyth_gov_staked_tokens"][1]
    f = ps.frame(res, "2026-10-27")
    assert set(f["project"]) == {"Pyth"} and set(f["date"]) == {"2026-10-27"} and set(f["tier"]) == {2}


def test_1_a_lookalike_owner_or_a_refused_program_read_stores_nothing_for_that_line():
    from fetch import pyth_stocks as ps
    http = _FakeHttp(bad_owner="8iRsZ71SSe34neW644NBNB9qn2VMx84yZYKD3oLkr6ma", refuse_gpa=True)
    res = ps.read(http=http)
    stored = {m for m, *_ in res.rows}
    failed = dict(res.failed)
    assert "pyth_douro_pyth_tokens" not in stored and "NOT STORED" in failed["pyth_douro_pyth_tokens"]
    assert {"pyth_unvested_genesis_tokens", "pyth_unvested_other_tokens"} <= set(failed) and \
        not ({"pyth_unvested_genesis_tokens", "pyth_unvested_other_tokens"} & stored)
    assert "not available" in failed["pyth_unvested_genesis_tokens"]
    assert len(stored) == 14                                        # every other line still read
    lines = ps.log_entries(res)
    assert lines[0].status == "warn" and "RPC call(s)" in lines[0].message and "14 STOCK line(s) stored" in lines[0].message
    assert sum(1 for e in lines if e.status == "failed") == 3


def test_1_the_pacer_spaces_calls_and_a_429_waits_its_retry_after(monkeypatch):
    from fetch import base
    from fetch import pyth_stocks as ps
    rpc = ps._Rpc(config.PYTH_STOCKS)
    assert rpc.http.min_interval == config.PYTH_STOCKS["rpc_min_interval_s"] == 0.5
    assert rpc.http.max_retry_wait == 30.0
    naps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: naps.append(s))

    class R:
        def __init__(self, code, headers=None):
            self.status_code, self.headers = code, headers or {}

        def json(self):
            return {"jsonrpc": "2.0", "result": {"context": {"slot": 1}, "value": None}}

        def raise_for_status(self):
            pass
    seq = iter([R(429, {"Retry-After": "10"}), R(200)])
    monkeypatch.setattr(rpc.http.s, "post", lambda *a, **k: next(seq))
    assert rpc("getSlot", []) == {"context": {"slot": 1}, "value": None}
    assert 10.0 in naps and rpc.http.stats.get("throttled") == 1


def test_1_a_store_only_project_never_reaches_the_build(tmp_path, monkeypatch):
    import build_workbook as bw
    long = pd.DataFrame({"project": ["Pyth", "Hyperliquid"], "metric": ["pyth_mint_supply_tokens", "price_usd"],
                         "value": [1.0, 2.0]})
    assert list(bw.drop_store_only(long)["project"]) == ["Hyperliquid"]
    # end to end: a Pyth row in the store and a one-project build — no tab carries it
    import store as sm
    from test_trajectory import _built
    import real_dump
    orig = real_dump.to_store

    def with_pyth(payload, db):
        orig(payload, db)
        st = sm.Store(str(db))
        st.upsert(pd.DataFrame([{"date": "2026-10-13", "project": "Pyth", "metric": "pyth_mint_supply_tokens",
                                 "value": 9_999_958_880.26, "source": "chain:solana:getTokenSupply x[slot 1]",
                                 "tier": 2}]))
        st.close()
    monkeypatch.setattr(real_dump, "to_store", with_pyth)
    wb, _rows = _built(tmp_path, monkeypatch, "Hyperliquid")
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            assert not any(isinstance(v, str) and ("Pyth|" in v or "pyth_mint_supply" in v) for v in row), ws.title


# ============================================================================================ 2 rule 9 on ratios
def test_2_every_guard_with_two_legs_tests_the_span_unless_it_is_a_sum():
    import build_workbook as bw
    R = bw.Refs(10, 0, [])
    two = bw._coverage_guard(R, 5, [("burn", "gross_burn_tokens", "q0"), ("issuance", "pool_release_tokens", "q0")],
                             "1/2")
    one = bw._coverage_guard(R, 5, [("burn", "gross_burn_tokens", "q0")], "1")
    summed = bw._coverage_guard(R, 5, [("a", "outflow_a_tokens", "q0"), ("b", "outflow_b_tokens", "q0")], "1+2",
                                same_span=False)
    assert "ABS(" in two and "/10" in two
    assert "ABS(" not in one and "ABS(" not in summed


def test_2_the_blocked_text_names_each_legs_holes(monkeypatch):
    import build_workbook as bw
    R = bw.Refs(10, 0, [])
    monkeypatch.setitem(bw._PROJECT, "name", "Hyperliquid")
    monkeypatch.setitem(bw.WINDOW_HOLES, ("Hyperliquid", "pool_release_tokens", "q0"),
                        [pd.Timestamp("2026-10-02"), pd.Timestamp("2026-10-03"), pd.Timestamp("2026-10-04"),
                         pd.Timestamp("2026-10-05"), pd.Timestamp("2026-10-12")])
    f = bw._coverage_guard(R, 5, [("burn", "gross_burn_tokens", "q0"), ("issuance", "pool_release_tokens", "q0")], "1")
    assert "holes: 2026-10-02..10-05, 2026-10-12" in f
    many = [pd.Timestamp("2026-01-01") + pd.Timedelta(days=2 * i) for i in range(60)]
    monkeypatch.setitem(bw.WINDOW_HOLES, ("Hyperliquid", "pool_release_tokens", "q0"), many)
    lits = re.findall(r'"([^"]*)"', bw._coverage_guard(R, 5, [("b", "gross_burn_tokens", "q0"),
                                                              ("i", "pool_release_tokens", "q0")], "1"))
    assert max(len(x) for x in lits) <= 255 and any("(60 days)" in x for x in lits)


def test_2_hyperliquids_burn_over_issuance_is_blocked_with_both_legs_holes_on_the_real_dump(tmp_path, monkeypatch):
    import xlcalc
    from test_trajectory import _built
    wb, _rows = _built(tmp_path, monkeypatch, "Hyperliquid")
    X = xlcalc.Workbook(wb)
    ws = wb["A4 Permanent Burn"]
    col = next(c for r in range(1, 8) for c in range(1, ws.max_column + 1)
               if isinstance(ws.cell(r, c).value, str) and ws.cell(r, c).value.startswith("BURN ÷ ISSUANCE"))
    row = next(r for r in range(2, ws.max_row + 1) if "Hyperliquid" in (ws.cell(r, 1).value, ws.cell(r, 2).value))
    v = X.value("A4 Permanent Burn", row, col)
    assert isinstance(v, str) and v.startswith("BLOCKED — unequal coverage"), v
    assert "gross_burn_tokens, q0) covers 87 days" in v and "pool_release_tokens, q0) covers 7 days" in v, v
    assert "holes: 2026-09-13, 2026-09-19..09-20" in v and "holes: 2026-10-02..10-05, 2026-10-12" in v, v


# ============================================================================================ 3 CCIP: a record
def test_3_the_ccip_decision_is_recorded_and_the_rule_unchanged():
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert "| D7-3 |" in spec and "NOT ADOPTED" in spec.split("| D7-3 |")[1].split("\n")[0]
    import inspect
    from fetch import ccip_value
    src = inspect.getsource(ccip_value)
    assert "Rubicon" not in src and "getAlphaPrice" not in src, "R1 is not wired"
