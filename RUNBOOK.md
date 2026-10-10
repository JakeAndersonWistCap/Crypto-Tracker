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


## 11bg. Jake's run on 20d0eb4 (2026-10-10)

**1. Bribe depositor 0x80f7… and "unpriced at deposit":** `python check_offline_items.py aerodrome_bribe_scan`.
- **Scope:** every Q0 epoch; every bribe token worth ≥ $1,000 at the quoted price, or unpriced, on every
  BribeVotingReward it was paid on.
- **Method:** each NotifyReward is located through the Base balance-bisection route. For each one it records the
  depositor, the time and DefiLlama's price AT THE DEPOSIT.
- **Per token:** $ at the quoted price, depth, whether the illiquid cap caught it, and the flag UNPRICED AT DEPOSIT.
- **Epoch table:** a flag count and its $ beside the cap's exclusions. The flag is NOT a second exclusion rule.
- **Output:**
  - (a) every reward 0x80f7… posted;
  - (b) every flagged token the cap did not catch.
- **Not covered:** tokens below the floor, and fee tokens.

**2. Morpho in_interest_day (+10.5% vs ±10%):** `python check_offline_items.py morpho_interest_listed_vs_all`.
- **Yes, the API can sum ALL markets:** drop `listed` from `where`.
- **The unfiltered population** is mostly fabricated self-lent supply (lending_api.listed_vs_unlisted_2026_09_23). So
  the probe splits three ways:
  - LISTED (our reference);
  - UNLISTED SELF-LENT (supply == borrow within 0.5%, over $1M);
  - UNLISTED OTHER.
- **Interest per day** = borrowAssetsUsd × ((1 + borrowApy)^(1/365) − 1).
- **Comparisons:** each split against DefiLlama's fees for the last 7 complete days from the local store.
- **The last 7 days per market** come from `historicalState` if the API serves it; a schema error is printed, not
  guessed around.
- **The 10% tolerance is unchanged** until Jake decides.

**3. Aerodrome actual_buyback_tokens "BUG: measuring point changed": fixed.**
- **Cause:** a negative day's derived row carried " [NEGATIVE: a lock left the buyback wallets]". Stripping the
  bracket left a trailing space, so it read as a second measuring point.
- **Fix, one source string everywhere:**
  - the derived buyback has one source;
  - the seeded and daily lock rows are both `ve_managed:foundation_locks` (the archive read is said in the log line).
- **Why E14 0.97% / AH14 −2.076M showed while the Q0 buyback read n/a:**
  - The retirement split views (retired_buyback_tokens etc.) are computed from the same rows the parent row blanks.
  - Their own source passed the check.
  - So the rates were built on a series the sheet was withholding.
- **Now the split rows inherit the parent's withholding**: status, blank windows and the reason.
- **Q0 change in the Foundation's locked AERO:** `python check_offline_items.py aerodrome_foundation_q0` (local store).
  - It prints the stock at both ends and the change, the claimable at both ends, and the summed daily buy-and-lock.
  - It also lists every negative day.
  - It is set beside Blockworks' 9.17M for Q2 (secondary, for scale only).

**4. Fluid 0xCaBe…:**
- **Relabelled** "team-controlled Avocado wallet, not a custodian or time-lock". It stays non-circulating.
- **Alert:** a balance read below 5,000,000 is a Review Queue item every run (`FLUID_IGP137_WATCH`,
  `fetch/validate.check_watched_wallet`).

**5. NEAR settlement backfill at 193/365 on 10-09 and 10-10.**
- **Mechanics:** an expired login stops every BigQuery read for the run and stores nothing (`_reauth`). The held days
  cannot move.
- **Recovery:** the next run after `gcloud auth application-default login` resumes from the state file — the top-up
  first, then one ≤31-day backfill chunk a run (`--seed near_bigquery` runs every chunk the budget allows).
- **The monthly budget can also hold the backfill** ("BACKFILL HELD" in the run log).
- **Run:** `python check_offline_items.py near_bq_backfill_status`. It reads the state only — the days held, the
  re-auth dates and the month's ledger vs the budget and reserve — and says which.

**6. Aethir customer revenue reference 15.408M.**
- **What it is:** the ARR tile $62.49M × 90/365. The tile is the last complete month × 12 (aethir_arr_formula, exact),
  so the reference was ONE month scaled to a quarter. It was not the right reference.
- **Now:** Aethir's own monthlyNetworkRevenue over the last three complete months (Jul-Sep $12.42M), scaled to our
  covered days.
  - Formula: `months_scaled`.
  - Same source, so the row is FRESH at best. The tolerance is unchanged at 25%.

**7. Aethir ARR (Jake decided):**
- `arr_usd` (ratios) = the last 3 complete months of monthlyNetworkRevenue × 4 ($49.70M). It is a read-time view,
  `_aethir_arr_view`.
- The tile is stored as `arr_tile_usd`, a labelled second figure. Rows stored as arr_usd before today are read as the
  tile, so no stored row is rewritten.

**8. Aethir node-reward share (Jake decided):** 50% = 21bn (15% checker + 35% compute).
- Source: docs.aethir.com/aethir-tokenomics/token-vesting.
- Split per Aethir Edge's post (15% checkers + 12% data centres + 23% edge).
- The 55% is kept as `superseded`.

## 11bf. Follow-ups on 0eda6ce (Jake, 2026-10-10)

**1. Aethir ARR (`arr_usd`, $62.49M): what it is computed from.**
- **Source:** GET `https://dashboard.aethir.com/protocol/demand-metric`, the page's server-rendered (RSC) payload.
- **Field:** `"arr"`, read with `fetch/aethir_pages.key_scalar` (pinned in Jake's probes5, 2026-10-01: 62,489,999.90).
- **Formula:** none of ours. It is stored as served, dated the read day. Aethir labels the tile "Annual Recurring
  Revenue (ARR) (1d)" and publishes no formula, so "(1d)" reads as a one-day run-rate, but that is not established.
- **Why it differs from Aethir's $126M (2025-04-11), $147M+ (FAQ) and $166M (Q3 2025, the blog's ARR beside $39.8M of
  quarterly revenue):**
  - **Dates:** those are 2025 figures; ours is 2026-10.
  - **Definition:** the 2025 figures annualise booked revenue, enterprise contracts included (Q3's $39.8M × 4 = $159M,
    near the $166M stated beside it).
  - **Scale:** the dashboard's own Network Revenue series read $868,694 for the week of 8 June 2026 (~$45M a year).
- **Not found:** no primary source gives the ~$156M.
- **Run:** `python check_offline_items.py aethir_arr_formula`. It prints the object holding `arr` and tests last
  week × 52, last month × 12, any daily-looking sibling × 365 and others; the one within 1% is the formula.

**2. Aethir allocation: 55% (config) vs 50% (docs).**
- **The 55%:** `allocation_reference` (23.1bn) has `source_url` docs.aethir.com/aethir-tokenomics/token-overview. It was
  first committed in cba7df0 on 2026-09-14 with `source_date` 2026-09-14. It is a reference record only: no code reads
  it.
- **The 50%:** docs.aethir.com/aethir-tokenomics/token-vesting gives 50% (21bn).
  - Read 2026-10-09 from a search snippet; GitBook exposes no version.
  - Trackers agree: Tokenomist 50.00%, Tokenomics.com 50.00% (updated 2026-06-18).
  - altfins splits it 15% checker + 35% compute providers.
- **None found:** no source reachable from here (searched 2026-10-10) shows 55%.
- **What changes downstream:**
  - Circulating schedule: NOTHING. It is Aethir's own monthly table (`CIRCULATING_SCHEDULE["Aethir"]`, Jake 2026-10-07),
    read verbatim.
  - Supplier emissions: NOTHING. Checker base 2,874,743 ATH/day = 10% of 42bn over 4 years.
  - Remaining node-reward pool (pool − 9.99bn distributed by 2026-10-06):
    - at 55%: 13.11bn;
    - at 50%: 11.01bn.
  - Beyond the stated Phases 1+2 (16.8bn):
    - at 55%: 6.3bn;
    - at 50%: 4.2bn.
- **Decision:** Jake's; nothing changed.

**3. Aerodrome after 2026-10-22: HELD, not burned.**
- The 0eda6ce entry cited about.mdx L212 for "buy back and burn". L212 (docs@99680a79, 2026-07-28) actually says:
  "100% of revenue generated by the Momentum Fund goes to supporting the Aerodrome Protocol via buybacks, grants to
  contributors and other ecosystem support measures such as strategic partnerships". It does not mention a burn.
- "Buyback and burn" appears only in a co-founder's X posts (Dec 2025).
- The entry is now `hold`, `verified False`, `recheck` "at the Aero launch (2026-10-22)".

**4. What funds the Foundation's buys: its own voting revenue.**
- **Source:** docs@99680a79.
  - tokenomics.mdx L16-18: the Foundation earns revenue "through participation in this system" and "uses its share of
    revenue to grow the platform with efforts like token buybacks".
  - about.mdx L141: the same.
  - about.mdx L196 (Flight School): "vote and earn revenue which would then be used to buy liquid tokens".
- **Size:** the PGF's 174.8M in managed veNFTs is ~17% of Voter.totalWeight (1,018.6M, 2026-10-09). That is an estimate,
  if the managed NFT votes its full weight.
- **Conflict with "fee-funded buybacks stay at 0":** partly, in wording.
  - The protocol sets no fee share aside for buybacks, so the IMPLIED row stays 0.
  - About ~17% of voter fees and incentives come back as AERO buys through the Foundation's votes. Those are in the
    ACTUAL buyback, never added to the implied one.
  - The label now says "no protocol share of fees goes to buybacks" instead of "fees buy nothing".
- **Overlap to keep in mind:** the protocol staking yield is a per-veAERO rate that includes the Foundation's share of
  rewards, and the retirement rate counts what it buys. Neither number double-counts. A SUM of yield + retirement would
  count the Foundation's ~17% twice.

**5. NEAR lockups:** `python check_offline_items.py near_lockups`.
- **List:** NEAR's own lakehouse method: every `*.lockup.near` created or funded and never deleted, from
  `receipt_actions`.
  - The probe DRY-RUNS first and prints the GB.
  - It runs only with `NEAR_LOCKUPS_RUN_BQ=1`. Or set `NEAR_LOCKUPS_FILE` to a list, one account per line.
- **Reads per lockup:** every read at one block, using the contract's own getters (core-contracts@1b0436c9
  getters.rs):
  - get_balance;
  - get_locked_amount: NEAR's own locked;
  - get_vesting_information → get_unvested_amount(schedule): STILL UNVESTED;
  - locked − unvested: VESTED, still lockup-locked;
  - get_owners_balance: released, not withdrawn.
- **Buggy contracts:** lockups on the two buggy code hashes are totalled apart.
- **Output:** aggregates only; no account is named and nothing is stored.
- **Under Jake's rule** only STILL UNVESTED leaves circulating. NEAR's own figure subtracts all of LOCKED.

**6. Fluid 0xCaBe…: CORRECTION to 11be.**
- It IS wired: `igp137_lock`, `noncirculating_igp137_tokens`, Jake's probes15 on 2026-10-07. The custody held exactly
  5,000,000 then.
- **Run:** `python check_offline_items.py fluid_igp137_custody`. It prints:
  - the Blockscout name and tags and Etherscan's verified contract name;
  - the code and the FLUID balance now;
  - every FLUID transfer IN and OUT since 2026-08-14.
- No OUT rows and 5,000,000 = the wiring stands.

## 11be. External blind audit 2026-10-09 + Jake's retirement rule 2026-10-10

**1. Aerodrome's Foundation buy-and-lock is now its buyback.**
- No buyer address is published. Aerodrome's X posts name the Public Goods Fund as the buyer ("The Aerodrome PGF has
  acquired and max-locked 312K $AERO", x.com/AerodromeFi/status/1996261069078348016).
- Its address 0x834C0DA0…2fDa52 is in Aerodrome's docs (security.mdx L34-37 @99680a79, read 2026-07-28).
- 0x623CF63A…57a1 "Buyback / Locked Funds" is from Aerodrome's Blockworks filing (read 2026-10-07).
- The docs say "over 184M AERO acquired and locked". Blockworks' 9.17M (Q2) / 27.95M (YTD) were not found in a
  primary source.
- **It is read from state.** Base logs are paid (Blockscout 402; Alchemy free = 10 blocks per getLogs), so the purchases
  themselves cannot be scanned.
  - Once a day `ve_managed` reads, for the two wallets' veNFTs:
    - NORMAL locks: the locked amount;
    - locks deposited into managed veNFTs: their `weights`;
    - managed veNFTs they own: skipped;
    - the rebase still claimable (RewardsDistributor 0x227f6513…).
  - `actual_buyback_tokens = Δ locked + min(Δ claimable, 0)` on consecutive days (`build_workbook._foundation_buyback_views`).
    A rebase claim nets to 0. A lock leaving the wallets is kept negative and named.
  - `actual_buyback_usd` = tokens × that day's price.
- **Destination:** held (locked). CORRECTED in 11bf: no first-party source says burned after 2026-10-22, so it stays
  held and is re-checked at the launch.
- **No protocol share of fees goes to buybacks.** The implied buyback stays a structural 0; the Foundation's buys, funded
  from its own voting share (11bf), are the actual buyback that the retirement rates judge.
- `in_buyback` is a DOCUMENTED LIMITATION. Upgrade path: Aerodrome's next Blockworks report, or a paid Base log route.
- **Run:** `python token_metrics.py --seed aero_buyback` (100 days of archive reads at each day's first block, only days
  with no row). Until then the Aerodrome A3 rates read n/a, not 0.

**2. Aerodrome migration: plan only, nothing built.**
- **Launch:** 2026-10-22 00:00 UTC on 7 chains (aero.xyz/articles/aero-launch-update-all-systems-go, 2026-09-25).
- **The new AERO address is not published yet.** Legacy Base AERO is 0x940181a9…8631.
  - Conversion: AERO 1:1, VELO ~0.044, through upgrade portals.
  - Coinbase converts AERO Nov 2-4.
  - veAERO/veVELO become sAERO (permanent locks → max).
- **History chaining:** keep the legacy series up to the day before launch. Price and supply then come from the new
  token, scaled 1:1 for AERO, so no restatement is needed. VELO-derived supply arrives as a step on the launch day and is
  marked as a measuring-point change, not issuance.
- **Replaced reads:**
  - `Voter.totalWeight` → total sAERO staked/allocated.
  - Voter rewards → continuous pool revenue to sAERO, with no epochs: weekly windows become daily flows.
  - The Foundation lock read stops. The Momentum Fund's buys are read where its address and destination are published
    (a burn only if a first-party source says so — 11bf).
  - Emissions are set by revenue (AER Engine caps rewards per pool by projected revenue; ~11% a year).
- **MATURING from 10-22 until the new reads have a full window:** in_revenue, in_voter_apr_epoch, a3_protocol_yield,
  in_voting_power, in_locked, in_emissions / rebase, in_buyback, and the buyback split.
- **Before building:** the new token and sAERO addresses from Aerodrome's docs or repo, each with a URL and a date.

**3. Aethir.**
- **Dashboard circulating 24.05bn, decomposed:** vesting buckets ~12.97bn + node-reward residual ~11.1bn (the dashboard
  shows 9.99bn of rewards distributed).
- **Tokenomist's 20,128,764,593** equals our own schedule table's 2026-05 value. It is ~5 months stale; it does not
  exclude a bucket.
- **Tokenomics.com (~33%)** excludes the checker/compute pool ("Undisclosed").
- **ARR $62.49M** is Aethir's dashboard key `arr` ("Annual Recurring Revenue (ARR) (1d)"), read as published
  (`arr_usd`, `_a2_headline.arr()`).
  - The ~$156M was not found in a primary source. Aethir's own figures are $126M (Apr 2025), $147M+ (FAQ) and $166M
    (Q3 2025).
- **Open for Jake:** the docs allocation reads checker/compute 50% but our config note says 55% (token overview). The
  listed buckets sum to 95%. Not changed.

**4. NEAR circulating.** NearBlocks' 1,249,836,992 should not be primary.
- NearBlocks' current code (98e773b2) sets circulating = total supply (since a16191e9, 2025-11-20).
- The lockup-excluding formula was removed in fa04a93f. The live figure cannot be reproduced from their code.
- The lockup balance (~58.7M per the audit) cannot be read from here (NearBlocks and NEAR RPC are unreachable).
- The first-party figure is BigQuery `circulating_supply` (unchanged).

**5. NEAR watch** (config `protocol_burn.watch`).
- **HSP-027** passed 2026-07-06 (gov.near.org/t/42213) and is implemented in nearcore 17b93d9a. Only 2.14.0-rc.1..3 are
  tagged, so it is **not live** and has no activation block.
  - `switch_on` stays None. The first `near_protocol_version >= 87` reading dates it.
- **Issuance 2.5% → 1.6%:** a draft (gov.near.org/t/42644, 2026-10-07). The vote date is unconfirmed.

**6. GEODNET staking** (config `superhex_staking.terms_2026_10_10`).
- Terms: 20% bonus after a 1-year producing period (Phase II; Phase I paid 10%); 180-day no-show refund without the
  bonus; 20,000 GEOD per SuperHex (docs.geodnet.com/geod-console-advanced/staking-faqs).
- On 2,993,000 staked (Blockworks, 2026-10-04): ≤ 598,600 GEOD a year (an upper bound).
- Pending Jake's review.

**7. Fluid AGI3.**
- Passed and executed as IGP-137 (Tally "Executed"; fluid-governance@8891f73).
- It moved 5,000,000 FLUID from Treasury 0x28849D2b… to Team Multisig 0x4F6F977a…. Both are already non-circulating
  (IGP-137 is the source).
- On 2026-08-14 the multisig sent 5,000,000 onward to 0xCaBebC7f…3fd7. CORRECTED in 11bf: that custody IS wired, as
  `igp137_lock` (Jake's probes15, 2026-10-07; series `noncirculating_igp137_tokens`). Kinetic's buys are unconfirmed.

**8. Sign-off disagreed between the two reports.**
- Morpho `in_interest_day` sums DefiLlama fees over the 7 complete days to yesterday UTC. A report evaluated after 00:00
  UTC lacked the not-yet-published day and read "CHECK (no figure)".
- Both reports now read ONE cached evaluation (`credibility_report.evaluated`: store fingerprint + code hash +
  narrowing + as-of; `metrics.db.credibility.pkl`, 12 h) and print the same `signoff_block`.
- `TOKEN_METRICS_FRESH_EVAL=1` forces a fresh evaluation.
- Test: `test_credibility_and_completeness_print_the_same_signoff_across_midnight`.

**9. Base logs.**
- `EXPLORER_LOG_ROUTES[8453] = []`: Blockscout answers 402 on Base.
- `base_logs_where_balance_moves` bisects archive `balanceOf` to ≤10-block windows, then calls eth_getLogs only there
  (Alchemy's free cap).
- `aerodrome_epoch_rewards` uses it for NotifyReward. `explorer_logs` names the missing route instead of "no key".
- **Run:** `python check_offline_items.py aerodrome_epoch_rewards` (LAPTOP 0xb0952747…, XDP 0x07b3d902…) to print each
  depositor, tx, block time and DefiLlama price at the deposit.

**10. Label.** The 17.37% row is the per-epoch voter APR UNCAPPED (before the illiquid-token rule), DefiLlama's
like-for-like dollars vs on-chain. It is relabelled "CHECK ROW, NOT THE HEADLINE". The headline 12.10% is capped.

**11. Artemis.**
- Ethereum `settlement_volume_usd` ends 2026-08-25. No retirement or rename was found in Artemis's docs or changelog.
- It is stale (AMBER) by the 45-day rule from today (`ARTEMIS_SETTLEMENT.series_status`).
- **Proposal (not built):** BigQuery P2P + DefiLlama DEX/NFT, validated against Artemis to 08-25.

**12. Retirement = buybacks BURNED or HELD (Jake, 2026-10-10).**
- **Rule:** `BUYBACK_DESTINATION_SHARES` holds dated, sourced shares per project (`buyback_destination_shares`). The split
  rows are buyback_burned / held / distributed_tokens.
  - The retirement rates, "actual buyback %" and net absorption read `retired_buyback_*` (burned + held).
  - The full buyback stays as its own row.
  - The yield-payout column reads the distributed part.
- **Classified from primary sources:**
  - Burn: Uniswap, GEODNET, Hyperliquid (burned by consensus).
  - Hold: Chainlink, NEAR, Fluid, Maple (MIP-019); Aerodrome (locked; no burn sourced — 11bf).
  - Distribute: Pendle (sPENDLE), Ether.fi (sETHFI).
  - Sky: held until 2026-08-17, then 5/27.5 burned and 22.5/27.5 distributed.
- **Corrections to Jake's read:**
  - Sky burns 5% of NPS; the 0.55 in config is buyback vs farm.
  - Ether.fi's new programme is reportedly 50/50 (secondary source only; kept as distribute).
  - Aave is unverified.
- **Staking yield (12c):** Pendle's per-epoch APR and sETHFI's share-price growth already measure the distributed
  buybacks, so nothing is added.
  - Sky's yield is the USDS farm only; adding its SKY leg needs the SKY farm's stake (proposed, not added).
- **Before → after on the six real dumps (12d):**
  - Pendle:
    - circulating retirement 1.387% → 0
    - FDV retirement 1.174% → 0
    - actual buyback % 1.387% → 0
    - net absorption +729,811 → −83,732
  - Aerodrome: retirement 0 (N/A) → n/a until `--seed aero_buyback`, then measured.
  - Chainlink, Maple, Ethereum, Plume: no move.
  - Expected elsewhere: Ether.fi → 0; Sky → only post-08-17 buys, 18.2% retired; burn/hold routes unchanged.

## 11bd. Jake's run 2026-10-09 18:13: tolerances restored, the probe's epoch check, the fee-leg finding

- **Tolerance lost on conditional rows.** A row whose reference is a `verdict_when` keeps its tolerance in the branch
  it takes (`otherwise`), but the row took the outer spec's: blank, so Aerodrome's epoch rows read CHECK at -5.9% /
  -5.6%. `credibility.reference` now returns the taken branch's `tol`; both rows are ±10% again.
  - `tests/test_real_dumps.py::test_real_every_judged_row_has_a_tolerance` checks every judged row on all six dumps.
- **`aerodrome_epoch_rewards` refused two Thursdays.** Epochs are unix weeks (`ts - ts % 1 weeks`; 1970-01-01 was a
  Thursday), so the check is `ts % 604800 == 0`. The old check shifted it by three days.
- **The tokens, from the evening dump's run log** (the excluded-token log line now names the address too):
  - 09-03: LAPTOP $7,546,432 quoted vs $595,818 depth → $6,950,614 of bribes excluded (+ $145 of fee tokens).
  - 09-24: XDP $627,592 quoted vs $194,108 depth → $433,484 of bribes excluded (+ $657 of fee tokens).
  - The probe prints each one's address, depositor, tx and DefiLlama's price at the deposit.
- **`in_revenue` is a VERIFIED FINDING** while the quarter passes ±10% and DefiLlama's fee leg sums below on-chain
  (`credibility._aero_fee_leg`). DefiLlama under-counts Aerodrome voter fees by ~4-7% in a typical week; three weeks far
  lower; Dune agrees with on-chain. The on-chain figure is the reference.
  - 10-01's -37.4% is mostly 09-24's XDP bribe: subtracting $838,660 of bribes at our epoch-end prices, of which
    DefiLlama apparently booked far less.
- **Today's-price APR on the same capped flow.** `in_apr_today_price` used the uncapped flow (12.2%) and named the
  headline 17.55%. It now applies the illiquid-token rule: 8.52%, beside the 12.10% headline.

## 11bc. Jake's run 2026-10-09 15:59 + checks 17:05: Aerodrome epochs, the LAPTOP bribe, the illiquid-token rule; Pendle Optimism = 0

**1. Aerodrome epochs never stored (tier timeout).**
- `python token_metrics.py --seed aero_epochs` (since 593e702) stores every Q0 epoch in one sitting: fees, bribes, the
  illiquid excess and `Voter.totalWeight` at each epoch's start block. No per-run cap, no tier budget.
- The daily `aero_voter` tier now reads the newest epoch plus at most ONE missing one (`backfill_per_run: 1`). The pool
  set at past blocks is read only when a missing epoch is in the read. The newest epoch alone uses the pools with
  votes now; its start-block `totalWeight` is still read (two block lookups and one call).
- **A timeout never lapses the 10-10 MATURING to CHECK.** `credibility._tier_timed_out` reads the last `aero_voter` run
  in `run_log`. If that run ended with "TIER TIMED OUT", `in_revenue` / `in_voter_apr_epoch` stay MATURING and say
  "not stored (tier timeout: …)". A DefiLlama day still missing after 10-10 is still a CHECK.
- `dump_fixture.py` now also carries the tier-wide run-log lines (project NULL) of the project's sources, so a dump
  shows the timeout.

**2. Epoch 2026-09-03: $7,595,469 of bribes.**
- **What DefiLlama does with it.** DefiLlama/dimension-adapters @af2f691 `dexs/aerodrome/utils.ts` L30-72
  (`PRE_LAUNCH_TOKEN_PRICING`) lists LAPTOP `0xb095274743941e953c746f9c228da9c18bb6ec29` ("Hunter Biden's Laptop
  ($LAPTOP), launched on Base on 2026-09-09", L66-71). It is an Aero Ignition token whose bribes were deposited before
  it traded. `handleBribeToken` (L78-94) values them at a hand-entered `conversionRate: 1.86` until `tradesFrom
  2026-09-10`. Jake's store holds DefiLlama $7,922,809 for 2026-09-09: 32% of Q0's $24.48M, against $100K-$425K on
  other days. That is the LAPTOP bribe at $1.86.
- **Not independently checked yet.** Our like-for-like uses our on-chain bribes on both sides. Dune (@0xkhmerlab)
  values the same epoch's CL200-USDC/LAPTOP bribes at ~$14.94M: three prices for one deposit.
- **The token list: `python check_offline_items.py aerodrome_epoch_rewards`.** Default epochs are 2026-09-03 and
  2026-09-24; `AERO_EPOCHS=YYYY-MM-DD,...` overrides. For every reward token of the epoch it prints:
  - token, symbol, kind, raw amount and decimals;
  - the price used (DefiLlama coins at the epoch end) with DefiLlama's `confidence`, and the $ value;
  - the depth a sale could reach, and what the rule excludes.

  For the 12 largest bribes it also gives each `NotifyReward` (`IReward.sol` L16 @1ba3081) — depositor, tx, time —
  with DefiLlama's price at the deposit. For the largest bribe token it gives both sides of each pool it was paid on.
  24h volume is not read: the Swap logs are paid on Base.

**The illiquid-token rule (Jake 2026-10-09 17:05, Jake to confirm the definition).**
- A non-native reward token (anything but AERO) counts at its quoted price only up to its DEPTH at the epoch's end
  block: the OTHER token's `balanceOf` in each voted Aerodrome pool that holds it, × that token's price.
- This is the most a seller could take out. It is stricter than an aggregator's "liquidity", which counts both sides
  at the quoted price.
- Pools it was paid on are read first, 50 per token per round, stopping once the depth covers the amount. Pools
  outside the voted set and other venues are not read, so the depth is a FLOOR.
- A token held by no voted pool, or whose pools could not be read, is NOT capped. It is listed instead
  (`liquidity_unread`).
- **Storage.** The excess per epoch is `voter_rewards_illiquid_fees_usd` / `voter_rewards_illiquid_bribes_usd`. The
  `usd` / `fees` / `bribes` figures stay at the quoted price, because the like-for-like check needs the same basis as
  DefiLlama.
- **The headline.** `credibility.payday_headline` takes each epoch's excess off DefiLlama's flow on the days it was
  booked: bribes over the epoch's own days, fees over the week before, an equal share a day.
  - The labelled line is `rewards_illiquid_excluded_usd`. The figure before the exclusion is in the source text.
  - Until an epoch's rule is stored, the working says "ILLIQUID-TOKEN RULE NOT YET READ".
- **Other projects.** Only Aerodrome's yield includes non-native rewards. Sky pays USDS, Pendle PENDLE, Ether.fi
  ETHFI; no other project has a per-token breakdown.
- **`in_bribe_outliers` (an a3 input).** An outlier is an epoch whose bribes after the rule exceed 5× the quarter's
  median week.
  - If one survives the rule, the row is a VERIFIED FINDING, and its value is the headline excluding that epoch: the
    labelled second figure.
  - If none survives, N/A (recorded). If the epochs or the rule are not stored, MATURING (until 2026-10-10).
- **Before / after on the 15:26 dump (illustrative until the probe reads the depth).** The headline is 17.44%. If the
  rule excludes LAPTOP down to DexPaprika's TODAY total liquidity (~$614K, not the payment-time depth), it is ~12.2%.

**3. Like-for-like gaps.**
- `aerodrome_epochs_q0` now prints a fee-leg column: DefiLlama's week before less that week's on-chain bribes,
  against the epoch's on-chain fees. It also prints the fee gap, the excluded $, and the fee leg's quarter sum and
  spread. `in_revenue`'s working gives the same.
- DefiLlama's 2026-09-24..09-30 are all stored ($1,889,766, fetched 10-09 09:14).
- 10-01 at −32% means the $838,660 of 09-24 bribes is far more than DefiLlama booked that week. Its whole week is
  $1.89M against $1.92M the week before. This points to a 09-24 bribe token DefiLlama priced lower (or not at all) on
  its deposit day than at the epoch end. The probe's 09-24 table lists it, with both prices.
- Dune's per-epoch fees match ours (157 / 158 / 161). That is recorded on `in_revenue` as independent evidence on
  the fee leg.

**4. Pendle.**
- **Optimism = 0.** Jake's reading on 2026-10-09: the gauge's last token transfer was 743 days earlier. It is stored
  in `manual_overrides.csv` as `emissions_tokens_gauge_optimism` and `_direct`, both 0, with that evidence.
  - A hand-entered stream does not bound the daily emissions sum's last day.
  - On the 15:26 dump with these rows, `in_emissions` names only Arbitrum as missing.
- **Arbitrum** is unchanged: see 11bb, `pendle_gauge_reconcile`, and the Q0-window reconciliation.

## 11bb. Jake's `--seed pendle_gauges` 2026-10-09 15:33: Arbitrum short 733K, Optimism HTTP 500

Mainnet reconciled (direct count 92,225.20 from 2026-07-01). Arbitrum did not reconcile: in−out 812,025.6 against
balanceOf 78,717.9, so 733,307.6 of outflow is missing. Optimism's Blockscout answered HTTP 500 three times.

- **The token and the payout route, from source.** pendle-core-v2-public @87685c8: `deployments/42161-core.json` lists
  one PENDLE (0x0c880f67…) and one gaugeController (0x1e56299e…). `PendleGaugeControllerUpg.sol` holds an immutable
  `pendle` (L28) and moves it only through `IERC20(pendle).safeTransfer` (`redeemMarketReward` L58, `withdrawPendle`
  L81), which are plain Transfer events. There is no other token or contract route, and no counterparty filter applies
  to the reconciliation (it is in − out over every Transfer).
- **Where the outflows went missing: `python check_offline_items.py pendle_gauge_reconcile`** (chain arbitrum by
  default). It:
  - reads `gauge.pendle()` against the configured token;
  - checks the cached streams for duplicates, events past `scanned_to`, and the largest gaps between payouts;
  - bisects archive `balanceOf` against the cached in−out to the FIRST block where they part;
  - reads that block afresh through RPC `eth_getLogs` and the explorer, listing every Transfer the cache lacks, with
    its counterparty.
- **The quarter, reconciled on its own (`fetch/logscan._window_scan`, `window_days: 100` on the L2 gauge scans).**
  When the full history cannot be used (not reconciled, refused, or still seeding):
  - the last 100 days are read fresh: explorer first, else RPC `eth_getLogs`; the block for the window's start comes
    from the explorer, else a bisection on RPC headers;
  - they are stored only if `balanceOf(end) − balanceOf(start)` equals in − out to the wei;
  - the direct count is the same fresh read (reconciled), and the log line names what served it. Once a day.
  - Optimism: zero payouts with an unchanged balance reconcile, so the window is stored as 0 with that evidence and no
    longer blocks the sum. The direct count falls back to RPC too.
- **Optimism routes.** Blockscout, then Etherscan V2 chainid 10 (researched as paid on the free key; its own answer
  is logged), then RPC `eth_getLogs` in the window scan.
- **Aerodrome, from the 14:57 dumps.** The 14:37 run stored no epoch at all: no fees/bribes split and no
  epoch-start weight, and its run log has no aero_voter epoch line. Reading all fourteen epochs in one call outran
  the tier's 180s budget, and a timed-out tier stores nothing.
  - Routine runs now backfill at most 3 earlier epochs a run (`backfill_per_run`), newest first, and name how many
    are left.
  - `python token_metrics.py --seed aero_epochs` reads every Q0 epoch in one sitting.
  - Epoch-start blocks come from the archive backfill's day-block cache (`archive-blocks-base.json`) before any
    header search.

## 11ba. Jake's run 2026-10-09 ~14:10: Aerodrome timing, Q0 backfill, per-epoch stake; Pendle L2 emissions; rebuild guard

That run (on 81a07f3) signed off 14; only Aerodrome stayed OPEN.

1. **Aerodrome.**
   - **1a. Timing, verified from the contracts.** Fees reach FeesVotingReward only through `Gauge._claimFees()`,
     called inside `Gauge.notifyRewardAmount`, which only the Voter calls, from `distribute()`:
     - aerodrome-finance/contracts @1ba3081: `gauges/Gauge.sol` L78-102 and L197-203; `Voter.sol` L486-513.
     - `Reward._notifyRewardAmount` books the amount to `epochStart(block.timestamp)` (`rewards/Reward.sol` L240-247).
     - Slipstream's CLGauge does the same (slipstream @f8717fa, `contracts/gauge/CLGauge.sol` L295-313, L356-382).

     So epoch E's fees are those earned in E-1, while bribes are booked to the epoch they are deposited in. DefiLlama
     books staked-LP fees on the swap day and bribes on the NotifyReward day (dimension-adapters @0219a7b
     `dexs/aerodrome/index.ts` L59-79, L214-240).
     - Like-for-like: DefiLlama over E-1's seven days − E-1's bribes + E's bribes. The bribes come from the on-chain
       split, which is the same NotifyReward events DefiLlama reads; its label split is not stored here.
     - The row therefore tests the fee leg, which the working prints on its own. The 10% tolerance is unchanged.
   - **1b. Every Q0 epoch.** `fetch/aero_voter.py` reads every epoch from the one before Q0 to the last complete one in
     one pass (`check_offline_items.aerodrome_voter_epochs`). It stores:
     - `voter_rewards_onchain_fees_usd` and `voter_rewards_onchain_bribes_usd` beside the total;
     - earlier epochs are retried once a day until stored.

     `in_revenue` is judged on the quarter's sum, with every epoch's gap and the per-epoch spread in the working.
     `in_voter_apr_epoch` is judged on the mean of the per-epoch APRs. A latest epoch still missing after 2026-10-10
     is CHECK. The pool set is pools with votes now plus, from the archive, pools with votes at the last block of any
     epoch read.
   - **1c. Per-epoch stake.** `Voter.totalWeight` is read at each epoch's start block. The block is found from
     headers by `rpc_block_at`, and the read is stored on the epoch's start date (`:archive`) wherever no live
     reading exists. The headline (`payday_yield`, `stake_epoch_days: 7`) divides each paid day by its epoch's stake.
     An epoch without its own reading borrows the nearest one and is named.
     - Before and after on the real dump: 17.445% and 17.445%. The dump holds one totalWeight reading (2026-10-09),
       so all 13 epochs borrow it.
     - The true "after" comes on the next run, once the archive reads land.
   - `python check_offline_items.py aerodrome_epochs_q0` prints every epoch: fees, bribes, totalWeight at its start
     and the like-for-like gap.
2. **Pendle emissions.**
   - The L2 scans seed from block 0, at 140s a run, and store nothing until their whole history reconciles. Until
     then the sum read mainnet only.
   - The emissions column is now refused while any listed scan has stored nothing, and the block reason names it.
     `python token_metrics.py --seed pendle_gauges` finishes the seeds in one sitting.
   - The reference is each chain's DIRECT count: once a day, one fresh windowed explorer query with no cache, stored
     as `emissions_tokens_gauge_<chain>_direct`. Every chain is needed, or there is no reference. This is
     pendle_emissions_q0's computation, not the series ours is built from.
3. **Pendle rebuild guard.** A day's fall is taken only from two reads at least 20h apart, scaled to 24h.
   - Read times: an archive read is at the UTC day start; a live read is at its `fetched_at` when that falls on its
     date, otherwise unknown and skipped.
   - Active locked must be no more than all PENDLE locked (`locked_tokens_legacy_vependle`).
   - A failing day is skipped: never rebuilt, calibrated on or judged for drift.
   - The probe, the headline and the Credibility rows use the one guarded `credibility.pendle_calibration`. The
     probe's own unguarded fall (archive 10-08 00:00 → live 10-09 10:41, 34.7h, taken as one day) gave the 94.95M.
   - On the real dump the 10-09 pair is now 34.7h apart and scaled: active 61.44M, ratio 0.9873. Calibration ×0.9865
     (0.9862..0.9873), no drift, headline 1.55%.

## 11az. Pendle 1c: the calibrated on-chain rebuild (Jake's decision, 2026-10-09)

Pendle's per-epoch `aprs` read 0, and the API's virtual sPENDLE is stored only from 2026-09-29. For Q0 epochs before
that, the stake's virtual part is **our on-chain rebuild, calibrated to the API**:

- **Rebuild** (`credibility.virtual_rebuild`) = active locked + 3 × vePENDLE supply. Active locked = the vePENDLE
  supply's daily fall × 728 days (104 weeks). Expired locks no longer decay, so they drop out. A fall is taken only
  between consecutive days at the same measuring point (archive with archive, live with live); otherwise the last good
  one is carried.
- **Calibration** = the mean of API / rebuild over every day both exist. The stake used = rebuild × that ratio. Each
  such epoch's source reads "calibrated on-chain rebuild (x ratio = API/rebuild mean over N day(s) a..b, range lo..hi)".
  The rebuild comes before Pendle's published APR. That APR is used only for an epoch with no rebuild.
- **Daily check.** On every day the API has a value, a ratio more than 0.5 percentage points
  (`epoch_mean.calibrate.drift_pp`) from the mean is DRIFT: `in_epochs_first_party` reads CHECK, naming the days, and
  so does the headline.
- **Headline** = the mean of the per-epoch APRs over every Q0 epoch with a published distribution. A 0 inside the
  observed 7-day publish lag is left out (2026-09-22, until 2026-10-13).
- **Verdict.** `in_epochs_first_party` is a DOCUMENTED LIMITATION (calibrated, not first-party) while any rebuilt
  epoch is in Q0, and lapses to N/A when the API covers every Q0 epoch (~2026-12-28). The headline inherits it.
  `in_epoch_reproduction`/`in_epoch_mean` are a DOCUMENTED LIMITATION while Pendle publishes no per-epoch APR.
- On Jake's store (`fixtures/real_pendle.json`): ×0.9864 (= 1/1.0138), range 0.9862..0.9868 over 8 API days
  (09-29..10-09), no drift. Per epoch: 07-14 1.64%, 07-28 2.30%, 08-11 1.12%, 08-25 1.68%, 09-08 1.01%. Headline 1.55%,
  DOCUMENTED LIMITATION.
- `python check_offline_items.py pendle_epoch_table` prints the CALIBRATION line and every API day's ratio.
  `pendle_virtual_rebuild` prints the uncalibrated daily table.

## 11ay. Jake's run 2026-10-09 11:41: real dumps committed; Pendle, Aerodrome, Ethereum, Maple

That run (on c303cd3) signed off 13; Aerodrome and Pendle stayed OPEN. `fixtures/real_*.json` are now committed, and
`tests/test_real_dumps.py` judges every item below on them.

1. **Pendle yield.**
   - **1a. Pendle's per-epoch APR is empty.** `sPendleHistoricalData.aprs` is present but reads 0 for every completed
     epoch. Evidence: run log 2026-10-09, "no complete epoch with an APR above 0". The published-APR fallback cannot be
     used. The fetcher now logs this instead of storing nothing silently.
   - **1b. Publish lag.** An epoch counts only if we were watching when it was published:
     - read at 0 after it ended, then above 0 later;
     - or ended after our first read;
     - or was the latest complete epoch at our first read (an upper bound).
     On Jake's store that is 2026-09-08: +7 days. Before the fix, the 239 days counted epochs first logged on 10-09.
   - **1c. Virtual sPENDLE rebuilt on-chain does not pass the 1% rule.** `check_offline_items.py
     pendle_virtual_rebuild` prints the daily table.
     - locked + 3 × vePENDLE supply: +2.51 to +2.56% over the API on all 8 days we have.
     - Without expired locks: +1.36 to +1.40%. The active lock is taken from vePENDLE's daily decay × 728 days, about
       2.0M of locks are expired but unwithdrawn, and the formula leaves them out.
     - Jake's 178.76M paired the 09-29 lock with the 09-22 vePENDLE supply; the same-day figure is 37.80M.
     - Our stake stays the API's, which is stored from 2026-09-29 only. Q0 epochs before that have no APR, so
       in_epochs_first_party is CHECK and the headline is not formed.
   - **1d.** 2026-09-22 (read 0) stays unpublished until 2026-10-13.
2. **Pendle emissions.**
   - Gross issuance is MEASURED: the change in mainnet PENDLE `totalSupply()`. It is read as `total_supply_gross`
     (contract `token_gross`) and derived through `issuance_from_gross_supply`; ISSUANCE_PRIMARY is first_party.
     `python archive_backfill.py --run --project Pendle` fills Q0.
   - Emissions are the gauge payouts summed by day: mainnet + Arbitrum + Optimism. The L2 scans run on their own tier,
     `explorer_gauge_l2`, and seed on the next run. `in_emissions` sets the sum against each chain's scan.
   - BNB 56, Sonic 146, Mantle 5000 and Berachain 80094 have no free log route. They are named on
     `in_emissions_unrouted`, a DOCUMENTED LIMITATION, which a3_net_absorption inherits.
   - Coverage rule: a flow from a log scan that reads from genesis covers the whole window (the scanned range), not
     only the days with events.
3. **Aerodrome.**
   - **3a.** Coinbase's reader now asks for Q0 once. If the first candle is later than asked, the log says the product
     did not trade before then. `in_price_confirmed` compares the payment-weighted price from CoinGecko and Coinbase
     over the days both hold, needing at least 30. On Jake's store: 33 days, $0.6450 vs $0.6453, PASS.
   - **3c.** No on-chain epoch was stored. The epoch marker said "stored already" while the row had been lost, so
     `fetch/aero_voter.py` never read it again. It now checks the store. `aero_epoch_pending` names a missing on-chain
     epoch too, so the rows read MATURING (until 2026-10-10) and name it.
   - Today's-price APR is recorded, not judged (Jake's convention).
4. **Ethereum.**
   - The 17,022 covered 6 single-day issuance rows. It left out the 10-05 row, which spans 10-02..10-05, because the
     burn was looked up day by day, while the 10-05 burn row is span-tagged too.
   - Our burn now covers a span through the burn rows' own spans: 28,722 over the full 10 days (09-30..10-09), the
     same as the A4 headline.
   - The net view is marked differenced, so J5 annualises over 10 days, not 9.
5. **Maple.** VERIFIED on Jake's run: both mints were the Safe executing `claim()` on the RecapitalizationModule
   0x5dfe0460; `currentIssuanceRate()` reads 0, so the schedule has ended.

## 11ax. Price convention: payment-day prices (Jake, 2026-10-09)

Every protocol yield with non-native rewards converts them to the staked token at the PAYMENT-DAY price: tokens = the
sum over the window's days of $paid_d / price_d. They are divided by the stake in tokens over the same window (the mean
of its stored days there) and annualised over the days counted. A paid day with no same-day price is left out and
named. One function, `credibility.payday_yield`, is used by the workbook view (`build_workbook._payday_yield_views`)
and by the Credibility rows that reproduce it.

- **Aerodrome.** `token_yield.payday`: holders revenue (fees + bribes) over 90 days, AERO price, Voter.totalWeight,
  ×365.25. It replaces the Q0 simple mean price. The dollar column on the same basis is the same number (the price
  cancels), so it reads the same row. The voting-power column divides the same payday tokens a year
  (`rewards_tokens_payday_annual`) by voting power. `in_voter_apr_epoch` converts each epoch day at its own price; the
  on-chain dollars use the epoch's payment-weighted price. `in_apr_today_price` stays as the labelled second figure;
  it compares spot with the payment-weighted Q0 price, from CoinGecko and Coinbase.
- **Sky.** `token_yield.payday`: USDS paid to the USDS farm over 28 days, SKY price, the farm's mean stake, ×365. It
  replaces USDS ×365/28 / (stake × spot). `in_apy_usds_farm(_rr/_ba)` and `in_yield_economy_wide` use `basis: payday`.
  Their references (Sky's page, rewardRate, Block Analitica) are live spot-price rates, so they now differ from ours
  by SKY's move over the window as well.
- **Unchanged:** Pendle and Ether.fi (native rewards). Their dollar columns still divide by stake × spot.
- `python check_offline_items.py price_basis_before_after` prints, per project from your store: before, the
  price-basis step alone, after, and the APR at today's price.

## 11aw. Jake's run 2026-10-09 ~10:15: tested on the real store

That run (on 1cb8f6b) signed off 10 projects. Five were OPEN: Ethereum, Chainlink, Plume, Aerodrome and Pendle. The
fixtures had predicted 14-15; the real store disagreed. **From this round, a fix is tested against Jake's own rows.**

0. **Real dumps.** `python dump_fixture.py --project X` writes `fixtures/real_<x>.json`: the project's rows over 400
   days, its review queue, overrides, 30 days of run log (URLs redacted) and the latest gap report. Commit the file.
   `tests/test_real_dumps.py` loads it into a fresh store and runs `credibility_report` as of the dump's date. Each
   test skips until its dump is committed.
1. **Ethereum a4_net_change.** An issuance row can cover several days: Etherscan's supply doesn't move daily, and the
   day it moves stores d(supply) + the burn since it last moved. The row is tagged `[span=Nd]`; the live path now tags
   it too. Ours sums the rows of the common days and their spans, never the full Q0 sum. The working lists each row
   with its span ("OUR ISSUANCE ROWS").
2. **Chainlink.**
   - The release differences only consecutive days with the SAME wallet set and no PARTIAL component. The +400,000
     step was the summed balance stepping when wallets were added to the list (24 → 27), not a release.
     `check_offline_items.py chainlink_release_steps` prints every step and marks those "NOT A RELEASE".
   - A1 FEES ÷ ISSUANCE divides by `gross_issuance_tokens` (LINK minted). LINK was fully minted at genesis, so A1 is
     N/A by definition. Neither the release nor the RewardVault's emissions is that figure.
   - a3_buyback_locked: "N/A (recorded — judged by the exact-date row)".
3. **Plume.** Jake confirmed supply.plume.org = 6,606,142,167 (2026-10-09). `sources.yaml` records
   `first_run_confirmed` on the entry. A confirmed anchor stops raising `anchor_unconfirmed`, and the confirmation
   persists across runs.
4. **Aerodrome.**
   - `in_apr_today_price`: the voter APR at TODAY's AERO price, beside the headline (which converts Q0 rewards at the
     Q0 mean price). It is a VERIFIED FINDING when AERO moved more than 5% over Q0 and Coinbase confirms CoinGecko's
     Q0 mean within 2%. CHECK when Coinbase is missing or apart; N/A (recorded, price flat) otherwise.
     `check_offline_items.py aerodrome_price_q0` prints both sources' daily prices.
   - `in_voter_apr_epoch` uses ONE price on both sides: the week's mean.
   - in_revenue / in_voter_apr_epoch are MATURING until 2026-10-10 while DefiLlama has not published a day of the
     latest epoch; the row names the missing day. After that date they are CHECK.
5. **Pendle.**
   - **Why the headline read 11.55% against 1.436%.** On 1cb8f6b the cell was (Q0 sum / q0_events) × 365.25/14 /
     (shares + IF(ISNUMBER(virtual "now"), virtual, 0)). Two things differed from the Python:
     - q0_events counts only epochs above 0, so the 0 epoch dropped out of the divisor (×1.2: 131,975 vs 109,979);
     - a blank virtual "now" silently dropped ~170M virtual sPENDLE from the stake (×6.7).
     1.2 × 6.7 ≈ 8.04 = 11.55 / 1.436. Since 2ed3ed9 the headline is a Python view (`token_yield_epoch_mean_pct`).
     `test_real_pendle_headline_cell_equals_the_python` holds the cell to `credibility.epoch_headline` on the real dump.
   - **A 0 is not yet published.** `credibility.epoch_publish_lag` reads the run log for the first run that read each
     epoch above 0. The longest observed (first non-zero − epoch end) is Pendle's lag. Inside the lag, or with no lag
     observed, an epoch read at 0 is "unpublished": named and left out of the mean. A 0 still standing after the lag
     counts as 0. The fetcher now logs every stored epoch each run (`STORED PER EPOCH:`).
     `pendle_epoch_table` prints each epoch's first non-zero date.
   - **Emissions = gross issuance.** Pendle declares `emissions_from_metric: gross_issuance_tokens`, so the emissions
     column, in_emissions and a3_net_absorption read the declared 2%/yr × total supply (~1.42M over Q0, a ceiling).
     Against the mainnet gauge's ~77K it reads CHECK until `check_offline_items.py pendle_emissions_q0` shows where
     the difference is. That probe prints the Q0 totalSupply change, the mints, the GaugeController's inflows by
     sender, and each chain's gauge payouts with the L2 share (gauges from pendle-core-v2-public @87685c8).
6. **Maple.**
   - Both mints were sent by the Safe 0x6b1A78C1943b03086F7Ee53360f9b0672bD60818 (execTransaction). Maple's address
     registry (maple-labs/address-registry @3df2052, MapleAddressRegistryETH.md L6, L10) names that Safe its
     securityAdmin AND its recapitalizationClaimer. Maple's Blockworks filing calls it a 3-of-6 multisig. The governor
     is a different contract (governorTimelock 0x2eFFf887, L5).
   - RecapitalizationModule.claim() accepts only the claimer, so the documented path is
     Safe → module 0x5dfe0460 (L294) → SYRUP.mint. Each mint is a claim of the accrued MIP-009 schedule (×100 by
     MIP-010), not a separately authorised mint.
   - That attribution stays UNVERIFIED until `maple_syrup_mints` decodes the Safe's inner call. The probe now prints
     it, the Safe's owners and threshold, and `currentIssuanceRate()` read ON THE MODULE. The earlier read hit the
     Safe.
   - totalSupply moved by exactly the mints. The 2.02 SYRUP sent to 0x0 did not reduce it: transfers, not burns.
   - Q0 gross issuance is 0; the trailing 12 months are 44,401,784 (~3.7%).


## 11av. Jake's run 2026-10-08 11:27: closing the five, and the lapsing MATURING rows

That run signed off 10 projects. Five were OPEN: Ethereum, Chainlink, Near, Sky and Pendle (156 PASS / 12 CHECK).
Four MATURING rows lapsed on 10-08 or 10-09. Each now has a verdict, not a new date.

**Every item has a fixture test: `tests/test_signoff_fixtures.py`.** Each test writes a store shaped like the real
one, runs `credibility_report.evaluate(project, asof=..., narrow=True)` and asserts the verdict on the tab. That is
the same build and evaluation path as `python credibility_report.py`. `narrow=True` builds only that project
(~1s instead of ~25s).

1. **Ethereum a4_net_change.**
   - The judged figure (`ours_py`) is OUR issuance − burn, summed over exactly the days the reference uses: the days
     of the headline's own net view. a4_net_change_pct follows.
   - The working prints the per-day figures for both.
   - Jake's exact failing shape was not reproduced here: the fixture passes on both the old and the new code. The
     test pins the arithmetic instead.
2. **Chainlink in_issuance: stock vs flow.**
   - Two log scans run over the 27 non-circulating wallets: `noncirc_out` and `noncirc_in`. Each is reconciled to
     balanceOf to the wei before anything is stored.
   - The balance-derived release is judged against the scanned net outflow over Q0 (`q0_net_flow`, 1%).
   - CoinGecko's d(circulating) moves in steps. It is recorded as `in_issuance_coingecko`, N/A.
   - When both the release and the scan are negative (tokens RETURNED into the wallets),
     a1_fees_issuance is a VERIFIED FINDING: "n/a — net release negative in Q0" (`finding_when`).
   - `check_offline_items.py chainlink_noncirc_transfers` lists every transfer (tx, date, from, to, amount). It then
     sets archive balanceOf of all 27 wallets at the window's ends against the scanned flow, to the wei.
3. **NEAR.**
   - in_buyback had "CHECK (no figure)" because `use_views` sat inside `args`, so the row read the raw store. It now
     reads the views. The figure is the 30 days to 10-07: Σ d(combined balance) × that day's price.
   - a3_net_absorption's emissions input is the gross issuance row (`config.EMISSIONS_ARE_ISSUANCE`).
   - in_locked is judged against NearBlocks `/v3/validators/info` `total_stake`, the sum of current_epoch_stake
     (Nearblocks/nearblocks @e9e74695). Tolerance 2%, one call a day. Stored as `locked_tokens_nearblocks`.
4. **Sky.**
   - The headline VALUE is the USDS-farm row's own arithmetic: the last 28 days' USDS rewards over the farm's stake
     at the same days' price (`staking_yield_usds_farm_28d_pct`).
   - The Q0 economy-wide 2.72% stays as the second figure.
   - `sky_farm_rates` prices lsSKY at SKY.
   - The USDS farm's `rewardRate()` is read daily (`usds_farm_reward_rate_usds_per_s`). It is a second reference row,
     `in_apy_usds_farm_rr`, tolerance 15%.
5. **Pendle.**
   - The headline (the Q0 epoch average) inherits ONLY if its own arithmetic reproduces Pendle's APR epoch by epoch.
     The arithmetic is distributed × 365.25/14 / (sPENDLE + virtual).
   - This is checked on `in_epoch_reproduction`, tolerance 10%. Pendle's per-epoch APRs are stored as
     `pendle_epoch_apr_published`.
   - Epochs before virtual sPENDLE was first read are named, not judged.
   - **Jake's 5c decision (2026-10-09).** The headline is the MEAN of the Q0 per-epoch APRs, each over its own stake,
     not mean distribution / today's stake (`build_workbook._epoch_mean_views`, `token_yield_epoch_mean_pct`). Each
     epoch's APR is OURS where our stake exists, from ~2026-09-11 when virtual sPENDLE history starts. Before that it
     is PENDLE'S PUBLISHED APR for the epoch, which embeds that epoch's own stake; project-first figures are the rule.
     `credibility.epoch_apr_table` is the one computation the view, the rows and the probe all use. The headline's
     inputs:
     - `in_epoch_reproduction`: every epoch we compute against Pendle's APR, 5%.
     - `in_epoch_mean`: the mean of our APRs against Pendle's, over the epochs we compute, 5%.
     - `in_epochs_first_party`: how many epochs use Pendle's APR. While any do, it is a DOCUMENTED LIMITATION
       ("first-party APR only, virtual sPENDLE history starts 2026-09-11"). It lapses to N/A by itself once our stake
       covers every Q0 epoch (`verdict_when`).
     So the headline reads DOCUMENTED LIMITATION (inputs) until ~2026-12-10, then PASS (inputs).
   - `check_offline_items.py pendle_epoch_table` prints the table and the headline arithmetic.
   - The legacy vePENDLE `totalSupplyCurrent()` is read daily, and archive-read by `archive_backfill.py`.
   - The gauge scan runs on its own tier: `explorer_gauge`, 280s scan budget. A timed-out tier now names the series it
     gapped.
   - in_emissions is judged against the gauge scan, tolerance 25%. It is no longer MATURING.
6. **Aerodrome: what voters were paid, from state, weekly.**
   - `fetch/aero_voter.py` (source `aero_voter`, tier 2, 180s) runs the same computation as `aerodrome_voter_rewards`
     for the last COMPLETE epoch. It stores each epoch once, dated the epoch's start:
     - `voter_rewards_onchain_usd`: priced at the epoch's END
     - `voter_rewards_unpriced_count`
     - `voter_rewards_onchain_apr`: × 52 / (totalWeight × price)
   - It also stores `voter_total_weight_tokens` every day (one eth_call).
   - in_revenue is DefiLlama over the SAME epoch's seven UTC days against the on-chain figure, 10%.
   - The headline's denominator is now Voter.totalWeight, the votes cast. It was veAERO.supply(), AERO locked.
   - `in_voter_apr_epoch` reproduces the headline's own arithmetic on each epoch: DefiLlama $ / the week's mean price
     × 365.25/7 / totalWeight. It is judged against the on-chain APR, tolerance 10%.
   - Its working prints the HEADLINE's numerator and denominator, and how far each factor sits from the epoch's:
     revenue/week, price, stake.
   - The rebase is its own labelled row, `in_rebase_apr`: tokensPerWeek × 52 / totalWeight, ~2.5%. It is N/A,
     recorded: a separate stream, in neither figure.
   - Unpriced tokens are reported by count, with each token's raw amount. A dollar share needs the price that is
     missing.
7. **Maple.**
   - in_buyback is a DOCUMENTED LIMITATION: the page is first-party, and there is no on-chain trail into the
     documented wallets.
   - The probe's "page buybacks" column read 0 every month because the table's index held only months WITH an
     inflow. The page's months now join. A month the page doesn't cover prints n/a. 0x0 transfers get their own
     column, `minted (0x0)`.
   - **SYRUP is minted into the treasury.** The RecapitalizationModule issues MIP-009's 3-year 5%-a-year emission,
     carried into SYRUP by MIP-010. Sources: maple-labs/maple-docs @bd3647e9,
     technical-resources/syrup/recapitalization-module.md and syrup-tokenomics/README.md L7-15, which gives an
     expected supply of 1,267,875,000 by September 2026.
   - Maple now declares `burn_mechanism: no_burn`, so `gross_issuance_tokens` derives as d(total_supply). The mints
     are gross issuance into non-circulating: FDV moves, free float doesn't.
   - The emissions N/A wording says so.
   - `check_offline_items.py maple_syrup_mints` lists every mint and burn with the contract each mint transaction
     called. It sets minted − burned against archive totalSupply to the wei, and reads the module's
     `currentIssuanceRate()`. A rate of 0 means the schedule has ended.
   - A burn found there refutes the no_burn block.
8. **Plume probe crash, "[Errno 13] Permission denied: '.'".** `.env.example` ships `TOKEN_METRICS_SOURCES=` (empty).
   `Path("")` is `.`, which exists, and opening a directory on Windows raises Errno 13. An empty value now falls back
   to `sources.yaml`, and the same applies to `TOKEN_METRICS_CACHE`.

Only Aethir's two rows (until 10-17) are still MATURING.


## 11au. Jake's run 2026-10-08 08:37: closing the rest

That run signed off 4 projects (Aethir, Morpho, Uniswap, GEODNET); 11 were OPEN. Item by item:

1. **Ethereum a4_net_change: identical days.** Both sides now use the days that hold issuance, burn AND staked ETH.
   - The headline view requires `beacon_chain_eth` (`net_change_common_days.require`).
   - The reference reads the read-time views (`use_views`) instead of the raw store, so it can't see 9 days while
     the headline sees 6.
2. **Chainlink in_issuance.** LINK's total is a constant 1bn: `uint public constant totalSupply = 10**27`
   (smartcontractkit/LinkToken @8fd6d624, contracts/v0.4/LinkToken.sol L10). So issuance = d(CoinGecko circulating)
   over Q0 (`delta_q0`), and a1_fees_issuance follows.
3. **Hyperliquid.**
   - in_fees is a VERIFIED FINDING: $1.067bn of fees against $843M of ASXN revenue. The ~21% difference is builder-code
     and HIP-3 fees, which don't reach the protocol.
   - The full-curve emissions row (−21.3%) is a VERIFIED FINDING: ~21% of stake is inactive. It sits beside the
     active row (−5.3%, PASS).
   - The 16-day ASXN HyperEVM lag stays flagged as it is.
4. **NEAR.**
   - in_buyback had "no figure" because credibility read the raw store, while NEAR's buyback is a read-time view
     (Δ of the three wallets' balance). It now reads the views, and so do the A3 rows it feeds.
   - Gross burn: the BigQuery header burn is PRIMARY (`a4_burn_metric`), so burn yield and crossover use it.
     Fees × 0.70 is the labelled cross-check: a VERIFIED FINDING that ~7,373 NEAR (~11%) of the burn is non-gas.
   - in_emissions is N/A on the issuance route, the same call as completeness.
5. **Plume in_circ "no figure": not reproduced here.** The entry is ready and the metric is in scope; supply.plume.org
   is unreachable from the build environment. `check_offline_items.py plume_supply_read` walks each step the run takes,
   in order: robots, back-off, cache, the GET (status and first bytes), the json_path, then what metrics.db, run_log,
   gap_report and review_queue hold. The first step that says no is the cause.
6. **Maple / Ether.fi a3_net_absorption.** Each has an N/A emissions input row, sourced in
   `config.EMISSIONS_DECLARED_ZERO`. `resolve_alias` counts it as the declared zero.
7. **Fluid in_circ: like-for-like.** The IGP-137 custody (0xcabebc7f…, 5M) is its own series,
   `noncirculating_igp137_tokens`. It is subtracted from ours and added back for the CoinGecko comparison
   (`coingecko_counts`). 77.964M + 5M = 82.96M against 83.70M is −0.9%. The 5M is shown on the definitional-gap row
   (in_circ_gap). The remaining ~0.73M isn't attributed.
8. **Sky.**
   - a3: the headline is the USDS farm's revenue yield, judged against the farm's APY on Sky's page (4.61%).
     in_yield_economy_wide (holders revenue / all staked SKY, ~2.72%) is the labelled second figure.
   - The SKY farm's rate is read on chain: `rewardRate()` on REWARDS_LSSKY_SKY is stored daily as
     `sky_farm_reward_rate_tokens_per_s` (endgame-toolkit @db3cc6a4 StakingRewards.sol L44). APR = rate ×
     31,536,000 / staked, 6.58%, matching Block Analitica's 0.06575.
   - The USDS farm keeps the 28-day paid method, because its period ends today.
   - in_rewards_sky_farm_ba is retired. Weekly releases against a daily accrual never share 20 days.
9. **Ether.fi a3: DOCUMENTED LIMITATION.** The Accountant's rate (7.91%) is first-party on-chain. The 14.21% top-ups
   figure is built on the retracted decomposition. in_yield_q0 and in_apy_published stay.
10. **Pendle.**
    - The headline stays the Q0 epoch average, the same trailing window as every other project (Jake, 2026-10-08).
    - It inherits the verdict of in_epoch_apr (the `inputs` pattern). That row compares our distribution for the
      2026-09-08 epoch against Jake's 82,545 PENDLE, both × 26 / (sPENDLE + virtual) that day (`epoch_apr`). An
      average is never set against one epoch.
    - The merkleDistributor 0x33305665… (249,852 PENDLE over 120 days, from EOAs) matches none of the epochs, so the
      on-chain route is recorded as NOT IDENTIFIED.
    - in_emissions is MATURING to 2026-10-09: the gauge scan was at 84%.
11. **Aerodrome.**
    - in_voting_power is judged by Voter.totalWeight(), 1,021.4M (ours 1.029bn).
    - Jake's 881,100,168 is now in manual_references.csv as `in_voting_power_page`: recorded, pending a check of the
      page's label.
    - `aerodrome_voter_rewards` batches through Multicall3.aggregate3 (0xcA11bde0…, mds1/multicall @b667d67e).
      Alchemy refused the JSON-RPC batches. The code at the address is checked before use.
    - a3 and in_revenue are MATURING to 2026-10-09 for the re-run.
12. **UTF-8.** completeness_report's .md, logcache's atomic writes, the BigQuery .sql dump and recalc's macro all
    name UTF-8. A test fails any `open(..., "w")` without an encoding.
13. **Price backfill before 2025-09-12: skipped (Jake, 2026-10-08).** Q0 and the trailing year are already priced
    from 2025-09-12. If it is ever wanted: a second price source in `price_usd` trips the measuring-point guard, so it
    needs a declared handover per project (DefiLlama before 2025-09-12, CoinGecko after).

```bash
python check_offline_items.py aerodrome_voter_rewards sky_farm_rates plume_supply_read
python token_metrics.py            # stores the SKY farm's rewardRate; Pendle's gauge scan finishes
python credibility_report.py
python completeness_report.py      # writes its .md as UTF-8
```

## 11at. Corrections to the sign-off round, 2026-10-08

**Maple: the trail shows SYRUP leaving, not buys settling.** 2968b16 read "buys settle OTC via 0x83971edb". That
was the wrong direction.
- What was seen: SYRUP LEFT the SSF trail. 0x58be0049 → 0x99f03ca0 → 26 recipients, among them 0x83971edb, which
  also relayed Ether.fi's 5M ETHFI Binance withdrawal.
- That points to SELLING through an OTC-like counterparty. It is an inference, not proof.
- It is recorded beside the open question `ssf_selling_question.trail_inference` ("does the buyback-funded SSF sell
  SYRUP?"). `a3_buyback_locked`, `in_buyback` and the `maple_buyback_inflows` probe are reworded to match.

**Sky in_revenue: not single-source.** Sky's own financials table has a monthly Revenue line, Jan–Sep 2026: 26.05M,
52.25M, 45.73M, 34.32M, 35.56M, 37.31M, 36.64M, 31.85M, 30.80M.
- Those months are stored as `revenue_usd_reported` (manual_overrides.csv, read_by Jake).
- They judge Block Analitica's P&L revenue on the same gross basis: Jan–Sep summed, tolerance 2%.
- October (834.58K to date) is month to date and not compared.
- DefiLlama's revenue is net of the savings rate. It stays as a labelled cross-check row,
  `in_revenue_defillama_net`, "N/A (cross-check …)", never the judge.

## 11as. Sign-off round, 2026-10-07 (overnight): finish-line categories, per-project status

**The finish line.** A project is SIGNED OFF when every Credibility row is one of these:
- PASS
- N/A
- DOCUMENTED LIMITATION (no free second source: reason, evidence, what would upgrade it)
- MATURING (fills by a stated date)
- VERIFIED FINDING (our figure is confirmed; the gap is a real-world fact, with evidence)

Anything else is OPEN.
- The Credibility header shows each category's count, OPEN and the sign-off per project.
- `credibility_report.py` prints LIMIT / MATUR / FIND / OPEN / SIGN-OFF.
- `completeness_report.py` has a sign-off section (skip it with `--no-signoff`).
- `config._c_lim` / `_c_find` / `_c_mat` refuse to build a row without its evidence.
- A same-source match shows its `fresh_label` (a category) in place of FRESH-only.
- A manual reading of a different quantity can be recorded *beside* a headline (`manual_refs` "beside"). NEAR's 4.56%
  pool APR, which is NET, is recorded that way.

**What moved (static rows: 37 OPEN before, 0 after; computed rows are judged by the run):**
- **Fluid:**
  - On-chain primary. The three Avocado wallets are not the team's, so they count as circulating.
  - Buyback-locked: VERIFIED FINDING (halted 2026-05-11).
  - Emissions and revenue: DOCUMENTED LIMITATION. Base logs are paid, and there is no Fluid revenue API.
  - Tokenomist's figure is recorded beside the row.
- **Hyperliquid:**
  - The release is now d(tokenDetails circulating) + the Assistance Fund buyback. CoinGecko's delta is kept as
    `pool_release_tokens_coingecko`.
  - 2026-10-07's +3,736,300 is the October contributor payout (~3.75M sold OTC) against a 9.92M/month projection:
    VERIFIED FINDING.
  - in_circ: DOCUMENTED LIMITATION.
  - in_fees is judged against ASXN's 30-day annualised revenue (internal use only).
- **Aethir:** in_locked, in_arr and customer revenue are MATURING until 2026-10-17 (ten daily runs of
  `aethir_distributor_match`). in_circ: DOCUMENTED LIMITATION (the dashboard follows the published schedule).
- **NEAR:**
  - The validator yield is now gross vs gross. The reference is BigQuery's actual mint over 28 days × 365/28 × 90%,
    divided by stake.
  - in_emissions is judged against the BigQuery mint × 0.9.
  - in_revenue is judged against NearBlocks txn_fee × 0.7 × price. DefiLlama's adapter is Allium fees × 0.7.
  - in_locked: MATURING to 2026-10-08 (a hand reading of total staked).
  - The probe's stats/balance limit is now 365.
- **Pendle:**
  - in_emissions is judged against PENDLE out of the mainnet GaugeController (new log scan `gauge_pendle_out`,
    seeded from block 0).
  - in_revenue: DOCUMENTED LIMITATION.
  - The yield row: DOCUMENTED LIMITATION (shared API input) until `pendle_spendle_rewards_onchain` finds the funded
    merkle contract.
- **Maple:**
  - Buyback-locked: DOCUMENTED LIMITATION (awaiting Maple; SSF wallets unpublished). See 11at for the trail.
  - Revenue: VERIFIED FINDING. Maple counts OTC, Basic-strategy and Base revenue that DefiLlama doesn't read
    (Blockworks categories).
  - in_buyback: MATURING to 2026-10-08 (`maple_buyback_inflows`).
- **Ether.fi:**
  - in_buyback: VERIFIED FINDING. The programme is declared, but nothing has been bought into 0x2f53… since
    2026-04-01; the only buys are 1.05M via Uniswap v4 in 2026-09.
  - in_revenue: DOCUMENTED LIMITATION (`etherfi_withdrawal_fees` reads the fees).
  - The holders_revenue_usd closure now carries its native evidence, so it is no longer a BUG.
- **Morpho:** a2_emissions is a VERIFIED FINDING. Morpho moved new programmes to Merkl in July 2025 (14,634 scheduled
  over 90 days); the rest of the claims are legacy URD rewards.
- **Aerodrome:**
  - in_locked (AERO locked, 1.053bn) and voting power (881.1M, decayed) are separate rows. in_locked is a DOCUMENTED
    LIMITATION.
  - a3 and in_revenue are MATURING to 2026-10-08 (`aerodrome_voter_rewards`, read from state).
- **Sky:**
  - in_revenue: superseded in 11at (Sky's published monthly revenue is the reference; DefiLlama a cross-check).
  - NPS: DOCUMENTED LIMITATION (Sky's own accounting on both sides).
  - `sky_farm_rates` reads the farms' rewardRate.
- **Ethereum:** a4_net_change now prints ours leg by leg beside the reference. in_price_llama is N/A (CoinGecko relay;
  Coinbase is the check).
- **Chainlink, GEODNET, Plume, Uniswap:** as in section 1 of the round (Chainlink on-chain primary; GEODNET revenue
  from the burn reports; Plume issuance 0 from supply.plume.org).

**Housekeeping:** zero-value lookalike transfers are dropped in `balance_flow` as well as the log scan.

```bash
python credibility_report.py                       # per-project SIGN-OFF column
python completeness_report.py                      # sign-off section at the end
python headline_diff.py chainlink_onchain_preview
python archive_backfill.py --project Chainlink     # plan; then --run (also Fluid, Ether.fi, Aerodrome, Maple)
python check_offline_items.py pendle_spendle_rewards_onchain maple_buyback_inflows etherfi_withdrawal_fees
python check_offline_items.py aerodrome_voter_rewards sky_farm_rates hl_pool_release_compare
python token_metrics.py                            # the normal run (Pendle gauge scan seeds from block 0)
```

## 11ar. Jake's probes16 + run 2026-10-07 20:57: Ether.fi Accountant, Aerodrome managed locks, trails

**Ether.fi: the 32% was an artefact of cross-chain shares.** sETHFI is a Veda BoringVault.
- Its "burns without outflow" are shares bridged out by the LayerZero Teller; the ETHFI stays behind. That made
  ETHFI held ÷ Ethereum-only supply rise with no reward.
- The yield is now the vault Accountant's `getRate()` growth over the trailing year (headline) and over Q0
  (`in_yield_q0`). The Accountant is found from the Teller's `accountant()` and must report the same `vault()`.
  `fetch/share_price.py` reads it today and 365 / 180 / 90 days back, as `sethfi_accountant_rate`.
- Reference: the genuine reward top-ups over the ETHFI staked, at 25%. The stake leaves out ETHFI sitting in
  strategy positions, so it reads high.
- Decomposition classes: `bridge` (burn with no asset out, or mint with no asset in), `strategy` (PositionManager
  0xCF413A19…) and `oft` (OFT lockbox 0xe0080d2F…). None of the three is a reward. probes15's queue / round-trip
  classes are retracted.
- Funding mix: rebuilt from genuine top-ups only (7.63M). The "stakers paying stakers" note is retracted.
- The OFT lockbox's 31.08M is counted once: inside Ethereum's total, never subtracted, and L2 supply is never added.
- `etherfi_accountant` prints `getRate()` at the same three blocks as the archive probe.

**Aerodrome: on-chain primary.**
- Circulating = total − the six filing wallets' liquid AERO − the AERO they deposited into managed veNFTs.
  `filing_managed_lock_tokens` is read daily by `fetch/ve_managed.py` (`idToManaged` / `weights`).
- Free float subtracts the lock less those managed locks, so they come out once.
- CoinGecko's basis is the PERMANENT locks. Like-for-like = ours + the liquid filing wallets vs CoinGecko + permanent
  locks − the managed locks.
- The genesis 95M constant is superseded and kept under `was`.

**Maple:** `maple_ssf_trail` compares the 0x58be0049 Safe's owners with 0xd6d4's signers. It then follows the EOA
0x99f03ca0 (27.5M SYRUP on 2025-10-29) and tests the combination from 2025-10-29. If the trail fragments, it reports
"awaiting Maple".

**Fluid:** the five recipients are not vesting contracts.
- The liquidity layer and the Uniswap pool are counted as circulating (recorded).
- `fluid_avocado_owners` decides the three Avocado wallets. Team-owned wallets get wired, and Fluid then becomes
  on-chain primary (rule in config).

**Pendle:** spendle/data revenues stop at the 2026-04-07 epoch, so they can't serve as Q0 reference. `in_revenue` is
CLOSED as single-source.

**Hyperliquid pool release:** `hl_pool_release_compare` prints both sides day by day from `metrics.db`.

## 11aq. Jake's probes15 + run 2026-10-07 19:07: roots A-O, Ether.fi reclassification, new wallets and probes

**New references (code):**
- **GEODNET `in_circ` (A):** judged by the bridge reconciliation. Polygon NTT custody must be at least the Solana
  supply, with no slack, and the filing must list our wallets (`bridge_reconciled`, recorded 2026-10-07). The static
  462M is its own `in_circ_static` row, "N/A (recorded, stale)".
- **Hyperliquid `a4_pool_release` (B):** the daily change in tokenDetails circulating plus that day's AF buyback, on
  our own days (`daily_delta_plus_flow`). tokenDetails is read forward-only, so the per-day mean on the shared days
  is carried over our days, and the source text gives the count.
- **NEAR (C):** `in_buyback` is relabelled to the three-wallet balance change x price over the 30 days to 2026-10-07.
  It is judged against revenue.near.org's 30-day net revenue, $2.08M (Jake). The A3 "Buyback to LOCKED supply" cell
  now shows the held balance for any 'hold' destination; the effective float is unchanged.
- **Sky (F, G):** `a4_gross_burn` is judged month by month. September is set against the spell's 2,860,943.76 SKY at
  0.01% (`in_burn_spell_2026_09`). `in_revenue` sets DefiLlama monthly against Block Analitica P&L revenue, a new
  stored series, `revenue_usd_ba`.
- **Chainlink `in_revenue` (H):** DefiLlama against our aggregator core scan, over the same 30 days.
- **Ethereum `a4_net_change` (J):** the issuance curve uses each day's staked ETH. On a day without DefiLlama or a
  price, the burn is the BurntFees counter, differenced.
- **Aerodrome `in_emissions` (K):** V / T reads begin after the oldest archive epoch, so the first read within a week
  of the flip is used, and the source text says so.
- **Aethir APRs (N):** "N/A (recorded)". Our APR is CLOSED and no verified pool reward-rate read exists.
- **Pendle (M):** CLOSED. The docs (@3cc3658d) have only `/v1/spendle/data` and `/v1/spendle/:address`.

**Ether.fi:**
- 0xf4e147db… is classed as a WITHDRAWAL QUEUE: its burns and the vault's payouts to it are one withdrawal.
- 0xcf413a19… and 0xe0080d2f… are classed as ROUND TRIPS: vault ETHFI goes out to them and comes back.
- Neither class is a reward. Both are left out of `sethfi_reward_tokens_reconciled` and held still in the new
  `sethfi_aps_reward_only` walk, which is A3's reference. The all-classes walk is still stored.
- These classes come from the flows in Jake's trace. `etherfi_contract_ids` confirms them by verified name and decodes
  the two sample selectors. `etherfi_vault_archive` is the decisive outside check: convertToAssets at ~365d, ~180d and
  today.

**Wallets wired (non-circulating):**
- Ether.fi treasury reserve Safe 0xe4439b1d…: 3-of-7, five owners shared with the buyback Safe, 20M ETHFI from
  Treasury.
- Fluid IGP-137 custody 0xcabebc7f…: 5M. Fluid stays on CoinGecko until `fluid_vesting_recipients` classifies the
  five earlier recipients.

**Morpho:** Merkl's leg is recorded: 14,634 MORPHO over 90 days, 11 campaigns. The URD leg dominates, so the row needs
Morpho's rewards API, read on Jake's machine.

**New probes:**
- `etherfi_vault_archive`, `etherfi_contract_ids`
- `fluid_vesting_recipients`
- `maple_ssf_partial`: identity, matching runs, divergence recipients, v3 LP decomposed, combinations
- `aerodrome_managed_venfts`: deposits into managed NFTs, every managed NFT's owner, CoinGecko's basis
- `pendle_epoch_revenues`: Pendle's own per-epoch revenues, raw, before anything is wired

## 11ap. Jake's probes14 + run 2026-10-07 18:11: like-for-like rows fixed, Ether.fi references, filing wallets

**Like-for-like rows.**
- `in_circ` now sums from the BUILT rows (`now_sum`). The on-chain circulating is a read-time view, absent from the
  store, so the store-only sum read "CHECK (no reference)".
- The gap row is "N/A (recorded)". A bare N/A means "empty by design", so a figure in it read as a contradiction.
- Ether.fi 797.21M + 168.14M = 965.35M = CoinGecko, and Uniswap matches the same way: both should now PASS.

**Ether.fi trailing yield: the yield is real, the references were wrong.**
- The A3 reference is the year's own assets-per-share, rebuilt from the Transfer logs. The decomposition stores it
  daily as `sethfi_aps_rebuilt`.
- It is compared like-for-like as a sum of daily returns: ln(1.3247) = 28.1% vs the headline's 27.74%, at 5%.
- Q0 realised stays its own row. The app's APY is "N/A (recorded)" (manual page `record_only`): a forward rate, never
  judged against a realised year.
- The year's funding mix is in `PROTOCOL_YIELD["Ether.fi"]["funding_mix_2026_10_07"]`. Only the old programme's
  6.49M was bought. The 14.12M of burns without an asset outflow are stakers paying stakers.
- `etherfi_sender_trace` covers the two unlabelled senders (0xcf413a19…, 0xe0080d2f…) and the three holder-report
  wallets: Safe owners against the buyback and top-up Safes, plus their ETHFI sources. It also samples the burns
  without an outflow: who burned, whether the burner was paid later, and the called function.

**Filing wallets (non-circulating, source = each project's Blockworks filing):**
- **Aerodrome:** Flight School, Buyback/Locked Funds, TGE Incentives, Velodrome Foundation airdrop. The Team and
  Public Goods wallets were already subtracted. Their veAERO locks are read by `aerodrome_filing_wallets`, which also
  builds the on-chain circulating and the like-for-like.
- **Morpho:** the Association Master and Ops Safes, two Contributor Grants SAFEs and two Operative SAFEs (Ethereum;
  other chains not on file). On-chain becomes primary once the like-for-like holds.
- **GEODNET and Fluid:** the filings confirm the wallets already subtracted.
- **Pendle, Uniswap, Hyperliquid and the Ether.fi holder-report wallets:** `blockworks_wallet_balances` reads what
  each filing address holds, from the cached filings, before anything is wired.

**Maple:** `maple_ssf_candidates` now also takes 0xd6d4's recipients. It counts stSYRUP as SYRUP and tests single
wallets, every pair of the ten largest and the whole set. LP positions are not decomposed.

**Fluid / Morpho:** `fluid_igp137_wallet` finds where IGP-137's 5M went after the Team Multisig. `morpho_merkl_campaigns`
pro-rates Merkl's MORPHO campaigns to the window.

**Aethir:** re-run `aethir_distributor_match` after about 10 daily runs. It now prints each candidate's code, name and
symbol first.

## 11ao. Jake's run 2026-10-07 17:08: like-for-like circulating, Ether.fi yield, Maple SSF, Aethir distributors, filings

**Circulating, like-for-like.** Where our on-chain figure is primary and stricter than CoinGecko, the spec names the
subtracted balances CoinGecko still counts (`coingecko_counts`). Ether.fi and Uniswap declare
`noncirculating_holding_tokens`. The `in_circ` row compares CoinGecko against ours plus those balances on the same
day, still at ±2%, so a gap left beyond that is a real disagreement. `in_circ_gap` reports the balances themselves:
the definitional gap, marked N/A because it is recorded, not judged. Sky (CoinGecko vs our total) and Pendle
(free float + sPENDLE) already compared like-for-like.

**Ether.fi trailing yield (25.97% vs the share price's 9.95%).**
- The headline was the year's reward tokens over the year's AVERAGE stake. In a vault that grew, with its top-ups
  landing late (Aug–Sep), that over-weights them.
- The headline is now time-weighted: each day's reconciled rewards over that day's stake, summed. That is what a
  holder earned, and it tracks the share price. The same rule applies on Credibility (`trailing_token_yield`).
- `python check_offline_items.py etherfi_yield_reconcile` replays the cached sETHFI logs offline and prints:
  - the decomposition by class;
  - top-ups by sender, with the old 0x2f53 programme apart;
  - top-ups with a mint-only transaction within 20 blocks (a split deposit);
  - d(assets-per-share) x average shares, set against the reconciled total and the top-ups;
  - the yield three ways: over the average stake (old), time-weighted (new) and assets-per-share growth.
- It also prints what metrics.db stores for each series.

**Maple:**
- `maple_dao_vs_ssf` found 0xd6d4 is NOT the SSF.
- `python check_offline_items.py maple_ssf_candidates` rebuilds daily SYRUP balances from each wallet's own transfers,
  for 0xa9466eab and every address it sent SYRUP to. Each is tested against the page's SSF series on every shared
  day; a MATCH is all days within 2%.

**Aethir:**
- `python check_offline_items.py aethir_distributor_match` sets each candidate's daily Arbitrum outflow against the
  daily rise in the dashboard's checkerRewards, cloudHostRewards and edgeRewards. Those rises are taken from the
  stored cumulatives, because the emissions_* rises are read-time views.
- A MATCH is the sums within 10% and a daily correlation of at least 0.8.
- A matched candidate is wired as that stream's on-chain released-reward measure.

**Blockworks filings:** the probe now lists every address in each filing, with its section, question label and the
fields beside it. Searching for a "wallet" label found none, because the section is "Labelled Unissued & Operational
Token Wallets". Cached filings are reused, so no new requests are made.

## 11an. The full sweep — first-party wallet lists, on-chain circulating, open rows — 2026-10-07 (evening)

**Convention (Jake):** circulating = total supply minus treasury, team/investor unvested, foundation, operating
reserves and burned tokens. Staked tokens stay IN (they come out only for free float). **Hierarchy:**
1. The on-chain count, using wallets the PROJECT documents (docs, its Blockworks filing, repo, supply API).
2. The project's own published figure, as a cross-check (primary only where it documents no wallets).
3. CoinGecko, as a last resort.

**On-chain is now primary for:** Uniswap, Sky, Ether.fi, GEODNET, Pendle and Aerodrome.
- **Uniswap** also subtracts the wallets in its docs ("Miscellaneous Addresses"): four treasury vesting contracts and
  the airdrop merkle distributor.
- **Sky:** its own definition counts the Pause Proxy treasury as circulating. We record that in `project_definition`
  but don't follow it.
- **Ether.fi:** the wallets labelled in its Blockworks filing.

**CoinGecko stays primary, with the reason in each spec's `decision`:**
- **Chainlink:** our set is now the 27 wallets of its 2022 post. Its current list can't be read from here.
- **Fluid:** the dedicated wallet for IGP-137's 5M FLUID is unpublished.
- **Morpho:** the vesting contracts aren't found.
- **Maple:** no address is found for the Syrup Strategic Fund.

New wallets live in `config._NONCIRC_WALLETS_FIRST_PARTY`, one row per wallet: key, address, chain, symbol, source
URL, date read and role. Never wired:
- Aerodrome's `team` role address (no document says it holds tokens).
- Fluid's delegateCall-only distributor 0x9d694b7f….
- 0x9Afb8C17…, listed under ReserveContract.

**Headline moves:** Jake runs these against metrics.db:

    python headline_diff.py --only onchain_sweep               # Uniswap / Sky / Ether.fi: CoinGecko -> on-chain
    python headline_diff.py --only geodnet_onchain             # GEODNET: Blockworks' static 462M -> on-chain
    python headline_diff.py --only chainlink_onchain_preview   # a PREVIEW (read the sign reversed)

**Blockworks filings probe:** `python check_offline_items.py blockworks_filings`. It reads Blockworks' documented API:
- List a project's latest filings: `api.blockworks.com/v1/ttf/filings?tickers=<T>&latest=true`.
- Fetch one filing: `/v1/ttf/filings/<id>`.

It prints every row of each filing's wallet question and caches the JSON under `.cache/blockworks/` (a cached answer
is reused). Nothing is stored. Attach the cache or paste the section, and the rows are wired with the filing as their
source.

**Completion sweep (part 2):**
- Before: 26 static CHECK and 14 UNVERIFIABLE rows. 14 of the CHECKs already had Jake's readings on file, which
  decide them at build time.
- Closed in code (a free second source we already read):
  - **NEAR `a3_buyback_locked`:** the RPC balance against NearBlocks' daily close of the same three wallets.
  - **Sky `in_buyback`:** 90 days against the rise in Block Analitica's cumulative SKY bought.
  - **Morpho `in_supply`:** blue-api's listed-market borrow (utilisation x supply) against DefiLlama's borrowed,
    stored as `borrowed_usd_llama`. That route only adds a reference metric, so blue-api still owns supply and
    utilisation.
- What remains, and why, is in the sweep report. In short: DefiLlama-only TVL/DEX and settlement rows need a paid
  source; several rows need a project to publish a figure, or Jake's reading.

**New first-party sources (Jake, 2026-10-07, evening):**
- **Maple** (its Blockworks filing): 0xd6d4 is the "Primary DAO address (manages stablecoins and SYRUP)", already
  subtracted. The Operational Admin (0xCe1cE7c7…) and Security Admin (0x6b1A78C1…) are now subtracted too. Run
  `python check_offline_items.py maple_dao_vs_ssf`. It reads 0xd6d4's SYRUP, stSYRUP and stablecoins plus both admins'
  SYRUP, sets the stored 0xd6d4 series against the page's SSF on every shared day (MATCH = all within 2%), and ranks
  SYRUP senders into 0xd6d4 (the buyback executor candidate). On a MATCH:
  - 0xd6d4 is recorded as the SSF;
  - `python archive_backfill.py --run --project Maple` fills its 365 days;
  - Maple's set is complete, so on-chain becomes primary.
- **Ether.fi** (etherfi.gitbook.io/gov/ethfi-buyback-program): recorded in `buyback_programme`.
  - Streams: weekly = 100% of eETH withdrawal fees (proposal #11); monthly = part of Stake/Liquid/Cash revenue
    (proposal #8).
  - Both are remitted to sETHFI.
  - The Foundation wallet 0x2f53… is the declared buyback wallet.
  - `in_buyback` now takes Jake's monthly readings of x.com/ether_fi_Fdn.
  - Withdrawal-fee revenue is noted but not yet measured.
- **Aethir** (docs Token Overview + Official ATH Bridges): recorded in `bridges`.
  - Axelar locks Ethereum ATH against the Arbitrum supply; Stargate backs Solana; eATH is LayerZero.
  - Never summed: Arbitrum's totalSupply stays the one supply read.
  - The rewards API is partner-only.
  - `python check_offline_items.py aethir_reward_distributors` groups one day of Arbitrum ATH transfers by sender, to
    find the checker and compute reward distributors.
- **Aerodrome:** the Blockworks probe also asks by project slug `aerodrome-finance`. Jake's project id is recorded but
  not queried, because app.blockworks.com research data is subscription.

## 11am. NEAR buyback by balance, Sky NPS confirmed, GEODNET's Solana side — 2026-10-07 (late)

**NEAR.** The buyback is the day's change in the THREE revenue wallets' combined liquid close. Internal moves cancel,
and the only outflow is consolidation (1csfundsadmin to the buyback wallet).
- The NearBlocks tier makes three `stats/balance` calls a run (`buyback_fund_balance_eod`), plus the two daily
  stats calls.
- `build_workbook._near_buyback_views` differences the series into `actual_buyback_tokens` and prices it as
  `actual_buyback_usd`.
- The transaction scans are retired (`near_account_flows_retired`). Their stored rows are listed in
  orphan_cleanup.sql CF: `python run_sql.py CF`, then `--delete CF`.
- Credibility `in_buyback_wallets` sets the combined holding against revenue.near.org's 3,786,229.6.
- `near_buyback_wallets` now reads balances only (6 calls, paced). The receipts read returned 0 and is dropped.

**Sky.**
- NPS = revenue − expense − revenue_distribution ("Remitted to Sky Reserves"). This is confirmed against Sky's own
  table and declared as `nps_formula`; it is always stored. A month that differs from the reported figure goes to
  the Review Queue.
- Sky's Jan–Sep 2026 table is the reference (manual rows; August corrected to 10.63M; September 7.15M). This closes
  the NEEDS JAKE item.
- The quarterlies are pre-allocation (revenue − expenses), so the months-to-quarter reconciliation is retired.
- Monthly Revenue Allocation (`revenue_allocation_usd_ba`) is compared with our flapper buyback $ + USDS-farm mints
  (Credibility `in_revenue_allocation`).

**GEODNET.**
- The 462M is Blockworks' figure, a third party. It is now `circulating_supply_third_party` with `ratios_use`
  `third_party_reference`, kept until the Solana side is resolved. After that, our on-chain set becomes primary.
- Bridge custody is the Wormhole NTT manager `0x2006B446…` on Polygon (locking). `0x6762157b…` is Wormhole's shared
  pass-through helper and is never added.
- `python check_offline_items.py geod_residual` (needs SOLANA_RPC_URL) reads the Solana supply and largest accounts
  (with owners) and the custody. It computes our set − custody + Solana supply − Solana exclusions (none named yet)
  and compares the result with 462M.
- The Polygon staking-contract search is closed.

## 11al. Circulating policy, Sky accounting, Aethir table, NEAR wallets — 2026-10-07 (afternoon)

**Circulating policy.** The project's own published figure is primary wherever one exists, and CoinGecko is used
only where the project publishes nothing. Our on-chain set is the cross-check: a gap beyond tolerance flags the
figure in use for review and never replaces it.
- `CIRCULATING_POLICY` (config) records, for each of the 15 projects, the source used, its definition, how staked
  tokens are treated, any add-back, and what the ratios used before.
- `ratios_use` in `CIRCULATING_ONCHAIN` carries the choice (`circulating_primary_metric`):
  - `first_party` — GEODNET, from a dated manual row (462M, AMBER after 45 days);
  - `onchain` — Pendle, whose documented method this set is, with sPENDLE + vePENDLE added back;
  - `coingecko` — Uniswap and Sky;
  - `coingecko_plus_staked` — Aerodrome: CoinGecko + veAERO − the team's 95M, added once.
- Unchanged: Ethereum (protocol total); Hyperliquid, NEAR, Aethir and Plume (their own endpoints); Chainlink, Morpho,
  Maple, Ether.fi and Fluid (CoinGecko).
- Credibility `in_circ` compares the figure in use with the on-chain set, like-for-like where CoinGecko counts every
  token (Sky: compared with our total). The Review Queue raises `first_party_vs_onchain_set` when a project's own
  figure and the set disagree.
- GEODNET's un-netted Solana burn adjusts CoinGecko's figure only, never GEODNET's own.
- To see which headlines moved: `python headline_diff.py --only circulating_policy` (needs metrics.db).

**Sky (Block Analitica).**
- NPS is no longer assumed. The adapter computes three combinations of the P&L history (revenue − expense; minus
  Security and Maintenance; minus all revenue_distribution). It stores the one that meets every month Sky reported
  (May–Aug) within 2%. If none does, nothing is stored and the failure prints the table.
- info-sky provides cumulative SKY bought (`sky_cumulative_buyback_ba`) and, for each farm, total staked, APY as
  published, and daily rewards = d(total_farmed). A fall in total_farmed is refused, never stored as negative rewards.
- New Credibility rows set these beside our reads: the rise of the cumulative buyback vs our flapper inflow over the
  same days; staked; APY (unit read off the first run); and rewards vs the measured release and the USDS mint scan.
- Sky books "SKY Rewards (USDS-SKY)" as a Direct Expense, which supports treating them as emissions (recorded on the
  vest stream).

**Aethir.** The full docs.aethir.com table (85 months, Jake 2026-10-07) is the published trajectory and the
`schedule_month` reference for Aethir's circulating by month.

**NEAR.** fefundsadmin is buy-and-HOLD as measured (OUT = 0; balance only rises), recorded on the destination decision.
`python check_offline_items.py near_buyback_wallets` is now:
- paced to the free plan (6/min; a 429 waits a minute);
- reads RECEIPTS addressed to each wallet, so transfers made inside other transactions are counted, excluding gas
  refunds and hops between the three wallets;
- caches pages in `.cache/` and resumes on the next run;
- prints senders by month (what funded Nov 2025–Feb 2026), the total against 3,786,229.6, and monthly NEAR × price.

## 11ak. Jake's readings and new sources, 2026-10-07

**Readings.**
- Your 22 readings are in `manual_references.csv`, each dated and with its page, read_by Jake, marked "entered by
  Claude Code".
- New rows hold them:
  - Sky: each farm's stake and APY;
  - Aethir: AI and Gaming APRs;
  - GEODNET: burns per month;
  - Ether.fi: the app's APY;
  - Aerodrome: voting power;
  - NEAR: all three wallets.
- Plume's and Morpho's circulating are now read daily from the APIs, so your readings of them are in config notes
  rather than manual references, which would go stale.
- The NEAR pool reading is labelled NET of that pool's commission against our GROSS yield. The account on that page
  is not stored.

**Wired.**
- **Plume:** `supply.plume.org/supply` is Plume's own circulating figure and is primary.
- **Morpho:**
  - TokenOps' figure is the circulating reference.
  - The URD `0x330eefa8…` joins Merkl in the emissions scan.
  - The OFT lockbox and legacy Wrapper are counted once, inside Ethereum totalSupply.
- **Sky:**
  - Each farm's stake is read as `lsSKY.balanceOf(farm)`.
  - The USDS-rewards farm's rewards are USDS minted to it by the Splitter (`count_mints`). They are A3's
    revenue-funded staking yield, over the SKY staked in that farm.
  - The SKY farm's rewards stay emissions.
- **NEAR:**
  - fefundsadmin and 1csfundsadmin are measured beside the buyback wallet, native NEAR only.
  - The three together are compared with the page's 3,786,229.6 NEAR.
- **Ether.fi:**
  - A3's headline is the trailing-365-day realised yield over the average stake.
  - Q0 realised, the share-price version and the app's APY are on Credibility.
  - Dune's "eETH Staking" is ETH restaking and is not used for sETHFI.
- **Aerodrome:** a voter APR on voting power sits beside the yield on AERO locked.
- **Aethir:** the docs' schedule gives next-12-month unlock dilution on A2. It uses your three October points. A
  monthly table reconstructed from search snippets is kept as an unconfirmed candidate.
- **Maple:** locked tokens are N/A (MIP-019). SYRUP left in stSYRUP is `stsyrup_legacy_tokens`.

**Not wired yet, and why.**
- **Block Analitica accounting API:** no public schema or category names. `python check_offline_items.py
  sky_ba_endpoints` prints what each path returns.
- **revenue.near.org:** its API needs NEAR's private `X-API-Key`.
- `python check_offline_items.py near_buyback_wallets` measures NEAR and wNEAR in and out by month for the three
  wallets, the reconciliation with 3,786,229.6, and hold vs burn.

## 11aj. Jake's run 2026-10-07 09:16: three failures fixed, Sky emissions, Pendle and ETH references

**Failures.**
- **NEAR supply flows:** the per-day CTE was named `day` and also had a `day` column, so `ORDER BY day` named a
  STRUCT. It is now `per_day`, and a test checks every `sql/near` file for a CTE named like a column.
- **Aethir pools:** the contract's symbol is `veAethir`.
- **Fluid Arbitrum:** the reconciliation's past-block balance read now falls through every RPC endpoint, with
  `ARBITRUM_RPC_URL` first once set. If no endpoint serves past state, it uses balanceOf(latest) minus the holder's
  logged transfers after the pinned block.

**Sky emissions.**
- The measured release (distributor → farm, 189.1M over Q0) is right. The declared streams missed the 06-18 and
  07-16 vests; rebuilt from every reset spell, Q0 is ~182M (3.8% under).
- `emissions_tokens` now shows the measured release. The rebuilt schedule is `emissions_tokens_declared`, the
  reference.
- Old schedule rows: `python run_sql.py CE`, then `python run_sql.py --delete CE`.
- Per-release listing: `python check_offline_items.py sky_lssky_releases`.

**Pendle.**
- Ours is Pendle's documented circulating (Tokenomics.md: no sPENDLE, no vePENDLE, no multisigs).
- CoinGecko still counts sPENDLE: our figure plus sPENDLE is within 0.4% of it.
- The check adds sPENDLE back for the comparison only; the headline keeps the documented figure.

**Ethereum net supply change.** The reference was CoinGecko's change in circulating supply (1.43M ETH, impossible).
It is now the issuance curve minus DefiLlama's burn, on our own common days.

**GEODNET (addendum, Jake read docs.geodnet.com 'Tokenomics' on 2026-10-07).**
- Team, Investor, Vendor/Marketing and Public sale (0xcEcccB3e…) are now subtracted from on-chain circulating
  (`confirm_candidates` is on). Ecosystem, Mining and Mining distribution were already subtracted.
- Jake's probe put ours at ~536.4M without the public-sale wallet, against CoinGecko's 462.4M. The public-sale
  balance has not been read yet: `python check_offline_items.py geod_candidate_wallets` prints it and the result
  against the circulating row's 5%. Within 5%, GEODNET's ratios switch to the on-chain figure (one line in config).
- A year of the new wallets' balances: `python archive_backfill.py --run --project GEODNET`.
- **Base-reward ceiling.** Pool release and emissions are checked against base reward x active stations
  (supply_units) x our days: 6 GEOD per triple-band station per day from 2026-07-01, 12 the year before. This is a
  plausibility bound with 15% tolerance, because data-quality, hex, SuperHex and performance rules move the actual
  reward either way. It replaces "no published schedule".
- `python check_offline_items.py geod_residual` still lists the Solana-side candidates.

**Alchemy (addendum).**
- Arbitrum: past-block balance reads use `ARBITRUM_RPC_URL` first, so Fluid's Arbitrum distributor now reads
  through Alchemy. `python check_offline_items.py archive_probe` re-tests a year back on Arbitrum and Base.
- Base logs: `python check_offline_items.py alchemy_base_logs` measures the widest eth_getLogs range the key
  serves, and the calls a year would take for Chainlink's Base lines and Fluid's Base distributors.
  - Alchemy's support page puts the free tier at 10 blocks on Base. That is ~1.58M calls per filter for a year, so
    Base stays PARTIAL.
  - PAYG serves 10,000-block ranges: ~1,600 calls a year.

**Manual readings in chat (addendum).** Readings Jake sends in chat are loaded without the CSV, through the same
checks as the form:

    python manual_form.py text "Chainlink in_locked = 45,123,456 (2026-10-07, staking.chain.link)"
    python manual_form.py text --file readings.txt

- One reading per line: `Project row [YYYY-MM] = value (YYYY-MM-DD, source)`.
- Each is stored with read_by Jake and the page, and marked as entered by Claude Code.
- A later reading for the same row and period replaces the earlier one.

## 11ai. Overnight round 2026-10-06: wrong figures fixed, independent references, ops

**Decisions recorded (Jake):** the circulating convention is CONFIRMED — staked tokens count as circulating
and are subtracted only for free float. Morpho's 10% interest tolerance is accepted.

**Wrong figures (A1-A3), and how to see every term.** `python supply_components.py Aerodrome Pendle Sky`
prints the total, each subtracted balance with its wallets, declared exclusions, the locked legs, the
circulating the ratios use, our free float and CoinGecko's figures.
- Aerodrome / Pendle free float read the lock TWICE: the Credibility check read the raw store, which has no
  on-chain view, fell back to CoinGecko's circulating, which already excludes staked or locked tokens, and
  subtracted the lock again. Fixed in `credibility._free_float_now`. It reads the views and never falls back
  to CoinGecko. With no on-chain figure, CoinGecko's free-float basis is restored as circulating =
  CoinGecko + the lock.
- Pendle's free float also subtracts the legacy vePENDLE lock (`free_float_lock_extra`).
- Sky: CoinGecko counts every SKY. The input row now compares OUR TOTAL with it, and the Pause Proxy plus
  MKR converters are printed as the amount ours is stricter by.

**New independent references.** Each is empty until its data has accumulated.

| Row | Ours | Reference | Fills when |
|---|---|---|---|
| Chainlink in_fees | DefiLlama fees | our scan of every ERC-20 into the fee aggregator (`fees_usd_aggregator_scan`; unpriced receipts left out, as DefiLlama does) | 20 shared days; routine runs read 120 s a day, or `--seed chainlink_fees` |
| Ethereum in_fees | DefiLlama fees | Blockscout stats `txnsFee` × price (no blob fees: expect ours a little above) | first read takes 365 days |
| Near in_fees | DefiLlama fees | NearBlocks `txn_fee` × price (gross gas) | first read takes 100 days |
| Chainlink in_issuance | CoinGecko pool release | fall in the 24 wallets' balance over Q0 | `python archive_backfill.py --run --project Chainlink` |
| Aethir in_locked_pools | dashboard aiStaked + gamingStaked | veAethir.balanceOf(each pool), read on-chain | next run |
| Aerodrome in_emissions | RewardsDistributor rebase | Minter growth formula over 4 epochs | stored epochs |
| Sky in_emissions | — | LSSKY rewards released (log scan) | — |
| Near A4 burn / issuance | — | BigQuery `blocks.total_supply` (dry run first; 365 days once, then top-ups) | — |
| Ethereum | — | net supply change over the days issuance and burn share (A5) | — |

- Fluid: Arbitrum distributors are scanned into `emissions_tokens_arbitrum`. Base (no free logs) and Plasma
  (no primary-source RPC on file) are not; see config.

**Rows that stay open, by design.**
- The Pendle schedule is a ceiling (AIM pays less), so that row stays CHECK.
- Plume has no primary emission schedule; an unlock table would not confirm minting anyway.
- Merkl: unclear terms, so not wired; email contact@merkl.xyz.
- Maple: the holders-share factor cited earlier was wrong. DefiLlama's dailyRevenue is gross of it.

**GEODNET candidates (C).** `python check_offline_items.py geod_candidate_wallets` prints each candidate wallet's
Polygon balance, its in and out transfers, and what the exclusion would subtract. Flip
`NONCIRCULATING_CANDIDATES["GEODNET"]["confirm_candidates"]` only after opening GEODNET's tokenomics page.

**A7.** `python check_offline_items.py chainlink_revenue_coverage` shows each component's year coverage, the
days refused, Q0, ARR and free float / ARR.

**Manual form (D).** `manual_readings_form.csv` is regenerated and sorted by what a reading clears. The
committed copy has no `our_value`, because it was built without a store; `python manual_form.py make`
refills it. Load it with `python manual_form.py load manual_readings_form.csv`.

**Windows (E1/E2).** `daily_run.ps1` now handles three Windows details:
- It decodes Python's output as UTF-8; PowerShell 5.1 otherwise uses the OEM code page.
- It finds the interpreter in order: .venv, venv, the `py` launcher, `python`.
- It logs a missing interpreter instead of throwing.

The log ends with `daily_summary.py`: counts against the previous day, NETWORK-WIDE TROUBLE, ACTION NEEDED and
new CHECKs (state in `logs/credibility_state.json`).

## 11ah. Credibility burn-down 2026-10-06 (part 2): circulating decisions, Ether.fi decomposition, manual form

**2. Circulating supply, per project.**

- **New metric:** each project's own non-circulating wallets are read into `noncirculating_holding_tokens`
  (`token.balanceOf`, summed), which the on-chain set subtracts.
  - Every wallet carries its source and the date read: `config._NONCIRC_WALLETS`.
- **New per-project switches:**
  - `ratios_use: "onchain"` makes the on-chain figure primary even where the set is partial.
  - `coingecko_basis: "free_float"` means CoinGecko's circulating already excludes staked/locked tokens. It is then
    compared with OUR free float, never with our circulating.
- **What the ratios use:**

| Project | Ratios use | Why |
|---|---|---|
| Uniswap | on-chain (established) | CoinGecko's own method: total − dEaD − Timelock; set complete |
| Sky | on-chain (established) | − Pause Proxy − the MKR_SKY converters' pre-minted SKY (MKR's claim) |
| Pendle | on-chain (established) | Pendle's documented set minus staking. Pendle's own figure = our free float |
| Aerodrome | on-chain (partial, by decision) | CoinGecko excludes ALL veAERO (= free float); ours reads high by ≤ ~8% |
| Chainlink | CoinGecko | it IS Chainlink's API figure; 24 labelled wallets + Reserve are the cross-check |
| Maple | CoinGecko | no Maple figure; registry wallets wired; SSF / DefiLlama 294.93M unexplained |
| Fluid | CoinGecko | IGP-137 custody wallet and Merkle distributors unclassified |
| Ether.fi | CoinGecko | vesting to 2027-02-18, contracts unknown; Foundation multisig secondary |
| GEODNET | CoinGecko | team/investor/vendor wallets seen only in search summaries: candidates, unwired |

- **Market cap** = price × the chosen circulating. **Free float** = it − locked.
- **History:** a newly primary on-chain figure has history only from its first run. CoinGecko fills the days before.
  To backfill: `python archive_backfill.py` for `noncirculating_holding_tokens`.
- **`python headline_diff.py`:** every headline moving more than 10% because of these decisions (before vs after,
  from your store).

**5. Ether.fi: why sETHFI's share price moved.**

- **The method:** the sETHFI scan rebuilds the vault's assets and shares from the full Transfer history and splits
  the 90-day change in assets-per-share by transaction shape (`fetch/share_decompose.py`):
  - identified top-ups;
  - unclassified no-mint inflows;
  - share burns without asset outflow (unstake fees / penalties);
  - withdrawal and deposit fees;
  - asset outflows.
- **The identity:** the parts sum to the change exactly.
- **Stored:** the reconciled reward tokens per day (`sethfi_reward_tokens_reconciled`), now the token yield's
  numerator. `sethfi_topup_tokens` stays stored beside it.
- **The run line:** prints each class.
- **Open note:** did the 5M ETHFI arrive via a Binance hop (CEX test)? It does not change a headline.

**4. Manual readings form.**

- **`python manual_form.py make`** writes `manual_readings_form.csv`: every open row whose root fix is a manual
  reading, with its URL, the exact tile, our value and the headline rows it clears. Monthly rows get one line per
  month.
- **Fill it in:** `value` and `read_on`.
- **`python manual_form.py load manual_readings_form.csv`** validates, previews, takes one typed "yes", and writes
  `manual_references.csv`. Credibility judges those rows from it on the next build.


## 11ag. Credibility burn-down 2026-10-06 (part 1): crash fix, NEAR protocol burn, root-cause map, Morpho interest

**0. The crash.** In the 18:21 run, `explorer / None: adapter crashed: invalid literal for int() with base 16: '0x'`
killed the whole log-scan tier and the `etherfi_topup_safe` probe.

- **The cause:** a log whose data word is empty (`0x`).
- **The fix:**
  - `fetch/logscan.hexint` reads `""` and `"0x"` as no value (0).
  - The run line counts such logs: "N log(s) carried EMPTY data ('0x') — read as no value."
  - Each scan is isolated. A crash fails that scan alone ("scan crashed — … The other scans ran."). Only an
    explorer timeout still stops the tier.
  - In `check_offline_items.py`, an `eth_call` that returns `0x` prints "returned 0x (no value)" and is treated as
    no value.

**6. NEAR burn = DefiLlama fees × the burned share in force, every day.**

- **The rule:** 0.70 before protocol v87, 1.00 from its mainnet activation. It is labelled
  `derived:near_protocol_rule_x_defillama_fees/price`, and DefiLlama's revenue is a labelled cross-check in the run
  line, never converted.
- **Switch date:** taken from the new `near_protocol_version` metric, read every run from RPC `status`. The first
  stored reading ≥ 87 switches the share from that day, and the log says "v87 FIRST READ {date}".
- **Covered days:** `not_active_as_of` 2026-10-06 (mainnet protocol 86 at block 218,820,858) covers every day up
  to then.
- **Uncovered days:** days after the last reading with no version read are `:PARTIAL` (0.70 used; a lower bound).
- **Run line:** prints Q0 burn and burn yield before vs after.

**1. Root-cause map: `python credibility_report.py --roots`.**

- **What it does:** traces every CHECK / UNVERIFIABLE row to the root input(s) holding it open. A derived row
  fails because an input fails.
- **Output:**
  - The roots are ranked by headline rows blocked, each with why it fails and its fix class: code / data source /
    manual reading / genuinely unverifiable.
  - The same roots are then grouped by input type across projects.
- **Run it after each run and paste it back.**

**Morpho customer revenue (one root, four A2 headlines).**

- **The new reference:** Morpho's own API now gives borrower interest per day: listed markets,
  sum(borrowAssetsUsd × ((1 + borrowApy)^(1/365) − 1)), stored as `borrow_interest_usd_day_morpho_api`.
- **Isolation:** it is its own query, so a schema error there never costs the confirmed supply read.
- **The comparison:** the A2 row is judged by `in_interest_day`, which compares DefiLlama's fees and the API
  figure on the latest completed day both hold.
- **Tolerance:** 10%, a judgment pending Jake's review. The API figure is a rate at the moment of the read, over
  listed markets only.


## 11af. Jake's run + probe 2026-10-06 17:20: Ether.fi transfers, NEAR protocol rule, Aethir tile, Etherscan pace

**1. Ether.fi.**

- **(a) The Binance 5M — `check_offline_items.py etherfi_cex_test`:**
  - What it reads: USDC, USDT and ETH leaving the top-up Safe, the old buyback Safe, the 600K Safe and the
    deployer over 2026-07-15..08-13. For ETH it reads both the wallet's own transactions and internal ones,
    because a Safe sends ETH as an internal transaction.
  - What counts as Binance: a recipient that IS the Binance hot wallet `0x28c6c062…`, or one that forwards to it
    within 7 days (a deposit address).
  - **If payment is found:** the 5M is "bought on a CEX (inferred)", its own category, never on-chain "bought".
  - **Until then:** the pipeline counts it as transferred. 0x83971edb is labelled as an EOA relaying a Binance
    withdrawal.
- **(b) Address poisoning:** a zero-value transfer whose counterparty shares the first or last four hex digits
  with a holder or a real counterparty is dropped from every log scan (`fetch/logscan.drop_poison`, counted in the
  run line) and from the Ether.fi probes. It moves nothing, so no sum changes.
- **(c) July senders:** `etherfi_topup_safe` now reads 0x83971edb, 0x5ec5e6b4, 0x66fcfc15 and 0xe4439b1d for
  each of: code, Safe owners vs the buyback Safe, VestingWallet getters, and their own ETHFI in and out by
  counterparty. They are labelled "unidentified; transfer" until then.
- **(d) Recorded:** ~20.95M ETHFI into the top-up Safe since 2024-07, 1,296,885 (6%) bought on-chain.
  - The scan stores `buyback_bought_share_alltime` each run.
  - A3 shows "of which BOUGHT on-chain — FULL HISTORY" beside the Q0 share.
- **(e) Yield reference:**
  - **New adapter:** `fetch/share_price.py` reads sETHFI's share price at ONE block each, today and ~89 days back
    via archive. It uses `convertToAssets(1e18)`, else `balanceOf/totalSupply` at that block, stores
    `sethfi_share_price_onchain`, and the credibility row annualises it.
  - **The 13.15%:** it came from the previous reference, `lock_assets_per_share`, not from DefiLlama. That is a
    ratio of two separately stored series whose share leg was once the over-counting Dune figure, so its Q0 growth
    (and the "101.7%/yr" week) is not established as real accrual.

**2. NEAR burn.**

- **The rule before 2026-06-19:** DefiLlama's revenue equals its fees there, so burn = fees × 0.70, from the
  protocol rule (nearcore `parameters.yaml` `burnt_gas_reward: 3/10`).
  - It is labelled `derived:near_protocol_rule_x_defillama_fees/price`, under a declared handover to the
    revenue-based burn.
  - It applies only where revenue == fees and before 2026-07-06.
  - The run line counts the rule days.
- **Found while confirming:** nearcore protocol **v87** (2.14.0; `87.yaml`; HSP-027, approved 2026-07-06)
  removes the 30% contract reward, so from v87's mainnet activation **100%** of gas is burned. From then,
  DefiLlama's fees × 0.7 understates the burn.
  - `check_offline_items.py near_protocol_v87` finds the activation block and date. Paste it back and the
    post-activation burn gets fixed.

**3. Aethir staked.** The tile is the UNDATED object carrying all four `*Staked` keys. The `stakeHistory` entries
carry them too, with `startTime`/`endTime`, and are skipped.

**4. Etherscan pace.** `api.etherscan.io` now runs at 2.5/s. Etherscan's own message says its limit is 3/s.


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
