"""ROUND D2 (Jake 2026-10-26, head 6537f98) — the data phase, round 2.

3 Merkl = what Merkl ALLOCATED TO USERS (the creator's returns out; allocated-but-unclaimed in; a running campaign to
the engine's computedUntil; a failed read stores nothing).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

T = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731
E18 = 10 ** 18


def _camp(cid, amount, start, end, **kw):
    return {"campaignId": cid, "amount": str(int(amount * E18)), "startTimestamp": T(start), "endTimestamp": T(end),
            "distributionChainId": 8453, "rewardToken": {"address": "0xTOK", "symbol": "MORPHO", "decimals": 18},
            "creatorAddress": "0xCREATOR", **kw}


# ================================================================================================== 3
def test_3_allocated_basis_counts_allocated_less_returns_spread_to_until():
    from fetch.merkl_campaigns import daily_amounts
    camps = [_camp("0xended", 300, "2026-01-01", "2026-01-04"),
             _camp("0xlive", 100, "2026-01-05", "2026-01-15"),
             _camp("0xunread", 50, "2026-01-01", "2026-01-02"),
             _camp("0xchild", 30, "2026-01-01", "2026-01-04", parentCampaignId="1")]
    alloc = {"0xended": (210 * E18, T("2026-01-04")),            # 300 allocated, 90 credited back to the creator
             "0xlive": (40 * E18, T("2026-01-07"))}              # computed to 01-07 00:00: 40 over two days
    per_day, skipped = daily_amounts(camps, "0xtok", pd.Timestamp("2026-02-01").date(), 8453, set(), allocated=alloc)
    got = {str(k): round(v, 6) for k, v in per_day.items()}
    assert got == {"2026-01-01": 70, "2026-01-02": 70, "2026-01-03": 70, "2026-01-05": 20, "2026-01-06": 20}
    assert any(c == "0xunread" and "allocation to users was not read" in r for c, r in skipped)
    assert any(c == "0xchild" and "sub-campaign" in r for c, r in skipped)


def test_3_only_returns_are_subtracted_not_the_creators_own_earnings():
    from fetch.merkl_campaigns import RETURN_REASONS, returned_to_creator
    assert set(RETURN_REASONS) == {"end_campaign", "reallocation", "Invalid", "no_recipient"}
    got = returned_to_creator({"amount": str(int(70.5 * E18)), "breakdowns": [
        {"reason": "end_campaign", "amount": str(60 * E18)}, {"reason": "reallocation", "amount": str(10 * E18)},
        {"reason": "ERC20_0xc0Df5784f28046D11813356919B869dDA5815B16", "amount": str(E18 // 2)}]})
    assert got == 70 * E18                                       # Plume 0x194409c2: 8 WPLUME earned as a participant
    assert returned_to_creator({"amount": "0"}) == 0
    assert returned_to_creator({"amount": "5"}) is None          # held, no breakdown: a return cannot be told apart


class _Dc:
    def due(self, *a):
        return True

    def done(self, *a):
        pass


def _reader(monkeypatch, users, totals, camps):
    from fetch import merkl_campaigns as mc
    calls = []

    class H:
        def get(self, url, params=None):
            calls.append(url)
            if "/users/" in url:
                return users(url.rsplit("/", 1)[1])
            if url.endswith("/rewards/total"):
                return totals(params["campaignId"])
            return camps if params.get("page") == 0 else []
    monkeypatch.setattr(mc, "_now", lambda: T("2026-01-10 12:00"))
    monkeypatch.setattr(mc, "today", lambda: pd.Timestamp("2026-01-10"))
    return mc.MerklCampaigns(http=H(), daily_checks=_Dc()), calls


SPEC = {"url": "https://api.merkl.xyz/v4/campaigns", "creator": "0xCREATOR", "metric": "x",
        "chains": {8453: {"name": "Base", "token": "0xTOK"}}}


def test_3_the_reader_stores_allocated_to_users_and_stops_at_computed_until(monkeypatch):
    from fetch.base import FetchOutput
    camps = [_camp("0xended", 300, "2026-01-01", "2026-01-04"),
             _camp("0xlive", 100, "2026-01-05", "2026-01-15",
                   campaignStatus={"computedUntil": T("2026-01-08 06:00")}),
             _camp("0xfuture", 999, "2026-02-01", "2026-02-10")]
    users = {"0xended": {"amount": str(90 * E18), "breakdowns": [{"reason": "end_campaign", "amount": str(90 * E18)}]},
             "0xlive": {"amount": "0", "breakdowns": []}}
    totals = {"0xended": {"amount": str(300 * E18)}, "0xlive": {"amount": str(31 * E18)}}
    m, calls = _reader(monkeypatch, lambda c: users[c], lambda c: totals[c], camps)
    out = FetchOutput()
    m._project("Morpho", SPEC, out)
    f = [x for x in out.frames if x is not None and not x.empty][0]
    s = f.assign(date=pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")).set_index("date")["value"].round(6)
    # 0xended: 300 − 90 over 3 days; 0xlive: 31 over 01-05 00:00..01-08 06:00 (3.25 days); the series ends 01-07
    assert s.to_dict() == {"2026-01-01": 70, "2026-01-02": 70, "2026-01-03": 70, "2026-01-04": 0,
                           "2026-01-05": round(31 / 3.25, 6), "2026-01-06": round(31 / 3.25, 6),
                           "2026-01-07": round(31 / 3.25, 6)}
    assert not any("0xfuture" in c for c in calls)               # not started: nothing to read
    assert any("ends 2026-01-07: a running campaign is computed by Merkl only to 2026-01-08 06:00" in e.message
               for e in out.log)


def test_3_a_failed_total_read_stores_nothing(monkeypatch):
    from fetch.base import FetchOutput
    camps = [_camp("0xended", 300, "2026-01-01", "2026-01-04")]
    m, _ = _reader(monkeypatch, lambda c: {"amount": "0", "breakdowns": []}, lambda c: {"error": "down"}, camps)
    out = FetchOutput()
    m._project("Morpho", SPEC, out)
    assert not [f for f in out.frames if f is not None and not f.empty]
    assert any("NOTHING STORED" in e.message and "no allocated total read" in e.message for e in out.log)


def test_3_the_schedule_references_are_recorded_and_the_allocation_is_compared():
    import references as rf
    for k in ("merkl_rewards_rate", "g6_merkl_base_campaign_total"):
        r = rf.ROUTES[("Morpho", k)]
        assert r[0] == "recorded" and "schedule basis" in r[1] and "ALLOCATED TO USERS" in r[1]
    route = rf.ROUTES[("Morpho", "d2_merkl_base_campaign_allocated")]
    row = [r for r in rf.load() if (r["project"], r["metric"]) == ("Morpho", "d2_merkl_base_campaign_allocated")][0]
    assert float(row["value"]) == pytest.approx(199_378.3625 - 61_137.478, abs=0.01)
    assert str(row["source_type"]).startswith("same-source")
    # the allocated series of the live read (2026-10-26): 4,608.03 a day inside the campaign
    d = pd.date_range("2026-09-15", "2026-10-25")
    v = [4608.0295 if x <= pd.Timestamp("2026-10-20") else 4822.0 if x == pd.Timestamp("2026-10-21") else 5464.1
         for x in d]
    g = pd.DataFrame({"date": d, "project": "Morpho", "metric": route[1], "value": v, "source": "merkl_campaigns",
                      "tier": 1})
    got, how, err = rf._our_value(route, {("Morpho", route[1]): g}, "Morpho", pd.Timestamp("2026-10-19"), 138_240.88)
    assert not err and got == pytest.approx(138_240.88, abs=0.1)


def test_3_labels_say_allocated_to_users():
    for m in ("merkl_campaigns_non_ethereum_tokens", "merkl_campaigns_plume_tokens"):
        assert "ALLOCATED TO USERS" in config.METRICS[m]["label"]
    assert "ALLOCATED TO USERS" in config.CREDIBILITY_WIRING["Morpho"]["in_merkl_off_ethereum"]["ref"]["why"]
    assert "INCLUDES the off-Ethereum claims" in config.CREDIBILITY_WIRING["Morpho"]["in_merkl_off_ethereum"]["ref"]["why"]
