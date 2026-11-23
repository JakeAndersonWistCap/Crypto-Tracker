"""Round 17 — Chainlink (Jake 2026-11-23): a back-filled price day is never judged as "now" (4); the staking claims
stay checked week against week, per the live read (3); CCIP token value never passes a part as the whole (2)."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch import ccip_value as cv  # noqa: E402
from fetch import price_history as ph  # noqa: E402
from fetch import validate  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

P = "Chainlink"


def _flags(rows, prior, meta):
    out = FetchOutput()
    df = pd.DataFrame([{"date": d, "project": P, "metric": m, "value": v, "source": s, "tier": 1}
                       for d, m, v, s in rows])
    validate.validate_frame(df, prior, out, meta)
    return [r for r in out.review if r["reason"] == "change_threshold"]


# ----- 4: the price row dated 2025-06-13 flagged against today's price ------------------------------------------------
def test_4_a_backfilled_price_day_is_not_compared_with_the_stores_newest_price():
    """The price-history tier's rows as it writes them today (HISTORY_POINT), newest 2025-06-13, against the store's
    newest price (2026-11-23): a back-fill of past days, not a change."""
    src = f"{ph.HISTORY_POINT}[coins.llama.fi coingecko:chainlink]"
    rows = [("2025-06-12", "price_usd", 13.10, src), ("2025-06-13", "price_usd", 13.05, src)]
    prior = {(P, "price_usd"): 31.0}
    meta = {(P, "price_usd"): (31.0, "2026-11-23", "coingecko")}
    assert _flags(rows, prior, meta) == []
    assert _flags(rows, prior, None) == [], "with or without the stored row's date"
    # the tier's first spelling is still skipped
    old = [("2025-06-13", "price_usd", 13.05, "price_history:coins.llama.fi[coingecko:chainlink]")]
    assert _flags(old, prior, meta) == []


def test_4_the_guard_reads_the_tiers_own_measuring_point():
    assert ph.HISTORY_POINT.startswith(validate._PRICE_BACKFILL_POINTS)
    assert not "coingecko".startswith(validate._PRICE_BACKFILL_POINTS)
    assert not "defillama:fees".startswith(validate._PRICE_BACKFILL_POINTS)


def test_4_a_real_price_move_is_still_flagged():
    """Nothing is loosened: a stored-price series moving beyond its threshold on a later day flags as before."""
    thr = config.change_threshold_pct(P, "price_usd") / 100.0
    now, was = 10.0, 10.0 * (1 + thr) * 1.5
    # another measuring point, dated after the stored row: an ordinary day-on-day comparison
    got = _flags([("2026-11-23", "price_usd", now, "coinbase:spot")], {(P, "price_usd"): was},
                 {(P, "price_usd"): (was, "2026-11-22", "coinbase:spot")})
    assert len(got) == 1 and got[0]["value"] == now and got[0]["prior_value"] == was
    assert str(got[0]["prior_date"])[:10] == "2026-11-22"
    # and a move inside the threshold does not
    assert _flags([("2026-11-23", "price_usd", was * (1 - thr / 2), "coinbase:spot")], {(P, "price_usd"): was},
                  {(P, "price_usd"): (was, "2026-11-22", "coinbase:spot")}) == []


# ----- 3: emissions_claimed_tokens, -81% week on week -----------------------------------------------------------------
# LINK Transfer events out of the RewardVault 0x996913c8…, read 2026-11-23 from eth.blockscout.com (tokentx), per UTC day
CLAIMS = {"2026-11-09": 6313.50, "2026-11-10": 7131.31, "2026-11-11": 2777.36, "2026-11-12": 5437.28,
          "2026-11-13": 27462.74, "2026-11-14": 2246.76, "2026-11-15": 3247.87, "2026-11-16": 759.77,
          "2026-11-17": 456.32, "2026-11-18": 1030.44, "2026-11-19": 298.01, "2026-11-20": 4200.75,
          "2026-11-21": 2695.98, "2026-11-22": 981.96}


def test_3_the_claims_week_is_still_checked_and_reads_the_real_fall():
    """The series stays a checked, lumpy daily flow (two 7-day windows): the live fortnight flags, at the real -80.9% —
    a fall of the claims themselves (444 claims on 2026-11-13 in the earlier week), not a hole in the read."""
    m = "emissions_claimed_tokens"
    assert config.lumpy_flow(P, m) and config.METRICS[m]["kind"] == "flow"
    rows = [(d, m, v, "explorer:logs") for d, v in CLAIMS.items()]
    got = _flags(rows, {(P, m): 981.96}, None)
    assert len(got) == 1
    cur, prev = got[0]["value"], got[0]["prior_value"]
    assert abs(cur - 10423.23) < 0.01 and abs(prev - 54616.82) < 0.01
    assert abs(cur / prev - 1 - (-0.809)) < 0.001
    assert "TRAILING 7-DAY SUM" in got[0]["basis"]
    # one day carries half the earlier week: the fall is that day's cluster of claims
    assert CLAIMS["2026-11-13"] / prev > 0.5


# ----- 2: CCIP token value — a part is never the whole ----------------------------------------------------------------
class _Out(FetchOutput):
    pass


def _emit(tmp_path, tokens, prices, n_msgs):
    """One closed day through CcipValue._emit with the given per-token raw sums and prices."""
    c = cv.CcipValue(root=tmp_path)
    c.spec = {**cv.DEFAULTS}
    c.stats = __import__("collections").defaultdict(int)
    c.target, c.yesterday = "2026-11-22", "2026-11-22"
    c._detail_error = c._price_error = None
    c._priced = set()
    eth, base = "5009297550715157269", "15971525489660198786"
    c._tok = {"tokens": {f"{eth}|0xaa": [6, "USDC", None], f"{base}|0xbb": [18, "XYZ", None]}}
    c._groups = {}
    c.st = {"days": {"2026-11-22": {"stage": "closed", "n_msgs": 1000, "n_token_msgs": n_msgs, "tokens": tokens,
                                    "multi": [], "check": {}, "dests": {}}}, "walks": [], "floor": None}
    c.prices = {"2026-11-22": prices}
    out = _Out()
    c._emit(P, out, [])
    rows = pd.concat(out.frames, ignore_index=True) if out.frames else pd.DataFrame(columns=["metric", "source"])
    return rows[rows["metric"] == cv.VALUE], out


def test_2_a_day_with_an_unpriced_transfer_is_marked_partial_never_a_plain_figure(tmp_path):
    eth, base = "5009297550715157269", "15971525489660198786"
    tokens = {f"{eth}|0xaa": [str(5_000_000 * 10 ** 6), 99], f"{base}|0xbb": [str(10 ** 18), 1]}
    val, _out = _emit(tmp_path, tokens, {"ethereum:0xaa": [1.0, 6], "base:0xbb": None}, 100)
    assert len(val) == 1 and abs(float(val["value"].iloc[0]) - 5_000_000.0) < 1e-6
    src = str(val["source"].iloc[0])
    assert src.startswith(config.mark_source(cv.SRC, "PARTIAL")) and "1 of 100 transfers unpriced" in src


def test_2_a_day_mostly_unpriced_is_refused_not_stored_smaller(tmp_path):
    eth, base = "5009297550715157269", "15971525489660198786"
    tokens = {f"{eth}|0xaa": [str(5_000_000 * 10 ** 6), 28], f"{base}|0xbb": [str(10 ** 18), 72]}
    val, out = _emit(tmp_path, tokens, {"ethereum:0xaa": [1.0, 6], "base:0xbb": None}, 100)
    assert val.empty, "72% of the day's messages unpriced: no row, never the priced 28% as the day"
    assert any("REFUSED" in str(e.message) for e in out.log)
