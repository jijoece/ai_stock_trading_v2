"""Per-strategy operating controls (docs/automated-live-trading-implementation-plan.md Phase 2).

`config/strategies.yaml` keeps one global `activation_stage`. These controls let each strategy carry
its own mode, stage, symbol allowlist, budgets and signal validity window, so strategies can be
enabled one at a time. They are read by the paper bridge only; nothing here sends an order.

The global stage stays the ceiling: no strategy may be configured past it. `live` is a recognised
mode but is refused by the loader while `LIVE_MODE_AVAILABLE` is false (ADR 0010 is not accepted and
the repository is paper only).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..evaluation.market_calendar import add_trading_days, regular_session_close
from ..hashing import hash_config

STRATEGY_MODE_BACKTEST = "backtest"
STRATEGY_MODE_SHADOW = "shadow"
STRATEGY_MODE_PAPER = "paper"
STRATEGY_MODE_LIVE = "live"
STRATEGY_MODES = (STRATEGY_MODE_BACKTEST, STRATEGY_MODE_SHADOW, STRATEGY_MODE_PAPER, STRATEGY_MODE_LIVE)

# Flipping this is a reviewed code change that follows acceptance of ADR 0010, never a config edit.
LIVE_MODE_AVAILABLE = False

# A strategy needs at least this stage before its signals may reach the local paper book.
MINIMUM_PAPER_STAGE = "STAGE_5_LOCAL_PAPER_BOOK"

DEFAULT_SIGNAL_VALIDITY_SESSIONS = 1

KNOWN_STRATEGY_IDS = ("momentum_breakout", "mean_reversion", "event_catalyst")


class StrategyControlsError(ValueError):
    """A strategy's operating controls are invalid."""


def _canonical_decimal(value: Decimal | None) -> str | None:
    """Fixed-point text with no trailing zeros, so 5000 and 5000.00 are one setting."""
    return None if value is None else format(value.normalize(), "f")


@dataclass(frozen=True)
class StrategyControls:
    """What one strategy may do.

    `symbol_allowlist` empty means no symbol restriction. A budget of None means "no strategy-specific
    budget"; the book-level limits still apply.

    `capital_budget_usd` caps the notional the strategy may hold plus have pending. `risk_budget_fraction`
    caps its planned open risk, `shares x (entry - stop)`, as a fraction of book equity.
    """

    strategy_id: str
    mode: str
    activation_stage: str
    symbol_allowlist: tuple[str, ...] = ()
    capital_budget_usd: Decimal | None = None
    risk_budget_fraction: Decimal | None = None
    signal_validity_sessions: int = DEFAULT_SIGNAL_VALIDITY_SESSIONS

    def __post_init__(self) -> None:
        if self.mode not in STRATEGY_MODES:
            raise StrategyControlsError(f"{self.strategy_id}: mode {self.mode!r} not one of {STRATEGY_MODES}")
        if self.mode == STRATEGY_MODE_LIVE and not LIVE_MODE_AVAILABLE:
            raise StrategyControlsError(
                f"{self.strategy_id}: mode 'live' is not available; ADR 0010 is not accepted and the "
                "repository is paper only"
            )
        if self.capital_budget_usd is not None and self.capital_budget_usd <= 0:
            raise StrategyControlsError(f"{self.strategy_id}: capital_budget_usd must be > 0")
        if self.risk_budget_fraction is not None and not (Decimal("0") < self.risk_budget_fraction <= Decimal("1")):
            raise StrategyControlsError(f"{self.strategy_id}: risk_budget_fraction must be within (0, 1]")
        if self.signal_validity_sessions < 1:
            raise StrategyControlsError(f"{self.strategy_id}: signal_validity_sessions must be >= 1")
        if len(set(self.symbol_allowlist)) != len(self.symbol_allowlist):
            raise StrategyControlsError(f"{self.strategy_id}: symbol_allowlist has duplicates")
        for symbol in self.symbol_allowlist:
            if not symbol or symbol != symbol.strip().upper():
                raise StrategyControlsError(f"{self.strategy_id}: symbol {symbol!r} must be upper-case and non-empty")

    def allows_symbol(self, symbol: str) -> bool:
        return not self.symbol_allowlist or symbol in self.symbol_allowlist

    def audit_record(self) -> dict:
        """Every setting that can change a decision, in a canonical form, so it can be stored and re-hashed.

        The allowlist is sorted because its order carries no meaning."""
        return {
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "activation_stage": self.activation_stage,
            "symbol_allowlist": sorted(self.symbol_allowlist),
            "capital_budget_usd": _canonical_decimal(self.capital_budget_usd),
            "risk_budget_fraction": _canonical_decimal(self.risk_budget_fraction),
            "signal_validity_sessions": self.signal_validity_sessions,
        }

    @property
    def controls_hash(self) -> str:
        """Identity of the effective controls. Changing any setting changes it."""
        return hash_config(self.audit_record())


def signal_expires_at(session_date: date, validity_sessions: int) -> datetime:
    """The close of the `validity_sessions`-th session after the signal's own session.

    A daily-bar signal is produced after a session closes and is acted on from the next session, so a
    window of 1 means "valid until the close of the next session". Derived from persisted fields only
    (the stored `session_date` and the config), so a replay reproduces it."""
    if validity_sessions < 1:
        raise StrategyControlsError("validity_sessions must be >= 1")
    return regular_session_close(add_trading_days(session_date, validity_sessions))
