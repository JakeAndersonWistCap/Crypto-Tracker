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
because that reading was correct and only the column was wrong.

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
