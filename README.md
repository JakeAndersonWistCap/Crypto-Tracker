# Crypto-Tracker — token supply & demand metrics

Gathers fundamental supply-and-demand metrics for a fixed universe of crypto assets, stores the
full available history locally, and regenerates an Excel workbook on every run.

```
python token_metrics.py
```

One command, no arguments. Fetches, updates the local store, rebuilds the workbook.

## Why

Reported burn figures are almost always **gross**, published in isolation, with emissions
disclosed somewhere else entirely. This tool puts **gross burn, gross issuance and net supply
change side by side** for every asset and shows the ratio between them. Net supply change is
the headline column on the archetype 4 tab, and nothing in the design is allowed to make it
harder to see.

The second purpose is trajectory: whether a project is improving on its own metrics over 30
days and 3, 6 and 9 months, not merely what the number is today.

## Design principle: minimise paid APIs

Paid data is the constraint, not engineering effort. Sources are tried in this order:

| Tier | Source | Covers | Cost |
|---|---|---|---|
| 1 | DefiLlama, CoinGecko, config schedules | fees, revenue, TVL, stablecoins, RWA category, price, supply, FDV, deterministic issuance | free |
| 2 | **contract reads** (`web3.py`, public RPC) | burn-address balances, vote-escrow totalSupply for lock rates, buyback-fund balances, authoritative supply | free |
| 3 | **the protocol's own dashboard** (Playwright) | anything a protocol publishes itself: net mint, burn totals, node counts | free |
| 4 | Dune | historical backfill only, for the month-by-month series a point-in-time read cannot give | API key |

A protocol's own HTTP API sits alongside tier 1 where one exists: Hyperliquid publishes the
Assistance Fund balance through its documented info endpoint and Tron exposes BURN_TRX through any
node. Both are plain HTTPS with no key, no chain and no RPC, and they replace a contract read that
was never viable rather than supplementing one.
| 5 | off-chain operational | archetype 2 supply units, utilisation, customer revenue | free where an API exists |

Where a protocol publishes a figure on its own dashboard, the tool takes the self-reported
number and cites it rather than reconstructing it.

**Known paywalls, not designed around as if free:** DefiLlama emissions/unlocks is Pro-tier
(separate API plan); DefiLlama per-protocol-version fees is Pro-tier, which is why Uniswap's
implied burn cannot be computed from the free API; Token Terminal has no API on our tier. Each
appears in the Gap Report as *paywalled*, a different problem from *missing*.

## Layout

| File | Role |
|---|---|
| `config.py` | One entry per project: archetypes, fee/burn-split parameters with source URL and date, contract addresses, data-source identifiers, sanity bounds. **No logic.** |
| `sources.yaml` | The scrape registry. **Adding a source is a YAML edit, no Python.** |
| `fetch/` | One module per tier: `llama.py`, `coingecko.py`, `schedule.py` (1), `chain.py` (2), `scrape.py` (3, 5), `dune.py` (4), plus `validate.py` and `gaps.py`. |
| `adapters/` | Named overrides for pages that genuinely need bespoke handling. Almost nothing belongs here. |
| `store.py` | SQLite (`metrics.db`): one long table keyed `(date, project, metric)`, upsert on conflict, plus review-queue and gap tables. |
| `build_workbook.py` | Reads the store, writes `token_metrics.xlsx` from scratch. |
| `token_metrics.py` | Orchestrator. |
| `manual_overrides.csv` | `date, project, metric, value, source_note, entered_on`. Loaded last; wins for matching keys. |
| `recalc.py`, `office/` | LibreOffice formula recalculation and error scan. Needs `libreoffice-calc`. |
| `tests/` | Fixture build, mocked adapter tests, and the DOM-anchor contract against real Chromium. |

## Before the first run

`RUNBOOK.md` is the step-by-step for a fresh clone: prerequisites, virtualenv, credentials,
connectivity check, the run itself, what to check in the workbook and in what order, and the
failures most likely on day one.

```bash
python preflight.py            # what the run will attempt, what it will skip and why. No network calls.
python preflight.py --check    # probes every dependency in ~30s. Exits non-zero if anything critical is down.
```

Run the check before the backfill. It tells you an RPC is unreachable in thirty seconds rather
than eight minutes into a full-history pull.

```bash
python dune_probe.py 8683038          # one Dune query: its real columns, types and sample rows
python dune_probe.py --config         # every Dune query in config and whether it is mapped yet
python dune_probe.py --stored GEODNET/gross_burn_tokens --before 2026-08-01
```

`dune_probe.py` works on a single Dune query without a full run, and writes nothing to the store
or the workbook. Mapping a query is iterative; a full run is the wrong unit of work for it.
`--stored` reads the other way, showing what actually landed, so "the run did not error" and
"the history is really there" stay separate questions. See RUNBOOK 11a.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium          # tier 3
cp .env.example .env                 # DUNE_API_KEY is optional; tiers 1-3 run without it
python token_metrics.py
```

**First run** backfills the entire available history from every source, so the 3/6/9-month
trajectory columns are populated on run one rather than accumulating from install.
**Later runs** extend the series and re-fetch a trailing 30-day window to catch source
revisions; tier 4 is skipped for any series the store already covers.

The store is the durable artefact. The workbook is disposable output, rebuilt from scratch.

Validate a build: `python recalc.py token_metrics.xlsx` must report `"status": "success"`.
Never ship on `errors_found`.

## The workflow this is built around

1. Run it. 2. Open the **Gap Report** tab: every metric no tier could resolve, why, and what
would fix it. 3. For each row, find the page that publishes the figure and complete its entry
in `sources.yaml` (`url`, then either `url_contains` + `json_path`, or `anchor`), set
`enabled: true`. 4. Re-run.

`sources.yaml` ships with the schema documented inline, two worked examples, six **enabled**
lock-rate and staking entries, and pre-populated stubs for the metrics where a published page is
the only route. The six enabled entries carry `needs_first_run_check: true`, because their
anchors were inferred without sight of the pages: each puts its first value in the Review Queue
for eyeballing. Aave's two staking pages are deliberately two separate metrics that are never
summed, since they have different claims on revenue and different unstaking mechanics.

## Anti-brittleness

A scraper's dangerous failure is not the site going down, which is obvious. It is a redesign
where the selector still matches but now points at a different number. Three defences:

- **Anchor on label text, never on position.** The extractor finds the element whose text
  matches the expected label and reads the number beside it. Verified against real headless
  Chromium across stat cards, table rows, nested markup and layouts where the value comes
  *before* the label (`tests/test_dom_anchor.py`).
- **Sanity bounds per metric.** A value outside its plausible range is **rejected, not
  stored**, and appears in the Review Queue. A wrong number in the sheet is worse than a gap.
- **Change threshold.** A value moving more than the configured percentage is **stored but
  flagged** to the Review Queue. A genuine step change (a halving, a one-off 100m burn) must
  not be silently dropped.
- A **differenced flow with fewer than two dated observations** is not reported at all. With one
  reading there is no interval, and the difference is 0 whatever the truth is — a 0 in the burn
  column of an archetype 4 name is the most misleading cell this tool can produce. It renders
  `n/a` with a reason instead.
- A **burn address holding exactly zero** is flagged as evidence about the ADDRESS. A burn address
  is one-way, so an exact zero means nothing has ever arrived — which, for a protocol understood
  to have burned, more likely means the burn does not route there than that it never burned.
- A **zero burn derived from a balance delta** is stored but flagged too: a balance cannot
  distinguish "nothing burned" from "the burn did not route to the address we watch", and a 0
  that means the second must not render like a 0 that means the first.

Tier 3 is polite: one run a day, responses cached per day, `robots.txt` respected, and an
honest user agent naming the tool.

## Contract addresses are unverified — read this before trusting tier 2

The brief requires every address be verified against the **protocol's own documentation**, not
an aggregator. The 12 addresses currently in `config.py` came from the assistant's training
data, which is exactly the kind of third-party source the brief rules out. Every one carries
`verified: None`.

**Default behaviour: they are refused.** Tier 2 reads nothing and writes a Gap Report row per
address naming what to check and where. To verify one, confirm it on the protocol's own docs,
then set `source_url` to that page and `verified` to the date you checked.

`TOKEN_METRICS_ALLOW_UNVERIFIED=1` reads them anyway, and every resulting value is flagged
`address_unverified` in the Review Queue. Independently of that flag, **every ERC-20 read calls
`symbol()` and rejects the address if it does not match `expected_symbol`** — an on-chain
self-check that catches a transposed or stale address before a plausible-looking wrong number
reaches the sheet.

## Workbook

1. **Master** — all projects, archetype labels, all live figures, legend at the bottom.
2. **A1 Infrastructure** — fees ÷ issuance is the key ratio; TVL the primary demand metric; RWA from both sources with the divergence shown.
3. **A2 Coordination** — customer revenue per token emitted; supplier earnings from emissions vs from customers.
4. **A3 Revenue Buyback** — revenue × documented split ÷ 90-day average price ÷ supply, annualised = buyback as % of supply. Actual buyback alongside; the gap is itself a signal. Emissions, net absorption, coverage ratio, lock rate. Yield destination is tracked separately from burn and never netted.
5. **A4 Permanent Burn** — gross burn, gross issuance, **net supply change** (headline), burn ÷ issuance, burn as share of fees, both as % of supply annualised, implied vs actual.
6. **Charts** — native Excel charts, live off the tabs above.
7. **Config & Sources** — every parameter with its URL, date, tier, `programmed` flag and status. This tab is what makes the numbers defensible.
8. **Gap Report** — the to-do list. Every unresolved metric, tiers attempted, reason, and the fix.
9. **Review Queue** — values rejected by sanity bounds or flagged by the change threshold, with old and new values.
9a. **Config & Sources → Closed** — figures chased to a dead end, with what was tried, plus limitations on figures that work (current state only, no history). Deliberately NOT in the Gap Report: a permanent entry on a to-do list teaches the reader to skim it. Cells depending on a closed figure read `none available`, not `n/a`.
10. **Staging** — figures a source returned that no metric takes. Captured so they are not lost or re-discovered; **read by nothing**. Promoting one is a deliberate edit to `config.py`.
11. **Run Log** — rows per source and tier, failures, last successful fetch per source/project.

Plus **Data** and **Monthly**, the literal aggregates the formulas reference.

**Formula policy:** raw fetched values are literals, because they are source data. Every derived
figure is an Excel formula referencing the Config tab, so the maths is visible and the
assumptions flex in the sheet.

Conventions: Arial throughout · `$#,##0` currency with the unit in the header · percentages as
fractions · zeros render `-` · negatives in parentheses · **blue** hardcoded inputs and config
levers · **black** formulas · **green** cross-sheet pulls · **yellow fill** manual override
(entered_on in the cell comment) · **grey fill** unconfirmed split, derived figure suppressed ·
**orange fill** stale · **amber fill** programme paused · **lilac fill** flagged to the Review
Queue.

Token conversions use the period-**average** price; spot is shown separately and labelled.

## Four config fields that do real work

- **`programmed`** — contract-enforced versus revisable by forum post. Aave is the case that
  proves the distinction matters: the *routing rule* is immutable (`programmed: true`) but the
  *budget* is governance-set, which is how an immutable mechanism still got cut from ~$50m to
  ~$30m in March 2026 and paused entirely from 19 April 2026.
- **`buyback_destination`** — burn versus distribute is not fixed per protocol. Sky's is a
  governance-set parameter sending repurchased tokens either to burn or to staked holders.
  Tokens paid out as yield re-enter float; netting them against burn flatters the picture.
- **`burn_execution`** — Uniswap's burn is holder-elected, not protocol-executed. Fee routing
  is deterministic; realised burn depends on redemption behaviour, so implied and actual
  diverge for reasons unrelated to revenue.
- **`materiality`** — OriginTrail's yield is genuinely revenue-funded, but revenue is small and
  not publicly tracked. It qualifies mechanically while not being comparable to Aave or
  Hyperliquid. Flagged, not excluded.

Where a split's status is `unconfirmed`, the workbook greys the cell and **suppresses the
derived figure**. Nothing we have not documented is ever estimated.

## Two burn mechanisms, never conflated

Burning happens two different ways, and treating them as equivalent produces a confidently
wrong number:

- **Transfer burn** — tokens move to an address no one controls, readable as a balance. Gets a
  contract entry and `burn_read_method: "transfer"`. PancakeSwap, Venice, Uniswap, Sky, GEODNET.
- **Protocol burn** — supply is destroyed at the protocol level with no transfer. There is *no*
  address to read; `burn_address` is `None` and the method is `"protocol_level"`. Ethereum,
  Solana, Near, Canton, Injective. Reading a dead address for one of these returns other
  people's discarded tokens, not the protocol burn. Injective's case is the trap: the
  `0x1111...1111` address that circulates publicly is a contribution subaccount, **not** a burn
  destination, and the Gap Report says so where someone would otherwise be tempted to add it.
- **Undetermined** — Tron, where four candidate black-hole addresses circulate *and* the chain
  also burns at the protocol level. Nothing is read until the mechanism itself is settled.

The tier 2 adapter refuses to read a burn address unless the method is `transfer`, so the
distinction is enforced rather than merely documented.

## Read methods matter more than addresses

A correct address read the wrong way produces a number that is wrong by orders of magnitude and
looks entirely plausible in a cell. Two cases are handled explicitly:

- **veNFT versus ERC-20.** A vote escrow can be a fungible ERC-20, where `totalSupply()` is the
  staked amount, or an NFT position, where `totalSupply()` is a *count of positions*. veAERO is
  the latter: read as an ERC-20 supply it would report a few thousand instead of hundreds of
  millions of AERO. Lock contracts carry `read_method` and `token_standard`, `escrow_balance_of`
  reads `underlying.balanceOf(escrow)`, and pairing `erc721` with `erc20_total_supply` is
  rejected at import by `config.validate_config()`. A read method left unset means **refused**,
  never assumed.
- **Multi-chain supply.** CAKE is a LayerZero OFT, so BSC `totalSupply` is not total supply. The
  adapter sums every declared deployment and marks the result `PARTIAL` in the source string, the
  Review Queue and the sheet, so a one-chain figure never reads as complete.

## Deprecated contracts are removed, not kept as fallbacks

vePENDLE is winding down and users are migrating to sPENDLE. A vePENDLE balance read would show a
*falling* figure that reflects migration rather than falling lock-in, which is a false negative on
the exact metric this tool exists to measure. It was deleted rather than retained.

## Hold is not a payout

`buyback_destination` drives `destination_effect`, and a hold and a distribution move float in
opposite directions, so they never share a formula. Chainlink's Reserve has a multi-day withdrawal
timelock with no withdrawals expected for years, so accumulated LINK is **locked supply**: it is
subtracted from effective float. A distribution returns tokens to float instead. The A3 tab has a
separate column for each, and no project can populate both.

## A later tier never overwrites an earlier one

Frames concatenate in tier order and the store upserts on `(date, project, metric)`, so without a
guard the last writer would win and a tier 3 page scrape would overwrite a verified tier 2
contract read. `_resolve_tier_collisions` keeps the earlier tier, drops the later one, and raises
both a Review Queue item and a Gap Report row, because a collision means one of the two entries
is pointing at the wrong metric. A deliberate second source is a cross-check with its own metric
name, so it never collides.

## Two sources for one figure are compared, not silently merged

Where a figure is available from both a contract read and the protocol's own dashboard, both are
stored as separate metrics. The contract read is preferred; a divergence beyond the configured
tolerance is flagged to the Review Queue, and the divergence is shown as a column on the sheet.
Chainlink's Reserve and the four lock rates (Aerodrome, Pendle, Sky, Aave) all work this way.

## Ambiguous addresses are never guessed

Where two or more addresses circulate publicly and none is established, config lists them all
under `candidates` with `ambiguous: True`, and the adapter refuses to read any of them. Two
cases today: the SKY token (two addresses differing only after the eighth character, which is
exactly how a transposition survives) and Tron's four black-hole candidates. Picking one on a
guess would silently poison every downstream figure for that project.

## Splits change, and history is not rewritten

`fee_split.history` records each period's split with its own source and status. At build time
every comparison window is matched to the split that actually applied to it. A window that
spans a change, or sits in a period we have not documented, reads `unconfirmed` and its derived
figure is **suppressed**.

Sky is the live case: the 55/45 split dates from a governance proposal of 13 August 2026, so
the trailing-quarter window spans the change and is suppressed rather than being computed at
55%. That column stays blank until a full quarter sits after the change date. That is the
correct answer, not a bug.

A per-product split is never collapsed into one number either. PancakeSwap burns 15-23% of spot
trading fees and 20% of perpetual trading profit; those are shown as separate figures and the
single implied column is suppressed.

## Self-reported figures win

Where a protocol publishes a figure itself, that is the number used. `self_reported_net_mint`
marks those projects, and the A4 headline prefers the published net mint over the derived
issuance-minus-burn, showing the derived figure beside it so a divergence is visible. The
preference is keyed on the config flag, not on data merely being present, so a project that
does not publish net mint always derives.

`reference_values` in config holds figures the protocol has already published (PancakeSwap's
May and June 2026 net mint). Every run compares the scraped value for those months against
them, so a scraper that drifts is caught as a regression rather than quietly rewriting history.

## Revenue that has not landed is not booked

`revenue_sources` carries a `booked` flag. Hyperliquid's AQAv2 leg went live on 26 August 2026
and accrues on 30-day cycles, but the first payment is not due until 3 October 2026, so it is
recorded with `booked: False` and excluded from revenue. It shows on the Config tab as
accruing-but-not-booked, and the Gap Report carries a reminder to flip it once a payment is
actually observed.

## Status

The universe is **30** projects.

**No live run has happened yet.** The development sandbox's egress policy blocks DefiLlama,
CoinGecko, Dune, every public RPC endpoint and every protocol site, so tiers 1 to 5 have been
built and tested against stubs and fixtures but never against a real source.

**No live run has happened yet.** The development sandbox's egress policy blocks DefiLlama,
CoinGecko, Dune, every public RPC endpoint and every protocol site, so tiers 1 to 5 have been
built and tested against stubs and fixtures but never against a real source. Everything offline
is verified: the full pipeline runs end to end with all 148 network calls failing and still
produces a workbook that recalculates with zero formula errors, and the DOM-anchor contract
passes against real headless Chromium. Run it on a machine with network access and work the
Gap Report tab.
