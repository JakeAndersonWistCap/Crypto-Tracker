"""P5-1.1c (Jake 2026-11-04): no test reaches the network. conftest refuses every real connection and every browser
for the whole test process and fails the test that tried, even when the code under test caught the refusal."""
import socket

import conftest


class _Sock:
    def __init__(self, family):
        self.family = family


def test_the_allow_rule_loopback_servers_yes_the_proxy_and_the_world_no():
    proxy = {41479}
    inet = _Sock(socket.AF_INET)
    assert conftest._network_allowed(inet, ("127.0.0.1", 8080), proxy), "a server the test started itself"
    assert conftest._network_allowed(_Sock(socket.AF_INET6), ("::1", 8080), proxy)
    assert not conftest._network_allowed(inet, ("127.0.0.1", 41479), proxy), "the agent proxy reaches the internet"
    assert not conftest._network_allowed(inet, ("104.20.23.154", 443), proxy)
    assert not conftest._network_allowed(inet, ("example.com", 443), proxy)
    assert conftest._network_allowed(_Sock(socket.AF_UNIX), "/tmp/lo.sock", proxy), "Unix sockets (LibreOffice)"


def test_the_proxy_port_is_read_from_the_environment(monkeypatch):
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:41479")
    assert conftest._proxy_ports() == {41479}


def test_a_real_connection_is_refused_with_a_clear_message_and_recorded():
    before = len(conftest.NETWORK_ATTEMPTS)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("192.0.2.1", 443))                 # TEST-NET-1: never routed
        raise AssertionError("the connection was not refused")
    except ConnectionRefusedError as e:
        assert "NETWORK BLOCKED IN TESTS" in str(e) and "test_a_real_connection_is_refused" in str(e)
    finally:
        s.close()
    assert s.connect_ex is not None
    got = conftest.NETWORK_ATTEMPTS[before:]
    assert len(got) == 1 and "192.0.2.1" in got[0][1]
    del conftest.NETWORK_ATTEMPTS[before:]            # recorded as it should be; not this test's failure


def test_a_browser_is_refused_and_recorded():
    import pytest
    pw = pytest.importorskip("playwright.sync_api")
    before = len(conftest.NETWORK_ATTEMPTS)
    try:
        pw.sync_playwright()
        raise AssertionError("the browser was not refused")
    except RuntimeError as e:
        assert "BROWSER BLOCKED IN TESTS" in str(e)
    assert conftest.NETWORK_ATTEMPTS[before:][0][1] == "start a Playwright browser"
    del conftest.NETWORK_ATTEMPTS[before:]
