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


class _HolderTokenReader:
    """A HOLDER WHOSE OWN SYMBOL DIFFERS FROM THE TOKEN IT HOLDS — the sPENDLE/PENDLE shape.

    One address answers symbol() with the vault's ticker and the other with the token's, and
    balanceOf on the token returns the assets while totalSupply on the holder returns the shares.
    That is the whole point: the two reads give DIFFERENT, both-plausible numbers, so which
    contract is called and which is asked for its symbol are separate questions.
    """

    def __init__(self, holder, token, holder_symbol, token_symbol, shares, assets):
        self.holder, self.token = holder.lower(), token.lower()
        self.holder_symbol, self.token_symbol = holder_symbol, token_symbol
        self.shares, self.assets = shares, assets
        self.symbol_calls, self.read_calls = [], []

    def _sym(self, address):
        return self.token_symbol if str(address).lower() == self.token else self.holder_symbol

    def symbol_matches(self, chain, address, expected):
        self.symbol_calls.append((str(address).lower(), expected))
        actual = self._sym(address)
        return str(actual).lower() == str(expected).lower(), actual

    def has_code(self, chain, address):
        return True

    def scaled(self, chain, address, call, *args):
        self.read_calls.append((str(address).lower(), call, args))
        # balanceOf(holder) on the TOKEN -> assets.  totalSupply() on the HOLDER -> shares.
        return self.assets if args and args[0] else self.shares


def test_every_check_is_reachable_from_main_and_prints_its_section(capsys, monkeypatch):
    """** A CHECK WAS BUILT, TESTED, PUSHED — AND NEVER RAN. **

    aerodrome_lock_inputs was added, covered by its own unit test, and left out of main()'s
    list: the edit meant to register it did not match, did nothing, and said nothing. The
    verification was `'aerodrome_lock_inputs' in dir(module)` — which asks whether the function
    EXISTS, not whether anything calls it. A defined, unreferenced check produces no output, no
    error and no failing test. It is invisible in exactly the way a missing check is.

    ** THE UNIT TESTS COULD NOT HAVE CAUGHT IT, AND THAT IS THE LESSON. ** Every other test of
    this script either reads its source as text or calls one function directly. Nothing ran the
    entry point, so the suite was green with a hole in the run path.

    Two assertions, and the second is the one that generalises:
      1. main() end-to-end prints a section for every registered check.
      2. EVERY function in the file that prints a section header is in the registry — so
         forgetting to register the next one fails here rather than in six weeks' output.
    """
    import inspect
    import re
    import check_offline_items as coi

    # No network: every endpoint list is emptied, so each check takes its unreachable path and
    # prints its header regardless. The headers are what this test is about.
    monkeypatch.setattr(coi, "ETH_RPCS", [], raising=False)
    monkeypatch.setattr(coi, "_PUBLIC_BASE_RPCS", [], raising=False)
    monkeypatch.setattr(coi, "_rpcs_for", lambda chain="ethereum": [], raising=False)

    class _Dead:
        def post(self, *a, **k):
            raise RuntimeError("no network in tests")

        def get(self, *a, **k):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(coi, "requests", _Dead(), raising=False)
    monkeypatch.setattr(sys, "argv", ["check_offline_items.py"])

    rc = coi.main()
    out = capsys.readouterr().out
    assert rc == 0, "one unreachable check must never stop the rest"

    # ===== 1. EVERY REGISTERED CHECK LEFT A TRACE IN THE OUTPUT.
    for fn in coi.CHECKS:
        assert fn.__name__ in out, f"{fn.__name__} is registered but printed nothing"
    assert f"{len(coi.CHECKS)} checks ran" in out, out[-400:]

    # ** AND THE ONE THAT WENT MISSING IS NAMED EXPLICITLY. ** A regression here is the exact
    # bug, not a generic one.
    assert "aerodrome_lock_inputs" in out
    assert "AERODROME" in out.upper(), "the Aerodrome section header must appear"

    # ** AND A SECTION THAT PRINTS SOMETHING IS NOT THE SAME AS ONE THAT WORKS. ** Running the
    # script for real — which is what this test does and nothing else did — showed the Aerodrome
    # probe printing "block (None, None)": eth_block_number returns (block, endpoint) and the
    # guard tested the TUPLE against None, which is never None, so an unreachable chain fell
    # through and made four calls that could not work. Asserting only that the header appears
    # would have passed that too.
    aero = out[out.upper().index("AERODROME"):]
    aero = aero[:aero.find("=====", 200) if aero.find("=====", 200) > 0 else len(aero)]
    assert "(None, None)" not in aero, f"a tuple leaked into the output: {aero[:300]}"
    assert "UNREACHABLE — no Base RPC" in aero, \
        f"with no network it must say so and stop, not proceed: {aero[:300]}"

    # ===== 2. NOTHING THAT PRINTS A SECTION IS LEFT OUT OF THE REGISTRY.
    # A "check" is any public module-level function whose body calls head() — that is what
    # prints a section, so it is the honest definition and it maintains itself.
    src = inspect.getsource(coi)
    registered = {f.__name__ for f in coi.CHECKS}
    unregistered = []
    for name, fn in vars(coi).items():
        if (not name.startswith("_") and inspect.isfunction(fn)
                and fn.__module__ == coi.__name__ and name not in registered):
            try:
                body = inspect.getsource(fn)
            except OSError:
                continue
            if re.search(r"^\s+head\(", body, re.M):
                unregistered.append(name)
    assert not unregistered, (
        f"these print a section but main() never calls them: {unregistered}. "
        f"Add them to check_offline_items.CHECKS — a check that is defined and unreferenced "
        f"produces no output, no error and no failing test.")
    del src
    print(f"entry point ok: {len(coi.CHECKS)} checks registered, all reachable, none orphaned")


def test_a_holder_whose_symbol_differs_from_its_tokens_is_read_through_the_token():
    """** THE RUN OF 20260923T095552Z REFUSED PENDLE'S ASSET READ ON A SYMBOL MISMATCH. **

        FAILED chain / Pendle: spendle_underlying: symbol check FAILED —
        0x9999...4144 reports 'sPENDLE', config expects 'PENDLE'.

    For a balanceOf read the HOLDER is sPENDLE and the TOKEN is PENDLE, so checking the holder's
    symbol against the token's is asking the wrong contract. Same shape as the Chainlink
    staking-pool fix: the call is made on the holder, and symbol() and decimals() come from
    `underlying`.

    ** AND THE SYMBOL MISMATCH WAS THE SYMPTOM, NOT THE FAULT. ** The entry had no read_method,
    so it was never a balanceOf read at all — it would have fallen through to totalSupply() on
    the holder, returning 30,310,807 SHARES to be stored under locked_tokens, the ASSETS column.
    Routing the symbol check through the token and stopping there would have made the gate pass
    and written exactly the confusion the entry exists to remove, with nothing left to catch it.
    So this pins both halves: the right contract is asked for its symbol, AND the right call is
    made.
    """
    HOLDER, TOKEN = "0x9999999999", "0x8888888888"
    SHARES, ASSETS = 30_310_807.38, 35_557_337.0
    project = {
        "name": "Pendle", "archetypes": [3],
        "contracts": {
            "token": {"address": TOKEN, "chain": "ethereum", "kind": "erc20_total_supply",
                      "expected_symbol": "PENDLE", "source_url": "u", "verified": "2026-09-23"},
            "spendle_underlying": {
                "address": HOLDER, "chain": "ethereum", "kind": "stake_underlying",
                "expected_symbol": "PENDLE", "source_url": "u", "verified": "2026-09-23",
                "read_method": "escrow_balance_of", "underlying": "token",
                "token_standard": "erc20", "holder_has_code": True,
                "metric_override": "locked_tokens"},
        },
    }
    c = Chain()
    c.reader = _HolderTokenReader(HOLDER, TOKEN, "sPENDLE", "PENDLE", SHARES, ASSETS)
    out = FetchOutput()
    c.run([project], None, out)

    got = out.frame()
    got = got[got.metric == "locked_tokens"]
    assert not got.empty, f"the read must no longer be refused: {out.log}"
    # ** THE ASSETS, NOT THE SHARES. ** Both numbers are plausible and they differ by the
    # accrued rate, which is exactly why the wrong one would never have looked wrong.
    assert abs(float(got.value.iloc[0]) - ASSETS) < 1e-6, \
        f"locked_tokens must be the ASSETS ({ASSETS:,.0f}), not the shares ({SHARES:,.0f})"
    assert not [e for e in out.log if e.status == "failed"], out.log

    # THE SYMBOL WAS ASKED OF THE TOKEN, NEVER OF THE HOLDER.
    asked = {a for a, _ in c.reader.symbol_calls}
    assert TOKEN in asked, c.reader.symbol_calls
    assert HOLDER not in asked, \
        f"the holder's own symbol is not the token's and must not be checked: {c.reader.symbol_calls}"
    # AND THE CALL WAS MADE ON THE TOKEN WITH THE HOLDER AS ITS ARGUMENT — a balanceOf, not a
    # totalSupply on the holder.
    assert any(addr == TOKEN and args for addr, _, args in c.reader.read_calls), c.reader.read_calls


class _GtpStub:
    """growthepie's fundamentals.json, in the shape the query implies — with the date and value
    keys DELIBERATELY not called `date` and `value`, because that is the thing nobody has read
    from a real row yet."""

    def __init__(self, rows=None, doc=None):
        self.calls = 0
        self._doc = doc if doc is not None else (rows if rows is not None else [])

    def get(self, url, params=None):
        self.calls += 1
        return self._doc


def _gtp_rows(origin="ethereum", key="daa", n=3, dk="date", vk="value"):
    return [{"origin_key": origin, "metric_key": key, dk: f"2026-09-{10+i:02d}",
             vk: 400_000.0 + i} for i in range(n)]


def test_growthepie_reports_the_field_names_instead_of_guessing_them():
    """** CONFIRMED 2026-09-23 FROM LIVE ROWS, and the unconfirmed path is kept for the next one.

    {'metric_key': 'daa', 'origin_key': 'ethereum', 'date': '2026-06-25', 'value': '563332.0'}

    date_field 'date', value_field 'value'. ** THE VALUE ARRIVES AS A STRING. ** float() handles
    it and the adapter has always gone through float() rather than trusting the type — pinned
    here because a numeric-looking string is exactly what a later refactor "tidies" into a
    direct assignment, after which the column sorts and sums as text.

    The unconfirmed branch is still exercised below: it is what will settle the NEXT chain's
    field names, and a path that only ran once is a path that has stopped being tested.
    """
    from fetch.base import FetchOutput
    from fetch.growthepie import GrowThePie

    eth = config.PROJECT_BY_NAME["Ethereum"]
    spec = eth["growthepie"]
    assert spec["status"] == "confirmed"
    assert spec["origin_key"] == "ethereum"
    assert spec["metrics"] == {"active_addresses": "daa", "tx_count": "txcount"}
    assert (spec["date_field"], spec["value_field"]) == ("date", "value")
    assert config.PROJECT_BY_NAME["Plume"]["growthepie"]["origin_key"] == "plume"

    # ** THE STRING VALUE IS CAST, NOT ASSUMED NUMERIC. ** Pinned against the real sample row.
    live = [{"metric_key": "daa", "origin_key": "ethereum", "date": "2026-06-25",
             "value": "563332.0"},
            {"metric_key": "txcount", "origin_key": "plume", "date": "2026-06-25",
             "value": "206496.0"}]
    a = GrowThePie()
    a.http = _GtpStub(doc=live)
    out0 = FetchOutput()
    a.run([eth, config.PROJECT_BY_NAME["Plume"]], None, out0)
    got0 = out0.frame()
    e = got0[(got0.project == "Ethereum") & (got0.metric == "active_addresses")]
    p_ = got0[(got0.project == "Plume") & (got0.metric == "tx_count")]
    assert float(e.value.iloc[0]) == 563332.0 and float(p_.value.iloc[0]) == 206496.0
    assert all(isinstance(v, float) for v in got0.value), \
        "a numeric STRING in the value column would sort and sum as text"

    # ===== ** 120 ROWS, AND THIS IS WHERE THEY LAND. ** Asked for after the first live run:
    # 120 is exactly 2 projects x 2 metrics x 30 days, and the arithmetic working out is not
    # proof the mapping is right. Run EVERY project against a document shaped like the real one
    # — several chains, several metrics — and check that nothing else is touched.
    many = [{"origin_key": c, "metric_key": m, "date": f"2026-08-{d:02d}",
             "value": f"{1000 + d}.0"}
            for c in ("ethereum", "plume", "base", "arbitrum", "optimism")
            for m in ("daa", "txcount", "fees_paid_usd", "tvl")
            for d in range(1, 31)]
    a2 = GrowThePie()
    a2.http = _GtpStub(doc=many)
    wide = FetchOutput()
    a2.run(config.PROJECTS, None, wide)
    df = wide.frame()
    assert len(df) == 120, f"2 projects x 2 metrics x 30 days = 120, got {len(df)}"
    assert sorted(set(df.project)) == ["Ethereum", "Plume"], sorted(set(df.project))
    assert sorted(set(df.metric)) == ["active_addresses", "tx_count"], sorted(set(df.metric))
    assert {(p_, m_): len(g) for (p_, m_), g in df.groupby(["project", "metric"])} == {
        ("Ethereum", "active_addresses"): 30, ("Ethereum", "tx_count"): 30,
        ("Plume", "active_addresses"): 30, ("Plume", "tx_count"): 30}
    assert not [e for e in wide.log if e.status == "failed"], wide.log
    # THREE OTHER CHAINS AND TWO OTHER METRICS WERE IN THE DOCUMENT AND NONE LEAKED — that is
    # what the origin_key AND metric_key filter together buys, and filtering on either alone
    # would have quietly summed chains or pulled in fees.
    assert a2.http.calls == 1, "still one fetch for the whole run"

    # THE UNCONFIRMED PATH, as it will be for the next chain added.
    spec = dict(spec, status="unconfirmed", date_field=None, value_field=None)
    eth = dict(eth, growthepie=spec)
    # ROBOTS CHECKED AND DATED, because respecting it is a design requirement here.
    assert "2026-09-23" in spec["robots_checked"] and "api.growthepie.com" in spec["robots_checked"]

    def run(project, doc):
        a = GrowThePie()
        a.http = _GtpStub(doc=doc)
        out = FetchOutput()
        a.run([project], None, out)
        return out, a

    doc = _gtp_rows("ethereum", "daa", dk="day", vk="val") + \
        _gtp_rows("ethereum", "txcount", dk="day", vk="val") + \
        _gtp_rows("plume", "daa", dk="day", vk="val")
    out, _ = run(eth, doc)
    assert out.frame().empty, "nothing may be stored while the field names are unread"
    msgs = [e.message for e in out.log if e.status == "skipped"]
    assert len(msgs) == 2, msgs
    m = msgs[0]
    # ** IT REPORTS WHAT THE ROW ACTUALLY HAS. ** That is the whole point of the state.
    assert "THE FILTER WORKS: 3 row(s)" in m, m
    assert "'day'" in m and "'val'" in m, f"the real keys must be named: {m}"
    assert "'origin_key', 'val'" in m or "keys ['day'" in m, m
    assert "set status to 'confirmed'" in m

    # ONE DOCUMENT, FETCHED ONCE. It carries every chain and metric, so a call per project would
    # fetch the same megabytes repeatedly and be rude about it.
    a = GrowThePie()
    a.http = _GtpStub(doc=doc)
    out2 = FetchOutput()
    a.run([eth, config.PROJECT_BY_NAME["Plume"]], None, out2)
    assert a.http.calls == 1, f"fetched {a.http.calls} times for two projects"

    # ===== CONFIRMED: IT WRITES, and only then.
    conf = dict(eth, growthepie=dict(spec, status="confirmed", date_field="day",
                                     value_field="val"))
    out3, _ = run(conf, doc)
    got = out3.frame()
    assert set(got.metric) == {"active_addresses", "tx_count"}, sorted(set(got.metric))
    daa = got[got.metric == "active_addresses"].sort_values("date")
    assert len(daa) == 3 and float(daa.value.iloc[0]) == 400_000.0
    assert all(str(s).startswith("growthepie") for s in got.source)

    # ** PLUME IS FILTERED OUT OF ETHEREUM'S SERIES. ** One document, many chains: a filter on
    # metric_key alone would silently sum two chains into one column.
    assert len(daa) == 3, "only the three ethereum daa rows, not plume's as well"

    # ===== A MISSING COMBINATION SAYS WHICH HALF OF THE FILTER MISSED. Two different problems
    # with two different fixes, and the document itself can tell them apart.
    out4, _ = run(conf, _gtp_rows("ethereum", "daa", dk="day", vk="val"))   # no txcount rows
    gaps = [g for g in out4.gaps if g["metric"] == "tx_count"]
    assert gaps and "origin_key 'ethereum' IS present" in gaps[0]["reason"], gaps
    assert "daa" in gaps[0]["reason"], "and it lists what that chain DOES carry"

    out5, _ = run(conf, _gtp_rows("base", "daa", dk="day", vk="val"))       # chain absent
    gaps = [g for g in out5.gaps if g["metric"] == "active_addresses"]
    assert gaps and "is NOT in the document at all" in gaps[0]["reason"], gaps

    # ===== A ROW THAT DOES NOT PARSE IS COUNTED, NEVER DROPPED SILENTLY. A partial series under
    # a full-coverage header is the error this project keeps finding, and it is invisible unless
    # the count is stated.
    ragged = _gtp_rows("ethereum", "daa", n=3, dk="day", vk="val")
    ragged[1]["val"] = None
    ragged[2]["val"] = "not a number"
    out6, _ = run(conf, ragged)
    stored = out6.frame()
    assert len(stored[stored.metric == "active_addresses"]) == 1
    assert any("2 of 3 growthepie row(s) had no usable" in e.message for e in out6.log), out6.log

    # AND IF NONE PARSE, THE FIELD NAMES IN CONFIG ARE WRONG — said plainly, nothing stored.
    out7, _ = run(dict(eth, growthepie=dict(spec, status="confirmed", date_field="nope",
                                            value_field="alsonope")), doc)
    assert out7.frame().empty
    assert any("field names in config are wrong" in e.message for e in out7.log), out7.log

    # A DOCUMENT THAT IS NOT A LIST FAILS LOUDLY RATHER THAN LOOKING EMPTY.
    out8, _ = run(conf, {"data": []})
    assert any("expected a list of rows" in e.message for e in out8.log), out8.log
    print("growthepie ok: field names reported not guessed, one fetch per run, both halves of a "
          "missed filter named, ragged rows counted")


def test_pool_release_is_the_gap_between_the_two_supply_series_and_is_never_clamped():
    """** RELEASE IS NOT ISSUANCE, AND THE WHOLE DIFFICULTY IS THAT THEY LOOK ALIKE. **

    Minting creates tokens: total supply rises and circulating rises with it. Releasing moves
    tokens that ALREADY EXIST out of a pre-minted pool: circulating rises and total does not.
    Both raise circulating supply, so that column alone cannot tell a protocol inflating from
    one distributing what it pre-minted years ago — and for the DePIN names the second is the
    entire supply-side story, which is why these were not closed as not_applicable.
    """
    from fetch import _derive_pool_release
    from fetch.base import FetchOutput, LONG_COLUMNS

    assert config.METRICS["pool_release_tokens"]["kind"] == "flow"
    for n in ("Chainlink", "GEODNET", "Maple", "Hyperliquid", "Aethir"):
        assert "pool_release_tokens" in config.metrics_for_project(config.PROJECT_BY_NAME[n]), n
    # SCOPED. A project with no pre-minted pool must not grow the column.
    assert "pool_release_tokens" not in config.metrics_for_project(
        config.PROJECT_BY_NAME["Uniswap"])

    def run(circ, total, p_circ, p_total, name="Chainlink", dates=None):
        out = FetchOutput()
        rows = [{"date": pd.Timestamp("2026-09-23"), "project": name, "metric": m, "value": v,
                 "source": "coingecko", "tier": 1}
                for m, v in (("circulating_supply", circ), ("total_supply", total))
                if v is not None]
        out.add(pd.DataFrame(rows)[LONG_COLUMNS], "test", name, "", 1)
        delta = {(name, "circulating_supply"): p_circ, (name, "total_supply"): p_total}
        delta = {k: v for k, v in delta.items() if v is not None}
        pdates = dates if dates is not None else {
            (name, "circulating_supply"): pd.Timestamp("2026-09-22"),
            (name, "total_supply"): pd.Timestamp("2026-09-22")}
        _derive_pool_release(out, [config.PROJECT_BY_NAME[name]], delta, pdates)
        got = out.frame()
        got = got[got.metric == "pool_release_tokens"]
        return (float(got.value.iloc[0]) if not got.empty else None), out

    # ** THE CASE THE COLUMN EXISTS FOR: ** total FLAT, circulating up 5m. All release.
    v, out = run(605_000_000.0, 1_000_000_000.0, 600_000_000.0, 1_000_000_000.0)
    assert v == 5_000_000.0, v
    msg = [e.message for e in out.log if "pool_release_tokens=" in e.message][0]
    assert "RELEASE, NOT ISSUANCE" in msg, msg
    assert "CoinGecko" in msg, "the inherited classification is stated on the row"

    # MINTING STRAIGHT INTO CIRCULATION IS NOT A RELEASE — both rise together, so it is zero.
    v, _ = run(605_000_000.0, 1_005_000_000.0, 600_000_000.0, 1_000_000_000.0)
    assert v == 0.0, f"newly minted tokens are issuance, not release: {v}"

    # ** NEVER CLAMPED. ** Circulating falling faster than total is tokens going back OUT of
    # circulation — into a lockup or a treasury purchase. Real, and exactly what a
    # "releases can't be negative" floor would erase.
    v, _ = run(598_000_000.0, 1_000_000_000.0, 600_000_000.0, 1_000_000_000.0)
    assert v == -2_000_000.0, f"a negative release must survive: {v}"

    # A MISSING STOCK SAYS SO RATHER THAN HALF-COMPUTING.
    v, out = run(605_000_000.0, None, 600_000_000.0, 1_000_000_000.0)
    assert v is None and any("missing total_supply" in e.message for e in out.log), out.log

    # NO PRIOR IS NOT A SOURCING PROBLEM, and the message says which it is.
    v, out = run(605_000_000.0, 1_000_000_000.0, None, 1_000_000_000.0)
    assert v is None
    assert any("no prior yet" in e.message for e in out.log), out.log

    # ** THE TWO STOCKS MUST BE DIFFERENCED OVER THE SAME WINDOW. ** They are fetched
    # independently, so their priors can land on different dates — and subtracting a 7-day
    # change from a 1-day change gives a number with no referent that still looks like a
    # release.
    v, out = run(605_000_000.0, 1_000_000_000.0, 600_000_000.0, 1_000_000_000.0,
                 dates={("Chainlink", "circulating_supply"): pd.Timestamp("2026-09-16"),
                        ("Chainlink", "total_supply"): pd.Timestamp("2026-09-22")})
    assert v is None, "different prior dates must refuse, not silently mix windows"
    assert any("DIFFERENT dates" in e.message and "NOTHING STORED" in e.message
               for e in out.log), out.log

    # AND THE GAP REASON SENDS THE READER TO THE INPUTS, not to write a scraper for a figure
    # nothing publishes.
    print("pool release ok: total-flat-circulating-up is all release, minting is zero, negative "
          "survives, and mismatched windows refuse")


def test_aerodromes_lock_duration_is_a_proxy_and_permanent_locks_come_off_both_sides():
    """** THE MODEL WAS CONFIRMED FROM SOURCE BEFORE IT WAS WIRED, AND IT HAD A TRAP IN IT. **

    VotingEscrow.sol line 26 states the decay is LINEAR with MAXTIME four years (line 549), and
    lines 603-604 give each position's weight as amount/MAXTIME x (end - now). So the
    amount-weighted mean remaining lock falls out of two aggregates.

    ** BUT BalanceLogicLibrary.supplyAt RETURNS `bias + permanentLockBalance`. ** A permanent
    position contributes its FULL amount to totalSupply for ever, so the naive
    totalSupply/locked ratio reports "nearly four years remaining" for locks with no end date at
    all — and the error runs in the direction that makes the protocol look most locked-in, which
    is the direction nobody questions. That is why the source was read rather than assumed.
    """
    from fetch import _derive_lock_duration
    from fetch.base import FetchOutput, LONG_COLUMNS

    spec = config.PROJECT_BY_NAME["Aerodrome"]["lock_duration_proxy"]
    assert spec["max_days"] == 1460 and spec["is_a_proxy"] is True
    assert "VotingEscrow.sol" in spec["source_url"]

    def run(vp, locked, perm, project="Aerodrome"):
        out = FetchOutput()
        rows = [{"date": pd.Timestamp("2026-09-23"), "project": project, "metric": m,
                 "value": v, "source": "chain:base:x", "tier": 2}
                for m, v in (("ve_voting_power_tokens", vp),
                             ("ve_locked_supply_tokens", locked),
                             ("permanent_locked_tokens", perm)) if v is not None]
        out.add(pd.DataFrame(rows)[LONG_COLUMNS], "test", project, "", 2)
        _derive_lock_duration(out, [config.PROJECT_BY_NAME[project]])
        got = out.frame()
        got = got[got.metric == "avg_lock_duration_days"]
        return (float(got.value.iloc[0]) if not got.empty else None), out

    # HALF THE MAXIMUM: 100m locked, none permanent, voting power exactly half -> 730 days.
    days, out = run(50_000_000.0, 100_000_000.0, 0.0)
    assert abs(days - 730.0) < 1e-6, days
    msg = [e.message for e in out.log if "avg_lock_duration_days=" in e.message][0]
    assert "PROXY, not a measurement" in msg, msg
    assert "no distribution can be read off it" in msg, msg

    # ** THE TRAP. ** 100m locked of which 60m is PERMANENT, and the decaying 40m averages half
    # its term. totalSupply = 60m (permanent, undecayed) + 20m (decayed bias) = 80m.
    #   naive   80/100 x 1460 = 1,168 days — nearly four years, and wrong
    #   correct (80-60)/(100-60) x 1460 = 730 days
    days, _ = run(80_000_000.0, 100_000_000.0, 60_000_000.0)
    assert abs(days - 730.0) < 1e-6, f"permanent locks must come off BOTH sides, got {days}"
    naive = 80_000_000.0 / 100_000_000.0 * 1460
    assert abs(naive - 1168.0) < 1e-6 and naive > days * 1.5, \
        "and the naive ratio is the failure this guards — 1,168 days against a true 730"

    # EVERYTHING PERMANENT IS A REAL STATE, NOT A FAILURE: nothing is counting down, so there is
    # no average to report and the reason says where to look instead.
    days, out = run(100_000_000.0, 100_000_000.0, 100_000_000.0)
    assert days is None
    assert any("decaying cohort is empty" in e.message and "permanent_locked_tokens" in e.message
               for e in out.log), out.log

    # ** A MISSING PERMANENT FIGURE DOES NOT FALL BACK TO ZERO. ** Treating absent as zero is
    # exactly the naive ratio, arrived at by omission instead of by choice.
    days, out = run(80_000_000.0, 100_000_000.0, None)
    assert days is None, "no figure may be produced without the permanent tranche"
    assert any("IS NOT OPTIONAL" in e.message for e in out.log), out.log

    # OUT OF THE CONTRACT'S OWN BOUND MEANS AN INPUT IS WRONG — nothing stored, nothing clipped.
    days, out = run(120_000_000.0, 100_000_000.0, 0.0)
    assert days is None
    assert any("NOTHING STORED and nothing clipped" in e.message for e in out.log), out.log

    # ===== ** THE REAL BLOCK, AND THE DENOMINATOR THAT WAS WRONG. Block 51,693,612. ** =====
    # The first live run divided by AERO.balanceOf(escrow) — what the escrow HOLDS — and got
    # 26,425 days, refused by the bound. The escrow's own accounting of what it has LOCKED is
    # `supply` (VotingEscrow.sol line 556), and the two differ by 59,653,709.90 AERO:
    #
    #     balanceOf  990,636,288.30   supply()  1,050,289,998.20
    #
    # Since 94% of the lock is permanent, the decaying cohort is a small difference between two
    # large numbers — so a 5.68% error in the denominator became 18.1x in the ratio. The bias is
    # computed against the LOCKED amount, so the ratio must be too.
    VP, SUPPLY, PERM = 1_026_940_797.62, 1_050_289_998.20, 988_513_136.74
    days, out = run(VP, SUPPLY, PERM)
    assert days is not None, f"the corrected denominator must STORE, not gap: {out.log}"
    assert abs(days - 908.2) < 0.1, f"expected ~908.2 days, got {days}"
    assert 0 <= days <= 1460, "and inside the contract's own bound"
    # ** THE OLD DENOMINATOR IS STILL REFUSED, which is what makes this a fix and not a widening
    # of the bound. ** Nothing about the 0-1460 range changed.
    BALANCE_OF = 990_636_288.30
    bad, out_bad = run(VP, BALANCE_OF, PERM)
    assert bad is None, f"balanceOf as the denominator must still refuse, got {bad}"
    assert any("NOTHING STORED and nothing clipped" in e.message for e in out_bad.log)

    # AND locked_tokens IS UNTOUCHED — it has six other consumers and none was in scope.
    assert config.PROJECT_BY_NAME["Aerodrome"]["lock_duration_proxy"]["locked"] == \
        "ve_locked_supply_tokens"
    ve = config.PROJECT_BY_NAME["Aerodrome"]["contracts"]["ve"]
    assert ve["read_method"] == "escrow_balance_of", \
        "locked_tokens must still be AERO.balanceOf(escrow)"
    assert config.contract_serves(ve) == {"locked_tokens"}

    # THE DISTRIBUTION IS FLAGGED AS THE NEXT STEP, NOT STARTED — it needs the per-NFT
    # enumeration this proxy exists to avoid.
    assert spec["next_step"]["status"].startswith("NOT STARTED")
    assert "per-NFT enumeration" in spec["next_step"]["cost"]
    print("aerodrome duration ok: linear decay confirmed from source, permanent tranche removed "
          "from both sides, and the naive ratio would have read 1,168 days against a true 730")


def test_pendles_locked_tokens_history_is_all_shares_and_the_0918_cliff_is_not_a_read_change():
    """** THE THREE-REGIME READING WAS WRONG, AND THE ARITHMETIC ONLY CLOSES ONE WAY. **

    locked_tokens was reported as assets to 2026-09-17, a silent switch to shares from 09-18,
    and assets again after the fix — with the -12.20% drop on 09-18 attributed to the reader
    changing under an unchanged source string. Checked against git instead of inferred from the
    numbers: contracts.spendle is byte-identical either side of the boundary, read_method has
    been erc20_total_supply continuously since 09-11 16:11, the adapter's escrow-vs-totalSupply
    dispatch is unchanged, and nothing else could write the metric. The measuring point never
    moved, so EVERY row is the share count and the cliff is a change in the QUANTITY.

    ** THE IDENTICAL SOURCE STRING IS A TRUE OBSERVATION WITH THE OPPOSITE MEANING. ** It is
    identical because nothing changed, not because a change slipped past it.

    This test pins the arithmetic, which is what makes the git evidence decisive rather than
    merely consistent: read regime 1 as shares and assets fall in step with shares; read it as
    assets and the share count has to RISE 4% through a week in which the stored series FELL
    12%. Both cannot describe one event.
    """
    d = config.PROJECT_BY_NAME["Pendle"]["non_comparable"]["locked_tokens"]
    cliff = d["discrepancy_2026_09_23"]["cliff_2026_09_18"]
    assert cliff["is_a_read_change"] is False
    assert cliff["change_pct"] == -12.20

    RATIO = config.PROJECT_BY_NAME["Pendle"]["lock_ratio"]["measured"]["ratio"]
    S17, S23, A23 = 34_162_882, 30_310_807.38, 35_557_548.09

    # Today's pair is internally consistent, which is what makes it a usable anchor at all.
    assert abs(S23 * RATIO - A23) / A23 < 1e-4, "shares x ratio must reproduce the measured assets"

    # READ REGIME 1 AS SHARES: assets then ~40.08m, falling ~11% to today — the same direction
    # and roughly the same size as the -12.2% fall in shares. One event, both series moving.
    as_shares = (A23 / (S17 * RATIO)) - 1.0
    assert -0.13 < as_shares < -0.09, as_shares

    # READ REGIME 1 AS ASSETS: shares then ~29.12m, RISING 4% to today — while the stored series
    # fell 12% over the same week. That needs the two to move in opposite directions through one
    # event, which is the reading this test exists to refuse.
    as_assets = (S23 / (S17 / RATIO)) - 1.0
    assert as_assets > 0.03, as_assets
    assert as_shares < 0 < as_assets, \
        "the two readings disagree in SIGN — that is the whole discriminator"

    # ** AND THE CURRENT WIRING IS THE ONLY WIRING THAT PRODUCES ASSETS. ** One contract maps to
    # locked_tokens and it is the balanceOf read; the totalSupply read maps to the share count.
    c = config.PROJECT_BY_NAME["Pendle"]["contracts"]
    assert config.contract_serves(c["spendle_underlying"]) == {"locked_tokens"}
    assert config.contract_serves(c["spendle"]) == {"locked_tokens_shares"}
    assert c["spendle_underlying"]["read_method"] == "escrow_balance_of"
    assert c["spendle"]["read_method"] == "erc20_total_supply"
    # The expected first post-fix reading travels with the entry, because locked_tokens has no
    # assets history to extrapolate from — the check is arithmetic, not a trend.
    assert "35,557,337" in c["spendle_underlying"]["note"]
    assert "near 30.3M means the fix did not take" in c["spendle_underlying"]["note"]
    print("pendle cliff ok: one series, all shares, and the two readings disagree in sign")


def test_a_stake_underlying_entry_with_no_read_method_is_refused_not_guessed():
    """THE CONTROL, and it is the half that matters more. A stake_underlying entry with no
    read_method does not FAIL — it silently becomes a totalSupply read on the holder, which
    returns the share count under an assets name. The symbol gate caught Pendle's only because
    the holder and the token happen to be different contracts with different tickers; where a
    vault IS its own underlying the symbol would match and the wrong quantity would be stored
    with nothing to notice.

    So the requirement is declared on the KIND rather than left to a symbol collision.
    """
    HOLDER, TOKEN = "0x9999999999", "0x8888888888"
    project = {
        "name": "Pendle", "archetypes": [3],
        "contracts": {
            "token": {"address": TOKEN, "chain": "ethereum", "kind": "erc20_total_supply",
                      "expected_symbol": "PENDLE", "source_url": "u", "verified": "2026-09-23"},
            "spendle_underlying": {
                "address": HOLDER, "chain": "ethereum", "kind": "stake_underlying",
                "expected_symbol": "PENDLE", "source_url": "u", "verified": "2026-09-23",
                "underlying": "token", "token_standard": "erc20",
                "metric_override": "locked_tokens"},          # <-- no read_method
        },
    }
    c = Chain()
    c.reader = _HolderTokenReader(HOLDER, TOKEN, "sPENDLE", "PENDLE", 30_310_807.38, 35_557_337.0)
    out = FetchOutput()
    c.run([project], None, out)

    assert out.frame()[out.frame().metric == "locked_tokens"].empty, \
        "nothing may be stored from an entry whose read method was never established"
    gap = [g for g in out.gaps if g["metric"] == "locked_tokens"]
    assert gap, out.gaps
    # AND THE REASON NAMES THIS HAZARD, not the vote-escrow one. A reason that points at the
    # wrong obstacle looks actionable, so somebody acts on it.
    assert "the WRONG QUANTITY rather than a failure" in gap[0]["reason"], gap[0]["reason"]
    assert "SHARE count" in gap[0]["reason"] and "ASSETS metric" in gap[0]["reason"]
    assert "ERC-721" not in gap[0]["reason"], "that is the vote-escrow hazard and not this one"
    assert "escrow_balance_of" in gap[0]["suggestion"]
    print("stake_underlying ok: the token answers for the symbol, the holder is the argument, "
          "and an unset read method is refused rather than silently becoming totalSupply")


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
    # ** NOW FOUND AND WRITTEN DOWN: 20,663,735 (2026-09-23). ** The discovery mechanism stays
    # declared — it is what found it, and it is what will find the next one — but a block number
    # established once does not change, and re-running a binary search on eth_getCode every run
    # spends calls to re-learn a constant.
    assert cfg["from_block"] == 20_663_735 and cfg["from_block_discover"] == "deployment"
    assert cfg["from_block_found_on"] == "2026-09-23"
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
# ** THE STAGE 2 BURNER IS THE PAUSE PROXY. ** Corrected 2026-09-23 from Sky's executive of
# 2026-09-11 ("Execute the Monthly Settlement Cycle for August 2026 ... burn SKY from the Pause
# Proxy balance"), executed 2026-09-13, with the 2.86M announced the next day. It is no longer
# discovered from the logs and no longer a separate address.
STAGE2 = PAUSE_PROXY
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


def test_sky_burns_are_decomposed_by_sender_and_the_pause_proxy_leg_is_stage_2():
    """** THE DECOMPOSITION HAD A CATEGORY THAT DOES NOT EXIST, AND IT HAD THE SIGN OF THE ERROR
    BACKWARDS. ** Corrected 2026-09-23.

    It split Pause Proxy burns off as "governance" — a one-off executive action, explicitly not
    to be annualised — and then went looking for a SEPARATE Stage 2 burner. There is not one.
    Sky's executive of 2026-09-11, executed 2026-09-13, reads "Execute the Monthly Settlement
    Cycle for August 2026 ... burn SKY from the Pause Proxy balance", and Sky announced the first
    2.86M the next day. The monthly executive IS the mechanism and the Pause Proxy IS where it
    burns from.

    So Transfer(pause_proxy -> 0x0) is the RECURRING, revenue-funded 5%-of-NPS leg — and the old
    classification called it the opposite. Both readings produce a number; only one of them is
    the demand signal, and nothing on a row of numbers says which you are looking at.

    Two series remain, not three: the Stage 2 leg, and everything else.
    """
    events = [
        {"from": PAUSE_PROXY, "value": int(2_860_000 * WAD), "block": 23_400_100},
        {"from": PAUSE_PROXY, "value": int(1_140_000 * WAD), "block": 23_450_000},
        {"from": CONVERTER, "value": int(250_000 * WAD), "block": 22_000_000},
    ]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(events)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    got = {r.metric: float(r.value) for r in out.frame().itertuples(index=False)}

    # THE PAUSE PROXY'S BURNS ARE THE STAGE 2 LEG, and land in the archetype 4 metric.
    assert got["burn_address_balance"] == 4_000_000.0, got
    # AND THERE IS NO "GOVERNANCE" SERIES ANY MORE. There was never a second category, only a
    # mislabelled first one.
    assert "governance_burn_balance" not in got, got
    # EVERYTHING NOT FROM THE PAUSE PROXY IS SURFACED, not folded in.
    assert got["other_burn_balance"] == 250_000.0, got
    # ** THE SUM IS NOT EITHER OF THEM, which is still the point. **
    assert got["burn_address_balance"] != sum(e["value"] for e in events) / WAD

    flagged = [r for r in out.review if r["reason"] == "unrecognised_burn_sender"]
    assert flagged and CONVERTER.lower() in flagged[0]["basis"].lower(), out.review
    print("sky decomposition ok: the Pause Proxy's 4.0m IS Stage 2, unrecognised 250k flagged, "
          "and the phantom governance category is gone")


def test_the_stage_2_burner_is_named_by_a_primary_source_and_the_discovery_is_retired():
    """** THE DISCOVERY WAS THE RIGHT ANSWER TO A QUESTION THAT IS NOW SETTLED. **

    While the burner's address was in no source on file, finding the event matching a known
    amount on a known date and taking its sender was the only honest route — and it refused
    unless exactly one candidate matched, because picking one of two would have put a whole
    series under an address nobody checked.

    Sky's executive of 2026-09-11 names it: the burn comes "from the Pause Proxy balance". So the
    address is primary-sourced, and the discovery is RETIRED rather than merely satisfied —
    leaving it armed would let a failed match REFUSE a read we can identify directly, which is a
    guard doing the opposite of its job.
    """
    cfg = config.PROJECT_BY_NAME["Sky"]["contracts"]["burn_logs"]["burn_logs"]
    assert cfg["stage2_burner"]["address"].lower() == PAUSE_PROXY.lower()
    assert cfg["stage2_burner"]["discover_by"] is None, "the discovery must not still be armed"
    assert "Pause Proxy balance" in cfg["stage2_burner"]["source_quote"]
    assert cfg["stage2_burner"]["discovery_retired_on"] == "2026-09-23"
    # THE ADDRESS IS THE ONE ALREADY VERIFIED ON THIS PROJECT, not a second copy of it.
    assert (cfg["stage2_burner"]["address"]
            == config.PROJECT_BY_NAME["Sky"]["contracts"]["pause_proxy"]["address"])
    # AND named_senders IS EMPTY — the Pause Proxy is named as the burner, not as a third party.
    # Two entries for one address is how a later edit changes one and not the other.
    assert cfg["named_senders"] == {}, cfg["named_senders"]

    # IT READS WITHOUT A DISCOVERY CALL. The event's sender is matched against config, and no
    # amount or date is used to identify anybody.
    one = [{"from": PAUSE_PROXY, "value": int(2_860_000 * WAD), "block": 23_400_100}]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(one)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    assert float(out.frame().query("metric == 'burn_address_balance'").value.iloc[0]) == 2_860_000.0

    # ** A BURN FROM SOMEBODY ELSE STILL DOES NOT BECOME STAGE 2. ** That is what the discovery's
    # refusal protected and it has to survive the discovery's removal: an unrecognised sender
    # goes to other_burn_balance and is flagged, never absorbed into the recurring leg.
    mixed = one + [{"from": CONVERTER, "value": int(2_870_000 * WAD), "block": 23_400_200}]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(mixed)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    got = {r.metric: float(r.value) for r in out.frame().itertuples(index=False)}
    assert got["burn_address_balance"] == 2_860_000.0, got
    assert got["other_burn_balance"] == 2_870_000.0, got
    assert [r for r in out.review if r["reason"] == "unrecognised_burn_sender"]
    print("burner ok: named from Sky's own executive, discovery retired, and a stranger's burn "
          "still cannot become the recurring leg")


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
    # ** NOW FOUND AND WRITTEN DOWN: 20,663,735 (2026-09-23). ** The discovery mechanism stays
    # declared — it is what found it, and it is what will find the next one — but a block number
    # established once does not change, and re-running a binary search on eth_getCode every run
    # spends calls to re-learn a constant.
    assert cfg["from_block"] == 20_663_735 and cfg["from_block_discover"] == "deployment"
    assert cfg["from_block_found_on"] == "2026-09-23"
    assert cfg["max_blocks_per_run"] is None, \
        "a full-history scan is the intent here, not the accident the ceiling guards against"

    reader = _LogReader([{"from": STAGE2, "value": int(2_860_000 * WAD), "block": 23_400_100}],
                        deployed=20_700_000)
    c = Chain(prior_values={}, prior_dates={})
    c.reader = reader
    out = FetchOutput()
    # ===== ** THE CONFIGURED from_block IS USED AND DISCOVERY IS SKIPPED. ** 20,663,735 was
    # found on 2026-09-23 and written down; a binary search on eth_getCode every run spends
    # calls to re-learn a constant. Discovery stays declared and is exercised below — it is what
    # found this one and what will find the next.
    c.run([_sky_log_probe()], None, out)
    assert "deployment" not in reader.asked, \
        "a known from_block must not re-run the deployment search"
    assert reader.asked["scan"]["from_block"] == 20_663_735, reader.asked

    # AND WITH IT UNSET, DISCOVERY RUNS — the mechanism is not dead code.
    r2 = _LogReader([{"from": STAGE2, "value": int(2_860_000 * WAD), "block": 23_400_100}],
                    deployed=20_700_000)
    c2 = Chain(prior_values={}, prior_dates={})
    c2.reader = r2
    out2 = FetchOutput()
    c2.run([_sky_log_probe(from_block=None)], None, out2)
    assert r2.asked["deployment"][1] == config.PROJECT_BY_NAME["Sky"]["contracts"]["token"]["address"]
    assert r2.asked["scan"]["from_block"] == 20_700_000, r2.asked
    assert reader.asked["scan"]["chunk"] == 10_000
    assert reader.asked["scan"]["burn_to"] == config.BURN_ADDRESSES["zero"]

    # THE SCANNED RANGE IS AN ANNOTATION, not a measuring point: it moves every run, and without
    # the stripper the series would blank itself for a change that never happened.
    from fetch.base import _measuring_point
    row = out.frame().query("metric == 'burn_address_balance'").iloc[0]
    assert "logs@20663735-" in row["source"], row["source"]
    assert _measuring_point(row["source"]) == "chain:ethereum:burn_logs", row["source"]
    # ** THE SIBLING SERIES NAME THEIR METRIC IN BRACKETS, AND THAT PLACEMENT IS THE POINT. **
    # One contract key emitting three parts has to disambiguate them, and the first version did it
    # with a fourth colon-delimited piece: chain:ethereum:burn_logs:governance_burn_balance. That
    # is the slot every key parser takes the CONTRACT KEY from, so all three of these series would
    # have rendered ORPHANED — "written by contract(s) governance_burn_balance, which are no
    # longer in config" — the day this read stopped 403ing. Fourth instance of that one bug.
    gov = out.frame().query("metric == 'other_burn_balance'").iloc[0]
    assert "[other_burn_balance]" in gov["source"], gov["source"]
    assert config.orphaned_contract_keys("Sky", gov["source"]) == [], gov["source"]
    # THE THREE SHARE A MEASURING POINT, which is correct and not a collision: they are three
    # metrics, and a measuring point is only ever compared against the same metric's own history.
    # What it must not do is MOVE, and the scanned block range moves every run — so the range
    # lives in brackets too, and the stripper is what keeps each series from blanking itself for
    # a change that never happened.
    assert _measuring_point(gov["source"]) == "chain:ethereum:burn_logs"
    assert _measuring_point(gov["source"]) == _measuring_point(
        "chain:ethereum:burn_logs[other_burn_balance][logs@1-2,deployed@1]")
    print("scan ok: starts at the derived deployment block, 10k chunks, range is an annotation")


def test_the_first_stage_2_read_is_gated_on_the_decomposed_figure_not_the_scan_total():
    """A full-history scan legitimately includes governance and converter burns, so the total is
    far above the 2,860,000 reference. Gating on the total would reject a correct read every run
    — which is why the gate moved onto the decomposed Stage 2 leg when the decomposition landed.
    """
    # ** THE CONTAMINATING BURN IS THE CONVERTER'S, NOT THE PAUSE PROXY'S. ** It was the Pause
    # Proxy's until 2026-09-23, when the Pause Proxy turned out to BE the Stage 2 burner — so
    # that pairing stopped separating the leg from the total and the case tested nothing.
    events = [
        {"from": PAUSE_PROXY, "value": int(2_860_000 * WAD), "block": 23_400_100},
        {"from": CONVERTER, "value": int(40_000_000 * WAD), "block": 23_380_000},
    ]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(events)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    # The total is 42.86m and the Stage 2 leg is 2.86m. The gate passes on the leg.
    assert float(out.frame().query("metric == 'burn_address_balance'").value.iloc[0]) == 2_860_000.0
    assert not [r for r in out.review if r["reason"] == "first_read_disagrees_with_reference"]

    # ** AND IT STILL REFUSES A STAGE 2 LEG THAT MISSES — the gate is narrowed, not switched off,
    # and that matters more now that the discovery is gone. ** Until 2026-09-23 a scan that began
    # too late was caught twice: by the burner discovery failing to find the 2.86M event, and by
    # this floor. The discovery has been retired, so this floor is now the ONLY thing standing
    # between a short scan and a confident, plausible, too-small cumulative.
    #
    # The Pause Proxy's leg is 41,000 here — the 2.86M sits with the converter, where it does not
    # count — so the leg is below its floor and NOTHING is stored.
    bad = [{"from": PAUSE_PROXY, "value": int(41_000 * WAD), "block": 23_400_100},
           {"from": CONVERTER, "value": int(2_860_000 * WAD), "block": 23_380_000}]
    c = Chain(prior_values={}, prior_dates={})
    c.reader = _LogReader(bad)
    out = FetchOutput()
    c.run([_sky_log_probe()], None, out)
    assert out.frame().query("metric == 'burn_address_balance'").empty
    gaps = [g for g in out.gaps if g["metric"] == "burn_address_balance"]
    assert gaps and "BELOW ITS FLOOR" in gaps[0]["reason"].upper(), [g["reason"][:80] for g in gaps]
    assert "Do NOT widen tolerance_pct" in gaps[0]["suggestion"].replace("do NOT", "Do NOT")
    print("gate ok: measured on the Stage 2 leg, passes with a large total, and is now the only "
          "thing catching a scan that began too late")


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


def test_a_stale_source_is_told_apart_from_a_stale_fetch():
    """** "last point 2026-09-10; last successful fetch 2026-09-22" SENDS THE READER TO THE WRONG
    PLACE. ** It reads as a pipeline that has fallen behind. For Ether.fi the fetch ran and
    SUCCEEDED — Dune query 8683038's own data ends 2026-09-10 and no re-run produces a newer
    point. That is the source ageing, and it needs a different action: chase the query, not the
    run.

    Derived from the two dates already on the row, never declared: a fetch that succeeded INSIDE
    the staleness window while the data did not move can only mean the source had nothing newer.
    """
    import build_workbook as B

    asof = pd.Timestamp("2026-09-22")
    long = pd.DataFrame([{"date": pd.Timestamp("2026-09-10"), "project": "Ether.fi",
                          "metric": "locked_tokens", "value": 100.0, "source": "dune:8683038",
                          "tier": 4, "is_manual": 0, "entered_on": "", "source_note": ""}])
    fresh = pd.DataFrame([{"source": "dune", "project": "Ether.fi",
                           "last_success_at": "2026-09-22"}])
    agg = B.aggregate(long, fresh, asof)
    row = agg[(agg.project == "Ether.fi") & (agg.metric == "locked_tokens")].iloc[0]
    assert row["status"] == "stale", row["status"]
    assert "THE SOURCE IS STALE, NOT THE FETCH" in row["note"], row["note"]
    assert "Chase the source, not the run" in row["note"]

    # ** AND A FETCH THAT REALLY HAS FALLEN BEHIND IS NOT RELABELLED. ** The distinction is only
    # drawn where the fetch itself is current; an old last_success means the run is the problem.
    old = pd.DataFrame([{"source": "dune", "project": "Ether.fi", "last_success_at": "2026-07-01"}])
    agg2 = B.aggregate(long, old, asof)
    row2 = agg2[(agg2.project == "Ether.fi") & (agg2.metric == "locked_tokens")].iloc[0]
    assert row2["status"] == "stale"
    assert "THE SOURCE IS STALE" not in row2["note"], row2["note"]
    print("staleness ok: a source that has stopped publishing reads differently from a fetch "
          "that has stopped running")


def test_pendles_fee_split_is_confirmed_from_its_own_docs_with_the_right_base():
    """** THE SHARE WAS DOCUMENTED ALL ALONG — ON A DIFFERENT PAGE. ** It was carried as
    undocumented, with programmed=False, because the tokenomics page does not state it. The
    sPENDLE page does:

      "80% of Pendle V2 fees from Yield and Swap Fees are allocated to PENDLE token buybacks.
       Up to 100% of repurchased PENDLE will be distributed to active sPENDLE holders in the
       form of sPENDLE."

    Two things in that sentence are easy to lose and both change the number:
      * the BASE is V2 YIELD AND SWAP fees. Secondary sources fold Boros in; the docs do not.
      * "UP TO 100%" is a ceiling on the DISTRIBUTION, not a floor, and not the 80%.
    """
    fs = config.PROJECT_BY_NAME["Pendle"]["fee_split"]
    assert fs["share_to_buyback"] == 0.80 and fs["programmed"] is True
    assert "sPENDLE" in fs["source_url"] and fs["source_date"] == "2026-09-23"
    assert fs["destination"] == "distribute"
    # THE BASE IS NAMED AND BOROS IS NAMED OUT OF IT.
    assert "YIELD and SWAP" in fs["base"] and "Boros" in fs["base"]
    # THE CEILING IS A CEILING. A floor of 1.0 here would assert something the docs do not say.
    assert fs["destination_ceiling"] == 1.00 and fs["destination_floor"] is None
    # AIRDROPS ARE NOT BUYBACK VOLUME — distributed in kind, never purchased.
    assert "IN KIND" in fs["airdrops_excluded"]

    # ** THE LEAD THAT PRECEDED IT WAS RIGHT ABOUT THE NUMBER AND WRONG ABOUT THE SENTENCE. **
    # Kept, because it is the case for holding an unsourced lead: acting on "80% of revenue to
    # holders" would have applied 0.80 to the wrong base and skipped the buyback step, and the
    # figure would have looked right the whole time.
    lead = fs["lead_80_20"]
    assert lead["status"].startswith("CONFIRMED 2026-09-23")
    assert "not the same base" in lead["status"] and "skipped the buyback step" in lead["status"]
    assert "the wrong base" in lead["kept_because"] and "wrong destination" in lead["kept_because"]

    # ** AND THE CADENCE MAKES IT LUMPY. ** Biweekly, buying across the following week — so a
    # 7-day window holds two weeks of buying or none, and neither is a change in the protocol.
    assert fs["cadence"]["period"] == "biweekly"
    assert config.level_break_windows("Pendle", "actual_buyback_tokens") == (30, 90)
    assert config.lumpy_flow("Pendle", "actual_buyback_usd") is not None

    # THE BUYBACK CONTRACT IS REPORTED ABSENT, NOT GUESSED AT. Pendle's own deployment file was
    # read in full and does not name one; a distributor is not a purchaser.
    reg = config.PROJECT_BY_NAME["Pendle"]["deployment_registry"]
    assert reg["buyback_contract"] is None
    assert "NOT IN THE FILE" in reg["buyback_contract_status"]
    assert reg["treasury"] == "0x8270400d528c34e1596EF367eeDEc99080A1b592"
    assert reg["s_pendle"] == config.PROJECT_BY_NAME["Pendle"]["contracts"]["spendle"]["address"]
    print("pendle ok: 0.80 of V2 yield+swap fees, programmed, distributed under a ceiling, "
          "biweekly and lumpy, and no buyback address invented")


def test_pendles_lock_discrepancy_does_not_fit_the_boost_hypothesis():
    """** THE OBVIOUS READING IS TO FILE THE 3x GAP UNDER THE VIRTUAL-BALANCE QUESTION, AND THE
    DIRECTION FORBIDS IT. **

    Reporting puts sPENDLE staking above 100,000,000 PENDLE by early July 2026. Our
    sPENDLE.totalSupply() read is 34,100,000. A BOOSTED balance would make totalSupply LARGER
    than the PENDLE behind it — so under that hypothesis real staked PENDLE is at or below
    34.1m, which is further from 100m, not closer.

    So the gap is recorded with three candidates and none chosen, including the cheap one that is
    easiest to forget because it needs no mechanism at all: the two figures are two and a half
    months apart, and the cooldown is 14 days.
    """
    nc = config.is_non_comparable("Pendle", "locked_tokens")
    d = nc["discrepancy_2026_09_23"]
    assert d["reported_tokens"] == 100_000_000 and d["read_tokens"] == 34_100_000
    assert 2.9 < d["ratio"] < 3.0
    # THE HYPOTHESIS IS EXPLICITLY REJECTED FOR THIS, and the old entry is not deleted — it is
    # still a live question about what totalSupply means, just not the explanation for this.
    assert d["boost_hypothesis_fits"] is False
    assert "further from 100m" in d["why_not"]
    assert "BOOSTED" in nc["why"], "the original virtual-balance question stays on file"
    # ALL THREE CANDIDATES ARE NAMED, INCLUDING UNSTAKING BETWEEN THE TWO DATES.
    assert any("unstaking" in c for c in d["candidates"]), d["candidates"]
    # ** CANDIDATE (a) IS SETTLED AND DID NOT EXPLAIN IT. ** H1 ran on 2026-09-23: sPENDLE
    # compounds at 1.1731 assets per share, so our read was the SHARES and the assets are 17.3%
    # larger — right direction, and 12.6% of supply against a reported 36%. Three of the four
    # candidates are now gone and the remaining two are load-bearing.
    sa = d["shares_vs_assets_settled"]
    assert sa["assets_per_share"] == 1.1731 and sa["closes_the_gap"] is False
    assert "12.6% of supply" in sa["why_not"]
    assert not any("shares-vs-assets" in c for c in d["candidates"]), "settled, so no longer open"
    # AND NEITHER FIGURE IS PREFERRED, with the cost of getting it wrong stated.
    assert "12% to 36%" in d["do_not"]
    print("pendle lock ok: the 3x gap is recorded with the boost hypothesis ruled OUT by "
          "direction, and unstaking named as the candidate nobody would think of")


def test_a_column_that_is_another_column_says_so_instead_of_sitting_empty():
    """** AN EMPTY CELL SAYS "WE COULD NOT FIND THIS", AND TWICE THAT WAS WRONG. **

    GEODNET's and Morpho's customer_revenue_usd both sat blank while the number they wanted was
    in fees_usd on the same row. GEODNET because DefiLlama computes its fees AS the on-chain
    burn / 0.8, which IS the gross end-user spend; Morpho because it is archetype 2, so fees_usd
    is fetched for it and has no column of its own.

    NOTHING IS COMPUTED. The series is copied, the source says which column it came from, and the
    caveat travels to the cell on the label — which is where the reader is.
    """
    from fetch import _restate_metrics
    from fetch.base import FetchOutput

    def run(project, rows):
        out = FetchOutput()
        out.frames = [pd.DataFrame(rows)]
        _restate_metrics(out, [config.PROJECT_BY_NAME[project]])
        return out

    fees = [{"date": pd.Timestamp("2026-09-20") + pd.Timedelta(days=i), "project": "GEODNET",
             "metric": "fees_usd", "value": 25_000.0 + i, "source": "defillama", "tier": 1}
            for i in range(3)]
    out = run("GEODNET", fees)
    got = out.frame().query("metric == 'customer_revenue_usd'")
    assert len(got) == 3
    assert list(got.value) == [25_000.0, 25_001.0, 25_002.0]
    assert str(got.source.iloc[0]) == "derived:=fees_usd", got.source.iloc[0]
    # THE CAVEAT REACHES THE CELL. A restated column that looks like an independent measurement
    # is worse than a blank one — a reader would compare it against fees_usd and find agreement
    # they think means something.
    label = config.metric_label("GEODNET", "customer_revenue_usd")
    assert "THE SAME SERIES AS fees_usd" in label and "burn / 0.8" in label
    assert "0.8706" in label, "the unreconciled split has to travel with it too"
    assert "THE SAME SERIES AS fees_usd" in config.metric_label("Morpho", "customer_revenue_usd")
    # AND NOBODY ELSE'S LABEL MOVES.
    assert config.metric_label("Uniswap", "customer_revenue_usd") == "End-user revenue"

    # A MEASURED FIGURE WINS, and nothing is restated over it.
    both = run("GEODNET", fees + [{"date": pd.Timestamp("2026-09-20"), "project": "GEODNET",
                                   "metric": "customer_revenue_usd", "value": 1.0,
                                   "source": "scrape:x", "tier": 3}])
    assert len(both.frame().query("metric == 'customer_revenue_usd'")) == 1
    assert [e for e in both.log if e.status == "skipped" and "already has a figure" in e.message]

    # NO SOURCE COLUMN MEANS NO SEPARATE GAP — the reader is sent to the one row that matters.
    empty = run("GEODNET", [{"date": pd.Timestamp("2026-09-20"), "project": "GEODNET",
                             "metric": "price_usd", "value": 0.1, "source": "coingecko", "tier": 1}])
    assert empty.frame().query("metric == 'customer_revenue_usd'").empty
    assert [e for e in empty.log if "Not a separate gap: see fees_usd" in e.message]
    print("restatement ok: the column is filled from the column it is, and the caveat is on the "
          "label rather than in config")


def test_etherfis_two_addresses_have_two_roles_and_neither_is_read_as_a_balance():
    """** THE MISMATCH WAS REAL AND THE READING OF IT WAS WRONG. ** These are not two candidates
    for one label — they are two contracts doing two jobs, and three independent sources agree:

      (1) DefiLlama's adapter: eETH withdrawal fees go to 0x2f5301a3..., misc staking revenue
          (eETH, EIGEN) to 0x0c83EA...
      (2) Ether.fi's OWN test suite, test/TestSetup.sol:294, declares 0x2f5301a3... as
          `buybackWallet` — found in an earlier round and PARKED as an unverified lead, which is
          what leads are for.
      (3) Ether.fi's governance gitbook: 100% of eETH withdrawal fee revenue funds the weekly
          ETHFI buybacks.

    (1) and (3) are the same claim from two directions; (2) names the contract. The adapter's
    loose "to the treasury" was describing a buyback wallet.
    """
    p = config.PROJECT_BY_NAME["Ether.fi"]
    assert "treasury_candidates" not in p, "the ambiguity is resolved, not still recorded as one"
    bw, tr = p["buyback_wallet"], p["treasury"]
    assert bw["address"] == "0x2f5301a3D59388c509C65f8698f521377D41Fd0F"
    assert tr["address"] == "0x0c83EAe1FE72c390A02E426572854931EefF93BA"
    assert bw["address"] != tr["address"]
    assert len(bw["sources"]) == 3, "three independent sources is the standard that was met"
    assert any("TestSetup.sol" in x for x in bw["sources"])
    assert any("gitbook" in x for x in bw["sources"])

    # ** AND THE BUYBACK WALLET'S BALANCE IS NOT actual_buyback_tokens. ** Third time this round,
    # after NEAR's revenue wallets and Plume's fee receiver: a wallet that exists to SPEND has a
    # balance delta of inflow MINUS spending, so it understates the buyback by the buyback.
    assert "understates the buyback by the buyback" in bw["not_wired_as_balance"]
    assert "log scan" in bw["route_that_would_work"] and "not a balance read" in bw["route_that_would_work"]
    # NEITHER IS DECLARED AS A CONTRACT, so nothing reads either one yet.
    assert not any(c.get("address", "").lower() in (bw["address"].lower(), tr["address"].lower())
                   for c in p["contracts"].values()), \
        "recorded, not wired — what the treasury HOLDS has not been read"
    print("ether.fi ok: buyback wallet and treasury told apart by three sources, and the "
          "buyback wallet's balance is explicitly not the buyback")


def test_spendle_is_ethereum_only_so_the_multichain_candidate_is_ruled_out():
    """** THE ONLY CANDIDATE POINTING THE RIGHT WAY FOR A 3x GAP, AND IT IS WRONG. **

    Pendle runs on fourteen chains, so an Ethereum-only totalSupply read missing the rest would
    understate by construction — which is exactly the shape a 34.1m read against a 100m report
    needs. The boost hypothesis points the other way; this one did not.

    Ten chains' deployment files were read on 2026-09-23. sPendle appears in ONE: Ethereum. Every
    other chain carries the bridged PENDLE token and a DEPRECATED vePendle. So locked_tokens is
    COMPLETE, not partial — and the cheap explanation for the 3x is gone, which makes the
    remaining candidates more likely rather than less.
    """
    d = config.is_non_comparable("Pendle", "locked_tokens")["discrepancy_2026_09_23"]
    mc = d["multichain_ruled_out"]
    assert mc["spendle_found_on"] == (1,), mc
    assert len(mc["chains_answered"]) >= 9 and 42161 in mc["chains_answered"]
    assert "COMPLETE, not partial" in mc["verdict"]
    # ** BUT vePENDLE REALLY WAS MULTI-CHAIN, and that sharpens a different candidate. ** A
    # reported "100m staked" that predates or straddles the migration could be summing vePENDLE
    # across chains, which is not the same quantity as sPENDLE on Ethereum.
    assert "deprecated" in mc["but_vependle_was_multichain"].lower()
    assert "0x3209E9412" in mc["but_vependle_was_multichain"]
    # THE OTHER THREE CANDIDATES SURVIVE UNCHANGED — ruling one out is not choosing another.
    assert len(d["candidates"]) == 2
    assert d["boost_hypothesis_fits"] is False
    print("pendle ok: sPENDLE is Ethereum-only, so locked_tokens is complete and the one "
          "candidate that pointed the right way is ruled out")


def test_morphos_own_api_writes_nothing_until_a_live_run_confirms_it():
    """** CONFIRMED ON THE LISTED SUBSET, AND THE ROUTE TO GETTING THERE IS WHAT THIS PINS. **

    Morpho publishes per-market supplyAssetsUsd and borrowAssetsUsd — the figures morpho-blue's
    TVL adapter reads and discards. Summed, that is utilisation with NO collateral in the
    denominator: the figure itself rather than a labelled approximation.

    THE FIRST LIVE RUN SUMMED THE WHOLE PERMISSIONLESS POPULATION: 7,868 markets, $39.47bn at
    98.1% utilisation. Filtered to whitelisted markets it is 651 markets, $5.87bn, 0.8802 — and
    every listed market individually sits at 0.89-0.90. The bar was never "it fetched": it was
    "the number is explained", and the explanation is that 92% of the market count and 85% of
    the raw supply is unlisted, with 78% of the total in four markets whose supply equals their
    borrow to the dollar.
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama, MorphoBlueApi

    api = config.PROJECT_BY_NAME["Morpho"]["lending_api"]
    assert api["status"] == "confirmed", "the listed subset was confirmed on 2026-09-23"
    assert api["confirmed_values"] == {"supply_units": 5_868_948_998.0,
                                       "utilisation_pct": 0.8802, "listed_markets": 651}
    lo, hi = api["plausible_utilisation"]
    assert lo <= api["confirmed_values"]["utilisation_pct"] <= hi, \
        "the confirmed figure must sit inside the band that refused the unfiltered one"
    assert api["endpoint"] == "https://blue-api.morpho.org/graphql"
    assert "DefiLlama" in api["schema_evidence"], "the supply field's provenance is named"
    assert "measuring-point change" in api["on_confirm"]

    # ** THE FILTER IS IN THE QUERY, NOT APPLIED AFTERWARDS. ** Post-filtering would mean the
    # $39.47bn is summed at least once in memory, one edit from being the number that ships.
    assert "listed:true" in api["markets_query"].replace(" ", ""), api["markets_query"]
    assert api["filter_field"] == "listed" and api["verify_field"] == "listed"
    # ===== ** AND `whitelisted` IS WRONG, THOUGH SIX REPOSITORIES USE IT. ** The API answered:
    # 'Field "whitelisted" is not defined by type "MarketFilters". Did you mean "listed"?'
    # The corroboration was real and still wrong — whitelisted is the filter on the `vaults`
    # type, not on `markets`. Six agreeing SECONDARY sources are still secondary, and the schema
    # was one call away. Pinned so the reasoning is not redone and the field not reverted.
    assert "whitelisted" not in api["markets_query"], api["markets_query"]
    rej = api["rejected_field_2026_09_23"]
    assert rej["field"] == "whitelisted" and "MarketFilters" in rej["why_it_was_wrong"]
    assert "vaults" in rej["why_it_was_wrong"]
    # ** IT WAS CAUGHT BY AN ERROR, NOT BY A WRONG NUMBER ** — which is what putting the filter
    # in the query rather than post-filtering bought.
    assert "by an error" in rej["how_it_was_caught"]

    class Stub:
        def __init__(self, borrow_key="borrowAssetsUsd", listed=True):
            self.borrow_key, self.listed = borrow_key, listed

        def post(self, url, json_body=None, **kw):
            if "chains" in (json_body or {}).get("query", ""):
                return {"data": {"chains": [{"id": 1}, {"id": 8453}]}}
            st = {"supplyAssetsUsd": 600.0, self.borrow_key: 400.0}
            return {"data": {"markets": {"pageInfo": {"countTotal": 1},
                                         "items": [{"marketId": "0xa", "chain": {"id": 1},
                                                    "listed": self.listed, "state": st}]}}}

    def run(project, stub):
        a = MorphoBlueApi()
        a.http = stub
        out = FetchOutput()
        a.run([project], None, out)
        return out

    morpho = config.PROJECT_BY_NAME["Morpho"]
    unconfirmed = dict(morpho, lending_api=dict(api, status="unconfirmed"))

    # THE UNCONFIRMED STATE STILL WORKS — it is how the next route will be judged.
    out = run(unconfirmed, Stub())
    assert out.frame().empty, out.frame()
    msg = [e.message for e in out.log if e.status == "skipped"]
    assert msg and "THE FETCH PARSED" in msg[0], out.log
    assert "utilisation_pct=0.6667" in msg[0], msg[0]
    assert "stands DefiLlama's down" in msg[0]
    assert "Parsing is NOT the bar" in msg[0], msg[0]

    # ===== ** A FILTER THE SERVER IGNORES IS NOT AN ERROR — IT IS A 200 CARRYING $39.47bn. **
    # This is the one failure mode that would silently put the unfiltered population back, so
    # every returned row is asked to confirm it is listed, and one that is not stops the read.
    ignored = run(morpho, Stub(listed=False))
    assert ignored.frame().empty, "nothing may be stored when the filter did not apply"
    fmsg = [e.message for e in ignored.log if e.status == "failed"]
    assert fmsg and "THE LISTED FILTER DID NOT APPLY" in fmsg[0], ignored.log
    assert "NOTHING STORED" in fmsg[0] and "listed:true" in fmsg[0], fmsg[0]

    # ===== THE PARTIAL-FIELD PROBLEM, AND THE TWO ABSENCES THAT ARE NOT THE SAME THING. =====
    # BOTH FIELDS ABSENT is an empty market: it contributes nothing to either side, so dropping
    # it and summing it as zero are the same arithmetic. The probe settled this on the real
    # population — of 1,117 markets with no borrowAssetsUsd, ZERO carried a supply figure.
    assert api["partial_field_2026_09_23"]["of_those_carrying_supply"] == 0

    class WithEmpties(Stub):
        def post(self, url, json_body=None, **kw):
            if "chains" in (json_body or {}).get("query", ""):
                return {"data": {"chains": [{"id": 1}]}}
            items = [{"marketId": "0xa", "chain": {"id": 1}, "listed": True,
                      "state": {"supplyAssetsUsd": 600.0, "borrowAssetsUsd": 400.0}},
                     {"marketId": "0xb", "chain": {"id": 1}, "listed": True, "state": {}}]
            return {"data": {"markets": {"pageInfo": {"countTotal": 2}, "items": items}}}

    ok = run(morpho, WithEmpties())
    vals = {r.metric: float(r.value) for r in ok.frame().itertuples(index=False)}
    assert vals["supply_units"] == 600.0 and abs(vals["utilisation_pct"] - 2 / 3) < 1e-9, vals
    assert any("empty market(s) carried neither field" in e.message for e in ok.log), ok.log

    # ** ONE PRESENT AND THE OTHER ABSENT STILL REFUSES THE WHOLE READ. ** Not observed on the
    # real population, and the guard is kept because the day it happens there is no safe
    # default — and the error would run in the direction of a too-low utilisation, which is the
    # number this route exists to get right.
    class Lopsided(Stub):
        def post(self, url, json_body=None, **kw):
            if "chains" in (json_body or {}).get("query", ""):
                return {"data": {"chains": [{"id": 1}]}}
            items = [{"marketId": "0xa", "chain": {"id": 1}, "listed": True,
                      "state": {"supplyAssetsUsd": 600.0, "borrowAssetsUsd": 400.0}},
                     {"marketId": "0xbad", "chain": {"id": 1}, "listed": True,
                      "state": {"supplyAssetsUsd": 900.0}}]
            return {"data": {"markets": {"pageInfo": {"countTotal": 2}, "items": items}}}

    lop = run(morpho, Lopsided())
    assert lop.frame().empty, "one side present without the other must stop the whole read"
    lmsg = [e.message for e in lop.log if e.status == "failed"]
    assert lmsg and "carry ONE of" in lmsg[0], lop.log
    assert "not knowable from the absence itself" in lmsg[0], lmsg[0]

    # ===== THE FINDINGS THAT JUSTIFIED CONFIRMING, recorded because they are facts about the
    # ENDPOINT and not about our use of it: anyone summing it unfiltered gets ~85% not-Morpho.
    split = api["listed_vs_unlisted_2026_09_23"]
    assert split["listed"]["markets"] == 651 and split["unlisted"]["markets"] == 7_217
    assert split["unlisted"]["utilisation"] == 0.9989, "self-dealt markets hold no idle liquidity"
    assert len(split["four_markets_are_78pct_of_the_total"]) == 5, "four markets plus the signature"
    assert "EXACTLY equal" in split["four_markets_are_78pct_of_the_total"]["signature"]

    # ** AND THE TVL CROSS-CHECK IS INAPPLICABLE, NOT UNMET. ** It was written as the decider
    # and it cannot decide: DefiLlama's tvl counts loanToken AND collateralToken while
    # supplyAssetsUsd is the loan side alone, so no ratio between them says anything about the
    # filter. Left on file as an unmet condition it reads as work outstanding, and someone
    # eventually makes two different quantities agree by adjusting the one that is right.
    assert "INAPPLICABLE" in api["tvl_cross_check"]["status"]
    assert "Do not reinstate it as a gate" in api["tvl_cross_check"]["so"]

    # ===== ** A CLEAN FETCH THAT SUMS TO NONSENSE MUST NOT READ AS A SUCCESS. ** The first live
    # run came back with 7,868 markets, $39.47bn supplied and $38.73bn borrowed — 98.1%
    # utilisation, which no lending protocol runs at — and the old message called that "IT
    # WORKED" and invited a human to flip the flag. All it had established was that the field
    # NAMES were right. So a reported utilisation outside the plausible band says plainly not to
    # confirm, and it still stores nothing either way.
    assert api["plausible_utilisation"] == (0.40, 0.92), api.get("plausible_utilisation")
    first = api["first_run_2026_09_23"]
    assert first["implied_utilisation"] == 0.981
    assert "PERMISSIONLESS" in first["leading_hypothesis"]
    assert "API_MIN_USD = 1000" in first["supporting_evidence"] and \
        "listed === true" in first["supporting_evidence"], "DefiLlama's own filter is the evidence"

    class Nonsense(Stub):
        def post(self, url, json_body=None, **kw):
            if "chains" in (json_body or {}).get("query", ""):
                return {"data": {"chains": [{"id": 1}]}}
            st = {"supplyAssetsUsd": 39_470_000_000.0, "borrowAssetsUsd": 38_730_000_000.0}
            # listed TRUE deliberately: the band must fire on its own merits, not because the
            # filter guard happened to reject the row first.
            return {"data": {"markets": {"pageInfo": {"countTotal": 1},
                                         "items": [{"marketId": "0xa", "chain": {"id": 1},
                                                    "listed": True, "state": st}]}}}

    bad = run(unconfirmed, Nonsense())
    assert bad.frame().empty, "still nothing stored — the band reports, it does not filter"
    bmsg = [e.message for e in bad.log if e.status == "skipped"]
    assert bmsg and "DO NOT CONFIRM ON THIS" in bmsg[0], bmsg
    assert "0.9813" in bmsg[0] and "outside the plausible 0.40-0.92 band" in bmsg[0], bmsg[0]
    assert "Parsing is NOT the bar" not in bmsg[0], "the invitation must not survive alongside it"

    # CONFIRMED, SO IT WRITES, and the row says the bias is gone rather than merely named.
    confirmed = morpho
    out2 = run(confirmed, Stub())
    got = {r.metric: float(r.value) for r in out2.frame().itertuples(index=False)}
    assert got["supply_units"] == 600.0 and abs(got["utilisation_pct"] - 2 / 3) < 1e-9, got
    assert any("NO COLLATERAL in the denominator" in e.message for e in out2.log)

    # ** AND THE TWO ROUTES NEVER ALTERNATE. ** Two sources taking turns on one column is a
    # measuring-point change, which blanks the series — the failure this file has now recorded
    # three times. Once confirmed, DefiLlama's lending route stands down COMPLETELY, not
    # "unless the API fails".
    d = DefiLlama()
    d.http = object()          # never called: the stand-down happens before any request
    out3 = FetchOutput()
    d.lending_supply(confirmed, None, out3)
    assert out3.frame().empty
    assert [e for e in out3.log if e.status == "skipped" and "must not alternate" in e.message]

    # ** A MISSPELT BORROW FIELD IS REPORTED, NEVER SUMMED AS ZERO. ** A borrow side reading 0
    # gives utilisation 0.0000 on a lending protocol — a number, not a gap, and entirely
    # plausible on a quiet day. It reaches the lopsided branch, which is exactly right: the
    # market HAS a supply figure and its borrow side is unreadable, which is the one case with
    # no safe default.
    out4 = run(confirmed, Stub(borrow_key="borrowAssetsUSD"))
    assert out4.frame().empty
    bad = [e for e in out4.log if e.status == "failed"]
    assert bad and "carry ONE of" in bad[0].message, out4.log
    assert "borrowAssetsUsd" in bad[0].message, "the expected spelling is named"
    assert "understates utilisation" in bad[0].message, \
        "the reason names the direction of the error it refuses to make"
    print("morpho api ok: reports what it would write and stores nothing until confirmed, "
          "stands DefiLlama's route down when it is, and refuses a missing borrow field")


def test_morphos_utilisation_is_stored_with_its_bias_named_not_left_blank():
    """** THE REFUSAL OF 2026-09-22 FOUND SOMETHING REAL, AND THE ANSWER CHANGED ANYWAY. **

    DefiLlama cannot give clean utilisation. From projects/morpho-blue/index.js, the tvl function
    builds its token list from BOTH loanToken AND collateralToken and sums the Morpho Blue
    singleton's balance of each — and Morpho Blue custodies collateral. Total supplied is idle
    loan tokens plus borrowed; tvl + borrowed adds collateral on top.

    So the column is populated from DefiLlama and the bias is declared rather than the cell left
    empty. What makes that acceptable is the DIRECTION: the denominator is too large, so
    utilisation reads too SMALL, always. A figure whose error has a known sign can be reasoned
    about. The finding is not withdrawn — it is on the label, on non_comparable, and in
    utilisation_pct_blocked, which still names the exact route.
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    spec = config.PROJECT_BY_NAME["Morpho"]["lending_supply"]
    assert spec["utilisation_formula"] == "borrowed / (tvl + borrowed)"
    assert "UNDERSTATES" in spec["bias"] and "collateral" in spec["bias"]

    # ===== ** AND THE CAVEAT STOPPED TRAVELLING TO THE CELL WHEN THE ROUTE STOOD DOWN. ** =====
    # Confirming blue-api on 2026-09-23 removed the collateral denominator entirely, so
    # "THE DENOMINATOR INCLUDES COLLATERAL" became a false statement about a true figure. A
    # warning that is wrong still gets acted on, and the action here is to mentally adjust a
    # correct number upwards. The RECORD is kept — it says what the old route measured and what
    # replaced it — and it returns automatically if the confirmation is reverted, because both
    # are driven by the same flag.
    # ===== ** THE ROW'S OWN SOURCE RETIRES THE CAVEAT — AND KEYING IT ON A CONFIG FLAG WAS A
    # ** BUG SHIPPED ON 2026-09-23. ** The first version suppressed it whenever
    # lending_api.status was "confirmed". Confirming a route and having it WRITE are different
    # things: blue-api then failed on a wrong filter field, nothing new was stored, DefiLlama
    # had already stood down, and the store's older DefiLlama rows rendered with the caveat
    # suppressed by the flag. utilisation_pct showed 0.3231 — the collateral-inflated figure —
    # as `ok` with nothing on it, while supply_units gapped. One route, two different fates.
    #
    # A flag describes an intention; a source string describes the row in front of you.
    raw = config.PROJECT_BY_NAME["Morpho"]["non_comparable"]
    for metric in ("utilisation_pct", "supply_units"):
        assert "COLLATERAL" in raw[metric]["why"].upper(), metric
        assert raw[metric]["superseded_by_source"] == "morpho_api", metric
        # A row from the new route carries no collateral, so no caveat.
        assert config.is_non_comparable("Morpho", metric, "morpho_api:markets") is None, metric
        # ** A DefiLlama ROW KEEPS IT FOR EVER, WHATEVER THE FLAG SAYS. ** This is the assertion
        # the flag version could not make.
        nc = config.is_non_comparable("Morpho", metric, "defillama")
        assert nc and "COLLATERAL" in nc["why"].upper(), \
            f"{metric}: a stored DefiLlama row is still collateral-inflated"
        # AN UNKNOWN SOURCE KEEPS IT TOO. A stale caveat is a nuisance; a missing one on a
        # biased figure is what this field exists to prevent.
        assert config.is_non_comparable("Morpho", metric) is not None, metric
    # ** AND THE TWO METRICS CANNOT DIVERGE, because each row answers for itself. **
    for src in ("defillama", "morpho_api:markets", None):
        verdicts = {m: config.is_non_comparable("Morpho", m, src) is None
                    for m in ("utilisation_pct", "supply_units")}
        assert len(set(verdicts.values())) == 1, f"{src}: one route, two fates — {verdicts}"
    # A caveat with no supersession key is untouched by any of this.
    assert config.is_non_comparable("Uniswap", "buyback_fund_balance") is not None
    assert config.is_non_comparable("Uniswap", "buyback_fund_balance", "morpho_api") is not None
    # THE EXACT ROUTE IS STILL ON FILE, and still honest about its cost.
    blocked = config.PROJECT_BY_NAME["Morpho"]["utilisation_pct_blocked"]
    assert "superseded" in blocked["status"]
    assert "totalBorrowAssets / totalSupplyAssets" in blocked["wanted"]
    assert "NOT started without a decision" in blocked["route_that_would_work"]

    class Stub:
        def get(self, url, params=None):
            return {"tvl": [{"date": 1_758_000_000, "totalLiquidityUSD": 600.0},
                            {"date": 1_758_086_400, "totalLiquidityUSD": 700.0}],
                    "chainTvls": {"borrowed": {"tvl": [
                        {"date": 1_758_000_000, "totalLiquidityUSD": 400.0},
                        {"date": 1_758_086_400, "totalLiquidityUSD": 300.0}]}}}

    # ** THE ROUTE ITSELF IS EXERCISED AGAINST AN UNCONFIRMED COPY, because on the real project
    # it now stands down entirely — that stand-down is asserted in the blue-api test. What is
    # pinned here is what it DID and why it was replaced, which is not withdrawn.
    legacy = dict(config.PROJECT_BY_NAME["Morpho"],
                  lending_api=dict(config.PROJECT_BY_NAME["Morpho"]["lending_api"],
                                   status="unconfirmed"))
    d = DefiLlama()
    d.http = Stub()
    out = FetchOutput()
    d.lending_supply(legacy, None, out)
    got = out.frame()
    units = got.query("metric == 'supply_units'").sort_values("date")
    util = got.query("metric == 'utilisation_pct'").sort_values("date")
    assert list(units.value) == [1000.0, 1000.0], list(units.value)
    assert list(util.value) == [0.4, 0.3], list(util.value)
    # THE CAVEAT IS ON THE ROW THAT LANDS IN THE RUN LOG TOO, not only in config.
    assert any("DENOMINATOR INCLUDES COLLATERAL" in e.message for e in out.log), out.log

    # ** THE TWO SERIES ARE PAIRED BY DATE, NEVER BY POSITION. ** Aligning two series on their
    # index rather than their date is how a borrowed figure gets divided by the wrong day's tvl,
    # and the result looks entirely reasonable.
    class Ragged(Stub):
        def get(self, url, params=None):
            j = Stub.get(self, url, params)
            j["chainTvls"]["borrowed"]["tvl"] = [{"date": 1_758_086_400, "totalLiquidityUSD": 300.0}]
            return j

    d2 = DefiLlama()
    d2.http = Ragged()
    out2 = FetchOutput()
    d2.lending_supply(legacy, None, out2)
    u2 = out2.frame().query("metric == 'utilisation_pct'")
    assert len(u2) == 1 and float(u2.value.iloc[0]) == 0.3, u2

    # A RESPONSE WITHOUT THE BORROW SERIES NAMES THE KEYS IT DID CARRY, so the next run's log
    # says what shape arrived rather than only that the expected one did not.
    class NoBorrow:
        def get(self, url, params=None):
            return {"tvl": [], "chainTvls": {"Ethereum": {}, "staking": {}}}

    d3 = DefiLlama()
    d3.http = NoBorrow()
    out3 = FetchOutput()
    d3.lending_supply(legacy, None, out3)
    assert out3.frame().empty
    assert [e for e in out3.log if e.status == "failed" and "Keys present: Ethereum, staking" in e.message], out3.log
    print("morpho capacity ok: stored with the collateral bias named and its direction stated, "
          "paired by date, and a missing borrow series names what arrived instead")


# ---------------------------------------------- World Mobile's issuance, from the curve
def test_world_mobiles_curve_is_a_cross_check_because_it_has_no_launch_date_to_test_against():
    """** THE MODEL'S ONE INDEPENDENT TEST HAD NOTHING TO TEST AGAINST. **

    The whitepaper's rate law integrates to S(t) = S0(t+1)^k with S0 = 2bn/21^k, and the model is
    invertible, so the missing emission start date looked like an OUTPUT rather than a missing
    input: total_supply_gross = 1,714,232,116 puts t at 4.44 years, implying emission began
    around 2022-04-16. That was offered as the one independent check on the parameterisation.

    WORLD MOBILE HAS NO SUCH DATE. The Cardano-era mainnet was PLANNED for Q3 2022 and re-planned
    for Q1 2023; the Chain's public testnet was 2025-03, a permissioned Developer Mainnet 2025-06,
    and public mainnet is still phasing through 2026. Not one of those is 2022-04, and more to
    the point there is no single t0 at all.

    A test that cannot be run is not a test — so the curve stops writing the column and becomes a
    cross-check. emissions_tokens comes from OBSERVED MINTING, which needs no t0. The curve still
    runs and still reports its implied t, because the two disagreeing is worth seeing.
    """
    from fetch import _derive_curve_issuance
    from fetch.base import FetchOutput

    def run(supply, metric_rows=()):
        rows = [{"date": pd.Timestamp("2026-09-22"), "project": "World Mobile",
                 "metric": "total_supply_gross", "value": supply,
                 "source": "chain:sum(...)", "tier": 2}] + list(metric_rows)
        out = FetchOutput()
        out.frames = [pd.DataFrame(rows)]
        _derive_curve_issuance(out, [config.PROJECT_BY_NAME["World Mobile"]])
        return out

    curve = config.issuance_curve("World Mobile")
    assert curve["role"] == "cross_check" and curve["demoted_on"] == "2026-09-23"
    assert "no single t0" in curve["why_demoted"]
    assert "d(total_supply_gross)" in curve["issuance_route_instead"]

    out = run(1_714_232_116.0)
    # ** NOTHING IS WRITTEN. ** That is the change: a cross-check that also fills the column is
    # not a cross-check, it is the route wearing a different word.
    assert out.frame().query("metric == 'emissions_tokens'").empty, out.frame()
    flags = [r for r in out.review if r["reason"] == "curve_cross_check"]
    assert len(flags) == 1, out.review
    b = flags[0]["basis"]
    assert "WRITES NOTHING" in b
    # THE ARITHMETIC IS STILL REPORTED IN FULL, because that is what makes it a cross-check.
    assert "t=4.44y" in b and "2.0986%/yr" in b, b
    assert "35,974,3" in b, b
    assert "Cardano-era mainnet was planned for Q3 2022" in b, "the reason travels with the row"
    assert "no single t0" in b
    assert [e for e in out.log if e.status == "skipped" and "CROSS-CHECK, not the route" in e.message]

    # THE MATHS IS UNCHANGED AND STILL ASSERTED — demoting the role must not quietly break it.
    s0 = config.issuance_curve_s0(curve)
    assert abs(s0 - 1_413_073_572.18) < 1.0, s0
    assert 0.29 <= (curve["cap"] - s0) / curve["cap"] <= 0.30

    # ** BELOW THE CURVE'S OWN ORIGIN IT STILL REFUSES RATHER THAN CLAMPING, ** and that has to
    # survive the demotion: a cross-check reporting a clamped t would be worse than silence.
    low = run(1_000_000_000.0)
    assert not [r for r in low.review if r["reason"] == "curve_cross_check"]
    assert [g for g in low.gaps if "at or below the model's own origin" in g["reason"]]
    print("world mobile ok: the curve reports its implied t and writes nothing, because the "
          "launch date it was going to be tested against does not exist")


def test_world_mobiles_emissions_come_from_observed_minting_not_from_the_model():
    """** THE COLUMN NEEDED A ROUTE THAT DOES NOT NEED A LAUNCH DATE. ** The whitepaper's curve
    integrates cleanly and reproduces its own 29% target, and it still cannot be anchored —
    World Mobile has no single t0. A measurement needs none: the contracts' own totalSupply moved
    or it did not.

    AND THE PARTIALITY NOW HAS A DIRECTION, which the model's did not. total_supply_gross sums
    the EVM deployments and excludes Cardano, so a mint there is invisible and the figure can
    only be too SMALL. The curve's error could go either way, because understating supply
    understated t, which raised the rate and lowered the base.
    """
    from fetch import _derive_observed_minting
    from fetch.base import FetchOutput

    def run(now, prior, prior_date="2026-09-21"):
        out = FetchOutput()
        out.frames = [pd.DataFrame([{"date": pd.Timestamp("2026-09-22"),
                                     "project": "World Mobile", "metric": "total_supply_gross",
                                     "value": now, "source": "chain:sum(...)", "tier": 2}])]
        _derive_observed_minting(
            out, [config.PROJECT_BY_NAME["World Mobile"]],
            {("World Mobile", "total_supply_gross"): prior},
            {("World Mobile", "total_supply_gross"): prior_date})
        return out

    spec = config.PROJECT_BY_NAME["World Mobile"]["observed_minting"]
    assert spec["metric"] == "emissions_tokens" and spec["supply_metric"] == "total_supply_gross"
    assert "no single launch date" in spec["why"]

    out = run(1_714_332_116.0, 1_714_232_116.0)
    got = out.frame().query("metric == 'emissions_tokens'")
    assert len(got) == 1 and abs(float(got.value.iloc[0]) - 100_000.0) < 1e-6, got
    src = str(got.source.iloc[0])
    assert "d_total_supply_gross" in src and src.endswith(":delta"), src
    assert "PARTIAL" in src, "Cardano is excluded, and the row has to say so"
    part = [r for r in out.review if r["reason"] == "supply_partial"]
    assert part and "can only be too small" in part[0]["basis"], part

    # ** ONE OBSERVATION IS NOT A FLOW. ** Same rule as every other differenced series: two dated
    # readings or nothing, never a 0 that reads as "nothing was minted".
    lone = run(1_714_332_116.0, None)
    assert lone.frame().query("metric == 'emissions_tokens'").empty
    # AND A MEASURED FIGURE STILL WINS.
    out2 = FetchOutput()
    out2.frames = [pd.DataFrame([
        {"date": pd.Timestamp("2026-09-22"), "project": "World Mobile",
         "metric": "total_supply_gross", "value": 1_714_332_116.0, "source": "chain:sum(...)", "tier": 2},
        {"date": pd.Timestamp("2026-09-22"), "project": "World Mobile",
         "metric": "emissions_tokens", "value": 42.0, "source": "dune:1", "tier": 4}])]
    _derive_observed_minting(out2, [config.PROJECT_BY_NAME["World Mobile"]],
                             {("World Mobile", "total_supply_gross"): 1_714_232_116.0},
                             {("World Mobile", "total_supply_gross"): "2026-09-21"})
    assert len(out2.frame().query("metric == 'emissions_tokens'")) == 1
    assert [e for e in out2.log if e.status == "skipped" and "already has a figure" in e.message]

    # W3 — THE ALLOCATION TABLE VALIDATES TO THE TOKEN, which is corroboration of the TABLE.
    v = config.PROJECT_BY_NAME["World Mobile"]["allocation_validation"]
    assert v["bucket_share"] * 2_000_000_000 / v["vesting_months"] == v["next_unlock_tokens"]
    assert v["next_unlock_tokens"] == 5_000_000 and v["verdict"].startswith("EXACT")

    # W2 — RANDOMISED CADENCE, SO NO WINDOW BEHAVES. The strongest lumpy case in the file.
    assert config.PROJECT_BY_NAME["World Mobile"]["buyback_cadence"]["interval"] == "randomized"
    assert config.PROJECT_BY_NAME["World Mobile"]["buyback_cadence"]["share_of_earnings"] is None
    assert config.level_break_windows("World Mobile", "actual_buyback_tokens") == (30, 90)
    print("world mobile ok: emissions measured from the gross-supply delta, partial in a KNOWN "
          "direction, and the 18%/72-month bucket validated to the token")


# ------------------------------------------------------------------ NEAR's staked supply
class _NearStub:
    """Stands in for a NEAR node. Records the bodies it was posted."""

    def __init__(self, payload, fail_urls=()):
        self.payload, self.fail_urls, self.calls = payload, set(fail_urls), []

    def post(self, url, json_body=None, **kw):
        self.calls.append((url, json_body))
        if url in self.fail_urls:
            raise RuntimeError("connection refused")
        return self.payload


def _near_payload(stakes):
    return {"jsonrpc": "2.0", "id": "token-metrics",
            "result": {"current_validators": [{"account_id": f"v{i}.poolv1.near",
                                               "stake": str(int(v * 10 ** 24))}
                                              for i, v in enumerate(stakes)],
                       # THE NEXT EPOCH'S SEATS ARE NOT ADDITIONAL STAKE. Present in the real
                       # response and deliberately not summed: nearly every validator carries
                       # over, so adding them would roughly double the figure.
                       "next_validators": [{"account_id": "v0.poolv1.near",
                                            "stake": str(int(10 ** 30))}],
                       "epoch_height": 2180}}


def _run_near(payload, supply=1_240_000_000.0, node_api=None, fail_urls=()):
    from fetch.base import FetchOutput
    from fetch.near import NearNode

    p = dict(config.PROJECT_BY_NAME["Near"])
    if node_api is not None:
        p["node_api"] = node_api
    n = NearNode(prior_values={("Near", "total_supply"): supply} if supply else {})
    stub = _NearStub(payload, fail_urls=fail_urls)
    n.http = stub
    out = FetchOutput()
    n.run([p], None, out)
    return out, stub


def test_near_staked_is_summed_from_the_validators_call_and_scaled_from_nears_own_sdk():
    """** THERE IS NO LOCK CONTRACT TO READ. ** NEAR's staking is protocol-level and spread across
    one staking-pool contract PER VALIDATOR, so any single address is one validator's stake and
    not the network's. locked_tokens gapped every run asking for an address that does not exist.

    The `validators` RPC returns every current validator with its stake INCLUDING delegations,
    and the sum is the figure.

    ** THE EXPONENT IS SOURCED, NOT GUESSED. ** yoctoNEAR is 10^24 — NEAR_NOMINATION_EXP in
    near/near-api-js, read 2026-09-22 and on the config entry. The EVM's 18 would turn a 600m
    stake into 600 billion; 30 would turn it into 0.0006.
    """
    out, stub = _run_near(_near_payload([200_000_000.0, 150_000_000.0, 100_000_000.0]))
    got = out.frame().query("metric == 'locked_tokens'")
    assert len(got) == 1, out.log
    assert abs(float(got.value.iloc[0]) - 450_000_000.0) < 1.0, float(got.value.iloc[0])
    assert str(got.source.iloc[0]) == "near_rpc:validators"
    # NEXT EPOCH'S SEATS ARE NOT SUMMED — the stub carries a 10^6 NEAR next_validators entry.
    assert float(got.value.iloc[0]) < 1_000_000_000.0
    body = stub.calls[0][1]
    assert body["method"] == "validators" and body["params"] == [None]

    # ** AN EXPONENT TOO SMALL IS CAUGHT BY A STRUCTURAL BOUND. ** Nothing staked can exceed
    # everything in existence, so 10^18 on a yoctoNEAR figure fails and NOTHING is stored.
    api = dict(config.PROJECT_BY_NAME["Near"]["node_api"], yocto_exponent=18)
    out2, _ = _run_near(_near_payload([200_000_000.0]), node_api=api)
    assert out2.frame().query("metric == 'locked_tokens'").empty
    assert [e for e in out2.log if e.status == "failed" and "FAILS ITS BOUND" in e.message], out2.log
    assert [g for g in out2.gaps if "do NOT pick the exponent that makes the number look right"
            in g["suggestion"].replace("Do NOT", "do NOT")]

    # ** AN EXPONENT TOO LARGE IS NOT, AND THE FLAG IS THE ONLY PLACE IT SHOWS. ** 10^30 turns
    # 450m into 0.00045, which is still "greater than zero and under supply". It is STORED — a
    # low staked share is possible and the floor is a judgement — and flagged, with the SDK URL
    # to check.
    api2 = dict(config.PROJECT_BY_NAME["Near"]["node_api"], yocto_exponent=30)
    out3, _ = _run_near(_near_payload([200_000_000.0]), node_api=api2)
    assert not out3.frame().query("metric == 'locked_tokens'").empty, "a judgement must not refuse"
    low = [r for r in out3.review if r["reason"] == "below_expected_share"]
    assert low and "near-api-js" in low[0]["basis"], out3.review
    assert "STORED ANYWAY" in low[0]["basis"]

    # NO SUPPLY TO BOUND AGAINST MEANS NOTHING IS STORED. An unbounded scaled figure is the one
    # thing this read cannot check about itself.
    out4, _ = _run_near(_near_payload([200_000_000.0]), supply=None)
    assert out4.frame().query("metric == 'locked_tokens'").empty
    assert [e for e in out4.log if "no total_supply in the store to bound" in e.message]

    # A JSON-RPC ERROR IS A 200, and treating it as a payload would report "no validators carried
    # a stake" for a rejected request. The next endpoint is tried.
    out5, stub5 = _run_near({"jsonrpc": "2.0", "error": {"name": "HANDLER_ERROR"}, "id": "x"})
    assert out5.frame().empty
    assert len(stub5.calls) == 3, "every configured endpoint is tried before giving up"
    assert [e for e in out5.log if e.status == "failed" and "JSON-RPC error" in e.message]
    print("near stake ok: current validators summed, 10^24 from NEAR's own SDK, a small exponent "
          "refused by the bound and a large one flagged where nothing else would see it")


# ------------------------------------------------- a chain's burn, already in the store
def _chain_frame(rows):
    out = FetchOutput()
    out.frames = [pd.DataFrame(rows)]
    return out


def _chain_rows(project, days, rev, fees, price, start="2026-08-24"):
    rows = []
    for i in range(days):
        d = pd.Timestamp(start) + pd.Timedelta(days=i)
        rows.append({"date": d, "project": project, "metric": "revenue_usd", "value": rev,
                     "source": "defillama", "tier": 1})
        if fees is not None:
            rows.append({"date": d, "project": project, "metric": "fees_usd", "value": fees,
                         "source": "defillama", "tier": 1})
        rows.append({"date": d, "project": project, "metric": "price_usd", "value": price,
                     "source": "coingecko", "tier": 1})
    return rows


def test_a_chains_burn_is_its_defillama_revenue_and_that_is_read_from_the_adapter():
    """** THE FIGURE WAS ALREADY IN THE STORE UNDER ANOTHER NAME. **

    Ethereum and Near both gapped gross_burn_tokens asking for a source while revenue_usd sat
    beside them carrying exactly that quantity. For a CHAIN, DefiLlama's "Revenue" is not a share
    of fees taken by a protocol — it is the part of the fees nobody receives, because it was
    destroyed. Read from the adapters themselves on 2026-09-22:

      fees/ethereum/index.ts   dailyRevenue.addGasToken(baseFeesWei ...); addGasToken(blobFeesWei ...)
                               Revenue: "Amount of ETH burned — base fees plus blob fees"
      fees/near/index.ts       dailyRevenue.addCGToken('near', totalFees * 0.7, 'Burned NEAR')
                               Revenue: "70% of every gas fee is permanently burned"
    """
    from fetch import _derive_chain_burn

    # NEAR: 70% of fees, priced on each day's own price.
    out = _chain_frame(_chain_rows("Near", 5, rev=70_000.0, fees=100_000.0, price=3.50))
    _derive_chain_burn(out, [config.PROJECT_BY_NAME["Near"]])
    got = out.frame().query("metric == 'gross_burn_tokens'")
    assert len(got) == 5, out.log
    assert abs(float(got.value.iloc[0]) - 20_000.0) < 1e-6, got
    assert "defillama_burned_fee_revenue" in str(got.source.iloc[0])
    detail = [e.message for e in out.log if "gross_burn_tokens =" in e.message]
    assert detail and "derived from DefiLlama burned-fee revenue" in detail[0], detail

    # ** THE RATIO GATE IS A TRIPWIRE ON THE METHODOLOGY, NOT A CHECK ON THE FIGURE. ** NEAR's
    # revenue IS fees x 0.7, so 0.700 holding proves nothing — it is our own arithmetic read back.
    # What it catches is DefiLlama CHANGING the split, at which point revenue stops being the burn.
    moved = _chain_frame(_chain_rows("Near", 4, rev=50_000.0, fees=100_000.0, price=3.50))
    _derive_chain_burn(moved, [config.PROJECT_BY_NAME["Near"]])
    assert moved.frame().query("metric == 'gross_burn_tokens'").empty, \
        "a changed split must stop the derivation, not rescale it"
    flag = [r for r in moved.review if r["reason"] == "burn_share_changed"]
    assert flag and "0.5000" in flag[0]["basis"], flag
    assert "dimension-adapters" in str(flag[0]["source"])

    # ETHEREUM has NO ratio gate: priority fees are in Fees and not in Revenue, so the ratio
    # legitimately moves, and a gate on a moving number is one that gets widened until it is
    # meaningless.
    eth = _chain_frame(_chain_rows("Ethereum", 30, rev=98_350.77, fees=405_412.30, price=2_745.0))
    _derive_chain_burn(eth, [config.PROJECT_BY_NAME["Ethereum"]])
    burn = eth.frame().query("metric == 'gross_burn_tokens'")
    assert len(burn) == 30 and not [r for r in eth.review if r["reason"] == "burn_share_changed"]
    # ===== ** THE BAND IS GONE, AND IT WAS RESOLVED RATHER THAN WIDENED. ** $2,950,523 over 30
    # days at $2,745 is ~1,075 ETH, about 36/day, against the old 50-70/day reference — which
    # fired every run. The monthly series settled it: May 2026 averaged 75.6 ETH/day, ABOVE the
    # band's own ceiling, and the burn has declined steadily since. So the reference described a
    # period that had passed and the ~36/day is recency, not error.
    #
    # ** NOT WIDENED TO FIT, WHICH IS THE PART WORTH PINNING. ** The series ran 141.7 ETH/day in
    # October and 19.8 two months later; a band that never fires on that has to span 20-142, and
    # a flag that wide catches nothing. Removing it is the honest answer, and this assertion is
    # what stops a future round quietly re-adding a loose one.
    assert abs(float(burn.value.iloc[0]) - 35.83) < 0.05, float(burn.value.iloc[0])
    assert not [r for r in eth.review if r["reason"] == "outside_expected_band"], eth.review
    decl = config.PROJECT_BY_NAME["Ethereum"]["chain_burn_from_revenue"]
    assert decl["expect_daily_tokens"] is None, "resolved, not re-banded"
    res = decl["band_resolution_2026_09_23"]
    assert res["monthly_mean_eth_per_day"]["2026-05"] == 75.6, "above the old ceiling"
    assert res["monthly_mean_eth_per_day"]["2026-09"] == 32.5
    assert "a flag that wide catches nothing" in res["why_no_new_band"]
    # AND THE PRICE-COVERAGE FLOOR IS STATED ON THE METRIC, because a decade of revenue beside an
    # empty burn column reads as a fetch that failed — and the "fix" somebody reaches for is the
    # latest price, which is the one thing the derivation refuses.
    floor = decl["derived_series_floor"]
    assert floor["earliest_possible_date"] == "2025-09-12"
    assert "not a fetch failure" in {k.lower() for k in floor} or floor["not_a_fetch_failure"]
    assert "latest price" in floor["not_a_fetch_failure"]

    # A SOURCED SERIES WINS AND THE DERIVATION IS SKIPPED, never ranked against it: two figures
    # for one burn is a measuring-point change, and that blanks the column.
    rows = _chain_rows("Near", 2, rev=70_000.0, fees=100_000.0, price=3.50)
    rows.append({"date": pd.Timestamp("2026-08-24"), "project": "Near",
                 "metric": "gross_burn_tokens", "value": 19_000.0, "source": "dune:1", "tier": 4})
    both = _chain_frame(rows)
    _derive_chain_burn(both, [config.PROJECT_BY_NAME["Near"]])
    assert len(both.frame().query("metric == 'gross_burn_tokens'")) == 1
    assert [e for e in both.log if e.status == "skipped" and "already has a figure" in e.message]

    # A DAY WITH NO PRICE IS NOT CONVERTED AT THE LATEST PRICE. A July burn valued in September
    # is not what was destroyed.
    #
    # ** AND THE MESSAGE MUST NAME THE SURVIVORS, NOT ONLY THE REFUSAL. ** On the live run one
    # missing price day (2026-08-24) produced "1 revenue row(s) have no price_usd on their own
    # date ... were NOT converted", and that was read as the whole series being blocked — while
    # 29 of the 30 days had in fact been converted and stored. The refusal was right; the report
    # was one-sided. So the assertion is on the count that DID convert and the span it covers,
    # because a message that only ever names the hole is the bug.
    noprice = [r for r in _chain_rows("Near", 30, rev=70_000.0, fees=100_000.0, price=3.50)
               if not (r["metric"] == "price_usd" and r["date"] == pd.Timestamp("2026-08-24"))]
    out2 = _chain_frame(noprice)
    _derive_chain_burn(out2, [config.PROJECT_BY_NAME["Near"]])
    assert len(out2.frame().query("metric == 'gross_burn_tokens'")) == 29
    msg = [e.message for e in out2.log if "no price_usd on their own date" in e.message]
    assert msg, out2.log
    assert "29 of 30 revenue row(s) WERE converted and stored" in msg[0], msg[0]
    assert "2026-08-25..2026-09-22" in msg[0], msg[0]
    assert "not blocked" in msg[0] and "2026-08-24" in msg[0], msg[0]

    # With NO priced day at all the same line says so plainly, rather than claiming a stored span.
    allmissing = [r for r in _chain_rows("Near", 3, rev=70_000.0, fees=100_000.0, price=3.50)
                  if r["metric"] != "price_usd"]
    out3 = _chain_frame(allmissing)
    _derive_chain_burn(out3, [config.PROJECT_BY_NAME["Near"]])
    assert out3.frame().query("metric == 'gross_burn_tokens'").empty
    m3 = [e.message for e in out3.log if "no price_usd on their own date" in e.message]
    assert m3 and "NONE of the 3 revenue row(s) could be converted" in m3[0], m3
    assert "WERE converted" not in m3[0] and "not blocked" not in m3[0], m3[0]
    print("chain burn ok: revenue is the burn per the adapters, the ratio gate is a methodology "
          "tripwire, Ethereum's band is flagged not fixed, and a sourced series wins")


def test_a_chains_issuance_follows_once_the_burn_exists():
    """gross_issuance_tokens = d(total_supply) + burn, and the burn was the only missing input.

    Both chains destroy at the protocol level, so issuance_supply_rule already said 'add_burn' —
    it just had no burn figure to add, and gapped every run for want of a number that was one
    division away from revenue_usd.
    """
    from fetch import _derive_chain_burn, _derive_issuance

    rows = _chain_rows("Near", 1, rev=70_000.0, fees=100_000.0, price=3.50, start="2026-09-14")
    rows.append({"date": pd.Timestamp("2026-09-14"), "project": "Near", "metric": "total_supply",
                 "value": 1_240_000_000.0, "source": "coingecko", "tier": 1})
    out = _chain_frame(rows)
    p = config.PROJECT_BY_NAME["Near"]
    _derive_chain_burn(out, [p])
    _derive_issuance(out, [p], {("Near", "total_supply"): 1_239_900_000.0},
                     {("Near", "total_supply"): "2026-09-13"})
    got = out.frame().query("metric == 'gross_issuance_tokens'")
    assert len(got) == 1, [g["reason"] for g in out.gaps if g["metric"] == "gross_issuance_tokens"]
    # 100,000 of supply change + 20,000 burned = 120,000 minted. Supply is NET of a protocol
    # burn by construction — the tokens are destroyed at the protocol, so they are not in it.
    assert abs(float(got.value.iloc[0]) - 120_000.0) < 1e-6, float(got.value.iloc[0])
    print("chain issuance ok: d(total_supply) + the derived burn, with no new source")


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
        # The governance Timelock joined this project on 2026-09-23 as the treasury holder, and
        # a treasury_holding holder must be a deployed contract — so it belongs in CODE. It was
        # the guard catching a stub that had not been updated, which is the guard working.
        TIMELOCK = "0x1a9C8182C09F50C8318d769245beA52c32BE35BC"
        CODE = {("ethereum", "0xf38521f130fcCF29dB1961597bc5d2B60F995f85"),
                ("ethereum", "0x5E74C9f42EEd283bFf3744fBD1889d398d40867d"),
                ("ethereum", "0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721"),
                ("ethereum", TIMELOCK)}
        VALUES = {"0xf38521f130fcCF29dB1961597bc5d2B60F995f85": 3_000_000.0,
                  "0x5E74C9f42EEd283bFf3744fBD1889d398d40867d": 1_500_000.0,
                  "0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721": 95_000_000.0,
                  TIMELOCK: 12_000_000.0}

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
    # THE TREASURY IS ITS OWN METRIC AND IS NOT SUMMED INTO THE JARS. Governance-controlled UNI
    # is redeployable supply; fees awaiting a burn election are not the same quantity, and adding
    # them would report one as the other.
    assert rows["treasury_holding_tokens"] == 12_000_000.0, rows
    assert "treasury" in df[df.metric == "treasury_holding_tokens"].source.iloc[0]
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
    # ** AND IT IS A LOWER BOUND, WHICH THE ROW SAYS. ** The Assistance Fund is the
    # fee-conversion burn and nothing else: Hyperliquid's own docs name HyperEVM base fees,
    # HyperEVM priority fees, HyperCore order priority fees, HIP-3 slashing and HIP-1 deployment
    # gas, none of which pass through 0xfefe...fe. A component presented as the whole is the
    # exact shape of error this book labels rather than argues about.
    burn_src = str(out.frame().query("metric == 'gross_burn_tokens'").source.iloc[0])
    assert burn_src.endswith(":delta:PARTIAL"), burn_src
    assert config.orphaned_contract_keys("Hyperliquid", burn_src) == [], burn_src
    part = [g for g in out.gaps if g["metric"] == "gross_burn_tokens"]
    assert part and "LOWER BOUND" in part[0]["reason"], out.gaps
    assert "zero address's EVM balance" in part[0]["suggestion"], "the readable component is named"

    hype = config.PROJECT_BY_NAME["Hyperliquid"]
    assert hype["contracts"] == {}, "the contract route is replaced, not supplemented"
    api = hype["node_api"]
    assert "chain" not in api and not any("rpc" in k.lower() for k in api), \
        f"the info API must need no chain and no RPC: {sorted(api)}"
    print("hypercore ok: 48,420,000 HYPE read over plain HTTPS, no chain and no RPC involved")


def test_token_details_settles_the_supply_convention_and_is_not_stored_as_a_metric():
    """** gross_issuance_tokens HAS BEEN BLOCKED ON ONE QUESTION. ** Is the stored total_supply
    NET of the Assistance Fund burn or GROSS of it? The two formulas differ by the ENTIRE burn,
    so there is no safe default — and the test needs a supply figure from Hyperliquid ITSELF to
    compare the provider's against.

    tokenDetails is that figure, and it does return them: maxSupply, totalSupply and
    circulatingSupply, as decimal strings in HUMAN units. Confirmed 2026-09-22 from the
    documented example response and from a maintained SDK's typed response.

    ** IT IS NOT STORED AS A METRIC. ** Hyperliquid's fees page says Assistance Fund HYPE is
    "removing the tokens permanently from the circulating and total supply" — which taken
    literally would make this figure net too, and whether it includes the fund is precisely the
    question. Storing it under total_supply_gross would assert the answer. So the three numbers
    and the subtraction are reported as EVIDENCE, once.
    """
    from fetch.hypercore import HyperCoreInfo

    class ByType:
        """Answers each info request type with its own payload, as the real endpoint does."""

        def __init__(self, hl_total):
            self.hl_total = hl_total

        def post(self, url, json_body=None, headers=None):
            t = (json_body or {}).get("type")
            if t == "spotClearinghouseState":
                return {"balances": [{"coin": "HYPE", "total": "48420000.0"}]}
            if t == "spotMeta":
                return {"tokens": [{"name": "USDC", "weiDecimals": 8, "tokenId": "0x" + "0" * 32},
                                   {"name": "HYPE", "weiDecimals": 8,
                                    "tokenId": "0x0d01dc56dcaaca66ad901c959b4011ec"}]}
            if t == "tokenDetails":
                assert json_body["tokenId"] == "0x0d01dc56dcaaca66ad901c959b4011ec", json_body
                return {"name": "HYPE", "maxSupply": "1000000000.0",
                        "totalSupply": self.hl_total, "circulatingSupply": "333000000.0",
                        "szDecimals": 2, "weiDecimals": 8}
            return {"error": f"unexpected type {t}"}

    def run(hl_total, provider=951_580_000.0, af=48_420_000.0):
        h = HyperCoreInfo(prior_values={("Hyperliquid", "total_supply"): provider,
                                        ("Hyperliquid", "burn_address_balance"): af})
        h.http = ByType(hl_total)
        out = FetchOutput()
        h.run([config.PROJECT_BY_NAME["Hyperliquid"]], None, out)
        return out

    # NET OF BURN: Hyperliquid's figure exceeds the provider's by exactly the fund's balance.
    out = run("1000000000.0")
    ev = [r for r in out.review if r["reason"] == "supply_convention_evidence"]
    assert len(ev) == 1, out.review
    assert "VERDICT: net_of_burn" in ev[0]["basis"], ev[0]["basis"]
    assert "issuance is d(supply) + burn" in ev[0]["basis"]
    # ** NOT STORED. ** No metric row carries Hyperliquid's own supply figure.
    frame = out.frame()
    assert 1_000_000_000.0 not in set(frame.value), frame
    assert "total_supply_gross" not in set(frame.metric)
    assert "NOTHING IS DERIVED FROM THIS AUTOMATICALLY" in ev[0]["basis"]

    # GROSS: the two agree, so neither subtracts the fund.
    same = run("951580000.0")
    ev2 = [r for r in same.review if r["reason"] == "supply_convention_evidence"]
    assert "VERDICT: gross" in ev2[0]["basis"], ev2[0]["basis"]

    # ** NEITHER, AND IT SAYS SO RATHER THAN PICKING THE CLOSER ONE. ** A difference that matches
    # neither the burn nor zero means something else sits between the two figures, and choosing
    # the nearer reading is how a wrong convention gets declared with confidence.
    odd = run("970000000.0")
    ev3 = [r for r in odd.review if r["reason"] == "supply_convention_evidence"]
    assert "VERDICT: NEITHER" in ev3[0]["basis"] and "Do NOT pick the closer one" in ev3[0]["basis"]

    # NO COMPARISON WITHOUT BOTH SIDES — and the skip says which side is missing.
    lone = run("1000000000.0", provider=None)
    assert not [r for r in lone.review if r["reason"] == "supply_convention_evidence"]
    assert [e for e in lone.log if e.status == "skipped" and "supply convention" in e.message
            and "total_supply is not there yet" in e.message]
    print("tokenDetails ok: the three supply figures arrive, the subtraction names the "
          "convention, and nothing is stored under a name that would assert it")


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
        {"project": "Uniswap", "metric": "gross_burn_tokens", "tier": 3, "enabled": True, "url": "https://x", "method": "dom"},
        # ** A STUB FOR A COLUMN THIS PROJECT DOES NOT HAVE. ** Uniswap publishes no net mint, so
        # metrics_for_project excludes net_mint_monthly for it (requires_flag). This path was the
        # one place that did not ask — it gapped on the mere existence of a registry line — and
        # four projects carried "net_mint_monthly — EMPTY STUB" because of it, plus Ether.fi's
        # staked_tokens, a metric that has been RETIRED.
        {"project": "Uniswap", "metric": "net_mint_monthly", "tier": 3, "enabled": False, "url": None, "method": "dom"},
    ]))
    s = Scrape(registry_path=tmp)
    out = FetchOutput()
    s.run([{"name": "PancakeSwap"}, {"name": "Uniswap"}], None, out)
    assert len(out.gaps) == 2, out.gaps
    reasons = {(g["project"], g["metric"]): g["reason"] for g in out.gaps}
    assert "disabled" in reasons[("PancakeSwap", "net_mint_monthly")], reasons
    assert "anchor" in reasons[("Uniswap", "gross_burn_tokens")], reasons
    assert ("Uniswap", "net_mint_monthly") not in reasons, \
        "a stub for a column this project does not have is not a to-do item"
    # ** AND IT IS NOT SILENT. ** The row says the entry was ignored and why, so nobody hunts for
    # a source that was never wanted and nobody deletes a registry line that is a useful note.
    skipped = [e for e in out.log if e.status == "skipped" and "does not apply" in e.message]
    assert skipped and "net_mint_monthly" in skipped[0].message, out.log
    assert "NOT a gap" in skipped[0].message
    assert out.frame().empty
    print("tier 3 registry ok: incomplete entries become gap rows, and a stub for a column the "
          "project does not have is skipped with its reason instead")


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
    assert not buyback.empty, (
        "actual_buyback_tokens has never been fetched before — its first backfill must ignore "
        "the run's 30-day window and return its full history, not silently zero rows")
    assert buyback.value.iloc[0] == 600_000.0

    # ** gross_burn_tokens IS WRITTEN TOO, AND THAT CHANGED ON 2026-09-23. ** It used to be
    # skipped here: the gate was per metric, so "the store already holds a row" fired even though
    # the query was executing anyway for its sibling. The skip exists to avoid PAYING for a
    # query, and when the query runs regardless that reason is gone — the response is already in
    # memory and writing the second metric from it costs nothing.
    #
    # THE LIVE CASE THIS WAS COSTING: Ether.fi's dune:8683038 serves three metrics and only one
    # carries the 7-day cadence, so the weekly re-pull refreshed locked_tokens_dashboard and left
    # lock_rate_pct and staker_count stale beside their own fresh values in the same response.
    assert not burn.empty, (
        "the query is executing for its sibling, so every metric it serves is written — leaving "
        "one stale beside fresh data in the same response is the bug this replaced")
    assert burn.value.iloc[0] == 600_000.0
    print("dune first-time-ignores-window ok: a brand-new metric sharing an already-backfilled "
          "query gets its full history, and the sibling is written from the same response")


class _Rows:
    """A stub Dune HTTP layer returning one fixed page of rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def get(self, url, params=None, headers=None):
        # Counted, because "one execution serves every metric" is what makes carrying free —
        # paying per metric would trade one bug for a more expensive one.
        if (params or {}).get("offset", 0) == 0:
            self.calls += 1
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

    # ===== ** THE RATE GUARD, PINNED TO THE REAL SERIES THAT SLIPPED THROUGH. ** =====
    # sETHFI ran 1.238612 (09-14) -> 1.239700 (09-21) -> 1.244475 (09-23). Every step was an
    # INCREASE, so the direction check above said nothing — correctly, by its own contract —
    # while the daily rate went 0.0126%/day to 0.1924%/day, fifteen times faster, on a series
    # that is now the denominator of Ether.fi's lock figures.
    def rate_run(prior_ratio, prior_day, day, assets, shares=SHARES):
        out = FetchOutput()
        out.add(pd.DataFrame([
            {"date": pd.Timestamp(day), "project": "Ether.fi", "metric": "locked_tokens",
             "value": shares, "source": "dune:8683038", "tier": 4},
            {"date": pd.Timestamp(day), "project": "Ether.fi",
             "metric": "locked_tokens_underlying", "value": assets,
             "source": "chain:ethereum:sethfi:PARTIAL", "tier": 2},
        ])[LONG_COLUMNS], "test", "Ether.fi", "", 2)
        key = ("Ether.fi", "lock_assets_per_share")
        _derive_lock_ratio(out, [config.PROJECT_BY_NAME["Ether.fi"]],
                           {key: prior_ratio}, {key: prior_ratio},
                           {key: pd.Timestamp(prior_day)})
        return out

    # THE 7-DAY WINDOW — 4.7%/yr. Ordinary accrual, and it MUST stay silent, or the guard is
    # just noise with a threshold.
    quiet = rate_run(1.238611622166, "2026-09-14", "2026-09-21", SHARES * 1.2397)
    assert not [r for r in quiet.review
                if r["reason"] == "accrued_rate_implausible"], quiet.review

    # THE 2-DAY WINDOW — 0.1924%/day, which compounds to 101.7%/yr against a 25% ceiling.
    loud = rate_run(1.2397, "2026-09-21", "2026-09-23", SHARES * 1.244475)
    hot = [r for r in loud.review if r["reason"] == "accrued_rate_implausible"]
    assert len(hot) == 1, loud.review
    b = hot[0]["basis"]
    assert "0.1924%/day" in b, b
    assert "in 2 day(s)" in b, b

    # ** THE FIGURE IS STORED AS MEASURED — NOTHING IS BLANKED OR RESCALED. ** The flag is the
    # output; the ratio is still the ratio.
    got = loud.frame()[loud.frame().metric == "lock_assets_per_share"]
    assert abs(float(got.value.iloc[0]) - 1.244475) < 1e-9, "stored exactly as measured"

    # ** AND THE ANNUALISATION IS AN INTERVAL ARTEFACT, SAID SO IN THE ROW AND STORED NOWHERE. **
    # Two points across two days cannot measure a yield, and a number that looks like one gets
    # quoted as one.
    assert "INTERVAL ARTEFACT, NOT A YIELD" in b, b
    assert "101.7%" in b, b
    assert not [r for r in loud.review if str(r.get("value")) == "1.017"], \
        "the annualised figure must never be a stored value"
    # The row points at the three mechanisms and says the ratio alone cannot separate them.
    assert "lumpy reward deposit" in b and "exit fee" in b and "reward rate" in b, b
    assert "not distinguishable from this ratio alone" in b, b

    # A FALL STILL WINS. Both guards cannot fire on one observation — a fall is not a fast rise.
    fell = rate_run(1.30, "2026-09-21", "2026-09-23", SHARES * 1.244475)
    reasons = {r["reason"] for r in fell.review}
    assert reasons == {"accrued_rate_fell"}, fell.review

    # NO PRIOR DATE MEANS NO RATE, and the guard says so rather than assuming an interval —
    # "probably a week" turns a 2-day move into a plausible weekly one and silences the exact
    # case this exists for.
    out = FetchOutput()
    out.add(pd.DataFrame([
        {"date": pd.Timestamp("2026-09-23"), "project": "Ether.fi", "metric": "locked_tokens",
         "value": SHARES, "source": "dune:8683038", "tier": 4},
        {"date": pd.Timestamp("2026-09-23"), "project": "Ether.fi",
         "metric": "locked_tokens_underlying", "value": SHARES * 1.244475,
         "source": "chain:ethereum:sethfi", "tier": 2},
    ])[LONG_COLUMNS], "test", "Ether.fi", "", 2)
    key = ("Ether.fi", "lock_assets_per_share")
    _derive_lock_ratio(out, [config.PROJECT_BY_NAME["Ether.fi"]],
                       {key: 1.2397}, {key: 1.2397}, {})
    assert not [r for r in out.review if r["reason"] == "accrued_rate_implausible"]
    assert any("the rise guard did not run" in e.message for e in out.log), \
        "its silence must be explicable, not mistaken for a pass"

    # ** A PROJECT WITHOUT A DECLARED CEILING KEEPS THE DIRECTION CHECK ALONE. ** Opt-in, so
    # nothing gains a flag it was not given.
    assert config.PROJECT_BY_NAME["Ether.fi"]["lock_ratio"]["max_annualised_accrual"] == 0.25
    for p_ in config.PROJECTS:
        spec_ = p_.get("lock_ratio")
        if spec_ and "max_annualised_accrual" in spec_:
            assert 0 < spec_["max_annualised_accrual"] < 1.0, \
                f"{p_['name']}: a ceiling at or above 100%/yr cannot fire on anything real"

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


def _shares_query(project, query_id):
    """Every metric served by one Dune query — the unit the refresh cadence works on.

    A fixture that states history for SOME of a query's metrics describes a store that cannot
    exist: they are written together from one response. Listing them by hand is how two of these
    tests came to assert a skip while silently exercising the first-time path for a sibling.
    """
    return {m for m, spec in (config.PROJECT_BY_NAME[project].get("dune_queries") or {}).items()
            if spec.get("query_id") == query_id}


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
    # EVERY METRIC THE QUERY SERVES, not two of the three. They are written together from one
    # response, so a store holding history for some and not others cannot arise — and a fixture
    # that says otherwise makes the missing one first-time, which re-pulls the query and carries
    # its siblings, exercising a path this test is not about.
    held = {("Ether.fi", m) for m in _shares_query("Ether.fi", 8683038)}

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

    # ===== (1) A DECLARED no_burn IS A STRONGER CASE THAN SILENCE, AND MUST ALSO NOT FLAG. =====
    # Plume used to be the "no burn claimed by any of the four signals" case: nothing declared
    # anywhere, so nothing to flag. On 2026-09-23 its registry settled it — an Arbitrum Orbit
    # chain whose fees are COLLECTED at a receiver, never destroyed — so it now declares
    # no_burn/confirmed, which trips the "claims a burn" test by having a mechanism at all.
    #
    # THE FLAG MUST STILL NOT FIRE, and for a better reason than before: the mechanism is not
    # assumed, it is confirmed to be absent. A project that has DONE the work must not read worse
    # than one that has not.
    plume = config.PROJECT_BY_NAME["Plume"]
    assert 4 not in plume["archetypes"] and plume.get("burn_split") is None \
        and plume.get("burn_read_method") is None
    mech = config.burn_mechanism(plume)
    assert mech["model"] == "no_burn" and mech["status"] == "confirmed", mech
    assert mech.get("source_url") and mech.get("source_date"), "confirmed needs a provenance"
    assert config.not_applicable_reason("Plume", "gross_burn_tokens"), \
        "and the metric itself is declared n/a, so the column never asks for a figure"
    band, why = bw.confidence_for("Plume", "gross_burn_tokens", row, asof)
    assert "MECHANISM" not in why, f"a CONFIRMED absence of a burn must not read as assumed: {why}"
    # AND IT CLOSES THE ISSUANCE QUESTION TOO — the two were one question.
    assert config.issuance_supply_rule(plume, mech["model"]) == "delta_only", \
        "with no burn there is nothing to add back: issuance is d(total_supply)"

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
    # BOTH METRICS OF QUERY 123, because staked_tokens is first-time and so the query executes;
    # gross_burn_tokens is then written from the SAME response rather than skipped beside it.
    # Changed 2026-09-23 — see the Ether.fi case in the first-time test above.
    assert set(df["metric"]) == {"staked_tokens", "gross_burn_tokens"}, set(df["metric"])
    assert set(df["tier"]) == {4} and set(df.source) == {"dune:123"}
    assert any("no query_id" in e.message for e in out.log)

    # AND THE SKIP IS STILL THERE WHEN THE QUERY IS NOT RUNNING AT ALL. The rule is "a query that
    # executes writes everything it serves", not "history no longer skips anything".
    du2 = Dune(has_history={("Ethereum", "gross_burn_tokens"), ("Ethereum", "staked_tokens")})
    du2.http = StubHttp({"/query/123/results": {"result": {"rows": []}}})
    out2 = FetchOutput()
    du2.run([p], None, out2)
    assert out2.frame().empty, "nothing is due, so the query must not run"
    assert len([e for e in out2.log if e.status == "skipped"
                and "backfill NOT run" in e.message]) == 2
    print("tier 4 ok: a running query writes every metric it serves; nothing due still skips")


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
    # ===== AND THE PARAMETERISATION IS NOW CLOSED, BY THE SAME STEP THAT CLOSES BOTH. =====
    # Read the rate as the DERIVATIVE of a supply curve rather than a percentage of a base:
    #     dS/S = k dt/(t+1)  =>  S(t) = S0 (t+1)^k,  and S(20) = cap pins S0 = cap / 21^k.
    # ** THAT REPRODUCES THE 29% THE NAIVE READINGS COULD NOT. ** Nothing was tuned: the only
    # inputs are k, the horizon and the cap, all three quoted from the whitepaper.
    assert curve.get("resolved_on") == "2026-09-22"
    c = config.issuance_curve("World Mobile")
    s0 = config.issuance_curve_s0(c)
    assert abs(s0 - 1_413_073_572.18) < 1.0, s0
    minted = cap - s0
    assert 0.29 <= minted / cap <= 0.30, f"{minted / cap:.4f} must land on the stated 29%"
    assert months_pct < minted / cap < years_pct, \
        "the integrated reading lands between the two naive ones, which is why it is the right one"
    # S0 IS COMPUTED, NOT STORED. Writing it beside the three inputs is how the four drift.
    assert "s0" not in c and "S0" not in c
    print("World Mobile ok: 580m/20yr certain, the TGE anchor falsifies the old percentage test by "
          ">2x, and the integrated curve reproduces the 29% target from k, the horizon and the cap")


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


def test_morphos_held_out_days_come_off_the_thirty_day_coverage():
    """** THE 30-DAY SUM WAS SILENTLY MISSING NINE DAYS OF ~$600,000. **

    Morpho's fees_usd has run since 2021 and is missing 2026-09-12..2026-09-20 because DefiLlama
    did not report Morpho Blue for them — days deliberately held out rather than filled with
    Morpho Midnight's ~$2/day. Coverage asks how long the series has EXISTED, so it read 30 of
    30, and the trailing-30-day figure omitted nine days under a full-window header.

    ** THAT IS THE SAME SHAPE OF ERROR THE HOLE EXISTS TO AVOID, ONE LEVEL UP. ** The days were
    held out because a wrong-but-plausible number is worse than an absence; a 30-day header over
    a 21-day sum puts the understatement back with nothing at all to notice.

    DISCLOSED, NOT WITHHELD: a 21-day total IS the honest 21-day total once the header says so.
    """
    rows = ([("2021-06-01", "Morpho", "fees_usd", 500_000.0, "defillama", 1)]
            + [(f"2026-09-{d:02d}", "Morpho", "fees_usd", 600_000.0, "defillama", 1)
               for d in list(range(1, 12)) + list(range(21, 23))])   # 09-12..09-20 ABSENT
    got = _aggregate_rows(rows, "2026-09-23")[("Morpho", "fees_usd")]

    assert "COVERS 21 OF 30 DAYS" in got["note"], got["note"]
    # ** AND THE REASON IS THE SOURCE, NOT THE SERIES' AGE. ** A series running since 2021 that
    # said "its history does not span the full window" would be stating something false, and it
    # sends the reader to the wrong question.
    assert "the source did not report them" in got["note"], got["note"]
    assert "2026-09-12..2026-09-20" in got["note"], got["note"]
    assert "history does not span" not in got["note"], "that is the OTHER reason, and it is wrong here"
    assert "do NOT interpolate" in got["note"], "the config instruction travels to the cell"
    assert got["confidence"] == "AMBER", got["confidence"]
    assert "covers only 21 of the 30 days" in got["why_amber"], got["why_amber"]
    # The figure is DISCLOSED, not blanked — it is a true 21-day sum.
    assert got["now"] == 600_000.0 * 13, got["now"]
    print("morpho coverage ok:", got["note"][:110])


def test_the_hole_stops_being_subtracted_once_the_source_backfills_it():
    """THE CONTROL, and the thing that makes this self-clearing. Nobody edits a date by hand:
    the recovery path sets break_window_backfilled when morpho-blue reports those days, and the
    coverage deduction disappears with it.
    """
    import copy
    assert config.declared_source_hole("Morpho", "fees_usd") is not None, "open today"
    # Only the TARGET metric carries it — revenue_usd was never routed through the restructure.
    assert config.declared_source_hole("Morpho", "revenue_usd") is None
    # And a project with no declared restructure is untouched, which is what keeps this from
    # becoming a density check over every sparse series.
    assert config.declared_source_hole("Hyperliquid", "gross_burn_tokens") is None

    filled = copy.deepcopy(config.PROJECT_BY_NAME["Morpho"])
    filled["defillama_restructure"]["break_window_backfilled"] = True
    saved = config.PROJECT_BY_NAME["Morpho"]
    config.PROJECT_BY_NAME["Morpho"] = filled
    try:
        assert config.declared_source_hole("Morpho", "fees_usd") is None, \
            "a backfilled window is not a hole, and nothing should have to be edited to say so"
    finally:
        config.PROJECT_BY_NAME["Morpho"] = saved
    print("hole control ok: it clears itself when the source fills the dates")


class _Recorder:
    """Captures review_item and skipped calls without needing the full FetchOutput."""

    def __init__(self):
        self.items, self.skips = [], []

    def review_item(self, project, metric, reason, action, **kw):
        self.items.append({"project": project, "metric": metric, "reason": reason, **kw})

    def skipped(self, source, project, message, tier=None):
        self.skips.append({"source": source, "project": project, "message": message})


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

    # ** AND SINCE 2026-09-23 IT IS NOT STORED EITHER. ** Excluding it from the change check
    # stops a false flag and does nothing about the figure reaching the sheet: a near-empty
    # today sits inside every trailing-window "Now" figure until the next run replaces it. The
    # providers are called with a trailing window, so tomorrow's run fetches the day complete.
    import pandas as pd
    from fetch.validate import validate_frame
    rows = pd.DataFrame([{"date": pd.Timestamp(d), "project": "Chainlink", "metric": "revenue_usd",
                          "value": v, "source": "defillama:chainlink", "tier": 1}
                         for d, v in [(before, 1_050_000.0), (yesterday, 1_105_263.0), (today, 0.0)]])
    rec = _Recorder()
    kept = validate_frame(rows, {}, rec)
    assert str(today) not in set(kept["date"].astype(str).str[:10]), \
        "today's provider flow row must not survive to the store"
    assert len(kept) == 2 and str(yesterday) in set(kept["date"].astype(str).str[:10])
    assert rec.skips and "was NOT STORED" in rec.skips[0]["message"], rec.skips
    assert "trailing" in rec.skips[0]["message"], "the row must say why it costs nothing"

    # A STOCK IS CORRECT AT ANY HOUR and is kept — dropping it would throw away the only
    # reading of the day for no gain.
    stocks = pd.DataFrame([{"date": pd.Timestamp(today), "project": "Chainlink",
                            "metric": "price_usd", "value": 12.0, "source": "coingecko", "tier": 1}])
    rec2 = _Recorder()
    assert len(validate_frame(stocks, {}, rec2)) == 1 and not rec2.skips
    # AND SO IS A CONTRACT READ — the rule is about providers that publish a day early, not
    # about today.
    chain_rows = pd.DataFrame([{"date": pd.Timestamp(today), "project": "Uniswap",
                                "metric": "gross_burn_tokens", "value": 1_000.0,
                                "source": "chain:ethereum:burn_dead:delta", "tier": 2}])
    rec3 = _Recorder()
    assert len(validate_frame(chain_rows, {}, rec3)) == 1 and not rec3.skips


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
        # THE WHOLE QUERY'S HISTORY, not this metric's alone — see _shares_query. The cadence is
        # a property of the query, so a sibling left out of the fixture would be first-time, run
        # the query, and carry this metric along with it.
        siblings = {(project, m) for m in _shares_query(
            project, (config.PROJECT_BY_NAME[project]["dune_queries"][metric] or {}).get("query_id"))}
        seen = (pd.Timestamp.now().normalize()
                - pd.Timedelta(days=last_seen_days_ago)).date().isoformat()
        d = Dune(has_history=siblings, last_dates={k: seen for k in siblings})
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
    d = _D(has_history={("Ether.fi", m) for m in _shares_query("Ether.fi", 8683038)}, last_dates={})
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

    # ** THE BAND WAS 1.4-1.7bn AND IT REJECTED A CORRECT READ. WIDENED 2026-09-23. ** The live
    # four-deployment sum came to 1,714,232,116 and was refused for being 14m over a ceiling
    # picked by hand — "a bit above the 1.494bn Ethereum already reads", which is a guess about
    # the other three chains, not a bound the design imposes. The figure sits squarely on the
    # whitepaper's own Fig. 1 curve, so the read was right and the ceiling was wrong.
    #
    # BOTH ENDS NOW RULE OUT SOMETHING THAT CANNOT BE TRUE. 1.42bn is mainnet launch on that
    # curve, so below it a component failed; 2.0bn is the ERC20Capped ceiling in the deployed
    # source, so above it is impossible by construction. That is not tolerance added to a failing
    # check — the band moved to figures the source documents state.
    lo, hi = config.sanity_bounds("World Mobile", "total_supply_gross")
    assert (lo, hi) == (1_420_000_000, 2_000_000_000)
    assert lo <= float(row.value.iloc[0]) <= hi
    assert lo <= 1_714_232_116 <= hi, "the live sum that was wrongly rejected must now pass"
    assert not (lo <= 2_000_000_001 <= hi), "above the contract's cap stays impossible"

    # THE CAP CANNOT LEAK INTO THIS METRIC ANYWAY, and that is a STRUCTURAL guarantee rather
    # than a band: total_supply_gross is written only by contract reads carrying metric_override,
    # and CoinGecko's 2,000,000,000 goes to total_supply. The old band was standing in for this
    # check by refusing any figure near the cap, which is why it also refused a real one.
    assert all(c["metric_override"] == "total_supply_gross" for c in contracts.values())
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
    # ** THE DATE IS TAKEN FROM THE CODE, NOT TYPED IN. ** It was the literal "2026-09-22", which
    # meant "today" on the day it was written and meant "yesterday" the next morning — so this
    # case silently stopped testing a same-day re-run and started testing an ordinary one-day
    # delta, which of course derives a flow. A test whose premise is "the same day" has to ASK
    # what day it is.
    from fetch.base import today as _today
    out = run(5_240_000.0, 5_180_000.0, prior_date=str(_today())[:10])
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

    # ===== H3 — AND THE SELECTORS ARE CHECKED, NOT PINNED. Corrected 2026-09-23. =====
    # ** THIS TEST USED TO PIN TWO WRONG VALUES. ** It asserted want() == 0x1f1c827f and
    # spotter() == 0xf3701da2 — neither of which is keccak of anything on that contract — so it
    # locked in the bug rather than catching it, and the resulting failures were attributed to
    # llamarpc returning 525. A test that repeats a magic number back at the file it came from
    # confirms only that nobody has retyped it.
    #
    # Now every selector in the script is derived and compared. flapper() on the Splitter was
    # wrong too, which is worse: it is the one call that answers whether the splitter has been
    # re-pointed, and a wrong selector there reads as an unreachable endpoint.
    import check_offline_items as coi

    from web3 import Web3
    for table in (coi.SELECTORS, coi.SELECTORS_SPLITTER, coi.SELECTORS_SPLITTER_PARAMS):
        for sig, sel in table.items():
            assert sel == "0x" + Web3.keccak(text=sig).hex()[:8], sig
    # ** AND THE BARE CONSTANTS, WHICH IS WHERE A FOURTH WRONG ONE SURVIVED. ** The 2026-09-23
    # fix checked the three DICTS; list() is a module constant and slipped through, so the
    # ChainLog enumeration — the check built precisely to avoid guessing an address — was
    # calling a function that does not exist and would have reported UNREACHABLE.
    for sig, sel in (("list()", coi.CHAINLOG_LIST), ("getAddress(bytes32)", coi.CHAINLOG_GET),
                     ("totalSupply()", coi.SEL_TOTAL_SUPPLY),
                     ("balanceOf(address)", coi.SEL_BALANCE_OF),
                     ("decimals()", coi.SEL_DECIMALS), ("threshold()", coi.SEL_THRESHOLD)):
        assert sel == "0x" + Web3.keccak(text=sig).hex()[:8], sig
    # THE EVENT TOPIC TOO — 32 bytes, same class of typo, and a wrong one matches nothing and
    # reads as "the parameter never changed".
    assert coi.FILE_TOPIC_UINT == "0x" + Web3.keccak(text="File(bytes32,uint256)").hex()
    assert coi.WHAT_BURN == "0x" + b"burn".hex().ljust(64, "0")
    # ** AND NO HAND-WRITTEN SELECTOR SURVIVES ANYWHERE ELSE IN THE CODEBASE. ** Every four-byte
    # hex literal outside the tests is either in this file (checked above) or in config's record
    # of the corrections themselves.
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    stray = []
    for f in list(root.glob("*.py")) + list((root / "fetch").glob("*.py")):
        if f.name == "check_offline_items.py":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r'"0x[0-9a-fA-F]{8}"', line) and "selector_correction" not in line:
                if f.name == "config.py" and '"was":' in line:
                    continue          # the recorded corrections, which are meant to be wrong
                stray.append(f"{f.name}:{i}: {line.strip()[:70]}")
    assert not stray, ("a hand-written four-byte selector outside check_offline_items is "
                       "unchecked:\n  " + "\n  ".join(stray))
    # The endpoint order is LEFT ALONE — it costs nothing — but it is no longer justified by a
    # diagnosis that has been superseded.
    assert src.index('"https://ethereum-rpc.publicnode.com"') < src.index('"https://eth.llamarpc.com"')
    assert "def sky_chainlog" in src and "CHAINLOG_LIST" in src
    assert "may not be \"MCD_SPLIT\"" in src, \
        "the registry key is not guessed either — list() is printed in full first"

    # ===== ** THIS GUARD EXISTED AND STILL MISSED ONE, WHICH IS THE INTERESTING PART. **
    # It read main()'s SOURCE TEXT for a HARDCODED list of eight names. aerodrome_lock_inputs
    # was a ninth, nobody added it here, and the guard passed while the check never ran — a
    # completeness check that has to be kept complete by hand is not a completeness check.
    #
    # The list is now the module's own registry, and the real assertion lives in
    # test_every_check_is_reachable_from_main_and_prints_its_section: it runs main() end to end
    # and separately proves that every function printing a section is registered. This keeps the
    # named-eight assertion as a cheap regression on the ones that matter most, but it is no
    # longer what the suite relies on.
    import check_offline_items as _coi
    registered = {f.__name__ for f in _coi.CHECKS}
    for fn in ("pendle_spendle_virtual", "uniswap_firepit_threshold", "sky_chainlog",
               "sky_splitter", "sky_splitter_params", "sky_splitter_history", "sky",
               "morpho_blue_api", "aerodrome_lock_inputs"):
        assert fn in registered, f"{fn} is defined but not in CHECKS, so main() never calls it"
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


# THE SIX REAL SHAPES a stock/flow pair takes in this store, one per adapter that writes one.
# Written out rather than generated, because the POINT is that they differ: the suffix ordering,
# the annotation, the stitched backfill and the non-EVM source strings are exactly what a
# resolution keyed on the wrong thing falls over on.
SIX_SHAPES = [
    ("Uniswap",     "chain:ethereum:burn_dead",              "chain:ethereum:burn_dead:delta"),
    ("PancakeSwap", "chain:bsc:burn_dead",                   "chain:bsc:burn_dead:delta"),
    ("Venice AI",   "chain:base:burn_zero",                  "chain:base:burn_zero:delta"),
    ("Hyperliquid", "hypercore_info:spotClearinghouseState", "hypercore_info:spotClearinghouseState:delta"),
    ("Tron",        "tron_node:getburntrx",                  "tron_node:getburntrx:delta"),
    # GEODNET IS THE AWKWARD ONE ON PURPOSE: a Dune backfill with NO :delta marker sits under a
    # live chain delta, so the flow series is stitched from two shapes and only one of them
    # telescopes. The anchor must be the stock before the first DELTA row, not before the first
    # row of the series — those are different dates and the Dune rows are months earlier.
    ("GEODNET",     "chain:polygon:burn_polygon",            "chain:polygon:burn_polygon:delta"),
]


def _stock_flow_frame(project, stock_src, flow_src, stock_metric="burn_address_balance",
                      flow_metric="gross_burn_tokens", dune_rows=False):
    """A clean, telescoping stock/flow pair: three stock readings and the two deltas between."""
    import pandas as pd
    stock = [(f"2026-09-1{i}", 1_000_000.0 + i * 5_000) for i in (7, 8, 9)]
    rows = [{"date": pd.Timestamp(d), "project": project, "metric": stock_metric, "value": v,
             "source": stock_src, "tier": 2, "is_manual": False, "entered_on": ""}
            for d, v in stock]
    for (d0, v0), (d1, v1) in zip(stock, stock[1:]):
        rows.append({"date": pd.Timestamp(d1), "project": project, "metric": flow_metric,
                     "value": v1 - v0, "source": flow_src, "tier": 2,
                     "is_manual": False, "entered_on": ""})
    if dune_rows:
        # Months earlier, no :delta marker — a backfilled period figure, not a difference.
        rows.append({"date": pd.Timestamp("2026-05-01"), "project": project, "metric": flow_metric,
                     "value": 4_000_000.0, "source": "dune:8683175", "tier": 4,
                     "is_manual": False, "entered_on": ""})
    return pd.DataFrame(rows)


def test_the_telescoping_check_resolves_its_anchor_for_every_real_source_shape():
    """The SQL preview came back blank for five of six projects — only Uniswap computed a
    stock_at_start. This asserts the PYTHON mechanism resolves for all six, because a guard that
    cannot see the project it was built for is worse than no guard: it reads as a pass.

    All six shapes differ in ways a resolution keyed on the wrong thing falls over on — the
    non-EVM source strings, and GEODNET's stitched series where a Dune backfill with no :delta
    marker sits months under a live chain delta.
    """
    import build_workbook as bw

    for project, stock_src, flow_src in SIX_SHAPES:
        long = _stock_flow_frame(project, stock_src, flow_src,
                                 dune_rows=(project == "GEODNET"))
        flows = long[long.metric == "gross_burn_tokens"]
        stock = long[long.metric == "burn_address_balance"]
        got = bw.telescoping(project, "gross_burn_tokens", "burn_address_balance", flows, stock)
        assert got["flow_recon_blocked"] is None, \
            f"{project}: the check must RUN — {got['flow_recon_blocked']}"
        assert got["flow_stock_move"] == 10_000.0, f"{project}: {got['flow_stock_move']}"
        assert got["flow_accounted"] == 10_000.0, f"{project}: {got['flow_accounted']}"
        assert got["flow_residual"] == 0.0, f"{project}: residual {got['flow_residual']}"
        assert got["flow_span"] == ("2026-09-17", "2026-09-19"), f"{project}: {got['flow_span']}"
        # AND IT REACHES THE SHEET: the same computation, run inside aggregate, leaves the row
        # unblocked rather than silently skipped.
        out = bw.aggregate(long, pd.DataFrame(), pd.Timestamp("2026-09-20"),
                           gaps=pd.DataFrame(), review=pd.DataFrame())
        row = out[(out.project == project) & (out.metric == "gross_burn_tokens")].iloc[0]
        assert "RECONCILIATION DID NOT RUN" not in str(row["why_amber"]), project
    print(f"anchor ok: resolves for all {len(SIX_SHAPES)} shapes including GEODNET's stitched series")


def test_a_blocked_telescoping_check_says_so_instead_of_reading_as_a_pass():
    """** THE SILENT SKIP IS THE BUG, NOT THE MISSING ANCHOR. ** A row whose reconciliation never
    ran is indistinguishable on the sheet from one that ran and passed, and that is precisely how
    five of six projects could have shown nothing wrong while nothing had been checked."""
    import build_workbook as bw

    # THE ANCHOR CUT OFF: stock rows deleted from under a flow series that was derived from them.
    # A delta is computed from a reading STRICTLY EARLIER than itself, so that reading existed
    # when the flow was written — its absence is evidence of a deletion, not of a clean start.
    long = _stock_flow_frame("Uniswap", "chain:ethereum:burn_dead", "chain:ethereum:burn_dead:delta")
    cut = long[~((long.metric == "burn_address_balance") & (long.date == pd.Timestamp("2026-09-17")))]
    got = bw.telescoping("Uniswap", "gross_burn_tokens", "burn_address_balance",
                         cut[cut.metric == "gross_burn_tokens"],
                         cut[cut.metric == "burn_address_balance"])
    assert "flow_stock_move" not in got, "nothing can be computed without the anchor"
    assert "NO STOCK READING BEFORE THE FIRST FLOW" in got["flow_recon_blocked"]
    assert "not in the store now" in got["flow_recon_blocked"]
    # AND IT REACHES THE READER, through the same path every other disclosure takes.
    out = bw.aggregate(cut, pd.DataFrame(), pd.Timestamp("2026-09-20"),
                       gaps=pd.DataFrame(), review=pd.DataFrame())
    row = out[(out.project == "Uniswap") & (out.metric == "gross_burn_tokens")].iloc[0]
    assert "THE FLOW RECONCILIATION DID NOT RUN" in str(row["why_amber"]), row["why_amber"]

    # THE STOCK SERIES ABSENT ENTIRELY — a different cause, and it says which.
    got = bw.telescoping("Uniswap", "gross_burn_tokens", "burn_address_balance",
                         long[long.metric == "gross_burn_tokens"], long[long.metric == "nothing"])
    assert "is EMPTY for this project" in got["flow_recon_blocked"], got

    # AND A BACKFILLED PERIOD FIGURE IS CORRECTLY EXEMPT rather than reported as a failure: it is
    # not a difference of two readings, so there is nothing to telescope.
    dune = _stock_flow_frame("GEODNET", "chain:polygon:burn_polygon", "dune:8683175")
    got = bw.telescoping("GEODNET", "gross_burn_tokens", "burn_address_balance",
                         dune[dune.metric == "gross_burn_tokens"],
                         dune[dune.metric == "burn_address_balance"])
    assert "no differenced rows" in got["flow_recon_blocked"]
    assert "correctly exempt" in got["flow_recon_blocked"]
    print("blocked ok: cut-off anchor, absent stock and exempt backfill each named distinctly")


def test_every_declared_flow_has_a_stock_the_check_can_find():
    """THE HAND-MAINTAINED INVERSE HAD ALREADY GONE STALE, which is why it is derived now.

    DERIVED_FLOW_STOCK was a literal dict written when burn_address_balance was the only
    cumulative in the book, carrying a comment that it was explicit "because getting it wrong in
    the derived direction would silently disarm the guard". Getting it wrong by hand is what
    happened: Chainlink's Reserve inflow and Sky's two decomposed burn legs arrived on
    2026-09-22 and had no entry, so the telescoping and implausible-delta checks never ran for
    them at all. No answer, silently.
    """
    import build_workbook as bw

    missing = []
    for p in config.PROJECTS:
        for stock, flow in {**config.CUMULATIVE_FLOW, **(p.get("cumulative_flow") or {})}.items():
            if not flow:
                continue
            if bw.flow_stock(p["name"], flow) != stock:
                missing.append((p["name"], stock, flow, bw.flow_stock(p["name"], flow)))
    assert not missing, f"every declared flow must invert back to its own stock: {missing}"

    # THE THREE THAT WERE INVISIBLE, named so this cannot regress quietly.
    assert bw.flow_stock("Chainlink", "actual_buyback_tokens") == "buyback_fund_balance"
    assert bw.flow_stock("Sky", "governance_burn_tokens") == "governance_burn_balance"
    assert bw.flow_stock("Sky", "other_burn_tokens") == "other_burn_balance"
    # AND THE ALIAS still resolves through its parent: burn_revenue_funded is a re-labelled copy
    # of gross_burn_tokens, not a separate differencing of a separate stock.
    assert bw.flow_stock("Venice AI", "burn_revenue_funded") == "burn_address_balance"
    # A PROJECT THAT DECLARES NO SUCH FLOW gets None, not a global default that does not apply.
    assert bw.flow_stock("Maple", "actual_buyback_tokens") is None
    print("inverse ok: derived from the live mapping, all three previously-invisible flows found")


def _m2_statement() -> str:
    """Section M's telescoping SELECT, taken from the file rather than retyped here.

    A test that keeps its own copy of the query proves nothing about the query that runs.
    """
    import run_sql
    sql = (Path(__file__).resolve().parent.parent / "orphan_cleanup.sql").read_text(encoding="utf-8")
    section = run_sql.parse_sections(sql)["M"]["text"]
    stmts = [run_sql.strip_comments(s) for s in run_sql.split_statements(section)]
    hits = [s for s in stmts if "stock_at_start" in s]
    assert len(hits) == 1, f"expected exactly one telescoping SELECT in section M, found {len(hits)}"
    return hits[0]


def _seed_six_shapes(path) -> None:
    """The same six source shapes the Python test uses, in a real store this time."""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE metrics (
        date TEXT NOT NULL, project TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
        source TEXT NOT NULL, tier INTEGER, fetched_at TEXT NOT NULL,
        PRIMARY KEY (date, project, metric))""")
    rows = []
    for project, stock_src, flow_src in SIX_SHAPES:
        stock = [(f"2026-09-1{i}", 1_000_000.0 + i * 5_000) for i in (7, 8, 9)]
        for d, v in stock:
            rows.append((d, project, "burn_address_balance", v, stock_src, 2, "2026-09-20T00:00:00"))
        for (d0, v0), (d1, v1) in zip(stock, stock[1:]):
            rows.append((d1, project, "gross_burn_tokens", v1 - v0, flow_src, 2,
                         "2026-09-20T00:00:00"))
        if project == "GEODNET":
            rows.append(("2026-05-01", project, "gross_burn_tokens", 4_000_000.0, "dune:8683175",
                         4, "2026-09-20T00:00:00"))
    conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_the_cleanup_sql_resolves_the_same_anchor_the_python_does(tmp_path):
    """** THE FIRST DIAGNOSIS OF THE BLANK stock_at_start WAS WRONG, and this test is what
    established that. **

    Running section M against the live store returned a blank stock_at_start for five of six
    projects — GEODNET, Hyperliquid, PancakeSwap, Tron and Venice AI — with only Uniswap
    computing. That was written up as a query fault of the J3 class. It is not one: seeded with
    all six real source shapes, the ORIGINAL query resolved the anchor for every one of them.

    So the blanks are the answer, not a failure to produce one — those five have no
    burn_address_balance reading dated strictly before their first differenced flow row. The
    second half of this test reproduces the observed pattern by truncating one project's stock
    series, and asserts the rewrite NAMES that cause instead of returning an empty cell, which
    is the defect that was actually worth fixing.
    """
    db = tmp_path / "six.db"
    _seed_six_shapes(db)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    got = {r["project"]: dict(r) for r in conn.execute(_m2_statement())}

    assert set(got) == {p for p, _, _ in SIX_SHAPES}, \
        f"every shape must produce a row, missing {[p for p, _, _ in SIX_SHAPES if p not in got]}"
    for project, _, _ in SIX_SHAPES:
        r = got[project]
        assert r["stock_at_start"] is not None, \
            f"{project}: stock_at_start blank — {r['diagnostic']}"
        assert r["start_date"] == "2026-09-17", f"{project}: anchored on {r['start_date']}"
        assert r["stock_moved"] == 10_000.0, f"{project}: {r['stock_moved']}"
        assert r["residual"] == 0.0, f"{project}: residual {r['residual']}"
        assert r["diagnostic"] == "RECONCILES", f"{project}: {r['diagnostic']}"

    # GEODNET'S STITCHED SERIES IS THE SHAPE THAT BREAKS A NAIVE ANCHOR. Its Dune backfill sits
    # months before the first delta and carries no :delta marker, so a query that anchors on the
    # first row of the flow series looks for a stock reading before 2026-05-01, finds none, and
    # reports a blank. The anchor is the first DIFFERENCED row.
    assert got["GEODNET"]["first_flow"] == "2026-09-18", got["GEODNET"]
    assert got["GEODNET"]["flow_rows"] == 2, "the 4,000,000 Dune row must not be summed in"
    assert got["GEODNET"]["flows_recorded"] == 10_000.0, got["GEODNET"]

    # THE OBSERVED PATTERN, REPRODUCED: cut one project's stock series back to start ON its first
    # flow date and that project blanks while the rest still compute. One project computing and
    # five not is what a store in this state looks like — not what a broken lookup looks like.
    conn.execute("DELETE FROM metrics WHERE project='Tron' AND metric='burn_address_balance'"
                 " AND date='2026-09-17'")
    after = {r["project"]: dict(r) for r in conn.execute(_m2_statement())}
    assert after["Tron"]["stock_at_start"] is None
    assert after["Uniswap"]["diagnostic"] == "RECONCILES", "only the truncated project blanks"

    # AND THE BLANK IS NAMED. The two causes have different remedies, so the diagnostic reports
    # where the stock series starts rather than asserting a deletion it cannot know about.
    d = after["Tron"]["diagnostic"]
    assert d.startswith("CANNOT BE CHECKED"), d
    assert "the stock series starts 2026-09-18" in d, d
    assert "first differenced flow 2026-09-18" in d, d
    assert "deleted" in d and "wrote no balance" in d, "both causes named, neither asserted"
    assert after["Tron"]["first_stock"] == "2026-09-18", "first_stock is what separates them"

    # A STOCK SERIES ABSENT ALTOGETHER is a third, distinct answer.
    conn.execute("DELETE FROM metrics WHERE project='Tron' AND metric='burn_address_balance'")
    gone = {r["project"]: dict(r) for r in conn.execute(_m2_statement())}["Tron"]
    assert "NO STOCK SERIES AT ALL" in gone["diagnostic"], gone["diagnostic"]
    conn.close()
    print(f"cleanup sql ok: anchor resolves for all {len(SIX_SHAPES)} shapes; each blank names its cause")


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
    # The metric is locked_tokens since 2026-09-23: staked_tokens was the same quantity under a
    # second name with no route to it, and was merged into the one every contract read writes.
    assert set(config.ARCHETYPE_NAMES) == {1, 2, 3, 4}
    assert "locked_tokens" in config.metrics_for_project(p)
    assert "staked_tokens" not in config.METRICS

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


# ============================================================================================
# REBUILDING A DIFFERENCED FLOW FROM THE STOCK IT CAME FROM
# ============================================================================================

def _hyperliquid_store(tmp_path, flow_rows=None):
    """Hyperliquid's real shape: a sound daily balance series under a corrupted flow series.

    The balances are one surviving reading per date — the last run of each day — and were never
    differenced, so the same-day re-run bug could not touch them. The flow row for 2026-09-21
    holds only the LAST run's increment, because four runs that day each overwrote the one before.
    """
    import sqlite3
    import store as store_mod
    db = tmp_path / "hl.db"
    conn = sqlite3.connect(db)
    conn.executescript(store_mod.SCHEMA)
    src = "hypercore_info:spotClearinghouseState"
    bal = [("2026-09-11", 47_155_473.0), ("2026-09-12", 47_180_000.0), ("2026-09-13", 47_205_000.0),
           ("2026-09-16", 47_280_000.0), ("2026-09-17", 47_300_000.0), ("2026-09-18", 47_320_000.0),
           ("2026-09-21", 47_387_407.0)]
    rows = [(d, "Hyperliquid", "burn_address_balance", v, src, 1, "2026-09-21T18:00:00")
            for d, v in bal]
    flow = flow_rows if flow_rows is not None else [
        ("2026-09-12", 24_527.0), ("2026-09-13", 25_000.0), ("2026-09-16", 75_000.0),
        ("2026-09-17", 20_000.0), ("2026-09-18", 20_000.0), ("2026-09-21", 83_344.4791)]
    rows += [(d, "Hyperliquid", "gross_burn_tokens", v, f"{src}:delta", 1, "2026-09-21T18:00:00")
             for d, v in flow]
    conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO run_log(run_id, ts, source, tier, project, rows, status, message)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(f"r{i}", f"{d}T{h:02d}:00:00", "hypercore_info", 1, "Hyperliquid", 2, "ok", "")
         for i, (d, h) in enumerate([("2026-09-16", 9), ("2026-09-17", 9), ("2026-09-18", 9),
                                     ("2026-09-21", 9), ("2026-09-21", 12), ("2026-09-21", 15),
                                     ("2026-09-21", 18)])])
    conn.commit()
    return conn


def test_a_corrupted_flow_series_is_rebuilt_from_its_stock_not_deleted(tmp_path):
    """** THE BALANCES WERE NEVER WRONG, so the history does not have to be thrown away. **

    The cleanup proposed deleting all eight differenced rows and letting the next run recover the
    span as a single figure. That is a real remedy and it costs the daily shape permanently. The
    flow is redundant information — delta(d) = stock(d) - stock(previous stored d) — so it
    rebuilds from readings the bug could not reach.
    """
    import rederive
    conn = _hyperliquid_store(tmp_path)
    plan = rederive.rederive_flow_from_stock(conn, "Hyperliquid", "gross_burn_tokens")

    assert plan.stock_metric == "burn_address_balance", "taken from config, not from the caller"
    assert plan.blocked is None and not plan.refusals
    # 47,387,407 - 47,155,473. The identity the whole exercise exists to satisfy.
    assert round(plan.new_total, 4) == 231_934.0
    assert round(plan.new_total, 4) == round(47_387_407.0 - 47_155_473.0, 4)
    assert round(plan.old_total, 4) == 247_871.4791, "what is stored over-counts"
    # THE CORRUPTED DATE IS THE ONE THAT MOVES, and only it.
    by_date = {p.date: p.value for p in plan.proposed}
    assert round(by_date["2026-09-21"], 4) == 67_407.0, "the day's true move, not the last run's"
    assert by_date["2026-09-16"] == 75_000.0, "an uncorrupted date rebuilds to what it already was"
    # A DATE WITH NO FLOW ROW IS NOT INVENTED: the first balance has nothing before it.
    assert "2026-09-11" not in by_date

    removed, written = rederive.apply_plan(conn, plan)
    assert removed == 6 and written == 6
    after = dict(conn.execute("SELECT date, value FROM metrics WHERE project='Hyperliquid'"
                              " AND metric='gross_burn_tokens' ORDER BY date").fetchall())
    assert round(sum(after.values()), 4) == 231_934.0
    assert round(after["2026-09-21"], 4) == 67_407.0
    # THE BALANCES ARE UNTOUCHED — they are the evidence, and the rebuild reads them.
    assert conn.execute("SELECT COUNT(*) FROM metrics WHERE project='Hyperliquid'"
                        " AND metric='burn_address_balance'").fetchone()[0] == 7
    print("rebuild ok: 231,934.0000 HYPE across the span, daily shape recovered, balances intact")


def test_a_rebuilt_row_is_not_read_as_a_change_of_measuring_point():
    """** THE MARKER HAS TO BE REGISTERED, NOT JUST WRITTEN. **

    A rebuilt row carries ':rederived' so its provenance is on the row. Every parser that pulls
    contract keys out of a source string filters the colon-delimited pieces against
    config.SOURCE_MARKERS and treats what is left as a contract key — and _measuring_point, which
    decides whether two readings measured the same thing, does the same. An unregistered piece
    would make the rebuilt row a DIFFERENT measuring point from the live one, so the very next
    run would refuse to difference against it and report a change of address that never happened:
    the rebuild would disarm the series it just repaired.
    """
    from fetch.base import _measuring_point
    assert "rederived" in config.SOURCE_MARKERS
    live = "hypercore_info:spotClearinghouseState:delta"
    rebuilt = "hypercore_info:spotClearinghouseState:delta:rederived"
    assert _measuring_point(live) == _measuring_point(rebuilt)
    assert not config.orphaned_contract_keys("Hyperliquid", rebuilt), \
        "':rederived' must not be mistaken for a contract key that is no longer in config"
    # AND IT STILL TELESCOPES: build_workbook finds differenced rows by containment, so the
    # trailing marker does not hide the row from the check that would catch a bad rebuild.
    assert ":delta" in rebuilt
    print("marker ok: a rebuilt row measures the same point and is still checked as a delta")


def test_the_rebuild_refuses_the_two_pairs_that_are_not_flows(tmp_path):
    """A difference is only a flow if both readings measured the same thing, and only a flow if
    the quantity moved in a direction it can move. Neither refusal is silent — a pair that yields
    no row says why, because a missing row and a rejected row look identical once printed."""
    import rederive
    conn = _hyperliquid_store(tmp_path)
    # THE MEASURING POINT MOVES mid-series: Uniswap's Firepit, in Hyperliquid's shape.
    conn.execute("UPDATE metrics SET source='hypercore_info:otherAccount'"
                 " WHERE project='Hyperliquid' AND metric='burn_address_balance'"
                 " AND date='2026-09-17'")
    # AND THE CUMULATIVE FALLS between two readings.
    conn.execute("UPDATE metrics SET value=47100000.0 WHERE project='Hyperliquid'"
                 " AND metric='burn_address_balance' AND date='2026-09-13'")
    conn.commit()
    plan = rederive.rederive_flow_from_stock(conn, "Hyperliquid", "gross_burn_tokens")
    refused = {p.date: p.refusal for p in plan.refusals}
    assert "the cumulative FELL" in refused["2026-09-13"], refused
    assert "measuring point CHANGED" in refused["2026-09-17"], refused
    # THE READING AFTER THE MOVED POINT IS REFUSED TOO — 09-18 differences against 09-17's new
    # address, so the pair straddles the change from the other side.
    assert "measuring point CHANGED" in refused["2026-09-18"], refused
    assert all(p.value is None for p in plan.refusals), "a refused pair proposes NO value"
    assert not any(p.value < 0 for p in plan.proposed), "no negative burn is ever proposed"
    print("refusals ok: a changed measuring point and a falling cumulative each yield no row, with"
          " the reason on the pair")


def test_the_rebuild_leaves_rows_that_are_not_differences_alone(tmp_path):
    """GEODNET's shape: a Dune backfill of real period totals stitched under a live chain delta.
    Those rows are evidence, not arithmetic — rebuilding them from a stock would replace a
    measured figure with a derived one and nothing on the sheet would say so."""
    import rederive
    conn = _hyperliquid_store(tmp_path)
    conn.execute("INSERT INTO metrics VALUES ('2026-05-01','Hyperliquid','gross_burn_tokens',"
                 "4000000.0,'dune:8683175',4,'2026-09-01T00:00:00')")
    conn.commit()
    plan = rederive.rederive_flow_from_stock(conn, "Hyperliquid", "gross_burn_tokens")
    assert "2026-05-01" in plan.untouched and "2026-05-01" not in plan.existing
    rederive.apply_plan(conn, plan)
    kept = conn.execute("SELECT value, source FROM metrics WHERE project='Hyperliquid'"
                        " AND metric='gross_burn_tokens' AND date='2026-05-01'").fetchone()
    assert kept == (4_000_000.0, "dune:8683175"), "the backfilled period figure survives untouched"
    print("scope ok: only :delta rows are replaced; a measured period total is left where it is")


def test_the_rebuild_refuses_a_flow_config_does_not_declare_a_stock_for(tmp_path):
    """The default comes from config.stock_for_flow, so a flow cannot be paired with a cumulative
    it was never differenced from. Guessing one would produce a plausible series out of two
    unrelated quantities."""
    import rederive
    conn = _hyperliquid_store(tmp_path)
    plan = rederive.rederive_flow_from_stock(conn, "Maple", "actual_buyback_tokens")
    assert plan.blocked and "no cumulative" in plan.blocked
    try:
        rederive.apply_plan(conn, plan)
    except ValueError as e:
        assert "blocked plan" in str(e)
    else:
        raise AssertionError("a blocked plan must never be applied")
    print("pairing ok: the stock comes from config, and an undeclared flow is refused")


def test_metrics_cannot_answer_how_many_runs_wrote_on_a_date(tmp_path):
    """** THE ORIGINAL M1 COULD NEVER RETURN A ROW, and it returned "(no rows)" on the section
    whose whole premise is four runs on one date. **

    It grouped `metrics` by (project, metric, date) — the table's PRIMARY KEY — and kept groups
    with more than one distinct fetched_at. Every group holds exactly one row, so the count is 1
    by construction. The upsert is why, and it is the same fact the section is about: a second
    run on one date overwrites the first, so `metrics` cannot hold the evidence of a re-run.
    """
    import rederive
    conn = _hyperliquid_store(tmp_path)
    dead = conn.execute(
        """SELECT project, metric, date FROM metrics
            WHERE metric = 'burn_address_balance'
            GROUP BY project, metric, date
           HAVING COUNT(DISTINCT fetched_at) > 1""").fetchall()
    assert dead == [], "the old query is not merely empty here — it is empty by construction"

    runs = rederive.runs_per_date(conn, "Hyperliquid", "burn_address_balance")
    assert runs["2026-09-21"] == 4, "run_log still has what metrics destroyed"
    assert runs["2026-09-16"] == runs["2026-09-17"] == runs["2026-09-18"] == 1
    # DATES BEFORE THE LOG REACHES ARE ABSENT, not reported as zero runs.
    assert "2026-09-11" not in runs and "2026-09-12" not in runs
    print("run count ok: 2026-09-21 x4, 09-16/17/18 single-run, earlier dates have no log to read")


# ============================================================================================
# GENESIS SUPPLY: ONE READING THAT SETTLES CUMULATIVE ISSUANCE
# ============================================================================================

def test_one_gross_reading_at_genesis_proves_cumulative_issuance_is_zero():
    """** SECTION L'S VERDICT WAS TOO CAUTIOUS, and the reason is arithmetic, not judgement. **

    Uniswap's 219,999.99 issuance row was marked "only one gross reading — cannot judge, leave
    it". But the single reading is EXACTLY the genesis supply, minting is the only thing that
    moves that figure up, and nothing moves it down. Cumulative issuance since deployment is
    therefore zero, and a quantity that is zero over all time is zero over every window inside
    it. A second reading adds nothing a proof already gives.
    """
    why = config.issuance_provably_zero("Uniswap", 1_000_000_000.0)
    assert why and "CUMULATIVE issuance is zero" in why
    assert "1,000,000,000" in why and "Uni.sol" in why, "the argument travels with the answer"

    # THE GENESIS FIGURE IS SOURCED, not recalled. Same discipline as a contract address.
    g = config.genesis_supply("Uniswap")
    assert g["tokens"] == 1_000_000_000
    assert g["source"].startswith("https://raw.githubusercontent.com/Uniswap/governance/")
    assert "1_000_000_000e18" in g["quote"], "the line from the contract, not a paraphrase"
    assert g["confirmed_on"] == "2026-09-22"

    # ONE TOKEN OFF AND IT IS NOT A PROOF. No tolerance: both sides are whole-token integers, so
    # a tolerance could only let a real mint through.
    assert config.issuance_provably_zero("Uniswap", 1_000_000_001.0) is None
    assert config.issuance_provably_zero("Uniswap", 999_999_999.0) is None
    assert config.issuance_provably_zero("Uniswap", None) is None
    print("genesis ok: gross == 1,000,000,000 exactly, so cumulative UNI issuance is provably zero")


def test_the_genesis_proof_refuses_every_project_it_does_not_hold_for():
    """The inference rests on four things and each one can be false. A rule that fired without
    them would turn a pre-minted token's flat supply into "nothing is being issued", which is the
    exact inversion GEODNET's n/a reason exists to prevent."""
    # GEODNET'S GROSS ALSO READS EXACTLY 1,000,000,000, so the arithmetic would "work" and the
    # conclusion would be false in the sense the column means: GEOD is entirely pre-minted and
    # emissions are DISTRIBUTION from mining wallets. Minting does not move its totalSupply, so
    # the figure would sit at genesis for ever while real tokens reached the market.
    assert config.genesis_supply("GEODNET") is None, \
        "not on file — and it must not be added without BOTH a source and the minting question"
    assert config.issuance_provably_zero("GEODNET", 1_000_000_000.0) is None
    na = config.PROJECT_BY_NAME["GEODNET"]["issuance_derivation"]["na_reason"]
    assert "DISTRIBUTION from pre-minted mining wallets, not minting" in na, \
        "GEODNET already says why the equality proves nothing there"

    # PANCAKESWAP: CAKE is a LayerZero OFT and the gross figure is a partial multi-chain sum, so
    # a partial that happened to equal genesis would be a coincidence, not a proof.
    assert config.genesis_supply("PancakeSwap") is None
    assert config.issuance_provably_zero("PancakeSwap", 1_000_000_000.0) is None

    # EVERY REQUIRED FIELD IS A REFUSAL WHEN MISSING, never a default — three of the four
    # conditions are things a token CAN do, and absent-means-false would turn "nobody looked"
    # into "it does not happen".
    p = config.PROJECT_BY_NAME["Uniswap"]
    whole = p["genesis_supply"]
    try:
        for field in config.GENESIS_PROOF_REQUIRES:
            p["genesis_supply"] = {k: v for k, v in whole.items() if k != field}
            assert config.issuance_provably_zero("Uniswap", 1_000_000_000.0) is None, \
                f"{field} missing must refuse the inference, not default it"
        # AND A REAL _burn WOULD BREAK IT: gross could return to genesis after minting and
        # burning the same amount, so equality would prove nothing.
        p["genesis_supply"] = {**whole, "burn_reduces_total_supply": True}
        assert config.issuance_provably_zero("Uniswap", 1_000_000_000.0) is None
    finally:
        p["genesis_supply"] = whole
    print("refusals ok: GEODNET, PancakeSwap and every missing condition each refuse the inference")


def test_the_cleanup_sqls_genesis_literals_match_config(tmp_path):
    """The SQL cannot call a Python function, so section L carries the genesis figure as a
    literal — and a number copied by hand is exactly the kind of thing that goes stale silently.
    Every occurrence is checked against config, so a change in one place fails here rather than
    deleting the wrong rows later."""
    section = _cleanup_sql_section("-- L. THE PHANTOM ISSUANCE ROWS")
    pairs = set(re.findall(r"'([A-Za-z][A-Za-z .]*)',\s*(\d+\.\d+)", section))
    assert pairs, "section L must carry its genesis figures as literals"
    for project, tokens in pairs:
        g = config.genesis_supply(project)
        assert g is not None, f"section L names {project!r}, which has no sourced genesis in config"
        assert float(tokens) == float(g["tokens"]), \
            f"{project}: SQL says {tokens}, config says {g['tokens']}"
    # AND EVERY SOURCED PROJECT IS PRESENT — a genesis recorded in config but absent from the SQL
    # is a verdict that silently stays too cautious.
    named = {p for p, _ in pairs}
    for p in config.PROJECTS:
        if config.genesis_supply(p["name"]):
            assert p["name"] in named, f"{p['name']} has a sourced genesis that section L ignores"
    # THE THREE PLACES IT APPEARS — L0/L3b, L3c and L5 — must all be there, so the delete cannot
    # be scoped differently from the preview that licensed it.
    assert section.count("1000000000.0") == 4, \
        "L0, L3b, L3c and L5 each carry the figure; a count change means one was edited alone"
    print(f"drift guard ok: section L's genesis literals match config for {sorted(named)}")


def _seed_issuance_store(tmp_path):
    """Uniswap pinned at genesis with phantom rows from two routes, plus two projects the proof
    must not touch."""
    import sqlite3
    import store as store_mod
    conn = sqlite3.connect(tmp_path / "iss.db")
    conn.executescript(store_mod.SCHEMA)
    conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-19", "Uniswap", "total_supply_gross", 1e9, "chain:ethereum:token", 2, "t"),
        ("2026-09-20", "Uniswap", "total_supply_gross", 1e9, "chain:ethereum:token", 2, "t"),
        ("2026-09-21", "Uniswap", "total_supply_gross", 1e9, "chain:ethereum:token", 2, "t"),
        ("2026-09-21", "Uniswap", "gross_issuance_tokens", 219_999.99, "derived:d_supply+burn", 2, "t"),
        ("2026-09-20", "Uniswap", "gross_issuance_tokens", 1_234.0, "derived:d_supply", 2, "t"),
        ("2026-09-19", "Uniswap", "gross_issuance_tokens", 0.0, "derived:d_supply", 2, "t"),
        ("2026-09-21", "GEODNET", "total_supply_gross", 1e9, "chain:polygon:token", 2, "t"),
        ("2026-09-21", "GEODNET", "gross_issuance_tokens", 5_000.0, "derived:d_supply+burn", 2, "t"),
        ("2026-09-21", "Venice AI", "total_supply_gross", 114_897_403.56, "chain:base:token", 2, "t"),
        ("2026-09-21", "Venice AI", "gross_issuance_tokens", 900.0, "derived:d_supply+burn", 2, "t"),
    ])
    conn.commit()
    return conn


def test_section_l_reaches_its_decisive_verdict_and_the_delete_acts_on_it(tmp_path):
    """The verdict is only worth widening if the DELETE follows it. L4 can only reach rows
    carrying the retired formula's source string; the genesis proof condemns a positive issuance
    row whatever route wrote it, so L5 is what turns the argument into a cleanup."""
    import sqlite3
    import run_sql
    conn = _seed_issuance_store(tmp_path)
    conn.row_factory = sqlite3.Row
    section = _cleanup_sql_section("-- L. THE PHANTOM ISSUANCE ROWS")
    selects = [run_sql.strip_comments(s) for s in run_sql.split_statements(section)
               if run_sql.classify(s) == "select"]

    verdicts = {r["project"]: r["verdict"] for r in conn.execute(selects[0])}
    assert "PHANTOM — gross supply is EXACTLY the genesis supply" in verdicts["Uniswap"]
    assert "cannot judge, leave it" in verdicts["GEODNET"], "no genesis on file, no verdict"
    assert "cannot judge, leave it" in verdicts["Venice AI"]

    condemned = [(r["project"], r["date"], r["source"])
                 for r in conn.execute(next(s for s in selects if "WOULD DELETE" in s))]
    assert ("Uniswap", "2026-09-21", "derived:d_supply+burn") in condemned
    assert ("Uniswap", "2026-09-20", "derived:d_supply") in condemned, \
        "the widening is the point — a phantom from the SURVIVING route is still a phantom"
    assert not [c for c in condemned if c[0] in ("GEODNET", "Venice AI")]
    assert not [c for c in condemned if c[1] == "2026-09-19"], "a true zero is left alone"

    # AND THE DELETES RUN, with the preview run_sql would show. A DELETE whose WHERE clause
    # cannot be previewed against its own table is refused by run_sql, so this also asserts the
    # statement is shaped so that what is printed is what goes.
    writes = [run_sql.strip_comments(s)
              for s in run_sql.split_statements("\n".join(run_sql.uncommented_write(section)))
              if run_sql.classify(s) == "write"]
    assert len(writes) == 2, "L4 for the retired formula, L5 for the genesis proof"
    for d in writes:
        where = d[d.upper().index(" WHERE ") + 7:]
        conn.execute(f"SELECT * FROM metrics WHERE {where}").fetchall()   # the preview must run
        conn.execute(d)
    conn.commit()

    left = conn.execute("SELECT date, project, value FROM metrics"
                        " WHERE metric='gross_issuance_tokens' ORDER BY project, date").fetchall()
    assert [tuple(r) for r in left] == [("2026-09-19", "Uniswap", 0.0)], \
        "only the true zero survives; Venice's row went with the retired formula, as L4 intends"
    assert conn.execute("SELECT COUNT(*) FROM metrics"
                        " WHERE metric='total_supply_gross'").fetchone()[0] == 5, \
        "the supply readings are the evidence and are never touched"
    conn.close()
    print("section L ok: the verdict is decisive and both deletes act on exactly what they printed")


# ============================================================================================
# BLOCKED READS: A PER-METHOD RPC REFUSAL, AND A GUARD THAT COULD NOT SEE A DERIVED FLOW
# ============================================================================================

class _RefusingLogs:
    """An RPC that connects, answers everything else, and returns 403 to eth_getLogs.

    That is not a hypothetical shape — it is ethereum-rpc.publicnode.com, the first entry in the
    ethereum fallback list, which is why Sky's burn read failed with three working alternatives
    sitting unused.
    """

    def __init__(self, refuse_urls, range_cap=None):
        self.refuse_urls, self.range_cap = set(refuse_urls), range_cap
        self.calls = []

    class _Eth:
        def __init__(self, outer, url):
            self.outer, self.url = outer, url
            self.block_number = 1_000_000

        def get_logs(self, params):
            span = params["toBlock"] - params["fromBlock"] + 1
            self.outer.calls.append((self.url, span))
            if self.url in self.outer.refuse_urls:
                raise Exception("403 Client Error: Forbidden for url: " + self.url)
            if self.outer.range_cap and span > self.outer.range_cap:
                raise Exception("query returned more than 10000 results")
            return []

    def provider_for(self, url):
        class _W3:
            pass
        w3 = _W3()
        w3.eth = self._Eth(self, url)
        w3.provider = type("P", (), {"endpoint_uri": url})()
        return w3


def _reader_with(monkeypatch, stub, urls):
    """A real ChainReader wired to the stub, with the endpoint list under test."""
    from fetch import chain as chain_mod
    monkeypatch.setattr(chain_mod, "rpc_endpoints", lambda c: list(urls))
    monkeypatch.setattr(chain_mod, "Web3", None, raising=False)
    reader = chain_mod.ChainReader()
    reader._w3["ethereum"] = stub.provider_for(urls[0])

    import sys
    import types
    fake = types.ModuleType("web3")
    fake.Web3 = lambda provider, **kw: stub.provider_for(provider)
    fake.HTTPProvider = lambda url, **kw: url
    monkeypatch.setitem(sys.modules, "web3", fake)
    return reader


def test_an_rpc_that_refuses_one_method_falls_over_to_the_next_endpoint(monkeypatch):
    """** THE FALLBACK LIST WAS ONLY CONSULTED AT CONNECT TIME, WHICH IS THE WRONG MOMENT. **

    ChainReader.web3 tries each endpoint until one answers is_connected() and then keeps it for
    every call on that chain. publicnode serves eth_blockNumber, eth_call and eth_getCode and
    returns 403 to eth_getLogs — so it is chosen, everything else on Ethereum works, and only the
    log scan dies, with three usable providers in the list it never reaches.
    """
    urls = ["https://publicnode.example", "https://second.example", "https://third.example"]
    stub = _RefusingLogs(refuse_urls={urls[0]})
    reader = _reader_with(monkeypatch, stub, urls)

    logs, upper, chunk = reader._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []},
                                                    0, 50_000, 10_000)
    assert logs == [] and upper == 9_999 and chunk == 10_000
    assert [u for u, _ in stub.calls] == [urls[0], urls[1]], \
        "the refusing endpoint is tried once, then the next one — not hammered round the list"
    # ** WHICH ENDPOINT SERVED IT, AND WHICH REFUSED FIRST. ** The run of 2026-09-22 reported
    # Sky's burn read as "403 from publicnode" and the log could not say whether the failover then
    # found a working endpoint or ran out of them. Those are different problems — one is nothing
    # to do, the other needs RPC_ETHEREUM set — so the scan records both.
    # ** RECORDED BY HOST, NEVER BY URL. ** A keyed endpoint is https://<host>/v2/<key>, and
    # these fields go into the run log, the Run Log tab and the failover messages — all of which
    # a human reads and pastes. The host answers the only question asked of the field (which
    # provider), so cutting the path off loses nothing and stops the key travelling.
    from fetch.chain import rpc_host
    assert reader.log_endpoint_used["ethereum"] == rpc_host(urls[1]) == "second.example"
    assert reader.log_endpoints_refused["ethereum"][0].startswith("publicnode.example")
    assert "403" in reader.log_endpoints_refused["ethereum"][0]
    assert not any("https://" in v for v in [reader.log_endpoint_used["ethereum"]]
                   + reader.log_endpoints_refused["ethereum"]), \
        "a full URL in any of these fields is an API key in a log line"

    # EVERY ENDPOINT REFUSING IS A DIFFERENT ANSWER FROM A NARROWER RANGE, and says so: the
    # remedy is an endpoint that serves logs, not a smaller chunk.
    stub2 = _RefusingLogs(refuse_urls=set(urls))
    reader2 = _reader_with(monkeypatch, stub2, urls)
    try:
        reader2._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []}, 0, 50_000, 10_000)
    except RuntimeError as e:
        assert "ALL 3 configured ethereum RPC endpoint(s) refused eth_getLogs" in str(e)
        assert "PER-METHOD refusal" in str(e) and "not a narrower range" in str(e)
        # NAMED IN ORDER, WITH WHAT EACH ONE SAID — BY HOST. "403 from publicnode" was ambiguous
        # precisely because it named one endpoint out of four and gave no verdict on the rest.
        for u in urls:
            assert rpc_host(u) in str(e), f"{rpc_host(u)} refused and is not named in the failure"
        # ** AND NOT ONE FULL URL ANYWHERE IN IT. ** Redacting the endpoint field is not enough
        # on its own: a provider's exception embeds the request URI ("403 Client Error for url:
        # https://<host>/v2/<key>"), and that text is interpolated into this very message. The
        # first version of this redaction did exactly that and this assertion is what caught it.
        assert "https://" not in str(e) and "http://" not in str(e), \
            "a full URL in the failure text is an API key in a log line"
        assert "_RPC_URL in .env" in str(e), "and the remedy names the PREPEND, not the replace"
        assert len({u for u, _ in stub2.calls}) == 3, "each endpoint tried exactly once"
        assert len(reader2.log_endpoints_refused["ethereum"]) == 3
        assert "ethereum" not in reader2.log_endpoint_used, "nothing served it"
    else:
        raise AssertionError("all endpoints refusing must raise, not return an empty scan")
    print("rpc fallback ok: a per-method 403 moves to the next endpoint; all refusing says why")


def test_a_keyed_endpoint_is_preferred_without_discarding_the_public_fallbacks(monkeypatch):
    """** PREPEND, NOT REPLACE — AND REPLACE WAS THE ONLY OPTION BEFORE. **

    The one method that needs a keyed endpoint is eth_getLogs: publicnode answers eth_call and
    eth_getCode perfectly and returns 403 to logs, which is why four Sky burn rows gapped for
    days. The existing RPC_<CHAIN> override REPLACES the list, so getting logs that way would
    have thrown away four working endpoints for every other call on the chain.

    ETHEREUM_RPC_URL is tried FIRST and the public list stays behind it, so one provider's
    outage or rate limit does not take the run down.
    """
    from fetch.chain import rpc_endpoints, rpc_host, redact_urls
    KEYED = "https://eth-mainnet.g.alchemy.com/v2/SUPERSECRETKEY"

    monkeypatch.delenv("RPC_ETHEREUM", raising=False)
    monkeypatch.setenv("ETHEREUM_RPC_URL", KEYED)
    eps = rpc_endpoints("ethereum")
    assert eps[0] == KEYED, "the keyed endpoint is tried first"
    assert eps[1:] == config.DEFAULT_RPC["ethereum"], "and the public list is kept, in order"

    # ** NOT ONE OF THESE FIELDS MAY CARRY THE PATH. ** The key lives in it.
    assert rpc_host(KEYED) == "eth-mainnet.g.alchemy.com"
    assert "SUPERSECRETKEY" not in rpc_host(KEYED)
    assert "SUPERSECRETKEY" not in redact_urls(f"403 Client Error for url: {KEYED}")

    # DEDUPLICATED, ORDER PRESERVED — a keyed endpoint that is also a default is tried once.
    monkeypatch.setenv("ETHEREUM_RPC_URL", config.DEFAULT_RPC["ethereum"][2])
    eps = rpc_endpoints("ethereum")
    assert eps[0] == config.DEFAULT_RPC["ethereum"][2]
    assert len(eps) == len(config.DEFAULT_RPC["ethereum"]), f"duplicated: {eps}"
    assert len(set(eps)) == len(eps)

    # RPC_<CHAIN> STILL REPLACES. Two knobs, two meanings, and the escape hatch keeps working.
    monkeypatch.setenv("RPC_ETHEREUM", "https://only.example")
    assert rpc_endpoints("ethereum") == ["https://only.example"], \
        "RPC_<CHAIN> is the 'use exactly these' override and must still win outright"

    # AND WITH NEITHER SET, NOTHING CHANGES FROM BEFORE.
    monkeypatch.delenv("RPC_ETHEREUM", raising=False)
    monkeypatch.delenv("ETHEREUM_RPC_URL", raising=False)
    assert rpc_endpoints("ethereum") == config.DEFAULT_RPC["ethereum"]
    print("rpc preference ok: keyed endpoint first, publics behind it, host-only in every field")


def test_an_http_403_is_already_a_per_method_refusal_and_not_an_unclassified_error():
    """** THE PROPOSED MECHANISM FOR SKY'S FAILURE WAS NOT THE MECHANISM. **

    The reading was that an HTTP 403 arrives as a requests.HTTPError rather than a JSON-RPC error
    object, so its text would not match the refusal list and it would fall into "anything else
    raises" — never reaching the failover. It does match. web3's session manager calls
    raise_for_status(), and the exception it raises reads "403 Client Error: Forbidden for url:
    ...", which contains both "403" and "forbidden".

    So the failover WAS reached, and "403 from publicnode" in the run log means every Ethereum
    endpoint in the list refused, not that one did and the code gave up. That is a different
    problem with a different remedy — RPC_ETHEREUM pointing at a provider that serves logs — and
    it is why the scan now records which endpoint served it and which refused first.

    Pinned as a test because the next person to read that log entry will form the same hypothesis.
    """
    import requests
    from fetch.chain import ChainReader

    r = requests.Response()
    r.status_code, r.reason = 403, "Forbidden"
    r.url = "https://ethereum-rpc.publicnode.com"
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        msg = str(e).lower()
        assert any(sub in msg for sub in ChainReader.LOGS_ENDPOINT_REFUSED), msg
        # AND IT IS NOT MISTAKEN FOR A RANGE PROBLEM, which would halve the chunk and retry the
        # same refusing endpoint all the way down to the floor.
        assert not any(sub in msg for sub in ChainReader.LOGS_RANGE_TOO_WIDE), msg
    else:
        raise AssertionError("raise_for_status did not raise on 403")
    print("403 ok: an HTTPError classifies as a per-method refusal, not an unclassified error")


def test_a_range_the_server_will_not_serve_is_narrowed_not_retried_blindly(monkeypatch):
    """Halving is a REPLY to what the server said, not a guess. The two failures are told apart
    by the error text, because moving endpoint on a range error would ask four providers the same
    oversized question, and narrowing on a 403 would shrink the scan for ever against a provider
    that was never going to answer."""
    from fetch import chain as chain_mod
    urls = ["https://only.example"]
    stub = _RefusingLogs(refuse_urls=set(), range_cap=2_500)
    reader = _reader_with(monkeypatch, stub, urls)

    logs, upper, chunk = reader._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []},
                                                    0, 50_000, 10_000)
    assert chunk == 2_500 and upper == 2_499, f"narrowed to {chunk}"
    assert [span for _, span in stub.calls] == [10_000, 5_000, 2_500], \
        "halved on each refusal — 10,000 then 5,000 then 2,500, not re-tried at the same size"
    # AND THE SIZE THAT WORKED IS RECORDED, so the scan reports what it cost rather than what it
    # asked for.
    assert reader.log_chunk_used["ethereum"] == 2_500

    # A FLOOR, NOT AN INFINITE CLIMB DOWN. A scan needing thousands of chunks is a configuration
    # answer, not a retry answer, so below the floor it raises.
    tiny = _RefusingLogs(refuse_urls=set(), range_cap=1)
    reader2 = _reader_with(monkeypatch, tiny, urls)
    try:
        reader2._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []}, 0, 50_000, 10_000)
    except Exception as e:
        assert "query returned more than" in str(e), e
    else:
        raise AssertionError("narrowing must stop at the floor rather than shrink for ever")
    assert min(span for _, span in tiny.calls) == chain_mod.ChainReader.MIN_LOG_CHUNK

    # AND AN ERROR THAT IS NEITHER IS RAISED AT ONCE. A decoding fault must not be papered over
    # by trying another provider, which turns one clear failure into four vague ones.
    class _Broken(_RefusingLogs):
        class _Eth(_RefusingLogs._Eth):
            def get_logs(self, params):
                self.outer.calls.append((self.url, 0))
                raise ValueError("could not decode topic")
    broken = _Broken(refuse_urls=set())
    reader3 = _reader_with(monkeypatch, broken, urls * 3)
    try:
        reader3._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []}, 0, 50_000, 10_000)
    except ValueError:
        assert len(broken.calls) == 1, "raised on the first failure, not after a tour of the list"
    else:
        raise AssertionError("an unrecognised error must propagate")
    print("range narrowing ok: halved on the server's own limit, floored, and unrelated errors raise")


def test_a_400_whose_body_names_the_range_narrows_and_the_body_is_kept(monkeypatch):
    """** SKY'S SCAN HAD WORKING NARROWING LOGIC AND NEVER NARROWED ONCE. **

    With a keyed endpoint configured, Sky's burn read moved 403 -> 400: the method is now
    permitted and the REQUEST is being rejected. But requests' HTTPError stringifies to
    "400 Client Error: Bad Request for url: ..." with THE BODY DROPPED — and the body is where
    the provider names its own limit. So str(e) matched neither LOGS_RANGE_TOO_WIDE nor
    LOGS_ENDPOINT_REFUSED, the handler re-raised, and ~5.4m blocks were never split.

    Two fixes, and the first is the one that matters: read the body. Matching a message that has
    had its content removed is not matching.
    """
    import fetch.chain as chain_mod

    class _Resp:
        text = '{"error":{"code":-32602,"message":"block range exceeds 10000 blocks"}}'

    class _Body400(_RefusingLogs):
        class _Eth(_RefusingLogs._Eth):
            def get_logs(self, params):
                span = int(params["toBlock"]) - int(params["fromBlock"]) + 1
                self.outer.calls.append((self.url, span))
                if span > 10_000:
                    e = Exception("400 Client Error: Bad Request for url: "
                                  "https://eth-mainnet.g.alchemy.com/v2/SECRETKEY")
                    e.response = _Resp()
                    raise e
                return []

    stub = _Body400(refuse_urls=set())
    reader = _reader_with(monkeypatch, stub, ["https://a.example"])
    logs, upper, chunk = reader._get_logs_resilient(
        "ethereum", {"address": "0xabc", "topics": []}, 0, 5_400_000, 40_000)
    assert chunk == 10_000, f"narrowed to the provider's stated limit, got {chunk}"
    assert [span for _, span in stub.calls] == [40_000, 20_000, 10_000]

    # ** THE BODY IS KEPT, because the working chunk size is only interpretable next to the
    # complaint that produced it. A 400 whose body was never printed is what left this
    # unexplained for days.
    kept = reader.log_range_errors["ethereum"]
    assert kept and "block range exceeds 10000" in kept[0], kept
    # AND THE KEY IS NOT IN IT. The body arrives beside a URL that carries one.
    assert "SECRETKEY" not in " ".join(kept), "the error body is redacted like everything else"
    assert "https://" not in " ".join(kept)

    # A BARE 400 WITH NO BODY STILL NARROWS, and says it is INFERRING rather than reading.
    class _Bare400(_RefusingLogs):
        class _Eth(_RefusingLogs._Eth):
            def get_logs(self, params):
                span = int(params["toBlock"]) - int(params["fromBlock"]) + 1
                self.outer.calls.append((self.url, span))
                if span > 5_000:
                    raise Exception("400 Client Error: Bad Request")
                return []

    bare = _Bare400(refuse_urls=set())
    r2 = _reader_with(monkeypatch, bare, ["https://a.example"])
    _, _, chunk2 = r2._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []},
                                          0, 5_400_000, 20_000)
    assert chunk2 == 5_000, f"a bare 400 over a wide span is treated as a range limit: {chunk2}"

    # ** AND THE INFERENCE IS BOUNDED. ** At the floor it raises rather than shrinking for ever,
    # so an unexplained 400 can never become an unbounded retry loop.
    class _Always400(_RefusingLogs):
        class _Eth(_RefusingLogs._Eth):
            def get_logs(self, params):
                self.outer.calls.append((self.url, 0))
                raise Exception("400 Client Error: Bad Request")

    always = _Always400(refuse_urls=set())
    r3 = _reader_with(monkeypatch, always, ["https://a.example"])
    try:
        r3._get_logs_resilient("ethereum", {"address": "0xabc", "topics": []}, 0, 5_400_000, 20_000)
    except Exception:
        pass
    else:
        raise AssertionError("a 400 that survives narrowing to the floor must raise")
    assert len(always.calls) <= 12, f"bounded, not unbounded: {len(always.calls)} calls"
    print("400 handling ok: body read and kept, range narrowed to the stated limit, a bare 400 "
          "inferred and bounded at the floor")


def test_scan_logs_is_the_one_chunked_reader_and_advances_by_what_was_served(monkeypatch):
    """** THE TRAVERSAL EXISTED AND WAS NOT REUSABLE. ** Chunking, narrowing and endpoint
    failover were all written, fused inside the Transfer-specific burn reader — so the next
    event-based read would have had to copy them or go without. scan_logs is that traversal
    extracted, returning entries UNDECODED so a Transfer scan and a pool-outflow scan share it
    without sharing an ABI.

    ** IT ADVANCES BY WHAT THE SERVER SERVED, NOT BY WHAT WAS ASKED. ** If a chunk narrows
    mid-scan, advancing by the requested size would skip every block between the narrowed end
    and the assumed one — silently, and only on runs where narrowing happened, which is the
    hardest kind of gap to notice.
    """
    class _NarrowsOnce(_RefusingLogs):
        class _Eth(_RefusingLogs._Eth):
            def get_logs(self, params):
                lo, hi = int(params["fromBlock"]), int(params["toBlock"])
                self.outer.calls.append((lo, hi))
                if hi - lo + 1 > 10_000:
                    raise Exception("400: block range exceeds 10000 blocks")
                return [{"blockNumber": lo}]

    stub = _NarrowsOnce(refuse_urls=set())
    reader = _reader_with(monkeypatch, stub, ["https://a.example"])
    logs, chunks, used = reader.scan_logs("ethereum", {"address": "0xabc", "topics": []},
                                          0, 29_999, 20_000)
    assert used == 10_000 and chunks == 3, f"chunks={chunks} used={used}"
    served = [(lo, hi) for lo, hi in stub.calls if hi - lo + 1 <= 10_000]
    # CONTIGUOUS AND COMPLETE: every block from 0 to 29,999 covered exactly once.
    assert served == [(0, 9_999), (10_000, 19_999), (20_000, 29_999)], served
    assert len(logs) == 3, "entries are returned undecoded, all of them"
    print("scan_logs ok: one traversal, advances by what was served, no silent hole on narrowing")


def test_a_contract_that_serves_a_derived_flow_is_not_read_as_withdrawn():
    """** CHAINLINK'S BUYBACK RENDERED "MEASURING CONTRACT WITHDRAWN" ON A READ THAT WORKS. **

    config._flow_parents was a literal list naming gross_burn_tokens and burn_revenue_funded,
    both differenced from burn_address_balance, and knew about nothing else. Chainlink's
    actual_buyback_tokens is differenced from buyback_fund_balance, so the Reserve contract —
    which does serve the stock — was judged not to serve the flow.

    This is the SAME hazard as build_workbook's DERIVED_FLOW_STOCK, fixed the day before, in a
    second hand-maintained copy of the same relationship that was missed because it is spelled
    differently and lives in another file. Both are derived from config.stock_for_flow now.
    """
    assert config.withdrawn_contract_keys(
        "Chainlink", "actual_buyback_tokens", "chain:ethereum:reserve:delta") == []
    assert config.withdrawn_contract_keys(
        "Uniswap", "gross_burn_tokens", "chain:ethereum:burn_dead:delta") == []

    # A GENUINE WITHDRAWAL STILL READS AS ONE. The token contracts were repointed to
    # total_supply_gross by metric_override, so old total_supply rows must stay withheld.
    assert config.withdrawn_contract_keys(
        "Uniswap", "total_supply", "chain:ethereum:token") == ["token"]

    # AND EVERY DECLARED FLOW RESOLVES, so this cannot go stale again the way the literal did.
    for p in config.PROJECTS:
        for stock, flow in {**config.CUMULATIVE_FLOW, **(p.get("cumulative_flow") or {})}.items():
            assert stock in config._flow_parents(p["name"], flow), (p["name"], stock, flow)
    print("withdrawn guard ok: a flow's stock contract serves the flow, for every declared pair")


def test_one_contract_can_serve_several_metrics_and_the_guard_knows_it():
    """** SKY'S FOUR DECOMPOSED SERIES WERE ALL ABOUT TO RENDER WITHDRAWN. **

    The guard asked `metric_override or KIND_METRIC[kind]` and got ONE answer, which is right for
    a balance read and wrong for burn_transfer_logs: one contract, one scan, split by the event's
    sender into burn_address_balance and other_burn_balance. Only the first is what the KIND maps
    to, so the rest — and the flows derived from them — were judged to be written by a contract
    that no longer serves them.

    Latent rather than visible only because the read is currently 403ing, which is exactly the
    kind of bug that surfaces the day something else starts working.

    (The decomposition was THREE series until 2026-09-23, when governance_burn_balance turned out
    to be the Stage 2 leg mislabelled — see the decomposition test. The guard has to follow the
    config rather than a remembered list, which is why this asserts against contract_serves and
    not against a set typed out twice.)
    """
    spec = config.PROJECT_BY_NAME["Sky"]["contracts"]["burn_logs"]
    assert config.contract_serves(spec) == {"burn_address_balance", "other_burn_balance"}
    for metric in ("burn_address_balance", "other_burn_balance", "other_burn_tokens",
                   "gross_burn_tokens"):
        assert config.withdrawn_contract_keys(
            "Sky", metric, "chain:ethereum:burn_logs:delta") == [], metric
    # ** AND A METRIC IT NO LONGER SERVES IS CORRECTLY JUDGED WITHDRAWN. ** governance_burn_balance
    # was retired on 2026-09-23 — it was the Stage 2 leg under the wrong name — so any row still
    # carrying it must read as written by a contract that has stopped serving it. That is the
    # guard working, not a fault, and it is how stale rows from before the reclassification are
    # kept off the sheet.
    assert config.withdrawn_contract_keys(
        "Sky", "governance_burn_balance", "chain:ethereum:burn_logs:delta") == ["burn_logs"]

    # THE DECOMPOSITION TARGETS COME FROM THE CONTRACT'S OWN BLOCK, so adding a named sender adds
    # its metric here with nothing else to remember.
    named = spec["burn_logs"]["named_senders"]
    assert set(named.values()) <= config.contract_serves(spec)

    # AN ORDINARY CONTRACT STILL SERVES EXACTLY ONE METRIC — this must not become a rule that
    # lets anything through.
    reserve = config.PROJECT_BY_NAME["Chainlink"]["contracts"]["reserve"]
    assert config.contract_serves(reserve) == {"buyback_fund_balance"}
    print("contract_serves ok: a decomposing read serves the metrics its own block names, "
          "a balance read serves one, and a retired metric reads as withdrawn")


def test_a_repulled_query_writes_every_metric_it_serves():
    """** THE REFRESH CADENCE WAS A PROPERTY OF THE METRIC AND IT HAD TO BE THE QUERY'S. **

    Ether.fi's dune:8683038 returns staked_supply, perc_staked and num_holders in ONE response,
    mapped to three metrics. Only locked_tokens_dashboard carries refresh_days=7. The gate ran
    per metric, so the weekly cadence re-pulled the query, wrote that one, and skipped
    lock_rate_pct and staker_count with "store already holds a row" — while their own newer
    values sat in the response already in memory. Two series stale by construction, and the paid
    query bought a third of what it fetched.
    """
    import os
    from fetch.dune import Dune

    os.environ["DUNE_API_KEY"] = "test-key"
    served = _shares_query("Ether.fi", 8683038)
    assert served == {"locked_tokens_dashboard", "lock_rate_pct", "staker_count"}, served
    cadences = {m: config.PROJECT_BY_NAME["Ether.fi"]["dune_queries"][m].get("refresh_days")
                for m in served}
    assert sum(1 for v in cadences.values() if v) == 1, \
        f"exactly one metric carries the cadence — that asymmetry is the bug's cause: {cadences}"

    now = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    rows = [dict(ETHERFI_ROW, day=f"{(now - pd.Timedelta(days=i)).date()} 00:00:00.000 UTC")
            for i in range(30)]
    held = {("Ether.fi", m) for m in served}
    # NINE DAYS OLD: past locked_tokens_dashboard's 7-day cadence, so the query is due. The other
    # two have no cadence of their own and would each have been skipped.
    stale = (now - pd.Timedelta(days=9)).date().isoformat()

    d = Dune(has_history=held, last_dates={k: stale for k in held})
    d.http = _Rows(rows)
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Ether.fi"]], 30, out)
    df = out.frame()

    written = set(df.metric)
    assert served <= written, f"every metric the query serves must be written, got {written}"
    for m in served:
        assert len(df[df.metric == m]) == 30, \
            f"{m}: a carried metric takes the whole history too, got {len(df[df.metric == m])}"
    assert not [e for e in out.log if e.status == "skipped" and e.message.split(":")[0] in served], \
        "nothing the query serves may be skipped while the query is executing"

    # AND THE RESPONSE IS FETCHED ONCE. Three metrics, one execution — the cache is what makes
    # carrying free, and paying three times would trade one bug for a worse one.
    assert d.http.calls == 1, f"one execution for all three metrics, got {d.http.calls}"
    print(f"query cadence ok: {len(served)} metrics written from one re-pull, 30 rows each")


def test_portfolio_scope_applies_to_the_build_and_not_only_to_the_fetch(tmp_path):
    """** THE RUN FETCHED 16 PROJECTS AND THE WORKBOOK DREW 30. **

    Fourteen of those were frozen at whatever the store last held, with nothing on the sheet
    saying the run had not touched them — a stale cell presented exactly like a fresh one, which
    is the failure this project corrects in every other form.

    Parked projects are NOT deleted. Their history stays in the store and comes back the moment
    they are named again, because a series that stops collecting cannot be backfilled from a
    provider that only serves current values.
    """
    import sqlite3
    from openpyxl import load_workbook
    import build_workbook as bw
    import store as store_mod

    db = tmp_path / "scope.db"
    conn = sqlite3.connect(db)
    conn.executescript(store_mod.SCHEMA)
    conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-21", p, "price_usd", 1.0, "coingecko", 1, "t")
        for p in ("Uniswap", "GEODNET", "Sky", "Bitcoin")])
    conn.executemany(
        "INSERT INTO gap_report(run_id, ts, project, metric, tiers_attempted, reason, suggestion,"
        " priority, priority_label) VALUES (?,?,?,?,?,?,?,?,?)",
        [("r1", "2026-09-21T00:00:00", p, "emissions_tokens", "1", "no source", "add one", 6, "P6")
         for p in ("Uniswap", "Bitcoin")])
    conn.commit()
    conn.close()

    held = ["Uniswap", "GEODNET", "Sky"]
    out = tmp_path / "scoped.xlsx"
    bw.build_workbook(store_mod.Store(db), out, only=held)
    wb = load_workbook(out)

    drawn = [wb["Config & Sources"].cell(row=r, column=1).value
             for r in range(bw.CFG_R0, bw._cfg_r1() + 1)]
    assert drawn == held, drawn

    master = [wb["Master"].cell(row=r, column=1).value for r in range(5, 5 + len(held))]
    assert master == held, master
    # THE TITLE STATES THE SCOPE. "all projects" on a narrowed workbook tells the reader nothing
    # is missing, which is the same untruth as the stale cell.
    title = wb["Master"].cell(row=1, column=1).value
    assert f"{len(held)} of {len(bw.PROJECTS)} projects" in title and "--all" in title, title

    # THE GAP REPORT NARROWS TOO — it is the tab most likely to be read as the work queue, and a
    # row for a project the run never fetched is a to-do item that is out of scope by design.
    gap = wb["Gap Report"]
    cells = {gap.cell(row=r, column=c).value
             for r in range(1, gap.max_row + 1) for c in range(1, 4)}
    assert "Bitcoin" not in cells, "a parked project must not appear on the Gap Report"
    assert "Uniswap" in cells

    # --all RESTORES, and the store still holds the parked project's rows.
    everything = tmp_path / "all.xlsx"
    bw.build_workbook(store_mod.Store(db), everything)
    wb2 = load_workbook(everything)
    assert wb2["Master"].cell(row=1, column=1).value == "Master — all projects"
    assert bw._cfg_r1() - bw.CFG_R0 + 1 == len(bw.PROJECTS)
    st = store_mod.Store(db)
    assert not st.load_long().query("project == 'Bitcoin'").empty, \
        "parking a project must never delete its history"
    st.close()

    # AN UNKNOWN NAME IS REFUSED, not quietly dropped: a workbook missing a held asset looks the
    # same as one where that asset has no data.
    try:
        bw.build_workbook(store_mod.Store(db), tmp_path / "bad.xlsx", only=["Uniswapp"])
    except ValueError as e:
        assert "unknown project" in str(e)
    else:
        raise AssertionError("an unknown name in the build scope must refuse")
    print(f"build scope ok: {len(held)} drawn, Gap Report narrowed, --all restores all "
          f"{len(bw.PROJECTS)}, parked history intact")


# ============================================================================================
# CONFIG WINS: ONE SLUG READ FROM THE SOURCE, AND TWO SOURCES CHECKED AND REFUSED
# ============================================================================================

def test_geodnets_defillama_slug_is_wired_and_says_what_the_series_actually_is():
    """** THE 80% METHODOLOGY NOTE THIS FILE HAS BEEN CITING SINCE 2026-09-15 IS DEFILLAMA'S OWN
    GEODNET PAGE. ** So the listing exists, and the slug was confirmed by reading DefiLlama's
    adapter source rather than by trying names against an API.

    AND READING IT CHANGED WHAT THE SERIES IS WORTH. DefiLlama does not measure GEODNET's fees:
    it measures the BURN and divides by 0.8. dailyRevenue IS dailyFees — one object returned
    twice — and dailyHoldersRevenue is the burn itself. None of the three is independent of the
    burn already read, which is exactly the thing a cross-check has to be.
    """
    geo = config.PROJECT_BY_NAME["GEODNET"]
    assert geo["defillama_fees_slug"] == "geodnet"
    ev = geo["defillama_fees_evidence"]
    assert "dimension-adapters" in ev["source_url"] and "fees/geodnet.ts" in ev["source_url"]
    assert ev["read_on"] == "2026-09-23"
    assert ev["fees_are_derived_from_the_burn"] is True
    assert ev["formula"] == "dailyFees = dailyHoldersRevenue / 0.8; dailyRevenue = dailyFees"

    # THE QUOTE TIES THE LISTING TO THE NOTE ALREADY ON FILE. If these ever diverge, one of them
    # has been edited and the identification no longer holds.
    quoted = ev["methodology_quote"]
    na = geo["not_applicable"]["buyback_fund_balance"]
    assert quoted.rstrip(".") in na, "the adapter's methodology and the cited note must match"

    # AND THE ONE THING IT IS GENUINELY GOOD FOR is coverage, not revenue: DefiLlama reads the
    # burn on Solana too, and our own chain read is Polygon-only.
    assert set(ev["chains_read"]) == {"polygon", "solana"}
    assert ev["solana_start"] == "2024-09-24"
    print("geodnet slug ok: wired from DefiLlama's own adapter, and recorded as burn-derived")


class _InfoStub:
    """Hyperliquid's info endpoint: one POST, answered by request `type`."""

    def __init__(self, summaries, balances=None, wei_decimals=8, tokens=None):
        self.summaries, self.calls = summaries, []
        self.balances = balances if balances is not None else [{"coin": "HYPE", "total": "47387407.0"}]
        # spotMeta is where Hyperliquid publishes the token's smallest-unit decimals. HYPE's
        # weiDecimals is 8, NOT the EVM's 18 — which is the whole reason this is read rather
        # than assumed.
        self.tokens = tokens if tokens is not None else [
            {"name": "USDC", "weiDecimals": 8, "index": 0},
            {"name": "HYPE", "weiDecimals": wei_decimals, "szDecimals": 2, "index": 150},
        ]

    def post(self, url, json_body=None, headers=None):
        self.calls.append(json_body.get("type"))
        if json_body.get("type") == "validatorSummaries":
            return self.summaries
        if json_body.get("type") == "spotMeta":
            return {"tokens": self.tokens, "universe": []}
        return {"balances": self.balances}


def _validator(stake, jailed=False):
    return {"validator": "0x" + "a" * 40, "stake": stake, "isJailed": jailed,
            "name": "v", "commission": "0.05"}


def test_hyperliquid_staked_hype_is_summed_from_the_aggregate_the_sdks_do_not_expose():
    """** THE REFUSAL WAS RIGHT ABOUT WHAT IT CHECKED AND WRONG ABOUT WHAT EXISTS. **

    Both of Hyperliquid's OWN SDKs expose only PER-USER staking types — every one takes a `user`
    address — and summing per-user calls needs a delegator set nothing enumerates. That much
    holds. What the SDKs do not carry, the public API does: validatorSummaries returns every
    validator with a `stake` field, so the network figure is one request and a sum.

    An SDK is a convenience wrapper, not an inventory of an API. The lesson is on the entry.
    """
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo

    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    src = hl["hyperliquid_staking_sourcing"]
    assert src["status"] == "wired" and src["endpoint_type"] == "validatorSummaries"
    assert "chainstack" in src["source"] and "NOT the open-source node" in src["served_by"]
    assert "not an inventory of an API" in src["lesson"]

    read = hl["node_api"]["extra_reads"][0]
    assert read["metric"] == "locked_tokens" and read["request"] == {"type": "validatorSummaries"}
    assert read["sum_field"] == "stake" and read["includes_jailed"] is True

    # 400,000,000 HYPE across four validators, one of them jailed, in 8-decimal units.
    stakes = [150_000_000, 120_000_000, 90_000_000, 40_000_000]
    summaries = [_validator(int(s * 10 ** 8), jailed=(i == 3)) for i, s in enumerate(stakes)]
    hc = HyperCoreInfo(prior_values={("Hyperliquid", "total_supply"): 955_000_000.0},
                       prior_dates={}, prior_delta={})
    hc.http = _InfoStub(summaries)
    out = FetchOutput()
    hc.run([hl], None, out)
    df = out.frame()

    locked = df[df.metric == "locked_tokens"]
    assert len(locked) == 1, df.to_dict()
    assert float(locked.value.iloc[0]) == 400_000_000.0, locked.to_dict()
    assert locked.source.iloc[0] == "hypercore_info:validatorSummaries"
    assert "validatorSummaries" in hc.http.calls and "spotClearinghouseState" in hc.http.calls

    # ** JAILED STAKE IS IN THE TOTAL. ** Delegated HYPE is locked whatever the validator's
    # status — a jailed validator's delegators cannot withdraw any faster than anyone else's —
    # so excluding them would understate locked supply by whatever is delegated to the validators
    # currently in trouble, which is exactly when that number moves.
    assert float(locked.value.iloc[0]) == sum(stakes), "the jailed validator's 40m must be in"

    # AND THE ACTIVE-ONLY FIGURE IS BESIDE IT, STAGED. It answers a different question (how much
    # stake is securing the chain now) and nothing has chosen it for anything, so it is captured
    # where nothing reads it rather than quietly driving a column.
    staged = [s for s in out.staged if s["name"].startswith("locked_tokens_active")]
    assert len(staged) == 1 and staged[0]["value"] == 360_000_000.0, out.staged
    assert "3 of 4 validators not jailed" in staged[0]["note"]
    print("hyperliquid staking ok: 400,000,000 HYPE summed including jailed, 360,000,000 active "
          "staged beside it")


def test_the_stake_scaling_is_sourced_from_the_metadata_and_gated_by_supply():
    """** THE TWO ENDPOINTS DO NOT AGREE ON UNITS, AND ASSUMING 18 WOULD BE CATASTROPHIC. **

    spotClearinghouseState returns `total` as a decimal string in human units;
    validatorSummaries returns `stake` as an integer in the token's smallest unit — and
    HyperCore's decimals are NOT the EVM convention. Assuming 18 would report a real 400m HYPE
    stake as 0.0004, a number that reads as a rounding error rather than as a fault.

    ** AN EARLIER DESIGN INFERRED THE DIVISOR BY TESTING CANDIDATES AGAINST THE SUPPLY BAND, AND
    THIS TEST KILLED IT. ** For a genuine 400m stake against a 955m supply, THREE divisors pass:
    10^8 gives 400m, 10^9 gives 40m, 10^18 gives 0.04, and every one is "above zero and under
    supply". Demanding a unique match would have refused a perfectly good read; taking the
    largest would have been choosing a number to make the answer look right.

    So the divisor is READ from spotMeta, where Hyperliquid publishes it, and the band is kept as
    a GATE. Sourced answer first, bound second.
    """
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo

    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    read = hl["node_api"]["extra_reads"][0]
    assert read["wei_decimals"] is None, "unset on purpose — it is read from the metadata"
    assert read["decimals_from"]["request"] == {"type": "spotMeta"}
    assert read["decimals_from"]["decimals_key"] == "weiDecimals"

    # THE ARITHMETIC THAT RULED OUT INFERENCE, asserted rather than only described.
    raw = 400_000_000 * 10 ** 8
    passing = [d for d in (0, 6, 8, 9, 18) if 0 < raw / 10 ** d <= 955_000_000]
    assert len(passing) == 3 and passing == [8, 9, 18], \
        f"three divisors fit the band, which is why it cannot be the discriminator: {passing}"

    # THE GATE IS A DIFFERENT JOB AND STILL DOES IT: a figure exceeding all supply is refused.
    value, detail = HyperCoreInfo._apply_scale(raw, 8, 955_000_000.0)
    assert value == 400_000_000.0 and "inside" in detail
    value, detail = HyperCoreInfo._apply_scale(raw, 0, 955_000_000.0)
    assert value is None and "FAILS ITS BOUND" in detail
    assert "Nothing staked can exceed everything in existence" in detail
    # ** THE GATE IS ASYMMETRIC, AND SAYING SO IS PART OF THE DESIGN. ** It catches a figure
    # that is too LARGE, because nothing staked can exceed everything in existence. It CANNOT
    # catch one that is too small: a declared 18 gives 0.04 HYPE, which is absurd as a network
    # stake and still inside (0, supply]. There is no structural lower bound to test against —
    # "a real network stakes at least X%" is a judgement, not a property — so the small side is
    # held by the metadata being right, not by the gate. Recorded so nobody later reads the gate
    # as protection it does not give.
    value, detail = HyperCoreInfo._apply_scale(raw, 18, 955_000_000.0)
    assert value == 0.04 and "inside" in detail, \
        "the band cannot catch a too-small divisor — that is what the metadata is for"

    # NO SUPPLY, NO ANSWER. Without a bound there is nothing to catch a changed decimals field.
    value, detail = HyperCoreInfo._apply_scale(raw, 8, None)
    assert value is None and "no total_supply in the store" in detail

    # ** THE METADATA IS READ ONCE PER RUN AND THE VALUE COMES FROM IT. **
    stub = _InfoStub([_validator(int(400_000_000 * 10 ** 8))], wei_decimals=8)
    hc = HyperCoreInfo(prior_values={("Hyperliquid", "total_supply"): 955_000_000.0},
                       prior_dates={}, prior_delta={})
    hc.http = stub
    out = FetchOutput()
    hc.run([hl], None, out)
    assert stub.calls.count("spotMeta") == 1, f"fetched once, not per read: {stub.calls}"
    ok = out.frame()
    assert float(ok[ok.metric == "locked_tokens"].value.iloc[0]) == 400_000_000.0

    # A METADATA CHANGE IS CAUGHT BY THE GATE. If weiDecimals starts meaning something else, the
    # scaled figure leaves the band and the run refuses with the raw number — which is the one
    # failure the metadata cannot self-report.
    stub2 = _InfoStub([_validator(int(400_000_000 * 10 ** 8))], wei_decimals=0)
    hc2 = HyperCoreInfo(prior_values={("Hyperliquid", "total_supply"): 955_000_000.0},
                        prior_dates={}, prior_delta={})
    hc2.http = stub2
    out2 = FetchOutput()
    hc2.run([hl], None, out2)
    assert out2.frame()[out2.frame().metric == "locked_tokens"].empty, "nothing stored"
    gap = next(g for g in out2.gaps if g["metric"] == "locked_tokens")
    assert "FAILS ITS BOUND" in gap["reason"] and "40,000,000,000,000,000" in gap["reason"]
    assert "Do NOT assume 18" in gap["suggestion"]

    # AND A TOKEN MISSING FROM THE METADATA REFUSES WITH WHAT IT DID SEE, rather than defaulting.
    stub3 = _InfoStub([_validator(1)], tokens=[{"name": "USDC", "weiDecimals": 8}])
    hc3 = HyperCoreInfo(prior_values={("Hyperliquid", "total_supply"): 955_000_000.0},
                        prior_dates={}, prior_delta={})
    hc3.http = stub3
    out3 = FetchOutput()
    hc3.run([hl], None, out3)
    gap = next(g for g in out3.gaps if g["metric"] == "locked_tokens")
    assert "HYPE not in the metadata" in gap["reason"] and "USDC" in gap["reason"]
    print("stake scaling ok: weiDecimals read from spotMeta once per run, supply gate catches a "
          "changed field, and the band is never used to pick a divisor")


def test_a_defillama_listing_checked_and_absent_stops_asking_for_a_slug():
    """** "ADD A SLUG IF DEFILLAMA COVERS IT" IS THE WRONG INSTRUCTION ONCE SOMEONE HAS LOOKED. **
    It reads as unfinished work, so the next person repeats the search — four rows a run, for
    ever, on a telecom operator with no on-chain protocol for DefiLlama to index.
    """
    from fetch.gaps import _tier_note, _priority, P_SUPPRESSED, P_UNCOVERED

    wm = config.PROJECT_BY_NAME["World Mobile"]
    checked = wm["defillama_listing_checked"]
    assert checked["status"] == "absent" and len(checked["slugs_tried"]) == 6
    # THE CONTROL IS WHAT MAKES ABSENCE MEAN ABSENCE rather than a bad probe.
    assert "geodnet" in checked["control"] and "200" in checked["control"]

    for metric in ("fees_usd", "revenue_usd", "holders_revenue_usd"):
        reason, suggestion = _tier_note(wm, metric, {})
        assert "NOT TRACKED BY DEFILLAMA — checked 2026-09-23" in reason, metric
        assert "Otherwise set defillama_fees_slug" not in suggestion, metric
        assert "Nothing to add here" in suggestion, metric
        # SETTLED ABSENCE IS NOT AN UNCOVERED METRIC. P6 would file finished work at the bottom
        # of the to-do list, which is where the repeated search comes from.
        assert _priority("World Mobile", metric, reason) == P_SUPPRESSED, metric
        assert _priority("World Mobile", metric, reason) != P_UNCOVERED

    # A PROJECT THAT HAS NOT BEEN CHECKED STILL GETS THE ORIGINAL INSTRUCTION — this must not
    # become a blanket excuse for every missing slug.
    other = next(p for p in config.PROJECTS
                 if not p.get("defillama_fees_slug") and not p.get("defillama_listing_checked"))
    reason, suggestion = _tier_note(other, "fees_usd", {})
    assert "checked" not in reason.lower() and "set defillama_fees_slug" in suggestion.lower()
    print(f"defillama absence ok: World Mobile's four rows close as checked; {other['name']} "
          f"still asks for the slug")


# ============================================================================================
# THE BUYBACK ROUTE: WHERE THE TOKENS GO DECIDES HOW THE FLOW IS MEASURED
# ============================================================================================

def test_the_buyback_route_is_derived_from_the_destination_already_on_file():
    """actual_buyback_tokens was gapping with "no buyback_fund_balance contract" on seven
    projects, and that is the wrong instruction for most of them: a buyback that BURNS has no
    fund because the tokens no longer exist, and tokens handed to stakers sit in a staker's
    address, not the protocol's.

    The destination is already declared on every entry. Nothing new had to be established.
    """
    expected = {
        "Hyperliquid": "burn", "GEODNET": "burn", "Uniswap": "burn",
        "Chainlink": "treasury_inflow", "Maple": "treasury_inflow", "Fluid": "treasury_inflow",
        "Ether.fi": "distribute", "Pendle": "distribute", "World Mobile": "distribute",
        "Sky": "split", "Aerodrome": "none", "Morpho": "none",
    }
    for name, route in expected.items():
        assert config.buyback_route(name)["route"] == route, name

    # CHAINLINK RESOLVES TO ITS OWN FUND, because it declares cumulative_flow on the Reserve.
    assert config.buyback_route("Chainlink")["metric"] == "buyback_fund_balance"
    # MAPLE DOES NOT, and the reason names the findable thing rather than a generic contract.
    assert config.buyback_route("Maple")["metric"] is None
    assert "no fund address is declared" in config.buyback_route("Maple")["reason"]

    # ** AERODROME IS "none", NOT "distribute", AND THE DIFFERENCE MATTERS. ** No AERO is ever
    # bought: 100% of fees go to voters in the PAIR'S tokens. A "distribute" gap would invite
    # someone to go and find a flow that does not exist.
    aero = config.buyback_route("Aerodrome")
    assert "NO AERO IS BOUGHT AT ALL" in aero["reason"]
    applicable = set(config.metrics_for_project(config.PROJECT_BY_NAME["Aerodrome"]))
    assert not (applicable & set(config.BUYBACK_METRICS)), \
        "a flow that does not exist must not be reported as missing"
    # AND THE CLAIM IS THE PROJECT'S OWN, not this function's opinion.
    assert config.PROJECT_BY_NAME["Aerodrome"]["fee_split"]["destination_model"] == "distribute_to_voters"

    print("buyback routes ok: 12 projects routed from the destination already declared")


def test_a_burn_destination_buyback_is_the_burn_and_its_usd_twin_is_priced_on_the_day():
    """ONE EVENT, TWO NAMES. Where a protocol buys its token and destroys it, the buyback flow
    IS the burn flow. Reading it twice from two places lets the two disagree, and then the sheet
    shows a protocol that burned more than it bought.

    And the USD figure is not a separate observation either — a buyback is one event with a token
    amount and a price.
    """
    import fetch
    from fetch.base import FetchOutput, point

    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    out = FetchOutput()
    out.add(pd.concat([point("Hyperliquid", "gross_burn_tokens", 1_000.0, "hypercore:x:delta", 2,
                             pd.Timestamp("2026-09-20")),
                       point("Hyperliquid", "gross_burn_tokens", 2_000.0, "hypercore:x:delta", 2,
                             pd.Timestamp("2026-09-21"))], ignore_index=True),
            "chain", "Hyperliquid", "burn", 2)
    out.add(pd.concat([point("Hyperliquid", "price_usd", 40.0, "coingecko", 1,
                             pd.Timestamp("2026-09-20")),
                       point("Hyperliquid", "price_usd", 50.0, "coingecko", 1,
                             pd.Timestamp("2026-09-21"))], ignore_index=True),
            "coingecko", "Hyperliquid", "price", 1)

    fetch._derive_buyback(out, [hl])
    df = out.frame()
    toks = df[df.metric == "actual_buyback_tokens"].sort_values("date")
    usd = df[df.metric == "actual_buyback_usd"].sort_values("date")
    assert list(toks.value) == [1_000.0, 2_000.0], toks.to_dict()
    assert toks.source.iloc[0].endswith(":as-buyback"), "the re-labelling is visible in the source"

    # PRICED ON EACH FLOW'S OWN DATE. Today's price on a July burn is not what was spent, and on
    # a monthly series that error compounds across the whole window.
    assert list(usd.value) == [40_000.0, 100_000.0], usd.to_dict()
    assert usd.source.iloc[0] == "derived:tokens*price"

    print("burn-route buyback ok: 1,000 and 2,000 HYPE, priced at 40 and 50 on their own days")


def test_a_measured_buyback_series_is_never_displaced_by_the_derivation():
    """** GEODNET PUBLISHES BOTH LEGS THROUGH DUNE 8683175. ** Emitting a derived row beside a
    measured one would put the tier-collision guard in charge of which survives — a rule about
    tiers, not about evidence. The derivation stands down instead, and says so."""
    import fetch
    from fetch.base import FetchOutput, point

    geo = config.PROJECT_BY_NAME["GEODNET"]
    assert config.buyback_route("GEODNET")["route"] == "burn"
    out = FetchOutput()
    out.add(point("GEODNET", "gross_burn_tokens", 900.0, "chain:polygon:burn_polygon:delta", 2,
                  pd.Timestamp("2026-09-21")), "chain", "GEODNET", "burn", 2)
    out.add(point("GEODNET", "actual_buyback_tokens", 777.0, "dune:8683175", 4,
                  pd.Timestamp("2026-09-21")), "dune", "GEODNET", "measured", 4)
    out.add(point("GEODNET", "actual_buyback_usd", 123.0, "dune:8683175", 4,
                  pd.Timestamp("2026-09-21")), "dune", "GEODNET", "measured", 4)
    out.add(point("GEODNET", "price_usd", 2.0, "coingecko", 1, pd.Timestamp("2026-09-21")),
            "coingecko", "GEODNET", "price", 1)

    fetch._derive_buyback(out, [geo])
    df = out.frame()
    assert list(df[df.metric == "actual_buyback_tokens"].value) == [777.0], \
        "the measured Dune figure stands; nothing derived is emitted beside it"
    assert list(df[df.metric == "actual_buyback_usd"].value) == [123.0], \
        "and the sourced USD series wins over tokens x price"
    print("sourced wins ok: GEODNET's measured legs are left alone, no derived twin emitted")


def test_a_buyback_row_with_no_price_on_its_own_date_is_reported_not_valued_at_todays():
    """A July burn valued at September's price is not what was spent. Saying which dates could
    not be priced is the honest answer; carrying the latest price backwards is a confident wrong
    number of exactly the kind this project keeps correcting."""
    import fetch
    from fetch.base import FetchOutput, point

    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    out = FetchOutput()
    out.add(pd.concat([point("Hyperliquid", "gross_burn_tokens", 1_000.0, "hypercore:x:delta", 2,
                             pd.Timestamp("2026-07-01")),
                       point("Hyperliquid", "gross_burn_tokens", 500.0, "hypercore:x:delta", 2,
                             pd.Timestamp("2026-09-21"))], ignore_index=True),
            "chain", "Hyperliquid", "burn", 2)
    out.add(point("Hyperliquid", "price_usd", 50.0, "coingecko", 1, pd.Timestamp("2026-09-21")),
            "coingecko", "Hyperliquid", "price", 1)

    fetch._derive_buyback(out, [hl])
    df = out.frame()
    usd = df[df.metric == "actual_buyback_usd"]
    assert list(usd.value) == [25_000.0], "only the day that has its own price is valued"
    assert not any(v == 50_000.0 for v in usd.value), "the July row is NOT valued at September"
    skipped = [e for e in out.log if e.status == "skipped" and "actual_buyback_usd" in e.message]
    assert skipped and "2026-07-01" in skipped[0].message, skipped
    assert "not what was spent" in skipped[0].message
    # and the same two-sidedness as gross_burn_tokens: the row that DID value is named first, so
    # the line cannot be read as the whole derivation having failed.
    assert "1 of 2 buyback row(s) WERE valued and stored" in skipped[0].message, skipped[0].message
    assert "not blocked" in skipped[0].message, skipped[0].message
    print("pricing ok: the unpriced date is named and left out, not carried at today's price, "
          "and the priced row is reported alongside it")


def test_the_emissions_paywall_is_the_last_explanation_not_the_first():
    """** "DefiLlama Pro tier only" WAS RETURNED FOR EVERY PROJECT, and it was the wrong obstacle
    on nearly all of them. ** It sends the reader to buy a $300/mo plan for a figure that either
    has a declared schedule already, is not minting at all, or does not exist. A reason that
    names the wrong obstacle is worse than none: it looks actionable, so somebody acts on it.
    """
    from fetch.gaps import _tier_note, PRO_PAYWALLED

    cases = {
        "Aerodrome": ("EMISSIONS ARE MINTING HERE", "Nothing to buy"),
        "GEODNET": ("NOT MINTING", "OUTFLOW history"),
        "Chainlink": ("NOT MINTING", "OUTFLOW history"),
        "Fluid": ("THE EMISSION HAS FINISHED", "end date"),
        "Morpho": ("NO EMISSION MECHANISM", "Nothing to source"),
    }
    for name, (in_reason, in_suggestion) in cases.items():
        reason, suggestion = _tier_note(config.PROJECT_BY_NAME[name], "emissions_tokens", {})
        assert in_reason in reason, f"{name}: {reason[:120]}"
        assert in_suggestion in suggestion, f"{name}: {suggestion[:120]}"
        # The paywall must not be the HEADLINE obstacle. The premint reasons mention the Pro
        # tier deliberately — to say it would not answer the question — which is the opposite of
        # blaming it, so the test is on what the row leads with.
        assert PRO_PAYWALLED["emissions_tokens"] not in reason, \
            f"{name} still leads with the paywall: {reason[:120]}"

    # ** THE PAYWALL SURVIVES WHERE IT IS ACTUALLY THE OBSTACLE. ** An unclassified project has
    # no better explanation available, and saying so — plus that classifying it is the first
    # step — is more useful than either the bare paywall or silence.
    reason, _ = _tier_note(config.PROJECT_BY_NAME["Bitcoin"], "emissions_tokens", {})
    assert PRO_PAYWALLED["emissions_tokens"] in reason
    assert "no emissions_model declared" in reason
    assert config.emissions_model("Bitcoin") is None

    # ** AN UNSOURCED CLASSIFICATION SAYS SO ON THE ROW. ** A claim about whether a token mints
    # is not something this book gets to assert casually, and a review note presented as an
    # established fact is exactly the pattern that has bitten here before.
    reason, _ = _tier_note(config.PROJECT_BY_NAME["Near"], "emissions_tokens", {})
    assert "CLASSIFIED, NOT YET SOURCED" in reason and "Jake" in reason
    assert config.emissions_model("Near")["sourced"] is False
    # AND A SOURCED ONE DOES NOT CARRY THE CAVEAT.
    reason, _ = _tier_note(config.PROJECT_BY_NAME["GEODNET"], "emissions_tokens", {})
    assert "CLASSIFIED, NOT YET SOURCED" not in reason
    assert config.emissions_model("GEODNET")["sourced"] is True

    # THE CONFIG CHECK REFUSES A MODEL THAT DOES NOT SAY WHETHER IT WAS CHECKED.
    geo = config.PROJECT_BY_NAME["GEODNET"]
    whole = geo["emissions_model"]
    try:
        geo["emissions_model"] = {k: v for k, v in whole.items() if k != "sourced"}
        assert any("no `sourced` flag" in e for e in config._check_emissions_models())
        geo["emissions_model"] = {**whole, "source": None}
        assert any("sourced=True with no `source`" in e for e in config._check_emissions_models())
    finally:
        geo["emissions_model"] = whole
    print("emissions reasons ok: 11 projects classified, the paywall kept only where it is the "
          "real obstacle, and unsourced classifications say so")


def test_net_mint_monthly_is_self_reported_and_is_never_derived():
    """** DERIVING THIS WOULD HAVE DESTROYED THE THING IT EXISTS FOR. **

    It was proposed as "monthly issuance minus monthly burn". The A4 tab already carries that
    derived figure in its own row, and puts a THIRD row beside them: "Self-reported - derived (a
    gap here means one of the two is wrong)". Deriving this one makes that difference
    structurally zero for every project and the comparison stops working — silently, because a
    row of zeros looks exactly like agreement.
    """
    m = config.METRICS["net_mint_monthly"]
    assert m["requires_flag"] == "self_reported_net_mint"
    assert "SELF-REPORTED" in m["label"] and "never derived" in m["label"]

    # ONE FLAG, ONE MEANING. build_workbook's _net_change already keys the formula preference on
    # self_reported_net_mint; applicability keys on the same flag rather than a second rule.
    import build_workbook as bw
    src = __import__("inspect").getsource(bw._net_change)
    assert "self_reported_net_mint" in src

    for name in ("Ethereum", "GEODNET", "Hyperliquid", "Uniswap", "Sky"):
        assert "net_mint_monthly" not in config.metrics_for_project(config.PROJECT_BY_NAME[name]), \
            f"{name} does not publish a net mint — the derived figure is already on the sheet"
    # AND IT SURVIVES WHERE A PROTOCOL DOES PUBLISH ONE.
    assert config.PROJECT_BY_NAME["PancakeSwap"]["self_reported_net_mint"] is True
    assert "net_mint_monthly" in config.metrics_for_project(config.PROJECT_BY_NAME["PancakeSwap"])
    print("net mint ok: self-reported only, so the self-reported-vs-derived check keeps working")


def test_only_a_time_lock_has_an_average_duration():
    """Eleven gaps, and for ten of them the quantity does not exist. An average duration presumes
    positions with an END DATE that varies between holders. Everywhere else the stake is
    cooldown-based: one notice period for everyone, so the "average" is that constant and
    computing it from chain data would be an elaborate way to read a number out of the docs."""
    assert config.METRICS["avg_lock_duration_days"]["requires_lock_model"] == "time_locked"

    aero = config.PROJECT_BY_NAME["Aerodrome"]["lock_model"]
    assert aero["model"] == "time_locked" and aero["sourced"] is True
    assert "avg_lock_duration_days" in config.metrics_for_project(config.PROJECT_BY_NAME["Aerodrome"])
    # ** AND veAERO'S OWN FIGURE IS NOT BUILT, DELIBERATELY. ** It needs every veNFT's lock end
    # enumerated and weighted — a Dune query or a full log scan, not a balance read. Flagged so
    # the cost is visible before anyone starts rather than discovered halfway through.
    assert aero["derivation_cost"].startswith("HEAVY")

    for name in ("Pendle", "Sky", "Chainlink", "Maple", "Ether.fi", "World Mobile", "GEODNET"):
        assert "avg_lock_duration_days" not in config.metrics_for_project(
            config.PROJECT_BY_NAME[name]), name

    # ** THE COOLDOWN IS READ, NOT CARRIED. ** It was held for a day as "14 days, sourced=False",
    # which was the wrong shape twice over: the figure came from Pendle's own sPENDLE docs, so it
    # was not unsourced — and it is a GOVERNANCE PARAMETER, so a constant is right until the day
    # it is not and nothing about a stale 14 would look wrong on the sheet.
    pendle = config.PROJECT_BY_NAME["Pendle"]["lock_model"]
    assert pendle["model"] == "cooldown" and pendle["sourced"] is True
    assert "cooldownDuration()" in pendle["source"] and "read live every run" in pendle["source"]
    # NO CONSTANT BESIDE THE LIVE READ — that is how the two drift, with the constant being what
    # a reader quotes and the read being what is true.
    assert "cooldown_days" not in pendle, "the figure lives in the dated metric, not in config"
    cd = config.PROJECT_BY_NAME["Pendle"]["contracts"]["spendle_cooldown"]
    assert cd["kind"] == "cooldown_duration" and cd["call"] == "cooldownDuration"
    assert cd["address"] == config.PROJECT_BY_NAME["Pendle"]["contracts"]["spendle"]["address"], \
        "one contract, two reads — the cooldown is enforced by the staking token itself"
    print("lock duration ok: veAERO is the one time-lock; ten cooldown stakes stop asking")


def test_geodnets_revenue_split_reconciles_at_87_not_80_and_that_is_left_open():
    """The declared 80% split annualises August's burn to ~$11.3m against a reported ~$10.4m ARR.
    The implied share is 0.8706 — exactly the "~87%, against a declared 80% fee_split" this entry
    already carried from a different route. Two independent paths to the same 8.8% discrepancy,
    so it is real, and nothing here picks which of the three readings is right."""
    rec = config.PROJECT_BY_NAME["GEODNET"]["revenue_split_reconciliation"]
    assert rec["declared_share"] == 0.80
    assert round(rec["burn_usd_august"] / 0.80 * 12) == rec["implied_annualised_at_declared"]
    assert round(rec["burn_usd_august"] * 12 / rec["reported_arr"], 4) == rec["observed_share_against_reported_arr"]
    assert abs(rec["implied_annualised_at_observed"] - rec["reported_arr"]) / rec["reported_arr"] < 0.01
    assert rec["status"].startswith("OPEN")

    # THE SECOND, INDEPENDENT ROUTE TO ~87% that was already on file.
    blob = str(config.PROJECT_BY_NAME["GEODNET"])
    assert "~87%, against a declared 80% fee_split" in blob

    # AND NO DERIVED customer_revenue_usd WAS ADDED, because DefiLlama's own adapter already
    # computes fees as burn/0.8 — a second column would be the same number twice, with the 8.8%
    # question still unasked.
    ev = config.PROJECT_BY_NAME["GEODNET"]["defillama_fees_evidence"]
    assert ev["fees_are_derived_from_the_burn"] is True
    print(f"geodnet reconciliation ok: 80% implies ${rec['implied_annualised_at_declared']:,}, "
          f"87% implies ${rec['implied_annualised_at_observed']:,} against ${rec['reported_arr']:,}")


def test_a_lumpy_series_is_compared_week_against_week_instead_of_being_exempted():
    """** AN EXEMPTION IS A CHECK THAT NEVER RUNS. Changed 2026-09-23. **

    Maple's fees read $1,949,229 on one day and $15,855 on another, from the same source with
    nothing wrong: lending fees are booked on settlement rather than accrued evenly, so the daily
    series is a booking calendar. The same-weekday fix does not touch this — last Tuesday is
    exactly as lumpy as this Tuesday — and the old answer was to stop checking the series at all,
    which is where a scraper redesign lands silently.

    The lumpiness is in the DAILY shape, not in the weekly total, so two full weeks compare.
    """
    import pandas as pd

    assert config.lumpy_flow("Maple", "fees_usd"), "Maple's fees must be declared lumpy"

    # A fortnight of violently lumpy bookings whose WEEKLY TOTALS are nearly identical: nothing
    # has changed about the business, only which day each loan settled on.
    week_a = [2_000_000.0, 10_000.0, 15_000.0, 1_800_000.0, 20_000.0, 5_000.0, 150_000.0]
    week_b = [8_000.0, 1_950_000.0, 12_000.0, 25_000.0, 1_790_000.0, 190_000.0, 25_000.0]
    assert abs(sum(week_b) / sum(week_a) - 1) < 0.02, "the fixture's two weeks must be level"
    rows = []
    for i, v in enumerate(week_a + week_b):
        rows.append((pd.Timestamp("2026-09-01") + pd.Timedelta(days=i), "Maple", "fees_usd", v,
                     "defillama:maple", 1))
    flags = _validated(rows, prior={("Maple", "fees_usd"): 150_000.0})
    assert not flags, f"two level weeks must not flag, however lumpy the days: {flags}"

    # A REAL LEVEL SHIFT STILL FIRES, and the row says which two windows it used.
    quiet = [(pd.Timestamp("2026-09-08") + pd.Timedelta(days=i), "Maple", "fees_usd", v,
              "defillama:maple", 1) for i, v in enumerate([1_000.0] * 7)]
    flags = _validated(rows[:7] + quiet, prior={("Maple", "fees_usd"): 150_000.0})
    assert len(flags) == 1, f"a week that collapsed must still flag: {flags}"
    basis = flags[0]["basis"]
    assert "TRAILING 7-DAY SUM" in basis and "PRIOR 7-DAY SUM" in basis, basis
    assert "NOT a day against a trailing average" in basis, basis
    assert flags[0]["prior_value"] == sum(week_a), flags[0]

    # ** AND THE WINDOW COMPARISON IS SCOPED TO LUMPY SERIES ON PURPOSE. ** A seven-day sum is
    # deliberately insensitive to ONE day — a collapse from 450,000 to 9,000 moves the sum by
    # about 8%, under any threshold worth having — which is exactly the scraper-redesign failure
    # the daily check exists to catch. Applying windows everywhere would trade false flags for a
    # blind spot on the one thing that matters.
    one_day = (450_000.0 - 9_000.0) / (6 * 1_000_000.0 + 450_000.0)
    assert one_day < 0.10, (
        f"a single-day collapse moves a weekly sum by only {one_day:.1%} — which is why "
        f"non-lumpy series keep the same-weekday daily check")
    assert not config.lumpy_flow("Chainlink", "revenue_usd")
    print(f"lumpy ok: two level weeks pass, a collapsed week fires, and a one-day collapse only "
          f"moves a weekly sum {one_day:.1%} — so daily series keep the daily check")


def test_a_row_that_loses_a_tier_collision_is_not_also_flagged_as_a_change():
    """** THE LOSER WAS ARRIVING IN THE REVIEW QUEUE AS A -92% COLLAPSE. **

    validate_frame runs per TIER inside the fetch loop; collisions are resolved afterwards. So
    Aethir's rejected chain read (3.45bn against CoinGecko's 42bn) had already been compared
    against the stored series and flagged. That reads as "this series collapsed" when what
    happened is "two sources disagree and we kept the other one" — a flag about a value nobody
    will ever see, filed under the wrong reason, sitting beside the row that is the real finding.
    """
    import fetch
    from fetch.base import FetchOutput, point
    from fetch.validate import REASON_CHANGE

    out = FetchOutput()
    out.add(point("Aethir", "total_supply", 42_000_000_000.0, "coingecko", 1,
                  pd.Timestamp("2026-09-21")), "coingecko", "Aethir", "provider", 1)
    out.add(point("Aethir", "total_supply", 3_450_000_000.0, "chain:arbitrum:token", 2,
                  pd.Timestamp("2026-09-21")), "chain", "Aethir", "contract", 2)
    # The flag validate would have raised on the tier-2 row before the collision was known.
    out.review_item("Aethir", "total_supply", REASON_CHANGE, "stored_flagged",
                    value=3_450_000_000.0, prior_value=42_000_000_000.0,
                    date=pd.Timestamp("2026-09-21"), source="chain:arbitrum:token", tier=2)

    fetch._resolve_tier_collisions(out)
    reasons = [r["reason"] for r in out.review]
    assert REASON_CHANGE not in reasons, f"the loser's change flag must go: {out.review}"
    assert "tier_collision" in reasons, "the disagreement IS the finding and must stay"

    # THE WINNER'S OWN FLAGS ARE UNTOUCHED — this removes flags on rows that were dropped, not
    # flags on the series.
    out2 = FetchOutput()
    out2.add(point("Aethir", "total_supply", 42_000_000_000.0, "coingecko", 1,
                   pd.Timestamp("2026-09-21")), "coingecko", "Aethir", "provider", 1)
    out2.review_item("Aethir", "total_supply", REASON_CHANGE, "stored_flagged",
                     value=42_000_000_000.0, prior_value=1.0,
                     date=pd.Timestamp("2026-09-21"), source="coingecko", tier=1)
    fetch._resolve_tier_collisions(out2)
    assert [r["reason"] for r in out2.review] == [REASON_CHANGE], \
        "no collision, so nothing is dropped"
    print("collision ok: the loser's change flag goes, the tier_collision row stays, the "
          "winner's flags are untouched")


def test_a_multi_day_delta_is_compared_per_day_not_as_a_big_day():
    """** A THREE-DAY DELTA IS NOT A BIG DAY. **

    Hyperliquid's rebuilt series compared a one-day delta of 17,366 against a three-day delta of
    89,954 and the change check reported a 418% rise. That is entirely the calendar: a run that
    misses a weekend writes a three-day delta, and nothing on the row said so.

    The span travels with the row as a bracketed annotation, which is the slot that already
    exists — config.strip_source_annotations removes it before resolving contract keys, and
    _measuring_point uses the same stripper, so a span can never be mistaken for an address and
    two rows with different spans are still the same measuring point.
    """
    import pandas as pd
    from fetch.base import derive_flow_from_cumulative, _measuring_point
    from fetch.validate import _span_days

    three = derive_flow_from_cumulative(
        1_089_954.0, 1_000_000.0, "Hyperliquid", "gross_burn_tokens", "hypercore_info:x:delta", 2,
        when=pd.Timestamp("2026-09-21"), prior_date="2026-09-18",
        stock_metric="burn_address_balance")
    one = derive_flow_from_cumulative(
        1_017_366.0, 1_000_000.0, "Hyperliquid", "gross_burn_tokens", "hypercore_info:x:delta", 2,
        when=pd.Timestamp("2026-09-21"), prior_date="2026-09-20",
        stock_metric="burn_address_balance")

    assert three.source.iloc[0] == "hypercore_info:x:delta[span=3d]"
    assert _span_days(three.source.iloc[0]) == 3
    # AN ORDINARY DAILY DELTA IS NOT ANNOTATED — saying "span=1d" on every source string in the
    # book would be noise to state the expected thing.
    assert one.source.iloc[0] == "hypercore_info:x:delta" and _span_days(one.source.iloc[0]) == 1

    # THE ANNOTATION CANNOT BREAK ANYTHING THAT READS THE SOURCE.
    assert _measuring_point(three.source.iloc[0]) == _measuring_point(one.source.iloc[0]), \
        "a span must not read as a change of measuring point"
    assert not config.orphaned_contract_keys("Hyperliquid", three.source.iloc[0])
    assert ":delta" in three.source.iloc[0], "it must still be recognised as a differenced row"

    # AND THE COMPARISON NORMALISES. 89,954 over 3 days is 29,985/day against 17,366/day — a
    # 73% rise rather than 418%, and whichever side of the threshold that lands, the row says
    # what was actually compared.
    rows = [(pd.Timestamp("2026-09-18"), "Hyperliquid", "gross_burn_tokens", 89_954.0,
             "hypercore_info:x:delta[span=3d]", 2),
            (pd.Timestamp("2026-09-19"), "Hyperliquid", "gross_burn_tokens", 17_366.0,
             "hypercore_info:x:delta", 2)]
    flags = _validated(rows, prior={("Hyperliquid", "gross_burn_tokens"): 89_954.0})
    if flags:
        b = flags[0]["basis"]
        assert "NORMALISED TO PER DAY" in b and "3" in b, b
        assert abs(flags[0]["prior_value"] - 89_954.0 / 3) < 0.01, flags[0]
        assert abs(flags[0]["value"] - 17_366.0) < 0.01, flags[0]
    raw_move = abs(89_954.0 - 17_366.0) / 17_366.0
    per_day = abs(89_954.0 / 3 - 17_366.0) / 17_366.0
    assert raw_move > 4.0 and per_day < 0.8, \
        f"raw {raw_move:.0%} against per-day {per_day:.0%} — the calendar was most of the move"
    print(f"span ok: 3-day delta annotated and normalised; raw move {raw_move:.0%} -> "
          f"per-day {per_day:.0%}")


def test_a_revenue_figure_derived_from_our_own_burn_cannot_cross_check_it():
    """** THE WORST CASE IS NOT THE USELESS ROW, IT IS THE FALSE CONFIRMATION. **

    DefiLlama's GEODNET adapter does not measure fees: it measures the burn and divides by 0.8.
    So A3's "Implied − actual ($)" computes (burn/0.8) × 0.8 − burn, which is zero however wrong
    either figure is — and that row is the headline evidence for whether a declared split is
    being honoured. A structural zero there is the most reassuring answer the sheet can give,
    and it would survive the burn read breaking entirely.
    """
    import build_workbook as bw

    why = config.implied_check_is_circular("GEODNET")
    assert why and why.startswith("CIRCULAR BY CONSTRUCTION")
    assert "zero however wrong either figure is" in why
    assert "dailyFees = dailyHoldersRevenue / 0.8" in why, "the formula travels with the reason"
    # ONLY WHERE IT IS ACTUALLY CIRCULAR. This must not become a blanket excuse.
    for other in ("Sky", "Maple", "Hyperliquid", "Chainlink"):
        assert config.implied_check_is_circular(other) is None, other

    # THE CELL CARRIES THE REASON, NOT A FORMULA — a reader finding an empty cell where a
    # difference used to be would otherwise assume a broken read.
    geo = config.PROJECT_BY_NAME["GEODNET"]
    out = bw.circular_gated(geo, "=A1-B1")
    assert out.startswith('"CIRCULAR BY CONSTRUCTION') and "A1-B1" not in out
    assert bw.circular_gated(config.PROJECT_BY_NAME["Sky"], "=A1-B1") == "=A1-B1"

    # AND THE THREE SERIES SAY WHAT THEY ARE ON THE SHEET.
    assert "DERIVED FROM THE ON-CHAIN BURN" in config.metric_label("GEODNET", "fees_usd")
    assert "THE SAME SERIES AS fees_usd" in config.metric_label("GEODNET", "revenue_usd")
    assert "THE BURN RESTATED" in config.metric_label("GEODNET", "holders_revenue_usd")
    # A project whose fees are genuinely measured keeps the library label.
    assert config.metric_label("Sky", "fees_usd") == config.METRICS["fees_usd"]["label"]

    # ** AND IT IS NOT COUNTED AS CORROBORATING THE 80% SPLIT. ** The ARR-implied share stays the
    # only independent test, and stays open.
    rec = geo["revenue_split_reconciliation"]
    assert rec["status"].startswith("OPEN")
    assert rec["observed_share_against_reported_arr"] == 0.8706
    print("circular check ok: both implied-vs-actual rows blanked with their reason, three "
          "series relabelled, and the 0.87 ARR test stays the only independent one")


def test_a_governance_parameter_is_read_from_the_contract_and_not_scaled_as_a_token():
    """** A CONSTANT IS RIGHT UNTIL THE DAY IT IS NOT. **

    Pendle's cooldown was carried for a day as "14 days, sourced=False", which was the wrong
    shape twice over: the figure came from Pendle's own sPENDLE docs, so it was not unsourced —
    and it is a governance parameter, so a number copied out of the docs stays on the sheet
    looking entirely reasonable after governance changes it.

    AND IT IS NOT A TOKEN AMOUNT. cooldownDuration() returns SECONDS. Through scaled() a 14-day
    notice period becomes 1.2e-12 days — small enough to read as zero and be believed.
    """
    from fetch.base import FetchOutput
    from fetch.chain import Chain

    pendle = config.PROJECT_BY_NAME["Pendle"]
    SPENDLE = "0x999999999991E178D52Cd95AFd4b00d066664144"
    assert config.KIND_METRIC["cooldown_duration"] == "cooldown_days"

    class _Stub:
        def __init__(self):
            self.raw, self.scaled_calls = [], []

        def has_code(self, chain, address):
            return True

        def symbol_matches(self, chain, address, expected):
            return True, "sPENDLE"

        def raw_call(self, chain, address, call, *args):
            self.raw.append((address, call))
            return 14 * 86_400          # the contract's own units: seconds

        def scaled(self, chain, address, call, *args, decimals_from=None):
            self.scaled_calls.append((address, call))
            return 1_000_000.0

    c = Chain()
    c.reader = _Stub()
    out = FetchOutput()
    c.run([pendle], None, out)
    df = out.frame()

    row = df[df.metric == "cooldown_days"]
    assert len(row) == 1, df.to_dict()
    assert float(row.value.iloc[0]) == 14.0, row.to_dict()

    # ** THE UNSCALED PATH, AND ONLY FOR THIS KIND. ** The two paths are separate so neither can
    # be reached by accident: the cooldown goes through raw_call, the supply read does not.
    assert (SPENDLE, "cooldownDuration") in c.reader.raw, c.reader.raw
    assert not [call for addr, call in c.reader.scaled_calls if call == "cooldownDuration"], \
        "a seconds figure must never go through the decimals-scaling path"
    assert c.reader.scaled_calls, "the ordinary reads on this project still use scaled()"

    # WHAT SCALING IT WOULD HAVE DONE, asserted rather than only described.
    assert (14 * 86_400) / 10 ** 18 / 86_400 < 1e-11, \
        "through scaled() a 14-day cooldown reads as zero"

    # THE METRIC EXISTS ONLY WHERE THE CONTRACT DOES — a cooldown is not a property of an
    # archetype, and inferring it would put an empty column on every archetype-3 project.
    assert "cooldown_days" in config.metrics_for_project(pendle)
    for other in ("Sky", "Maple", "Aerodrome", "Chainlink", "Ether.fi"):
        assert "cooldown_days" not in config.metrics_for_project(config.PROJECT_BY_NAME[other]), other
    print("cooldown ok: 1,209,600 seconds read raw = 14.0000 days, unscaled, and scoped to the "
          "one project whose contract enforces it")


def _morpho_break_frame(break_on="2026-09-11", end="2026-09-21", start="2026-08-13"):
    """Morpho's real shape: ~$500-650K/day, then near-zero from 2026-09-12."""
    import pandas as pd
    after = [21.64, 0.0, 2.13, 2.99, 23.02, 157.0, 4.1]
    rows, d, i = [], pd.Timestamp(start), 0
    while d <= pd.Timestamp(end):
        v = (575_000.0 + (i % 5) * 20_000) if d <= pd.Timestamp(break_on) else after[i % len(after)]
        rows.append({"date": d, "project": "Morpho", "metric": "fees_usd", "value": v,
                     "source": "defillama:morpho", "tier": 1})
        d, i = d + pd.Timedelta(days=1), i + 1
    return pd.DataFrame(rows)


def test_a_level_break_fires_on_morphos_real_shape_and_keeps_firing():
    """** change_threshold COMPARES TWO DAYS, SO A BREAK IS VISIBLE FOR ONE DAY AND THEN NEVER. **

    Morpho's fees ran $500-650K/day to 2026-09-11 and then $21.64, $0, $2.13, $2.99. Borrower
    interest did not stop; the source changed. On the day it happened the daily check fired once;
    from the next day on every comparison was post-break against post-break, and $2.13 against
    $2.99 is a quiet series. The trailing-30-day sum then decays toward zero over a month and
    reads as a business collapse rather than as a broken feed.

    That is a property of the comparison, not a threshold needing tuning.
    """
    import pandas as pd
    from fetch.base import FetchOutput
    from fetch.validate import check_level_breaks, REASON_LEVEL_BREAK

    out = FetchOutput()
    check_level_breaks(_morpho_break_frame(), out, asof=pd.Timestamp("2026-09-21"))
    flags = [r for r in out.review if r["reason"] == REASON_LEVEL_BREAK]
    assert len(flags) == 1, f"Morpho's real shape must fire: {out.review}"
    f = flags[0]
    assert f["project"] == "Morpho" and f["metric"] == "fees_usd"
    assert f["prior_value"] == 615_000.0 and f["value"] < 100, f
    assert "7-day MEDIAN" in f["basis"] and "prior 30-day MEDIAN" in f["basis"]
    assert "STAYED" in f["basis"] and "PERSISTS until it is acknowledged" in f["basis"]

    # ** AND IT KEEPS FIRING — that is the whole point. ** A month after the break the ratio is
    # unchanged, where the daily check has had thirty quiet days in a row.
    out2 = FetchOutput()
    check_level_breaks(_morpho_break_frame(end="2026-10-11"), out2, asof=pd.Timestamp("2026-10-11"))
    assert [r for r in out2.review if r["reason"] == REASON_LEVEL_BREAK], \
        "a month later the break must STILL be flagged — a one-day flag is what failed here"

    # THE GAP ROW SAYS WHAT TO DO AND WHAT NOT TO DO.
    gap = next(g for g in out.gaps if "level FELL" in g["metric"])
    assert "slug split into parent and child" in gap["reason"]
    assert "do NOT widen LEVEL_BREAK_FACTOR" in gap["suggestion"].replace("Do NOT", "do NOT")

    # AN ACKNOWLEDGED BREAK GOES QUIET — a real wind-down is accepted in config with a reason,
    # rather than by widening the factor, which would turn the check off for everything else.
    config.LEVEL_BREAK_ACKNOWLEDGED[("Morpho", "fees_usd")] = {"why": "test"}
    try:
        out3 = FetchOutput()
        check_level_breaks(_morpho_break_frame(), out3, asof=pd.Timestamp("2026-09-21"))
        assert not [r for r in out3.review if r["reason"] == REASON_LEVEL_BREAK]
    finally:
        del config.LEVEL_BREAK_ACKNOWLEDGED[("Morpho", "fees_usd")]
    print("level break ok: fires on Morpho's real shape, still fires a month later, and an "
          "acknowledgement is the only thing that quiets it")


def test_the_level_check_does_not_fire_on_the_things_it_must_not():
    """A check that fires on ordinary variation is one nobody reads by the second week."""
    import pandas as pd
    from fetch.base import FetchOutput
    from fetch.validate import check_level_breaks, REASON_LEVEL_BREAK

    def flags(rows, asof="2026-09-21"):
        out = FetchOutput()
        check_level_breaks(pd.DataFrame(rows), out, asof=pd.Timestamp(asof))
        return [r for r in out.review if r["reason"] == REASON_LEVEL_BREAK]

    def series(project, metric, values, source="defillama:x", start="2026-08-13"):
        return [{"date": pd.Timestamp(start) + pd.Timedelta(days=i), "project": project,
                 "metric": metric, "value": v, "source": source, "tier": 1}
                for i, v in enumerate(values)]

    # ORDINARY VARIATION, including a weekend cycle and one huge outlier. A median is chosen
    # precisely so a single $1.9m booking cannot fake a level.
    ordinary = [450_000.0 if (pd.Timestamp("2026-08-13") + pd.Timedelta(days=i)).weekday() >= 5
                else 1_000_000.0 for i in range(40)]
    ordinary[20] = 9_000_000.0
    assert not flags(series("Chainlink", "revenue_usd", ordinary)), "a weekly cycle is not a break"

    # A STOCK IS NOT CHECKED. A supply figure that halves is a real event with its own guards,
    # and a "level" is not what a stock has.
    assert not flags(series("Chainlink", "total_supply", [1e9] * 30 + [1e6] * 10))

    # A HALF-EMPTY RECENT WINDOW IS A COVERAGE GAP WEARING A BREAK'S CLOTHES — the median is
    # lower for want of data, not because the level moved.
    sparse = series("Chainlink", "revenue_usd", [1_000_000.0] * 33)
    sparse = sparse[:31] + sparse[-1:]
    assert not flags(sparse), "too few recent points to judge a level"

    # A LUMPY SERIES USES WIDER WINDOWS. Maple books on settlement, so a 7-day median is often
    # zero on an ordinary week — which would fire every time.
    assert config.level_break_windows("Maple", "fees_usd") == (30, 90)
    assert config.level_break_windows("Morpho", "fees_usd") == (7, 30)
    lumpy = []
    for i in range(140):
        v = 1_900_000.0 if i % 7 == 3 else 8_000.0
        lumpy.append(v)
    assert not flags(series("Maple", "fees_usd", lumpy, start="2026-05-05"), asof="2026-09-21"), \
        "a lumpy series' own booking cycle must not read as a level break"
    # MAPLE'S THIRD LEG IS DECLARED TOO. holders_revenue_usd is the same booked fees split a
    # different way, and it was left out of LUMPY_FLOWS when fees_usd and revenue_usd went in —
    # so the one leg still on a 7-day window kept flagging the booking calendar as a fault.
    assert config.level_break_windows("Maple", "holders_revenue_usd") == (30, 90)
    assert not flags(series("Maple", "holders_revenue_usd", lumpy, start="2026-05-05"),
                     asof="2026-09-21")

    # ** A MARKET FLOW IS NOT CHECKED AT ALL, and that is scoping rather than tuning. ** The run
    # of 2026-09-22 raised World Mobile volume_usd down 50x, GEODNET up 11x and Near up 11x —
    # three true statements about trading volume and not one of them a finding. Three
    # unactionable rows are how the Morpho-shaped row beside them stops being read.
    collapse = [1_000_000.0] * 30 + [20_000.0] * 10
    assert not flags(series("World Mobile", "volume_usd", collapse)), \
        "volume is a market, not a feed: an order of magnitude in a month is a Tuesday"
    assert not flags(series("GEODNET", "tx_count", collapse))
    assert not flags(series("Aerodrome", "emissions_tokens", collapse)), \
        "a schedule-driven series moves by design at an epoch boundary"
    # AND THE FUNDAMENTALS STILL ARE. The same shape on the metrics the check was built for.
    assert flags(series("Chainlink", "fees_usd", collapse)), "fees must still be checked"
    assert flags(series("Chainlink", "gross_burn_tokens", collapse)), "burns must still be checked"
    print("level break ok: quiet on weekly cycles, outliers, stocks, sparse windows, lumpy "
          "booking patterns and market flows — and still loud on fees and burns")


# ============================================================================================
# A PARENT WHOSE CHILDREN CARRY THE FEES — THE RESTRUCTURE THAT LEAVES NO ERROR
# ============================================================================================

# The real numbers from DefiLlama on 2026-09-23. morpho-blue ran at the pre-break level to
# 2026-09-11 and has no point after it; morpho-midnight spans the break at ~$100/day; and the
# PARENT's post-break series is midnight's, to the cent.
MORPHO_PARENT_30D = 13_046_131.76
MORPHO_BLUE_30D = 13_041_273.00
MORPHO_MIDNIGHT_30D = 4_858.76
# 2026-09-12 .. 2026-09-22 inclusive, exactly as the probe printed them — and exactly what
# the PARENT's chart showed over the same dates, to the cent. That identity is the finding.
MORPHO_MIDNIGHT_AFTER_BREAK = [21.64, 0.00, 2.13, 2.99, 23.02, 62.77, 122.0, 174.0, 178.0,
                               157.0, 112.0]


class _LlamaStub:
    """DefiLlama's summary endpoint, answering per slug."""

    def __init__(self, summaries):
        self.summaries, self.calls = summaries, []

    def get(self, url, params=None, headers=None):
        slug = url.rstrip("/").split("/")[-1]
        self.calls.append(slug)
        if slug not in self.summaries:
            raise RuntimeError(f"404 for {slug}")
        return self.summaries[slug]


def _chart(pairs):
    import datetime as dt
    return [[int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp()), v]
            for d, v in pairs]


def _morpho_summaries():
    import datetime as dt
    blue = [(f"2026-08-{d:02d}", 575_000.0) for d in range(13, 32)] + \
           [(f"2026-09-{d:02d}", 610_000.0) for d in range(1, 12)]
    mid = [(f"2026-09-{d:02d}", v) for d, v in zip(range(12, 23), MORPHO_MIDNIGHT_AFTER_BREAK)]
    # ** THE PARENT'S CHART KEEPS ITS PRE-BREAK DAYS. ** That is what really happened, and it is
    # what makes the naive test useless: while 2026-08-13..09-11 are still inside the 30-day
    # window the parent's chart sums to ~84% of its total, which looks unremarkable. The check
    # has to fire on the day of the break, not a month later.
    return {
        # ** THE CHILD ENTRIES ARE OBJECTS, and this fixture used to pretend they were strings. **
        # That pretence is why the check shipped passing its children's dict reprs into the URL:
        # GET /summary/fees/{'name': 'Morpho Blue', ...} -> 404, eleven times in one run. The
        # shape here is the shape the API returns — name and defillamaId, no slug.
        "morpho": {"name": "Morpho", "total30d": MORPHO_PARENT_30D,
                   "childProtocols": [{"name": "Morpho Blue", "defillamaId": "5980"},
                                      {"name": "Morpho Midnight", "defillamaId": "6321"}],
                   "totalDataChart": _chart(blue + mid)},
        "morpho-blue": {"name": "Morpho Blue", "parentProtocol": "parent#morpho",
                        "total30d": MORPHO_BLUE_30D, "childProtocols": [],
                        "totalDataChart": _chart(blue)},
        "morpho-midnight": {"name": "Morpho Midnight", "parentProtocol": "parent#morpho",
                            "total30d": MORPHO_MIDNIGHT_30D, "childProtocols": [],
                            "totalDataChart": _chart(mid)},
    }


def test_the_restructure_check_would_have_caught_morpho():
    """** THE FAILURE LEAVES NO ERROR ANYWHERE. ** DefiLlama restructured Morpho on 2026-09-12
    into a parent with two children. The parent's slug kept working, kept returning 200, and kept
    returning a daily series — it just stopped being the protocol's fees. Stored fees fell from
    ~$575K/day to $2.13 and the only thing that noticed was a human reading the sheet eleven days
    later.

    THE ARITHMETIC THAT EXPOSES IT: the parent's total30d still equals the SUM of its children's
    — DefiLlama has not lost the money, it has moved where it is reported — while its DAILY chart
    no longer carries them. Both halves are needed: the totals agreeing rules out "the protocol
    collapsed", and the dailies disagreeing rules out "nothing happened".
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    # THE PREMISE, from the live figures: parent total == blue + midnight, to the cent.
    assert abs(MORPHO_BLUE_30D + MORPHO_MIDNIGHT_30D - MORPHO_PARENT_30D) < 0.01

    d = DefiLlama()
    d.http = _LlamaStub(_morpho_summaries())
    out = FetchOutput()
    d.check_restructure({"name": "Morpho", "defillama_fees_slug": "morpho"}, out)

    flags = [r for r in out.review if r["reason"] == "source_restructured"]
    assert len(flags) == 1, f"Morpho's real shape must fire: {out.review}"
    b = flags[0]["basis"]
    assert "is a PARENT with 2 child listing(s)" in b and "RESTRUCTURED" in b
    assert "morpho-blue 30d=13,041,273" in b and "morpho-midnight 30d=4,859" in b
    # ** IT FIRES WHILE THE PRE-BREAK DAYS ARE STILL IN THE WINDOW. ** The parent's chart still
    # holds 2026-08-13..09-11 at ~$575K/day, so a 30-day sum comparison would see ~84% of the
    # total and call it unremarkable. The recent level against what the total implies is what
    # makes this a same-day check rather than a month-late one.
    assert "last 7 days of daily chart sum to" in b, b
    assert "against the 3,044,097 that total implies" in b, b
    assert "NOT REWIRED AUTOMATICALLY" in b, "the row must not pretend to have fixed it"
    # THE CHILDREN'S LAST DATA POINTS ARE NAMED, because that is what decides handover vs switch.
    assert "last point 2026-09-11" in b and "last point 2026-09-22" in b

    gap = next(g for g in out.gaps if "restructured" in g["metric"])
    assert "llama_probe.py <child> --days 45" in gap["suggestion"]
    assert "Do NOT leave the parent feeding the series" in gap["suggestion"]
    print("restructure ok: Morpho's real numbers fire, both children named with their last "
          "points, and nothing is rewired automatically")


def test_the_restructure_check_is_quiet_on_everything_that_is_not_one():
    """A check that fires on an ordinary parent is one nobody reads by the second week."""
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    def flags(summaries, project=("Morpho", "morpho")):
        d = DefiLlama()
        d.http = _LlamaStub(summaries)
        out = FetchOutput()
        d.check_restructure({"name": project[0], "defillama_fees_slug": project[1]}, out)
        return [r for r in out.review if r["reason"] == "source_restructured"], out

    # A PARENT WHOSE DAILY CHART STILL CARRIES ITS CHILDREN is working normally.
    s = _morpho_summaries()
    s["morpho"] = {**s["morpho"],
                   "totalDataChart": _chart([(f"2026-09-{d:02d}", 500_000.0) for d in range(1, 23)])}
    assert not flags(s)[0], "a parent that still reports its children's fees is not restructured"

    # A SLUG WITH NO CHILDREN cannot have been restructured this way.
    assert not flags({"chainlink": {"total30d": 200_000.0, "childProtocols": [],
                                    "totalDataChart": _chart([("2026-09-01", 6_000.0)])}},
                     project=("Chainlink", "chainlink"))[0]

    # ** A GENUINE COLLAPSE IS NOT A RESTRUCTURE, and the totals are what tell them apart. ** If
    # the children have also gone quiet, the money really has stopped and this is the wrong flag
    # — level_break is the right one, and it fires on its own.
    dead = _morpho_summaries()
    dead["morpho"] = {**dead["morpho"], "total30d": 4_858.76}
    dead["morpho-blue"] = {**dead["morpho-blue"], "total30d": 0.0,
                           "totalDataChart": _chart([("2026-09-01", 0.0)])}
    assert not flags(dead)[0], "a real collapse must not be reported as a restructure"
    # THE ARITHMETIC OF WHY: at $4,859 over 30 days the implied week is $1,134, and the observed
    # week is $743. Those agree. A protocol that genuinely stopped earning reports a small total
    # AND small dailies, and the two stay consistent — which is exactly what level_break is for.
    assert 743.0 * 10 > 4_858.76 / 30 * 7, \
        "a genuine collapse keeps its total and its dailies consistent"

    # AN UNREADABLE CHILD IS REPORTED, not silently treated as zero — which would manufacture a
    # totals mismatch and turn a network blip into a restructure.
    broken = _morpho_summaries()
    del broken["morpho-blue"]
    got, out = flags(broken)
    assert not got, "a child that could not be read must not decide the verdict"
    # ** SKIPPED, NOT FAILED. ** A child listing that will not answer is not a failure of this
    # project's fetch — fees_usd came off the parent, and the run of 2026-09-22 turned exactly
    # this into eleven red rows against eight healthy protocols. It still has to be VISIBLE,
    # because an under-counted child sum is how a real restructure goes unreported.
    assert not [e for e in out.log if e.status == "failed"], out.log
    skips = [e for e in out.log if e.status == "skipped" and "INCOMPLETE" in e.message]
    assert skips, out.log
    assert "lower bound" in skips[0].message
    print("restructure ok: quiet on a healthy parent, a childless slug, a genuine collapse, and "
          "an unreadable child")


def test_every_marker_the_code_can_emit_is_registered_and_none_is_appended_by_hand():
    """** THE BUG THAT HAS NOW BITTEN FOUR TIMES, CLOSED AT THE POINT OF WRITING. **

    `[tail@21bps]` blanked Aerodrome's issuance as ORPHANED. `:rederived` would have read as a
    change of measuring point. `:as-buyback` put GEODNET's and Uniswap's actual_buyback_tokens on
    the sheet as "written by contract(s) as-buyback, which are no longer in config" — a message
    naming the wrong file for a contract that never existed. Sky's `:governance_burn_balance` was
    the fourth and had not surfaced only because the read behind it is still 403ing.

    Every time the marker was RIGHT and the registry entry was MISSING, and every time the
    symptom appeared somewhere else entirely, days later, as a blank column. So the registry is
    enforced where the marker is ATTACHED: config.mark_source refuses an unregistered one at the
    emitting line. This test holds that door shut — no marker may be glued on by hand, because a
    concatenation is exactly how the previous four got in.
    """
    import pathlib
    import re

    # (1) THE GUARD REFUSES what is not registered, and says what to do about it.
    for bad in ("as_buyback", "AS-BUYBACK", "partial", "stitched"):
        try:
            config.mark_source("chain:base:minter", bad)
        except ValueError as e:
            assert "not a registered source marker" in str(e)
            assert "SOURCE_MARKERS" in str(e), "the refusal has to say where to register it"
        else:
            raise AssertionError(f"{bad!r} was accepted as a marker")
    for good in config.SOURCE_MARKERS:
        assert config.mark_source("chain:base:minter", good) == f"chain:base:minter:{good}"

    root = pathlib.Path(__file__).resolve().parent.parent
    files = [f for f in root.glob("*.py")] + [f for f in (root / "fetch").glob("*.py")]

    # (2) EVERY MARKER THE CODE ASKS FOR IS REGISTERED. Reads the literals out of the mark_source
    # calls themselves, so a new one added without a registry line fails here rather than in a
    # workbook.
    asked = set()
    for f in files:
        asked |= set(re.findall(r'mark_source\([^()]*?,\s*"([^"]+)"\)', f.read_text()))
    assert asked, "no mark_source call found — the guard has been routed around wholesale"
    unregistered = asked - set(config.SOURCE_MARKERS)
    assert not unregistered, f"emitted but not in SOURCE_MARKERS: {sorted(unregistered)}"

    # (3) AND NONE IS APPENDED BY HAND. An f-string that builds a source ending in a marker is
    # the shape all four bugs had; membership tests (`":PARTIAL" in src`) are not, and are how
    # the workbook reads them back.
    glued = re.compile(r'f"[^"]*:(' + "|".join(re.escape(m) for m in config.SOURCE_MARKERS) + r')(["}:\[])')
    offenders = [f"{f.name}:{i}: {line.strip()}"
                 for f in files
                 for i, line in enumerate(f.read_text().splitlines(), 1)
                 if glued.search(line)]
    assert not offenders, ("a marker glued on by hand bypasses config.mark_source, which is the "
                           "only thing checking the registry:\n" + "\n".join(offenders))
    print(f"markers ok: {sorted(asked)} all registered, none glued on by hand")


def test_a_child_listing_is_resolved_to_a_slug_and_never_to_its_dict_repr():
    """** THE THIRD COST OF ASSUMING A PAYLOAD'S SHAPE. ** `childProtocols` holds OBJECTS with
    `name` and `defillamaId`. The first version did `str(k)` on each and put the result in the
    URL, so every parent in the portfolio asked DefiLlama for
    /summary/fees/{'name': 'Chainlink Requests', ...} — 404 every time, 11 run-log failures
    across eight healthy protocols, and the tier's wall clock 63s -> 125s.

    DefiLlama's slug for a listing is its name lowercased with spaces hyphenated. The dot in
    "ether.fi Stake" SURVIVES: the slug is ether.fi-stake, not etherfi-stake.
    """
    from fetch.llama import DefiLlama
    cs = DefiLlama._child_slug

    assert cs({"name": "Morpho Blue", "defillamaId": "5980"}) == "morpho-blue"
    assert cs({"name": "ether.fi Stake", "defillamaId": "3001"}) == "ether.fi-stake"
    assert cs({"name": "Chainlink Requests"}) == "chainlink-requests"
    # A PAYLOAD THAT ALREADY CARRIES A SLUG IS BELIEVED over the derivation — the rule is the
    # fallback, not the mechanism, so the day DefiLlama adds the field this stops guessing.
    assert cs({"name": "Uniswap V3", "slug": "uniswap-v3"}) == "uniswap-v3"
    # AND THE OLD SHAPE STILL WORKS, because nothing says the API cannot go back to strings.
    assert cs("morpho-blue") == "morpho-blue"
    # NOTHING RESOLVABLE RESOLVES TO NOTHING — never to "none" or to a repr that 404s.
    for junk in ({"defillamaId": "6321"}, {}, None, 5980, {"name": "   "}):
        assert cs(junk) is None, junk
    print("restructure ok: child entries resolve by name, dots survive, junk yields no call")


def test_morphos_restructure_is_a_confirmed_gap_not_a_slug_to_guess_at():
    """** A SECOND, 45-DAY PROBE SETTLED IT: morpho-blue reports NOTHING after 2026-09-11. **

    Not truncated at 30 days — absent at 45. Continuous ~$490-650K/day through 09-11 (last point
    $614,631), then nothing. That is neither case (a) (blue continuous across the break) nor case
    (b) (blue beginning at the break) — it rules both out, because there is no second series to
    hand over to and no continuation to switch onto today.

    AND IT IS NOT OUR CODE. Every commit touching the adapter since late August is dated and
    unrelated: a blacklist entry, an added chain, a corrected start date — nothing on or before
    the break. The likely cause is a DefiLlama indexing failure on morpho-blue's own listing.
    """
    r = config.PROJECT_BY_NAME["Morpho"]["defillama_restructure"]
    assert r["status"] == "confirmed_gap"
    assert r["blue_last_daily_point"] == "2026-09-11"
    assert r["midnight_last_daily_point"] == "2026-09-22"
    assert "45-day" in r["blue_45day_probe"] or "45 day" in r["blue_45day_probe"]
    assert "$614,631" in r["blue_45day_probe"]

    # THE ARITHMETIC, asserted rather than only described: the children sum to the parent's
    # total to the cent, so the money moved rather than stopped.
    kids = r["children"]
    assert abs(sum(kids.values()) - r["parent_total30d"]) < 0.01

    # THE CODE-UNCHANGED EVIDENCE is on file, dated, so "likely cause" is not a shrug.
    assert len(r["code_unchanged_at_break"]) == 3
    assert all("2026-08" in c or "2026-09" in c for c in r["code_unchanged_at_break"])
    assert "indexing failure" in r["likely_cause"] and "DefiLlama" in r["likely_cause"]

    # THE GAP REASON IS THE EXACT TEXT THE ROW MUST CARRY.
    assert r["gap_reason"] == ("DefiLlama has not reported Morpho Blue fees since 2026-09-12; "
                               "adapter code unchanged at that date and actively maintained — "
                               "likely an indexing failure on DefiLlama's side.")

    # THE AUTO-RECOVERY SCAFFOLD IS DECLARED, NOT HARDCODED IN THE ADAPTER.
    assert r["watch_child"] == "morpho-blue"
    assert r["recovery"] == {"target_metric": "fees_usd",
                             "sum_slugs": ["morpho-blue", "morpho-midnight"]}
    assert "automatically" in r["do_not"] and "BY HAND" in r["do_not"]
    print("morpho ok: confirmed gap, code exonerated with dates, recovery scaffold declared")


class _MorphoLlamaStub:
    """DefiLlama's summary endpoint for Morpho's parent and two children."""

    def __init__(self, blue_after_break=None):
        import datetime as dt
        self.calls = []
        blue_pre = [(f"2026-08-{d:02d}", 575_000.0) for d in range(13, 32)] + \
                   [(f"2026-09-{d:02d}", 610_000.0) for d in range(1, 12)]
        blue = blue_pre + (blue_after_break or [])
        mid = [(f"2026-09-{d:02d}", v) for d, v in
              zip(range(12, 23), [21.64, 0.0, 2.13, 2.99, 23.02, 62.77, 122.0, 174.0, 178.0,
                                  157.0, 112.0])]
        self.summaries = {
            "morpho": {"name": "Morpho", "total30d": 13_046_131.76,
                      "childProtocols": ["morpho-blue", "morpho-midnight"],
                      "totalDataChart": _chart(blue_pre + mid)},
            "morpho-blue": {"name": "Morpho Blue", "parentProtocol": "parent#morpho",
                           "total30d": sum(v for _, v in blue_pre), "childProtocols": [],
                           "totalDataChart": _chart(blue)},
            "morpho-midnight": {"name": "Morpho Midnight", "parentProtocol": "parent#morpho",
                               "total30d": sum(v for _, v in mid), "childProtocols": [],
                               "totalDataChart": _chart(mid)},
        }

    def get(self, url, params=None, headers=None):
        slug = url.rstrip("/").split("/")[-1]
        self.calls.append(slug)
        if slug not in self.summaries:
            raise RuntimeError(f"404 for {slug}")
        return self.summaries[slug]


def test_the_parents_post_break_residual_is_never_stored_while_the_gap_is_unresolved():
    """** THE POST-BREAK RESIDUAL IS MORPHO-MIDNIGHT'S FEES WEARING MORPHO'S NAME. **

    The parent's slug answers 200 after the break and returns a chart — it is midnight's fees,
    to the cent. Storing it under Morpho's fees_usd would put a wrong-but-plausible number in a
    column a daily check no longer distinguishes from a real one. Only the genuinely pre-break
    history is kept, and the gap is reported with the exact reason config declares.
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    d = DefiLlama()
    d.http = _MorphoLlamaStub()   # blue reports nothing after 09-11 — the confirmed state
    out = FetchOutput()
    d.fees(config.PROJECT_BY_NAME["Morpho"], None, out)

    fees = out.frame()[out.frame().metric == "fees_usd"]
    assert not fees.empty, "the genuine pre-break history must still be stored"
    assert fees["date"].max() < pd.Timestamp("2026-09-12"), \
        f"nothing dated on or after the break may be stored: {sorted(fees['date'].astype(str))}"
    assert fees["date"].min() == pd.Timestamp("2026-08-13")

    gap = next(g for g in out.gaps if g["metric"] == "fees_usd")
    assert gap["reason"] == config.PROJECT_BY_NAME["Morpho"]["defillama_restructure"]["gap_reason"]
    assert "morpho-blue" in gap["suggestion"]

    # REVENUE AND HOLDERS' REVENUE ARE UNAFFECTED — Morpho's revenue is hardcoded to 0 by
    # protocol design regardless of which listing carries the fees, so they keep the ordinary
    # path and are not blanked by the guard.
    assert not out.frame()[out.frame().metric == "revenue_usd"].empty
    print("residual ok: only pre-break fees stored, the gap carries config's exact reason, "
          "revenue untouched")


def test_recovery_applies_itself_the_day_the_watch_child_reports_again():
    """** NO HUMAN STEP. ** The day morpho-blue reports again, fees_usd becomes
    sum(morpho-blue, morpho-midnight) over the FULL history — not a trimmed re-fetch, which
    would rewrite only the recent month and leave everything before the switch at the old,
    broken value — and a review row records the recovery. The level-break flag needs no separate
    clearing: it is computed from the stored numbers each run, so a correct series just stops
    tripping it.
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    d = DefiLlama()
    # Blue reports again from 2026-09-12 onward, roughly at its pre-break level.
    d.http = _MorphoLlamaStub(blue_after_break=[(f"2026-09-{d_:02d}", 605_000.0)
                                                for d_ in range(12, 23)])
    out = FetchOutput()
    d.fees(config.PROJECT_BY_NAME["Morpho"], None, out)

    fees = out.frame()[out.frame().metric == "fees_usd"].sort_values("date")
    assert fees["date"].max() == pd.Timestamp("2026-09-22"), \
        "the series must now extend through the latest recovered day"
    assert fees["date"].min() == pd.Timestamp("2026-08-13"), "full history, not a trimmed re-pull"
    # THE SWITCH-DAY VALUE IS THE SUM, not either child alone.
    switch_day = fees[fees.date == pd.Timestamp("2026-09-12")]
    assert abs(float(switch_day.value.iloc[0]) - (605_000.0 + 21.64)) < 0.01

    recov = [r for r in out.review if r["reason"] == "source_restructure_recovered"]
    assert len(recov) == 1, out.review
    assert "RECOVERED" in recov[0]["basis"] and "morpho-blue" in recov[0]["basis"]
    assert "No human step was needed" in recov[0]["basis"]
    assert not [g for g in out.gaps if g["metric"] == "fees_usd"], \
        "a recovered series must not also report a gap"
    assert "covers the whole break window" in recov[0]["basis"], \
        "continuity is CHECKED against the child's own chart, never asserted"
    print("recovery ok: full history summed and stored, switch day sums both children, "
          "recorded with no gap alongside it")


def test_a_recovery_that_does_not_backfill_leaves_the_hole_visible():
    """** RECOVERY IS NOT BACKFILL, AND STORING THE OTHER CHILD ALONE WOULD HIDE THE DIFFERENCE. **

    This is what actually happened: morpho-blue went quiet on 2026-09-12 and started reporting
    again on 2026-09-21, without filling in the nine days it missed. Summing the declared
    children over "the full history" then writes 09-12..09-20 as morpho-midnight ALONE — about
    $2 a day standing in for Morpho Blue's ~$600,000 — and the old code declared in its review
    row that the series "has no gap at the switch".

    ** THAT IS THE SAME WRONG-BUT-PLAUSIBLE NUMBER THE PRE-RECOVERY BRANCH REFUSES TO STORE **,
    arriving through the back door and with a continuity claim attached. An absent day is a hole
    a reader can see; a $2.13 day is a number they will believe. So the uncovered days are held
    out, reported as a gap in their own right, and the review row says the window is NOT
    backfilled rather than claiming it is.
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    d = DefiLlama()
    d.http = _MorphoLlamaStub(blue_after_break=[(f"2026-09-{d_:02d}", 605_000.0)
                                                for d_ in range(21, 23)])
    out = FetchOutput()
    d.fees(config.PROJECT_BY_NAME["Morpho"], None, out)

    fees = out.frame()[out.frame().metric == "fees_usd"].sort_values("date")
    stored = set(fees["date"])
    assert fees["date"].max() == pd.Timestamp("2026-09-22"), "the recovered tail is stored"
    assert fees["date"].min() == pd.Timestamp("2026-08-13"), "the pre-break history is stored"
    # ** THE NINE DAYS ARE ABSENT, NOT SMALL. ** This is the whole assertion: the series either
    # side is intact and the middle is a visible hole.
    for day in range(12, 21):
        assert pd.Timestamp(f"2026-09-{day:02d}") not in stored, \
            f"09-{day:02d} must be HELD OUT — morpho-blue has no point on it"
    assert pd.Timestamp("2026-09-11") in stored and pd.Timestamp("2026-09-21") in stored

    gap = [g for g in out.gaps if g["metric"] == "fees_usd"]
    assert len(gap) == 1, out.gaps
    assert "NOT STORED" in gap[0]["reason"] and "2026-09-12" in gap[0]["reason"], gap[0]["reason"]
    assert "a hole is visible and a wrong number is not" in gap[0]["reason"]
    assert "interpolation" in gap[0]["suggestion"], "and nobody is to fill them by hand"

    recov = [r for r in out.review if r["reason"] == "source_restructure_recovered"]
    assert len(recov) == 1 and "THE BREAK WINDOW IS NOT BACKFILLED" in recov[0]["basis"], recov
    assert "the hole is VISIBLE" in recov[0]["basis"]
    assert "covers the whole break window" not in recov[0]["basis"], \
        "the continuity claim must not survive a window that is not covered"
    print("recovery hole ok: the nine unbackfilled days are absent and reported, not filled "
          "with the other child's ~$2/day wearing Morpho's name")


def test_the_hole_closes_by_itself_the_day_the_source_backfills_it():
    """** NOBODY EDITS A DATE. ** The recovery path re-pulls the watch child's whole chart every
    run and recomputes which days it covers, so a backfill closes the hole the same way the
    recovery itself applied: by the next run noticing, with no human step and no config change.

    THE CONTROL FOR THE HELD-OUT DAYS. Holding days out is only defensible if it is temporary
    and self-clearing; a hole that needs someone to remember it is a hole that stays.
    """
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    # Blue starts reporting from 09-21 AND fills in the nine days it missed.
    d = DefiLlama()
    d.http = _MorphoLlamaStub(blue_after_break=[(f"2026-09-{d_:02d}", 605_000.0)
                                                for d_ in range(12, 23)])
    out = FetchOutput()
    d.fees(config.PROJECT_BY_NAME["Morpho"], None, out)
    stored = set(out.frame()[out.frame().metric == "fees_usd"]["date"])
    for day in range(12, 21):
        assert pd.Timestamp(f"2026-09-{day:02d}") in stored, \
            f"09-{day:02d} is backfilled upstream and must now be stored"
    assert not [g for g in out.gaps if g["metric"] == "fees_usd"], \
        "a filled window is not a gap, and nothing had to be edited to say so"
    recov = [r for r in out.review if r["reason"] == "source_restructure_recovered"]
    assert recov and "covers the whole break window" in recov[0]["basis"], recov
    print("backfill ok: the held-out days return on their own once morpho-blue reports them")


def test_the_generic_restructure_check_stands_down_once_a_project_is_declared():
    """Once a restructure has been investigated and recorded in config, the generic
    check_restructure (built to catch an UNDECLARED one) must not also raise a duplicate flag
    for the same fact every run."""
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama

    d = DefiLlama()
    d.http = _MorphoLlamaStub()
    out = FetchOutput()
    d.check_restructure(config.PROJECT_BY_NAME["Morpho"], out)
    assert not out.review and not out.gaps, \
        "a project with a declared defillama_restructure must not also get the generic flag"
    assert not d.http.calls, "the generic check should not even call the endpoint"
    print("generic check ok: stands down for a project already diagnosed in config")


def test_morphos_second_revenue_route_is_recorded_as_a_watch_item():
    """morpho-midnight's own methodology: "the settlement fee ... plus the continuous fee ...
    BOTH ARE DISABLED AT LAUNCH, so Revenue is currently 0." Those are DESIGNED IN — a switch
    waiting to be thrown, not a mechanism that would have to be built — and independent of the
    Blue fee switch, which is blocked on legal and tax structuring. This entry tracked only one
    of the two."""
    w = config.PROJECT_BY_NAME["Morpho"]["fee_split"]["midnight_fee_watch"]
    assert w["status"] == "designed_in_but_disabled"
    assert len(w["fees"]) == 2 and "settlement fee" in w["fees"][0]
    assert "blocked on legal/tax structuring" in w["independent_of"]
    assert "revisit the archetype 3 exclusion" in w["if_enabled"]

    # ** revenue_usd = 0 IS CORRECT, NOT A GAP. ** Nothing here should chase a figure the
    # protocol has deliberately set to zero, and the fee_split note points at the second route
    # so the next reader finds it.
    assert w["until_then"] == "revenue_usd = 0 is correct, not a gap"
    assert config.PROJECT_BY_NAME["Morpho"]["fee_split"]["share_to_buyback"] == 0.0
    assert "midnight_fee_watch" in config.PROJECT_BY_NAME["Morpho"]["fee_split"]["note"]
    print("midnight watch ok: a second, independent route recorded without wiring anything")
