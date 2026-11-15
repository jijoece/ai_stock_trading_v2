"""Intraday earnings-gap research strategy: configuration and candidate filter.

Research / backtest only (plan sections 4-5). Nothing here touches a broker, a
model, the paper bridge, the shortlist or the scheduler; the strategy id is
registered separately from the paper-capable `KNOWN_STRATEGY_IDS`.

`select_candidates` decides eligibility PER VARIANT using only bars with
`available_at <= as_of`, where `as_of` is that variant's own entry decision time
(plan 5.2):

* `orb`, `pmh_break`, `vwap_pullback`: 09:25 ET on the reaction day;
* `premarket_entry`: each bar end from 08:00 to 09:25, signals recomputed as of
  that time; the first time all checks pass wins;
* `post_report_overnight`: `post_signal_time` (default 16:30) on the report date,
  from `post_report_move` only. It never reads a bar from the reaction day.

Release time (plan D10): a missing `release_time` uses the conservative bound
(`am` 09:30 on the report date, `pm` 20:00 on the report date). An event whose
bound is later than the variant's decision time is excluded with reason
`release_time_unverified` and counted by the report; a SUPPLIED release later
than the decision time is excluded as `release_after_decision_time`.

Additional reason code beyond the plan list: `variant_not_applicable`
(`post_report_overnight` needs a `pm` report).

Conservative readings (recorded in the runbook): history must be the 20 most
recent XNYS sessions up to the last session before the reaction, every one with
regular bars; the pre-market history must have volume in all 20 sessions;
`premarket_hold` is strict (> VWAP).

Relative imports only; must not import research/evidence modules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ..backtesting.models import IntradayBar
from ..evaluation.market_calendar import (
    DEFAULT_EARLY_CLOSE_POSTMARKET_END,
    MARKET_TIMEZONE_NAME,
    MarketCalendarError,
    is_trading_day,
    regular_session_close,
)
from ..hashing import hash_config
from .earnings_surprise import (
    EarningsSurprise,
    EarningsSurpriseError,
    ReactionWindow,
    reaction_window,
)
from .extended_hours_signals import (
    INSUFFICIENT_PREMARKET_HISTORY,
    SignalValue,
    continuation,
    post_hold,
    post_report_move,
    premarket_gap,
    premarket_hold,
    premarket_rel_volume,
)

STRATEGY_ID = "earnings_gap_intraday"

Variant = Literal["orb", "pmh_break", "vwap_pullback", "premarket_entry", "post_report_overnight"]
VARIANTS: tuple[str, ...] = (
    "orb",
    "pmh_break",
    "vwap_pullback",
    "premarket_entry",
    "post_report_overnight",
)
_NINE_TWENTY_FIVE_VARIANTS = ("orb", "pmh_break", "vwap_pullback")
OPENING_RANGE_MINUTES = (5, 15, 30)

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"

# Reason codes.
NO_BEAT = "no_beat"
GAP_BELOW_MINIMUM = "gap_below_minimum"
GAP_NOT_HOLDING = "gap_not_holding"
LOW_PREMARKET_INTEREST = "low_premarket_interest"
INSUFFICIENT_PREMARKET_HISTORY_REASON = INSUFFICIENT_PREMARKET_HISTORY
PRICE_BELOW_MINIMUM = "price_below_minimum"
ILLIQUID = "illiquid"
INSUFFICIENT_HISTORY = "insufficient_history"
TREND_FILTER = "trend_filter"
RELEASE_TIME_UNVERIFIED = "release_time_unverified"
RELEASE_AFTER_DECISION_TIME = "release_after_decision_time"
VARIANT_NOT_APPLICABLE = "variant_not_applicable"

_NY = ZoneInfo(MARKET_TIMEZONE_NAME)


class EarningsGapIntradayConfigError(ValueError):
    """An earnings_gap_intraday setting is invalid."""


@dataclass(frozen=True)
class EarningsGapIntradayConfig:
    """Every threshold of plan sections 4-5. Percent fields are percentages
    (5 means 5%); `*_fraction` fields are fractions; `*_bps` are basis points."""

    minimum_price: Decimal = Decimal("3")
    minimum_avg_dollar_volume: Decimal = Decimal("5000000")
    history_sessions: int = 20
    minimum_gap_percent: Decimal = Decimal("5")
    minimum_premarket_rel_volume: Decimal = Decimal("3")
    premarket_history_sessions: int = 20
    post_signal_time: time = time(16, 30)
    premarket_decision_time: time = time(9, 25)
    premarket_entry_first_decision_time: time = time(8, 0)
    opening_range_minutes: int = 15
    earliest_entry_time: time = time(9, 45)
    pmh_break_earliest_entry_time: time = time(9, 35)
    latest_entry_time: time = time(14, 30)
    flat_time: time = time(15, 55)
    overnight_flat_time: time = time(10, 30)
    post_market_exit: bool = False
    maximum_risk_percent: Decimal = Decimal("8")
    maximum_trades_per_day: int = 3
    maximum_losses_per_day: int = 2
    minimum_extended_fill_usd: Decimal = Decimal("1000")
    extended_participation: Decimal = Decimal("0.02")
    regular_participation: Decimal = Decimal("0.01")
    risk_fraction: Decimal = Decimal("0.005")
    maximum_position_fraction: Decimal = Decimal("0.10")
    slippage_bps: Decimal = Decimal("10")
    spread_floor_bps: Decimal = Decimal("5")
    spread_k: Decimal = Decimal("0.1")
    extended_spread_floor_bps: Decimal = Decimal("30")
    extended_spread_k: Decimal = Decimal("0.25")
    vwap_stop_percent: Decimal = Decimal("1")
    # UNCONFIRMED against vendor/broker - operator must confirm before merge.
    early_close_postmarket_end: time = DEFAULT_EARLY_CLOSE_POSTMARKET_END
    volume_tolerance: Decimal = Decimal("0.20")
    in_sample_fraction: Decimal = Decimal("0.6")
    trend_filter_enabled: bool = False
    trend_benchmark: str = "SPY"
    trend_sma_sessions: int = 50

    def __post_init__(self) -> None:
        positive = (
            "minimum_price",
            "minimum_avg_dollar_volume",
            "minimum_gap_percent",
            "minimum_premarket_rel_volume",
            "maximum_risk_percent",
            "extended_participation",
            "regular_participation",
            "risk_fraction",
            "maximum_position_fraction",
            "vwap_stop_percent",
            "minimum_extended_fill_usd",
        )
        for name in positive:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise EarningsGapIntradayConfigError(f"earnings_gap_intraday.{name} must be a positive Decimal")
        non_negative = (
            "slippage_bps",
            "spread_floor_bps",
            "spread_k",
            "extended_spread_floor_bps",
            "extended_spread_k",
            "volume_tolerance",
        )
        for name in non_negative:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise EarningsGapIntradayConfigError(f"earnings_gap_intraday.{name} must be a non-negative Decimal")
        for name in ("extended_participation", "regular_participation", "risk_fraction", "maximum_position_fraction"):
            if getattr(self, name) > 1:
                raise EarningsGapIntradayConfigError(f"earnings_gap_intraday.{name} must be <= 1")
        if self.maximum_risk_percent >= 100:
            raise EarningsGapIntradayConfigError("earnings_gap_intraday.maximum_risk_percent must be < 100")
        if self.vwap_stop_percent >= 100:
            raise EarningsGapIntradayConfigError("earnings_gap_intraday.vwap_stop_percent must be < 100")
        if not (Decimal(0) < self.in_sample_fraction < Decimal(1)):
            raise EarningsGapIntradayConfigError("earnings_gap_intraday.in_sample_fraction must be within (0, 1)")
        for name in (
            "history_sessions",
            "premarket_history_sessions",
            "maximum_trades_per_day",
            "maximum_losses_per_day",
            "trend_sma_sessions",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise EarningsGapIntradayConfigError(f"earnings_gap_intraday.{name} must be a positive integer")
        if self.opening_range_minutes not in OPENING_RANGE_MINUTES:
            raise EarningsGapIntradayConfigError(
                f"earnings_gap_intraday.opening_range_minutes must be one of {OPENING_RANGE_MINUTES}"
            )
        for name in (
            "post_signal_time",
            "premarket_decision_time",
            "premarket_entry_first_decision_time",
            "earliest_entry_time",
            "pmh_break_earliest_entry_time",
            "latest_entry_time",
            "flat_time",
            "overnight_flat_time",
            "early_close_postmarket_end",
        ):
            value = getattr(self, name)
            if not isinstance(value, time) or value.tzinfo is not None:
                raise EarningsGapIntradayConfigError(f"earnings_gap_intraday.{name} must be a naive ET wall-clock time")
        if self.premarket_entry_first_decision_time > self.premarket_decision_time:
            raise EarningsGapIntradayConfigError(
                "earnings_gap_intraday.premarket_entry_first_decision_time must not be after premarket_decision_time"
            )
        if not (time(4, 0) < self.premarket_decision_time <= time(9, 30)):
            raise EarningsGapIntradayConfigError("earnings_gap_intraday.premarket_decision_time must be pre-market")
        if not (time(16, 0) < self.post_signal_time <= time(20, 0)):
            raise EarningsGapIntradayConfigError("earnings_gap_intraday.post_signal_time must be post-market")
        if not isinstance(self.trend_benchmark, str) or not self.trend_benchmark.strip():
            raise EarningsGapIntradayConfigError("earnings_gap_intraday.trend_benchmark must be a symbol")

    def audit_record(self) -> dict[str, Any]:
        """Every setting, canonical, for hashing and the run identity."""
        record: dict[str, Any] = {}
        for name in sorted(self.__dataclass_fields__):
            value = getattr(self, name)
            if isinstance(value, time):
                value = value.isoformat()
            record[name] = value
        return record

    @cached_property
    def configuration_hash(self) -> str:
        return hash_config(self.audit_record())


@dataclass(frozen=True)
class CandidateDecision:
    """One event's eligibility for one variant.

    `decision_as_of` (aware ET) is the time the decision was made: signals were
    computed from bars available at or before it. `signals` holds the values the
    decision used; `report_signals` holds later-time values recorded for the
    report only (never filtered on). `release_time_unverified` is True when the
    event was excluded with reason `release_time_unverified` (D10 bound)."""

    symbol: str
    fiscal_period: str
    report_date: date
    reaction_date: date
    variant: str
    status: str
    reasons: tuple[str, ...]
    decision_as_of: datetime | None
    release_time_unverified: bool
    signals: Mapping[str, Any] = field(default_factory=dict)
    report_signals: Mapping[str, Any] = field(default_factory=dict)
    prior_close: Decimal | None = None
    avg_dollar_volume: Decimal | None = None
    trend_filter_passed: bool | None = None
    # D10 release bound (aware ET) and report timing, so the simulator never
    # reads a bar that started before the report could be known.
    assumed_release: datetime | None = None
    timing: str = ""

    @property
    def eligible(self) -> bool:
        return self.status == ELIGIBLE


BarSource = Any  # Mapping[(symbol, date) -> Sequence[IntradayBar]] or object with get_bars(symbol, date)


def _bars_for(source: BarSource, symbol: str, day: date) -> tuple[IntradayBar, ...]:
    if hasattr(source, "get_bars"):
        return tuple(source.get_bars(symbol, day))
    return tuple(source.get((symbol, day), ()))


def _previous_sessions(before: date, count: int) -> list[date] | None:
    """The `count` XNYS sessions strictly before `before`, oldest first, or None
    if the calendar cannot supply them."""
    sessions: list[date] = []
    day = before
    for _ in range(count * 3 + 30):
        day -= timedelta(days=1)
        try:
            if is_trading_day(day):
                sessions.append(day)
        except MarketCalendarError:
            return None
        if len(sessions) == count:
            return sessions[::-1]
    return None


def _regular_bars(bars: Sequence[IntradayBar]) -> list[IntradayBar]:
    return sorted((b for b in bars if b.session == "regular"), key=lambda b: b.start)


def _session_close(bars: Sequence[IntradayBar], day: date) -> Decimal | None:
    """Regular close from the session's last regular bar, only if it is the bar
    that ends at the scheduled close."""
    regular = _regular_bars(bars)
    if not regular:
        return None
    try:
        scheduled = regular_session_close(day)
    except MarketCalendarError:
        return None
    last = regular[-1]
    if last.end != scheduled:
        return None
    return last.close


def _dollar_volume(bars: Sequence[IntradayBar]) -> Decimal:
    return sum((b.close * b.volume for b in _regular_bars(bars) if b.volume > 0), Decimal(0))


def _trend_value(
    source: BarSource, config: EarningsGapIntradayConfig, ref_session: date
) -> bool | None:
    """True when the benchmark's last close is above its N-session average,
    None when the data is missing. Recorded always; filters only when enabled."""
    sessions = _previous_sessions(ref_session + timedelta(days=1), config.trend_sma_sessions)
    if sessions is None or sessions[-1] != ref_session:
        return None
    closes: list[Decimal] = []
    for day in sessions:
        close = _session_close(_bars_for(source, config.trend_benchmark, day), day)
        if close is None:
            return None
        closes.append(close)
    return closes[-1] > sum(closes, Decimal(0)) / Decimal(len(closes))


def _et(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=_NY)


def _premarket_decision_times(day: date, config: EarningsGapIntradayConfig) -> list[datetime]:
    times: list[datetime] = []
    cur = _et(day, config.premarket_entry_first_decision_time)
    last = _et(day, config.premarket_decision_time)
    while cur <= last:
        times.append(cur)
        cur += timedelta(minutes=5)
    return times


def _release_exclusion(
    window: ReactionWindow, decision_times: Sequence[datetime], supplied: bool
) -> str | None:
    """Reason code if the report cannot be known by ANY of the decision times."""
    if window.assumed_release <= max(decision_times):
        return None
    return RELEASE_AFTER_DECISION_TIME if supplied else RELEASE_TIME_UNVERIFIED


def _common_checks(
    surprise: EarningsSurprise,
    source: BarSource,
    config: EarningsGapIntradayConfig,
    ref_session: date,
) -> tuple[list[str], Decimal | None, Decimal | None, bool | None]:
    reasons: list[str] = []
    if not surprise.beat:
        reasons.append(NO_BEAT)
    sessions = _previous_sessions(ref_session + timedelta(days=1), config.history_sessions)
    prior_close: Decimal | None = None
    avg_dv: Decimal | None = None
    if sessions is None or sessions[-1] != ref_session:
        reasons.append(INSUFFICIENT_HISTORY)
    else:
        per_session: dict[date, tuple[IntradayBar, ...]] = {
            d: _bars_for(source, surprise.symbol, d) for d in sessions
        }
        closes = {d: _session_close(per_session[d], d) for d in sessions}
        if any(c is None for c in closes.values()):
            reasons.append(INSUFFICIENT_HISTORY)
        else:
            prior_close = closes[ref_session]
            avg_dv = sum((_dollar_volume(per_session[d]) for d in sessions), Decimal(0)) / Decimal(len(sessions))
            if prior_close is not None and prior_close < config.minimum_price:
                reasons.append(PRICE_BELOW_MINIMUM)
            if avg_dv < config.minimum_avg_dollar_volume:
                reasons.append(ILLIQUID)
    trend = _trend_value(source, config, ref_session)
    if config.trend_filter_enabled and trend is not True:
        reasons.append(TREND_FILTER)
    return reasons, prior_close, avg_dv, trend


def _premarket_checks(
    source: BarSource,
    surprise: EarningsSurprise,
    config: EarningsGapIntradayConfig,
    window: ReactionWindow,
    prior_close: Decimal | None,
    as_of: datetime,
) -> tuple[list[str], dict[str, Any]]:
    """The three pre-market checks as of `as_of`, reading only bars available
    by then."""
    day = window.reaction_date
    bars = _bars_for(source, surprise.symbol, day)
    not_before = window.pre_market.start if window.pre_market is not None else window.assumed_release
    reasons: list[str] = []
    signals: dict[str, Any] = {}
    gap = (
        premarket_gap(bars, day, prior_close, as_of, not_before=not_before)
        if prior_close is not None
        else SignalValue(None, None, "no_prior_close")
    )
    hold = premarket_hold(bars, day, as_of, not_before=not_before)
    history_days = _previous_sessions(day, config.premarket_history_sessions)
    if history_days is None:
        rel = SignalValue(None, None, INSUFFICIENT_PREMARKET_HISTORY)
    else:
        history = [(d, _bars_for(source, surprise.symbol, d)) for d in history_days]
        rel = premarket_rel_volume(
            bars,
            day,
            history,
            as_of,
            not_before=not_before,
            minimum_history_sessions=config.premarket_history_sessions,
        )
    signals["premarket_gap_percent"] = gap.value
    signals["premarket_hold"] = hold.value
    signals["premarket_rel_volume"] = rel.value
    signals["signals_available_at"] = max(
        (s.available_at for s in (gap, hold, rel) if s.available_at is not None), default=None
    )
    if gap.value is None and hold.value is None:
        # No usable (post-release, traded) pre-market bar at all.
        reasons.append(LOW_PREMARKET_INTEREST)
    else:
        if not isinstance(gap.value, Decimal) or gap.value < config.minimum_gap_percent:
            reasons.append(GAP_BELOW_MINIMUM)
        if hold.value is not True:
            reasons.append(GAP_NOT_HOLDING)
    if rel.note == INSUFFICIENT_PREMARKET_HISTORY:
        reasons.append(INSUFFICIENT_PREMARKET_HISTORY)
    elif not isinstance(rel.value, Decimal) or rel.value < config.minimum_premarket_rel_volume:
        if LOW_PREMARKET_INTEREST not in reasons:
            reasons.append(LOW_PREMARKET_INTEREST)
    return reasons, signals


def evaluate_candidate(
    surprise: EarningsSurprise,
    bars: BarSource,
    config: EarningsGapIntradayConfig,
    variant: str,
    *,
    as_of: datetime | None = None,
) -> CandidateDecision:
    """Eligibility of one event for one variant as of one decision time.

    `as_of` (aware) defaults to the variant's standard decision time; for
    `premarket_entry` pass a specific bar end to evaluate that single time (use
    `select_candidates` to scan 08:00-09:25). Only bars with
    `available_at <= as_of` are read for the variant's signals."""
    if variant not in VARIANTS:
        raise EarningsGapIntradayConfigError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
    if as_of is not None and as_of.tzinfo is None:
        raise EarningsGapIntradayConfigError("as_of must be timezone-aware")
    try:
        window = reaction_window(
            surprise.report_date,
            surprise.timing,
            surprise.release_time,
            early_close_postmarket_end=config.early_close_postmarket_end,
        )
    except EarningsSurpriseError:
        return CandidateDecision(
            surprise.symbol, surprise.fiscal_period, surprise.report_date, surprise.report_date, variant,
            NOT_ELIGIBLE, (INSUFFICIENT_HISTORY,), None, False,
        )
    supplied = window.release_time_supplied
    unverified = window.release_time_unverified
    day = window.reaction_date

    def decision(
        status: str, reasons: Sequence[str], when: datetime | None, signals: Mapping[str, Any] | None = None,
        report_signals: Mapping[str, Any] | None = None, prior_close: Decimal | None = None,
        avg_dv: Decimal | None = None, trend: bool | None = None,
    ) -> CandidateDecision:
        return CandidateDecision(
            symbol=surprise.symbol, fiscal_period=surprise.fiscal_period, report_date=surprise.report_date,
            reaction_date=day, variant=variant, status=status, reasons=tuple(reasons), decision_as_of=when,
            release_time_unverified=unverified and RELEASE_TIME_UNVERIFIED in reasons,
            signals=dict(signals or {}), report_signals=dict(report_signals or {}), prior_close=prior_close,
            avg_dollar_volume=avg_dv, trend_filter_passed=trend,
            assumed_release=window.assumed_release, timing=surprise.timing,
        )

    # Decision time(s) for the variant.
    if variant == "post_report_overnight":
        decision_time = as_of or _et(surprise.report_date, config.post_signal_time)
        times = [decision_time]
    elif variant == "premarket_entry":
        times = [as_of] if as_of is not None else _premarket_decision_times(day, config)
    else:
        times = [as_of or _et(day, config.premarket_decision_time)]
    when = times[-1]

    if variant == "post_report_overnight" and surprise.timing != "pm":
        return decision(NOT_ELIGIBLE, [VARIANT_NOT_APPLICABLE], when)
    excluded = _release_exclusion(window, times, supplied)
    if excluded is not None:
        return decision(NOT_ELIGIBLE, [excluded], when)

    ref_session_list = _previous_sessions(day, 1)
    if ref_session_list is None:
        return decision(NOT_ELIGIBLE, [INSUFFICIENT_HISTORY], when)
    ref_session = ref_session_list[0]
    common, prior_close, avg_dv, trend = _common_checks(surprise, bars, config, ref_session)

    if variant == "post_report_overnight":
        assert window.post_market is not None  # pm, release supplied and before the decision
        reasons = list(common)
        report_day = surprise.report_date
        post_bars = _bars_for(bars, surprise.symbol, report_day)
        move = (
            post_report_move(post_bars, report_day, prior_close, when, not_before=window.post_market.start)
            if prior_close is not None
            else SignalValue(None, None, "no_prior_close")
        )
        signals = {"post_report_move_percent": move.value, "signals_available_at": move.available_at}
        if not isinstance(move.value, Decimal) or move.value < config.minimum_gap_percent:
            reasons.append(GAP_BELOW_MINIMUM)
        status = NOT_ELIGIBLE if reasons else ELIGIBLE
        # Structurally never reads a reaction-day bar: later-time values for the
        # report come from `report_only_signals`, called after the decision.
        return decision(status, reasons, when, signals, {}, prior_close, avg_dv, trend)

    if variant in _NINE_TWENTY_FIVE_VARIANTS:
        reasons = list(common)
        pre_reasons, signals = _premarket_checks(bars, surprise, config, window, prior_close, when)
        reasons.extend(r for r in pre_reasons if r not in reasons)
        status = NOT_ELIGIBLE if reasons else ELIGIBLE
        return decision(status, reasons, when, signals, {}, prior_close, avg_dv, trend)

    # premarket_entry: scan the decision times in order; the first passing time wins.
    last_reasons: list[str] = []
    last_signals: dict[str, Any] = {}
    for moment in times:
        if window.assumed_release > moment:
            last_reasons = [RELEASE_AFTER_DECISION_TIME if supplied else RELEASE_TIME_UNVERIFIED]
            last_signals = {}
            when = moment
            continue
        pre_reasons, signals = _premarket_checks(bars, surprise, config, window, prior_close, moment)
        reasons = list(common) + [r for r in pre_reasons if r not in common]
        last_reasons, last_signals, when = reasons, signals, moment
        if not reasons:
            return decision(ELIGIBLE, [], moment, signals, {}, prior_close, avg_dv, trend)
    return decision(NOT_ELIGIBLE, last_reasons, when, last_signals, {}, prior_close, avg_dv, trend)


def report_only_signals(
    surprise: EarningsSurprise,
    bars: BarSource,
    config: EarningsGapIntradayConfig,
) -> dict[str, Any]:
    """Later-time values for the report breakdown ONLY (`continuation` sign,
    `post_hold`, the 09:25 gap) for a `pm` event. Never used for eligibility:
    `evaluate_candidate` does not call this and a variant must not filter on it.
    Empty when the event is not `pm`, the window is unusable, or the release time
    is unverified."""
    out: dict[str, Any] = {}
    try:
        window = reaction_window(
            surprise.report_date, surprise.timing, surprise.release_time,
            early_close_postmarket_end=config.early_close_postmarket_end,
        )
    except EarningsSurpriseError:
        return out
    ref = _previous_sessions(window.reaction_date, 1)
    if surprise.timing != "pm" or window.post_market is None or ref is None:
        return out
    report_day = surprise.report_date
    post_bars = _bars_for(bars, surprise.symbol, report_day)
    prior_close = _session_close(post_bars, report_day)
    if prior_close is None:
        return out
    post = window.post_market
    end_of_post = post.end
    move_late = post_report_move(post_bars, report_day, prior_close, end_of_post, not_before=post.start)
    hold = post_hold(post_bars, report_day, end_of_post, not_before=post.start)
    move_at_signal = post_report_move(
        post_bars, report_day, prior_close, _et(report_day, config.post_signal_time), not_before=post.start
    )
    react_bars = _bars_for(bars, surprise.symbol, window.reaction_date)
    pre_end = _et(window.reaction_date, config.premarket_decision_time)
    pre_not_before = window.pre_market.start if window.pre_market is not None else window.assumed_release
    gap = premarket_gap(react_bars, window.reaction_date, prior_close, pre_end, not_before=pre_not_before)
    out["post_hold"] = hold.value
    out["post_report_move_at_post_close_percent"] = move_late.value
    out["premarket_gap_percent"] = gap.value
    out["continuation"] = continuation(gap, move_at_signal).value
    return out


def select_candidates(
    surprises: Sequence[EarningsSurprise],
    bars: BarSource,
    config: EarningsGapIntradayConfig,
    variant: str,
) -> tuple[CandidateDecision, ...]:
    """Decide eligibility for every surprise under `variant`, in a deterministic
    order (report_date, symbol, fiscal_period). `bars` maps `(symbol, session
    date)` to that session's `IntradayBar`s, or is an object with
    `get_bars(symbol, date)`. Excluded events are returned (never dropped), so
    the report can count `release_time_unverified` and every other reason."""
    ordered = sorted(surprises, key=lambda s: (s.report_date, s.symbol, s.fiscal_period))
    return tuple(evaluate_candidate(s, bars, config, variant) for s in ordered)


def count_release_time_unverified(decisions: Sequence[CandidateDecision]) -> int:
    """Number of events excluded because their release time was unverified (D10)."""
    return sum(1 for d in decisions if RELEASE_TIME_UNVERIFIED in d.reasons)


def count_reasons(decisions: Sequence[CandidateDecision]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in decisions:
        for reason in d.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))
