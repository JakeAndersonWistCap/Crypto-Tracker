"""PR1 FREEZE (Jake 2026-11-05): a finished project's cells may not move without a declared reason. No network."""
import json

import pytest

import config
import project_freeze as pf


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "pr1_freeze")
    assert "error" not in rec["stats"], rec["stats"]
    return rec


@pytest.mark.parametrize("project", sorted(config.FROZEN_PROJECTS))
def test_gate_a_frozen_project_moves_only_by_declaration(built, project):
    base = json.loads(pf.baseline_path(project).read_text(encoding="utf-8"))
    now = pf.cells(built["audit"], built["reader"], project)
    assert len(base) > 300, f"{project}'s baseline holds only {len(base)} cells"
    bad = pf.undeclared(base, now, config.FROZEN_MOVES.get(project, {}))
    assert not bad, (f"{len(bad)} {project} cell(s) moved without a declared reason (config.FROZEN_MOVES; then "
                     f"python project_freeze.py --write {project}):\n" + "\n".join(bad[:40]))


def test_a_declaration_needs_the_value_it_now_reads_a_reason_and_a_date():
    base, now = {"a": 1.0, "b": "x"}, {"a": 2.0, "b": "x", "c": 3.0}
    assert pf.undeclared(base, now, {}) == ["a: 1.0 -> 2.0", "c: '<absent>' -> 3.0"]
    ok = {"a": {"to": 2.0, "why": "PR2.X: restated", "date": "2026-11-20"}}
    assert pf.undeclared(base, now, ok) == ["c: '<absent>' -> 3.0"]
    assert pf.undeclared(base, now, {"a": {"to": 2.5, "why": "w", "date": "d"}})[0].startswith("a:"), "wrong value"
    assert pf.undeclared(base, now, {"a": {"to": 2.0, "why": "", "date": "d"}})[0].startswith("a:"), "no reason"
    assert pf._norm(0.1234567891234) == 0.123456789 and pf._norm("n/a") == "n/a"
