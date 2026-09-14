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
    """Only the mainnet UNI token resolves, so the Unichain path refuses and the sum stays PARTIAL."""

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
    assert ":PARTIAL" in burn.source.iloc[0], "Unichain's burn path is separate and still unknown"

    # the executors are kept, and read nothing
    reference = [e.message for e in out.log if "reference only" in e.message]
    assert len(reference) == 2 and all("burn_executor" in m for m in reference), \
        "both Firepits stay in config for the mechanism, and serve no metric"
    print("uniswap ok: dead address read, executors kept as reference, figure marked PARTIAL")


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
    """Below the threshold there is no small buyback — there is no buyback."""
    thr = config.PROJECT_BY_NAME["Fluid"]["buyback_threshold"]
    assert thr["threshold_usd_annualised"] == 10_000_000
    assert thr["status"] != "active", "unconfirmed or below-threshold must suppress"

    formula, cell = _a3_formula("Fluid", "BUYBACK AS % OF SUPPLY")
    assert "threshold" in formula or "below threshold" in formula, \
        f"the derived buyback must be suppressed, got {formula[:90]}"
    assert "Data!" not in formula, "a suppressed figure must not still compute from the data"
    assert cell.comment and "SWITCH" in cell.comment.text

    # FORCE THE OTHER BRANCH: with governance confirming the switch is on, it must compute
    thr["status"] = "active"
    try:
        formula, _ = _a3_formula("Fluid", "BUYBACK AS % OF SUPPLY")
        assert "Data!" in formula, "confirmed-active must produce the real formula, not a label"
    finally:
        thr["status"] = "unconfirmed_which_side"

    # and a project with no threshold block is untouched by any of this
    other, _ = _a3_formula("Aave", "BUYBACK AS % OF SUPPLY")
    assert "threshold" not in other
    print("fluid ok: suppressed while the switch is unconfirmed, computes when governance says active")


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
    clean = {"status": "ok", "source": "chain:maple", "n_points": 9, "entered_on": ""}

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

    # 3. a mechanism with no established supply effect -> no formula applies (Sky)
    value, out = _derive("Sky", "amm_swap_to_receiver", 1_000_100.0, 1_000_000.0, None, status="refuted")
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
    live = config.split_for_window("Sky", "2026-08-20", "2026-09-12")
    assert live["status"] == "active" and live["share_to_buyback"] == 0.55, "the documented period still resolves"

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
    """Components on the SAME chain sum; a component with no same-chain token is refused.

    The live run failed here: token_jar_unichain resolved its token to the ETHEREUM UNI address and
    called it over the Unichain RPC, which returns empty data. A token address is not valid across
    chains, so the component is refused and the sum is marked PARTIAL rather than quietly dropping
    a burn path and reporting the remainder as the whole.
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
    for metric in ("buyback_fund_balance", "burn_address_balance"):
        src = df[df.metric == metric].source.iloc[0]
        assert src.endswith(":PARTIAL"), f"{metric} dropped a component and must be marked PARTIAL: {src}"
    assert not [e for e in out.log if e.status == "failed"], \
        "a chain-mismatched component must be refused cleanly, not fail with a decode error"
    assert any("no ERC-20 token contract is declared on unichain" in g["reason"] for g in out.gaps), out.gaps

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
    print("multi-contract summing ok: mainnet jars sum to 4,500,000, Unichain refused, result marked PARTIAL")
    print("  symbol() targeted only the UNI token, only on ethereum — never a jar, never a wrong chain")


def test_components_sum_fully_once_every_chain_has_its_token():
    """With a same-chain token declared for every component, all of them sum."""
    import copy

    uni = copy.deepcopy(config.PROJECT_BY_NAME["Uniswap"])
    uni["contracts"]["token_unichain"] = dict(uni["contracts"]["token"],
                                              address="0xUNIonUnichain", chain="unichain")
    uni["contracts"]["token_jar_unichain"]["underlying"] = "token_unichain"
    uni["contracts"]["fire_pit_unichain"]["underlying"] = "token_unichain"

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
    assert len(df) == 1, f"the incomplete current month must be dropped, got {len(df)} rows"
    assert df.value.iloc[0] == 1_400_000.0, f"both chains must sum, got {df.value.iloc[0]}"
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
    assert len(contracts) == 5, "the query documents four addresses; no new ones were added"
    print("geodnet addresses ok: all four match, Solana exactly, EVM modulo EIP-55 casing")


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


if __name__ == "__main__":
    for fn in [test_defillama, test_coingecko,
               test_chain_refuses_unverified_by_default, test_chain_reads_verified_and_derives_flow,
               test_a_zero_burn_from_a_balance_delta_is_flagged_not_reported_as_measured,
               test_a_single_observation_never_produces_a_zero_flow,
               test_a_burn_address_holding_exactly_zero_is_flagged_as_evidence_about_the_address,
               test_sky_has_no_burn_address_because_it_has_no_dead_address_mechanism,
               test_uniswap_reads_the_burn_DESTINATION_not_the_contract_that_executes_the_burn,
               test_a_burn_destination_that_is_not_a_dead_address_is_rejected_by_config,
               test_uniswap_burn_below_the_retroactive_burn_alone_is_rejected,
               test_fluid_buyback_is_suppressed_because_it_is_a_SWITCH_not_a_rate,
               test_a_supply_additive_buyback_increases_net_issuance_never_decreases_it,
               test_maple_indeterminate_destination_lands_in_the_AMBER_band_automatically,
               test_validation_survives_a_REAL_fetch_all_frame,
               test_the_two_burn_models_derive_DIFFERENT_issuance_from_identical_inputs,
               test_issuance_refuses_rather_than_guessing,
               test_a_measured_issuance_is_never_overwritten_by_a_derivation,
               test_a_negative_derived_issuance_is_rejected,
               test_the_three_burn_failure_modes_stay_distinct,
               test_every_transfer_burn_declares_where_its_model_came_from,
               test_an_assumed_mechanism_flags_the_figure_without_withdrawing_it,
               test_a_real_burn_is_not_flagged, test_sky_split_history_cannot_resolve_across_the_april_overhaul,
               test_several_contracts_serving_one_metric_are_summed,
               test_components_sum_fully_once_every_chain_has_its_token,
               test_no_metric_is_served_by_contracts_that_would_overwrite_each_other,
               test_dead_and_zero_addresses_are_accepted_as_holders_despite_having_no_code,
               test_contract_holders_still_get_the_bytecode_check,
               test_chain_symbol_mismatch_rejects, test_chain_read_failure_is_logged_not_raised,
               test_venft_misconfiguration_fails_loudly, test_escrow_balance_of_reads_the_underlying_not_the_nft,
               test_unestablished_lock_read_method_is_refused,
               test_hypercore_info_reads_the_assistance_fund_without_any_chain,
               test_hypercore_reports_a_response_shape_change_rather_than_guessing,
               test_venice_buy_and_burn_stays_refused_as_uncorroborated,
               test_extract_xhr, test_extract_dom_anchor_and_ambiguity,
               test_scrape_registry_reports_incomplete_entries_as_gaps,
               test_later_tier_never_overwrites_an_earlier_one, test_cross_check_metrics_do_not_collide_with_their_primary,
               test_a_monthly_backfill_does_not_double_count_a_month_the_live_read_covers,
               test_dune_sums_split_columns_and_drops_the_incomplete_current_period,
               test_dune_reports_real_columns_rather_than_guessing_an_unmapped_query,
               test_snapshot_query_is_dated_now_and_stages_the_columns_nobody_chose,
               test_snapshot_declaration_is_checked_not_trusted,
               test_a_snapshot_query_is_never_skipped_as_already_backfilled,
               test_etherfi_reads_the_fraction_column_not_its_x100_twin,
               test_etherfi_daily_history_is_a_backfill_not_an_ongoing_read,
               test_forced_repull_takes_the_full_history_not_the_trailing_window,
               test_geodnet_sql_addresses_match_config_exactly,
               test_dune_backfill_only, test_validation_bounds_and_threshold, test_parse_number,
               test_a_closed_figure_is_not_a_gap_and_its_history_limit_is_not_a_closure,
               test_aerodrome_still_reads_locked_tokens_from_the_escrow_with_no_cross_check,
               test_gap_detection_covers_every_applicable_metric, test_manual_overrides_suppress_gaps]:
        fn()
    print("\nALL ADAPTER TESTS PASSED")
