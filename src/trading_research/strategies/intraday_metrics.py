"""Intraday earnings-gap metrics, baselines, control, acceptance gate, report
(plan section 6 Step 5 and section 7).

Research / backtest only. Pure functions over `IntradayBacktestResult`; no
broker, model, network, wall clock or randomness. The gate NEVER reports PASS on
an insufficient sample, and a passing software test suite is not research
acceptance (stated in every gate result and report).

Conservative readings where the plan is ambiguous (also recorded in the runbook):

* Baselines are compared with the strategy on mean NET RETURN PERCENT per trade
  (pnl / entry notional), since baselines have no stop and so no R. The strategy
  must be strictly greater than every baseline; a baseline with no computable
  trades fails the criterion (cannot be shown to be beaten).
* Baselines run on every ELIGIBLE candidate of the variant (the "same candidate
  set"), not only the ones the strategy traded. Both sides pay slippage plus the
  spread proxy per side; extended-hours fills use the extended proxy.
* The ordinary-day control re-runs the variant's rules, through the same engine,
  on a session `control_offset_sessions` (10) trading sessions before each
  event's report date (skipping that symbol's event dates), with the decision
  time-of-day preserved. The control is "also profitable" when its expectancy
  is > 0 R; a control with no trades cannot show the edge is from the catalyst,
  so that criterion fails.
* Gap buckets are half-open: [5,10), [10,20), [20,inf); below 5 is `below_5`.
* Profit factor with no losing trade and some profit is treated as meeting the
  threshold (reported as None = unbounded).
* "Share of orders unfilled" = unfilled / (filled + unfilled) submitted orders.
* The doubled-spread rerun doubles extended_spread_floor_bps and
  extended_spread_k and re-runs the whole engine.

Relative imports only; must not import research/evidence modules.
"""

from __future__ import annotations

import dataclasses
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..backtesting.intraday_engine import IntradayBacktestResult, IntradayTrade, _spread_bps, run_intraday_backtest
from ..backtesting.intraday_history import IntradayHistoryProvider
from ..backtesting.models import BacktestError, IntradayBar
from ..evaluation.market_calendar import MARKET_TIMEZONE_NAME, is_trading_day
from ..hashing import hash_config
from .earnings_gap_intraday import CandidateDecision, EarningsGapIntradayConfig, evaluate_candidate
from .earnings_surprise import EarningsSurprise

REPORT_SCHEMA_VERSION = "intraday-gap-report-v1"
MINIMUM_TRADES = 100
MINIMUM_EXPECTANCY_R = Decimal("0.10")
MINIMUM_PROFIT_FACTOR = Decimal("1.3")
MAXIMUM_DRAWDOWN_R = Decimal("10")
MINIMUM_MEDIAN_FILLED_NOTIONAL = Decimal("2000")
MAXIMUM_UNFILLED_SHARE = Decimal("0.30")
WORST_LOSS_FLOOR_R = Decimal("-3")
CONTROL_OFFSET_SESSIONS = 10
EXTENDED_VARIANTS = ("premarket_entry", "post_report_overnight")
BASELINE_NAMES = ("open_to_close", "0945_to_close", "premarket_0925_to_close", "post_1630_to_next_open")

NOT_ACCEPTANCE_NOTE = (
    "Passing software tests is NOT research acceptance. Only the operator's single "
    "out-of-sample run, judged by these criteria, can support a claim about a variant."
)
RESEARCH_NOTE = "Research only, not financial advice. Paper/backtest simulation; no orders were placed."
CAVEATS = (
    "Survivorship: the earnings universe comes from a current export and may omit delisted or "
    "acquired symbols; results are biased toward survivors.",
    "Point-in-time estimates: EPS estimates may be revised/consensus-as-of-export values, not "
    "necessarily the consensus known at release time.",
    "Release time: a missing release_time uses the conservative bound (am released at 09:30, "
    "pm at 20:00); those events are excluded as release_time_unverified and counted below.",
    "Fill model: bar-based simulation; market fills at the next bar open plus slippage and a "
    "spread proxy; extended-hours limits fill only if traded through; real liquidity may be worse. "
    "The early-close post-market end (17:00 ET default) is UNCONFIRMED against vendor/broker - "
    "operator must confirm before merge.",
)

_NY = ZoneInfo(MARKET_TIMEZONE_NAME)
_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_BPS = Decimal(10000)


# -- small helpers ------------------------------------------------------------


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(values: Sequence[Decimal], pct: int) -> Decimal | None:
    """Nearest-rank percentile (deterministic)."""
    if not values:
        return None
    s = sorted(values)
    rank = int((Decimal(pct) * len(s) / _HUNDRED).to_integral_value(rounding=ROUND_CEILING))
    return s[max(rank, 1) - 1]


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, _ZERO) / len(values) if values else None


def _ny(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise BacktestError("naive datetime rejected; timestamps must be timezone-aware")
    return value.astimezone(_NY)


# -- metrics ------------------------------------------------------------------


@dataclass(frozen=True)
class GroupMetrics:
    trades: int
    win_rate: Decimal | None
    average_r: Decimal | None
    expectancy_r: Decimal | None
    total_r: Decimal
    profit_factor: Decimal | None  # None: undefined (no trades/no loss)
    gross_profit_r: Decimal
    gross_loss_r: Decimal


def _group(rs: Sequence[Decimal]) -> GroupMetrics:
    wins = [r for r in rs if r > 0]
    gp = sum(wins, _ZERO)
    gl = -sum((r for r in rs if r < 0), _ZERO)
    n = len(rs)
    return GroupMetrics(
        trades=n,
        win_rate=Decimal(len(wins)) / n if n else None,
        average_r=_mean(rs),
        expectancy_r=_mean(rs),
        total_r=sum(rs, _ZERO),
        profit_factor=(gp / gl) if gl > 0 else None,
        gross_profit_r=gp,
        gross_loss_r=gl,
    )


@dataclass(frozen=True)
class CapacityMetrics:
    median_filled_notional: Decimal | None
    p10_filled_notional: Decimal | None
    unfilled_orders: int
    submitted_orders: int
    unfilled_share: Decimal | None
    fills_below_minimum: int


@dataclass(frozen=True)
class IntradayMetrics:
    variant: str
    trades: int
    win_rate: Decimal | None
    average_r: Decimal | None
    median_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor: Decimal | None
    gross_profit_r: Decimal
    gross_loss_r: Decimal
    total_r: Decimal
    max_drawdown_r: Decimal
    average_minutes_in_trade: Decimal | None
    mae_r_distribution: Mapping[str, Decimal | None]
    mean_return_percent: Decimal | None
    worst_trade_r: Decimal | None
    total_r_without_top3: Decimal | None
    expectancy_r_without_top3: Decimal | None
    capacity: CapacityMetrics
    breakdowns: Mapping[str, Mapping[str, GroupMetrics]]
    release_time_unverified_count: int
    candidates_count: int


def _trade_return_percent(t: IntradayTrade) -> Decimal:
    notional = t.entry_price * t.filled_shares
    return t.pnl / notional * _HUNDRED if notional > 0 else _ZERO


def _max_drawdown_r(trades: Sequence[IntradayTrade]) -> Decimal:
    ordered = sorted(trades, key=lambda t: (t.exit_time, t.symbol, t.fiscal_period))
    cum = peak = dd = _ZERO
    for t in ordered:
        cum += t.r_multiple
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def gap_bucket(gap: Decimal | None) -> str:
    if gap is None:
        return "unknown"
    if gap < 5:
        return "below_5"
    if gap < 10:
        return "5_to_10"
    if gap < 20:
        return "10_to_20"
    return "20_plus"


def _continuation_sign(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        return "unknown"
    return "positive" if value > 0 else "negative" if value < 0 else "zero"


def compute_intraday_metrics(
    result: IntradayBacktestResult, candidates: Sequence[CandidateDecision] = ()
) -> IntradayMetrics:
    """Metrics for one run. `candidates` (optional) supplies the report-only
    `continuation` sign for the breakdown; without it that breakdown is `unknown`."""
    trades = result.trades
    rs = [t.r_multiple for t in trades]
    g = _group(rs)
    minutes = [Decimal((t.exit_time - t.entry_time).total_seconds()) / 60 for t in trades]
    maes = [t.mae_r for t in trades]
    mae = {
        "p50": _percentile(maes, 50),
        "p75": _percentile(maes, 75),
        "p90": _percentile(maes, 90),
        "max": max(maes) if maes else None,
    }
    if len(trades) > 3:
        rest = sorted(rs, reverse=True)[3:]
        total_wo3: Decimal | None = sum(rest, _ZERO)
        exp_wo3: Decimal | None = total_wo3 / len(rest)
    else:
        total_wo3 = exp_wo3 = None

    notionals = [t.entry_price * t.filled_shares for t in trades]
    submitted = len(trades) + result.unfilled_count
    capacity = CapacityMetrics(
        median_filled_notional=_median(notionals),
        p10_filled_notional=_percentile(notionals, 10),
        unfilled_orders=result.unfilled_count,
        submitted_orders=submitted,
        unfilled_share=Decimal(result.unfilled_count) / submitted if submitted else None,
        fills_below_minimum=result.below_minimum_fill_count,
    )

    cont_by_key: dict[tuple[str, str, date], str] = {}
    for c in candidates:
        cont_by_key[(c.symbol, c.fiscal_period, c.report_date)] = _continuation_sign(
            c.report_signals.get("continuation")
        )
    dims: dict[str, Callable[[IntradayTrade], str]] = {
        "variant": lambda t: t.variant,
        "entry_session": lambda t: t.entry_session,
        "opening_range_minutes": lambda t: str(t.opening_range_minutes),
        "gap_bucket": lambda t: gap_bucket(t.gap_percent),
        "continuation_sign": lambda t: cont_by_key.get((t.symbol, t.fiscal_period, t.report_date), "unknown"),
    }
    breakdowns: dict[str, dict[str, GroupMetrics]] = {}
    for dim, keyfn in dims.items():
        buckets: dict[str, list[Decimal]] = defaultdict(list)
        for t in trades:
            buckets[keyfn(t)].append(t.r_multiple)
        breakdowns[dim] = {k: _group(buckets[k]) for k in sorted(buckets)}

    return IntradayMetrics(
        variant=result.variant, trades=len(trades), win_rate=g.win_rate, average_r=g.average_r,
        median_r=_median(rs), expectancy_r=g.expectancy_r, profit_factor=g.profit_factor,
        gross_profit_r=g.gross_profit_r, gross_loss_r=g.gross_loss_r, total_r=g.total_r,
        max_drawdown_r=_max_drawdown_r(trades), average_minutes_in_trade=_mean(minutes),
        mae_r_distribution=mae, mean_return_percent=_mean([_trade_return_percent(t) for t in trades]),
        worst_trade_r=min(rs) if rs else None, total_r_without_top3=total_wo3,
        expectancy_r_without_top3=exp_wo3, capacity=capacity, breakdowns=breakdowns,
        release_time_unverified_count=result.release_time_unverified_count,
        candidates_count=result.candidates_count,
    )


# -- baselines ----------------------------------------------------------------


@dataclass(frozen=True)
class BaselineMetrics:
    name: str
    trades: int
    skipped: int  # eligible candidates without the needed bars
    mean_return_percent: Decimal | None
    win_rate: Decimal | None


def _proxy_bps(bar: IntradayBar, floor: Decimal, k: Decimal) -> Decimal:
    return max(floor, k * (bar.high - bar.low) / bar.close * _BPS)


def _regular_bars(bars: Sequence[IntradayBar], day: date) -> list[IntradayBar]:
    return sorted((b for b in bars if b.session == "regular" and _ny(b.start).date() == day), key=lambda b: b.start)


def _ext_price_bar(
    bars: Sequence[IntradayBar], session: str, day: date, as_of: datetime, not_before: datetime | None
) -> IntradayBar | None:
    usable = [
        b
        for b in bars
        if b.session == session
        and _ny(b.start).date() == day
        and b.available_at <= as_of
        and b.volume > 0
        and not b.is_closing_print
        and (not_before is None or b.start >= not_before)
    ]
    return max(usable, key=lambda b: b.start) if usable else None


@dataclass(frozen=True)
class _Leg:
    """One baseline execution: its price and the information available when it executes."""

    price: Decimal
    bars: Sequence[IntradayBar]
    session: str  # pre | regular | post
    day: date
    at: datetime  # execution time: only bars with available_at <= at inform the spread


def _baseline_return(
    buy: _Leg, sell: _Leg, cfg: EarningsGapIntradayConfig, not_before: datetime | None
) -> Decimal:
    """Same cost model as the engine: slippage plus the spread proxy from the first
    session bar already available at the execution time (never the executing bar's
    own or later range). Extended buys divide by the limit-price notional with the
    entry cost held separately, as `_trade_return_percent` does for strategy trades."""

    def side_bps(leg: _Leg) -> Decimal:
        ext = leg.session != "regular"
        floor, k = (
            (cfg.extended_spread_floor_bps, cfg.extended_spread_k) if ext else (cfg.spread_floor_bps, cfg.spread_k)
        )
        return cfg.slippage_bps + _spread_bps(
            leg.bars, leg.session, leg.day, leg.at, floor, k, not_before if ext else None
        )

    cost_in = buy.price * (1 + side_bps(buy) / _BPS)
    proceeds = sell.price * (1 - side_bps(sell) / _BPS)
    denominator = buy.price if buy.session != "regular" else cost_in
    return (proceeds - cost_in) / denominator * _HUNDRED


def compute_baselines(
    candidates: Sequence[CandidateDecision],
    provider: IntradayHistoryProvider,
    config: EarningsGapIntradayConfig,
) -> dict[str, BaselineMetrics]:
    """The four Step 5 baselines over the ELIGIBLE candidates, same cost model."""
    returns: dict[str, list[Decimal]] = {n: [] for n in BASELINE_NAMES}
    skipped: dict[str, int] = {n: 0 for n in BASELINE_NAMES}
    for c in sorted(
        (c for c in candidates if c.eligible), key=lambda c: (c.report_date, c.symbol, c.fiscal_period)
    ):
        react = tuple(provider.get_bars(c.symbol, c.reaction_date))
        reg = _regular_bars(react, c.reaction_date)
        not_before = c.assumed_release
        open_bar = reg[0] if reg else None
        close_bar = reg[-1] if reg else None
        # open -> close
        if open_bar and close_bar:
            returns["open_to_close"].append(
                _baseline_return(
                    _Leg(open_bar.open, react, "regular", c.reaction_date, open_bar.start),
                    _Leg(close_bar.close, react, "regular", c.reaction_date, close_bar.start),
                    config, not_before,
                )
            )
        else:
            skipped["open_to_close"] += 1
        # 09:45 -> close
        cutoff = datetime.combine(c.reaction_date, config.earliest_entry_time, tzinfo=_NY)
        b945 = next((b for b in reg if b.start >= cutoff), None)
        if b945 and close_bar:
            returns["0945_to_close"].append(
                _baseline_return(
                    _Leg(b945.open, react, "regular", c.reaction_date, b945.start),
                    _Leg(close_bar.close, react, "regular", c.reaction_date, close_bar.start),
                    config, not_before,
                )
            )
        else:
            skipped["0945_to_close"] += 1
        # 09:25 pre-market price -> close
        as_of = datetime.combine(c.reaction_date, config.premarket_decision_time, tzinfo=_NY)
        pre_bar = _ext_price_bar(react, "pre", c.reaction_date, as_of, not_before)
        if pre_bar and close_bar:
            returns["premarket_0925_to_close"].append(
                _baseline_return(
                    _Leg(pre_bar.close, react, "pre", c.reaction_date, as_of),
                    _Leg(close_bar.close, react, "regular", c.reaction_date, close_bar.start),
                    config, not_before,
                )
            )
        else:
            skipped["premarket_0925_to_close"] += 1
        # 16:30 post-market price -> next regular open (pm reports only)
        post_ok = False
        if c.timing == "pm" and open_bar is not None:
            rep = tuple(provider.get_bars(c.symbol, c.report_date))
            at = datetime.combine(c.report_date, config.post_signal_time, tzinfo=_NY)
            post_bar = _ext_price_bar(rep, "post", c.report_date, at, not_before)
            if post_bar is not None:
                returns["post_1630_to_next_open"].append(
                    _baseline_return(
                        _Leg(post_bar.close, rep, "post", c.report_date, at),
                        _Leg(open_bar.open, react, "regular", c.reaction_date, open_bar.start),
                        config, not_before,
                    )
                )
                post_ok = True
        if not post_ok:
            skipped["post_1630_to_next_open"] += 1
    out: dict[str, BaselineMetrics] = {}
    for name in BASELINE_NAMES:
        rs = returns[name]
        out[name] = BaselineMetrics(
            name=name, trades=len(rs), skipped=skipped[name], mean_return_percent=_mean(rs),
            win_rate=Decimal(sum(1 for r in rs if r > 0)) / len(rs) if rs else None,
        )
    return out


# -- ordinary-day control and doubled-spread rerun -----------------------------


def _session_back(day: date, n: int) -> date:
    cur = day
    while n > 0:
        cur -= timedelta(days=1)
        if is_trading_day(cur):
            n -= 1
    return cur


def _prev_session(day: date) -> date:
    return _session_back(day, 1)


def control_candidates(
    candidates: Sequence[CandidateDecision],
    provider: IntradayHistoryProvider,
    config: EarningsGapIntradayConfig,
    *,
    offset_sessions: int = CONTROL_OFFSET_SESSIONS,
) -> tuple[CandidateDecision, ...]:
    """Re-date every eligible candidate to an ordinary session (no event of that
    symbol) `offset_sessions` sessions earlier, preserving time-of-day, and re-run
    the variant's eligibility rules (gap, hold, relative volume, liquidity, price,
    trend) point-in-time on that ordinary day. Control days that fail those rules
    are returned as ineligible, so they are rejected rather than traded. The
    event's own beat is not re-tested: an ordinary day has no report."""
    event_days: dict[str, set[date]] = defaultdict(set)
    for c in candidates:
        event_days[c.symbol].update({c.report_date, c.reaction_date})
    out: list[CandidateDecision] = []
    for c in sorted(candidates, key=lambda c: (c.report_date, c.symbol, c.fiscal_period)):
        if not c.eligible or c.decision_as_of is None:
            continue
        new_react = _session_back(c.reaction_date, offset_sessions)
        new_report = _prev_session(new_react) if c.report_date != c.reaction_date else new_react
        if {new_react, new_report} & event_days[c.symbol]:
            continue

        released = c.assumed_release
        # A synthetic, always-"beating" event on the ordinary day: only its symbol, timing and release
        # time-of-day matter to the eligibility rules; the control must not inherit the catalyst-day
        # gap/hold/volume/liquidity outcome, so everything else is recomputed from the provider's bars.
        control_event = EarningsSurprise(
            symbol=c.symbol, fiscal_period=c.fiscal_period, report_date=new_report, timing=c.timing,  # type: ignore[arg-type]
            release_time=_ny(released).time().replace(tzinfo=None) if released is not None else None,
            eps_estimate=Decimal(0), eps_actual=Decimal(1), source="ordinary_day_control",
            captured_at=datetime.combine(new_report, time(0), tzinfo=_NY),
        )
        decided = evaluate_candidate(control_event, provider, config, c.variant)
        if decided.reaction_date != new_react:
            continue
        out.append(dataclasses.replace(decided, report_signals={}))
    return tuple(out)


def run_control(
    candidates: Sequence[CandidateDecision],
    provider: IntradayHistoryProvider,
    config: EarningsGapIntradayConfig,
    variant: str,
    equity: Decimal,
) -> IntradayBacktestResult:
    return run_intraday_backtest(control_candidates(candidates, provider, config), provider, config, variant, equity)


def doubled_spread_config(config: EarningsGapIntradayConfig) -> EarningsGapIntradayConfig:
    return dataclasses.replace(
        config,
        extended_spread_floor_bps=config.extended_spread_floor_bps * 2,
        extended_spread_k=config.extended_spread_k * 2,
    )


def run_doubled_spread(
    candidates: Sequence[CandidateDecision],
    provider: IntradayHistoryProvider,
    config: EarningsGapIntradayConfig,
    variant: str,
    equity: Decimal,
) -> IntradayBacktestResult:
    """Re-run the engine with the extended spread proxy doubled."""
    return run_intraday_backtest(candidates, provider, doubled_spread_config(config), variant, equity)


# -- acceptance gate (section 7) ----------------------------------------------


@dataclass(frozen=True)
class CriterionResult:
    name: str
    passed: bool
    value: str
    threshold: str
    detail: str = ""


@dataclass(frozen=True)
class GateResult:
    variant: str
    verdict: str  # PASS | FAIL | INSUFFICIENT_SAMPLE
    passed: bool
    criteria: tuple[CriterionResult, ...]
    note: str = NOT_ACCEPTANCE_NOTE


def _fmt(value: Decimal | None) -> str:
    return "n/a" if value is None else format(value.normalize() if value == value.to_integral() else value, "f")


def evaluate_gate(
    metrics: IntradayMetrics,
    baselines: Mapping[str, BaselineMetrics],
    control: IntradayMetrics | None,
    doubled_spread: IntradayMetrics | None = None,
    *,
    minimum_trades: int = MINIMUM_TRADES,
) -> GateResult:
    """Pure section-7 gate. Never PASS with fewer than `minimum_trades` (floored
    at MINIMUM_TRADES) trades, or with any criterion failed or unevaluated."""
    minimum_trades = max(minimum_trades, MINIMUM_TRADES)
    crit: list[CriterionResult] = []

    def add(name: str, ok: bool, value: str, threshold: str, detail: str = "") -> None:
        crit.append(CriterionResult(name, bool(ok), value, threshold, detail))

    n = metrics.trades
    add("minimum_trades", n >= minimum_trades, str(n), f">= {minimum_trades}")
    exp = metrics.expectancy_r
    add("expectancy_r", exp is not None and exp > MINIMUM_EXPECTANCY_R, _fmt(exp), f"> {MINIMUM_EXPECTANCY_R}")
    pf_ok = (metrics.profit_factor is not None and metrics.profit_factor >= MINIMUM_PROFIT_FACTOR) or (
        metrics.profit_factor is None and metrics.gross_loss_r == 0 and metrics.gross_profit_r > 0
    )
    add(
        "profit_factor", pf_ok, "unbounded" if metrics.profit_factor is None and pf_ok else _fmt(metrics.profit_factor),
        f">= {MINIMUM_PROFIT_FACTOR}",
    )
    wo3 = metrics.total_r_without_top3
    add("positive_without_top_3", wo3 is not None and wo3 > 0, _fmt(wo3), "total R > 0 after removing top 3")

    mine = metrics.mean_return_percent
    beaten: list[str] = []
    failed: list[str] = []
    for name in BASELINE_NAMES:
        b = baselines.get(name)
        if b is None or b.mean_return_percent is None or mine is None or not mine > b.mean_return_percent:
            failed.append(name)
        else:
            beaten.append(name)
    add(
        "beats_every_baseline", not failed, _fmt(mine) + "% per trade", "> each baseline mean return",
        "failed or unevaluated: " + ", ".join(failed) if failed else "all four beaten",
    )
    add("max_drawdown_r", metrics.max_drawdown_r <= MAXIMUM_DRAWDOWN_R, _fmt(metrics.max_drawdown_r), f"<= {MAXIMUM_DRAWDOWN_R}")
    if control is None or control.trades == 0:
        add("ordinary_day_control_not_profitable", False, "n/a", "control expectancy <= 0 R", "control not evaluated or no trades")
    else:
        add(
            "ordinary_day_control_not_profitable", control.expectancy_r is not None and control.expectancy_r <= 0,
            _fmt(control.expectancy_r), "control expectancy <= 0 R",
        )

    if metrics.variant in EXTENDED_VARIANTS:
        cap = metrics.capacity
        med = cap.median_filled_notional
        add("median_filled_notional", med is not None and med >= MINIMUM_MEDIAN_FILLED_NOTIONAL, _fmt(med), f">= {MINIMUM_MEDIAN_FILLED_NOTIONAL}")
        add(
            "unfilled_share", cap.unfilled_share is not None and cap.unfilled_share <= MAXIMUM_UNFILLED_SHARE,
            _fmt(cap.unfilled_share), f"<= {MAXIMUM_UNFILLED_SHARE}",
        )
        if doubled_spread is None:
            add("positive_with_doubled_spread", False, "n/a", "expectancy > 0 R", "doubled-spread rerun not provided")
        else:
            d = doubled_spread.expectancy_r
            add("positive_with_doubled_spread", d is not None and d > 0, _fmt(d), "expectancy > 0 R")
    if metrics.variant == "post_report_overnight":
        w = metrics.worst_trade_r
        add("worst_single_trade_loss_r", w is not None and w >= WORST_LOSS_FLOOR_R, _fmt(w), f">= {WORST_LOSS_FLOOR_R}")

    if n < minimum_trades:
        verdict, passed = "INSUFFICIENT_SAMPLE", False
    elif all(c.passed for c in crit):
        verdict, passed = "PASS", True
    else:
        verdict, passed = "FAIL", False
    return GateResult(variant=metrics.variant, verdict=verdict, passed=passed, criteria=tuple(crit))


# -- report -------------------------------------------------------------------


@dataclass(frozen=True)
class IntradayEvaluation:
    result: IntradayBacktestResult
    metrics: IntradayMetrics
    baselines: Mapping[str, BaselineMetrics]
    control: IntradayMetrics
    doubled_spread: IntradayMetrics | None
    gate: GateResult


def evaluate_variant(
    candidates: Sequence[CandidateDecision],
    provider: IntradayHistoryProvider,
    config: EarningsGapIntradayConfig,
    variant: str,
    equity: Decimal,
) -> IntradayEvaluation:
    """Engine run + metrics + baselines + control (+ doubled-spread rerun for
    extended variants) + gate."""
    result = run_intraday_backtest(candidates, provider, config, variant, equity)
    metrics = compute_intraday_metrics(result, candidates)
    baselines = compute_baselines(candidates, provider, config)
    control = compute_intraday_metrics(run_control(candidates, provider, config, variant, equity))
    doubled = (
        compute_intraday_metrics(run_doubled_spread(candidates, provider, config, variant, equity))
        if variant in EXTENDED_VARIANTS
        else None
    )
    gate = evaluate_gate(metrics, baselines, control, doubled)
    return IntradayEvaluation(result, metrics, baselines, control, doubled, gate)


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def build_report(
    evaluation: IntradayEvaluation, *, split: str = "unspecified", parameters: Mapping[str, Any] | None = None,
    input_manifest_hash: str | None = None,
) -> dict[str, Any]:
    """Deterministic report dict (no wall clock): caveats first, then identity,
    gate, metrics, baselines, control."""
    r = evaluation.result
    # The engine's run id only sees bars its own simulation read. When the caller supplies a manifest
    # covering every consumed input (eligibility, benchmark, baseline, control bars; EPS rows), fold it in.
    # The report identity also covers every report-defining field (split and persisted parameters such as
    # the requested start/end), so distinct reports can never resolve to the same artifact path.
    params_json = _jsonable(dict(parameters or {}))
    run_id = "igb-" + hash_config(
        {"engine_run_id": r.run_id, "input_manifest_hash": input_manifest_hash, "split": split,
         "parameters": params_json}
    )[:24]
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA_VERSION,
        "notes": [RESEARCH_NOTE, NOT_ACCEPTANCE_NOTE],
        "caveats": list(CAVEATS),
        "run_id": run_id,
        "input_manifest_hash": input_manifest_hash,
        "variant": r.variant,
        "split": split,
        "configuration_hash": r.configuration_hash,
        "data_hash": r.data_hash,
        "parameters": params_json,
        "counts": {
            "candidates": r.candidates_count,
            "trades": len(r.trades),
            "rejected": len(r.rejected),
            "release_time_unverified": r.release_time_unverified_count,
            "unfilled": r.unfilled_count,
            "fills_below_minimum_extended_fill": r.below_minimum_fill_count,
        },
        "gate": _jsonable(evaluation.gate),
        "metrics": _jsonable(evaluation.metrics),
        "baselines": _jsonable(dict(evaluation.baselines)),
        "ordinary_day_control": _jsonable(evaluation.control),
        "doubled_spread": _jsonable(evaluation.doubled_spread),
    }
    report["report_hash"] = hash_config(report)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [f"# Intraday earnings-gap report: {report['variant']} ({report['run_id']})", ""]
    lines += [f"> {n}" for n in report["notes"]]
    lines += ["", "## Caveats", ""] + [f"- {c}" for c in report["caveats"]]
    counts = report["counts"]
    lines += [
        "", "## Identity", "",
        f"- split: {report['split']}",
        f"- configuration_hash: {report['configuration_hash']}",
        f"- data_hash: {report['data_hash']}",
        f"- candidates: {counts['candidates']}; trades: {counts['trades']}; rejected: {counts['rejected']}",
        f"- release_time_unverified (excluded): {counts['release_time_unverified']}",
        f"- unfilled orders: {counts['unfilled']}; fills below minimum_extended_fill_usd: "
        f"{counts['fills_below_minimum_extended_fill']}",
        "", f"## Gate: {report['gate']['verdict']}", "",
        "| criterion | result | value | threshold | detail |", "|---|---|---|---|---|",
    ]
    for c in report["gate"]["criteria"]:
        lines.append(f"| {c['name']} | {'PASS' if c['passed'] else 'FAIL'} | {c['value']} | {c['threshold']} | {c['detail']} |")
    m = report["metrics"]
    lines += ["", "## Metrics", ""]
    for key in (
        "trades", "win_rate", "average_r", "median_r", "expectancy_r", "profit_factor", "total_r",
        "max_drawdown_r", "average_minutes_in_trade", "mean_return_percent", "worst_trade_r",
        "total_r_without_top3", "expectancy_r_without_top3",
    ):
        lines.append(f"- {key}: {m[key]}")
    lines.append(f"- mae_r_distribution: {json.dumps(m['mae_r_distribution'], sort_keys=True)}")
    lines.append(f"- capacity: {json.dumps(m['capacity'], sort_keys=True)}")
    lines += ["", "## Breakdowns", ""]
    for dim, groups in m["breakdowns"].items():
        lines += [f"### {dim}", "", "| key | trades | win rate | expectancy R | total R |", "|---|---|---|---|---|"]
        for k, g in groups.items():
            lines.append(f"| {k} | {g['trades']} | {g['win_rate']} | {g['expectancy_r']} | {g['total_r']} |")
        lines.append("")
    lines += ["## Baselines (mean net return % per trade)", "", "| baseline | trades | skipped | mean % | win rate |", "|---|---|---|---|---|"]
    for name, b in report["baselines"].items():
        lines.append(f"| {name} | {b['trades']} | {b['skipped']} | {b['mean_return_percent']} | {b['win_rate']} |")
    ctl = report["ordinary_day_control"]
    lines += ["", "## Ordinary-day control", "", f"- trades: {ctl['trades']}; expectancy_r: {ctl['expectancy_r']}; total_r: {ctl['total_r']}"]
    if report["doubled_spread"] is not None:
        d = report["doubled_spread"]
        lines.append(f"- doubled extended spread: trades {d['trades']}; expectancy_r: {d['expectancy_r']}")
    lines += ["", RESEARCH_NOTE, ""]
    return "\n".join(lines)


def write_report(report: Mapping[str, Any], reports_dir: Path | str = "reports") -> tuple[Path, Path]:
    """Write `intraday-gap-<run_id>.json` and `.md` (directory created lazily,
    never committed). Same report -> identical bytes."""
    out = Path(reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"intraday-gap-{report['run_id']}"
    json_path, md_path = out / f"{stem}.json", out / f"{stem}.md"
    json_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
