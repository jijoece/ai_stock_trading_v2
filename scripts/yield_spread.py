"""10Y-2Y Treasury spread from FRED (series T10Y2Y), for `score.py --yield-spread fred`.

The public CSV download needs no API key and no account:

    https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y&cosd=<start>&coed=<end>

The parser is standard library only. The download uses httpx (a project dependency, imported only when
fetching): on a python.org macOS Python 3.14, `urllib` and `http.client` both timed out against this host while
curl and httpx answered in under half a second, so do not swap in a stdlib client.

Rules, because this feeds a score:

* No look-ahead: only observations dated strictly before the session being scored are used. A session's
  own T10Y2Y value is not available at that session's close: Treasury's daily par yield curve is built from
  ~3:30 PM ET quotes and published after the close, and FRED posts it the next business day. The prior
  session's value was published the evening before, so it is the latest one known at the scored close.
* Vintage: the public CSV always returns the *current* vintage, which can include revisions published
  after a past session's close. It is therefore point-in-time only for the latest completed session;
  `score.py` refuses `--yield-spread fred` for an older `--as-of` session. Each download records its
  `retrieved_at` time and `vintage="current"` so the audit trail says which data was used.
* Fail closed: a response that is not the T10Y2Y CSV, a non-numeric or implausible value, too few
  observations for macro_pillar.py's slope (it needs 21), or a last observation more than a week
  older than the session raises `YieldSpreadError`; nothing is guessed or padded.
* FRED marks non-trading days with ".": those rows are skipped, never filled.
* The request carries a generic User-Agent only: no account, host or user details.
* TLS verification is always on (httpx uses certifi's CA bundle). Errors name only exception types,
  never host or provider text.
"""
from __future__ import annotations

import csv
import math
import ssl
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

SERIES_ID = "T10Y2Y"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
USER_AGENT = "trading-research-score/1.0"
MIN_OBSERVATIONS = 21  # macro_pillar.py: slope window 20 + 1
DEFAULT_OBSERVATIONS = 60
MAX_STALENESS_DAYS = 7
MAX_BYTES = 5_000_000
PLAUSIBLE_RANGE = (-10.0, 10.0)  # percentage points


class YieldSpreadError(Exception):
    """The FRED yield spread could not be obtained or validated."""


@dataclass(frozen=True)
class YieldSpread:
    values: list[float]  # percentage points, oldest first
    first_date: date
    last_date: date
    series_id: str = SERIES_ID
    vintage: str = "current"  # the public CSV serves the latest revision, never a past snapshot
    retrieved_at: Optional[datetime] = None  # UTC; set by fetch_fred_yield_spread

    def summary(self) -> str:
        return (f"FRED {self.series_id}, {len(self.values)} observations through {self.last_date}, "
                f"latest {self.values[-1]:+.2f}")


def parse_fred_csv(text: str, *, last_session: date, observations: int = DEFAULT_OBSERVATIONS) -> YieldSpread:
    """Validate a FRED CSV and return the last `observations` values dated strictly before `last_session`.

    `last_session`'s own observation is dropped: it is published only after that session's close.
    """
    rows = list(csv.reader(text.lstrip("﻿").splitlines()))
    if len(rows) < 2 or len(rows[0]) != 2 or rows[0][1].strip() != SERIES_ID:
        raise YieldSpreadError(f"the response is not the {SERIES_ID} CSV (unexpected header)")
    points: dict[date, float] = {}
    for row in rows[1:]:
        if len(row) != 2:
            raise YieldSpreadError("a row does not have exactly two columns")
        raw_day, raw_value = row[0].strip(), row[1].strip()
        try:
            day = date.fromisoformat(raw_day)
        except ValueError:
            raise YieldSpreadError("a row has a date that is not YYYY-MM-DD") from None
        if day >= last_session:
            continue  # not yet published at the scored close (same day) or later: never look ahead
        if raw_value in ("", "."):  # FRED's marker for a non-trading day
            continue
        try:
            value = float(raw_value)
        except ValueError:
            raise YieldSpreadError(f"{day}: value is not a number") from None
        if not math.isfinite(value) or not PLAUSIBLE_RANGE[0] <= value <= PLAUSIBLE_RANGE[1]:
            raise YieldSpreadError(f"{day}: value {raw_value} is outside the plausible range "
                                   f"{PLAUSIBLE_RANGE[0]:g}..{PLAUSIBLE_RANGE[1]:g} percentage points")
        if day in points:
            raise YieldSpreadError(f"{day}: duplicate observation")
        points[day] = value
    days = sorted(points)[-observations:]
    if len(days) < MIN_OBSERVATIONS:
        raise YieldSpreadError(
            f"only {len(days)} observations before {last_session}; need at least {MIN_OBSERVATIONS}")
    if (last_session - days[-1]).days > MAX_STALENESS_DAYS:
        raise YieldSpreadError(
            f"latest observation is {days[-1]}, more than {MAX_STALENESS_DAYS} days before {last_session}")
    return YieldSpread(values=[points[d] for d in days], first_date=days[0], last_date=days[-1])


def _describe_http_error(exc: BaseException) -> str:
    """Why a request failed, as type names only: host and provider text are never echoed."""
    chain: list[BaseException] = []
    link: Optional[BaseException] = exc
    while link is not None and len(chain) < 5 and link not in chain:
        chain.append(link)
        link = link.__cause__ or link.__context__
    detail = type(exc).__name__
    if any(isinstance(item, ssl.SSLError) for item in chain):
        detail += ": the TLS certificate could not be verified"
    return detail


def fetch_fred_yield_spread(last_session: date, *, observations: int = DEFAULT_OBSERVATIONS,
                            timeout: float = 15.0, transport: Any = None) -> YieldSpread:
    """Download and validate T10Y2Y up to `last_session` (one public GET, no credentials).

    `transport` is an httpx transport, for tests (httpx.MockTransport); leave it unset for a real request.
    """
    try:
        import httpx
    except ImportError:
        raise YieldSpreadError("downloading from FRED needs httpx; run in the project environment") from None
    retrieved_at = datetime.now(timezone.utc)
    start = last_session - timedelta(days=int(observations * 1.8) + 10)
    params ={"id": SERIES_ID, "cosd": start.isoformat(), "coed": last_session.isoformat()}
    chunks: list[bytes] = []
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport) as client:
            with client.stream("GET", FRED_CSV_URL, params=params) as response:
                if response.status_code != 200:
                    raise YieldSpreadError(f"FRED request failed (HTTP {response.status_code})")
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise YieldSpreadError("the FRED response is unexpectedly large")
                    chunks.append(chunk)
    except YieldSpreadError:
        raise
    except httpx.TimeoutException as exc:
        raise YieldSpreadError(f"FRED request timed out ({type(exc).__name__}, {timeout:g}s)") from None
    except httpx.HTTPError as exc:
        raise YieldSpreadError(f"FRED request failed ({_describe_http_error(exc)})") from None
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise YieldSpreadError("the FRED response is not UTF-8 text") from None
    spread = parse_fred_csv(text, last_session=last_session, observations=observations)
    return replace(spread, retrieved_at=retrieved_at)
