"""Load and validate config/strategies.yaml (Milestone 23, B1-B6).

Same load-and-validate-strictly pattern as `analysis/screener.py` and
`execution/config.py`: a frozen dataclass with `__post_init__` checks, a
`config_hash` over the whole file for audit/reproducibility, and a
per-strategy `configuration_hash` (hash of just that strategy's sub-dict)
so a `StrategySignal.configuration_hash` is traceable without recomputing
the whole file's hash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from ..hashing import hash_config
from .controls import (
    DEFAULT_SIGNAL_VALIDITY_SESSIONS,
    KNOWN_STRATEGY_IDS,
    STRATEGY_MODE_PAPER,
    StrategyControls,
    StrategyControlsError,
)

DEFAULT_STRATEGY_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "strategies.yaml"

# B9 activation progression. Each stage must be passed, in order, before the
# next is entered; `strategy_candidate_selection.activation_stage` in
# config/strategies.yaml records where the system currently stands.
ACTIVATION_STAGES = (
    "STAGE_1_OFFLINE_FIXTURES",
    "STAGE_2_HISTORICAL_BACKTEST",
    "STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST",
    "STAGE_4_SHADOW_RECOMMENDATION_TRACKING",
    "STAGE_5_LOCAL_PAPER_BOOK",
    "STAGE_6_SUPERVISED_ALPACA_PAPER",
    "STAGE_7_MULTI_DAY_PAPER_SOAK",
)


class StrategyConfigError(RuntimeError):
    """The strategy configuration is missing, malformed, or out of range."""


@dataclass(frozen=True)
class MomentumBreakoutConfig:
    enabled: bool
    breakout_lookback_days: int
    trend_sma_days: int
    trend_slope_lookback_days: int
    volume_lookback_days: int
    minimum_volume_ratio: float
    minimum_relative_strength: float
    maximum_breakout_extension_percent: float
    atr_period: int
    atr_stop_multiple: float
    maximum_holding_days: int
    configuration_hash: str

    def __post_init__(self) -> None:
        for name in (
            "breakout_lookback_days", "trend_sma_days", "trend_slope_lookback_days",
            "volume_lookback_days", "minimum_volume_ratio", "maximum_breakout_extension_percent",
            "atr_period", "atr_stop_multiple", "maximum_holding_days",
        ):
            if getattr(self, name) <= 0:
                raise StrategyConfigError(f"momentum_breakout.{name} must be > 0")


@dataclass(frozen=True)
class MeanReversionConfig:
    enabled: bool
    mean_lookback_days: int
    zscore_entry_threshold: float
    rsi_period: int
    maximum_entry_rsi: float
    long_trend_sma_days: int
    require_price_above_long_trend: bool
    atr_period: int
    atr_stop_multiple: float
    maximum_holding_days: int
    configuration_hash: str

    def __post_init__(self) -> None:
        for name in ("mean_lookback_days", "rsi_period", "maximum_entry_rsi",
                     "long_trend_sma_days", "atr_period", "atr_stop_multiple", "maximum_holding_days"):
            if getattr(self, name) <= 0:
                raise StrategyConfigError(f"mean_reversion.{name} must be > 0")
        if self.zscore_entry_threshold >= 0:
            raise StrategyConfigError("mean_reversion.zscore_entry_threshold must be negative")


@dataclass(frozen=True)
class EventCatalystConfig:
    enabled: bool
    maximum_event_age_hours: float
    minimum_volume_ratio: float
    volume_lookback_days: int
    maximum_gap_percent: float
    confirmation_window_days: int
    maximum_holding_days: int
    minimum_positive_response_percent: float
    atr_period: int
    atr_stop_multiple: float
    configuration_hash: str

    def __post_init__(self) -> None:
        for name in ("maximum_event_age_hours", "minimum_volume_ratio", "volume_lookback_days",
                     "maximum_gap_percent", "confirmation_window_days", "maximum_holding_days",
                     "minimum_positive_response_percent", "atr_period", "atr_stop_multiple"):
            if getattr(self, name) <= 0:
                raise StrategyConfigError(f"event_catalyst.{name} must be > 0")


@dataclass(frozen=True)
class ShortlistConfig:
    """Research prefilter limits.

    `maximum_symbol_allocation_fraction` is intentionally conservative and
    must not be interpreted as the authoritative execution-risk cap. The
    tracked 8% value is below the standing 10% paper-book concentration cap;
    final risk validation remains authoritative.
    """
    maximum_candidates_per_strategy: int
    maximum_combined_shortlist: int
    maximum_symbols_to_research_per_day: int
    maximum_fresh_research_cycles_per_day: int
    minimum_signal_strength_for_research: float
    maximum_symbol_allocation_fraction: float
    minimum_candidate_allocation_fraction: float
    configuration_hash: str

    def __post_init__(self) -> None:
        for name in ("maximum_candidates_per_strategy", "maximum_combined_shortlist",
                     "maximum_symbols_to_research_per_day", "maximum_fresh_research_cycles_per_day"):
            if getattr(self, name) <= 0:
                raise StrategyConfigError(f"shortlist.{name} must be > 0")
        if not (0.0 <= self.minimum_signal_strength_for_research <= 1.0):
            raise StrategyConfigError("shortlist.minimum_signal_strength_for_research must be within [0.0, 1.0]")
        if not (0.0 < self.maximum_symbol_allocation_fraction <= 1.0):
            raise StrategyConfigError("shortlist.maximum_symbol_allocation_fraction must be within (0.0, 1.0]")
        if not (0.0 < self.minimum_candidate_allocation_fraction <= self.maximum_symbol_allocation_fraction):
            raise StrategyConfigError(
                "shortlist.minimum_candidate_allocation_fraction must be within "
                "(0.0, maximum_symbol_allocation_fraction]"
            )


@dataclass(frozen=True)
class StrategyConfig:
    version: int
    strategy_candidate_selection_enabled: bool
    activation_stage: str
    momentum_breakout: MomentumBreakoutConfig
    mean_reversion: MeanReversionConfig
    event_catalyst: EventCatalystConfig
    shortlist: ShortlistConfig
    config_hash: str
    raw: dict
    # One entry per known strategy; a strategy with no `strategy_controls` entry gets the defaults
    # (paper mode, the global stage, no allowlist, no budgets, one session of signal validity).
    controls: dict[str, StrategyControls] = field(default_factory=dict)

    def controls_for(self, strategy_id: str) -> StrategyControls:
        try:
            return self.controls[strategy_id]
        except KeyError:
            raise StrategyConfigError(f"no strategy controls for {strategy_id!r}") from None


_CONTROL_KEYS = {
    "mode", "activation_stage", "symbol_allowlist", "capital_budget_usd", "risk_budget_fraction",
    "signal_validity_sessions",
}


def _optional_decimal(value: object, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise StrategyConfigError(f"{name} must be a number")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise StrategyConfigError(f"{name} must be a number") from exc
    if not parsed.is_finite():
        raise StrategyConfigError(f"{name} must be finite")
    return parsed


def _parse_controls(raw: dict, global_stage: str) -> dict[str, StrategyControls]:
    """Build the per-strategy controls from the optional `strategy_controls` section.

    A strategy may not be configured past the global stage, which stays the ceiling for every strategy."""
    # Only an absent or null section/entry means "use the defaults"; any other non-mapping value (including
    # falsy ones such as [] or false) is a malformed config and must not fall back to permissive defaults.
    section = raw.get("strategy_controls")
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise StrategyConfigError("strategy_controls must be a mapping of strategy id to settings")
    unknown = sorted(set(section) - set(KNOWN_STRATEGY_IDS))
    if unknown:
        raise StrategyConfigError(f"strategy_controls names unknown strategies: {unknown}")
    controls: dict[str, StrategyControls] = {}
    for strategy_id in KNOWN_STRATEGY_IDS:
        entry = section.get(strategy_id)
        if entry is None:
            entry = {}
        if not isinstance(entry, dict):
            raise StrategyConfigError(f"strategy_controls.{strategy_id} must be a mapping")
        extra = sorted(set(entry) - _CONTROL_KEYS)
        if extra:
            raise StrategyConfigError(f"strategy_controls.{strategy_id} has unknown keys: {extra}")
        stage = entry.get("activation_stage", global_stage)
        if stage not in ACTIVATION_STAGES:
            raise StrategyConfigError(f"strategy_controls.{strategy_id}.activation_stage {stage!r} is not a known stage")
        if ACTIVATION_STAGES.index(stage) > ACTIVATION_STAGES.index(global_stage):
            raise StrategyConfigError(
                f"strategy_controls.{strategy_id}.activation_stage {stage} is past the global stage {global_stage}; "
                "the global stage is the ceiling"
            )
        allowlist = entry.get("symbol_allowlist")
        if allowlist is None:
            allowlist = []
        if not isinstance(allowlist, list) or not all(isinstance(item, str) for item in allowlist):
            raise StrategyConfigError(f"strategy_controls.{strategy_id}.symbol_allowlist must be a list of symbols")
        validity = entry.get("signal_validity_sessions", DEFAULT_SIGNAL_VALIDITY_SESSIONS)
        if isinstance(validity, bool) or not isinstance(validity, int):
            raise StrategyConfigError(f"strategy_controls.{strategy_id}.signal_validity_sessions must be an integer")
        try:
            controls[strategy_id] = StrategyControls(
                strategy_id=strategy_id, mode=str(entry.get("mode", STRATEGY_MODE_PAPER)), activation_stage=stage,
                symbol_allowlist=tuple(allowlist),
                capital_budget_usd=_optional_decimal(
                    entry.get("capital_budget_usd"), f"strategy_controls.{strategy_id}.capital_budget_usd"),
                risk_budget_fraction=_optional_decimal(
                    entry.get("risk_budget_fraction"), f"strategy_controls.{strategy_id}.risk_budget_fraction"),
                signal_validity_sessions=validity,
            )
        except StrategyControlsError as exc:
            raise StrategyConfigError(str(exc)) from exc
    return controls


def _sub_hash(raw: dict, key: str) -> str:
    return hash_config({key: raw[key]})


def load_strategy_config(path: str | Path | None = None) -> StrategyConfig:
    """Load and validate config/strategies.yaml. Fails closed on any problem."""
    config_path = Path(path) if path else DEFAULT_STRATEGY_CONFIG_PATH
    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except OSError as exc:
        raise StrategyConfigError(f"cannot read strategy config at {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise StrategyConfigError(f"invalid YAML in strategy config at {config_path}: {exc}") from exc

    required_top = {"version", "strategy_candidate_selection", "momentum_breakout",
                     "mean_reversion", "event_catalyst", "shortlist"}
    missing = required_top - raw.keys()
    if missing:
        raise StrategyConfigError(f"strategy config missing keys: {sorted(missing)}")

    selection = raw["strategy_candidate_selection"] or {}
    if "enabled" not in selection:
        raise StrategyConfigError("strategy config strategy_candidate_selection missing key: 'enabled'")
    if "activation_stage" not in selection:
        raise StrategyConfigError("strategy config strategy_candidate_selection missing key: 'activation_stage'")
    if selection["activation_stage"] not in ACTIVATION_STAGES:
        raise StrategyConfigError(
            f"strategy config activation_stage {selection['activation_stage']!r} not one of {ACTIVATION_STAGES}"
        )
    if bool(selection["enabled"]) and selection["activation_stage"] in (
        "STAGE_1_OFFLINE_FIXTURES", "STAGE_2_HISTORICAL_BACKTEST",
    ):
        raise StrategyConfigError(
            "strategy_candidate_selection cannot be enabled while activation_stage is still "
            "offline-fixtures or historical-backtest only"
        )

    mb = raw["momentum_breakout"] or {}
    required_mb = {"enabled", "breakout_lookback_days", "trend_sma_days", "trend_slope_lookback_days",
                   "volume_lookback_days", "minimum_volume_ratio", "minimum_relative_strength",
                   "maximum_breakout_extension_percent", "atr_period", "atr_stop_multiple",
                   "maximum_holding_days"}
    missing_mb = required_mb - mb.keys()
    if missing_mb:
        raise StrategyConfigError(f"strategy config momentum_breakout missing keys: {sorted(missing_mb)}")

    mr = raw["mean_reversion"] or {}
    required_mr = {"enabled", "mean_lookback_days", "zscore_entry_threshold", "rsi_period",
                   "maximum_entry_rsi", "long_trend_sma_days", "require_price_above_long_trend",
                   "atr_period", "atr_stop_multiple", "maximum_holding_days"}
    missing_mr = required_mr - mr.keys()
    if missing_mr:
        raise StrategyConfigError(f"strategy config mean_reversion missing keys: {sorted(missing_mr)}")

    ec = raw["event_catalyst"] or {}
    required_ec = {"enabled", "maximum_event_age_hours", "minimum_volume_ratio", "volume_lookback_days",
                   "maximum_gap_percent", "confirmation_window_days", "maximum_holding_days",
                   "minimum_positive_response_percent", "atr_period", "atr_stop_multiple"}
    missing_ec = required_ec - ec.keys()
    if missing_ec:
        raise StrategyConfigError(f"strategy config event_catalyst missing keys: {sorted(missing_ec)}")

    sl = raw["shortlist"] or {}
    required_sl = {"maximum_candidates_per_strategy", "maximum_combined_shortlist",
                   "maximum_symbols_to_research_per_day", "maximum_fresh_research_cycles_per_day",
                   "minimum_signal_strength_for_research", "maximum_symbol_allocation_fraction",
                   "minimum_candidate_allocation_fraction"}
    missing_sl = required_sl - sl.keys()
    if missing_sl:
        raise StrategyConfigError(f"strategy config shortlist missing keys: {sorted(missing_sl)}")

    return StrategyConfig(
        version=raw["version"],
        strategy_candidate_selection_enabled=bool(selection["enabled"]),
        activation_stage=selection["activation_stage"],
        momentum_breakout=MomentumBreakoutConfig(
            enabled=bool(mb["enabled"]),
            breakout_lookback_days=int(mb["breakout_lookback_days"]),
            trend_sma_days=int(mb["trend_sma_days"]),
            trend_slope_lookback_days=int(mb["trend_slope_lookback_days"]),
            volume_lookback_days=int(mb["volume_lookback_days"]),
            minimum_volume_ratio=float(mb["minimum_volume_ratio"]),
            minimum_relative_strength=float(mb["minimum_relative_strength"]),
            maximum_breakout_extension_percent=float(mb["maximum_breakout_extension_percent"]),
            atr_period=int(mb["atr_period"]),
            atr_stop_multiple=float(mb["atr_stop_multiple"]),
            maximum_holding_days=int(mb["maximum_holding_days"]),
            configuration_hash=_sub_hash(raw, "momentum_breakout"),
        ),
        mean_reversion=MeanReversionConfig(
            enabled=bool(mr["enabled"]),
            mean_lookback_days=int(mr["mean_lookback_days"]),
            zscore_entry_threshold=float(mr["zscore_entry_threshold"]),
            rsi_period=int(mr["rsi_period"]),
            maximum_entry_rsi=float(mr["maximum_entry_rsi"]),
            long_trend_sma_days=int(mr["long_trend_sma_days"]),
            require_price_above_long_trend=bool(mr["require_price_above_long_trend"]),
            atr_period=int(mr["atr_period"]),
            atr_stop_multiple=float(mr["atr_stop_multiple"]),
            maximum_holding_days=int(mr["maximum_holding_days"]),
            configuration_hash=_sub_hash(raw, "mean_reversion"),
        ),
        event_catalyst=EventCatalystConfig(
            enabled=bool(ec["enabled"]),
            maximum_event_age_hours=float(ec["maximum_event_age_hours"]),
            minimum_volume_ratio=float(ec["minimum_volume_ratio"]),
            volume_lookback_days=int(ec["volume_lookback_days"]),
            maximum_gap_percent=float(ec["maximum_gap_percent"]),
            confirmation_window_days=int(ec["confirmation_window_days"]),
            maximum_holding_days=int(ec["maximum_holding_days"]),
            minimum_positive_response_percent=float(ec["minimum_positive_response_percent"]),
            atr_period=int(ec["atr_period"]),
            atr_stop_multiple=float(ec["atr_stop_multiple"]),
            configuration_hash=_sub_hash(raw, "event_catalyst"),
        ),
        shortlist=ShortlistConfig(
            maximum_candidates_per_strategy=int(sl["maximum_candidates_per_strategy"]),
            maximum_combined_shortlist=int(sl["maximum_combined_shortlist"]),
            maximum_symbols_to_research_per_day=int(sl["maximum_symbols_to_research_per_day"]),
            maximum_fresh_research_cycles_per_day=int(sl["maximum_fresh_research_cycles_per_day"]),
            minimum_signal_strength_for_research=float(sl["minimum_signal_strength_for_research"]),
            maximum_symbol_allocation_fraction=float(sl["maximum_symbol_allocation_fraction"]),
            minimum_candidate_allocation_fraction=float(sl["minimum_candidate_allocation_fraction"]),
            configuration_hash=_sub_hash(raw, "shortlist"),
        ),
        config_hash=hash_config(raw),
        raw=raw,
        controls=_parse_controls(raw, selection["activation_stage"]),
    )
