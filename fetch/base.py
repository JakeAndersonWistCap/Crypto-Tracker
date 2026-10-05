"""
fetch/base.py — shared plumbing for every tier adapter.

Every adapter returns tidy long frames: date, project, metric, value, source, tier.
A failed source must never kill the run: failures are logged, gaps are reported.
"""
from __future__ import annotations

import contextlib
import itertools
import logging
import os
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests
import requests.adapters

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


# ===== SECRETS NEVER REACH A LOG, A PRINT, THE STORE OR THE WORKBOOK. 2026-09-29 (Jake). =====
# archive_probe printed "403 Client Error: Forbidden for url: https://arb-mainnet.g.alchemy.com/
# v2/<key>" — the key sits in the URL PATH, and the provider's exception carries the whole URL.
# redact() is applied at every sink (LogEntry, FetchOutput.fail/gap, the logging filter, the CLI
# stdout wrapper, the store's run_log/gap rows, the workbook's Run Log), so no call site has to
# remember. Three layers, each enough on its own for the common case:
#   1. every URL is cut to scheme://host — the path and query are where keys live;
#   2. key=value / apikey=value style parameters outside a URL are masked;
#   3. the VALUES of secret-bearing environment variables (*_KEY, *_TOKEN, *_SECRET, and the
#      path/query of every *_URL) are masked wherever they appear, URL or not.
# It never raises: a redactor that throws takes down the read it was describing, and falling
# back to the raw text is how a redactor leaks.
import re as _re

_URL_ANY = _re.compile(r"(https?|wss?)://([^/\s'\"<>)\]]+)([^\s'\"<>)\]]*)", _re.I)
_KEY_PARAM = _re.compile(r"((?:api[_-]?key|apikey|access[_-]?token|token|dkey|key|secret)\s*[=:]\s*)"
                         r"([A-Za-z0-9_\-\.]{8,})", _re.I)
_SECRET_ENV = _re.compile(r"(_KEY|_TOKEN|_SECRET|_PASSWORD)$", _re.I)
# Hosts that carry the key IN THE PATH. Their whole path goes, whatever it looks like.
_KEYED_HOSTS = ("alchemy.com", "infura.io", "quiknode.pro", "drpc.org", "ankr.com", "blastapi.io",
                "chainstack.com", "getblock.io", "nodereal.io", "moralis.io", "llamarpc.com",
                "tenderly.co", "helius-rpc.com", "helius.xyz", "fastnear.com")
# A path segment shaped like a key: long, mixed letters and digits, not an 0x address/hash and not
# a 40/64-hex digest (commit and transaction hashes in docs and explorer links stay readable).
_KEYISH = _re.compile(r"^(?!0x)(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{20,}$")
_HEX_DIGEST = _re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")


def _env_secrets() -> list[str]:
    out = []
    for name, val in os.environ.items():
        val = (val or "").strip()
        if not val:
            continue
        if _SECRET_ENV.search(name) and len(val) >= 8 and "://" not in val:
            out.append(val)
        if name.upper().endswith("_URL") or name.upper().startswith("RPC_"):
            for u in val.split(","):
                parts = urllib.parse.urlsplit(u.strip())
                out += [p for p in parts.path.split("/") if len(p) >= 12]
                if len(parts.query) >= 8:
                    out.append(parts.query)
    return sorted(set(out), key=len, reverse=True)


def _url(m) -> str:
    scheme, host, rest = m.group(1), m.group(2), m.group(3) or ""
    bare = host.split("@")[-1].split(":")[0].lower()
    if "@" in host:                                   # user:pass@host
        host = "***@" + host.split("@")[-1]
    if any(bare == h or bare.endswith("." + h) for h in _KEYED_HOSTS):
        return f"{scheme}://{host}"
    path, _, query = rest.partition("?")
    segs = path.split("/")
    if any(_KEYISH.match(x) and not _HEX_DIGEST.match(x) for x in segs):
        return f"{scheme}://{host}"
    if query:
        query = _KEY_PARAM.sub(lambda q: q.group(1) + "***", query)
        return f"{scheme}://{host}{path}?{query}"
    return f"{scheme}://{host}{path}"


def redact(text) -> str:
    """The text with key-bearing URLs cut to their host, key parameters masked, and the values of
    secret environment variables masked wherever they appear."""
    try:
        s = str(text)
        for secret in _env_secrets():
            if secret in s:
                s = s.replace(secret, "***")
        s = _URL_ANY.sub(_url, s)
        return _KEY_PARAM.sub(lambda m: m.group(1) + "***", s)
    except Exception:  # noqa: BLE001 — a redactor must never raise
        return "<unprintable: redaction failed>"


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and record.exc_info[1] is not None:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
                record.exc_info = None
        except Exception:  # noqa: BLE001
            record.msg, record.args = "<unprintable: redaction failed>", None
        return True


class _RedactingStream:
    """stdout/stderr wrapper for the CLI scripts: whatever is printed is redacted first."""

    def __init__(self, stream):
        self._s = stream

    def write(self, text):
        return self._s.write(redact(text))

    def __getattr__(self, name):
        return getattr(self._s, name)


_FACTORY_INSTALLED = False


def install_redaction(streams: bool = True) -> None:
    """Redact every log record — at CREATION, through the record factory, so every logger and
    every handler added later is covered — and, for a CLI, stdout/stderr."""
    import sys
    global _FACTORY_INSTALLED
    if not _FACTORY_INSTALLED:
        old = logging.getLogRecordFactory()

        def factory(*a, **kw):
            rec = old(*a, **kw)
            _RedactFilter().filter(rec)
            return rec
        logging.setLogRecordFactory(factory)
        _FACTORY_INSTALLED = True
    f = _RedactFilter()
    root = logging.getLogger()
    if not any(isinstance(x, _RedactFilter) for x in root.filters):
        root.addFilter(f)
    for h in root.handlers:
        if not any(isinstance(x, _RedactFilter) for x in h.filters):
            h.addFilter(f)
    if streams:
        for name in ("stdout", "stderr"):
            cur = getattr(sys, name)
            if not isinstance(cur, _RedactingStream):
                setattr(sys, name, _RedactingStream(cur))


class RateLimitedTooLong(RuntimeError):
    """A rate-limited call could not be completed inside the time budget."""


class AdaptivePacer:
    """Spacing for a rate-limited endpoint, learned from its 429s (2026-09-29).

    Starts at `start` seconds between calls (0 for a keyed endpoint that has never limited us).
    A 429 — an HTTP 429, or an exception whose text says "429" / "Too Many Requests" / "rate
    limit" — waits (Retry-After if given, else 5 -> 15 -> 30 -> 60 -> 120s), retries the SAME call,
    and slows the spacing x1.5 (at least 0.25s, at most `ceiling`); 25 clean calls in a row speed
    it x0.9. The caller persists `pace` so the next pass starts from what worked."""

    BACKOFFS = (5, 15, 30, 60, 120)

    def __init__(self, start: float = 0.0, ceiling: float = 15.0, floor: float = 0.0,
                 sleep=None, deadline: float | None = None):
        self.pace, self.ceiling, self.floor = float(start), float(ceiling), float(floor)
        self.sleep = sleep or time.sleep
        self.deadline = deadline
        self._last, self._streak = 0.0, 0
        self.stats = {"calls": 0, "429s": 0, "waited_s": 0.0}

    @staticmethod
    def limited(e) -> bool:
        t = str(e).lower()
        return "429" in t or "too many requests" in t or "rate limit" in t or "rate-limit" in t

    def _nap(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.deadline is not None and time.monotonic() + seconds > self.deadline:
            raise RateLimitedTooLong(f"a {seconds:.0f}s wait would overrun the time budget")
        self.stats["waited_s"] += seconds
        self.sleep(seconds)

    def run(self, fn):
        for attempt in range(len(self.BACKOFFS) + 1):
            self._nap(self._last + self.pace - time.monotonic())
            self._last = time.monotonic()
            self.stats["calls"] += 1
            try:
                out = fn()
            except Exception as e:  # noqa: BLE001
                if not self.limited(e):
                    raise
                self.stats["429s"] += 1
                self._streak = 0
                self.pace = min(max(self.pace * 1.5, 0.25), self.ceiling)
                if attempt == len(self.BACKOFFS):
                    raise
                self._nap(self.BACKOFFS[attempt])
                continue
            self._streak += 1
            if self._streak >= 25:
                self.pace, self._streak = max(self.pace * 0.9, self.floor), 0
            return out


@dataclass
class LogEntry:
    source: str
    project: str | None
    rows: int
    status: str            # ok | failed | skipped | unconfigured
    message: str = ""
    tier: int | None = None

    def __post_init__(self):
        # EVERY log line is redacted where it is made, so no adapter can forget (2026-09-29).
        self.message = redact(self.message)



@dataclass
class FetchOutput:
    """Collects frames, log entries, review-queue items and gap-report items across every adapter."""
    frames: list = field(default_factory=list)
    log: list = field(default_factory=list)
    review: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    staged: list = field(default_factory=list)
    # Wall clock per source, filled by fetch_all. Not stored and not displayed on a tab: it is
    # an operational fact about one run, not a figure about a project, and putting it in the
    # store would make it look like one.
    timings: list = field(default_factory=list)
    # {source: (tier, budget seconds)} for every source the dispatcher abandoned this run.
    timed_out: dict = field(default_factory=dict)
    # (project, metric) pairs a source deliberately did not re-fetch because the store already
    # holds the latest figure it could give (a daily aggregate stored for yesterday, a check that
    # runs once a day). Not a gap: detect() treats them as covered. See current().
    current: set = field(default_factory=set)

    def mark_current(self, source: str, project: str, metric: str, message: str,
                     tier: int | None = None):
        """Skipped because the stored figure is already the latest — logged, and NOT a gap."""
        self.current.add((project, metric))
        self.log.append(LogEntry(source, project, 0, "skipped", message, tier))

    def add(self, df: pd.DataFrame | None, source: str, project: str | None, message: str = "", tier: int | None = None):
        n = 0 if df is None else len(df)
        if df is not None and n:
            self.frames.append(df[LONG_COLUMNS])
        self.log.append(LogEntry(source, project, n, "ok", message, tier))

    def fail(self, source: str, project: str | None, err, tier: int | None = None):
        msg = redact(err)
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
                    prior_value=None, date=None, source=None, tier=None,
                    prior_date=None, basis=None):
        """prior_date and basis say WHAT the value was compared against, and why that one.

        A change_threshold row without them states that something moved and leaves the reader to
        guess the comparison. Twelve such flags on the run of 2026-09-21 were all the same
        comparison — a Sunday against a Friday — and nothing on the row said so.
        """
        self.review.append({"project": project, "metric": metric, "reason": reason, "action": action,
                            "value": value, "prior_value": prior_value,
                            "date": str(date)[:10] if date is not None else None,
                            "prior_date": str(prior_date)[:10] if prior_date is not None else None,
                            "basis": basis,
                            "source": source, "tier": tier})

    def gap(self, project: str, metric: str, reason: str, tiers_attempted="", suggestion=""):
        self.gaps.append({"project": project, "metric": metric, "reason": redact(reason),
                          "tiers_attempted": tiers_attempted, "suggestion": redact(suggestion)})

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


class BudgetExhausted(RuntimeError):
    """A caller's total time budget ran out — distinct from a server's answer."""


def retry_after(value) -> float | None:
    """Retry-After in seconds (RFC 9110 §10.2.3: delay-seconds OR an HTTP-date), None if absent
    or unreadable. The old float() read raised on the HTTP-date form."""
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip()
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        import datetime as _dt
        when = parsedate_to_datetime(v)
        return max(0.0, (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError):
        return None


# ===== EVERY HTTP CALL IS BOUNDED. 2026-09-28, after a run hung for over an hour. =====
# CONNECT and READ timeouts are separate: a host that never accepts the connection fails in
# CONNECT_TIMEOUT_S, and one that accepts and then goes quiet fails in the read timeout.
CONNECT_TIMEOUT_S = float(os.environ.get("TOKEN_METRICS_CONNECT_TIMEOUT", 10))
READ_TIMEOUT_S = float(os.environ.get("TOKEN_METRICS_READ_TIMEOUT", 60))
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
# ** THE LONGEST A 429 IS EVER WAITED OUT. ** beaconcha.in's limiter (gobitfly/eth2-beaconchain-
# explorer, ratelimit/ratelimit.go) sets Retry-After to the seconds until its quota window
# resets — the next UTC HOUR, or the next UTC MONTH — and Http slept for whatever it said. That
# was the hang of 2026-09-28. A Retry-After longer than this is not waited: the call fails at
# once, naming the wait the server asked for, and the next run tries again.
MAX_RETRY_WAIT_S = float(os.environ.get("TOKEN_METRICS_MAX_RETRY_WAIT", 65))
# Rate-limit headers worth printing when a 429 is refused — counts and windows, never keys.
_RATE_HEADERS = ("retry-after", "ratelimit-reset", "ratelimit-window", "x-ratelimit-remaining-minute",
                 "x-ratelimit-remaining-hour", "x-ratelimit-remaining-day",
                 "x-ratelimit-remaining-month", "x-ratelimit-reset", "x-ratelimit-limit")


class RateLimited(RuntimeError):
    """A 429 whose Retry-After is longer than MAX_RETRY_WAIT_S. Not waited; the message says why.
    `retry_after` (seconds) and `headers` (the rate-limit headers only, never keys) let a caller
    that holds several endpoints of one host record each endpoint's own wait (2026-09-30:
    beaconcha.in asked 150,198s on /ethstore/latest and 36,338s on /ethstore/{day})."""

    def __init__(self, msg: str, retry_after: float | None = None, headers: dict | None = None):
        super().__init__(msg)
        self.retry_after = retry_after
        self.headers = headers or {}


# ===== WHAT THE RUN IS WAITING ON, FOR THE HEARTBEAT. =====
# Every outstanding HTTP request (any requests-based client, web3 included — see
# _instrumented_send) and every deliberate sleep (Http's retry naps, NearBlocks' pacing) is
# registered here with the source running it (the dispatcher names each source's thread) and the
# host. fetch.Heartbeat reads it and logs anything outstanding for more than 30 seconds, so a
# stall is visible instead of looking like a frozen shell.
_INFLIGHT: dict[int, tuple[str, str, float]] = {}
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_SEQ = itertools.count()


@contextlib.contextmanager
def waiting_on(what: str):
    token = next(_INFLIGHT_SEQ)
    with _INFLIGHT_LOCK:
        _INFLIGHT[token] = (threading.current_thread().name, what, time.monotonic())
    try:
        yield
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(token, None)


def inflight() -> list[tuple[str, str, float]]:
    """(source, what, seconds outstanding) for everything currently being waited on."""
    now = time.monotonic()
    with _INFLIGHT_LOCK:
        return [(src, what, now - t0) for src, what, t0 in _INFLIGHT.values()]


def sleep(seconds: float, what: str) -> None:
    """time.sleep, registered so the heartbeat can name it."""
    if seconds <= 0:
        return
    with waiting_on(what):
        time.sleep(seconds)


# ===== THE SAFETY NET UNDER EVERY requests CALL, web3's included. =====
# Registers the request for the heartbeat and, if a caller passed no timeout, applies
# DEFAULT_TIMEOUT rather than letting it wait for ever. tests/test_adapters.py also checks
# statically that every call in fetch/ passes one explicitly — this is the backstop, not the rule.
_ORIGINAL_SEND = requests.adapters.HTTPAdapter.send


def _instrumented_send(self, request, stream=False, timeout=None, **kw):
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    host = urllib.parse.urlsplit(request.url).netloc
    with waiting_on(f"{request.method} {host}"):
        return _ORIGINAL_SEND(self, request, stream=stream, timeout=timeout, **kw)


if getattr(requests.adapters.HTTPAdapter.send, "__name__", "") != "_instrumented_send":
    requests.adapters.HTTPAdapter.send = _instrumented_send


def query_params(params: dict | None) -> dict:
    """Keyset paging params as a QUERY STRING carries them (Jake's tokentx run 2026-10-01: HTTP 422 from
    explorer.plume.org/api/v2/tokens, "pointer /is_name_null, Invalid boolean. Got: string"). A
    Blockscout next_page_params object is JSON — booleans and nulls — and requests sends a Python
    True as "True" and DROPS a None, so is_name_null was refused and a null name / market_cap would
    have reset paging to page 1. Serialised as Blockscout's own frontend does: true/false, null."""
    out = {}
    for k, v in (params or {}).items():
        out[k] = "true" if v is True else "false" if v is False else "null" if v is None else v
    return out


def limit_wait(headers: dict | None, now: float | None = None) -> float:
    """Seconds to wait before the next call, from a server's x-ratelimit-* headers: 0 while calls
    remain. x-ratelimit-reset is MILLISECONDS to the end of the current fixed window on Blockscout
    (blockscout/blockscout plug/rate_limit.ex: `expires_at - now` from System.system_time(:millisecond));
    a value above 1e9 is read as an epoch time in seconds (the other common convention)."""
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    try:
        remaining = int(float(h.get("x-ratelimit-remaining")))
        reset = float(h.get("x-ratelimit-reset"))
    except (TypeError, ValueError):
        return 0.0
    if remaining > 0 or reset < 0:
        return 0.0
    if reset > 1e9:
        return max(reset - (time.time() if now is None else now), 0.0)
    return reset / 1000.0


class Http:
    """Retry with exponential backoff on 429/5xx. Never disables TLS verification."""

    def __init__(self, min_interval: float = 0.0, retries: int = 4, timeout: float = READ_TIMEOUT_S,
                 rate_limit_wait: float | None = None, max_retry_wait: float | None = None):
        # rate_limit_wait: the wait on a 429 that carries no Retry-After, for a source whose
        # limiter window is known (NearBlocks: one minute). None keeps the exponential backoff.
        self.rate_limit_wait = rate_limit_wait
        self.max_retry_wait = MAX_RETRY_WAIT_S if max_retry_wait is None else float(max_retry_wait)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.min_interval = float(os.environ.get("TOKEN_METRICS_MIN_INTERVAL", min_interval))
        self.retries = int(os.environ.get("TOKEN_METRICS_RETRIES", retries))
        self.timeout = float(timeout)          # the READ timeout; connect is CONNECT_TIMEOUT_S
        self._last = 0.0
        # WHERE A SOURCE'S TIME WENT (2026-09-28: DefiLlama timed out at 150s having taken 62s
        # the run before, and nothing said whether calls were slow, retried or simply many).
        # Read by fetch._dispatch into the source's timing line and its timeout message.
        self.stats = {"calls": 0, "http_s": 0.0, "retries": 0, "waited_s": 0.0,
                      "slowest_s": 0.0, "slowest": "", "times": []}

    def summary(self) -> str:
        st = self.stats
        if not st["calls"]:
            return "no HTTP calls"
        line = (f"{st['calls']} HTTP call(s), {st['http_s']:.0f}s in requests "
                f"(mean {st['http_s'] / st['calls']:.2f}s), {st['retries']} retr"
                f"{'y' if st['retries'] == 1 else 'ies'}, {st['waited_s']:.0f}s in backoff/spacing, "
                f"slowest {st['slowest_s']:.1f}s ({st['slowest']})")
        # PER-CALL TIMES (Jake's run 2026-10-05: DefiLlama timed out at 150s over 67 calls): the spread, and
        # the slowest calls by path, so a blip (a few calls hanging ~18s) reads apart from a slow API.
        times = sorted(t for t, _ in st.get("times") or ())
        if times:
            pick = lambda q: times[min(len(times) - 1, int(q * len(times)))]       # noqa: E731
            slow = sorted(st["times"], key=lambda x: -x[0])[:3]
            line += (f"; per call p50 {pick(0.5):.2f}s p90 {pick(0.9):.2f}s max {times[-1]:.1f}s, "
                     f"{sum(1 for t in times if t >= 10)} call(s) >= 10s"
                     + (f" (slowest: {', '.join(f'{p} {t:.1f}s' for t, p in slow)})" if slow else ""))
        return line

    def post(self, url: str, json_body: dict | None = None, headers: dict | None = None):
        """Same retry/backoff policy as get(). Some node APIs only accept POST."""
        return self._request("POST", url, json_body=json_body, headers=headers)

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            deadline: float | None = None):
        return self._request("GET", url, params=params, headers=headers, deadline=deadline)

    def _request(self, verb: str, url: str, params: dict | None = None,
                 json_body: dict | None = None, headers: dict | None = None,
                 deadline: float | None = None):
        # deadline (time.monotonic()): a caller's TOTAL budget. Each attempt's timeout is clipped
        # to what remains and a backoff sleep that would overrun it is not taken — the call fails
        # with BudgetExhausted instead. None keeps the old behaviour exactly.
        def left() -> float:
            return float("inf") if deadline is None else deadline - time.monotonic()

        host = urllib.parse.urlsplit(url).netloc

        def nap(seconds: float, why) -> None:
            if seconds >= left():
                raise BudgetExhausted(f"time budget exhausted before retrying ({why})")
            self.stats["waited_s"] += seconds
            sleep(seconds, f"sleeping {seconds:.0f}s before retrying {host} ({why})")

        wait = time.monotonic() - self._last
        if wait < self.min_interval:
            nap(self.min_interval - wait, "rate spacing")
        backoff, last_err = 2.0, None
        for attempt in range(self.retries + 1):
            if left() <= 0:
                raise BudgetExhausted(f"time budget exhausted ({last_err or 'before the first attempt'})")
            read = min(self.timeout, max(1.0, left()))
            timeout = (min(CONNECT_TIMEOUT_S, read), read)
            self._last = time.monotonic()
            if attempt:
                self.stats["retries"] += 1
            try:
                t0 = time.monotonic()
                try:
                    r = (self.s.post(url, json=json_body or {}, headers=headers, timeout=timeout)
                         if verb == "POST" else
                         self.s.get(url, params=params, headers=headers, timeout=timeout))
                finally:
                    took = time.monotonic() - t0
                    self.stats["calls"] += 1
                    self.stats["http_s"] += took
                    if len(self.stats.setdefault("times", [])) < 2000:
                        self.stats["times"].append((took, urllib.parse.urlsplit(url).path[:60]))
                    if took > self.stats["slowest_s"]:
                        self.stats["slowest_s"] = took
                        self.stats["slowest"] = urllib.parse.urlsplit(url).path[:80]
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {r.status_code} from {url}")
                    asked = retry_after(r.headers.get("Retry-After"))
                    if r.status_code == 429 and asked is not None and asked > self.max_retry_wait:
                        seen = {k: r.headers.get(k) for k in _RATE_HEADERS if r.headers.get(k)}
                        raise RateLimited(
                            f"HTTP 429 from {host}: the server asks for a {asked:,.0f}s wait "
                            f"(~{asked / 3600:.1f}h) — longer than the {self.max_retry_wait:.0f}s "
                            f"this tool waits, so NOT waited; retried on the next run. Its "
                            f"rate-limit headers: {seen or 'none'}", retry_after=asked, headers=seen)
                    if attempt < self.retries:
                        wait = asked
                        if wait is None and r.status_code == 429 and self.rate_limit_wait:
                            wait = self.rate_limit_wait
                        _progress_log.warning("RETRY %s: HTTP %d (attempt %d of %d)", host, r.status_code,
                                              attempt + 1, self.retries + 1)
                        nap(min(max(wait or 0.0, backoff), self.max_retry_wait), last_err)
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
                # THE LAST RESPONSE'S HEADERS (2026-09-30): a caller budgeting against the
                # server's own rate-limit counters (beaconcha.in's x-ratelimit-*) reads them here.
                self.last_headers = dict(r.headers)
                return r.json()
            except HttpError:
                raise          # a 4xx is a definite answer from the server, not worth retrying
            except requests.RequestException as e:
                last_err = e
                if attempt < self.retries:
                    # A STALLED OR DROPPED CALL IS SAID, NOT WAITED ON IN SILENCE (Jake, 2026-10-01).
                    # Host and error type only: the exception text can carry the URL, and the URL a key.
                    _progress_log.warning("RETRY %s: %s after %.0fs (attempt %d of %d); next try in %.0fs",
                                          host, type(e).__name__, time.monotonic() - t0, attempt + 1,
                                          self.retries + 1, backoff)
                    nap(backoff, e)
                    backoff *= 2
        raise RuntimeError(f"gave up after {self.retries + 1} attempts: {last_err}")


# ===== A LONG SEED SAYS WHERE IT IS, AND SAVES WHERE IT IS. Jake, 2026-10-01. =====
# `--seed plume_settlement` ran for hours with no console line, and its state file was not written
# after it started — so it could not be watched, and an interrupt would have started it over. Every
# long loop now ticks a Progress: every `every_units` units or `every_s` seconds it CHECKPOINTS (the
# caller's save) and prints one line — done / total (or an estimate from the fraction covered), what
# is covered, rows kept, elapsed, time remaining.
PROGRESS_EVERY_UNITS = int(os.environ.get("TOKEN_METRICS_PROGRESS_EVERY", 500))
PROGRESS_EVERY_S = float(os.environ.get("TOKEN_METRICS_PROGRESS_SECONDS", 60))
_progress_log = logging.getLogger("token_metrics.progress")


def _hms(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds == float("inf"):
        return "unknown"
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    return f"{h}h{rem // 60:02d}m" if h else f"{rem // 60}m{rem % 60:02d}s"


class Progress:
    """tick() per unit of work; flush() at the end. `total` when it is known; otherwise `fraction`
    (a callable, 0..1 of the job covered — e.g. days reached back towards the floor) gives the ETA
    and an estimated total. `status` (a callable) says what is covered and kept; `checkpoint` (a
    callable) saves state — it runs on every line, so the line never claims progress that is not on
    disk."""

    def __init__(self, label: str, total: int | None = None, unit: str = "pages", status=None,
                 checkpoint=None, fraction=None, every_units: int | None = None,
                 every_s: float | None = None, clock=time.monotonic, logger=None):
        self.label, self.total, self.unit = label, total, unit
        self.status, self.checkpoint, self.fraction = status, checkpoint, fraction
        self.every_units = PROGRESS_EVERY_UNITS if every_units is None else every_units
        self.every_s = PROGRESS_EVERY_S if every_s is None else every_s
        self.clock, self.log = clock, logger or _progress_log
        self.t0 = self._last_t = clock()
        self.done = self._last_n = 0
        self.lines: list[str] = []

    def tick(self, n: int = 1) -> None:
        self.done += n
        if self.done - self._last_n >= self.every_units or self.clock() - self._last_t >= self.every_s:
            self.flush()

    def flush(self, final: bool = False) -> str:
        if self.checkpoint:
            self.checkpoint()
        now = self.clock()
        self._last_n, self._last_t = self.done, now
        elapsed = now - self.t0
        frac = None
        if self.fraction is not None:
            try:
                frac = self.fraction()
            except Exception:  # noqa: BLE001 — a progress estimate never breaks the work
                frac = None
        elif self.total:
            frac = self.done / self.total
        if frac is not None:
            frac = min(max(frac, 0.0), 1.0)
        tot = (f"/{self.total:,}" if self.total else
               (f"/~{int(self.done / frac):,}" if frac and self.done else ""))
        eta = 0.0 if final else (elapsed * (1 - frac) / frac if frac else None)
        st = ""
        if self.status is not None:
            try:
                st = f"; {self.status()}"
            except Exception:  # noqa: BLE001
                st = ""
        line = (f"PROGRESS {self.label}: {self.done:,}{tot} {self.unit}"
                + (f" ({frac:.1%})" if frac is not None else "") + st
                + f"; elapsed {_hms(elapsed)}; " + ("DONE" if final else f"remaining ~{_hms(eta)}")
                + ("; state saved" if self.checkpoint else ""))
        self.log.info(line)
        self.lines.append(line)
        return line


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


# ===== SERIES WHOSE STORED HISTORY IS SHORTER THAN THE SOURCE'S. Added 2026-09-28. =====
# Only the store's FIRST run asked sources for full history; every later run asks for the trailing
# 30 days. So a series added later — GEODNET and Aethir fees, a derived burn — started short and
# only ever grew forward (~38 and ~52 days against a 90-day Q0). token_metrics names the
# (project, metric) pairs whose first stored date is inside the last BACKFILL_DAYS; window()
# keeps BACKFILL_DAYS of those instead of the routine window. Every adapter that trims with
# window() backfills without knowing it. Reset per run by fetch_all.
BACKFILL_DAYS = 365
BACKFILL: set = set()


def set_backfill(pairs) -> None:
    BACKFILL.clear()
    BACKFILL.update(pairs or ())


def window(df: pd.DataFrame, window_days: int | None) -> pd.DataFrame:
    if window_days is None or df.empty:
        return df
    if BACKFILL and {"project", "metric"} <= set(df.columns):
        key = (df["project"].iloc[0], df["metric"].iloc[0])
        if key in BACKFILL:
            window_days = max(window_days, BACKFILL_DAYS)
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
        # A FALL IN A CUMULATIVE IS REPORTED, NOT PASSED OVER IN SILENCE. Added 2026-09-22 with
        # Chainlink's Reserve, and it matters most there: a dead-address balance cannot fall, so
        # this branch was effectively dead code for the only caller that existed, and returning an
        # empty frame with no explanation was harmless. The Reserve's whole premise is that it
        # only accumulates — the staking-reward leg is a separate route from source and never
        # draws on it — so a fall is either that premise breaking or a bad read, and both are
        # findings. Silence would have rendered them as an ordinary quiet day.
        _no_flow(out, project, metric, stock_metric,
                 f"the cumulative figure FELL, from {prior_cumulative:,.4f} to "
                 f"{cumulative_value:,.4f} ({cumulative_value - prior_cumulative:,.4f}). A flow "
                 f"derived from it would be negative, which this metric cannot be, so none is "
                 f"stored. A fall means the source rebased, the measuring point moved, or the "
                 f"balance is not the one-directional quantity it was taken for")
        return pd.DataFrame(columns=LONG_COLUMNS)
    # ===== THE SPAN TRAVELS WITH THE ROW. Added 2026-09-23. =====
    # A differenced flow covers the interval between two readings, and that interval is NOT
    # always a day: a run that misses a weekend writes a three-day delta, and Hyperliquid's
    # rebuilt series has several. Nothing on the row said so, so the change check compared a
    # one-day delta (17,366) against a three-day one (89,954) and flagged a 418% rise that is
    # entirely the calendar.
    #
    # WRITTEN AS A BRACKETED ANNOTATION because that slot already exists and every parser in the
    # codebase already strips it: config.strip_source_annotations removes it before resolving
    # contract keys, and _measuring_point uses that same stripper — so a span cannot be mistaken
    # for an address, and two rows with different spans are still the same measuring point.
    # Same mechanism as minter[tail@21bps].
    #
    # ONLY WHERE IT IS NOT ONE DAY. Annotating every ordinary daily delta would add noise to
    # every source string in the book to say the expected thing.
    span = None
    if prior_date is not None and when is not None:
        try:
            span = (pd.Timestamp(str(when)[:10]) - pd.Timestamp(str(prior_date)[:10])).days
        except (TypeError, ValueError):
            span = None
    if span and span > 1:
        source = f"{source}[span={span}d]"
    return point(project, metric, cumulative_value - prior_cumulative, source, tier, when)


def _measuring_point(source: str) -> str:
    """The part of a source string that says WHAT was read, ignoring how it was labelled.

    'chain:ethereum:burn_dead:PARTIAL' and 'chain:ethereum:burn_dead' measure the same address;
    'chain:ethereum:fire_pit' does not. Trailing markers are stripped so a figure becoming PARTIAL
    does not read as a change of address.
    """
    import config
    # SAME STRIPPER AS THE ORPHAN AND WITHDRAWN PARSERS, deliberately shared. This one had not
    # bitten yet: "chain:base:minter[tail@21bps]" and "chain:base:minter" are the same address,
    # and comparing them as written would have reported a change of measuring point the moment
    # Aerodrome's tail branch flipped — blanking a series for a change that never happened.
    src = config.strip_source_annotations(source)
    parts = [p for p in src.split(":") if p not in config.SOURCE_MARKERS]
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
