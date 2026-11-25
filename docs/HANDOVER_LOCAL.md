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

## 8. Rounds 21 and 21b (2026-11-25) — burns in the net, bought-back rules, Jake's decisions: THE NEXT RUN ON THE STORE, IN ORDER

Round 21b applied Jake's decisions on Round 21: Hyperliquid's Assistance Fund is BURNED (the validators' vote, HyperFND
2025-12-17 proposal / 2025-12-24 result, 85% of stake for) — out of circulating and total supply, its buybacks
buy-and-burn, tags A3, A1, A4 (A4 scored on the Fund + the Core burns); Morpho's founder vest() claims of vested tokens
are not new supply (the 6, 11 and 23 Nov claims leave (e) and show as a named insider float movement); fee burns are
revenue sent to holders in A3's key (Aethir tagged A2, A3, A4 with the declared direct-fee-burn wording; Ethereum A1,
A3, A4; Near's gas burn beside its Intents buyback); NEAR's December P2P chunk approved; the keyless beacon node is
Ethereum's standing staked-ETH source. 239 more frozen cells moved on the fixture (all Hyperliquid), declared in
`docs/moves_r21.md` (section "Round 21b").

Round 21 changed rules (Jake's): every burn counts in net float pressure as its own line, separate from buying
(net float pressure = revenue-funded buying + burns − new supply − bought tokens returned, all ÷ free float); the
bought-back rules F1–F7; paused / off programmes show their label and "unranked" (decision 5); Hyperliquid's
Assistance Fund is a held buyback by default; Aethir's burn reader fixed and its unlocks read on the published schedule;
Pendle's buying reads its own buyback contract on chain. Nothing ran on the store and nothing was frozen. 259 frozen
cells moved on the fixture, each declared with its reason: `docs/moves_r21.md` (Round 20's are in `docs/moves_r20.md`).

Baseline (Jake's 24 Nov run on 4e1231d): done_check 12 READY; Aethir and Pendle NOT READY; Plume on hold; World Mobile
parked; both workbooks recalc 0 errors; check_workbook and check_nets printed []; compare all showed 1 undeclared move
on each of the nine (the cp_nfp Trend row — now declared).

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
#    expect: ALCHEMY_API_KEY PRESENT (Berachain asks it first); BEACONCHAIN_API_KEY says "nothing: ... the keyless
#    public beacon node is the standing staked-ETH source" (R21b) — no key is needed or used
python -m fetch.aethir_chain
#    stores nothing. Expect "schedule matches"; the burns read FIRST (no longer after the slow history): 22+ days from
#    2026-11-02, sum >= 11,955,182.09 ATH, one stray 0.0159 ATH transfer excluded; the claims check
#    "claimed <= scheduled" may fail for Q-4 (advisor vesters) — a printed finding, not an error

# 2. the daily run (takes the run lock itself). New this round, filled with no extra command:
#    Aethir      gross_burn_tokens has rows again (the 24 Nov run stored 0: the burn read sat behind the history scan)
#    Pendle      pendle_buyback_chain: every PENDLE in and out of the buyback contract 0x9e08…896b (~6 getLogs, <=100 s)
#    prices      the on-chain pool cross-check replaces coins.llama.fi in the price check (Uniswap v3 / Sky v2 pools,
#                GEODNET on Polygon, Hyperliquid's own book; Coinbase stays for ETH, LINK, NEAR, AERO, UNI)
#    Uniswap     World Chain's TokenJar slice is a named gap with its cap (~0.23% of inflow), never a TIER TIMED OUT
#    Run Log     "Http client" lines are relabelled by what they time (web calls, waits); longest tiers start first
python token_metrics.py

# 3. catch-ups
python archive_backfill.py --project Aerodrome --run --budget-min 45
#    as last round; re-run to continue
python token_metrics.py --seed pendle_berachain
#    stops itself after 10 minutes and names what it left unread (Berachain is 0.0001% of Pendle's TVL; Q-3 lacks
#    590.18 and Q-4 18,126.19 PENDLE of gauge emissions; Q0..Q-2 unaffected). Free Alchemy plan: 10 blocks a call,
#    so the 10,000-block ranges go to rpc.berachain.com. Only if the Alchemy plan is paid: add
#    ALCHEMY_GETLOGS_MAX_RANGE=10000 to .env first.
python token_metrics.py --seed tokenjar

# 4. build, check
python token_metrics.py
python -c "import free_float as f; a='token_metrics_audit.xlsx'; print(f.check_workbook(a,'token_metrics.xlsx')); print(f.check_nets(a)); print(f.check_burns(a))"
#    expect: []  []  []
#    check_workbook: every tab shows the one free float; check_nets: each net equals its sides; check_burns (NEW):
#    every project's burn series is read by its net float pressure, or declared out of it with its reason
python done_check.py --verbose > logs\done.txt
python headline_check.py --audit token_metrics_audit.xlsx --reader token_metrics.xlsx --db metrics.db --out ..\tm-exports\headline_check.md
python project_freeze.py --compare all
#    expect: nine lines, each "0 undeclared move(s)" (compare all reads the 15 dumps, not the store)
foreach ($p in 'Aerodrome','Chainlink','Ethereum','GEODNET','Aethir','Near','Morpho','Ether.fi','Maple','Sky','Pendle','Fluid','Hyperliquid','Uniswap') { python store_export.py --project $p --out-dir ..\tm-exports }
```

### On or after 2026-12-01 — NEAR's December P2P history chunk (approved, R21b)

Jake's dated approval is in config (NEAR_P2P_HISTORY_APPROVAL: approved 2026-11-25, for 2026-12 only, max 337 GB —
the month's room in the 1,000 GB budget after Ethereum's 31 weeks and the top-up reserve). One chunk, 2025-11-01..11-30.
```
python near_p2p_history.py plan
#    "2026-12   2025-11-01..2025-11-30   30   226.2 ..." on the first row of THE COMBINED SCHEDULE
python near_p2p_history.py dry-run
#    a free dry run; its GB must be <= 337 (else it refuses and says what to approve)
python near_p2p_history.py run
#    the chunk; refused before 2026-12-01 or after 2026-12-31, and after the month's one chunk
```

### Expected done_check after that run

Read from a build of the ten store exports on file (Aerodrome, Ether.fi, Maple, Morpho 20 Nov; Fluid, Pendle 18 Nov;
Sky 17 Nov; Hyperliquid 16 Nov; Near, Uniswap 20 Nov) at this code, each at its own as-of; not seen on the store.

| Project | Expected | Notes |
|---|---|---|
| Aerodrome, Ether.fi, Maple, Uniswap, Sky | READY | READY on their exports at this code. |
| Hyperliquid | READY | READY on its 16 Nov export at R21b. The Fund out: circulating about 539.5M, total supply about 949M; the A4 tab filled (burn ÷ issuance about 0.40 on the export); Credibility records in_total_supply_api at about −4.9% (the API still counts the Fund — named, not judged); in_total_supply_coingecko within 1%. |
| Near | READY | READY on its 20 Nov export at R21b; A3 key 0.190 → 0.202 with the gas burn in the numerator (A3 secondary; its primary is A1). |
| Morpho | READY | READY on its 20 Nov export at R21b. Key 0, rank "unranked" with its label "off" (decision 5). Total dilution 45.1% → 27.2% of float on the export (the 6 and 11 Nov claims out of (e)); on the store, with the 23 Nov 65M also out and on the float, about 23.9%. The Flows sheet's 2026-11-23 row: "± issuer" about +65.0M with the named insider line 65,000,000 (award #18 12.5M → 0xb6914e90…, #17 52.5M → 0xd9e0f068…). |
| Fluid | READY | READY on its 18 Nov export at this code. Key and rank read "paused, no restart condition stated" and "unranked" (decision 5). |
| Pendle | READY | Was STALE on 24 Nov. The new chain reader dates each epoch's buyback on the day it is bought, so buying pressure is current. On the 18 Nov export without the reader's rows it still reads STALE (16 days; its only lines); with them, READY (the Pendle lane's build). |
| Chainlink, GEODNET, Ethereum | READY | READY on 24 Nov. Chainlink's net holder yield is allowed "blocked until the history is complete" (Jake, item 8). Ethereum gains an A3 tab (secondary): its key is the EIP-1559 burn $ ÷ new supply; its buyback cells read N/A by the decided route "nothing is bought" (R21b fixed a Coverage line that read stale buyback copies as missing history). |
| Aethir | 1 line | The burn has rows again; the unlocks read the published schedule; the buyback line is the declared direct-fee-burn wording (R21b). What stays: UNIFORMITY ("no staker yield series is wired": the pools are emissions-funded) — section 9. |
| Plume | on hold | No change. |
| World Mobile | parked | No change. |

### Freeze commands (do NOT run until done_check reads READY on the store AND the full gate is green)

One commit per project. The nine already frozen are re-baselined (their declared moves stay in config as the record);
Chainlink, GEODNET, Ethereum and Near are newly frozen.
```
python gate.py --rev HEAD --out ..\tm-gate --workers 3
#    expect GATE_EXIT 0

# the nine, re-baselined
foreach ($p in 'Aerodrome','Ether.fi','Fluid','Hyperliquid','Maple','Morpho','Pendle','Sky','Uniswap') {
  python project_freeze.py --write $p
  git add tests/fixtures
  git commit -m "freeze: re-baseline $p after Round 21 (declared moves kept; done_check READY on the store, gate green)"
}

# the four new ones: first add the entry at the END of config.py (copy the Ether.fi block), e.g. for Chainlink
#   FROZEN_PROJECTS["Chainlink"] = {
#       "baseline": "tests/fixtures/frozen_chainlink.json", "since": "<today>",
#       "why": "done_check READY on Jake's store at this code (export <its store_export file>); the full gate green"}
# (baseline names: frozen_chainlink.json, frozen_geodnet.json, frozen_ethereum.json, frozen_near.json)
foreach ($p in 'Chainlink','GEODNET','Ethereum','Near') {
  # (add that project's FROZEN_PROJECTS entry to config.py, then:)
  python project_freeze.py --write $p
  git add config.py tests/fixtures
  git commit -m "freeze: $p (done_check READY on the store, gate green)"
}
python project_freeze.py --compare all
#    expect: thirteen lines, each "0 undeclared move(s)"
git push -u origin claude/crypto-metrics-supply-demand-ovkg8g
```
Aethir, Plume and World Mobile are not frozen; freeze Aethir when it reads READY.

## 9. Decisions open after Round 21b (Jake's)

- Aethir UNIFORMITY: done_check's one remaining Aethir line ("no staker yield series is wired"). The pools pay ATH from
  RewardFaucets fed only by the issuer Safe 0x3e7e6c3d… (emissions). Declare it n/a with that reason, or wire the
  pools' emissions-funded staker yield?
- Hyperliquid A4: scored on the Fund + the Core burns (G7 0c, 2026-10-19: the HyperEVM gas burn is A1 only, not
  rewritten by R21b). To score A4 on every burn, set `R21B_HL_A4_METRIC = "total_burn_tokens"`.
- Hyperliquid: CoinGecko's and CMC's circulating are 127% / 114% below ours (237.7M / 252.6M vs 539.5M on the 16 Nov
  export) — the R9.2 rule basis (team 238.25M + Foundation stake 60.64M + Grants 2.98M counted in), not the Fund.
  Unchanged; recorded.
- Morpho: Jake's text covers the founder awards. Claims on the other vested Vester awards (#1–9, #11, #12, #16, #19–23)
  still read as releases — treat them the same? Awards #10, #13, #14, #15 are tagged INFERRED founders (from the exact
  152M sum); #17/#18 are Paul Frambot's. 32M founder tokens are still unclaimed (#14 26M, #15 6M): a future claim reads
  as a release until it is added to config (MORPHO_FOUNDER_VESTER `claims`).
- Highlights: three cards were written for the old tags and key and may not render (left alone, as instructed).
