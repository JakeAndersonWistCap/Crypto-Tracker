"""own_checks.py - a phase's own checks, chosen from its diff (P5-0 c, Jake 2026-11-03).

    python own_checks.py --base <commit> [--workers 3] [--dry-run]

Compares the working tree with <base> (the commit the phase started from) and runs:

    1. every changed test file; every test file importing a changed test helper (tests/*.py); every test file
       when tests/conftest.py changed (the shared harness);
    2. tests/test_adapters.py and tests/test_alignment.py whenever a file that defines metrics, columns or the
       concept registry changed (config.py, build_workbook.py, reader*.py, highlights.py, coverage_audit.py,
       spec.py, SPEC.md) - a superset of the P4 own-check rule (a phase that adds a metric to config or adds or
       changes a column runs both);
    3. every test file that imports a changed module and names a function, class, module-level name or metric
       added or changed in it (config metrics: any test file importing config that names the metric);

then ruff F,E9 (the total must not exceed 81). It prints every selected file with its reason and every test file
it did NOT select, so a reader can see what was left to the full gate. The full gate at the end of each round
(gate.py) is unchanged and runs every test.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULE_FILES = ("config.py", "build_workbook.py", "highlights.py", "coverage_audit.py", "spec.py", "SPEC.md")
RULE_PREFIXES = ("reader",)                 # reader.py, reader_look.py, reader_layout.py
RULE_TESTS = ("tests/test_adapters.py", "tests/test_alignment.py")
RUFF_MAX = 81
MIN_NAME = 4
GENERIC = {"ROOT", "HERE", "log", "logger", "TIMEOUT", "main", "self", "None", "True", "False", "name", "value", "data", "path", "rows", "test", "args", "kwargs",
           "return", "import", "from", "with", "pass", "else", "elif", "list", "dict", "float", "tuple", "print"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def changed_files(base: str) -> list[str]:
    tracked = _git("diff", "--name-only", base).split()
    untracked = _git("ls-files", "--others", "--exclude-standard").split()
    return sorted(set(tracked) | set(untracked))


def names_from_diff(diff: str) -> dict[str, set[str]]:
    """{file: names} for every non-test .py file in a unified diff (-U0): the enclosing def/class of each hunk, every
    def/class/module-level assignment on an added or removed line, and, in config.py, every metric-like string."""
    out: dict[str, set[str]] = {}
    cur = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            f = line[6:] if line.startswith("+++ b/") else None
            cur = f if f and f.endswith(".py") and not f.startswith("tests/") else None
            continue
        if cur is None or line.startswith("--- "):
            continue
        names = out.setdefault(cur, set())
        if line.startswith("@@"):
            m = re.search(r"@@[^@]*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)", line)
            if m:
                names.add(m.group(1))
            continue
        if not line or line[0] not in "+-":
            continue
        body = line[1:]
        m = re.match(r"\s*(?:async\s+)?(?:def|class)\s+(\w+)", body)
        if m:
            names.add(m.group(1))
        m = re.match(r"([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)", body)          # module-level NAME = ...
        if m:
            names.add(m.group(1))
        if cur == "config.py":
            names.update(re.findall(r"[\"']([a-z][a-z0-9]*(?:_[a-z0-9]+)+)[\"']", body))
    return {f: {n for n in v if len(n) >= MIN_NAME and n not in GENERIC and not n.startswith("__")}
            for f, v in out.items()}


def module_ref(src: str) -> re.Pattern:
    """A test file that imports the module `src` (a repository path): import M / from M import / from pkg import m."""
    mod = src[:-3].replace("/", ".")
    pkg, _, last = mod.rpartition(".")
    alts = [rf"\bimport\s+{re.escape(mod)}\b", rf"\bfrom\s+{re.escape(mod)}\s+import\b"]
    if pkg:
        alts.append(rf"\bfrom\s+{re.escape(pkg)}\s+import\s+[^\n]*\b{re.escape(last)}\b")
    return re.compile("|".join(alts))


def select(changed: list[str], names: dict[str, set[str]], tests: dict[str, str]) -> dict[str, list[str]]:
    """{test file: [reasons]} - `tests` maps each test file (tests/test_*.py) to its text."""
    sel: dict[str, list[str]] = {}
    if "tests/conftest.py" in changed:                    # the shared harness: every test depends on it
        for t in tests:
            sel.setdefault(t, []).append("tests/conftest.py changed")
    for f in changed:
        if f in tests:
            sel.setdefault(f, []).append("changed in this phase")
        elif f.startswith("tests/") and f.endswith(".py") and f != "tests/conftest.py":
            helper = Path(f).stem                          # a helper the tests import by its bare name
            ref = re.compile(rf"\b(import\s+{re.escape(helper)}\b|from\s+{re.escape(helper)}\s+import\b)")
            for t, text in tests.items():
                if ref.search(text):
                    sel.setdefault(t, []).append(f"imports {f}")
    rule = [f for f in changed if f in RULE_FILES or (f.endswith(".py") and "/" not in f
                                                       and f.startswith(RULE_PREFIXES))]
    if rule:
        for t in RULE_TESTS:
            if t in tests:
                sel.setdefault(t, []).append(f"own-check rule: {', '.join(rule)} changed")
    for src, ns in sorted(names.items()):
        if not ns:
            continue
        pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, ns))) + r")\b")
        ref = module_ref(src)
        for t, text in tests.items():
            if not ref.search(text):
                continue
            hit = sorted(set(pat.findall(text)))
            if hit:
                sel.setdefault(t, []).append(f"names {', '.join(hit[:6])}{' ...' if len(hit) > 6 else ''} "
                                             f"({src})")
    return sel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="the commit the phase started from")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="print the selection only")
    a = ap.parse_args(argv)
    if not 1 <= a.workers <= 3:
        print("own_checks: --workers must be 1..3")
        return 2
    changed = changed_files(a.base)
    names = names_from_diff(_git("diff", "-U0", a.base))
    tests = {f"tests/{p.name}": p.read_text(encoding="utf-8") for p in sorted((ROOT / "tests").glob("test_*.py"))}
    sel = select(changed, names, tests)
    print(f"own checks against {a.base}: {len(changed)} changed file(s): {', '.join(changed) or '-'}")
    print(f"SELECTED {len(sel)} test file(s):")
    for t in sorted(sel):
        print(f"  {t}: {'; '.join(sel[t])}")
    left = sorted(set(tests) - set(sel))
    print(f"NOT SELECTED ({len(left)}, left to the full gate): {', '.join(Path(t).name for t in left)}")
    if a.dry_run:
        return 0
    rc = 0
    if sel:
        cmd = [sys.executable, "-m", "pytest", "-q", "-rf", "--durations=15", "-p", "no:cacheprovider"]
        if a.workers > 1:
            cmd += ["-n", str(a.workers), "--dist", "loadgroup"]
        rc = subprocess.run(cmd + sorted(sel), cwd=ROOT).returncode
    out = subprocess.run(["ruff", "check", "--select", "F,E9", "--statistics", "."], cwd=ROOT, capture_output=True,
                         text=True).stdout
    total = sum(int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+\S", out, re.M))
    print(f"ruff F,E9: {total} (max {RUFF_MAX})")
    return rc if total <= RUFF_MAX else (rc or 1)


if __name__ == "__main__":
    import progress  # noqa: E402
    progress.line_buffered()
    sys.exit(main())
