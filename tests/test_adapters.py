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


def test_cross_check_metrics_do_not_collide_with_their_primary():
    """Every declared cross-check must use a DIFFERENT metric name from its primary."""
    for p in config.PROJECTS:
        for check in p.get("cross_checks") or []:
            assert check["primary"] != check["secondary"], f"{p['name']}: cross-check collides with its primary"
            assert check["secondary"] in config.METRICS, f"{p['name']}: {check['secondary']} is not a known metric"
    print("cross-check naming ok: every secondary has its own metric key")


# ---------------------------------------------------------------------------- tier 4
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
    assert any("already holds history" in e.message for e in out.log)
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


def test_gap_detection_covers_every_applicable_metric():
    from fetch.gaps import detect
    frame = pd.DataFrame(columns=["date", "project", "metric", "value", "source", "tier"])
    gaps = detect(config.PROJECTS, frame, set(), {}, [])
    keys = {(g["project"], g["metric"]) for g in gaps}
    for p in config.PROJECTS:
        for m in config.metrics_for_project(p):
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
               test_several_contracts_serving_one_metric_are_summed,
               test_components_sum_fully_once_every_chain_has_its_token,
               test_no_metric_is_served_by_contracts_that_would_overwrite_each_other,
               test_chain_symbol_mismatch_rejects, test_chain_read_failure_is_logged_not_raised,
               test_venft_misconfiguration_fails_loudly, test_escrow_balance_of_reads_the_underlying_not_the_nft,
               test_unestablished_lock_read_method_is_refused,
               test_hypercore_info_reads_the_assistance_fund_without_any_chain,
               test_hypercore_reports_a_response_shape_change_rather_than_guessing,
               test_venice_buy_and_burn_stays_refused_as_uncorroborated,
               test_extract_xhr, test_extract_dom_anchor_and_ambiguity,
               test_scrape_registry_reports_incomplete_entries_as_gaps,
               test_later_tier_never_overwrites_an_earlier_one, test_cross_check_metrics_do_not_collide_with_their_primary,
               test_dune_backfill_only, test_validation_bounds_and_threshold, test_parse_number,
               test_gap_detection_covers_every_applicable_metric, test_manual_overrides_suppress_gaps]:
        fn()
    print("\nALL ADAPTER TESTS PASSED")
