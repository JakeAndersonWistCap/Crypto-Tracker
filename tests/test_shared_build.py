"""P5-0 a (Jake 2026-11-03): the shared build of the 15 real dumps. The cache key is the code version, every caller
gets its own copy, a changed master copy fails the run, and only the declared files use it. No real build here: the
build itself is replaced by a stand-in, so these tests take seconds."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared_build as sb  # noqa: E402

TESTS = Path(__file__).resolve().parent


def _tree(root: Path) -> Path:
    for rel, text in {"config.py": "A = 1\n", "build_workbook.py": "x = 2\n", "SPEC.md": "| float | ... |\n",
                      "fixtures/real_pendle.json": '{"v": 1}\n', "tests/test_x.py": "def test(): pass\n",
                      "sources.yaml": "a: 1\n", "manual_overrides.csv": "a,b\n"}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.mark.parametrize("rel", ["config.py", "build_workbook.py", "SPEC.md", "fixtures/real_pendle.json",
                                 "tests/test_x.py", "sources.yaml", "manual_overrides.csv"])
def test_any_change_to_any_source_or_data_file_is_a_new_key(tmp_path, rel):
    root = _tree(tmp_path)
    k0 = sb.code_key(root, env={})
    assert sb.code_key(root, env={}) == k0, "same files, same key"
    p = root / rel
    p.write_text(p.read_text() + " ")
    assert sb.code_key(root, env={}) != k0, f"a one-byte change to {rel} must change the key"


def test_a_new_file_a_renamed_file_and_the_environment_change_the_key(tmp_path):
    root = _tree(tmp_path)
    k0 = sb.code_key(root, env={})
    (root / "fetch").mkdir()
    (root / "fetch" / "new.py").write_text("")
    k1 = sb.code_key(root, env={})
    assert k1 != k0
    (root / "fetch" / "new.py").rename(root / "fetch" / "other.py")
    assert sb.code_key(root, env={}) != k1, "the path is part of the key"
    assert sb.code_key(root, env={"TOKEN_METRICS_STORE_VALUES": "1"}) != sb.code_key(root, env={})
    # the stale-state switch is a test mode, not a build input; unrelated variables are not inputs either
    assert sb.code_key(root, env={sb.STALE_ENV: "1", "HOME": "/x"}) == sb.code_key(root, env={})


def test_caches_scratch_and_run_outputs_are_not_inputs(tmp_path):
    root = _tree(tmp_path)
    k0 = sb.code_key(root, env={})
    for rel in ("__pycache__/a.py", ".cache/x.json", "tests/_scratch/y.json", ".git/z.txt", "logs/run.txt",
                "coverage_audit.csv", "wiring.json", "metrics.db", "token_metrics.xlsx"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("noise")
    assert sb.code_key(root, env={}) == k0


@pytest.fixture
def fake_build(monkeypatch):
    calls = []

    def build(d):
        calls.append(d)
        for n in ("audit.xlsx", "reader.xlsx", "metrics.db", "coverage_audit.csv"):
            (d / n).write_text(f"{n} {len(calls)}")
        return {"asof": "2026-11-01", "stats": {"failed": 0}, "bad_prints": {}, "groups": {"k": [1, 2]},
                "headlines": [{"h": 1}], "locations": {}}
    monkeypatch.setattr(sb, "_build", build)
    monkeypatch.setattr(sb, "_CACHE", {})
    monkeypatch.setattr(sb, "code_key", lambda *a, **k: "key-1")
    return calls


def test_one_build_per_key_and_every_caller_gets_its_own_copy_and_record(tmp_path_factory, fake_build, monkeypatch):
    a = sb.plain_build(tmp_path_factory, "first")
    b = sb.plain_build(tmp_path_factory, "second")
    assert len(fake_build) == 1, "the second caller reuses the build"
    assert a["built_fresh"] and not b["built_fresh"]
    assert a["dir"] != b["dir"] and a["audit"].read_text() == b["audit"].read_text() == "audit.xlsx 1"
    a["audit"].write_text("changed by a test")                      # a caller's copy is its own
    a["groups"]["k"].append(3)                                       # and so is its record
    c = sb.plain_build(tmp_path_factory, "third")
    assert c["audit"].read_text() == "audit.xlsx 1" and c["groups"]["k"] == [1, 2]
    assert sb.verify_unchanged() == []
    monkeypatch.setattr(sb, "code_key", lambda *a, **k: "key-2")    # the code changed: a fresh build
    d = sb.plain_build(tmp_path_factory, "fourth")
    assert len(fake_build) == 2 and d["built_fresh"] and d["audit"].read_text() == "audit.xlsx 2"


def test_a_test_that_writes_into_the_master_copy_fails_the_run(tmp_path_factory, fake_build):
    sb.plain_build(tmp_path_factory, "x")
    (master,) = [c["master"] for c in sb._CACHE.values()]
    (master / "reader.xlsx").write_text("tampered")
    bad = sb.verify_unchanged()
    assert len(bad) == 1 and "reader.xlsx" in bad[0]


def test_the_stale_mode_runs_a_different_build_after_every_call(tmp_path_factory, fake_build, monkeypatch):
    stale = []
    monkeypatch.setattr(sb, "_stale_build", lambda d: stale.append(d))
    monkeypatch.setenv(sb.STALE_ENV, "1")
    sb.plain_build(tmp_path_factory, "a")
    sb.plain_build(tmp_path_factory, "b")
    assert len(stale) == 2
    monkeypatch.delenv(sb.STALE_ENV)
    sb.plain_build(tmp_path_factory, "c")
    assert len(stale) == 2


def test_the_files_that_share_the_build_are_declared_and_none_still_builds_the_plain_recipe_itself():
    """The registry in conftest (one xdist group, so one build per gate) is exactly the files calling plain_build."""
    import conftest
    users = sorted(p.name for p in TESTS.glob("test_*.py")
                   if p.name != "test_shared_build.py" and "plain_build(" in p.read_text(encoding="utf-8"))
    assert users == sorted(conftest.SHARED_PLAIN_BUILD_FILES)
    # P5-1C.B1 (merged in project round 1, S10): test_round_p5_1c_flows.py shares the build; PR1: test_pr1_revenue.py and
    # test_pr1_status.py share it too; the Uniswap freeze gate (test_pr1_freeze.py) as well
    assert len(users) == 13
    for n in users:
        src = (TESTS / n).read_text(encoding="utf-8")
        assert "bw.build_workbook(st, d / \"audit.xlsx\"" not in src, f"{n} still builds the plain recipe itself"
