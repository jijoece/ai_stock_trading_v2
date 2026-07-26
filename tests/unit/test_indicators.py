"""Library-migration PR 4: `scripts/indicators.py` TA-Lib parity coverage.

Golden expected values below were captured from the pre-migration
hand-written EMA/RSI/MACD/TRIX/Bollinger implementation, reviewed, and are
recorded here as literals -- the removed implementation is never called at
test time (see docs/library-migration/STATUS.md for the full parity
record, including the one documented intentional semantic difference:
flat-price RSI).

TA-Lib is authoritative for the EMA, RSI, and BBANDS primitives; MACD and
TRIX are composed from TA-Lib's EMA primitive in a thin custom adapter
that preserves this stack's public-contract semantics -- see
docs/library-migration/STATUS.md for the exact authority boundary. The
"Behavioral proof of TA-Lib authority" section below proves this with
spies on the real `talib` calls, not a source-text substring check.

`scripts/` has no `__init__.py` (standalone CLI scripts, per the
`run-agentic-trading-desk` skill convention), so the module under test is
loaded directly from its file path, following the same convention as
`tests/unit/test_macro_pillar_market_data_shape.py`.

The `indicators` extra (TA-Lib) is not part of `.[dev]`, so the
`I` fixture skips (not fails) when TA-Lib is unavailable -- this keeps the
ordinary `pytest tests/ -q` suite green on a plain `.[dev]` install while
the dedicated indicators-focused CI job (which does install the extra)
runs every test in this file for real.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "indicators.py"


def _load_indicators(module_name: str = "indicators_under_test"):
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def I():
    pytest.importorskip("talib")
    return _load_indicators()


# ---------------------------------------------------------------------
# Deterministic fixture price series
# ---------------------------------------------------------------------

def _increasing(n: int) -> list[float]:
    return [100.0 + i * 0.75 for i in range(n)]


def _decreasing(n: int) -> list[float]:
    return [200.0 - i * 0.6 for i in range(n)]


def _flat(n: int, price: float = 123.45) -> list[float]:
    return [price] * n


def _oscillating(n: int) -> list[float]:
    return [round(100 + 15 * math.sin(i / 7.0), 6) for i in range(n)]


def _long_realistic(n: int = 250) -> list[float]:
    return [round(100 + 18 * math.sin(i / 22) + i * 0.06, 2) for i in range(n)]


# ---------------------------------------------------------------------
# Missing-dependency guard -- must run in every environment, including one
# where TA-Lib truly is not installed (the main-tests CI job).
# ---------------------------------------------------------------------

def test_missing_talib_raises_actionable_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "talib", None)
    with pytest.raises(ImportError, match=r'pip install -e "\.\[indicators\]"'):
        _load_indicators("indicators_missing_talib_probe")


# ---------------------------------------------------------------------
# Golden compute() parity.
#
# Tight tolerance (abs=1e-6) throughout except `bb_upper`/`bb_lower` on an
# exactly-flat, non-exactly-representable price (abs=1e-4): TA-Lib's
# BBANDS computes variance via a single-pass sum-of-squares formula that
# leaves ~1e-6 absolute floating-point noise for a constant window, whereas
# the pre-migration `statistics.pstdev` gave exactly 0.0. Both round
# identically at the `_round()` CLI boundary (4 decimals), and %B still
# resolves to exactly 0.5 there by symmetry -- see
# test_bollinger_percent_b_flat_bands_exact for proof the 0.5 guard is
# reachable exactly, not only approximately, for an exactly-representable
# flat price.
# ---------------------------------------------------------------------

_LOOSE_KEYS = {"bb_upper", "bb_lower"}


def _assert_compute_matches(indicators_module, close: list[float], expected: dict) -> None:
    actual = indicators_module._round(indicators_module.compute(close), 8)
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if isinstance(expected_value, float) and isinstance(actual_value, float):
            tol = 1e-4 if key in _LOOSE_KEYS else 1e-6
            assert actual_value == pytest.approx(expected_value, abs=tol), key
        else:
            assert actual_value == expected_value, key


def test_compute_increasing_prices(I):
    _assert_compute_matches(I, _increasing(40), {
        "n_bars": 40,
        "warning": "Only 40 bars; EMA200/some indicators may be None. Ideal >=220.",
        "close": 129.25,
        "ema20": 122.125, "ema50": None, "ema200": None,
        "ema20_slope": 3.75, "ema50_slope": None, "ema200_slope": None,
        "rsi14": 100.0, "rsi14_prev": 100.0,
        "macd_line": 5.25, "macd_signal": 5.25, "macd_hist": 0.0, "macd_hist_prev": 0.0,
        "trix": None, "trix_prev": None, "trix_signal": None, "trix_signal_prev": None,
        "bars_since_below_ema20": None,
        "bb_mid": 122.125, "bb_upper": 130.77442195, "bb_lower": 113.47557805,
        "percent_b": 0.91187724,
    })


def test_compute_decreasing_prices(I):
    _assert_compute_matches(I, _decreasing(40), {
        "n_bars": 40,
        "warning": "Only 40 bars; EMA200/some indicators may be None. Ideal >=220.",
        "close": 176.6,
        "ema20": 182.3, "ema50": None, "ema200": None,
        "ema20_slope": -3.0, "ema50_slope": None, "ema200_slope": None,
        "rsi14": 0.0, "rsi14_prev": 0.0,
        "macd_line": -4.2, "macd_signal": -4.2, "macd_hist": 0.0, "macd_hist_prev": 0.0,
        "trix": None, "trix_prev": None, "trix_signal": None, "trix_signal_prev": None,
        "bars_since_below_ema20": 0,
        "bb_mid": 182.3, "bb_upper": 189.21953756, "bb_lower": 175.38046244,
        "percent_b": 0.08812276,
    })


def test_compute_flat_prices_rsi_preserves_legacy_100_not_talib_zero(I):
    _assert_compute_matches(I, _flat(40), {
        "n_bars": 40,
        "warning": "Only 40 bars; EMA200/some indicators may be None. Ideal >=220.",
        "close": 123.45,
        "ema20": 123.45, "ema50": None, "ema200": None,
        "ema20_slope": 0.0, "ema50_slope": None, "ema200_slope": None,
        "rsi14": 100.0, "rsi14_prev": 100.0,
        "macd_line": 0.0, "macd_signal": 0.0, "macd_hist": 0.0, "macd_hist_prev": 0.0,
        "trix": None, "trix_prev": None, "trix_signal": None, "trix_signal_prev": None,
        "bars_since_below_ema20": None,
        "bb_mid": 123.45, "bb_upper": 123.45, "bb_lower": 123.45,
        "percent_b": 0.5,
    })


def test_compute_oscillating_prices(I):
    _assert_compute_matches(I, _oscillating(80), {
        "n_bars": 80,
        "warning": "Only 80 bars; EMA200/some indicators may be None. Ideal >=220.",
        "close": 85.626942,
        "ema20": 92.89161511, "ema50": 98.38160049, "ema200": None,
        "ema20_slope": -4.92455541, "ema50_slope": -2.89549071, "ema200_slope": None,
        "rsi14": 19.67379072, "rsi14_prev": 17.32003968,
        "macd_line": -5.41229277, "macd_signal": -4.36153392,
        "macd_hist": -1.05075885, "macd_hist_prev": -1.37511328,
        "trix": -0.60187118, "trix_prev": -0.55114442,
        "trix_signal": -0.32711754, "trix_signal_prev": -0.25842913,
        "bars_since_below_ema20": 0,
        "bb_mid": 94.97802355, "bb_upper": 113.01799382, "bb_lower": 76.93805328,
        "percent_b": 0.24082326,
    })


def test_compute_long_realistic_synthetic_series(I):
    _assert_compute_matches(I, _long_realistic(250), {
        "n_bars": 250, "warning": None,
        "close": 97.87,
        "ema20": 98.03445981, "ema50": 103.05584187, "ema200": 106.91280742,
        "ema20_slope": -0.42002126, "ema50_slope": -1.26173223, "ema200_slope": -0.49066064,
        "rsi14": 31.92120655, "rsi14_prev": 26.04266567,
        "macd_line": -1.6701015, "macd_signal": -2.34174605,
        "macd_hist": 0.67164455, "macd_hist_prev": 0.64738917,
        "trix": -0.33745806, "trix_prev": -0.36110782,
        "trix_signal": -0.41214913, "trix_signal_prev": -0.4308219,
        "bars_since_below_ema20": 0,
        "bb_mid": 97.086, "bb_upper": 98.21019571, "bb_lower": 95.96180429,
        "percent_b": 0.84869373,
    })


def test_compute_below_all_warmup_thresholds(I):
    _assert_compute_matches(I, _oscillating(5), {
        "n_bars": 5,
        "warning": "Only 5 bars; EMA200/some indicators may be None. Ideal >=220.",
        "close": 108.112513,
        "ema20": None, "ema50": None, "ema200": None,
        "ema20_slope": None, "ema50_slope": None, "ema200_slope": None,
        "rsi14": None, "rsi14_prev": None,
        "macd_line": None, "macd_signal": None, "macd_hist": None, "macd_hist_prev": None,
        "trix": None, "trix_prev": None, "trix_signal": None, "trix_signal_prev": None,
        "bars_since_below_ema20": None,
        "bb_mid": None, "bb_upper": None, "bb_lower": None, "percent_b": None,
    })


def test_compute_exactly_at_ema20_threshold(I):
    _assert_compute_matches(I, _increasing(20), {
        "n_bars": 20, "ema20": 107.125, "ema50": None, "ema200": None,
        "rsi14": 100.0,
        "bb_mid": 107.125, "bb_upper": 115.77442195, "bb_lower": 98.47557805,
        "percent_b": 0.91187724,
    })


def test_compute_exactly_at_rsi_threshold(I):
    _assert_compute_matches(I, _oscillating(15), {
        "n_bars": 15, "ema20": None,
        "rsi14": 91.68403621, "rsi14_prev": None,
        "macd_line": None,
        "bb_mid": None,
    })


def test_compute_exactly_at_bollinger_threshold(I):
    _assert_compute_matches(I, _oscillating(20), {
        "n_bars": 20,
        "bb_mid": 110.16629475, "bb_upper": 118.93829128, "bb_lower": 101.39429822,
        "percent_b": 0.27485321,
    })


def test_compute_exactly_at_macd_signal_threshold(I):
    _assert_compute_matches(I, _oscillating(34), {
        "n_bars": 34,
        "macd_line": -7.34822563, "macd_signal": -6.57601573,
        "macd_hist": -0.77220989, "macd_hist_prev": None,
        "trix": None,
    })


def test_compute_exactly_at_trix_line_threshold(I):
    _assert_compute_matches(I, _oscillating(44), {
        "n_bars": 44,
        "trix": -0.73444617, "trix_prev": None,
        "trix_signal": None, "trix_signal_prev": None,
    })


def test_compute_exactly_at_trix_signal_threshold(I):
    _assert_compute_matches(I, _oscillating(52), {
        "n_bars": 52,
        "trix": 0.0887524, "trix_prev": -0.02112886,
        "trix_signal": -0.3421001, "trix_signal_prev": None,
    })


# ---------------------------------------------------------------------
# Documented semantics, tested directly at the primitive level.
# ---------------------------------------------------------------------

def test_ema_seeds_with_sma_and_none_pads_warmup(I):
    values = _increasing(25)
    out = I.ema_series(values, 12)
    assert out[:11] == [None] * 11
    assert out[11] == pytest.approx(sum(values[:12]) / 12, abs=1e-9)
    assert all(v is not None for v in out[11:])


def test_rsi_first_valid_index_unchanged(I):
    out = I.rsi_wilder(_oscillating(30), 14)
    assert out[:14] == [None] * 14
    assert out[14] is not None


def test_rsi_flat_series_is_100_not_talib_default_zero(I):
    out = I.rsi_wilder(_flat(30), 14)
    assert all(v == 100.0 for v in out[14:])


def test_rsi_monotonic_decrease_is_zero(I):
    out = I.rsi_wilder(_decreasing(30), 14)
    assert all(v == 0.0 for v in out[14:])


def test_rsi_flat_prefix_then_movement_only_overrides_the_flat_prefix(I):
    # Flat for the first 20 bars, then a real move -- the legacy-100
    # override must not leak past the point the price actually starts
    # changing.
    close = _flat(20) + _increasing(20)[1:]
    out = I.rsi_wilder(close, 14)
    assert out[19] == 100.0
    assert out[-1] != 100.0 or out[-1] is None


def test_macd_line_available_before_signal_line(I):
    close = _oscillating(40)
    line, signal, hist = I.macd(close, 12, 26, 9)
    first_line = next(i for i, v in enumerate(line) if v is not None)
    first_signal = next(i for i, v in enumerate(signal) if v is not None)
    first_hist = next(i for i, v in enumerate(hist) if v is not None)
    assert first_line == 25  # slow - 1
    assert first_signal == 33  # slow - 1 + signal - 1
    assert first_signal > first_line
    assert first_hist == first_signal


def test_trix_warmup_alignment_scaling_and_signal(I):
    close = _oscillating(80)
    t, s = I.trix(close, 15, 9)
    first_t = next(i for i, v in enumerate(t) if v is not None)
    first_s = next(i for i, v in enumerate(s) if v is not None)
    assert first_t == 43  # 3 * (period - 1) + 1
    assert first_s == 43 + 8  # + (signal - 1)
    assert len(t) == len(close) == len(s)


def test_trix_pct_change_zero_denominator_returns_zero_not_nan_or_inf(I):
    assert I._pct_change(0.0, 0.0) == 0.0
    assert I._pct_change(5.0, 0.0) == 0.0
    assert I._pct_change(110.0, 100.0) == pytest.approx(10.0)
    assert I._pct_change(90.0, 100.0) == pytest.approx(-10.0)


def test_bollinger_percent_b_flat_bands_exact(I):
    # An exactly-representable flat price gives bit-identical upper/lower
    # bands (population stdev exactly 0.0), proving the %B == 0.5 guard is
    # reachable exactly, not only approximately (contrast with the ~1e-6
    # floating noise TA-Lib's BBANDS introduces for a non-exactly-
    # representable flat price such as 123.45, exercised in
    # test_compute_flat_prices_rsi_preserves_legacy_100_not_talib_zero).
    mid, upper, lower, pct_b = I.bollinger(_flat(25, price=100.0), 20, 2.0)
    assert upper == lower == mid == 100.0
    assert pct_b == 0.5


def test_bollinger_below_threshold_returns_all_none(I):
    assert I.bollinger(_flat(19, price=100.0), 20, 2.0) == (None, None, None, None)


def test_round_only_applied_at_cli_boundary_not_internally(I):
    raw = I.compute(_long_realistic(250))
    assert round(raw["ema20"], 8) != round(raw["ema20"], 2)  # real sub-cent precision retained
    assert I._round(raw)["ema20"] == round(raw["ema20"], 4)


# ---------------------------------------------------------------------
# Behavioral proof of TA-Lib authority.
#
# A source-text substring check (the previous version of this section)
# cannot prove TA-Lib is actually invoked, only that the string "talib."
# appears somewhere in the file. These tests spy on the real `talib.*`
# functions (`wraps=` still calls through, so results are unaffected) and
# assert they are actually called -- and, for MACD/TRIX, that the
# corresponding single-shot `talib.MACD()`/`talib.TRIX()` is never called,
# proving the composition-from-EMA-primitives design is what actually
# executes, not a hidden legacy formula.
# ---------------------------------------------------------------------

def test_ema_series_invokes_talib_ema(I):
    with patch.object(I.talib, "EMA", wraps=I.talib.EMA) as spy:
        I.ema_series(_oscillating(30), 12)
    assert spy.called


def test_rsi_wilder_invokes_talib_rsi(I):
    with patch.object(I.talib, "RSI", wraps=I.talib.RSI) as spy:
        I.rsi_wilder(_oscillating(30), 14)
    assert spy.called


def test_bollinger_invokes_talib_bbands(I):
    with patch.object(I.talib, "BBANDS", wraps=I.talib.BBANDS) as spy:
        I.bollinger(_oscillating(30), 20, 2.0)
    assert spy.called


def test_macd_is_composed_from_talib_ema_and_never_calls_talib_macd(I):
    with patch.object(I.talib, "EMA", wraps=I.talib.EMA) as ema_spy, \
         patch.object(I.talib, "MACD", wraps=I.talib.MACD) as macd_spy:
        I.macd(_oscillating(40), 12, 26, 9)
    assert ema_spy.call_count >= 3  # fast EMA, slow EMA, signal EMA
    assert not macd_spy.called


def test_trix_is_composed_from_talib_ema_and_never_calls_talib_trix(I):
    with patch.object(I.talib, "EMA", wraps=I.talib.EMA) as ema_spy, \
         patch.object(I.talib, "TRIX", wraps=I.talib.TRIX) as trix_spy:
        I.trix(_oscillating(80), 15, 9)
    assert ema_spy.call_count >= 4  # three chained EMA passes + signal EMA
    assert not trix_spy.called


# ---------------------------------------------------------------------
# Fail-closed input validation.
#
# `None` in this module's output means "documented warm-up gap". A NaN,
# infinity, boolean, string, or nested value slipping through as though
# it were a price would risk being coerced into that same `None`
# convention, `_strip()`-ed away as "more warm-up", and letting `compute()`
# silently return an older, stale indicator value instead of surfacing the
# corruption. Every case below must raise `IndicatorInputError` instead.
# ---------------------------------------------------------------------

def _base_prices(n: int = 30) -> list[float]:
    return _increasing(n)


def test_nan_at_first_bar_fails_closed(I):
    close = _base_prices()
    close[0] = float("nan")
    with pytest.raises(I.IndicatorInputError, match="close\\[0\\]"):
        I.compute(close)


def test_nan_at_middle_bar_fails_closed(I):
    close = _base_prices()
    close[15] = float("nan")
    with pytest.raises(I.IndicatorInputError, match="close\\[15\\]"):
        I.compute(close)


def test_nan_at_final_bar_fails_closed(I):
    close = _base_prices()
    close[-1] = float("nan")
    with pytest.raises(I.IndicatorInputError, match=r"close\[29\]"):
        I.compute(close)


def test_positive_infinity_fails_closed(I):
    close = _base_prices()
    close[5] = float("inf")
    with pytest.raises(I.IndicatorInputError, match="infinite"):
        I.compute(close)


def test_negative_infinity_fails_closed(I):
    close = _base_prices()
    close[5] = float("-inf")
    with pytest.raises(I.IndicatorInputError, match="infinite"):
        I.compute(close)


def test_empty_input_fails_closed(I):
    with pytest.raises(I.IndicatorInputError, match="at least 1 value"):
        I.compute([])


def test_nested_array_fails_closed(I):
    close = _base_prices()
    close[3] = [1.0, 2.0]  # type: ignore[list-item]
    with pytest.raises(I.IndicatorInputError, match=r"close\[3\]"):
        I.compute(close)


def test_boolean_values_fail_closed(I):
    close = _base_prices()
    close[3] = True  # type: ignore[list-item]
    with pytest.raises(I.IndicatorInputError, match=r"close\[3\]"):
        I.compute(close)


def test_numeric_strings_fail_closed_not_silently_coerced(I):
    close = _base_prices()
    close[3] = "101.5"  # type: ignore[list-item]
    with pytest.raises(I.IndicatorInputError, match=r"close\[3\]"):
        I.compute(close)


def test_invalid_period_zero_fails_closed(I):
    with pytest.raises(I.IndicatorInputError, match="positive integer"):
        I.ema_series(_base_prices(), 0)


def test_invalid_period_negative_fails_closed(I):
    with pytest.raises(I.IndicatorInputError, match="positive integer"):
        I.rsi_wilder(_base_prices(), -1)


def test_invalid_period_non_integer_fails_closed(I):
    with pytest.raises(I.IndicatorInputError, match="positive integer"):
        I.trix(_base_prices(), 15.5, 9)  # type: ignore[arg-type]


def test_macd_fast_greater_than_or_equal_to_slow_fails_closed(I):
    with pytest.raises(I.IndicatorInputError, match="fast < slow"):
        I.macd(_base_prices(), fast=26, slow=12, signal=9)
    with pytest.raises(I.IndicatorInputError, match="fast < slow"):
        I.macd(_base_prices(), fast=12, slow=12, signal=9)


def test_non_warmup_nan_from_a_mocked_talib_call_fails_closed_not_stale(I):
    # Simulates a TA-Lib bug or an unvalidated upstream corruption that
    # slips a NaN past the documented warm-up window. `ema_series` must
    # raise rather than convert it to `None` (which `_strip()` would treat
    # as more warm-up and let a caller silently read an older, stale bar).
    close = _oscillating(30)
    real_ema = I.talib.EMA

    def _inject_mid_array_nan(arr, timeperiod):
        out = real_ema(arr, timeperiod=timeperiod).copy()
        out[20] = np.nan  # well past EMA-12's period-1=11 warm-up
        return out

    with patch.object(I.talib, "EMA", side_effect=_inject_mid_array_nan):
        with pytest.raises(I.IndicatorInputError, match="unexpected NaN"):
            I.ema_series(close, 12)


def test_bollinger_nan_on_last_bar_from_a_mocked_talib_call_fails_closed(I):
    close = _oscillating(30)
    real_bbands = I.talib.BBANDS

    def _inject_nan_last_bar(arr, timeperiod, nbdevup, nbdevdn, matype):
        upper, mid, lower = real_bbands(
            arr, timeperiod=timeperiod, nbdevup=nbdevup, nbdevdn=nbdevdn, matype=matype
        )
        upper, mid, lower = upper.copy(), mid.copy(), lower.copy()
        mid[-1] = np.nan
        return upper, mid, lower

    with patch.object(I.talib, "BBANDS", side_effect=_inject_nan_last_bar):
        with pytest.raises(I.IndicatorInputError, match="NaN"):
            I.bollinger(close, 20, 2.0)


# ---------------------------------------------------------------------
# CLI input-shape validation -- malformed JSON must produce an actionable
# `IndicatorInputError`, never a raw `KeyError`, an opaque NumPy error, a
# stale value, or `NaN`/`Infinity` written into JSON output.
# ---------------------------------------------------------------------

def test_cli_dict_missing_close_key_raises_actionable_error_not_keyerror(I, tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"open": [1, 2, 3]}))
    monkeypatch.setattr(sys, "argv", ["indicators.py", str(path)])
    with pytest.raises(I.IndicatorInputError, match='"close" key'):
        I.main()


def test_cli_non_list_non_dict_root_raises_actionable_error(I, tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps("just a string"))
    monkeypatch.setattr(sys, "argv", ["indicators.py", str(path)])
    with pytest.raises(I.IndicatorInputError, match="must be a list of prices"):
        I.main()


def test_cli_malformed_price_element_raises_actionable_error_and_prints_nothing(I, tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"close": [100.0, "not-a-number", 102.0]}))
    monkeypatch.setattr(sys, "argv", ["indicators.py", str(path)])
    with pytest.raises(I.IndicatorInputError):
        I.main()
    assert capsys.readouterr().out == ""


def test_cli_never_emits_nan_or_infinity_json_even_if_compute_misbehaves(I, monkeypatch, capsys):
    # Defense in depth at the CLI boundary itself: even if `compute()` were
    # to return a non-finite value despite the validation above, the CLI
    # must refuse to serialize it rather than emit invalid `NaN`/`Infinity`
    # tokens into JSON output.
    monkeypatch.setattr(sys, "argv", ["indicators.py"])
    monkeypatch.setattr(I, "compute", lambda close, slope_lookback=5: {"bad": float("nan")})
    with pytest.raises(ValueError):
        I.main()
    assert capsys.readouterr().out == ""
