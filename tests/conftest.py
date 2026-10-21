"""Shared test fixtures."""
import pytest


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
def _no_cached_values(monkeypatch):
    """Round G11 A1: a build writes every formula's calculated value into the file (workbook_values, ~1 min on a full
    build). Tests skip it; the test of A1 itself sets TOKEN_METRICS_STORE_VALUES=1."""
    monkeypatch.setenv("TOKEN_METRICS_STORE_VALUES", "0")
