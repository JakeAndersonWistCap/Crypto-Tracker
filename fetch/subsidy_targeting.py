"""
fetch/subsidy_targeting.py — tier 2: SUBSIDY TARGETING BY POOL (G8, 2026-10-20; the owner's priority item). For
Aerodrome and Pendle: each pool's emissions against that pool's OWN fees, per complete epoch. It answers whether the
emissions go where fees are earned, not only how much is emitted. Every route was called live on 2026-10-20 (keyless).

STORED, per project per complete epoch, dated the epoch's START (UTC day):
    subsidy_fee_cover_pools              Σ fees_usd ÷ Σ emissions_usd over the pools receiving emissions (a ratio)
    subsidy_emissions_to_covered_share   Σ emissions_usd in pools whose own fees_usd ≥ their emissions_usd
                                         ÷ Σ emissions_usd (0..1)
    subsidy_emissions_usd                Σ emissions_usd (the epoch's emissions, valued)
and the per-pool table data/subsidy/<project_lower>_<epoch_start>.csv (CSV_COLUMNS; a blank cell is UNKNOWN, never 0).

UNKNOWN IS NEVER 0. A pool receiving emissions whose fees cannot be valued (a fee token with no price, or with no
decimals(), or no Sugar record) is left out of BOTH sides of the two ratios. They are stored MARKED PARTIAL, naming the
pools and their share of the emissions, only while that share is ≤ spec max_unvalued_share (default 5%, the
chainlink_fees cap). Above that share both ratios are refused. subsidy_emissions_usd does not depend on fees and is
stored clean, unless the emission token itself has no price (then all three are refused). A pool whose EMISSIONS are
unknown (Pendle: rate samples too far apart) refuses the epoch, because its share cannot be bounded.

=== AERODROME (route "aerodrome_sugar", Base) ===
Aerodrome's RewardsSugar 0x1b121EfDaF4ABb8785a315C51D29BCE0552A7678 (verified source on Sourcify, read 2026-10-20:
velodrome-finance/sugar RewardsSugar.vy + modules/lp_shared.vy). epochsByAddress(limit, offset, pool) returns
    LpEpoch(ts, lp, votes, emissions, bribes[(token, amount)], fees[(token, amount)])
for epoch start  block.timestamp // WEEK * WEEK - offset * WEEK  (offset 0 = the epoch in progress, never used):
    emissions  gauge.rewardRateByEpoch(ts): a PER-SECOND rate in AERO wei, which the gauge sets at the notify as
               amount ÷ time LEFT in the epoch. rate × 604,800 / 1e18 (Sugar's and the UI's figure) is the epoch's
               AERO only for a gauge notified at the flip. A late distribute() inflates it without bound: USDC/STAR,
               10-01, notified minutes before the week ended, read 716,751 AERO for about 1,305 streamed (14% of the
               epoch's "emissions", from one pool with 0.01% of the votes). So emissions_tokens is the AERO actually
               STREAMED in the epoch: each emitting gauge's rate is also read at the block early_s (3 h) into the
               epoch (an ARCHIVE eth_call; mainnet.base.org serves state 20M blocks back, 2026-10-20). Unchanged:
               rate × WEEK (≤ 1.8% over). Changed (1 gauge on 10-08, 3 on 10-01): the notify block is bisected and
               the two rate segments integrated (_aero_streamed). It is all the AERO the gauge streams: a partner's
               notifyRewardWithoutClaim top-up re-sets the same rate, so state cannot tell it apart from the
               Voter's distribution.
    fees       the FeesVotingReward's tokenRewardsPerEpoch[token][ts], in the pool's own tokens (raw units): what the
               gauge's _claimFees booked during the epoch. It runs inside the epoch's distribute(), so this is mostly
               the fees the pool's STAKED liquidity earned over the PREVIOUS week (fetch/aero_voter.py, same read),
               and it is what veAERO voters are paid. Unstaked LPs keep their own fees, which are not counted.
               Pairing epoch E's emissions with epoch E's fee booking is the contract's own per-epoch record. Both
               were set by the same distribute() call, so a pool that received emissions always had its fees claimed.
               The time-matched alternative (E's emissions against the E+1 booking) reads 0 fees for every pool that
               lost its votes, because nothing claims those fees. Both values are final once the epoch has ended.
    votes      the bribe contract's supply at the epoch's end (veAERO weight).
THE POOL SET: lp_shared._pools() behind epochsLatest walks EVERY pool of every factory (tens of thousands) and serves
only the epoch in progress, so it is not used. Instead: Voter 0x16613524… (verified 2026-10-20: ve() = veAERO
0xebf418…) length() / pools(i) / gauges(pool) give the gauged pools (2,013), and gauge.rewardRateByEpoch(E), a
per-epoch mapping readable now without an archive node, picks the pools with emissions in E (714 for 2026-10-08).
A gauge whose rate read reverts is UNKNOWN (refusing the epoch) unless no pool stands behind it (token0() reverts:
Voter pool 0x97cd4eb6… is a 128-byte always-revert contract with a getReward-only gauge 0x287c94a1…), which is left out
and named with its vote share. All are read through Multicall3 aggregate3 (500 a call). Sugar is called 20 pools per
aggregate3: each call costs more
than 12M gas, because Vyper allocates the 200 × 2 × 50 maximum DynArray in memory. 25 ran, while 50 exceeded the
node's 600M eth_call cap (2026-10-20). A gauge killed since the epoch gets NO Sugar record (isAlive is false now), so
its fees are unknown. Prices: DefiLlama coins historical at the epoch's END (fetch/aero_voter.py's convention), decimals
from each token's decimals() on chain. A price below spec min_confidence (default 0.9) counts as unpriced. Calls for one
epoch: about 10 for structure (once a run), 5 + 2 for rates (final and early), about 19 bisection batches when a gauge
was late, 36 for Sugar, 6 for metadata, and about 10 for prices. That is about 60-85 calls at 1 s spacing, roughly one
epoch per 120 s budget. A first read of epochs_back=4 therefore takes 2-4 daily runs, and after that one epoch a week.

=== PENDLE (route "pendle_gauge", Ethereum markets) ===
Fees per market per 14-day sPENDLE epoch: GET api-v2.pendle.finance/core/v1/statistics/market-fees?epochs=N&
includeOngoing=true. epochs is the number of COMPLETE epochs, ≤ 13 (a 400 answer above that). includeOngoing adds the
running one (read 2026-10-20: epochs=1&includeOngoing=true -> 10-20 ongoing + 10-06). Per market it gives poolFeeUsd
= explicitSwap + implicitSwap + limitOrder fees = protocolFeeUsd + lpFeeUsd (checked on reUSD), plus realizedYtFeeUsd
(the yield fee on YT, NOT trading by the subsidised liquidity). fees_usd = Σ spec fee_fields, default ["poolFeeUsd"],
valued in USD by Pendle. Markets with 0 fees are listed (229 of 330 on 10-06), so a market MISSING from the list is
unknown, never 0.
Emissions: GaugeController 0x47D74516B33eD5D70ddE7119A40839f6Fcc24e57 rewardData(market) -> (uint128 pendlePerSec,
accumulatedPendle, lastUpdated, incentiveEndsAt). ONLY THE CURRENT STATE IS READABLE KEYLESSLY. On 2026-10-20
ethereum-rpc.publicnode.com refused a historical eth_call even 1,000 blocks back ("Archive requests require a personal
token"), and refused eth_getLogs past ~5,000 blocks (~17 h). So no past epoch's rates (state or UpdateMarketReward
events) can be read from a primary source without a key. THE DESIGN: every run SAMPLES rewardData for every
market ON EVERY CHAIN WITH A GAUGECONTROLLER ON FILE (pendle_gauge_chains: mainnet, arbitrum, optimism, bsc, base,
mantle, sonic, berachain, hyperevm — G9 A1; G8 sampled chain 1 only) into data/subsidy/pendle_rates_<epoch_start>.json,
each sample tagged with its chain. Once the epoch is complete, each market's PENDLE is summed DAY BY DAY over the UTC
days on which ITS chain was sampled (pendle_emissions_by_day: inside a day, the nearest of that day's samples, capped at
incentiveEndsAt). A day with no sample on a chain is NEVER extrapolated and never refuses the epoch: it is excluded and
every figure of the epoch is stored PARTIAL naming the chain and days (".coverage.txt" beside the table keeps them for
the re-derivation). A market that a successful sample did not list counts as rate 0 at that sample. Nothing is stored
until the first sampled epoch completes (2026-11-03 for the epoch that began 2026-10-20).
WHY NOT THE PROXY: pendleApy × tvl from /core/v3/{chain}/markets/{addr}/historical-data serves history, but it is a
DERIVED APY that Pendle annualises from the rate (two derivation steps from the state). It would be stored under its
own "proxy" source, never this one. Gauge votes are no signal: voting ended under sPENDLE (lastEpochVotes 0), so votes
is blank. Each chain's rewardData is read from that chain's own GaugeController on its public endpoints. Prices: PENDLE
from DefiLlama coins at the epoch's end (one token, bridged).

INCREMENTAL AND BOUNDED. An epoch whose CSV exists is never read again. Its summary is re-derived from the CSV with no
network call, so a run that died after writing a table still stores the points. An epoch is written only after every
read succeeded. A transient failure (HTTP, RPC) writes nothing, and the next run retries. A genuine unknown (a token
DefiLlama has no price for) writes the table with blanks and is not retried. spec budget_s (default 120) bounds a run.
Epochs are read newest first, and a run that runs out of time stops cleanly, keeping the epochs already written.
POLITE: Http spacing 1 s, 4 retries. mainnet.base.org answers HTTP 429 "25/second request limit reached" when calls are
weighted heavily, and Http backs off on it. eth_getLogs is NOT used: it is refused keylessly on mainnet.base.org
("request limit reached") and on publicnode ("archive needs a personal token"), and base.blockscout.com serves a
Cloudflare challenge (2026-10-20). Keyless routes, and only hosts are ever logged.
"""
from __future__ import annotations

import csv
import functools
import io
import json
import logging
import time
import urllib.parse
from pathlib import Path

import pandas as pd

import config

from .base import BudgetExhausted, Http, LogEntry, tidy

log = logging.getLogger("token_metrics.fetch.subsidy_targeting")

SOURCE = "subsidy_targeting"
TIER = 2
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = "data/subsidy"
WEEK = 7 * 86_400
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"     # mds1/multicall3, same address on Base and Ethereum
LLAMA = "https://coins.llama.fi/prices/historical"
PRICE_BATCH = 80                    # keys per coins call (check_offline_items._llama_prices)
MC_BATCH = 500                      # light view calls per aggregate3
SUGAR_BATCH = 20                    # Sugar calls per aggregate3: 25 ran, 50 exceeded the 600M gas cap (2026-10-20)
PENDLE_MAX_EPOCHS = 13              # market-fees answers 400 above 13 (2026-10-20)
# DefiLlama's own flag of a weak price. On 2026-10-20, 342 of the 346 priced tokens in Aerodrome's 714 emitted pools read
# 0.99 and 3 read 0.8 ($785 of $3.78M emissions), so a price below it counts as unpriced at almost no cost
MIN_CONFIDENCE = 0.9

M_COVER = "subsidy_fee_cover_pools"
M_SHARE = "subsidy_emissions_to_covered_share"
M_EMIT = "subsidy_emissions_usd"
METRICS = (M_COVER, M_SHARE, M_EMIT)
CSV_COLUMNS = ["epoch_start", "pool", "name", "emissions_tokens", "emissions_usd", "fees_usd", "cover", "votes"]

SUGAR_OUT = "(uint256,address,uint256,uint256,(address,uint256)[],(address,uint256)[])[]"


# ===================================================================================================== ABI helpers
@functools.lru_cache(maxsize=64)
def selector(sig: str) -> bytes:
    from eth_utils import keccak                          # noqa: PLC0415 — web3's dependency, as fetch/chain.py
    return keccak(text=sig)[:4]


def _enc(types: list, values: list) -> bytes:
    from eth_abi import encode                            # noqa: PLC0415
    return encode(types, values)


def _dec(types: list, data: bytes):
    from eth_abi import decode                            # noqa: PLC0415
    return decode(types, data)


def _hex(b) -> bytes:
    s = b if isinstance(b, str) else ""
    if s:
        return bytes.fromhex(s[2:] if s.startswith("0x") else s)
    return bytes(b)


def _addr_word(b: bytes | None) -> str | None:
    """An address from a 32-byte return word, lower case; None for an empty or zero answer."""
    if not b or len(b) < 32:
        return None
    a = "0x" + bytes(b)[12:32].hex()
    return None if int(a, 16) == 0 else a


def _uint(b: bytes | None) -> int | None:
    return int.from_bytes(bytes(b)[:32], "big") if b and len(b) >= 32 else None


def _symbol(b: bytes | None) -> str | None:
    if not b:
        return None
    try:
        return str(_dec(["string"], bytes(b))[0])
    except Exception:  # noqa: BLE001 — a bytes32 symbol (MKR-style)
        return bytes(b)[:32].rstrip(b"\x00").decode("utf-8", "replace") or None


def decode_sugar(raw) -> list[dict]:
    """RewardsSugar epochsLatest / epochsByAddress return -> [{ts, lp, votes, emissions, bribes, fees}] (addresses lower
    case; bribes / fees as [(token, raw amount)])."""
    (rows,) = _dec([SUGAR_OUT], _hex(raw))
    return [{"ts": int(ts), "lp": str(lp).lower(), "votes": int(v), "emissions": int(e),
             "bribes": [(str(t).lower(), int(a)) for t, a in br], "fees": [(str(t).lower(), int(a)) for t, a in fe]}
            for ts, lp, v, e, br, fe in rows]


def sugar_calldata(limit: int, offset: int, pool: str) -> bytes:
    return selector("epochsByAddress(uint256,uint256,address)") + _enc(["uint256", "uint256", "address"],
                                                                         [int(limit), int(offset), pool])


def rate_to_tokens(rate_wei_per_s: int, seconds: int = WEEK, decimals: int = 18) -> float:
    """A per-second emission rate in token wei -> tokens over `seconds` (Aerodrome: rewardRateByEpoch × WEEK)."""
    return int(rate_wei_per_s) * int(seconds) / 10 ** int(decimals)


# ===================================================================================================== pure helpers
def complete_epochs(now_ts: int, n: int, week: int = WEEK) -> list[int]:
    """The `n` newest COMPLETE epoch starts before `now_ts`, newest first (the epoch in progress is never one)."""
    cur = int(now_ts) // week * week
    return [cur - k * week for k in range(1, int(n) + 1)]


def day_of(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))


def coverage_path(table: Path) -> Path:
    """Beside a Pendle epoch's table: the chains / days not sampled ("" when complete), so a re-derived epoch stays
    PARTIAL with the same names."""
    return Path(table).with_suffix(".coverage.txt")


def _day_ranges(days: list[str]) -> str:
    """'2026-10-20, 2026-10-22..2026-10-24' from sorted ISO days."""
    ts = [pd.Timestamp(d) for d in sorted(days)]
    out, i = [], 0
    while i < len(ts):
        j = i
        while j + 1 < len(ts) and (ts[j + 1] - ts[j]).days == 1:
            j += 1
        out.append(f"{ts[i].date()}" if i == j else f"{ts[i].date()}..{ts[j].date()}")
        i = j + 1
    return ", ".join(out)


def table_path(directory, project: str, epoch_start: int) -> Path:
    d = Path(directory)
    if not d.is_absolute():
        d = ROOT / d
    return d / f"{project.lower()}_{day_of(epoch_start)}.csv"


def value_amounts(amounts, decimals: dict, prices: dict, min_confidence: float = 0.0) -> tuple[float | None, list]:
    """USD of [(token, raw)] — None and the reasons when ANY token with a non-zero amount cannot be valued (no
    decimals, no price, or a price below min_confidence). Never values an unknown at 0."""
    usd, missing = 0.0, []
    for tok, raw in amounts:
        if not raw:
            continue
        dec = decimals.get(tok)
        pr = prices.get(tok) or {}
        sym = pr.get("symbol") or tok[:10]
        if dec is None:
            missing.append(f"{sym}: no decimals()")
            continue
        if pr.get("price") is None:
            missing.append(f"{sym}: no price")
            continue
        conf = pr.get("confidence")
        if conf is not None and float(conf) < min_confidence:
            missing.append(f"{sym}: price confidence {float(conf):.2f} < {min_confidence:.2f}")
            continue
        usd += int(raw) / 10 ** int(dec) * float(pr["price"])
    return (None, missing) if missing else (usd, [])


def _usable(pr: dict, min_confidence: float) -> bool:
    """A DefiLlama coins entry with a price at or above min_confidence (no confidence field: taken as served)."""
    if not pr or pr.get("price") is None:
        return False
    c = pr.get("confidence")
    return c is None or float(c) >= min_confidence


def summarize(rows: list[dict], max_unvalued_share: float = 0.05) -> dict:
    """The epoch's three figures from its per-pool rows (emissions_tokens / emissions_usd / fees_usd, None = unknown).

    status "ok" (every receiving pool valued), "partial" (the unvalued pools' share of emissions ≤ the cap: ratios over
    the valued pools only), or "refused" (`why`). emissions_usd is None only when some pool's emissions are not
    valued; it stands even when the ratios are refused."""
    recv = [r for r in rows if r.get("emissions_tokens") is None or r["emissions_tokens"] > 0]
    res = {"pools": len(recv), "emissions_usd": None, "cover": None, "covered_share": None, "status": "refused",
           "why": "", "unvalued": [], "unvalued_share": None}
    no_em = [r for r in recv if r.get("emissions_usd") is None]
    if no_em:
        res["why"] = (f"{len(no_em)} pool(s) whose EMISSIONS are not valued ({_names(no_em)}) — their share cannot "
                      f"be bounded")
        return res
    total = sum(r["emissions_usd"] for r in recv)
    res["emissions_usd"] = total
    if total <= 0:
        res["why"] = "no emissions this epoch — a ratio over nothing"
        return res
    unval = [r for r in recv if r.get("fees_usd") is None]
    share = sum(r["emissions_usd"] for r in unval) / total
    res["unvalued"], res["unvalued_share"] = unval, share
    if unval and share > max_unvalued_share:
        res["why"] = (f"{len(unval)} of {len(recv)} pools' fees unvalued, {share:.2%} of emissions > the "
                      f"{max_unvalued_share:.0%} cap ({_names(unval)})")
        return res
    valued = [r for r in recv if r.get("fees_usd") is not None]
    em_v = sum(r["emissions_usd"] for r in valued)
    if em_v <= 0:
        res["why"] = "no valued pool with emissions above 0"
        return res
    res["cover"] = sum(r["fees_usd"] for r in valued) / em_v
    res["covered_share"] = sum(r["emissions_usd"] for r in valued if r["fees_usd"] >= r["emissions_usd"]) / em_v
    res["status"] = "partial" if unval else "ok"
    return res


def _names(rows: list[dict], n: int = 5) -> str:
    rows = sorted(rows, key=lambda r: -(r.get("emissions_usd") or 0))
    out = [str(r.get("name") or r.get("pool"))[:24].replace("[", "(").replace("]", ")") for r in rows[:n]]
    return ", ".join(out) + (f" +{len(rows) - n} more" if len(rows) > n else "")


def _num(v) -> str:
    return "" if v is None else repr(float(v))


def write_table(path: Path, epoch_start: int, rows: list[dict]) -> None:
    """The per-pool table, largest emissions first; a blank cell is UNKNOWN (never 0)."""
    from .logcache import atomic_write_text               # noqa: PLC0415
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in sorted(rows, key=lambda r: (-(r.get("emissions_usd") or 0), -(r.get("emissions_tokens") or 0))):
        cover = (r["fees_usd"] / r["emissions_usd"] if r.get("fees_usd") is not None and r.get("emissions_usd")
                 else None)
        w.writerow([day_of(epoch_start), r["pool"], r.get("name") or "", _num(r.get("emissions_tokens")),
                    _num(r.get("emissions_usd")), _num(r.get("fees_usd")), _num(cover), _num(r.get("votes"))])
    atomic_write_text(path, buf.getvalue())


def read_table(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    f2 = (lambda s: None if s in ("", None) else float(s))
    return [{"pool": r["pool"], "name": r["name"], "emissions_tokens": f2(r["emissions_tokens"]),
             "emissions_usd": f2(r["emissions_usd"]), "fees_usd": f2(r["fees_usd"]), "votes": f2(r["votes"])}
            for r in rows]


def streamed_tokens(rate_final: int, rate_early: int | None, t_change: int | None, epoch_start: int,
                    week: int = WEEK, decimals: int = 18) -> float:
    """AERO a gauge streamed over epoch [E, E+WEEK) from its rewardRateByEpoch(E) read at the epoch's end
    (`rate_final`) and at the early block (`rate_early`), with `t_change` the time the rate became rate_final when the
    two differ (found by bisection). Unchanged since the early block: rate × WEEK (notified within early_s of the
    start). Changed: the early rate up to t_change (0 if not yet notified), the final rate after it."""
    if t_change is None or rate_early == rate_final:
        return rate_to_tokens(rate_final, week, decimals)
    t = min(max(int(t_change), epoch_start), epoch_start + week)
    return (int(rate_early or 0) * (t - epoch_start) + int(rate_final) * (epoch_start + week - t)) / 10 ** decimals


def pendle_emissions(points: list[tuple], start: int, end: int, max_gap_s: float) -> tuple[float | None, str]:
    """PENDLE wei emitted to one market over [start, end) from rate samples [(t, pendlePerSec, incentiveEndsAt)].

    Each instant takes its NEAREST sample's rate (boundaries at the midpoints), capped at that sample's
    incentiveEndsAt. (None, why) when a gap — epoch start to the first sample, between samples, or the last sample to
    the epoch end — exceeds max_gap_s: the rate there is not known."""
    pts = sorted((int(t), int(r), int(e)) for t, r, e in points if start <= int(t) < end)
    if not pts:
        return None, "no rate sample inside the epoch"
    gaps = [pts[0][0] - start, end - pts[-1][0]] + [b[0] - a[0] for a, b in zip(pts, pts[1:])]
    if max(gaps) > max_gap_s:
        return None, f"a {max(gaps) / 3600:.0f} h stretch with no rate sample (> {max_gap_s / 3600:.0f} h)"
    bounds = [start] + [(a[0] + b[0]) / 2 for a, b in zip(pts, pts[1:])] + [end]
    wei = 0.0
    for (t, rate, ends), lo, hi in zip(pts, bounds, bounds[1:]):
        wei += rate * max(0.0, min(hi, ends) - lo)
    return wei, ""


def pendle_emissions_by_day(points: list[tuple], start: int, end: int) -> tuple[float, list[str], list[str]]:
    """PENDLE wei emitted to one market over the UTC days of [start, end) that hold a rate sample, and (days sampled,
    days NOT sampled). Round G8 / G9 A1 (Jake 2026-10-20): a day with no sample is NEVER extrapolated and never refuses
    the epoch — it is excluded and named. Inside a sampled day each instant takes the nearest of THAT day's samples'
    rates (boundaries at their midpoints), capped at the sample's incentiveEndsAt."""
    pts = sorted((int(t), int(r), int(e)) for t, r, e in points if start <= int(t) < end)
    wei, have, miss = 0.0, [], []
    d0 = start
    while d0 < end:
        d1 = min(d0 + 86_400, end)
        day = [p for p in pts if d0 <= p[0] < d1]
        if not day:
            miss.append(day_of(d0))
        else:
            have.append(day_of(d0))
            bounds = [d0] + [(a[0] + b[0]) / 2 for a, b in zip(day, day[1:])] + [d1]
            for (_t, rate, ends), lo, hi in zip(day, bounds, bounds[1:]):
                wei += rate * max(0.0, min(hi, ends) - lo)
        d0 = d1
    return wei, have, miss


def rate_daily_metric(chain: str) -> str:
    """G9 N: the per-chain daily PENDLE emissions read from the sampled gauge rates."""
    return f"emissions_tokens_gauge_rate_{chain}"


def pendle_gauge_chains() -> list[dict]:
    """Every chain on which Pendle has a GaugeController on file — the same set Pendle|emissions_tokens sums (its
    gauge_pendle_out* log scans: mainnet, arbitrum, optimism, and the probed L2s bsc, base, mantle, sonic, berachain,
    hyperevm). One list, read from config, so a gauge added there is sampled here."""
    out, seen = [], set()
    p = config.PROJECT_BY_NAME.get("Pendle") or {}
    for sc in p.get("log_scans") or ():
        if str(sc.get("key", "")).startswith("gauge_pendle_out") and sc.get("holders"):
            out.append({"chain": sc["chain"], "chain_id": int(config.CHAIN_IDS[sc["chain"]]), "gauge": sc["holders"][0]})
    for cid, proj, chain, key, _m, _t, holders in getattr(config, "EXPLORER_PROBED_SCANS", ()):
        if proj == "Pendle" and key.startswith("gauge_pendle_out"):
            out.append({"chain": chain, "chain_id": int(cid), "gauge": holders[0]})
    res = []
    for c in out:
        if c["chain_id"] not in seen:
            seen.add(c["chain_id"])
            res.append(c)
    return res


# ===================================================================================================== JSON-RPC
class Rpc:
    """eth_call / Multicall3 aggregate3 against one JSON-RPC endpoint, through the run's Http (spacing, retries, the
    caller's deadline). Errors name the HOST only."""

    def __init__(self, http: Http, url: str, deadline: float | None = None, multicall: str = MULTICALL3):
        self.http, self.url, self.deadline, self.multicall = http, url, deadline, multicall
        self.host = urllib.parse.urlsplit(url).netloc or "?"

    def request(self, method: str, params: list):
        j = self.http.post(self.url, json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                           deadline=self.deadline)
        if not isinstance(j, dict) or "result" not in j:
            err = (j or {}).get("error") if isinstance(j, dict) else j
            raise RuntimeError(f"{self.host}: {method} answered an error: {str(err)[:160]}")
        return j["result"]

    def head(self) -> tuple[int, int]:
        """(number, timestamp) of the latest block."""
        b = self.request("eth_getBlockByNumber", ["latest", False])
        return int(b["number"], 16), int(b["timestamp"], 16)

    def head_ts(self) -> int:
        return self.head()[1]

    def batch_call(self, calls: list[tuple], chunk: int = 10) -> list[bytes | None]:
        """[(target, calldata, block hex)] as JSON-RPC BATCHES of `chunk` eth_calls (each at its own block, which one
        aggregate3 cannot do). None for an eth_call that answered an error (a revert); a failed request RAISES."""
        out: list = []
        for i in range(0, len(calls), chunk):
            part = calls[i:i + chunk]
            body = [{"jsonrpc": "2.0", "id": k, "method": "eth_call",
                     "params": [{"to": t, "data": "0x" + bytes(d).hex()}, b]} for k, (t, d, b) in enumerate(part)]
            j = self.http.post(self.url, json_body=body, deadline=self.deadline)
            if not isinstance(j, list) or len(j) != len(part):
                raise RuntimeError(f"{self.host}: a batch of {len(part)} eth_calls answered {str(j)[:120]}")
            by = {int(x.get("id", -1)): x for x in j if isinstance(x, dict)}
            for k in range(len(part)):
                x = by.get(k) or {}
                if "result" not in x and "limit" in str(x.get("error")):
                    raise RuntimeError(f"{self.host}: rate limited inside a batch ({str(x.get('error'))[:80]})")
                out.append(_hex(x["result"]) if x.get("result") not in (None, "0x") else None)
        return out

    def call(self, to: str, data: bytes, block: str = "latest") -> bytes:
        return _hex(self.request("eth_call", [{"to": to, "data": "0x" + bytes(data).hex()}, block]))

    def aggregate(self, calls: list[tuple], chunk: int = MC_BATCH, block: str = "latest") -> list[bytes | None]:
        """[(target, calldata)] -> [return data, or None for a reverted / empty sub-call], at `block` (an archive read
        when not "latest"). A failed aggregate3 RAISES: a transient failure must not read as a set of unknowns."""
        out: list = []
        sel = selector("aggregate3((address,bool,bytes)[])")
        for i in range(0, len(calls), chunk):
            part = calls[i:i + chunk]
            data = sel + _enc(["(address,bool,bytes)[]"], [[(t, True, bytes(d)) for t, d in part]])
            (res,) = _dec(["(bool,bytes)[]"], self.call(self.multicall, data, block))
            if len(res) != len(part):
                raise RuntimeError(f"{self.host}: aggregate3 returned {len(res)} results for {len(part)} calls")
            out += [bytes(r) if ok and r else None for ok, r in res]
        return out


# ===================================================================================================== the fetcher
class SubsidyTargeting:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, rpc_factory=None, clock=None, wall=None, **_ignored):
        # 1 s spacing, 4 retries: mainnet.base.org answers HTTP 429 "25/second request limit reached" (weighted: six
        # 500-call aggregate3s in 7 s tripped it, 2026-10-20), and Http backs off on a 429
        self.http = http or Http(min_interval=1.0, retries=4)
        self.rpc_factory = rpc_factory or (lambda url, deadline: Rpc(self.http, url, deadline))
        self.clock = clock or time.monotonic
        self.wall = wall or time.time

    def run(self, projects: list[dict], window_days, out):
        # PENDLE FIRST (G9 A1): its daily sample is time-critical (a missed day is a hole in the epoch for good) and
        # cheap; Aerodrome's epoch reads take the rest of the tier's budget
        projects = sorted(projects, key=lambda p: (p.get("subsidy_targeting") or {}).get("route") != "pendle_gauge")
        for p in projects:
            spec = p.get("subsidy_targeting")
            if not spec:
                continue
            name = p["name"]
            route = spec.get("route")
            deadline = self.clock() + float(spec.get("budget_s", 120))
            try:
                if route == "aerodrome_sugar":
                    self._aerodrome(name, spec, out, deadline)
                elif route == "pendle_gauge":
                    self._pendle(name, spec, out, deadline)
                else:
                    out.fail(SOURCE, name, f"unknown subsidy_targeting route {route!r} — NOTHING STORED", TIER)
            except BudgetExhausted as e:
                out.skipped(SOURCE, name, f"time budget ({spec.get('budget_s', 120)}s) reached before an epoch was "
                                          f"complete — {str(e)[:120]}; the next run resumes", TIER)
            except Exception as e:  # noqa: BLE001 — one failed project must not kill the run
                out.fail(SOURCE, name, f"NOTHING STORED this run — {type(e).__name__}: {str(e)[:200]}", TIER)

    # ------------------------------------------------------------------ shared
    def _left(self, deadline: float) -> float:
        return deadline - self.clock()

    def _prices(self, keys: list[str], ts: int, deadline: float) -> dict:
        """{key lower ("base:0x.."): coins entry} at unix ts. An HTTP failure RAISES (transient: nothing written); a
        key absent from a good answer is simply missing (DefiLlama has no price: a genuine unknown)."""
        out = {}
        keys = sorted(set(keys))
        for i in range(0, len(keys), PRICE_BATCH):
            j = self.http.get(f"{LLAMA}/{int(ts)}/" + ",".join(keys[i:i + PRICE_BATCH]), deadline=deadline)
            coins = (j or {}).get("coins")
            if not isinstance(coins, dict):
                raise RuntimeError(f"coins.llama.fi: no `coins` in the answer ({str(j)[:120]})")
            out.update({k.lower(): v for k, v in coins.items()})
        return out

    def _store(self, out, name: str, epoch: int, summ: dict, src: str, how: str, restored: bool = False) -> None:
        d = pd.Timestamp(day_of(epoch))
        tag = f"epoch {day_of(epoch)}"
        cov = summ.get("coverage") or ""
        # DAYS NOT SAMPLED (Pendle, G9 A1): every figure of the epoch is PARTIAL and names them — the emissions exclude
        # those days; the fees are the whole epoch's, so the cover reads HIGH by what those days emitted
        src_cov = (config.mark_source(src, "PARTIAL") + f"[{cov}; cover reads high by the unsampled days' emissions]"
                   if cov else src)
        if summ["emissions_usd"] is not None:
            out.add(tidy([(d, summ["emissions_usd"])], name, M_EMIT, src_cov, TIER), SOURCE, name,
                    f"{M_EMIT} {tag}: ${summ['emissions_usd']:,.0f} over {summ['pools']} pool(s) ({how})"
                    + (f" — PARTIAL: {cov}" if cov else ""), TIER)
        if summ["status"] == "refused":
            msg = (f"{M_COVER} / {M_SHARE} {tag}: REFUSED — {summ['why']}"
                   + ("" if summ["emissions_usd"] is not None else f"; {M_EMIT} refused too"))
            # a refusal re-derived from a table already written was reported FAILED on the run that read it; every
            # later run says it once more, as skipped, rather than adding a fresh failure a day
            (out.skipped if restored else out.fail)(SOURCE, name, msg + (f" ({how})" if restored else ""), TIER)
            return
        s = src_cov
        if summ["status"] == "partial":
            s = config.mark_source(src, "PARTIAL") + (
                f"[{len(summ['unvalued'])} of {summ['pools']} pools' fees unvalued, {summ['unvalued_share']:.2%} of "
                f"emissions: {_names(summ['unvalued'])}]") + (
                f"[{cov}; cover reads high by the unsampled days' emissions]" if cov else "")
        for m, v in ((M_COVER, summ["cover"]), (M_SHARE, summ["covered_share"])):
            out.add(tidy([(d, v)], name, m, s, TIER), SOURCE, name,
                    f"{m} {tag}: {v:.4f}" + (" — PARTIAL (unvalued pools named on the cell)"
                                             if summ["status"] == "partial" else "")
                    + (f" — PARTIAL: {cov}" if cov else "") + f" ({how})", TIER)

    def _restore(self, out, name: str, spec: dict, epochs: list[int], src: str) -> list[int]:
        """Re-derive the summary of every epoch whose table is already written (no network call). Returns the epochs
        still to read."""
        todo = []
        for ep in epochs:
            p = table_path(spec.get("dir", DEFAULT_DIR), name, ep)
            if not p.exists():
                todo.append(ep)
                continue
            try:
                rows = read_table(p)
            except Exception as e:  # noqa: BLE001 — an unreadable table is read again from the source
                out.log.append(LogEntry(SOURCE, name, 0, "warn", f"{p.name} unreadable ({type(e).__name__}) — read "
                                                                 f"again", TIER))
                todo.append(ep)
                continue
            summ = summarize(rows, float(spec.get("max_unvalued_share", 0.05)))
            cp = coverage_path(p)
            summ["coverage"] = cp.read_text(encoding="utf-8").strip() if cp.exists() else ""
            self._store(out, name, ep, summ, src,
                        f"from the table {p.name} written earlier; not read again", restored=True)
        return todo

    # ------------------------------------------------------------------ Aerodrome
    def _aerodrome(self, name: str, spec: dict, out, deadline: float) -> None:
        src = f"{SOURCE}:aerodrome_sugar"
        rpc = self.rpc_factory(spec["rpc"], deadline)
        head_n, now = rpc.head()                            # the chain's clock decides what is complete
        epochs = complete_epochs(now, int(spec.get("epochs_back", 4)))
        todo = self._restore(out, name, spec, epochs, src)
        if not todo:
            out.skipped(SOURCE, name, f"all {len(epochs)} complete epoch(s) {day_of(epochs[-1])}..{day_of(epochs[0])} "
                                      f"already tabled — nothing read", TIER)
            return
        struct = self._aero_structure(rpc, spec)
        done = []
        for ep in todo:                                     # newest first
            if self._left(deadline) <= 0:
                break
            try:
                rows, how = self._aero_epoch(rpc, spec, struct, ep, (head_n, now), deadline)
            except BudgetExhausted:
                break
            p = table_path(spec.get("dir", DEFAULT_DIR), name, ep)
            write_table(p, ep, rows)
            self._store(out, name, ep, summarize(rows, float(spec.get("max_unvalued_share", 0.05))), src,
                        f"{how}; table {p.name}")
            done.append(ep)
        left = [day_of(e) for e in todo if e not in done]
        if left:
            out.skipped(SOURCE, name, f"time budget ({spec.get('budget_s', 120)}s) reached: {len(done)} epoch(s) read, "
                                      f"{len(left)} left for the next run ({', '.join(left)})", TIER)

    def _aero_structure(self, rpc: Rpc, spec: dict) -> dict:
        voter = spec["voter"]
        n = _uint(rpc.call(voter, selector("length()")))
        if not n:
            raise RuntimeError(f"Voter {voter[:10]}… length() read nothing")
        pools = [_addr_word(b) for b in rpc.aggregate([(voter, selector("pools(uint256)") + _enc(["uint256"], [i]))
                                                       for i in range(n)])]
        if sum(p is None for p in pools) > n // 10:
            raise RuntimeError(f"Voter.pools(i): {sum(p is None for p in pools)} of {n} unreadable")
        pools = [p for p in pools if p]
        gauges = [_addr_word(b) for b in rpc.aggregate([(voter, selector("gauges(address)") + _enc(["address"], [p]))
                                                        for p in pools])]
        pg = [(p, g) for p, g in zip(pools, gauges) if g]
        return {"pairs": pg, "n": n, "tokens": {}, "decimals": {}, "symbols": {}}

    def _aero_meta(self, rpc: Rpc, struct: dict, pools: list[str], tokens: set) -> None:
        """token0/token1 of each pool and decimals()/symbol() of each token, read once a run (aggregate3)."""
        need = [p for p in pools if p not in struct["tokens"]]
        if need:
            calls = [(p, selector(s)) for p in need for s in ("token0()", "token1()")]
            res = rpc.aggregate(calls)
            for k, p in enumerate(need):
                struct["tokens"][p] = (_addr_word(res[2 * k]), _addr_word(res[2 * k + 1]))
        allt = set(tokens) | {t for p in pools for t in struct["tokens"].get(p, ()) if t}
        need_t = sorted(t for t in allt if t not in struct["symbols"])
        if need_t:
            calls = [(t, selector(s)) for t in need_t for s in ("decimals()", "symbol()")]
            res = rpc.aggregate(calls)
            for k, t in enumerate(need_t):
                dec = _uint(res[2 * k])
                if dec is not None and dec <= 255:
                    struct["decimals"][t] = dec
                struct["symbols"][t] = _symbol(res[2 * k + 1])

    def _aero_epoch(self, rpc: Rpc, spec: dict, struct: dict, ep: int, head: tuple, deadline: float):
        head_n, now = head
        pairs = struct["pairs"]
        rates = [_uint(b) for b in rpc.aggregate([(g, selector("rewardRateByEpoch(uint256)") + _enc(["uint256"], [ep]))
                                                  for _p, g in pairs])]
        # A REVERTED RATE READ IS UNKNOWN, NOT 0: the pool stays in the table with its emissions blank, which refuses
        # the epoch's ratios (summarize). ONE EXCEPTION, read not configured: a gauge with NO POOL behind it (its "pool"
        # answers token0() with a revert). 2026-10-20: Voter pool 0x97cd4eb6… is a 128-byte contract that reverts
        # every call, and its gauge 0x287c94a1… (rewardToken/notifyRewardAmount/getReward/left only) has no
        # rewardRateByEpoch. Sugar's own pool walk never reaches it. Its AERO subsidises no liquidity and earns no pool
        # fees, so it is outside "pools" and is named every epoch with its share of the votes now.
        unread = [(p, g) for (p, g), r in zip(pairs, rates) if r is None]
        nopool = []
        if unread:
            t0 = rpc.aggregate([(p, selector("token0()")) for p, _g in unread])
            nopool = [(p, g) for (p, g), b in zip(unread, t0) if _addr_word(b) is None]
            unread = [(p, g) for (p, g), b in zip(unread, t0) if _addr_word(b) is not None]
        if len(unread) > max(5, len(pairs) // 100):
            raise RuntimeError(f"rewardRateByEpoch unreadable on {len(unread)} of {len(pairs)} gauges")
        nopool_note = ""
        if nopool:
            w = rpc.aggregate([(spec["voter"], selector("weights(address)") + _enc(["address"], [p])) for p, _g in nopool]
                              + [(spec["voter"], selector("totalWeight()"))])
            tw = _uint(w[-1]) or 0
            nopool_note = "; " + ", ".join(
                f"gauge {g[:10]}… has no pool behind it (rate unreadable, token0() reverts) — left out, "
                + (f"{(_uint(b) or 0) / tw:.4%} of the votes now" if tw else "votes unread")
                for (_p, g), b in zip(nopool, w[:-1]))
        emitting = [(p, g, r) for (p, g), r in zip(pairs, rates) if r]
        streamed, late_note = self._aero_streamed(rpc, spec, emitting, ep, head_n, now)
        offset = (now // WEEK * WEEK - ep) // WEEK
        if offset < 1:
            raise RuntimeError(f"epoch {day_of(ep)} is not complete")
        recs = {}
        sugar = spec["sugar"]
        for i in range(0, len(emitting), SUGAR_BATCH):
            if self._left(deadline) <= 0:
                raise BudgetExhausted("time budget reached inside the Sugar reads")
            part = emitting[i:i + SUGAR_BATCH]
            res = rpc.aggregate([(sugar, sugar_calldata(1, offset, p)) for p, _g, _r in part], chunk=SUGAR_BATCH)
            for (p, _g, rate), b in zip(part, res):
                if b is None:
                    recs[p] = "the Sugar call reverted"
                    continue
                got = decode_sugar(b)
                if not got:
                    recs[p] = "Sugar serves no record (gauge not alive now)"
                    continue
                r = got[0]
                if r["ts"] != ep:
                    # the chain's week turned between our clock read and this call: transient, read again next run
                    raise RuntimeError(f"Sugar answered epoch {day_of(r['ts'])} for offset {offset}, not {day_of(ep)}")
                if r["emissions"] != rate:
                    raise RuntimeError(f"Sugar emissions {r['emissions']} != gauge.rewardRateByEpoch {rate} for {p}")
                recs[p] = r
        fee_tokens = {t for r in recs.values() if isinstance(r, dict) for t, a in r["fees"] if a}
        self._aero_meta(rpc, struct, [p for p, _g, _r in emitting], fee_tokens)
        chain = spec.get("chain", "base")
        reward = spec["reward_token"].lower()                       # "base:0x9401…"
        at = min(ep + WEEK, int(self.wall()) - 60)
        prices = self._prices([f"{chain}:{t}" for t in fee_tokens] + [reward], at, deadline)
        bare = {k.split(":", 1)[1]: v for k, v in prices.items() if k.startswith(chain + ":")}
        rp = prices.get(reward) or {}
        minc = float(spec.get("min_confidence", MIN_CONFIDENCE))
        r_ok = _usable(rp, minc)
        rows, why_missing = [], {}
        for p, g in unread:
            rows.append({"pool": p, "name": f"gauge {g[:10]}: rate unreadable", "emissions_tokens": None,
                         "emissions_usd": None, "fees_usd": None, "votes": None})
        for p, g, rate in emitting:
            toks = struct["tokens"].get(p) or (None, None)
            nm = "/".join(str(struct["symbols"].get(t) or (t or "?")[:8]) for t in toks)
            em_tok = streamed.get(g)
            r = recs.get(p)
            fees_usd, votes = None, None
            if isinstance(r, dict):
                votes = r["votes"] / 1e18
                fees_usd, miss = value_amounts(r["fees"], struct["decimals"], bare, minc)
                if miss:
                    why_missing[nm] = miss
            else:
                why_missing[nm] = [r or "no Sugar record"]
            rows.append({"pool": p, "name": nm, "emissions_tokens": em_tok,
                         "emissions_usd": em_tok * float(rp["price"]) if r_ok and em_tok is not None else None,
                         "fees_usd": fees_usd, "votes": votes})
        how = (f"Voter {struct['n']} pools, {len(emitting)} with emissions in the epoch; Sugar epochsByAddress offset "
               f"{offset}; DefiLlama prices at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(at))} UTC"
               + ("" if r_ok else f"; NO usable price for the emission token {reward}") + late_note + nopool_note)
        if why_missing:
            top = "; ".join(f"{k}: {', '.join(v[:2])}" for k, v in list(why_missing.items())[:6])
            how += f"; {len(why_missing)} pool(s) with fees not valued ({top})"
        return rows, how

    def _aero_streamed(self, rpc: Rpc, spec: dict, emitting: list, ep: int, head_n: int, now: int) -> tuple[dict, str]:
        """{gauge: AERO streamed in epoch ep} (None = unknown) and a note. rewardRateByEpoch is amount ÷ time LEFT at
        the notify, so rate × WEEK is the epoch's AERO only for a gauge notified at the flip. 2026-10-20: USDC/STAR
        (gauge 0x9aa4f2cf…) held 102,688 votes over four epochs and read rate × WEEK = 0 / 716,751 / 420 AERO for
        09-24 / 10-01 / 10-08. Its 10-01 distribute() ran minutes before the week ended (about 450 AERO streamed), and
        rate × WEEK made it 14% of the epoch. The fix: each gauge's rate at the block early_s (default 3 h) into
        the epoch, an ARCHIVE eth_call (mainnet.base.org serves state 20M blocks back, read 2026-10-20). Unchanged:
        rate × WEEK (it overstates by ≤ early_s / WEEK, ≤ 1.8%). Changed: the block the final rate took over is
        bisected (JSON-RPC batches, ~19 rounds), and the AERO is integrated over the two segments. A gauge
        re-notified more than once after early_s has its middle segment read at the final rate."""
        early_s = int(spec.get("early_s", 3 * 3600))
        # Base (OP stack) makes a block every 2 s exactly, so a block is found by arithmetic from the head (checked
        # 2026-10-20: the block computed for 2026-10-01 00:00 stamped 00:00:01).
        bt = int(spec.get("block_time_s", 2))
        block_at = (lambda t: head_n - (now - int(t)) // bt)
        ts_of = (lambda b: now - (head_n - int(b)) * bt)
        sel = selector("rewardRateByEpoch(uint256)") + _enc(["uint256"], [ep])
        b_early = block_at(ep + early_s)
        early = rpc.aggregate([(g, sel) for _p, g, _r in emitting], block=hex(b_early))
        out = {g: rate_to_tokens(r) for (_p, g, r), e in zip(emitting, early) if e is not None and _uint(e) == r}
        late = [(g, r, _uint(e) or 0) for (_p, g, r), e in zip(emitting, early) if g not in out]
        cap = int(spec.get("max_late_gauges", 60))
        for g, _r, _e in late[cap:]:
            out[g] = None                                    # beyond the bisection cap: unknown, never rate × WEEK
        late = late[:cap]
        lo = {g: b_early for g, _r, _e in late}
        hi = {g: block_at(ep + WEEK) - 1 for g, _r, _e in late}
        target = {g: r for g, r, _e in late}
        while True:
            open_ = [g for g in lo if hi[g] - lo[g] > 1]
            if not open_:
                break
            mids = {g: (lo[g] + hi[g]) // 2 for g in open_}
            got = rpc.batch_call([(g, sel, hex(mids[g])) for g in open_])
            for g, b in zip(open_, got):
                if b is not None and _uint(b) == target[g]:
                    hi[g] = mids[g]
                else:
                    lo[g] = mids[g]
        for g, r, e in late:
            out[g] = streamed_tokens(r, e, ts_of(hi[g]), ep)
        note = ""
        if late:
            big = sorted(late, key=lambda x: -(rate_to_tokens(x[1]) - out[x[0]]))[:3]
            note = (f"; {len(late)} gauge(s) notified after +{early_s // 3600} h — AERO integrated from the bisected "
                    f"notify time, not rate × WEEK (largest corrections: "
                    + ", ".join(f"{g[:10]}… {rate_to_tokens(r):,.0f} -> {out[g]:,.0f}" for g, r, _e in big) + ")")
        unknown = sum(1 for v in out.values() if v is None)
        if unknown:
            note += f"; {unknown} late gauge(s) beyond the {cap}-gauge bisection cap left UNKNOWN"
        return out, note

    # ------------------------------------------------------------------ Pendle
    def _rates_path(self, spec: dict, name: str, start: int) -> Path:
        return table_path(spec.get("dir", DEFAULT_DIR), name, start).with_name(
            f"{name.lower()}_rates_{day_of(start)}.json")

    def _pendle(self, name: str, spec: dict, out, deadline: float) -> None:
        src = f"{SOURCE}:pendle_gauge"
        api = spec.get("api", "https://api-v2.pendle.finance/core").rstrip("/")
        chains = spec.get("chains") or pendle_gauge_chains()
        directory = Path(spec.get("dir", DEFAULT_DIR))
        directory = directory if directory.is_absolute() else ROOT / directory
        # sampled epochs whose table is not written yet decide how far back the fee read goes
        sampled = sorted(int(json.loads(f.read_text(encoding="utf-8"))["epoch_start"])
                         for f in directory.glob(f"{name.lower()}_rates_*.json")) if directory.exists() else []
        open_eps = [s for s in sampled if not table_path(directory, name, s).exists()]
        # epochs=N counts COMPLETE epochs back from the running one: enough to reach the oldest open sampled epoch
        n_back = (min(PENDLE_MAX_EPOCHS, max(1, (int(self.wall()) - min(open_eps)) // (14 * 86_400) + 1))
                  if open_eps else 1)
        fees = self.http.get(f"{api}/v1/statistics/market-fees", params={"epochs": n_back, "includeOngoing": "true"},
                             deadline=deadline)
        eps = {int(pd.Timestamp(e["epochStart"]).timestamp()): e for e in (fees or {}).get("epochs") or []}
        ongoing = [e for e in eps.values() if e.get("isOngoing")]
        if len(ongoing) != 1:
            raise RuntimeError(f"market-fees: {len(ongoing)} ongoing epoch(s) in the answer, expected 1")
        on = ongoing[0]
        on_start, on_end = int(pd.Timestamp(on["epochStart"]).timestamp()), int(pd.Timestamp(on["epochEnd"]).timestamp())
        now = int(self.wall())
        # 1. SAMPLE the rates now, for the epoch in progress — EVERY chain with a gauge (G9 A1: Jake's run sampled
        # "52 chain-1 market(s)" only, while Pendle|emissions_tokens sums nine chains' gauges). One chain failing is
        # a gap in that chain's days, named when the epoch is stored; it never stops the others.
        said = []
        for ch in chains:
            try:
                n_mk, n_rate = self._pendle_sample(spec, name, api, ch, eps, on_start, on_end, now, deadline)
                said.append(f"{ch['chain']} {n_mk} market(s) ({n_rate} with a rate)")
            except BudgetExhausted:
                raise
            except Exception as e:  # noqa: BLE001 — a missed sample is a gap the coverage rule names later
                said.append(f"{ch['chain']} NOT SAMPLED ({type(e).__name__}: {str(e)[:80]})")
        out.log.append(LogEntry(SOURCE, name, 0, "ok" if all("NOT SAMPLED" not in x for x in said) else "warn",
                                f"rewardData sampled into the epoch from {day_of(on_start)}: " + "; ".join(said), TIER))
        # 1b. G9 N: EVERY CHAIN'S PENDLE PER DAY FROM ITS SAMPLED RATES (rate x seconds, on-chain state) — the route for
        # a gauge chain whose log scan stores nothing, and a check on the ones whose scan does
        try:
            self._pendle_daily(name, directory, chains, now, out)
        except Exception as e:  # noqa: BLE001 — a check series, never a reason to stop the epochs
            out.log.append(LogEntry(SOURCE, name, 0, "warn", f"rate-derived daily emissions not formed: "
                                                               f"{type(e).__name__}: {str(e)[:120]}", TIER))
        # 2. COMPLETE sampled epochs: the stored ones re-derived, the new ones computed
        complete = [s for s in sampled if s < on_start]
        todo = self._restore(out, name, spec, sorted(complete, reverse=True), src)
        if not complete:
            out.skipped(SOURCE, name, f"no fully sampled epoch complete yet (sampling the one from {day_of(on_start)}, "
                                      f"ending {day_of(on_end)}) — nothing to store", TIER)
            return
        for start in todo:
            if self._left(deadline) <= 0:
                out.skipped(SOURCE, name, f"time budget reached; {day_of(start)} left for the next run", TIER)
                return
            e = eps.get(start)
            if e is None:
                out.fail(SOURCE, name, f"epoch {day_of(start)}: market-fees served no such epoch (asked {n_back} back) "
                                       f"— NOTHING STORED; read again next run", TIER)
                continue
            rows, how, gaps = self._pendle_epoch(spec, name, chains, start, e, deadline)
            p = table_path(directory, name, start)
            write_table(p, start, rows)
            from .logcache import atomic_write_text         # noqa: PLC0415
            atomic_write_text(coverage_path(p), gaps)        # "" when every chain was sampled every day
            summ = summarize(rows, float(spec.get("max_unvalued_share", 0.05)))
            summ["coverage"] = gaps
            self._store(out, name, start, summ, src, f"{how}; table {p.name}")

    def _pendle_daily(self, name: str, directory: Path, chains: list, now: int, out) -> None:
        """G9 N: per gauge chain and COMPLETE sampled UTC day, the PENDLE its gauges paid = Σ over its markets of
        pendlePerSec x seconds (pendle_emissions_by_day on that day alone), stored as rate_daily_metric(chain). A day
        the chain was not sampled stores nothing (never extrapolated); today is never stored."""
        ids = {int(c["chain_id"]): c["chain"] for c in chains}
        today0 = now - now % 86_400
        per: dict = {}
        for f in sorted(directory.glob(f"{name.lower()}_rates_*.json")) if directory.exists() else ():
            st = json.loads(f.read_text(encoding="utf-8"))
            samples = sorted(({**s_, "chain_id": int(s_.get("chain_id", st.get("chain_id", 1)))}
                              for s_ in st["samples"]), key=lambda s_: s_["t"])
            for cid, chain in ids.items():
                own = [s_ for s_ in samples if s_["chain_id"] == cid]
                if not own:
                    continue
                markets = sorted({m for s_ in own for m in (s_["rates"] or {})})
                d0 = int(own[0]["t"]) - int(own[0]["t"]) % 86_400
                while d0 + 86_400 <= min(today0, int(st["epoch_end"])):
                    day_s = [s_ for s_ in own if d0 <= s_["t"] < d0 + 86_400]
                    if day_s and all(None not in (s_["rates"] or {}).values() for s_ in day_s):
                        wei = 0.0
                        for m in markets:
                            pts = [(s_["t"], int(((s_["rates"] or {}).get(m) or [0, 0])[0]),
                                    int(((s_["rates"] or {}).get(m) or [0, 0])[1])) for s_ in day_s]
                            wei += pendle_emissions_by_day(pts, d0, d0 + 86_400)[0]
                        per.setdefault(chain, []).append((pd.Timestamp(d0, unit="s"), wei / 1e18, len(day_s)))
                    d0 += 86_400
        for chain, rows in per.items():
            m = rate_daily_metric(chain)
            frame = pd.concat([tidy([(d, v)], name, m, f"{SOURCE}:pendle_rewardData_rate[{chain}: {n} sample(s)]", TIER)
                               for d, v, n in rows], ignore_index=True)
            out.add(frame, SOURCE, name, f"{m}: {len(rows)} sampled day(s) {rows[0][0].date()}..{rows[-1][0].date()} — "
                                         f"pendlePerSec x seconds over {chain}'s gauge markets", TIER)

    def _chain_aggregate(self, spec, ch, calls, deadline):
        """Multicall3 on the chain's public endpoints, in order, until one answers (hosts only ever logged)."""
        from .chain import rpc_endpoints                    # noqa: PLC0415
        urls = list((spec.get("rpcs") or {}).get(ch["chain"]) or ()) or [
            u for u in rpc_endpoints(ch["chain"]) if "alchemy" not in u and "infura" not in u]
        last = None
        for u in urls[:3]:
            try:
                return self.rpc_factory(u, deadline).aggregate(calls)
            except BudgetExhausted:
                raise
            except Exception as e:  # noqa: BLE001 — the next endpoint
                last = e
        raise RuntimeError(f"no {ch['chain']} endpoint answered ({type(last).__name__ if last else 'none listed'})")

    def _pendle_sample(self, spec, name, api, ch, eps, on_start, on_end, now, deadline) -> tuple[int, int]:
        chain_id = int(ch["chain_id"])
        act = self.http.get(f"{api}/v1/{chain_id}/markets/active", deadline=deadline)
        names = {str(m["address"]).lower(): m.get("name") for m in (act or {}).get("markets") or []}
        for e in eps.values():                              # markets the fee list names, unexpired
            for m in e.get("markets") or []:
                if int(m.get("chainId") or 0) == chain_id and pd.Timestamp(m.get("expiry") or 0).timestamp() > now:
                    names.setdefault(str(m["address"]).lower(), m.get("name"))
        mk = sorted(names)
        res = []
        if mk:                                              # no market on the chain: a sample of nothing, still a sample
            res = self._chain_aggregate(spec, ch, [(ch["gauge"], selector("rewardData(address)")
                                                    + _enc(["address"], [m])) for m in mk], deadline)
        rates = {}
        for m, b in zip(mk, res):
            if b is None or len(b) < 128:
                rates[m] = None                              # a failed read: a gap, never a 0
                continue
            ps, _acc, _lu, ends = _dec(["uint128"] * 4, bytes(b)[:128])
            rates[m] = [str(ps), int(ends)]
        path = self._rates_path(spec, name, on_start)
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {"epoch_start": on_start, "epoch_end": on_end, "names": {}, "samples": []}
        st["names"].update({m: n for m, n in names.items() if n})
        st["samples"].append({"t": now, "chain_id": chain_id, "rates": rates})
        from .logcache import atomic_write_text             # noqa: PLC0415
        atomic_write_text(path, json.dumps(st, separators=(",", ":")))
        return len(mk), sum(1 for v in rates.values() if v and int(v[0]))

    def _pendle_epoch(self, spec, name, chains, start, fee_epoch, deadline) -> tuple[list, str, str]:
        st = json.loads(self._rates_path(spec, name, start).read_text(encoding="utf-8"))
        end = int(st["epoch_end"])
        ids = {int(c["chain_id"]): c["chain"] for c in chains}
        # a sample written before G9 carries no chain: it was the mainnet-only sample (spec chain_id 1)
        samples = sorted(({**s, "chain_id": int(s.get("chain_id", st.get("chain_id", 1)))} for s in st["samples"]),
                         key=lambda s: s["t"])
        by_chain = {cid: [s for s in samples if s["chain_id"] == cid] for cid in ids}
        market_chain = {m: s["chain_id"] for s in samples for m in (s["rates"] or {})}
        markets = sorted(market_chain)
        fee_fields = list(spec.get("fee_fields") or ["poolFeeUsd"])
        fees, other_chains = {}, {}
        for m in fee_epoch.get("markets") or []:
            if int(m.get("chainId") or 0) not in ids:
                other_chains[int(m.get("chainId") or 0)] = other_chains.get(int(m.get("chainId") or 0), 0) + 1
            if int(m.get("chainId") or 0) in ids:
                vals = [m.get(f) for f in fee_fields]
                fees[str(m["address"]).lower()] = (None if any(v is None for v in vals)
                                                   else sum(float(v) for v in vals))
        reward = spec.get("reward_token", "ethereum:0x808507121b80c02388fad14726482e061b8da827").lower()
        at = min(end, int(self.wall()) - 60)
        pr = self._prices([reward], at, deadline).get(reward) or {}
        minc = float(spec.get("min_confidence", MIN_CONFIDENCE))
        price = float(pr["price"]) if _usable(pr, minc) else None
        rows = []
        for m in markets:
            cid = market_chain[m]
            pts = []
            for s in by_chain[cid]:
                v = (s["rates"] or {}).get(m, [0, 0])       # not listed by a successful sample: not incentivised
                if v is not None:
                    pts.append((s["t"], int(v[0]), int(v[1])))
            wei, _have, _miss = pendle_emissions_by_day(pts, start, end)
            if wei <= 0:
                continue                                    # no emissions on the sampled days: not a receiving pool
            tok = wei / 1e18
            rows.append({"pool": m, "name": st["names"].get(m) or "", "emissions_tokens": tok,
                         "emissions_usd": None if price is None else tok * price,
                         "fees_usd": fees.get(m), "votes": None})
        # DAYS NOT SAMPLED, PER CHAIN: excluded from the emissions (never extrapolated), named on the stored cells
        n_days = -(-(end - start) // 86_400)
        gaps = []
        for cid, chain in ids.items():
            _w, have, miss = pendle_emissions_by_day([(s["t"], 0, 0) for s in by_chain[cid]], start, end)
            if miss:
                gaps.append(f"{chain} {len(miss)} of {n_days} day(s) not sampled ({_day_ranges(miss)})")
        nofee = [r["name"] or r["pool"][:10] for r in rows if r["fees_usd"] is None]
        how = (f"{len(samples)} rewardData sample(s) over {day_of(start)}..{day_of(end)} on {len(ids)} chain(s), "
               f"{len(rows)} market(s) with emissions; fees = {'+'.join(fee_fields)} (Pendle market-fees, USD, the whole "
               f"epoch); PENDLE priced at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(at))} UTC"
               + ("" if price is not None else "; NO usable PENDLE price")
               + (f"; {len(nofee)} market(s) absent from the fee list: {', '.join(nofee[:6])}" if nofee else "")
               # a market on a chain with no GaugeController on file earns no PENDLE gauge incentive: not a gap
               + ("; fee-list markets on chains with no gauge on file (no PENDLE incentive): "
                  + ", ".join(f"chain {c} ×{n}" for c, n in sorted(other_chains.items())) if other_chains else ""))
        return rows, how, "; ".join(gaps)
