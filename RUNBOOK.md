# RUNBOOK — first live run

For someone starting from a fresh clone on a machine with network access, who has not run this
before. Every step has the exact command, what success looks like, and what failure looks like.

Total time for a first run, including setup: **roughly 20 to 35 minutes**, most of which is the
one-time Chromium download. The data fetch itself is 4 to 11 minutes.

---

## 1. Prerequisites

You need Python 3.10 or newer, git, and (optionally) LibreOffice for the formula check.

```bash
python3 --version
git --version
soffice --version      # optional, only used by recalc.py
```

**Expected:** `Python 3.11.x` or similar (3.10+), a git version, and either a LibreOffice
version or `command not found`.

**If Python is older than 3.10:** the code uses `X | None` type syntax and will fail at import
with `TypeError: unsupported operand type(s) for |`. Install a newer Python.

**If `soffice` is missing:** that is fine. Everything works; you only lose the automated formula
check in step 8. To add it: `brew install --cask libreoffice` on macOS,
`sudo apt install libreoffice-calc` on Debian/Ubuntu.

---

## 2. Clone and branch

```bash
git clone https://github.com/JakeAndersonWistCap/Crypto-Tracker.git
cd Crypto-Tracker
git checkout claude/crypto-metrics-supply-demand-ovkg8g
git log --oneline -1
```

**Expected:** a commit line. The branch name is long; copy it rather than typing it.

**If checkout fails** with `pathspec ... did not match`: run `git fetch origin` then retry.

---

## 3. Virtual environment

Keeps these dependencies out of your system Python.

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Windows (cmd):**
```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
```

**Expected:** your prompt gains a `(.venv)` prefix. Confirm with `which python` (macOS/Linux) or
`where python` (Windows) — it should point inside `.venv`.

**If PowerShell refuses** with `running scripts is disabled`, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and activate again.

You must re-activate in every new terminal. If a later step reports `ModuleNotFoundError`, the
usual cause is a terminal where the venv is not active.

---

## 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

**Expected:** `Successfully installed ...` listing pandas, openpyxl, requests, python-dotenv,
numpy, PyYAML, web3 and playwright. Takes 1 to 3 minutes; `web3` pulls a lot of transitive
dependencies.

**Verify:**
```bash
python -c "import pandas, openpyxl, requests, yaml, web3, playwright; print('all imports ok')"
```

**If `web3` fails to build** on an older pip: `pip install --upgrade pip` then retry.

---

## 5. Install the browser for the scraper

```bash
playwright install chromium
```

**This downloads roughly 150 to 400 MB and is one-time per machine.** It is a real browser
binary, not a Python package, which is why it is separate from step 4.

**Expected:** a progress bar, then `Chromium ... downloaded to ...`.

**If it fails behind a corporate proxy,** set `HTTPS_PROXY` and retry. If you cannot install it,
the run still works: the eight tier 3 page scrapes fail, are logged, and appear in the Gap
Report. You lose those eight metrics, nothing else.

---

## 6. Create your `.env`

```bash
cp .env.example .env
```

Then edit `.env`. **Nothing in it is required for the first run.** Every variable is optional and
every one degrades gracefully.

| Variable | Required run one? | Where to get it | If missing |
|---|---|---|---|
| `DUNE_API_KEY` | **No** | dune.com → Settings → API | Tier 4 is skipped. No Dune query ids are configured yet, so nothing is lost today. |
| `COINGECKO_API_KEY` | No | coingecko.com/en/developers/dashboard (free demo key) | Falls back to the public tier: slower, rate-limited harder. 429s are retried with backoff, then gapped. |
| `RPC_ETHEREUM`, `RPC_BSC`, `RPC_BASE`, `RPC_POLYGON`, `RPC_UNICHAIN` | No | Any provider (Alchemy, Infura, QuickNode), comma-separated for a fallback list | Uses the public endpoints built into `config.DEFAULT_RPC`. Public endpoints are rate-limited and sometimes flaky; if every endpoint for a chain fails, that chain's reads are gapped and the rest of the run continues. |
| `TOKEN_METRICS_ALLOW_UNVERIFIED` | No — **leave unset** | n/a | Unverified contract addresses stay refused with a gap row. Setting it to `1` reads them anyway and flags every resulting value in the Review Queue. |

**Nothing fails the run loudly.** A missing credential or an unreachable source is logged to the
Run Log tab and written to the Gap Report. The run always produces a workbook.

---

## 7. Pre-flight: check connectivity BEFORE the backfill

This is the step that saves you forty minutes.

```bash
python preflight.py            # the plan: what will run, what won't, and why. No network calls.
python preflight.py --check    # probes every dependency, ~30 seconds
```

**Expected from `--check`:** a list of `OK` lines covering three HTTPS APIs, five chains, the
TRON node API, six scraper domains, Playwright and LibreOffice, ending with
`ALL DEPENDENCIES REACHABLE`.

**If something is `DOWN`,** the summary separates critical from optional:

- **Critical** means DefiLlama, CoinGecko, or an entire chain's RPC fallback list. These carry
  most of the run. Fix before backfilling — see step 12.
- **Optional** means a scraper domain, the TRON node, Playwright, or LibreOffice. The run
  proceeds and gaps those metrics.

`--check` exits non-zero if anything critical is down, so it works in a script:
`python preflight.py --check && python token_metrics.py`.

**Do not start the run when the check reports critical sources down.** Each failed call retries
four times with exponential backoff (2s, 4s, 8s, 16s), so roughly 30 seconds per failing call.
A run with no network takes *longer* than a successful one — well over ten minutes of grinding
through retries to produce almost no data. Thirty seconds of `--check` saves that every time.

To fail fast deliberately while debugging, set `TOKEN_METRICS_RETRIES=0`:

```bash
TOKEN_METRICS_RETRIES=0 python token_metrics.py
```

Run `python preflight.py` with no arguments first anyway. It tells you exactly which metrics
will be attempted and which will not, so nothing in the output of step 8 is a surprise.

---

## 8. The first real run

```bash
python token_metrics.py
```

**This takes 4 to 11 minutes.** It pulls full history, not a snapshot, so the 3/6/9-month
trajectory columns are populated on run one rather than accumulating from today.

**Normal progress output**, in this order:

```
INFO token_metrics: run 20260911T... — FIRST RUN: full backfill
INFO token_metrics.fetch: tier 1 — schedule:config (window=full history)
INFO token_metrics.fetch: tier 1 — defillama (window=full history)
INFO token_metrics.fetch: tier 1 — coingecko (window=full history)
INFO token_metrics.fetch: tier 2 — chain (window=full history)
INFO token_metrics.fetch: tier 2 — tron_node (window=full history)
INFO token_metrics.fetch: tier 3 — scrape (window=full history)
INFO token_metrics.fetch: tier 4 — dune (window=full history)
INFO token_metrics: upserted NNNNN rows
INFO token_metrics: run summary: NNNNN rows | N fetch failures | N review items | NNN gaps | 0 manual overrides
INFO token_metrics: wrote .../token_metrics.xlsx
```

Roughly per stage: DefiLlama about 30 seconds, CoinGecko about 2 minutes (pure rate limiting,
60 calls with a 2.2 second floor between them), contract reads about 25 seconds, page scrapes
80 seconds to 6 minutes.

**`WARNING ... FAILED` lines during the run are normal.** Some sources are expected to fail; see
step 10 for which. They are logged, not fatal.

**What a stall looks like:** no new log line for more than about 60 seconds. The most common
cause is one RPC endpoint hanging rather than refusing. Wait 2 minutes, then Ctrl-C — the store
is written incrementally so nothing is corrupted — and re-run. If it stalls in the same place
twice, set the relevant `RPC_*` variable in `.env` to a provider you control.

**Optional formula check** (needs LibreOffice):
```bash
python recalc.py token_metrics.xlsx
```
**Expected:** `"status": "success"` with `"total_errors": 0`. Anything else means a formula
problem; send me the output.

---

## 9. Where the outputs land

All in the repo root:

| Path | What it is | Keep it? |
|---|---|---|
| `token_metrics.xlsx` | The workbook. **Rebuilt from scratch every run.** | Disposable. Never edit it expecting changes to survive. |
| `metrics.db` | SQLite store, the full history. **This is the durable artefact.** | Back this up. Losing it means re-backfilling. |
| `.cache/scrape/YYYY-MM-DD/` | Cached page responses, one folder per day | Safe to delete; it just forces a re-scrape. |
| `manual_overrides.csv` | Your hand-entered values | Yours. Never overwritten by a run. |

Logs go to the terminal only. To keep them:
```bash
python token_metrics.py 2>&1 | tee run-$(date +%Y%m%d-%H%M).log
```

---

## 10. What to check in the workbook, in order

Open `token_metrics.xlsx`. Check these four tabs in this order. **Some failures are expected on
a first run**, so here is what healthy looks like for each.

### 10.1 Run Log — did the plumbing work?

Look at the per-source table at the top.

**Healthy on run one:**
- `defillama` and `coingecko`: high OK counts, a handful of failures. Some projects genuinely are
  not on DefiLlama, and a few slugs may be wrong — those show as failures naming the slug.
- `chain`: around 19 OK reads across five chains. Some failures are normal if a public RPC is busy.
- `scrape`: up to 8 OK. Zero here with Playwright installed means the pages changed shape; check
  the Gap Report for the specific reason.
- `dune`: zero OK, many "unconfigured". **This is correct** — no query ids are configured.
- **Total fetch failures under about 30 is normal.** Over 100 suggests a systemic problem such as
  no network or a blocked proxy.

**Not healthy:** every source at zero OK. That means connectivity, not configuration. Go back to
step 7.

### 10.2 Gap Report — the to-do list

Sorted so decisions come first.

**Healthy on run one:**
- **`[open]` rows at the top, around 17.** These are questions for a human, not failures. Expected.
- **`[config]` rows next, around 15.** Splits we have not documented, so the derived figure is
  deliberately suppressed. Expected.
- **Data gaps below, several hundred.** Most are metrics no source covers yet, each with a
  specific reason and a suggested fix. Expected on run one and the reason this tab exists.

**Worth acting on immediately:** any row saying `verified but the tier 2 read returned nothing
this run`. That means a confirmed address failed to read, which is an RPC problem, not a
configuration one.

### 10.3 Review Queue — values the validator would not accept silently

**Healthy on run one:** possibly empty, or a handful of `change_threshold` rows. On a first run
there is no prior value to compare against, so most checks have nothing to fire on.

**Worth acting on:**
- `out_of_bounds` — the value was **rejected and is not in the store**. Either the source is
  wrong or the bound in `config.py` is too tight.
- `cross_check_divergence` — a contract read and a published dashboard disagree. One of them is
  wrong. This is the tab's most valuable output.
- `anchor_unconfirmed` — expected for the eight enabled scraper entries. Their anchors were
  inferred without sight of the pages. **Eyeball each value against the page once**, then remove
  `needs_first_run_check` from that entry in `sources.yaml`.
- `supply_partial` — expected for PancakeSwap. CAKE is a multi-chain token, so the on-chain sum
  covers only known deployments.

### 10.4 Master — the actual numbers

**Healthy:** price, market cap and supply populated for most projects; fees and revenue for the
DefiLlama-tracked ones; `n/a` in many burn and buyback columns.

`n/a` is a deliberate gap, never a zero. Every one has a matching Gap Report row explaining it.

**Wrong-looking numbers to check against the Gap Report before trusting:** anything greyed
(unconfirmed split, figure suppressed by design), anything in the net supply change column for a
project whose burn source is not yet configured.

**Read the Skipped section of the Run Log before you read anything else.** A skipped source
produces no error, no failure and no gap — it looks exactly like a source that ran and had
nothing to add. Tier 4 skips any series the store already holds a row for, so a single tier 2
row is enough to stop a backfill that has never run. `TOKEN_METRICS_DUNE_ALWAYS=1` forces it.

**Deleting a bad FLOW row does not make it recompute.** A flow is the difference between two
stored STOCK readings, so the stock rows are what determine it. If a flow row is wrong because
the stock row beside it is wrong, deleting the flow alone leaves the next run differencing from
the bad stock — and the real move is never recovered. PancakeSwap's 59,857,159.01 burn is the
case: with the 09-14 stock row still holding the post-jump balance, the next run computes a delta
of 43, not 59.86m. **Delete the STOCK row for that date as well**, and the next run differences
from the last good earlier date and recovers the magnitude — dated to the run day rather than the
day it happened, which is the best the store can do after the fact.

**Removing a source from config does NOT remove what it already wrote.** The store upserts and
never deletes, so a contract taken out of `config.py` stops producing NEW rows while every row it
already wrote stays exactly where it is — with its old source string, and reading as current until
it passes the 7-day stale threshold. Two real cases: Sky's `burn_zero` rows survived the removal of
the contract and rendered as a measured zero on a refuted mechanism, and Uniswap's burn switching
from the Firepit to the dead address produced a **111m UNI "burn"** that was simply the gap between
two different addresses. Clear orphans deliberately:

```bash
sqlite3 metrics.db "SELECT project, metric, source, COUNT(*), MIN(date), MAX(date)
                    FROM metrics GROUP BY project, metric, source ORDER BY project"
```

Any source naming a contract that is no longer in `config.py` is an orphan. Delete those rows by
source before trusting the metric.

**Supply history does not exist before your first run, and never will.** CoinGecko serves
`total_supply` as a current value only. `/coins/{id}/history` was checked on a live call
(2026-09-14) and returns `current_price`, `market_cap` and `total_volume` — no supply field. So
issuance derived from the supply change is **forward-only**: it begins accumulating the day the
tool first runs, and no amount of work will backfill it.

Historical issuance therefore exists only where one of two things provides it: a **config-declared
schedule** (Bitcoin, Zcash, Canton, Render) or a **live issuance endpoint** (Solana's
`getInflationRate`, Injective's mint module, NEAR's protocol config, beaconcha.in for Ethereum).
For every other project, a 3/6/9-month issuance trajectory will be blank until enough runs have
accumulated — and that is a permanent property of the data, not an unresolved gap.

**An address can be right and still be the wrong thing to read.** Verifying an address answers
"does this address exist and hold what we think" — not "does this protocol burn the way we
assumed". Sky passed every address check and its model was still wrong: it burns through a
Splitter and an AMM Flapper, never through a dead address. So every project claiming a transfer
burn now declares a `burn_mechanism` block saying where that model is documented. Where it says
`assumed`, the figure is still reported but flagged, and a Gap Report row names the document that
would settle it. `python -c "import config; config.validate_config()"` rejects a transfer burn
with no block at all.

**A differenced flow needs two observations on two different dates.** Burn flows are derived by
differencing a cumulative balance, so on the first reading — and on a second reading the same day,
which upserts onto the same row — there is no interval to difference and no flow is produced. The
cell reads `n/a` with a reason in the Gap Report, never `0`. It resolves itself on the next day's
run; nothing to fix.

If your store already holds zeros written before this rule existed, they will not clear
themselves, because the store upserts and never deletes:

```bash
sqlite3 metrics.db "DELETE FROM metrics WHERE metric='gross_burn_tokens' AND value=0 AND source LIKE '%:delta'"
```

That removes only differenced zeros, leaving genuine measured burn figures untouched.

**A lilac zero is not a measured zero.** Where a burn is read as a *balance* and differenced into
a flow, an unchanged balance produces 0 — and that 0 is consistent with no burn, with a burn that
routed somewhere other than the address being watched, and with the store simply not having
watched for long enough. All three look identical. Those zeros are stored, flagged to the Review
Queue, filled lilac and given a hover comment; only a transfer history settles which one it is.
Projects affected: Sky, Venice AI, PancakeSwap, Uniswap, GEODNET.

**`none available` is not `n/a`.** `n/a` means no value in the store — possibly not yet sourced,
possibly broken. `none available` in grey italics means somebody chased it, there is no route,
and the cell is empty on purpose. Hover it for what was tried, or read the **Closed** block at
the bottom of Config & Sources. Nothing in that block belongs in the Gap Report, and nothing in
it should be re-attempted without new information.

### 10.5 Staging — captured, used by nothing

Figures a source returned that no metric in the library takes: Ether.fi's `agg_14` and `agg_30`,
for instance, which are candidates for the 30-day trajectory column. **No cell on any other sheet
reads this one.** It exists so a useful column found while mapping a query is neither thrown away
nor quietly promoted into a number somebody is relying on. Adopting one is a deliberate edit to
`config.py`, never a default.

---

## 11. Re-running after a fix

**Re-running is safe.** It will not duplicate or corrupt stored history.

```bash
python token_metrics.py
```

The store is keyed on `(date, project, metric)` and upserts, so a re-run overwrites the same
rows rather than appending. The workbook is rebuilt from scratch every time.

What changes on a second run:
- Only a trailing 30-day window is re-fetched, not full history, so it is much faster.
- Tier 4 skips any series the store already has history for; Dune is a backfill dependency, not
  an ongoing one.
- Page scrapes from the same calendar day are served from `.cache/scrape/` and make no request.
  To force a fresh scrape, delete that day's cache folder.

**To force a Dune re-pull** — after correcting a column mapping, or where a backfill was skipped
because the store already held a row:

```bash
TOKEN_METRICS_DUNE_ALWAYS=1 python token_metrics.py     # or set it in .env
```

It is **additive and safe**. The store upserts on `(date, project, metric)`, so no row is
duplicated and none is lost: the only rows that change are ones with an identical key, which are
rewritten with the new value. A forced re-pull also ignores the trailing 30-day window and takes
the full history — rebuilding only the last month of a 794-row series would leave the rest at
their old values and still report success.

Two guards protect a verified contract read from the backfill landing on top of it: the
collision guard keeps the earlier tier where both wrote the same date, and the period-overlap
guard drops a monthly Dune row for any month the live read already covers, while still
backfilling the months it does not. Both are flagged to the Review Queue rather than done
silently.

**To start completely fresh** (you will re-backfill, so only do this deliberately):
```bash
mv metrics.db metrics.db.backup
python token_metrics.py
```

**After editing `sources.yaml` or `config.py`,** just re-run. Validate config changes first with
`python -c "import config; config.validate_config(); print('ok')"` — it rejects mistakes that
would produce plausible-looking wrong numbers.

---

## 11j. Monthly: refresh the Artemis settlement-volume export (manual step)

Settlement volume, and the Network Reserve Ratio built on it, come from Artemis CSV exports that
you save by hand. The Artemis API answered HTTP 410 without a key, so nothing reads it
automatically. Once a month:

1. Open Artemis → the asset (e.g. Ethereum) → Metrics → Market Data → **Settlement Volume**
   (`classic.artemis.ai/asset/ethereum?tab=metrics&category=MARKET_DATA&metric=SETTLEMENT_VOLUME`).
   Set the range to the full history, then export as CSV.
2. Save it in the **repository root** or in **`data/artemis/`**, overwriting the old file. Both name
   forms work, so you never need to rename: Artemis's own `<Chain> - Settlement Volume.csv` and
   `<Chain>_-_Settlement_Volume.csv`. The chains are listed in `config.ARTEMIS_SETTLEMENT["chains"]`
   (Ethereum, Near, Hyperliquid). Setting `TOKEN_METRICS_ARTEMIS_DIR` in `.env` replaces both folders.
   The file must have exactly two columns, `DateTime` and `<Chain> - Settlement Volume`, or it is
   refused, so a second definition can never be mixed in. A UTF-8 byte-order mark is fine. The
   **whole file** is imported every run, whatever the run's window.
3. **Do not commit it.** `.gitignore` excludes both name forms until Artemis's terms have been read.
4. Set `exported_on` for that chain in `config.ARTEMIS_SETTLEMENT` to the export date. It goes into
   the source string. If you leave it unset, the file's own date is used and the source says so.
5. Run `python token_metrics.py`. The Run Log line `artemis_csv` gives the exact path read, the rows
   read and stored, the date range, and how old the last day is. A missing file is a gap with that
   reason, never a failed run.

The NRR divides market cap on the export's **last date** by the 365 days ending on that date, so both
inputs share the same end date. A1 shows that date next to the ratio. Once it is more than 45 days
old, the cell turns stale (AMBER). Plume is closed as an ACCEPTED LIMIT: it is not on Artemis and
The Block does not cover it. A chain Artemis does not list gets the same treatment, with the page you
checked. Do not substitute another source's volume.

If an `ARTEMIS_API_KEY` ever appears, run `python check_offline_items.py settlement_sources` and paste
back what it prints. The API is wired only after a keyed response has been seen.

## 11k. beaconcha.in — dropped (2026-10-01)

beaconcha.in is no longer used at all: after the monthly reset the key's allowance was zero. Ethereum's
validator yield is Etherscan's d(Eth2Staking) plus priority fees (excluding MEV); its history is
forward-only from 2026-09-29.

## 11m. One-off seeds added 2026-10-01

```bash
python token_metrics.py --seed hl_candles         # Hyperliquid perps volume: a year of daily candles, ~1 call per market
python token_metrics.py --seed plume_settlement   # Plume P2P transfers (Artemis method, UNVALIDATED): ~21,755 pages, ~1.5h
python check_offline_items.py near_settlement_routes   # NEAR: BigQuery layout + dry-run bytes (11n)
```

Each seed caches its progress, so an interrupted run resumes; routine runs then add each new day.

**Progress lines and checkpoints (2026-10-01).** Every long seed prints a line like
```
PROGRESS plume_settlement ERC-20 Plume: 1,500/~21,700 pages (6.9%); reached back to 2026-09-12 (floor 2025-10-01); 18 day(s) with transfers; 41,230 P2P transfer(s) kept this run; elapsed 5m12s; remaining ~1h10m; state saved
```
every 500 pages or 60 seconds, whichever comes first (day loops print every 10–50 days or 60 s).
"state saved" means the checkpoint was written to the seed's state file
(`.cache/logscan/plume-settlement.json` for Plume) at that moment, so Ctrl-C loses at most
the work since the last line, and re-running the same command picks up from there.
A stalled call times out (60 s read timeout) and is retried. Each retry is logged as
`RETRY <host>: ReadTimeout after 60s (attempt 1 of 4)`, with the host only and never the URL or key.
Change the intervals with `TOKEN_METRICS_PROGRESS_EVERY` (units) and `TOKEN_METRICS_PROGRESS_SECONDS`.

**Plume settlement: the seed reads the year from `eth_getLogs` (route C, Jake 2026-10-02).**
`config Plume.settlement_rebuild.erc20_route` is `"logs"`. The seed asks rpc.plume.org for the Transfer
events of the 46 in-scope tokens (those DefiLlama prices) over ranges of up to 100,000 blocks. When
the node's 10,000-log cap refuses a range, the range is halved and retried, and it doubles back after
a success, so the dense weeks of late March 2026 just take more calls. Each address gets one cached
`eth_getCode` check. Day boundaries are found on the RPC (bisected, in batches), not on the explorer.
It resumes from whatever is saved, including a v2 seed that's partway through: that seed's full days
are kept and its partial oldest day is read again. Stop the overnight v2 seed before starting this
one, because both write `.cache/logscan/plume-settlement.json`.
```bash
python token_metrics.py --seed plume_settlement
```
Measured limits:
- explorer.plume.org /api/v2: 180 requests a minute per IP.
- The Etherscan-compatible /api: 10 requests per window of at least 40 minutes. `x-ratelimit-reset`
  is in milliseconds, so the tokentx route (B) would take 31–47 hours. It's opt-in only, with
  `PLUME_SETTLEMENT_ROUTE=tokentx`.
- `BLOCKSCOUT_API_KEY` has no effect on this instance.

Every Plume host's `x-ratelimit-*` headers are honoured: when nothing remains, the run waits out the
reset and logs it. `python check_offline_items.py plume_settlement_routes` re-measures everything,
with each endpoint's own limit counted.

A Plume seed started on code older than 2026-10-01 saved its state only at the end. Let it finish,
or stop it and start again on the new code. Don't run both at once: they write the same state file.


## 11ae. Jake's probe + run 2026-10-06 16:33: Ether.fi's new route wired, NEAR burn gates, Aethir tile and APRs

**1. Ether.fi.**

- **(a) Bought = any DEX fill.** `log_scans.buyback_wallet_inflow` now uses attribution `swap`. An inflow counts
  when either:
  - its sender is a swap venue: CoW GPv2Settlement, or the Uniswap v4 PoolManager
    `0x000000000004444c5dc75cB358380D2e3dE08A90` (Uniswap/sdks `sdk-core/src/addresses.ts`, read 2026-10-06); or
  - the wallet paid ANOTHER token out in the same transaction (any router or aggregator).

  The outgoing transfers come from a topic-only explorer query (every contract), cached. A transfer with nothing
  paid out is not a purchase: 0x83971edb's 5M, the deployer, the 600K Safe.
  - `check_offline_items.py etherfi_topup_safe` re-classifies the top-up Safe's inflows on that rule, bought vs
    transferred by month and by sender.
  - The `etherfi_sethfi_topups` line now counts v4 fills too.
- **(b) 0x83971edb:** not in ether.fi's `Deployed.s.sol`, and no web hit. `etherfi_topup_safe` reads it on-chain:
  code size, Safe owners vs the buyback Safe, VestingWallet getters, and where its ETHFI came from. Either way its
  5M is counted as a transfer, never a buyback. Paste the probe output back to name it.
- **(c) Wired:**
  - **Buyback:** `actual_buyback_tokens` = DEX buys into the old wallet (dormant since 2026-04-01) plus the top-up
    Safe `0x3fb6784e…` (4 of 5 owners shared with the buyback Safe). One series, the same source, a per-holder
    line in the log.
  - **Yield:** the token yield's numerator is `sethfi_topup_tokens` (all rewards, however funded). The new A3
    column "of which BOUGHT" = Q0 buys ÷ Q0 top-ups, capped at 100%, with purchases allocated first; the rest was
    transferred in.
  - **Status:** ACTIVE via the top-up Safe. The first top-up (2026-08-13) predates the 2026-09-03 vote.
- **(d) DefiLlama is a cross-check only:**
  - DefiLlama's holders revenue is stored as `holders_revenue_usd_defillama`, a labelled cross-check. It reads $0
    for this route.
  - `holders_revenue_usd` is closed UNAVAILABLE for Ether.fi, and the dollar yield column reads the cross-check.
  - **To move the stored rows:** run `python run_sql.py CD`, then `python run_sql.py --delete CD`.

**2. NEAR burn.**

- **Why:** the full-span rule existed and refused days silently.
- **Fix:** `gross_burn_tokens history — …` now prints the revenue and price spans, the rows written, and every
  refusal with its dates:
  - no price;
  - no fees;
  - the 70% tripwire (0.7 ± 0.001), with the ratios it saw.
- **Expected:** on synthetic data with the ratio holding, it fills every day that has a price (365). If DefiLlama's
  older NEAR revenue was booked on a different methodology (ratio ≠ 0.7), the tripwire refuses those days and the
  line shows the ratio. That is a methodology question for the burn, not something to loosen. The issuance history
  then runs on whatever burn exists.

**3. Aethir.**

- **(a) Staked components:** the current `aiStaked` / `gamingStaked` / `edgeStaked` / `idcStaked` are read from
  the object carrying all four (`current_group`, at any nesting). The flat reader could not see that nested tile,
  and `stakeHistory` stays the monthly history.
- **(b) APRs:** the `ai` / `gaming` arrays are undated, so `staking_apr_ai` / `staking_apr_gaming` are closed
  UNAVAILABLE (client-computed). Your 2026-10-01 readings (12.49% / 14.08%) are the reference. The `apr_series`
  pin is removed.


## 11ad. Jake's run 2026-10-06 15:33: sETHFI top-ups, Aethir page gate, NEAR burn inputs

**1. Ether.fi: does the new programme pay INTO sETHFI?**

- **(a) The split:** every ETHFI inflow to the sETHFI vault is split by its own transaction.
  - sETHFI minted in the same transaction = a staking **deposit**, not counted.
  - No mint = a **top-up**: assets in, no new shares, so the share price rises.
- **(a) Where it lives:** the new log scan `sethfi_reward_topups` (full history, reconciled to the wei).
  - It stores `sethfi_topup_tokens` and labels known senders: the old buyback Safe, the deployer, the 600K Safe,
    the treasury and CoW.
  - Its run line ends `SINCE 2026-09-03: counted …` with the senders since the programme passed.
  - The old buyback Safe's own top-ups before 2026-04 are the old programme's distributions, which checks the
    method against known history.
  - The first runs seed the vault's whole history over several budgets.
- **(a)+(c) The probe:** `python check_offline_items.py etherfi_sethfi_topups` covers 2026-08-01 onward:
  - deposits vs top-ups, and the top-up senders by day;
  - each sender as a Safe, with owners shared with the buyback Safe;
  - where each sender's ETHFI came from (CoW GPv2Settlement = **bought**);
  - top-ups × same-day price vs DefiLlama's ether.fi-stake holders revenue over the same days, from metrics.db.
- **(b) Not wired yet:** the top-ups are a reward to stakers. They are a **buyback** only if bought; the
  programme lets up to 20M treasury ETHFI cover shortfalls. So `actual_buyback_tokens` (from 2026-09-03) and the
  yield numerator are switched once the probe shows the senders' ETHFI came via CoW. The old 0x2f53 scan stays as
  the dormant route until then; its status stays SILENT. Paste the probe output back.
- **(d) Parent id:** `parent_id` is now `parent#ether-fi`, as the run log said.

**2. Aethir pins did not run.**

- **Cause:** the once-a-day key was the URL alone, so this morning's read on the old config marked the pages
  done.
- **Fix:** the key now carries a fingerprint of `dashboard_pages`. Any config change re-reads every page once,
  that run.
- **customer_revenue_cumulative_usd** failed for the same reason: the demand page was skipped, so the monthly list
  was never parsed. When a page was read earlier today on the same config, the total now stands quietly instead of
  failing.

**3. NEAR burn history (the issuance cross-check).**

- **Cause:** `revenue_usd` / `fees_usd` were stored from the store's first 100-day window (2026-06-19).
  `fetch/backfill.py` skipped them because the derived burn's own unchanged first date marked it "limited", and
  that skipped its inputs too.
- **Fix:** short inputs are now re-asked over the year, and the run log says `Near/gross_burn_tokens: limited
  itself …, but input(s) ['fees_usd', 'revenue_usd'] are short of the year — re-read`.
- **The history rule:** `history_derive` then fills the burn (revenue / price, with the 70% tripwire) over
  whatever span comes back.
- **The cross-check line:** it now prints the revenue and fees spans. If they still start 2026-06-19 after that
  run, DefiLlama's own chart starts there (its NEAR adapter reads Allium; `start: '2020-07-21'` is declared, the
  served depth is not). A different source would be needed for the earlier year.


## 11ac. Jake's runs 2026-10-06 (14:07 good, 13:59 void): Sky Q0, Ether.fi parent, ETH coverage, Aethir pins, Chainlink pricing

**Pendle 11.35%:** came from the void 13:59 run. Dropped, nothing changed.

**1. "SKY bought Q0 (flapper Exec)" n/a.**

- **Cause:** in the 11:01 run the flapper_purchases log scan failed on the RPC fault. `_derive_buyback`'s
  split-route relabel then wrote that run's Stage 2 burn into `actual_buyback_tokens` as
  `…:as-buyback:PARTIAL`. That one row on the newest day sits beside the scan's rows, so the column has two
  measuring points. Status became MEASURING_POINT_CHANGED and now/Q0 went blank, even though the 14:07 scan
  reconciled (2,016,749,226.09 SKY over 17,863 transfers).
- **Fix:** a declared log scan now owns its column, the same way a Dune query does
  (`config.log_scan_declared`). When the scan produced nothing, the relabel stands down and logs:
  `actual_buyback_tokens: NOT derived from split route — log scan flapper_purchases sources this column and
  produced nothing this run …`.
- **To restore Q0, remove the stray row:** run `python run_sql.py CC`, then `python run_sql.py --delete CC`.
  The section removes the relabelled token row(s) and the USD row on the same date. Expect the
  "written after this section was authored" note: the row is dated today.

**2. Ether.fi holders revenue: "no protocol whose parentProtocol is parent#ether.fi".**

- I could not read the DefiLlama listing from here, so the id is **read at run time**:
  - **Configured id matches nothing:** the ONE parentProtocol carried by entries named like "ether.fi" is used,
    and the line ends `(fix parent_id in config)`.
  - **Two candidates:** nothing is stored, and both ids are named.
  - **None:** the failure lists every ether.fi-named entry with its parentProtocol and slug, so the next run
    answers the question either way.
- **The include filter:** children under the id but none named "stake" are now reported as such, not as "no
  protocol".
- **Action:** paste the line back and the config id gets fixed.

**3. Ethereum issuance +15.5% vs the curve.**

- **Cause:** the first row of a differenced flow (`derived:d_…`, `…:delta`) is dated the day it was read but
  holds the day before. Seven daily Eth2Staking steps were annualised over 6 covered days.
- **Fix:** `_window_coverage` now starts such a series one day earlier when its last row is the as-of day
  (covered_days and q0_covered_days).
  - A differenced series read through yesterday already gets the last day from the lag allowance, so it is
    unchanged.
  - Plain daily series are unchanged.

**4. Aethir pins** (from `aethir_pin_keys`):

- **protocol/ecosystem, pinned by key:**

  | Key | Metric |
  |---|---|
  | totalStaked | `stath_sophon_pool_tokens` |
  | checkerRewards | `eco_checker_rewards_cumulative_tokens` |
  | cloudHostRewards | `cloud_host_rewards_cumulative_tokens` |
  | edgeRewards | `edge_rewards_cumulative_tokens` (earnings + stipend; declared handover from the old summed tiles) |
  | stakingRewards | `staker_rewards_cumulative_tokens` |

- **Total Rewards Distributed** (`ecosystem_rewards_cumulative_tokens`) is the sum of the four, from the same
  run.
- **Supplier emissions:** the day-on-day rise of checker + cloud host + edge. Each leg is shown beside the sum.
  Staking is apart. The compute-rewards add-on (supply page totalRewards) is retired: cloud hosts are the
  compute providers, so keeping both would very likely count that leg twice. totalRewards stays stored.
- **Locked / circulating:** computed (totalStaked / athCirculatingSupply) and logged. It is no longer matched
  by value.
- **APRs:** each `ai` / `gaming` series is tested as of 2026-10-01. The candidates are the point, the mean
  to date, and the 7/30/90-day means. Exactly one within 1% of 0.1249 / 0.1408 is pinned and stored. Otherwise
  the APR is left **UNPINNED**, and the line lists every candidate.
- **Total Network Revenue:** the sum of `monthlyNetworkRevenue`, marked PARTIAL `[June-July 2024 not in the
  monthly list]`.
- **UNAVAILABLE (client-loaded):** purchases, staked edge devices, edge stipend, edge earnings (separately),
  edge daily pool.

**5. Chainlink legacy pricing.**

- **Why it never finished:**
  - The price cache was written only after the loop, so a timed-out tier lost every price it had fetched.
  - Tokens DefiLlama answers with no price were asked again on every run.
- **Fix:**
  - Days are priced newest first, so Q0 fills first.
  - The cache is saved every 10 days.
  - No new call is made after 90 s.
  - An answer with no price is remembered (null). A failed call is not remembered and is asked again.
- **When it stops early:** the run logs `pricing PAUSED at the 90s budget: N day(s) still to price`, and the
  next run resumes from there.
- **Refused-day count:** the `customer_revenue_ccip_legacy_usd` line gives the final refused-day count once
  that pause line no longer appears.


## 11ab. Jake's run 2026-10-06 11:01: RPC fall-through, run banner, Aethir keys, Ether.fi Safe

**1. The eth_blockNumber failure.** It was not the explorer change.

- **Where it failed:** `logscan.py` pins its head block through its own ChainReader. `ChainReader.web3()`
  kept the first endpoint that passed `is_connected()`. Cloudflare passes that handshake, then refuses
  `eth_blockNumber` with −32046. In this run the keyed endpoint and the three public endpoints before
  Cloudflare failed the handshake, probably on the same flaky network. So the scan reader settled on
  Cloudflare, while the chain tier's own reader got Alchemy.
- **Fix, connect:** an endpoint is kept only after it answers `eth_blockNumber`.
- **Fix, head-block read:** `ChainReader.block_number()` falls through the list (ETHEREUM_RPC_URL first, then
  `DEFAULT_RPC`) when the kept endpoint stops answering, and keeps the one that answers.
- Error lines name hosts only, never keys.

**2. Run health.**

- **(a) Earlier values survive a timeout.** A timed-out tier keeps the frames it had already produced
  (`_abandon`). The store only upserts the (date, project, metric) rows that were written; nothing is
  deleted.
- **(b) Network-wide failures are named.** When sources on **4 or more** adapters time out or cannot connect
  in one run, the summary now **starts** with `NETWORK-WIDE TROUBLE — N sources …: RERUN when the network is
  steady`.

**3. Aethir.**

- **The contradiction:** `key_diagnosis` never checked for a key present with a number, so a present key fell
  through to "not in the payload". It now says `IS in the payload`, quotes what follows the key (values and
  context), and gives the page's bytes, numeric keys and RSC chunks. The next run shows whether it was a
  partial load or a parse change.
- **Still matched by value:** Total Network Revenue, Onchain Compute Purchases, both APRs, locked/circulating,
  Sophon stATH, ecosystem rewards and the edge figures. Their keys were never recorded.
- **To pin them:** run `python check_offline_items.py aethir_pin_keys`. For each one it prints the nearest
  keys by current value and the keys named like its label. Paste it back and each gets its `key`, the way
  `arr` is pinned.

**4. Ether.fi.** 0x01e42ad3… is a Safe v1.4.1, 2-of-5, with the **same five owners** as the buyback Safe
0x2f5301a3…, including the deployer EOA 0x9eac7114… (Jake's `etherfi_safe_owners` read, 2026-10-06). It is now
labelled ether.fi-controlled, an internal transfer. **No ETHFI was bought into the buyback wallet after
2026-04-01.**

**5. NEAR re-auth.** The summary now starts with `ACTION NEEDED — NEAR BigQuery: Google login expired (your
org forces re-auth about daily)` and the two lines to paste:

    gcloud auth application-default login
    gcloud auth application-default set-quota-project near-data-510309

## 11aa. Chainlink legacy CCIP: 168 days refused for an unpriced event (Jake, 2026-10-06)

Problem: `customer_revenue_ccip_legacy_usd` refused 168 of 197 days because an event on each had no price. Customer revenue sums a day only when every leg is present, so those days went blank.

**What the 1.2/1.5 lanes are paid in.** Chainlink's own docs repo (`chains.json` feeTokens and
`tokens.json`, commit 2c185d06, read 2026-10-06) lists:

| Chain | Fee tokens |
|---|---|
| Ethereum, Arbitrum, Base | GHO, LINK, WETH |
| Polygon | LINK, WPOL |
| Optimism | LINK, WETH |

Their addresses are now in config (`chainlink_fee_lines.chains.*.fee_tokens`), each with a source.

**Pricing.** Each token is priced by its own chain:address on DefiLlama coins. The search window is
now ±12 h around 12:00 UTC, so it covers the whole day (DefiLlama's default is 6 h). Where the token's
own address has no point that day, it is priced by its documented underlying:

- WETH and WPOL → the chain's native coin
- LINK on any chain → LINK
- GHO on Arbitrum or Base → Ethereum GHO (the same token, bridged)

The refused days are re-valued on the next routine run. The emit step re-reads the whole cached
year, and failed prices are never cached, so they are asked for again.

**Still unpriced → PARTIAL, not refused, when the share is small.**

- **Rule:** the day is stored without the token only if the token's share is **≤ 5%** of the day's
  value (`unpriced_partial_max_share`). The source is marked `:PARTIAL[unpriced excluded: <chain>
  <symbol> <address> ~x%]`.
- **How the share is estimated:** the token's amount at the nearest priced day within 7 days. That
  estimate only decides the rule; it is never stored.
- **Otherwise the day is refused:** a larger share, or no price within 7 days.
- **Customer revenue** carries the PARTIAL marker on those days (see 11y).

**The listing.** The run-log line for each line now ends `UNPRICED: <chain> <symbol> <address>: N
event(s), D day(s) first..last`, and says whether DefiLlama answered without the token or the price
call itself failed. From 2026-10-06 the cache counts events per token per day. Days seeded before
then say "events not counted"; a re-seed would count them.

## 11z. Jake's run 2026-10-05 21:00: staked ETH, Sky minting, Chainlink prices, Ether.fi, Polygon, HL

**1. Ethereum staked ETH: the 88,441,790 was never stake, and ~36M is out of date.**
- The old formula was deposit-contract balance + ethsupply2 `Eth2Staking` − `WithdrawnTotal`. The deposit
  contract keeps every deposit ever made (~91.6M ETH by Oct 2026), because withdrawals are minted on the
  execution layer and never leave it. `WithdrawnTotal` is ~7.6M against ~47–55M actually withdrawn, so
  the sum double-counts everything exited and re-staked. None of these fields can be repaired into staked ETH.
- **New source:** `fetch/staked_eth.py` reads beaconcha.in's finalized-epoch `votedether` (the active
  stake that attested) as validatorqueue.com commits it to GitHub:
  `raw.githubusercontent.com/etheralpha/validatorqueue-com/main/historical_data.json`. The repo is MIT
  licensed and has one row per day since 2023-05-21. No key, one request a day, and the first read
  stores a year.
- **Verified 2026-10-05:** 43,657,647 ETH (35.76% of 122,080,989). Published reports for early October
  2026 say ~43.5–43.6M (cryptoticker, validatorqueue.com). Each row's staked / supply must match its own
  `staked_percent`, or the read is refused.
- **Effect:**
  - The staking-yield denominator was 2.03× too high, so the yield would have read about half its value
    (not 2.5×, because the real stake is 43.7M, not 36M).
  - The curve 166.32·√staked is now **~3,011 ETH/day**, not 4,285.
- **Cleanup:** SQL **CB** removes the old deposit-contract rows (`python run_sql.py CB`, then
  `--delete CB`).
- **Issuance is not above the maximum.** ~2,975–2,983 ETH/day is ~99% of the 3,011 maximum at 43.7M
  staked. The 2,734 figure only applies at 36M.
  - `EthSupply` is genesis plus proof-of-work issuance and has not moved since the Merge, so
    d(EthSupply + Eth2Staking) = d(Eth2Staking).
  - `Eth2Staking` is cumulative consensus-layer issuance (net of penalties is likely but undocumented).
    It includes nothing from the execution layer: no MEV, no fees, no deposits.
- **The scatter is snapshot timing.** Eth2Staking moves in **daily steps** of ~2,975–2,983 ETH, so one
  snapshot-to-snapshot delta holds 0, 1 or 2 steps:
  - 11,932.6 over 98.2 h is 4 steps.
  - 2,973.8 over 20 h and 2,980.8 over 21.6 h are 1 step each.
  - Scaling by hours turns a 1-step window into 3,570/day.
  - **CB3** lists each reading in steps. Q0 sums still telescope correctly; only per-day rates over short
    windows mislead.

**2. Sky: nothing is minted, and the burn is real.**
- **The −2.1e-06 reference is d(totalSupply) + the Stage 2 burn.** So d(supply) = −2,860,943.76 to float
  precision: **the burn did lower totalSupply, and nothing was minted.**
- **Code behind it:**
  - In Sky.sol, `transfer` to address(0) reverts (L96–97), and `burn` lowers totalSupply (L156–176). Any
    `Transfer(x, 0x0)` is therefore a real burn, never an unrecoverable transfer.
  - The Stage 2 burn is `Sky.burn(pauseProxy, 2,860,943.76)` in the 2026-09-10 spell (executed
    2026-09-13).
- **Where the rewards come from:** the LSSKY→SKY farm is funded through **MCD_VEST_SKY_TREASURY
  0x67eaDb32…**, a DssVestTransferrable that runs `transferFrom(czar = Pause Proxy, …)`. That is SKY the
  treasury already holds, bought by the flapper (its receiver is the Pause Proxy).
  - Source: spells-mainnet 2025-10-30 L163–200 @c1ce7e14; dss-vest DssVest.sol L475–498.
  - The mintable vest stopped in mid-2025, and the 2025-06-26 spell burned 426.29M SKY to offset what it
    had minted.
- **Reclassified:**
  - The schedule is **emissions_only**: rewards released from existing supply, which dilute free float
    but are not inflation.
  - `gross_issuance_tokens` is a sourced declared zero (A4 renders burn-only, and a measured positive
    blocks that).
  - The Credibility issuance row compares the zero with d(supply) + burn. A delta now drops float noise
    below 0.01 token, so −2.1e-06 reads 0.
- **The stream was stale.** The same spell replaced 96,903,706 / 90 days with **143,208,393 SKY / 90
  days** (vestBgn = execution, 2026-09-13 to 2026-12-11; L159–168 @8a4c4b23).
- **Cleanup:** SQL **CA** removes the schedule's old `gross_issuance_tokens` rows, which Q0 still sums
  until they are deleted.

**3. Chainlink buyback USD: 2 of 431 valued.** The write-time valuation read only this run's prices,
which is a day or two of them since the incremental fetch. It now reads stored prices plus this run's,
with this run winning. The same fix applies to the chain-burn derivation. The full-history valuation
compares sources by measuring point and now says why whenever it stands down (it was silent before).
Only days before the price history should stay unvalued.

**4. Ether.fi.**
- The Stake-child read now ends its run-log line with
  `[REPORT ether.fi Stake since 2026-09-03: $… over N non-zero day(s) … ; DefiLlama says: …]`, quoting
  DefiLlama's own methodology text for where the buying happens.
- The 600K Safe 0x01e42ad3… has no public label. `python check_offline_items.py etherfi_safe_owners` reads
  its `getOwners()` and the buyback Safe's on chain and prints any owners they share.

**5. Chainlink seed, Polygon.**
- Polygon now routes to Etherscan only. Blockscout answers 402 "Featured chain 137 requires
  Builder/Business/Pro".
- "Server too busy" is retried with backoff (2/4/8/16 s) before the call is refused. It used to fall
  straight through to the paid Blockscout fallback.
- Base stays the PARTIAL gap (Blockscout paid plan).

**6. Hyperliquid emissions: 190,482 vs active 162,861 vs full 245,143.**
- Today's snapshot has **33.6% of stake inactive**: 162,861 / 245,143 = 66.4% active.
- Ours is 77.7% of the full curve, implying a time-averaged active share of ~78%. That fits a rise in
  inactive stake during Q0.
- The active-curve row now averages the curve over each stored day of the split instead of using today's
  snapshot, and reports the min / mean / latest inactive share. Where the split covers only part of Q0, it
  says so.
- **Commission explains nothing:** our emissions are the fall in futureEmissions, which pays validators'
  commission and delegators alike.
- **Epoch timing is unlikely to matter:** rewards are distributed daily and Q0 is covered-days based.

## 11y. Credibility results, 2026-10-05 17:00: Chainlink seed, seven disagreements, checker fixes

**A. Chainlink seed (Base/OP 429, 0 rows).**
- Set `BLOCKSCOUT_API_KEY` in `.env`. With a key, every chain is read from the PRO API,
  `https://api.blockscout.com/{chain_id}/api` (one key, every chain; the per-instance hosts are
  deprecated). The key is sent as `apikey` and scrubbed from every error and log line.
- Blockscout sends **no Retry-After**. Its 429 carries `x-ratelimit-reset` in **milliseconds**, and
  Http now waits that long, never less than its 2 s backoff floor. A 429 that survives the retries
  halves that host's pace for the rest of the run, for every adapter that shares the host
  (4 → 2 → 1 req/s …). The log says so ("pace for its host now … req/s").
- Free PRO tier: 5 req/s and 100K credits/day. getLogs costs 20 credits, so about 5,000 calls/day.
  **Base (8453) is paid-plan only** in Blockscout's docs. Optimism is on the free tier.
- **Base/OP share of the PLUS lines: not measured.** DefiLlama and Blockscout are both blocked from
  the build sandbox, and the store holds no Base/OP rows yet. To get the number, open
  defillama.com/fees for Chainlink VRF, CCIP, Automation and Requests, read the per-chain split for
  the last 30 days, and add up Base + OP against the total. Once OP is read on the free key, the
  seed's own run log gives OP's share directly.
- **Recommendation (implemented): store the completed lines now, marked PARTIAL.** Each fee line
  that fails only on `optional_chains` (base, optimism) is stored with
  `:PARTIAL[base, optimism not covered]` instead of nothing. `customer_revenue_usd` carries the
  marker on every day that has a short leg, and `seed_complete()` records `partial_chains`. A
  failure on any other chain still stores nothing for that line. If the defillama.com split shows
  Base + OP above ~10% of the PLUS lines, Base needs the paid Blockscout plan; Etherscan's free tier
  does not cover Base either.

**B. The seven disagreements.**
1. *Ether.fi yield 0 vs DefiLlama 14.03%.* DefiLlama split its ether.fi adapter on 2026-08-04
   (dimension-adapters PR #8586, commit 11744feb). Holders revenue now lives only in the **Stake**
   child, so the main slug reads ~0. It is now summed from the children whose listing name contains
   "stake" (`include_names`) and never also read from the main slug. DefiLlama's holders revenue is
   *every aggregator trade by taker 0x2f53…* (any token) plus 10 off-chain USDC buybacks
   (2024-07-31..2025-04-30, ~$1.31M). That is buy pressure, not payments to stakers. Our count is
   CoW settlements only. No address for a new programme was found in the adapter. The SILENT flag
   may be wrong: each log scan's run-log line now ends with "Not-counted inflow AFTER the last
   counted transfer: …", by sender with its last date. If a sender shows up there after
   2026-04-01, purchases moved route rather than stopped.
2. *Aerodrome implied buyback 18.25%.* `share_to_buyback` 1.0 is the share *distributed to voters
   in the pairs' own tokens*, not AERO bought. A3's implied $ / tokens / % are now a structural
   `=0`, with the reason "no buyback — fees to voters" in the number format.
3. *Aethir circulating 24.05bn vs CoinGecko 20.13bn.* The dashboard figure follows Aethir's own
   vesting schedule (20.13B May, 21.01B Jun, 21.78B Jul 2026; dashboard 23.31B → 24.05B on
   2026-10-01). CoinGecko's 20.13B is the May step, i.e. stale. The first-party figure belongs.
   Free float (circulating − 1.79B locked) is 22.26B first-party vs 18.34B CoinGecko. Market cap
   is 1.195x higher on the first-party count.
4. *Maple Q0 $2.84M vs DefiLlama $3.63M.* Q0 on the page held only Jul + Aug (1.367 + 1.471)
   against about three months of DefiLlama, so the opposite sign is an artefact. The rows now
   compare the same complete calendar months (`months_match`). Month by month the page is still
   1.17-1.34x higher, because DefiLlama's holders share is 0.2/0.25/0.1 of gross and its OTC Dune
   dataset has been stale since 2025-10-09. The row stays a forced CHECK.
5. *Fluid price $2.16 vs $1.771.* The reference was a **guessed** Coinbase FLUID-USD: a ticker
   that can belong to another asset. Coinbase is now read only for declared products (ETH, LINK,
   NEAR, UNI, AERO). Every project gets a second price from DefiLlama's coins API for its
   **verified** token address. For Fluid that is ethereum:0x6f40d4a6…, so the next run shows which
   price is right. A native coin keyed `coingecko:` is FRESH-only.
6. *ETH issuance 17,890 vs formula 21,430.* Same covered days (~8). The shortfall is ~18%/day.
   Likeliest cause: `beacon_chain_eth` overstates the stake the curve pays on (queued deposits,
   balances above the effective cap). Snapshot timing is the other candidate. SQL **BZ** (SELECT
   only) gives issuance per 24 h of snapshot time and the curve on `beacon_chain_eth`. Run
   `python run_sql.py BZ`; run_sql registers `sqrt` for SQLite builds without it.
7. *HL emissions −24% vs formula.* A new row computes the curve paid on **active** stake only
   (`hl_reward_active`: 2.37% x sqrt(400M/S_total) x S_active). If it passes, inactive stake is the
   whole gap, and the note gives the inactive share.

**C. Checker fixes.** Price rows compare the same 00:00 UTC instant on the latest completed day
both series hold; today's fetch-time point is never compared. The "Not listed on Coinbase" note no longer appears
beside a value, because a Coinbase row now exists only for a declared product. GEODNET's gross burn is compared over the same complete
calendar months as the monthly series (as SQL BX2). Pendle's `lastEpochApr` 0 means "not
published" (`zero_is_missing`). Sky issuance, emissions and net supply read SKY `totalSupply` under
`total_supply_protocol` (archive backfill on Ethereum). **ETH staking yield** is BLOCKED by
unequal coverage until each leg holds 7 days (`RATE_MIN_DAYS`). The consensus leg starts
2026-09-30, so it clears on the 2026-10-06/07 run if the MEV leg also has 7 days by then.

**D. `python credibility_report.py` needs no LibreOffice.** It evaluates the workbook's formulas in
Python (`xlcalc.py`: INDEX, MATCH(…,0), IF, IFERROR, ISNUMBER, AND, OR, NOT, LEFT, ABS, ROUND, SQRT,
NA, COUNTIF(S), COUNTA, TEXT "#,##0"). It agreed with LibreOffice on every one of 3,963 formulas,
on both an empty store and a 1.26M-row synthetic one. A function it does not know is named, never
guessed. `--libreoffice` recalculates with LibreOffice instead.

**8. Maple SSF vs DAO multisig (~3.4x)** is not a data error: the SSF is a different wallet and
its address is unpublished. The row reads "UNVERIFIABLE (awaiting the SSF address)".

**9. GEODNET fee split.** The current first-party statement is still **80%**: GEODNET's own X
account, June-2026 burn stats, posted 2026-07-02 (x.com/GEODNET/status/2072713418818068898). No GIP
changes it. It was read from a search-index copy (x.com and geodnet.com are blocked from the
sandbox), so **Jake to open the link once to confirm the wording**. Recorded under
`revenue_split_reconciliation.current_split`. 0.80 stays in use. DefiLlama's burn/0.8 agrees with
it, though DefiLlama gives no source of its own. The 0.8706 is burn / reported ARR, a question of
base (data revenue vs ARR), not a newer split, and it stays OPEN.

## 11x. The Credibility tab (2026-10-05): every headline cell beside an independent reference

```bash
python token_metrics.py                 # the xref tier reads Coinbase daily candles + Lido's APR once a day
python credibility_report.py            # the tab, recalculated and printed: table, counts, every CHECK/UNVERIFIABLE
python credibility_report.py --open-only
```
- **What the tab covers.** One row per headline cell on A1-A4 for the 15 projects in
  `config.CREDIBILITY_PROJECTS` (World Mobile parked). Each project also gets input rows: price,
  circulating, and whichever of revenue, buyback, locked, fees and issuance its headlines use.
- **Our value is the headline cell itself**, as a formula, so a row can never disagree with the
  tab it checks.
- **Verdicts:**
  - **PASS**: an independent source agrees within the stated tolerance.
  - **CHECK**: the source disagrees, or a reference exists but was not read. The note names the
    reference and how to read it.
  - **FRESH-only**: a re-read of our own source. Never a PASS.
  - **UNVERIFIABLE**: no independent source exists, with the reason.
  - **N/A**: no figure here by design. If the cell shows a number anyway, it turns into CHECK.
  - **"(inputs)"**: a derived ratio such as burn yield or crossover is judged by its input rows.
    It is PASS only when every input passes. An input with no row (unchecked) keeps it from
    ever passing.
- **Where things are configured.**
  - References: `config.CREDIBILITY`. Kinds: another stored series; the same day as our
    figure; a computed formula; a manual reading with its date and reader; or a static verdict
    with its resolution.
  - Rows: `credibility.py`. Sheet: `build_workbook.write_credibility`.
  - New fetches: `fetch/xref.py`, which stores only reference metrics. These never feed a
    headline and are never a gap.
- **To add a by-hand reading**, put a `{"manual": {...}}` reference on the row in
  `config.CREDIBILITY`. Include value, read_on, read_by and source.
- **Found by this pass:** Ethereum's A4 "GROSS ISSUANCE Q0" showed "none available".
  ultrasound.money's issuance route was closed, and the build treated that as the figure being
  closed. The record is now `route_only`, and the cell shows the Etherscan figure.

## 11w. Jake's run 2026-10-05 14:35: Chainlink fee lines become a seed; spot checks

```bash
python token_metrics.py --seed chainlink_fees     # a year of fee-line logs, no time budget, resumable
python check_offline_items.py spot_checks         # our figure vs an independent live reference, per item
```
- **Chainlink fee lines.**
  - What the seed reads, on each of the 5 chains:
    - the Router's OnRampSet history;
    - every OnRamp's CCIP 1.2/1.5 and 2.0 events;
    - the VRF v2.5 coordinator;
    - the Automation v2.3 registry.
  - It reads in 120s passes and saves `.cache/logscan/chainlink-fees.json` after each pass. A
    PROGRESS line per pass shows the share of blocks covered and the time remaining.
  - Interrupt it at any time; the next `--seed` resumes from the saved position.
  - Until the seed finishes, routine runs skip `chainlink_fees` with "seed not complete — run
    --seed chainlink_fees". After it, each routine run reads a day or two of logs per stream.
- **Explorer pacing.** Every explorer call in a run waits on ONE pacer per host, at 4 requests/s:
  - for Etherscan, that is one pacer for the key across every chain id;
  - for Blockscout, one pacer per chain host, e.g. base.blockscout.com.
  This fixes the 429s that came from several adapters reading the same host.
- **Spot checks.** Five items: Hyperliquid market cap, Aethir ARR and total staked, the Uniswap
  burn, Pendle staking, and Sky buybacks. For each, the check prints:
  - our figure, taken from the store through the workbook's own views;
  - a reference read live, after robots.txt is checked;
  - the % gap and a verdict against `SPOT_TOLERANCE_PCT`.

  What each verdict means:
  - **PASS** needs an independent reference within tolerance.
  - **FRESH** means a re-read of the same source as our figure, so it only shows the store is
    current. Examples are Aethir's dashboard and Hyperliquid's circulating supply.
  - **MANUAL** means no reference could be fetched; the line names the page and tile to read by
    hand. MANUAL is never PASS.

  Pages that need rendering (ASXN, financial.skyeco.com) are loaded once each in the browser.
  ASXN's figures are for internal checking only. Nothing is stored.

## 11v. Jake's run 2026-10-05 09:19: network blip, cache race, Aethir, Plume issuance

```bash
python run_sql.py BR              # Plume gross_issuance_tokens: both derivations, spans (SELECT first)
python run_sql.py --delete BR     # then: remove the superseded d(CoinGecko supply) rows
```
- **robots.txt retries.** Network and SSL errors on robots.txt are retried with backoff (2s,
  4s) before the host is concluded UNREACHABLE. After every tier, each still-unreachable host is
  re-checked once. If it answers, the sources it refused are re-run for those projects only
  ("ROBOTS RE-CHECK" in the log).
- **Per-call times.** A source's HTTP summary (including a TIMED OUT line) now gives p50 / p90 /
  max per call, the count of calls over 10s, and the slowest paths.
- **Cache writes.** Every cache file is written under a per-file lock, through its own temp
  file. The rename retries a Windows sharing violation (WinError 32). This fixes
  blockscout_stats' daily-checks crash.
- **Aethir pages.**
  - A page served without its data (a challenge, loading shell or error page) now fails once,
    saying what came back, and is NOT marked read, so the rerun tries it again.
  - A missing pinned key says whether it was renamed or served as a string.
  - A by-value match that fails names the nearest current figures.
- **Plume gross_issuance_tokens.** The BUG is two derivations in one series:
  d(CoinGecko total_supply) (`derived:d_supply`, retired 2026-10-01) and d(ERC-20 totalSupply)
  (`derived:d_total_supply_protocol`). They overlap, so this is a cleanup, not a handover (BR).

## 11u. probes9, 2026-10-02: Aethir durations unavailable, HyperEVM tx_count, revenue cross-check

- **Aethir stake duration is UNAVAILABLE (config UNAVAILABLE).** The tile "read" 2,024, which
  is the year on the chart's axis. Aethir's browser route is removed.
- **Tile guard (all sites).** A tile value is refused if it:
  - is a bare year;
  - is followed by a month name;
  - or sits among evenly spaced axis labels.
- **HyperEVM.**
  - `tx_count` = `transaction_count` (HyperEVM only), still checked against "Avg Daily Txns
    334K".
  - `tx_count_successful` = `successful_transactions`.
  - ASXN's HyperEVM series run ~10 days behind. Their staleness threshold is widened by 10
    days, and a lag past 15 days is flagged "LAG GREW" (the data is still stored).
- **Revenue cross-check.** ASXN `annualized_revenue_30d` ($724,957,544 on 2026-10-02) is
  compared each run with our DefiLlama revenue / fees / holders revenue, each annualised over
  its last 30 covered days. These are the CROSS-CHECK lines in the Run Log.

## 11t. probes8, 2026-10-02: ASXN units concluded, HyperEVM page, Aethir hours and utilisation

```bash
python check_offline_items.py browser_captures   # /hyperevm/fees pins, stake-duration toggles (dry)
python run_sql.py BQ                              # Aethir utilisation_pct rows from containers x 168h (SELECT first)
```
- **ASXN.**
  - All three buyback-page legs are USD: `total` = the sum of the legs on 561/561 days, and
    HyperCore Buybacks is USD. They are stored as `*_usd`; total burn's history converts them at
    the same-day price.
  - The HyperEVM pins (tx_count, burn, fees) now sit on their own page entry,
    `/hyperevm/fees`.
  - Validated: ASXN's perps volume agrees with ours (median ratio 0.997).
- **Aethir.**
  - Weekly compute hours come from the demand page's server payload. The key is pinned by the
    prefix `weeklyComputeHo*`, and the Run Log prints the full key.
  - Utilisation = delivered / online hours. The weekly ratio is primary; the cumulative ratio
    (~69%) sits beside it; containers x 168h is the lower bound.
  - Stake duration is read from the "Average Stake Duration (Days)" tile after the AI and Gaming
    toggles. If that fails, it is UNAVAILABLE.

## 11s. Plume native-PLUME seed: "day(s) held" stuck at 183 (2026-10-02)

- **What happened.** The native leg walks newest first from yesterday. Day 183 back is
  2026-04-01. Every day read before that (2026-03-31 back to 2026-02-12 when this was noticed)
  had more than 400 pages, i.e. more than 20,000 native transfers: Points Season 2 farming.
  Each one was refused by `native_max_pages_per_day`. The refusals went to the end-of-run notes
  only, so the progress line never showed them.
- **What changed.**
  - A refused day is remembered (state `native.capped`), so no run reads it again unless the
    cap is raised.
  - The progress line now prints "N refused over the 400-page cap".
  - `native_from: "2026-04-01"` skips the farmed period. Those days only feed the
    incentive-inflated full-year NRR, which needs all 365 days, so that figure reads NOT
    AVAILABLE and says why. To build it, set `native_from` to None and raise the cap.
- **A seed already running on older code** stores its 183 organic days only at its end. If it
  is stopped, rerun `python token_metrics.py --seed plume_settlement` on this code: the held days
  are kept and the farmed days are not read.

## 11r. probes7, 2026-10-02: Kai-Ching, bloXroute, ASXN units, Aethir keys

```bash
python check_offline_items.py browser_captures      # dry run of every pin: unit checks, CROSS-CHECKs, RSC key paths
python run_sql.py BP                                 # any ASXN legs 3b01a2a stored as HYPE from USD (SELECT first)
```
- **NEAR.**
  - The drop is Kai-Ching: its signers account for ~98.8% of it (METHODOLOGY_FLAGS
    near_activity_cause).
  - `tx_count_ex_kaiching` is new, built from BigQuery with signers `*.kaiching` excluded; the
    excluded part is stored as `tx_count_kaiching`. The first run reads yesterday, then the
    rest of the year in one backfill query (~25 GB expected; the dry run decides). After that,
    a daily top-up.
  - Raw `tx_count` / `active_addresses` cells are caveated: before April 2026 they are ~70% one
    app's payouts.
- **MEV.** bloXroute Max Profit is dropped: its DNS fails and no replacement host is published
  anywhere checked. Its past blocks now count as non-relay.
- **ASXN.**
  - Every buyback-page leg's units are checked:
    - HyperCore Buybacks against the stored AF and holders-revenue figures (it is USD);
    - HyperEVM Burn against ASXN's own burned_hype / burned_usd;
    - Auction Burn by `total` = the sum of the legs.
  - Each leg is stored as read, under the name its verdict gives (`*_usd` or `*_tokens`).
    Total burn's history converts USD legs at the same-day price.
  - Also new: the 30-day annualised revenue (`annualized_revenue_30d`); HyperEVM daily
    transactions as `tx_count` (HyperEVM only), checked against the "Avg Daily Txns" tile;
    HyperEVM burn and fees as cross-checks.
  - HyperCore users (cumulative) are stored daily, with the daily change as `hypercore_new_users`.
    Daily active addresses are UNAVAILABLE from ASXN.
- **Aethir.**
  - Weekly compute hours and the AI/Gaming stake durations are read by key from the
    server-component payload.
  - Weekly compute hours must match Jake's 22,510,837 within 5%.
  - The tiles are only a fallback. When keys don't resolve, the probe prints the RSC lines and
    key paths to pin.

## 11q. probes6 follow-up, 2026-10-02: MEV seed, ASXN, Aethir tiles, NEAR re-login

```bash
python check_offline_items.py mev_relays                    # each relay + what a 90- and 365-day seed costs
python token_metrics.py --seed mev_relays --seed-days 90    # 90 days first; a later full seed resumes
python check_offline_items.py browser_captures              # ASXN pins dry-run + unit check; Aethir tiles; HyperEVM pages
python check_offline_items.py near_activity_signers         # NEAR drop, 2nd pass: signers, account types (~2 GB)
```
- **Plume NRR.** The primary figure is the organic window from 2026-04-01 onward, annualised
  over its covered days. The full-year, incentive-inflated figure (it includes the Points
  Season 2 farming) sits beside it as `network_reserve_ratio_incl_incentive`.
- **Aethir emissions.**
  - `emissions_tokens` is what was RELEASED: checker + edge + compute rewards net of locked,
    i.e. the rise of totalRewards − totalLockedRewards. It feeds the supply trajectory and
    free-float dilution.
  - `emissions_earned_tokens` is what was EARNED, a commitment (accrued, still vesting).
  - Checker and edge carry no locked split, so they count as released.
- **MEV.**
  - bloXroute pages at most 100 rows. Its HTTP 400 was limit=200; relayscan's
    data-api-backfill.go also caps bloXroute at 100.
  - The max-profit host is right: it is the same host as in relayscan's mainnet config, so its
    ConnectionError was network-side.
  - A relay that fails no longer stops the day. The day is kept PARTIAL with the relay named,
    and it is re-read on up to 3 later runs.
  - The non-relay leg defaults to an ESTIMATE: (DefiLlama fees − burn) ÷ price × the non-relay
    share of slots. It is computed when the workbook is built, adds no extra calls, and its
    cells are caveated as ESTIMATE.
  - Per-block reads are off (`per_block_days: 0`). At 78% relay coverage they cost about 3,200
    RPC calls a day. Set it to 1 for the forward daily top-up.
- **ASXN.**
  - Access is rendered page loads only, once a day. The API is session-gated: never call it
    directly, never reproduce the check, and ask ASXN before any public use (SOURCE_REGISTER).
  - Each run decides whether the buyback legs are HYPE or USD by comparing them with the stored
    Assistance Fund and holders-revenue figures. If neither matches, or both do, nothing is stored.
  - Auction Burn and HyperEVM Burn become total burn's history before the Core leg's own read
    begins.
  - Volume, buybacks and the non-AF burns are logged as `CROSS-CHECK` lines against the stored
    series.
- **Aethir.**
  - Text bodies are captured now, including text/x-component (React Server Components).
  - The AI and Gaming stake durations are read from the rendered tiles, forward-only.
  - Last week's compute hours already come from the page payload.
- **NEAR.**
  - When the login has expired ("Reauthentication is needed"), every NEAR BigQuery read stops.
    The end of the run prints `ACTION NEEDED — run gcloud auth application-default login`, and
    the count of days it has happened is kept.
  - If ADC has no quota project, the run reminds you to run
    `gcloud auth application-default set-quota-project near-data-510309`.

## 11p. Research round, 2026-10-02: MEV, browser charts, NEAR's drop

```bash
python check_offline_items.py mev_relays          # each relay: status, limit headers, share of recent slots
python token_metrics.py --seed mev_relays         # a year of relay deliveries (+ ETHEREUM_RPC_URL for non-relay blocks)
python check_offline_items.py browser_captures    # ASXN + Aethir charts: robots, terms excerpt, every captured response
python check_offline_items.py near_activity_cause # NEAR's top receivers, Mar 9-15 vs Apr 6-12 (~16 GB, on the ledger)
```
- **Ethereum yield, MEV included.** Execution = relay-delivered value + priority fees of non-relay
  blocks, in ETH. Without `ETHEREUM_RPC_URL` the relay leg is stored and the execution figure is
  PARTIAL. The relays covered are listed in config `Ethereum.mev_relays`; the stored share of blocks
  they delivered shows how much the covered relays account for.
- **Browser charts.** Nothing renders until a page is `permitted: True` (ASXN waits on its robots.txt
  and terms), and nothing is stored until its series or tiles are pinned from the probe's output.
- **Aethir compute rewards** are the third supplier component of `emissions_tokens`, alongside
  checker and edge rewards. `totalLockedRewards` is stored separately, because whether it's vesting
  isn't known yet.

## 11o. history_audit.py reads what completeness_report.py reads (2026-10-01)

`python history_audit.py --short-only` lists only REAL gaps. It works on the series the workbook
reads, read-time views included, and uses completeness_report.py's own classifications:
- **FULL AS READ**: short in the store, full as read. Example: Hyperliquid's buyback and burn
  before the live read come from DefiLlama holders revenue divided by the same-day price. That
  history is prepended when the workbook is built and never stored.
- **DECIDED**: the completeness report calls it COMPLETE, N/A, ACCEPTED LIMIT or WAITING ON A
  DATE. That covers declared zeros, superseded releases, mechanism starts and halted programmes.
- **BY DESIGN**: `total_supply` is CoinGecko's figure, and only today's value is read. Chain
  history, where it's wanted, is `total_supply_gross`.

`--no-classify` skips the workbook build and is fast, but decided items then show as short.

Archive passes run per project. Aethir's `locked_tokens_wrapper` (the Ethereum wrapper's ATH) is
archivable:
```bash
python archive_backfill.py --run --project Aethir
```

## 11n. NEAR from BigQuery — Jake's own login (Application Default Credentials)

Google's public NEAR dataset (`bigquery-public-data.crypto_near_mainnet_us`) is live. Queries run in
Jake's sandbox project **near-data-510309**, which has no billing: it cannot be charged, and BigQuery
refuses queries once the free 1 TB/month is used. fetch/near_bigquery.py keeps its own ledger under
900 GB/month and logs a `QUOTA` line on every run.

**The month's top-ups are reserved before backfill spends (Jake, 2026-10-01).** The daily top-up
comes out of the same 900 GB, so backfill may spend only `900 − used − reserve`, where reserve =
days left in the month × the latest measured one-day top-up dry run × 1.2 (plus the 30-day token
census, ×1.2, if its refresh falls due before the month ends). The one-day size is measured by the
`near_settlement_routes` probe (saved for the adapter) and refreshed by every day's own dry run;
until one exists, backfill is held. The `QUOTA` line prints the bytes billed this run, the backfill
spend, the month used, the remaining quota, the reserve with its arithmetic, and what is left for
backfill. Circulating supply and the P2P leg are both
approved (Jake, 2026-10-01): each run tops up the newest days first, then backfills one month chunk,
newest first.

**Why a login, not a key.** The service-account key route is BLOCKED: Jake's Google organisation
enforces `iam.disableServiceAccountKeyCreation` (secure by default — leave it on). So the adapter uses
Application Default Credentials from Jake's own login, with no key file anywhere.

**Set up once, on the machine that runs token_metrics.py:**
```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project near-data-510309
pip install google-cloud-bigquery
```
Optional in `.env`: `NEAR_BQ_PROJECT=near-data-510309` (the adapter defaults to it and refuses any
other value).

**THE GUARD, which matters more with a login than with a key:** these credentials carry Jake's user
access, which also reaches cbc-risk-regime-api. So the adapter:
- creates every job in near-data-510309 and refuses to start if the ADC quota project, the client's
  project or NEAR_BQ_PROJECT is anything else;
- refuses any query that names a table outside `bigquery-public-data.crypto_near_mainnet_us`, before
  it is sent;
- caps every job at its own dry run (maximum_bytes_billed), and stops at the monthly budget.

Without credentials the run does not fail: NEAR's BigQuery figures gap with "run `gcloud auth
application-default login`", and the CSV fallback below still works.

**The March–April drop is REAL (Jake's probes5, 2026-10-01).** Average daily transactions for
Apr 2–30 compared with Mar 1–23 are ×0.26 in BigQuery *and* ×0.26 in NearBlocks. Two independent
indexers agree, so NEAR activity genuinely fell about 74%. The series break is RESOLVED, nothing
is marked SUSPECT, and there is no backfill floor any more. The full year is valid, and the 365-day
NRR needs it. Backfill runs newest first within the budget left after the top-up reserve (about
3 months for the 1.91 TB year). `check_offline_items.py near_activity_break` can still be re-run;
the verdict typed in config (REAL) takes precedence over the file.

**Then:**
```bash
python check_offline_items.py near_settlement_routes   # layout + dry-run bytes (~30 MB; dry runs free)
python token_metrics.py --seed near_bigquery           # top-up, then month chunks until only the top-up reserve is left
python token_metrics.py                                # routine: circulating (10 MB) + top-up + one chunk
```

**Fallback without credentials:** `check_offline_items.py near_settlement_routes` writes each query
with literal dates to `data/near/console/`. Paste one into the BigQuery console (near-data-510309): the
editor shows the bytes before running. Run the P2P query for a month and use Save results → CSV; save
it as `data/near/p2p_<YYYY-MM>.csv` (columns day, token, amount, n). The next run reads it.
`data/near/` is never committed. (The sandbox has no scheduled queries, so this step is by hand.)

## 11l. One-off: Plume's staking history from the live diamond

`locked_tokens`, the gross and net staking APR and the commission are read from Plume's live staking
diamond (0x30c791E4…). Its past days are read the same way, at the first block of each day:

```bash
python check_offline_items.py plume_archive      # does rpc.plume.org serve past state? (1..365 days back)
python token_metrics.py --seed plume_staking     # a year of history, newest first; held days skipped
```

The seed **stops** at the first day the RPC refuses state and says so: days before that are
forward-only on this RPC. A block where the diamond has no code yet is its deployment, recorded as
the series' start.

## 11c. Checking numbers without Excel

Every derived column in the workbook is a formula with no cached result — Excel computes on open,
and nothing else can read it. `headline.py` prints the same aggregation straight from the store:

```bash
python headline.py                        the headline figures, with confidence bands
python headline.py --project Uniswap      one project in full
python headline.py --band GREEN           only what is safe to act on today
```

It writes nothing, needs no LibreOffice, and computes the windows exactly as the workbook does.

---

## 11b. Triaging a run — what changed, and was it supposed to?

A summary line ("41 review items") is a number without a cause, and after a round that adds new
checks the only question that matters is which half of the jump was the new checks firing as
designed and which half is something that went wrong. `run_triage.py` reads that out of the
store. It writes nothing.

```bash
python run_triage.py --compare-previous      # start here: the delta, split into expected vs not
python run_triage.py                         # this run in full: review by reason, failures, skips, gaps
python run_triage.py --metric Uniswap/burn_address_balance
                                             # one figure: what was stored, flagged, logged and gapped
python run_triage.py --runs                  # recent run ids
```

`--compare-previous` splits every changed reason code into **EXPECTED** (a check introduced by a
recent round, with the commit that added it) and **NOT EXPLAINED BY THIS ROUND'S NEW CHECKS**.
The second list is the one to read.

---

## 11a. Working on ONE Dune query, without a full run

Mapping a Dune query is iterative — look at the columns, try a mapping, look at the series it
produces, adjust. A full run is the wrong unit of work for that, so `dune_probe.py` does it in
one round trip. **It never writes to `metrics.db` and never rebuilds the workbook.** The only
thing it writes is a shape file under `.cache/dune/query-<id>.json`, which is plain JSON you can
open, grep or send on.

```bash
# what does this query actually return? columns, types and real sample rows
python dune_probe.py 8683038
python dune_probe.py Ether.fi/locked_tokens        # same thing, id resolved from config.py

# read a shape captured earlier — no network, no API key needed
python dune_probe.py --show 8683038

# try a mapping before committing it to config.py: prints the series it WOULD store
python dune_probe.py 8683175 --map month:tokens_burned,sol_tokens_burned --drop-current

# a 4xx? compare against a control id, rather than retrying variations
python dune_probe.py 2986047 --diagnose

# what did actually land in the store for a series, and how far back does it go?
python dune_probe.py --stored GEODNET/gross_burn_tokens --before 2026-08-01

# every Dune query in config and whether its columns are mapped yet
python dune_probe.py --config
```

`--stored` is the verification step after a backfill. It prints the rows held, the date range,
how many came from Dune, how many predate a cut-off you name, and whether the incomplete current
period was dropped — so "the run did not error" and "the history is actually there" stay
separate questions.

---

## 11i. sqlite3 stores a numpy integer as a BLOB, silently

Found 2026-09-22, one step from the end of a run: a clean fetch of 6,967 rows, then
`int(b'\x02\x00\x00\x00\x00\x00\x00\x00')` in `build_workbook.write_review_queue`, with the
workbook unwritten and the traceback pointing at the reader.

**The mechanism.** `numpy.float64` subclasses Python `float`, so it binds as REAL and nobody
notices. `numpy.int64` does **not** subclass `int` — it falls through sqlite3's type dispatch to
the buffer protocol, and eight little-endian bytes go into an INTEGER column without an error:

```
np.int64(2)   ->  typeof() = 'blob',  x'0200000000000000'
np.int32(2)   ->  blob, 4 bytes
np.bool_(True)->  blob, 1 byte
np.float64    ->  real      (safe)
np.str_       ->  text      (safe)
```

So the exposure is integer-like and boolean numpy scalars only, and it is invisible at the write.

**Where they come from.** A Series element off a mixed-dtype DataFrame — `g.iloc[-1]["tier"]` —
is a numpy scalar. `itertuples()` happens to hand back Python ints for the same column, which is
why moving one loop from `itertuples` to `groupby`/`.iloc` was enough to start writing blobs.

**The rule.** Every integer column is coerced at the store boundary by `store._int_or_none`,
which converts `None`, Python ints, numpy integers, bools and integral floats, and **raises** on
anything else — a str, a bytes, a list, a non-integral float are not "an int needing conversion",
they are a caller passing the wrong thing. Cast at the source too; the boundary is the net, not
the fix.

**Do not defend at read time.** Wrapping the `int()` in `build_workbook` would have hidden bad
data being written, which is the pattern this project has been burned by before. The test that
belongs here asserts `typeof()` in the database, not that the reader survived.

`orphan_cleanup.sql` section K diagnoses and repairs an affected store. Usually no repair is
needed: `review_queue` is rebuilt per run and read for the latest run only, so one clean run
leaves the poisoned rows behind as history nothing reads.

## 11h. Running the cleanup SQL: `run_sql.py`

`orphan_cleanup.sql` is written to be READ before it is run — every section looks first and
deletes second, and the DELETEs ship commented out. That discipline only works if looking is
easy, and it was not: the SELECTs had to be pasted into an inline `python -c` or a sqlite3 shell,
and on Windows PowerShell the quoting around SQL string literals broke repeatedly. "Read this
before you delete anything" became "fight the shell, then guess", and several sections went unrun
for weeks as a result.

```
python run_sql.py                 list the sections, with what each is about
python run_sql.py E               run section E's SELECTs and print the results
python run_sql.py --delete E      run its DELETE, after showing the rows and confirming
python run_sql.py E --db other.db run against a different store
```

**The two modes are separate on purpose.** The plain command refuses to execute anything that is
not a SELECT — *even if a DELETE has been uncommented in the file* — so a half-finished edit
cannot delete rows because somebody ran the "just look" command. Deleting takes the explicit
`--delete` flag, prints the rows first using the DELETE's own WHERE clause (so the preview cannot
drift from what actually goes), and requires you to type `DELETE <letter>` exactly.

`--delete` also covers section A, whose fix is an UPDATE rather than a DELETE — a re-attribution,
because that reading was correct and only the column was wrong. The same holds for every later
UPDATE (BK, BL): `--delete` reads the commented statement as the file ships it.

**Nothing is ever uncommented by hand (Jake, 2026-10-01).** Every section's DELETE or UPDATE ships
commented; `python run_sql.py --delete <letter>` is the only way it runs, with the preview and the
typed `DELETE <letter>`. A section's own text says which `--delete` to run.

**A section with no write is not a section with a missing one.** Section H is SELECT-only because
which rows to remove depends on what its queries show, and under one of the two readings the
answer is none. The tool says so rather than failing silently.

## 11g. A getter can stop meaning what its name says, without failing

Found 2026-09-21 on Aerodrome, and it is the most dangerous shape of wrong number this tool
produces: the read succeeded, the value was plausible, the magnitude was roughly right, and it
had not been the emission rate for the better part of two years.

`Minter.weekly()` is Aerodrome's per-epoch pool emission. `updatePeriod()` assigns it back only
on one of its two branches:

```solidity
bool _tail = _weekly < TAIL_START;
if (_tail) { _emission = (_totalSupply * tailEmissionRate) / MAX_BPS; }
else       { _emission = _weekly;
             _weekly = _weekly * (epochCount < 15 ? WEEKLY_GROWTH : WEEKLY_DECAY) / MAX_BPS;
             weekly  = _weekly; }          // <- the assignment lives HERE, and only here
```

So the first epoch below `TAIL_START` freezes `weekly` permanently while the real emission
becomes a share of supply. The getter keeps answering. It answers with the number the emission
used to be.

**How it was caught, and what to copy.** Not by the value looking wrong — it looked fine. By the
value sitting one token below a named constant. Replaying the contract's own constants
(10,000,000 start, x1.03 for epochs 1-14, x0.99 after) lands on 8,969,149.540108 at epoch 67,
against `TAIL_START` of 8,969,150 and a stored figure of 8,969,149. Decay steps are ~90,000 AERO
apart, so "just below the threshold" is not a coincidence that happens — it is the one outcome
the frozen branch guarantees.

**The rule.** Before a public getter becomes the source of a metric, read the function that
WRITES it, not just the one that reads it. Ask: is there a branch on which this variable stops
being assigned? If so, the read needs the same branch the contract takes, on the contract's own
threshold, and the branch it took belongs in the source string so a change of basis mid-series
is visible rather than a mystery step change.

**And never fall back.** When the tail legs cannot be read, the adapter stores nothing. The
alternative is storing `weekly()`, which is a number of entirely plausible magnitude, years
stale, with nothing on the row to flag it. A blank gets asked about; a plausible wrong number
gets acted on.

**A second, separable fault in the same rows: cadence.** Both Aerodrome reads return one figure
per weekly epoch and were dated to the RUN, so each daily run laid down another row with the
same number and a trailing-30-day sum counted each as its own week — roughly 7x. A periodic read
is dated to its PERIOD (`granularity` on the contract; the adapter dates weekly reads to the
epoch start), so re-reads inside one period overwrite one key instead of accumulating. Check
both faults separately: re-dating a frozen constant still leaves a frozen constant.

## 11f. Characterise a provider's supply field before any derivation uses it

**The rule, in one line:** a formula must follow the convention of the *figure it is applied to*,
not the mechanism it is reasoning about.

Found on 2026-09-21, in the run audit. It had been producing wrong numbers quietly for weeks and
neither half of it looked like a mistake in isolation — which is what makes it worth a section.

The issuance derivation was keyed on the **burn mechanism**: a transfer burn moves tokens to a
dead address without reducing the contract's `totalSupply`, so issuance is just the supply delta.
That reasoning is correct *about the contract's figure*. But the stored `total_supply` comes from
**CoinGecko**, and CoinGecko's `total_supply` for these tokens is contract `totalSupply` **minus
the dead-address balance**:

| | contract totalSupply | CoinGecko total_supply | difference | burn_address_balance |
|---|---|---|---|---|
| GEODNET | 1,000,000,000.00 | 961,518,067.62 | 38,481,932.38 | **38,481,932.38** |
| Uniswap | 1,000,000,000 | 888,114,418.92 | 111,885,581 | 111,953,581 (read timing) |

So differencing it gives issuance **minus** burn, under a column labelled gross. For a
non-minting token that is approximately `-burn` — which is exactly how Uniswap came to report
−242,000 and trip `negative_derived_issuance`. The negative was not a data error; it was the
formula reading the right number under the wrong convention.

**What to do, every time a provider field feeds a derivation:**

1. Establish the convention *before* writing the formula, by arithmetic, not by reading docs:
   subtract the provider's figure from the on-chain one and see whether the difference equals a
   quantity you already hold (here, the burn balance).
2. Record it as a declared field with its evidence — `total_supply_convention` alongside the
   existing `circulating_supply_convention`. Both are `net_of_burn` | `gross`, both require
   `..._evidence` with a test and a date, and `config.validate_config` enforces that.
3. Where the convention is **untested**, refuse to derive and say which test to run. Do not pick
   the likely answer. The two formulas here differ by the *entire* burn, so a guess is not
   approximately right — it is wrong by 100% of the thing being measured. PancakeSwap and Venice
   AI sit in this state deliberately.
4. Note where the convention **cannot** matter and skip the ceremony: with `no_burn` there is
   nothing to net out, and with `protocol_level_destruction` the contract's own figure falls and
   there is no dead-address balance for a provider to subtract. Only `transfer_to_dead_address`
   distinguishes them — which is why `config.ISSUANCE_FROM_SUPPLY_DELTA_BY_MECHANISM` deliberately
   *omits* that key: a future edit that forgets the convention gets a refusal, not a default.

**The related trap, worth knowing about:** the gross on-chain figure *is* fetched (tier 2 reads
the contract) but never wins — `_resolve_tier_collisions` keeps the earlier tier, and CoinGecko is
tier 1. So the net-of-burn figure lands in the store and the gross one is dropped as a collision.
Deriving from the chain read instead would also be correct, but it means inverting that
preference, which the collision resolver treats as always-wrong. Declaring the convention is the
cheaper of the two fixes and the one taken.

## 11e. Do not infer set-overlap from value-proximity when a fixed total constrains both

**The trap, in one line:** two quantities that sum to a known total are near-equal exactly when
the split is near 50/50 — and that says *nothing* about whether the sets overlap.

This produced a nearly-wrong diagnosis on 2026-09-16 and is worth recognising on sight.

Aerodrome's `locked_tokens` exceeded `circulating_supply` by 0.106%. Two explanations were on the
table: circulating *includes* locked (so the overshoot is a real contradiction), or circulating
*excludes* locked (so they are disjoint and the comparison is meaningless). The argument made
against the second was:

> The two figures agree to one part in 940. If they measured disjoint populations there is no
> mechanism that would place them that close — a disjoint split can land anywhere.

**That is backwards.** The figures were:

```
locked      989,752,701
circulating 988,697,600
total     1,978,450,301      locked + circulating = total, to 0.00%
```

They are disjoint halves of a fixed total, and they are near-equal *because the lock rate is
50.03%*. A disjoint split cannot "land anywhere" once both parts must sum to a fixed total —
being close is exactly what a balanced partition looks like. The proximity was evidence *for* the
hypothesis it was used to reject.

The same reasoning also dismissed the implied 50.03% as a meaningless artifact ("any two
near-equal numbers give ~50% under that formula"). True, and irrelevant: that figure *was* the
answer — the actual lock rate.

**What to do instead.** Proximity between two quantities is not evidence about their relationship
in either direction. Find a third number whose predicted value *differs* between the hypotheses,
and read it:

| hypothesis | prediction |
|---|---|
| circulating EXCLUDES locked (disjoint) | `circulating + locked ≈ total_supply` |
| circulating INCLUDES locked (overlapping) | `circulating ≈ total_supply`, and the sum overshoots badly |

One test, two predictions that cannot both hold. `diagnose_lock_vs_float.py` does exactly this and
needs no network — the deciding number was already in the store, and nobody had looked at it.

**Generalise it.** Whenever reasoning about a two-part split — locked vs float, staked vs liquid,
burned vs outstanding, treasury vs public — do not reason from how close the parts are. Reason
from a constraint that the competing hypotheses disagree about. If no such constraint is
available, say the question is unresolved rather than settling it on a plausibility argument.

**And the consequence for checks.** A relation is only worth zero tolerance if it is actually an
identity. `locked_tokens <= circulating_supply` read like one and was not, because it silently
assumed a convention. Checked at zero tolerance it did not catch a subtle error — it manufactured
a violation every run on a project that was behaving normally, which is how a Review Queue gets
ignored. Rigour on the threshold is worth nothing without rigour on the premise.

## 11d. Read-time vs write-time: which config changes re-judge stored data

Audited 2026-09-15 after a stale figure survived a config correction. Worth knowing before
you change any rule, because it decides whether you also have to clear the store.

Two passes run over different data:

```
fetch_all()      -> validators see ONLY this run's fetched rows      (write-time)
upsert(store)
build_workbook() -> aggregate() + confidence_for() see THE WHOLE STORE  (read-time)
```

So the rule is mechanical: **anything evaluated in `build_workbook.py` re-judges everything
in the store on every build; anything in `fetch/` only affects the next write.**

**Read-time — change config and the sheet corrects itself on the next build:**
the orphan guard, measuring-point change, `non_comparable`, `destination_indeterminate`,
`destination_status: disputed`, cross-check "waiting", manual-quarterly staleness, unverified
addresses, `burn_mechanism` `assumed` / `confirmed` / `refuted`, `metric_labels`,
`supply_additive`, `threshold_gated`, `UNAVAILABLE`.

**Write-time — change config and stored rows keep their old judgement until you clear them:**
sanity bounds, change thresholds, `cross_checks` divergence, `reference_values`,
impossible-relations, the `:PARTIAL` marker (baked into the source string when the row is
written), every address gate in `chain._gate` (ambiguous, unverified, chain coverage, lock
read method), the `derive_flow_from_cumulative` measuring-point guard, and schedule `until`.

Tier 4 amplifies all of it: Dune series are skipped once backfilled, so they are never
re-fetched and their write-time checks never run again on that data.

If you change a write-time rule, add a SELECT to `orphan_cleanup.sql` for what it now judges
differently. There is a worked audit of all four categories at the end of that file.

### FUTURE CONSIDERATION, NOT BUILT — move the write-time set to read-time

Sanity bounds, cross-checks, reference values and the `:PARTIAL` marker could all be
evaluated at build time against the store instead of at fetch time against one run's rows.
Config changes would then self-correct stored data automatically and the manual-clear step
would mostly disappear.

This is a **genuine new mechanism, not a targeted fix**, and it is deliberately not built.
Recorded here so it is not lost. Two things to think about first: the `:PARTIAL` marker is
currently *data* (part of the source string) rather than a *rule*, so moving it means deciding
whether a historical row should be re-marked by a flag set after it was written; and
impossible-relations is a cross-metric check whose cost at read time scales with the whole
store rather than one run.

## 12. Troubleshooting

### `ModuleNotFoundError: No module named 'pandas'`
The virtual environment is not active in this terminal. Re-run the activate command from step 3.

### Every source fails with `ProxyError` or `Max retries exceeded`
No outbound network, or a proxy is intercepting. Confirm with
`curl -sS https://api.llama.fi/protocols | head -c 100`. Behind a corporate proxy, set
`HTTPS_PROXY` in your shell before running. This is the failure mode that looks alarming in the
log but has one cause.

### `all RPC endpoints failed for ethereum`
Public RPCs are rate-limited and go down. Set `RPC_ETHEREUM` in `.env` to a provider you control
(Alchemy and Infura both have free tiers). Comma-separate several for a fallback list. Everything
else in the run is unaffected — only that chain's reads are gapped.

### CoinGecko returns 429 repeatedly
You are on the public tier and hitting the rate limit. Get a free demo key and set
`COINGECKO_API_KEY`. The adapter already backs off and retries; a key raises the ceiling.

### `Playwright unavailable` or every scrape fails
Run `playwright install chromium` (step 5). If Chromium is installed but pages still fail, the
page shape changed: check the Gap Report for the specific reason per entry, then fix
`url_contains` and `json_path`, or `anchor`, in `sources.yaml`. Use browser DevTools → Network →
Fetch/XHR to find the endpoint the page calls.

### `symbol check FAILED — 0x... reports 'XYZ', config expects 'ABC'`
**This is the safety net working, not a bug.** An address is wrong or stale, and it was rejected
before a plausible-looking wrong number reached the sheet. Re-check that address against the
protocol's own documentation, then update `config.py`. Do not relax the check.

### A figure looks an order of magnitude wrong
Check the Data tab's Source column for that metric. A `:PARTIAL` suffix means the figure is a
sum over known components only. A `sum(...)` source names every component. If the source is a
lock-rate contract, confirm its `read_method` in `config.py`: reading an NFT-based escrow as an
ERC-20 supply returns a count of positions, which is wrong by orders of magnitude and looks
entirely plausible.

### `recalc.py` reports `errors_found`
A formula problem. Do not ship the workbook. Send me the `error_summary` from the JSON output.

---

## 12. Narrowing a run

`python token_metrics.py` still does the whole job with no arguments. Four flags narrow it, and
none of them changes what any figure means.

### `--no-fetch` — rebuild the workbook, fetch nothing

```
python token_metrics.py --no-fetch
```

Every display rule in this project is read-time: the confidence bands, the eight withheld
mechanisms, the labels, the window arithmetic. All of them are worked on against the store as it
stands, so this is the loop for any of them — and it is the only way to rebuild without spending
a day's politeness budget on the free endpoints, against a standing rule of one run a day.

It passes no `run_id`, deliberately. Nothing was fetched, so there is no run to attribute the
workbook to; dating it to the previous run would claim a freshness it does not have.

### `--project NAME` — one project, repeatable

```
python token_metrics.py --project Sky --project Uniswap
```

Names are exact, as in `config.py`. A name it does not recognise is **refused with the list**
rather than fetching nothing: a mistyped `--project` that quietly fetched nothing looks identical
to a run where every source had nothing to add — same empty result, same clean exit, no error
anywhere.

### `portfolio.txt` — the default scope

Put one project name per line in `portfolio.txt` (blank lines and `#` comments ignored) and the
**default** run covers only those. `--all` restores the full thirty.

```
# holdings
Sky
Uniswap
Ether.fi        # comments are fine
```

Names must match `config.py`'s spelling; **case does not matter** (`sky` resolves to `Sky`) and a
name listed twice is fetched once. Trailing `# comments`, full-line comments, blank lines and
surrounding whitespace are all ignored.

**A typo widens the run, it never narrows it**, and the run says loudly what it could not match —
an ERROR naming every line it could not resolve, followed by the full list of valid names.
The asymmetry is deliberate: a name wrongly parked stops collecting silently, and a series that
stops collecting cannot be backfilled — CoinGecko serves `total_supply` as a current value only,
confirmed on a live call. Fetching a project that is no longer held costs one extra API call a
day. On any doubt, the run widens.

**A narrowed run builds a narrowed workbook.** *Changed 2026-09-23 — it used to draw all thirty.*
The scope applies to the build as well as the fetch, on every tab including the Gap Report, and
the Master title says so ("16 of 30 projects — portfolio scope; `--all` renders the rest").
Drawing a project the run never touched put a frozen cell beside a fresh one with nothing to tell
them apart, which is the failure this whole tool exists to avoid.

Parked projects are **not** deleted. Their history stays in the store and comes back the moment
they are named again.

### Un-parking a project after more than 30 days

**Force a full backfill the first time you un-park anything that has been parked longer than a
month:**

```
TOKEN_METRICS_DUNE_ALWAYS=1 python token_metrics.py --project "Name"
```

A parked project's last stored day is usually a **partial** one: it stopped being fetched
part-way through the day it was parked, and providers publish the current day from the moment it
starts. Ordinarily that heals by itself, because the providers are called with a trailing 30-day
window and the next run re-requests the whole span. Past 30 days it cannot: the partial day has
fallen out of the window, so no ordinary run will ever ask for it again and it stays in the store
looking like a real observation.

Inside 30 days, just un-park it — the next run fixes it without being told.

### What the incremental window actually does

"Later runs re-fetch a trailing 30-day window" is true of what reaches the **store** on every
source. It is true of what goes over the **wire** on exactly one:

| source | effect of the 30-day window |
|---|---|
| coingecko | `days=30` is in the request. Real, and it saves the transfer. |
| defillama | full daily history downloaded, trimmed to 30 days locally — `/summary/fees/{slug}` has no date parameter. Saves storage and validation work, no network time. |
| dune | the query executes in full; a Dune query has no incremental mode. A metric fetched for the first time ignores the window deliberately, so a backfill is never truncated. |
| chain / hypercore / tron / scrape | accept the window and ignore it, correctly — they read a current value, not a series. |

So the way to make a run faster is `--portfolio` or `--project`, not a shorter window.

### Known-absent endpoints

A `(source, project)` pair whose endpoint returns **404** and that has **never** succeeded is
recorded in `fetch_status.absent_since` and is not called again for 14 days. Both conditions are
required:

- **404 specifically.** A timeout, a 429 or a 5xx is a source that is down or busy, and retrying
  tomorrow is right. A 404 is the server saying the thing is not there.
- **Never succeeded.** A pair that worked once and 404s now is a source that *moved*, which is a
  finding worth seeing every run — not something to stop asking about.

Any success clears it permanently, so a resource that appears later is picked straight back up.
The 14 days are measured from the last *attempt*, and a skip does not count as one, so the pair
offers itself for a retry on its own. The list is derived from the store, never declared in
config: a hand-maintained register of absent resources goes stale against reality and nothing
reconciles it back. Skips appear in the Run Log's SKIPPED column and say `KNOWN ABSENT`.

### Where the time went

Every run now prints wall clock per source, slowest first, with that source's row, failure and
skip counts beside it. It is not a profile and does not try to be: it answers the one question
that decides what to do about a slow run — *which tier is slow*. Tier 2 is contract reads over
public RPC and tier 4 is Dune; they have nothing in common and the remedy for one does nothing
for the other. A source taking minutes and returning nothing is the most useful line in a slow
run's log, and a rows-only summary cannot show it.

---

## 13. Mechanisms added 2026-09-22

### `unreconciled_flow` — the eighth withheld mechanism

A differenced flow whose values do not sum to its stock's move across the same span is blanked
and RED. The identity is exact — consecutive differences of one stock telescope, every
intermediate reading appearing once with each sign — so the tolerance is IEEE 754 and nothing
else. It is not a "close enough" band and must not become one: a residual that survives it is
tokens the flow column failed to report.

Found it: Hyperliquid's burn address moved 231,934 across 2026-09-21 while `gross_burn_tokens`
recorded 83,344. Nothing about the 83,344 looked wrong — right order, right address, right day,
and about a third of the truth.

### Every differenced flow anchors on `values_before`, not `latest_values`

The value being subtracted and the date that guards it must come from the same row. `prior_values`
is the newest reading of **any** date, which after the first run of a day is this morning's;
`prior_dates` is the last **earlier-dated** row. Using one with the other's date passes the
same-date guard while subtracting today's number, so each re-run writes only the increment since
the last and overwrites it on the `(date, project, metric)` key.

This was fixed for the chain adapter when PancakeSwap's 59,857,159.01 burn became 0.0123, and left
standing in `hypercore`, `tron` and `scrape`. All four now take `prior_delta`, and a test asserts
the second argument of every `derive_flow_from_cumulative` call across them — a fix applied to one
call site and not its siblings is the shape of bug that produced this one.

### `series_handover` — a deliberately stitched series

`measuring_point_changed` blanks any series read from more than one place, which is right for an
accident and wrong for a backfill handing over to a live read. A handover declares the ordered
pair and **nothing else**: the no-overlap is re-checked against the stored dates on every build,
and any third source blanks the column again. Run `python run_sql.py N` to read off the seam.

Where the legs also differ in what they *cover* — GEODNET's Dune backfill sums Polygon and Solana
burns while the live read is Polygon alone — that is a `composition_change` and renders as an
AMBER disclosure. The declaration does not settle it and does not pretend to.

### Daily flows compare against the same weekday

A daily flow carries a weekly cycle, so an adjacent-day comparison flags the calendar. Run
20260921T204341Z raised twelve `change_threshold` flags, all 40–60% drops, on a Sunday.

A trailing 7-day average does **not** fix this and the arithmetic is in the test: if a weekend day
is half a weekday, the mean is `(5W + 2×0.5W)/7 = 0.857W` and Sunday at `0.5W` still reads as a
42% drop. Same weekday one week earlier removes the cycle instead of averaging over it, and a real
step change survives because it is still there seven days later.

The Review Queue now carries `prior_date` and a `basis` string naming the rule that picked it.
Where no same-weekday point exists, the basis says the cycle was **not** removed rather than
implying it was.

### `reports_cap` — a provider serving the cap as the supply

CoinGecko returns World Mobile `total_supply = max_supply = 2,000,000,000`, the ERC20Capped
ceiling, not an amount anyone minted. The number is not wrong; it answers a different question,
and nothing about it looks incomplete the way a `:PARTIAL` marker would. The cell says so, and
`config.supply_denominator_unusable()` is the single place any future ratio denominated by
`total_supply` must consult.

### `metric_unit` — a label override that is not cosmetic

The number format comes from the metric's unit, so relabelling a cell without overriding its unit
leaves the format saying something else. 600 TB/day stored under `utilisation_pct` renders as
60,000% however the column is captioned. Rare on purpose, and always with `non_comparable` beside
it: a figure needing a different unit from the metric it sits in is by construction answering a
different question from that column everywhere else.

### `cumulative_flow` per project

`burn_address_balance → gross_burn_tokens` is global, because a dead-address balance means the
same thing everywhere and a transfer burn is monotonic by construction. Everything else is
declared per project: Chainlink's Reserve only accumulates, so its delta is an inflow; Uniswap's
TokenJar holds fee tokens that get swept, and GEODNET has no fund at all. A global entry for
`buyback_fund_balance` would have derived a "buyback" on all three from whatever each balance
happened to do.

A **fall** in a cumulative is now reported by name rather than returning an empty frame in
silence. That branch was harmless while its only caller was a dead-address balance, which cannot
fall, and would have hidden the Reserve's premise breaking.

### `revenue_base` — what a documented share is a share *of*

Sky states its Stage 2 allocation as percentages of monthly Net Protocol Surplus, which is a
different quantity from DefiLlama's `revenue_usd` and cannot be mapped to it. The implied-buyback
formula now multiplies the metric the protocol actually names. Windows ending before — or
spanning — the base's `effective_from` stay grey, because applying an NPS base to a window before
Stage 2 multiplies the right number by a share that did not exist yet.

`split_legs` renders the allocation's legs separately with their effects named. A single 27.5%
figure is right as buy pressure and wrong by exactly 5.5× as supply reduction, and nothing on a
row of numbers tells a reader which they are looking at.
