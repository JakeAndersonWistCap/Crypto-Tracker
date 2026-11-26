# Handover to a local Claude Code session (Jake's laptop, live store)

Written 2026-11-23 by the cloud session at the end of Round 17. The cloud session could NOT reach Jake's store: every
figure it checked came from the 15 fixture dumps (as of 2026-10-13) or from four exports of 2026-11-20 21:11
(Aerodrome, Ether.fi, Maple, Morpho). You can run against the real store. That is the whole point of this handover.

Branch: `claude/crypto-metrics-supply-demand-ovkg8g`. No PR. Push to no other branch.

## 1. What must never happen

- Never print, log, commit or paste an API key. Mask anything after `/v2/` in any URL you show.
- Never open, print, edit or commit `.env`. Never commit `explorer_routes.json` contents or any personal account ID.
- Never send Jake's name or email to an outside service. User-Agent is `token-metrics-check/1.0`.
- Never write to the store (`metrics.db`) outside `storelock.py`'s run lock. One writer at a time. Read-only checks
  open it with `file:metrics.db?mode=ro`. Never delete rows by hand; never edit the store with ad-hoc SQL.
- Never use `git stash`. Kill processes by PID only. `.ps1` files stay ASCII.
- PowerShell 5.1: in any `python -c "..."` use only single quotes inside the double-quoted string; SQL values as
  parameters.
- Never loosen a bound or tolerance to make a check pass. Never renew an expired allowlist entry; an allowance is
  Jake's to extend. Unknown is never 0. Never invent an exclusion from circulating.
- Do not use AWS credentials. BigQuery only through Jake's own `gcloud auth application-default login` (he does the
  login; quota project `near-data-510309`).
- A frozen project's cell moves only as a declared move (section 5).

## 2. Speed-mode rules (Jake, 23 Nov) — in force until all 15 projects read READY

- The goal is a working workbook. Full checking rounds come after.
- Push once the round's tests + `project_freeze.py --compare all` + `done_check.py` pass. Do not wait on the full gate.
- Tests that pin wording, layout, tab order or record text must not block: relax or delete them when they break.
  Keep every test that checks a number.
- Frozen projects: keep compare all; one moves table per round (project, cell, before, after, reason) instead of
  per-cell declarations. The table the compare reads is `tests/fixtures/frozen_moves_rNN.json`; the readable copy is
  `docs/moves_rNN.md`.
- Guards (measuring point, source handover and the like): report a trip in one line and move on, unless it shows a
  number is wrong.
- Lint (`ruff check --select F,E9 .` = 80), the full gate, RUNBOOK and SPEC tidying: once, at the end, before the
  final freeze.
- Keep: done_check, compare all, the circulating rule, the CoinGecko/CMC cross-check, source checks.
- Rules are frozen: no new metric, no new rule.

The circulating rule (Jake, 17 Nov): OUT of circulating only if all three hold — not yet vested or released, nobody
can move it, and it earns nothing. Burned = out of circulating and total. Everything else circulates. Off free float:
anything staked, locked, earning, or held by an issuer, foundation or treasury (held buybacks included). Insider
wallets from on-chain and docs evidence; inferred ones tagged INFERRED.

## 3. Commands on the live store

```
cd C:\Users\jake\Crypto-Tracker
.venv\Scripts\Activate.ps1
git switch claude/crypto-metrics-supply-demand-ovkg8g
git pull
git log --oneline -1

# read-only checks first
python project_freeze.py --compare all        # builds the 15 fixture dumps; expect nine lines, each "0 undeclared move(s)"
python done_check.py --verbose > logs\done.txt # reads token_metrics.xlsx + token_metrics_audit.xlsx in this folder
python headline_check.py --audit token_metrics_audit.xlsx --reader token_metrics.xlsx --db metrics.db --out ..\tm-exports\headline_check.md

# build (writes the store; takes the run lock itself)
python token_metrics.py                        # the daily run: fetch, store, build both workbooks
python token_metrics.py --project Chainlink    # one project
python token_metrics.py --seed aero_buyback    # Aerodrome: every buyback window day the store missed (the 11-20 burn)
python archive_backfill.py --project Aerodrome --run   # Aerodrome: issuance rows 10-23..10-27
python staked_eth_rebuild.py                   # Ethereum: staked ETH history from BigQuery (needs Jake's ADC login)

# hand a project's rows to a cloud session
python store_export.py --project Aethir --out-dir ..\tm-exports

# freeze a project (only when done_check reads READY on the live store and the gate is green)
python project_freeze.py --write <Project>     # writes tests/fixtures/frozen_<project>.json from the fixture build
#   then add it to config.FROZEN_PROJECTS (copy the Ether.fi block at the end of config.py) and commit both
python gate.py --rev HEAD --out ..\tm-gate --workers 4   # the full gate, ~40 min; failures in <out>\tests.txt
```

How a frozen project is checked on the live store: `--compare all` guards the CODE (fixture build); on the store the
check is done_check READY plus explaining every move between two consecutive builds.

## 4. Where every round stands

- Rounds 1–16: done and pushed. Nine projects frozen: Aerodrome, Ether.fi, Fluid, Hyperliquid, Maple, Morpho, Pendle,
  Sky, Uniswap.
- Round 17 (pushed, full gate NOT run — speed mode):
  - Aerodrome: flow statement fixed in code; READY on the 11-20 export once the two commands above have run
    (residual −0.05%). Not yet seen on the live store.
  - Ether.fi: the five cells that fell to CHECK were a code fault in the CoinGecko cross-check; fixed.
  - Holder value yield now divides by the average market cap over its window (44 declared cells:
    `docs/moves_r17.md`).
  - New: `config.NOT_VERIFIED` (grey "not verified" cells, not blockers; empty so far), `headline_check.py`,
    the Leaderboard first sheet (`leaderboard.py`).
  - Chainlink, Ethereum, Near, GEODNET, Plume fixes below.

## 5. Finish list, per project

Statuses marked (fixture) were seen only on the 2026-10-13 dumps and may not match the live store. First job on the
laptop: pull, run the daily run, run done_check, and replace every (fixture) line below with what the store says.

| Project | State | Next step |
|---|---|---|
| Aerodrome (frozen) | READY on the export with the R17 rows | Run `--seed aero_buyback` and `archive_backfill.py --project Aerodrome --run`, rebuild, confirm READY and Flows Q0 within ±0.5%. Decision open: is a burn funded by a mint to the same wallet a buyback (cloud session capped it at tokens bought; ~8.18M of the 10M sits on the new "destroyed, not a buyback" line). |
| Ether.fi (frozen) | READY on the 11-20 export | Confirm the five cells read VERIFIED/PASS again on the store. |
| Maple, Morpho (frozen) | READY on the 11-20 exports | Confirm. |
| Fluid, Hyperliquid, Pendle, Sky, Uniswap (frozen) | Not re-seen on the store this round | done_check on the store; only holder value yield moved. |
| Chainlink | NOT READY (fixture: 8 lines) | Needs Jake's decisions (docs/r15_chainlink_pack.md, decisions 1–8): S13 fee/revenue split, whether node payments are outflow (b), insider share floor, circulating under the rule, theatre-flag window. The price row dated 2025-06-13 is fixed in code (confirm its source starts `defillama_coins:history`). CCIP value −72% and claimed emissions −81% are real (read live 23 Nov); resolving the review rows is Jake's call. |
| Ethereum | NOT READY | Staked ETH: the node read now has a staleness check and a second source (ultrasound.money). The days 2026-10-26 to the first node row exist only in BigQuery: run `staked_eth_rebuild.py` after Jake's ADC login. Then done_check. |
| Near | NOT READY | NearBlocks timeouts were a billing miscount (the run spent ~70 credits against 6 a minute); the reader now asks only owed days (~12 credits). Run it and check the 429s are gone. Stage-2 approval is per calendar month and Jake's. |
| GEODNET | NOT READY (fixture: 2 lines) | Now on the circulating rule (circulating = total − burned; gap to CoinGecko named by wallet, to the token). Left: UNIFORMITY (docs say stakers get +20% GEOD — a decision), insider share of stake (expected to clear on the store). Jake to confirm the three revenue lines the agent populated. |
| Aethir | NOT READY | The 6 MISSING / 5 CHECK on Jake's store were NOT reproducible on the fixture. Paste the store's done_check lines and work them one by one. Its subsidy-cover allowance expired 2026-11-14 (Jake's to extend); three MATURING rows expired 2026-10-17. |
| Plume | NOT READY | `rwa_defillama_usd` +52% was a double count, fixed (last 30 days re-read once on the next run). Claimed emissions +174% is real. The 3 MISSING / 2 CHECK were not reproducible on the fixture: paste the store's lines. DefiLlama's RWA category on Plume is now ~$1M (issuers re-filed elsewhere) — a decision. |

"Not verified": add an entry to `config.NOT_VERIFIED[project]` only after a real search of primary sources, with
`cols`, `searched` and `read_on`. Never for a BROKEN, STALE, error or blank cell, never on a frozen project.

Freeze order once a project reads READY on the store and the gate is green: one project per commit, baseline from
`project_freeze.py --write`, then `--compare all`.

## 6. Things the cloud session got wrong or could not establish

- R15 said Aerodrome's 10.58M AERO on 11-20 was a batch of gauge claims. Wrong: 10.5M was one mint (10M to the
  Foundation buyer Safe, burned next day). Why the Foundation did it is not established.
- Why Ether.fi's five cells showed PASS on the store before 23 Nov is not established (they were already CHECK on
  the 11-20 export at the old code).
- That 0x28C6c062… is a Binance wallet (Chainlink pack) was not re-read from a primary source.
- The full gate has not run on Round 17. Run it before any freeze.

## 7. Ethereum and Near — detail from the agent that did the work (2026-11-23)

- Full suite at the agent's commit (before the merge with the rest of Round 17): 3086 passed, 1 skipped, 1 failed
  (an xdist worker crash on `test_round_p4_q8`; it passes alone). The merged tree has NOT had a full run.
- Ethereum staked ETH: `python staked_eth_rebuild.py` first (a test, stores nothing); only if it prints
  OVERLAP TEST PASSED run `python staked_eth_rebuild.py --apply`. NOT PASSED is a decision for Jake: keep the series
  stale, or declare the seam a change of quantity (actual balances read +0.80% above validatorqueue's definition).
- Ethereum has other open lines on the fixture (revenue split, rule-3 cells, insider list, settlement series that
  ends 2026-08-25): it needs its own round after the rebuild. `done_check.py --verbose` on the store shows which remain.
- Near: a NearBlocks key does NOT fix the timeouts (a key is already set; a free key and a keyless caller are on the
  same plan; only a paid plan lifts the limits). The fix is asking less. On the day the weekly lockup-list read runs,
  the daily NearBlocks reads may say "not called" and are read the next day.
- Near's Rank and Archetype key now carry the date they form (two years of settlement; R22: on Jake's store the run is
  unbroken from 2026-02-19, so the key forms 2028-02-18 on the daily run alone, or 2026-12-09 once the billed
  one-shot fill reaches the 2024-12-10 floor — docs/r19_bigquery_plan.md, "R22: NEAR's dates reconciled") and no longer block; NRR on settlement volume blocks until the run on or after 2026-12-01 that stores the
  P2P days back to as-of minus 364.
- Check lines: see the agent's block "11fg-ethereum-near" in SPEC "Round 17 — Ethereum and Near" and run:
  `python token_metrics.py --project Ethereum --project Near`, then
  `python done_check.py --verbose | Select-String "^(Ethereum|Near)" -Context 0,16`.

## 8. After Round 23 (2026-11-25): THE ONE STORE RUN BEFORE THE FREEZE, IN ORDER

**FIRST, read-only, on Jake's store (R23, docs/r23_near_history_paths.md):**
```
python near_p2p_history.py gaps --db metrics.db          # NEAR's missing P2P days from 2025-12-29, the cost of filling just
                                                         # them (7.54 GB a day, ESTIMATE), the dry-run command lines, Ethereum's
                                                         # and NEAR's stored settlement MIN / MAX / COUNT
python near_p2p_history.py gaps --db metrics.db --dry-run   # also dry-runs each range (free; needs bq signed in)
```
It tells a hole from a series that simply starts 2026-02-19 (the likeliest: the newest-first backfill never reached
earlier). From now the daily run reads those holes through the normal top-up path (at most 3 chunks a run, inside the
existing monthly ledger). If Ethereum's MIN is on or before 2024-11-25 no Ethereum fill is needed. The billed one-shot
(`bigquery_backfill.py`) stays built, dry-run-ready and OFF.

CIRCULATING RULE, R23 wording (Jake 2026-11-25): Circulating follows the issuer's own published figure, adjusted by Jake's rule where on-chain evidence differs. Locked or earning holder tokens are in. An issuer-declared unreleased reserve is out until it moves; when it moves it counts as new supply. Unvested and non-transferable is out of circulating even when staked, because the holder can't sell it. Chainlink's circulating cell carries the caveat "issuer-declared reserve; no on-chain lock (27 Safes 3-of-5, 4 multisigs 3-of-6, 2 EOAs); release schedule is a published statement"; docs/r23_chainlink_wallets.md is the record.

Round 23 added to what follows: unvested-and-staked stays out of circulating (the rule's wording); Aethir's holder value
yield and burn ÷ issuance are read since the burn began (2026-11-02, labelled "since 2026-11-02"); Plume's "Insider share
of stake" reads n/a "issuer publishes no insider wallet list" (allowed label) and Plume is OUT of the freeze commands;
Hyperliquid's issuance % is labelled "13-day window, annualised".

(Round 22 text, still in force:)

Rounds 21, 21b and 22 are on this branch; Jake has not run the store since 4e1231d (24 Nov). This is the single run.
What changed since that run (details: SPEC "Round 22" / "Round 21b" / "Round 21", docs/moves_r22.md, docs/moves_r21.md):
- burns in net float pressure; the bought-back rules F1–F7; paused / off labels "unranked" (R21);
- Hyperliquid's Assistance Fund burned (the validators' December 2025 vote), A3 + A4, A4 on every burn (R21b, R22);
- circulating on Jake's rule, lined up with the industry (R22, docs/r22_circulating.md): Hyperliquid's unvested team
  HYPE out (539.8M → about 301M), Ether.fi's unvested team leg out, Chainlink's 33 non-circulating wallets out
  (equal to Chainlink's own figure, CoinGecko and CMC), GEODNET's Mining reserve out, Near's LOCKED out;
- Morpho: every vesting contract — claims of vested tokens are not new supply, vesting counts on its schedule
  (docs/r22_morpho_vesting.md); Aethir's staking yield wired (UNIFORMITY forms); fee burns in A3's key (Aethir,
  Ethereum, Near); Plume active again with no holder value;
- Jake's accepted named gaps never block: World Chain TokenJar, Pendle on Berachain, the HyperEVM base fee;
- NEAR's December P2P chunk approved (for 2026-12 only, ≤ 337 GB); the keyless beacon node is Ethereum's staked-ETH
  source (no beaconcha.in key, no trial).

### The run, in order

```
cd C:\Users\jake\Crypto-Tracker
.venv\Scripts\Activate.ps1
git switch claude/crypto-metrics-supply-demand-ovkg8g
git pull
git log --oneline -1
gcloud auth application-default login
gcloud auth application-default set-quota-project near-data-510309

# 1. read-only, before anything is written
python project_freeze.py --compare all
#    expect: nine lines, each "0 undeclared move(s)"
python check_offline_items.py env_keys
#    expect: ALCHEMY_API_KEY PRESENT; BEACONCHAIN_API_KEY "nothing: ... the keyless public beacon node ..." (no key needed)
python -c "import sqlite3; c=sqlite3.connect('file:metrics.db?mode=ro', uri=True); print(c.execute('SELECT MIN(date), MAX(date), COUNT(*) FROM metrics WHERE project=? AND metric=?', ('Ethereum','settlement_volume_usd')).fetchone())"
#    if MIN is on or before 2024-11-25 (MAX 2026-08-25, where Artemis' CSV ends), Ethereum's key forms without a backfill
python -m fetch.aethir_chain
#    stores nothing: "schedule matches"; the burns read first; the claims check may print a Q-4 finding (advisor vesters)

# 2. the daily run (takes the run lock itself); new tiers fill with no extra command:
#    Aethir staker_faucet_out (the two RewardFaucets' weekly 1,000,000 ATH), the daily fee burn
#    Pendle pendle_buyback_chain; prices: the on-chain pool cross-check
python token_metrics.py

# 3. catch-ups
python archive_backfill.py --project Aerodrome --run --budget-min 45
python token_metrics.py --seed pendle_berachain
#    stops after 10 minutes; Berachain is an accepted named gap (never blocks)
python token_metrics.py --seed tokenjar
python token_metrics.py --seed plume_payouts
#    only if Plume's emissions_claimed_tokens lags the 90-day window ("stored after …s — N event(s), M row(s) stored")

# 4. build, check
python token_metrics.py
python -c "import free_float as f; a='token_metrics_audit.xlsx'; print(f.check_workbook(a,'token_metrics.xlsx')); print(f.check_nets(a)); print(f.check_burns(a))"
#    expect: []  []  []
python done_check.py --verbose > logs\done.txt
python circ_legs.py --release Morpho
#    "morpho_relocked_tokens: fall 8,260,273.97", "morpho_vesting_unvested_tokens: fall 613,973.95 ...", "less
#    morpho_vest_claims_tokens: ... taken out of (e)" (the 23 Nov 65M founder claim); Unlocks Q0 about 10.5M
python headline_check.py --audit token_metrics_audit.xlsx --reader token_metrics.xlsx --db metrics.db --out ..\tm-exports\headline_check.md
python project_freeze.py --compare all
#    expect: nine lines, each "0 undeclared move(s)" (compare all reads the 15 dumps, not the store)
foreach ($p in 'Aerodrome','Chainlink','Ethereum','GEODNET','Aethir','Near','Morpho','Ether.fi','Maple','Sky','Pendle','Fluid','Hyperliquid','Uniswap','Plume') { python store_export.py --project $p --out-dir ..\tm-exports }
```

### Expected done_check after that run (all 15)

Predictions from builds of Jake's exports at this code (Hyperliquid 16 Nov, Ether.fi / Morpho / Maple / Aerodrome /
Near / Uniswap 20 Nov, Fluid / Pendle 18 Nov, Sky 17 Nov — each at its own as-of) and from the 24 Nov run; not seen on
the store.

| Project | Expected | Notes |
|---|---|---|
| Aerodrome, Maple, Uniswap, Sky, Fluid | READY | READY on their exports at R21/R21b; R22 does not move them. |
| Hyperliquid | READY | READY on its 16 Nov export at R22. Circulating about 301.0M (tokenDetails' circulating − the HyperEVM zero address); against CoinGecko +26.7% (the Foundation 60.66M + Grants 2.98M, counted in by the rule) and CMC +19.4% (CMC takes the burned Fund out twice). A4 reads every burn. |
| Ether.fi | READY | READY on its 20 Nov export at R22. Circulating 974,680,443 until 2026-12-17 (the unvested team leg out, MODELLED); its CoinGecko check raises a review row (about −2.4%), named in config. |
| Morpho | READY | READY on its 20 Nov export at R22. Total dilution 27.2% → 9.6% of float on the export (vesting on schedule, claims not supply, the founders' relock out). Circulating about 966.9M. |
| Near | READY | READY on its 20 Nov export at R22; circulating = total − LOCKED (about 1,310.3M). |
| Pendle | READY | The chain reader dates each epoch's buyback the day it is bought (was STALE on 24 Nov); Berachain is an accepted gap. R22 addition: the 25 Nov store run read NOT READY (payout ratio and holder share BLOCKED, `fees_usd_lagm` 8 of 90 days). The R5.3 lag match no longer cuts the fee side once the buyback is current from the contract, so both cells read over their own 90 days, with the note "NOT LAG-MATCHED (R5.3; R22) … not a publish lag". |
| Chainlink | READY | READY on 24 Nov. Circulating 766,849,970 (the 33 wallets out, = Chainlink's API, CoinGecko and CMC) until the next tranche (~2026-12-18). |
| GEODNET | READY | READY on 24 Nov. Circulating about 836M (the Mining reserve out). |
| Ethereum | READY | READY on 24 Nov. New A3 tab (secondary): the EIP-1559 burn in the key; its buyback cells N/A by the route "nothing is bought". |
| Aethir | READY expected | UNIFORMITY forms (staking yield wired). Holder value yield keeps the same-span rule: if the faucet scan's first stored day leaves Q0 part-covered, it reads BLOCKED (unequal coverage) until both legs cover 90 days — the schedule rows from 2026-08-06 should cover Q0. R22 addition: BURN ÷ ISSUANCE and release ÷ burn read over the days since the burn began, formatted "x since 2026-11-02" (no longer MISSING). |
| Plume | READY expected | 12 fixture lines, each filled by the run: outflow (a) emissions_claimed_tokens (6 lines), (b) merkl_campaigns_plume_tokens (Subsidy cover, Leakage), tbill_3m_yield, market_share_pct (may need ~3 runs; if DefiLlama's RWA move empties it, Jake declares it). "Insider share of stake" is n/a, "issuer publishes no insider wallet list" (allowed, R23). Plume is not frozen now. What can remain after the run: only a line the run could not fill (an emissions window older than the event log reaches, or a DefiLlama RWA series that stays empty): list from done_check --verbose. |
| World Mobile | parked | No change. |

### OPTIONAL — only if billing is enabled on near-data-510309: both P2P histories in one go

NEAR back to 2024-12-10 (~2,684 GB) and Ethereum 2024-11-04..2026-05-31 (~772 GB): about 3,456 GB, about $19.65 at
$6.25/TiB (hard cap 4,000 GB = $22.74; $16.49 if the month's free TiB is unused). The command refuses unless config says
billing is on AND the Cloud Billing API confirms it; every query carries maximum_bytes_billed; it dry-runs first, is
chunked and resumable (rerun after any failure). If billing stays off, the free-tier schedule (docs/r19_bigquery_plan.md,
config.BIGQUERY_SCHEDULE) stands and NEAR's December chunk below runs instead.
```
python bigquery_backfill.py plan
python bigquery_backfill.py dry-run
#    free; a line per chunk (GB, $, maximum_bytes_billed), TOTAL near 3,456 GB (~$19.65), "the cap: fits"
# set config.BIGQUERY_BILLING: billing_enabled True, confirmed_by, confirmed_on 'YYYY-MM-DD'
# (if asked: gcloud services enable cloudbilling.googleapis.com --project near-data-510309)
python bigquery_backfill.py run
#    "DRY RUNS FIRST", "BILLING CONFIRMED", "[i/n] ... DONE" per chunk, "BILLED this run ..."; exit 1 = rerun; exit 3 = refused
python token_metrics.py --project Near
python token_metrics.py --project Ethereum
```

### On or after 2026-12-01 — NEAR's December P2P history chunk (free tier; approved, R21b)

Skip if the one-shot above ran. Approved 2026-11-25 for 2026-12 only, max 337 GB; one chunk, 2025-11-01..11-30.
```
python near_p2p_history.py plan
python near_p2p_history.py dry-run
python near_p2p_history.py run
```

### Freeze commands (only once done_check reads READY on the store AND the full gate is green)

One commit per project. The nine already frozen are re-baselined (their declared moves stay as the record); every
other project that reads READY is newly frozen. World Mobile stays parked.
```
python gate.py --rev HEAD --out ..\tm-gate --workers 3
#    expect GATE_EXIT 0

# the nine, re-baselined
foreach ($p in 'Aerodrome','Ether.fi','Fluid','Hyperliquid','Maple','Morpho','Pendle','Sky','Uniswap') {
  python project_freeze.py --write $p
  git add tests/fixtures
  git commit -m "freeze: re-baseline $p after Round 22 (declared moves kept; done_check READY on the store, gate green)"
}

# newly frozen, each only if READY: first add its entry at the END of config.py, e.g. for Chainlink
#   FROZEN_PROJECTS["Chainlink"] = {
#       "baseline": "tests/fixtures/frozen_chainlink.json", "since": "<today>",
#       "why": "done_check READY on Jake's store at this code (export <its store_export file>); the full gate green"}
# baseline names: frozen_chainlink.json, frozen_geodnet.json, frozen_ethereum.json, frozen_near.json,
#                 frozen_aethir.json, frozen_plume.json
foreach ($p in 'Chainlink','GEODNET','Ethereum','Near','Aethir') {      # Plume is NOT frozen now (R23)
  # (only if $p reads READY; add its FROZEN_PROJECTS entry to config.py, then:)
  python project_freeze.py --write $p
  git add config.py tests/fixtures
  git commit -m "freeze: $p (done_check READY on the store, gate green)"
}
#   Plume: not frozen now (Jake, R23); its insider share is n/a "issuer publishes no insider wallet list" (allowed label)
python project_freeze.py --compare all
#    expect: one line per frozen project, each "0 undeclared move(s)"
git push -u origin claude/crypto-metrics-supply-demand-ovkg8g
```

## 9. Decisions open after Round 22 (Jake's)

- Chainlink's 33 non-circulating wallets classed (b) an unreleased reserve (Chainlink's own set, ~7% a year release):
  their purpose mixes company reserve and incentives — the call most worth a look (docs/r22_circulating.md).
- Hyperliquid's team wallet is held out WHOLE: the API exposes no vested/unvested split (all 238.36M staked, locks
  expired 2026-06-07); each payout out of the wallet counts as it vests. Reported, not counted, per item 1(a).
- Morpho: (1) the founders' modelled relock tranche (152M) is out — their MIP1 awards vested on chain 2025-06-11; undo
  by restoring the Founders tuple in MORPHO_RELOCK["tranches"]. (2) What is left of dilution is NOT mostly DAO
  emissions: Cohort 3's modelled relock (67M, no contract found) is ~65% of it. (3) The Labs vester's 3.04M unvested
  MORPHO sits in the Contributor Grants Safe, counted in circulating and off the float: taking it out under item 1(a)
  needs the float's issuer leg split (else it leaves the float twice) — circulating is 0.3% high meanwhile; the float is
  right either way. (4) The DAO re-approved 150M to the Vester on 23 Nov (tx 0x9ab069b3…).
- Aethir: holder value yield is read since the burn began (2026-11-02), both legs over the same days, labelled (R23, Jake).
- Plume: not frozen now; "Insider share of stake" n/a (allowed). Chainlink: the 33 wallets stay out until Jake confirms;
  the alternative classification is docs/r23_chainlink_wallets.md (NOT applied).
- Hyperliquid: an "Issuance % of supply" cell returns to its pre-R9.2 value (69.9%, a 13-day annualisation of the
  October team tranche) — the figure is right on its definition but reads high; a longer window is a rule change.
- Aave / Pyth (branch claude/aave-pyth, not merged until after the freeze): docs/r22_aave.md, docs/r22_pyth.md —
  Ecosystem Reserve in or out of circulating; the held buyback in the AFC Safe; Pyth's publisher payouts as (b);
  Pyth's INFERRED funder wallets off the float; Douro's in-kind PYTH as buys.
- Highlights: three cards were written for the old tags and key and may not render (left alone, as instructed).
