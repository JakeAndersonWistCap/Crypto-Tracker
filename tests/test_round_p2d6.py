"""ROUND P2d-6 (Jake's backfill 2026-10-28): Pendle cooldown_days logged 170 days NOT WRITTEN, one FAILED line a day.

Read live (2026-10-28, public Ethereum archive RPC): sPENDLE has no code through block 24,237,455 and code from
24,237,456 (2026-01-15 03:34:59 UTC); cooldownDuration() answers 1,209,600 s from block 24,237,468; the implementation
slot is the same at deployment and at every block sampled since. The 170 days (2025-07-30..2026-01-15) are the days
before sPENDLE existed: an eth_call to an address with no code answers 0x. The archive used to date them by sPENDLE's
code; P2b-2 (EOA holders) stopped that for every address not meant to have code, and sPENDLE is CALLED, not held.

Fixed: (1) a called contract dates its series by its code again (fetch.chain.address_is_called); (2) a raw call that
answers EMPTY on the day before its first answered day — with the window's oldest day empty too — is "not available
before <first day>", recorded once, never a failure; an empty return after that day, a revert, or an RPC error still
is; (3) the declared start (config.ARCHIVE_SERIES_START) names the date on the Data row. No network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

PN, CD = "Pendle", "cooldown_days"
ASOF = pd.Timestamp("2026-10-27")
EMPTY = "Could not decode contract function call to cooldownDuration with return data: b'', output_types: ['uint24']"


def _archive(tmp_path, monkeypatch, answer, seed=None, days=6, runs=1):
    """The archive route on a fake chain (one block an hour, code at every block): answer(day) -> seconds, or raises.
    Returns (results of each run, stored rows, the days raw_call was asked)."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: ASOF)
    t0 = int((ASOF - pd.Timedelta(days=40)).tz_localize("UTC").timestamp())
    asked = []

    class Eth:
        block_number = 40 * 24 + 10

        def get_block(self, n):
            return {"timestamp": t0 + n * 3600}

        def get_code(self, a, block_identifier=None):
            return b"\x60\x80"                     # sPENDLE has code at every block of this fake

    class Reader:
        def __init__(self):
            self.at_block = {}

        def web3(self, chain):
            return SimpleNamespace(eth=Eth())

        def checksum(self, a):
            return a

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def raw_call(self, chain, address, call, *args):
            day = pd.Timestamp(t0 + self.at_block[chain] * 3600, unit="s").normalize()
            asked.append(str(day.date()))
            return float(answer(day))

    p = dict(config.PROJECT_BY_NAME[PN])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k == "spendle_cooldown"}
    monkeypatch.setitem(config.PROJECT_BY_NAME, PN, p)
    tmp_path.mkdir(parents=True, exist_ok=True)
    st = sm.Store(tmp_path / "a.db")
    if seed is not None:
        st.upsert(seed)
    res = [ar.ArchiveBackfill(st, [p], budget_s=120, reader=Reader(), days=days).run({"ethereum"})
           for _ in range(runs)]
    rows = st.conn.execute("SELECT substr(date,1,10), value FROM metrics WHERE project=? AND metric=? ORDER BY date",
                           (PN, CD)).fetchall()
    return res, rows, asked


def _empty_before(first: str, before=lambda d: (_ for _ in ()).throw(ValueError(EMPTY))):
    def answer(day):
        return 1_209_600 if day >= pd.Timestamp(first) else before(day)
    return answer


def _lines(res, text):
    return [x for x in res["report"] if text in x]


# ============================================================================================ the rule
def test_an_empty_call_before_its_first_day_is_not_available_before_that_day(tmp_path, monkeypatch):
    res, rows, asked = _archive(tmp_path, monkeypatch, _empty_before("2026-10-24"), runs=2)
    r1, r2 = res
    assert rows == [("2026-10-24", 14.0), ("2026-10-25", 14.0), ("2026-10-26", 14.0)], rows   # days >= D, 1,209,600 s
    one = [x for x in r1["report"] if x.startswith(f"{PN}/{CD}: not available before ")]
    assert len(one) == 1, r1["report"]
    assert one[0].startswith(f"{PN}/{CD}: not available before 2026-10-24 — cooldownDuration() returns empty before "
                             f"the sPENDLE version that has it (a pre-deployment absence, not a failure)"), one[0]
    assert not _lines(r1, "NOT WRITTEN"), r1["report"]
    # read from the data: 10-23 answered 0x, and the window's oldest day (10-21) was probed once and was 0x too;
    # 10-22 was never read
    assert sorted(set(asked)) == ["2026-10-21", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"], asked
    assert asked.count("2026-10-21") == 1
    from fetch import archive as ar
    assert ar.series_starts()[(PN, CD)]["from"] == "2026-10-24"
    # the second run retries nothing before D, writes nothing, reports no failure and no second discovery
    assert not r2["written"] and not _lines(r2, "NOT WRITTEN")
    assert not [x for x in r2["report"] if x.startswith(f"{PN}/{CD}: not available")]
    assert any(x.startswith(f"COMPLETE FROM 2026-10-24: {PN}/{CD}") for x in r2["report"]), r2["report"]
    assert len(asked) == 5, "a later run reads none of the days before D"


def test_an_empty_return_after_the_first_day_is_still_a_failure(tmp_path, monkeypatch):
    """10-24 answers 0x between days that answer: the probe of the oldest day answers too, so it is a hole, not a start.
    And with the earlier history already stored, the empty day is a failure without any probe."""
    def answer(day):
        if day == pd.Timestamp("2026-10-25"):
            raise ValueError(EMPTY)
        return 1_209_600
    res, rows, _ = _archive(tmp_path, monkeypatch, answer)
    assert "2026-10-25" not in {d for d, _ in rows} and len(rows) == 5          # never a value, never 0
    assert _lines(res[0], f"NOT WRITTEN {PN}/{CD}: 1 day(s)") and "return data: b''" in _lines(res[0], "NOT WRITTEN")[0]
    assert not _lines(res[0], f"{PN}/{CD}: not available before")
    from fetch import archive as ar
    assert (PN, CD) not in ar.series_starts() or ar.series_starts()[(PN, CD)]["from"] < "2026-10-21"

    seed = pd.DataFrame({"date": pd.to_datetime(["2026-10-21", "2026-10-26"]), "project": PN, "metric": CD,
                         "value": [14.0, 14.0], "tier": 2, "source": "chain:ethereum:spendle_cooldown"})
    res, rows, asked = _archive(tmp_path / "b", monkeypatch, _empty_before("2026-10-26"), seed=seed)
    assert _lines(res[0], f"NOT WRITTEN {PN}/{CD}: 4 day(s)"), res[0]["report"]        # 10-22..10-25
    assert not _lines(res[0], f"{PN}/{CD}: not available before")
    assert [d for d, _ in rows] == ["2026-10-21", "2026-10-26"]


def test_a_revert_or_an_rpc_error_before_the_first_day_is_still_a_failure(tmp_path, monkeypatch):
    def revert(day):
        raise RuntimeError("execution reverted")
    res, rows, _ = _archive(tmp_path, monkeypatch, _empty_before("2026-10-24", before=revert))
    assert [d for d, _ in rows] == ["2026-10-24", "2026-10-25", "2026-10-26"]
    assert _lines(res[0], f"NOT WRITTEN {PN}/{CD}: 3 day(s) — spendle_cooldown (ethereum): execution reverted")
    assert not _lines(res[0], f"{PN}/{CD}: not available before")

    def rpc(day):
        raise RuntimeError("HTTPSConnectionPool: Read timed out")
    res, _, _ = _archive(tmp_path / "b", monkeypatch, _empty_before("2026-10-24", before=rpc))
    assert _lines(res[0], f"NOT WRITTEN {PN}/{CD}: 3 day(s)") and not _lines(res[0], f"{PN}/{CD}: not available before")


def test_the_probe_that_errs_keeps_the_day_a_failure(tmp_path, monkeypatch):
    """10-23 answers 0x but the oldest day (10-21) cannot be read: nothing is confirmed, nothing is skipped."""
    def answer(day):
        if day >= pd.Timestamp("2026-10-24"):
            return 1_209_600
        if day == pd.Timestamp("2026-10-21"):
            raise RuntimeError("429 Client Error: Too Many Requests")
        raise ValueError(EMPTY)
    res, _, asked = _archive(tmp_path, monkeypatch, answer)
    assert not _lines(res[0], f"{PN}/{CD}: not available before")
    assert _lines(res[0], f"NOT WRITTEN {PN}/{CD}: 3 day(s)")
    assert "2026-10-22" in asked


# ============================================================================================ sPENDLE's own code
def test_spendle_is_called_so_its_empty_code_dates_the_series_again(tmp_path, monkeypatch):
    """P2b-2 checked code only on holders meant to have it; sPENDLE is the CALLED contract of cooldownDuration(), so
    its empty code before block 24,237,456 is a deployment date again, and a start recorded that way is honoured."""
    from fetch import archive as ar
    from fetch.chain import address_is_called
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    spec = config.PROJECT_BY_NAME[PN]["contracts"]["spendle_cooldown"]
    assert address_is_called(spec)
    assert not address_is_called(config.PROJECT_BY_NAME["Chainlink"]["contracts"]["noncirc_cl_page_4"])

    class Rdr:
        at_block = {"ethereum": 24_000_000}

        def web3(self, chain):
            return SimpleNamespace(eth=SimpleNamespace(get_code=lambda a, block_identifier=None: b""))

        def checksum(self, a):
            return a

        def deployment_block(self, chain, a):
            return 24_237_456
    bf = ar.ArchiveBackfill.__new__(ar.ArchiveBackfill)
    bf.reader = Rdr()
    why = bf._undeployed(config.PROJECT_BY_NAME[PN], CD)
    assert why and why.startswith("spendle_cooldown 0x99999999... has no code at block 24,000,000")
    assert "deployed at block 24,237,456" in why
    monkeypatch.setattr(config, "ARCHIVE_SERIES_START", {})
    ar.record_series_start(PN, CD, "2026-01-16", f"{why} — not deployed before 2026-01-16")
    assert ar.series_starts()[(PN, CD)]["from"] == "2026-01-16"           # P2b-2 had dropped this record


def test_the_declared_start_is_the_live_reading():
    s = config.ARCHIVE_SERIES_START[(PN, CD)]
    assert s["from"] == "2026-01-16" and "24,237,456" in s["why"]
    assert config.series_absent_before(PN, CD) == (
        "not available before 2026-01-16 (cooldownDuration() absent before sPENDLE's deployment at block 24,237,456 "
        "on 2026-01-15 — a pre-deployment absence, not a hole to fill)")
    assert config.series_absent_before(PN, CD, "2026-02-01") is None      # a window that starts after it: nothing
    assert config.series_absent_before(PN, "locked_tokens") is None


# ============================================================================================ the Data row
def test_the_data_row_names_the_date_not_a_hole():
    import build_workbook as bw
    asof = pd.Timestamp("2026-10-27")
    days = pd.date_range("2026-01-16", "2026-10-26", freq="D")
    long = pd.DataFrame({"date": days, "project": PN, "metric": CD, "value": 14.0, "tier": 2,
                         "source": "chain:ethereum:spendle_cooldown", "is_manual": False, "entered_on": "",
                         "source_note": ""})
    data = bw.aggregate(long, pd.DataFrame(), asof)
    row = data[(data["project"] == PN) & (data["metric"] == CD)].iloc[0]
    assert str(row["note"]).startswith("not available before 2026-01-16 (cooldownDuration() absent before sPENDLE's "
                                       "deployment at block 24,237,456"), row["note"]
    assert "hole" not in str(row["note"]).split(" | ")[0].replace("not a hole to fill", "")
    assert row["now"] == 14.0
    assert pd.isna(row["q4"]) or row["q4"] is None                         # nothing before the start, never 0
