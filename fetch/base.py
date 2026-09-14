"""
fetch/base.py — shared plumbing for every tier adapter.

Every adapter returns tidy long frames: date, project, metric, value, source, tier.
A failed source must never kill the run: failures are logged, gaps are reported.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger("token_metrics.fetch")

LONG_COLUMNS = ["date", "project", "metric", "value", "source", "tier"]
USER_AGENT = os.environ.get(
    "TOKEN_METRICS_USER_AGENT",
    "token-metrics/2.0 (research tool; +https://github.com/JakeAndersonWistCap/Crypto-Tracker)",
)


def today() -> pd.Timestamp:
    return pd.Timestamp.now("UTC").tz_localize(None).normalize()


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


@dataclass
class LogEntry:
    source: str
    project: str | None
    rows: int
    status: str            # ok | failed | skipped | unconfigured
    message: str = ""
    tier: int | None = None


@dataclass
class FetchOutput:
    """Collects frames, log entries, review-queue items and gap-report items across every adapter."""
    frames: list = field(default_factory=list)
    log: list = field(default_factory=list)
    review: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    staged: list = field(default_factory=list)

    def add(self, df: pd.DataFrame | None, source: str, project: str | None, message: str = "", tier: int | None = None):
        n = 0 if df is None else len(df)
        if df is not None and n:
            self.frames.append(df[LONG_COLUMNS])
        self.log.append(LogEntry(source, project, n, "ok", message, tier))

    def fail(self, source: str, project: str | None, err, tier: int | None = None):
        msg = str(err)
        log.warning("FAILED %s / %s: %s", source, project, msg)
        self.log.append(LogEntry(source, project, 0, "failed", msg, tier))

    def unconfigured(self, source: str, project: str | None, message: str, tier: int | None = None):
        self.log.append(LogEntry(source, project, 0, "unconfigured", message, tier))

    def skipped(self, source: str, project: str | None, message: str, tier: int | None = None):
        """A source that COULD have run and deliberately did not.

        Distinct from unconfigured (nothing to run) and from ok (it ran). Collapsing the three is
        how a backfill that never executed gets read as a backfill that succeeded: no error, no
        failure, no gap — just a quiet zero that looks like a clean run.
        """
        self.log.append(LogEntry(source, project, 0, "skipped", message, tier))

    def review_item(self, project: str, metric: str, reason: str, action: str, value=None,
                    prior_value=None, date=None, source=None, tier=None):
        self.review.append({"project": project, "metric": metric, "reason": reason, "action": action,
                            "value": value, "prior_value": prior_value,
                            "date": str(date)[:10] if date is not None else None,
                            "source": source, "tier": tier})

    def gap(self, project: str, metric: str, reason: str, tiers_attempted="", suggestion=""):
        self.gaps.append({"project": project, "metric": metric, "reason": reason,
                          "tiers_attempted": tiers_attempted, "suggestion": suggestion})

    def stage(self, project: str, name: str, value, date=None, source=None, tier=None, note: str = ""):
        """Capture a figure WITHOUT letting it near the metrics table.

        A source sometimes returns something useful that is not yet an agreed metric — a
        14-day aggregate where the model uses a monthly one, say. Throwing it away means
        re-discovering it later; storing it as a metric means it silently starts driving a
        number nobody chose it for. Staging is the third option: it is recorded, dated and
        visible in the workbook under its own sheet, and nothing reads it.
        """
        self.staged.append({"project": project, "name": name, "value": value,
                            "date": str(date)[:10] if date is not None else None,
                            "source": source, "tier": tier, "note": note})

    def frame(self) -> pd.DataFrame:
        if not self.frames:
            return pd.DataFrame(columns=LONG_COLUMNS)
        return pd.concat(self.frames, ignore_index=True)


class HttpError(RuntimeError):
    """A 4xx, carrying the server's own message.

    Kept distinct from a transport failure because the body is the useful part: it is what tells
    you whether a resource does not exist, or exists and you lack access to it. Raised without
    retrying, since a 4xx is a definite answer.
    """

    def __init__(self, status: int, url: str, detail: str = ""):
        self.status, self.url, self.detail = status, url, detail
        super().__init__(f"HTTP {status} from {url}" + (f" — {detail}" if detail else ""))


class Http:
    """Retry with exponential backoff on 429/5xx. Never disables TLS verification."""

    def __init__(self, min_interval: float = 0.0, retries: int = 4, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.min_interval = float(os.environ.get("TOKEN_METRICS_MIN_INTERVAL", min_interval))
        self.retries = int(os.environ.get("TOKEN_METRICS_RETRIES", retries))
        self.timeout = timeout
        self._last = 0.0

    def post(self, url: str, json_body: dict | None = None, headers: dict | None = None):
        """Same retry/backoff policy as get(). Some node APIs only accept POST."""
        return self._request("POST", url, json_body=json_body, headers=headers)

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        return self._request("GET", url, params=params, headers=headers)

    def _request(self, verb: str, url: str, params: dict | None = None,
                 json_body: dict | None = None, headers: dict | None = None):
        wait = time.monotonic() - self._last
        if wait < self.min_interval:
            time.sleep(self.min_interval - wait)
        backoff, last_err = 2.0, None
        for attempt in range(self.retries + 1):
            self._last = time.monotonic()
            try:
                r = (self.s.post(url, json=json_body or {}, headers=headers, timeout=self.timeout)
                     if verb == "POST" else
                     self.s.get(url, params=params, headers=headers, timeout=self.timeout))
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {r.status_code} from {url}")
                    if attempt < self.retries:
                        time.sleep(max(float(r.headers.get("Retry-After", backoff)), backoff))
                        backoff *= 2
                    continue
                if 400 <= r.status_code < 500:
                    detail = ""
                    try:
                        body = r.json()
                        detail = body.get("error") or body.get("message") or str(body)[:300]
                    except Exception:  # noqa: BLE001
                        detail = (r.text or "")[:300]
                    raise HttpError(r.status_code, url, detail)
                r.raise_for_status()
                return r.json()
            except HttpError:
                raise          # a 4xx is a definite answer from the server, not worth retrying
            except requests.RequestException as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(backoff)
                    backoff *= 2
        raise RuntimeError(f"gave up after {self.retries + 1} attempts: {last_err}")


def tidy(rows, project: str, metric: str, source: str, tier: int) -> pd.DataFrame:
    """(date, value) pairs -> tidy long frame, normalised to midnight UTC, tz-naive."""
    df = pd.DataFrame(list(rows), columns=["date", "value"])
    if df.empty:
        return pd.DataFrame(columns=LONG_COLUMNS)
    df["date"] = pd.to_datetime(df["date"], utc=True, format="mixed").dt.tz_localize(None).dt.normalize()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"]).drop_duplicates(subset=["date"], keep="last")
    df["project"], df["metric"], df["source"], df["tier"] = project, metric, source, tier
    return df[LONG_COLUMNS]


def point(project: str, metric: str, value: float, source: str, tier: int, when=None) -> pd.DataFrame:
    """A single point-in-time observation — what a contract read or a dashboard scrape produces."""
    return tidy([(when or today(), value)], project, metric, source, tier)


def window(df: pd.DataFrame, window_days: int | None) -> pd.DataFrame:
    if window_days is None or df.empty:
        return df
    return df[df["date"] >= today() - pd.Timedelta(days=window_days)]


def derive_flow_from_cumulative(cumulative_value: float, prior_cumulative: float | None,
                                project: str, metric: str, source: str, tier: int,
                                when=None, prior_date=None, stock_metric: str | None = None,
                                out=None, prior_source: str | None = None, source_base: str | None = None) -> pd.DataFrame:
    """Turn a running total into the flow since the last observation.

    A dashboard that publishes "total burned to date" is a stock. The archetype 4 tab needs a
    flow. Differencing against the previously stored cumulative gives that; a decrease means
    the source rebased, so no flow row is emitted (the Review Queue picks it up separately).

    A DELTA REQUIRES AN INTERVAL, and that is not the same thing as having a prior number to
    subtract. Two readings that land on the same date collapse to one row in the store, so the
    difference between them spans nothing the series can represent — and it comes out at 0
    whatever the truth is. A zero in a burn column is the most misleading cell this tool can
    produce: it is indistinguishable from a measured "nothing was burned this period", and it
    appears on exactly the archetype-4 names where burn is the whole point. So a flow is emitted
    only when there is a prior observation ON AN EARLIER DATE; otherwise nothing is stored and
    the reason is reported, which renders as n/a rather than as a confident zero.
    """
    if prior_cumulative is None:
        _no_flow(out, project, metric, stock_metric,
                 "there is no prior observation to difference against — this is the first reading "
                 "of the cumulative figure")
        return pd.DataFrame(columns=LONG_COLUMNS)
    if prior_date is not None and when is not None and str(prior_date)[:10] >= str(when)[:10]:
        _no_flow(out, project, metric, stock_metric,
                 f"the only prior observation is dated {str(prior_date)[:10]}, the same date as this "
                 f"one, so the store holds a SINGLE observation and there is no interval to "
                 f"difference over")
        return pd.DataFrame(columns=LONG_COLUMNS)
    # A DELTA IS ONLY A FLOW IF BOTH READINGS MEASURED THE SAME THING.
    # Uniswap's burn address was re-pointed from the Firepit contract (which holds UNI awaiting
    # release) to the dead address (which holds every UNI ever burned). The next run differenced
    # 4,000 against 111,341,581 and reported 111,337,581 UNI burned in a month, against a real
    # rate of 100-134k a day. Nothing about that figure looked wrong: it was in range, it had a
    # plausible source, and it was thirty times the truth.
    if prior_source and source_base and _measuring_point(prior_source) != _measuring_point(source_base):
        _no_flow(out, project, metric, stock_metric,
                 f"the measuring point CHANGED between observations — the prior reading came from "
                 f"{_measuring_point(prior_source)!r} and this one from {_measuring_point(source_base)!r}. "
                 f"Differencing across that reports the change of address as though it were a flow")
        return pd.DataFrame(columns=LONG_COLUMNS)
    if cumulative_value < prior_cumulative:
        return pd.DataFrame(columns=LONG_COLUMNS)
    return point(project, metric, cumulative_value - prior_cumulative, source, tier, when)


def _measuring_point(source: str) -> str:
    """The part of a source string that says WHAT was read, ignoring how it was labelled.

    'chain:ethereum:burn_dead:PARTIAL' and 'chain:ethereum:burn_dead' measure the same address;
    'chain:ethereum:fire_pit' does not. Trailing markers are stripped so a figure becoming PARTIAL
    does not read as a change of address.
    """
    parts = [p for p in str(source).split(":") if p not in ("PARTIAL", "delta")]
    return ":".join(parts)


def _no_flow(out, project: str, metric: str, stock_metric: str | None, why: str):
    """Say why a differenced flow could not be computed, instead of storing a 0 that means nothing."""
    if out is None:
        return
    stock = stock_metric or "the cumulative figure"
    out.gap(project, metric,
            reason=f"{metric} is NOT AVAILABLE, and is deliberately not reported as 0: {why}. A "
                   f"differenced flow measures the change between two dated readings, so with fewer "
                   f"than two it has no value — not a value of zero.",
            tiers_attempted="2, 3",
            suggestion=f"It resolves itself: run again on a later day and {stock} will have two "
                       f"observations to difference. Nothing to fix. If the period figure is needed "
                       f"sooner, or for history before the tool started watching, that needs a "
                       f"transfer-history source (a Dune query on the decoded transfer table), not a "
                       f"balance read.")


NUMERIC_SUFFIX = {"k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9, "t": 1e12}


def parse_number(text) -> float | None:
    """Parse a figure as a dashboard renders it: '1,234,567', '$1.2M', '(1,958,514)', '45.2%'.

    Percentages come back as fractions. Parenthesised values are negative. Returns None when
    the text holds no number — the caller treats that as an extraction failure, never as zero.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    percent = "%" in s
    s = s.replace("%", "").replace("$", "").replace(" ", " ").replace(",", "").strip()
    if s.startswith("-"):
        negative, s = True, s[1:].strip()
    if s.startswith("+"):
        s = s[1:].strip()
    mult = 1.0
    low = s.lower()
    for suffix in sorted(NUMERIC_SUFFIX, key=len, reverse=True):
        if low.endswith(suffix):
            head = low[: -len(suffix)].strip()
            if head and _is_float(head):
                mult, s = NUMERIC_SUFFIX[suffix], head
                break
    if not _is_float(s):
        return None
    v = float(s) * mult
    if percent:
        v /= 100.0
    return -v if negative else v


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def json_path_get(payload, path: str):
    """Walk a dotted path through nested dicts/lists: 'data.items.0.netMint'."""
    cur = payload
    for part in str(path).split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur
