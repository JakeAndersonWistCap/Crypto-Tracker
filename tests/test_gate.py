"""P5-0 d2 (Jake 2026-11-03): the gate runner records the machine it ran on, so a gate timed while other test runs
shared its CPUs is marked CONTENDED instead of being read as the tests' own time. Memory: the gate does not start
below 10 GB free and never runs more than 3 workers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate  # noqa: E402


def test_free_memory_is_read_from_meminfo():
    assert gate.mem_available_gb("MemTotal: 16000000 kB\nMemAvailable:   10485760 kB\n") == 10.0
    assert gate.mem_available_gb("MemTotal: 1 kB\n") is None


def test_the_gate_s_own_tree_is_not_foreign_and_only_python_test_processes_are():
    table = {1: (0, "init", 0), 10: (1, "python3 gate.py --out x", 50),
             11: (10, "python3 -m pytest -q tests", 900), 12: (11, "python3 -c xdist worker pytest", 800),
             20: (1, "python3 -m pytest tests/test_adapters.py", 700),          # another run: foreign
             21: (1, "/usr/bin/python3 measuring_guard.py --newer m.db", 600),  # another guard: foreign
             22: (1, "grep pytest", 1),                                       # a shell looking for it: not a test
             23: (1, "python3 token_metrics.py", 400),                       # not a test process
             24: (20, "/usr/bin/python3 -u -c import sys;exec(eval(sys.stdin.readline()))", 900)}  # its xdist worker
    own = gate.descendants(table, 10)
    assert own == {10, 11, 12}
    assert [p for p, _c in gate.foreign_test_processes(table, own)] == [20, 21, 24]


def _s(t, load, foreign=(), rss=None):
    return {"t": t, "load1": load, "mem_gb": 12.0, "foreign": list(foreign), "own_rss_kb": rss or {}, "own_cmd": {}}


def test_a_step_is_contended_when_another_test_process_ran_or_the_load_exceeds_its_own_processes():
    clean = [_s(1, 2.9), _s(2, 3.4), _s(3, 3.2)]
    r = gate.summarize(clean, 0, 10, procs=3)
    assert not r["contended"] and r["load_min"] == 2.9 and r["load_max"] == 3.4
    assert gate.summarize(clean + [_s(4, 3.0, foreign=[(99, "python3 -m pytest")])], 0, 10, 3)["contended"]
    assert gate.summarize([_s(1, 5.0), _s(2, 5.2)], 0, 10, procs=3)["contended"], "mean 5.1 > 3 + 1.5"
    assert not gate.summarize([_s(1, 4.4)], 0, 10, procs=3)["contended"], "4.4 is within the margin"
    # samples outside the step's window do not count
    assert not gate.summarize([_s(1, 3.0), _s(50, 9.0, foreign=[(1, "x")])], 0, 10, procs=3)["contended"]


def test_peak_memory_is_kept_per_process():
    r = gate.summarize([_s(1, 1, rss={5: 1024 * 900}), _s(2, 1, rss={5: 1024 * 1500, 6: 1024 * 200})], 0, 9, 1)
    assert list(r["peak_rss_mb"].values()) == [1500, 200]


def test_ruff_guard_and_durations_are_read_from_their_outputs():
    assert gate.ruff_total("60\tF401\t[*] unused-import\n21\tF841\tunused-variable\n") == 81
    assert gate.ruff_total("") == 0
    assert gate.guard_violations("MEASURING-POINT GUARD — 0 violation(s)\n") == 0
    assert gate.guard_violations("MEASURING-POINT GUARD — 3 violation(s)\n") == 3
    assert gate.guard_violations("crashed") is None
    out = ("============ slowest durations ============\n"
           "487.52s call     tests/test_adapters.py::test_a\n"
           "12.00s setup    tests/test_b.py::test_c\n"
           "1.00s call     tests/test_b.py::test_d\n")
    d = gate.durations(out)
    assert d[0] == (487.52, "call", "tests/test_adapters.py::test_a")
    assert gate.per_file(d) == [(487.5, "tests/test_adapters.py"), (13.0, "tests/test_b.py")]


def test_the_tests_step_runs_every_test_file_and_keeps_a_file_on_one_worker():
    assert gate.tests_command(1, "py")[-1] == "tests" and "-n" not in gate.tests_command(1, "py")
    c = gate.tests_command(3, "py", junit="j.xml")
    assert c[c.index("-n") + 1] == "3" and c[c.index("--dist") + 1] == "loadgroup" and c[-1] == "tests"
    assert "--durations=0" in c and "--junitxml=j.xml" in c


def test_every_test_file_is_one_xdist_group_and_the_shared_build_files_are_one():
    """loadgroup keeps a group on one worker: conftest groups by file, and the eight shared-build files together."""
    import conftest

    class _Item:
        def __init__(self, f):
            self.fspath, self.marks = f, []

        def add_marker(self, m):
            self.marks.append(m)
    items = [_Item(f"/x/tests/{n}") for n in ("test_round_p1.py", "test_round_p4_q5.py", "test_adapters.py",
                                              "test_adapters.py")]
    conftest.pytest_collection_modifyitems(None, None, items)
    groups = [i.marks[0].kwargs["name"] for i in items]
    assert groups == [conftest.SHARED_PLAIN_BUILD_GROUP, conftest.SHARED_PLAIN_BUILD_GROUP, "test_adapters.py",
                      "test_adapters.py"]


def test_outcomes_are_read_per_test_id_from_junit(tmp_path):
    (tmp_path / "j.xml").write_text(
        '<testsuites><testsuite><testcase classname="tests.test_a" name="test_ok@test_a.py"/>'
        '<testcase classname="tests.test_a" name="test_bad[x]@shared_plain_build"><failure message="m"/></testcase>'
        '<testcase classname="tests.test_b" name="test_err"><error message="e"/></testcase>'
        '</testsuite></testsuites>')
    assert gate.junit_outcomes(tmp_path / "j.xml") == {"tests/test_a.py::test_ok": "passed",
                                                       "tests/test_a.py::test_bad[x]": "failed",
                                                       "tests/test_b.py::test_err": "error"}


def test_memory_and_workers_are_measured_not_limited():
    """P5-1.1a (Jake 2026-11-04): no fixed caps - the gate defaults to the machine's CPU count and never refuses on free
    memory; load and memory stay in every step's record."""
    import os
    import inspect
    src = inspect.getsource(gate.main)
    assert "MIN_FREE_GB" not in src and "MAX_WORKERS" not in src and "below the" not in src
    assert gate.main.__module__ == "gate"
    ap_default = [ln for ln in inspect.getsource(gate.main).splitlines() if '"--workers"' in ln][0]
    assert "os.cpu_count()" in ap_default
    assert os.cpu_count() and os.cpu_count() >= 1
    r = gate.summarize([{"t": 1, "load1": 1.0, "mem_gb": 2.0, "foreign": [], "own_rss_kb": {}, "own_cmd": {}}], 0, 9, 4)
    assert r["mem_min_gb"] == 2.0, "low memory is recorded, not refused"


def test_the_runbook_section_for_the_gate_is_at_the_top_of_the_11d_run():
    import re
    text = (Path(__file__).resolve().parent.parent / "RUNBOOK.md").read_text(encoding="utf-8")
    heads = re.findall(r"^## (11d\w*)\.", text, re.M)
    assert heads[0] == "11dt" and heads[1] == "11ds"
    sec = text[text.index("## 11dt."):text.index("## 11ds.")]
    for m in ("gate.py", "own_checks.py", "TOKEN_METRICS_SHARED_BUILD_STALE", "No fixed cap", "NETWORK BLOCKED IN TESTS",
              "never", "skipped"):
        assert m in sec, m
