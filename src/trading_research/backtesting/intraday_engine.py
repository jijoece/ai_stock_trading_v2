"""Intraday earnings-gap simulator (plan sections 5.3-5.6, D5, D6, D11, D12).

Research / backtest only. No broker, model, network or wall clock. Bars are
walked in time order per event window across sessions, with incrementally
maintained VWAP (09:30 or 04:00 anchor), opening range and pre-market levels.

Conservative readings chosen where the plan is ambiguous (also recorded in the
runbook and the task handoff):

* Close-derived signals (orb, pmh_break, vwap_pullback, VWAP trail) are known at
  the signal bar's `available_at`; the order is submitted then and a market
  entry fills at the NEXT bar's open plus costs, never at the signal close. The
  risk guard, nonpositive-risk check and sizing use only the signal close (the
  reference entry) and the variant's stop. The next open never cancels or
  resizes the order; a gap through the stop still fills and the stop exits at
  the worse of the stop and that open.
* Costs (D6): market fills pay `slippage_bps` + the spread proxy (full proxy per
  side, regular: max(floor, k * first bar range/close)). Extended-hours limit
  fills execute AT the limit (never better) and pay `slippage_bps` (+ the
  extended proxy on entries) as an explicit USD cost; extended stop-sell limits
  carry the extended proxy in their price.
* Simulated extended-hours stop (D11): when a bar closes below the stop, a limit
  sell is placed at THAT bar's low minus the extended proxy and works in the
  next contiguous bar only (the next bar's own low is unknowable at
  submission). It may fail to fill; a new order follows after any later bar that
  closes below the stop.
* The +1R target is entry fill price + planned risk per share. Half the shares
  (floor) are sold there and the VWAP trail then arms for the remainder (the
  trail does not apply before the partial). Stop and target in one bar: stop
  first.
* A guard on entry time applies to the order submission (the signal bar's end):
  submission must lie in [earliest_entry_time, latest_entry_time], so the fill is
  never before the earliest nor after the latest time. Signals outside that
  window are ignored; scanning continues until the latest time. One signal
  attempt per event: if it is skipped by a guard the event is done.
* Day guards count orders submitted earlier that day (filled or not) and losses
  already realized (exit at or before the submission time) that same ET day.
* Equity for sizing = initial equity + realized pnl of trades exited at or before
  the submission time.

Relative imports only; must not import research/evidence modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from ..evaluation.market_calendar import (
    MARKET_TIMEZONE_NAME,
    MarketCalendarError,
    postmarket_close,
    regular_session_close,
    regular_session_open,
)
from ..hashing import hash_config
from .intraday_history import IntradayHistoryProvider, intraday_data_hash
from .models import BacktestError, IntradayBar

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from ..strategies.earnings_gap_intraday import CandidateDecision, EarningsGapIntradayConfig

ENGINE_SCHEMA_VERSION = "intraday-engine-v1"
VWAP_ANCHORS = ("0930", "0400")
_SAME_DAY_VARIANTS = ("orb", "pmh_break", "vwap_pullback", "premarket_entry")
_VARIANTS = (*_SAME_DAY_VARIANTS, "post_report_overnight")

_NY = ZoneInfo(MARKET_TIMEZONE_NAME)
_UTC = timezone.utc
_BPS = Decimal(10000)
_THREE = Decimal(3)
_CENT = Decimal("0.01")

# Reason codes.
NONPOSITIVE_RISK = "nonpositive_risk"
RISK_PERCENT_EXCEEDED = "risk_percent_exceeds_maximum"
MAX_TRADES_PER_DAY = "max_trades_per_day"
MAX_LOSSES_PER_DAY = "max_losses_per_day"
ZERO_SHARES = "zero_shares"
BELOW_MINIMUM_EXTENDED_NOTIONAL = "below_minimum_extended_notional"
NO_SIGNAL = "no_signal"
AFTER_LATEST_ENTRY = "after_latest_entry_time"
NO_NEXT_BAR = "no_next_bar"
LIMIT_NOT_REACHED = "limit_not_reached"
INCOMPLETE_SESSION_DATA = "incomplete_session_data"
NO_PREMARKET_LEVELS = "no_premarket_levels"
NO_REFERENCE_CLOSE = "no_reference_close"
NO_BARS = "no_bars"


Stage = Literal["candidate", "submission", "unfilled"]


@dataclass(frozen=True)
class TradeLeg:
    time: datetime  # aware UTC
    session: Literal["pre", "regular", "post"]
    side: Literal["buy", "sell"]
    shares: int
    price: Decimal
    reason: str
    cost: Decimal  # explicit USD cost on top of `price` (extended-hours fills; 0 when embedded)


@dataclass(frozen=True)
class IntradayTrade:
    symbol: str
    fiscal_period: str
    report_date: date
    reaction_date: date
    variant: str
    vwap_anchor: str
    opening_range_minutes: int
    submitted_at: datetime
    entry_time: datetime
    entry_session: Literal["pre", "regular", "post"]
    entry_price: Decimal
    exit_time: datetime
    exit_session: Literal["pre", "regular", "post"]
    exit_price: Decimal  # share-weighted average over all sell legs
    exit_reason: str  # reason of the last leg
    requested_shares: int
    filled_shares: int
    reference_entry: Decimal
    stop_price: Decimal
    risk_per_share: Decimal  # planned: reference entry - stop
    pnl: Decimal  # after every cost
    r_multiple: Decimal  # pnl / (risk_per_share * filled_shares)
    mae_r: Decimal
    mfe_r: Decimal
    partial_taken: bool
    below_minimum_fill: bool  # extended-hours fill under minimum_extended_fill_usd
    gap_percent: Decimal | None
    legs: tuple[TradeLeg, ...]

    @property
    def entry_notional(self) -> Decimal:
        return self.entry_price * self.filled_shares


@dataclass(frozen=True)
class RejectedOrder:
    """A candidate that never became a position: skipped before submission
    (`stage` 'candidate' / 'submission') or submitted but never filled
    ('unfilled')."""

    symbol: str
    fiscal_period: str
    report_date: date
    variant: str
    stage: Stage
    reasons: tuple[str, ...]
    submitted_at: datetime | None = None


@dataclass(frozen=True)
class DailyEquity:
    day: date  # ET date
    equity: Decimal  # realized equity at the end of that day


@dataclass(frozen=True)
class IntradayBacktestResult:
    variant: str
    vwap_anchor: str
    initial_equity: Decimal
    trades: tuple[IntradayTrade, ...]
    daily_equity: tuple[DailyEquity, ...]
    rejected: tuple[RejectedOrder, ...]
    configuration_hash: str
    data_hash: str
    run_id: str
    candidates_count: int = 0
    release_time_unverified_count: int = 0
    below_minimum_fill_count: int = 0
    unfilled_count: int = 0

    @property
    def final_equity(self) -> Decimal:
        return self.daily_equity[-1].equity if self.daily_equity else self.initial_equity


# -- helpers -----------------------------------------------------------------


class _Vwap:
    """Cumulative typical-price VWAP over usable (volume, non-closing-print) bars."""

    def __init__(self) -> None:
        self._pv = Decimal(0)
        self._v = 0

    def add(self, bar: IntradayBar) -> None:
        if bar.volume > 0 and not bar.is_closing_print:
            self._pv += (bar.high + bar.low + bar.close) / _THREE * bar.volume
            self._v += bar.volume

    @property
    def value(self) -> Decimal | None:
        return self._pv / Decimal(self._v) if self._v > 0 else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise BacktestError("naive datetime rejected; timestamps must be timezone-aware")
    return value.astimezone(_UTC)


def _ny(value: datetime) -> datetime:
    return value.astimezone(_NY)


def _et_dt(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=_NY)


def _q_down(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_DOWN)


def _q_up(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_UP)


def _usable_pre_post(
    bars: Sequence[IntradayBar], session: str, day: date, as_of: datetime, not_before: datetime | None
) -> list[IntradayBar]:
    return [
        b
        for b in bars
        if b.session == session
        and _ny(b.start).date() == day
        and b.available_at <= as_of
        and b.volume > 0
        and not b.is_closing_print
        and (not_before is None or b.start >= not_before)
    ]


def _spread_bps(
    bars: Sequence[IntradayBar],
    session: str,
    day: date,
    as_of: datetime,
    floor: Decimal,
    k: Decimal,
    not_before: datetime | None,
) -> Decimal:
    """max(floor, k * first bar (high-low)/close) in bps, from the first usable
    bar of `session` on `day` that is already available at `as_of`."""
    first = next(
        (
            b
            for b in bars
            if b.session == session
            and _ny(b.start).date() == day
            and b.available_at <= as_of
            and (session == "regular" or (b.volume > 0 and not b.is_closing_print))
            and (not_before is None or b.start >= not_before)
        ),
        None,
    )
    if first is None:
        return floor
    return max(floor, k * (first.high - first.low) / first.close * _BPS)


@dataclass
class _Plan:
    cand: Any
    kind: Literal["regular", "extended"]
    submitted_at: datetime  # aware UTC
    reference_entry: Decimal
    stop: Decimal
    signal_volume: int
    timeline: list[IntradayBar]
    fill_index: int | None = None  # regular: index of the next bar
    limit: Decimal | None = None  # extended
    work_session: str = ""  # extended: 'pre' or 'post'
    work_start_index: int = 0
    work_until: datetime | None = None
    not_before: datetime | None = None


# -- pass 1: find the signal / order ------------------------------------------


def _timeline(
    provider: IntradayHistoryProvider, cand: Any, variant: str, cache: dict[tuple[str, date], tuple[IntradayBar, ...]]
) -> list[IntradayBar]:
    def load(day: date) -> tuple[IntradayBar, ...]:
        key = (cand.symbol, day)
        if key not in cache:
            cache[key] = tuple(sorted(provider.get_bars(cand.symbol, day), key=lambda b: b.start))
        return cache[key]

    days = [cand.report_date, cand.reaction_date] if variant == "post_report_overnight" else [cand.reaction_date]
    bars: list[IntradayBar] = []
    for day in dict.fromkeys(days):
        bars.extend(load(day))
    bars.sort(key=lambda b: b.start)
    return bars


def _regular_plan(
    cand: Any, cfg: Any, variant: str, anchor: str, timeline: list[IntradayBar]
) -> tuple[_Plan | None, str | None]:
    day = cand.reaction_date
    not_before = _utc(cand.assumed_release) if cand.assumed_release is not None else None
    decision = _utc(cand.decision_as_of)
    open_dt = _utc(regular_session_open(day))
    or_end = open_dt + timedelta(minutes=cfg.opening_range_minutes)
    min_submit = _utc(_et_dt(day, cfg.pmh_break_earliest_entry_time if variant == "pmh_break" else cfg.earliest_entry_time))
    latest = _utc(_et_dt(day, cfg.latest_entry_time))
    try:
        close = _ny(regular_session_close(day))
        flat_deadline = _utc(_et_dt(day, min(cfg.flat_time, (close - timedelta(minutes=5)).time())))
    except MarketCalendarError:
        flat_deadline = _utc(_et_dt(day, cfg.flat_time))

    pre =_usable_pre_post(timeline, "pre", day, decision, not_before)
    pmh = max((b.high for b in pre), default=None)
    pm_vwap_acc = _Vwap()
    for b in pre:
        pm_vwap_acc.add(b)
    pm_vwap = pm_vwap_acc.value
    if variant == "pmh_break" and (pmh is None or pm_vwap is None):
        return None, NO_PREMARKET_LEVELS

    vwap = _Vwap()
    if anchor == "0400":
        for b in _usable_pre_post(timeline, "pre", day, open_dt, not_before):
            vwap.add(b)

    reg_idx = [i for i, b in enumerate(timeline) if b.session == "regular" and _ny(b.start).date() == day]
    if not reg_idx:
        return None, NO_BARS
    or_high: Decimal | None = None
    or_low: Decimal | None = None
    prev_above_or = False
    late_signal = False
    for pos, i in enumerate(reg_idx):
        b = timeline[i]
        vwap.add(b)
        in_or = b.start < or_end
        if in_or:
            or_high = b.high if or_high is None else max(or_high, b.high)
            or_low = b.low if or_low is None else min(or_low, b.low)
        submit = b.available_at
        signal: Decimal | None = None  # the stop
        if variant == "orb":
            if not in_or and or_high is not None and or_low is not None and b.close > or_high:
                signal = or_low
        elif variant == "pmh_break":
            if pmh is not None and b.close > pmh and pm_vwap is not None and or_low is not None:
                signal = min(pm_vwap, or_low)
        elif variant == "vwap_pullback":
            v = vwap.value
            after_30 = b.start >= open_dt + timedelta(minutes=30)
            if after_30 and prev_above_or and v is not None and b.low <= v < b.close:
                signal = v * (Decimal(1) - cfg.vwap_stop_percent / Decimal(100))
        if variant == "vwap_pullback":
            prev_above_or = (not in_or) and or_high is not None and b.close > or_high
        if signal is None:
            continue
        if submit > latest:
            late_signal = True
            break
        if submit < min_submit:
            continue
        next_idx = reg_idx[pos + 1] if pos + 1 < len(reg_idx) else None
        if next_idx is not None and _utc(timeline[next_idx].start) > latest:
            next_idx = None  # sparse history: the fill bar must not start after the entry cutoff
        if next_idx is not None and not cfg.post_market_exit and _utc(timeline[next_idx].start) >= flat_deadline:
            next_idx = None  # no entry at/after the effective flat time (early-close days)
        plan = _Plan(
            cand=cand, kind="regular", submitted_at=_utc(submit), reference_entry=b.close, stop=signal,
            signal_volume=b.volume, timeline=timeline, fill_index=next_idx, not_before=not_before,
        )
        return plan, None
    return None, AFTER_LATEST_ENTRY if late_signal else NO_SIGNAL


def _extended_plan(
    cand: Any, cfg: Any, variant: str, timeline: list[IntradayBar]
) -> tuple[_Plan | None, str | None]:
    not_before = _utc(cand.assumed_release) if cand.assumed_release is not None else None
    t0 = _utc(cand.decision_as_of)
    if variant == "premarket_entry":
        day, session = cand.reaction_date, "pre"
        until = _utc(regular_session_open(day))
    else:
        day, session = cand.report_date, "post"
        until = _utc(postmarket_close(day, cfg.early_close_postmarket_end))
    usable = _usable_pre_post(timeline, session, day, t0, not_before)
    if not usable:
        return None, NO_PREMARKET_LEVELS
    acc = _Vwap()
    for b in usable:
        acc.add(b)
    vwap = acc.value
    if vwap is None:
        return None, NO_PREMARKET_LEVELS
    limit = _q_down(vwap)
    if variant == "premarket_entry":
        stop = min(b.low for b in usable)
    else:
        if cand.prior_close is None:
            return None, NO_REFERENCE_CLOSE
        stop = cand.prior_close
    start_idx = next((i for i, b in enumerate(timeline) if b.start >= t0), len(timeline))
    plan = _Plan(
        cand=cand, kind="extended", submitted_at=t0, reference_entry=limit, stop=stop,
        signal_volume=usable[-1].volume, timeline=timeline, limit=limit, work_session=session,
        work_start_index=start_idx, work_until=until, not_before=not_before,
    )
    return plan, None


# -- pass 2: simulate one position --------------------------------------------


@dataclass
class _Outcome:
    trade: IntradayTrade | None = None
    unfilled_reason: str | None = None


def _truncated_after_entry(cand: Any) -> BacktestError:
    """The entry already filled but the bars end before the exit horizon. The position's outcome is
    unknown, so dropping it (or calling it "unfilled") would silently remove a possibly losing trade
    from every metric: the whole run is invalid instead."""
    return BacktestError(
        f"{INCOMPLETE_SESSION_DATA}: {cand.symbol} {cand.fiscal_period} entered but the bar data ends "
        "before the exit horizon; refetch complete sessions"
    )


def _simulate(
    plan: _Plan, shares: int, cfg: Any, variant: str, anchor: str
) -> _Outcome:
    cand = plan.cand
    tl = plan.timeline
    rd: date = cand.reaction_date
    not_before = plan.not_before
    slip = cfg.slippage_bps
    risk = plan.reference_entry - plan.stop
    stop = plan.stop

    def reg_bps(as_of: datetime, day: date) -> Decimal:
        return slip + _spread_bps(tl, "regular", day, as_of, cfg.spread_floor_bps, cfg.spread_k, None)

    def ext_spread(as_of: datetime, session: str, day: date) -> Decimal:
        return _spread_bps(
            tl, session, day, as_of, cfg.extended_spread_floor_bps, cfg.extended_spread_k, not_before
        )

    # ---- entry ----
    legs: list[TradeLeg] = []
    below_min = False
    if plan.kind == "regular":
        if plan.fill_index is None:
            return _Outcome(unfilled_reason=NO_NEXT_BAR)
        entry_idx = plan.fill_index
        eb = tl[entry_idx]
        bps = reg_bps(_utc(eb.start), _ny(eb.start).date()) / _BPS
        entry_price = eb.open * (Decimal(1) + bps)
        qty = shares
        legs.append(TradeLeg(_utc(eb.start), "regular", "buy", qty, entry_price, "entry", Decimal(0)))
    else:
        assert plan.limit is not None and plan.work_until is not None
        entry_idx = -1
        qty = 0
        limit = plan.limit
        for i in range(plan.work_start_index, len(tl)):
            b = tl[i]
            if b.start >= plan.work_until:
                break
            if b.session != plan.work_session:
                continue
            if b.volume <= 0 or b.is_closing_print or b.low > limit:
                continue
            cap = int((cfg.extended_participation * b.volume).to_integral_value(rounding=ROUND_DOWN))
            fill = min(shares, cap)
            if fill < 1:
                continue
            entry_idx, qty = i, fill
            break
        if entry_idx < 0:
            return _Outcome(unfilled_reason=LIMIT_NOT_REACHED)
        eb = tl[entry_idx]
        day = _ny(eb.start).date()
        bps = (slip + ext_spread(plan.submitted_at, plan.work_session, day)) / _BPS
        entry_price = limit
        legs.append(
            TradeLeg(_utc(eb.start), eb.session, "buy", qty, limit, "entry_limit", limit * qty * bps)
        )
        below_min = limit * qty < cfg.minimum_extended_fill_usd

    target = entry_price + risk
    remaining = qty
    partial_taken = False
    trail_active = False
    pending_trail = False
    pending_ext: tuple[Decimal, str, datetime] | None = None  # limit, reason, valid start
    lows = entry_price
    highs = entry_price
    vwap = _Vwap()
    overnight = variant == "post_report_overnight"
    same_day_post_exit = bool(cfg.post_market_exit) and not overnight
    flat_cache: dict[date, datetime] = {}

    def flat_dt(day: date) -> datetime:
        if day not in flat_cache:
            if overnight:
                at = cfg.overnight_flat_time
            else:
                try:
                    close = _ny(regular_session_close(day))
                    at = min(cfg.flat_time, (close - timedelta(minutes=5)).time())
                except MarketCalendarError:
                    at = cfg.flat_time
            flat_cache[day] = _utc(_et_dt(day, at))
        return flat_cache[day]

    def sell_regular(b: IntradayBar, raw: Decimal, count: int, reason: str, at: datetime) -> None:
        nonlocal remaining
        bps = reg_bps(_utc(b.start), _ny(b.start).date()) / _BPS
        legs.append(TradeLeg(at, "regular", "sell", count, raw * (Decimal(1) - bps), reason, Decimal(0)))
        remaining -= count

    def in_vwap(b: IntradayBar) -> bool:
        if _ny(b.start).date() != rd:
            return False
        if b.session == "regular":
            return True
        return anchor == "0400" and b.session == "pre" and (not_before is None or b.start >= not_before)

    done = False
    last_i = entry_idx
    last_regular_i: int | None = None
    for i, b in enumerate(tl):
        if in_vwap(b):
            vwap.add(b)
        if i < entry_idx:
            continue
        last_i = i
        regular = b.session == "regular"
        if regular:
            last_regular_i = i
        day = _ny(b.start).date()
        is_entry = i == entry_idx
        carried_ext = pending_ext  # order armed by the previous bar, before this bar consumes it
        if not is_entry:
            if (
                b.session == "post" and not overnight and not same_day_post_exit
                and last_regular_i is not None and remaining > 0
            ):
                # The flat-time bar was missing: close at the last regular bar, never in the post-market.
                lb = tl[last_regular_i]
                if lb.end < flat_dt(_ny(lb.start).date()):
                    raise _truncated_after_entry(cand)  # truncated, not a missing flat bar
                sell_regular(lb, lb.close, remaining, "flat_time_close", _utc(lb.end))
                done = True
                break
            if not regular:
                pending_trail = False
            if regular:
                pending_ext = None
            # exits executed at this bar's open
            if regular and pending_trail:
                sell_regular(b, b.open, remaining, "vwap_trail", _utc(b.start))
                lows, highs = min(lows, b.open), max(highs, b.open)
                done = True
                break
            if regular and not same_day_post_exit and b.start >= flat_dt(day):
                sell_regular(b, b.open, remaining, "flat_time", _utc(b.start))
                lows, highs = min(lows, b.open), max(highs, b.open)
                done = True
                break
            if (
                same_day_post_exit
                and b.session == "post"
                and day == rd
                and b.start >= _utc(_et_dt(day, cfg.post_signal_time))
            ):
                break  # deadline reached; finalized below
            if not regular and pending_ext is not None:
                lim, reason, valid = pending_ext
                pending_ext = None
                if b.start == valid and b.volume > 0 and not b.is_closing_print and b.high >= lim:
                    cap = int((cfg.extended_participation * b.volume).to_integral_value(rounding=ROUND_DOWN))
                    fill = min(remaining, cap)
                    if fill >= 1:
                        cost = lim * fill * slip / _BPS
                        legs.append(TradeLeg(_utc(b.start), b.session, "sell", fill, lim, reason, cost))
                        remaining -= fill
                        if remaining == 0:
                            lows, highs = min(lows, b.low), max(highs, b.high)
                            done = True
                            break
        prev_lows = lows
        lows, highs = min(lows, b.low), max(highs, b.high)
        if regular:
            if b.low <= stop:
                raw = min(stop, b.open)
                gapped = b.open < stop
                lows = min(prev_lows, raw)  # nothing after the stop execution counts toward MAE
                sell_regular(b, raw, remaining, "stop_gap" if gapped else "stop", _utc(b.start if gapped else b.end))
                done = True
                break
            if not partial_taken and b.high >= target:
                half = remaining // 2
                if half >= 1:
                    sell_regular(b, target, half, "target_1r", _utc(b.end))
                partial_taken = True
                trail_active = True
            v = vwap.value
            if trail_active and v is not None and b.close < v:
                pending_trail = True
            is_last_regular = i + 1 >= len(tl) or tl[i + 1].session != "regular"
            if same_day_post_exit and is_last_regular and remaining > 0:
                spread = ext_spread(_utc(b.end), "post", day) / _BPS
                pending_ext = (_q_up(b.close * (Decimal(1) - spread)), "post_market_exit", _utc(b.end))
        elif remaining > 0 and b.is_closing_print:
            # The closing print is untradable and its price is auction data: it never creates an exit
            # order. Re-arm the order the prior bar placed so it can work on the next tradable bar.
            if carried_ext is not None and not is_entry:
                pending_ext = (carried_ext[0], carried_ext[1], _utc(b.end))
        elif remaining > 0:
            spread = ext_spread(_utc(b.end), b.session, day) / _BPS
            if b.close < stop:
                pending_ext = (_q_up(b.low * (Decimal(1) - spread)), "stop_ext", _utc(b.end))
            elif same_day_post_exit and b.session == "post" and day == rd:
                pending_ext = (_q_up(b.close * (Decimal(1) - spread)), "post_market_exit", _utc(b.end))
        if remaining == 0:
            done = True
            break

    if remaining > 0 and not done:
        # Data ended or the post-market deadline passed with shares left.
        last = tl[last_i]
        day = _ny(last.start).date()
        if same_day_post_exit and last.session == "post":
            post_bars = [
                x for x in tl[entry_idx:] if x.session == "post" and _ny(x.start).date() == rd
                and x.start < _utc(_et_dt(rd, cfg.post_signal_time))
            ]
            worst = min((x.low for x in post_bars), default=last.close)
            spread = ext_spread(_utc(last.end), "post", day) / _BPS
            raw = _q_down(worst * (Decimal(1) - spread))
            legs.append(TradeLeg(_utc(last.end), "post", "sell", remaining, raw, "post_market_unfilled_marked", Decimal(0)))
            remaining = 0
        elif last.session == "regular" and last.end >= flat_dt(day):
            # D5 fallback: the flat-time bar is missing but the session reached its flat deadline.
            sell_regular(last, last.close, remaining, "flat_time_close", _utc(last.end))
        else:
            # Data stopped before the scheduled exit horizon was observed: fail closed.
            raise _truncated_after_entry(cand)

    buys = [lg for lg in legs if lg.side == "buy"]
    sells = [lg for lg in legs if lg.side == "sell"]
    entry_cost = sum((lg.cost for lg in buys), Decimal(0))
    sold = sum(lg.shares for lg in sells)
    proceeds = sum((lg.price * lg.shares - lg.cost for lg in sells), Decimal(0))
    pnl = proceeds - (entry_price * qty + entry_cost)
    last_leg = sells[-1]
    trade = IntradayTrade(
        symbol=cand.symbol, fiscal_period=cand.fiscal_period, report_date=cand.report_date,
        reaction_date=cand.reaction_date, variant=variant, vwap_anchor=anchor,
        opening_range_minutes=cfg.opening_range_minutes, submitted_at=plan.submitted_at,
        entry_time=buys[0].time, entry_session=buys[0].session, entry_price=entry_price,
        exit_time=last_leg.time, exit_session=last_leg.session,
        exit_price=sum((lg.price * lg.shares for lg in sells), Decimal(0)) / Decimal(sold),
        exit_reason=last_leg.reason, requested_shares=shares, filled_shares=qty,
        reference_entry=plan.reference_entry, stop_price=stop, risk_per_share=risk, pnl=pnl,
        r_multiple=pnl / (risk * qty),
        mae_r=max(Decimal(0), entry_price - lows) / risk,
        mfe_r=max(Decimal(0), highs - entry_price) / risk,
        partial_taken=partial_taken, below_minimum_fill=below_min,
        gap_percent=_gap(cand), legs=tuple(legs),
    )
    return _Outcome(trade=trade)


def _gap(cand: Any) -> Decimal | None:
    signals = getattr(cand, "signals", None) or {}
    for key in ("premarket_gap_percent", "post_report_move_percent"):
        value = signals.get(key)
        if isinstance(value, Decimal):
            return value
    return None


# -- driver -------------------------------------------------------------------


def run_intraday_backtest(
    candidates: Sequence["CandidateDecision"],
    provider: IntradayHistoryProvider,
    config: "EarningsGapIntradayConfig",
    variant: str,
    equity: Decimal,
    *,
    vwap_anchor: str = "0930",
) -> IntradayBacktestResult:
    """Simulate `variant` over `candidates` (eligible ones trade; the rest are
    recorded as rejected with their reasons). Deterministic: same inputs give the
    same trades, hashes and run id."""
    if variant not in _VARIANTS:
        raise BacktestError(f"unknown variant {variant!r}; expected one of {_VARIANTS}")
    if vwap_anchor not in VWAP_ANCHORS:
        raise BacktestError(f"vwap_anchor must be one of {VWAP_ANCHORS}")
    if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
        raise BacktestError("equity must be a positive Decimal")

    cache: dict[tuple[str, date], tuple[IntradayBar, ...]] = {}
    rejected: list[RejectedOrder] = []
    plans: list[_Plan] = []
    ordered = sorted(candidates, key=lambda c: (c.report_date, c.symbol, c.fiscal_period, c.reaction_date))
    for cand in ordered:
        if cand.variant != variant:
            raise BacktestError(f"candidate variant {cand.variant!r} does not match run variant {variant!r}")

        def reject(stage: Stage, reasons: Sequence[str], at: datetime | None = None, cand: Any = cand) -> None:
            rejected.append(
                RejectedOrder(cand.symbol, cand.fiscal_period, cand.report_date, variant, stage, tuple(reasons), at)
            )

        if not cand.eligible:
            reject("candidate", cand.reasons)
            continue
        if cand.decision_as_of is None:
            reject("candidate", ("no_decision_time",))
            continue
        timeline = _timeline(provider, cand, variant, cache)
        if variant in ("premarket_entry", "post_report_overnight"):
            plan, why = _extended_plan(cand, config, variant, timeline)
        else:
            plan, why = _regular_plan(cand, config, variant, vwap_anchor, timeline)
        if plan is None:
            reject("submission", (why or NO_SIGNAL,))
            continue
        plans.append(plan)

    all_bars = [b for key in sorted(cache) for b in cache[key]]
    data_hash = intraday_data_hash(tuple(all_bars))

    plans.sort(key=lambda p: (p.submitted_at, p.cand.symbol, p.cand.fiscal_period))
    trades: list[IntradayTrade] = []
    submitted_by_day: dict[date, int] = {}
    for plan in plans:
        cand = plan.cand
        sub_day = _ny(plan.submitted_at).date()

        def skip(reasons: Sequence[str], stage: Stage = "submission", plan: _Plan = plan) -> None:
            rejected.append(
                RejectedOrder(
                    plan.cand.symbol, plan.cand.fiscal_period, plan.cand.report_date, variant,
                    stage, tuple(reasons), plan.submitted_at,
                )
            )

        risk = plan.reference_entry - plan.stop
        if risk <= 0:
            skip((NONPOSITIVE_RISK,))
            continue
        if risk / plan.reference_entry * Decimal(100) > config.maximum_risk_percent:
            skip((RISK_PERCENT_EXCEEDED,))
            continue
        if submitted_by_day.get(sub_day, 0) >= config.maximum_trades_per_day:
            skip((MAX_TRADES_PER_DAY,))
            continue
        losses = sum(
            1
            for t in trades
            if t.pnl < 0 and t.exit_time <= plan.submitted_at and _ny(t.exit_time).date() == sub_day
        )
        if losses >= config.maximum_losses_per_day:
            skip((MAX_LOSSES_PER_DAY,))
            continue
        equity_now = equity + sum((t.pnl for t in trades if t.exit_time <= plan.submitted_at), Decimal(0))
        participation = config.regular_participation if plan.kind == "regular" else config.extended_participation
        by_risk = int((config.risk_fraction * equity_now / risk).to_integral_value(rounding=ROUND_DOWN))
        by_position = int(
            (config.maximum_position_fraction * equity_now / plan.reference_entry).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        by_volume = int((participation * plan.signal_volume).to_integral_value(rounding=ROUND_DOWN))
        shares = min(by_risk, by_position, by_volume)
        if shares < 1:
            skip((ZERO_SHARES,))
            continue
        if plan.kind == "extended" and plan.reference_entry * shares < config.minimum_extended_fill_usd:
            skip((BELOW_MINIMUM_EXTENDED_NOTIONAL,))
            continue
        submitted_by_day[sub_day] = submitted_by_day.get(sub_day, 0) + 1
        outcome = _simulate(plan, shares, config, variant, vwap_anchor)
        if outcome.trade is not None:
            trades.append(outcome.trade)
        else:
            skip((outcome.unfilled_reason or LIMIT_NOT_REACHED,), "unfilled")

    daily: dict[date, Decimal] = {}
    running = equity
    days = sorted({_ny(t.exit_time).date() for t in trades} | {_ny(t.entry_time).date() for t in trades})
    for day in days:
        running += sum((t.pnl for t in trades if _ny(t.exit_time).date() == day), Decimal(0))
        daily[day] = running
    daily_equity = tuple(DailyEquity(d, daily[d]) for d in days)

    config_hash = hash_config(
        {
            "schema": ENGINE_SCHEMA_VERSION,
            "config": config.audit_record(),
            "variant": variant,
            "vwap_anchor": vwap_anchor,
        }
    )
    run_id_material = {
        "configuration_hash": config_hash,
        "data_hash": data_hash,
        "equity": equity,
        "candidates": [
            [
                c.symbol, c.fiscal_period, c.report_date.isoformat(), c.variant, c.status, list(c.reasons),
                c.decision_as_of.astimezone(_UTC).isoformat() if c.decision_as_of is not None else "",
                c.reaction_date.isoformat(), c.timing,
                str(c.prior_close) if c.prior_close is not None else "",
                c.assumed_release.astimezone(_UTC).isoformat() if c.assumed_release is not None else "",
                str(c.avg_dollar_volume) if c.avg_dollar_volume is not None else "",
                "" if c.trend_filter_passed is None else str(c.trend_filter_passed),
                [[k, str(v)] for k, v in sorted(c.signals.items())],
                [[k, str(v)] for k, v in sorted(c.report_signals.items())],
            ]
            for c in ordered
        ],
    }
    run_id = "igb-" + hash_config(run_id_material)[:24]
    trades_sorted = tuple(sorted(trades, key=lambda t: (t.entry_time, t.symbol, t.fiscal_period)))
    unverified = sum(1 for r in rejected if "release_time_unverified" in r.reasons)
    return IntradayBacktestResult(
        variant=variant, vwap_anchor=vwap_anchor, initial_equity=equity, trades=trades_sorted,
        daily_equity=daily_equity, rejected=tuple(rejected), configuration_hash=config_hash,
        data_hash=data_hash, run_id=run_id, candidates_count=len(ordered),
        release_time_unverified_count=unverified,
        below_minimum_fill_count=sum(1 for t in trades_sorted if t.below_minimum_fill),
        unfilled_count=sum(1 for r in rejected if r.stage == "unfilled"),
    )
