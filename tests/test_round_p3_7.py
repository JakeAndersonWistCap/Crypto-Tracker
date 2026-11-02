"""P3-7 (Jake 2026-11-02): the onboarding kit — projects/*.yaml beside config.py, add_project.py --probe / --backfill.

Plain asserts, no network (the probe runs on fake sources). The FIXTURE test builds the committed real dumps twice, in
two fresh processes (config is read at import): once with no project folder, once with a folder holding one ACTIVE
dummy project (synthetic store rows) — the dummy appears on every reader tab it belongs to and on the audit file, its
TODO fields read "not wired" with their reasons, and no other project's cell changes.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import project_intake as I  # noqa: E402

DUMMY = "Testcoin P37"
DUMMY_YAML = textwrap.dedent(f"""\
    status: active
    as_of: 2026-11-02
    name:   {{value: "{DUMMY}", source: "https://example.org/testcoin/docs"}}
    symbol: {{value: TSTP, source: "https://example.org/testcoin/token"}}
    coingecko_id: {{value: testcoin-p37, source: "https://www.coingecko.com/en/coins/testcoin-p37"}}
    defillama:
      fees_slug:            {{value: testcoin-p37, source: "https://defillama.com/protocol/testcoin-p37"}}
      revenue_slug:         {{value: testcoin-p37, source: "https://defillama.com/protocol/testcoin-p37"}}
      holders_revenue_slug: {{value: null, source: "TODO: the holders-revenue line — DefiLlama lists none for testcoin yet"}}
      tvl_slug:             {{value: testcoin-p37, source: "https://defillama.com/protocol/testcoin-p37"}}
    chains:
      - chain:          {{value: ethereum, source: "https://example.org/testcoin/token"}}
        token_contract: {{value: "0x1111111111111111111111111111111111111111", source: "https://etherscan.io/token/0x1111111111111111111111111111111111111111"}}
        decimals:       {{value: 18, source: "https://etherscan.io/token/0x1111111111111111111111111111111111111111"}}
    max_supply: {{value: 1000000000, source: "https://example.org/testcoin/tokenomics"}}
    noncirculating_wallets:
      - key: treasury
        chain: ethereum
        label:   {{value: "Testcoin treasury", source: "https://example.org/testcoin/treasury"}}
        address: {{value: "0x2222222222222222222222222222222222222222", source: "https://example.org/testcoin/treasury"}}
    unlock_schedule: {{value: [], source: "https://example.org/testcoin/tokenomics"}}
    emission:
      mechanism:      {{value: "none", source: "https://example.org/testcoin/tokenomics"}}
      model:          {{value: none, source: "https://example.org/testcoin/tokenomics"}}
      tokens_per_day: {{value: null, source: "TODO: the staking reward rate — not published"}}
      from:           {{value: null, source: "TODO: the date the rate starts"}}
    buyback:
      chain: ethereum
      address:       {{value: null, source: "TODO: no buyback wallet"}}
      destination:   {{value: none, source: "https://example.org/testcoin/governance"}}
      revenue_share: {{value: null, source: "TODO: no buyback programme share"}}
      status:        {{value: null, source: "TODO: n/a"}}
    staking:
      chain: ethereum
      contract:    {{value: null, source: "TODO: the staking contract address — not yet deployed per the team's forum post"}}
      symbol:      {{value: null, source: "TODO: the receipt token"}}
      mechanism:   {{value: null, source: "TODO: the staking design"}}
      read_method: {{value: null, source: "TODO: the read"}}
    insider_wallets: {{value: [], source: "https://example.org/testcoin/disclosure"}}
    archetypes:
      primary:   {{value: 3, reason: "fees fund holders", source: "https://example.org/testcoin/governance"}}
      secondary:
        - {{value: 4, reason: "a fee burn", source: "https://example.org/testcoin/governance"}}
    unit_of_demand: {{value: "swaps", metric: null, source: "TODO: the unit of demand series"}}
    """)
STAKING_TODO = "the staking contract address — not yet deployed per the team's forum post"
HOLDERS_TODO = "the holders-revenue line — DefiLlama lists none for testcoin yet"


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def _ns():
    """config's helpers for to_project, without importing config (the shapes are checked in the fixture build)."""
    return {"_contract": lambda addr, chain, kind, sym, url, **kw: {"address": addr, "chain": chain, "kind": kind,
                                                                    "expected_symbol": sym, "source_url": url, **kw},
            "_dune": lambda *m: {}, "_NO_SPLIT": {"share_to_buyback": None}}


# ============================================================================================== the files
def test_the_template_parses_and_every_field_has_a_source_or_a_todo():
    doc = I.read(ROOT / "projects" / "_intake_template.yaml")
    assert I.source_problems(doc) == []
    paths = {p for p, _ in I.fields(doc)}
    for want in ("name", "symbol", "coingecko_id", "defillama.fees_slug", "defillama.revenue_slug",
                 "defillama.holders_revenue_slug", "defillama.tvl_slug", "chains[0].token_contract",
                 "chains[0].decimals", "max_supply", "noncirculating_wallets[0].address",
                 "noncirculating_wallets[0].label", "unlock_schedule", "emission.mechanism", "buyback.address",
                 "buyback.destination", "buyback.revenue_share", "staking.contract", "insider_wallets[0].address",
                 "archetypes.primary", "unit_of_demand"):
        assert want in paths, want
    assert all(I.is_todo(f) or f["source"].startswith("http") for _, f in I.fields(doc))
    assert doc["status"] == "draft"
    assert I.yaml_files(ROOT / "projects") == [ROOT / "projects" / "aave.yaml"]      # '_' files are never loaded


def test_aave_parses_every_field_has_a_source_or_a_todo_and_it_is_a_draft():
    doc = I.read(ROOT / "projects" / "aave.yaml")
    assert I.source_problems(doc) == [] and I.validate(doc, "aave.yaml") == []
    assert doc["status"] == "draft"
    assert I.value(doc, "chains[0].token_contract") == "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9"
    assert I.value(doc, "chains[0].decimals") == 18 and I.value(doc, "max_supply") == 16_000_000
    assert I.value(doc, "staking.contract") == "0x4da27a545c0c5B758a6BA100e3a049001de870f5"
    assert {w["key"] for w in doc["noncirculating_wallets"]} == {"ecosystem_reserve", "collector"}
    for _, f in I.fields(doc):
        assert I.is_todo(f) or re.match(r"https://\S+$", f["source"]), f
    assert {p for p, _ in I.todos(doc)} == {"unlock_schedule", "emission.from", "buyback.revenue_share",
                                            "staking.read_method", "insider_wallets"}


def test_a_bare_todo_or_a_missing_source_is_refused():
    doc = I.read(ROOT / "projects" / "aave.yaml")
    doc["max_supply"] = {"value": 16_000_000}
    doc["symbol"] = {"value": "AAVE", "source": "TODO:"}
    probs = I.source_problems(doc)
    assert any(p.startswith("max_supply: no source") for p in probs)
    assert any(p.startswith("symbol: 'TODO:' without") for p in probs)


# ============================================================================================== loading rules
def test_a_draft_is_not_loaded_and_an_active_one_is(tmp_path):
    _write(tmp_path, "aave.yaml", (ROOT / "projects" / "aave.yaml").read_text(encoding="utf-8"))
    _write(tmp_path, "_ignored.yaml", "not: [valid")                   # '_' files are never even parsed
    assert I.active_projects([{"name": "Bitcoin"}], _ns(), tmp_path) == []
    _write(tmp_path, "testcoin.yaml", DUMMY_YAML)
    got = I.active_projects([{"name": "Bitcoin"}], _ns(), tmp_path)
    assert [p["name"] for p in got] == [DUMMY]
    p = got[0]
    assert p["archetypes"] == [3, 4] and p["coingecko_id"] == "testcoin-p37"
    assert p["defillama_fees_slug"] == "testcoin-p37" and p["defillama_protocol"] == "testcoin-p37"
    assert p["contracts"]["token"]["kind"] == "erc20_total_supply"
    assert p["contracts"]["noncirc_treasury"]["metric_override"] == "noncirculating_holding_tokens"
    assert "staking" not in p["contracts"]                             # a TODO address never reaches config
    assert p["buyback_destination"] == "n/a" and p["destination_effect"] == "none"


def test_a_name_config_already_declares_is_a_clear_error(tmp_path):
    text = (ROOT / "projects" / "aave.yaml").read_text(encoding="utf-8").replace("status: draft", "status: active")
    _write(tmp_path, "aave.yaml", text)
    with pytest.raises(I.IntakeError) as e:
        I.active_projects([{"name": "Aave"}], _ns(), tmp_path)
    msg = str(e.value)
    assert "aave.yaml" in msg and "'Aave' is already declared in config.py" in msg and "status: draft" in msg
    # two YAML files with one name: the second is refused, naming the first
    _write(tmp_path, "aave.yaml", DUMMY_YAML)
    _write(tmp_path, "testcoin2.yaml", DUMMY_YAML)
    with pytest.raises(I.IntakeError, match=r"testcoin2\.yaml: the project 'Testcoin P37' is already declared in aave\.yaml"):
        I.active_projects([{"name": "Aave"}], _ns(), tmp_path)


def test_an_invalid_active_file_stops_the_load_naming_the_field(tmp_path):
    _write(tmp_path, "testcoin.yaml", DUMMY_YAML.replace('max_supply: {value: 1000000000, source: "https://example.org'
                                                         '/testcoin/tokenomics"}', "max_supply: {value: 1000000000}"))
    with pytest.raises(I.IntakeError, match="max_supply: no source"):
        I.active_projects([], _ns(), tmp_path)


def test_a_todo_becomes_a_not_wired_code_with_its_reason_through_the_gap_path():
    import reader
    from fetch import gaps
    doc = __import__("yaml").safe_load(DUMMY_YAML)
    p = I.to_project(doc, "testcoin", _ns())
    rec = p["locked_tokens_blocked"]
    assert STAKING_TODO in rec["wanted"] and rec["status"] == "not wired"
    reason, suggestion = gaps._format_blocked(rec)
    assert reason.startswith("not wired — WANTED: TODO: " + STAKING_TODO)
    assert "add_project.py testcoin --probe" in suggestion
    # the build's n/a text for a formula reading this row, and the reader's code for it
    text = f"n/a — locked_tokens (q0): gap — {reason}"
    assert reader.code_of(text) == "not wired"
    assert STAKING_TODO in p["holders_revenue_usd_blocked"]["wanted"] or HOLDERS_TODO in \
        p["holders_revenue_usd_blocked"]["wanted"]


# ============================================================================================== no YAML: config unchanged
_DIGEST = ("import json, config; print(json.dumps({'projects': config.PROJECTS, 'metrics': config.METRICS}, "
           "sort_keys=True, default=repr))")


def test_with_no_active_yaml_config_is_identical_to_config_py_alone(tmp_path):
    """The hook with the committed folder (template + the draft) and with an empty folder gives the same PROJECTS and
    METRICS, value for value — so every build output is today's (the build reads nothing else from the folder)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    outs = []
    for folder in (None, empty):
        env = {**os.environ}
        env.pop("TOKEN_METRICS_PROJECTS_DIR", None)
        if folder is not None:
            env["TOKEN_METRICS_PROJECTS_DIR"] = str(folder)
        r = subprocess.run([sys.executable, "-c", _DIGEST], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        outs.append(r.stdout.strip().splitlines()[-1])
    assert outs[0] == outs[1]
    assert len(json.loads(outs[0])["projects"]) == 30 and DUMMY not in outs[0]


# ============================================================================================== the fixture builds
_CHILD = r'''
import sys, sqlite3
from pathlib import Path
ROOT = Path(sys.argv[1]); out = Path(sys.argv[2]); dummy = sys.argv[3]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import os
os.environ["TOKEN_METRICS_STORE_VALUES"] = "0"
sys.modules["recalc"] = None
import pandas as pd
import config, build_workbook as bw, measuring_guard as mg, store as sm
from real_dump import to_store
getattr(config, "RESTATEMENT_ACTIVE", {}).clear()
db = out / "metrics.db"
asofs = []
for pl in mg._payloads():
    to_store(pl, db)
    asofs.append(pl["meta"]["asof"])
asof = pd.Timestamp(max(asofs))
sm.DB_PATH = str(db)
if dummy in config.PROJECT_BY_NAME:
    days = pd.date_range(asof - pd.Timedelta(days=454), asof, freq="D")
    series = {"price_usd": ("coingecko", 1, 2.0), "market_cap_usd": ("coingecko", 1, 1.2e9),
              "volume_usd": ("coingecko", 1, 3e7), "circulating_supply": ("coingecko:mcap/price", 2, 6e8),
              "fees_usd": ("defillama", 1, 5e5), "revenue_usd": ("defillama", 1, 1e5),
              "protocol_tvl_usd": ("defillama", 1, 8e8), "total_supply": ("chain:ethereum:token", 2, 1e9),
              "noncirculating_holding_tokens": ("chain:ethereum:noncirc_treasury", 2, 3e8)}
    con = sqlite3.connect(db)
    con.executemany("INSERT OR REPLACE INTO metrics (date, project, metric, value, source, tier, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(str(d.date()), dummy, m, v * (1 + 0.001 * i), s, t, f"{d.date()}T12:00:00+00:00")
                     for m, (s, t, v) in series.items() for i, d in enumerate(days)])
    con.commit()
    st = sm.Store(str(db))
    from fetch import gaps, registry_reasons
    rows = gaps.detect([config.PROJECT_BY_NAME[dummy]], st.load_long(), set(), registry_reasons(), [])
    rows = [r for r in rows if r["project"] == dummy]     # detect also re-emits every project's open questions
    run, ts = con.execute("SELECT run_id, ts FROM gap_report ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
    st.close()
    # the dummy's rows join the latest run's report (as a run would write them), so every other row stays the report
    con.executemany("INSERT INTO gap_report(run_id, ts, project, metric, tiers_attempted, reason, suggestion, priority,"
                    " priority_label) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(run, ts, r["project"], r["metric"], r.get("tiers_attempted", ""), r["reason"],
                      r.get("suggestion", ""), r.get("priority", 5), r.get("priority_label", "P5 uncovered"))
                     for r in rows])
    con.commit()
    con.close()
st = sm.Store(str(db))
try:
    bw.build_workbook(st, out / "audit.xlsx", asof=asof, reader_path=out / "reader.xlsx")
finally:
    st.close()
assert "error" not in bw.READER_STATS, bw.READER_STATS
print("BUILT", len(config.PROJECTS))
'''


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    base = tmp_path_factory.mktemp("p37")
    folder = base / "projects"
    _write(folder, "testcoin.yaml", DUMMY_YAML)
    empty = base / "none"
    empty.mkdir()
    procs = {}
    for key, pdir in (("plain", empty), ("dummy", folder)):
        d = base / key
        d.mkdir()
        env = {**os.environ, "TOKEN_METRICS_PROJECTS_DIR": str(pdir)}
        procs[key] = (d, subprocess.Popen([sys.executable, "-c", _CHILD, str(ROOT), str(d), DUMMY], cwd=ROOT, env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    out = {}
    for key, (d, p) in procs.items():
        log, _ = p.communicate(timeout=3000)
        assert p.returncode == 0, log[-3000:]
        out[key] = d
    return out


def _rows(path: Path, data_only: bool) -> dict:
    """(sheet, project label, header, occurrence) -> cell value, for every sheet with a 'Project' header row; row and
    sheet-relative row numbers inside formulas are normalised (a row inserted above shifts them, not the figure)."""
    wb = openpyxl.load_workbook(path, data_only=data_only, read_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hdr = next((i for i, r in enumerate(rows[:12]) if r and r[0] in ("Project", "Key")), None)
        if hdr is None:
            continue
        heads, seen = [], {}
        for h in rows[hdr]:
            seen[h] = seen.get(h, 0) + 1
            heads.append((h, seen[h]))
        count: dict = {}
        for r in rows[hdr + 1:]:
            if not r or r[0] in (None, ""):
                continue
            lab = str(r[0])
            count[lab] = count.get(lab, 0) + 1
            for (h, n), v in zip(heads, r):
                if h is None:
                    continue
                if isinstance(v, str) and v.startswith("="):
                    v = re.sub(r"(\$?[A-Z]{1,3}\$?)\d+", r"\1#", v)
                out[(ws.title, lab, count[lab], h, n)] = v
    wb.close()
    return out


def test_the_active_dummy_is_on_every_tab_it_belongs_to_and_its_todos_read_not_wired(builds):
    wb = openpyxl.load_workbook(builds["dummy"] / "reader.xlsx")
    tabs = {}
    for name in ("Summary", "A3 Revenue Buyback", "A4 Permanent Burn", "Comparison", "Business"):
        ws = wb[name]
        tabs[name] = [r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == DUMMY]
        assert tabs[name], f"{DUMMY} is not on {name}"
    for name in ("A1 Infrastructure", "A2 Coordination"):
        ws = wb[name]
        assert not any(ws.cell(r, 1).value == DUMMY for r in range(1, ws.max_row + 1)), name
    # a TODO reads as the code "not wired" with its reason somewhere on the reader (the staking TODO feeds locked_tokens)
    hits = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            if row[0].value != DUMMY:
                continue
            for c in row:
                if c.value == "not wired" and c.comment and "TODO:" in c.comment.text:
                    hits.append((ws.title, c.coordinate, c.comment.text))
    assert hits, "no 'not wired' cell with a TODO reason for the dummy"
    assert any(STAKING_TODO in t for _, _, t in hits) or any(HOLDERS_TODO in t for _, _, t in hits), hits[:3]
    # the audit file: the dummy's rows on Master and Data, the TODO on the Data row's note
    aw = openpyxl.load_workbook(builds["dummy"] / "audit.xlsx", read_only=True)
    master = [r[0] for r in aw["Master"].iter_rows(values_only=True)]
    assert DUMMY in master
    data = [r for r in aw["Data"].iter_rows(values_only=True) if r and str(r[0]).startswith(DUMMY + "|")]
    assert data and any("TODO: " + STAKING_TODO in " ".join(map(str, r)) for r in data)
    aw.close()


def _project_rows(rows: dict) -> dict:
    """Only rows labelled with a project (or a Data key 'Project|metric'): aggregate rows (coverage counts such as
    'GREEN 226 of 454', medians) count the projects there are, so they move with any added project by design."""
    import config
    names = {p["name"] for p in config.PROJECTS} | {DUMMY}
    return {k: v for k, v in rows.items() if k[1] in names or k[1].split("|", 1)[0] in names}


def test_no_other_projects_value_changes(builds):
    """Every project row other than the dummy's, on every reader tab (values) and every audit tab (formulas with row
    numbers normalised, literals), reads the same with and without the YAML project."""
    for fname, data_only in (("reader.xlsx", True), ("audit.xlsx", False)):
        a = _project_rows(_rows(builds["plain"] / fname, data_only))
        b = _project_rows(_rows(builds["dummy"] / fname, data_only))
        b_other = {k: v for k, v in b.items() if DUMMY not in k[1]}
        assert any(DUMMY in k[1] for k in b), fname
        missing = [k for k in a if k not in b_other]
        assert not missing, (fname, missing[:10])
        diff = [(k, a[k], b_other[k]) for k in a if a[k] != b_other[k]]
        assert not diff, (fname, len(diff), diff[:10])
        assert len(a) > 1000


# ============================================================================================== add_project.py
def _fake_net():
    import add_project as ap

    def get(url, params=None):
        if "coingecko" in url:
            return {"market_data": {"current_price": {"usd": 2.0}, "circulating_supply": 6e8}}
        if "/summary/fees/" in url:
            return {"totalDataChart": [[1, 10.0], [2, 12.0]]}
        if "/protocol/" in url:
            return {"tvl": [{"date": 1, "totalLiquidityUSD": 8e8}]}
        raise AssertionError(url)

    def post(url, body):
        data = body["params"][0]["data"]
        if data == ap.SEL["decimals"]:
            return hex(18)
        if data == ap.SEL["totalSupply"]:
            return hex(10 ** 27)
        return hex(3 * 10 ** 26)
    return ap.Net(get=get, post=post, pace=0)


def test_probe_opens_nothing_writable_and_prints_a_forecast_on_fake_sources(tmp_path, monkeypatch, capsys):
    import builtins
    import add_project as ap
    import store
    folder = tmp_path / "projects"
    _write(folder, "testcoin.yaml", DUMMY_YAML.replace("status: active", "status: draft"))
    monkeypatch.setenv("TOKEN_METRICS_PROJECTS_DIR", str(folder))
    # a tiny audit file and column map, shaped as the build writes them
    audit = tmp_path / "audit.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master"
    ws.append(["Project", "Fees Q0 ($)", "Staked"])
    ws.append(["X", '=IFERROR(INDEX(Data!$E$2:$E$9,MATCH($A2&"|fees_usd",Data!$A$2:$A$9,0)),"n/a")',
               '=IFERROR(INDEX(Data!$E$2:$E$9,MATCH($A2&"|locked_tokens",Data!$A$2:$A$9,0)),"n/a")'])
    wb.save(audit)
    cmap = tmp_path / "reader_column_map.csv"
    cmap.write_text("audit_file,audit_tab,audit_column,audit_header,reader_file,reader_tab,reader_column,reader_header,"
                    "note\na,Master,B,Fees Q0 ($),r,Business,D,\"Fees, last 90 days\",moved\n"
                    "a,Master,C,Staked,r,A3 Revenue Buyback,E,Staked,moved\n", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.rglob("*"))

    def no(*a, **k):
        raise AssertionError("the probe opened the store")
    real_open = builtins.open

    def ro_open(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            raise AssertionError(f"the probe opened {file} for writing")
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", no)
    monkeypatch.setattr(store.Store, "__init__", no)
    monkeypatch.setattr(builtins, "open", ro_open)
    fake = _fake_net()
    monkeypatch.setattr(ap, "Net", lambda: fake)
    assert ap.main(["testcoin", "--probe", "--audit", str(audit), "--column-map", str(cmap)]) == 0
    monkeypatch.setattr(builtins, "open", real_open)
    out = capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert "OK   coingecko" in out and "OK   contract  chains[0].decimals" in out and "OK   wallet" in out
    assert "COVERAGE FORECAST (per reader column" in out
    assert re.search(r"Business \| Fees, last 90 days\s+number\s+fees_usd ✓", out), out
    assert re.search(r"A3 Revenue Buyback \| Staked\s+not wired\s+locked_tokens: staking\.\w+: TODO: ", out), out


def test_the_forecast_regex_is_the_builds_own():
    a = re.search(r"^_REF = re\.compile\((r'.*?')\)$", (ROOT / "build_workbook.py").read_text(encoding="utf-8"), re.M)
    b = re.search(r"^    ref = re\.compile\((r'.*?')\)$", (ROOT / "add_project.py").read_text(encoding="utf-8"), re.M)
    assert a and b and a.group(1) == b.group(1)


def test_backfill_takes_the_run_lock_and_resumes_where_it_stopped(tmp_path):
    import add_project as ap
    import runlock
    db = tmp_path / "metrics.db"
    calls, held = [], []

    class Lock(runlock.RunLock):
        def __enter__(self):
            held.append(self.what)
            return super().__enter__()

    clock = {"t": 0.0}
    steps = [("fetch", lambda left: calls.append("fetch") or clock.__setitem__("t", clock["t"] + 100) or True),
             ("archive", lambda left: calls.append(f"archive {left:.0f}") or False)]
    rc = ap.backfill(DUMMY, "testcoin", [3], 60, steps=steps, db_path=db, state_dir=tmp_path, lock=Lock,
                     clock=lambda: clock["t"], say=lambda *_: None)
    assert rc == 3 and calls == ["fetch"] and held == [f"add_project --backfill {DUMMY}"]
    assert json.loads((tmp_path / "testcoin.json").read_text())["done"] == ["fetch"]
    # resumable: the finished step is skipped, the rest runs inside a fresh budget
    clock["t"] = 0.0
    steps[1] = ("archive", lambda left: calls.append(f"archive {left:.0f}") or True)
    assert ap.backfill(DUMMY, "testcoin", [3], 60, steps=steps, db_path=db, state_dir=tmp_path, lock=Lock,
                       clock=lambda: clock["t"], say=lambda *_: None) == 0
    assert calls == ["fetch", "archive 60"]
    assert json.loads((tmp_path / "testcoin.json").read_text())["done"] == ["fetch", "archive"]
    # the SAME lock token_metrics takes: while another run holds metrics.db, the backfill does not start
    with runlock.RunLock(db, "token_metrics run", wait_s=0):
        with pytest.raises(runlock.RunLocked):
            ap.backfill(DUMMY, "x2", [3], 60, steps=steps, db_path=db, state_dir=tmp_path,
                        lock=lambda p, w, say: runlock.RunLock(p, w, wait_s=0, say=say), say=lambda *_: None)
    assert not (tmp_path / "x2.json").exists()


def test_backfill_is_refused_for_a_draft(tmp_path, monkeypatch, capsys):
    import add_project as ap
    monkeypatch.setenv("TOKEN_METRICS_PROJECTS_DIR", str(ROOT / "projects"))
    assert ap.main(["aave", "--backfill"]) == 2
    out = capsys.readouterr().out
    assert "REFUSED: --backfill needs an ACTIVE YAML project" in out and "already declared in config.py" in out
