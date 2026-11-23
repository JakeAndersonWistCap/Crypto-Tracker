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
