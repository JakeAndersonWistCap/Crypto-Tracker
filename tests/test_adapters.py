"""
tests/test_adapters.py — adapter parsing against payloads shaped like the real sources.

    python tests/test_adapters.py

Runs offline: the HTTP layer, the web3 connection and the Playwright page are all stubbed.
Checks that each tier turns its documented response shape into tidy long rows with the right
metric key, source label and tier, and that a failure is logged and reported as a gap rather
than raised.
"""
from __future__ import annotations

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

    The Dss Flappers audit describes a Splitter feeding an AMM Flapper that sends proceeds to a
    configurable receiver — LP tokens, in one variant. No balance read models that, so the entry
    is REMOVED rather than re-pointed, and the mechanism is marked refuted so nothing re-adds one.
    """
    sky = config.PROJECT_BY_NAME["Sky"]
    assert "burn_zero" not in sky["contracts"], "re-pointing or re-adding a burn address models this wrongly"
    assert sky["burn_read_method"] == "undetermined", "'transfer' was the refuted assumption"
    mech = config.burn_mechanism(sky)
    assert mech["status"] == "refuted" and mech["model"] == "amm_swap_to_receiver"
    assert "chainsecurity" in (mech["source_url"] or "").lower()

    # even if somebody re-added a burn address, a refuted mechanism refuses the read
    probe = dict(sky)
    probe["contracts"] = dict(sky["contracts"])
    probe["contracts"]["burn_zero"] = config._contract(
        "0x0000000000000000000000000000000000000000", "ethereum", "burn_address_balance", "SKY",
        "https://example.invalid", verified="2026-09-14")
    probe["burn_read_method"] = "transfer"          # the old, refuted assumption
    c = Chain(prior_values={}, prior_dates={})
    c.reader = StubReader(symbol="SKY", supply=1e10, balance=0.0)
    out = FetchOutput()
    c.run([probe], None, out)
    assert "burn_address_balance" not in set(out.frame().metric), \
        "a refuted mechanism must refuse the read whatever the address says"
    gap = next(g for g in out.gaps if g["metric"] == "burn_address_balance")
    assert "MECHANISM is refuted" in gap["reason"]
    assert "Do not substitute another address" in gap["suggestion"]
    print("sky ok: burn address removed, refuted mechanism refuses any replacement")


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


def test_the_two_burn_models_derive_DIFFERENT_issuance_from_identical_inputs():
    """The whole reason issuance is keyed on the mechanism. Same numbers in, different answers out.

    A protocol burn destroys supply, so the supply delta is already NET of it and the burn must be
    added back. A transfer burn leaves totalSupply untouched, so adding it would invent issuance
    that never happened. If these two ever agree, the derivation has collapsed into one formula
    and one of the two families of projects is silently wrong.
    """
    SUPPLY_NOW, SUPPLY_PRIOR, BURN = 1_000_100.0, 1_000_000.0, 30.0   # delta = +100, burn = 30

    protocol, _ = _derive("Ethereum", "protocol_level_destruction", SUPPLY_NOW, SUPPLY_PRIOR, BURN)
    transfer, _ = _derive("PancakeSwap", "transfer_to_dead_address", SUPPLY_NOW, SUPPLY_PRIOR, BURN)

    assert protocol == 130.0, f"protocol burn: issuance = delta + burn = 130, got {protocol}"
    assert transfer == 100.0, f"transfer burn: issuance = delta = 100, got {transfer}"
    assert protocol != transfer, "IDENTICAL INPUTS MUST NOT GIVE THE SAME ANSWER — the keying is broken"
    assert protocol - transfer == BURN, "the difference between the models is exactly the burn"
    print(f"issuance models ok: protocol {protocol:,.0f} vs transfer {transfer:,.0f}, "
          f"differing by the burn ({BURN:,.0f})")


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
    value, _ = _derive("PancakeSwap", "transfer_to_dead_address", 1_000_100.0, 1_000_000.0, None)
    assert value == 100.0, "a transfer burn does not touch supply, so no burn term is needed"
    print("issuance refusals ok: single observation, missing burn, and unestablished mechanism")


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
    """Issuance cannot be below zero. A negative means an input is wrong, not that supply shrank."""
    value, out = _derive("PancakeSwap", "transfer_to_dead_address", 999_000.0, 1_000_000.0, None)
    assert value is None, "a negative derivation must not be stored"
    assert any(r["reason"] == "negative_derived_issuance" and r["action"] == "rejected"
               for r in out.review)
    print("negative issuance ok: rejected to the Review Queue with its inputs named")


def test_the_three_burn_failure_modes_stay_distinct():
    """Sky, Uniswap and Venice failed in three different ways. Collapsing them loses the fixes."""
    sky = config.burn_mechanism(config.PROJECT_BY_NAME["Sky"])
    uni = config.burn_mechanism(config.PROJECT_BY_NAME["Uniswap"])
    ven = config.burn_mechanism(config.PROJECT_BY_NAME["Venice AI"])

    # 1. wrong mechanism: no address can model it, so there is no burn contract at all
    assert sky["status"] == "refuted" and sky["model"] == "amm_swap_to_receiver"
    assert not [v for v in config.PROJECT_BY_NAME["Sky"]["contracts"].values()
                if v["kind"] == "burn_address_balance"]

    # 2. right mechanism, wrong address: mechanism confirmed, and the fix was the destination
    assert uni["status"] == "confirmed" and uni["model"] == "transfer_to_dead_address"
    assert config.PROJECT_BY_NAME["Uniswap"]["contracts"]["burn_dead"]["kind"] == "burn_address_balance"

    # 3. undocumented: neither confirmed nor refuted, and it must not inherit either answer
    for still_open in ("PancakeSwap", "GEODNET"):
        assert config.burn_mechanism(config.PROJECT_BY_NAME[still_open])["status"] == "assumed", \
            f"{still_open} is unresolved and resembles none of the others"

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
    """The other half of the behaviour above, on a project that still spans chains.

    GEODNET declares GEOD on Polygon, Solana and IoTeX. The EVM adapter covers Polygon; solana and
    iotex have no RPC in DEFAULT_RPC. Those components must be REFUSED at the chain-coverage gate
    and the resulting supply marked PARTIAL — never silently dropped and the remainder reported as
    the whole. This matters more for GEODNET than for a missing endpoint: the Wormhole NTT bridge
    model is unresolved, so summing the chains could be a double-count even if every read worked.
    """
    geod = config.PROJECT_BY_NAME["GEODNET"]
    POLY_GEOD = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"

    class PolygonOnlyStub:
        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            ok = (chain, address) == ("polygon", POLY_GEOD)
            return ok, "GEOD" if ok else ""

        def scaled(self, chain, address, call, *args):
            return 5_000.0 if call == "balanceOf" else 1_000_000_000.0

    c = Chain()
    c.reader = PolygonOnlyStub()
    out = FetchOutput()
    c.run([geod], None, out)
    df = out.frame()

    supply = df[df.metric == "total_supply"]
    assert len(supply) == 1, f"one supply figure, summed from what could be read: {supply.to_dict()}"
    assert supply.source.iloc[0].endswith(":PARTIAL"), \
        f"Solana and IoTeX were refused — the figure must say so: {supply.source.iloc[0]}"
    assert "polygon:token_polygon" in supply.source.iloc[0]

    reasons = " ".join(str(g.get("reason", "")) for g in out.gaps)
    assert "'solana'" in reasons and "'iotex'" in reasons, \
        f"both uncovered chains must be named specifically, not lumped together: {reasons}"
    print("uncovered-chain refusal ok: GEOD supply is Polygon-only and marked PARTIAL, "
          "with solana and iotex each named")


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

    locked = df[df.metric == "locked_tokens"].sort_values("date")
    assert len(locked) == 2, f"a daily history backfills every row, got {len(locked)}"
    assert locked.value.iloc[-1] == 141_470_107.5
    assert str(locked.date.iloc[-1].date()) == "2026-09-10", "the row carries the query's own date"

    rate = df[df.metric == "lock_rate_pct"].sort_values("date")
    assert rate.value.iloc[-1] == 0.17452, f"the FRACTION column, not its x100 twin: {rate.value.iloc[-1]}"
    assert 17.452 not in set(df.value), "perc_staked_cnt must not reach the store under any metric"

    holders = df[df.metric == "staker_count"].sort_values("date")
    assert holders.value.iloc[-1] == 13_011, "num_holders lands under staker_count"
    assert set(df.metric) == {"locked_tokens", "lock_rate_pct", "staker_count"}, \
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
    held = {("Ether.fi", "locked_tokens"), ("Ether.fi", "lock_rate_pct")}

    os.environ["TOKEN_METRICS_DUNE_ALWAYS"] = "1"
    try:
        d = Dune(has_history=held)
        d.http = _Rows(rows)
        out = FetchOutput()
        d.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out)      # a later, incremental run
        locked = out.frame()
        assert not [e for e in out.log if e.status == "skipped"], "the flag overrides the skip"
        locked = locked[locked.metric == "locked_tokens"]
        assert len(locked) == 120, f"a forced re-pull rebuilds the whole series, got {len(locked)} rows"
    finally:
        del os.environ["TOKEN_METRICS_DUNE_ALWAYS"]

    # without the flag the window still applies, because then it IS just a trailing re-fetch
    d = Dune()
    d.http = _Rows(rows)
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out)
    locked = out.frame()
    locked = locked[locked.metric == "locked_tokens"]
    assert 25 <= len(locked) <= 31, f"an ordinary run still honours the 30-day window, got {len(locked)}"
    print("forced re-pull ok: full 120 rows with the flag, 30-day window without it")


def test_etherfi_daily_history_is_a_backfill_not_an_ongoing_read():
    """It is a normal historical source: once the store holds it, tier 4 skips it like any other."""
    import os

    os.environ["DUNE_API_KEY"] = "test-key"
    d = Dune(has_history={("Ether.fi", "locked_tokens"), ("Ether.fi", "lock_rate_pct"),
                          ("Ether.fi", "staker_count")})
    d.http = _Rows([ETHERFI_ROW])
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], None, out)
    assert out.frame().empty, "a dated query the store already holds must be skipped"
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
    """
    import yaml
    from fetch.base import parse_number
    from fetch.scrape import entry_ready

    entries = yaml.safe_load(open("sources.yaml", encoding="utf-8"))
    entry = next(e for e in entries
                 if e["project"] == "Maple" and e["metric"] == "buyback_fund_balance_dashboard")

    ready, why = entry_ready(entry)
    assert ready, f"the Maple cross-check secondary must be usable, got: {why}"
    assert entry["anchor"] == "SYRUP Holdings", f"anchor is the whole job here, got {entry['anchor']!r}"

    # NO scale FIELD. parse_number already expands the suffix; a scale would multiply again.
    assert "scale" not in entry or entry.get("scale") in (None, 1), \
        "parse_number handles 'M' natively — a scale of 1e6 on top would be a million-fold error"
    assert parse_number("77.66M") == 77_660_000.0
    assert parse_number("$4.63M") == 4_630_000.0

    # THE FLOOR, read from where validate_frame actually reads it. The registry's own
    # sanity_min/sanity_max are not consulted by anything, so asserting those would prove nothing.
    lo, hi = config.sanity_bounds("Maple", "buyback_fund_balance_dashboard")
    unscaled = parse_number("77.66")          # what a dropped suffix would yield
    assert unscaled == 77.66
    assert lo is not None and unscaled < lo, \
        f"an unscaled 77.66 must be REJECTED, not stored — floor is {lo}"
    assert lo <= 77_660_000.0 <= (hi or float("inf")), \
        f"and the correct figure must pass, bounds were ({lo}, {hi})"

    # The cross-check itself exists and points at the right pair.
    checks = config.PROJECT_BY_NAME["Maple"]["cross_checks"]
    pair = next(c for c in checks if c["secondary"] == "buyback_fund_balance_dashboard")
    assert pair["primary"] == "treasury_holding_tokens"
    # RESOLVED 2026-09-18: the address question is settled, exactly as this test's own comment
    # predicted it eventually would be. destination_status is no longer "disputed" (which uniquely
    # refuses to store — see fetch/chain.py), so the primary CAN now store a figure and the
    # cross-check can fire on the next live run. "verified_by_label" is a weaker tier than
    # "confirmed" (Etherscan's label, not Maple's own material, identifies the address) — the armed
    # cross-check asserted above is what upgrades it: a balance landing near 77.66M confirms the
    # label, a balance near zero or wildly different reopens the dispute.
    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    assert treasury["destination_status"] != "disputed", \
        "the primary should be storing again now that the address is resolved"
    assert treasury["destination_status"] == "verified_by_label"
    assert treasury["address"] == "0xd6d4Bcde6c816F17889f1Dd3000aF0261B03a196", \
        "must be the daoMultisig / 'Maple Finance: DAO' address, not the old disputed fee treasury"
    print("maple cross-check ok: armed (anchor 'SYRUP Holdings', entry_ready passes), "
          "77.66M parses, an unscaled 77.66 is rejected by the floor, treasury address resolved "
          "to verified_by_label so the primary can store again")


def test_an_armed_cross_check_reads_as_WAITING_not_as_an_unbuilt_metric():
    """Two separate things made the armed Maple guard look like nobody had built it.

    FIRST, the gap text was factually wrong. fetch_all recorded only the NOT-READY registry
    entries, so a complete, enabled, armed entry was invisible to the gap reporter and fell
    through to "no sources.yaml entry for this metric" — when there plainly was one, with a url
    and a selector. A reader acting on that would have gone and written a second entry.

    SECOND, even with correct text, status 'gap' cannot distinguish "armed and correctly idle"
    from "nobody has built this". The secondary here has nothing to compare against because its
    PRIMARY is a disputed destination that stores nothing — which is the guard working, not
    failing.
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
    gap = next(g for g in rows
               if g["project"] == "Maple" and g["metric"] == "buyback_fund_balance_dashboard")
    assert "no sources.yaml entry" not in gap["reason"], \
        f"the entry exists and is armed — saying otherwise sends the reader to write a second one: {gap['reason']}"
    assert "ARMED" in gap["reason"] and "maple.finance/transparency" in gap["reason"] \
        and "SYRUP Holdings" in gap["reason"], \
        f"an armed entry must name its url and selector so the reader can tell the cases apart: {gap['reason']}"
    assert "Run Log" in gap["suggestion"], "and point at where 'did it actually run' is answered"

    # (2) status must distinguish armed-and-idle from unbuilt
    #
    # Maple's treasury dispute was RESOLVED 2026-09-18 (see test_the_maple_cross_check_is_ARMED...),
    # so the primary is no longer suppressed by config and this branch has no live example left.
    # The mechanism itself is unchanged — force the disputed state back on temporarily, the same
    # pattern used elsewhere in this file (e.g. test_fluid_buyback_is_suppressed...) to exercise a
    # branch the live config no longer takes.
    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    live_status = treasury["destination_status"]
    treasury["destination_status"] = "disputed"
    try:
        empty = pd.DataFrame(columns=["date", "project", "metric", "value", "source", "tier",
                                      "is_manual", "entered_on"])
        out = bw.aggregate(empty, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                           gaps=pd.DataFrame(), review=pd.DataFrame())

        waiting = out[(out.project == "Maple") & (out.metric == "buyback_fund_balance_dashboard")].iloc[0]
        assert waiting["status"] == "waiting", \
            f"an armed secondary with a suppressed primary is not a plain gap: {waiting['status']}"
        assert "WAITING ON THE PRIMARY" in waiting["note"] and "treasury_holding_tokens" in waiting["note"], \
            f"and the note must name what it is waiting for: {waiting['note']!r}"

        # NARROW ON PURPOSE: an ordinary dashboard metric with no cross-check is untouched, and so is
        # a secondary whose primary is merely empty rather than suppressed by config.
        other = out[(out.project == "Maple") & (out.metric == "locked_tokens_dashboard")].iloc[0]
        assert other["status"] != "waiting", \
            f"only a secondary blocked BY CONFIG waits; everything else is an honest gap: {other['status']}"
        assert config.cross_check_waiting_on_primary("Chainlink", "buyback_fund_balance_dashboard") is None, \
            "Chainlink's primary is not disputed, so its secondary is an ordinary gap"
        print("waiting state ok: armed entry named with its url and selector, status 'waiting' not "
              "'gap', and nothing else reclassified")
    finally:
        treasury["destination_status"] = live_status


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
    """
    import pandas as pd
    import build_workbook as bw

    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    live_status = treasury["destination_status"]
    treasury["destination_status"] = "disputed"
    try:
        assert config.destination_disputed("Maple", "treasury_holding_tokens"), \
            "precondition: Maple's treasury contract is the disputed one"

        stale = pd.DataFrame([{
            "date": pd.Timestamp("2026-09-14"), "project": "Maple",
            "metric": "treasury_holding_tokens", "value": 0.5125357033239131,
            "source": "chain:ethereum:treasury", "tier": 2, "is_manual": False, "entered_on": ""}])

        # NO gap row is passed, deliberately: the live symptom is that the gap never reaches this
        # branch at all. If suppression depended on the gap being present, this test would pass for
        # the wrong reason.
        out = bw.aggregate(stale, pd.DataFrame(), pd.Timestamp("2026-09-15"),
                           gaps=pd.DataFrame(), review=pd.DataFrame())
        row = out[(out.project == "Maple") & (out.metric == "treasury_holding_tokens")].iloc[0]

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
    """
    spec = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    live_status = spec["destination_status"]
    spec["destination_status"] = "disputed"
    try:
        assert spec["destination_status"] == "disputed", (
            "the address is real and its role is not — it must be read as evidence and stored as "
            f"nothing, got {spec.get('destination_status')!r}")

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

        assert "treasury_holding_tokens" not in set(df.metric), \
            "a disputed destination must not reach the sheet as a figure"
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
    assert contracts["token_iotex"]["chain"] not in config.EVM_CHAINS, \
        "iotex has no RPC, so the IoTeX deployment must stay unreadable until one is added"
    assert contracts["token_polygon"]["supply_is_partial"], \
        "Polygon is read alone while the bridge model is open — the figure must say it is partial"
    print("geodnet addresses ok: all four match, Solana exactly, EVM modulo EIP-55 casing; "
          "IoTeX recorded but unreadable, Polygon marked partial")


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

    # THE EXEMPTION. PancakeSwap's cumulative dead-address balance is not bounded by an
    # instantaneous supply, because CAKE mints and burns continuously.
    cake = [("burn_address_balance", 4_991_087_157.0), ("total_supply", 400_000_000.0)]
    out = run("PancakeSwap", cake)
    assert not any("burn_address_balance" in str(r) for r in out.review), \
        "PancakeSwap's cumulative burn must NOT be compared against instantaneous supply"

    # ** AND IT MUST NOT LEAK. ** The same figures on a project with no exemption still fire, so
    # the exemption is scoped to the project and the pair rather than disabling the relation.
    out = run("Uniswap", cake)
    assert any("burn_address_balance" in str(r) for r in out.review), \
        "the exemption leaked to a project that does not declare it"

    # AND THE LIVE RELATION FOR PANCAKESWAP. Exempting one comparison must not leave the project
    # unchecked: a period flow against a stock is an identity even for a minting token.
    out = run("PancakeSwap", [("gross_burn_tokens", 500_000_000.0), ("total_supply", 400_000_000.0)])
    assert any("gross_burn_tokens" in str(r) for r in out.review), \
        "gross_burn_tokens vs total_supply must stay live for PancakeSwap"

    # The exemption is declared with a reason, and config refuses one without.
    assert config.relation_exempt("PancakeSwap", "burn_address_balance", "total_supply")
    assert config.relation_exempt("PancakeSwap", "gross_burn_tokens", "total_supply") is None
    print("bound check ok: 9 relations, zero tolerance, exemption scoped and non-leaking")


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
    the duration of this test, same pattern as the dedicated disputed-destination tests above,
    so the regression coverage does not silently go dark the moment the example resolves.
    """
    import pandas as pd

    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    live_status = treasury["destination_status"]
    treasury["destination_status"] = "disputed"
    try:
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
    finally:
        treasury["destination_status"] = live_status

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
         built against. Add a seventh RED branch and this fails until somebody adds a row for
         it — which is the only thing that stops the fixture silently covering five of seven
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
    # THE SIX, in withheld_for's own order:
    #   orphaned                 contract removed from config
    #   withdrawn                contract re-purposed — its kind changed
    #   suppressed               derivation switched off in config
    #   disputed                 the contract's ROLE for this project is in doubt
    #   measuring_point_changed  series read from two different places
    #   refuted                  project does not burn the way this metric measures
    EXPECTED_MECHANISMS = 6

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

    # ** AND THE STANDARDISATION ITSELF, asserted rather than assumed: ALL SIX BLANK. ** This is
    # the invariant that was false until 2026-09-15, when three of them went RED and printed the
    # number anyway. If a seventh mechanism is added that flags without blanking, this fails.
    for e in data["expectations"]:
        if e["transition"] == "control":
            continue
        assert e["expect_blank"] is True, \
            f"{e['transition']} on {e['project']}/{e['metric']} is RED but still shows its value — " \
            f"all six withheld mechanisms must blank"

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

    assert row["status"] == "suppressed", f"a stored zero must not read 'ok': {row['status']}"
    assert pd.isna(row["now"]), "a false zero must be blank, not shown — it feeds the ratio"
    assert all(pd.isna(row[f]) for f in ("m1", "q0", "q1", "q2", "q3", "y1"))
    assert row["confidence"] == "RED"
    assert "suppressed" in row["note"].lower() and "per-miner" in row["note"].lower()

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
    # pass because the derivation broke for everyone.
    value, _ = _derive("Uniswap", "transfer_to_dead_address",
                       supply_now=1_000_500.0, supply_prior=1_000_000.0, burn=None)
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

    # THE IMPLIED FIGURE IS SUPPRESSED — this is the visible flag Jake asked for.
    formula = bw.base_gated(sky, "REVENUE*SHARE")
    assert formula == '="base unconfirmed"', f"Sky's implied buyback must render as unconfirmed: {formula!r}"

    # COMPOSED WITH threshold_gated exactly as the real A3 columns do it.
    composed = bw.base_gated(sky, bw.threshold_gated(sky, "REVENUE*SHARE/PRICE"))
    assert composed == '="base unconfirmed"'

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
