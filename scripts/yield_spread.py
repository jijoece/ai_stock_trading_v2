"""10Y-2Y Treasury spread from FRED (series T10Y2Y), for `score.py --yield-spread fred`.

Standard library only. The public CSV download needs no API key and no account:

    https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y&cosd=<start>&coed=<end>

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
* TLS verification is always on. certifi's CA bundle is used when installed, because some Python
  builds ship no root certificates for urllib (every HTTPS request would otherwise fail).
"""
from __future__ import annotations

import csv
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

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


def _ssl_context() -> ssl.SSLContext:
    """A verifying TLS context that trusts certifi's CA bundle when it is installed.

    Some Python builds (python.org's macOS installer, for one) ship no root certificates for `urllib`, so
    every HTTPS request fails verification. certifi (already a dependency of httpx) fixes that. Certificate and
    hostname verification are always on; without certifi this is just the default context.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def _describe_url_error(exc: urllib.error.URLError) -> str:
    """Why a request failed, as type names only: provider or host text is never echoed."""
    reason = exc.reason
    detail = type(reason).__name__ if isinstance(reason, BaseException) else "URLError"
    hint = ""
    if isinstance(reason, ssl.SSLError):
        hint = (": the TLS certificate could not be verified; this Python may have no root certificates "
                "(install certifi, or run the Python 'Install Certificates.command')")
    return f"{detail}{hint}"


def fetch_fred_yield_spread(last_session: date, *, observations: int = DEFAULT_OBSERVATIONS,
                            urlopen: Optional[Callable[..., Any]] = None, timeout: float = 15.0) -> YieldSpread:
    """Download and validate T10Y2Y up to `last_session` (a single public GET, no credentials)."""
    opener = urlopen or urllib.request.urlopen
    retrieved_at = datetime.now(timezone.utc)
    start = last_session - timedelta(days=int(observations * 1.8) + 10)
    query = urllib.parse.urlencode({"id": SERIES_ID, "cosd": start.isoformat(), "coed": last_session.isoformat()})
    request = urllib.request.Request(f"{FRED_CSV_URL}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout, context=_ssl_context()) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise YieldSpreadError(f"FRED request failed (HTTP {exc.code})") from None
    except urllib.error.URLError as exc:
        raise YieldSpreadError(f"FRED request failed ({_describe_url_error(exc)})") from None
    except (TimeoutError, OSError) as exc:
        raise YieldSpreadError(f"FRED request failed ({type(exc).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise YieldSpreadError("the FRED response is unexpectedly large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise YieldSpreadError("the FRED response is not UTF-8 text") from None
    spread = parse_fred_csv(text, last_session=last_session, observations=observations)
    return replace(spread, retrieved_at=retrieved_at)
