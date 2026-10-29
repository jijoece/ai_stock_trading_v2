"""Stage 4 shadow tracking (docs/paper-testing-plan.md Phase 2).

For every ELIGIBLE stored signal without a terminal outcome, applies the
backtest engine's own rules (`run_backtest`, unchanged) to the bars that
arrived after the signal: next-session limit fill or no fill, stop before
target, maximum holding period, slippage and fees. Results are hypothetical:
no order, intent, fill or position is created anywhere.

Each signal is run through the engine on its own, one share, with ample cash,
so the outcome depends only on the signal and the bars and is reported per
share. The data provider is clamped so no bar that was not yet available at
the update session's close can be seen.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..backtesting.alpaca_history import AlpacaHistoricalDataProvider, warmup_start
from ..backtesting.configuration import BacktestConfiguration
from ..backtesting.data_provider import HistoricalDataProvider
from ..backtesting.engine import run_backtest
from ..backtesting.models import BacktestError, BacktestResult, EntrySignal, HistoricalBar
from ..backtesting.strategy_backtest_runner import DATA_SOURCES, acquire_snapshot
from ..evaluation.market_calendar import regular_session_close
from ..hashing import hash_config
from ..storage.strategy_shadow_repositories import (
    ShadowOutcomeRecord,
    insert_shadow_outcome,
    list_unresolved_eligible_signals,
)
from ..storage.transactions import transaction
from ..strategies.config import ACTIVATION_STAGES, load_strategy_config

MINIMUM_ACTIVATION_STAGE = "STAGE_4_SHADOW_RECOMMENDATION_TRACKING"
DEFAULT_SLIPPAGE_BPS = Decimal("10")
SHADOW_CASH = Decimal("1000000")

PENDING, OPEN, NO_FILL, REJECTED, CLOSED = "PENDING", "OPEN", "NO_FILL", "REJECTED", "CLOSED"
TERMINAL_STATES = (NO_FILL, REJECTED, CLOSED)

CAVEATS = (
    "Hypothetical outcomes only; no order, intent, fill or position exists. Not financial advice.",
    "Outcomes are per share and use the backtest engine's rules, including its stop-first assumption on "
    "daily bars.",
)


class _ClampedProvider:
    """Serves bars only if they were available at `cutoff`, whatever `as_of` the caller asks for."""

    def __init__(self, inner: HistoricalDataProvider, cutoff: datetime):
        self._inner, self._cutoff = inner, cutoff

    def bars(self, *, symbol: str, start: date, end: date, as_of: datetime) -> tuple[HistoricalBar, ...]:
        return self._inner.bars(symbol=symbol, start=start, end=end, as_of=min(as_of, self._cutoff))


def entry_signal_from_row(row, *, quantity: Decimal = Decimal("1")) -> EntrySignal:
    """Rebuild the engine's entry contract from a stored ELIGIBLE row.

    Mirrors `strategies.backtest_adapter.strategy_signal_to_entry_signal`; a
    test compares the two so they cannot drift."""
    if row["data_as_of"] is None:
        raise BacktestError("stored signal is missing data_as_of")
    limit = row["limit_reference"] or row["entry_reference"]
    if limit is None:
        raise BacktestError("stored signal is missing both limit_reference and entry_reference")
    return EntrySignal(
        signal_id=row["signal_content_id"], symbol=row["symbol"],
        generated_after_session=datetime.fromisoformat(row["data_as_of"]).date(),
        limit_price=Decimal(limit), quantity_hint=quantity,
        initial_stop_reference=Decimal(row["initial_stop_reference"]) if row["initial_stop_reference"] else None,
        target_reference=Decimal(row["target_reference"]) if row["target_reference"] else None,
        maximum_holding_sessions=row["expected_holding_period"], strategy_signal_id=row["signal_content_id"],
    )


def _per_share_outcome(result: BacktestResult, signal: EntrySignal, sessions: list[date], last_close: Decimal | None):
    """Map one single-signal engine run to (state, details)."""
    rejection = next((r["reason"] for r in result.rejected_entries if r["signal_id"] == signal.signal_id), None)
    buys = [f for f in result.fills if f.side == "BUY"]
    if not buys:
        if rejection == "NO_NEXT_SESSION" or rejection is None:
            return PENDING, {}
        if rejection == "LIMIT_NOT_FILLED":
            return NO_FILL, {"reject_reason": rejection}
        return REJECTED, {"reject_reason": rejection}
    buy = buys[0]
    sells = [f for f in result.fills if f.side == "SELL"]
    entry_index = sessions.index(buy.market_date)
    details = {
        "entry_session": buy.market_date.isoformat(), "entry_price": buy.fill_price,
        "last_close": last_close,
    }
    if not sells:
        latest = sessions[-1]
        return OPEN, {**details, "holding_sessions": sessions.index(latest) - entry_index}
    sell = sells[-1]
    quantity = buy.quantity
    pnl = sum((f.fill_price * f.quantity - f.fees for f in sells), Decimal("0")) \
        - (buy.fill_price * quantity + buy.fees)
    return CLOSED, {
        **details, "exit_session": sell.market_date.isoformat(), "exit_price": sell.fill_price,
        "exit_reason": sell.exit_reason, "holding_sessions": sessions.index(sell.market_date) - entry_index,
        "net_pnl_per_share": pnl / quantity, "return_fraction": pnl / (buy.fill_price * quantity),
    }


def _text(value) -> str | None:
    return None if value is None else format(value, "f") if isinstance(value, Decimal) else str(value)


def evaluate_signal_outcome(
    row, *, provider: HistoricalDataProvider, as_of: date, slippage_bps: Decimal, fee_per_order: Decimal,
) -> tuple[str, dict]:
    """Apply the engine's rules to the bars after one stored signal, as of the `as_of` close."""
    signal = entry_signal_from_row(row)
    cutoff = regular_session_close(as_of)
    clamped = _ClampedProvider(provider, cutoff)
    configuration = BacktestConfiguration(
        start_date=warmup_start(signal.generated_after_session), end_date=as_of, symbols=(signal.symbol,),
        initial_cash=SHADOW_CASH, slippage_bps=slippage_bps, fee_per_order=fee_per_order,
    )
    result = run_backtest(configuration=configuration, data_provider=clamped, signals=(signal,))
    bars = clamped.bars(
        symbol=signal.symbol, start=configuration.start_date, end=as_of, as_of=cutoff,
    )
    sessions = [bar.session_date for bar in bars]
    last_close = next((bar.close for bar in reversed(bars)), None)
    return _per_share_outcome(result, signal, sessions, last_close)


def build_outcome_record(
    row, *, as_of: date, state: str, details: dict, slippage_bps: Decimal, fee_per_order: Decimal, snapshot_hash: str,
) -> ShadowOutcomeRecord:
    unhashed = ShadowOutcomeRecord(
        record_hash="", outcome_id=f"{row['strategy_evaluation_id']}|{as_of.isoformat()}",
        strategy_evaluation_id=row["strategy_evaluation_id"], signal_content_id=row["signal_content_id"],
        strategy_id=row["strategy_id"], strategy_version=row["strategy_version"], symbol=row["symbol"],
        signal_session=row["session_date"], as_of_session=as_of.isoformat(),
        row_kind="TERMINAL" if state in TERMINAL_STATES else "PROGRESS", state=state,
        reject_reason=details.get("reject_reason"), entry_session=details.get("entry_session"),
        entry_price=_text(details.get("entry_price")), exit_session=details.get("exit_session"),
        exit_price=_text(details.get("exit_price")), exit_reason=details.get("exit_reason"),
        last_close=_text(details.get("last_close")), holding_sessions=details.get("holding_sessions"),
        net_pnl_per_share=_text(details.get("net_pnl_per_share")), return_fraction=_text(details.get("return_fraction")),
        slippage_bps=str(slippage_bps), fee_per_order=str(fee_per_order), snapshot_hash=snapshot_hash,
    )
    content = asdict(unhashed)
    del content["record_hash"]
    return replace(unhashed, record_hash=hash_config(content))


def run_shadow_update(
    *, as_of: date, data_source: str, snapshot_path: Path | str | None, conn, strategy_id: str | None = None,
    slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS, fee_per_order: Decimal = Decimal("0"), adjustment: str = "split",
    strategy_config_path: Path | str | None = None, env: Mapping[str, str] | None = None, now: datetime | None = None,
) -> dict:
    if data_source not in DATA_SOURCES:
        raise BacktestError(f"unknown data source {data_source!r}; expected one of {DATA_SOURCES}")
    config = load_strategy_config(strategy_config_path)
    if ACTIVATION_STAGES.index(config.activation_stage) < ACTIVATION_STAGES.index(MINIMUM_ACTIVATION_STAGE):
        raise BacktestError(
            f"activation_stage is {config.activation_stage}; shadow tracking needs {MINIMUM_ACTIVATION_STAGE} "
            "or later. Only the operator moves the stage, by a reviewed change to config/strategies.yaml."
        )
    clock = now or datetime.now(timezone.utc)
    try:
        close = regular_session_close(as_of)
    except Exception as exc:
        raise BacktestError(f"{as_of} is not a trading session: {exc}") from exc
    if close > clock:
        raise BacktestError(f"session {as_of} has not closed yet (closes {close.isoformat()})")

    rows = list_unresolved_eligible_signals(conn, as_of_session=as_of.isoformat(), strategy_id=strategy_id)
    summary: dict = {
        "as_of": as_of.isoformat(), "activation_stage": config.activation_stage, "unresolved_signals": len(rows),
        "states": {}, "newly_terminal": 0, "recorded": 0, "already_recorded": 0,
        "slippage_bps": str(slippage_bps), "fee_per_order": str(fee_per_order), "caveats": list(CAVEATS),
    }
    if not rows:
        return summary

    symbols = tuple(sorted({row["symbol"] for row in rows}))
    first_session = min(date.fromisoformat(row["session_date"]) for row in rows)
    snapshot = acquire_snapshot(
        data_source=data_source, snapshot_path=snapshot_path, symbols=symbols, start=first_session, end=as_of,
        adjustment=adjustment, env=env, now=clock,
    )
    provider = AlpacaHistoricalDataProvider(snapshot)
    counts: Counter[str] = Counter()
    with transaction(conn):
        for row in rows:
            state, details = evaluate_signal_outcome(
                row, provider=provider, as_of=as_of, slippage_bps=slippage_bps, fee_per_order=fee_per_order,
            )
            record = build_outcome_record(
                row, as_of=as_of, state=state, details=details, slippage_bps=slippage_bps,
                fee_per_order=fee_per_order, snapshot_hash=snapshot.content_hash,
            )
            added = insert_shadow_outcome(conn, record, recorded_at=clock.isoformat())
            counts[state] += 1
            summary["recorded"] += int(added)
            summary["already_recorded"] += int(not added)
            summary["newly_terminal"] += int(added and state in TERMINAL_STATES)
    summary["states"] = dict(sorted(counts.items()))
    summary["snapshot_hash"] = snapshot.content_hash
    return summary
