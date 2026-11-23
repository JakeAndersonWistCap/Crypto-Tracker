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
- Near's Rank and Archetype key now carry the date they form (two years of settlement: late 2027 on the store as it
  stands) and no longer block; NRR on settlement volume blocks until the run on or after 2026-12-01 that stores the
  P2P days back to as-of minus 364.
- Check lines: see the agent's block "11fg-ethereum-near" in SPEC "Round 17 — Ethereum and Near" and run:
  `python token_metrics.py --project Ethereum --project Near`, then
  `python done_check.py --verbose | Select-String "^(Ethereum|Near)" -Context 0,16`.

## 8. Round 18 (2026-11-24) — research and piping; THE NEXT RUN ON THE STORE, IN ORDER

Round 18 ran nothing on the store and froze nothing. Every route below is wired so one pass fills it. This section
supersedes the "Next step" column of section 5 where they differ. Decisions still open are in section 9.

```
cd C:\Users\jake\Crypto-Tracker
.venv\Scripts\Activate.ps1
git switch claude/crypto-metrics-supply-demand-ovkg8g
git pull
git log --oneline -1
gcloud auth application-default login                                   # Jake, once per login
gcloud auth application-default set-quota-project near-data-510309

# 1. read-only, before anything is written
python project_freeze.py --compare all
#    expect: nine lines, each "0 undeclared move(s)"
python check_offline_items.py plume_rwa_issuers
#    expect: seven issuers, a list sum near 62.8M, category about 986,609 over 4, no "NOT DECLARED either way" line
python check_offline_items.py plume_rwa_archive
#    prints DefiLlama's RWA-category protocols on Plume as of 2026-10-11 (Wayback). PASTE IT BACK: it names the
#    $11-42M of the old series nobody could attribute
python near_p2p_history.py plan
#    no network; prints the monthly schedule and "NEXT: NOT APPROVED - the chunk ..."

# 2. the daily run (takes the run lock itself). It fills, with no extra command:
#    Aerodrome  velo_fees_usd / velo_revenue_usd / velo_holders_revenue_usd (Velodrome's revenue, taken off before 10-22)
#    Chainlink  issuer_exchange_outflow_tokens, node_operator_payments_tokens, insider_stake_principal_tokens,
#               reward_vault_balance_tokens, issuer_path_holding_tokens, circulating_supply_chainlink_api
#    Ethereum   ef_held_tokens, ef_inferred_held_tokens, ef_staked_tokens (Ethereum Foundation wallets)
#    Plume      rwa_defillama_usd rebuilt once from 2026-05-21 on the issuer list (Run Log: "CLEAN-UP R18-plume-rwa",
#               "the ONE rebuild"), plus rwa_defillama_category_usd
#    GEODNET    nothing new is fetched: revenue = burn / 0.8, fees = burn / 0.4 are built from the stored burn
#    Near       NearBlocks reads billed as NearBlocks bills (no "TIER TIMED OUT" for nearblocks)
python token_metrics.py

# 3. Aerodrome's two catch-ups (Round 17), then Chainlink's
python token_metrics.py --seed aero_buyback
python archive_backfill.py --project Aerodrome --run
#    expect: "Velodrome VELO history Aerodrome: not read - before the merger the series are Aerodrome's alone" and
#    "Aerodrome issuance before the first v3 read ..." (rows 10-23..10-27)
python archive_backfill.py --run --project Chainlink
python token_metrics.py --project Chainlink
#    repeat this line if the Run Log says a scan stopped at its budget (the first run seeds 4,231 transfers)

# 4. Ethereum
python -m fetch.ef_wallets --backfill
#    optional, 15-20 min, resumable; repeat until "nothing owed". Without it the float trend reads part-held for a quarter
python staked_eth_rebuild.py --apply
#    exit 0: OVERLAP TEST PASSED, rebuilt days stored
#    exit 3: NOT PASSED -> DECLARED CHANGE OF QUANTITY, one row beacon_chain_eth_seam_step_pct stored (Jake's R18 ruling:
#            the series switches to actual balances at the seam, labelled; no question asked)
#    exit 1: BigQuery unavailable, nothing stored: log in and run again
python settlement_extend.py validate
#    read-only: the settlement rebuild's legs and missing days beside the Artemis CSV

# 5. Near's history backfill - ONLY on or after 2026-12-01, and never without Jake's dated approval
python token_metrics.py --project Near
python near_p2p_history.py dry-run                  # free: the next chunk's exact GB
#    Jake edits config.NEAR_P2P_HISTORY_APPROVAL (approved True, approved_by, approved_on in THIS month, max_gb, chunks)
python near_p2p_history.py run
#    without approval it prints "NEAR P2P HISTORY NOT RUN ... Nothing billed; no BigQuery call was made."

# 6. build, check, export
python token_metrics.py
python done_check.py --verbose > logs\done.txt
python headline_check.py --audit token_metrics_audit.xlsx --reader token_metrics.xlsx --db metrics.db --out ..\tm-exports\headline_check.md
python project_freeze.py --compare all
foreach ($p in 'Aerodrome','Chainlink','Ethereum','GEODNET','Aethir','Plume','Near') { python store_export.py --project $p --out-dir ..\tm-exports }
```

What to expect in `logs\done.txt` after that pass (each is a prediction from the fixture or an export, not seen on the store):

| Project | Expected | If not |
|---|---|---|
| Aerodrome | READY; Flows Q0 shows "+ merger issuance" 116,999,999 and a residual within 0.5% | If the fees tier was skipped that day the three velo_* series land on the next daily run |
| Chainlink | READY if the store holds `ccip_token_value_usd`; theatre flag RAISED near 11.4x | Flag NOT TESTABLE means `actual_buyback_tokens` starts after 2025-08-10 on the store |
| Ethereum | The four "not yet split" lines, insider share of stake and staked-ETH staleness gone; settlement (key, rank, NRR) and four rule-3 lines remain | Settlement needs the BigQuery P2P rebuild: Artemis itself stopped publishing on 2026-08-25 |
| GEODNET | One or two lines: UNIFORMITY (a decision), insider share of stake (should clear) | — |
| Plume | Business slot 1 near $62.8M with a declared series break at 2026-05-21 | The done_check lines were never reproduced off the store: paste them |
| Aethir | Unchanged: allowance and three MATURING rows now run to 2026-12-31 | Run `python check_offline_items.py aethir_distributor_match`; with 10 daily runs stored, re-judge the rows instead |
| Near | Key and rank dated, not blocking; NRR blocks until the December P2P days are stored | — |

Frozen moves this round: `docs/moves_r18.md` (and `tests/fixtures/frozen_moves_r18.json` when any cell moved).

## 9. Decisions open after Round 18 (Jake's)

- Aerodrome: the 10M is explained (governance `Voter.spendSurplus`: the Gauge Caps' unminted surplus, minted to the
  Foundation Safe and burned whole; not merger-related, not a buyback). The 8.18M cap was kept as instructed; taking
  the whole 10M off the buyback line gives burned Q0 407,816 and key 0.0056 (now 2,226,496 and 0.0304).
- Aerodrome: dilution counts the cap rate; about a third of it accrued as surplus that mints only if governance
  spends it. Netting it needs a new stored series.
- Chainlink: Safe 0x77dd1a9b… paid 2,082,892 LINK straight to the same recipients (not in outflow b); whether the
  exchange leg moves to category (f) as Ether.fi's did; whether A1's "float after staking" takes the issuer stock out.
- GEODNET: fees = burn / 0.4 is twice GEODNET's only first-party figure ($4M ARR, May 2025 = burn / 0.8 within 0.5%).
  UNIFORMITY: declare the n/a, or wire the +20% staker bonus (at most 328,400 GEOD a year, 0.035% of circulating).
- Ethereum: keep the two INFERRED Foundation wallets (2,474 ETH) off the float or not; Master fees on DefiLlama
  ($37.19M) beside S13 fees on Blockscout ($37.97M).
- Near: 1 chunk a month (key forms 2027-05-31, 1,380 GB) or 2 (2027-03-31, 1,840 GB).
- Plume: the issuer list as wired (62.8M today) or one of the other bases; Plume's weekly RWA market share still
  reads the gutted category and should be treated as missing.
