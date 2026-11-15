"""Earnings-surprise records, strict CSV loader and report reaction windows.

Operator-supplied CSV snapshot (plan D2). The estimate is the last pre-report
consensus as captured, not a point-in-time series. Loading is strict: every
malformed row is an error, never silently skipped.

Release time (plan D10): the first usable signal bar must start at or after the
release. A missing `release_time` uses a conservative bound, NOT an optimistic
default: `am` -> 09:30 ET regular open on the report date; `pm` -> 20:00 ET on
the report date (end of normal post-market). Segments of the reaction window
are clipped so they never start before the assumed release.

Relative imports only; no research/evidence_providers imports.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from ..evaluation.market_calendar import (
    DEFAULT_EARLY_CLOSE_POSTMARKET_END,
    MARKET_TIMEZONE_NAME,
    MarketCalendarError,
    next_trading_session,
    postmarket_close,
    premarket_open,
    regular_session_close,
    regular_session_open,
)

CSV_COLUMNS: tuple[str, ...] = (
    "symbol",
    "fiscal_period",
    "report_date",
    "timing",
    "release_time",
    "eps_estimate",
    "eps_actual",
    "source",
    "captured_at",
)
_REQUIRED_NONBLANK = tuple(c for c in CSV_COLUMNS if c != "release_time")

# D10 conservative bounds when release_time is absent (ET wall clock, report_date).
DEFAULT_AM_ASSUMED_RELEASE = time(9, 30)
DEFAULT_PM_ASSUMED_RELEASE = time(20, 0)

Timing = Literal["am", "pm"]
_NY = ZoneInfo(MARKET_TIMEZONE_NAME)


class EarningsSurpriseError(ValueError):
    """Raised for any malformed earnings-surprise input."""


@dataclass(frozen=True)
class EarningsSurprise:
    symbol: str
    fiscal_period: str
    report_date: date
    timing: Timing
    release_time: time | None
    eps_estimate: Decimal
    eps_actual: Decimal
    source: str
    captured_at: datetime

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip():
            raise EarningsSurpriseError("symbol must be non-blank and trimmed")
        if self.timing not in ("am", "pm"):
            raise EarningsSurpriseError(f"unknown timing {self.timing!r}")
        if self.release_time is not None and self.release_time.tzinfo is not None:
            raise EarningsSurpriseError("release_time must be a naive ET wall-clock time")
        for name in ("eps_estimate", "eps_actual"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise EarningsSurpriseError(f"{name} must be a finite Decimal")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise EarningsSurpriseError("captured_at must be timezone-aware")

    @property
    def surprise(self) -> Decimal:
        return self.eps_actual - self.eps_estimate

    @property
    def beat(self) -> bool:
        return self.eps_actual > self.eps_estimate


@dataclass(frozen=True)
class ReactionSegment:
    session: Literal["post", "pre", "regular"]
    session_date: date
    start: datetime  # aware ET, never earlier than the assumed release
    end: datetime  # aware ET


@dataclass(frozen=True)
class ReactionWindow:
    report_date: date
    timing: Timing
    assumed_release: datetime  # aware ET
    release_time_supplied: bool
    reaction_date: date  # first regular session in which the report is the news
    post_market: ReactionSegment | None  # pm only; None if empty after clipping
    pre_market: ReactionSegment | None
    regular: ReactionSegment

    @property
    def release_time_unverified(self) -> bool:
        """True when the release is a conservative D10 bound, not a supplied time."""
        return not self.release_time_supplied

    @property
    def segments(self) -> tuple[ReactionSegment, ...]:
        return tuple(s for s in (self.post_market, self.pre_market, self.regular) if s)


def _parse_decimal(raw: str, column: str, line: int) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise EarningsSurpriseError(f"line {line}: invalid decimal in {column}: {raw!r}") from exc
    if not value.is_finite():
        raise EarningsSurpriseError(f"line {line}: non-finite decimal in {column}: {raw!r}")
    return value


def load_earnings_csv(path: str | Path) -> tuple[EarningsSurprise, ...]:
    """Load the operator CSV strictly; returns rows sorted by
    (report_date, symbol, fiscal_period)."""
    try:
        handle = open(path, newline="", encoding="utf-8")
    except OSError as exc:
        raise EarningsSurpriseError(f"cannot read earnings CSV {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        missing = [c for c in CSV_COLUMNS if c not in header]
        extra = [c for c in header if c not in CSV_COLUMNS]
        if missing:
            raise EarningsSurpriseError(f"missing CSV columns: {missing}")
        if extra:
            raise EarningsSurpriseError(f"unexpected CSV columns: {extra}")
        rows: list[EarningsSurprise] = []
        seen: set[tuple[str, str]] = set()
        for row in reader:
            line = reader.line_num
            if None in row or any(v is None for v in row.values()):
                raise EarningsSurpriseError(f"line {line}: wrong number of fields")
            for col in _REQUIRED_NONBLANK:
                if not row[col].strip():
                    raise EarningsSurpriseError(f"line {line}: blank {col}")
            symbol = row["symbol"].strip().upper()
            period = row["fiscal_period"].strip()
            timing = row["timing"].strip().lower()
            if timing not in ("am", "pm"):
                raise EarningsSurpriseError(f"line {line}: unknown timing {row['timing']!r}")
            try:
                report_date = date.fromisoformat(row["report_date"].strip())
            except ValueError as exc:
                raise EarningsSurpriseError(
                    f"line {line}: malformed report_date {row['report_date']!r}"
                ) from exc
            release_raw = row["release_time"].strip()
            release_time: time | None = None
            if release_raw:
                try:
                    release_time = time.fromisoformat(release_raw)
                except ValueError as exc:
                    raise EarningsSurpriseError(
                        f"line {line}: malformed release_time {release_raw!r}"
                    ) from exc
                if release_time.tzinfo is not None:
                    raise EarningsSurpriseError(
                        f"line {line}: release_time must be a naive ET wall-clock time"
                    )
            try:
                captured_at = datetime.fromisoformat(row["captured_at"].strip())
            except ValueError as exc:
                raise EarningsSurpriseError(
                    f"line {line}: malformed captured_at {row['captured_at']!r}"
                ) from exc
            if captured_at.tzinfo is None or captured_at.utcoffset() is None:
                raise EarningsSurpriseError(f"line {line}: captured_at must be timezone-aware")
            key = (symbol, period)
            if key in seen:
                raise EarningsSurpriseError(
                    f"line {line}: duplicate (symbol, fiscal_period) {key}"
                )
            seen.add(key)
            rows.append(
                EarningsSurprise(
                    symbol=symbol,
                    fiscal_period=period,
                    report_date=report_date,
                    timing=timing,  # type: ignore[arg-type]
                    release_time=release_time,
                    eps_estimate=_parse_decimal(row["eps_estimate"], "eps_estimate", line),
                    eps_actual=_parse_decimal(row["eps_actual"], "eps_actual", line),
                    source=row["source"].strip(),
                    captured_at=captured_at.astimezone(timezone.utc),
                )
            )
    rows.sort(key=lambda r: (r.report_date, r.symbol, r.fiscal_period))
    return tuple(rows)


def _clip(
    session: Literal["post", "pre", "regular"],
    day: date,
    start: datetime,
    end: datetime,
    release: datetime,
) -> ReactionSegment | None:
    start = max(start, release)
    if start >= end:
        return None
    return ReactionSegment(session=session, session_date=day, start=start, end=end)


def reaction_window(
    report_date: date,
    timing: str,
    release_time: time | None = None,
    *,
    early_close_postmarket_end: time = DEFAULT_EARLY_CLOSE_POSTMARKET_END,
) -> ReactionWindow:
    """Segments in which the report is knowable, clipped to the assumed release.

    `am`: report_date pre-market and regular. `pm`: report_date post-market,
    then the next trading session's pre-market and regular (the next session
    skips weekends and holidays). Raises EarningsSurpriseError when
    `report_date` is not a trading session or the timing is unknown.
    """
    if timing not in ("am", "pm"):
        raise EarningsSurpriseError(f"unknown timing {timing!r}")
    if release_time is not None and release_time.tzinfo is not None:
        raise EarningsSurpriseError("release_time must be a naive ET wall-clock time")
    supplied = release_time is not None
    try:
        # Raises for weekends/holidays.
        regular_session_open(report_date)
        if release_time is None:
            release_time = (
                DEFAULT_AM_ASSUMED_RELEASE if timing == "am" else DEFAULT_PM_ASSUMED_RELEASE
            )
        release = datetime.combine(report_date, release_time, tzinfo=_NY)
        if timing == "am":
            react = report_date
            post = None
        else:
            react = next_trading_session(report_date)
            post = _clip(
                "post",
                report_date,
                regular_session_close(report_date),
                postmarket_close(report_date, early_close_postmarket_end),
                release,
            )
        pre = _clip("pre", react, premarket_open(react), regular_session_open(react), release)
        regular = _clip(
            "regular", react, regular_session_open(react), regular_session_close(react), release
        )
    except MarketCalendarError as exc:
        raise EarningsSurpriseError(
            f"cannot build reaction window for {report_date.isoformat()} {timing}: {exc}"
        ) from exc
    if regular is None:
        raise EarningsSurpriseError(
            f"release_time {release_time} leaves no regular session for {report_date.isoformat()}"
        )
    return ReactionWindow(
        report_date=report_date,
        timing=timing,  # type: ignore[arg-type]
        assumed_release=release,
        release_time_supplied=supplied,
        reaction_date=react,
        post_market=post,
        pre_market=pre,
        regular=regular,
    )
