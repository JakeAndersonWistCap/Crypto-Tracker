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
