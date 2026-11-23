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

## 8. Round 20 (2026-11-24) — rules, free float and fixes: THE NEXT RUN ON THE STORE, IN ORDER

Round 20 changed rules (Jake's): new archetype definitions and tags, a new A3 key (revenue sent to holders ÷ value of
new supply), ONE free float that every tab reads (`free_float.py`), daily float movements on the Flows sheet, two nets
(net holder yield on market cap; net float pressure on the free float), "history filling", and the run fixes of the
23 Nov store run. Nothing ran on the store and nothing was frozen. 1,118 frozen cells moved on the fixture, each
declared with its reason: `docs/moves_r20.md`. Rounds 18 and 19 are in git history and in docs/r19_*.md, docs/r20_*.md.

Store state Jake reported on 23 Nov: done_check 10 READY (nine frozen + Chainlink); NOT READY Ethereum 8, Aethir 9,
GEODNET 1, Near 1; Plume on hold.

### New key Jake must add (after 2026-12-01 15:02 UTC, not before)

beaconcha.in is the staked-ETH, validator-count and staking-yield source from Round 20. Its docs offer no standing free
plan: a "Free Trial" of 30 days (1 request a second, 1,000 requests, one key), then 59 EUR a month. Until a key is
set, the keyless public beacon node stores the day's staked ETH (cross-checked against ultrasound.money), so the
series is live again from the next run; 2026-10-26 to that day is a named hole the key's first run closes.
1. After 2026-12-01 15:02 UTC (the declared cooldown; starting earlier wastes trial days): log in at
   https://beaconcha.in/login, open https://beaconcha.in/user/api-key-management, start the Free Trial, create the key.
2. In `C:\Users\jake\Crypto-Tracker\.env` add the line `BEACONCHAIN_API_KEY=<the key>` (no quotes; nowhere else;
   never paste it into a chat or a commit).
3. `python check_offline_items.py env_keys` shows PRESENT, never the value. Do NOT run `ethstore_check` (it spends
   trial requests). Never run `staked_eth_rebuild.py`: it now refuses (the 1,170 GB query is retired).
Decision for Jake when the trial ends: pay, or make the keyless node the standing source.

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
#    expect: BEACONCHAIN_API_KEY ABSENT until December, the others PRESENT
python -c "import sqlite3; c=sqlite3.connect('file:metrics.db?mode=ro', uri=True); [print(r) for r in c.execute('select date, value, fetched_at from metrics where project=? and metric=? and date>=? order by date', ('Morpho','noncirculating_holding_tokens','2026-11-18'))]"
#    Morpho: 2026-11-20 = 283719215.78; a later row 16.5M lower (run before 15:37 UTC on 23 Nov) or 65M lower (after)
python -m fetch.aethir_chain
#    stores nothing: "schedule matches", two DIRECT and two INFERRED wallets with locked 0.00, 21+ burns >= 11,955,182.09 ATH
python near_p2p_history.py plan
#    starts "THE COMBINED SCHEDULE", chunks 1/2/2/3, key forms 2027-03-31

# 2. the daily run (takes the run lock itself). New this round, filled with no extra command:
#    Ethereum   staked ETH from the public beacon node ("cross-check agrees"); EF wallets; settlement P2P pieces
#               (5 of 13 fit November's budget, the other 8 on the first December run: all 89 days by 2026-12-01)
#    Aethir     aethir_chain: vesting stock, issuer wallets, insider stake, the daily fee burn (455 past days once)
#    Aerodrome  gauge_caps_surplus_tokens ("over 9 pot(s)"); aero_leaf lines for the seven other chains
#    Chainlink  node_payments_safe_out and exchange_leg_out scans (the flag cell opens "RAISED - 11.4x ...")
#    Uniswap    tokenjar has 900 s (no TIER TIMED OUT)
#    Near       NearBlocks tier budget 750 s: no "NOT CALLED" line
python token_metrics.py

# 3. catch-ups
python archive_backfill.py --project Aerodrome --run --budget-min 45
#    "Gauge Caps surplus ... ~33 row(s) written"; "staker revenue on the other chains ... N row(s)" (5-6 min an epoch; re-run to continue)
python token_metrics.py --seed pendle_berachain
#    "HISTORY REPAIR ... re-reading blocks", every trace line naming rpc.berachain.com, then reconciled
python token_metrics.py --seed tokenjar

# 4. build, check
python token_metrics.py
python -c "import free_float as f; print(f.check_workbook('token_metrics_audit.xlsx','token_metrics.xlsx')); print(f.check_nets('token_metrics_audit.xlsx'))"
#    expect: [] and []   (every tab shows the one float; each net equals its two sides)
python done_check.py --verbose > logs\done.txt
#    expect a line "HISTORY FILLING: N cell(s) shown grey, not counted"
python headline_check.py --audit token_metrics_audit.xlsx --reader token_metrics.xlsx --db metrics.db --out ..\tm-exports\headline_check.md
python project_freeze.py --compare all
foreach ($p in 'Aerodrome','Chainlink','Ethereum','GEODNET','Aethir','Near','Morpho','Ether.fi','Maple','Sky','Pendle','Fluid','Hyperliquid','Uniswap') { python store_export.py --project $p --out-dir ..\tm-exports }

# 5. December only (on or after 2026-12-01), each needing Jake's dated approval in config
python near_p2p_history.py dry-run
python near_p2p_history.py run
```

### Expected done_check after that run (predictions from the fixture and four exports; not seen on the store)

| Project | Expected | Notes |
|---|---|---|
| Aerodrome, Ether.fi, Maple, Morpho | READY | READY on the 20 Nov exports at this code. Morpho's total dilution will read about 89% of float unless the two wallets that received 65M MORPHO from the DAO treasury on 23 Nov are classified (section 9). |
| Fluid, Hyperliquid, Pendle, Sky, Uniswap | READY expected | Not built from their exports this round. New columns (dilution % of market cap, net float pressure) block wherever net holder yield already blocked. |
| Chainlink | READY | The (f) cells read BLOCKED until its scans store rows (first run). |
| Ethereum | 3 lines: key, rank, NRR | They form once the 89 settlement days are in (by 2026-12-01) if the Artemis CSV + rebuild are hole-free for 365 / 730 days; else "history filling". Staked ETH live again; the four rule-3 lines declared; insider share and yield form. Risk: the static `validator_apr_gross` reference can read BROKEN against a daily yield; burn_30d reference was BROKEN on the fixture. |
| Aethir | 2 lines: UNIFORMITY, "no buyback programme declared" | Both true statements needing Jake's declaration. Float falls by 7.72bn (issuer wallets off). |
| GEODNET | READY | Uniformity is declared n/a; insider share should clear on the store. |
| Near | READY or 1 line | Key, rank and NRR read "history filling" (not counted). |
| Plume | on hold | No change. |

### Freeze commands (do NOT run until done_check reads READY on the store AND the full gate is green)

Already-frozen projects moved this round: re-baseline each (the declared moves stay as the record), one commit each.
```
python gate.py --rev HEAD --out ..\tm-gate --workers 4
foreach ($p in 'Aerodrome','Ether.fi','Fluid','Hyperliquid','Maple','Morpho','Pendle','Sky','Uniswap') { python project_freeze.py --write $p }
python project_freeze.py --write Chainlink
python project_freeze.py --write GEODNET
#   for Chainlink and GEODNET also add a FROZEN_PROJECTS entry at the end of config.py (copy the Ether.fi block:
#   baseline tests/fixtures/frozen_<name>.json, since <today>, why <the store export it read READY on>)
python project_freeze.py --compare all
git add tests/fixtures config.py
git commit -m "freeze: <project> (done_check READY on the store, gate green)"
```
Ethereum, Aethir and Near are not expected READY on this run; freeze them when they are.

## 9. Decisions open after Round 20 (Jake's)

- Morpho: who controls 0xb6914e90… (12.5M) and 0xd9e0f068… (52.5M)? If Morpho's, add them to the issuer set (a
  treasury move); if not, the 65M is a real unlock and dilution near 89% of float is right.
- Hyperliquid: the Assistance Fund's HYPE can be moved by a hard fork (the Foundation's own words). Treat as a held
  buyback (circulating +49.3M, float unchanged, A4 off) or keep as burned? A4 is off either way this round.
- Net float pressure for projects whose buying is a protocol burn (Uniswap reads −3.35%, Ethereum n/a): should
  fee burns count as the demand side?
- Paused / off keys read 0 and rank at the bottom even where the project has no data (Ether.fi on the fixture).
- The eight rule questions in docs/r20_research_frozen_audit.md (buybacks paid to non-holders, buybacks paid to
  stakers in net float pressure, staking rewards paid from bought-back tokens, the wording of "paused", one-off
  buybacks, in-kind airdrops, held-buyback falls, authorised-but-unissued supply).
- beaconcha.in after the 30-day trial: pay or use the keyless node.
- Aethir: outflow (e) on the published-schedule model (about 1.04bn a quarter) or the measured vester claims (406M).
- Highlights: three cards were written for the old tags and key and may not render.
