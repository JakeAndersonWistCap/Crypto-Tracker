"""Shared test fixtures."""
import pytest


def pytest_collection_finish(session):
    """P2f-4: the full suite stops before it starts, in one line, when the disk is too full for it (it ran at 1.7 GB
    free in the P2e round and died half-way with OSError 28). A targeted run needs far less. See disk_guard.py."""
    import tempfile
    import disk_guard
    n = len(session.items)
    msg = disk_guard.check([disk_guard.ROOT, tempfile.gettempdir()], disk_guard.suite_need_gb(n),
                           f"full suite, {n} tests" if n >= disk_guard.SUITE_MIN_TESTS else f"{n} tests")
    if msg:
        pytest.exit(msg, returncode=disk_guard.EXIT_DISK)


@pytest.fixture(autouse=True)
def _nearblocks_fake_clock(monkeypatch):
    """fetch/nearblocks.py paces calls to the free plan's 6 credits a minute with real sleeps.
    Tests run it on a fake clock the fake sleep advances, so pacing is exercised, not waited on.
    The list of pauses taken is returned for tests that assert on the pacing itself."""
    from fetch import nearblocks
    now, pauses = [1000.0], []
    monkeypatch.setattr(nearblocks, "_clock", lambda: now[0])

    def fake_sleep(s):
        pauses.append(s)
        now[0] += s
    monkeypatch.setattr(nearblocks, "_sleep", fake_sleep)
    return pauses


@pytest.fixture(autouse=True)
def _isolated_logcache(monkeypatch, tmp_path):
    """fetch/logcache.py persists scanned events between runs. Each test gets its own empty
    cache, so no test reads another's events or writes into the working tree's .cache/."""
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "logcache"))
    # once-a-day checks run every time in tests; the test of the gate itself unsets this
    monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "always")
    # tiers start together in tests (the stagger, Round G 1.1, has its own test)
    monkeypatch.setenv("TOKEN_METRICS_TIER_STAGGER", "0")
    # no connect-error retry naps in tests (Round G 1.1 has its own test, which sets it)
    monkeypatch.setenv("TOKEN_METRICS_CONNECT_RETRIES", "0")


@pytest.fixture(autouse=True)
def _no_solana_network(monkeypatch):
    """fetch/solana.py reads a live Solana RPC. No test reaches it: the default transport raises
    at once. A test that needs a Solana value replaces Chain.solana (or passes its own http)."""
    from fetch import solana

    class _NoNetwork:
        def __init__(self, *a, **k):
            pass

        def post(self, url, body):
            raise RuntimeError("no Solana RPC in tests")
    monkeypatch.setattr(solana, "Http", _NoNetwork)


@pytest.fixture
def dumps_20261009(monkeypatch):
    """Read the 2026-10-09 vintage of the real dumps (tests/real_dump.py VINTAGE) for this test."""
    import real_dump
    monkeypatch.setattr(real_dump, "VINTAGE", "20261009")
    return "20261009"



@pytest.fixture(autouse=True)
def _no_pending_restatement():
    """P2b-2 / P2c-5: a build declares a restatement's pending seam for ITSELF (config.RESTATEMENT_ACTIVE, refilled by
    every build from what its store holds). A fixture build in one test left Chainlink's declared in the next
    (test_d4b_chainlink_wallet_set_growth_is_a_declared_nested_handover failed after test_round_g11 in one process);
    each test starts with none, as a fresh build does."""
    import config
    getattr(config, "RESTATEMENT_ACTIVE", {}).clear()
    yield
    getattr(config, "RESTATEMENT_ACTIVE", {}).clear()


@pytest.fixture(autouse=True)
def _no_cached_values(monkeypatch):
    """Round G11 A1: a build writes every formula's calculated value into the file (workbook_values, ~1 min on a full
    build). Tests skip it; the test of A1 itself sets TOKEN_METRICS_STORE_VALUES=1."""
    monkeypatch.setenv("TOKEN_METRICS_STORE_VALUES", "0")


# ============================================================================== P5-0 a / b (Jake 2026-11-03)
# The files that share ONE build of the 15 real dumps (tests/shared_build.py). They are one xdist group, so a gate
# run builds it once; every other test file is a group of its own (a file never splits across workers, so its
# module fixtures are built once). tests/test_shared_build.py checks this list against the files that use it.
SHARED_PLAIN_BUILD_FILES = (
    "test_round_p1.py", "test_round_p2e2.py", "test_round_p2e34.py", "test_round_p2e_ranks.py",
    "test_round_p3_1.py", "test_round_p3_2.py", "test_round_p3_a2.py", "test_round_p4_q5.py", "test_round_p5_1c_flows.py",
    "test_pr1_revenue.py",
    "test_pr1_status.py",
    "test_pr1_freeze.py",
    "test_pr1b_circ.py",
    "test_pr3b_maple_fluid.py",
    "test_pr4_morpho.py",
)
SHARED_PLAIN_BUILD_GROUP = "shared_plain_build"


def pytest_configure(config):
    config.addinivalue_line("markers", "xdist_group(name): run every test of the group on one xdist worker")
    _install_network_block()


_WIDE_SCOPE = None


def needs_one_worker(path) -> bool:
    """P5-1.1a: a file stays on one worker only when a fixture it defines outlives a single test (module, class,
    package or session scope): splitting it would build that fixture on every worker that runs one of its tests."""
    import re as _re
    global _WIDE_SCOPE
    if _WIDE_SCOPE is None:
        _WIDE_SCOPE = _re.compile(r"""scope\s*=\s*["'](module|class|package|session)["']""")
    try:
        return bool(_WIDE_SCOPE.search(open(path, encoding="utf-8").read()))
    except OSError:
        return True


@pytest.hookimpl(tryfirst=True)       # before xdist's own hook, which reads the mark to group the tests
def pytest_collection_modifyitems(session, config, items):
    import pathlib
    seen: dict = {}
    for item in items:
        path = pathlib.Path(str(item.fspath))
        name = path.name
        if name in SHARED_PLAIN_BUILD_FILES:
            item.add_marker(pytest.mark.xdist_group(name=SHARED_PLAIN_BUILD_GROUP))
        elif seen.setdefault(name, needs_one_worker(path)):
            item.add_marker(pytest.mark.xdist_group(name=name))
        # otherwise no group: under --dist loadgroup the file's tests are distributed one by one


# ================================================================================ P5-1.1c: no network in tests
NETWORK_ATTEMPTS: list = []        # (test id, what) for every refused attempt in this process


def _proxy_ports() -> set:
    import os as _os
    import urllib.parse as _up
    ports = set()
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        v = _os.environ.get(k) or ""
        try:
            if v and _up.urlsplit(v).port:
                ports.add(_up.urlsplit(v).port)
        except ValueError:
            pass
    return ports


def _current_test() -> str:
    import os as _os
    return (_os.environ.get("PYTEST_CURRENT_TEST") or "(outside a test)").split(" ")[0]


def _network_allowed(sock, address, proxy_ports) -> bool:
    import socket as _socket
    if sock.family not in (_socket.AF_INET, _socket.AF_INET6):
        return True                                   # Unix sockets: LibreOffice, local IPC
    try:
        host, port = address[0], int(address[1])
    except (TypeError, IndexError, ValueError):
        return False
    loopback = host in ("localhost", "::1") or str(host).startswith("127.")
    return loopback and port not in proxy_ports      # a server the test started itself; never the proxy


def _install_network_block():
    """Refuse every real connection and every browser for the whole test process, and record each attempt."""
    import socket as _socket
    if getattr(_socket.socket.connect, "_test_block", False):
        return
    proxy_ports = _proxy_ports()
    real_connect, real_connect_ex = _socket.socket.connect, _socket.socket.connect_ex

    def refuse(what):
        NETWORK_ATTEMPTS.append((_current_test(), what))
        return ConnectionRefusedError(f"NETWORK BLOCKED IN TESTS: {_current_test()} tried {what}; stub the network "
                                      f"in the test (tests/conftest.py, P5-1.1c)")

    def connect(self, address):
        if not _network_allowed(self, address, proxy_ports):
            raise refuse(f"connect {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _network_allowed(self, address, proxy_ports):
            refuse(f"connect_ex {address!r}")
            return 111                                 # ECONNREFUSED
        return real_connect_ex(self, address)
    connect._test_block = connect_ex._test_block = True
    _socket.socket.connect, _socket.socket.connect_ex = connect, connect_ex
    try:
        import playwright.sync_api as _pw
    except Exception:  # noqa: BLE001 - no Playwright installed: nothing to block
        return

    def no_browser(*a, **k):
        NETWORK_ATTEMPTS.append((_current_test(), "start a Playwright browser"))
        raise RuntimeError(f"BROWSER BLOCKED IN TESTS: {_current_test()} tried to start a browser; stub "
                           f"fetch.browser_capture / playwright in the test (tests/conftest.py, P5-1.1c)")
    _pw.sync_playwright = no_browser


_NETWORK_CHECKED = [0]             # attempts up to this index were already reported by a test


@pytest.fixture(autouse=True)
def _no_network_was_attempted():
    """P5-1.1c: a test during which a real connection or a browser was attempted fails, with every attempt listed,
    even when the code under test caught the refusal and carried on. Everything since the previous test's check
    counts, so an attempt inside a module fixture's setup fails the first test that set it up."""
    yield
    mine = NETWORK_ATTEMPTS[_NETWORK_CHECKED[0]:]
    _NETWORK_CHECKED[0] = len(NETWORK_ATTEMPTS)
    if mine:
        pytest.fail("NETWORK BLOCKED IN TESTS - this test (or its module fixture) tried the network "
                    f"{len(mine)} time(s): " + "; ".join(sorted({w for _t, w in mine}))[:1500], pytrace=False)


@pytest.fixture(autouse=True, scope="session")
def _shared_build_master_unchanged():
    """P5-0 a: a test that wrote into the shared build's master copy fails the run (checked once, at the end)."""
    yield
    import pathlib
    import sys as _sys
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in _sys.path:
        _sys.path.insert(0, here)
    import shared_build
    bad = shared_build.verify_unchanged()
    late = NETWORK_ATTEMPTS[_NETWORK_CHECKED[0]:]         # a module fixture's teardown, after its last test
    bad += [f"NETWORK BLOCKED IN TESTS after the last test checked: {t}: {w}" for t, w in late]
    assert not bad, "\n".join(bad)


_PROCESS_DEFAULTS: dict = {}


@pytest.fixture(autouse=True, scope="module")
def _each_file_starts_as_a_fresh_process():
    """P5-0 b (2026-11-03): under xdist a worker runs test files in an order no group ever did, and three pieces of
    process state leak from one file into the next (the audit run found them; each file passed alone):
      - sys.modules["recalc"] = None, left by measuring_guard.build and a1_break_check.read_groups (they keep the
        build's LibreOffice step out of their own process); a later LibreOffice test then cannot import recalc;
      - build_workbook._SCOPE, narrowed by any build with only=[...] (a script builds once, so the build never
        widens it again); a later unit test of aggregate() then sees one project;
      - store.DB_PATH, pointed at a temporary store by measuring_guard.build.
    Each test FILE now starts with all three as a fresh process has them. Nothing a file sets for its own tests is
    touched until the next file starts."""
    import sys as _sys
    if "recalc" in _sys.modules and _sys.modules["recalc"] is None:
        del _sys.modules["recalc"]
    if "build_workbook" in _sys.modules:
        bw = _sys.modules["build_workbook"]
        bw._SCOPE = list(bw.PROJECTS)
    import store as _store
    _PROCESS_DEFAULTS.setdefault("DB_PATH", _store.DB_PATH)
    _store.DB_PATH = _PROCESS_DEFAULTS["DB_PATH"]
    yield
