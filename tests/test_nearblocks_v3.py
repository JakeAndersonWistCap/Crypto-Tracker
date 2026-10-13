"""
tests/test_nearblocks_v3.py — NearBlocks API v1 -> v3 (Jake 2026-10-13, Part E; v1 retires 2026-10-31).

E4: any NearBlocks call to a v1 / v2 path fails the build — written in the code, declared in config, or attempted at
run time — and a deprecation / sunset answer is named on the Run Log, in the daily summary and on daily_run's console
line. The one allowed v1 call is check_offline_items.nearblocks_v3_parity's /v1/stats, beside its v3 twin, and only
until the sunset.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

SUNSET = date(2026, 10, 31)
# a string literal naming a v1 / v2 NearBlocks path: the full URL, or a bare "/v1/…" in a NearBlocks file
_URL = re.compile(r"""["'](?:https?://)?api\.nearblocks\.io/v[12]/""")
_BARE = re.compile(r"""["']/v[12]/""")


def _code_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".cache/", "snapshots/")) or "/." in rel:
            continue
        yield rel, p.read_text(encoding="utf-8")


def test_no_code_calls_a_nearblocks_v1_or_v2_url():
    bad = []
    for rel, text in _code_files():
        for i, ln in enumerate(text.splitlines(), start=1):
            if ln.lstrip().startswith("#"):
                continue
            if _URL.search(ln):
                bad.append(f"{rel}:{i}: {ln.strip()[:120]}")
    assert not bad, "NearBlocks v1/v2 URL(s) — v1 retires 2026-10-31; use the /v3 equivalent:\n" + "\n".join(bad)


def test_the_nearblocks_modules_hold_no_bare_v1_path_but_the_one_parity_call():
    """fetch/nearblocks.py holds none; check_offline_items.py only NEARBLOCKS_V1_PARITY_PATH, and only until the
    sunset (after it, the parity call has nothing to compare with and must go)."""
    nb = (ROOT / "fetch" / "nearblocks.py").read_text(encoding="utf-8")
    assert not [ln for ln in nb.splitlines() if _BARE.search(ln) and not ln.lstrip().startswith("#")]
    coi = (ROOT / "check_offline_items.py").read_text(encoding="utf-8")
    hits = [ln.strip() for ln in coi.splitlines() if "nearblocks" in ln.lower() or "NEARBLOCKS" in ln
            if _BARE.search(ln) and not ln.lstrip().startswith("#")]
    allowed = 'NEARBLOCKS_V1_PARITY_PATH = "/v1/stats"'
    assert all(h.startswith(allowed) for h in hits), hits
    if date.today() > SUNSET:
        assert not hits, "NearBlocks v1 is retired: remove the parity call (check_offline_items.nearblocks_v3_parity)"


def test_every_declared_nearblocks_path_is_v3():
    near = config.PROJECT_BY_NAME["Near"]
    spec = near["nearblocks"]
    paths = [m["path"] for m in spec["metrics"].values()]
    paths += [spec[k]["path"] for k in ("validators_info", "supply_stats", "balance_history") if spec.get(k)]
    assert paths and all(p.startswith("/v3/") for p in paths), paths
    for p in config.PROJECTS:
        for f in (p.get("near_account_flows") or []) + (p.get("near_account_flows_retired") or []):
            assert "api.nearblocks.io" in f["base_url"] and "/v1" not in f["base_url"], f


def test_the_client_refuses_a_v1_path_before_any_call():
    from fetch import nearblocks as nb

    class H:
        calls = 0

        def get(self, *a, **k):
            H.calls += 1
            return {}

    client = nb.NearBlocks(http=H())
    for path in ("/v1/stats", "/v1/account/x.near/txns", "/v2/txns/abc"):
        with pytest.raises(nb.RetiredPath):
            client._get("https://api.nearblocks.io" + path, {}, "k")
    assert H.calls == 0
    client._get("https://api.nearblocks.io/v3/stats", {}, "k")
    assert H.calls == 1


def test_the_account_flow_read_is_v3_shaped():
    """v3: /v3/accounts/{a}/txns, `receiver` + `limit` + `before_ts` + opaque `next`; rows under `data`; a distinct
    source, so a stored v1 series is never silently continued on the v3 basis (E2)."""
    from fetch import nearblocks as nb
    assert nb.V3_ACCOUNT_TXNS == "/v3/accounts/{account}/txns"
    assert nb.FLOW_SOURCE_V3 != nb.SOURCE and nb.FLOW_SOURCE_V3.startswith(nb.SOURCE)
    assert 1 <= nb.FLOW_PER_PAGE <= 100
    src = (ROOT / "fetch" / "nearblocks.py").read_text(encoding="utf-8")
    assert '"after_date"' not in src and '"per_page": FLOW_PER_PAGE' not in src


def test_a_sunset_answer_is_named_on_the_run_log_and_in_the_summaries(caplog):
    import logging

    from fetch import nearblocks as nb
    from fetch.base import FetchOutput

    class H:
        last_headers = {"Sunset": "Sat, 31 Oct 2026 00:00:00 GMT", "Deprecation": "@1793404800"}

        def get(self, url, params=None, headers=None):
            return {"data": {"total_supply": str(10 ** 33), "circulating_supply": str(9 * 10 ** 32)}}

    client = nb.NearBlocks(http=H())
    with caplog.at_level(logging.WARNING, logger="token_metrics.fetch.nearblocks"):
        client._get("https://api.nearblocks.io/v3/stats", {}, "k")
    assert "ACTION NEEDED" in caplog.text and nb.SUNSET_MARKER in caplog.text
    out = FetchOutput()
    client.run([], None, out)                         # no project: recorded, not attributed
    client.run([config.PROJECT_BY_NAME["Near"] | {"nearblocks": {}}], None, out)
    assert any(nb.SUNSET_MARKER in e.message for e in out.log), [e.message for e in out.log]

    class Gone(Exception):
        status = 410

    class G:
        def get(self, *a, **k):
            raise Gone("HTTP 410 Gone — this endpoint has been retired")

    c2 = nb.NearBlocks(http=G())
    with pytest.raises(Gone):
        c2._get("https://api.nearblocks.io/v3/txn-stats", {}, "k")
    assert "/v3/txn-stats" in c2.sunsets

    # the daily summary lists the ACTION NEEDED line; daily_run's console line names NearBlocks
    import daily_summary
    assert "ACTION NEEDED" in Path(daily_summary.__file__).read_text(encoding="utf-8")
    ps = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    assert '*NEARBLOCKS API SUNSET*' in ps and '"NearBlocks: deprecation/sunset answer' in ps
    assert ps.index("$NbSunset") < ps.index("Write-Log $summary")


def test_the_parity_probe_is_registered():
    import check_offline_items as coi
    assert coi.nearblocks_v3_parity in coi.CHECKS
