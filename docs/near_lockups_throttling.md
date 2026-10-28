# NEAR lockups read: why it takes several passes, and what to do (SPEC P2f-3)

Written 2026-10-28 on branch `claude/p2f-overnight`. Read-only investigation from the code and the providers' PUBLIC
documentation; no key was used and no live call was made. Every documentation fact below carries its URL and the date it
was read (all 2026-10-28). Anything that could not be confirmed is marked **UNVERIFIED**.

No definition changed: the pinned block, the LOCKED definition, the buggy-code recomputation and every value the lockups
check computes are untouched. The code changes (section 4) alter only WHEN a call is made.

## 1. What the read costs (from the code)

* `near_lockups_at_block` (check_offline_items.py) calls `near_lockups(block_id=..., accounts=alive)`; the weekly task
  (`near_lockups_weekly.py`) calls `near_lockups()`. ~1,956 to 1,960 live lockups.
* Default route is `state` (`NEAR_LOCKUP_ROUTE`): per lockup `view_account` + `view_state` = 2 calls, and every 20th lockup
  (`NEAR_LOCKUP_CHECK_EVERY`) is also read by its getters (about 7.8 calls) as a cross-check. The code's own figure is 2.4
  calls a lockup (`NEAR_LOCKUP_CALLS_PER_LOCKUP["state"]`), so **about 4,700 calls a pass**, plus one `block` call to pin.
  The getters route (fallback if the check refutes the state route) is 7.8 calls a lockup, about 15,200 calls.
* Two calls a lockup is the floor for this design: the account balance and code hash come only from `view_account`, the
  schedule only from `view_state`.

## 2. (a) Endpoints, tiers, keys (hosts only; no key was read)

| Host | Used by | Key? |
|---|---|---|
| `rpc.mainnet.fastnear.com` | lockups, live route (`NEAR_RPC_LIVE[0]`, base spacing 0.25 s); check_offline_items probes | none sent |
| `rpc.mainnet.near.org` | lockups, live fallback (`NEAR_RPC_LIVE[1]`, base spacing 1.0 s); check_offline_items probes (lines ~963, ~990, ~2228); NearBlocks payer_out recipients read (config.py ~32949) | none sent |
| `archival-rpc.mainnet.fastnear.com`, `archival-rpc.mainnet.near.org` | `fetch.archive.NEAR_ARCHIVAL_DEFAULT`: lockups at a PAST block (`near_lockups_at_block`), NearArchive (tier-2 backfill: supply, staked) | none sent. Env var **`NEAR_ARCHIVAL_RPC_URL`** (comma list) REPLACES the default list; it is already supported, and the code prints hosts only |
| `rpc.mainnet.near.org`, `rpc.fastnear.com`, `free.rpc.fastnear.com`, `near.drpc.org` | tier 2 `near_rpc` (fetch/near.py, config.py `node_api.endpoints`, ~4170): status, validators, block, view_account, once a run | none sent |
| `api.nearblocks.io` | the lockup LIST (`near_lockup_accounts`), daily balances; not RPC | optional env var **`NEARBLOCKS_API_KEY`** (Bearer, never printed) |

There is no NEAR RPC key anywhere in the code today. The only keyed thing on this path is the optional NearBlocks key.
The code has no env override for the LIVE endpoints (`NEAR_RPC_LIVE` is a constant).

### Provider documentation (public)

1. **NEAR's own public RPC is rate limited hard and deprecated.** NEAR announcement, final phase from 2025-08-01:
   mainnet RPC **30 requests/min and 75 per 10 min**; archival mainnet RPC **4 requests/min and 10 per 10 min**. FastNear
   keeps those endpoints running at these limits. Recommended providers: FastNear, Lava. Source:
   https://near.org/blog/deprecation-of-near-org-and-pagoda-co-rpc-endpoints (read 2026-10-28). Whether these figures are
   still the live limits today is **UNVERIFIED** (no live call made); the code comment in check_offline_items.py that "neither
   endpoint publishes a limit" is out of date: NEAR published these.
2. **FastNear free tier.** NEAR's provider table lists `https://free.rpc.fastnear.com` as public, free tier, limit "Not
   published", archival "Paid only"; lists `https://archival-rpc.mainnet.near.org` as archival, "Severely rate limited", limit
   "Not published", no paid plan. https://docs.near.org/api/rpc/providers (read 2026-10-28). FastNear's own RPC page
   (https://docs.fastnear.com/rpc, read 2026-10-28) names `rpc.mainnet.fastnear.com` (regular, "the most recent epochs of
   state") and `archival-rpc.mainnet.fastnear.com` ("the full chain history"), says to send an API key on both, and states
   **no** rate limit for unauthenticated or free use. **The free-tier limit is UNVERIFIED (not published).**
   A search-result snippet claimed unauthenticated FastNear calls are limited to "1 call in 2**16-1 seconds per IP"; I could
   not find it on any FastNear page I fetched: **UNVERIFIED, and not consistent with Jake's runs** (config.py ~4168 and
   ~20437 record archival-rpc.mainnet.fastnear.com serving 400+ epochs back without a key).
3. **A key and a paid plan.** FastNear: a key goes in `Authorization: Bearer ...` (recommended) or `?apiKey=`; keys come from
   https://dashboard.fastnear.com. https://docs.fastnear.com/docs/rpc-api/api-key (read 2026-10-28). The docs say
   "authenticated requests with higher rate limits" (search summary of builder-docs.fastnear.com; the page itself gives no
   numbers). Plans on https://fastnear.com/ (read 2026-10-28): Dev $69/month, 10M RPC requests/month; Pro $199, 36M;
   Business $499, 100M; Enterprise custom, unlimited. **Requests per second, and archival access per plan, are not stated:
   UNVERIFIED.** A free key tier: UNVERIFIED (the dashboard page is client-rendered and showed nothing). The weekly read
   needs ~4,700 calls, about 20,000 a month: 0.2% of the Dev allowance.
4. **Archival without a key.** `archival-rpc.mainnet.near.org`: yes, public, but 4/min (item 1). `archival-rpc.mainnet.fastnear.com`:
   NEAR's table says paid only; in practice Jake's reads have been served unauthenticated (code comments above) but are
   throttled. Other archival providers in NEAR's table: QuickNode (free tier "15 req/s", needs an account and a URL with a
   token), All That Node (500K CU/day), Lava, Zeeve, node101 (paid). https://docs.near.org/api/rpc/providers (read 2026-10-28).
   Their real behaviour on `view_state` is **UNVERIFIED**.
5. **Other FREE, PUBLIC, NON-archival endpoints** (fine for the weekly read at a block under 48 h old; not for a past
   block). Same table: `near.blockpi.network/v1/rpc/public` 20 req/s; `api.zan.top/node/v1/near/mainnet/` ~20 req/s;
   `near.drpc.org` ~2,100 CU/s (compute units per NEAR call: UNVERIFIED); `rpc.intea.rs` not published; `1rpc.io/near`
   200 req/day (useless). Whether each accepts `view_state` and `block_id` on its free tier: **UNVERIFIED**.
6. **Batching: not available.** "nearcore does not support JSON-RPC batch requests ... Send one JSON-RPC request object per
   HTTP request" (https://docs.near.org/api/rpc/introduction, read 2026-10-28); a batch body gets HTTP 400, code -32700
   (https://docs.near.org/api/rpc/providers, read 2026-10-28). FastNear's pages say nothing about batching (UNVERIFIED).
   The same page warns "Never load test a shared public endpoint". For that reason I made **no** test calls.
7. **A different method?** `view_state` and `call_function` take one `account_id` each; the docs list no multi-account view
   (https://docs.near.org/api/rpc/contracts, read 2026-10-28). `view_state` returns at most 50 kB on default nodes
   (a lockup's state is far below that). `block_effects` lists accounts touched in one block, not their state
   (https://docs.near.org/api/rpc/block-chunk, read 2026-10-28). The state route (2 calls) is already the cheapest read of
   the primary data; the getters (7.8) were the old one.

## 3. (b) The pacer and the progress, as the code stood

| Question | Answer (check_offline_items.py, before this change) |
|---|---|
| Retry-After honoured? | Yes, per endpoint, but only as a plain number (`float()`); an HTTP-date fell to the ladder, and `inf` would have parked the endpoint for good. |
| Backoff | 5 s doubling to 60 s when no Retry-After. **No jitter.** One success reset the ladder to 5 s, so in a 90% 429 storm it never climbed. |
| Global or per call | The cool-down is per endpoint and shared by all threads (good). But the SPACING stayed at its base (0.25 s / 1.0 s): every cool-down ended with four workers firing at the base rate again. NearArchive (fetch/archive.py) already slows x1.5 on a 429; this pacer did not. |
| Concurrency | Bounded: 4 workers (`workers=4`), one `requests.post` each; all ~1,960 reads were queued at once. |
| Progress between passes | A local JSON cache in the LogCache root: `near-lockups-read.json` (weekly) or `near-lockups-at-<block>.json`; not metrics.db, not run_log. Saved every 25 completions and at the end. Per lockup: its figures. |
| Same block on resume? | Yes. The cache holds `height`; the weekly pin resumes for 48 h while a node still serves the block; an at-block read resumes only at that block. |
| 429'd lockup retried first or last? | LAST, and only in the NEXT run: a lockup whose call used up its 8 attempts was not cached and not tried again in the same run. |
| Already-read lockups re-read? | No (`todo` = lockups not in the cache). A lockup's partial work is not kept: if `view_account` answered and `view_state` failed, the next run asks `view_account` again. |
| Two ways progress was lost | (1) If the pin could not be fetched in 8 attempts (a 429 storm at the start), the run ended "no endpoint gave the final block". (2) On a resume, the "is the pinned block still served" check treated eight 429s as "not served" and **started a new pin from nothing**, discarding every lockup already read. (3) An interrupt waited for all queued reads, then dropped their results. |
| ETA honest about throttling? | Yes for the weekly read: calls x lockups / the calls-per-second measured end to end on the last full read, cooling included (default 0.92 calls/s from Jake's 2026-10-26 run), and the "min left" line extrapolates the elapsed time. For `near_lockups_at_block` the throughput is not saved (archival pace differs), so it quotes the weekly figure. |

Calls: about 2.4 a lockup, 4,700 a pass. Jake's measured end-to-end rate (15,183 calls in 4 h 35 m, 2,733 throttled) is
0.92 calls/s, i.e. about 85 minutes for 4,700 calls: this matches the ~79 min first pass.

**Theoretical minimum for ~4,700 calls at a documented limit:** FastNear free: no documented limit (UNVERIFIED), cannot be
computed. near.org public live: 75 per 10 min = 7.5/min = **10.4 h**; near.org archival: 10 per 10 min = **78 h**. Neither
public near.org endpoint can carry this read; FastNear's unauthenticated tier is the only one that has. Pace implied by the
code alone: 4,700 x 0.25 s = 20 min if FastNear never throttled.

## 4. What changed in code (safe without approval)

check_offline_items.py only; no endpoint, key, or paid plan added; same questions at the same block.

1. `retry_after_seconds`: delta-seconds or HTTP-date; negative, non-finite or unparseable falls back to the ladder; capped at
   600 s.
2. `_NearPacer`: jitter (production pacer only, 25%, never earlier than the server asked); a global, debounced slow-down of an
   endpoint's spacing (x1.5 per burst of 429s, ceiling 15 s a call) with recovery (x0.9 per 25 clean calls); the cool-down
   ladder resets only after 5 clean calls in a row; injectable clock and sleep for tests.
3. `near_lockups`: lockups still unread are tried again in the same run, last (up to 3 sweeps, stopping at a sweep that reads
   nothing new), at the same block; errors counted once per lockup; the pin fetch and the pin check retry 4 times, and the
   pin check treats "only throttled" as "unknown, keep the progress" (an endpoint that ANSWERS "no such block" still ends the
   pin); an interrupt cancels the queued reads and saves what was read.
4. Tests: tests/test_p2f3_near_throttling.py (fakes and a fake clock; no network). It asserts the same lockup figures (a
   hand-computed 42 / 21 / 10 / 21 NEAR) on a clean run and under five 429 / 5xx / reset / hostile-header sequences.

## 5. Options for Jake (not implemented)

N = ~4,700 calls. Times are N divided by the rate shown; only rows 0 and 4 are measured or documented.

| # | Option | Needs | Rate | Time / pass | Risk |
|---|---|---|---|---|---|
| 0 | Today, before this change | nothing | 0.92 calls/s measured | ~85 min, 2-3 passes (~2.2 h) | what happened |
| 1 | This change (adaptive spacing, in-run retry, pin kept) | nothing | whatever FastNear accepts (UNVERIFIED) | N / accepted rate: 1/s 78 min, 2/s 39 min, 5/s 16 min; no manual reruns | none for figures; may still be slow if the free tier is tight |
| 2 | FastNear Dev plan ($69/mo, 10M requests/mo) via `FASTNEAR_API_KEY`, Bearer on `*.fastnear.com` only | Jake creates a key; ~15 lines of code (host-only logging) | per-second limit UNVERIFIED (ask FastNear) | UNVERIFIED | a monthly cost; the key must stay out of the repo and logs |
| 3 | A documented 15 to 20 req/s provider as an extra live endpoint (ZAN, BlockPI: 20 req/s) | a new URL in `NEAR_RPC_LIVE` (and an env override, e.g. `NEAR_RPC_URLS`) | 20/s documented | ~4 min (weekly read only; not archival) | UNVERIFIED that it serves `view_state` + `block_id`; a new endpoint to trust (the every-20th getter cross-check would catch a wrong state) |
| 4 | A keyed archival provider for `near_lockups_at_block` (QuickNode free tier lists 15 req/s) via the EXISTING env var `NEAR_ARCHIVAL_RPC_URL` | Jake creates an account; no code change | 15/s documented | ~5 min | the URL contains a token: keep it in `.env` only; hosts only are logged |
| 5 | Stay on near.org | nothing | 7.5/min live; 1/min archival | 10.4 h live; 78 h archival | not viable; do not rely on it |
| 6 | JSON-RPC batching | not supported by nearcore | n/a | n/a | n/a |

**Recommendation.** Keep this change (it only helps). Then, for the weekly read, ask FastNear what the unauthenticated
tier and the Dev plan allow per second; if Dev is the answer, option 2 is the smallest dependency (one key, one host).
For the one-off `near_lockups_at_block` overlap check, option 4 costs no code.

## 6. RUNBOOK text (RUNBOOK.md not edited)

> NEAR lockups and 429s (P2f-3). The read now slows its calls to an endpoint after a 429 (and speeds up after 25 clean calls),
> adds a little random delay to each wait, tries lockups it could not read again at the end of the same run (up to 3 sweeps),
> and keeps its pinned block when the pin check itself is throttled. The progress line shows `pacer {... 'slowdowns': n}`.
> If a run still ends with "not yet read", run `python near_lockups_weekly.py --force` (or `near_lockups_at_block` again): it
> reads only the missing lockups, at the same block. The figures do not change. Details and options: docs/near_lockups_throttling.md.
