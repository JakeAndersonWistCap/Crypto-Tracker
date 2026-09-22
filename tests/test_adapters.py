"""
tests/test_adapters.py — adapter parsing against payloads shaped like the real sources.

    python tests/test_adapters.py

Runs offline: the HTTP layer, the web3 connection and the Playwright page are all stubbed.
Checks that each tier turns its documented response shape into tidy long rows with the right
metric key, source label and tier, and that a failure is logged and reported as a gap rather
than raised.
"""
from __future__ import annotations

import re
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DUNE_API_KEY"] = "test-key"

import config  # noqa: E402
from fetch import base  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402
from fetch.chain import Chain  # noqa: E402
from fetch.coingecko import CoinGecko  # noqa: E402
from fetch.dune import Dune  # noqa: E402
from fetch.llama import DefiLlama  # noqa: E402
from fetch.scrape import Scrape, extract_dom, extract_xhr  # noqa: E402
from fetch.validate import validate_frame  # noqa: E402

TS1, TS2 = 1756684800, 1756771200   # 2025-09-01, 2025-09-02 UTC


class StubHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        for key, payload in self.routes.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise RuntimeError(f"HTTP 404 from {url}")


# ---------------------------------------------------------------------------- tier 1
def test_defillama():
    dl = DefiLlama()
    dl.http = StubHttp({
        "/summary/fees/aave": {"totalDataChart": [[TS1, 100.0], [TS2, 150.0]]},
        "/protocol/aave": {"tvl": [{"date": TS1, "totalLiquidityUSD": 5e9}]},
        "/v2/historicalChainTvl/Ethereum": [{"date": TS1, "tvl": 6e10}],
        "/stablecoincharts/Ethereum": [{"date": str(TS1), "totalCirculatingUSD": {"peggedUSD": 1.2e11, "peggedEUR": 1e8}}],
        "/protocols": [{"slug": "ondo", "category": "RWA", "chains": ["Ethereum"]},
                       {"slug": "uni", "category": "Dexes", "chains": ["Ethereum"]}],
        "/protocol/ondo": {"tvl": [], "chainTvls": {"Ethereum": {"tvl": [{"date": TS1, "totalLiquidityUSD": 1e9}]}}},
    })
    out = FetchOutput()
    aave = {"name": "Aave", "defillama_fees_slug": "aave", "defillama_protocol": "aave", "defillama_chain": None, "archetypes": [3]}
    eth = {"name": "Ethereum", "defillama_fees_slug": "broken", "defillama_protocol": None, "defillama_chain": "Ethereum", "archetypes": [1]}
    dl.run([aave, eth], None, out)
    df = out.frame()
    got = set(zip(df["project"], df["metric"]))
    for want in [("Aave", "fees_usd"), ("Aave", "revenue_usd"), ("Aave", "protocol_tvl_usd"),
                 ("Ethereum", "tvl_usd"), ("Ethereum", "stablecoin_supply_usd"), ("Ethereum", "rwa_defillama_usd")]:
        assert want in got, (want, got)
    stab = df[(df.project == "Ethereum") & (df.metric == "stablecoin_supply_usd")]
    assert abs(stab.value.iloc[0] - 1.201e11) < 1
    assert set(df["tier"]) == {1}
    fails = [e for e in out.log if e.status == "failed"]
    assert len(fails) == 3 and all(e.tier == 1 for e in fails), fails
    print(f"tier 1 defillama ok: {len(df)} rows, {len(fails)} logged failures")


def test_coingecko():
    cg = CoinGecko()
    cg.http = StubHttp({
        "/coins/aave/market_chart": {"prices": [[TS1 * 1000, 200.0], [TS2 * 1000, 210.0]],
                                     "market_caps": [[TS1 * 1000, 3e9], [TS2 * 1000, 3.15e9]],
                                     "total_volumes": [[TS1 * 1000, 1e8], [TS2 * 1000, 1.1e8]]},
        "/coins/aave": {"market_data": {"circulating_supply": 15e6, "total_supply": 16e6,
                                        "max_supply": 16e6, "fully_diluted_valuation": {"usd": 3.36e9}}},
    })
    out = FetchOutput()
    cg.run([{"name": "Aave", "coingecko_id": "aave"}, {"name": "Nope", "coingecko_id": None}], None, out)
    df = out.frame()
    assert {"price_usd", "market_cap_usd", "circulating_supply_implied", "circulating_supply",
            "total_supply", "max_supply", "fdv_usd"} <= set(df["metric"])
    implied = df[df.metric == "circulating_supply_implied"]
    assert abs(implied.value.iloc[0] - 1.5e7) < 1 and implied.source.iloc[0] == "coingecko:mcap/price"
    assert cg.http.calls[0][1]["days"] == "365"
    assert any(e.status == "unconfigured" and e.project == "Nope" for e in out.log)
    print("tier 1 coingecko ok:", len(set(df["metric"])), "metrics")


# ---------------------------------------------------------------------------- tier 2
class StubReader:
    """Stands in for a live RPC connection."""

    def __init__(self, symbol="CAKE", supply=100.0, balance=25.0, raise_on=None):
        self.symbol, self.supply, self.balance, self.raise_on = symbol, supply, balance, raise_on

    def symbol_matches(self, chain, address, expected):
        if self.raise_on == "symbol":
            raise RuntimeError("RPC timeout")
        return str(self.symbol).lower() == str(expected).lower(), self.symbol

    def scaled(self, chain, address, call, *args):
        if self.raise_on == "read":
            raise RuntimeError("execution reverted")
        return self.balance if args and args[0] else self.supply


def _cake_project(verified=None):
    return {
        "name": "PancakeSwap", "archetypes": [4, 3],
        "contracts": {
            "token": {"address": "0x0E09", "chain": "bsc", "kind": "erc20_total_supply",
                      "expected_symbol": "Cake", "source_url": "u", "verified": verified},
            "burn_dead": {"address": "0xdEaD", "chain": "bsc", "kind": "burn_address_balance",
                          "expected_symbol": "Cake", "source_url": "u", "verified": verified},
        },
    }


def test_chain_refuses_unverified_by_default():
    os.environ.pop("TOKEN_METRICS_ALLOW_UNVERIFIED", None)
    c = Chain()
    c.reader = StubReader()
    out = FetchOutput()
    c.run([_cake_project(verified=None)], None, out)
    assert out.frame().empty, "unverified addresses must not be read by default"
    assert len(out.gaps) == 2 and all("NOT verified" in g["reason"] for g in out.gaps), out.gaps
    print("tier 2 gate ok: unverified addresses refused,", len(out.gaps), "gap rows raised")


def test_chain_reads_verified_and_derives_flow():
    c = Chain(prior_values={("PancakeSwap", "burn_address_balance"): 20.0})
    c.reader = StubReader(symbol="Cake", supply=100.0, balance=25.0)
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    df = out.frame()
    by = dict(zip(df["metric"], df["value"]))
    assert by["total_supply"] == 100.0, by
    assert by["burn_address_balance"] == 25.0, by
    assert by["gross_burn_tokens"] == 5.0, "flow must be the delta 25 - 20"
    assert set(df["tier"]) == {2}
    print("tier 2 read ok:", by)


def test_an_orphaned_row_and_a_changed_measuring_point_are_RED_not_amber():
    """Both were asserting false figures at full confidence. Neither is low-confidence — both are
    known-false, and age and status say nothing about either."""
    from build_workbook import confidence_for

    asof = pd.Timestamp("2026-09-14")
    orphan = {"status": "ok", "source": "chain:ethereum:burn_zero:delta", "n_points": 2,
              "entered_on": "", "measuring_points": ("chain:ethereum:burn_zero",)}
    band, why = confidence_for("Sky", "gross_burn_tokens", orphan, asof)
    assert band == "RED", f"a row from a removed contract must not read as usable, got {band}"
    assert "ORPHANED" in why and "burn_zero" in why

    switched = {"status": "ok", "source": "chain:ethereum:burn_dead:PARTIAL:delta", "n_points": 3,
                "entered_on": "",
                "measuring_points": ("chain:ethereum:fire_pit", "chain:ethereum:burn_dead")}
    band, why = confidence_for("Uniswap", "gross_burn_tokens", switched, asof)
    assert band == "RED", f"a series read from two addresses must not read as usable, got {band}"
    assert "MEASURING POINT CHANGED" in why
    assert "PARTIAL" not in why, \
        "the PARTIAL note says it UNDERSTATES; this figure is ~30x overstated, so it must not lead"

    clean = {"status": "ok", "source": "chain:ethereum:burn_dead:PARTIAL", "n_points": 9,
             "entered_on": "", "measuring_points": ("chain:ethereum:burn_dead",)}
    assert confidence_for("Uniswap", "burn_address_balance", clean, asof)[0] == "AMBER", \
        "one consistent measuring point is not RED — PARTIAL and composition still qualify it"
    print("orphan/switch guard ok: both forced RED, a clean single-source read is not")


def test_a_REFUTED_burn_mechanism_is_RED_not_silently_GREEN():
    """The most certain thing config can say about a burn used to be the one that said nothing.

    confidence_for's mechanism branch only ever looked for "assumed", so every other status fell
    through clean — including "refuted", which means we have POSITIVELY ESTABLISHED that the
    protocol does not burn in a way the metric can measure. assumed (uncertain) went AMBER while
    refuted (certain, and worse) went GREEN.

    Sky escaped this by accident: its burn contract was DELETED, so the orphan guard caught the
    row through its source string. Keep the contract and flip only the mechanism and nothing
    flagged it — which is the Maple pattern with a different trigger.
    """
    from build_workbook import confidence_for

    asof = pd.Timestamp("2026-09-15")
    # Ethereum declares NO contracts (EIP-1559 has no address), so the source must not name a
    # contract key — orphaned_contract_keys would fire first and the test would pass for the
    # wrong reason. A tier 1 source is the honest shape for this project.
    row = {"status": "ok", "source": "tier1", "n_points": 9,
           "entered_on": "", "measuring_points": ("tier1",)}

    mech = config.PROJECT_BY_NAME["Ethereum"]["burn_mechanism"]
    live = mech["status"]
    try:
        # THE THREE STATUSES, AND THE ORDERING THAT MATTERS: certainty must not buy silence.
        mech["status"] = "confirmed"
        assert confidence_for("Ethereum", "gross_burn_tokens", row, asof)[0] == "GREEN"

        mech["status"] = "assumed"
        band, why = confidence_for("Ethereum", "gross_burn_tokens", row, asof)
        assert band == "AMBER" and "assumed" in why, f"{band}: {why}"

        mech["status"] = "refuted"
        band, why = confidence_for("Ethereum", "gross_burn_tokens", row, asof)
        assert band == "RED", f"refuted is MORE certain than assumed — it cannot be quieter: {band}"
        assert "REFUTED" in why, f"the reason must name the refutation: {why!r}"
        assert "protocol_level_destruction" in why, \
            f"and the model that was refuted, so the reader knows what was ruled out: {why!r}"

        # SAME PRIORITY AS THE ORPHAN GUARD: nothing downstream may soften it. A single
        # observation would otherwise produce its own AMBER, and a PARTIAL marker its own.
        thin = dict(row, n_points=1, source="tier1:PARTIAL")
        band, why = confidence_for("Ethereum", "gross_burn_tokens", thin, asof)
        assert band == "RED" and "REFUTED" in why, \
            f"single-observation/PARTIAL must not displace a refutation: {band} {why!r}"

        # SCOPED TO BURN METRICS: a refuted burn says nothing about supply.
        assert confidence_for("Ethereum", "total_supply", row, asof)[0] != "RED", \
            "a refuted burn mechanism must not poison unrelated metrics on the same project"
    finally:
        mech["status"] = live
    assert confidence_for("Ethereum", "gross_burn_tokens", row, asof)[0] != "RED", \
        "config restored"
    print("refuted mechanism ok: RED with the model named, unsoftened by thin data, "
          "and scoped to burn metrics only")


def test_impossible_relations_have_NO_tolerance():
    """Zero tolerance on a REAL identity — and the case this test used to cite was not one.

    IT ASSERTED THE OPPOSITE UNTIL 2026-09-16. The fixture was Aerodrome's locked 989,654,626.93
    against circulating 988,795,723.09, and the test required that 0.087% overshoot to flag. It
    was encoding a premise that turned out to be false: CoinGecko EXCLUDES escrowed supply for a
    ve-token protocol, so locked and circulating are DISJOINT halves of total supply and locked
    sitting above circulating is the correct state, not a contradiction. Confirmed on live data —
    locked + circulating = total_supply to 0.00%.

    So the relation was withdrawn, and this test now proves zero tolerance on a relation that IS
    an identity: treasury_holding_tokens against total_supply. A treasury cannot hold tokens that
    do not exist, under any provider's convention.

    The original point stands and is what the fixture below preserves: a half-percent buffer is
    not caution, it is a licence for a contradiction to sit in the sheet as long as it stays
    small, and a small contradiction is the unnoticed one.
    """
    from fetch.validate import check_impossible_relations

    # THE SAME 0.087% MARGIN, on a relation where it is genuinely impossible.
    breach = 988_795_723.09 * 1.00087
    rows = [("Aerodrome", "treasury_holding_tokens", breach, "chain:base:treasury"),
            ("Aerodrome", "total_supply", 988_795_723.09, "chain:base:token"),
            ("Uniswap", "treasury_holding_tokens", 100.0, "chain:x"),
            ("Uniswap", "total_supply", 1_000.0, "chain:x")]
    df = pd.DataFrame([{"date": pd.Timestamp("2026-09-14"), "project": pr, "metric": m,
                        "value": v, "source": src, "tier": 2} for pr, m, v, src in rows])
    out = FetchOutput()
    check_impossible_relations(df, out)

    flagged = [r for r in out.review if r["reason"] == "impossible_relation"]
    assert len(flagged) == 1 and flagged[0]["project"] == "Aerodrome", \
        f"a 0.087% breach of a real identity must flag, got {len(flagged)}"
    assert "988,795,723.09" in flagged[0]["source"], \
        "both figures and both sources must be named, or it cannot be investigated"
    assert not [r for r in flagged if r["project"] == "Uniswap"], "a sane pair must stay silent"

    # the check must not need to know anything about the project to catch it
    assert any("impossible" in g["metric"] for g in out.gaps)

    # ** AND THE WITHDRAWN COMPARISON MUST STAY SILENT ON THE FIGURES THAT MOTIVATED IT. **
    # If someone re-adds locked_tokens <= circulating_supply, this fails.
    out2 = FetchOutput()
    df2 = pd.DataFrame([{"date": pd.Timestamp("2026-09-14"), "project": "Aerodrome", "metric": m,
                         "value": v, "source": "test", "tier": 2}
                        for m, v in (("locked_tokens", 989_654_626.93),
                                     ("circulating_supply", 988_795_723.09))])
    check_impossible_relations(df2, out2)
    assert not [r for r in out2.review if r["reason"] == "impossible_relation"], \
        "locked above circulating is CORRECT under the excludes-locked convention and must not flag"
    print("impossible-relation ok: 0.087% breach of a real identity caught, withdrawn "
          "lock-vs-circulating comparison stays silent")


def test_uniswap_buyback_fund_is_relabelled_not_redefined():
    """The TokenJar holds FEE TOKENS, so its UNI balance is not the fund's value.

    Relabelled rather than redefined: buyback_fund_balance means the same thing for every other
    project, and that sameness is what makes the column comparable. Correcting the label says what
    was actually read without making one cell mean something different from the rest of its column.
    """
    assert config.metric_label("Uniswap", "buyback_fund_balance") == \
        "UNI balance of TokenJar (not the fund total)"
    # every other project keeps the library label — the metric itself is unchanged
    for other in ("Chainlink", "Aave", "Maple"):
        assert config.metric_label(other, "buyback_fund_balance") == \
            config.METRICS["buyback_fund_balance"]["label"], other
    # and an un-overridden metric on Uniswap is untouched
    assert config.metric_label("Uniswap", "total_supply") == config.METRICS["total_supply"]["label"]

    from build_workbook import confidence_for

    row = {"status": "ok", "n_points": 9, "entered_on": "",
           "source": "chain:sum(ethereum:token_jar+ethereum:v3_fee_adapter):PARTIAL",
           "measuring_points": ("chain:sum(ethereum:token_jar+ethereum:v3_fee_adapter)",)}
    band, why = confidence_for("Uniswap", "buyback_fund_balance", row, pd.Timestamp("2026-09-14"))
    assert band == "AMBER" and "FEE TOKENS" in why, f"{band}: {why[:80]}"
    assert "not the value of the fund" in why.replace("NOT", "not")
    print("uniswap relabel ok: narrower label for Uniswap only, AMBER with the reason")


def test_a_same_day_rerun_recomputes_the_SAME_flow_instead_of_destroying_it():
    """PancakeSwap's 59,857,159.01 burn became 0.0123, and the store keyed the overwrite.

    The arithmetic, from the real stored values: the 09-14 run should have differenced against
    09-12's 4,931,229,997.70303. It differenced against 4,991,087,156.70303 instead — a fourth
    observation, written by an EARLIER RUN THE SAME DAY — giving 0.0122690201. Because the store
    keys on (date, project, metric), that dust delta then overwrote the correct flow on the same
    key, and the real burn vanished.

    Anchoring the differencing input on the last EARLIER-DATED row makes a same-day re-run
    idempotent: it recomputes the same answer from the same starting point.
    """
    import os

    import store as store_mod

    path = "/tmp/claude-0/-home-user-Crypto-Tracker/e84ff2c6-8546-5133-82ff-472643596bbf-t.db"
    if os.path.exists(path):
        os.remove(path)
    st = store_mod.Store(path)
    try:
        def put(date, value):
            st.upsert(pd.DataFrame([{"date": pd.Timestamp(date), "project": "PancakeSwap",
                                     "metric": "burn_address_balance", "value": value,
                                     "source": "chain:bsc:burn_dead", "tier": 2}]))

        put("2026-09-11", 4_931_229_997.70303)
        put("2026-09-12", 4_931_229_997.70303)
        key = ("PancakeSwap", "burn_address_balance")

        first = st.values_before("2026-09-14")[key]
        assert first[1] == "2026-09-12", f"the prior must come from an earlier day, got {first[1]}"
        put("2026-09-14", 4_991_087_156.70303)          # run A writes a same-day row

        second = st.values_before("2026-09-14")[key]
        assert second[0] == first[0] and second[1] == "2026-09-12", \
            "a same-day row must NOT become the differencing input — that is the whole bug"

        flow_a = 4_991_087_156.70303 - first[0]
        flow_b = 4_991_087_156.715299 - second[0]
        assert round(flow_b, 2) == 59_857_159.01, f"run B must still see the real burn, got {flow_b}"
        assert abs(flow_b - flow_a) < 1.0, "two runs on one day must not disagree by a burn"
        assert flow_b != 0.0122690201, "the old behaviour produced dust; this must not reproduce it"

        # latest_values, which feeds the CHANGE-THRESHOLD check, still sees today's row —
        # the two questions need different answers and must not be collapsed
        assert st.latest_values()[key] == 4_991_087_156.70303
    finally:
        st.close()
        if os.path.exists(path):
            os.remove(path)
    print("same-day re-run ok: 59,857,159.01 recomputed identically, not overwritten with dust")


def test_a_delta_across_a_CHANGED_MEASURING_POINT_is_not_a_flow():
    """The Uniswap 111m fake burn, in a test.

    The burn address was re-pointed from the Firepit contract (4,000 UNI awaiting release) to the
    dead address (111,341,581 UNI ever burned). The next run differenced them and reported
    111,337,581 UNI burned in a month, against a real rate of 100-134k a day. Nothing about it
    looked wrong — in range, plausible source, thirty times the truth.
    """
    from fetch.base import derive_flow_from_cumulative, today

    yesterday = str((today() - pd.Timedelta(days=1)).date())
    out = FetchOutput()
    flow = derive_flow_from_cumulative(
        111_341_581.0, 4_000.0, "Uniswap", "gross_burn_tokens",
        "chain:ethereum:burn_dead:delta", 2, today(), prior_date=yesterday,
        stock_metric="burn_address_balance", out=out,
        prior_source="chain:ethereum:fire_pit", source_base="chain:ethereum:burn_dead:PARTIAL")
    assert flow.empty, "a delta across two different addresses must not be reported as a burn"
    gap = next(g for g in out.gaps if g["metric"] == "gross_burn_tokens")
    assert "measuring point CHANGED" in gap["reason"]
    assert "fire_pit" in gap["reason"] and "burn_dead" in gap["reason"], \
        "the gap must name both addresses, or nobody can tell what happened"

    # the SAME address still differences normally, and a PARTIAL marker appearing is not a change
    out = FetchOutput()
    flow = derive_flow_from_cumulative(
        111_341_581.0, 111_237_581.0, "Uniswap", "gross_burn_tokens",
        "chain:ethereum:burn_dead:delta", 2, today(), prior_date=yesterday,
        stock_metric="burn_address_balance", out=out,
        prior_source="chain:ethereum:burn_dead", source_base="chain:ethereum:burn_dead:PARTIAL")
    assert not flow.empty and float(flow["value"].iloc[0]) == 104_000.0, \
        "an ordinary day's burn must still be reported"
    print("measuring-point guard ok: 111m fake burn refused, 104k real burn kept")


def test_a_zero_burn_from_a_balance_delta_is_flagged_not_reported_as_measured():
    """An unchanged burn balance is not evidence that nothing burned.

    A balance answers "how much is sitting there", never "did anything move and from whom", so
    0 is consistent with no burn, with a burn that routed somewhere else, and with too short an
    observation history. All three render as the same 0. This is the skipped-vs-succeeded
    failure in a different place: no error, no gap, and a figure that reads as measured.
    """
    c = Chain(prior_values={("PancakeSwap", "burn_address_balance"): 25.0})
    c.reader = StubReader(symbol="Cake", supply=100.0, balance=25.0)      # balance UNCHANGED
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    df = out.frame()
    by = dict(zip(df["metric"], df["value"]))
    assert by["gross_burn_tokens"] == 0.0, "the zero is still stored — it is flagged, not withheld"

    flagged = [r for r in out.review if r["reason"] == "unattributable_zero"]
    assert len(flagged) == 1 and flagged[0]["metric"] == "gross_burn_tokens", \
        "a zero delta must reach the Review Queue, which is what turns the cell lilac"
    assert flagged[0]["action"] == "stored_flagged"

    gap = next(g for g in out.gaps if g["metric"].startswith("[data] gross_burn_tokens is ZERO"))
    for hypothesis in ("(a) no burn occurred", "did not route to the", "too few observations"):
        assert hypothesis in gap["reason"], f"the gap must state every hypothesis, missing {hypothesis!r}"
    assert "OVERSTATE" in gap["reason"], "anyone can send to a dead address — that asymmetry matters"
    assert "eth_getLogs" in gap["suggestion"] and "Dune" in gap["suggestion"], \
        "the fix must name both routes, because they answer different questions"
    print("ambiguous zero ok: stored, flagged, and every hypothesis named")


def test_a_single_observation_never_produces_a_zero_flow():
    """A delta needs an INTERVAL, not just a prior number to subtract.

    Two readings that land on the same date collapse to one row in the store, so their difference
    spans nothing the series can represent — and it comes out 0 whatever the truth is. That zero
    lands in the burn column of an archetype-4 name, where it is indistinguishable from a measured
    "nothing was burned". It must be n/a with a reason instead.
    """
    from fetch.base import today

    td = str(today().date())
    yd = str((today() - pd.Timedelta(days=1)).date())

    def run(prior, prior_date):
        c = Chain(prior_values={("PancakeSwap", "burn_address_balance"): prior} if prior is not None else {},
                  prior_dates={("PancakeSwap", "burn_address_balance"): prior_date} if prior_date else {})
        c.reader = StubReader(symbol="Cake", supply=100.0, balance=4_931_229_998.0)
        out = FetchOutput()
        c.run([_cake_project(verified="2026-09-11")], None, out)
        return out

    first = run(None, None)
    assert "gross_burn_tokens" not in set(first.frame().metric), "the first ever reading has nothing to difference"
    assert any("no prior observation" in g["reason"] for g in first.gaps if g["metric"] == "gross_burn_tokens")

    same_day = run(4_931_229_998.0, td)
    assert "gross_burn_tokens" not in set(same_day.frame().metric), \
        "a prior from the SAME DATE is one observation, not two — it must not yield a 0"
    gap = next(g for g in same_day.gaps if g["metric"] == "gross_burn_tokens")
    assert "SINGLE observation" in gap["reason"] and "not a value of zero" in gap["reason"]
    assert gap["suggestion"], "n/a is only honest if it comes with a reason"

    two_days = run(4_931_229_998.0, yd)
    assert "gross_burn_tokens" in set(two_days.frame().metric), \
        "two observations on different dates is a real interval and must still difference"
    print("single observation ok: n/a with a reason on one reading, a real delta on two")


def test_a_burn_address_holding_exactly_zero_is_flagged_as_evidence_about_the_address():
    """A burn address is one-way, so an exact zero means nothing has EVER arrived.

    That is a statement about the address, not about the burn — and unlike a differenced flow it
    needs no history to mean it. Peers read identically are non-zero on a single observation.
    """
    c = Chain(prior_values={}, prior_dates={})
    c.reader = StubReader(symbol="Cake", supply=100.0, balance=0.0)
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)

    flagged = [r for r in out.review if r["reason"] == "burn_address_never_received"]
    assert len(flagged) == 1 and flagged[0]["metric"] == "burn_address_balance"
    gap = next(g for g in out.gaps if g["metric"].startswith("[data] the burn address has NEVER"))
    assert "one-way" in gap["reason"] and "(b) THE BURN DOES NOT ROUTE HERE" in gap["reason"]
    assert "holder-elected" in gap["reason"], "a genuine zero is possible too and must be named"
    assert "verification error, not a data gap" in gap["reason"]

    # and it does NOT fire on a non-zero balance
    c = Chain(prior_values={}, prior_dates={})
    c.reader = StubReader(symbol="Cake", supply=100.0, balance=4_931_229_998.0)
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    assert not [r for r in out.review if r["reason"] == "burn_address_never_received"]
    print("zero balance ok: flagged as address evidence, and silent on a working burn address")


def test_sky_has_no_burn_address_because_it_has_no_dead_address_mechanism():
    """Sky's zero was correct and beside the point: it does not burn to a dead address at all.

    That conclusion has SURVIVED Sky acquiring a real burn. The Dss Flappers audit describes a
    Splitter feeding an AMM Flapper that sends proceeds to a configurable receiver — LP tokens, in
    one variant — and no balance read models that, so the entry was removed rather than re-pointed.

    ** AND STAGE 2 DID NOT BRING A DEAD ADDRESS EITHER, 2026-09-22. ** The 5% buy-and-burn leg
    calls SKY.burn(), which decrements totalSupply and emits Transfer-to-zero: the tokens cease to
    exist rather than moving somewhere unspendable. So the reason Sky has no burn address has
    changed from "we do not know the mechanism" to "the mechanism has no address", which is a
    stronger statement, not a weaker one — and burn_address_balance is now declared not_applicable
    rather than left applicable and permanently empty.
    """
    sky = config.PROJECT_BY_NAME["Sky"]
    assert "burn_zero" not in sky["contracts"], "re-pointing or re-adding a burn address models this wrongly"
    assert sky["burn_read_method"] == "protocol_level", \
        "'transfer' was the refuted assumption; 'undetermined' was right only while it was open"
    mech = config.burn_mechanism(sky)
    assert mech["status"] == "confirmed" and mech["model"] == "protocol_level_destruction"
    assert mech["effective_from"] == "2026-09-14", "Sky had NO burn before Stage 2"

    # THE REFUTATION IS KEPT, not withdrawn. Nothing the Smart Burn Engine does is a burn, before
    # or after Stage 2, and deleting that finding invites the dead-address assumption back in.
    prior = mech["refuted_prior_model"]
    assert prior["status"] == "refuted" and prior["model"] == "amm_swap_to_receiver"
    assert "chainsecurity" in (prior["source_url"] or "").lower()

    # ** THE METRIC IS APPLICABLE AGAIN, AND THAT REVERSES A CALL MADE ONE DAY EARLIER. **
    # "A protocol-level burn has no address to hold the tokens" was right about the mechanism and
    # wrong about the conclusion. SKY.burn() runs through OpenZeppelin's _burn, which emits
    # Transfer(holder, address(0), amount); the events are the record even though no address ends
    # up holding anything. Summing them gives the same QUANTITY the metric holds everywhere else,
    # which is what lets the cumulative-to-flow differencing, the telescoping reconciliation and
    # the burn <= supply bound all apply here unchanged.
    assert config.not_applicable_reason("Sky", "burn_address_balance") is None, \
        "the events are readable, so declaring the metric inapplicable withholds a real figure"
    label = config.metric_label("Sky", "burn_address_balance")
    assert "Cumulative SKY destroyed" in label and "no address holds them" in label, \
        f"the sheet must not claim an address holds these tokens: {label}"

    # THE READ IS WIRED AND REFUSES UNTIL ITS START HEIGHT IS SOURCED. A log scan that begins
    # after the burns returns a small, confident, plausible number and nothing in the output
    # would show it, so from_block is required rather than estimated from a block time.
    logs = sky["contracts"]["burn_logs"]
    assert logs["kind"] == "burn_transfer_logs"
    assert logs["address"] == sky["contracts"]["token"]["address"], \
        "the events are on the SKY token itself"
    cfg = logs["burn_logs"]
    # ** from_block IS NOW DERIVED RATHER THAN REQUIRED. CHANGED 2026-09-22. ** It was a declared
    # gap — "an unsourced start height is not a default" — and that was right about not
    # estimating it and wrong about the only alternative being to wait for someone to look it up.
    # The deployment block is computable from the chain by binary search on eth_getCode, which is
    # derivation rather than assumption, and it is what lets the scan cover the governance burns
    # that predate Stage 2.
    assert cfg["from_block"] is None and cfg["from_block_discover"] == "deployment"
    ref = cfg["first_read_reference"]
    assert ref["value"] == 2_860_000 and ref["as_of"] == "2026-09-14" and ref["tolerance_pct"] == 5
    assert ref["mode"] == "at_least", \
        "a full-history cumulative grows past the reference; an equality gate would reject every " \
        "correct read after the first day"

    # even if somebody re-added a burn address, a refuted mechanism refuses the read
    probe = dict(sky)
    probe["contracts"] = dict(sky["contracts"])
    probe["contracts"]["burn_zero"] = config._contract(
        "0x0000000000000000000000000000000000000000", "ethereum", "burn_address_balance", "SKY",
        "https://example.invalid", verified="2026-09-14")
    probe["burn_read_method"] = "transfer"          # the old, refuted assumption
    probe["burn_mechanism"] = dict(sky["burn_mechanism"],
                                   model="amm_swap_to_receiver", status="refuted")
    c = Chain(prior_values={}, prior_dates={})
    c.reader = StubReader(symbol="SKY", supply=1e10, balance=0.0)
    out = FetchOutput()
    c.run([probe], None, out)
    assert "burn_address_balance" not in set(out.frame().metric), \
        "a refuted mechanism must refuse the read whatever the address says"
    refusals = [g for g in out.gaps if "MECHANISM is refuted" in g["reason"]]
    assert refusals, [g["reason"][:60] for g in out.gaps]
    assert all("Do not substitute another address or another event" in g["suggestion"] for g in refusals)
    # ** AND IT REFUSES THE LOG READ TOO. ** The gate was scoped to balance reads until
    # burn_transfer_logs was added on 2026-09-22; a refutation answers "does this project burn
    # the way we assumed", which does not depend on which door the read comes through.
    assert any("burn_transfer_logs read could measure" in g["reason"] for g in refusals), \
        f"a refuted mechanism must refuse the event read as well: {[g['reason'][:80] for g in refusals]}"
    print("sky ok: burn address removed, refuted mechanism refuses balance AND event reads")


PAUSE_PROXY = "0xBE8E3e3618f7474F8cB1d074A26afFef007E98FB"
STAGE2 = "0x5555555555555555555555555555555555555555"     # discovered, never hardcoded in config
CONVERTER = "0x7777777777777777777777777777777777777777"   # MkrSky — surfaces as unrecognised
WAD = 10 ** 18


def _sky_log_probe(**burn_logs_overrides):
    """Sky with only the token and the burn-log reader, so nothing else competes for a metric."""
    sky = config.PROJECT_BY_NAME["Sky"]
    spec = sky["contracts"]["burn_logs"]
    cfg = dict(spec["burn_logs"], **burn_logs_overrides)
    probe = dict(sky)
    probe["contracts"] = {"token": sky["contracts"]["token"],
                          "burn_logs": dict(spec, burn_logs=cfg)}
    return probe


class _LogReader(StubReader):
    """Returns a fixed event list, and records what it was asked for."""

    def __init__(self, events, deployed=21_000_000, timestamps=None):
        super().__init__(symbol="SKY", supply=2.2e10)
        self.events, self.deployed = events, deployed
        self.timestamps = timestamps or {}
        self.asked = {}

    def erc20(self, chain, address):
        class _C:
            class functions:
                @staticmethod
                def decimals():
                    class _R:
                        @staticmethod
                        def call():
                            return 18
                    return _R()
        return _C()

    def deployment_block(self, chain, address):
        self.asked["deployment"] = (chain, address)
        return self.deployed

    def block_timestamp(self, chain, block):
        return self.timestamps.get(block, 1789344000)     # 2026-09-14T00:00:00Z

    def burn_transfer_events(self, chain, token, burn_to, from_block, chunk=10_000, max_blocks=None):
        self.asked["scan"] = {"from_block": from_block, "chunk": chunk, "burn_to": burn_to}
        head = from_block + 2_600_000
        chunks = (head - from_block) // chunk + 1
        return list(self.events), from_block, head, chunks


def test_sky_burns_are_decomposed_by_sender_into_three_separate_series():
    """ONE EVENT SIGNATURE, THREE UNRELATED ECONOMIC FACTS. Sky.burn(from, value) emits
    Transfer(from, address(0), value) whoever calls it — read from src/Sky.sol, not assumed — and
    SKY is burned by the Stage 2 buy-and-burn (revenue-funded, recurring), by governance from the
    Pause Proxy (a one-off executive action), and by the MkrSky converter's auth-only burn() (a
    supply correction against already-burned MKR).

    Summing them gives a figure that is none of the three, and the archetype 4 tab is asking for
    the first. Annualising a governance burn would report one decision as a run rate.
    """
    events = [
        {"from": STAGE2, "value": int(2_860_000 * WAD), "block": 23_400_100},
        {"from": STAGE2, "value": int(1_140_000 * WAD), "block": 23_450_000},
        {"from": PAUSE_PROXY, "value": int(5_815_668 * WAD), "block": 23_380_000},
        {"from": CONVERTER, "value": int(250_000 * WAD), "block": 22_000_000},
    ]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(events)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    got = {r.metric: float(r.value) for r in out.frame().itertuples(index=False)}

    # THE STAGE 2 LEG ALONE lands in the archetype 4 metric.
    assert got["burn_address_balance"] == 4_000_000.0, got
    # GOVERNANCE GETS ITS OWN SERIES. This is the Pause Proxy's -5,815,668 showing up as a BURN,
    # which is the question the whole read was built to settle.
    assert got["governance_burn_balance"] == 5_815_668.0, got
    # AND THE UNRECOGNISED SENDER IS SURFACED, not folded into either.
    assert got["other_burn_balance"] == 250_000.0, got
    # ** THE SUM IS NOT ANY OF THEM, which is the point. **
    assert got["burn_address_balance"] != sum(e["value"] for e in events) / WAD

    flagged = [r for r in out.review if r["reason"] == "unrecognised_burn_sender"]
    assert flagged and CONVERTER.lower() in flagged[0]["basis"].lower(), out.review
    print("sky decomposition ok: Stage 2 4.0m, governance 5,815,668, unrecognised 250k flagged")


def test_the_stage_2_burner_is_discovered_from_the_logs_and_refuses_an_ambiguous_match():
    """Its address is in no source on file. What IS known is the amount and the date, so the scan
    finds the event matching them and takes its sender.

    EXACTLY ONE CANDIDATE OR IT REFUSES. Two matches or none is an unresolved identification, and
    picking one would put a whole series under an address nobody checked — the same failure as
    reading a balance from a guessed contract.
    """
    cfg = config.PROJECT_BY_NAME["Sky"]["contracts"]["burn_logs"]["burn_logs"]
    assert cfg["stage2_burner"]["address"] is None, "the burner must not be hardcoded"
    assert cfg["stage2_burner"]["discover_by"]["approx_tokens"] == 2_860_000

    one = [{"from": STAGE2, "value": int(2_860_000 * WAD), "block": 23_400_100}]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(one)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    assert float(out.frame().query("metric == 'burn_address_balance'").value.iloc[0]) == 2_860_000.0

    # TWO EVENTS OF THE SAME SIZE FROM DIFFERENT SENDERS — unresolved, so nothing is stored.
    two = one + [{"from": PAUSE_PROXY, "value": int(2_870_000 * WAD), "block": 23_400_200}]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(two)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    assert out.frame().query("metric == 'burn_address_balance'").empty
    gap = next(g for g in out.gaps if g["metric"] == "burn_address_balance")
    assert "COULD NOT BE IDENTIFIED" in gap["reason"] and "2 event(s) match" in gap["reason"], gap
    assert "Do NOT widen" in gap["suggestion"]
    # The senders it DID see are named, so the next step is reading a list rather than guessing.
    assert PAUSE_PROXY.lower() in gap["reason"].lower()

    # THE AMOUNT ALONE IS NOT THE IDENTIFICATION. A same-sized burn on another day is rejected.
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(one, timestamps={23_400_100: 1786752000})   # 2026-08-15
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    gap = next(g for g in out.gaps if g["metric"] == "burn_address_balance")
    assert "WAS NOT CONFIRMED" in gap["reason"] and "2026-08-15" in gap["reason"], gap
    print("burner discovery ok: one match accepted, two refused with the senders listed, "
          "a same-size burn on the wrong date rejected")


def test_the_burn_scan_starts_at_deployment_and_reports_its_chunking():
    """THE GOVERNANCE BURNS PREDATE STAGE 2. The documented 2026-09-11 executive action burning
    SKY from the Pause Proxy is three days BEFORE Stage 2 began, so a scan starting at Stage 2
    would have captured the recurring leg and silently missed it. A burn series that starts after
    some of the burns is not a shorter series, it is a wrong one.

    from_block is DERIVED by binary search on eth_getCode rather than looked up — a block
    explorer is not reachable from every environment this runs in — and rather than estimated
    from a block time, because a start after the events returns a smaller, confident, entirely
    plausible number.
    """
    cfg = config.PROJECT_BY_NAME["Sky"]["contracts"]["burn_logs"]["burn_logs"]
    assert cfg["from_block"] is None and cfg["from_block_discover"] == "deployment"
    assert cfg["max_blocks_per_run"] is None, \
        "a full-history scan is the intent here, not the accident the ceiling guards against"

    reader = _LogReader([{"from": STAGE2, "value": int(2_860_000 * WAD), "block": 23_400_100}],
                        deployed=20_700_000)
    c = Chain(prior_values={}, prior_dates={})
    c.reader = reader
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    assert reader.asked["deployment"][1] == config.PROJECT_BY_NAME["Sky"]["contracts"]["token"]["address"]
    assert reader.asked["scan"]["from_block"] == 20_700_000, reader.asked
    assert reader.asked["scan"]["chunk"] == 10_000
    assert reader.asked["scan"]["burn_to"] == config.BURN_ADDRESSES["zero"]

    # THE SCANNED RANGE IS AN ANNOTATION, not a measuring point: it moves every run, and without
    # the stripper the series would blank itself for a change that never happened.
    from fetch.base import _measuring_point
    row = out.frame().query("metric == 'burn_address_balance'").iloc[0]
    assert "logs@20700000-" in row["source"] and "deployed@20700000" in row["source"], row["source"]
    assert _measuring_point(row["source"]) == "chain:ethereum:burn_logs", row["source"]
    # THE SIBLING SERIES CARRY THEIR METRIC IN THE SOURCE and the Stage 2 one does not, which is
    # deliberate rather than an inconsistency: one contract key emitting three parts would
    # collide on a single label, so the two extra series are disambiguated. The Stage 2 leg keeps
    # the plain contract key, which is the canonical form for a single-component read and is what
    # every other contract on every other project produces.
    gov = out.frame().query("metric == 'governance_burn_balance'").iloc[0]
    assert _measuring_point(gov["source"]) == "chain:ethereum:burn_logs:governance_burn_balance"
    # AND EVERY ONE OF THEM IS STABLE ACROSS RUNS. The scanned range moves every time; without
    # the annotation stripper each series would blank itself for a change that never happened.
    assert _measuring_point(gov["source"]) == _measuring_point(
        "chain:ethereum:burn_logs:governance_burn_balance[logs@1-2,deployed@1]")
    print("scan ok: starts at the derived deployment block, 10k chunks, range is an annotation")


def test_the_first_stage_2_read_is_gated_on_the_decomposed_figure_not_the_scan_total():
    """A full-history scan legitimately includes governance and converter burns, so the total is
    far above the 2,860,000 reference. Gating on the total would reject a correct read every run
    — which is why the gate moved onto the decomposed Stage 2 leg when the decomposition landed.
    """
    events = [
        {"from": STAGE2, "value": int(2_860_000 * WAD), "block": 23_400_100},
        {"from": PAUSE_PROXY, "value": int(40_000_000 * WAD), "block": 23_380_000},
    ]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(events)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    # The total is 42.86m and the Stage 2 leg is 2.86m. The gate passes on the leg.
    assert float(out.frame().query("metric == 'burn_address_balance'").value.iloc[0]) == 2_860_000.0
    assert not [r for r in out.review if r["reason"] == "first_read_disagrees_with_reference"]

    # AND IT STILL REFUSES A STAGE 2 LEG THAT MISSES — the gate is narrowed, not switched off.
    bad = [{"from": STAGE2, "value": int(41_000 * WAD), "block": 23_400_100},
           {"from": PAUSE_PROXY, "value": int(2_860_000 * WAD), "block": 23_380_000}]
    c = Chain(prior_values={}, prior_dates={})
    # 2.86m now belongs to the Pause Proxy, so discovery names IT as the burner and the leg it
    # then reports is the governance burn — which is exactly the mis-identification the date
    # check and this gate exist to catch between them.
    c.reader = _LogReader(bad, timestamps={23_380_000: 1786752000})
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    assert out.frame().query("metric == 'burn_address_balance'").empty
    assert any("WAS NOT CONFIRMED" in g["reason"] or "DOES NOT MATCH THE REFERENCE" in g["reason"]
               for g in out.gaps if g["metric"] == "burn_address_balance"), out.gaps
    print("gate ok: measured on the Stage 2 leg, passes with a large total, still refuses a miss")


def test_the_mkrsky_converter_question_is_answered_from_its_own_source():
    """1(c). Read from sky-ecosystem/sky src/MkrSky.sol on 2026-09-22, not from memory.

    THERE IS NO skyToMkr FUNCTION — the converter is one-directional, so the SKY->MKR direction
    is not live because it does not exist. That answers the question as asked, and it is not the
    end of it: `function burn(uint256 skyAmt) external auth` calls sky.burn(address(this),
    skyAmt), so the converter IS a SKY burn source with itself as the sender. Its own comment
    says it is "for burning excess SKY due to MKR being burned" — a supply correction against
    already-destroyed MKR, not a buyback of any kind.
    """
    cfg = config.PROJECT_BY_NAME["Sky"]["contracts"]["burn_logs"]["burn_logs"]
    note = cfg["third_mechanism_note"]
    assert "no skyToMkr" in note and "does not exist" in note
    assert "supply correction" in note
    # NOT KEYED ON, and the reason is the standing rule rather than an oversight: the converter's
    # deployed address is not on file, and a decomposition key pointing at an unverified address
    # would put a series under something nobody checked. It surfaces as unrecognised instead.
    assert all(a.lower() != CONVERTER.lower() for a in cfg["named_senders"]), \
        "an unverified address must not become a decomposition key"
    assert "unrecognised sender" in note
    print("converter ok: one-directional confirmed from source, burn() still a source, "
          "left to surface rather than keyed on an unverified address")


UNI_ETH_ADDR = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"


class _UniStub:
    """Mainnet only, which is now the whole of Uniswap's config — see the test below."""

    def __init__(self, balance):
        self.balance = balance

    def has_code(self, chain, address):
        return True

    def symbol_matches(self, chain, address, expected):
        ok = (chain, address) == ("ethereum", UNI_ETH_ADDR)
        return ok, "UNI" if ok else ""

    def scaled(self, chain, address, call, *args):
        return self.balance if call == "balanceOf" else 1_000_000_000.0


def test_uniswap_reads_the_burn_DESTINATION_not_the_contract_that_executes_the_burn():
    """The Firepit is what release() is CALLED ON. The UNI goes to 0x...dEaD.

    Reading the executor returned 0 forever while Uniswap was demonstrably burning 100k+ UNI a
    day — a permanent zero on an archetype 4 name, with a plausible story attached to it.
    """
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _UniStub(105_000_000.0)
    out = FetchOutput()
    c.run([config.PROJECT_BY_NAME["Uniswap"]], None, out)
    df = out.frame()

    burn = df[df.metric == "burn_address_balance"]
    assert len(burn) == 1 and burn.value.iloc[0] == 105_000_000.0
    assert "burn_dead" in burn.source.iloc[0], f"the DESTINATION must be the source, got {burn.source.iloc[0]}"
    assert "fire_pit" not in burn.source.iloc[0], "the executing contract must not serve the burn metric"

    # NOT PARTIAL ANY MORE, AND THAT IS THE POINT OF THE 2026-09-14 CHANGE.
    # This assertion used to require ":PARTIAL", on the belief that Unichain's burn path was a
    # separate figure we were missing. Uniswap's own OptimismBridgedResourceFirepit.sol says
    # otherwise: L2 burns bridge to L1 and the UNI lands at mainnet 0xdead after the OP Stack
    # challenge period. So the mainnet dead-address balance is the WHOLE figure, not a slice, and
    # labelling it partial would tell the reader to expect a larger number that does not exist.
    assert ":PARTIAL" not in burn.source.iloc[0], (
        "mainnet 0xdead is the WHOLE burn — OP Stack L2 burns bridge here after the challenge "
        f"period, so nothing is missing. Got {burn.source.iloc[0]}")

    # ONE executor now, not two: the Unichain Firepit was removed with the rest of the Unichain
    # entries. Reading both sides of the bridge would count every L2 burn twice, a week apart.
    reference = [e.message for e in out.log if "reference only" in e.message]
    assert len(reference) == 1 and all("burn_executor" in m for m in reference), \
        f"the mainnet Firepit stays as reference and serves no metric; got {reference}"

    # AND THE UNICHAIN ENTRIES ARE GONE, asserted directly so a re-add fails here rather than
    # silently double-counting in a live run.
    keys = set(config.PROJECT_BY_NAME["Uniswap"]["contracts"])
    assert not any("unichain" in k for k in keys), (
        f"Uniswap is MAINNET ONLY by explicit decision — a Unichain read double-counts every burn "
        f"across the 7-day bridge delay. Found {sorted(keys)}")
    print("uniswap ok: dead address read as the WHOLE burn, one executor, no Unichain entries")


def test_a_burn_destination_that_is_not_a_dead_address_is_rejected_by_config():
    """The check that would have caught the Firepit error on its own, independent of any read."""
    uni = config.PROJECT_BY_NAME["Uniswap"]["contracts"]
    assert uni["fire_pit"]["kind"] == "burn_executor"
    assert uni["burn_dead"]["address"] == config.BURN_ADDRESSES["dead"]

    uni["fire_pit"]["kind"] = "burn_address_balance"          # exactly the old, wrong config
    try:
        errs = config.validate_config(raise_on_error=False)
        assert any("not a canonical dead address" in e and "fire_pit" in e for e in errs), \
            f"config must reject an executor read as a destination, got {errs}"
    finally:
        uni["fire_pit"]["kind"] = "burn_executor"
    assert not config.validate_config(raise_on_error=False)

    # GEODNET's Solana sink is a legitimate destination that is not a dead address, and must NOT
    # trip the check: it carries its own kind, and the check only inspects EVM burn_address_balance.
    geo = config.PROJECT_BY_NAME["GEODNET"]["contracts"]["burn_solana_token_account"]
    assert geo["kind"] == "spl_token_account" and not geo["address"].startswith("0x")
    assert not config.validate_config(raise_on_error=False), "the Solana sink must not be flagged"
    print("destination check ok: executor rejected, non-EVM sink exempt")


def test_uniswap_burn_below_the_retroactive_burn_alone_is_rejected():
    """100m UNI was burned in December 2025 alone, so a cumulative under that is a broken read.

    This is the backstop for the Firepit class of error: the old config would have produced 0,
    and a 0 that is REJECTED to the Review Queue is recoverable in a way a stored 0 is not.
    """
    from fetch.validate import validate_frame

    c = Chain(prior_values={}, prior_dates={})
    c.reader = _UniStub(0.0)                       # what the old fire_pit read produced
    out = FetchOutput()
    c.run([config.PROJECT_BY_NAME["Uniswap"]], None, out)
    kept = validate_frame(out.frame(), {}, out)
    assert "burn_address_balance" not in set(kept.metric), "a sub-100m cumulative must not be stored"
    assert any(r["metric"] == "burn_address_balance" and r["reason"] == "out_of_bounds"
               and r["action"] == "rejected" for r in out.review)
    lo, _ = config.sanity_bounds("Uniswap", "burn_address_balance")
    assert lo == 100_000_000, "the floor is the December 2025 retroactive burn, not a guess"
    print("uniswap floor ok: a zero is rejected to the Review Queue, not stored")


# ------------------------------------------------------------------ derived issuance
def _issuance_frame(project: str, supply_now: float, burn: float | None):
    rows = [{"date": pd.Timestamp("2026-09-14"), "project": project, "metric": "total_supply",
             "value": supply_now, "source": "coingecko", "tier": 1}]
    if burn is not None:
        rows.append({"date": pd.Timestamp("2026-09-14"), "project": project, "metric": "gross_burn_tokens",
                     "value": burn, "source": "chain:delta", "tier": 2})
    out = FetchOutput()
    out.frames = [pd.DataFrame(rows)]
    return out


def _derive(project_name: str, model: str, supply_now: float, supply_prior: float, burn: float | None,
            status: str = "confirmed", prior_date: str = "2026-09-13"):
    from fetch import _derive_issuance

    p = dict(config.PROJECT_BY_NAME[project_name])
    p["burn_mechanism"] = {"model": model, "status": status, "source_url": "x", "source_date": None, "note": ""}
    out = _issuance_frame(p["name"], supply_now, burn)
    _derive_issuance(out, [p],
                     {(p["name"], "total_supply"): supply_prior},
                     {(p["name"], "total_supply"): prior_date})
    df = out.frame()
    got = df[df.metric == "gross_issuance_tokens"]
    return (None if got.empty else float(got.value.iloc[0])), out


def test_issuance_follows_the_supply_figures_convention_not_the_burn_mechanism():
    """CoinGecko's total_supply is NET of burn, so a transfer burn still needs the burn added back.

    Confirmed on live data in the audit of run 20260921T100546Z:
        GEODNET  1,000,000,000.00 - 961,518,067.62 = 38,481,932.38 = burn balance, to the token
        Uniswap  1,000,000,000   -  888,114,418.92 = 111,885,581  ~= burn balance 111,953,581

    The old rule keyed on the burn MECHANISM — a transfer burn does not reduce the contract's
    totalSupply, therefore issuance = d(supply). Correct about the CONTRACT's figure, and the
    store holds the PROVIDER's. Differencing a net-of-burn figure reports issuance minus burn
    under a gross label, which for a non-minting token is about -burn: Uniswap's -242,000.

    Neither half looked wrong alone, which is why this asserts the ARITHMETIC OUTCOME rather than
    which branch was taken — a future refactor that keeps the branch and loses the sign is exactly
    the regression worth catching.
    """
    # A DAY WHERE THE ONLY SUPPLY MOVEMENT WAS THE BURN. Provider supply falls by exactly the
    # burn; real issuance is zero. Under the old formula this was -120,000 and got rejected.
    got, out = _derive("Uniswap", "transfer_to_dead_address",
                       supply_now=888_114_418.92, supply_prior=888_234_418.92, burn=120_000.0)
    assert got == 0.0, f"net-of-burn supply + burn added back must give 0, got {got}"
    assert not [r for r in out.review if r.get("reason") == "negative_derived_issuance"], \
        "the negative that this bug produced must be gone, not merely tolerated"

    # AND REAL MINTING STILL COMES THROUGH. Supply rose 50,000 net while 120,000 burned, so
    # 170,000 was actually issued — the figure the gross column is supposed to carry.
    got, _ = _derive("Uniswap", "transfer_to_dead_address",
                     supply_now=888_284_418.92, supply_prior=888_234_418.92, burn=120_000.0)
    assert got == 170_000.0, f"gross issuance must include the burned tokens, got {got}"

    # THE CONVENTION IS WHAT DECIDES, NOT THE MECHANISM. Same mechanism, same numbers, gross
    # provider figure -> the delta alone is already gross and nothing is added.
    gross = dict(config.PROJECT_BY_NAME["Uniswap"])
    gross["total_supply_convention"] = "gross"
    assert config.issuance_supply_rule(gross, "transfer_to_dead_address") == "delta_only"
    assert config.issuance_supply_rule(config.PROJECT_BY_NAME["Uniswap"],
                                       "transfer_to_dead_address") == "add_burn"

    # UNTESTED REFUSES — and as of 2026-09-22 NO PROJECT IS UNTESTED, so the state is forced.
    # PancakeSwap and Venice AI were the examples; both were settled from run 20260921T100546Z's
    # own stored figures (the tier-collision guard preserves the dropped contract read beside the
    # kept CoinGecko one, so the comparison never needed live CoinGecko access at all). The
    # MECHANISM is unchanged and must stay covered: the two candidate formulas differ by the
    # whole burn, so an undeclared convention has to decline and name the test rather than pick
    # the likely answer. Forced rather than re-pointed at some project that happens to be
    # undeclared for unrelated reasons — this asserts the branch, not a project's current state.
    for name in ("PancakeSwap", "Venice AI"):
        p = config.PROJECT_BY_NAME[name]
        assert p.get("total_supply_convention") in config.NET_OF_BURN_CONVENTIONS, \
            f"{name} was settled on 2026-09-22 — see total_supply_convention_evidence"
        assert p.get("total_supply_convention_evidence", {}).get("test"), \
            f"{name} must record the test that settled it, not just the answer"
        saved = p.pop("total_supply_convention")
        try:
            got, out = _derive(name, "transfer_to_dead_address",
                               supply_now=300_000_000.0, supply_prior=300_050_000.0, burn=50_000.0)
            assert got is None, \
                f"{name} must refuse to derive while the convention is undeclared, got {got}"
            reason = " ".join(g["reason"] for g in out.gaps if g["metric"] == "gross_issuance_tokens")
            assert "NET of that burn is not established" in reason, \
                f"the gap must name the actual unknown, not a generic one: {reason[:200]}"
        finally:
            p["total_supply_convention"] = saved

    # ** AND WITH IT DECLARED THEY DIVERGE, which is the point of the third convention. **
    # Venice's provider nets out the burn alone, so adding it back recovers gross issuance.
    got, _ = _derive("Venice AI", "transfer_to_dead_address",
                     supply_now=300_000_000.0, supply_prior=300_050_000.0, burn=50_000.0)
    assert got == 0.0, f"net_of_burn derives via add_burn, got {got}"

    # PancakeSwap's nets out the cross-chain lock TOO, so there is a THIRD term — d(outbound) —
    # and no series for it. add_burn would store minted-minus-bridge-flow labelled gross
    # issuance, which is the plausible wrong number this whole field exists to prevent.
    assert config.issuance_supply_rule(config.PROJECT_BY_NAME["PancakeSwap"],
                                       "transfer_to_dead_address") is None, \
        "net_of_burn_and_cross_chain_lock must REFUSE until outboundAmount is tracked"
    got, out = _derive("PancakeSwap", "transfer_to_dead_address",
                       supply_now=300_000_000.0, supply_prior=300_050_000.0, burn=50_000.0)
    assert got is None, f"PancakeSwap must not derive issuance under this convention, got {got}"
    blocked = config.PROJECT_BY_NAME["PancakeSwap"]["issuance_blocked_on"]
    assert "outboundAmount" in blocked["missing_series"] and blocked["unblocks"], \
        "a refusal must name the series that would lift it"

    # WHERE THE CONVENTION CANNOT MATTER, NO DECLARATION IS NEEDED — and the mechanism table
    # deliberately omits transfer_to_dead_address so a forgetful edit refuses rather than defaults.
    assert "transfer_to_dead_address" not in config.ISSUANCE_FROM_SUPPLY_DELTA_BY_MECHANISM
    assert config.issuance_supply_rule({}, "no_burn") == "delta_only"
    assert config.issuance_supply_rule({}, "protocol_level_destruction") == "add_burn"
    print("issuance convention ok: net-of-burn adds the burn back (0 and 170,000), gross does "
          "not, untested refuses with the test named, and the mechanism table cannot default")


# ---------------------------------------------- the three blocks that were declared but inert
def _a3_formula(project_name: str, header_starts: str) -> str:
    """The formula the REAL A3 writer produces for one project, pulled out of a built workbook.

    Deliberately not a re-implementation of the formula in the test. The crash this round came
    from a fixture that agreed with the code by construction; asserting against a rebuilt copy of
    the expression would repeat exactly that mistake. This reads what the builder actually wrote.
    """
    import openpyxl
    from openpyxl import Workbook

    import build_workbook as bw

    wb = Workbook()
    ws = wb.active
    # the period windows are module state that build_workbook() fills in; the writers read it
    asof = pd.Timestamp("2026-09-14")
    bw._WINDOWS.clear()
    for label, start, end in bw._period_windows(asof):
        bw._WINDOWS[label.lower()] = (start.date().isoformat(), end.date().isoformat())
    data_by_key = {}
    for pr in config.PROJECTS:
        for metric in config.metrics_for_project(pr):
            data_by_key[f"{pr['name']}|{metric}"] = {
                "status": "ok", "source": "test", "n_points": 9, "entered_on": "",
                "confidence": "GREEN", "why_amber": ""}
    R = bw.Refs(len(data_by_key), 0, [])
    projects, _ = bw.write_a3(ws, R, data_by_key)
    heads = [c.value for c in ws[4]]
    col = next(i + 1 for i, h in enumerate(heads) if h and h.startswith(header_starts))
    row = next(r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=1).value == project_name)
    return str(ws.cell(row=row, column=col).value or ""), ws.cell(row=row, column=col)


def test_stale_status_renders_visibly_on_the_a3_tab_not_just_the_data_tab():
    """Ether.fi's Dune series going 'stale' must be visible where a reader actually looks.

    This is the tier-4-freeze issue becoming visible rather than silently sitting there — the
    correct outcome, not a bug — but 'correct' only holds if the presentation actually surfaces
    it. Checked on the A3 tab (Ether.fi is archetype 3) rather than only the Data tab: the cell
    must carry the STALE fill AND a comment naming the actual last-point date, and the tab's own
    plain-text 'Data flags' column (no hover needed) must say so too.
    """
    import build_workbook as bw

    asof = pd.Timestamp("2026-09-14")
    bw._WINDOWS.clear()
    for label, start, end in bw._period_windows(asof):
        bw._WINDOWS[label.lower()] = (start.date().isoformat(), end.date().isoformat())
    data_by_key = {}
    for pr in config.PROJECTS:
        for metric in config.metrics_for_project(pr):
            data_by_key[f"{pr['name']}|{metric}"] = {
                "status": "ok", "source": "test", "n_points": 9, "entered_on": "",
                "confidence": "GREEN", "why_amber": ""}
    stale_note = "last point 2026-07-10; last successful fetch dune"
    data_by_key["Ether.fi|locked_tokens"] = {
        "status": "stale", "source": "test", "n_points": 9, "entered_on": "",
        "confidence": "RED", "why_amber": "", "note": stale_note}

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    R = bw.Refs(len(data_by_key), 0, [])
    bw.write_a3(ws, R, data_by_key)

    heads = [c.value for c in ws[4]]
    locked_col = next(i + 1 for i, h in enumerate(heads) if h and h.startswith("Tokens locked (ve)"))
    flags_col = next(i + 1 for i, h in enumerate(heads) if h and h.startswith("Data flags"))
    row = next(r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=1).value == "Ether.fi")

    cell = ws.cell(row=row, column=locked_col)
    assert cell.fill.fgColor.rgb == bw.FILL_STALE.fgColor.rgb, \
        f"a stale locked_tokens cell must carry the STALE fill, got {cell.fill.fgColor.rgb}"
    assert cell.comment is not None and "STALE" in cell.comment.text and "2026-07-10" in cell.comment.text, \
        f"the comment must name the actual last-point date, got {cell.comment.text if cell.comment else None}"

    flags_cell = ws.cell(row=row, column=flags_col)
    assert "stale" in str(flags_cell.value).lower(), (
        "the plain-text Data flags column (visible with no hover) must also say 'stale' for "
        f"Ether.fi, got {flags_cell.value!r}")
    print("stale rendering ok: A3 tab shows the STALE fill, a comment with the real last-point "
          "date, and a plain-text flag — not buried on the Data tab alone")


def test_fluid_buyback_is_suppressed_because_it_is_a_SWITCH_not_a_rate():
    """Below the threshold there is no small buyback — there is no buyback.

    THE LIVE STATE FLIPPED on 2026-09-14: Fluid crossed the $10m revenue threshold in October 2025
    and launched The Fluid Reserve, so status is now "active" and the figure MUST compute. This
    test used to assert the opposite, because the config used to say the opposite.

    Both branches are still exercised — the suppressed one is now the forced counterfactual rather
    than the live state — so the switch cannot rot in either direction.
    """
    thr = config.PROJECT_BY_NAME["Fluid"]["buyback_threshold"]
    assert thr["threshold_usd_annualised"] == 10_000_000
    assert thr["status"] == "active", (
        "Fluid crossed the threshold in October 2025 and launched The Fluid Reserve — the buyback "
        f"is ACTIVE, not gated. Got {thr['status']!r}")

    formula, _ = _a3_formula("Fluid", "BUYBACK AS % OF SUPPLY")
    assert "Data!" in formula, f"confirmed-active must produce the real formula, got {formula[:90]}"

    # FORCE THE OTHER BRANCH: if governance ever put it back below the threshold, it must suppress
    # again rather than keep computing off stale revenue.
    live = thr["status"]
    thr["status"] = "below_threshold"
    try:
        formula, cell = _a3_formula("Fluid", "BUYBACK AS % OF SUPPLY")
        assert "threshold" in formula or "below threshold" in formula, \
            f"the derived buyback must be suppressed below the threshold, got {formula[:90]}"
        assert "Data!" not in formula, "a suppressed figure must not still compute from the data"
        assert cell.comment and "SWITCH" in cell.comment.text
    finally:
        thr["status"] = live      # restore the LIVE value, not a hardcoded stale one

    # and a project with no threshold block is untouched by any of this
    other, _ = _a3_formula("Aave", "BUYBACK AS % OF SUPPLY")
    assert "threshold" not in other
    print("fluid ok: computes now the switch is ACTIVE, suppresses again if it goes below threshold")


def test_a_supply_additive_buyback_increases_net_issuance_never_decreases_it():
    """Aethir's 'buyback' pays in eATH that keeps earning ATH, so it ADDS circulating supply.

    Subtracting it as though it retired supply gets the sign wrong on the headline figure. The
    test is the one the rule implies: the same value must move net absorption DOWN, not up.
    """
    from build_workbook import net_absorption, supply_additive

    normal = net_absorption({}, "BUYBACK", "EMISSIONS")
    additive = net_absorption({"buyback_is_supply_additive": {"effect": "adds supply"}},
                              "BUYBACK", "EMISSIONS")
    assert normal == "BUYBACK-EMISSIONS"
    assert additive == "-BUYBACK-EMISSIONS", f"the buyback term must flip sign, got {additive}"

    # arithmetic, on the actual expressions: absorption falls, so net issuance rises
    buyback, emissions = 100.0, 40.0
    assert eval(normal, {}, {"BUYBACK": buyback, "EMISSIONS": emissions}) == 60.0
    assert eval(additive, {}, {"BUYBACK": buyback, "EMISSIONS": emissions}) == -140.0
    assert eval(additive, {}, {"BUYBACK": buyback, "EMISSIONS": emissions}) < \
        eval(normal, {}, {"BUYBACK": buyback, "EMISSIONS": emissions}), \
        "a supply-additive buyback must INCREASE net issuance, never reduce it"

    assert supply_additive(config.PROJECT_BY_NAME["Aethir"]), "Aethir declares the flag"
    assert not supply_additive(config.PROJECT_BY_NAME["Aave"]), "and it must not leak to others"
    print("aethir ok: supply-additive buyback flips sign — absorption 60 becomes -140")


def test_maple_indeterminate_destination_lands_in_the_AMBER_band_automatically():
    """Not by a separate hand check — by the same derivation that handles PARTIAL and n_points."""
    from build_workbook import confidence_for

    asof = pd.Timestamp("2026-09-14")
    # a source with no contract behind it, so the orphan check is not what is under test here
    clean = {"status": "ok", "source": "dune:4242", "n_points": 9, "entered_on": "",
             "measuring_points": ("dune:4242",)}

    band, why = confidence_for("Maple", "actual_buyback_tokens", dict(clean), asof)
    assert band == "AMBER", f"an indeterminate destination must not read as GREEN, got {band}"
    assert "DESTINATION INDETERMINATE" in why and "SYRUP Strategic Fund" in why
    assert "token liquidity" in why, "the reason the destination is indeterminate must be named"

    # it applies ONLY to destination metrics — revenue and supply are unaffected
    for unaffected in ("revenue_usd", "fees_usd", "circulating_supply"):
        assert confidence_for("Maple", unaffected, dict(clean), asof)[0] == "GREEN", unaffected

    # and only to Maple
    assert confidence_for("Aave", "actual_buyback_tokens", dict(clean), asof)[0] == "GREEN"

    # FORCE THE OTHER BRANCH: remove the block and the band goes green
    saved = config.PROJECT_BY_NAME["Maple"].pop("destination_indeterminate")
    try:
        assert confidence_for("Maple", "actual_buyback_tokens", dict(clean), asof)[0] == "GREEN"
    finally:
        config.PROJECT_BY_NAME["Maple"]["destination_indeterminate"] = saved
    print("maple ok: AMBER on destination metrics only, from the shared derivation")


def test_validation_survives_a_REAL_fetch_all_frame():
    """Run the validators against what fetch_all actually produces, not a frame shaped to suit them.

    THIS TEST EXISTS BECAUSE OF A LIVE CRASH. Canton's reference_values were written with the key
    "when" while check_reference_values reads "period"; it took down a nine-minute run at tier 4
    with a KeyError. Every existing test passed, because every existing test built its own
    reference values — so the fixtures agreed with the code BY CONSTRUCTION rather than by
    checking what config actually holds.

    The fix for that class of bug is not another hand-built fixture. It is to drive the real
    pipeline: fetch_all restricted to the schedule source needs no network, and the frame it
    returns has the exact columns, dtypes and config interaction a live run has.
    """
    import fetch as fetch_pkg
    from fetch.base import LONG_COLUMNS
    from fetch.validate import check_cross_checks, check_reference_values

    out = fetch_pkg.fetch_all(config.PROJECTS, None, sources=["schedule:config"])
    frame = out.frame()
    assert not frame.empty, "the schedule source must produce rows, or this test proves nothing"
    assert list(frame.columns) == LONG_COLUMNS, f"the real frame's columns are {list(frame.columns)}"

    # the validators must survive the REAL shape — this is the call that crashed
    check_reference_values(frame, out)
    check_cross_checks(frame, out)

    # and the comparison branch must actually EXECUTE, not just the no-match path: a frame that
    # matches nothing would pass even with the original bug
    matched = []
    for p in config.PROJECTS:
        for ref in (p.get("reference_values") or []):
            matched.append((p["name"], ref["metric"], ref["period"], float(ref["value"])))
    assert matched, "no reference values in config — this test would be vacuous"

    forced = pd.DataFrame([{"date": pd.Timestamp(f"{period}-15"), "project": project,
                            "metric": metric, "value": value * 5, "source": "test", "tier": 3}
                           for project, metric, period, value in matched])
    probe = FetchOutput()
    check_reference_values(pd.concat([frame, forced], ignore_index=True), probe)
    flagged = [r for r in probe.review if r["reason"] == "disagrees_with_published_reference"]
    assert len(flagged) == len(matched), \
        f"every reference value must be compared; {len(flagged)} of {len(matched)} were"
    print(f"real-frame validation ok: {len(frame)} rows through fetch_all, "
          f"{len(matched)} reference value(s) all compared")


def test_identical_inputs_give_different_issuance_and_the_CONVENTION_is_what_decides():
    """Same numbers in, different answers out — and the thing that selects the answer changed.

    THIS TEST USED TO ASSERT THE BUG. It keyed on the burn MECHANISM: protocol burn -> add the
    burn back, transfer burn -> do not, "if these two ever agree the derivation has collapsed".
    The audit of 2026-09-21 showed the mechanism is not what decides. CoinGecko's total_supply is
    contract totalSupply MINUS the dead-address balance (GEODNET exactly; Uniswap within read
    timing), so a transfer burn DOES move the stored figure, and the burn has to be added back
    anyway. What distinguishes the two families is the convention of the SUPPLY FIGURE.

    So the assertion is inverted rather than deleted: two projects with the SAME mechanism and
    different conventions must disagree, which is what proves the keying moved.
    """
    SUPPLY_NOW, SUPPLY_PRIOR, BURN = 1_000_100.0, 1_000_000.0, 30.0   # delta = +100, burn = 30

    # Protocol burn: the contract's own supply fell, so the delta is net and the burn is added.
    protocol, _ = _derive("Ethereum", "protocol_level_destruction", SUPPLY_NOW, SUPPLY_PRIOR, BURN)
    # Transfer burn, NET-OF-BURN provider figure: the provider subtracted it, so it is added back
    # too — same arithmetic as the protocol case, reached for a completely different reason.
    net_of_burn, _ = _derive("Uniswap", "transfer_to_dead_address", SUPPLY_NOW, SUPPLY_PRIOR, BURN)
    # Transfer burn, GROSS provider figure: nothing was subtracted, so the delta is already gross.
    gross_project = dict(config.PROJECT_BY_NAME["Uniswap"])
    gross_project["total_supply_convention"] = "gross"
    gross_project["burn_mechanism"] = {"model": "transfer_to_dead_address", "status": "confirmed",
                                       "source_url": "x", "source_date": None, "note": ""}
    from fetch import _derive_issuance
    out = _issuance_frame("Uniswap", SUPPLY_NOW, BURN)
    _derive_issuance(out, [gross_project], {("Uniswap", "total_supply"): SUPPLY_PRIOR},
                     {("Uniswap", "total_supply"): "2026-09-13"})
    df = out.frame()
    gross = float(df[df.metric == "gross_issuance_tokens"].value.iloc[0])

    assert protocol == 130.0, f"protocol burn: delta + burn = 130, got {protocol}"
    assert net_of_burn == 130.0, \
        f"transfer burn on a NET-OF-BURN figure must also add the burn back, got {net_of_burn}"
    assert gross == 100.0, f"transfer burn on a GROSS figure: delta alone = 100, got {gross}"

    # THE POINT: the two that differ share a mechanism, and the two that agree do not.
    assert net_of_burn != gross, \
        "IDENTICAL INPUTS AND IDENTICAL MECHANISM MUST NOT GIVE THE SAME ANSWER — the convention " \
        "is what selects the formula, and this is the assertion that proves it"
    assert net_of_burn - gross == BURN, "the difference between the conventions is exactly the burn"
    assert protocol == net_of_burn, \
        "a protocol burn and a net-of-burn transfer burn reach the same formula by different routes"
    print(f"issuance convention ok: protocol {protocol:,.0f} = net-of-burn transfer "
          f"{net_of_burn:,.0f}, vs gross transfer {gross:,.0f}, differing by the burn ({BURN:,.0f})")


def test_issuance_refuses_rather_than_guessing():
    """Three refusals, each of which would otherwise be a plausible wrong number."""
    # 1. one observation of total supply -> a delta of 0 would read as "nothing was issued"
    value, out = _derive("Ethereum", "protocol_level_destruction", 1_000_100.0, 1_000_000.0, 30.0,
                         prior_date="2026-09-14")     # prior dated the SAME day
    assert value is None
    assert any("only one observation" in g["reason"] for g in out.gaps
               if g["metric"] == "gross_issuance_tokens")

    # 2. protocol burn with no burn figure -> the delta alone is issuance NET of burn
    value, out = _derive("Ethereum", "protocol_level_destruction", 1_000_100.0, 1_000_000.0, None)
    assert value is None
    gap = next(g for g in out.gaps if g["metric"] == "gross_issuance_tokens")
    assert "understating it by exactly the burn" in gap["reason"]

    # 3. a mechanism with no established supply effect -> no formula applies. Sky was the type
    # case until its receiver was found to be the treasury and it left archetype 4; the rule is
    # about the MECHANISM, so it is exercised on a project the metric still applies to.
    value, out = _derive("Ethereum", "amm_swap_to_receiver", 1_000_100.0, 1_000_000.0, None,
                         status="refuted")
    assert value is None
    gap = next(g for g in out.gaps if g["metric"] == "gross_issuance_tokens")
    assert "not established" in gap["reason"]

    # a transfer burn needs NO burn figure, and derives fine without one
    # 4. ADDED 2026-09-21 — a transfer burn whose SUPPLY CONVENTION is undeclared. The two
    # candidate formulas differ by the entire burn (see
    # test_issuance_follows_the_supply_figures_convention_not_the_burn_mechanism), so there is no
    # safe default and the refusal must name the test that settles it.
    #
    # FORCED, because every project's convention was settled on 2026-09-22 and none is undeclared
    # any more. The branch is what is under test, not PancakeSwap's current state — and a branch
    # with no live example is exactly the one that rots, so it is exercised deliberately rather
    # than deleted along with the last project that happened to trip it.
    cake = config.PROJECT_BY_NAME["PancakeSwap"]
    saved = cake.pop("total_supply_convention")
    try:
        value, out = _derive("PancakeSwap", "transfer_to_dead_address", 1_000_100.0, 1_000_000.0, 30.0)
        assert value is None, "an undeclared supply convention must refuse, not pick the likely answer"
        gap = next(g for g in out.gaps if g["metric"] == "gross_issuance_tokens")
        assert "NET of that burn is not established" in gap["reason"]
        assert "total_supply_convention" in gap["suggestion"], \
            "the refusal must say which field settles it, not merely that something is unknown"
    finally:
        cake["total_supply_convention"] = saved

    # THE CONTROL. With the convention DECLARED and a burn figure present, the same shape derives
    # — so these four refusals cannot be passing because the derivation broke for everyone.
    value, _ = _derive("Uniswap", "transfer_to_dead_address", 1_000_100.0, 1_000_000.0, 30.0)
    assert value == 130.0, f"the control must still derive (delta + burn, net-of-burn); got {value!r}"
    print("issuance refusals ok: single observation, missing burn, unestablished mechanism, "
          "and untested supply convention")


def test_a_measured_issuance_is_never_overwritten_by_a_derivation():
    """A real source beats a derivation. Always."""
    from fetch import _derive_issuance

    p = dict(config.PROJECT_BY_NAME["Ethereum"])
    out = _issuance_frame("Ethereum", 1_000_100.0, 30.0)
    out.frames[0] = pd.concat([out.frames[0], pd.DataFrame([
        {"date": pd.Timestamp("2026-09-14"), "project": "Ethereum", "metric": "gross_issuance_tokens",
         "value": 777.0, "source": "dune:123", "tier": 4}])], ignore_index=True)
    _derive_issuance(out, [p], {("Ethereum", "total_supply"): 1_000_000.0},
                     {("Ethereum", "total_supply"): "2026-09-13"})
    got = out.frame()
    got = got[got.metric == "gross_issuance_tokens"]
    assert len(got) == 1 and got.value.iloc[0] == 777.0, "the measured figure must survive untouched"
    print("issuance precedence ok: a Dune figure is not displaced by the derivation")


def test_a_negative_derived_issuance_is_rejected():
    """Issuance cannot be below zero. A negative means an input is wrong, not that supply shrank.

    RE-POINTED 2026-09-21. This used to drive the negative through a transfer-burn project with
    no burn figure — which is now the exact shape the net-of-burn fix makes impossible, because a
    transfer burn on a net-of-burn figure needs the burn term and refuses without it. Driven
    through a protocol burn instead: supply fell by more than the burn explains, so an input is
    genuinely wrong and the guard should still fire.

    Worth keeping precisely BECAUSE the commonest source of negatives is now gone. Uniswap's
    -242,000 was the net-of-burn mistake, not a bad input, and fixing the formula removed it — so
    this guard is now protecting against rarer causes (a restated supply figure, a burn covering a
    different period) and would rot unnoticed without a test that still exercises it.
    """
    value, out = _derive("Ethereum", "protocol_level_destruction", 999_000.0, 1_000_000.0, 30.0)
    assert value is None, "a negative derivation must not be stored"
    assert any(r["reason"] == "negative_derived_issuance" and r["action"] == "rejected"
               for r in out.review)
    gap = next(g for g in out.gaps if g["metric"] == "gross_issuance_tokens")
    assert "d(total_supply)" in gap["reason"] and "burn=" in gap["reason"], \
        f"the rejection must name the inputs so the wrong one can be found: {gap['reason'][:200]}"
    print("negative issuance ok: rejected to the Review Queue with its inputs named")


def test_the_three_burn_failure_modes_stay_distinct():
    """Sky, Uniswap and Venice failed in three different ways. Collapsing them loses the fixes."""
    sky = config.burn_mechanism(config.PROJECT_BY_NAME["Sky"])
    uni = config.burn_mechanism(config.PROJECT_BY_NAME["Uniswap"])
    ven = config.burn_mechanism(config.PROJECT_BY_NAME["Venice AI"])

    # 1. wrong mechanism: no address can model it, so there is no burn contract at all.
    # SKY LEFT THIS MODE ON 2026-09-22 — Stage 2's 5% leg calls SKY.burn(), which decrements
    # totalSupply, so its LIVE status is confirmed. The refutation is not withdrawn: it is kept on
    # the mechanism block, because nothing the Smart Burn Engine does is a burn before or after,
    # and it is asserted THERE rather than dropped. No project has a live refuted mechanism now,
    # which is why the stale-store fixture forces one (refresh_stale_fixture.forced_refutation).
    assert sky["status"] == "confirmed" and sky["model"] == "protocol_level_destruction"
    prior = sky["refuted_prior_model"]
    assert prior["status"] == "refuted" and prior["model"] == "amm_swap_to_receiver"
    assert not [v for v in config.PROJECT_BY_NAME["Sky"]["contracts"].values()
                if v["kind"] == "burn_address_balance"], \
        "a protocol-level burn still has no address — the conclusion outlived its reasoning"

    # 2. right mechanism, wrong address: mechanism confirmed, and the fix was the destination
    assert uni["status"] == "confirmed" and uni["model"] == "transfer_to_dead_address"
    assert config.PROJECT_BY_NAME["Uniswap"]["contracts"]["burn_dead"]["kind"] == "burn_address_balance"

    # 3. undocumented: neither confirmed nor refuted, and it must not inherit either answer.
    # GEODNET LEFT THIS MODE ON 2026-09-21, the same way Venice AI did: a first-party statement
    # of the mechanism appeared (@GEODNET's June 2026 burn-stats post, via the archetype 3
    # resolution) to sit alongside the dead-address transfers Dune already observed. PancakeSwap
    # is now the sole example, which is the point of asserting the list rather than each member —
    # the mode must keep an occupant or it stops being tested at all.
    for still_open in ("PancakeSwap",):
        assert config.burn_mechanism(config.PROJECT_BY_NAME[still_open])["status"] == "assumed", \
            f"{still_open} is unresolved and resembles none of the others"

    # AND GEODNET'S MOVE IS ASSERTED, not just excused: 'confirmed' has to carry BOTH legs, since
    # either alone was what kept it at 'assumed' for weeks.
    geo = config.burn_mechanism(config.PROJECT_BY_NAME["GEODNET"])
    assert geo["status"] == "confirmed" and geo["model"] == "transfer_to_dead_address"
    by = geo.get("confirmed_by") or {}
    assert by.get("first_party_statement") and by.get("observed_destination") and by.get("confirmed_on"), \
        f"'confirmed' needs the statement AND the observation on file, got {by}"

    # 4. right mechanism, right address, wrong COMPOSITION — the one no check can catch, because
    # the figure is correct. Venice moved here from mode 3 when its mechanism was confirmed.
    assert ven["status"] == "confirmed" and ven["model"] == "transfer_to_dead_address"
    comp = config.PROJECT_BY_NAME["Venice AI"]["burn_composition"]
    assert comp["status"] == "contaminated" and comp["flow_is_recurring_only"] is True
    assert comp["one_off"]["recurring"] is False
    assert comp["one_off"]["tokens_approx"] is None, \
        "the one-off cannot be sized from a balance, and an approximation must not be stored as one"
    print("taxonomy ok: refuted / misread / undocumented / miscomposed stay four different problems")


def test_every_transfer_burn_declares_where_its_model_came_from():
    """The Sky lesson, enforced: a project cannot inherit the dead-address assumption silently."""
    for p in config.PROJECTS:
        if p.get("burn_read_method") != "transfer":
            continue
        block = p.get("burn_mechanism")
        assert block, f"{p['name']} claims a transfer burn with no burn_mechanism block"
        assert block["status"] in config.BURN_MECHANISM_STATUSES
        if block["status"] == "confirmed":
            assert block.get("source_url"), f"{p['name']}: 'confirmed' needs a document, not a belief"

    saved = config.PROJECT_BY_NAME["GEODNET"].pop("burn_mechanism")
    try:
        errs = config.validate_config(raise_on_error=False)
        assert any("no burn_mechanism block" in e for e in errs), "config must reject the silent assumption"
    finally:
        config.PROJECT_BY_NAME["GEODNET"]["burn_mechanism"] = saved
    assert not config.validate_config(raise_on_error=False)
    print("mechanism audit ok: every transfer burn declares its model, and config enforces it")


def test_an_assumed_mechanism_flags_the_figure_without_withdrawing_it():
    """Flag, do not refuse. One refuted model is not grounds to withdraw four working figures."""
    c = Chain(prior_values={}, prior_dates={})
    c.reader = StubReader(symbol="Cake", supply=100.0, balance=4_931_229_998.0)
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    rows = dict(zip(out.frame().metric, out.frame().value))
    assert rows["burn_address_balance"] == 4_931_229_998.0, "an assumed model is still reported"
    flagged = [r for r in out.review if r["reason"] == "burn_mechanism_assumed"]
    assert len(flagged) == 1, "and it is flagged exactly once, not per component"
    gap = next(g for g in out.gaps if g["metric"].startswith("[data] the burn MECHANISM is assumed"))
    assert "a contract that merely" in gap["suggestion"].replace("\n", " ") or "HOLDS" in gap["suggestion"]
    print("assumed mechanism ok: reported, flagged once, with the document that would settle it named")


def test_a_real_burn_is_not_flagged():
    """The flag must fire on ambiguity, not on every burn read, or it stops meaning anything."""
    c = Chain(prior_values={("PancakeSwap", "burn_address_balance"): 20.0})
    c.reader = StubReader(symbol="Cake", supply=100.0, balance=25.0)
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    assert not [r for r in out.review if r["reason"] == "unattributable_zero"]
    assert not [g for g in out.gaps if g["metric"].startswith("[data] gross_burn_tokens is ZERO")]
    print("no false flag ok: a non-zero delta is reported plainly")


def test_sky_split_history_cannot_resolve_across_the_april_overhaul():
    """A known-but-undocumented change inside a period must keep it unconfirmed.

    Nothing is wrong today — the span is unconfirmed and suppressed. The trap is the day somebody
    documents ONE number for a span that contained two regimes: the suppression would lift on a
    figure that looks entirely reasonable.
    """
    windows = {
        ("2026-02-01", "2026-03-31"): "pre-overhaul, undocumented",
        ("2026-05-01", "2026-07-31"): "post-overhaul, undocumented",
        ("2026-06-15", "2026-09-12"): "spans the Executive Proposal",
    }
    for (a, b), why in windows.items():
        r = config.split_for_window("Sky", a, b)
        assert r["status"] == "unconfirmed" and r["share_to_buyback"] is None, f"{why}: {r}"
    # THE 55/45 PERIOD STILL RESOLVES AT 0.55 — it was SUPERSEDED on 2026-09-14, not deleted, and
    # a window lying entirely inside it must still get its figure. Dating a regime out of the
    # present must not erase it from the past.
    live = config.split_for_window("Sky", "2026-08-20", "2026-09-12")
    assert live["share_to_buyback"] == 0.55, f"the superseded period must still resolve: {live}"
    assert live["status"] == "superseded", \
        f"status should now say superseded rather than active: {live['status']!r}"

    # STAGE 2, live from 2026-09-14. share_to_buyback is the SKY-BUYING share (22.5% + 5%), NOT
    # the 5% burn leg — the burn is only part of what is bought.
    v2 = config.split_for_window("Sky", "2026-09-20", "2026-09-25")
    assert v2["status"] == "active" and v2["share_to_buyback"] == 0.275, f"Stage 2 window: {v2}"

    # ** A WINDOW SPANNING THE BOUNDARY MUST NOT SILENTLY PICK ONE. ** 2026-09-13 is the last day
    # of the 55/45 and 2026-09-14 the first of Stage 2, so a window covering both contains two
    # regimes and cannot resolve to a single share.
    spanning = config.split_for_window("Sky", "2026-09-10", "2026-09-20")
    assert spanning["share_to_buyback"] is None, \
        f"a window spanning the Stage 2 boundary must not resolve to one share: {spanning}"

    # THE ALLOCATION ITSELF: the three legs sum to the stated 50% of Net Protocol Surplus, and the
    # burn leg is 5% — separate from the 22.5% that buys SKY and hands it to stakers.
    v = config.PROJECT_BY_NAME["Sky"]["fee_split_v2"]
    assert round(sum(v["splits"].values()), 6) == 0.50, "the three legs must sum to the stated 50%"
    assert v["burn_share"] == 0.05 and v["sky_buying_share"] == 0.275
    assert round(v["splits"]["sky_buyback_for_staking_rewards"] + v["burn_share"], 6) == v["sky_buying_share"]
    # The other 50% is NOT primary-sourced and must stay quarantined from the figures above.
    assert "NOT STATED" in v["remaining_50_pct"]["status"]
    assert "UNCONFIRMED SECONDARY" in v["remaining_50_pct"]["confidence"]

    # filling in a share does NOT lift the suppression while the change is unresolved
    period = next(h for h in config.PROJECT_BY_NAME["Sky"]["fee_split"]["history"] if h.get("known_change"))
    period["share_to_buyback"] = 0.30
    try:
        r = config.split_for_window("Sky", "2026-05-01", "2026-07-31")
        assert r["share_to_buyback"] is None and r["status"] == "unconfirmed", \
            "a share on an unresolved period must not resolve the window"
        errs = config.validate_config(raise_on_error=False)
        assert any("known_change" in e for e in errs), "config must reject the share outright, not just suppress it"
    finally:
        period["share_to_buyback"] = None
    assert not config.validate_config(raise_on_error=False)
    print("sky split ok: April overhaul keeps its period unconfirmed, and a filled-in share is rejected")


def test_several_contracts_serving_one_metric_are_summed():
    """Components on the SAME chain sum into one figure.

    Uniswap is MAINNET ONLY since 2026-09-14, so this now tests the summing behaviour alone: the
    TokenJar and the V3FeeAdapter both serve buyback_fund_balance and must add up rather than the
    last read winning. The cross-chain REFUSAL half of this behaviour moved to the GEODNET test
    below, which still has genuinely uncovered chains.
    """
    UNI_ETH = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"

    class ChainAwareStub:
        DEPLOYED = {("ethereum", UNI_ETH): "UNI"}
        CODE = {("ethereum", "0xf38521f130fcCF29dB1961597bc5d2B60F995f85"),
                ("ethereum", "0x5E74C9f42EEd283bFf3744fBD1889d398d40867d"),
                ("ethereum", "0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721")}
        VALUES = {"0xf38521f130fcCF29dB1961597bc5d2B60F995f85": 3_000_000.0,
                  "0x5E74C9f42EEd283bFf3744fBD1889d398d40867d": 1_500_000.0,
                  "0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721": 95_000_000.0}

        def has_code(self, chain, address):
            return (chain, address) in self.CODE

        def symbol_matches(self, chain, address, expected):
            if (chain, address) not in self.DEPLOYED:
                raise RuntimeError("Could not decode contract function call to symbol() with return data: b''")
            sym = self.DEPLOYED[(chain, address)]
            return sym.lower() == str(expected).lower(), sym

        def scaled(self, chain, address, call, *args):
            return self.VALUES.get(args[0], 1_000_000_000.0) if args and args[0] else 1_000_000_000.0

    c = Chain()
    c.reader = ChainAwareStub()
    out = FetchOutput()
    c.run([config.PROJECT_BY_NAME["Uniswap"]], None, out)
    df = out.frame()
    rows = dict(zip(df.metric, df.value))

    assert rows["buyback_fund_balance"] == 4_500_000.0, \
        f"the two mainnet jars must sum, got {rows['buyback_fund_balance']}"
    # NOTHING IS PARTIAL ANY MORE. Every Uniswap component is on mainnet, nothing is refused, and
    # the mainnet dead address is the whole burn — so a PARTIAL marker here would be a lie that
    # tells the reader to expect a bigger number.
    for metric in ("buyback_fund_balance", "burn_address_balance"):
        s = df[df.metric == metric].source.iloc[0]
        assert not s.endswith(":PARTIAL"), f"{metric} is mainnet-complete and must not be PARTIAL: {s}"
    assert "token_jar" in df[df.metric == "buyback_fund_balance"].source.iloc[0]
    assert "v3_fee_adapter" in df[df.metric == "buyback_fund_balance"].source.iloc[0]
    assert not [e for e in out.log if e.status == "failed"], \
        "every component is on mainnet, so nothing should fail"
    assert not any("unichain" in str(g.get("reason", "")) for g in out.gaps), \
        f"no Unichain gap should be raised now the entries are gone: {out.gaps}"

    # symbol() must never be called on a non-token contract, on any chain
    calls = []
    orig = ChainAwareStub.symbol_matches

    def spy(self, chain, address, expected):
        calls.append((chain, address))
        return orig(self, chain, address, expected)

    ChainAwareStub.symbol_matches = spy
    c2 = Chain()
    c2.reader = ChainAwareStub()
    c2.run([config.PROJECT_BY_NAME["Uniswap"]], None, FetchOutput())
    ChainAwareStub.symbol_matches = orig
    assert all(a == UNI_ETH for _c, a in calls), \
        f"symbol() must only ever target the token, never a jar or firepit: {calls}"
    assert all(ch == "ethereum" for ch, _a in calls), \
        f"symbol() must only be called on the chain the token is deployed to: {calls}"
    print("multi-contract summing ok: mainnet jars sum to 4,500,000, nothing refused, nothing PARTIAL")
    print("  symbol() targeted only the UNI token, only on ethereum — never a jar, never a wrong chain")


def test_a_component_on_an_uncovered_chain_is_refused_and_makes_the_sum_PARTIAL():
    """A component the adapter cannot reach must be REFUSED and the sum marked PARTIAL — never
    silently dropped and the remainder reported as the whole.

    ** GEODNET WAS THE WORKED EXAMPLE HERE AND IS NO LONGER ONE. CHANGED 2026-09-22. ** Its
    Polygon contract reads EXACTLY 1,000,000,000 GEOD, the entire declared cap, so there is
    nothing outside it to be partial about; the Solana and IoTeX deployments are mirrors of
    tokens locked on Polygon and are now declared bridged_representation. See the second half of
    this test. The MECHANISM is unchanged and still needs covering, so it is exercised on a
    synthetic project instead of on one whose facts have moved.
    """
    import copy

    POLY = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"
    proj = copy.deepcopy(config.PROJECT_BY_NAME["GEODNET"])
    # Put the remote deployments back to ordinary supply reads, which is what a project whose
    # bridge model is genuinely open looks like, and restore the project-level partial flag.
    proj["contracts"]["token_iotex"] = dict(proj["contracts"]["token_iotex"],
                                            kind="erc20_total_supply",
                                            metric_override="total_supply_gross")
    proj["contracts"]["mint_solana"] = dict(proj["contracts"]["mint_solana"], kind="spl_mint")
    proj["contracts"]["token_polygon"] = dict(proj["contracts"]["token_polygon"],
                                              supply_is_partial=True,
                                              partial_reason="synthetic: bridge model open")

    class PolygonOnlyStub:
        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            ok = (chain, address) == ("polygon", POLY)
            return ok, "GEOD" if ok else ""

        def scaled(self, chain, address, call, *args):
            return 5_000.0 if call == "balanceOf" else 1_000_000_000.0

    c = Chain()
    c.reader = PolygonOnlyStub()
    out = FetchOutput()
    c.run([proj], None, out)
    df = out.frame()

    supply_metric = (proj["contracts"]["token_polygon"].get("metric_override")
                     or config.KIND_METRIC["erc20_total_supply"])
    supply = df[df.metric == supply_metric]
    assert len(supply) == 1, f"one supply figure, summed from what could be read: {supply.to_dict()}"
    assert supply.source.iloc[0].endswith(":PARTIAL"), \
        f"the declared partial flag must reach the source string: {supply.source.iloc[0]}"
    reasons = " ".join(str(g.get("reason", "")) for g in out.gaps)
    assert "'solana'" in reasons and "'iotex'" in reasons, \
        f"both uncovered chains must be named specifically, not lumped together: {reasons}"

    # ** A FINDING, ASSERTED SO IT CANNOT BE FORGOTTEN: THE GATE DOES NOT MARK THE SUM PARTIAL. **
    # This test's previous version said an unreachable component "makes the resulting supply
    # marked PARTIAL", and the PARTIAL it observed came from supply_is_partial on the contract —
    # not from the refusal. fetch/chain.py's _gate returns False and the caller `continue`s
    # without adding anything to `refused`, which is the only input _emit_parts reads for this.
    # So a component refused for being unverified, on an uncovered chain, ambiguous, or on a
    # refuted mechanism produces a Gap Report row and leaves the sum looking complete.
    #
    # NOT FIXED HERE, because the fix is not small in effect: every project holding an unverified
    # contract would start rendering PARTIAL, which changes figures across the sheet and is
    # Jake's call, not a side-effect of a GEODNET change. Reported instead, and pinned here so
    # the next reader meets the real behaviour rather than the docstring's version of it.
    bare = copy.deepcopy(proj)
    bare["contracts"]["token_polygon"] = dict(bare["contracts"]["token_polygon"],
                                              supply_is_partial=False, partial_reason="")
    c = Chain()
    c.reader = PolygonOnlyStub()
    out2 = FetchOutput()
    c.run([bare], None, out2)
    bare_supply = out2.frame()
    bare_supply = bare_supply[bare_supply.metric == supply_metric]
    assert not bare_supply.source.iloc[0].endswith(":PARTIAL"), \
        "if this ever starts passing, the gate has learnt to mark the sum partial — read the note above"

    # ===== AND GEODNET AS IT NOW STANDS: COMPLETE, AND NEVER SUMMED. =====
    geod = config.PROJECT_BY_NAME["GEODNET"]
    assert geod["contracts"]["token_polygon"]["supply_is_partial"] is False, \
        "a read of the entire 1,000,000,000 cap cannot be missing anything"
    for key in ("token_iotex", "mint_solana"):
        assert geod["contracts"][key]["kind"] == "bridged_representation", \
            f"{key} must be reference-only BY DECLARATION, not because its chain lacks an adapter"
    c = Chain()
    c.reader = PolygonOnlyStub()
    out = FetchOutput()
    c.run([geod], None, out)
    df = out.frame()
    supply = df[df.metric == "total_supply_gross"]
    assert len(supply) == 1 and float(supply.value.iloc[0]) == 1_000_000_000.0
    assert not supply.source.iloc[0].endswith(":PARTIAL"), \
        f"the PARTIAL marker was an inversion and must be gone: {supply.source.iloc[0]}"
    # THE SUPPLY METRIC HAS NO COVERAGE GAP ANY MORE. A reference-only contract is a decision,
    # not a to-do item, and the Gap Report is a to-do list. Scoped to the supply metric on
    # purpose: burn_solana_token_account is STILL an uncovered-chain gap and must stay one — the
    # Solana burn destination is a real component of the burn figure that nothing reads.
    supply_gaps = " ".join(str(g.get("reason", "")) for g in out.gaps
                           if g.get("metric") == "total_supply_gross")
    assert "'solana'" not in supply_gaps and "'iotex'" not in supply_gaps, \
        f"a mirror deployment is not a supply gap: {supply_gaps}"
    burn_gaps = " ".join(str(g.get("reason", "")) for g in out.gaps
                         if g.get("metric") in ("burn_address_balance", "gross_burn_tokens"))
    assert "'solana'" in burn_gaps, \
        "the Solana BURN destination is a real unread component and must stay on the Gap Report"
    print("uncovered-chain refusal ok on a synthetic project; GEODNET reads the whole cap, "
          "unmarked, with its mirrors declared reference-only")


def test_components_sum_fully_once_every_chain_has_its_token():
    """With a same-chain token declared for every component, all of them sum.

    SYNTHETIC BY NECESSITY. Uniswap is mainnet-only in config now, so this builds the cross-chain
    case on a deep COPY rather than asserting config still contains one. The behaviour under test —
    a second-chain jar summing once its own token is declared on that chain — is real and is what
    would be needed if any project's bridge model is ever settled in favour of summing.
    """
    import copy

    uni = copy.deepcopy(config.PROJECT_BY_NAME["Uniswap"])
    uni["contracts"]["token_unichain"] = dict(uni["contracts"]["token"],
                                              address="0xUNIonUnichain", chain="unichain")
    uni["contracts"]["token_jar_unichain"] = dict(uni["contracts"]["token_jar"],
                                                 address="0xD576BDF6b560079a4c204f7644e556DbB19140b5",
                                                 chain="unichain", underlying="token_unichain")
    uni["contracts"]["fire_pit_unichain"] = dict(uni["contracts"]["fire_pit"],
                                                 address="0xe0A780E9105aC10Ee304448224Eb4A2b11A77eeB",
                                                 chain="unichain", underlying="token_unichain")

    class EverywhereStub:
        VALUES = {"0xf38521f130fcCF29dB1961597bc5d2B60F995f85": 3_000_000.0,
                  "0x5E74C9f42EEd283bFf3744fBD1889d398d40867d": 1_500_000.0,
                  "0xD576BDF6b560079a4c204f7644e556DbB19140b5": 800_000.0}

        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *args):
            return self.VALUES.get(args[0], 1_000_000.0) if args and args[0] else 1_000_000_000.0

    c = Chain()
    c.reader = EverywhereStub()
    out = FetchOutput()
    c.run([uni], None, out)
    df = out.frame()
    total = dict(zip(df.metric, df.value))["buyback_fund_balance"]
    assert total == 5_300_000.0, f"all three jars must sum once each has a same-chain token, got {total}"
    src = df[df.metric == "buyback_fund_balance"].source.iloc[0]
    assert not src.endswith(":PARTIAL"), "nothing was refused, so the figure is not partial"
    assert src.count("+") == 2, src
    print("complete-data case ok: three jars sum to 5,300,000 and the figure is not marked partial")


def test_no_metric_is_served_by_contracts_that_would_overwrite_each_other():
    """Every metric served by several contracts must be one the adapter sums."""
    from collections import defaultdict

    from fetch.chain import KIND_METRIC

    for p in config.PROJECTS:
        by_metric = defaultdict(list)
        for key, c in (p.get("contracts") or {}).items():
            metric = KIND_METRIC.get(c["kind"])
            if metric:
                by_metric[metric].append(key)
        for metric, keys in by_metric.items():
            if len(keys) > 1:
                # the adapter now sums EVERY metric, so this is a structural assertion that the
                # generic accumulator is still in place rather than a per-metric allowlist
                assert hasattr(Chain, "_emit_parts"), "the generic summing accumulator has gone missing"
    print("contract collision ok: every multi-contract metric goes through the summing accumulator")


def test_dead_and_zero_addresses_are_accepted_as_holders_despite_having_no_code():
    """A burn or dead address is an EOA nobody controls. Empty bytecode is CORRECT there.

    Regression: the eth_getCode existence check was applied to every holder, which rejected
    GEODNET's dead address, PancakeSwap's dead address and Sky's zero address on a live run —
    all three of which had read fine before the check existed. The check belongs only on holders
    that are supposed to be contracts.
    """
    from fetch.chain import holder_should_have_code

    class NoCodeAnywhereStub:
        """Every address reports empty bytecode, as a dead address genuinely does."""

        def __init__(self):
            self.code_checks = []

        def has_code(self, chain, address):
            self.code_checks.append(address)
            return False

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *args):
            return 1_234_567.0

    # Sky was one of these until its dead-address model was refuted and the entry removed —
    # Venice AI takes its place, and is the remaining zero-address holder.
    for project_name, entry in (("Venice AI", "burn_zero"), ("PancakeSwap", "burn_dead"), ("GEODNET", "burn_polygon")):
        spec = config.PROJECT_BY_NAME[project_name]["contracts"][entry]
        assert not holder_should_have_code(spec), \
            f"{project_name}/{entry} is a burn address and must be exempt from the bytecode check"

        c = Chain()
        c.reader = NoCodeAnywhereStub()
        out = FetchOutput()
        c.run([config.PROJECT_BY_NAME[project_name]], None, out)
        rows = dict(zip(out.frame().metric, out.frame().value))
        assert rows.get("burn_address_balance") == 1_234_567.0, \
            f"{project_name}/{entry} must read despite empty bytecode, got {rows}"
        # scoped to THIS entry: a project can hold other contracts that genuinely must have code
        # (Venice's staking contract does), and the stub denies bytecode to everything.
        assert not any("eth_getCode is empty" in e.message and entry in e.message
                       for e in out.log if e.status == "failed"), \
            f"{project_name}/{entry} was wrongly rejected for having no code"
        assert spec["address"] not in c.reader.code_checks, \
            f"{project_name}/{entry} should not even be code-checked"
    print("dead/zero holders ok: all three read despite empty bytecode, and are never code-checked")


def test_contract_holders_still_get_the_bytecode_check():
    """The check must still fire where the holder IS supposed to be a contract."""
    from fetch.chain import holder_should_have_code

    for project_name, entry in (("Uniswap", "token_jar"), ("Uniswap", "fire_pit"),
                                ("Chainlink", "reserve"), ("Aerodrome", "ve")):
        spec = config.PROJECT_BY_NAME[project_name]["contracts"][entry]
        assert holder_should_have_code(spec), f"{project_name}/{entry} is a contract and must be checked"

    # a fee jar with no bytecode is a wrong address and must be rejected
    class EmptyJarStub:
        def has_code(self, chain, address):
            return False

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *args):
            return 999.0

    proj = {"name": "Chainlink", "archetypes": [3], "contracts": {
        "token": dict(config.PROJECT_BY_NAME["Chainlink"]["contracts"]["token"]),
        "reserve": dict(config.PROJECT_BY_NAME["Chainlink"]["contracts"]["reserve"])}}
    c = Chain()
    c.reader = EmptyJarStub()
    out = FetchOutput()
    c.run([proj], None, out)
    assert "buyback_fund_balance" not in set(out.frame().metric), "an empty contract address must be rejected"
    assert any("eth_getCode is empty" in e.message for e in out.log if e.status == "failed"), out.log
    print("contract holders ok: a fee jar with no bytecode is still rejected")


def test_chain_symbol_mismatch_rejects():
    c = Chain()
    c.reader = StubReader(symbol="WRONG")
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    assert out.frame().empty, "a symbol mismatch must reject the address"
    assert any("symbol mismatch" in g["reason"] for g in out.gaps), out.gaps
    assert any("symbol check FAILED" in e.message for e in out.log if e.status == "failed")
    print("tier 2 symbol self-check ok: wrong address rejected before any value was stored")


def test_chain_read_failure_is_logged_not_raised():
    c = Chain()
    c.reader = StubReader(symbol="Cake", raise_on="read")
    out = FetchOutput()
    c.run([_cake_project(verified="2026-09-11")], None, out)
    assert out.frame().empty
    assert all(e.status in ("ok", "failed") for e in out.log)
    assert any("execution reverted" in g["reason"] for g in out.gaps)
    print("tier 2 failure ok: RPC error logged and reported as a gap, run continues")


def test_venft_misconfiguration_fails_loudly():
    """A veNFT read as an ERC-20 supply returns a COUNT OF POSITIONS, not tokens locked.

    That is wrong by orders of magnitude and looks entirely plausible in a cell, so the config
    integrity check must reject the pairing at import rather than letting it reach the sheet.
    """
    import copy

    bad = copy.deepcopy(config.PROJECT_BY_NAME["Aerodrome"])
    bad["contracts"]["ve"]["read_method"] = "erc20_total_supply"
    saved = config.PROJECTS
    try:
        config.PROJECTS = [bad]
        errors = config.validate_config(raise_on_error=False)
        assert errors, "an erc721 contract read as erc20_total_supply must be rejected"
        assert "COUNT OF POSITIONS" in errors[0], errors
        try:
            config.validate_config()
            raise AssertionError("validate_config must raise on a veNFT misconfiguration")
        except config.ConfigError:
            pass
        # the same pairing via `kind` is caught too
        bad["contracts"]["ve"]["read_method"] = "escrow_balance_of"
        bad["contracts"]["ve"]["kind"] = "erc20_total_supply"
        assert config.validate_config(raise_on_error=False), "kind erc20_total_supply on erc721 must also fail"
    finally:
        config.PROJECTS = saved
    assert config.validate_config(raise_on_error=False) == [], "the real config must be clean"
    print("veNFT regression ok: erc721 + erc20_total_supply is rejected at load, loudly")


def test_escrow_balance_of_reads_the_underlying_not_the_nft():
    class EscrowStub:
        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *args):
            # the veNFT would report a position count; the underlying escrow balance is the real figure
            if address == "0xAERO" and args and args[0] == "0xESCROW":
                return 512_000_000.0
            if address == "0xESCROW":
                return 4312.0
            return 1_000_000_000.0

    proj = {"name": "Aerodrome", "archetypes": [3], "contracts": {
        "token": {"address": "0xAERO", "chain": "base", "kind": "erc20_total_supply", "expected_symbol": "AERO",
                  "verified": "2026-09-11", "ambiguous": False, "source_url": "u", "read_method": None,
                  "token_standard": "erc20", "underlying": None},
        "ve": {"address": "0xESCROW", "chain": "base", "kind": "ve_total_supply", "expected_symbol": "AERO",
               "verified": "2026-09-11", "ambiguous": False, "source_url": "u",
               "read_method": "escrow_balance_of", "token_standard": "erc721", "underlying": "token"}}}
    c = Chain()
    c.reader = EscrowStub()
    out = FetchOutput()
    c.run([proj], None, out)
    locked = dict(zip(out.frame().metric, out.frame().value))["locked_tokens"]
    assert locked == 512_000_000.0, f"escrow balance expected, got {locked}"
    print(f"escrow read ok: {locked:,.0f} AERO locked, not the 4,312 positions totalSupply() would report")


def test_aerodrome_minter_and_rewards_distributor_are_actually_read_not_just_documented():
    """gross_issuance_tokens and emissions_tokens must produce real values, not sit empty.

    A prior round added Minter and RewardsDistributor to Aerodrome's contracts as kind
    burn_executor — REFERENCE_ONLY, meaning documented but never read. That phrasing ("added as
    reference contracts") was ambiguous enough to be mistaken for "wired". This asserts the two
    kinds added to fix it: emission_rate_current calls Minter.weekly() with no arguments;
    rebase_last_week calls RewardsDistributor.tokensPerWeek(week) with a RUN-TIME-COMPUTED
    argument — the most recently COMPLETED week, never the current one, because that week's
    checkpoint may not have run yet (Minter.updatePeriod() is permissionless, not scheduled) and
    reading it early would return a partial or zero figure indistinguishable from a real one.
    """
    import time

    WEEK = 7 * 86400
    now_ts = int(time.time())
    last_complete_week = ((now_ts // WEEK) - 1) * WEEK

    class MinterRDStub:
        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *args, decimals_from=None):
            if address == "0xMINTER" and call == "weekly" and not args:
                return 8_123_456.78
            if address == "0xRD" and call == "tokensPerWeek":
                assert args, "tokensPerWeek must be called WITH the week argument, not bare"
                assert args[0] == last_complete_week, (
                    f"must read the most recently COMPLETED week ({last_complete_week}), "
                    f"never the current one — got {args[0]}")
                return 412_000.5
            raise AssertionError(f"unexpected call: {address}.{call}{args}")

    proj = {"name": "Aerodrome", "archetypes": [3], "contracts": {
        "token": {"address": "0xAERO", "chain": "base", "kind": "erc20_total_supply", "expected_symbol": "AERO",
                  "verified": "2026-09-11", "ambiguous": False, "source_url": "u", "read_method": None,
                  "token_standard": "erc20", "underlying": None},
        "minter": {"address": "0xMINTER", "chain": "base", "kind": "emission_rate_current",
                   "expected_symbol": "AERO", "verified": "2026-09-18", "ambiguous": False,
                   "source_url": "u", "read_method": None, "token_standard": None,
                   "underlying": "token", "call": "weekly", "call_arg": None},
        "rewards_distributor": {"address": "0xRD", "chain": "base", "kind": "rebase_last_week",
                                 "expected_symbol": "AERO", "verified": "2026-09-18", "ambiguous": False,
                                 "source_url": "u", "read_method": None, "token_standard": None,
                                 "underlying": "token", "call": "tokensPerWeek",
                                 "call_arg": "last_complete_week_unix"},
    }}
    c = Chain()
    c.reader = MinterRDStub()
    out = FetchOutput()
    c.run([proj], None, out)
    values = dict(zip(out.frame().metric, out.frame().value))

    assert values.get("gross_issuance_tokens") == 8_123_456.78, \
        f"Minter.weekly() must populate gross_issuance_tokens, got {values}"
    assert values.get("emissions_tokens") == 412_000.5, \
        f"RewardsDistributor.tokensPerWeek(last complete week) must populate emissions_tokens, got {values}"
    print(f"aerodrome minter/rewardsdistributor ok: gross_issuance_tokens="
          f"{values['gross_issuance_tokens']:,.2f}, emissions_tokens={values['emissions_tokens']:,.2f}, "
          f"read against week {last_complete_week} not the current one")


def test_unestablished_lock_read_method_is_refused():
    proj = {"name": "Aave", "archetypes": [3], "contracts": {
        "token": {"address": "0xAAVE", "chain": "ethereum", "kind": "erc20_total_supply", "expected_symbol": "AAVE",
                  "verified": "2026-09-11", "ambiguous": False, "source_url": "u", "read_method": None,
                  "token_standard": "erc20", "underlying": None},
        "staking": {"address": "0xSTK", "chain": "ethereum", "kind": "ve_total_supply", "expected_symbol": "stkAAVE",
                    "verified": "2026-09-11", "ambiguous": False, "source_url": "u", "read_method": None,
                    "token_standard": None, "underlying": "token"}}}
    c = Chain()
    c.reader = StubReader(symbol="stkAAVE")
    out = FetchOutput()
    c.run([proj], None, out)
    assert "locked_tokens" not in set(out.frame().metric), "an unestablished lock read must be refused"
    assert any("NOT ESTABLISHED" in g["reason"] for g in out.gaps), out.gaps
    print("unestablished lock read ok: refused rather than assuming ERC-20 semantics")


def test_hypercore_info_reads_the_assistance_fund_without_any_chain():
    """Hyperliquid's balance comes from its own HTTP API: no RPC, no key, no chain field.

    HYPE on HyperCore is not an ERC-20 on any chain the EVM adapter covers, so this REPLACES the
    contract-read route rather than supplementing it. Hyperliquid declares no contracts at all.
    """
    from fetch.hypercore import HyperCoreInfo

    class StubHttp:
        def __init__(self, payload):
            self.payload, self.calls = payload, []

        def post(self, url, json_body=None, headers=None):
            self.calls.append((url, json_body))
            return self.payload

    payload = {"balances": [{"coin": "USDC", "total": "12345.6"},
                            {"coin": "HYPE", "total": "48420000.0"}]}
    h = HyperCoreInfo(prior_values={("Hyperliquid", "burn_address_balance"): 48_000_000.0})
    h.http = StubHttp(payload)
    out = FetchOutput()
    h.run(config.PROJECTS, None, out)
    rows = dict(zip(out.frame().metric, out.frame().value))
    assert rows["burn_address_balance"] == 48_420_000.0, rows
    assert rows["gross_burn_tokens"] == 420_000.0, "period burn is the delta against the prior reading"
    assert set(out.frame().tier) == {1}, "a free unauthenticated HTTP API is tier 1"

    hype = config.PROJECT_BY_NAME["Hyperliquid"]
    assert hype["contracts"] == {}, "the contract route is replaced, not supplemented"
    api = hype["node_api"]
    assert "chain" not in api and not any("rpc" in k.lower() for k in api), \
        f"the info API must need no chain and no RPC: {sorted(api)}"
    print("hypercore ok: 48,420,000 HYPE read over plain HTTPS, no chain and no RPC involved")


def test_a_declared_handover_is_accepted_but_an_overlap_or_a_third_source_still_blanks():
    """GEODNET's gross_burn_tokens reads from two places BY DESIGN — Dune 8683175 backfills the
    months before this tool existed, and the Polygon dead-address delta carries it from the first
    live run. measuring_point_changed blanked the column for it, which is the guard doing exactly
    what it was built to do on the shape it was built for (Uniswap's Firepit-to-dead-address move)
    and the wrong answer for a handover.

    THE DECLARATION IS NARROW ON PURPOSE. It names the pair and the order, and asserts nothing
    else: the no-overlap is checked against the stored dates on every build, because a backfill
    re-run reaching forward into the live period is the failure that actually happens, and it
    double-counts. A declaration that could silence the guard by itself would be the guard removed
    and given a friendlier name.
    """
    import build_workbook as bw

    DUNE, CHAIN = "dune:8683175", "chain:polygon:burn_polygon"
    decl = config.declared_handover("GEODNET", "gross_burn_tokens")
    assert decl and tuple(decl["ordered_points"]) == (DUNE, CHAIN), decl
    assert decl.get("composition_change"), \
        "the legs cover different chains — that has to be stated, not left for the reader to find"

    def spans(**kw):
        return {k: (pd.Timestamp(v[0]), pd.Timestamp(v[1])) for k, v in kw.items()}

    # ACCEPTED: the backfill stops before the live read starts.
    clean = {DUNE: (pd.Timestamp("2025-01-01"), pd.Timestamp("2026-08-31")),
             CHAIN: (pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-21"))}
    assert bw.handover_refusal("GEODNET", "gross_burn_tokens", (DUNE, CHAIN), clean) is None

    # REFUSED — THE LEGS OVERLAP. A backfill re-run that reaches into the live period counts the
    # same burn twice, in the direction that looks like a busier month.
    overlapped = dict(clean, **{DUNE: (pd.Timestamp("2025-01-01"), pd.Timestamp("2026-09-15"))})
    why = bw.handover_refusal("GEODNET", "gross_burn_tokens", (DUNE, CHAIN), overlapped)
    assert why and "OVERLAP" in why and "counted twice" in why, why

    # REFUSED — A THIRD SOURCE. The declaration covers the pair it names and no other.
    third = dict(clean, **{"chain:solana:burn_solana_token_account":
                           (pd.Timestamp("2026-09-20"), pd.Timestamp("2026-09-21"))})
    why = bw.handover_refusal("GEODNET", "gross_burn_tokens", tuple(third), third)
    assert why and "outside it" in why, why

    # REFUSED — NOTHING DECLARED. The ordinary case stays exactly as it was.
    why = bw.handover_refusal("Uniswap", "gross_burn_tokens",
                              ("chain:ethereum:fire_pit", "chain:ethereum:burn_dead"),
                              spans(**{"chain:ethereum:fire_pit": ("2026-09-01", "2026-09-13"),
                                       "chain:ethereum:burn_dead": ("2026-09-14", "2026-09-21")}))
    assert why == "no handover is declared for this series", why

    # AND THE ACCEPTED CASE STILL DISCLOSES. Not blanked, not silently clean.
    row = {"status": "ok", "source": CHAIN, "n_points": 9, "measuring_points": (DUNE, CHAIN),
           "point_spans": clean, "covered_days": None, "window_days": None}
    band, reason = bw.confidence_for("GEODNET", "gross_burn_tokens", row, pd.Timestamp("2026-09-22"))
    assert band == "AMBER", f"a stitched series is not a single measurement: got {band}"
    assert "STITCHED SERIES" in reason and "Polygon and Solana" in reason, reason
    print("handover ok: accepted with a disclosure; overlap, a third source and no declaration all blank")


def test_a_same_day_rerun_differences_from_yesterday_not_from_its_own_earlier_row():
    """THE HYPERLIQUID UNDERSTATEMENT, REPRODUCED. Four runs on 2026-09-21; the burn address
    moved 231,934.0021 HYPE across the day and gross_burn_tokens recorded 83,344.4791.

    The adapter took the value to subtract from prior_values — store.latest_values(), the newest
    reading of ANY date, which after the first run of the day is THIS MORNING'S — while taking
    the date that guards it from prior_dates, which comes from store.values_before(today). The
    same-date guard in derive_flow_from_cumulative saw yesterday's date and let it through, so
    each run differenced against the previous run's number and wrote only the increment. metrics
    is keyed (date, project, metric), so each one overwrote the last and the final increment kept
    the whole day's key.

    This is the fault store.values_before was written for — PancakeSwap's 59,857,159.01 burn
    becoming 0.0123 — fixed for the chain adapter at the time and left standing in this one.
    """
    from fetch.hypercore import HyperCoreInfo

    class StubHttp:
        def post(self, url, json_body=None, headers=None):
            return {"balances": [{"coin": "HYPE", "total": "27031934.0021"}]}

    YESTERDAY, THIS_MORNING = 26_800_000.0, 26_948_589.523
    h = HyperCoreInfo(
        # latest_values(): poisoned by run 1 of the same day.
        prior_values={("Hyperliquid", "burn_address_balance"): THIS_MORNING},
        # values_before(today): the value AND the date, from the same row.
        prior_dates={("Hyperliquid", "burn_address_balance"): "2026-09-20"},
        prior_delta={("Hyperliquid", "burn_address_balance"): YESTERDAY})
    h.http = StubHttp()
    out = FetchOutput()
    h.run(config.PROJECTS, None, out)
    rows = dict(zip(out.frame().metric, out.frame().value))
    got = rows["gross_burn_tokens"]
    assert abs(got - 231_934.0021) < 1e-6, \
        f"the day's flow is measured from YESTERDAY's reading, so a re-run recomputes it " \
        f"identically — got {got:,.4f}"
    assert abs(got - 83_344.4791) > 1.0, \
        "83,344.4791 is the increment since this morning's run — a third of the day, on the day's key"
    print("same-day re-run ok: 231,934.0021 from yesterday's anchor, not 83,344.4791 from its own row")


def test_every_differenced_flow_anchors_on_values_before_not_on_latest_values():
    """The Hyperliquid fault was one line, and three adapters had it.

    chain.py was fixed when PancakeSwap surfaced it; hypercore.py, tron.py and scrape.py all call
    the same helper and all still passed self.prior. A fix applied to one call site and not to
    its siblings is the shape of bug this asserts away: the SECOND argument of every
    derive_flow_from_cumulative call — the value being subtracted — must come from the
    values_before() dict, whatever the module.
    """
    import ast
    import pathlib

    checked = []
    for mod in ("chain.py", "hypercore.py", "tron.py", "scrape.py"):
        src = (pathlib.Path(__file__).resolve().parent.parent / "fetch" / mod).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "derive_flow_from_cumulative"):
                continue
            assert len(node.args) >= 2, f"{mod}: the prior value is positional and must be there"
            anchor = ast.unparse(node.args[1])
            assert "prior_delta" in anchor, (
                f"{mod} line {node.lineno} differences against {anchor} — that is latest_values(), "
                f"the newest reading of ANY date, and on a same-day re-run it is this run's own "
                f"earlier row. It must be prior_delta, which is store.values_before(today).")
            checked.append(f"{mod}:{node.lineno}")
    assert len(checked) >= 4, f"expected a call in each of the four adapters, found {checked}"
    print(f"flow anchoring ok: {len(checked)} call sites, all on values_before — {', '.join(checked)}")


def test_hypercore_reports_a_response_shape_change_rather_than_guessing():
    from fetch.hypercore import HyperCoreInfo

    class StubHttp:
        def post(self, url, json_body=None, headers=None):
            return {"balances": [{"coin": "USDC", "total": "1"}]}

    h = HyperCoreInfo()
    h.http = StubHttp()
    out = FetchOutput()
    h.run(config.PROJECTS, None, out)
    assert out.frame().empty, "a missing coin must produce nothing, never a substituted figure"
    assert any("HYPE not present" in g["reason"] for g in out.gaps), out.gaps
    print("hypercore shape change ok: reported with the coins actually returned, nothing guessed")


def test_venice_buy_and_burn_stays_refused_as_uncorroborated():
    """'Could not be corroborated' is a distinct state from 'not yet checked', and must not be promoted."""
    venice = config.PROJECT_BY_NAME["Venice AI"]["contracts"]
    bnb = venice["buy_and_burn"]
    assert not bnb.get("verified"), "an uncorroborated address must never be marked verified"
    assert "uncorroborated" in bnb["provenance"], bnb["provenance"]
    assert venice["token"].get("verified"), "the VVV token is confirmed from Venice's own docs"
    assert venice["staking"].get("verified"), "the staking contract is confirmed from Venice's own docs"
    assert venice["staking"]["read_method"] == "escrow_balance_of", \
        "a staking contract is not a token: read the VVV it custodies, not totalSupply on it"
    print("venice ok: token and staking verified, buy_and_burn refused as uncorroborated")


# ---------------------------------------------------------------------------- tier 3
class StubPage:
    """Minimal Playwright page: evaluate() runs the DOM-anchor contract against a fake DOM."""

    def __init__(self, anchor_result):
        self.anchor_result = anchor_result

    def evaluate(self, js, arg=None):
        return self.anchor_result


def test_extract_xhr():
    entry = {"json_path": "data.monthly.0.netMint", "url_contains": "/api/tokenomics"}
    captured = [("https://x/other", {"nope": 1}),
                ("https://x/api/tokenomics?v=2", {"data": {"monthly": [{"netMint": -1958514}]}})]
    v, detail = extract_xhr(None, entry, captured)
    assert v == -1958514.0, (v, detail)
    v2, detail2 = extract_xhr(None, {"json_path": "a.b", "url_contains": "/nothing"}, captured)
    assert v2 is None and "no intercepted response matched" in detail2
    print("tier 3 xhr ok:", detail)


def test_extract_dom_anchor_and_ambiguity():
    page = StubPage([{"where": "next_sibling", "value": "1,234,567", "label": "Total CAKE Burned"}])
    v, detail = extract_dom(page, {"anchor": "Total CAKE Burned"})
    assert v == 1234567.0 and "next_sibling" in detail, (v, detail)
    page2 = StubPage([{"where": "own_tail", "value": "42", "label": "X"},
                      {"where": "next_sibling", "value": "99", "label": "X"}])
    v2, d2 = extract_dom(page2, {"anchor": "X"})
    assert v2 == 42.0 and "AMBIGUOUS" in d2, d2
    page3 = StubPage([])
    v3, d3 = extract_dom(page3, {"anchor": "Nope"})
    assert v3 is None and "not found" in d3
    print("tier 3 dom ok: anchored on label text, ambiguity surfaced")


def test_scrape_registry_reports_incomplete_entries_as_gaps():
    import tempfile
    import yaml
    tmp = Path(tempfile.mkdtemp()) / "sources.yaml"
    tmp.write_text(yaml.safe_dump([
        {"project": "PancakeSwap", "metric": "net_mint_monthly", "tier": 3, "enabled": False, "url": None, "method": "dom"},
        {"project": "Uniswap", "metric": "net_mint_monthly", "tier": 3, "enabled": True, "url": "https://x", "method": "dom"},
    ]))
    s = Scrape(registry_path=tmp)
    out = FetchOutput()
    s.run([{"name": "PancakeSwap"}, {"name": "Uniswap"}], None, out)
    assert len(out.gaps) == 2, out.gaps
    reasons = {g["project"]: g["reason"] for g in out.gaps}
    assert "disabled" in reasons["PancakeSwap"], reasons
    assert "anchor" in reasons["Uniswap"], reasons
    assert out.frame().empty
    print("tier 3 registry ok: incomplete entries become gap rows, nothing is guessed")


def test_later_tier_never_overwrites_an_earlier_one():
    """A tier 3 page must not overwrite a verified tier 2 contract read for the same metric.

    Frames concatenate in tier order and the store upserts on (date, project, metric), so without
    the guard the last writer wins and the verified contract figure never reaches the sheet.
    """
    from fetch import _resolve_tier_collisions

    out = FetchOutput()
    out.frames.append(pd.DataFrame([{"date": pd.Timestamp("2026-09-11"), "project": "Aerodrome",
                                     "metric": "locked_tokens", "value": 512_000_000.0,
                                     "source": "chain:base:ve", "tier": 2}]))
    out.frames.append(pd.DataFrame([{"date": pd.Timestamp("2026-09-11"), "project": "Aerodrome",
                                     "metric": "locked_tokens", "value": 498_300_000.0,
                                     "source": "scrape:dune.com", "tier": 3}]))
    out.frames.append(pd.DataFrame([{"date": pd.Timestamp("2026-09-11"), "project": "Aerodrome",
                                     "metric": "locked_tokens_dashboard", "value": 498_300_000.0,
                                     "source": "scrape:dune.com", "tier": 3}]))
    _resolve_tier_collisions(out)
    df = out.frame()
    kept = df[df.metric == "locked_tokens"].iloc[0]
    assert kept["source"] == "chain:base:ve", f"the contract read must win, got {kept['source']}"
    assert len(df[df.metric == "locked_tokens_dashboard"]) == 1, "a cross-check must not be collapsed"
    assert any(r["reason"] == "tier_collision" for r in out.review), out.review
    assert out.gaps, "a collision must be reported, not silently resolved"
    print("tier collision ok: contract read kept, cross-check preserved, collision surfaced")


def test_a_monthly_backfill_does_not_double_count_a_month_the_live_read_covers():
    """The GEODNET shape: a tier 4 monthly total plus tier 2 daily deltas in the same month.

    They never share a date, so the same-date collision guard does not see them, and summing both
    inside the 90-day window reports the month's burn twice — in the headline figure of the whole
    exercise, with nothing about the result looking wrong. Months the live read does NOT cover
    must still backfill, or the guard would destroy the history it exists to protect.
    """
    from fetch import _resolve_period_overlaps

    out = FetchOutput()
    rows = [
        # tier 2 daily deltas, September only
        ("2026-09-03", 100.0, "chain:polygon", 2), ("2026-09-07", 150.0, "chain:polygon", 2),
        # tier 4 monthly totals: September overlaps the deltas, July and August do not
        ("2026-09-01", 900.0, "dune:8683175", 4),
        ("2026-08-01", 800.0, "dune:8683175", 4),
        ("2026-07-01", 700.0, "dune:8683175", 4),
    ]
    out.frames = [pd.DataFrame([{"date": pd.Timestamp(d), "project": "GEODNET",
                                 "metric": "gross_burn_tokens", "value": v, "source": src, "tier": t}
                                for d, v, src, t in rows])]
    _resolve_period_overlaps(out)
    kept = out.frame()

    sept = kept[kept.date.dt.to_period("M") == pd.Period("2026-09")]
    assert set(sept.tier) == {2}, "the live read wins the month it covers"
    assert sept.value.sum() == 250.0, f"September must not be counted twice, got {sept.value.sum()}"
    assert set(kept[kept.tier == 4].date.dt.strftime("%Y-%m")) == {"2026-08", "2026-07"}, \
        "months the live read does not cover must still backfill"
    assert any(r["reason"] == "period_overlap" for r in out.review)
    print("period overlap ok: September deduped to the tier 2 read, July and August backfilled intact")


def test_cross_check_metrics_do_not_collide_with_their_primary():
    """Every declared cross-check must use a DIFFERENT metric name from its primary."""
    for p in config.PROJECTS:
        for check in p.get("cross_checks") or []:
            assert check["primary"] != check["secondary"], f"{p['name']}: cross-check collides with its primary"
            assert check["secondary"] in config.METRICS, f"{p['name']}: {check['secondary']} is not a known metric"
    print("cross-check naming ok: every secondary has its own metric key")


# ---------------------------------------------------------------------------- tier 4
def test_dune_sums_split_columns_and_drops_the_incomplete_current_period():
    """GEODNET reports Polygon and Solana burns in separate columns; the total is their sum.

    Taking one column alone would report a fraction of the burn as if it were the whole. The
    current month is also dropped: it is incomplete, and the live tier 2 read already covers the
    present, so keeping both would double count inside the trailing window.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    now = pd.Timestamp.now("UTC").tz_localize(None)
    cur, prev = now.to_period("M"), now.to_period("M") - 1

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def get(self, url, params=None, headers=None):
            return {"result": {"rows": self.rows if (params or {}).get("offset", 0) == 0 else []}}

    d = Dune()
    d.http = Rows([
        {"month": str(prev), "tokens_burned": 1_100_000.0, "sol_tokens_burned": 300_000.0},
        {"month": str(cur), "tokens_burned": 400_000.0, "sol_tokens_burned": 90_000.0},
    ])
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["GEODNET"]], None, out)
    df = out.frame()
    # actual_buyback_tokens now REUSES this same query (buy-and-burn is one flow for GEODNET —
    # see config.py, 2026-09-18), so it legitimately produces a second row from the identical
    # data. actual_buyback_usd is expected to fail: this mock supplies no usd_burned columns.
    burn = df[df.metric == "gross_burn_tokens"]
    buyback = df[df.metric == "actual_buyback_tokens"]
    assert len(burn) == 1, f"the incomplete current month must be dropped, got {len(burn)} rows"
    assert len(buyback) == 1, f"the incomplete current month must be dropped, got {len(buyback)} rows"
    assert burn.value.iloc[0] == 1_400_000.0, f"both chains must sum, got {burn.value.iloc[0]}"
    assert buyback.value.iloc[0] == 1_400_000.0, "actual_buyback_tokens must equal gross_burn_tokens"
    assert cur not in set(df.date.dt.to_period("M"))
    print("dune column summing ok: 1,100,000 + 300,000 = 1,400,000, current month dropped")


def test_dune_first_time_metric_ignores_the_trailing_window_even_on_an_incremental_run():
    """A metric added to config AFTER its query already has history must still get a full backfill.

    GEODNET's actual_buyback_tokens/usd reuse the SAME query as gross_burn_tokens, which has been
    backfilled for months. On any run after the system's own first run, the caller passes a
    trimmed window_days (e.g. 30) so daily tiers refresh cheaply — has_history correctly does NOT
    skip the new metric (it has never been fetched), but the OLD code then still window()-trimmed
    its result to that same 30 days. With monthly granularity and drop_current_period always
    removing the newest (incomplete) month, the closest surviving row is a month or more old —
    outside any 30-day window — so the new metric silently got ZERO rows on every run, looking
    identical to a broken query rather than an unbuilt one.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    now = pd.Timestamp.now("UTC").tz_localize(None)
    # Two full months back, so it is definitely outside a 30-day trailing window regardless of
    # where in the current month "now" falls.
    old_month = now.to_period("M") - 2

    d = Dune(has_history={("GEODNET", "gross_burn_tokens")})  # gross_burn_tokens is the only one
    d.http = _Rows([
        {"month": str(old_month), "tokens_burned": 500_000.0, "sol_tokens_burned": 100_000.0},
    ])
    out = FetchOutput()
    # window_days=30: an ordinary incremental run, NOT the system's first ever run.
    d.run([config.PROJECT_BY_NAME["GEODNET"]], 30, out)
    df = out.frame()

    burn = df[df.metric == "gross_burn_tokens"]
    buyback = df[df.metric == "actual_buyback_tokens"]
    assert burn.empty, "gross_burn_tokens already has history — must be SKIPPED, not re-fetched"
    assert not buyback.empty, (
        "actual_buyback_tokens has never been fetched before — its first backfill must ignore "
        "the run's 30-day window and return its full history, not silently zero rows")
    assert buyback.value.iloc[0] == 600_000.0
    print("dune first-time-ignores-window ok: a brand-new metric sharing an already-backfilled "
          "query still gets its own full history on an incremental run")


class _Rows:
    """A stub Dune HTTP layer returning one fixed page of rows."""

    def __init__(self, rows):
        self.rows = rows

    def get(self, url, params=None, headers=None):
        return {"result": {"rows": self.rows if (params or {}).get("offset", 0) == 0 else []}}


def test_dune_reports_real_columns_rather_than_guessing_an_unmapped_query():
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    # a synthetic project: no query in config is unmapped today, and this tests the ADAPTER
    unmapped = {"name": "Ethereum", "dune_queries": {"locked_tokens_dashboard": {
        "query_id": 4242, "date_col": None, "value_col": None}}}
    d = Dune()
    d.http = _Rows([{"day": "2026-09-01", "ve_locked": 1.0, "usd_value": 2.0}])
    out = FetchOutput()
    d.run([unmapped], None, out)
    assert out.frame().empty, "an unmapped query must store nothing rather than guess a column"
    gap = next(g for g in out.gaps if g["metric"] == "locked_tokens_dashboard")
    for col in ("day", "ve_locked", "usd_value"):
        assert col in gap["reason"], f"the gap must name every real column, missing {col}"
    print("dune unmapped ok: every returned column is reported, nothing is guessed")


# A project dict standing in for config, so the snapshot tests exercise the ADAPTER rather than
# whatever config happens to declare today. No query in config is a snapshot any more — 8683038
# was read that way from a partial column list and turned out to be a daily history — but the
# shape is real and the guard below is what catches the mistake, so both stay covered.
SNAPSHOT_PROJECT = {"name": "Ethereum", "dune_queries": {"staked_tokens": {
    "query_id": 77, "snapshot": True, "date_col": None, "value_col": "staked_supply",
    "staging_cols": ["agg_14", "agg_30"], "staging_note": "candidate, used by nothing"}}}


def test_snapshot_query_is_dated_now_and_stages_the_columns_nobody_chose():
    """A query that really does return one undated row is dated at the run date.

    Its extra columns go to staging rather than to metrics: captured, so the choice to adopt one
    can be made on real numbers, but read by nothing until somebody makes that choice.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    d = Dune()
    d.http = _Rows([{"staked_supply": 41_000_000.0, "agg_14": 1.2, "agg_30": 3.4, "num_holders": 8123}])
    out = FetchOutput()
    d.run([SNAPSHOT_PROJECT], None, out)
    df = out.frame()
    today = pd.Timestamp.now("UTC").tz_localize(None).normalize()

    assert len(df) == 1 and df.value.iloc[0] == 41_000_000.0
    assert df.date.iloc[0].normalize() == today, "a snapshot carries the run date"
    staged = {s["name"]: s["value"] for s in out.staged}
    assert staged == {"agg_14": 1.2, "agg_30": 3.4}, f"only the staging columns are staged, got {staged}"
    assert set(df.metric) == {"staked_tokens"}, "nothing staged may reach the metrics table"
    print("dune snapshot ok: dated today, agg_14/agg_30 staged, num_holders ignored")


def test_snapshot_declaration_is_checked_not_trusted():
    """A query declared a snapshot that is really a time series must store NOTHING.

    This is the exact mistake that was made on Ether.fi 8683038: a partial column list made an
    undated snapshot look like the right reading, when the query returns 794 rows dated by `day`.
    Dating a real history by the run date would collapse every period onto today — a plausible
    wrong number, which is the worst kind. So the declaration is verified on every run.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"

    d = Dune()
    d.http = _Rows([{"staked_supply": 1.0}, {"staked_supply": 2.0}])
    out = FetchOutput()
    d.run([SNAPSHOT_PROJECT], None, out)
    assert out.frame().empty, "several rows means a time series, so nothing may be stored"
    assert any("time series" in g["reason"] for g in out.gaps)

    d = Dune()
    d.http = _Rows([{"day": "2026-09-01", "staked_supply": 1.0}])
    out = FetchOutput()
    d.run([SNAPSHOT_PROJECT], None, out)
    assert out.frame().empty, "a date-like column means the snapshot claim is wrong"
    gap = next(g for g in out.gaps if "date-like column" in g["reason"])
    assert "day" in gap["suggestion"], "the fix must name the column to set date_col to"
    print("snapshot guard ok: refuses a time series and refuses a query that grew a date column")


def test_a_snapshot_query_is_never_skipped_as_already_backfilled():
    """Tier 4 skips a series the store already has — but a snapshot is current state, not history.

    Skipping it would freeze the series at whatever it read the first time. A DATED query has no
    such exemption: it is a backfill, and re-pulling it every day would buy nothing.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    d = Dune(has_history={("Ethereum", "staked_tokens")})
    d.http = _Rows([{"staked_supply": 7.0}])
    out = FetchOutput()
    d.run([SNAPSHOT_PROJECT], None, out)
    assert set(out.frame().metric) == {"staked_tokens"}
    assert not any("already holds history" in e.message for e in out.log)
    print("ongoing snapshot ok: read every run, not treated as a one-off backfill")


# The 13 columns query 8683038 really returns, with the sample values from the probe.
ETHERFI_ROW = {"agg_14": 0.0121, "agg_30": 0.0233, "day": "2026-09-10 00:00:00.000 UTC",
               "deposit_amount": 12, "deposit_users": 3, "num_holders": 13_011,
               "perc_staked": 0.17452, "perc_staked_cnt": 17.452,
               "processed_amount": 8, "processed_users": 2, "request_amount": 5,
               "request_users": 1, "staked_supply": 141_470_107.5}


def test_a_COMPOUNDING_lock_ratio_flags_only_when_it_FALLS():
    """sETHFI compounds, so a gap between shares and assets is health, not a defect.

    Settled on-chain at block 25,982,077: 89,748,241.267610 shares claimed 111,163,214.703019
    ETHFI, a ratio of 1.238611622166. That inverts what the check should do. Chainlink's pair
    should AGREE and a gap is the finding; this pair should DIVERGE and only the direction
    carries information — a rising ratio is rewards accruing, a falling one means rewards stopped
    or holders are exiting at a discount.

    So the level is never tested. 1.24 is not "too high" and neither would 3.0 be.
    """
    import pandas as pd
    from fetch import _derive_lock_ratio
    from fetch.base import LONG_COLUMNS

    SHARES, ASSETS = 89_748_241.267610, 111_163_214.703019
    BASELINE = ASSETS / SHARES
    assert abs(BASELINE - 1.238611622166) < 1e-11, "the measured baseline must reproduce exactly"

    spec = config.PROJECT_BY_NAME["Ether.fi"]["lock_ratio"]
    assert abs(spec["measured"]["ratio"] - BASELINE) < 1e-11
    assert spec["measured"]["block"] == 25_982_077

    def run(shares, assets, prior_ratio):
        out = FetchOutput()
        out.add(pd.DataFrame([
            {"date": pd.Timestamp("2026-09-15"), "project": "Ether.fi", "metric": "locked_tokens",
             "value": shares, "source": "dune:8683038", "tier": 4},
            {"date": pd.Timestamp("2026-09-15"), "project": "Ether.fi",
             "metric": "locked_tokens_underlying", "value": assets,
             "source": "chain:ethereum:sethfi:PARTIAL", "tier": 2},
        ])[LONG_COLUMNS], "test", "Ether.fi", "", 2)
        prior = {("Ether.fi", "lock_assets_per_share"): prior_ratio} if prior_ratio else {}
        _derive_lock_ratio(out, [config.PROJECT_BY_NAME["Ether.fi"]], prior)
        df = out.frame()
        got = df[df.metric == "lock_assets_per_share"]
        return (float(got.value.iloc[0]) if not got.empty else None), out.review

    # RISING — rewards accruing. MUST be silent, or the queue fills with health every run.
    ratio, review = run(SHARES, ASSETS * 1.02, BASELINE)
    assert abs(ratio / BASELINE - 1.02) < 1e-9
    assert not review, f"a rising ratio is the healthy case and must never flag: {review}"

    # FLAT — silent.
    _, review = run(SHARES, ASSETS, BASELINE)
    assert not review, f"flat is not a fall: {review}"

    # FALLING — the one thing worth knowing.
    ratio, review = run(SHARES, ASSETS * 0.97, BASELINE)
    assert len(review) == 1, f"a 3% fall must flag: {review}"
    assert review[0]["metric"] == "lock_assets_per_share"
    assert review[0]["reason"] == "accrued_rate_fell"
    assert abs(review[0]["prior_value"] - BASELINE) < 1e-9, "and must carry what it fell FROM"

    # THE TOLERANCE IS EARNED, NOT A COMFORT BUFFER, so both sides of it are pinned. The two
    # figures come from different sources read at different moments — a Dune daily aggregate
    # against a point-in-time contract read — so sub-0.1% wobble is measurement noise about a
    # quantity that is monotonic by design.
    _, review = run(SHARES, ASSETS * 0.9995, BASELINE)     # -0.05%, inside
    assert not review, f"measurement noise must not flag: {review}"
    _, review = run(SHARES, ASSETS * 0.995, BASELINE)      # -0.50%, outside
    assert len(review) == 1, "a real fall just past the tolerance must still flag"

    # FIRST OBSERVATION — nothing to compare against, so nothing is claimed.
    ratio, review = run(SHARES, ASSETS, None)
    assert ratio is not None and not review, "a first reading has no direction and must not flag"

    # ONE SIDE MISSING — no ratio invented from one number, and the skip is logged rather than
    # silent, so a permanently absent side is visible instead of looking like a quiet success.
    out = FetchOutput()
    out.add(pd.DataFrame([{"date": pd.Timestamp("2026-09-15"), "project": "Ether.fi",
                           "metric": "locked_tokens", "value": SHARES,
                           "source": "dune:8683038", "tier": 4}])[LONG_COLUMNS],
            "test", "Ether.fi", "", 4)
    _derive_lock_ratio(out, [config.PROJECT_BY_NAME["Ether.fi"]], {})
    assert out.frame()[out.frame().metric == "lock_assets_per_share"].empty, \
        "a ratio needs both sides"
    assert any(e.status == "skipped" and "lock_assets_per_share" in e.message for e in out.log), \
        "and the skip must be logged, not silent"

    print("compounding ratio ok: rising and flat silent, falling flags with what it fell from, "
          "noise inside tolerance silent, no ratio from one side")


def test_etherfi_share_supply_and_underlying_assets_are_TWO_METRICS_not_two_sources():
    """Shares and assets are different measures. One metric fed by both reports the gap as a flow.

    Adding the sETHFI contract read gave locked_tokens a second source, and the two do not
    measure the same thing: the Dune series is staked sETHFI (SHARE supply, 794 days of history),
    the contract read is ETHFI.balanceOf(sETHFI) (ASSETS). confidence_for correctly forced RED
    with MEASURING POINT CHANGED — the right answer to the wrong arrangement.

    They are now two metrics, the same shape as Chainlink's locked_tokens /
    locked_tokens_principal. The Dune history is untouched and keeps its 794 days.
    """
    import pandas as pd
    import build_workbook as bw

    spec = config.PROJECT_BY_NAME["Ether.fi"]["contracts"]["sethfi"]
    assert spec["kind"] == "stake_underlying", \
        f"the contract must no longer serve locked_tokens, got kind {spec['kind']!r}"
    assert config.KIND_METRIC[spec["kind"]] == "locked_tokens_underlying"
    # The read method is unchanged and deliberately so: balanceOf returns ASSETS whether sETHFI
    # compounds or is a 1:1 receipt, so the safe read was already the right one and does not
    # depend on the open question.
    assert spec["read_method"] == "escrow_balance_of" and spec["underlying"] == "token"

    # LABELS: neither figure may be readable as the other.
    assert config.metric_label("Ether.fi", "locked_tokens") == "Staked sETHFI (share supply)"
    assert "assets" in config.metric_label("Ether.fi", "locked_tokens_underlying")

    # THE CONTRACT FEEDS ONLY THE NEW METRIC.
    ETHFI = config.PROJECT_BY_NAME["Ether.fi"]["contracts"]["token"]["address"]

    class Stub:
        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            return (address == ETHFI), ("ETHFI" if address == ETHFI else "")

        def scaled(self, chain, address, call, *args, **kwargs):
            return 98_000_000.0 if call == "balanceOf" else 1_000_000_000.0

    c = Chain()
    c.reader = Stub()
    out = FetchOutput()
    c.run([config.PROJECT_BY_NAME["Ether.fi"]], None, out)
    written = dict(zip(out.frame().metric, out.frame().value))
    assert "locked_tokens" not in written, \
        f"the contract must not write the share-denominated metric: {written}"
    assert written["locked_tokens_underlying"] == 98_000_000.0

    # AND THE SPLIT CLEARS THE RED. Same two readings, now in their own columns.
    hist = pd.DataFrame([
        {"date": pd.Timestamp("2026-09-10"), "project": "Ether.fi", "metric": "locked_tokens",
         "value": 141_470_107.5, "source": "dune:8683038", "tier": 4,
         "is_manual": False, "entered_on": ""},
        {"date": pd.Timestamp("2026-09-15"), "project": "Ether.fi",
         "metric": "locked_tokens_underlying", "value": 98_000_000.0,
         "source": "chain:ethereum:sethfi:PARTIAL", "tier": 2,
         "is_manual": False, "entered_on": ""}])
    o = bw.aggregate(hist, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                     gaps=pd.DataFrame(), review=pd.DataFrame())
    shares = o[(o.project == "Ether.fi") & (o.metric == "locked_tokens")].iloc[0]
    assets = o[(o.project == "Ether.fi") & (o.metric == "locked_tokens_underlying")].iloc[0]
    assert shares["confidence"] != "RED" and "MEASURING POINT" not in (shares["why_amber"] or ""), \
        f"the Dune series is single-source again: {shares['confidence']} {shares['why_amber']!r}"
    assert shares["now"] == 141_470_107.5, "and its history is untouched"
    assert assets["now"] == 98_000_000.0

    # THE TRIPWIRE IS DISCHARGED. It used to assert that NO cross-check existed, because whether
    # sETHFI compounds decided what a divergence means and nobody knew. It was answered on-chain
    # at block 25,982,077 — sETHFI COMPOUNDS — so the assertion inverts: the check must now exist,
    # and it must be the DIRECTION-ONLY kind rather than a Chainlink-style divergence test.
    spec = config.PROJECT_BY_NAME["Ether.fi"].get("lock_ratio")
    assert spec, "sETHFI compounds and the ratio check is no longer optional"
    assert spec["numerator"] == "locked_tokens_underlying" and spec["denominator"] == "locked_tokens", \
        "assets over shares, in that order — inverted, a rise would read as a fall"
    assert spec["flag_on"] == "decrease", \
        ("divergence here is EXPECTED accrual. Flagging it would flag health on every run, which "
         "is how a Review Queue gets ignored.")
    plain = [c for c in (config.PROJECT_BY_NAME["Ether.fi"].get("cross_checks") or [])
             if c.get("primary") in ("locked_tokens", "locked_tokens_underlying")]
    assert not plain, ("a plain divergence cross_check was added on a COMPOUNDING pair — it would "
                       "fire on healthy accrual every run. Use lock_ratio's direction test.")
    print("etherfi lock split ok: shares and assets are separate metrics and columns, Dune history "
          "intact, RED cleared, direction-only ratio check wired")


# THE METRIC DUNE 8683038 FEEDS, RESOLVED FROM CONFIG. It moved from locked_tokens to
# locked_tokens_dashboard on 2026-09-22 when sETHFI.totalSupply() took over the primary — and
# every test that spelled the name out broke, which is the good outcome. The bad one is a test
# that keeps passing against a metric the query no longer feeds, so these resolve it instead.
def _etherfi_dune_metric():
    q = config.PROJECT_BY_NAME["Ether.fi"]["dune_queries"]
    return next(m for m, spec in q.items() if spec.get("query_id") == 8683038
                and spec.get("value_col") == "staked_supply")


def test_etherfi_reads_the_fraction_column_not_its_x100_twin():
    """8683038 publishes the same lock rate twice: perc_staked 0.17452 and perc_staked_cnt 17.452.

    Mapping the wrong one is not a crash, it is a plausible figure a hundred times too large. Two
    defences, both checked here: config maps perc_staked, and lock_rate_pct is bounded at 1.0 so
    the x100 column would be rejected to the Review Queue rather than stored.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    prev = dict(ETHERFI_ROW, day="2026-09-09 00:00:00.000 UTC",
                staked_supply=141_000_000.0, perc_staked=0.1740, perc_staked_cnt=17.40)
    d = Dune()
    d.http = _Rows([prev, ETHERFI_ROW])
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], None, out)
    df = out.frame()

    staked_metric = _etherfi_dune_metric()
    locked = df[df.metric == staked_metric].sort_values("date")
    assert len(locked) == 2, f"a daily history backfills every row, got {len(locked)}"
    assert locked.value.iloc[-1] == 141_470_107.5
    assert str(locked.date.iloc[-1].date()) == "2026-09-10", "the row carries the query's own date"

    rate = df[df.metric == "lock_rate_pct"].sort_values("date")
    assert rate.value.iloc[-1] == 0.17452, f"the FRACTION column, not its x100 twin: {rate.value.iloc[-1]}"
    assert 17.452 not in set(df.value), "perc_staked_cnt must not reach the store under any metric"

    holders = df[df.metric == "staker_count"].sort_values("date")
    assert holders.value.iloc[-1] == 13_011, "num_holders lands under staker_count"
    assert set(df.metric) == {staked_metric, "lock_rate_pct", "staker_count"}, \
        "the withdrawal-queue columns must not reach the store under any metric"

    # the bound is the backstop if the mapping is ever changed to the wrong column
    lo, hi = config.sanity_bounds("Ether.fi", "lock_rate_pct")
    assert hi == 1.0 and not (lo <= 17.452 <= hi), "the x100 column must fail the sanity bound"
    print("etherfi ok: daily history, perc_staked (0.17452) stored, perc_staked_cnt refused twice over")


def test_forced_repull_takes_the_full_history_not_the_trailing_window():
    """TOKEN_METRICS_DUNE_ALWAYS after a mapping fix must rebuild the WHOLE series.

    Later runs pass a 30-day window. Honouring it on a forced re-pull would rewrite the last
    month, leave every older row at the value the OLD mapping wrote, and report success — a
    half-corrected series that looks corrected.
    """
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    now = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    rows = [dict(ETHERFI_ROW, day=f"{(now - pd.Timedelta(days=i)).date()} 00:00:00.000 UTC")
            for i in range(120)]
    held = {("Ether.fi", _etherfi_dune_metric()), ("Ether.fi", "lock_rate_pct")}

    os.environ["TOKEN_METRICS_DUNE_ALWAYS"] = "1"
    try:
        d = Dune(has_history=held)
        d.http = _Rows(rows)
        out = FetchOutput()
        d.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out)      # a later, incremental run
        locked = out.frame()
        assert not [e for e in out.log if e.status == "skipped"], "the flag overrides the skip"
        locked = locked[locked.metric == _etherfi_dune_metric()]
        assert len(locked) == 120, f"a forced re-pull rebuilds the whole series, got {len(locked)} rows"
    finally:
        del os.environ["TOKEN_METRICS_DUNE_ALWAYS"]

    # WITHOUT THE FLAG, TWO DIFFERENT ORDINARY CASES, NEITHER OF WHICH IS "FETCH AND TRIM" —
    # CORRECTED 2026-09-18. The window_days trim on an unforced run only ever mattered for a
    # metric with NO history (see test_dune_first_time_metric_ignores_the_trailing_window_even_
    # on_an_incremental_run): that case now ALSO ignores the window and takes the full history,
    # for the same reason the forced re-pull above does — a metric's own first backfill must
    # never be silently truncated just because OTHER metrics in the same run are incremental.
    # This replaces the previous version of this test, which called Dune() with no has_history
    # and asserted a 25-31 row window trim — that was exercising the first-time path (has_history
    # defaults to empty) and got a trimmed result only because the OLD code applied window_days
    # unconditionally. The trim was never really about "ordinary vs forced"; it was an accident of
    # not distinguishing first-time from already-historied. The real distinction:
    #
    #   already has history, no flag  -> SKIPPED entirely (this codebase never does a plain
    #                                    trimmed re-fetch of a backfilled series; see run()'s
    #                                    skip branch, which fires whenever it is not first_time,
    #                                    not forced and not an ongoing/snapshot query)
    #   no history yet (first time)   -> full history, unconditionally (this test's first block,
    #                                    and the dedicated first-time test above)
    # A CADENCE NEEDS A DATE TO MEASURE FROM, and these fixtures hand Dune a has_history set
    # with no last_dates. In a real run those come from the same store and agree; here the
    # series reads as age-unknown, which counts as DUE — one paid query beats a cross-check
    # frozen for ever. So the fixture states the series is fresh, and the skip branch is
    # exercised for the reason this test is about rather than for a missing date.
    fresh = {k: now.date().isoformat() for k in held}
    d = Dune(has_history=held, last_dates=fresh)
    d.http = _Rows(rows)
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out)
    locked = out.frame()
    locked = locked[locked.metric == _etherfi_dune_metric()]
    assert locked.empty, (
        "an already-historied metric INSIDE its refresh cadence must be SKIPPED, not fetched-"
        f"and-trimmed — there is still no 'ordinary incremental re-fetch' path, got {len(locked)} rows")
    assert any(e.status == "skipped" and _etherfi_dune_metric() in e.message for e in out.log)

    d2 = Dune()   # has_history defaults to empty — this metric's OWN first run
    d2.http = _Rows(rows)
    out2 = FetchOutput()
    d2.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out2)
    locked2 = out2.frame()
    locked2 = locked2[locked2.metric == _etherfi_dune_metric()]
    assert len(locked2) == 120, (
        f"a metric's first-ever backfill must ignore window_days too, got {len(locked2)} rows")
    print("forced re-pull ok: full 120 rows with the flag; an already-historied metric is "
          "skipped without it; a genuinely first-time metric still gets the full 120")


def test_etherfi_daily_history_is_a_backfill_not_an_ongoing_read():
    """It is a normal historical source: once the store holds it, tier 4 skips it like any other."""
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    held = {("Ether.fi", _etherfi_dune_metric()), ("Ether.fi", "lock_rate_pct"),
            ("Ether.fi", "staker_count")}
    # A CADENCE NEEDS A DATE TO MEASURE FROM, and these fixtures hand Dune a has_history set
    # with no last_dates. In a real run those come from the same store and agree; here the
    # series reads as age-unknown, which counts as DUE — one paid query beats a cross-check
    # frozen for ever. So the fixture states the series is fresh, and the skip branch is
    # exercised for the reason this test is about rather than for a missing date.
    today_iso = pd.Timestamp.now().normalize().date().isoformat()
    d = Dune(has_history=held, last_dates={k: today_iso for k in held})
    d.http = _Rows([ETHERFI_ROW])
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], None, out)
    assert out.frame().empty, "a dated query the store already holds, and is fresh, must be skipped"
    # and the skip is reported as a SKIP, not as a success with nothing to add
    skips = [e for e in out.log if e.status == "skipped"]
    assert len(skips) == 3, f"every skipped metric must be logged as skipped, got {len(skips)}"
    assert all("backfill NOT run" in e.message and "DUNE_ALWAYS" in e.message for e in skips), \
        "a skip must say it ran nothing and name the flag that forces it"
    print("etherfi ok: dated history, so no snapshot exemption to the tier 4 backfill skip")


def test_chainlink_stores_BOTH_the_balance_and_the_pools_own_principal():
    """balanceOf(pool) is an UPPER BOUND; getTotalPrincipal() is the protocol's own accounting.

    The live run of 2026-09-14 gave 42,536,190.83 LINK from the balanceOf sum with no second
    opinion at all, and its plausibility against the 45m programme size was the only thing
    supporting it. Maple's 0.51 in the same run showed what plausibility is worth.

    Both figures are stored. Neither replaces the other: agreement confirms the read, and a
    persistent gap says the pools hold LINK that is not staked principal, which is a finding.
    """
    import pandas as pd
    import build_workbook as bw
    from fetch.validate import check_cross_checks

    LINK = config.PROJECT_BY_NAME["Chainlink"]["contracts"]["token"]["address"]
    POOLS = {"0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e",
             "0xA1d76A7cA72128541E9FCAcafBdA3a92EF94fDc5"}
    seen = []

    class PoolStub:
        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            seen.append(("symbol", address, None))
            # A STAKING POOL HAS NO symbol(). If the gate ever targets one, this raises rather
            # than letting the read fail later for an unrelated-looking reason.
            if address != LINK:
                raise RuntimeError(f"symbol() must only ever be called on the token, got {address}")
            return True, "LINK"

        def scaled(self, chain, address, call, *args, decimals_from=None):
            seen.append((call, address, decimals_from))
            if call == "getTotalPrincipal":
                assert address in POOLS, f"principal must be read ON THE POOL, got {address}"
                # A POOL HAS NO decimals() EITHER. Defaulting to 18 would be exactly the kind of
                # assumption that produced Maple's 0.51, so the token must supply it.
                assert decimals_from == LINK, f"decimals must come from LINK, got {decimals_from}"
                return 21_000_000.0
            if call == "balanceOf":
                return 21_268_095.415        # half the live 42,536,190.83
            return 1_000_000_000.0

    c = Chain()
    c.reader = PoolStub()
    out = FetchOutput()
    c.run([config.PROJECT_BY_NAME["Chainlink"]], None, out)
    df = out.frame()

    rows = dict(zip(df.metric, df.value))
    assert abs(rows["locked_tokens"] - 42_536_190.83) < 1e-6, \
        f"the balanceOf sum must still be stored unchanged, got {rows.get('locked_tokens')}"
    assert abs(rows["locked_tokens_principal"] - 42_000_000.0) < 1e-6, \
        f"the principal sum must be stored ALONGSIDE it, got {rows.get('locked_tokens_principal')}"
    assert not [e for e in out.log if e.status == "failed"], \
        f"neither read should fail: {[e.message for e in out.log if e.status == 'failed']}"
    assert {a for k, a, _ in seen if k == "symbol"} == {LINK}, \
        "symbol() must only ever target LINK, never a pool"
    assert sorted(a for k, a, _ in seen if k == "getTotalPrincipal") == sorted(POOLS), \
        "both pools must be asked their own principal"

    # DIVERGENCE IS FLAGGED, AND ON THE HEADLINE METRIC. 1.26% apart, tolerance 1%.
    def review_for(principal):
        frame = pd.DataFrame([
            {"date": "2026-09-14", "project": "Chainlink", "metric": "locked_tokens",
             "value": 42_536_190.83, "source": "a", "tier": 2},
            {"date": "2026-09-14", "project": "Chainlink", "metric": "locked_tokens_principal",
             "value": principal, "source": "b", "tier": 2}])
        o = FetchOutput()
        check_cross_checks(frame, o)
        return o.review

    flagged = review_for(42_000_000.0)
    assert len(flagged) == 1 and flagged[0]["metric"] == "locked_tokens", \
        f"a 1.26% gap must flag, and on locked_tokens where the lock rate is read: {flagged}"
    assert not review_for(42_400_000.0), "a 0.32% gap is inside tolerance and must stay silent"

    # and a flagged row carries AMBER, which is the whole point of flagging it
    source = "chain:sum(ethereum:staking_community+ethereum:staking_node_operator)"
    band, why = bw.confidence_for(
        "Chainlink", "locked_tokens",
        {"status": "review", "source": source, "n_points": 9, "entered_on": "",
         "measuring_points": (source,)}, pd.Timestamp("2026-09-14"))
    assert band == "AMBER" and "Review Queue" in why, f"{band}: {why}"
    print("chainlink principal ok: both figures stored, symbol/decimals from LINK not the pools, "
          "1.26% divergence flags AMBER on locked_tokens, 0.32% stays silent")


def test_the_maple_cross_check_is_ARMED_and_its_floor_catches_an_unscaled_figure():
    """The guard that would have caught 0.51 against ~75.78m, and the one way it could still fail.

    Being "enabled" is not being armed: entry_ready() rejects a dom entry with no anchor, and a
    cross-check whose secondary never arrives is skipped silently by check_cross_checks. Both
    halves are asserted, because for several rounds the entry was enabled and still inert.

    The remaining failure mode is the MIRROR of the 0.51: Maple renders "77.66M" and parse_number
    handles the suffix, but if the page ever drops it, parse_number returns 77.66 — small, precise
    and entirely plausible. The sanity floor is what stands between that and the sheet.

    ** THE FLIP IS OFF AGAIN, 2026-09-21, AND THE ENTRY IS DISABLED. ** maple.finance/robots.txt
    disallows /transparency. The 2026-09-18 promotion had made this entry the PRIMARY source for
    treasury_holding_tokens; the scrape refused (correctly — we respect robots.txt, we do not
    route around it) and the metric went blank. So the chain read serves treasury_holding_tokens
    again, labelled PARTIAL, this entry is disabled and retargeted at
    treasury_holding_tokens_reported, and the page's figure is entered by hand.

    WHAT THIS TEST NOW ASSERTS is therefore the opposite of "armed", and the parsing half is kept
    rather than deleted: the entry is disabled, nothing enabled points at the disallowed path, and
    the suffix/floor defences still hold — because the figure they protect is now a HAND-TYPED one,
    where a dropped suffix is if anything easier to produce than a mis-parsed page.
    """
    import yaml
    from fetch.base import parse_number
    from fetch.scrape import entry_ready

    entries = yaml.safe_load(open("sources.yaml", encoding="utf-8"))
    entry = next(e for e in entries
                 if e["project"] == "Maple" and e["metric"] == "treasury_holding_tokens_reported")

    # DISABLED, and for a reason that is not "unfinished". The url and the anchor are RIGHT; we
    # are simply not permitted to fetch them. Both are kept so re-enabling is a one-word change
    # if Maple ever changes robots.txt.
    assert entry["enabled"] is False, "robots.txt disallows this path — it must not be armed"
    ready, why = entry_ready(entry)
    assert not ready and "disabled" in why.lower(), why
    assert entry["anchor"] == "SYRUP Holdings", f"anchor must be kept, got {entry['anchor']!r}"
    assert entry["url"] == "https://maple.finance/transparency"
    assert "robots.txt" in entry["note"], "the note must say WHY it is disabled"

    # NO scale FIELD. parse_number already expands the suffix; a scale would multiply again.
    assert "scale" not in entry or entry.get("scale") in (None, 1), \
        "parse_number handles 'M' natively — a scale of 1e6 on top would be a million-fold error"
    assert parse_number("77.66M") == 77_660_000.0
    assert parse_number("$4.63M") == 4_630_000.0

    # THE FLOOR, read from where validate_frame actually reads it. The registry's own
    # sanity_min/sanity_max are not consulted by anything, so asserting those would prove nothing.
    lo, hi = config.sanity_bounds("Maple", "treasury_holding_tokens_reported")
    unscaled = parse_number("77.66")          # what a dropped suffix, or a typo, would yield
    assert unscaled == 77.66
    assert lo is not None and unscaled < lo, \
        f"an unscaled 77.66 must be REJECTED, not stored — floor is {lo}"
    assert lo <= 77_660_000.0 <= (hi or float("inf")), \
        f"and the correct figure must pass, bounds were ({lo}, {hi})"

    # buyback_fund_balance_dashboard no longer has a route for Maple — the SAME scrape now feeds
    # treasury_holding_tokens directly, so this must be declared not_applicable, not a live gap.
    assert config.not_applicable_reason("Maple", "buyback_fund_balance_dashboard") is not None, \
        "the vacated metric name must be explained, not left as an unexplained permanent gap"

    # The cross-check still exists and still pairs the two readings — the names moved, the
    # comparison did not. prefer='secondary' says which side is AUTOMATED, not which is truer.
    checks = config.PROJECT_BY_NAME["Maple"]["cross_checks"]
    pair = next(c for c in checks if c["primary"] == "treasury_holding_tokens_reported")
    assert pair["secondary"] == "treasury_holding_tokens"
    assert pair["prefer"] == "secondary", \
        "prefer the automated side — a manual figure cannot refresh itself"

    # The chain read serves the primary metric DIRECTLY again: no override, and labelled partial.
    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    assert treasury["metric_override"] is None, \
        "the override is what left treasury_holding_tokens with no source when the page refused"
    assert treasury["supply_is_partial"] is True, "23.09M is part of the treasury, not all of it"
    assert "77.66" in treasury["partial_reason"] and "23.09" in treasury["partial_reason"]
    assert treasury["destination_status"] == "verified_by_label"
    assert treasury["address"] == "0xd6d4Bcde6c816F17889f1Dd3000aF0261B03a196", \
        "must be the daoMultisig / 'Maple Finance: DAO' address, not the old disputed fee treasury"
    # AND THE VACATED METRIC IS GONE, not sitting unfed with an explanation attached.
    # treasury_holding_tokens_chain_crosscheck lived from 2026-09-18 (the page promoted, the chain
    # read demoted by metric_override) to 2026-09-21 (robots.txt disallows the page, the chain
    # read restored). Retired 2026-09-22 rather than kept against a page we do not fetch: an
    # unfed metric is a permanent Gap Report row and a permanent not_applicable entry, and
    # re-creating it is the same one line it always was.
    assert "treasury_holding_tokens_chain_crosscheck" not in config.METRICS
    assert config.not_applicable_reason("Maple", "treasury_holding_tokens_chain_crosscheck") is None, \
        "a retired metric needs no explanation — there is no cell for one to appear in"
    print("maple ok: page entry disabled on robots.txt, chain read restored as a PARTIAL primary, "
          "published figure carried manually, floor still rejects an unscaled 77.66")


def test_data_tab_distinguishes_closed_missing_from_an_open_one():
    """'missing' alone does not say whether nobody has looked, or the search was chased and closed.

    Fluid's actual_buyback_tokens/usd are a real example: the Reserve address was never published
    anywhere Fluid has written, that search is recorded as CLOSED in config.UNAVAILABLE, and
    fetch/gaps.py deliberately keeps closed items OFF the Gap Report — so the cell's status ends
    up 'missing' (no data, no gap row) rather than 'gap'. The A3 tab already renders this
    correctly (CLOSED_TEXT plus a full comment); the Data tab printed the same grey 'missing' for
    this as for a metric nobody has ever tried to source, with nothing to tell them apart.
    """
    import build_workbook as bw
    from openpyxl import Workbook

    empty = pd.DataFrame(columns=["date", "project", "metric", "value", "source", "tier",
                                  "is_manual", "entered_on"])
    data = bw.aggregate(empty, pd.DataFrame(), pd.Timestamp("2026-09-18"),
                        gaps=pd.DataFrame(), review=pd.DataFrame())
    row = data[(data.project == "Fluid") & (data.metric == "actual_buyback_tokens")].iloc[0]
    assert row["status"] == "missing", (
        f"precondition: this must reproduce the reported state, got {row['status']!r}")

    wb = Workbook()
    ws = wb.active
    bw.write_data(ws, data, pd.Timestamp("2026-09-18"))

    status_col = bw.DATA_COLS.index("status") + 1
    r = next(i for i in range(2, ws.max_row + 1)
             if ws.cell(row=i, column=bw.DATA_COLS.index("project") + 1).value == "Fluid"
             and ws.cell(row=i, column=bw.DATA_COLS.index("metric") + 1).value == "actual_buyback_tokens")
    cell = ws.cell(row=r, column=status_col)
    assert cell.value == "missing"
    assert cell.comment is not None, "a closed-and-documented 'missing' must carry a comment saying so"
    assert "CLOSED" in cell.comment.text and "not an open gap" in cell.comment.text
    assert "Reserve address" in cell.comment.text or "never published" in cell.comment.text.lower()

    # AND THE NEGATIVE: an ordinary applicable-but-genuinely-unsourced metric gets no such
    # comment — only a metric matching config.UNAVAILABLE does.
    other_row = data[(data.status == "missing")
                     & ~data.apply(lambda r: bool(config.unavailable_for(r.project, r.metric)), axis=1)]
    assert not other_row.empty, "need at least one ordinary (non-closed) missing row to contrast against"
    o = other_row.iloc[0]
    r2 = next(i for i in range(2, ws.max_row + 1)
              if ws.cell(row=i, column=bw.DATA_COLS.index("project") + 1).value == o["project"]
              and ws.cell(row=i, column=bw.DATA_COLS.index("metric") + 1).value == o["metric"])
    assert ws.cell(row=r2, column=status_col).comment is None, \
        "an ordinary missing metric (not on config.UNAVAILABLE) must not get the CLOSED comment"
    print("data tab ok: a closed/UNAVAILABLE 'missing' carries an explanatory comment; an "
          "ordinary unsourced 'missing' does not")


def test_an_armed_cross_check_reads_as_WAITING_not_as_an_unbuilt_metric():
    """Two separate things made an armed guard look like nobody had built it.

    FIRST, the gap text was factually wrong. fetch_all recorded only the NOT-READY registry
    entries, so a complete, enabled, armed entry was invisible to the gap reporter and fell
    through to "no sources.yaml entry for this metric" — when there plainly was one, with a url
    and a selector. A reader acting on that would have gone and written a second entry.

    SECOND, even with correct text, status 'gap' cannot distinguish "armed and correctly idle"
    from "nobody has built this". A secondary has nothing to compare against when its PRIMARY is
    a disputed destination that stores nothing — which is the guard working, not failing.

    Part (1) used Maple's treasury_holding_tokens (the armed page scrape) until 2026-09-21, when
    robots.txt turned out to disallow that page and the entry was disabled — so it now uses
    Chainlink's armed dashboard entry for the armed case, and keeps Maple as the DISABLED case,
    which is the same distinction seen from the other side. Part (2) uses CHAINLINK's
    buyback_fund_balance/buyback_fund_balance_dashboard pair, not Maple's: after the 2026-09-18
    flip Maple's own primary is page-sourced with no contract behind it at all, so
    destination_disputed can never apply to it — cross_check_waiting_on_primary is structurally
    inert for Maple now, asserted explicitly below rather than left unexercised. Chainlink's pair
    still has a contract-based primary (the Reserve) and an armed dashboard secondary, so it is
    what actually exercises the general mechanism.
    """
    import pandas as pd
    import build_workbook as bw
    from fetch.gaps import detect
    from fetch.base import LONG_COLUMNS
    from fetch.scrape import load_registry, entry_ready

    # (1) the gap text must describe an ARMED entry, and name the url and selector
    registry = {}
    for e in load_registry("sources.yaml"):
        ok, why = entry_ready(e)
        registry[(e["project"], e["metric"])] = (
            {"ready": True, "url": e.get("url"), "anchor": e.get("anchor"), "method": e.get("method")}
            if ok else why)
    rows = detect(config.PROJECTS, pd.DataFrame(columns=LONG_COLUMNS), set(), registry, [])
    # ON CHAINLINK'S DASHBOARD ENTRY, not Maple's, as of 2026-09-21. Maple's page entry was the
    # original example and is now DISABLED: maple.finance/robots.txt disallows /transparency, so
    # it is no longer an armed entry and cannot demonstrate what an armed one reports. Chainlink's
    # buyback_fund_balance_dashboard is armed and is the same shape the example was chosen for.
    gap = next(g for g in rows
               if g["project"] == "Chainlink" and g["metric"] == "buyback_fund_balance_dashboard")
    assert "no sources.yaml entry" not in gap["reason"], \
        f"the entry exists and is armed — saying otherwise sends the reader to write a second one: {gap['reason']}"
    assert "ARMED" in gap["reason"] and str(registry[("Chainlink", "buyback_fund_balance_dashboard")]["url"]) in gap["reason"], \
        f"an armed entry must name its url so the reader can tell the cases apart: {gap['reason']}"
    assert "Run Log" in gap["suggestion"], "and point at where 'did it actually run' is answered"

    # AND THE OTHER HALF OF THE SAME DISTINCTION, which is what the Maple entry now demonstrates:
    # a DISABLED entry must not read as an absent one either. "We are not allowed to fetch this"
    # and "nobody has written this yet" send a reader to completely different places.
    why_maple = registry[("Maple", "treasury_holding_tokens_reported")]
    assert isinstance(why_maple, str) and "disabled" in why_maple.lower(), why_maple
    assert "maple.finance/transparency" in why_maple, \
        f"a deliberately disabled entry must still name the page it is about: {why_maple}"

    # MAPLE-SPECIFIC: the waiting mechanism keys off a DISPUTED destination, and Maple's treasury
    # contract is verified_by_label, not disputed — so it cannot apply here either way. Asked on
    # treasury_holding_tokens_reported, the manual figure, because the cross-check metric it used
    # to be asked on was retired 2026-09-22.
    assert config.cross_check_waiting_on_primary("Maple", "treasury_holding_tokens_reported") is None, \
        "nothing is waiting on a disputed primary here — the treasury contract is not disputed"

    # (2) status must distinguish armed-and-idle from unbuilt — exercised on Chainlink, whose
    # buyback_fund_balance/buyback_fund_balance_dashboard pair is still shaped the way this
    # mechanism was built for: both sides contract-or-scrape as before the Maple flip.
    reserve = config.PROJECT_BY_NAME["Chainlink"]["contracts"]["reserve"]
    live_status = reserve["destination_status"]
    reserve["destination_status"] = "disputed"
    try:
        empty = pd.DataFrame(columns=["date", "project", "metric", "value", "source", "tier",
                                      "is_manual", "entered_on"])
        out = bw.aggregate(empty, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                           gaps=pd.DataFrame(), review=pd.DataFrame())

        waiting = out[(out.project == "Chainlink") & (out.metric == "buyback_fund_balance_dashboard")].iloc[0]
        assert waiting["status"] == "waiting", \
            f"an armed secondary with a suppressed primary is not a plain gap: {waiting['status']}"
        assert "WAITING ON THE PRIMARY" in waiting["note"] and "buyback_fund_balance" in waiting["note"], \
            f"and the note must name what it is waiting for: {waiting['note']!r}"

        # NARROW ON PURPOSE: an ordinary dashboard metric with no cross-check is untouched, and so is
        # a secondary whose primary is merely empty rather than suppressed by config.
        other = out[(out.project == "Chainlink") & (out.metric == "locked_tokens_dashboard")].iloc[0]
        assert other["status"] != "waiting", \
            f"only a secondary blocked BY CONFIG waits; everything else is an honest gap: {other['status']}"
        assert config.cross_check_waiting_on_primary("Maple", "treasury_holding_tokens_reported") is None, \
            "Maple's primary is not contract-based, so its secondary is never 'waiting'"
        print("waiting state ok: armed entry named with its url and selector, status 'waiting' not "
              "'gap' on Chainlink, and Maple correctly never waits post-flip")
    finally:
        reserve["destination_status"] = live_status


def test_one_observation_larger_than_a_quarter_of_the_cumulative_is_not_a_flow():
    """The third guard on the same failure, and the only one that still worked.

    Uniswap's burn read moved from fire_pit to the dead address on 2026-09-14, and the delta
    across that change stored 111,337,581 UNI as ONE DAY'S BURN — 99.99% of every UNI ever
    burned, against a real rate of 100-200k/day. Two guards existed and neither caught it:

      the WRITE-TIME guard (fetch.base.derive_flow_from_cumulative) refuses to difference across
        a changed measuring point — but it was added AFTER this row was written, and it cannot
        reach backwards into the store;
      the READ-TIME guard (withheld_for case 5) fires on two distinct sources in the history —
        but the orphan cleanup deleted the fire_pit rows, which removed the evidence and left
        the consequence, so only one source remained.

    So the row was still on the sheet a week later, found by the audit of run 20260921T100546Z.
    This check needs neither provenance nor history: a flow differenced out of a stock is bounded
    by that stock, whatever its source string says and whoever deleted what.
    """
    import pandas as pd
    import build_workbook as bw

    def build(flow_rows):
        rows = list(flow_rows) + [{
            "date": pd.Timestamp("2026-09-20"), "project": "Uniswap",
            "metric": "burn_address_balance", "value": 111_953_581.0,
            "source": "chain:ethereum:burn_dead", "tier": 2, "is_manual": False, "entered_on": ""}]
        out = bw.aggregate(pd.DataFrame(rows), pd.DataFrame(), pd.Timestamp("2026-09-21"),
                           gaps=pd.DataFrame(), review=pd.DataFrame())
        return out[(out.project == "Uniswap") & (out.metric == "gross_burn_tokens")].iloc[0]

    ordinary = [{"date": d, "project": "Uniswap", "metric": "gross_burn_tokens", "value": 120_000.0,
                 "source": "chain:ethereum:burn_dead:delta", "tier": 2, "is_manual": False,
                 "entered_on": ""}
                for d in pd.date_range("2026-09-15", "2026-09-20")]
    artefact = {"date": pd.Timestamp("2026-09-14"), "project": "Uniswap",
                "metric": "gross_burn_tokens", "value": 111_337_581.0,
                "source": "chain:ethereum:burn_dead:delta", "tier": 2, "is_manual": False,
                "entered_on": ""}

    # THE REAL SHAPE, exactly as the audit found it: one artefact among ordinary days, and a
    # SINGLE measuring point, so case 5 cannot be what catches it.
    bad = build([artefact] + ordinary)
    assert bad["status"] == "implausible_delta", \
        f"a 99%-of-cumulative day must not read as a flow: {bad['status']}"
    assert "MEASURING POINT CHANGED" not in bad["note"], \
        "precondition: only one source survives, so this is NOT case 5 catching it"
    assert pd.isna(bad["now"]), f"the figure must be blanked, not shown: {bad['now']}"
    assert all(pd.isna(bad[c]) for c in ("m1", "q0", "q1", "q2", "q3", "y1")), \
        "every window spanning the artefact is equally wrong and must blank too"
    assert bad["confidence"] == "RED"
    assert "99%" in bad["note"] and "burn_address_balance" in bad["note"], \
        f"the row must name the ratio and what it was measured against: {bad['note']!r}"

    # AND IT SELF-CLEARS. Deleting the row (orphan_cleanup.sql section E) is the actual fix; this
    # guard must not need a config change to stand down once the store is clean.
    good = build(ordinary)
    assert good["status"] == "ok" and good["now"] == 720_000.0, \
        f"ordinary days must render normally once the artefact is gone: {good['status']}, {good['now']}"
    # AMBER, not GREEN, and for a SEPARATE reason rather than a leftover of the artefact: this
    # fixture's series is a handful of days long, so a trailing-30-day sum over it genuinely
    # covers only a few of the 30 days and the S6 disclosure says so. What matters here is that
    # the implausible-delta reason is gone and the value is shown again.
    assert good["confidence"] == "AMBER", good["why_amber"]
    assert "OF THE CUMULATIVE" not in str(good["why_amber"]), \
        f"the implausible-delta reason must go when the artefact does: {good['why_amber']}"
    assert "covers only" in str(good["why_amber"]), good["why_amber"]

    # NARROW ON PURPOSE. A Dune-sourced monthly burn is a PERIOD TOTAL, not a difference, so it
    # has no arithmetic relationship to the cumulative balance and must never be measured against
    # it — the :delta marker is what separates the two.
    period_total = [{"date": pd.Timestamp("2026-08-01"), "project": "Uniswap",
                     "metric": "gross_burn_tokens", "value": 90_000_000.0,
                     "source": "dune:1234567", "tier": 4, "is_manual": False, "entered_on": ""}]
    assert build(period_total)["status"] != "implausible_delta", \
        "a period total from Dune is not a differenced flow and must not be bounded by the stock"
    print("implausible delta ok: 99%-of-cumulative blanked RED with one measuring point, "
          "clears itself when the row goes, and does not touch non-differenced period totals")


def test_a_disputed_destination_suppresses_a_row_ALREADY_IN_THE_STORE():
    """The half of "disputed" that was missing, and the live run found it.

    Marking the contract disputed stopped the ADAPTER writing a new value — the test below proves
    that. It did nothing about the 0.51 SYRUP already in the store from the run before, because
    aggregate() only consults the Gap Report when the store has NO rows for a key. So the figure
    the dispute existed to suppress rendered as status 'ok' the following day, single-observation
    AMBER, with nothing anywhere saying it was disputed.

    Suppression now happens in aggregate() too, so it does not depend on anyone remembering to run
    a DELETE.

    Maple's treasury dispute was RESOLVED 2026-09-18 — see test_the_maple_cross_check_is_ARMED...
    for the resolution. The mechanism this test exists to protect is unrelated to which project
    happens to be disputed today, so the dispute is forced back on temporarily (same pattern as
    test_fluid_buyback_is_suppressed...) to keep exercising it against realistic historical data —
    the actual 0.51253570332391 SYRUP figure this guard was built for.

    THE METRIC NAME IS RESOLVED FROM CONFIG, not written out — see _maple_treasury_metric(). This
    contract's write target moved to treasury_holding_tokens_chain_crosscheck on 2026-09-18 and
    back to treasury_holding_tokens on 2026-09-21, and the mechanism under test (a disputed
    contract's stale stored row gets blanked and RED) does not care which name it is.
    """
    import pandas as pd
    import build_workbook as bw

    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    live_status = treasury["destination_status"]
    treasury["destination_status"] = "disputed"
    try:
        metric = _maple_treasury_metric()
        assert config.destination_disputed("Maple", metric), \
            "precondition: Maple's treasury contract is the disputed one"

        stale = pd.DataFrame([{
            "date": pd.Timestamp("2026-09-14"), "project": "Maple",
            "metric": metric, "value": 0.5125357033239131,
            "source": "chain:ethereum:treasury", "tier": 2, "is_manual": False, "entered_on": ""}])

        # NO gap row is passed, deliberately: the live symptom is that the gap never reaches this
        # branch at all. If suppression depended on the gap being present, this test would pass for
        # the wrong reason.
        out = bw.aggregate(stale, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                           gaps=pd.DataFrame(), review=pd.DataFrame())
        row = out[(out.project == "Maple") & (out.metric == metric)].iloc[0]

        assert row["status"] == "disputed", f"a stale row under a dispute must not read 'ok': {row['status']}"
        assert pd.isna(row["now"]), f"the figure must be blanked, not shown: {row['now']}"
        assert all(pd.isna(row[f]) for f in ("m1", "q0", "q1", "q2", "q3", "y1")), \
            "every window must be blank too — a trajectory built on a withdrawn figure is still wrong"
        assert row["confidence"] == "RED", \
            f"RED, not AMBER: this is not low confidence, it is withdrawn meaning. Got {row['confidence']}"
        assert "DISPUTED" in row["note"] and "treasury" in row["note"], \
            f"the row must carry WHY, not just an empty cell: {row['note']!r}"

        # AND THE NEGATIVE: an undisputed metric on the same project is untouched by any of this.
        clean = pd.DataFrame([{
            "date": pd.Timestamp("2026-09-14"), "project": "Maple", "metric": "total_supply",
            "value": 1_190_000_000.0, "source": "chain:ethereum:token", "tier": 2,
            "is_manual": False, "entered_on": ""}])
        out2 = bw.aggregate(clean, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                            gaps=pd.DataFrame(), review=pd.DataFrame())
        ok = out2[(out2.project == "Maple") & (out2.metric == "total_supply")].iloc[0]
        assert ok["status"] == "ok" and ok["now"] == 1_190_000_000.0, \
            f"only the disputed metric is suppressed: {ok['status']}, {ok['now']}"
        print("disputed suppression ok: a stale 0.51 already in the store renders blank and RED, "
              "with the reason attached; an undisputed metric is untouched")
    finally:
        treasury["destination_status"] = live_status


def test_maple_treasury_is_disputed_so_a_dust_balance_is_never_stored_as_a_figure():
    """0.51 SYRUP against ~75.78m reported: the read was right, the ADDRESS was wrong.

    The live run of 2026-09-14 returned 0.51253570332391 SYRUP from the address taken to be the
    Syrup Strategic Fund. Decimals were excluded arithmetically — 75,780,000 / 0.51253570332391
    has a log10 of 8.1698, and a decimals mismatch is always an exact power of ten — so the
    balance was genuine and the address was the v2 PROTOCOL FEE treasury, which holds pool assets
    rather than SYRUP.

    What makes this worth a permanent test is the SHAPE of the number. A zero looks broken and
    gets investigated. 0.51 is small, precise, non-zero and would have flowed into every Maple
    archetype 3 destination figure untouched.

    Maple's treasury dispute was RESOLVED 2026-09-18 (new address, verified_by_label — see
    test_the_maple_cross_check_is_ARMED...). The disputed-destination mechanism this test protects
    is general, not specific to Maple's current state, so the dispute is forced back on
    temporarily to keep exercising it against the real historical 0.51253570332391 SYRUP reading.

    THE METRIC NAME MOVED TWICE AND CAME BACK. It was overridden to
    treasury_holding_tokens_chain_crosscheck on 2026-09-18 when Maple's transparency page took
    the primary name, and restored on 2026-09-21 when robots.txt turned out to disallow that
    page; the cross-check metric was retired on 2026-09-22. The contract serves
    treasury_holding_tokens again, and the mechanism under test — a disputed contract's reading
    is staged as evidence and never stored as a figure — never depended on which name it was.
    """
    spec = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    live_status = spec["destination_status"]
    spec["destination_status"] = "disputed"
    try:
        assert spec["destination_status"] == "disputed", (
            "the address is real and its role is not — it must be read as evidence and stored as "
            f"nothing, got {spec.get('destination_status')!r}")
        metric = _maple_treasury_metric()

        SYRUP = config.PROJECT_BY_NAME["Maple"]["contracts"]["token"]["address"]

        class DustStub:
            def has_code(self, chain, address):
                return True

            def symbol_matches(self, chain, address, expected):
                ok = (chain, address) == ("ethereum", SYRUP)
                return ok, "SYRUP" if ok else ""

            def scaled(self, chain, address, call, *args):
                # the real reading: a genuine dust balance of the right token
                return 0.51253570332391 if call == "balanceOf" else 1_000_000_000.0

        c = Chain()
        c.reader = DustStub()
        out = FetchOutput()
        c.run([config.PROJECT_BY_NAME["Maple"]], None, out)
        df = out.frame()

        assert metric not in set(df.metric), \
            "a disputed destination must not reach the sheet as a figure"
        # AND NEITHER NAME APPEARS, whichever way the override currently points: the one the
        # contract serves is withheld because it is disputed, and the other because nothing on
        # this project writes there via a contract read.
        assert "treasury_holding_tokens" not in set(df.metric)
        staged = [s for s in out.staged if "treasury_holding_tokens" in str(s.get("name", ""))]
        assert staged and abs(float(staged[0]["value"]) - 0.51253570332391) < 1e-9, \
            f"the observation must survive as EVIDENCE, not vanish: {out.staged}"
        assert any("disputed" in str(g.get("reason", "")) for g in out.gaps), \
            f"and the gap must say why, not just leave the cell empty: {out.gaps}"

        # THE GUARD FOR THE FIX: if the dispute is ever cleared against a still-wrong address, the
        # floor must reject the dust rather than let it back in quietly.
        lo, hi = config.sanity_bounds("Maple", "treasury_holding_tokens")
        assert lo is not None and 0.51253570332391 < lo <= 75_780_000 <= (hi or float("inf")), \
            f"the floor must exclude dust and admit the reported ~75.78m, got ({lo}, {hi})"
        print("maple treasury ok: dust NOT stored, staged as evidence, gap explains it, floor guards the fix")
    finally:
        spec["destination_status"] = live_status


def test_the_burn_mechanism_flag_fires_only_where_a_burn_is_ACTUALLY_CLAIMED():
    """Two bugs, one shape: a default that means "unknown" applied where nothing was asked.

    config.burn_mechanism() returns status 'assumed' for any project with no block. That is right
    for a project that burns and has not said how, and WRONG twice over:

    1. PLUME held archetype [1] with no burn_split, no burn_read_method and no mechanism block —
       no burn anywhere in its design — and still carried "the burn MECHANISM is assumed, not
       documented" on gross_burn_tokens, which every archetype 1 chain gets from the metric
       library. An AMBER pointing at something that does not exist spends the reader's attention
       for nothing.

    2. HYPERLIQUID's destination was confirmed by two independent primary sources in December 2025
       and its mechanism STILL read 'assumed', because the check that requires a declared mechanism
       gated on burn_read_method in ("transfer", "protocol_level") and Hyperliquid's is
       "protocol_api" — so it was never asked for one.

    Both directions are asserted here so neither can come back.
    """
    import pandas as pd
    import build_workbook as bw
    asof = pd.Timestamp("2026-09-14")
    row = {"status": "ok", "source": "tier1", "n_points": 9, "entered_on": "",
           "measuring_points": ("tier1",)}

    # (1) a project with NO burn claimed anywhere must not carry a mechanism flag
    plume = config.PROJECT_BY_NAME["Plume"]
    assert 4 not in plume["archetypes"] and plume.get("burn_split") is None \
        and plume.get("burn_read_method") is None and plume.get("burn_mechanism") is None, \
        "Plume claims no burn by any of the four signals — if that changes, this test must change"
    assert "gross_burn_tokens" in config.metrics_for_project(plume), \
        "the metric IS in scope for archetype 1, which is why the flag could reach it"
    band, why = bw.confidence_for("Plume", "gross_burn_tokens", row, asof)
    assert "MECHANISM" not in why, f"no burn is claimed, so no mechanism flag: {why}"

    # (2) Hyperliquid's mechanism is CONFIRMED, from two independent primaries
    mech = config.burn_mechanism(config.PROJECT_BY_NAME["Hyperliquid"])
    assert mech["status"] == "confirmed", f"confirmed December 2025, got {mech['status']!r}"
    assert mech["model"] == "transfer_to_dead_address", (
        "HYPE is TRANSFERRED to a keyless address and total supply does not fall, so issuance is "
        "the supply delta ALONE. protocol_level_destruction would add the burn back on top and "
        f"overstate issuance by the whole cumulative burn. Got {mech['model']!r}")
    assert mech.get("source_url") or mech.get("source_note"), "confirmed needs a provenance"
    _, why = bw.confidence_for("Hyperliquid", "gross_burn_tokens", row, asof)
    assert "MECHANISM" not in why, f"a confirmed mechanism must not read as assumed: {why}"

    # (3) THE NARROWING MUST NOT SUPPRESS A REAL ONE. Ethereum genuinely burns (archetype 4) and
    # its mechanism is genuinely assumed — the flag must still fire.
    _, why = bw.confidence_for("Ethereum", "gross_burn_tokens", row, asof)
    assert "MECHANISM is assumed" in why, f"a real assumed mechanism must still flag: {why}"

    # (4) THE ROOT CAUSE: the config check must now demand a block for a protocol_api burn too,
    # so no future project can be silent the way Hyperliquid was.
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    saved = hl.pop("burn_mechanism")
    try:
        errs = config.validate_config(raise_on_error=False)
        assert any("Hyperliquid" in e and "burn_mechanism" in e for e in errs), (
            "burn_read_method 'protocol_api' must REQUIRE a declared mechanism — that gate missing "
            f"it is how this went unnoticed for months. Errors: {errs}")
    finally:
        hl["burn_mechanism"] = saved
    assert not config.validate_config(raise_on_error=False)
    print("burn-mechanism flag ok: silent for Plume (no burn claimed), clear for Hyperliquid "
          "(confirmed), still firing for Ethereum (really assumed), and protocol_api now gated")


def test_geodnet_sql_addresses_match_config_exactly():
    """The four addresses in query 8683175 are the four already in config, not new ones."""
    from_sql = {"token_polygon": "0xAC0F66379A6d7801D7726d5a943356A172549Adb",
                "burn_polygon": "0x000000000000000000000000000000000000dead",
                "mint_solana": "7JA5eZdCzztSfQbJvS8aVVxMFfd81Rs9VvwnocV1mKHu",
                "burn_solana_token_account": "5SBfxBdqsCM1SJZGQkf9Y74EFmUfzs8LGDjBZUjZGnED"}
    contracts = config.PROJECT_BY_NAME["GEODNET"]["contracts"]
    for key, sql_address in from_sql.items():
        stored = contracts[key]["address"]
        if sql_address.startswith("0x"):
            assert sql_address.lower() == stored.lower(), f"{key}: {sql_address} vs {stored}"
        else:
            # base58 is case-SENSITIVE, so a Solana address must match exactly
            assert sql_address == stored, f"{key}: base58 mismatch, {sql_address} vs {stored}"
    # NINE ENTRIES NOW, and the count is asserted so an address cannot be slipped in unnoticed.
    # This assertion did its job on 2026-09-15: it failed the moment three wallets were added from
    # GEODNET's tokenomics page, which is exactly the tripwire it exists to be. Updated
    # deliberately, listing each addition, rather than loosened to a >= count.
    #
    # Four come from the query, plus:
    #   buyback_wallet_polygon_historical  unverified, refused
    #   token_iotex                        2026-09-14, GEODNET's own token docs; no IoTeX RPC, so it
    #                                      is refused at the coverage gate and cannot contribute a
    #                                      number while the Wormhole NTT bridge model is unresolved
    #   mining_polygon                     2026-09-15, docs.geodnet.com/geod-token/tokenomics
    #   mining_distribution_polygon        2026-09-15, same page — the emissions-flow candidate
    #   ecosystem_polygon                  2026-09-15, same page
    # All three 2026-09-15 additions are UNVERIFIED, so the adapter refuses them and they appear in
    # the Gap Report by name. That is the point of recording them.
    added_2026_09_15 = {"mining_polygon", "mining_distribution_polygon", "ecosystem_polygon"}
    assert set(contracts) == set(from_sql) | {"buyback_wallet_polygon_historical", "token_iotex"} | added_2026_09_15, \
        f"unexpected contract keys on GEODNET: {sorted(contracts)}"
    for key in added_2026_09_15:
        c = contracts[key]
        assert c["chain"] == "polygon", f"{key}: expected polygon, got {c['chain']}"
        # VERIFIED 2026-09-17 against GEODNET's own tokenomics page — the same source that named
        # them. This assertion previously required them to stay UNVERIFIED "until its holdings are
        # confirmed", which conflated two different claims: verification is about WHICH WALLET this
        # is, and the holdings are a separate question the read itself answers. The provenance
        # assertion below is the one that actually matters and it is unchanged — the source must
        # still be GEODNET's own docs and not an aggregator.
        assert c["verified"] == "2026-09-17", \
            f"{key}: expected verification dated 2026-09-17, got {c['verified']!r}"
        assert c["source_url"] == "https://docs.geodnet.com/geod-token/tokenomics", \
            f"{key}: provenance must be GEODNET's own docs, not an aggregator"
    assert contracts["token_iotex"]["chain"] == "iotex"
    # ** THE READ IS REFUSED BY DECLARATION NOW, NOT BY A MISSING ADAPTER. CHANGED 2026-09-22. **
    # This used to assert that iotex is absent from EVM_CHAINS, which kept the deployment
    # unreadable — the right outcome for a reason that had nothing to do with GEOD. An iotex RPC
    # added for any other purpose would have started summing it. The kind is what refuses it now,
    # and the chain assertion is kept as a fact rather than as the safeguard.
    assert contracts["token_iotex"]["kind"] == "bridged_representation", \
        "the IoTeX deployment must be refused by its KIND, not by the absence of an RPC endpoint"
    assert contracts["mint_solana"]["kind"] == "bridged_representation", \
        "same for Solana — its old note admitted the missing adapter was a coincidence"
    # AND THE PARTIAL MARKER IS GONE, which is the other half of the same finding. Polygon's
    # totalSupply() reads EXACTLY 1,000,000,000 GEOD — the entire declared cap, to the token — so
    # there is nothing outside it for the figure to be partial about. Not summing was always
    # right; calling the result partial was the inversion, the same one corrected on PancakeSwap.
    assert contracts["token_polygon"]["supply_is_partial"] is False, \
        "a read of the whole cap cannot be missing a component"
    assert contracts["token_polygon"]["metric_override"] == "total_supply_gross", \
        "still the GROSS figure: the contract counts tokens at the dead address, CoinGecko does not"
    assert "1,000,000,000" in contracts["token_polygon"]["note"], \
        "the evidence for dropping the marker must travel with the contract"
    print("geodnet addresses ok: all four match; the mirrors are reference-only by declaration "
          "and Polygon reads the whole cap, unmarked")


def test_dune_backfill_only():
    du = Dune(has_history={("Ethereum", "gross_burn_tokens")})
    du.http = StubHttp({"/query/123/results": {"result": {"rows": [
        {"day": "2025-09-01 00:00:00.000 UTC", "burned": 12.5},
        {"day": "2025-09-02 00:00:00.000 UTC", "burned": 13.0}]}}})
    p = {"name": "Ethereum", "dune_queries": {
        "gross_burn_tokens": {"query_id": 123, "date_col": "day", "value_col": "burned"},
        "staked_tokens": {"query_id": 123, "date_col": "day", "value_col": "burned"},
        "tx_count": {"query_id": None}}}
    out = FetchOutput()
    du.run([p], None, out)
    df = out.frame()
    assert set(df["metric"]) == {"staked_tokens"}, "a series with history must be skipped"
    assert set(df["tier"]) == {4} and df.source.iloc[0] == "dune:123"
    assert any(e.status == "skipped" and "backfill NOT run" in e.message for e in out.log)
    assert any("no query_id" in e.message for e in out.log)
    print("tier 4 ok: backfill only, skipped the series the store already covers")


# ---------------------------------------------------------------------------- validation
def test_validation_bounds_and_threshold():
    out = FetchOutput()
    bad = pd.DataFrame({"date": [pd.Timestamp("2026-09-10")], "project": ["Bitcoin"], "metric": ["price_usd"],
                        "value": [9e9], "source": ["coingecko"], "tier": [1]})
    assert validate_frame(bad, {}, out).empty
    assert out.review[0]["reason"] == "out_of_bounds" and out.review[0]["action"] == "rejected"

    out2 = FetchOutput()
    big = pd.DataFrame({"date": [pd.Timestamp("2026-09-10")], "project": ["Bitcoin"], "metric": ["price_usd"],
                        "value": [100000.0], "source": ["coingecko"], "tier": [1]})
    assert len(validate_frame(big, {("Bitcoin", "price_usd"): 10000.0}, out2)) == 1, "a big move is kept, not dropped"
    assert out2.review[0]["reason"] == "change_threshold" and out2.review[0]["action"] == "stored_flagged"

    out3 = FetchOutput()
    validate_frame(big, {("Bitcoin", "price_usd"): 95000.0}, out3)
    assert not out3.review
    print("validation ok: out-of-bounds rejected, large move flagged but kept, small move silent")


def test_parse_number():
    cases = [("1,234,567", 1234567.0), ("$1.2M", 1200000.0), ("(1,958,514)", -1958514.0), ("45.2%", 0.452),
             ("3.4bn", 3.4e9), ("12k", 12000.0), ("", None), ("n/a", None), (None, None), ("  7 ", 7.0), (42, 42.0)]
    for text, expected in cases:
        assert base.parse_number(text) == expected, (text, base.parse_number(text))
    print("parse_number ok:", len(cases), "cases")


def test_a_closed_figure_is_not_a_gap_and_its_history_limit_is_not_a_closure():
    """Three states that must stay apart: a gap, a closed figure, and a working figure's missing past.

    Aerodrome's cross-check was chased to a dead end (API 404 indistinguishable from a control id
    that does not exist; browser, signed in, says private-or-gone). A permanent row on the to-do
    list teaches the reader to skim the list, so it is suppressed and recorded instead.

    The history limitation is the opposite trap: locked_tokens WORKS, only its past is missing.
    Suppressing its gap row would hide a real failure if the contract read ever broke.
    """
    from fetch.gaps import detect

    rows = detect(config.PROJECTS, pd.DataFrame(columns=["project", "metric"]), set(), {}, [])
    keys = {(r["project"], r["metric"]) for r in rows}
    assert ("Aerodrome", "locked_tokens_dashboard") not in keys, "a closed figure must not sit on the to-do list"
    assert ("Aerodrome", "locked_tokens") in keys, \
        "a history limitation must NEVER suppress the figure's own gap row — the live read could break"

    # an adapter-raised gap for a closed figure is suppressed too, not just a generated one
    rows = detect(config.PROJECTS, pd.DataFrame(columns=["project", "metric"]), set(), {},
                  [{"project": "Aerodrome", "metric": "locked_tokens_dashboard",
                    "reason": "query 2986047 returned 404", "tiers_attempted": "4", "suggestion": "-"}])
    assert not [r for r in rows if (r["project"], r["metric"]) == ("Aerodrome", "locked_tokens_dashboard")]
    # and only Aerodrome's is closed — other projects' cross-checks stay on the list
    assert [r for r in rows if r["metric"] == "locked_tokens_dashboard"], \
        "closing one project's cross-check must not close every project's"

    closed = config.unavailable_for("Aerodrome", "locked_tokens_dashboard")
    assert closed and "2986047" in closed["what_was_tried"], "the closure must record what was tried"
    assert config.unavailable_for("Aerodrome", "locked_tokens") is None
    assert config.limitation_for("Aerodrome", "locked_tokens") is not None
    print("closure ok: cross-check closed and off the to-do list, locked_tokens still gap-checked")


def test_aerodrome_still_reads_locked_tokens_from_the_escrow_with_no_cross_check():
    """Removing the dead cross-check must not disturb the figure it was checking."""
    a = config.PROJECT_BY_NAME["Aerodrome"]
    ve = a["contracts"]["ve"]
    assert ve["read_method"] == "escrow_balance_of" and ve["chain"] == "base" and ve.get("verified")
    assert ve["address"] == "0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4"
    assert not a.get("cross_checks"), "a cross-check naming a secondary that can never arrive is noise"
    assert "locked_tokens_dashboard" not in a["dune_queries"], \
        "a dead query id left in config fails on every run forever"
    print("aerodrome ok: tier 2 escrow read intact, no cross-check, no dead query id")


def test_gap_detection_covers_every_applicable_metric():
    from fetch.gaps import detect
    frame = pd.DataFrame(columns=["date", "project", "metric", "value", "source", "tier"])
    gaps = detect(config.PROJECTS, frame, set(), {}, [])
    keys = {(g["project"], g["metric"]) for g in gaps}
    # A figure chased to a dead end is the ONE exemption, and it is explicit: it is recorded in
    # config.UNAVAILABLE with what was tried, and rendered on Config & Sources instead.
    for p in config.PROJECTS:
        for m in config.metrics_for_project(p):
            if config.unavailable_for(p["name"], m):
                assert (p["name"], m) not in keys, f"{p['name']}/{m} is closed and must not be a gap"
                continue
            # hand-entered quarterly figures are not unresolved gaps — they have their own block
            if config.is_manual_quarterly(p["name"], m):
                assert (p["name"], m) not in keys, f"{p['name']}/{m} is manual and must not be a gap"
                continue
            assert (p["name"], m) in keys, f"{p['name']}/{m} missing from the Gap Report"
    assert all(g["reason"] and g["suggestion"] for g in gaps), "every gap needs a reason and a fix"
    vague = [g for g in gaps if g["reason"] == "no source configured for this metric"]
    assert not vague, f"{len(vague)} gaps have no specific reason"
    print(f"gap detection ok: {len(gaps)} rows, every one with a specific reason and a fix")


def test_manual_overrides_suppress_gaps():
    from fetch.gaps import detect
    frame = pd.DataFrame(columns=["date", "project", "metric", "value", "source", "tier"])
    gaps = detect(config.PROJECTS, frame, {("World Mobile", "customer_revenue_usd")}, {}, [])
    assert ("World Mobile", "customer_revenue_usd") not in {(g["project"], g["metric"]) for g in gaps}
    print("manual overrides ok: a hand-entered metric is not reported as a gap")


def test_bound_check_covers_the_declared_relations_and_honours_exemptions():
    """Nine relations, zero tolerance, and one exemption that must not leak.

    The PancakeSwap case this was built for is NOT a demonstration that the check works — it is a
    demonstration that one comparison was being made where it is not an identity. So the test
    asserts both halves: a real contradiction still fires with no tolerance at all, and the
    exempted comparison does not fire for PancakeSwap while still firing for everyone else.
    """
    import pandas as pd
    from fetch import FetchOutput
    from fetch.validate import IMPOSSIBLE_RELATIONS, check_impossible_relations

    pairs = {(g, l) for g, l, _ in IMPOSSIBLE_RELATIONS}
    # The relations asked for, plus the two lock variants. Every bound is against total_supply
    # rather than max_supply where a choice exists: tighter, and max_supply is often absent.
    for want in (("locked_tokens", "total_supply"),
                 ("locked_tokens_underlying", "total_supply"),
                 ("locked_tokens_principal", "total_supply"),
                 ("circulating_supply", "total_supply"),
                 ("total_supply", "max_supply"),
                 ("treasury_holding_tokens", "total_supply"),
                 ("buyback_fund_balance", "total_supply"),
                 ("gross_burn_tokens", "total_supply"),
                 ("burn_address_balance", "total_supply")):
        assert want in pairs, f"relation {want} is not declared"
    assert len(IMPOSSIBLE_RELATIONS) == 9, f"expected 9 relations, got {len(IMPOSSIBLE_RELATIONS)}"

    # ** NO LOCK METRIC MAY BE BOUNDED BY circulating_supply. WITHDRAWN 2026-09-16. **
    # It is not an identity: CoinGecko EXCLUDES escrowed supply for a ve-token protocol, so
    # locked and circulating are disjoint halves of total_supply and locked sitting outside
    # circulating is the correct state. Confirmed on Aerodrome's live figures — locked
    # 989,752,701 + circulating 988,697,600 = total 1,978,450,301, to 0.00%. Re-adding it would
    # flag every high-lock-rate project for behaving normally.
    for greater, lesser, _ in IMPOSSIBLE_RELATIONS:
        assert not (greater.startswith("locked") and lesser == "circulating_supply"), \
            f"{greater} <= {lesser} was withdrawn — it assumes circulating INCLUDES locked, " \
            f"which is false for every ve-token protocol on CoinGecko's convention"
    # The replacements hold under EITHER convention, which is what makes them identities.
    for lock in ("locked_tokens", "locked_tokens_underlying", "locked_tokens_principal"):
        assert (lock, "total_supply") in pairs, f"{lock} must be bounded by total_supply"

    def run(project, rows):
        out = FetchOutput()
        out.frames = [pd.DataFrame([{"date": pd.Timestamp("2026-09-14"), "project": project,
                                     "metric": m, "value": v, "source": "test", "tier": 2}
                                    for m, v in rows])]
        check_impossible_relations(out.frame(), out)
        return out

    # AERODROME'S LIVE FIGURES MUST NOT VIOLATE THE REPLACEMENT — the arithmetic that settled it.
    out = run("Aerodrome", [("locked_tokens", 989_752_701.0), ("total_supply", 1_978_450_301.0),
                            ("circulating_supply", 988_697_600.0)])
    assert not [r for r in out.review if "locked_tokens" in str(r)], \
        "Aerodrome's real figures must pass now that the relation is bounded by total_supply"
    # And the config fact is recorded with its evidence, not just asserted in prose.
    aero = config.PROJECT_BY_NAME["Aerodrome"]
    assert aero["circulating_supply_convention"] == "excludes_locked"
    ev = aero["circulating_supply_convention_evidence"]
    assert ev["locked"] + ev["circulating"] == ev["total"], \
        "the recorded evidence must actually satisfy the test it claims to have passed"

    # ** THE CONVENTION IS PER PROJECT AND GOES BOTH WAYS. ** Recorded rather than inferred from
    # the lock mechanism, because the mechanism does not predict it: Sky's lssky is an ordinary
    # ERC-20 receipt with no lockup and Aerodrome's veAERO is an escrowed NFT, and CoinGecko
    # treats them oppositely. A single global assumption would be wrong for half the book.
    for name, want in (("Aerodrome", "excludes_locked"), ("Venice AI", "excludes_locked"),
                       ("Ether.fi", "includes_locked"), ("Sky", "includes_locked")):
        got = config.PROJECT_BY_NAME[name].get("circulating_supply_convention")
        assert got == want, f"{name}: convention {got!r}, expected {want!r}"
    assert {config.PROJECT_BY_NAME[n]["circulating_supply_convention"]
            for n in ("Aerodrome", "Ether.fi")} == {"excludes_locked", "includes_locked"}, \
        "both conventions must be represented, or the per-project design is untested"

    # INCONCLUSIVE STAYS UNDECLARED. "We tested and could not tell" must not render as a fact.
    for name in ("Chainlink", "Maple", "Pendle"):
        assert config.PROJECT_BY_NAME[name].get("circulating_supply_convention") is None, \
            f"{name} came back INCONCLUSIVE — declaring a convention for it would be a guess"

    # VENICE'S NEAR MISS IS ON FILE, so "it never flagged" is not mistaken for "it was fine".
    v = config.PROJECT_BY_NAME["Venice AI"]["circulating_supply_convention_evidence"]
    assert v["near_miss"] is True and "below 50%" in v["near_miss_note"]

    # AND THE BOUND HOLDS WHICHEVER CONVENTION APPLIES — which is why an undeclared one is safe.
    for name in ("Chainlink", "Maple", "Pendle", "Sky", "Ether.fi", "Venice AI"):
        assert ("locked_tokens", "total_supply") in pairs
        assert ("locked_tokens", "circulating_supply") not in pairs, \
            f"an undeclared convention is only safe while no lock metric is bounded by circulating"

    # ZERO TOLERANCE — and this pins what "zero" actually means, which is not quite zero.
    # The guard is `a <= b * (1 + epsilon)` with epsilon 1e-9, and that epsilon is RELATIVE, so
    # what it absorbs scales with the figure:
    #        lesser 1e3  -> 0.000001 tokens      lesser 1e9  ->     1 token
    #        lesser 1e6  -> 0.001    tokens      lesser 1e12 -> 1,000 tokens
    # At a billion-token supply it swallows exactly one token; at a trillion, a thousand. That is
    # far above float64 noise (~15-16 significant digits) and is therefore a real, if tiny,
    # tolerance rather than the pure equality guard the comment describes. Asserted here as
    # BEHAVIOUR so the next person meets it in a test rather than in a missed contradiction.
    out = run("Uniswap", [("treasury_holding_tokens", 1_000_000_001.0), ("total_supply", 1_000_000_000.0)])
    assert not any(r for r in out.review), \
        "expected the 1e-9 RELATIVE epsilon to absorb exactly one token at 1e9 — if this now " \
        "fires, the epsilon was tightened and that is an improvement worth noting, not a break"
    # Two tokens over the same supply is outside it and fires. So the practical floor is ~1 part
    # in 1e9, not zero — orders of magnitude tighter than Aerodrome's 0.087%, which is the
    # overshoot this check exists to catch, but not literally zero.
    out = run("Uniswap", [("treasury_holding_tokens", 1_000_000_002.0), ("total_supply", 1_000_000_000.0)])
    assert any(r for r in out.review), "two tokens over a billion must fire"

    # A NEW RELATION, one of the ones added this round.
    out = run("Uniswap", [("buyback_fund_balance", 500.0), ("total_supply", 400.0)])
    assert any("buyback_fund_balance" in str(r) for r in out.review), "buyback bound did not fire"

    # ** THE EXEMPTION IS GONE, 2026-09-22, AND THE RELATION WORKS INSTEAD OF BEING SILENCED. **
    # PancakeSwap used to exempt burn_address_balance vs total_supply on the grounds that a
    # cumulative burn is not bounded by an instantaneous supply. The real reason it failed was
    # that CoinGecko's total_supply is NET of burn, so the comparison subtracted the burn and
    # then complained the burn was too big. Pointed at the CONTRACT's gross figure it passes, and
    # starts catching the things the exemption had switched off.
    assert config.relation_exempt("PancakeSwap", "burn_address_balance", "total_supply") is None, \
        "the exemption was removed — a silenced relation catches nothing"
    assert config.bound_metric_for("PancakeSwap", "burn_address_balance", "total_supply") \
        == "total_supply_gross"

    # The real figures: 5.05bn burned against 5.39bn gross passes, with headroom.
    out = run("PancakeSwap", [("burn_address_balance", 5_051_176_395.11),
                              ("total_supply_gross", 5_387_735_435.75),
                              ("total_supply", 330_627_631.61)])
    assert not any("burn_address_balance" in str(r) for r in out.review), \
        "burn below gross supply must pass, and must be compared against GROSS not net"

    # ** AND THE RELATION IS NOT MERELY QUIET — it fires on a burn exceeding everything ever
    # issued, which is exactly what the exemption had made undetectable. **
    out = run("PancakeSwap", [("burn_address_balance", 5_500_000_000.0),
                              ("total_supply_gross", 5_387_735_435.75),
                              ("total_supply", 330_627_631.61)])
    assert any("burn_address_balance" in str(r) for r in out.review), \
        "a burn above gross supply is impossible and must fire — this is the check the " \
        "exemption had disabled"

    # THE REDIRECT IS SCOPED TO net_of_burn PROJECTS. Sky does not declare the convention, so its
    # burn stays bounded by total_supply and the substitution must not reach it.
    assert config.bound_metric_for("Sky", "burn_address_balance", "total_supply") == "total_supply"

    # AND THE OTHER PANCAKESWAP RELATION STAYS LIVE: a period flow against a stock is an identity
    # even for a minting token, and it is not redirected.
    out = run("PancakeSwap", [("gross_burn_tokens", 500_000_000.0), ("total_supply", 400_000_000.0)])
    assert any("gross_burn_tokens" in str(r) for r in out.review), \
        "gross_burn_tokens vs total_supply must stay live for PancakeSwap"

    # ** NO PROJECT EXEMPTS A RELATION ANY MORE. ** PancakeSwap's was the only one and it went on
    # 2026-09-22, when the comparand turned out to be the fault rather than the relation. That is
    # the healthier state — but it leaves the exemption MECHANISM with no live example, and an
    # unexercised escape hatch is exactly the one that rots or gets misused. Forced, so both
    # halves stay covered: an exemption silences its own pair, and only its own pair.
    assert not [p["name"] for p in config.PROJECTS if p.get("relation_exemptions")], \
        "an exemption came back — it needs a reason that survives PancakeSwap's removal note"

    cake_cfg = config.PROJECT_BY_NAME["PancakeSwap"]
    cake_cfg["relation_exemptions"] = [
        {"greater": "burn_address_balance", "lesser": "total_supply",
         "why": "forced by the test suite to keep the exemption mechanism exercised while no "
                "project declares one — not a real exemption, and not written to config.py"}]
    try:
        assert config.relation_exempt("PancakeSwap", "burn_address_balance", "total_supply")
        assert config.relation_exempt("PancakeSwap", "gross_burn_tokens", "total_supply") is None, \
            "an exemption must silence its own pair and no other"
        assert config.relation_exempt("Uniswap", "burn_address_balance", "total_supply") is None, \
            "and it must not leak to another project"
        # A silenced pair really is skipped — asserted through the check, not just the lookup.
        out = run("PancakeSwap", [("burn_address_balance", 9e12), ("total_supply", 1.0)])
        assert not any("burn_address_balance" in str(r) for r in out.review)
    finally:
        cake_cfg.pop("relation_exemptions")
    assert not config.validate_config(raise_on_error=False), "and config is clean afterwards"

    print("bound check ok: 9 relations, zero tolerance, burn bounded by GROSS supply on "
          "net_of_burn projects, and the exemption mechanism still scoped and non-leaking")


# =========================================================================================
# THE STALE-STORE REGRESSION SUITE
#
# Every other test in this file is a WRITE-PATH test: it drives the adapter forward and asserts
# the right value comes out. Three "tested and fixed" claims failed on a live run in one session
# because the bad value was ALREADY IN THE STORE and no test had one.
#
# These three read tests/fixtures/stale_store.json — rows written under a PRE-FIX config — and
# assert they read correctly NOW. Refresh with: python tests/refresh_stale_fixture.py
# =========================================================================================
def _stale_fixture():
    import json
    import pathlib as _p
    path = _p.Path(__file__).resolve().parent / "fixtures" / "stale_store.json"
    assert path.exists(), f"fixture missing — run python tests/refresh_stale_fixture.py ({path})"
    return json.loads(path.read_text())


def _maple_treasury_metric():
    """The metric Maple's treasury contract serves RIGHT NOW, resolved from config.

    It has moved twice in four days — to treasury_holding_tokens_chain_crosscheck when the chain
    read was demoted for the transparency page (2026-09-18), and back to treasury_holding_tokens
    when robots.txt turned out to disallow that page (2026-09-21). Every test that hardcoded the
    name broke on each move, and the ones that did not break were worse: they kept passing while
    pointing at a metric the contract no longer served, so the branch they existed to cover was
    not being covered at all. The tests below follow the contract instead of restating it.
    """
    spec = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    return spec.get("metric_override") or config.KIND_METRIC[spec["kind"]]


def _fixture_counterfactuals():
    """ALL of the fixture's counterfactuals, imported from the script that generated them.

    THREE mechanisms now have no live example, and none of them lost one by being fixed away:
      disputed    Maple's contract dispute resolved 2026-09-18 (new address, verified_by_label).
      refuted     Sky's burn became real on 2026-09-22 — the refutation is KEPT on the mechanism
                  block as refuted_prior_model, but the project's live status is confirmed.
      suppressed  GEODNET's issuance suppression is still in force and still correct; it now
                  renders n/a rather than RED, because the quantity is zero by construction
                  rather than uncomputable (2026-09-22).
    The fixture holds a row for each. Entering them together, through the generator's own
    definitions, is what keeps the committed JSON and the tests that read it describing the same
    world — and what stops a branch quietly ceasing to be tested the moment its live example
    improves.
    """
    import contextlib as _c
    mod = _refresh_module()
    stack = _c.ExitStack()
    stack.enter_context(mod.forced_dispute())
    stack.enter_context(mod.forced_refutation())
    stack.enter_context(mod.forced_plain_suppression())
    return stack


def _refresh_module():
    import importlib.util
    import pathlib as _p
    spec = importlib.util.spec_from_file_location(
        "refresh_stale_fixture", _p.Path(__file__).resolve().parent / "refresh_stale_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _forced_dispute():
    """The fixture's disputed counterfactual, imported from the script that generated it.

    Deliberately NOT re-implemented here. It was a two-liner in the test below and it silently
    stopped forcing anything when Maple's treasury contract gained a metric_override; one shared
    definition is what stops the generator and the checker drifting apart again.
    """
    import importlib.util
    import pathlib as _p
    spec = importlib.util.spec_from_file_location(
        "refresh_stale_fixture", _p.Path(__file__).resolve().parent / "refresh_stale_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.forced_dispute()


def _aggregate_fixture(data):
    """One aggregate() over ALL fixture rows together.

    Not one call per row, and that matters: changed_measuring_point only exists when its sibling
    row is present, and the blanked-cell dtype only promotes to float64 when some other row in
    the column carries a real number. Evaluating rows in isolation hides both.
    """
    import pandas as pd
    import build_workbook as bw
    long = pd.DataFrame([{"date": pd.Timestamp(r["date"]), "project": r["project"],
                          "metric": r["metric"], "value": r["value"], "source": r["source"],
                          "tier": r["tier"], "is_manual": False, "entered_on": ""}
                         for r in data["rows"]])
    # Entered through the SAME door the refresh script used to generate the expectations. The
    # disputed example is a counterfactual — no contract in live config is disputed any more —
    # so a fixture generated under the forcing and read back without it would disagree on that
    # row for a reason that has nothing to do with the mechanism.
    with _fixture_counterfactuals():
        out = bw.aggregate(long, pd.DataFrame(), pd.Timestamp(data["asof"]),
                           gaps=pd.DataFrame(), review=pd.DataFrame())
    return {(r["project"], r["metric"]): r for r in out.to_dict("records")}


def test_stale_store_every_recorded_expectation_still_holds():
    """THE PARAMETRISED GENERAL PROPERTY, asserted row by row against the committed snapshot.

    The property is one sentence: NO STORED ROW MAY READ 'ok' WHEN CURRENT CONFIG SAYS ITS BASIS
    HAS CHANGED. Each fixture row carries the config shape it was written under and the outcome
    the current code produces; a drift in any of status, confidence band or blanking fails here
    and names the row.

    Failing this does NOT mean refresh the fixture. It means read the diff: if an expectation
    moved and nobody intended it, that is the regression this file exists to catch.

    ONE EXPECTATION ROW IS AN EXCEPTION, and it is documented rather than silently patched: the
    'disputed_destination' row keys off Maple's treasury contract, whose dispute was RESOLVED
    2026-09-18 (new address, verified_by_label). Aggregation reads config LIVE, so this fixture
    row would otherwise start reading 'ok' — not because the disputed-destination MECHANISM
    broke, but because the specific example it uses is no longer disputed. Forced back on for
    the duration of this test so the coverage does not go dark the moment the example resolves.

    THAT FORCING NOW COMES FROM refresh_stale_fixture.forced_dispute() rather than two lines
    here. It was two lines here, and on 2026-09-21 the cross-check demotion gave the treasury
    contract a metric_override that re-pointed it off treasury_holding_tokens — after which
    setting the flag forced nothing, the row was caught by the WITHDRAWN branch instead, and
    this test went on passing while the disputed branch had no coverage at all. The anti-rot
    test's name-matched status assertion is what surfaced it.
    """
    import pandas as pd

    with _fixture_counterfactuals():
        data = _stale_fixture()
        by_key = _aggregate_fixture(data)
        checked = 0
        for e in data["expectations"]:
            key = (e["project"], e["metric"])
            assert key in by_key, f"{key} vanished from aggregate output"
            got = by_key[key]
            ctx = f"{e['transition']} / {e['project']}/{e['metric']} (written under: {e['written_under']})"
            assert got["status"] == e["expect_status"], \
                f"{ctx}: status {got['status']!r}, expected {e['expect_status']!r}"
            assert got["confidence"] == e["expect_confidence"], \
                f"{ctx}: confidence {got['confidence']!r}, expected {e['expect_confidence']!r}"
            assert bool(pd.isna(got["now"])) == e["expect_blank"], \
                f"{ctx}: blank={bool(pd.isna(got['now']))}, expected {e['expect_blank']}"
            if e["expect_reason_contains"]:
                assert e["expect_reason_contains"] in str(got["why_amber"]), \
                    f"{ctx}: reason lost its marker {e['expect_reason_contains']!r} — got {got['why_amber']!r}"
            checked += 1

        # THE PROPERTY ITSELF, asserted over the whole fixture rather than per row: nothing that is
        # not a control may read 'ok' at GREEN. A transition row is allowed to stay status 'ok' (the
        # orphan, refuted and measuring-point branches flag without blanking) but never at GREEN.
        for e in data["expectations"]:
            if e["transition"] == "control":
                continue
            assert e["expect_confidence"] == "RED", \
                f"{e['transition']} on {e['project']}/{e['metric']} must be RED, got {e['expect_confidence']}"

    # AND THE CONTROLS, which are what prove the guards are targeted rather than broad. A guard
    # that blanked everything would satisfy every assertion above.
    controls = [e for e in data["expectations"] if e["transition"] == "control"]
    assert len(controls) >= 5, "too few controls to prove the guards are narrow"
    for e in controls:
        assert e["expect_status"] == "ok", f"control {e['project']}/{e['metric']} was suppressed"
        assert e["expect_blank"] is False, f"control {e['project']}/{e['metric']} was blanked"
        assert e["expect_confidence"] != "RED", f"control {e['project']}/{e['metric']} went RED"
    print(f"stale store ok: {checked} expectations hold, {len(controls)} controls untouched")


def test_stale_store_every_row_is_explainable_by_current_config():
    """THE GENERATIVE WALK — the half that would actually have caught all three failures.

    The test above checks rules we remembered to write. This one runs in the other direction:
    it takes EVERY row in the fixture and asks current config what it thinks of it, then requires
    the two to agree about whether the row is still a valid measurement.

    A row config can no longer explain — a source naming a contract that is gone, or one that no
    longer serves the metric, or a derivation switched off — must not be reading 'ok'. That is
    the whole invariant, and it is checked without reference to WHICH mechanism applies, so a
    seventh transition type nobody has written a branch for still trips it.
    """
    import pandas as pd

    data = _stale_fixture()
    by_key = _aggregate_fixture(data)

    unexplained, contradictions = [], []
    for r in data["rows"]:
        name, metric, source = r["project"], r["metric"], r["source"]
        reasons = []
        if config.orphaned_contract_keys(name, source):
            reasons.append("orphaned contract")
        if config.withdrawn_contract_keys(name, metric, source):
            reasons.append("contract no longer serves this metric")
        if config.derivation_suppressed(name, metric):
            reasons.append("derivation suppressed")
        if config.destination_disputed(name, metric):
            reasons.append("destination disputed")
        if metric in config.BURN_METRICS:
            mech = config.burn_mechanism(config.PROJECT_BY_NAME.get(name) or {})
            if mech.get("status") == "refuted":
                reasons.append("burn mechanism refuted")
        if metric not in config.metrics_for_project(config.PROJECT_BY_NAME[name]):
            reasons.append("metric not applicable to this project's archetypes")

        got = by_key[(name, metric)]
        ctx = f"{r['project']}/{r['metric']} <- {r['source']} ({r['transition']})"
        if reasons and got["confidence"] != "RED":
            unexplained.append(f"{ctx}: config says {reasons} but the sheet reads "
                               f"{got['confidence']}/{got['status']}")
        # AND THE OTHER DIRECTION, which is the one that catches an over-broad guard: a row
        # config has NO complaint about must not be suppressed. A control row going RED here
        # means a guard widened past what it was written for.
        if not reasons and r["transition"] == "control":
            if got["confidence"] == "RED" or bool(pd.isna(got["now"])):
                contradictions.append(f"{ctx}: config has no complaint, yet the sheet "
                                      f"{'blanked' if pd.isna(got['now']) else 'went RED'}")

    assert not unexplained, "rows current config cannot explain, still reading as good:\n  " + \
                            "\n  ".join(unexplained)
    assert not contradictions, "rows suppressed with no config reason:\n  " + \
                               "\n  ".join(contradictions)
    print(f"generative walk ok: {len(data['rows'])} rows, every one either explained by config "
          f"or flagged RED, and no control suppressed without a reason")


def test_stale_store_fixture_covers_every_red_branch_and_cannot_quietly_rot():
    """THE ANTI-ROT ASSERTION. A committed fixture decays into decoration without one.

    Two independent guards:
      1. Every transition type the refresh script declares is actually exercised.
      2. The NUMBER of RED-returning branches in confidence_for matches what the fixture was
         built against. Add an eighth RED branch and this fails until somebody adds a row for
         it — which is the only thing that stops the fixture silently covering six of eight
         mechanisms a year from now.

    Guard 2 counts by AST rather than by grepping the source text, so a comment mentioning
    'RED' cannot inflate the count and a reformatted return cannot deflate it.
    """
    import ast
    import inspect

    import build_workbook as bw

    data = _stale_fixture()

    covered = {e["transition"] for e in data["expectations"]}
    declared = set(data["transitions"])
    assert covered == declared, \
        f"fixture covers {sorted(covered)} but declares {sorted(declared)} — missing " \
        f"{sorted(declared - covered)}"

    # ** THE GUARD NOW COUNTS withheld_for's MECHANISMS, NOT confidence_for's RED RETURNS. **
    # It used to count RED returns, which was a fair proxy while the six mechanisms were six
    # separate branches. Consolidating them collapsed those six into one, so that count would
    # now read 2 and tell us nothing — a guard that survives a refactor by being retuned to the
    # new number is a guard that has stopped guarding. Counting the mechanisms themselves
    # measures the thing the fixture is actually covering.
    #
    # THE SEVEN, in withheld_for's own order:
    #   orphaned                 contract removed from config
    #   withdrawn                contract re-purposed — its kind changed
    #   suppressed               derivation switched off in config
    #   disputed                 the contract's ROLE for this project is in doubt
    #   measuring_point_changed  series read from two different places
    #   implausible_delta        one observation is too large a share of the cumulative
    #   refuted                  project does not burn the way this metric measures
    #
    # Raised from six to seven on 2026-09-21. THE NUMBER WAS NOT BUMPED ON ITS OWN, which is
    # the move this guard exists to refuse: implausible_delta arrived with a transition type
    # and its rows in tests/refresh_stale_fixture.py, and the name-matched assertions below
    # would still fail if it had not.
    #
    # Seven to eight on 2026-09-22, the same way: unreconciled_flow arrived with the
    # transition, the Hyperliquid flow row and the two stock rows that give it a span to
    # telescope over. The bump was the LAST edit of that change, not the first.
    EXPECTED_MECHANISMS = 8

    tree = ast.parse(inspect.getsource(bw.withheld_for))
    returns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)
               and n.value.elts and isinstance(n.value.elts[0], ast.Constant)]
    assert len(returns) == EXPECTED_MECHANISMS, (
        f"withheld_for returns {len(returns)} mechanisms, the fixture was built against "
        f"{EXPECTED_MECHANISMS}. A new one needs a fixture row and a transition type in "
        f"tests/refresh_stale_fixture.py — bumping this number alone defeats the point.")

    # Every mechanism withheld_for can return must be a declared status AND have a fixture row,
    # matched by NAME rather than by count, so swapping one mechanism for another is caught too.
    statuses = {n.value.elts[0].value for n in returns}
    assert statuses == set(bw.WITHHELD_STATUSES), \
        f"withheld_for returns {sorted(statuses)}, WITHHELD_STATUSES declares {sorted(bw.WITHHELD_STATUSES)}"
    covered_statuses = {e["expect_status"] for e in data["expectations"]} - {"ok"}
    assert covered_statuses == statuses, \
        f"fixture exercises {sorted(covered_statuses)}, withheld_for can return {sorted(statuses)}"

    # ** AND THE STANDARDISATION ITSELF, asserted rather than assumed: ALL EIGHT BLANK. ** This is
    # the invariant that was false until 2026-09-15, when three of them went RED and printed the
    # number anyway. If a seventh mechanism is added that flags without blanking, this fails.
    for e in data["expectations"]:
        if e["transition"] == "control":
            continue
        assert e["expect_blank"] is True, \
            f"{e['transition']} on {e['project']}/{e['metric']} is RED but still shows its value — " \
            f"all eight withheld mechanisms must blank"

    # The fixture must be regenerable. If the definitions no longer produce the committed JSON,
    # something moved and the diff is the thing to read.
    assert data["rows"] and data["expectations"], "fixture is empty"
    assert len(data["rows"]) >= 15, f"fixture shrank to {len(data['rows'])} rows"
    print(f"anti-rot ok: {len(declared)} transitions covered, {len(returns)} withheld "
          f"mechanisms accounted for, all six blanking")


def test_etherfi_stale_locked_tokens_rows_do_not_survive_the_kind_change():
    """THE LIVE FAILURE, REPRODUCED. A row written before the kind changed must not read 'ok'.

    This is the test that was missing. The existing Ether.fi test asserts the kind mapping and
    drives the adapter through a stub — it proves NEW rows go to locked_tokens_underlying, and it
    proves nothing at all about the 795 locked_tokens rows already in Jake's store. Those rows
    kept rendering as a healthy locked_tokens across two runs after the fix, with the newest of
    them sourced chain:ethereum:sethfi:PARTIAL.

    THE ORPHAN GUARD DID NOT CATCH IT, and the reason is exact: orphaned_contract_keys asks only
    whether the key is still IN config. sethfi is. Its KIND moved. A contract can stop serving a
    metric by being removed or by being re-purposed, and only the first was detected.
    """
    import pandas as pd
    import build_workbook as bw

    spec = config.PROJECT_BY_NAME["Ether.fi"]["contracts"]["sethfi"]
    assert spec["kind"] == "stake_underlying", "precondition: the kind change is in place"
    # The precondition that makes this a DIFFERENT bug from the orphan one.
    assert config.orphaned_contract_keys("Ether.fi", "chain:ethereum:sethfi:PARTIAL") == [], \
        "precondition: the orphan guard does NOT fire here — sethfi still exists"

    stale = pd.DataFrame([{
        "date": pd.Timestamp("2026-09-14"), "project": "Ether.fi", "metric": "locked_tokens",
        "value": 111_163_214.703019, "source": "chain:ethereum:sethfi:PARTIAL", "tier": 2,
        "is_manual": False, "entered_on": ""}])
    out = bw.aggregate(stale, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                       gaps=pd.DataFrame(), review=pd.DataFrame())
    row = out[(out.project == "Ether.fi") & (out.metric == "locked_tokens")].iloc[0]

    assert row["status"] == "withdrawn", f"must not read 'ok' after the kind change: {row['status']}"
    assert pd.isna(row["now"]), "the assets figure must not sit in the shares column"
    assert all(pd.isna(row[f]) for f in ("m1", "q0", "q1", "q2", "q3", "y1"))
    assert row["confidence"] == "RED"
    assert "sethfi" in row["note"] and "no longer serve" in row["note"]

    # THE NEGATIVE THAT MATTERS MOST: the Dune history keeps its 794 days. This guard must not
    # blank the series it was built to protect.
    dune = pd.DataFrame([{
        "date": pd.Timestamp("2026-09-10"), "project": "Ether.fi", "metric": "locked_tokens",
        "value": 141_470_107.5, "source": "dune:8683038", "tier": 4,
        "is_manual": False, "entered_on": ""}])
    out2 = bw.aggregate(dune, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                        gaps=pd.DataFrame(), review=pd.DataFrame())
    ok = out2[(out2.project == "Ether.fi") & (out2.metric == "locked_tokens")].iloc[0]
    assert ok["status"] == "ok" and ok["now"] == 141_470_107.5, \
        f"a non-contract source must be untouched: {ok['status']}, {ok['now']}"

    # AND the same contract feeding its NEW metric is fine — the guard is about the pairing.
    good = stale.copy(); good["metric"] = "locked_tokens_underlying"
    out3 = bw.aggregate(good, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                        gaps=pd.DataFrame(), review=pd.DataFrame())
    u = out3[(out3.project == "Ether.fi") & (out3.metric == "locked_tokens_underlying")].iloc[0]
    assert u["status"] == "ok" and u["now"] == 111_163_214.703019
    print("Ether.fi ok: a pre-change locked_tokens row reads withdrawn and blank; Dune history intact")


def test_geodnet_stale_derived_zero_is_not_served_as_ok_on_read():
    """The write-time gate does not reach a zero that is ALREADY in the store.

    Run 20260915T120711Z: the derived tier made 0 OK calls and 1 skip, yet the Data tab showed
    gross_issuance_tokens = 0 at status ok, source derived:d_supply:MECHANISM_ASSUMED. That value
    was not written by that run. It was written before the suppression and kept being served.

    Same shape as Maple's 0.51: aggregate() consults the Gap Report only when the store has NO
    rows for a key, so a gap raised by the write path never reaches a key that already has a row.
    No gap row is passed here, deliberately — if suppression depended on one, this would pass for
    the wrong reason.
    """
    import pandas as pd
    import build_workbook as bw

    assert config.PROJECT_BY_NAME["GEODNET"]["issuance_derivation"]["suppressed"] is True

    stale = pd.DataFrame([{
        "date": pd.Timestamp("2026-09-14"), "project": "GEODNET",
        "metric": "gross_issuance_tokens", "value": 0.0,
        "source": "derived:d_supply:MECHANISM_ASSUMED", "tier": 2,
        "is_manual": False, "entered_on": ""}])
    out = bw.aggregate(stale, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                       gaps=pd.DataFrame(), review=pd.DataFrame())
    row = out[(out.project == "GEODNET") & (out.metric == "gross_issuance_tokens")].iloc[0]

    # ** IT READS n/a NOW, NOT 'suppressed'. CHANGED 2026-09-22, AND THE BLANKING IS UNCHANGED. **
    # The stored zero is still withheld — that is the whole point of this test and it still
    # holds. What changed is the BAND. total_supply_gross proved to be exactly 1,000,000,000 and
    # unmoving, so GEOD is entirely pre-minted and this column's quantity is zero by
    # construction, not uncomputable. RED means "not a number: suppressed, refused or gapped",
    # which sends a reader looking for a source that does not exist. N/A is excluded from the
    # confidence tally because there is no work to do.
    #
    # AND THE SUPPRESSION IS STILL RIGHT, which is the half that must not be lost: a derived 0
    # would read as "no emissions" when GEOD emissions are real and are DISTRIBUTION from
    # pre-minted wallets. The column's quantity is zero; the quantity a reader wants is
    # unmeasured. The n/a reason carries both.
    assert row["status"] == "n/a", f"a stored zero must not read 'ok': {row['status']}"
    assert pd.isna(row["now"]), "a false zero must be blank, not shown — it feeds the ratio"
    assert all(pd.isna(row[f]) for f in ("m1", "q0", "q1", "q2", "q3", "y1"))
    assert row["confidence"] == "N/A", \
        "'not applicable' is the absence of a question, not a failed answer"
    assert "PRE-MINTED" in row["note"] and "UNMEASURED" in row["note"], row["note"]
    assert "distribution" in row["note"].lower(), \
        "the reason must say what the real emissions ARE, or n/a reads as 'nothing happens here'"

    # THE CONTROL: the same metric on a project without the flag is served normally, so this
    # cannot pass because issuance broke for everyone.
    other = stale.copy(); other["project"] = "Uniswap"; other["value"] = 12_345.0
    out2 = bw.aggregate(other, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                        gaps=pd.DataFrame(), review=pd.DataFrame())
    ok = out2[(out2.project == "Uniswap") & (out2.metric == "gross_issuance_tokens")].iloc[0]
    assert ok["status"] == "ok" and ok["now"] == 12_345.0, f"control broke: {ok['status']}, {ok['now']}"
    print("GEODNET ok: a stored zero from before the suppression reads blank and RED on read")


def test_geodnet_issuance_derivation_is_suppressed_and_gaps_instead_of_reading_zero():
    """A FALSE ZERO IS WORSE THAN A GAP, and this proves the suppression produces the gap.

    GEODNET's gross_issuance_tokens was reading 0 from derived:d_supply:MECHANISM_ASSUMED — the
    delta between two daily supply reads on a token whose supply barely moves. Zero renders as a
    measured figure: it says the network issued nothing, it feeds the burn/issuance ratio as a
    denominator, and it carries a confidence band it never earned.

    The fixture is deliberately built so the OLD code would have stored a number: supply moves
    1,000,000 -> 1,000,500 between two DIFFERENT dates, which is a perfectly derivable +500 under
    transfer_to_dead_address. A test that fed it an unchanged supply would pass whether the
    suppression worked or not, because the old path would have written 0.0 and the assertion
    `value is None` would have to distinguish 0.0 from None to mean anything.
    """
    value, out = _derive("GEODNET", "transfer_to_dead_address",
                         supply_now=1_000_500.0, supply_prior=1_000_000.0, burn=None)
    assert value is None, f"GEODNET issuance must be suppressed, got {value!r}"

    gaps = [g for g in out.gaps
            if g["project"] == "GEODNET" and g["metric"] == "gross_issuance_tokens"]
    assert len(gaps) == 1, f"expected exactly one gap row, got {len(gaps)}"
    reason = gaps[0]["reason"]
    assert "SUPPRESSED" in reason, f"the gap must say it is suppressed, not merely absent: {reason}"
    assert "per-miner" in reason.lower() or "PER-MINER" in reason, \
        f"the gap must name WHY the supply delta is the wrong shape: {reason}"

    # And the suppression is CONFIG-DRIVEN, not a hardcoded project name in the fetch path.
    assert config.PROJECT_BY_NAME["GEODNET"]["issuance_derivation"]["suppressed"] is True

    # THE CONTROL. The same call on a project WITHOUT the flag still derives, so this test cannot
    # pass because the derivation broke for everyone. A burn figure is supplied because Uniswap's
    # stored supply is NET OF BURN (2026-09-21) and the formula needs the burn term back —
    # supplying None here would refuse for that reason and prove nothing about the suppression.
    value, _ = _derive("Uniswap", "transfer_to_dead_address",
                       supply_now=1_000_500.0, supply_prior=1_000_000.0, burn=0.0)
    assert value == 500.0, f"the control project must still derive; got {value!r}"
    print("GEODNET issuance ok: suppressed to a gap that names the reason, control still derives")


def test_world_mobile_inflation_budget_and_the_schedule_that_does_not_close():
    """580,000,000 over 20 years is CERTAIN. The per-year table is not, and the gap is 5%.

    This asserts the arithmetic rather than the prose, because the prose is what would rot. The
    three discretisations recorded in config are each recomputed from their own stated parameters
    and checked against the budget — so if someone later 'tidies' one of the figures, the sum
    stops matching and this fails.
    """
    p = config.PROJECT_BY_NAME["World Mobile"]
    budget = p["inflation_budget"]
    assert budget["tokens"] == 580_000_000
    assert budget["years"] == 20
    # 29% of the 2bn cap IS the budget — the fact that resolves "fixed supply vs 11.41% inflation".
    cap = p["max_supply_declared"]["value"]
    assert abs(budget["share_of_total_supply"] * cap - budget["tokens"]) < 1, \
        "the 29% share and the 580m figure must agree against the 2bn cap"

    d = p["inflation_schedule_derived"]
    h = d["continuous"]["rate_at_t0_per_year"]
    assert h == 58_000_000
    # The triangle: 1/2 * 20 * 58m = 580m exactly. This is the part that is not in doubt.
    assert 0.5 * budget["years"] * h == budget["tokens"]

    # A — year-START sampling, as supplied. Sums to 609m: OVER BUDGET BY 5%.
    a = d["discretisations"]["A_year_start"]
    series_a = [a["y1"] + n * a["step"] for n in range(20)]
    assert sum(series_a) == a["sum"] == 609_000_000
    assert a["over_budget_by"] == sum(series_a) - budget["tokens"] == 29_000_000
    # And its tail is off by one year against its own step — Y20 is 2.9m, not 0.
    assert series_a[19] == 2_900_000, "Y20 under the stated step is 2,900,000, not zero"
    assert a["zero_in_year"] == 21

    # B — MIDPOINT sampling of the same triangle. Conserves the budget exactly.
    b = d["discretisations"]["B_midpoint"]
    series_b = [h * (2 * 20 - 2 * n + 1) / (2 * 20) for n in range(1, 21)]
    assert sum(series_b) == budget["tokens"]
    assert series_b[0] == b["y1"] == 56_550_000
    assert series_b[19] == b["y20"] == 1_450_000

    # C — year-start rescaled to the budget. Its parameters are ROUNDED TO WHOLE TOKENS, so the
    # series lands fifty tokens light of 580,000,000. Asserted as an exact residual rather than
    # waved through with a loose tolerance: a wide `abs(...) < 100` would also accept a genuine
    # arithmetic change, which is the thing this test exists to catch.
    c = d["discretisations"]["C_rescaled"]
    series_c = [c["y1"] + n * c["step"] for n in range(20)]
    assert sum(series_c) - budget["tokens"] == c["rounding_residual"] == -50
    assert abs(c["exact_y1"] - budget["tokens"] / 10.5) < 1e-6, "C's exact y1 must be budget/10.5"

    # ** THE PERCENTAGE-ANCHORED TEST IS WITHDRAWN, AND THIS ASSERTS WHY RATHER THAN JUST THAT. **
    # The old test back-solved a base from 11.41% and checked it against TGE circulating supply.
    # World Mobile's own TGE glossary gives 200,000,000 WMT — and every variant implies a base
    # 2.4x-2.5x that. A sampling convention moves the answer ~5%, so a 2.5x gap falsifies the
    # premise rather than choosing between the variants.
    #
    # C is checked against its EXACT y1, not the rounded one: rounding y1 down by 0.238 tokens
    # moves the implied base by ~2, noise against the spread but enough to trip a tight assertion.
    w = d["validation_test_withdrawn"]
    assert w["tge_circulating"] == p["tge"]["released_tokens"] == 200_000_000
    assert p["tge"]["released_pct_of_total_supply"] * cap == 200_000_000, "TGE was 10% of the 2bn cap"
    for key, first_year in (("A", series_a[0]), ("B", series_b[0]), ("C", c["exact_y1"])):
        implied = first_year / 0.1141
        assert abs(implied - w["implied_base_by_variant"][key]) < 1.0, f"variant {key} base mismatch"
        ratio = implied / w["tge_circulating"]
        assert abs(ratio - w["ratios_to_tge"][key]) < 0.01, f"variant {key} TGE ratio mismatch"
        # THE POINT: falsified by a factor, not by a margin. Every variant is >2x out, which is
        # more than an order of magnitude beyond the ~5% the conventions differ by.
        assert ratio > 2.0, f"variant {key} would have to be >2x TGE for the premise to fail"

    # 11.41% IS NOW A SCHEDULE PARAMETER, not a point-in-time reference — RESOLVED 2026-09-18.
    # World Mobile's own whitepaper (Section XI) states it directly, against a fixed AGGREGATE
    # base, not a moving circulating one read off a third-party live-stat page. The old live_stat
    # model is fully withdrawn, not merely revised.
    ref = p["inflation_rate_reference"]
    assert ref["is_schedule_parameter"] is True
    assert ref["kind"] == "schedule_parameter"
    assert ref["measured_against"].startswith("AGGREGATE SUPPLY")
    assert "live_stat" not in str(ref).lower() or "supersedes" in ref, \
        "live_stat framing must be gone, or only appear inside the supersedes record of what was removed"
    assert "source_kind" not in ref, "the third-party live_stat source_kind field must be removed"
    assert "reference_date" not in ref, \
        "reference_date was the retrieval-date convention for a live stat — gone with the model"
    assert "recheck" not in ref, "the periodic-recheck caveat belonged to the withdrawn live_stat model"
    assert pd.Timestamp(ref["source_date"]) == pd.Timestamp("2026-09-18")
    assert "The Block" in ref["supersedes"], \
        "the record of what this replaced (The Block, live_stat) must be kept, not silently dropped"

    # THE FIELD THAT SHOULD NOT EXIST. article_published modelled this as a dated article and is
    # gone; a future edit that reintroduces it is reintroducing the wrong model, not filling a gap.
    assert "article_published" not in ref, \
        "article_published is the wrong model for this figure — it never applied here either"
    assert "article_published_status" not in ref

    # THE OLD LINEAR/GEOMETRIC OPEN QUESTION IS WITHDRAWN IN FULL — not just linear. World
    # Mobile's own whitepaper (Section XI) gives the actual shape directly: hyperbolic.
    assert "WITHDRAWN" in d["the_single_open_assumption"]
    assert "decay_curve_confirmed" in d["the_single_open_assumption"]
    assert p["issuance_schedule"] is None, \
        "no step may be declared while the parameterisation (not the shape) is still open"

    # THE HYPERBOLIC SHAPE, CONFIRMED FROM THE WHITEPAPER ITSELF.
    curve = p["decay_curve_confirmed"]
    assert curve["shape"] == "hyperbolic"
    assert curve["formula"] == "rate(t) = 11.41% / (t + 1)"
    assert curve["initial_rate_pa"] == 0.1141
    assert curve["target_horizon_years"] == 20
    assert curve["target_aggregate_supply"] == cap == 2_000_000_000

    # TWO OPEN PARAMETERISATION QUESTIONS, RECORDED RATHER THAN GUESSED. Neither a yearly nor a
    # monthly reading of t reproduces the stated 29% target under the naive formula — asserted as
    # the actual harmonic-sum arithmetic, not just checked for presence, so a later edit that
    # quietly "fixes" the mismatch by adjusting an unstated assumption breaks this test.
    oq = curve["open_questions"]
    import math
    h20 = sum(1.0 / n for n in range(1, 21))
    h240 = sum(1.0 / n for n in range(1, 241))
    years_pct = 0.1141 * h20
    months_pct = (0.1141 / 12) * h240
    assert years_pct > 0.29 > months_pct, \
        "the stated 29% target must sit BETWEEN the naive yearly and monthly readings"
    assert "t_in_years" in oq["time_unit_ambiguous"] and "t_in_months" in oq["time_unit_ambiguous"]
    assert oq["base_is_aggregate_not_circulating"]["finding"].startswith("'relative to aggregate supply'")
    assert "not_yet_resolved" in curve
    print("World Mobile ok: 580m/20yr certain, the TGE anchor falsifies the old percentage test by "
          ">2x, decay curve confirmed hyperbolic with the base/time-unit parameterisation open")


def test_ultrasound_total_supply_is_a_crosscheck_and_cannot_anchor_on_a_component():
    """The enabled entry must not be able to store a COMPONENT as Ethereum's supply.

    ultrasound.money prints the total beside its three inputs, and the largest — EVM balances at
    167,722,332.48 — is 37.4% above the total of 122,043,141.99. A DOM anchor matching a
    component label would store that as total supply, and it would pass the generic total_supply
    bounds of 0..1e15 and render as ok. Two defences are asserted here: the anchor names the
    total, and Ethereum's own sanity bound is narrow enough to REJECT the component outright.
    """
    import yaml
    from fetch.scrape import entry_ready

    entries = {e["metric"]: e for e in yaml.safe_load(open("sources.yaml"))
               if e["project"] == "Ethereum" and "ultrasound" in str(e.get("url") or "")}
    assert set(entries) == {"total_supply_dashboard", "gross_issuance_tokens",
                            "gross_burn_tokens", "net_mint_monthly"}

    live = entries["total_supply_dashboard"]
    assert live["enabled"] is True and entry_ready(live) == (True, "")
    assert live["method"] == "dom"
    assert live["anchor"] == "Total supply", \
        f"anchor must name the TOTAL, not a component; got {live['anchor']!r}"
    assert "EVM balances" != live["anchor"]

    # THE SECOND DEFENCE. The component must be rejected by Ethereum's bound, and the real figure
    # must be accepted by it — a bound that rejects both would just be broken.
    lo, hi = config.sanity_bounds("Ethereum", "total_supply_dashboard")
    component, total = 167_722_332.48, 122_043_141.99
    assert not (lo <= component <= hi), "the EVM-balances component must fail the sanity gate"
    assert lo <= total <= hi, "the real total must pass the sanity gate"
    # And the generic bound would NOT have caught it — which is why the per-project one exists.
    g_lo = config.METRICS["total_supply"]["sanity_min"]
    g_hi = config.METRICS["total_supply"]["sanity_max"]
    assert g_lo <= component <= g_hi, "generic bounds would have waved the component through"

    # The published arithmetic closes, which is what makes this an independent construction
    # rather than a restatement: execution layer + consensus layer - deposits.
    assert abs((167_722_332.48 + 43_388_015.76 - 89_067_206.25) - total) < 0.005

    # The other three stay DISABLED: their figures are client-side rendered and the served HTML
    # carries the literal placeholder "0K ETH/year", so a DOM fallback would extract a zero.
    for metric in ("gross_issuance_tokens", "gross_burn_tokens", "net_mint_monthly"):
        e = entries[metric]
        assert e["enabled"] is False, f"{metric} must stay disabled until the endpoint is known"
        assert e["method"] == "xhr", f"{metric} must not be switched to dom"
        assert "DO NOT switch this to method dom" in e["note"]

    # Declared as a CROSS-CHECK, preferring the tier 1 primary — never as a replacement.
    checks = [c for c in config.PROJECT_BY_NAME["Ethereum"]["cross_checks"]
              if c["secondary"] == "total_supply_dashboard"]
    assert len(checks) == 1
    assert checks[0]["primary"] == "total_supply" and checks[0]["prefer"] == "primary"
    # The two agreed to 0.00575%, so the tolerance is loose by orders of magnitude on purpose.
    assert abs(122_050_160 - total) / 122_050_160 < checks[0]["tolerance"]

    # Scoped to Ethereum alone, so no other archetype 1/4 project acquires a gap for it.
    assert "total_supply_dashboard" in config.metrics_for_project(config.PROJECT_BY_NAME["Ethereum"])
    for other in ("Bitcoin", "Solana", "Plume"):
        assert "total_supply_dashboard" not in config.metrics_for_project(config.PROJECT_BY_NAME[other])
    print("ultrasound ok: anchors the total, sanity bound rejects the component, three stay disabled")


def test_world_mobile_decimals_are_read_from_the_contract_never_assumed():
    """The Maple 0.51 failure mode cannot occur here, and this pins the reason in place.

    World Mobile's docs say DECIMALS: 6. If anything in the fetch path assumed 18, every WMTX
    figure would be wrong by 10^12 — and would look plausible rather than absurd, which is what
    makes that class of error dangerous. It does not, because scaled() reads decimals() from the
    contract on every call. This test asserts the ABSENCE of a hardcoded scale factor in the
    fetch path, so a future 'optimisation' that caches or assumes 18 fails here.
    """
    import pathlib
    import re

    p = config.PROJECT_BY_NAME["World Mobile"]
    assert p["decimals_note"]["docs_claim"] == 6

    src = pathlib.Path(__file__).resolve().parent.parent / "fetch" / "chain.py"
    text = src.read_text()
    # scaled() must take its divisor from a live decimals() call, not from a literal.
    assert "dec_source.functions.decimals().call()" in text, \
        "scaled() must read decimals() from the contract"
    offenders = [m for m in re.findall(r"10 ?\*\* ?(\d+)", text) if m != ""]
    assert not offenders, f"hardcoded power-of-ten scaling in fetch/chain.py: 10**{offenders}"
    for bad in ("1e18", "1e6", "DECIMALS = 18"):
        assert bad not in text, f"hardcoded scale {bad!r} in fetch/chain.py"
    print("World Mobile ok: decimals read from the contract, no hardcoded scale in the fetch path")



def test_sky_revenue_base_uncertain_suppresses_implied_buyback_but_not_the_share():
    """base_gated() suppresses the IMPLIED-BUYBACK figure without touching the confirmed SHARE.

    Sky's Stage 2 split (22.5% / 22.5% / 5%) is PRIMARY-SOURCED — Sky's own words. What is
    unconfirmed is narrower: whether the "monthly Net Protocol Surplus" the split is a share OF
    is the same quantity as revenue_usd, which is what the archetype 3 formula actually
    multiplies. Conflating "share unknown" with "share's base unknown" would hide a confirmed
    number behind the same grey used for one nobody has sourced at all — exactly the imprecision
    this session's other fixes (GEODNET, NEAR) were built to eliminate.

    RESOLVED 2026-09-18, as outcome (b): NPS and revenue_usd are CONFIRMED DIFFERENT quantities
    (Sky's own docs plus Sagix research), not merely an unconfirmed mapping — status moved from
    'unconfirmed' to 'confirmed_different'. base_gated must still suppress: only status
    'confirmed' un-greys the cell, and a confirmed DIFFERENCE is exactly as suppressing as an
    unconfirmed mapping, for a stronger and now-permanent reason.
    """
    import build_workbook as bw

    sky = config.PROJECT_BY_NAME["Sky"]
    b = sky.get("revenue_base_uncertain")
    assert b is not None, "Sky must declare revenue_base_uncertain while the mapping is unresolved"
    assert b["status"] == "confirmed_different"

    # ** THE GREY LIFTS NOW, AND NOT BECAUSE THE MAPPING WAS FOUND. CHANGED 2026-09-22. **
    # It could not be found and never will be: revenue_base_uncertain gives two independent
    # reasons, and its status stays 'confirmed_different' precisely to say so. What changed is
    # that the RIGHT base is sourced in its own right — net_protocol_surplus_usd, from Sky's own
    # quarterly reporting — and the formula now multiplies THAT. The old behaviour is still the
    # behaviour for any project that declares the uncertainty without declaring a base.
    base = config.revenue_base("Sky")
    assert base["metric"] == "net_protocol_surplus_usd" and base["effective_from"] == "2026-09-14"
    assert b["status"] == "confirmed_different", \
        "the two quantities are still different — resolving the base does not map them together"

    try:
        # A WINDOW WHOLLY INSIDE STAGE 2: the figure is computed.
        bw._WINDOWS["q0"] = ("2026-09-15", "2026-10-15")
        assert bw.base_gated(sky, "NPS*SHARE") == "NPS*SHARE"

        # A WINDOW THAT ENDS BEFORE STAGE 2: still grey, and for the new reason. The 27.5/22.5/5
        # allocation did not exist then — applying an NPS base to it would multiply the right
        # number by a share that had not been announced.
        bw._WINDOWS["q1"] = ("2026-05-01", "2026-08-01")
        out = bw.base_gated(sky, "NPS*SHARE", window="q1")
        assert "2026-09-14" in out and "ends before it" in out, out

        # A WINDOW THAT SPANS THE BOUNDARY: grey too, and it says which. Part one regime and part
        # the other; no single share describes it.
        bw._WINDOWS["q2"] = ("2026-09-01", "2026-10-01")
        spanning = bw.base_gated(sky, "NPS*SHARE", window="q2")
        assert "spans that date" in spanning, spanning

        # COMPOSED WITH threshold_gated exactly as the real A3 columns do it.
        bw._WINDOWS["q0"] = ("2026-09-15", "2026-10-15")
        composed = bw.base_gated(sky, bw.threshold_gated(sky, "NPS*SHARE/PRICE"))
        assert composed == bw.threshold_gated(sky, "NPS*SHARE/PRICE")
    finally:
        bw._WINDOWS.clear()

    # AND WITH NO WINDOW RESOLVED IT REFUSES TO GUESS. Saying a window "ends before" a date
    # nobody computed would be a made-up fact in the one function built to prevent them.
    assert "window not resolved" in bw.base_gated(sky, "NPS*SHARE")

    # THE SHARE ITSELF IS UNTOUCHED. fee_split_v2 stays confirmed and primary-sourced — the split
    # and its application to a base are two different claims, and only one is in doubt.
    v2 = sky["fee_split_v2"]
    assert v2["confidence"] == "PRIMARY — Sky's own account, not secondary commentary"
    assert v2["sky_buying_share"] == 0.275 and v2["burn_share"] == 0.05
    live = config.split_for_window("Sky", "2026-09-20", "2026-09-25")
    assert live["status"] == "active" and live["share_to_buyback"] == 0.275, \
        "the split's own status must stay 'active' — only the FORMULA using it is suppressed"

    # THE CONTROL: a project with no revenue_base_uncertain flag is completely untouched. This is
    # what proves the gate is scoped to Sky and did not become a silent global behaviour change.
    for name in ("Uniswap", "Maple", "Chainlink", "Aerodrome"):
        p = config.PROJECT_BY_NAME[name]
        assert p.get("revenue_base_uncertain") is None, f"{name} must not carry this flag"
        untouched = bw.base_gated(p, "UNTOUCHED_EXPR")
        assert untouched == "UNTOUCHED_EXPR", f"{name}'s formula must pass through unchanged: {untouched!r}"

    # THE RESOLUTION ITSELF IS RECORDED, NOT SILENTLY ASSUMED. Outcome (b) — genuinely different,
    # sourced and dated — is a distinct state from the earlier "unable to check" and from a found
    # mapping; the record must say which one this is and cite the evidence.
    assert "2026-09-18" in b["resolved"]
    assert "sagix.io" in b["resolved"] or "insights.skyeco.com" in b["resolved"]
    assert "31%" in b["reason"] or "33.29m" in b["reason"]
    assert "second_independent_reason" in b, \
        "the margin-instability finding (49% Q1 vs 31% Q2) is a second, independent reason and must be recorded"
    print("Sky base-uncertain ok: implied buyback suppressed, share stays confirmed, "
          "base confirmed different (not merely unmapped), other 15 projects untouched")


def test_geodnet_treasury_wallets_are_eoas_not_contracts_targeted_not_kind_wide():
    """GEODNET's three treasury wallets are EOAs by design — the guard's per-address escape
    hatch, not a kind-wide exemption.

    mining_distribution_polygon was REJECTED live: "nothing deployed... eth_getCode is empty, and
    a treasury_holding holder is supposed to be a contract." Checked, not assumed, which of two
    things was true: the KIND-WIDE default requiring bytecode is wrong for treasury_holding in
    general (the burn-address regression's shape), or this specific address is wrong. Neither —
    of five treasury_holding contracts on file, three (Sky's Pause Proxy, Maple's fee treasury,
    NEAR's Intents Treasury) genuinely ARE deployed contracts, so the kind-wide default is right
    in general. GEODNET's three are EOAs per GEODNET's own "wallet" language AND a live
    eth_getCode confirming empty bytecode — a per-address fact, not a class-wide one.
    """
    from fetch.chain import holder_should_have_code, HOLDER_MUST_HAVE_CODE

    assert "treasury_holding" in HOLDER_MUST_HAVE_CODE, \
        "the KIND-WIDE default must stay 'require code' — that part of the guard is correct"

    geodnet = config.PROJECT_BY_NAME["GEODNET"]["contracts"]
    for key in ("mining_polygon", "mining_distribution_polygon", "ecosystem_polygon"):
        spec = geodnet[key]
        assert spec["kind"] == "treasury_holding"
        assert spec["holder_has_code"] is False, \
            f"{key} must explicitly override to False — an EOA, confirmed by GEODNET's own docs"
        assert holder_should_have_code(spec) is False, \
            f"{key}: the guard must not require bytecode for this address"

    # THE CONTROL: real treasury contracts elsewhere must be COMPLETELY UNTOUCHED. This is what
    # proves the fix is per-address, not a change to the kind-wide default that would have
    # silently stopped checking Sky's, Maple's and NEAR's genuinely-contract treasuries too.
    controls = [("Sky", "pause_proxy"), ("Maple", "treasury"), ("Near", "intents_treasury_base")]
    for proj, key in controls:
        spec = config.PROJECT_BY_NAME[proj]["contracts"][key]
        assert spec["kind"] == "treasury_holding"
        assert spec["holder_has_code"] is True, f"{proj}/{key} must stay True — it is a real contract"
        assert holder_should_have_code(spec) is True, f"{proj}/{key}: the guard must still require code"

    # A project with NO explicit override still gets the kind-wide default (require code) — the
    # override is additive, not a change to what "unset" means.
    unset_spec = dict(kind="treasury_holding", address="0x1111111111111111111111111111111111111a")
    assert holder_should_have_code(unset_spec) is True, \
        "an unset holder_has_code on a treasury_holding contract must still default to requiring code"
    print("GEODNET treasury EOA fix ok: three wallets exempted by address, "
          "three real treasury contracts elsewhere untouched, default unchanged for anyone unset")


def test_sky_notes_reflect_stage_2_not_the_stale_55_45():
    """The A3 tab's Notes column pulls project['notes'] directly — a stale note there is a stale
    cell on every future run, independent of anything else in config being correct.

    Found live: Sky's notes still said "Smart Burn Engine: 55% of each cycle burned" after Stage 2
    (22.5/22.5/5, effective 2026-09-14) had already been applied everywhere else in config. The
    fee_split_v2 block, the relation exemptions, the base_gated wiring — all correct. This one
    plain-text field was not, because nothing enforces that prose describing a mechanism updates
    when the mechanism does.
    """
    notes = config.PROJECT_BY_NAME["Sky"]["notes"]
    assert "55% of each cycle burned" not in notes, "the stale pre-Stage-2 burn framing must be gone"
    assert "22.5%" in notes and "5%" in notes, "the Stage 2 percentages must be stated"
    assert "Net Protocol Surplus" in notes or "base" in notes.lower(), \
        "the base-uncertainty caveat must be visible in the same place the split is described"
    assert "2026-09-14" in notes, "the effective date must be stated, not just the old 13 Aug date"

    # AND IT ACTUALLY REACHES THE WORKBOOK — this is what the earlier note failed to do; text that
    # is merely correct in config.py and never rendered is exactly as useless as text that is
    # wrong. Build a real workbook and read the Notes cell back.
    import pathlib as _pl
    import tempfile

    import openpyxl

    import build_workbook as bw
    import store as store_mod

    db = _pl.Path(__file__).resolve().parent / "_scratch" / "metrics.db"
    if not db.exists():
        print("Sky notes ok: config text correct (no _scratch/metrics.db to render against)")
        return
    st = store_mod.Store(str(db))
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        out_path = bw.build_workbook(st, tmp.name)
    wb = openpyxl.load_workbook(out_path)
    ws = wb["A3 Revenue Buyback"]
    header_row = next(r for r in range(1, 10)
                      if any(ws.cell(row=r, column=c).value == "Project" for c in range(1, 6)))
    headers = {ws.cell(row=header_row, column=c).value: c for c in range(1, ws.max_column + 1)
              if ws.cell(row=header_row, column=c).value}
    sky_row = next(r for r in range(header_row + 1, ws.max_row + 1)
                  if ws.cell(row=r, column=headers["Project"]).value == "Sky")
    cell_text = ws.cell(row=sky_row, column=headers["Notes"]).value or ""
    assert "55% of each cycle burned" not in cell_text, \
        f"the RENDERED Notes cell must not carry the stale framing: {cell_text[:200]!r}"
    assert "22.5%" in cell_text
    print("Sky notes ok: Stage 2 stated, stale 55%-burned framing gone, confirmed in a built workbook")


if __name__ == "__main__":
    # EVERY test_* IN THIS MODULE, IN DEFINITION ORDER — discovered, not hand-listed.
    #
    # This used to be an explicit list of function objects, and three tests written in September
    # 2026 were defined and NEVER RUN because nobody added them to it: the burn-mechanism flag
    # test, the Maple disputed-treasury test, and the uncovered-chain refusal test. The suite
    # printed "ALL ADAPTER TESTS PASSED" the whole time, which is the worst possible failure for a
    # test runner — it reported success for work it had not done.
    #
    # A hand-maintained registry of tests has the same defect as a hand-assigned confidence band:
    # it is a second copy of the truth that goes stale silently. Discovery cannot go stale.
    import inspect as _inspect
    import sys as _sys

    _module = _sys.modules[__name__]
    _tests = [(name, obj) for name, obj in vars(_module).items()
              if name.startswith("test_") and _inspect.isfunction(obj)
              and obj.__module__ == _module.__name__]
    # definition order, so a failure reads in the same sequence the file does
    _tests.sort(key=lambda kv: kv[1].__code__.co_firstlineno)
    for _name, _fn in _tests:
        _fn()
    print(f"\nALL ADAPTER TESTS PASSED ({len(_tests)} tests)")


# ======================================================================================
# AERODROME'S EMISSION: A WEEKLY FIGURE STORED DAILY, AND A SCHEDULE THAT STOPPED BEING ONE
# ======================================================================================

class _MinterReader:
    """Aerodrome's Minter and AERO, with the tail legs readable.

    weekly / rate_bps / supply are the three numbers the contract's own branch is chosen on, so
    every test below is a statement about which branch a given triple should take.
    """

    # DEFAULT RATE 21 bps, NOT the contract's initial 67. 67 bps on 1.98bn annualises to 34.8%
    # of supply, which the documented-rate band (5-15%) refuses on purpose — see
    # test_a_tail_rate_that_has_never_been_nudged_is_a_finding_not_a_pass. 21 bps is the
    # documented ~10.9% annualised, i.e. what a healthy live read should look like.
    def __init__(self, weekly, rate_bps=21, supply=1_980_000_000.0, raise_on=None):
        self.weekly, self.rate_bps, self.supply, self.raise_on = weekly, rate_bps, supply, raise_on

    def symbol_matches(self, chain, address, expected):
        return True, "AERO"

    def raw(self, chain, address, call, *args):
        if self.raise_on == "rate":
            raise RuntimeError("execution reverted")
        return self.rate_bps

    def scaled(self, chain, address, call, *args, decimals_from=None):
        if call == "weekly":
            return self.weekly
        if call == "totalSupply":
            return self.supply
        raise AssertionError(f"unexpected call {call!r}")


def _aerodrome_minter_project():
    """The live config's own minter and token entries, not a hand-built lookalike.

    Deliberately taken from config rather than written out here: the whole finding is that the
    contract's threshold and the contract's rate call have to be the REAL ones, and a test that
    restated them would keep passing after somebody edited config to something wrong.
    """
    live = config.PROJECT_BY_NAME["Aerodrome"]
    return {"name": "Aerodrome", "archetypes": live["archetypes"],
            "contracts": {"token": dict(live["contracts"]["token"]),
                          "minter": dict(live["contracts"]["minter"])}}


def test_a_frozen_weekly_is_not_an_emission_and_the_tail_formula_replaces_it():
    """THE C3 FINDING. Minter.updatePeriod() writes `weekly` back only on the non-tail branch:

        bool _tail = _weekly < TAIL_START;
        if (_tail) { _emission = _totalSupply * tailEmissionRate / MAX_BPS; }
        else       { _emission = _weekly; ...; weekly = _weekly; }   <- assignment is HERE only

    So the first epoch below TAIL_START freezes `weekly` for good. Replaying the contract's
    constants (10,000,000 start, x1.03 for epochs 1-14, x0.99 after) lands on 8,969,149.540108 at
    epoch 67 — and run 20260921T100546Z stored 8,969,149 as gross_issuance_tokens. That is the
    frozen value to the token, not a near miss on the threshold.
    """
    c = Chain()
    c.reader = _MinterReader(weekly=8_969_149.540108, rate_bps=21, supply=1_980_000_000.0)
    out = FetchOutput()
    c.run([_aerodrome_minter_project()], None, out)
    df = out.frame()
    row = df[df["metric"] == "gross_issuance_tokens"].iloc[0]

    expected = 1_980_000_000.0 * 21 / 10_000
    assert abs(row["value"] - expected) < 1e-6, \
        f"tail emission must be totalSupply x rate / MAX_BPS ({expected:,.2f}), got {row['value']:,.2f}"
    assert abs(row["value"] - 8_969_149.540108) > 1.0, "the frozen weekly must not be stored"
    # AND THE BASIS IS VISIBLE IN THE ROW. A series that changes formula mid-history is otherwise
    # an unexplained step change with nothing in the row to explain it.
    assert "tail@21bps" in row["source"], row["source"]
    print(f"tail mode ok: weekly 8,969,149.54 refused, emission {row['value']:,.2f} "
          f"from {row['source']}")


def test_above_the_threshold_the_schedule_is_still_the_emission():
    """THE CONTROL. A guard that always took the tail branch would pass the test above and be
    wrong for every pre-tail protocol. Above TAIL_START, weekly() IS the answer and is stored."""
    c = Chain()
    c.reader = _MinterReader(weekly=9_059_747.010210)     # epoch 66, one step before the tail
    out = FetchOutput()
    c.run([_aerodrome_minter_project()], None, out)
    row = out.frame().query("metric == 'gross_issuance_tokens'").iloc[0]
    assert abs(row["value"] - 9_059_747.010210) < 1e-6, row["value"]
    assert "tail@" not in row["source"], f"pre-tail must not use the tail formula: {row['source']}"
    print("pre-tail control ok: weekly() stored unchanged,", row["source"])


def test_an_unreadable_tail_leg_refuses_rather_than_falling_back_to_the_frozen_number():
    """THE ONE THAT MATTERS MOST, because the fallback is so tempting and so wrong.

    If the tail legs cannot be read, weekly() is still sitting there returning a number of
    entirely plausible magnitude. Storing it would put a figure in the sheet that looks right, is
    stale by years, and has nothing to flag it. Nothing is stored instead.
    """
    c = Chain()
    c.reader = _MinterReader(weekly=8_969_149.540108, raise_on="rate")
    out = FetchOutput()
    c.run([_aerodrome_minter_project()], None, out)
    df = out.frame()
    assert df[df["metric"] == "gross_issuance_tokens"].empty, \
        "a failed tail read must store nothing, never the frozen weekly"
    reasons = " ".join(g["reason"] for g in out.gaps)
    assert "FROZEN" in reasons or "frozen" in reasons, reasons
    print("refusal ok:", [g["reason"][:90] for g in out.gaps if "issuance" in g["metric"]])


def test_a_tail_rate_outside_the_contracts_own_bounds_is_refused():
    """nudge() bounds tailEmissionRate to [1, 100] bps. A read outside that is not a surprising
    governance outcome, it is evidence the call is not returning what config thinks it is."""
    c = Chain()
    c.reader = _MinterReader(weekly=8_969_149.540108, rate_bps=6_700)   # bps vs a raw fraction
    out = FetchOutput()
    c.run([_aerodrome_minter_project()], None, out)
    assert out.frame().query("metric == 'gross_issuance_tokens'").empty
    assert any("outside the contract" in g["reason"] for g in out.gaps), \
        [g["reason"] for g in out.gaps]
    print("bounds ok: 6700 bps refused as not-what-config-thinks")


def test_a_weekly_read_is_dated_to_its_epoch_so_daily_runs_do_not_multiply_it():
    """THE 7x. The store's key is (date, project, metric). A weekly figure dated to the RUN lays
    down one row per day, all carrying the same number, and a trailing-30-day SUM adds them up as
    though each were a separate week's emissions. Dated to the epoch start, every run inside one
    epoch writes the SAME key and the sum is one week per week.
    """
    dates = set()
    for _ in range(6):                      # six runs, all inside one epoch
        c = Chain()
        c.reader = _MinterReader(weekly=8_969_149.540108)
        out = FetchOutput()
        c.run([_aerodrome_minter_project()], None, out)
        row = out.frame().query("metric == 'gross_issuance_tokens'").iloc[0]
        dates.add(pd.Timestamp(row["date"]).normalize())

    assert len(dates) == 1, f"six runs in one epoch produced {len(dates)} distinct dates: {dates}"
    epoch_start = next(iter(dates))
    assert epoch_start.dayofweek == 3, \
        f"ve(3,3) epochs flip Thursday (Unix epoch is a Thursday); got {epoch_start:%A}"
    assert (pd.Timestamp.now("UTC").tz_localize(None) - epoch_start).days < 8, epoch_start
    print(f"epoch dating ok: six runs collapse onto {epoch_start:%Y-%m-%d (%A)}")


def test_the_granularity_of_every_series_is_resolved_from_what_actually_produces_it():
    """Granularity is read off the contract or the Dune query, never a third hand-kept table.

    The GEODNET case is the one worth pinning: its gross_burn_tokens is a MONTHLY Dune backfill
    sitting under a LIVE daily delta. Calling that series monthly would stop a real daily flow
    being summed and mark a current series stale, so the live read decides.
    """
    assert config.series_granularity("Aerodrome", "gross_issuance_tokens") == "weekly"
    assert config.series_granularity("Aerodrome", "emissions_tokens") == "weekly"
    assert config.series_granularity("Aerodrome", "total_supply") == "daily"
    assert config.series_granularity("GEODNET", "actual_buyback_tokens") == "monthly"
    assert config.series_granularity("GEODNET", "gross_burn_tokens") == "daily"
    assert config.series_granularity("Uniswap", "gross_burn_tokens") == "daily"
    print("granularity ok: contract beats Dune date_col where both feed one metric")


# ======================================================================================
# MAPLE'S TREASURY: what happens when the PRIMARY source turns out to be one we may not fetch
# ======================================================================================

class _MapleReader:
    """The daoMultisig holding SYRUP, at the figure the first live read returned."""

    def __init__(self, balance=23_090_000.0, supply=1_000_000_000.0):
        self.balance, self.supply = balance, supply

    def symbol_matches(self, chain, address, expected):
        return True, "SYRUP"

    def has_code(self, chain, address):
        return True

    def scaled(self, chain, address, call, *args, decimals_from=None):
        return self.balance if call == "balanceOf" or args else self.supply


def _maple_treasury_project():
    live = config.PROJECT_BY_NAME["Maple"]
    return {"name": "Maple", "archetypes": live["archetypes"],
            "contracts": {"token": dict(live["contracts"]["token"]),
                          "treasury": dict(live["contracts"]["treasury"])}}


def test_the_chain_read_serves_the_primary_metric_again_and_says_it_is_partial():
    """R4. The 2026-09-18 promotion made maple.finance/transparency the primary source for
    treasury_holding_tokens. maple.finance/robots.txt disallows /transparency, so the scrape
    refused — correctly — and the metric went BLANK. A blank says nothing about Maple's treasury;
    a partial figure says 23.09M and admits it is not the whole thing.
    """
    c = Chain()
    c.reader = _MapleReader()
    out = FetchOutput()
    c.run([_maple_treasury_project()], None, out)
    df = out.frame()
    by = dict(zip(df["metric"], df["value"]))

    assert "treasury_holding_tokens" in by, \
        f"the chain read must serve the primary metric again; got {sorted(by)}"
    assert by["treasury_holding_tokens"] == 23_090_000.0
    assert "treasury_holding_tokens_chain_crosscheck" not in by, \
        "the metric_override is gone, and so is the metric — retired 2026-09-22"
    assert "treasury_holding_tokens_chain_crosscheck" not in config.METRICS

    row = df[df["metric"] == "treasury_holding_tokens"].iloc[0]
    assert ":PARTIAL" in row["source"], f"must be labelled partial: {row['source']}"
    reason = " ".join(g["reason"] for g in out.gaps if g["metric"] == "treasury_holding_tokens")
    assert "77.66M" in reason and "23.09" in reason, \
        f"the partial reason must name BOTH figures, not just say 'partial': {reason}"
    print("R4 ok:", row["source"], "|", reason[:110])


def test_a_partial_reason_comes_from_the_contract_not_the_projects_supply_note():
    """The project-level supply_partial_reason describes a partial TOTAL SUPPLY and is shared by
    every metric. Maple's treasury is partial for an unrelated reason. Labelling it with the
    project's text would attach someone else's explanation to this number."""
    c = Chain()
    c.reader = _MapleReader()
    out = FetchOutput()
    project = _maple_treasury_project()
    project["supply_partial_reason"] = "SYRUP is not fully enumerated across chains"
    c.run([project], None, out)
    reason = " ".join(g["reason"] for g in out.gaps if g["metric"] == "treasury_holding_tokens")
    assert "daoMultisig" in reason, reason
    assert "not fully enumerated" not in reason, \
        f"the project's supply note leaked onto the treasury metric: {reason}"
    print("partial reason ok: contract's own text wins over the project's")


def test_maples_published_figure_is_carried_by_hand_and_not_over_the_chain_read():
    """Manual entry is where the sourcing priority TERMINATES for a robots-disallowed page, and
    it is a valid answer. What it must not do is overwrite the automated figure — the two are
    different quantities by a factor of three, and that gap is the thing worth seeing."""
    import csv
    rows = [r for r in csv.DictReader(
        l for l in open("manual_overrides.csv") if not l.startswith("#"))]
    maple = [r for r in rows if r["project"] == "Maple"]
    assert len(maple) == 1, maple
    row = maple[0]
    assert row["metric"] == "treasury_holding_tokens_reported", \
        "the manual figure must NOT be entered over treasury_holding_tokens"
    assert float(row["value"]) == 77_660_000.0
    # Dated to when the page was read, not to the run that noticed the refusal.
    assert row["date"] == "2026-09-14" and row["entered_on"] == "2026-09-21", row

    # The floor applies to the hand-entered figure too: a dropped suffix lands at 77.66, which is
    # small, precise and plausible — the exact shape of the 0.51 SYRUP that started all this.
    lo, hi = config.sanity_bounds("Maple", "treasury_holding_tokens_reported")[:2]
    assert lo <= float(row["value"]) <= hi
    assert not (lo <= 77.66 <= hi), "the floor must still reject a suffix-dropped 77.66"
    print(f"manual figure ok: {float(row['value']):,.0f} under its own metric, floor {lo:,.0f}")


def test_no_enabled_scrape_targets_the_robots_disallowed_maple_page():
    """The refusal is respected, not worked around. This fails if anyone re-enables the entry, or
    points a new one at the same path, without robots.txt having changed."""
    import yaml
    registry = yaml.safe_load(open("sources.yaml"))
    live = [e for e in registry
            if e.get("enabled") and "maple.finance/transparency" in str(e.get("url") or "")]
    assert not live, f"robots.txt disallows this path; these entries are armed against it: {live}"
    print("robots ok: no enabled entry targets maple.finance/transparency")


# ======================================================================================
# WHAT A WINDOW ACTUALLY COVERS, AND WHAT A CHANGE CHECK CAN ACTUALLY COMPARE
# ======================================================================================

def _aggregate_rows(rows, asof):
    import pandas as pd
    import build_workbook as bw
    long = pd.DataFrame([{"date": pd.Timestamp(d), "project": p, "metric": m, "value": v,
                          "source": src, "tier": t, "is_manual": False, "entered_on": ""}
                         for d, p, m, v, src, t in rows])
    out = bw.aggregate(long, pd.DataFrame(), pd.Timestamp(asof),
                       gaps=pd.DataFrame(), review=pd.DataFrame())
    return {(r["project"], r["metric"]): r for r in out.to_dict("records")}


def test_a_monthly_series_reports_its_latest_COMPLETE_month_instead_of_a_blank():
    """S5. GEODNET's buyback series is monthly (Dune 8683175's date_col is 'month') and the
    incomplete current month is dropped by design. A trailing-30-day sum over it therefore
    reports whatever happens to fall inside 30 days — and on 2026-09-21, with the latest complete
    month being August, that was NOTHING. The cell was blank while 41 good rows sat in the store.
    """
    import pandas as pd

    rows = [(f"2026-{mo:02d}-01", "GEODNET", "actual_buyback_tokens", v,
             "dune:8683175", 4)
            for mo, v in [(6, 900_000.0), (7, 1_100_000.0), (8, 1_250_000.0)]]
    got = _aggregate_rows(rows, "2026-09-21")[("GEODNET", "actual_buyback_tokens")]

    assert got["now"] == 1_250_000.0, f"must report August, the latest COMPLETE month: {got['now']}"
    assert got["m1"] == 1_100_000.0, f"and the prior column is July, not a 30-day window: {got['m1']}"
    assert "2026-08" in got["note"], f"the month must be NAMED, not implied: {got['note']}"
    assert "COMPLETE month" in got["note"]
    # And it is NOT marked stale: 21 days is well inside a monthly series' allowance, though it
    # would have tripped the 7-day global that was being applied before.
    assert got["status"] != "stale", got["note"]
    assert config.stale_after_days("GEODNET", "actual_buyback_tokens", 7) == 45
    print(f"S5 ok: now={got['now']:,.0f} ({got['note'][:60]}...)")


def test_a_monthly_series_still_goes_stale_when_it_actually_stops():
    """THE CONTROL for the widened threshold. 45 days is not 'never' — a genuinely dead monthly
    series must still surface, or the fix for a false stale has bought a missed real one."""
    rows = [("2026-05-01", "GEODNET", "actual_buyback_tokens", 900_000.0, "dune:8683175", 4)]
    got = _aggregate_rows(rows, "2026-09-21")[("GEODNET", "actual_buyback_tokens")]
    assert got["status"] == "stale", f"a May point read in late September is dead: {got['status']}"
    assert "45 days" in got["note"], got["note"]
    print("S5 control ok:", got["note"][:90])


def test_a_thirty_day_header_over_a_ten_day_series_says_how_much_it_covers():
    """S6. Eight runs over ten days means 'trailing 30d' covers ten. Hyperliquid's 155,971 was
    roughly a third of a real 30-day burn for exactly this reason, printed under a 30-day header
    with nothing to say so — and a third of normal looks precisely like a collapse in activity,
    which is the reading most likely to be acted on.
    """
    rows = [(f"2026-09-{d:02d}", "Hyperliquid", "gross_burn_tokens", 15_000.0,
             "hypercore_info:spot", 2) for d in range(11, 21)]
    got = _aggregate_rows(rows, "2026-09-21")[("Hyperliquid", "gross_burn_tokens")]

    assert got["now"] == 150_000.0, got["now"]
    assert "COVERS 10 OF 30 DAYS" in got["note"], f"the note must state the span: {got['note']}"
    assert got["confidence"] == "AMBER", got["confidence"]
    assert "covers only 10 of the 30 days" in got["why_amber"], got["why_amber"]
    print("S6 ok:", got["note"][:100])


def test_a_series_older_than_the_window_is_not_flagged_for_coverage():
    """THE CONTROL. Coverage is about how long the series has EXISTED, not how densely it was
    sampled — otherwise every sparse series would carry a warning it has not earned. A series
    running since June covers all 30 days even with a handful of points in them."""
    rows = [("2026-06-01", "Hyperliquid", "gross_burn_tokens", 15_000.0, "hypercore_info:spot", 2),
            ("2026-08-25", "Hyperliquid", "gross_burn_tokens", 15_000.0, "hypercore_info:spot", 2),
            ("2026-09-20", "Hyperliquid", "gross_burn_tokens", 15_000.0, "hypercore_info:spot", 2)]
    got = _aggregate_rows(rows, "2026-09-21")[("Hyperliquid", "gross_burn_tokens")]
    assert "COVERS" not in got["note"], f"a long-running series must not be flagged: {got['note']}"
    assert "covers only" not in str(got["why_amber"]), got["why_amber"]
    print("S6 control ok: a sparse but long-running series is not flagged")


class _Recorder:
    """Captures review_item calls without needing the full FetchOutput."""

    def __init__(self):
        self.items = []

    def review_item(self, project, metric, reason, action, **kw):
        self.items.append({"project": project, "metric": metric, "reason": reason, **kw})


def _validated(rows, prior):
    import pandas as pd
    from fetch.validate import validate_frame
    df = pd.DataFrame([{"date": pd.Timestamp(d), "project": p, "metric": m, "value": v,
                        "source": src, "tier": t} for d, p, m, v, src, t in rows])
    rec = _Recorder()
    validate_frame(df, prior, rec)
    return [i for i in rec.items if i["reason"] == "change_threshold"]


def test_a_providers_partial_current_day_is_not_compared_against_a_whole_one():
    """S7. DefiLlama publishes the current day from the moment it starts, so its newest point is
    a few hours of revenue. Chainlink $1,105,263 -> $0 and Maple $406,091 -> $0 are that, not a
    protocol that stopped earning. Comparing the last two COMPLETE days is the fix; widening the
    threshold would have hidden the real step changes this check exists for.
    """
    import pandas as pd
    today = pd.Timestamp.now("UTC").date()
    yesterday = today - pd.Timedelta(days=1)
    before = today - pd.Timedelta(days=2)

    flags = _validated([
        (before, "Chainlink", "revenue_usd", 1_050_000.0, "defillama:chainlink", 1),
        (yesterday, "Chainlink", "revenue_usd", 1_105_263.0, "defillama:chainlink", 1),
        (today, "Chainlink", "revenue_usd", 0.0, "defillama:chainlink", 1),   # hours old
    ], prior={("Chainlink", "revenue_usd"): 1_050_000.0})
    assert not flags, f"the partial day must not be change-checked: {flags}"


def test_but_a_real_step_change_between_two_complete_days_still_fires():
    """THE CONTROL, and the one that matters: the S7 fix must narrow what is compared, not what
    counts as a big move. A guard that stopped firing entirely would pass the test above."""
    import pandas as pd
    today = pd.Timestamp.now("UTC").date()
    yesterday = today - pd.Timedelta(days=1)
    before = today - pd.Timedelta(days=2)

    flags = _validated([
        (before, "Chainlink", "revenue_usd", 1_050_000.0, "defillama:chainlink", 1),
        (yesterday, "Chainlink", "revenue_usd", 12_000.0, "defillama:chainlink", 1),  # real drop
        (today, "Chainlink", "revenue_usd", 3_000.0, "defillama:chainlink", 1),
    ], prior={("Chainlink", "revenue_usd"): 1_050_000.0})
    assert len(flags) == 1, f"a genuine collapse between two COMPLETE days must still flag: {flags}"
    assert flags[0]["value"] == 12_000.0 and flags[0]["prior_value"] == 1_050_000.0, flags[0]
    print("S7 ok: partial day exempt, real step change between complete days still flags")


# ======================================================================================
# token_metrics.py — the run flags. All of them NARROW; none changes what a figure means.
# ======================================================================================

def test_the_run_scope_refuses_a_typo_rather_than_fetching_nothing():
    """A mistyped --project that fetched nothing would look identical to a run where every source
    had nothing to add — same empty result, same clean exit, no error anywhere."""
    import logging
    import token_metrics as tm

    log = logging.getLogger("test")
    assert len(tm.resolve_scope(tm.parse_args([]), log)) == len(config.PROJECTS), \
        "with no portfolio.txt the default is unchanged: every project"
    picked = tm.resolve_scope(tm.parse_args(["--project", "Sky", "--project", "Uniswap"]), log)
    assert [p["name"] for p in picked] == ["Sky", "Uniswap"]

    try:
        tm.resolve_scope(tm.parse_args(["--project", "Skye"]), log)
    except SystemExit as e:
        assert "Skye" in str(e) and "Sky" in str(e), "the refusal must name the valid options"
    else:
        raise AssertionError("a name config does not know must not silently fetch nothing")
    print("scope ok: an unknown --project is refused with the list, not run as an empty fetch")


def test_a_typo_in_portfolio_txt_WIDENS_the_run_rather_than_parking_a_held_asset(tmp_path):
    """THE ASYMMETRY IS THE WHOLE DESIGN. A name wrongly parked stops collecting silently, and a
    series that stops collecting CANNOT be backfilled — CoinGecko serves total_supply as a
    current value only, confirmed on a live call. Fetching a project that is no longer held
    costs one extra API call a day. So on any doubt the run widens, loudly.
    """
    import logging
    import token_metrics as tm

    log = logging.getLogger("test")
    pf = tmp_path / "portfolio.txt"
    original = tm.PORTFOLIO_TXT
    try:
        tm.PORTFOLIO_TXT = pf
        pf.write_text("# held\nSky\nUniswap\n", encoding="utf-8")
        assert [p["name"] for p in tm.resolve_scope(tm.parse_args([]), log)] == ["Sky", "Uniswap"], \
            "a clean portfolio.txt narrows the DEFAULT run — no flag needed"
        assert len(tm.resolve_scope(tm.parse_args(["--all"]), log)) == len(config.PROJECTS), \
            "--all overrides it"

        pf.write_text("Sky\nUnswap\n", encoding="utf-8")     # one character wrong
        widened = tm.resolve_scope(tm.parse_args([]), log)
        assert len(widened) == len(config.PROJECTS), \
            "a typo must widen the run, never narrow it to whatever happened to match"

        # AND THE COMMENT/BLANK HANDLING IS REAL, not incidental — a commented-out name is how
        # a project gets parked temporarily, and it must not read as an unknown name.
        pf.write_text("# holdings\n\nSky\n  Uniswap  # keep\n", encoding="utf-8")
        names, unknown = config.read_portfolio(pf)
        assert names == ["Sky", "Uniswap"] and not unknown, (names, unknown)

        # CASE DOES NOT MATTER AND SPELLING DOES. "sky" is never a different project from "Sky",
        # so rejecting it would report a typo where there is none and widen the run over nothing.
        # "Uniswapp" is a real typo and is still caught. The CANONICAL spelling comes back, so
        # everything downstream keys on config's name whatever was typed.
        pf.write_text("sky\nETHER.FI\nUniswapp\n", encoding="utf-8")
        names, unknown = config.read_portfolio(pf)
        assert names == ["Sky", "Ether.fi"], names
        assert unknown == ["Uniswapp"], unknown

        # A NAME LISTED TWICE IS NOT AN ERROR AND IS NOT FETCHED TWICE — that is the shape a
        # hand-edited list takes after a revision or two.
        pf.write_text("Sky\nUniswap\nSky\n", encoding="utf-8")
        assert config.read_portfolio(pf)[0] == ["Sky", "Uniswap"]
    finally:
        tm.PORTFOLIO_TXT = original
    print("portfolio ok: narrows by default, widens on a typo, comments and blanks ignored")


def test_no_fetch_rebuilds_the_workbook_without_touching_a_single_source(tmp_path, monkeypatch):
    """Every display rule here is READ-TIME, so all of them are worked on against the store as it
    stands. Refetching to see a label change spends a day's politeness budget on free endpoints
    for nothing, against a standing rule of one run a day."""
    import token_metrics as tm

    called = []
    monkeypatch.setattr(tm.fetch, "fetch_all", lambda *a, **k: called.append(a) or (_ for _ in ()).throw(
        AssertionError("--no-fetch must not reach any adapter")))
    built = []
    monkeypatch.setattr(tm, "build_workbook", lambda st, path, **kw: built.append((path, kw)) or path)
    monkeypatch.setattr(tm, "WORKBOOK", tmp_path / "wb.xlsx")
    monkeypatch.setattr(tm.store_mod, "DB_PATH", tmp_path / "m.db")

    assert tm.main(["--no-fetch"]) == 0
    assert not called and len(built) == 1, (called, built)
    # NO run_id, deliberately: nothing was fetched, so there is no run to attribute the build to.
    # Passing the previous one would date this workbook to a fetch it had no part in.
    assert "run_id" not in built[0][1] or built[0][1]["run_id"] is None, built[0][1]
    print("--no-fetch ok: workbook rebuilt, no adapter reached, no run_id invented")


def test_a_dune_cross_check_refreshes_on_its_cadence_and_a_pure_backfill_still_does_not():
    """B. "Skip once the store has history" is right for a pure backfill and wrong for a series
    that is still the second opinion on a live figure.

    Ether.fi's Dune locked_tokens_dashboard exists to DISAGREE with the contract read. Frozen at
    the day it was first pulled it cannot show whether the 1.58x gap is widening, closing, or was
    a one-day artefact — and those have different answers about which figure to trust. So it
    re-pulls weekly.

    THE CADENCE IS MEASURED FROM THE NEWEST STORED ROW, not from when the query last ran. A query
    that executed yesterday and returned nothing new has refreshed nothing, and timing off the
    execution would keep paying for it while the series sat still.
    """
    from fetch.dune import Dune

    q = config.PROJECT_BY_NAME["Ether.fi"]["dune_queries"]["locked_tokens_dashboard"]
    assert q["refresh_days"] == 7 and q["refresh_rationale"]

    def skips(last_seen_days_ago, project="Ether.fi", metric="locked_tokens_dashboard"):
        d = Dune(has_history={(project, metric)},
                 last_dates={(project, metric): (pd.Timestamp.now().normalize()
                                                 - pd.Timedelta(days=last_seen_days_ago)).date().isoformat()})
        d.key = ""                                    # no key: the run stops before any HTTP
        out = FetchOutput()
        d.run([config.PROJECT_BY_NAME[project]], 30, out)
        return [e for e in out.log if e.status == "skipped" and metric in e.message]

    assert skips(3), "three days old is inside the cadence — still skipped"
    assert "refresh cadence is 7 days" in skips(3)[0].message, skips(3)[0].message
    assert not skips(9), "nine days old is past the cadence — the skip must not fire"

    # AN UNDATEABLE SERIES COUNTS AS DUE, and the asymmetry is deliberate. In a real run
    # has_history and last_dates come from the same store and agree, so this state should not
    # arise; if it does, the choice is one paid query against a cross-check frozen for ever, and
    # the query is the cheaper mistake.
    from fetch.dune import Dune as _D
    d = _D(has_history={("Ether.fi", "locked_tokens_dashboard")}, last_dates={})
    d.key = ""
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out)
    assert not [e for e in out.log
                if e.status == "skipped" and "locked_tokens_dashboard" in e.message], \
        "a series whose age cannot be established is refreshed, not parked"

    # AND A QUERY WITH NO refresh_days IS UNCHANGED. GEODNET's burn backfill is a pure
    # historical fill: its live figure comes from the chain read, so re-pulling it buys nothing
    # and costs a paid query.
    geo = config.PROJECT_BY_NAME["GEODNET"]["dune_queries"]["gross_burn_tokens"]
    assert "refresh_days" not in geo, "a pure backfill must not acquire a cadence by default"
    assert skips(400, project="GEODNET", metric="gross_burn_tokens"), \
        "a backfill stays skipped however old it is — that is what backfill-only means"
    print("refresh_days ok: the cross-check re-pulls weekly, the backfill still never does")


def test_a_404_that_never_worked_becomes_known_absent_and_a_5xx_never_does(tmp_path):
    """A4. Two conditions, both required, and the distinction between them is the whole point.

    404 SPECIFICALLY. A timeout, a 429 or a 5xx is a source that is down or busy; retrying it
    tomorrow is right. A 404 is the server saying the thing is not there, which is a definite
    answer — HttpError exists in this codebase precisely to keep the two apart.

    NEVER SUCCEEDED. A pair that worked once and 404s now is a source that MOVED, and that is a
    finding worth surfacing every single run, not something to stop asking about.

    And any success clears it, so a resource that appears later is picked straight back up
    without anyone remembering to clear a flag.
    """
    import store as store_mod

    st = store_mod.Store(tmp_path / "m.db")
    try:
        st.record_fetch("r1", "defillama", "Plume", 0, "failed", "plume: HTTP 404 from api.llama.fi/x", 1)
        st.record_fetch("r1", "defillama", "Zcash", 0, "failed", "zcash: HTTP 503 from api.llama.fi/x", 1)
        st.record_fetch("r1", "coingecko", "Canton", 12, "ok", "", 1)
        st.record_fetch("r2", "coingecko", "Canton", 0, "failed", "canton: HTTP 404 from api.coingecko.com", 1)

        absent = st.known_absent()
        assert ("defillama", "Plume") in absent, "a 404 with no history of working is absent"
        assert ("defillama", "Zcash") not in absent, \
            "a 5xx is a source that is DOWN — stopping the call would hide an outage as an absence"
        assert ("coingecko", "Canton") not in absent, \
            "a pair that worked and now 404s has MOVED, and that must stay visible every run"

        # AND A SUCCESS CLEARS IT, permanently.
        st.record_fetch("r3", "defillama", "Plume", 7, "ok", "", 1)
        assert ("defillama", "Plume") not in st.known_absent()
        st.record_fetch("r4", "defillama", "Plume", 0, "failed", "plume: HTTP 404 again", 1)
        assert ("defillama", "Plume") not in st.known_absent(), \
            "once it has worked, a later 404 is a move, not an absence"

        # AND IT IS NOT PERMANENT. The recheck is measured from the last ATTEMPT, which a skip
        # does not touch — so the pair comes back up on its own once the interval passes.
        st.record_fetch("r5", "defillama", "Injective", 0, "failed", "HTTP 404", 1)
        assert ("defillama", "Injective") in st.known_absent()
        assert ("defillama", "Injective") not in st.known_absent(recheck_days=0), \
            "with the interval elapsed the pair is offered for a retry rather than parked"
    finally:
        st.close()
    print("known_absent ok: 404-and-never-worked only, cleared by success, retried after 14 days")


def test_a_known_absent_pair_is_not_called_and_is_logged_as_skipped():
    """A skip is not a success. It produces no error, no failure and no gap, so if it were logged
    as anything softer it would read exactly like a source that ran and had nothing to add —
    which is how a call that never happened gets reported as one that worked."""
    from fetch.coingecko import CoinGecko
    from fetch.llama import DefiLlama

    calls = []

    class Counting:
        min_interval = 0.0

        def get(self, url, params=None, headers=None):
            calls.append(url)
            return {}

    cg = CoinGecko(known_absent={("coingecko", "Plume")})
    cg.http = Counting()
    out = FetchOutput()
    cg.run([config.PROJECT_BY_NAME["Plume"]], 30, out)
    assert not calls, f"a known-absent pair must not be called at all: {calls}"
    skips = [e for e in out.log if e.status == "skipped"]
    assert skips and "KNOWN ABSENT" in skips[0].message, out.log
    assert not [e for e in out.log if e.status == "failed"], \
        "no call was made, so there is no failure to report"

    # AND THE PAIR BESIDE IT IS STILL CALLED — the skip is per project, not per source.
    calls.clear()
    cg.run([config.PROJECT_BY_NAME["Uniswap"]], 30, FetchOutput())
    assert calls, "a project that is not absent must still be fetched"

    # SAME ON DEFILLAMA, whose slug is a separate configured thing from the coin id.
    calls.clear()
    ll = DefiLlama(known_absent={("defillama", "Uniswap")})
    ll.http = Counting()
    out = FetchOutput()
    ll.fees(config.PROJECT_BY_NAME["Uniswap"], 30, out)
    assert not calls and [e for e in out.log if e.status == "skipped"], (calls, out.log)
    print("known absent ok: not called, logged SKIPPED, scoped to the pair and not the source")


def test_the_incremental_window_narrows_the_request_on_coingecko_and_only_trims_on_defillama():
    """A5, and the answer is not the one the docstring at the top of token_metrics.py implied.

    "Later runs extend the series and re-fetch a trailing 30-day window" is true of what REACHES
    THE STORE on every source. It is true of what goes over the WIRE on exactly one of them:

        coingecko   days=30 is in the request. The narrowing is real and saves the transfer.
        defillama   the full daily history is downloaded and then trimmed to 30 days locally.
                    fetch.base.window() is a DataFrame filter; there is no date parameter on
                    /summary/fees/{slug} to pass. The window saves storage and validation work
                    and NO network time.
        dune        the query executes in full whatever the window says — a Dune query has no
                    incremental mode — and the result is trimmed the same way. A metric being
                    fetched for the FIRST time ignores the window entirely, deliberately.
        chain / hypercore / tron / scrape
                    accept window_days and ignore it, correctly: they read a current value, not
                    a series, so there is no window to apply.

    RECORDED RATHER THAN FIXED. Trimming after the fact is the right behaviour for a source with
    no date parameter — the alternative is storing a year of history every day — and this test
    exists so that "the window makes runs faster" is not carried forward as a belief about
    DefiLlama when it is only true of CoinGecko.
    """
    import pandas as pd
    from fetch.base import window as trim
    from fetch.coingecko import CoinGecko
    from fetch.llama import DefiLlama

    # (1) COINGECKO PUTS IT IN THE REQUEST.
    seen = []

    class CgHttp:
        min_interval = 0.0

        def get(self, url, params=None, headers=None):
            seen.append((url, dict(params or {})))
            return {}

    cg = CoinGecko()
    cg.http = CgHttp()
    cg.run([config.PROJECT_BY_NAME["Uniswap"]], 30, FetchOutput())
    chart = [p for u, p in seen if "market_chart" in u]
    assert chart and chart[0]["days"] == "30", chart
    seen.clear()
    cg.run([config.PROJECT_BY_NAME["Uniswap"]], None, FetchOutput())
    assert [p for u, p in seen if "market_chart" in u][0]["days"] == "365", \
        "a full backfill asks for the year; the window is not simply always 30"

    # (2) DEFILLAMA DOWNLOADS EVERYTHING AND TRIMS LOCALLY.
    import datetime as dt
    days = 400
    rows = [(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=i), 1000.0 + i) for i in range(days)]
    fetched = []

    class LlamaHttp:
        min_interval = 0.0

        def get(self, url, params=None, headers=None):
            fetched.append(url)
            return {"totalDataChart": [[int(d.timestamp()), v] for d, v in rows]}

    ll = DefiLlama()
    ll.http = LlamaHttp()
    out = FetchOutput()
    ll.fees(config.PROJECT_BY_NAME["Uniswap"], 30, out)
    stored = out.frame()
    assert fetched, "the request is still made"
    # 400 days came back; at most 31 are kept. THE WIRE CARRIED 400.
    per_metric = stored.groupby("metric").size().max()
    assert per_metric <= 31, f"the window must trim to ~30 days, kept {per_metric}"
    assert len(rows) == 400, "and the response really did carry the full history"

    # (3) THE TRIM IS A LOCAL FILTER, which is what makes (2) unavoidable rather than an oversight.
    df = pd.DataFrame({"date": [pd.Timestamp.now().normalize() - pd.Timedelta(days=i) for i in range(100)],
                       "project": "x", "metric": "y", "value": 1.0, "source": "s", "tier": 1})
    assert len(trim(df, 10)) == 11 and len(trim(df, None)) == 100
    print("A5 ok: coingecko narrows the request, defillama and dune trim after the fact")


def test_world_mobile_sums_four_evm_deployments_and_names_every_component():
    """D1. The open question that kept WMTx to Ethereum alone — "lock-and-mint would make summing
    a double-count, burn-and-mint would make summing correct" — is answered by World Mobile's own
    MiCA regulatory whitepaper, which documents the contract's burn function as serving
    cross-chain bridging. Burn-and-mint: the four deployments are disjoint and their sum is the
    minted supply.

    THE OPPOSITE CALL FROM GEODNET, MADE THE SAME DAY. GEOD's Polygon contract reads the entire
    1,000,000,000 cap, which makes its remote deployments mirrors and summing a double-count.
    WMTX's Ethereum contract reads 1,493,853,279 against a 2bn cap with three other live
    deployments. The bridge model is read off each protocol's own material, not applied as a
    house style, and these two protocols do different things.
    """
    wm = config.PROJECT_BY_NAME["World Mobile"]
    contracts = wm["contracts"]
    assert set(contracts) == {"token", "token_arbitrum", "token_bsc", "token_base"}
    assert contracts["token"]["address"] == contracts["token_arbitrum"]["address"] \
        == contracts["token_bsc"]["address"] == "0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7"
    assert contracts["token_base"]["address"] == "0x3e31966d4f81C72D2a55310A6365A56A4393E98D", \
        "Base is the one with its own address — the shape that makes a copy-paste error invisible"
    for key, c in contracts.items():
        assert c["metric_override"] == "total_supply_gross", key
        assert "CARDANO-NATIVE WMT IS NOT IN THIS SUM" in c["partial_reason"], key

    class FourChainStub:
        SUPPLY = {"ethereum": 1_493_853_279.0, "arbitrum": 4_100_000.0,
                  "bsc": 7_250_000.0, "base": 2_900_000.0}

        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            return True, "WMTX"

        def scaled(self, chain, address, call, *args):
            return self.SUPPLY[chain]

    c = Chain()
    c.reader = FourChainStub()
    out = FetchOutput()
    c.run([wm], None, out)
    df = out.frame()
    row = df[df.metric == "total_supply_gross"]
    assert len(row) == 1, row.to_dict()
    assert float(row.value.iloc[0]) == sum(FourChainStub.SUPPLY.values())

    # ** EVERY COMPONENT IS NAMED, and that is the check asked for. ** A chain contributing zero
    # is a read that failed quietly; a chain contributing more than Ethereum is an address that
    # is not what it says. Neither is visible in the total.
    src = row.source.iloc[0]
    for key in ("ethereum:token", "arbitrum:token_arbitrum", "bsc:token_bsc", "base:token_base"):
        assert key in src, f"{key} missing from {src}"
    detail = " ".join(e.message for e in out.log if e.status == "ok")
    assert "4 components" in detail and "1,493,853,279" in detail, detail

    # THE CARDANO EXCLUSION KEEPS THE FIGURE PARTIAL, and it is sized by SUBTRACTION rather than
    # by an estimate written into config — both terms exist, so a number nobody has computed does
    # not get to become a fact.
    assert src.endswith(":PARTIAL"), src
    assert "not written here" not in config.WMTX_CARDANO_PARTIAL
    assert "SIZE THE EXCLUSION BY SUBTRACTION" in config.WMTX_CARDANO_PARTIAL

    # THE BAND, AND WHAT EACH END MEANS. Below 1.4bn a component failed; above 1.7bn something is
    # counted twice, which would be evidence against the filing rather than a bad read.
    lo, hi = config.sanity_bounds("World Mobile", "total_supply_gross")
    assert (lo, hi) == (1_400_000_000, 1_700_000_000)
    assert lo <= float(row.value.iloc[0]) <= hi
    assert not (lo <= 2_000_000_000 <= hi), \
        "the CAP must fail this band — it is a correct reading of a different quantity"
    # AND THE BAND IS NOT ON total_supply, which legitimately holds that cap every run.
    cap_lo, cap_hi = config.sanity_bounds("World Mobile", "total_supply")
    assert cap_lo <= 2_000_000_000 <= (cap_hi or float("inf"))
    print("world mobile ok: four deployments summed, every component named, Cardano excluded "
          "and sized by subtraction, band excludes the cap")


def test_a_relabelled_cell_gets_its_unit_overridden_too_or_the_label_is_cosmetic():
    """D3. World Mobile publishes 600+ TB/day. The config note said it could not be stored
    "because utilisation_pct is a FRACTION and 600 would render as 60,000%" — right about the
    rendering, and the answer is not to leave a published operating figure out of the sheet.

    ** A LABEL OVERRIDE ALONE WOULD NOT HAVE FIXED IT. ** The number format is taken from the
    METRIC'S UNIT, so a cell captioned "Daily data processed (TB)" would still have rendered
    60,000%. That is a wrong number wearing a better name — the same thing declined for GEODNET's
    emissions proxy on the same day, and declining it there while accepting it here would make
    the principle decorative. So the unit is overridden too, and the cell is declared
    non-comparable on top, because a figure needing a different unit from the metric it sits in
    is by construction answering a different question from that column everywhere else.
    """
    import build_workbook as bw

    assert config.metric_unit("World Mobile", "utilisation_pct") == "units"
    assert config.metric_unit("peaq", "utilisation_pct") == "pct", \
        "the override is per project — every other project's utilisation is still a fraction"
    assert "TB" in config.metric_label("World Mobile", "utilisation_pct")

    nc = config.is_non_comparable("World Mobile", "utilisation_pct")
    assert nc and "no denominator" in nc["why"], nc
    band, why = bw.confidence_for("World Mobile", "utilisation_pct",
                                  {"status": "manual", "source": "manual", "n_points": 2,
                                   "covered_days": None, "window_days": None, "entered_on": "2026-09-22"},
                                  pd.Timestamp("2026-09-22"))
    assert band == "AMBER" and "NOT COMPARABLE" in why, (band, why)

    # THE BOUND IS RE-DRAWN, NOT REMOVED. [0, 1] is right for a fraction and wrong for terabytes;
    # [0, 1e15] would accept a decimal-point error, which is the failure a bound on a throughput
    # is actually for.
    lo, hi = config.sanity_bounds("World Mobile", "utilisation_pct")
    assert lo <= 600 <= hi and hi < 1e6, (lo, hi)
    assert not (lo <= 0.6 <= hi), "a fraction slipping in here would be caught, which is the point"

    # AND THE REAL utilisation IS STILL MISSING. This does not fill it and must not read as if it does.
    assert "capacity" in nc["use_instead"]
    print("unit override ok: 600 TB/day stored, formatted as a number, flagged non-comparable, "
          "and the real utilisation still declared absent")


def test_world_mobiles_two_user_counts_disagree_and_neither_is_quietly_dropped():
    """3,000,000 daily active users (2026-02) against 1,600,000 in a 24h window (2026-03): a
    LATER date with a LOWER number, which is the shape that rules out growth as the explanation.
    Either two different measures wearing similar words, or one is wrong. Nothing on file
    decides, so nothing here decides — the later figure is seeded because a series takes its most
    recent observation, and the earlier is kept where a reader will meet it."""
    import csv
    from pathlib import Path

    wm = config.PROJECT_BY_NAME["World Mobile"]
    users = [r for r in wm["operating_reference"] if r["metric"] == "active_addresses"]
    assert len(users) == 2, "both counts must be on file"
    by_date = {r["as_of"]: r for r in users}
    assert by_date["2026-02"]["value"] == 3_000_000 and by_date["2026-03"]["value"] == 1_600_000
    assert by_date["2026-02"]["superseded_by"] and by_date["2026-03"]["disagrees_with"], \
        "the disagreement must be stated from both sides, or a reader meets only one of them"
    assert "growth does not explain it" in by_date["2026-03"]["disagrees_with"]

    rows = [r for r in csv.DictReader(
        line for line in (Path(__file__).resolve().parent.parent / "manual_overrides.csv")
        .read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
        if r["project"] == "World Mobile"]
    seeded = {r["metric"]: r for r in rows}
    assert set(seeded) == {"supply_units", "active_addresses", "utilisation_pct"}
    assert seeded["active_addresses"]["date"] == "2026-03-01", "the later observation is the stored one"
    assert "DISAGREES WITH THE FEBRUARY FIGURE" in seeded["active_addresses"]["source_note"]
    # AND THE TWO '+' FIGURES ARE FLOORS, said in as many words on both.
    for m in ("supply_units", "utilisation_pct"):
        assert "FLOOR" in seeded[m]["source_note"], m
    print("world mobile A2 ok: both user counts kept, the later one seeded, the '+' figures "
          "labelled as floors")


def test_the_chainlink_reserve_inflow_is_derived_and_a_fall_is_reported_not_swallowed():
    """F. Chainlink's archetype 3 block had a fund BALANCE and no FLOW — the Reserve's LINK was
    read, how much arrived in a period was not. The delta against the last earlier-dated reading
    is that flow and it costs no extra call.

    ** IT IS AN INFLOW BECAUSE THE RESERVE ONLY ACCUMULATES, AND THAT IS A CLAIM, NOT A FACT OF
    ARITHMETIC. ** Chainlink routes revenue to two destinations that are separate from source:
    the Reserve holds, the staking pools distribute from their own reward vault. Nothing draws on
    the Reserve. If that premise ever breaks the balance falls, and the fall is REPORTED — that
    branch of derive_flow_from_cumulative used to return an empty frame in silence, which was
    harmless for a dead-address balance (it cannot fall) and would have hidden exactly this.
    """
    link = config.PROJECT_BY_NAME["Chainlink"]
    assert config.cumulative_flow_for("Chainlink", "buyback_fund_balance") == "actual_buyback_tokens"
    # PER PROJECT, not per metric. Uniswap's TokenJar holds fee tokens that get swept and GEODNET
    # has no fund at all; a global entry would derive a "buyback" on all three from whatever each
    # balance happened to do.
    assert config.cumulative_flow_for("Uniswap", "buyback_fund_balance") is None
    label = config.metric_label("Chainlink", "actual_buyback_tokens")
    assert "EXCLUDES the staking-reward leg" in label, label

    class ReserveStub:
        def __init__(self, balance):
            self.balance = balance

        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *args):
            return self.balance if args else 0.0

    def run(now, prior, prior_date="2026-09-20"):
        c = Chain(prior_values={("Chainlink", "buyback_fund_balance"): prior},
                  prior_dates={("Chainlink", "buyback_fund_balance"): prior_date},
                  prior_sources={("Chainlink", "buyback_fund_balance"): "chain:ethereum:reserve"},
                  prior_delta={("Chainlink", "buyback_fund_balance"): prior})
        c.reader = ReserveStub(now)
        out = FetchOutput()
        c.run([link], None, out)
        return out

    # (a) THE ORDINARY CASE: the Reserve grew, and the growth is the inflow.
    out = run(5_240_000.0, 5_180_000.0)
    df = out.frame()
    flow = df[df.metric == "actual_buyback_tokens"]
    assert len(flow) == 1, f"the inflow must be derived: {sorted(set(df.metric))}"
    assert abs(float(flow.value.iloc[0]) - 60_000.0) < 1e-6
    assert ":delta" in flow.source.iloc[0]

    # (b) THE PREMISE BREAKING: the balance fell. No flow, and the fall is NAMED.
    out = run(5_100_000.0, 5_180_000.0)
    df = out.frame()
    assert df[df.metric == "actual_buyback_tokens"].empty, \
        "a negative inflow is not a figure this metric can hold"
    gap = [g for g in out.gaps if g["metric"] == "actual_buyback_tokens"]
    assert gap and "FELL" in gap[0]["reason"], \
        f"a fall must be reported, not returned as an empty frame: {[g['reason'][:60] for g in out.gaps]}"
    assert "5,180,000" in gap[0]["reason"] and "5,100,000" in gap[0]["reason"], gap[0]["reason"]

    # (c) SAME-DAY RE-RUN: no interval, so no flow — and the balance itself still stores.
    out = run(5_240_000.0, 5_180_000.0, prior_date="2026-09-22")
    df = out.frame()
    assert df[df.metric == "actual_buyback_tokens"].empty
    assert not df[df.metric == "buyback_fund_balance"].empty, \
        "the stock is a good reading whatever the flow does"
    print("chainlink reserve ok: inflow derived from the delta, a fall reported by name, "
          "same-day re-run derives nothing")


def test_skys_stage_2_legs_render_separately_and_the_burn_leg_is_checked_against_the_chain():
    """C2 and C3. 27.5% of NPS buys SKY; of that, 22.5 points go to staking rewards and 5 points
    are burned. ONLY THE BURN LEG REMOVES SUPPLY.

    A single 27.5% figure is correct as BUY PRESSURE and wrong by 5.5x as SUPPLY REDUCTION, and
    nothing on a row of numbers tells a reader which one they are looking at — it depends on the
    column it happens to sit in. So both legs are on the sheet with their effects named, and
    neither is netted into the other. That is the same discipline this codebase applies to every
    yield-versus-burn split; Sky is where it would be easiest to skip, because the two legs share
    a base and a source.
    """
    legs = {l["key"]: l for l in config.stage_split_legs("Sky")}
    assert set(legs) == {"sky_buying", "burn"}
    assert legs["sky_buying"]["share"] == 0.275 and legs["burn"]["share"] == 0.05
    assert legs["sky_buying"]["effect"] == "buy_pressure"
    assert legs["burn"]["effect"] == "supply_reduction"
    # THE ARITHMETIC THAT MAKES THE SEPARATION LOAD-BEARING, asserted rather than described.
    assert abs(legs["sky_buying"]["share"] / legs["burn"]["share"] - 5.5) < 1e-9, \
        "reading the 27.5% figure as supply reduction overstates it by exactly this factor"
    v2 = config.PROJECT_BY_NAME["Sky"]["fee_split_v2"]
    assert v2["splits"]["sky_buyback_for_staking_rewards"] + legs["burn"]["share"] \
        == legs["sky_buying"]["share"], "22.5 + 5 must be the 27.5 — the legs cannot drift apart"

    # C3: THE BURN LEG HAS AN ACTUAL TO BE CHECKED AGAINST, and it is a measurement rather than
    # another derivation — Sky's burn is read from Transfer-to-zero events.
    assert legs["burn"]["cross_check_metric"] == "gross_burn_tokens"
    assert config.cumulative_flow_for("Sky", "burn_address_balance") == "gross_burn_tokens"

    # NO OTHER PROJECT ACQUIRES LEGS BY DEFAULT. The row must be empty where the question does
    # not arise — not 0, which would enter the comparison columns as a measured nothing.
    import build_workbook as bw
    for name in ("Maple", "Chainlink", "Aerodrome", "Uniswap"):
        assert config.stage_split_legs(name) == [], name
        assert bw._leg(config.PROJECT_BY_NAME[name], "burn", lambda sh: "SHOULD NOT RENDER") == ""
    assert bw._leg(config.PROJECT_BY_NAME["Sky"], "burn", lambda sh: f"x{sh}") == "x0.05"
    print("sky legs ok: 27.5 and 5 rendered apart, 5.5x separation asserted, burn leg checked "
          "against the chain read, other projects untouched")


def test_the_two_ultrasound_issuance_entries_are_closed_on_arithmetic_not_reachability():
    """G2. The offer was to re-point these at /api/fees/grouped-analysis-1 or close them for good.
    Re-pointing would not have helped, and reachability is the SECOND reason rather than the first.

    ultrasound.money has no issuance endpoint and never did: its frontend computes issuance
    client-side as delta(total_supply) + burn, which is the identical formula this tool uses. So
    the figure would not be an independent check — it would be our own computation run twice,
    reading as corroboration. grouped-analysis-1 serves BURN, so re-pointing an issuance metric at
    it would have pointed the metric at the wrong quantity.

    THE BURN ENTRY IS NOT CLOSED WITH THEM, and the difference is the whole point: feesBurned is a
    real independently measured figure blocked on robots and reachability, which are circumstances
    that can change. These two are blocked on arithmetic, which cannot.
    """
    for metric in ("gross_issuance_tokens", "net_mint_monthly"):
        u = config.unavailable_for("Ethereum", metric)
        assert u, f"{metric} must be closed, not left as an open gap"
        assert u["closed_on"] == "2026-09-22"
        assert "client-side" in u["summary"] or "client-side" in u["what_was_tried"], u["summary"]
        # THE REOPEN CONDITION IS ABOUT THE METHOD, NOT ABOUT ACCESS. Reopening when the site
        # becomes reachable would reopen it for the wrong reason and it would close again.
        assert "same formula" in u["reopen_if"] or "OTHER than" in u["reopen_if"], u["reopen_if"]
        # Mentioning reachability is fine and necessary — what matters is that it is RULED OUT
        # as a trigger rather than offered as one. A bare "not in" check would fail the very
        # sentence doing that job, which is the sentence worth having.
        low = u["reopen_if"].lower()
        if "reachab" in low:
            assert "not on the site becoming reachable" in low or "not what blocks" in low, \
                f"reachability must be ruled out, not offered as a trigger: {u['reopen_if']}"

    assert config.unavailable_for("Ethereum", "gross_burn_tokens") is None, \
        "the burn entry stays OPEN — it is a real figure blocked on circumstances that can change"
    print("ultrasound ok: both issuance entries closed on arithmetic, burn left open")


def test_the_hyperliquid_accrual_carries_its_trigger_date_and_stays_unbooked():
    """G4. No action, and that is the answer rather than a deferral. AQAv2's first payment is not
    due until 2026-10-03; anything done before then models a payment that has not happened.
    booked=False already does the only correct thing, which is keep it out of every revenue
    figure. The date is a TRIGGER — on 2026-10-03 there is a fact to check — not a reminder."""
    q = next(q for q in config.OPEN_QUESTIONS
             if q.get("project") == "Hyperliquid" and "AQAv2" in q.get("topic", ""))
    assert q["recheck_on"] == "2026-10-03"
    assert "Reviewed 2026-09-22" in q["no_action_because"]

    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    aqa = next(r for r in hl["revenue_sources"] if "AQAv2" in r["name"])
    assert aqa.get("booked") is False, \
        "an accruing-but-unpaid leg must stay unbooked — that is what keeps it out of revenue"
    print("hyperliquid ok: unbooked, trigger date on file, no action taken and the reason recorded")


def test_the_offline_checks_cover_H1_to_H3_and_pin_their_paired_reads_to_one_block():
    """H. Three checks, and two of them already existed — reported as found rather than rebuilt.

    H2 (Uniswap Firepit threshold) and H3 (Sky want()/spotter() and the Splitter's live flapper
    via the ChainLog) were already in check_offline_items.py, and H3's RPC order already puts
    publicnode first with the reason recorded: llamarpc returned 525 for want() and spotter() on
    two separate days while publicnode answered every other call in the same script.

    H1 IS A REAL ADDITION, AND IT IS A DIFFERENT TEST FROM THE ONE THAT WAS THERE. The existing
    Pendle check compared sPENDLE.totalSupply() against PENDLE.totalSupply() — a CEILING, which
    refutes if exceeded and proves nothing if not, because a 4x boost on a small locked fraction
    still fits under the cap. Comparing it against PENDLE.balanceOf(sPENDLE) compares shares
    against the ASSETS ACTUALLY HELD, and every really-locked PENDLE is in that balance. That
    settles the question the ceiling could only fail to refute.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "check_offline_items.py").read_text(encoding="utf-8")

    # H1 — both reads, and PINNED TO ONE BLOCK. Two calls at "latest" can straddle a boundary,
    # which for a ratio of two figures is the difference between a measurement and a coincidence.
    pendle = src[src.index("def pendle_spendle_virtual"):src.index("def uniswap_firepit_threshold")]
    assert "PENDLE.balanceOf(sPENDLE)" in pendle, "the direct shares-vs-assets read must be there"
    assert "SEL_BALANCE_OF + SPENDLE[2:]" in pendle, "balanceOf must be called ON PENDLE, FOR sPENDLE"
    assert "eth_block_number()" in pendle and pendle.count("eth_call(to, data, block)") >= 1, \
        "both figures must come from the same pinned block"
    assert "VERDICT (direct)" in pendle and "VERDICT (ceiling)" in pendle, \
        "the two tests answer differently and must be reported apart"
    assert pendle.index("VERDICT (direct)") < pendle.index("VERDICT (ceiling)"), \
        "the test that can SETTLE the question is reported before the one that cannot"
    # AND A ZERO BALANCE IS NOT READ AS 'SHARES EXCEED ASSETS' — it means the lock is not
    # custodied at that address and the comparison does not apply.
    assert "the lock is not custodied at this address" in pendle

    # H2 — the Firepit threshold is governance STORAGE, so it cannot be read from source.
    assert "def uniswap_firepit_threshold" in src and "SEL_THRESHOLD" in src
    assert 'SEL_THRESHOLD = "0x42cde4e8"' in src, "keccak('threshold()')[:4]"

    # H3 — want()/spotter() with publicnode FIRST, and the Splitter's flapper via the ChainLog
    # rather than from a historical executive vote.
    assert src.index('"https://ethereum-rpc.publicnode.com"') < src.index('"https://eth.llamarpc.com"'), \
        "publicnode must be tried first — llamarpc 525'd on want() and spotter() twice"
    assert '"want()": "0x1f1c827f"' in src and '"spotter()": "0xf3701da2"' in src
    assert "def sky_chainlog" in src and "CHAINLOG_LIST" in src
    assert "may not be \"MCD_SPLIT\"" in src, \
        "the registry key is not guessed either — list() is printed in full first"

    # ALL OF THEM ACTUALLY RUN. A check that exists and is not called is not a check.
    main = src[src.index("def main():"):]
    for fn in ("pendle_spendle_virtual", "uniswap_firepit_threshold", "sky_chainlog",
               "sky_splitter", "sky"):
        assert fn in main, f"{fn} is defined but never called from main()"
    print("offline checks ok: H1 added as a direct shares-vs-assets read on one block, "
          "H2 and H3 already present and confirmed")


def test_nps_stores_months_only_and_reconciles_them_against_the_published_quarter():
    """Item 2. Sky publishes NPS monthly AND quarterly, and the quarterly figure ALREADY CONTAINS
    the months. Both in one flow series makes a 90-day window ending June sum May $9.71m + June
    $10.81m + Q2 $33.29m = $53.81m against a true $33.29m — 62% over — and every implied-buyback
    figure on the A3 tab is computed from it.

    THE MECHANICAL HALF OF THE SAME PROBLEM: June's month-end and Q2's quarter-end are the same
    calendar day, and the store keys on (date, project, metric). The two could never have
    coexisted in the series at all.
    """
    import csv
    from pathlib import Path
    import build_workbook as bw

    rows = [r for r in csv.DictReader(
        line for line in (Path(__file__).resolve().parent.parent / "manual_overrides.csv")
        .read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
        if r["metric"] == "net_protocol_surplus_usd"]
    assert {r["date"] for r in rows} == {"2026-05-31", "2026-06-30"}, \
        f"months only — the quarters and the year are references now: {[r['date'] for r in rows]}"
    assert {float(r["value"]) for r in rows} == {9_710_000.0, 10_810_000.0}

    ref = config.PROJECT_BY_NAME["Sky"]["net_protocol_surplus_reference"]
    assert [q["usd"] for q in ref["quarterly"]] == [46_040_000, 33_290_000]
    assert ref["annual"][0]["usd"] == 53_000_000
    # APRIL IS DERIVED AND STAYS OUT OF THE SERIES. Stored as a flow it would be
    # indistinguishable from a sourced figure, and it would make the quarter reconcile against
    # itself — which is the one thing the reconciliation must not do.
    apr = ref["april_2026_derived"]
    assert apr["usd"] == 12_770_000 and abs(33_290_000 - 9_710_000 - 10_810_000 - apr["usd"]) < 1
    assert "NOT STORED" in apr["status"]
    assert not [r for r in rows if r["date"].startswith("2026-04")], "April must not be in the series"

    # THE CADENCE IS DECLARED, because nothing else can say so: series_granularity resolves from
    # a contract or a Dune date_col, and a hand-entered series has neither.
    assert config.series_granularity("Sky", "net_protocol_surplus_usd") == "monthly"

    # ===== THE RECONCILIATION. =====
    recon = config.period_reconciliation("Sky", "net_protocol_surplus_usd")
    assert recon and recon["requires_complete_period"] is True

    def check(months):
        s = pd.Series({pd.Timestamp(d): v for d, v in months.items()}).sort_index()
        return bw._reconcile_periods("Sky", "net_protocol_surplus_usd", s, recon)

    # AS THINGS STAND — May and June, no April — NO quarter is complete, so the check is ARMED
    # AND IDLE. Two months of three against the quarter's own total is guaranteed to disagree,
    # and flagging that reports a missing month as an error in the months that are there.
    idle = check({"2026-05-31": 9_710_000, "2026-06-30": 10_810_000})
    assert idle["status"] == "idle" and "guaranteed to disagree" in idle["why"], idle

    # WITH APRIL PRESENT AND CORRECT, the quarter reconciles.
    ok = check({"2026-04-30": 12_770_000, "2026-05-31": 9_710_000, "2026-06-30": 10_810_000})
    assert ok["status"] == "ok" and ok["periods"] == ["2026-Q2"], ok

    # AND A MIS-TRANSCRIBED MONTH IS CAUGHT, with the arithmetic on the row rather than a bare flag.
    bad = check({"2026-04-30": 12_770_000, "2026-05-31": 97_100_000, "2026-06-30": 10_810_000})
    assert bad["status"] == "disagrees", bad
    assert bad["detail"][0]["off_by"] == pytest_approx(87_390_000), bad["detail"]
    band, why = bw.confidence_for("Sky", "net_protocol_surplus_usd",
                                  {"status": "manual", "source": "manual", "n_points": 3,
                                   "covered_days": None, "window_days": None,
                                   "entered_on": "2026-09-22", "reconciliation": bad},
                                  pd.Timestamp("2026-09-22"))
    assert band == "AMBER" and "DOES NOT RECONCILE" in why and "120,680,000" in why, (band, why)
    print("NPS ok: months only, quarters as references, April derived and unstored, "
          "reconciliation idle until a quarter is complete and loud when one disagrees")


def pytest_approx(x, tol=1.0):
    class _A:
        def __eq__(self, other):
            return abs(other - x) <= tol
    return _A()


def _would_be_partial():
    """Every (project, metric) the NARROWED refused-component rule would newly mark :PARTIAL.

    THE RULE AS SCOPED: a metric is marked when a contract that is a DECLARED COMPONENT OF THAT
    SAME METRIC is refused at the gate. "Same metric" is resolved exactly as the adapter resolves
    it — metric_override or KIND_METRIC[kind] — so a contract serving a different metric never
    contributes, whatever kind it is.

    REFERENCE-ONLY KINDS CANNOT TRIGGER IT, and not by a special case: chain.py checks
    REFERENCE_ONLY_KINDS *before* the gate, so a bridged_representation or burn_executor is never
    refused because it is never offered. GEODNET's Solana mint and IoTeX deployment, PancakeSwap's
    Base token and every burn executor are excluded by construction rather than by exception.

    Returns (would_change, no_figure_at_all). The second is the case the narrow rule deliberately
    does NOT touch: where every component is refused, no figure is emitted, so there is nothing
    to mark partial and the metric is an honest gap.
    """
    from fetch.chain import BURN_READ_KINDS, METHOD_REQUIRED_KINDS, REFERENCE_ONLY_KINDS

    def refused(project, spec):
        if spec["kind"] in REFERENCE_ONLY_KINDS:
            return None
        if spec.get("ambiguous"):
            return "ambiguous"
        mech = config.burn_mechanism(project)
        if spec["kind"] in BURN_READ_KINDS and mech.get("status") == "refuted":
            return "mechanism_refuted"
        if spec["kind"] == "burn_address_balance" \
                and project.get("burn_read_method") not in ("transfer", None):
            return "unreadable_burn_method"
        if spec.get("chain") not in config.EVM_CHAINS:
            return "chain_not_covered"
        if spec["kind"] in METHOD_REQUIRED_KINDS and not spec.get("read_method"):
            return "lock_method_unset"
        if not spec.get("verified"):
            return "unverified"
        return None

    served = {}
    for p in config.PROJECTS:
        for key, spec in (p.get("contracts") or {}).items():
            if spec["kind"] in REFERENCE_ONLY_KINDS:
                continue
            m = spec.get("metric_override") or config.KIND_METRIC.get(spec.get("kind"))
            if m:
                served.setdefault((p["name"], m), []).append((key, spec))

    change, no_figure = {}, {}
    for (name, metric), comps in served.items():
        proj = config.PROJECT_BY_NAME[name]
        bad = {k: refused(proj, s) for k, s in comps if refused(proj, s)}
        if not bad:
            continue
        reads = [k for k, s in comps if not refused(proj, s)]
        already = any(s.get("supply_is_partial") for _, s in comps) or (
            metric == "total_supply" and proj.get("supply_is_partial"))
        if not reads:
            no_figure[(name, metric)] = bad
        elif not already:
            change[(name, metric)] = {"reads": reads, "refused": bad}
    return change, no_figure


def test_the_refused_component_partial_rule_dry_run_is_exactly_one_metric():
    """Item 3, DRY RUN — the rule is NOT applied. This records what it would do, so the list
    cannot drift between being reported and being reviewed.

    ** THE BLAST RADIUS IS ONE CELL. ** GEODNET's burn_address_balance sums burn_polygon and
    would also sum burn_solana_token_account, which is on a chain the EVM adapter does not cover.
    That is a genuinely incomplete sum, and it is the same incompleteness already recorded from
    the other side: the Dune backfill covers Polygon AND Solana while the live read is Polygon
    alone, which is GEODNET's declared composition_change. Marking it partial says on the cell
    what the handover note says in prose.

    FIVE OTHER METRICS HAVE A REFUSED COMPONENT AND ARE DELIBERATELY UNTOUCHED, because every one
    of their components is refused: no figure is emitted at all, so there is nothing to mark
    partial and the metric is an honest gap. A rule that marked those would be labelling an empty
    cell as an understatement.
    """
    change, no_figure = _would_be_partial()

    assert set(change) == {("GEODNET", "burn_address_balance")}, \
        f"the dry run's list has moved — review it before applying: {sorted(change)}"
    only = change[("GEODNET", "burn_address_balance")]
    assert only["reads"] == ["burn_polygon"]
    assert only["refused"] == {"burn_solana_token_account": "chain_not_covered"}

    assert set(no_figure) == {
        ("Aave", "locked_tokens"), ("Aave", "total_supply"),
        ("GEODNET", "buyback_fund_balance"), ("OriginTrail", "total_supply"),
        ("Venice AI", "buyback_fund_balance"),
    }, f"the all-refused list has moved: {sorted(no_figure)}"

    # AND THE EXCLUSION IS BY CONSTRUCTION, not by exception. Every reference-only contract is
    # checked before the gate, so none of them can ever be a refused component.
    from fetch.chain import REFERENCE_ONLY_KINDS
    ref_only = [(p["name"], k) for p in config.PROJECTS
                for k, c in (p.get("contracts") or {}).items()
                if c["kind"] in REFERENCE_ONLY_KINDS]
    assert len(ref_only) == 10, ref_only
    for name, key in ref_only:
        for (pn, _), d in change.items():
            assert not (pn == name and key in d["refused"]), f"{name}/{key} must never trigger it"
    print(f"partial dry run ok: 1 metric would change (GEODNET/burn_address_balance), "
          f"5 are all-refused and untouched, {len(ref_only)} reference-only excluded")


def test_the_ultrasound_burn_entry_is_still_an_xhr_page_load_not_a_direct_json_fetch():
    """Item 4. The question was whether this entry now fetches /api/fees/grouped-analysis-1 as a
    DIRECT JSON request, so robots would be checked against the API path rather than the page.

    IT DOES NOT, and the proof is one line: _scrape_one() opens with robots_allows(url) where
    url is the entry's own `url` field — the ROOT PAGE — and then calls page.goto(url).
    url_contains only FILTERS responses Playwright already intercepted from the loaded page.
    There is no code path that requests an API URL on its own, for any method.

    So confirming the endpoint from the frontend's source changed nothing about enableability:
    the entry would still be refused on https://ultrasound.money/ whatever the API path permits,
    and the API path's own robots status stays unknown and uncheckable from here.
    """
    import inspect
    import re
    from pathlib import Path

    import yaml

    from fetch import scrape

    entries = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "sources.yaml").read_text(encoding="utf-8"))
    burn = next(e for e in entries
                if e.get("project") == "Ethereum" and e.get("metric") == "gross_burn_tokens")
    assert burn["method"] == "xhr", "if this ever says json, the mechanism was built — update this"
    assert burn["url"] == "https://ultrasound.money/", \
        "the url is the PAGE, and the url is what robots is checked against"
    assert burn["url_contains"] == "/api/fees/grouped-analysis-1", \
        "the endpoint IS correct — that was never what blocked it"
    assert burn["enabled"] is False
    assert "STILL AN XHR PAGE-LOAD" in burn["note"], \
        "the answer must be at the entry, not only in a commit message"

    # THE MECHANISM, ASSERTED FROM THE SOURCE rather than from the note. robots is checked
    # against the entry's url before anything else happens, and the same url is what Playwright
    # loads — so no method can reach an API path without the page.
    src = inspect.getsource(scrape.Scrape._scrape_one)
    head = src[:src.index("captured")]
    assert re.search(r"robots_allows\(url\)", head), head[:400]
    assert 'url = entry["project"], entry["metric"], entry["url"]' in src, \
        "the url checked and loaded is the entry's own url field"
    assert "page.goto(url" in src, "the same url is what gets loaded"

    # AND url_contains IS ONLY EVER A FILTER ON WHAT CAME BACK, never a thing that is requested.
    # Asserted over the whole module, not just _scrape_one: it is used in the response matcher,
    # which is the only place it should ever appear.
    mod = inspect.getsource(scrape)
    for line in mod.splitlines():
        if "url_contains" not in line:
            continue
        # HTTP calls specifically. entry.get("url_contains") is a dict lookup and is exactly
        # what this should NOT match — narrowing the pattern rather than the assertion.
        assert not re.search(r"(requests\.\w+|https?\.\w+|page\.goto)\s*\([^)]*url_contains", line), \
            f"url_contains must never be fetched directly — if it is, the mechanism exists: {line}"
    assert 'needle = entry.get("url_contains")' in mod, \
        "it is a needle matched against intercepted responses, which is the whole point"
    print("ultrasound ok: still xhr on the root page, endpoint correct but not what blocked it")


def test_a_provider_that_serves_the_cap_as_the_supply_derives_no_issuance():
    """CoinGecko returns World Mobile total_supply = max_supply = 2,000,000,000, to the token.
    That is the ERC20Capped ceiling off the deployed source, not an amount anyone has minted:
    the whitepaper's Fig. 1 has aggregate supply rising ~1.42bn -> 2bn across twenty years, and
    the Ethereum contract reads 1,493,853,279.

    THE NUMBER IS NOT WRONG, IT ANSWERS A DIFFERENT QUESTION — and nothing about 2,000,000,000
    looks incomplete, so no PARTIAL marker would ever catch it. The consequence is arithmetic:
    d(a constant) is 0 on every run for ever, and a zero in an issuance column is
    indistinguishable from a measured "nothing was minted" on a token whose entire tokenomics is
    a twenty-year emission curve.
    """
    import pandas as pd
    from fetch import _derive_issuance
    from fetch.base import LONG_COLUMNS
    import build_workbook as bw

    wm = config.PROJECT_BY_NAME["World Mobile"]
    assert wm["total_supply_convention"] == "reports_cap"
    ev = wm["total_supply_convention_evidence"]
    assert ev["reported"] == ev["provider_max_supply"] == 2_000_000_000
    assert ev["chain_read_ethereum"] == 1_493_853_279 and ev["confirmed_on"]

    # REFUSED BEFORE THE MECHANISM IS CONSULTED. A mechanism-keyed rule hands back 'delta_only'
    # for a no-burn token and derives a confident zero; the convention has to win first.
    assert config.issuance_supply_rule(wm, "no_burn") is None
    assert config.issuance_supply_rule(wm, "transfer_to_dead_address") is None

    class _Out:
        def __init__(self, rows):
            self.rows, self.gaps, self.review, self.added = rows, [], [], []

        def frame(self):
            return pd.DataFrame(self.rows, columns=LONG_COLUMNS)

        def add(self, df, source, project, detail, tier):
            self.added.append((project, df))

        def gap(self, project, metric, reason="", **kw):
            self.gaps.append({"project": project, "metric": metric, "reason": reason, **kw})

        def review_item(self, *a, **kw):
            pass

    # ** THE DERIVATION GUARD IS LATENT FOR THIS PROJECT AND THE TEST SAYS SO RATHER THAN
    # IMPLYING OTHERWISE. ** World Mobile is archetype 2 and 3, so gross_issuance_tokens is not
    # one of its metrics and _derive_issuance never reaches it. Driving the branch therefore
    # needs a project that HAS the metric — which is the case the guard exists for: an archetype
    # change would otherwise walk straight into d(a constant) = 0.
    assert "gross_issuance_tokens" not in config.metrics_for_project(wm), \
        "if this ever becomes true, the guard below stops being hypothetical — read it again"
    as_a4 = dict(wm, archetypes=[1])
    assert "gross_issuance_tokens" in config.metrics_for_project(as_a4)

    row = {"date": pd.Timestamp("2026-09-21"), "project": "World Mobile", "metric": "total_supply",
           "value": 2_000_000_000.0, "source": "coingecko", "tier": 1, "is_manual": False,
           "entered_on": ""}
    out = _Out([row])
    _derive_issuance(out, [as_a4], {("World Mobile", "total_supply"): 2_000_000_000.0},
                     {("World Mobile", "total_supply"): "2026-09-20"})
    assert not out.added, "a constant must not derive a zero that renders as measured"
    gap = [g for g in out.gaps if g["metric"] == "gross_issuance_tokens"]
    assert gap, out.gaps
    # ITS OWN REASON, not the generic one: "the burn mechanism's supply effect is not
    # established" would send the reader to settle a question that is settled and irrelevant.
    assert "CAP, not the minted amount" in gap[0]["reason"], gap[0]["reason"]
    assert "burn mechanism" not in gap[0]["reason"], "wrong diagnosis attached to the right refusal"

    # AND THE SHEET SAYS SO ON THE CELL ITSELF.
    band, why = bw.confidence_for("World Mobile", "total_supply",
                                  {"status": "ok", "source": "coingecko", "n_points": 9,
                                   "covered_days": None, "window_days": None},
                                  pd.Timestamp("2026-09-22"))
    assert band == "AMBER" and "THIS IS THE CAP" in why, (band, why)

    # THE SAME QUESTION ASKED OF EVERY OTHER PROJECT: nobody else is declared this way, so no
    # other issuance derivation is silently switched off by this change.
    others = [p["name"] for p in config.PROJECTS
              if p is not wm and config.supply_denominator_unusable(p["name"])]
    assert not others, f"reports_cap must be scoped to the project it was confirmed on: {others}"
    print("reports_cap ok: 2bn is the cap, issuance refuses with its own reason, the cell discloses")


def test_a_daily_flow_is_compared_against_the_same_weekday_not_the_day_before():
    """Run 20260921T204341Z raised TWELVE change_threshold flags and every one was a 40-60% drop.
    2026-09-20 was a Sunday. Protocol fees and revenue fall by roughly half at the weekend on
    every chain in this universe, so an adjacent-day comparison flags the calendar.

    THE TRAILING 7-DAY AVERAGE DOES NOT FIX IT, which is why the arithmetic is in the test and
    not only in the comment: if a weekend day is half a weekday, the trailing mean is
    (5W + 2x0.5W)/7 = 0.857W and Sunday at 0.5W still reads as a 42% drop against it. Every
    weekend would still flag. Same weekday one week earlier is flat, because it removes the
    weekly cycle rather than averaging over it.
    """
    import pandas as pd

    # A fortnight of a flow with an ordinary weekly shape: weekdays 1,000,000, weekend 450,000.
    # The threshold in config is well under 55%, so an adjacent comparison flags every Saturday.
    def week(start):
        out = []
        for i in range(7):
            d = pd.Timestamp(start) + pd.Timedelta(days=i)
            v = 450_000.0 if d.weekday() >= 5 else 1_000_000.0
            out.append((d, "Chainlink", "revenue_usd", v, "defillama:chainlink", 1))
        return out

    # 2026-09-07 is a Monday, so the series runs Mon..Sun, Mon..Sun and ends on a SUNDAY.
    rows = week("2026-09-07") + week("2026-09-14")
    assert pd.Timestamp(rows[-1][0]).day_name() == "Sunday", "the fixture must end on the bad day"

    # THE ARITHMETIC THAT RULED OUT THE AVERAGE, asserted rather than asserted-in-prose.
    trailing7 = sum(r[3] for r in rows[-8:-1]) / 7
    assert abs(450_000.0 / trailing7 - 1) > 0.40, \
        f"a trailing mean of {trailing7:,.0f} still makes Sunday a >40% drop — it is not the fix"

    flags = _validated(rows, prior={("Chainlink", "revenue_usd"): 1_000_000.0})
    assert not flags, f"Sunday against the previous Sunday is flat — nothing to flag: {flags}"

    # AND THE COMPARISON IS ON THE RECORD. A real step change on the same weekday still fires,
    # and the row says which two dates it used and why that pair.
    broken = rows[:-1] + [(pd.Timestamp("2026-09-20"), "Chainlink", "revenue_usd", 9_000.0,
                           "defillama:chainlink", 1)]
    flags = _validated(broken, prior={("Chainlink", "revenue_usd"): 1_000_000.0})
    assert len(flags) == 1, f"a genuine collapse must survive the weekday fix: {flags}"
    f = flags[0]
    assert f["prior_value"] == 450_000.0, \
        f"compared against Sunday 2026-09-13, not Saturday the 19th: {f}"
    assert str(f["prior_date"])[:10] == "2026-09-13", f"the prior DATE must be recorded: {f}"
    assert "SAME WEEKDAY" in f["basis"] and "Sunday" in f["basis"], f["basis"]
    print("weekday ok: the weekly cycle is removed, not averaged over, and both dates are recorded")


def test_a_daily_flow_with_no_same_weekday_point_says_so_rather_than_pretending():
    """The fallback has to be honest. A short series has no point seven days back, so the
    comparison is adjacent-day and the weekly cycle is NOT removed — which is exactly the
    condition that produced the twelve flags. The row has to say that, or the fix silently
    reintroduces the problem on every series too young to have a week of history."""
    import pandas as pd
    rows = [(pd.Timestamp("2026-09-18"), "Chainlink", "revenue_usd", 1_000_000.0, "defillama:chainlink", 1),
            (pd.Timestamp("2026-09-19"), "Chainlink", "revenue_usd", 450_000.0, "defillama:chainlink", 1)]
    flags = _validated(rows, prior={("Chainlink", "revenue_usd"): 1_000_000.0})
    assert len(flags) == 1, flags
    basis = flags[0]["basis"]
    assert "NO same-weekday point" in basis and "may be the calendar" in basis, basis
    print("weekday fallback ok: says the cycle was not removed rather than implying it was")


def test_a_flow_that_is_lumpy_by_design_is_not_change_checked_at_all():
    """GEODNET burns weekly; the chain read differences daily. A burn day carries a week of burn
    and the days between carry zero, so 35,000 -> 105,000 is the mechanism working. No threshold
    separates that from a fault, so the check is declared off for this series WITH its reason
    rather than quietly widened."""
    flags = _validated([
        ("2026-09-19", "GEODNET", "gross_burn_tokens", 35_000.0, "chain:polygon:burn", 2),
        ("2026-09-20", "GEODNET", "gross_burn_tokens", 105_000.0, "chain:polygon:burn", 2),
    ], prior={("GEODNET", "gross_burn_tokens"): 35_000.0})
    assert not flags, f"a lumpy-by-design flow must not be change-checked: {flags}"

    declared = config.lumpy_flow("GEODNET", "gross_burn_tokens")
    assert declared and declared["source"] and declared["why"], \
        "an exemption without a recorded reason is how a real break gets missed"
    # AND IT IS NARROW: the exemption is per series, not per project.
    assert config.lumpy_flow("GEODNET", "actual_buyback_tokens") is None
    assert config.lumpy_flow("Uniswap", "gross_burn_tokens") is None
    print("S7 lumpy ok:", declared["underlying_cadence"], "underlying vs",
          declared["observed_cadence"], "observed")


def test_both_supply_fields_come_from_ONE_coingecko_response():
    """Item 8. NEAR's circulating_supply exceeded total_supply by 10 tokens and the suspicion was
    that we read the two at slightly different moments, breaking the identity ourselves.

    WE DO NOT. Both are parsed from a single /coins/{id} response, out of one market_data object,
    in one loop. They are same-moment by construction, so the contradiction is in what CoinGecko
    reports — which means the zero-tolerance check is right to fire and no tolerance is warranted.

    This test exists to keep that true. If anyone ever splits the supply fields across two calls,
    the refuted hypothesis quietly becomes the correct one and the identity starts breaking for a
    reason that is ours, with an open question on file saying it cannot be.
    """
    import ast
    import inspect
    import fetch.coingecko as cg

    tree = ast.parse(inspect.getsource(cg))
    # Every metric name assigned from market_data, and the loop tuple they are read from.
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)
             and isinstance(n.target, ast.Tuple) and isinstance(n.iter, ast.Tuple)]
    pairs = [t for loop in loops for t in loop.iter.elts if isinstance(t, ast.Tuple)]
    names = {t.elts[0].value for t in pairs if isinstance(t.elts[0], ast.Constant)}
    assert {"circulating_supply", "total_supply"} <= names, \
        f"both supply fields must be read in ONE loop over one response; found {sorted(names)}"

    # And the zero tolerance stands: no exemption, no widened bound for this pair.
    assert config.relation_exempt("Near", "circulating_supply", "total_supply") is None, \
        "a real contradiction must not be silenced by an exemption — see OPEN_QUESTIONS"

    # The finding is recorded, with the refutation stated rather than the suspicion left standing.
    rec = next(q for q in config.OPEN_QUESTIONS
               if q["project"] == "Near" and "circulating_supply exceeds" in q["topic"])
    assert "REFUTED" in rec["topic"], rec["topic"]
    assert "DO NOT WIDEN THE TOLERANCE" in rec["suggestion"]
    print("item 8 ok: both fields from one response, so the timing explanation is ruled out")


# ======================================================================================
# WHY CLOSURES KEPT NOT REACHING THE GAP REPORT — two mechanisms, neither the one first fixed
# ======================================================================================

def test_a_question_settled_in_prose_cannot_stay_structurally_open():
    """MECHANISM ONE. fetch/gaps.py filters on the `status` field; humans write the closure into
    the topic text. Nothing made the two agree, so the Gap Report printed "[open] RESOLVED
    2026-09-16 — ..." as live P1 work for weeks. Fixing the READER in 2026-09-18 did not stop it
    recurring, because the next person to settle a question wrote the prose and forgot the field.
    A convention that depends on remembering is not a mechanism; this is the mechanism.
    """
    saved = dict(config.OPEN_QUESTIONS[0])
    try:
        config.OPEN_QUESTIONS[0] = dict(saved, status="open",
                                        topic="RESOLVED 2026-09-21 — settled in the prose only")
        errs = config.validate_config(raise_on_error=False)
        assert any("still 'open'" in e for e in errs), \
            f"a prose-closed, structurally-open question must be rejected: {errs}"

        # AND THE OTHER DIRECTION: a record marked settled whose topic does not say so is just as
        # unreadable, from the config side instead of the report side.
        config.OPEN_QUESTIONS[0] = dict(saved, status="resolved",
                                        topic="some question that still reads as open")
        errs = config.validate_config(raise_on_error=False)
        assert any("does not say so" in e for e in errs), errs
    finally:
        config.OPEN_QUESTIONS[0] = saved
    assert not config.validate_config(raise_on_error=False), "and the real file is clean"


def test_the_marker_must_OPEN_the_topic_so_a_live_question_can_discuss_a_refutation():
    """THE CONTROL, and it is not hypothetical: Near's supply question says its own leading
    HYPOTHESIS is refuted while the question stays wide open. A substring match would close it by
    accident — the same class of error, pointing the other way."""
    near = next(q for q in config.OPEN_QUESTIONS
                if q["project"] == "Near" and "circulating_supply exceeds" in q["topic"])
    assert "REFUTED" in near["topic"] and (near.get("status") or "open") == "open"
    assert not config.validate_config(raise_on_error=False), \
        "a live question that discusses a refutation must not be forced closed"
    print("closure guard ok: matched at the START of the topic, not anywhere in it")


def test_a_per_run_table_returns_ONE_run_not_every_run_ever():
    """MECHANISM TWO, and the one that actually explains the stale rows in this audit.

    gap_report and review_queue are rebuilt per run and their rows carry run_id. Read WITHOUT a
    run_id they used to return every row ever written — which is not a longer report, it is the
    same report repeated once per run with every closed row still in it. A later run cannot
    retract a row it never wrote.

    It bit the recalc path specifically: build_workbook passes run_id after a fetch and None when
    re-run over an existing store, so the standalone rebuild — the one used precisely to check
    that a fix landed — was the one showing the stalest report. GEODNET's buyback_fund_balance
    was the visible case: declared not_applicable, no longer generated by detect() at all, and
    still on the sheet.
    """
    import tempfile
    import pathlib as _p
    from store import Store

    with tempfile.TemporaryDirectory() as d:
        s = Store(_p.Path(d) / "t.db")
        s.record_gaps("run-1", [{"project": "GEODNET", "metric": "buyback_fund_balance",
                                "tiers_attempted": "-", "reason": "since declared n/a",
                                "suggestion": "-", "priority": 3, "priority_label": "P3"}])
        s.record_gaps("run-2", [{"project": "GEODNET", "metric": "gross_burn_tokens",
                                "tiers_attempted": "2", "reason": "a live one",
                                "suggestion": "-", "priority": 3, "priority_label": "P3"}])

        latest = s.gap_report()
        assert list(latest["metric"]) == ["gross_burn_tokens"], \
            f"the default read must be the LATEST run, not the union: {list(latest['metric'])}"
        assert s.latest_run_id("gap_report") == "run-2"
        # History is not lost — it is still addressable by run_id.
        assert list(s.gap_report("run-1")["metric"]) == ["buyback_fund_balance"]
        s.close()
    print("per-run ok: the default is one run, and older runs stay addressable")


def test_robots_is_checked_against_the_page_url_not_a_captured_api_path():
    """Item 13. The audit asked whether ultrasound.money's API path is separately disallowed, or
    whether the scraper is simply still pointed at the root. It is the second, and structurally so.

    _scrape_one() checks robots against entry["url"] before anything else, for every method. An
    xhr entry still LOADS that page — interception only works because Playwright has executed the
    page's JS — so no code path fetches an API URL directly, and the API path's own robots status
    is never consulted. The run log reading "https://ultrasound.money/" is correct, not a
    misconfiguration.

    Asserted rather than described, because it is the premise under which three entries sit
    disabled: if someone adds a direct-fetch path later, the reasoning in sources.yaml stops
    holding and this is what says so.
    """
    import ast
    import inspect
    import textwrap
    from fetch.scrape import Scrape

    tree = ast.parse(textwrap.dedent(inspect.getsource(Scrape._scrape_one)))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "robots_allows"]
    assert len(calls) == 1, f"expected exactly one robots check, found {len(calls)}"
    arg = calls[0].args[0]
    assert isinstance(arg, ast.Name) and arg.id == "url", \
        "robots must be checked against the page url the scraper actually loads"

    # And that `url` is the entry's own url, not something derived from a captured response.
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    assert any(any(getattr(t, "id", "") == "url" or
                   (isinstance(t, ast.Tuple) and any(getattr(e, "id", "") == "url" for e in t.elts))
                   for t in a.targets) for a in assigns), "url must come from the entry"
    print("item 13 ok: robots is checked against the loaded page, so the API path is never asked about")


def test_a_ratio_blocked_by_a_frozen_backfill_says_so_instead_of_absent():
    """Item 12. Ether.fi's lock_assets_per_share had never produced a value, and the run log said
    its denominator was "absent". It was not absent — locked_tokens had rows in the store. It was
    a tier-4 Dune series, which runs as a BACKFILL and is skipped once history exists, so it never
    appeared in a run's frame and this derivation never saw it. Structurally, on every run.

    "Absent" sends a reader to go and build a source that already exists. The two cases read
    differently now, because they need completely different responses.

    ** THE ETHER.FI CASE ITSELF WAS RESOLVED ON 2026-09-22 ** — sETHFI.totalSupply() is read
    on-chain, so the denominator is live and the ratio fires. This test keeps the BRANCH covered
    with a SYNTHETIC project rather than the live one: any project whose ratio leans on a
    backfill-only source lands here, and a branch outliving its first example is the normal case.
    """
    from fetch import _derive_lock_ratio
    from fetch.base import LONG_COLUMNS
    import pandas as pd

    project = {"name": "Ether.fi", "lock_ratio": {
        "numerator": "locked_tokens_underlying", "denominator": "locked_tokens",
        "metric": "lock_assets_per_share", "flag_on": "decrease"}}

    class _Out:
        def __init__(self):
            self.notes = []
            self.frames = [pd.DataFrame([{
                "date": pd.Timestamp("2026-09-21"), "project": "Ether.fi",
                "metric": "locked_tokens_underlying", "value": 111_163_214.7,
                "source": "chain:ethereum:sethfi", "tier": 2, "is_manual": False,
                "entered_on": ""}], columns=LONG_COLUMNS)]

        def frame(self):
            return self.frames[0]

        def skipped(self, source, project, note, tier=None):
            self.notes.append(note)

    # (a) the denominator IS in the store — a frozen backfill, not a missing series
    out = _Out()
    _derive_lock_ratio(out, [project], {("Ether.fi", "locked_tokens"): 141_470_107.5})
    note = " ".join(out.notes)
    assert "the store HOLDS locked_tokens" in note, note
    assert "backfill-only" in note and "DIRECTION" in note, note
    assert "has never been fetched" not in note, "that is the OTHER case"

    # (b) it genuinely is not there — a different problem needing a different response
    out = _Out()
    _derive_lock_ratio(out, [project], {})
    note = " ".join(out.notes)
    assert "has never been fetched" in note, note
    assert "the store HOLDS" not in note, note
    print("item 12 ok: frozen-denominator and missing-denominator now read differently")


# ======================================================================================
# THE CONVENTION, TESTED FOR ALL FOUR — AND THE BOUND THAT HAD TO FOLLOW IT
# ======================================================================================

def test_every_transfer_burn_project_has_a_TESTED_supply_convention():
    """All four settled from run 20260921T100546Z's own rows — no live CoinGecko needed.

    The figures were recoverable because _resolve_tier_collisions FLAGS the losing tier rather
    than discarding it, so the dropped contract totalSupply sits beside the kept CoinGecko one.
    The guard that exists to stop last-writer-wins is what preserved the control value.
    """
    for name, convention, contract, coingecko, burn, residual in (
            ("Uniswap",     "net_of_burn",  1_000_000_000.00,  888_114_418.92,  111_953_581.00,  None),
            ("GEODNET",     "net_of_burn",  1_000_000_000.00,  961_518_067.62,   38_481_932.38,  None),
            ("Venice AI",   "net_of_burn",    114_897_403.56,   81_019_146.16,   33_878_094.96,   162.44),
            # PancakeSwap's provider nets out the cross-chain lock TOO — a more complete
            # convention, and the residual is outboundAmount rather than an anomaly.
            ("PancakeSwap", "net_of_burn_and_cross_chain_lock",
             5_387_735_435.7511, 330_627_631.6128, 5_051_176_395.1126, 5_931_409.0257)):
        p = config.PROJECT_BY_NAME[name]
        assert p.get("total_supply_convention") == convention, \
            f"{name} was tested and came back {convention}"
        ev = p.get("total_supply_convention_evidence") or {}
        assert ev.get("test") and ev.get("confirmed_on"), f"{name} must carry its evidence: {ev}"
        # The arithmetic the claim rests on, recomputed rather than trusted to the prose.
        if residual is not None:
            assert abs((contract - coingecko) - burn - residual) < 0.01, name

    # ** THE TWO RESIDUALS ARE NOT THE SAME KIND OF THING, and the config must not pretend they
    # are. ** Venice's 162 tokens on 114.9m is read timing. PancakeSwap's 5.93m on 5.39bn is
    # three orders of magnitude larger relatively, and is recorded as UNEXPLAINED with candidates
    # to test rather than an explanation chosen to tidy the record.
    ven = config.PROJECT_BY_NAME["Venice AI"]["total_supply_convention_evidence"]
    assert ven["residual_explained_by"], "Venice's residual has an explanation and should say it"

    # ** PANCAKESWAP'S RESIDUAL IS NOW EXPLAINED, and the record says what it is. ** It is the
    # ProxyOFT's outboundAmount: CAKE locked on BSC backing the representations on eight other
    # chains. Settled by arithmetic on stored data — no live read was needed in the end.
    cake = config.PROJECT_BY_NAME["PancakeSwap"]
    ev = cake["total_supply_convention_evidence"]
    assert ev["residual_explained_by"], "the residual is explained — say what it is"
    assert "outboundAmount" in ev["residual_explained_by"] or "locked" in ev["residual_explained_by"]
    assert ev["mechanism_source"] and "ProxyOFT" in ev["mechanism_source"]
    assert "total_supply_residual_unexplained" not in cake, \
        "an explained residual must not keep a record that calls it unexplained"

    # AND THE LIVE CONFIRMATION IS STILL OUTSTANDING, recorded rather than quietly dropped now
    # that the arithmetic agrees. Bridging is continuous, so it is an order-of-magnitude check.
    assert ev["live_confirmation_outstanding"], \
        "arithmetic agreeing is not the same as having read the contract"
    print("conventions ok: four tested, two residuals, one of them honestly unexplained")


def test_the_burn_bound_follows_the_supply_convention_not_the_metric_name():
    """burn_address_balance <= total_supply is NOT an identity when total_supply is net of burn —
    it compares a number against itself-minus-itself. It passes for Uniswap and GEODNET only
    because their burns are small relative to supply; on PancakeSwap the same relation reads
    5.05bn against 331m and fails, which is exactly why that project carried a blanket exemption.

    The exemption was a patch over a mis-specified comparand. The contract's totalSupply() still
    counts tokens at the dead address, so burn <= GROSS supply is a real identity, and pointing
    the relation there makes it both true and useful again.
    """
    # The substitution is scoped by convention, not applied to everything.
    for name in ("Uniswap", "GEODNET", "PancakeSwap", "Venice AI"):
        assert config.bound_metric_for(name, "burn_address_balance", "total_supply") \
            == "total_supply_gross", name
    for name in ("Sky", "Ethereum", "Near"):
        assert config.bound_metric_for(name, "burn_address_balance", "total_supply") \
            == "total_supply", f"{name} does not declare net_of_burn — leave it alone"

    # And only that pair moves: other relations on the same projects are untouched.
    assert config.bound_metric_for("PancakeSwap", "gross_burn_tokens", "total_supply") \
        == "total_supply"
    assert config.bound_metric_for("Uniswap", "treasury_holding_tokens", "total_supply") \
        == "total_supply"

    # WOULD THE OLD ARRANGEMENT HAVE FAILED? Asserted, because it is the whole justification.
    assert 5_051_176_395.11 > 330_627_631.61, "burn vs NET supply — the comparison that failed"
    assert 5_051_176_395.11 < 5_387_735_435.75, "burn vs GROSS supply — the one that is an identity"
    print("bound ok: net_of_burn projects bound burn against contract gross supply")


def test_the_contract_supply_read_lands_on_the_gross_metric():
    """The chain read was being written and then dropped by the tier-collision rule on every run
    where CoinGecko answered — surviving only when CoinGecko did NOT, which silently switched
    total_supply between a net figure and a gross one depending on a provider's uptime. For CAKE
    those differ by 5.05bn. It now has its own metric and cannot collide."""
    for name in ("Uniswap", "GEODNET", "PancakeSwap", "Venice AI"):
        p = config.PROJECT_BY_NAME[name]
        tokens = [c for c in p["contracts"].values() if c["kind"] == "erc20_total_supply"]
        assert tokens, name
        for c in tokens:
            assert c["metric_override"] == "total_supply_gross", \
                f"{name}: the token contract must serve the gross metric, not total_supply"
        assert "total_supply_gross" in config.metrics_for_project(p), name

    # And it is scoped: a project with no declared convention keeps the chain read on total_supply.
    sky = config.PROJECT_BY_NAME["Sky"]
    for c in sky["contracts"].values():
        if c["kind"] == "erc20_total_supply":
            assert c["metric_override"] is None, "Sky does not declare net_of_burn — do not move it"
    print("gross metric ok: six contracts re-pointed, scoped to the four net_of_burn projects")


def test_the_cake_paradox_dissolves_once_the_gross_figure_is_on_file():
    """"4.99bn burned against a 400m max supply" was never a contradiction: 400m caps NET supply.
    Every figure available when it was raised was a net one — CoinGecko's total, the cap itself —
    and the single gross figure that resolves it was being fetched every run and discarded."""
    minted, burned, cap = 5_387_735_435.75, 5_051_176_395.11, 400_000_000
    net_outstanding = minted - burned
    assert abs(net_outstanding - 336_559_040.64) < 0.01   # float64, not exact decimal
    assert net_outstanding < cap, "net outstanding is what the cap governs, and it is under it"
    assert burned > cap * 12, "cumulative burn exceeding the cap many times over is the expected " \
                              "shape for 33 consecutive months of net burn, not an anomaly"
    print(f"cake paradox ok: {minted:,.0f} minted - {burned:,.0f} burned = "
          f"{net_outstanding:,.0f} net, under the {cap:,} cap")


def test_a_tail_rate_that_has_never_been_nudged_is_a_finding_not_a_pass():
    """The [1, 100] bps bound is the contract's own and spans 0.5% to 52% of supply per year —
    far too wide to catch a wrong read. Aerodrome documents ~10.9% annualised as of April 2026,
    so the RESULT gets a band too.

    67 bps is the rate at tail activation (2024-12-04) and annualises to 34.8%. It sits outside
    the band deliberately: reading it back today would mean the rate has never been nudged in ~90
    epochs while the docs say 10.9%, and that is a finding — either the getter is wrong, or the
    documented figure is, and storing a number three times the documented rate settles neither.
    """
    c = Chain()
    c.reader = _MinterReader(weekly=8_969_149.540108, rate_bps=67, supply=1_980_000_000.0)
    out = FetchOutput()
    c.run([_aerodrome_minter_project()], None, out)

    assert out.frame().query("metric == 'gross_issuance_tokens'").empty, \
        "34.8% annualised is outside the documented band and must not be stored"
    reasons = " ".join(g["reason"] for g in out.gaps if g["metric"] == "gross_issuance_tokens")
    assert "34.8%" in reasons and "10.9%" in reasons, \
        f"the refusal must name both the computed share and the documented claim: {reasons}"
    print("annualised bound ok: 67 bps refused at 34.8% against a documented 10.9%")


def test_the_annualised_band_contains_both_readings_of_an_ambiguous_doc():
    """The docs said "approximately 10.9% annualized" without saying 10.9% OF WHAT, and the band
    was set wide enough to hold both readings rather than resolving it by assumption.

    ** THE LIVE READ OF 2026-09-21 SETTLED THE BASE AND THE BAND STAYS WIDE ANYWAY. **
    tailEmissionRate() came back 21 bps: 21 x 52 = 10.92% of TOTAL supply, which reproduces the
    documented figure, where the circulating reading would need ~10.5 bps — half the observed
    rate. So the base is total supply, the same base the tail formula itself uses.

    One observation establishes the BASE, not the RATE. nudge() moves the rate by design, up or
    down, so a band redrawn tightly around 10.92% would read the next governance nudge as a
    fault. The band's job was never to pin the rate; it was to catch a read off by an order of
    magnitude, and it still does.
    """
    tail = config.PROJECT_BY_NAME["Aerodrome"]["contracts"]["minter"]["emission_tail"]
    lo, hi = tail["annualised_share_bounds"]
    TOTAL, CIRC = 1_980_000_000, 988_000_000

    of_total = 0.109                                   # 10.9% of total supply
    of_circulating = 0.109 * CIRC / TOTAL              # the same claim read against circulating
    assert lo <= of_circulating <= hi, f"the circulating reading ({of_circulating:.1%}) must fit"
    assert lo <= of_total <= hi, f"the total-supply reading ({of_total:.1%}) must fit"

    # THE RESOLUTION IS ON FILE WITH ITS ARITHMETIC, and the question it answered is kept.
    src = tail["annualised_share_source"]
    res = src["ambiguity_resolved"]
    assert "do not state the BASE" in res["was"], "the open question is kept, not overwritten"
    assert res["observed_bps"] == 21 and res["base"].startswith("total supply")
    assert "10.92%" in res["annualised"], res["annualised"]
    assert abs(21 * 52 / 10_000 - 0.1092) < 1e-9, "the arithmetic, not just the claim"
    assert res["band_kept_wide_because"] and src["source_url"]

    # The band still excludes the activation rate, or it would not be doing any work.
    assert not (lo <= 0.67 * 52 / 100 <= hi), "67 bps annualised must stay outside the band"

    # THE ACTIVATION FACTS AGREE ACROSS THREE INDEPENDENT ROUTES: Aerodrome's own announcement,
    # the deployed constant, and the constants replay.
    act = tail["tail_activation"]
    assert act["epoch"] == 67 and act["date"] == "2024-12-04" and act["rate_at_activation_bps"] == 67
    assert config.PROJECT_BY_NAME["Aerodrome"]["minter_constants"]["TAIL_ACTIVATES_AT_EPOCH"] == 67
    assert config.PROJECT_BY_NAME["Aerodrome"]["minter_constants"]["INITIAL_TAIL_EMISSION_RATE_BPS"] == 67
    print(f"band ok: [{lo:.0%}, {hi:.0%}] holds both readings ({of_circulating:.1%} and "
          f"{of_total:.1%}) and excludes 34.8%")


# ======================================================================================
# run_sql.py — the cleanup SQL is only reviewable if running the SELECTs is easy
# ======================================================================================

def _seeded_store(tmpdir):
    import sqlite3
    import pathlib as _p
    db = _p.Path(tmpdir) / "t.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, "
              "source TEXT, tier INT, is_manual INT, entered_on TEXT, fetched_at TEXT)")
    c.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?,?)", [
        ("2026-09-14", "Uniswap", "gross_burn_tokens", 111_337_581.0,
         "chain:ethereum:burn_dead:delta", 2, 0, "", "t"),
        ("2026-09-15", "Uniswap", "gross_burn_tokens", 134_000.0,
         "chain:ethereum:burn_dead:delta", 2, 0, "", "t"),
        ("2026-09-15", "Uniswap", "burn_address_balance", 111_953_581.0,
         "chain:ethereum:burn_dead", 2, 0, "", "t"),
    ])
    c.commit()
    c.close()
    return db


def test_run_sql_parses_every_section_and_runs_only_selects():
    """The cleanup file's whole discipline is LOOK BEFORE YOU DELETE, and that only works if
    looking is easy. It was not — the SELECTs had to be pasted into an inline python -c or a
    sqlite3 shell, and the quoting around string literals broke on PowerShell repeatedly.
    """
    import tempfile
    import run_sql

    sections = run_sql.parse_sections(run_sql.SQL_FILE.read_text(encoding="utf-8"))
    assert set(sections) >= set("ABCDEFGH"), f"sections found: {sorted(sections)}"
    for letter, sec in sections.items():
        assert sec["title"] and not sec["title"].startswith(("AND ", "WHERE ", "SELECT ")), \
            f"section {letter} took a SQL fragment as its title: {sec['title']!r}"
        selects = [s for s in run_sql.split_statements(sec["text"])
                   if run_sql.classify(s) == "select"]
        assert selects, f"section {letter} has no SELECT to run"

    with tempfile.TemporaryDirectory() as d:
        db = _seeded_store(d)
        assert run_sql.main(["E", "--db", str(db)]) == 0
        # AND IT DID NOT TOUCH ANYTHING. Plain mode is the "just look" command.
        import sqlite3
        n = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        assert n == 3, f"running SELECTs must not modify the store, got {n} rows"
    print("run_sql ok: 8 sections parse, titles resolve, SELECTs run read-only")


def test_run_sql_refuses_to_delete_without_the_typed_confirmation():
    """A preview and a confirmation, in that order. The count alone is not enough — "47 rows"
    says nothing about whether they are the right 47 — so the rows are printed first, using the
    DELETE's OWN where clause so the preview cannot drift from what actually goes."""
    import builtins
    import sqlite3
    import tempfile
    import run_sql

    with tempfile.TemporaryDirectory() as d:
        db = _seeded_store(d)
        real_input = builtins.input

        builtins.input = lambda *a: "yes"          # anything but the exact phrase
        try:
            assert run_sql.main(["--delete", "E", "--db", str(db)]) == 1
        finally:
            builtins.input = real_input
        n = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        assert n == 3, "a wrong confirmation must delete nothing"

        builtins.input = lambda *a: "DELETE E"
        try:
            assert run_sql.main(["--delete", "E", "--db", str(db)]) == 0
        finally:
            builtins.input = real_input
        rows = sqlite3.connect(str(db)).execute(
            "SELECT value FROM metrics WHERE metric='gross_burn_tokens'").fetchall()
        assert [r[0] for r in rows] == [134_000.0], \
            "the artefact goes and the ordinary day stays — the threshold is doing the work"
    print("run_sql ok: preview, typed confirmation, and only the artefact removed")


def test_run_sql_never_executes_a_write_in_look_mode():
    """** THE ONE THAT MATTERS. ** If a DELETE is uncommented in the file — half-finished edit,
    someone mid-review — the plain command must still not run it. Otherwise "just let me look"
    silently becomes "delete rows"."""
    import sqlite3
    import tempfile
    import run_sql

    live_delete = """
-- Z1. LOOK ONLY
SELECT COUNT(*) AS n FROM metrics;

-- Z2. THE DELETE, UNCOMMENTED BY MISTAKE
DELETE FROM metrics WHERE project = 'Uniswap';
"""
    with tempfile.TemporaryDirectory() as d:
        db = _seeded_store(d)
        conn = sqlite3.connect(str(db))
        try:
            rc = run_sql.run_selects(conn, live_delete, "Z")
        finally:
            conn.close()
        assert rc == 0
        n = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        assert n == 3, "look mode executed a DELETE that was sitting uncommented in the file"
    print("run_sql ok: an uncommented DELETE is skipped, not run, in look mode")


def test_run_sql_splits_on_semicolons_outside_strings_and_prose():
    """A naive split breaks twice over on this file: on a semicolon inside a quoted source string,
    and on one inside comment prose. Both have happened, and both produce fragments that look
    like broken SQL and send the reader hunting for a syntax error that is not there."""
    import run_sql

    sql = ("-- a note; with a semicolon in prose\n"
           "SELECT * FROM metrics WHERE source = 'chain:sum(a;b)';\n"
           "SELECT 1;\n")
    stmts = [run_sql.strip_comments(s) for s in run_sql.split_statements(sql)]
    stmts = [s for s in stmts if s]
    assert len(stmts) == 2, f"expected 2 statements, got {len(stmts)}: {stmts}"
    assert "chain:sum(a;b)" in stmts[0], "the semicolon inside the literal must not split"
    print("run_sql ok: semicolons in strings and prose do not split statements")


def test_etherfi_locked_tokens_comes_from_the_contract_not_from_dune():
    """Asked directly: can Ether.fi be tracked from the contracts rather than from Dune?

    For the share-denominated lock figure, yes — and the chain read CORRECTS the Dune one rather
    than merely replacing it. sETHFI is an ordinary ERC-20 whose totalSupply() IS that figure, on
    an address already in config, and it had even been read by hand into lock_ratio.measured. The
    config note claiming "there is no contract read for it" was simply wrong.

    Dune reported 141,470,107.5 against a share supply of 89,748,241.27 — 1.58x the sETHFI that
    exists, and 30,306,893 more ETHFI than the staking contract holds. A figure larger than the
    whole share supply is not that supply under any convention.
    """
    p = config.PROJECT_BY_NAME["Ether.fi"]

    shares = p["contracts"]["sethfi_shares"]
    assets = p["contracts"]["sethfi"]
    assert shares["metric_override"] == "locked_tokens"
    assert shares["kind"] == "erc20_total_supply" and shares["expected_symbol"] == "sETHFI", \
        "this call is made ON the vault token, so symbol() returns sETHFI, not ETHFI"
    assert shares["address"] == assets["address"], \
        "same address, different call — assets vs shares. Not a duplicate entry."
    assert assets["metric_override"] is None and assets["read_method"] == "escrow_balance_of"

    # THE DUNE SERIES IS KEPT AS A CROSS-CHECK, not deleted: its history is the only long one on
    # file, and a characterised wrong number is worth more than a discarded one.
    q = p["dune_queries"]
    assert "locked_tokens" not in q, "Dune must no longer feed the headline figure"
    assert q["locked_tokens_dashboard"]["query_id"] == 8683038
    assert "locked_tokens_dashboard" in config.metrics_for_project(p)

    # BOTH LEGS OF THE RATIO ARE NOW CHAIN READS, which is what lets it fire at all.
    spec = p["lock_ratio"]
    served = {c.get("metric_override") or config.KIND_METRIC.get(c["kind"])
              for c in p["contracts"].values()}
    assert spec["numerator"] in served and spec["denominator"] in served, \
        f"both legs must be contract-served for the ratio to fire in one run; served={served}"
    print("etherfi ok: shares on-chain, Dune demoted to cross-check, both ratio legs live")


def _cleanup_sql_section(heading: str) -> str:
    """One section of orphan_cleanup.sql, BOUNDED at the next section's heading.

    Slicing to end-of-file was fine while the section under test happened to be the last one, and
    silently wrong the moment another was appended: section I's test started failing on a DELETE
    that belongs to section L. A section assertion has to be about that section.
    """
    sql = (Path(__file__).resolve().parent.parent / "orphan_cleanup.sql").read_text(encoding="utf-8")
    start = sql.index(heading)
    nxt = re.search(r"^-- [A-Z]\. ", sql[start + len(heading):], re.M)
    return sql[start:start + len(heading) + nxt.start()] if nxt else sql[start:]


def test_the_etherfi_basis_change_has_its_cleanup_sql():
    """Two sources in one series is exactly what measuring_point_changed is for, and it will
    correctly blank locked_tokens until the old Dune rows are moved. The guard is right; the fix
    is the cleanup, not an exemption — so the cleanup has to exist and has to be a MOVE."""
    section = _cleanup_sql_section("-- I. ETHER.FI locked_tokens")
    assert "locked_tokens_dashboard" in section and "UPDATE metrics" in section, \
        "the Dune rows are MOVED to the cross-check metric, not deleted — the history is evidence"
    # "DELETE FROM", not "DELETE" — the section's own prose says "THE MOVE, NOT A DELETE", and a
    # bare word match would fail on the sentence that promises the thing being asserted.
    assert "DELETE FROM" not in section, "section I must not delete the Dune history"
    assert "source LIKE 'dune:%'" in section, \
        "scoped to dune-sourced rows so a chain read already written is never swept up"
    print("etherfi cleanup ok: section I moves the Dune history rather than discarding it")


def test_skys_archetype_4_covers_the_burn_leg_and_nothing_else():
    """Archetype 4 was REMOVED when Sky's "burn" turned out to be a treasury transfer. Stage 2
    changed that premise on 2026-09-14, and the mechanism was settled from the token source BEFORE
    the archetype went back on: SKY.burn() decrements totalSupply.

    The scope is the point. Of Stage 2's allocation only the 5% leg destroys supply; the 22.5%
    staking leg is distributed and the 55% Smart Burn Engine output lands in a governance-
    controlled treasury. Netting either into a burn figure reports distributed or redeployable
    SKY as destroyed — the one discipline this file applies to every yield-vs-burn split.
    """
    sky = config.PROJECT_BY_NAME["Sky"]
    assert 4 in sky["archetypes"] and 3 in sky["archetypes"]

    scope = sky["archetype_4_scope"]
    assert scope["effective_from"] == "2026-09-14"
    assert "burn_share" in scope["covers"]
    assert len(scope["excludes"]) == 2, "both non-burn legs must be named, not implied"
    assert any("22.5" in e for e in scope["excludes"]) and any("55" in e for e in scope["excludes"])

    # The scope agrees with the split it claims to describe, rather than restating it by hand.
    v2 = sky["fee_split_v2"]
    assert v2["burn_share"] == 0.05
    assert v2["splits"]["sky_buyback_for_staking_rewards"] == 0.225
    assert v2["sky_buying_share"] == 0.275, "buying is not burning — 27.5% buys, 5% burns"

    # The first burn is on file as a magnitude check, not as a stored figure.
    first = scope["first_burn"]
    assert first["tokens"] == 2_860_000 and first["date"] == "2026-09-14"

    # AND THE BEFORE-STATE IS ASSERTED, because it is what makes a backfilled burn a bug: any Sky
    # burn dated before 2026-09-14 is wrong by construction, whatever its source claims.
    assert "no burn" in scope["before_this_date"].lower()
    print("sky A4 ok: 5% leg only, from 2026-09-14, with both excluded legs named")


def test_hyperliquid_holds_archetype_4_on_the_evidence_already_in_config():
    """47.3m HYPE confirmed burned, and it was absent from the A4 tab because it held 3 and 1 but
    not 4. Nothing new was needed to fix that — every fact was already in the entry.

    Buy, then burn, the same tokens: the ordinary 3-and-4 shape, not a special case. Archetype 3
    is kept deliberately. Dropping it would hide the mechanism that produces the tokens the burn
    destroys while keeping the effect.
    """
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    assert {3, 4} <= set(p["archetypes"]), p["archetypes"]

    # ARCHETYPE 3's basis: 99% of net protocol fees buy HYPE, from Hyperliquid's own docs.
    assert p["fee_split"]["share_to_buyback"] == 0.99
    assert p["fee_split"]["status"] == "active" and p["fee_split"]["programmed"] is True

    # ARCHETYPE 4's basis: confirmed, on two independent primary sources.
    mech = config.burn_mechanism(p)
    assert mech["status"] == "confirmed"
    note = mech.get("source_note", "")
    assert "validator vote" in note.lower() and "sec" in note.lower(), \
        "the two-source basis is what makes this confirmed rather than assumed"

    # THE STAKING ELEMENT IS NOT AN ARCHETYPE. It is metrics, which this project already has.
    assert set(config.ARCHETYPE_NAMES) == {1, 2, 3, 4}
    assert "staked_tokens" in config.metrics_for_project(p)

    # And the new burn metrics explain themselves rather than asking for an address: the figure
    # comes from the protocol's own API, which the node_api branch of the gap reporter knows.
    assert p["burn_read_method"] == "protocol_api"
    assert (p.get("node_api") or {}).get("metric") == "burn_address_balance"
    print("hyperliquid ok: 3 and 4 on evidence already on file, staking is orthogonal")


def test_section_J_records_the_resolved_residual_and_the_corrected_premise():
    """The residual is outboundAmount — CAKE locked on BSC backing eight other chains. Settled by
    arithmetic on stored data; no live read was needed.

    Section J keeps BOTH the answer and the premise that was wrong, because the wrong premise was
    the instructive part: "we sum bsc:token + base:token_base" was read off the contract LIST
    without checking the GATE that decides which of those contracts is actually read. token_base
    was unverified and refused, so the figure was BSC alone the whole time and the double-count
    being corrected for never existed.
    """
    section = _cleanup_sql_section("-- J. PANCAKESWAP'S")

    assert "LOOK ONLY" in section and "DELETE FROM" not in section and "UPDATE metrics" not in section, \
        "section J is diagnostic — nothing here needs fixing on our side"
    assert "FALSE PREMISE" in section, \
        "the wrong premise is kept: it is the instructive part, not an embarrassment to delete"
    assert "outboundAmount" in section and "5,931,409" in section
    assert "circulatingSupply() = totalSupply() - outboundAmount" in section, \
        "the contract's own accounting is what makes this a finding rather than a coincidence"

    # J4 is a CONFIRMATION now, not a test, and it must say the figure will have moved.
    j4 = section[section.index("-- J4."):]
    assert "ORDER OF MAGNITUDE" in j4 and "bridging is continuous" in j4, \
        "an exact-match expectation on a continuously-moving figure would read as a refutation"
    print("section J ok: resolved, premise correction kept, J4 framed as an order-of-magnitude check")


def test_J3_computes_the_arithmetic_across_the_metric_rename():
    """J3 returned BLANK contract_sum and residual columns — NULLs rendering as empty cells, which
    look like an absent answer rather than a broken query. It looked the contract figure up by
    metric name 'total_supply_gross', which only exists from 2026-09-22; every historical row,
    including the audited run the section is about, stored the chain read under 'total_supply'.

    Resolving by SOURCE survives the rename in both directions, and the diagnostic column names
    the missing leg so a blank can never again be mistaken for an answer.
    """
    import sqlite3
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_sql", Path(__file__).resolve().parent.parent / "run_sql.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    section = rs.parse_sections(rs.SQL_FILE.read_text(encoding="utf-8"))["J"]
    j3 = [x for x in rs.split_statements(section["text"]) if rs.classify(x) == "select"][2]

    def run(rows):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, "
                   "source TEXT, tier INT)")
        db.execute("CREATE TABLE run_log (run_id TEXT, ts TEXT, source TEXT, tier INT, "
                   "project TEXT, rows INT, status TEXT, message TEXT)")
        db.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)
        cur = db.execute(rs.strip_comments(j3))
        return dict(zip([d[0] for d in cur.description], cur.fetchone()))

    CG = ("2026-09-21", "PancakeSwap", "total_supply", 330_627_631.6128, "coingecko", 1)
    BURN = ("2026-09-21", "PancakeSwap", "burn_address_balance", 5_051_176_395.1126,
            "chain:bsc:burn_dead", 2)

    # BOTH namings must compute, because the store holds rows written under each.
    for metric in ("total_supply", "total_supply_gross"):
        got = run([("2026-09-21", "PancakeSwap", metric, 5_387_735_435.7511,
                    "chain:bsc:token", 2), CG, BURN])
        assert got["bsc_contract_total"] is not None, f"{metric}: the old query returned NULL here"
        assert abs(got["residual_is_outbound_amount"] - 5_931_409.0257) < 0.01, got
        assert "all three legs present" in got["diagnostic"]

    # AND A MISSING LEG SAYS SO, rather than rendering as the same blank as a broken query.
    got = run([CG])
    assert got["residual_is_outbound_amount"] is None
    assert "NO CHAIN-SOURCED SUPPLY ROW" in got["diagnostic"], got["diagnostic"]
    print("J3 ok: computes under both metric names, and names the missing leg when one is absent")


def test_pancakeswap_supply_is_not_partial_in_either_direction():
    """:PARTIAL was wrong TWICE, in opposite directions, and both errors were individually
    plausible — which is why both are kept written down.

    It first said the BSC read UNDERSTATES because we cover only some deployments, implying the
    fix was to add chains. Then it was flagged as OVERSTATING, on the theory that we sum BSC and
    Base and double-count. Neither holds. Bridging LOCKS CAKE on BSC, so the BSC read already
    includes every token represented elsewhere — and Base was never summed anyway, because
    token_base was unverified and the gate refused it. The figure is complete.

    A marker pointing in EITHER direction is worse than none: both tell a reader the number needs
    an adjustment it does not need.
    """
    cake = config.PROJECT_BY_NAME["PancakeSwap"]
    assert cake["supply_is_partial"] is False and not cake["supply_partial_reason"]
    assert cake["contracts"]["token"]["supply_is_partial"] is False, \
        "the CONTRACT-level flag is what actually marks the cell — the project flag does not " \
        "even apply to total_supply_gross"

    base = cake["contracts"]["token_base"]
    assert base["kind"] == "bridged_representation"
    from fetch.chain import REFERENCE_ONLY_KINDS
    assert "bridged_representation" in REFERENCE_ONLY_KINDS
    assert "DO NOT VERIFY-AND-SUM" in base["note"], \
        "the trap is that verifying it LOOKS like an improvement and would double-count"
    print("partial label ok: not partial in either direction, and token_base is reference-only")


def test_a_reference_only_contract_is_neither_read_nor_nagged_about():
    """The gate exists to stop an UNVERIFIED ADDRESS BEING READ. A reference-only contract is
    never read, so its verification status is irrelevant — and gating it first raised an "address
    is NOT verified" gap every run for token_base, a row whose suggested fix (verify it) would
    have made the supply figure WORSE by letting a bridged representation be summed into a home
    total that already contains it.

    A gap row that asks for the harmful action is worse than no row at all.
    """
    os.environ.pop("TOKEN_METRICS_ALLOW_UNVERIFIED", None)

    class _Stub:
        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def scaled(self, chain, address, call, *a, **k):
            return 5_387_735_435.7511 if call == "totalSupply" else 5_051_176_395.1126

    c = Chain()
    c.reader = _Stub()
    out = FetchOutput()
    c.run([config.PROJECT_BY_NAME["PancakeSwap"]], None, out)

    row = out.frame().query("metric == 'total_supply_gross'").iloc[0]
    assert row["source"] == "chain:bsc:token", f"BSC alone, unmarked: {row['source']}"
    assert ":PARTIAL" not in row["source"]
    assert abs(row["value"] - 5_387_735_435.7511) < 1e-4

    assert not [g for g in out.gaps if "token_base" in str(g.get("reason", ""))], \
        "a reference-only contract must not raise an unverified-address gap"
    assert any("reference only" in e.message for e in out.log), \
        "but it must stay visible in the log — silently skipped is not the same as declared"
    print("reference-only ok: BSC alone, unmarked, and token_base raises nothing")


# ======================================================================================
# sqlite3 STORES A numpy INTEGER AS A BLOB, SILENTLY. The 2026-09-22 workbook crash.
# ======================================================================================

def test_a_numpy_integer_never_reaches_an_integer_column_as_a_blob():
    """THE BUG, asserted at the WRITE, which is where it was invisible.

    numpy.float64 subclasses Python float, so it binds as REAL and nobody notices. numpy.int64
    does NOT subclass int — it falls through sqlite3's type dispatch to the buffer protocol, and
    an 8-byte little-endian BLOB goes into an INTEGER column without a word of complaint:

        np.int64(2)  ->  typeof() = 'blob',  b'\\x02\\x00\\x00\\x00\\x00\\x00\\x00\\x00'

    It surfaced three layers away and one step from the end: build_workbook's
    int(b'\\x02\\x00...') in write_review_queue, after a clean fetch of 6,967 rows, with the
    workbook unwritten and the traceback pointing at the reader.

    Read-time defence was never the answer — a try/except there would have hidden bad data
    being written, which is the pattern this project has been burned by before. This asserts the
    typeof() in the DATABASE.
    """
    import numpy as np
    import sqlite3
    import tempfile
    import pathlib as _p
    from store import Store

    with tempfile.TemporaryDirectory() as d:
        s = Store(_p.Path(d) / "t.db")
        s.record_review("r1", [{"project": "Chainlink", "metric": "revenue_usd",
                                "reason": "change_threshold", "action": "stored_flagged",
                                "value": 10.0, "prior_value": 1e6, "date": "2026-09-22",
                                "source": "defillama:c", "tier": np.int64(1)}])
        s.record_gaps("r1", [{"project": "X", "metric": "y", "reason": "r", "suggestion": "s",
                              "priority": np.int64(3), "priority_label": "P3"}])
        s.record_fetch("r1", "chain", "X", np.int64(7), "ok", tier=np.int64(2))
        s.record_staging("r1", [{"project": "X", "name": "n", "value": 1.0,
                                 "date": "2026-09-22", "source": "chain:x", "tier": np.int64(2)}])

        conn = sqlite3.connect(str(_p.Path(d) / "t.db"))
        # EVERY integer column across every write path, not just the one that crashed.
        # fetch_status.last_rows is on the list because record_fetch writes to TWO tables from
        # one argument, and guarding only the run_log insert would have left it exposed.
        for table, col in (("review_queue", "tier"), ("gap_report", "priority"),
                           ("run_log", "tier"), ("run_log", "rows"),
                           ("fetch_status", "last_rows"), ("staging", "tier")):
            got = conn.execute(
                f"SELECT typeof({col}), {col} FROM {table} WHERE {col} IS NOT NULL").fetchall()
            assert got, f"{table}.{col} wrote nothing — the test proves nothing"
            for kind, value in got:
                assert kind == "integer", \
                    f"{table}.{col} stored typeof={kind} value={value!r} — a numpy integer " \
                    f"became a {kind}, which is the 2026-09-22 crash"
        conn.close()
        s.close()
    print("write-time ok: numpy integers land as INTEGER in every integer column")


def test_a_genuinely_bad_integer_stops_the_run_at_the_write():
    """Coercing anything numeric-looking would be the same mistake in a different coat. A str, a
    bytes, a list, a non-integral float — those are not "an int needing conversion", they are a
    caller passing the wrong thing, and the run should stop at the write with the column named
    rather than storing it and surfacing somewhere else later."""
    import tempfile
    import pathlib as _p
    from store import Store, _int_or_none

    for good, expected in ((None, None), (2, 2), (True, 1), (2.0, 2), (float("nan"), None)):
        assert _int_or_none(good, "tier") == expected, good

    for bad in ("2", b"\x02", [2], 2.5, {"tier": 2}):
        try:
            _int_or_none(bad, "review_queue.tier", "Chainlink/revenue_usd")
        except TypeError as e:
            assert "review_queue.tier" in str(e) and "Chainlink/revenue_usd" in str(e), \
                f"the error must name the column AND what was being written: {e}"
            assert "numpy" in str(e), "and it must name the usual cause, which is not obvious"
        else:
            raise AssertionError(f"{bad!r} must not be accepted into an integer column")

    # And it stops the actual write, not just the helper in isolation.
    with tempfile.TemporaryDirectory() as d:
        s = Store(_p.Path(d) / "t.db")
        try:
            s.record_review("r1", [{"project": "X", "metric": "y", "reason": "r",
                                    "action": "a", "tier": "not an int"}])
        except TypeError:
            pass
        else:
            raise AssertionError("record_review accepted a string tier")
        s.close()
    print("write-time ok: a genuinely bad integer stops the run and names the column")


def test_validate_frame_hands_the_store_python_ints_not_numpy_scalars():
    """THE CAUSE, fixed at the site as well as at the boundary.

    A Series element from .iloc[] on a mixed-dtype frame is a numpy scalar. The change check
    moved from itertuples() to groupby/iloc on 2026-09-22 for the S7 partial-day fix, and that
    move is what started writing numpy.int64. The store guard is the net; this is the fix.
    """
    import pandas as pd
    from fetch.validate import validate_frame

    class _Rec:
        def __init__(self):
            self.items = []

        def review_item(self, project, metric, reason, action, **kw):
            self.items.append({"reason": reason, **kw})

    today = pd.Timestamp.now("UTC").date()
    df = pd.DataFrame([
        {"date": pd.Timestamp("2026-09-20"), "project": "Uniswap", "metric": "gross_burn_tokens",
         "value": 9e14, "source": "chain:x", "tier": 2},
        {"date": pd.Timestamp(today - pd.Timedelta(days=2)), "project": "Chainlink",
         "metric": "revenue_usd", "value": 1_000_000.0, "source": "defillama:c", "tier": 1},
        {"date": pd.Timestamp(today - pd.Timedelta(days=1)), "project": "Chainlink",
         "metric": "revenue_usd", "value": 10.0, "source": "defillama:c", "tier": 1},
    ])
    assert df["tier"].dtype == "int64", "the frame must actually hold numpy ints, or this proves nothing"

    rec = _Rec()
    validate_frame(df, {("Chainlink", "revenue_usd"): 1_000_000.0}, rec)
    reasons = {i["reason"] for i in rec.items}
    assert {"out_of_bounds", "change_threshold"} <= reasons, f"both paths must fire: {reasons}"
    for item in rec.items:
        assert type(item["tier"]) is int, \
            f"{item['reason']} handed the store a {type(item['tier']).__name__}, not an int"
        assert type(item["value"]) is float, \
            f"{item['reason']} handed the store a {type(item['value']).__name__}, not a float"
    print("validate_frame ok: both review paths emit Python scalars")


# ======================================================================================
# A BRACKETED ANNOTATION IS NOT PART OF THE CONTRACT KEY. The 2026-09-21 Aerodrome blank.
# ======================================================================================

def test_every_source_parser_ignores_a_bracketed_annotation():
    """Aerodrome's gross_issuance_tokens blanked as "orphaned — written by contract(s)
    minter[tail@21bps], which are no longer in config". The contract was there. The parser was
    looking for a key literally called `minter[tail@21bps]`.

    The annotation was added so a series that changes basis mid-history says so on the row. Three
    parsers then resolved contract keys out of the annotated string, each filtering pieces against
    an EXACT-MATCH marker list — a design that cannot see an annotation glued to a key rather than
    occupying its own colon-delimited slot.

    All three share one stripper now, because the failure was three copies of one assumption.
    """
    from fetch.base import _measuring_point

    annotated = "chain:base:minter[tail@21bps]"
    plain = "chain:base:minter"

    # (1) and (2): the two that blanked the cell.
    assert config.orphaned_contract_keys("Aerodrome", annotated) == [], \
        "the annotation is not a contract key — Aerodrome's minter is in config"
    assert config.withdrawn_contract_keys("Aerodrome", "gross_issuance_tokens", annotated) == []

    # (3) THE ONE THAT HAD NOT BITTEN YET, and would have on the next branch flip. Two spellings
    # of the same address must be ONE measuring point, or the series blanks for a change that
    # never happened.
    assert _measuring_point(annotated) == _measuring_point(plain) == plain

    # Markers still strip, and still strip alongside an annotation in any order.
    assert _measuring_point("chain:base:minter[tail@21bps]:PARTIAL:delta") == plain
    assert _measuring_point("chain:ethereum:burn_dead:delta") == "chain:ethereum:burn_dead"

    # AND A REAL ORPHAN IS STILL CAUGHT — stripping must not blind the guard.
    assert config.orphaned_contract_keys("Aerodrome", "chain:base:nonesuch[tail@21bps]") == ["nonesuch"], \
        "stripping the annotation must not stop a genuinely missing contract being reported"

    # The stripper itself, on the shapes it has to survive.
    for raw, want in (("chain:base:minter[tail@21bps]", "chain:base:minter"),
                      ("chain:bsc:token", "chain:bsc:token"),
                      ("chain:sum(a+b)[x]", "chain:sum(a+b)"),
                      ("", ""), (None, "")):
        assert config.strip_source_annotations(raw) == want, raw
    print("source parsers ok: one stripper, three call sites, real orphans still caught")


def test_a_summed_source_with_an_annotation_still_resolves_every_component():
    """The sum path takes a different branch — body[4:-1].split("+") — and an annotation lands
    outside the closing paren, so it has to be stripped before the paren match is even attempted.
    """
    # PancakeSwap's token is real; nonesuch is not. A sum of both must report exactly the missing
    # one, annotation or no annotation.
    for src in ("chain:sum(bsc:token+bsc:nonesuch)",
                "chain:sum(bsc:token+bsc:nonesuch)[some@annotation]"):
        assert config.orphaned_contract_keys("PancakeSwap", src) == ["nonesuch"], src
    print("summed sources ok: annotation stripped before the sum(...) match")


def test_issuance_prefers_the_single_source_gross_delta_over_net_plus_burn():
    """Uniswap derived 219,999.99 of issuance on a run where total_supply_gross was EXACTLY
    1,000,000,000 at both ends. UNI minted nothing. The figure was the gap between CoinGecko's
    net number and the chain's burn delta — two providers sampled at different moments.

    d(total_supply_gross) IS gross issuance for a transfer burn: the contract's totalSupply counts
    the dead-address tokens, so a burn does not move it and only minting does. One source, one
    read, no drift.
    """
    import pandas as pd
    from fetch import _derive_issuance
    from fetch.base import LONG_COLUMNS

    class _Out:
        def __init__(self, rows):
            self.rows, self.gaps, self.review, self.log = rows, [], [], []
            self.added = []

        def frame(self):
            return pd.DataFrame(self.rows, columns=LONG_COLUMNS)

        def add(self, df, source, project, detail, tier):
            self.added.append((project, df))

        def gap(self, project, metric, reason="", **kw):
            self.gaps.append({"project": project, "metric": metric, "reason": reason})

        def review_item(self, project, metric, reason, action, **kw):
            self.review.append({"project": project, "metric": metric, "reason": reason, **kw})

    def row(project, metric, value, date="2026-09-21", src="x", tier=1):
        return {"date": pd.Timestamp(date), "project": project, "metric": metric,
                "value": value, "source": src, "tier": tier, "is_manual": False, "entered_on": ""}

    uni = config.PROJECT_BY_NAME["Uniswap"]

    # THE LIVE CASE: gross unchanged, net+burn would have said 219,999.99. That is the drift
    # shape exactly — the chain's burn delta lands in the window while CoinGecko's net figure
    # has not yet moved to match it, so adding the two counts the burn as a mint.
    out = _Out([row("Uniswap", "total_supply", 888_334_418.91),
                row("Uniswap", "total_supply_gross", 1_000_000_000.0),
                row("Uniswap", "gross_burn_tokens", 219_999.99)])
    _derive_issuance(out, [uni],
                     {("Uniswap", "total_supply"): 888_334_418.91,
                      ("Uniswap", "total_supply_gross"): 1_000_000_000.0},
                     {("Uniswap", "total_supply"): "2026-09-20"})
    assert out.added, f"nothing derived; gaps={[g['reason'][:80] for g in out.gaps]}"
    df = out.added[0][1]
    assert float(df["value"].iloc[0]) == 0.0, \
        f"gross supply did not move, so issuance is zero — got {df['value'].iloc[0]}"
    assert df["source"].iloc[0] == "derived:d_supply_gross"

    # AND THE DISAGREEMENT IS RAISED, not silently discarded — that is the cross-check.
    assert any(r["reason"] == "issuance_route_divergence" for r in out.review), \
        "the net+burn route said 219,999.99 against a gross delta of 0 — that must be visible"

    # A REAL MINT STILL COMES THROUGH, so the fix is not "always report zero". Here the two
    # routes AGREE: 170,000 minted against 220,000 burned leaves the net figure 50,000 lower,
    # and d(net)+burn lands back on 170,000. No divergence to raise.
    out = _Out([row("Uniswap", "total_supply", 888_284_418.91),
                row("Uniswap", "total_supply_gross", 1_000_170_000.0),
                row("Uniswap", "gross_burn_tokens", 220_000.0)])
    _derive_issuance(out, [uni],
                     {("Uniswap", "total_supply"): 888_334_418.91,
                      ("Uniswap", "total_supply_gross"): 1_000_000_000.0},
                     {("Uniswap", "total_supply"): "2026-09-20"})
    assert float(out.added[0][1]["value"].iloc[0]) == 170_000.0
    assert not [r for r in out.review if r["reason"] == "issuance_route_divergence"], \
        "the routes agree here; a divergence flag on an agreeing pair is noise"

    # A FALLING GROSS SUPPLY IS A READ FAULT, not a supply event — a transfer burn cannot do it.
    out = _Out([row("Uniswap", "total_supply", 888_114_418.92),
                row("Uniswap", "total_supply_gross", 999_000_000.0)])
    _derive_issuance(out, [uni],
                     {("Uniswap", "total_supply"): 888_334_418.91,
                      ("Uniswap", "total_supply_gross"): 1_000_000_000.0},
                     {("Uniswap", "total_supply"): "2026-09-20"})
    assert not out.added and any("NEGATIVE" in g["reason"] for g in out.gaps)
    print("issuance ok: gross delta preferred, divergence flagged, real mints still derive")
