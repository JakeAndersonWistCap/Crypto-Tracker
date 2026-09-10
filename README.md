# Crypto-Tracker — token supply & demand metrics

Pulls fundamental supply-and-demand metrics for a fixed universe of crypto assets, stores the
full available history locally, and regenerates an Excel workbook on every run.

```
python token_metrics.py
```

One command, no arguments. It fetches, updates the local store, and rebuilds the workbook.

## Why

Reported burn figures are almost always gross, published in isolation, with emissions disclosed
somewhere else. This tool puts **gross burn, gross issuance and net supply change side by side**
for every asset, shows the ratio between them, and tracks whether each project is improving on
its own metrics over 30 days and 3, 6 and 9 months.

## Layout

| File | Role |
|---|---|
| `config.py` | One dict per project: archetypes, fee-split / burn-split parameters, source URL + date for every parameter, data-source identifiers, Dune query slots, issuance schedules. **No logic.** |
| `fetch.py` | Source adapters. Each returns tidy long frames: `date, project, metric, value, source`. A failed source is logged, never fatal. |
| `store.py` | SQLite store (`metrics.db`): one long table keyed on `(date, project, metric)`, upsert on conflict. Manual overrides in their own table so a fetch can never clobber them. |
| `build_workbook.py` | Reads the store, writes `token_metrics.xlsx` from scratch. Derived figures are Excel formulas, not Python numbers. |
| `token_metrics.py` | Orchestrator. |
| `manual_overrides.csv` | `date, project, metric, value, source_note, entered_on`. Loaded last; wins for matching keys. |
| `recalc.py` + `office/` | LibreOffice-based formula recalculation and error scan (needs `libreoffice-calc`). |
| `tests/seed_fixture.py` | Builds the workbook from **synthetic** data (`FIXTURE:` sources) and recalcs it — for validating formulas offline. |
| `tests/test_adapters.py` | Adapter parsing against payloads shaped like the real APIs, HTTP stubbed. |

## Setup

```
pip install -r requirements.txt
cp .env.example .env        # add DUNE_API_KEY (and optionally COINGECKO_API_KEY)
python token_metrics.py
```

**First run** backfills the entire available history from every source (DefiLlama serves full
series; CoinGecko's public tier serves 365 days, which covers the 3/6/9-month columns).
**Subsequent runs** re-fetch a trailing 30-day window to catch source revisions and extend the
series. The store is the durable artefact; the workbook is disposable output.

Validate a build: `python recalc.py token_metrics.xlsx` must report `"status": "success"`.
Never ship on `errors_found`.

## Sources

| Source | Used for | Access |
|---|---|---|
| DefiLlama | fees, revenue, holders revenue, protocol TVL, chain TVL, stablecoin supply by chain, RWA category by chain | free, no key |
| CoinGecko | price, market cap, volume, circulating/total/max supply, FDV; implied circulating supply = mcap ÷ price (labelled `coingecko:mcap/price`) | free; optional demo key |
| Dune | burn, issuance, emissions, actual buybacks, locks, nodes, tx counts… — anything protocol-specific | `DUNE_API_KEY` in `.env`; query ids go in `config.py` `dune_queries` |
| `schedule:config` | deterministic issuance for Bitcoin, Zcash, Bittensor, Venice (stepped) | from `config.py` |
| RWA.xyz | RWA on chain | no API on our tier → `manual_overrides.csv` |
| Token Terminal | cross-check | no API on our tier → `manual_overrides.csv` |

Every stored value carries its source and the workbook shows it. Nothing is silently substituted:
a metric with no data reads `n/a`, never 0.

### Dune query slots

`config.py` has a `dune_queries` slot per project and metric with `query_id: None`. Until a
query id is filled in, the Run Log lists the slot as **unconfigured** and the cell reads `n/a`.
Set `query_id`, `date_col` and `value_col` to match the query's result columns.

## Workbook

Sheets, in order:

1. **Master** — all projects, archetype labels, all live figures, legend at the bottom.
2. **A1 Infrastructure** — fees ÷ issuance is the key ratio; TVL the primary demand metric; RWA from both sources with the divergence shown.
3. **A2 Coordination** — customer revenue per token emitted, supplier earnings from emissions vs customers.
4. **A3 Revenue Buyback** — revenue × documented split ÷ 90-day average price ÷ supply, annualised = buyback as % of supply; actual buyback alongside; emissions, net absorption, coverage ratio, lock rate. Yield destination is tracked separately from burn and never netted.
5. **A4 Permanent Burn** — gross burn, gross issuance, **net supply change** (headline), burn ÷ issuance, burn as share of fees, both as % of supply annualised, implied vs actual, CoinGecko supply cross-check.
6. **Charts** — native Excel charts off the tabs above.
7. **Config & Sources** — every lever with its URL, date, discretionary flag and status. Global levers (comparison window, annualisation) at the top.
8. **Run Log** — rows per source, failures, unconfigured slots, last successful fetch per source/project.
9. **Data** — raw window aggregates per project × metric (literals). `Now` = trailing 30d sum (flow) or latest (stock); `Q0..Q3` = consecutive 90-day windows back from today.
10. **Monthly** — calendar-month aggregates feeding the time charts.

Conventions: Arial throughout · `$#,##0` currency, unit in the header · percentages stored as
fractions · zeros render `-` · negatives in parentheses · **blue** hardcoded inputs / levers ·
**black** formulas · **green** cross-sheet pulls · **yellow fill** manual override (entered_on in
the cell comment) · **grey fill** unconfirmed split, derived figure suppressed · **orange fill**
stale (last good fetch in the comment) · **amber fill** programme paused.

Token conversions use the period-**average** price; the spot price is shown separately and
labelled.

## Notes on the universe

* The brief's table lists **29** projects (it says 30). All 29 are configured; nothing was invented to make 30.
* `status: "unconfirmed"` wherever the brief said confirm/verify (Solana, Tron, Near, Canton, Plume, Chainlink, World Mobile, Aethir, Venice split, Maple, Morpho, PancakeSwap split, Sky split, Fluid, Ether.fi). Aave is `paused` (19 April 2026). Morpho and Aethir hold archetype 3 (`archetypes_held`).
* World Mobile is manual-only via `manual_overrides.csv` until their page returns.
* CoinGecko ids and DefiLlama slugs were set from memory for a few names (Canton, peaq, Plume chain name, Hyperliquid chain name, Aerodrome parent slug); a wrong id shows up as a 404 in the Run Log — fix it in `config.py`.
* The first live backfill has not been run from the development sandbox (its egress policy blocks the data hosts). Run it locally and check the Run Log tab.
