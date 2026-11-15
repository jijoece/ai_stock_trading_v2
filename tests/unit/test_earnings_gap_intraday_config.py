import ast
from datetime import time
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.strategies.config import (
    DEFAULT_STRATEGY_CONFIG_PATH,
    StrategyConfigError,
    load_earnings_gap_intraday_config,
    load_strategy_config,
)
from trading_research.strategies.controls import (
    BACKTEST_ONLY_STRATEGY_IDS,
    KNOWN_STRATEGY_IDS,
    STRATEGY_MODE_BACKTEST,
)
from trading_research.strategies.earnings_gap_intraday import EarningsGapIntradayConfig

SECTION = "earnings_gap_intraday"


def _text() -> str:
    return DEFAULT_STRATEGY_CONFIG_PATH.read_text()


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "strategies.yaml"
    path.write_text(text)
    return path


def _without_section(text: str) -> str:
    start = text.index("\n# Intraday earnings-gap research strategy")
    return text[:start] + "\n"


def test_shipped_config_loads_every_plan_default():
    cfg = load_earnings_gap_intraday_config()
    assert cfg == EarningsGapIntradayConfig()  # yaml and dataclass defaults agree
    assert cfg.minimum_price == Decimal(3) and cfg.post_signal_time == time(16, 30)
    assert cfg.early_close_postmarket_end == time(17, 0)
    assert load_strategy_config().earnings_gap_intraday == cfg


def test_strategy_config_loads_when_the_section_is_absent(tmp_path):
    text = _without_section(_text()).replace("  # Research-only (docs/intraday", "  # (docs/intraday")
    config = load_strategy_config(_write(tmp_path, text))
    assert config.earnings_gap_intraday is None
    assert set(config.controls) == set(KNOWN_STRATEGY_IDS)
    with pytest.raises(StrategyConfigError, match="no earnings_gap_intraday section"):
        load_earnings_gap_intraday_config(_write(tmp_path, text))


def test_duplicate_yaml_keys_are_rejected_in_both_loading_paths(tmp_path):
    text = _text().replace("  risk_fraction:", "  risk_fraction: 0.5\n  risk_fraction:", 1)
    assert text != _text()
    path = _write(tmp_path, text)
    with pytest.raises(StrategyConfigError, match="duplicate mapping key"):
        load_strategy_config(path)
    with pytest.raises(StrategyConfigError, match="duplicate mapping key"):
        load_earnings_gap_intraday_config(path)
    section_twice = _text() + f"\n{SECTION}:\n  risk_fraction: 0.005\n"
    with pytest.raises(StrategyConfigError, match="duplicate mapping key"):
        load_strategy_config(_write(tmp_path, section_twice))


def test_the_new_id_is_not_a_paper_capable_strategy():
    assert SECTION in BACKTEST_ONLY_STRATEGY_IDS
    assert SECTION not in KNOWN_STRATEGY_IDS
    config = load_strategy_config()
    # config.controls is the only thing the paper bridge / paper cycle consume
    assert SECTION not in config.controls
    assert config.backtest_only_controls[SECTION].mode == STRATEGY_MODE_BACKTEST
    with pytest.raises(StrategyConfigError):
        config.controls_for(SECTION)


def test_global_activation_stage_and_event_catalyst_are_unchanged():
    config = load_strategy_config()
    assert config.activation_stage == "STAGE_2_HISTORICAL_BACKTEST"
    assert config.event_catalyst.maximum_gap_percent == 8.0
    assert config.controls["event_catalyst"].mode == "paper"


@pytest.mark.parametrize("mode", ["paper", "shadow", "live", "Paper", "bogus", "null_string"])
def test_loader_refuses_every_mode_but_backtest(tmp_path, mode):
    text = _text().replace(
        "  earnings_gap_intraday:\n    mode: backtest", f"  earnings_gap_intraday:\n    mode: {mode}", 1
    )
    assert f"mode: {mode}" in text
    path = _write(tmp_path, text)
    with pytest.raises(StrategyConfigError, match="research-only"):
        load_strategy_config(path)
    with pytest.raises(StrategyConfigError, match="research-only"):
        load_earnings_gap_intraday_config(path)


def test_loader_refuses_paper_mode_even_with_a_paper_stage(tmp_path):
    text = _text().replace("activation_stage: STAGE_2_HISTORICAL_BACKTEST", "activation_stage: STAGE_5_LOCAL_PAPER_BOOK", 1)
    text = text.replace("  earnings_gap_intraday:\n    mode: backtest", "  earnings_gap_intraday:\n    mode: paper", 1)
    with pytest.raises(StrategyConfigError):
        load_strategy_config(_write(tmp_path, text))


def test_mode_defaults_to_backtest_when_the_entry_is_absent(tmp_path):
    text = _text().replace("  earnings_gap_intraday:\n    mode: backtest\n", "", 1)
    config = load_strategy_config(_write(tmp_path, text))
    assert config.backtest_only_controls[SECTION].mode == "backtest"


def test_controls_entry_takes_only_mode(tmp_path):
    text = _text().replace(
        "  earnings_gap_intraday:\n    mode: backtest",
        "  earnings_gap_intraday:\n    mode: backtest\n    symbol_allowlist: [AAA]", 1,
    )
    with pytest.raises(StrategyConfigError, match="unknown keys"):
        load_strategy_config(_write(tmp_path, text))


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("  minimum_price: 3\n", "  minimum_price: 3\n  surprise_key: 1\n", "unknown keys"),
        ("  minimum_price: 3\n", "  minimum_price: abc\n", "number"),
        ("  minimum_price: 3\n", "  minimum_price: -1\n", "positive"),
        ("  post_signal_time: \"16:30\"", "  post_signal_time: 16:30", "quoted"),
        ("  post_signal_time: \"16:30\"", "  post_signal_time: \"25:99\"", "quoted"),
        ("  post_market_exit: false", "  post_market_exit: 0", "true or false"),
        ("  maximum_trades_per_day: 3", "  maximum_trades_per_day: 2.5", "integer"),
        ("  opening_range_minutes: 15", "  opening_range_minutes: 10", "opening_range_minutes"),
    ],
)
def test_malformed_values_fail_closed(tmp_path, old, new, match):
    text = _text()
    assert old in text
    with pytest.raises(StrategyConfigError, match=match):
        load_earnings_gap_intraday_config(_write(tmp_path, text.replace(old, new, 1)))


def test_section_must_be_a_mapping(tmp_path):
    text = _without_section(_text()) + "earnings_gap_intraday: [1, 2]\n"
    with pytest.raises(StrategyConfigError, match="mapping"):
        load_earnings_gap_intraday_config(_write(tmp_path, text))


def test_changing_a_threshold_changes_the_config_and_file_hashes(tmp_path):
    base = load_strategy_config()
    text = _text().replace("  minimum_gap_percent: 5\n", "  minimum_gap_percent: 6\n", 1)
    other = load_strategy_config(_write(tmp_path, text))
    assert other.earnings_gap_intraday is not None and base.earnings_gap_intraday is not None
    assert other.earnings_gap_intraday.configuration_hash != base.earnings_gap_intraday.configuration_hash
    assert other.config_hash != base.config_hash


def test_strategy_modules_do_not_import_the_paper_path():
    """The new strategy modules stay clear of the paper bridge, shortlist and selector."""
    root = Path(__file__).resolve().parents[2] / "src" / "trading_research" / "strategies"
    for name in ("earnings_gap_intraday.py", "extended_hours_signals.py"):
        tree = ast.parse((root / name).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for module in imported:
            assert not any(bad in module for bad in ("paper_books", "selector", "shortlist", "execution", "forward_testing")), (name, module)
