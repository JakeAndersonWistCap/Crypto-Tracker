"""P5-0 c (Jake 2026-11-03): a phase's own checks are chosen from its diff. Changed test files always run; the P4
own-check rule (test_adapters + test_alignment when a metric, a column or the concept registry can change) is kept
as a superset; any test file naming a changed function, class, name or metric runs. The full gate is unchanged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import own_checks as oc  # noqa: E402

DIFF = """diff --git a/build_workbook.py b/build_workbook.py
--- a/build_workbook.py
+++ b/build_workbook.py
@@ -3010,2 +3010,2 @@ def _hl_model_ratio(rows, stake):
-    n += 1
+    n += span_days(src)
@@ -20,0 +21,3 @@ import pandas as pd
+def span_days(source):
+    return 1
+SPAN_RE = None
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -5,0 +6 @@ METRICS = {
+    "staking_reward_modelled_tokens": {"unit": "tokens"},
diff --git a/tests/test_x.py b/tests/test_x.py
--- a/tests/test_x.py
+++ b/tests/test_x.py
@@ -1 +1 @@
-def test_old_helper(): pass
+def test_new(): pass
"""

TESTS = {
    "tests/test_adapters.py": "import build_workbook\n",
    "tests/test_alignment.py": "import spec\n",
    "tests/test_x.py": "def test_new(): pass\n",
    "tests/test_hl.py": "from build_workbook import _hl_model_ratio\n",
    "tests/test_spans.py": "import build_workbook as bw\nassert bw.span_days('x [span=3d]') == 3\n",
    "tests/test_metric.py": "import config\nm = 'staking_reward_modelled_tokens'\n",
    "tests/test_other.py": "import reader\nreader.plain('x')\n",
    "tests/test_unrelated.py": "def span_days(): pass  # same word, but build_workbook is not imported\n",
}


def test_names_come_from_the_hunk_s_function_new_defs_assignments_and_config_metrics():
    n = oc.names_from_diff(DIFF)
    assert n["build_workbook.py"] == {"_hl_model_ratio", "span_days", "SPAN_RE"}
    assert "staking_reward_modelled_tokens" in n["config.py"]
    assert "tests/test_x.py" not in n, "a test file's own names do not select other tests"


def test_the_selection_and_its_reasons():
    sel = oc.select(["build_workbook.py", "config.py", "tests/test_x.py"], oc.names_from_diff(DIFF), TESTS)
    assert set(sel) == {"tests/test_adapters.py", "tests/test_alignment.py", "tests/test_x.py", "tests/test_hl.py",
                        "tests/test_spans.py", "tests/test_metric.py"}
    assert sel["tests/test_x.py"] == ["changed in this phase"]
    assert "own-check rule" in sel["tests/test_adapters.py"][0]
    assert "_hl_model_ratio" in sel["tests/test_hl.py"][0]
    assert "tests/test_other.py" not in sel, "it names nothing that changed"
    assert "tests/test_unrelated.py" not in sel, "a name counts only in a file that imports its module"


def test_the_module_reference_forms():
    r = oc.module_ref("fetch/hl_ledger.py")
    for text in ("from fetch import hl_ledger", "from fetch import base, hl_ledger as hl", "import fetch.hl_ledger",
                 "from fetch.hl_ledger import Replay"):
        assert r.search(text), text
    assert not r.search("from fetch import hl_candles")
    assert oc.module_ref("build_workbook.py").search("import build_workbook as bw")


def test_the_p4_rule_files_select_adapters_and_alignment_and_others_do_not():
    for f in ("config.py", "build_workbook.py", "reader.py", "reader_look.py", "reader_layout.py", "highlights.py",
              "coverage_audit.py", "spec.py", "SPEC.md"):
        sel = oc.select([f], {}, TESTS)
        assert set(sel) == {"tests/test_adapters.py", "tests/test_alignment.py"}, f
    assert oc.select(["fetch/hl_ledger.py", "RUNBOOK.md"], {}, TESTS) == {}


def test_generic_and_short_names_select_nothing():
    d = "+++ b/store.py\n@@ -1 +1 @@ def main(args):\n+x = 1\n+def run(): pass\n+ROOT = 1\n+def __init__(): pass\n"
    assert oc.names_from_diff(d) == {"store.py": set()}


def test_a_changed_helper_selects_its_importers_and_a_changed_conftest_selects_everything():
    tests = dict(TESTS, **{"tests/test_dump.py": "from shared_build import plain_build\n"})
    sel = oc.select(["tests/shared_build.py"], {}, tests)
    assert set(sel) == {"tests/test_dump.py"} and sel["tests/test_dump.py"] == ["imports tests/shared_build.py"]
    assert set(oc.select(["tests/conftest.py"], {}, tests)) == set(tests)
