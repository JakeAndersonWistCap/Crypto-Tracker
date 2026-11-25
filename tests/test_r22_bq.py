"""Round 22, item 7 (Jake 2026-11-25) — the one-shot BILLED BigQuery history (bigquery_backfill.py). Fakes only: no
BigQuery, no billing API. Behaviour and numbers: the refusal without billing, the 4 TB cap, maximum_bytes_billed on
every query, a chunk over the per-query cap halved, resume after a partial run, the dry-run print, the ranges."""
import datetime as dt
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import bigquery_backfill as bb
import config
import near_p2p_history as nh

ROOT = Path(__file__).resolve().parent.parent
D = dt.date.fromisoformat
GB = 10 ** 9
BILLING_ON = {"project": "near-data-510309", "billing_enabled": True, "confirmed_by": "Jake (test)",
              "confirmed_on": "2026-12-01"}


class _Bq:
    class QueryJobConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.dry_run = kw.get("dry_run", False)
            self.maximum_bytes_billed = kw.get("maximum_bytes_billed")
    ScalarQueryParameter = staticmethod(lambda k, t, v: (k, t, v))
    ArrayQueryParameter = staticmethod(lambda k, t, v: (k, t, tuple(v)))


class _Client:
    """Both chains: NEAR's SQL names crypto_near_mainnet_us, Ethereum's crypto_ethereum. Dry runs answer days x GB a day;
    a real query returns one native row a day (minus `drop`) and records its maximum_bytes_billed."""

    def __init__(self, near_gb=7.54, eth_gb=1.354, drop=(), stop_after=None):
        self.rate = {"Near": near_gb, "Ethereum": eth_gb}
        self.drop, self.stop_after = set(drop), stop_after
        self.dry, self.real, self.caps = [], [], []

    @staticmethod
    def _chain(sql):
        return "Ethereum" if "crypto_ethereum" in sql else "Near"

    def query(self, sql, job_config=None):
        p = {k: v for k, _, v in job_config.query_parameters}
        chain, win = self._chain(sql), (str(p["d0"]), str(p["d1"]))
        n = int(((D(win[1]) - D(win[0])).days + 1) * self.rate[chain] * GB)
        if job_config.dry_run:
            self.dry.append((chain, *win))
            return SimpleNamespace(total_bytes_processed=n)
        if self.stop_after is not None and len(self.real) >= self.stop_after:
            raise KeyboardInterrupt("the run is killed mid-query")
        self.real.append((chain, *win))
        self.caps.append(job_config.maximum_bytes_billed)
        native = "NEAR" if chain == "Near" else "ETH"
        rows = [{"day": d, "token": native, "amount": "1000", "n": 1}
                for d in pd.date_range(*win) if str(d.date()) not in self.drop]
        return SimpleNamespace(result=lambda: rows, total_bytes_billed=n, project="near-data-510309")


def _setup(tmp_path, monkeypatch, client=None, sub="a", near_from="2025-09-01", eth_from="2026-03-01", cap_gb=4000.0,
           billing=BILLING_ON):
    from fetch import near_bigquery as nbq
    tmp_path = tmp_path / sub
    tmp_path.mkdir()
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp("2026-12-02"))
    monkeypatch.setattr(config, "BIGQUERY_BILLING", dict(billing))
    one = {**config.BIGQUERY_ONESHOT, "total_cap_gb": cap_gb,
           "near": {**config.BIGQUERY_ONESHOT["near"], "from": near_from},
           "ethereum": {**config.BIGQUERY_ONESHOT["ethereum"], "from": eth_from}}
    monkeypatch.setattr(config, "BIGQUERY_ONESHOT", one)
    monkeypatch.setenv("TOKEN_METRICS_STORE_LOCK", str(tmp_path / "store.lock"))
    cache = tmp_path / "near-bq.json"
    near_days = {str(d.date()): {"NEAR": "1"} for d in pd.date_range("2025-12-01", "2026-11-30")}
    eth_days = {str(d.date()): {"ETH": "1"} for d in pd.date_range("2026-05-28", "2026-11-22")}
    st = {"ledger": {"2026-12": 5 * GB}, "prices": {}, "tokens": {"list": ["wrap.near"], "on": "2026-12-01"},
          "days": near_days, "eth_p2p": {"days": eth_days, "prices": {}}}
    cache.write_text(json.dumps(st))
    db = tmp_path / "metrics.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT)")
    rows = [(str(d.date()), pj, m, 1.0, "t") for d in pd.date_range("2024-11-01", "2026-12-01")
            for pj, m in (("Near", "price_usd"), ("Near", "dex_volume_usd"), ("Ethereum", "price_usd"))]
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    client = client or _Client()
    rd = bb.readers(client=client, bq=_Bq, cache_file=cache, csv_dir=tmp_path / "x")
    return client, rd, cache, db


def _on(project):
    return True, "fake Cloud Billing API: billingEnabled"


# ================================================================================================= refusal
def test_without_billing_nothing_is_called_and_nothing_billed(tmp_path, monkeypatch, capsys):
    off = {**BILLING_ON, "billing_enabled": False}
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, billing=off)
    before = cache.read_text()
    asked = []
    assert bb.cmd_run(db, rd=rd, check=lambda p: asked.append(p) or (True, "x"), today="2026-12-02") == 3
    said = capsys.readouterr().out
    assert "REFUSED" in said and "no BigQuery call" in said and "free-tier schedule" in said
    assert cl.dry == cl.real == [] and asked == [] and cache.read_text() == before


def test_the_flag_alone_is_never_billing(tmp_path, monkeypatch, capsys):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch)
    # the runtime check says billing is off
    assert bb.cmd_run(db, rd=rd, check=lambda p: (False, "api"), today="2026-12-02") == 3
    assert "billing is OFF" in capsys.readouterr().out
    # the runtime check cannot be made: refused, and says so
    assert bb.cmd_run(db, rd=rd, check=lambda p: (None, "no credentials"), today="2026-12-02") == 3
    said = capsys.readouterr().out
    assert "could not be made" in said and "no credentials" in said
    assert cl.dry == cl.real == []
    # an incomplete or future-dated flag
    assert "not complete" in bb.flag_refusal({**BILLING_ON, "confirmed_by": ""}, "2026-12-02")
    assert "after today" in bb.flag_refusal(BILLING_ON, "2026-11-30")
    assert bb.flag_refusal(BILLING_ON, "2026-12-01") is None


def test_the_shipped_config_has_billing_off():
    assert config.BIGQUERY_BILLING["billing_enabled"] is False and config.BIGQUERY_BILLING["project"] == "near-data-510309"


def test_the_runtime_check_without_libraries_cannot_confirm_billing(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_google(name, *a, **k):
        if name.startswith("google"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_google)
    ok, how = bb.billing_check("near-data-510309")
    assert ok is None and "is installed" in how and "pip install" in how


# ================================================================================================= the bytes
def test_every_query_carries_maximum_bytes_billed_and_the_run_fills_both_chains(tmp_path, monkeypatch, capsys):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch)
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 0
    # NEAR: Nov, Oct, Sep 2025 newest first; Ethereum: Mar, Apr, May 2026 (to 05-27: 05-28.. is held)
    assert cl.real == [("Near", "2025-11-01", "2025-11-30"), ("Near", "2025-10-01", "2025-10-31"),
                       ("Near", "2025-09-01", "2025-09-30"), ("Ethereum", "2026-03-01", "2026-03-31"),
                       ("Ethereum", "2026-04-01", "2026-04-30"), ("Ethereum", "2026-05-01", "2026-05-27")]
    # every chunk dry-run BEFORE the first billed query (the plan), and again by the reader just before its query
    assert len(cl.dry) == 2 * len(cl.real)
    for (chain, a, b), cap in zip(cl.real, cl.caps):
        dry = int(((D(b) - D(a)).days + 1) * cl.rate[chain] * GB)
        assert cap is not None and dry < cap <= int(dry * 1.05) + 10 * 1024 ** 2
        assert cap <= (400 * GB if chain == "Near" else 60 * GB)
    st = json.loads(cache.read_text())
    assert min(st["days"]) == "2025-09-01" and len(st["days"]) == 365 + 91
    assert min(st["eth_p2p"]["days"]) == "2026-03-01" and "2026-05-27" in st["eth_p2p"]["days"]
    # the free-tier ledger is untouched (the daily reads keep their month); the billed bytes have their own
    assert st["ledger"] == {"2026-12": 5 * GB}
    assert st["ledger_billed"]["2026-12"] == bb.spent_bytes(st) == sum(
        int(((D(b) - D(a)).days + 1) * cl.rate[c] * GB) for c, a, b in cl.real)
    assert [a["status"] for a in st["oneshot"]["attempts"]] == ["done"] * 6
    # a one-shot chunk is not one of the month's approved NEAR chunks
    assert nh.done_in_month(st, "2026-12-15") == 0
    said = capsys.readouterr().out
    assert "DRY RUNS FIRST" in said and "TOTAL dry runs" in said and "$" in said and "token_metrics.py --project Near" in said
    assert not (cache.parent / "near-bq.json.tmp").exists()             # the state is written whole, by a rename


def test_the_total_cap_refuses_before_any_billed_query(tmp_path, monkeypatch, capsys):
    # 91 NEAR days x 7.54 + 88 Ethereum days x 1.354 = 805.3 GB of dry runs; their maximum_bytes_billed ~845.6 GB
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, cap_gb=800.0)
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 3
    said = capsys.readouterr().out
    assert "over the 800 GB cap" in said and cl.real == [] and len(cl.dry) == 6
    # judged on the worst case (every maximum_bytes_billed), not on the dry runs alone
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, cap_gb=830.0, sub="b")
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 3 and cl.real == []
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, cap_gb=850.0, sub="c")
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 0 and len(cl.real) == 6
    # what earlier runs of the one-shot billed counts against the cap
    st = {"oneshot": {"attempts": [{"status": "done", "billed_bytes": 3_300 * GB},
                                   {"status": "interrupted", "max_bytes": 100 * GB, "billed_bytes": 0}]}}
    assert bb.spent_bytes(st) == 3_400 * GB
    planned = [{"max_bytes": 600 * GB}]
    assert bb.cap_refusal(st, planned, {"total_cap_gb": 4000.0}) is None
    assert "4,000 GB cap" in bb.cap_refusal(st, planned + [{"max_bytes": 1}], {"total_cap_gb": 4000.0})


def test_a_chunk_over_the_per_query_cap_is_halved_until_it_fits(tmp_path, monkeypatch):
    # Ethereum at 3 GB a day: a 31-day month is 93 GB > the 60 GB per-query cap -> 15 + 16 days
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, client=_Client(eth_gb=3.0), eth_from="2026-03-01")
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 0
    eth = [w for w in cl.real if w[0] == "Ethereum"]
    assert eth[:2] == [("Ethereum", "2026-03-01", "2026-03-15"), ("Ethereum", "2026-03-16", "2026-03-31")]
    assert all(c <= 60 * GB for w, c in zip(cl.real, cl.caps) if w[0] == "Ethereum")
    assert bb.fit((D("2026-03-01"), D("2026-03-01")), lambda c: 99 * GB, 60 * GB) == [
        (D("2026-03-01"), D("2026-03-01"), 99 * GB)]                  # one day over the cap: returned, the run refuses


# ================================================================================================= resumable
def test_a_killed_run_resumes_without_reading_a_held_day_and_counts_the_unknown_query_at_its_cap(tmp_path, monkeypatch):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, client=_Client(stop_after=2))
    with pytest.raises(KeyboardInterrupt):
        bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02")
    st = json.loads(cache.read_text())
    # two chunks kept whole; the third's days are not held; its attempt is recorded as interrupted at its maximum
    assert min(st["days"]) == "2025-10-01" and not any(d.startswith("2025-09") for d in st["days"])
    att = st["oneshot"]["attempts"]
    assert [a["status"] for a in att] == ["done", "done", "interrupted"]
    assert bb.spent_bytes(st) == att[0]["billed_bytes"] + att[1]["billed_bytes"] + att[2]["max_bytes"]
    # the lock was released on the way out
    assert not (cache.parent / "store.lock").exists()
    # the rerun reads only what is not held: September 2025 and Ethereum's three months
    cl2 = _Client()
    rd2 = bb.readers(client=cl2, bq=_Bq, cache_file=cache, csv_dir=tmp_path / "x")
    assert bb.cmd_run(db, rd=rd2, check=_on, today="2026-12-02") == 0
    assert cl2.real[0] == ("Near", "2025-09-01", "2025-09-30") and len(cl2.real) == 4
    st = json.loads(cache.read_text())
    assert [a["status"] for a in st["oneshot"]["attempts"]] == ["done", "done", "interrupted"] + ["done"] * 4
    # a third run has nothing to read
    cl3 = _Client()
    assert bb.cmd_run(db, rd=bb.readers(client=cl3, bq=_Bq, cache_file=cache), check=_on, today="2026-12-02") == 0
    assert cl3.real == [] and cl3.dry == []


def test_a_chunk_with_a_missing_day_keeps_nothing_and_is_asked_again(tmp_path, monkeypatch):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch, client=_Client(drop=("2026-04-10",)))
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 1
    st = json.loads(cache.read_text())
    assert not any(d.startswith("2026-04") for d in st["eth_p2p"]["days"])
    a = [x for x in st["oneshot"]["attempts"] if x["d0"] == "2026-04-01"][0]
    assert a["status"] == "failed" and a["billed_bytes"] > 0          # billed is billed: it counts against the cap
    cl2 = _Client()
    assert bb.cmd_run(db, rd=bb.readers(client=cl2, bq=_Bq, cache_file=cache), check=_on, today="2026-12-02") == 0
    assert cl2.real == [("Ethereum", "2026-04-01", "2026-04-30")]


def test_a_chunk_the_store_cannot_value_is_held_back_unbilled(tmp_path, monkeypatch, capsys):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM metrics WHERE project='Near' AND metric='dex_volume_usd' AND date LIKE '2025-10-%'")
    con.commit()
    con.close()
    assert bb.cmd_run(db, rd=rd, check=_on, today="2026-12-02") == 0
    assert ("Near", "2025-10-01", "2025-10-31") not in cl.real and ("Near", "2025-11-01", "2025-11-30") in cl.real
    assert "HELD BACK Near 2025-10-01..2025-10-31" in capsys.readouterr().out


# ================================================================================================= print and plan
def test_the_dry_run_prints_gb_and_dollars_per_chunk_and_bills_nothing(tmp_path, monkeypatch, capsys):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch)
    before = cache.read_text()
    assert bb.cmd_dry_run(db, rd=rd) == 0
    said = capsys.readouterr().out
    assert cl.real == [] and len(cl.dry) == 6 and cache.read_text() == before
    # NEAR November 2025: 30 x 7.54 = 226.2 GB = $1.29 at $6.25 a TiB
    assert "2025-11-01..2025-11-30  30 d  dry run   226.20 GB  $  1.29" in said
    assert "TOTAL dry runs 805.3 GB" in said and "the cap: fits" in said and "$6.25 per TiB" in said


def test_the_ranges_come_from_config_and_the_full_estimate_is_under_the_cap():
    one = config.BIGQUERY_ONESHOT
    assert one["near"]["from"] == config.NEAR_P2P_HISTORY["floor"] == "2024-12-10"
    assert (one["ethereum"]["from"], one["ethereum"]["to"]) == ("2024-11-04", "2026-05-31")
    held = [d for d in nh._days(D("2025-12-01"), D("2026-11-30"))]
    near = bb.near_chunks(held, one["near"]["from"], config.NEAR_P2P_HISTORY["min_chunk_days"])
    assert near[0] == (D("2025-11-01"), D("2025-11-30")) and near[-1] == (D("2024-12-10"), D("2024-12-31"))
    assert len(near) == 12
    near_days = sum((b - a).days + 1 for a, b in near)
    eth = bb.eth_chunks(nh._days(D("2026-05-28"), D("2026-11-22")), one["ethereum"]["from"], one["ethereum"]["to"])
    assert eth[0] == (D("2024-11-04"), D("2024-11-30")) and eth[-1] == (D("2026-05-01"), D("2026-05-27"))
    eth_days = sum((b - a).days + 1 for a, b in eth)
    assert (near_days, eth_days) == (356, 570)
    gb = near_days * one["near"]["est_gb_per_day"] + eth_days * one["ethereum"]["est_gb_per_day"]
    assert round(gb, 1) == 3456.0 and gb < one["total_cap_gb"]        # ~2,684 NEAR + ~772 Ethereum
    # dollars at $6.25 a TiB, before the free TiB: the estimate $19.65, the 4,000 GB cap $22.74
    assert round(bb.cost_usd(gb * GB, one["price_usd_per_tib"]), 2) == 19.65
    assert round(bb.cost_usd(one["total_cap_gb"] * GB, one["price_usd_per_tib"]), 2) == 22.74


def test_the_plan_needs_no_network(tmp_path, monkeypatch, capsys):
    cl, rd, cache, db = _setup(tmp_path, monkeypatch)
    assert bb.cmd_plan(db, rd=rd) == 0
    said = capsys.readouterr().out
    assert cl.dry == cl.real == [] and "~226.2 GB" in said and "6 chunk(s)" in said


def test_nothing_scheduled_or_daily_names_the_one_shot():
    for f in ("windows/daily_run.ps1", "windows/register_task.ps1", "token_metrics.py", "check_offline_items.py"):
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        assert "bigquery_backfill" not in text, f
