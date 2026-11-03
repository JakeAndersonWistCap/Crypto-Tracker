"""ROUND P4-Q3 (Jake 2026-11-03, SPEC P4-Q3) — Chainlink's releases are the balance falls; the transfer scan labels.

unlock_release_tokens = the daily fall in Σ balanceOf of the 33 non-circulating wallets (noncirculating_holding_tokens),
by build_workbook._unlock_release_view — the method every other project uses: a rise releases 0 (it is
issuer_return_tokens), a day a wallet was not read is a hole, never 0. The explorer transfer scan (log_scans noncirc_out /
noncirc_in) reads only the blocks after its last successful block, labels destinations, and never gates the release.

Fakes only (no network). The fixture build check runs on the committed real dumps (fixtures/real_*.json).
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

CL = "Chainlink"
NC = "noncirculating_holding_tokens"
POINT33 = config.CHAINLINK_NONCIRC_POINTS["33"]
E18 = 10 ** 18


# ============================================================================================== helpers
def _stock(days, values, sources):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": CL, "metric": NC, "value": values, "tier": 2,
                         "source": sources, "fetched_at": "2026-11-03T00:00:00Z"})


def _release(monkeypatch, groups):
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[CL]])
    bw._unlock_views(groups)
    g = groups.get((CL, "unlock_release_tokens"))
    s = (pd.Series(dtype=float) if g is None else
         g.assign(date=pd.to_datetime(g["date"])).set_index("date")["value"].astype(float).sort_index())
    r = groups.get((CL, "issuer_return_tokens"))
    ret = (pd.Series(dtype=float) if r is None else
           r.assign(date=pd.to_datetime(r["date"])).set_index("date")["value"].astype(float).sort_index())
    return s, ret


def _scan_specs():
    return {s["key"]: s for s in config.PROJECT_BY_NAME[CL]["log_scans"] if s["key"] in ("noncirc_out", "noncirc_in")}


# ============================================================================================== the definition
def test_q3_the_release_legs_are_the_33_wallets_balance_only():
    """Chainlink's release reads one leg — the summed balanceOf of the 33 wallets — and no scan series."""
    assert config.unlock_stock_metrics(CL) == (NC,)
    assert config.CHAINLINK_RELEASE_Q3["wallets"] == 33 == len(config._CL_NC_KEYS_ALL)
    assert POINT33.startswith("chain:sum(") and POINT33.count("ethereum:") == 33
    assert CL not in config.UNLOCK_RELEASE_SUBSETS and CL not in config.RELEASE_LESS
    assert (CL, NC) in config.RELEASE_WHOLE_SET_ONLY


def test_q3_release_is_the_sum_of_daily_balance_falls_and_a_rise_is_not_a_release(monkeypatch):
    days = pd.date_range("2026-10-01", periods=6)
    vals = [1000.0, 990.0, 990.0, 1005.0, 900.0, 899.5]            # falls 10, 0, rise 15, falls 105, 0.5
    groups = {(CL, NC): _stock(days, vals, [POINT33] * 6)}
    rel, ret = _release(monkeypatch, groups)
    falls = (pd.Series(vals, index=days).shift(1) - pd.Series(vals, index=days)).dropna()
    assert rel.sum() == pytest.approx(falls.clip(lower=0).sum()) == pytest.approx(115.5)
    assert list(rel.round(6)) == [10.0, 0.0, 0.0, 105.0, 0.5]       # the rise day releases 0, never −15
    assert ret.sum() == pytest.approx(15.0) and ret.loc["2026-10-04"] == pytest.approx(15.0)
    src = set(groups[(CL, "unlock_release_tokens")]["source"])
    assert src == {f"derived:unlock_release[{NC}]"}, src             # nothing from a scan in the source


def test_q3_a_day_a_wallet_was_not_read_is_a_hole_never_a_zero(monkeypatch):
    """A PARTIAL sum (a wallet not read) is never differenced: no fall into it, no rise out of it; a day with no row
    is not a 0 — the fall across it is spread over the days it spans, as the method does everywhere."""
    days = pd.date_range("2026-10-01", periods=5)
    partial = config.mark_source(POINT33, "PARTIAL")
    vals = [100.0, 90.0, 50.0, 80.0, 80.0]                           # day 3 missed a 30-LINK wallet... and more
    rel, _ret = _release(monkeypatch, {(CL, NC): _stock(days, vals, [POINT33, POINT33, partial, POINT33, POINT33])})
    assert rel.sum() == pytest.approx(20.0)                          # 100 -> 90 -> (hole) -> 80 -> 80
    assert rel.loc["2026-10-03"] == pytest.approx(5.0) and rel.loc["2026-10-04"] == pytest.approx(5.0)
    # a day with no row at all: the same
    keep = [0, 1, 3, 4]
    rel2, _ = _release(monkeypatch, {(CL, NC): _stock(days[keep], [vals[i] for i in keep], [POINT33] * 4)})
    assert rel2.sum() == pytest.approx(20.0)
    # a gap wider than the method's 3 days is not differenced (unknown, never a release)
    far = pd.to_datetime(["2026-10-01", "2026-10-06"])
    rel3, _ = _release(monkeypatch, {(CL, NC): _stock(far, [100.0, 50.0], [POINT33] * 2)})
    assert rel3.empty


def test_q3_the_archive_backfill_fills_the_same_series(monkeypatch):
    """The archive read (archive_backfill.py) writes the same point marked `archive`: its days difference with the
    live days, so the backfill keeps filling the balance series' history (and so the releases)."""
    days = pd.date_range("2026-09-28", periods=4)
    arch = config.mark_source(POINT33, "archive")
    rel, _ = _release(monkeypatch, {(CL, NC): _stock(days, [500.0, 480.0, 470.0, 470.0], [arch, arch, POINT33, POINT33])})
    assert rel.sum() == pytest.approx(30.0) and len(rel) == 3
    r = config.restatement(CL, NC)
    assert r["point"] == POINT33 and "archive_backfill.py --run --project Chainlink" in r["refill"]


def test_q3_the_staking_funded_part_stays_counted_once(monkeypatch):
    """Round 3 B2 stands: the LINK a release sent on to fund staking rewards is (a), out of (e). The two together are
    the balance falls, day by day."""
    days = pd.date_range("2026-10-01", periods=3)
    groups = {(CL, NC): _stock(days, [1000.0, 900.0, 900.0], [POINT33] * 3)}
    for m, v in (("staking_path_in_tokens", 100.0), ("staking_path_out_tokens", 100.0), ("staking_funding_tokens", 60.0)):
        groups[(CL, m)] = pd.DataFrame({"date": [days[1]], "project": CL, "metric": m, "value": [v], "tier": 2,
                                        "source": "explorer:x"})
    rel, _ = _release(monkeypatch, groups)
    fund = groups[(CL, "staking_funding_release_tokens")]
    assert rel.sum() == pytest.approx(40.0) and float(fund["value"].sum()) == pytest.approx(60.0)
    assert rel.sum() + float(fund["value"].sum()) == pytest.approx(100.0)


# ============================================================================================== the scan never gates
def test_q3_the_scan_is_labels_only_and_holds_no_headline_reserve():
    specs = _scan_specs()
    assert set(specs) == {"noncirc_out", "noncirc_in"}
    holders = {a.lower() for a in config._CL_NC_ALL}
    assert len(holders) == 33
    for s in specs.values():
        assert s["labels_only"] and s["headline"] is False and {h.lower() for h in s["holders"]} == holders
        assert not config.scan_feeds_headline(CL, s) and config.scan_reserve_s(CL, s) == 0.0
        assert not s.get("window_days") and "LABELS DESTINATIONS ONLY" in s["role"]
        assert s["own_tier"] == f"explorer_noncirc{config.PROJECT_TIER_SEP}{CL}"
    for m in ("noncirc_outflow_scan_tokens", "noncirc_inflow_scan_tokens"):
        assert m not in config.HEADLINE_SCAN_METRICS
    assert "cashback_paid_tokens" in config.HEADLINE_SCAN_METRICS      # the other headline scans keep their reserve
    lab = specs["noncirc_out"]["destination_labels"]
    assert config.CL_STAKING_PATH["lump_wallet"].lower() in lab
    assert config.PROJECT_BY_NAME[CL]["contracts"]["reserve"]["address"].lower() in lab


def test_q3_the_release_is_the_same_with_or_without_the_scan(monkeypatch):
    """The scan's series present, absent (a timed-out run stores none) or disagreeing: the release does not move."""
    days = pd.date_range("2026-10-01", periods=5)
    base = _stock(days, [100.0, 90.0, 85.0, 85.0, 60.0], [POINT33] * 5)
    a, _ = _release(monkeypatch, {(CL, NC): base.copy()})
    scan = {(CL, m): pd.DataFrame({"date": days, "project": CL, "metric": m, "value": [999.0] * 5, "tier": 2,
                                   "source": f"explorer:{k}"})
            for m, k in (("noncirc_outflow_scan_tokens", "noncirc_out"), ("noncirc_inflow_scan_tokens", "noncirc_in"))}
    b, _ = _release(monkeypatch, {(CL, NC): base.copy(), **scan})
    pd.testing.assert_series_equal(a, b)
    assert a.sum() == pytest.approx(40.0)


# ============================================================================================== the scan, with fakes
WALLETS = [a.lower() for a in config._CL_NC_ALL]
LUMP = config.CL_STAKING_PATH["lump_wallet"].lower()
STRANGER = "0x00000000000000000000000000000000000000bb"


class _CountingReader:
    """test_adapters._FakeReader with a moving head and a count of balanceOf reads; dated_logs refuses to be used."""

    def __init__(self, balances, head):
        from test_adapters import _FakeReader
        self._r = _FakeReader(balances, head=head)
        self.reads = 0

    def web3(self, chain):                            # the chain moves on: each head read is one block later
        w = self._r.web3(chain)
        self._r.head += 1
        return w

    def erc20(self, chain, token):
        import types
        inner = self._r.erc20(chain, token)
        outer = self

        class _F:
            def balanceOf(self, h):
                outer.reads += 1
                return inner.functions.balanceOf(h)

            def decimals(self):
                return inner.functions.decimals()
        return types.SimpleNamespace(functions=_F())

    @staticmethod
    def checksum(a):
        return a

    def dated_logs(self, *a, **k):                    # a history repair from the chain: never for a labels-only scan
        raise AssertionError("a labels-only scan must not re-read history from the chain")


def _logs(extra_out=()):
    """Every wallet received 100 LINK at block 100 (2025-01-01); wallet 0 sent 30 to the staking lump wallet at block
    500 (2026-10-02) and 1 to a stranger at block 600; `extra_out` adds (block, to, value, date) outflows of wallet 0."""
    from test_adapters import _flow_logs
    ts = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731
    logs = {}
    for i, w in enumerate(WALLETS):
        outs = [(500, LUMP, 30 * E18, ts("2026-10-02")), (600, STRANGER, 1 * E18, ts("2026-10-02"))] if i == 0 else []
        if i == 0:
            outs += [(b, to, v, ts(d)) for b, to, v, d in extra_out]
        logs.update(_flow_logs(w, [(100, "0x00000000000000000000000000000000000000aa", 100 * E18, ts("2025-01-01"))],
                               outs))
    bal = {w: 100 * E18 for w in WALLETS}
    bal[WALLETS[0]] = 69 * E18 - sum(v for _b, _t, v, _d in extra_out)
    return logs, bal


def _run(monkeypatch, tmp_path, ls, key):
    from fetch.base import FetchOutput
    out = FetchOutput()
    ls._scan({"name": CL}, dict(_scan_specs()[key], token="0xtoken"), None, out)
    return out


def _ls(monkeypatch, tmp_path, logs, bal, head):
    from fetch.logcache import LogCache
    from fetch.logscan import LogScan
    from test_adapters import _explorer
    ex = _explorer(monkeypatch, logs, max_records=1000)
    reader = _CountingReader(bal, head)
    return LogScan(explorer=ex, cache=LogCache(tmp_path / "cache"), reader=reader), ex, reader


def test_q3_the_scan_reads_only_after_its_cursor_and_the_second_scan_reads_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr("fetch.logscan.today", lambda: pd.Timestamp("2026-10-05"))
    logs, bal = _logs()
    ls, ex, reader = _ls(monkeypatch, tmp_path, logs, bal, head=1_000)            # pinned block 980
    out = _run(monkeypatch, tmp_path, ls, "noncirc_out")
    msg = " ".join(e.message for e in out.log)
    assert "RECONCILED to the wei for 33 holder(s) at block 980" in msg, msg[:400]
    first_calls = [c[1] for c in ex.http.calls]
    assert len(first_calls) == 66 and {int(p["fromBlock"]) for p in first_calls} == {0}   # the first read: the seed
    assert reader.reads == 33
    # the second labels-only scan of the same run: the same pinned block, nothing after the cursor, balances shared
    out_in = _run(monkeypatch, tmp_path, ls, "noncirc_in")
    assert len(ex.http.calls) == 66 and reader.reads == 33
    assert any("RECONCILED to the wei for 33 holder(s) at block 980" in e.message for e in out_in.log)
    # THE NEXT RUN: a new head; every stream asks only for the blocks after its cursor (981..)
    logs2, bal2 = _logs(extra_out=[(1_500, LUMP, 5 * E18, "2026-10-04")])
    ls2, ex2, _reader2 = _ls(monkeypatch, tmp_path, logs2, bal2, head=2_000)    # pinned block 1,980
    out2 = _run(monkeypatch, tmp_path, ls2, "noncirc_out")
    asked = {int(c[1]["fromBlock"]) for c in ex2.http.calls}
    assert asked == {981}, asked
    m2 = " ".join(e.message for e in out2.log)
    assert "incremental:" in m2 and "1 new to block 1,980" in m2, m2[:500]
    # DESTINATION LABELS ATTACH: this run's new outflow, labelled from config
    assert "LABELS (destinations of the transfers in blocks 981..1,980, read after the cursor)" in m2
    assert f"{LUMP} 5.00 [staking/payments path, first hop" in m2
    assert "LABELS ONLY (P4-Q3)" in m2
    # the all-time counterparty table is labelled too; an unknown destination says so
    assert f"{LUMP} 35.00 [staking/payments path, first hop" in m2
    f = out2.frame()
    s = f[f["metric"] == "noncirc_outflow_scan_tokens"].set_index("date")["value"]
    assert s.loc[pd.Timestamp("2026-10-02")] == pytest.approx(31.0) and s.loc[pd.Timestamp("2026-10-04")] == 5.0


def test_q3_a_hop_between_two_of_the_33_is_internal(monkeypatch, tmp_path):
    """A transfer from one of the 33 to another is neither a destination nor an inflow (the balance sum nets it)."""
    from test_adapters import _flow_logs
    monkeypatch.setattr("fetch.logscan.today", lambda: pd.Timestamp("2026-10-05"))
    logs, bal = _logs()
    ts = int(pd.Timestamp("2026-10-03").timestamp())
    a, b = WALLETS[1], WALLETS[32]                                   # one of the 27 -> one of the 6 page wallets
    for k, v in _flow_logs(a, [], [(700, b, 10 * E18, ts)]).items():
        logs.setdefault(k, []).extend(v)
    for k, v in _flow_logs(b, [(700, a, 10 * E18, ts)]).items():
        logs.setdefault(k, []).extend(v)
    for k in logs:
        logs[k].sort(key=lambda r: int(r["blockNumber"], 16))
    bal[a] -= 10 * E18
    bal[b] += 10 * E18
    ls, _ex, _r = _ls(monkeypatch, tmp_path, logs, bal, head=1_000)
    out = _run(monkeypatch, tmp_path, ls, "noncirc_out")
    f = out.frame()
    s = f[f["metric"] == "noncirc_outflow_scan_tokens"].set_index("date")["value"]
    assert s.loc[pd.Timestamp("2026-10-03")] == 0.0 and s.sum() == pytest.approx(31.0)


def test_q3_a_scan_timeout_leaves_the_release_unchanged(monkeypatch, tmp_path):
    from fetch.explorer import ExplorerTimeout
    monkeypatch.setattr("fetch.logscan.today", lambda: pd.Timestamp("2026-10-05"))
    logs, bal = _logs()
    ls, ex, _r = _ls(monkeypatch, tmp_path, logs, bal, head=1_000)

    def timeout(*a, **k):
        raise ExplorerTimeout("explorer scan timed out after 1s, provider errors: none recorded")
    monkeypatch.setattr(ex, "get_logs", timeout)
    out = _run(monkeypatch, tmp_path, ls, "noncirc_out")
    fails = [e.message for e in out.log if e.status == "failed"]
    assert fails and "LABELS ONLY (P4-Q3)" in fails[0] and "leaves it unchanged" in fails[0]
    assert out.frame().empty                                          # the scan stored nothing ...
    # ... and the release, built from the balances, is what it is without the scan
    days = pd.date_range("2026-10-01", periods=4)
    base = _stock(days, [100.0, 70.0, 69.0, 69.0], [POINT33] * 4)
    rel, _ = _release(monkeypatch, {(CL, NC): base})
    assert rel.sum() == pytest.approx(31.0)


def test_q3_an_unreconciled_labels_scan_never_repairs_history(monkeypatch, tmp_path):
    """A balance that does not reconcile: nothing stored, no chain history re-read (the reader's dated_logs would
    raise), the cache cut back to its last proven block — the next run reads after it."""
    monkeypatch.setattr("fetch.logscan.today", lambda: pd.Timestamp("2026-10-05"))
    logs, bal = _logs()
    bal[WALLETS[5]] += 1                                             # one wei unaccounted for
    ls, _ex, _r = _ls(monkeypatch, tmp_path, logs, bal, head=1_000)
    out = _run(monkeypatch, tmp_path, ls, "noncirc_out")
    fails = [e.message for e in out.log if e.status == "failed"]
    assert any("SCAN DOES NOT RECONCILE" in m for m in fails), fails
    assert out.frame().empty


# ============================================================================================== the fixture build
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import build_workbook as bw
    import measuring_guard as mg
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("p4_q3")
    db = d / "metrics.db"
    asofs = []
    for pl in mg._payloads():
        to_store(pl, db)
        asofs.append(pl["meta"]["asof"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setattr(bw, "_SCOPE", list(bw.PROJECTS))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=pd.Timestamp(max(asofs)), reader_path=d / "reader.xlsx")
        finally:
            st.close()
    import xlcalc
    a = openpyxl.load_workbook(d / "audit.xlsx")
    return {"audit": a, "XA": xlcalc.Workbook(a), "groups": dict(bw._LAST_GROUPS), "asof": pd.Timestamp(max(asofs))}


def test_q3_fixture_q0_release_equals_the_sum_of_the_balance_falls(built):
    """On the committed Chainlink dump the release series is the balance falls, and the Data tab's Q0 equals their sum
    over Q0. THE FIXTURE HOLDS THE 27-WALLET SERIES (dumped 2026-10-13, before the 33-wallet set of 2026-10-26 and the
    P2b-2 restatement): the same method on the set it holds."""
    g = built["groups"]
    stock = g[(CL, NC)].assign(date=lambda x: pd.to_datetime(x["date"]).dt.normalize())
    stock = stock.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    from fetch.base import _measuring_point
    assert set(stock["source"].map(_measuring_point)) == {config.CHAINLINK_NONCIRC_POINTS["27"]}
    v = stock["value"].astype(float)
    gap = v.index.to_series().diff().dt.days
    falls = {}
    for day, fall, k in zip(v.index[gap <= 3], (v.shift(1) - v)[gap <= 3], gap[gap <= 3]):
        for j in range(int(k)):
            falls[day - pd.Timedelta(days=j)] = fall / int(k)
    falls = pd.Series(falls).sort_index().clip(lower=0.0)
    rel = g[(CL, "unlock_release_tokens")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")["value"]
    assert (CL, "staking_funding_release_tokens") not in g        # no staking-path scan rows on this dump
    pd.testing.assert_series_equal(rel.astype(float).sort_index(), falls.reindex(rel.index).astype(float),
                                   check_names=False, check_freq=False)
    asof = built["asof"]
    q0 = falls[(falls.index > asof - pd.Timedelta(days=90)) & (falls.index <= asof)].sum()
    ws = built["audit"]["Data"]
    r = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == f"{CL}|unlock_release_tokens")
    assert str(ws.cell(1, 12).value).startswith("Q0")
    got = built["XA"].value("Data", r, 12)
    assert got == pytest.approx(q0, rel=1e-12) and q0 == pytest.approx(18_750_000, rel=1e-9)
