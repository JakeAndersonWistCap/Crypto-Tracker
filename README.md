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

`sources.yaml` ships with the schema documented inline, two worked examples, and 53
pre-populated stubs for the metrics where a published page is the only route.

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
10. **Run Log** — rows per source and tier, failures, last successful fetch per source/project.

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

## Status

The universe table in the brief lists **29** projects (the heading says 30). All 29 are
configured; nothing was invented to reach 30.

**No live run has happened yet.** The development sandbox's egress policy blocks DefiLlama,
CoinGecko, Dune, every public RPC endpoint and every protocol site, so tiers 1 to 5 have been
built and tested against stubs and fixtures but never against a real source. Everything offline
is verified: the full pipeline runs end to end with all 148 network calls failing and still
produces a workbook that recalculates with zero formula errors, and the DOM-anchor contract
passes against real headless Chromium. Run it on a machine with network access and work the
Gap Report tab.
