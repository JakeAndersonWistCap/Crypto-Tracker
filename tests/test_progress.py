"""Item 1 of Jake's fine-tuning (2026-10-11): two probes piped through Tee-Object printed nothing for 40+ minutes —
stdout is block-buffered when it is not a terminal. Output must appear while the process is still running, piped."""
from __future__ import annotations

import re
import select
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _lines_while_alive(code: str, want: int, timeout: float = 20.0) -> tuple[list[str], bool]:
    """Run `code` with stdout PIPED; collect lines until `want` arrive or `timeout`. (lines, child still alive)."""
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    got, t0 = [], time.monotonic()
    try:
        while len(got) < want and time.monotonic() - t0 < timeout:
            r, _, _ = select.select([p.stdout], [], [], 0.5)
            if r:
                ln = p.stdout.readline()
                if ln:
                    got.append(ln.rstrip("\n"))
        return got, p.poll() is None
    finally:
        p.kill()
        p.wait()


def test_a_piped_line_appears_before_the_process_ends():
    code = ("import progress, time; progress.line_buffered(); print('first line'); time.sleep(60)")
    got, alive = _lines_while_alive(code, 1)
    assert got == ["first line"] and alive, (got, alive)


def test_the_heartbeat_prints_step_n_of_N_elapsed_and_eta_while_a_call_blocks():
    code = ("import progress, time; progress.line_buffered()\n"
            "with progress.Heartbeat('probe_x', every=0.5):\n"
            "    progress.tick('lockups read', 250, 1000)\n"
            "    time.sleep(60)\n")
    got, alive = _lines_while_alive(code, 2)
    assert alive and len(got) == 2, got
    assert re.match(r"  \[heartbeat\] probe_x — lockups read 250/1,000 \(25%\), elapsed 0m0\ds, ETA 0m\d\ds", got[0]), got


def test_every_entry_point_is_line_buffered_before_it_runs():
    mains = [f for f in [*ROOT.glob("*.py"), *ROOT.glob("fetch/*.py")]
             if re.search(r'^if __name__ == ["\']__main__["\']:', f.read_text(encoding="utf-8"), re.M)]
    assert len(mains) >= 20
    bad = [f.name for f in mains if not re.search(r'^if __name__ == ["\']__main__["\']:\n    import progress.*\n'
                                                     r'    progress\.line_buffered\(\)\n', f.read_text(encoding="utf-8"),
                                                     re.M)]
    assert not bad, bad


def test_check_offline_items_runs_each_check_inside_a_heartbeat(monkeypatch, capsys):
    import check_offline_items as coi
    import progress
    seen = []

    def probe():
        seen.append([h.name for h in progress._STACK])
    probe.__name__ = "probe_x"
    monkeypatch.setattr(coi, "CHECKS", (probe,))
    monkeypatch.setattr(coi, "_bind", lambda fn, args: fn)
    monkeypatch.setattr(sys, "argv", ["check_offline_items.py"])
    coi.main()
    assert seen == [["probe_x"]] and not progress._STACK


def test_run_summary_classes_failed_warn_and_skipped_by_design():
    """Item 7 (Jake, 2026-10-11): 'FAILED browser_capture … LAG GREW … stored' was a failure though data was stored.
    One classifier for every count: WARN for out.warn, SKIPPED for a skip or a robots.txt rule obeyed, FAILED for a
    read that should have worked — including a robots.txt that could not be read at all."""
    from fetch.base import FetchOutput, LogEntry, run_class
    out = FetchOutput()
    out.warn("browser_capture", "Hyperliquid", "tx_count: LAG GREW — 19 days behind; stored, but check the source", 3)
    out.fail("scrape", "Pendle", "locked_tokens_dashboard: robots.txt disallows app.pendle.finance — Disallow: /", 3)
    out.fail("beaconchain", "Ethereum", "x: robots.txt disallows beaconcha.in — robots.txt ITSELF answered HTTP 403", 3)
    out.fail("scrape", "Pendle", "x: robots.txt disallows api-v2.pendle.finance — robots.txt UNREACHABLE (SSLError)", 3)
    out.fail("defillama", "Plume", "plume:dailyFees: HTTP 404 from api.llama.fi", 1)
    out.skipped("aethir_page", "Aethir", "already read today", 3)
    out.unconfigured("dune", "Aethir", "no query")
    assert [run_class(e) for e in out.log] == ["WARN", "SKIPPED", "FAILED", "FAILED", "FAILED", "SKIPPED",
                                               "UNCONFIGURED"]
    assert run_class(LogEntry("x", None, 5, "ok", "")) == "OK"


def test_browser_capture_lag_grew_is_a_warn_not_a_failure():
    src = (ROOT / "fetch" / "browser_capture.py").read_text(encoding="utf-8")
    assert re.search(r'out\.warn\(SOURCE, name, f"\{pin\[\'metric\'\]\}: LAG GREW', src)


def test_daily_summary_leads_with_the_runs_three_counts():
    import daily_summary as ds
    log = ("run summary: 900 rows | 9 FAILED | 1 WARN | 40 SKIPPED-by-design | 3 review items\n"
           "run summary: 950 rows | 2 FAILED | 1 WARN | 41 SKIPPED-by-design | 3 review items\n")
    lines = ds.summarise({}, None, None, log)
    assert lines[2] == "RUN: 2 FAILED | 1 WARN | 41 SKIPPED-by-design", lines[:4]       # the rerun's, the last one
    assert ds.summarise({}, None, None, "")[2] == "RUN: no run summary line in the log"
