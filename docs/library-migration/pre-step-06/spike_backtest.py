"""LumiBot 4.5.78 offline-backtest feasibility spike.

Proves (or disproves) each claim the pre-step's architecture decision depends on:

  1. the installed LumiBot version is exactly 4.5.78
  2. PandasDataBacktesting replays a caller-supplied DataFrame
  3. no broker credentials are read
  4. no network connection is attempted (guards fail closed)
  5. no live broker / live data provider is initialized
  6. results are deterministic across repeated runs
  7. all input bars are caller supplied
  8. stdout contamination measured (protocol-corruption risk for a subprocess)

Run: venv-b/bin/python spike_backtest.py <run_label>
Writes JSON evidence to result_<run_label>.json; keeps stdout free for the
contamination measurement.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

import guards

# --- guards installed BEFORE lumibot is imported -------------------------
# SPIKE_CREDS=present -> seed sentinel broker credentials (models a developer
# machine / CI runner that already holds Alpaca paper credentials).
# SPIKE_CREDS=absent  -> scrub every known broker-credential variable first.
SPIKE_CREDS = os.environ.get("SPIKE_CREDS", "present")
if SPIKE_CREDS == "present":
    for key, value in guards.CREDENTIAL_SENTINELS.items():
        os.environ[key] = value
else:
    for key in list(os.environ):
        upper = key.upper()
        if any(marker in upper for marker in guards.CREDENTIAL_KEY_MARKERS):
            del os.environ[key]
guards.install_env_tracer()
guards.install_network_guard()

IMPORT_ERROR = None
try:
    import lumibot
    from lumibot.backtesting.pandas_backtesting import PandasDataBacktesting
    from lumibot.entities import Asset, Data
    from lumibot.strategies.strategy import Strategy
except guards.NetworkBlocked as exc:  # network attempt at import time
    IMPORT_ERROR = f"NetworkBlocked at import: {exc}"
    raise

import pandas as pd

ENV_READS_AT_IMPORT = list(guards.ENV_READS)
NET_AT_IMPORT = list(guards.NETWORK_ATTEMPTS)

SYMBOL = "SPKE"
ASSET = Asset(SYMBOL, asset_type="stock")

# --- caller-supplied bars: a fixed, hand-written fixture, no fetcher -----
BARS = [
    # (date, open, high, low, close, volume)
    ("2024-01-02", 100.0, 101.0, 99.5, 100.5, 1_000_000),
    ("2024-01-03", 100.5, 102.5, 100.0, 102.0, 1_100_000),
    ("2024-01-04", 102.0, 103.0, 101.0, 101.5, 900_000),
    ("2024-01-05", 101.5, 104.0, 101.0, 103.5, 1_250_000),
    ("2024-01-08", 103.5, 105.0, 103.0, 104.5, 1_050_000),
    ("2024-01-09", 104.5, 106.0, 104.0, 105.0, 980_000),
    ("2024-01-10", 105.0, 105.5, 102.5, 103.0, 1_400_000),
    ("2024-01-11", 103.0, 104.5, 102.0, 104.0, 1_010_000),
    ("2024-01-12", 104.0, 107.0, 103.5, 106.5, 1_600_000),
    ("2024-01-16", 106.5, 108.0, 105.5, 107.0, 1_200_000),
]


def active_bars() -> list:
    """SPIKE_PERTURB=1 raises the final close by 5.0. If the backtest result
    moves accordingly, the bars really are caller-supplied, not fetched."""
    if os.environ.get("SPIKE_PERTURB") != "1":
        return BARS
    perturbed = [list(bar) for bar in BARS]
    perturbed[-1][2] += 5.0  # high
    perturbed[-1][4] += 5.0  # close
    return [tuple(bar) for bar in perturbed]


def build_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        active_bars(), columns=["datetime", "open", "high", "low", "close", "volume"]
    )
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame.set_index("datetime")


def input_bars_digest() -> str:
    return hashlib.sha256(
        json.dumps(active_bars(), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class SpikeStrategy(Strategy):
    """Deterministic: buy 10 shares on the first iteration, then hold."""

    def initialize(self):
        self.sleeptime = "1D"
        self.set_market("NYSE")
        self.observed = []

    def on_trading_iteration(self):
        price = self.get_last_price(ASSET)
        self.observed.append(
            {"dt": str(self.get_datetime()), "price": None if price is None else float(price)}
        )
        if not self.get_position(ASSET) and price:
            order = self.create_order(ASSET, 10, "buy")
            self.submit_order(order)


def run_once() -> dict:
    frame = build_frame()
    pandas_data = {
        ASSET: Data(ASSET, frame, timestep="day", timezone="America/New_York")
    }
    result = SpikeStrategy.run_backtest(
        PandasDataBacktesting,
        backtesting_start=dt.datetime(2024, 1, 2),
        backtesting_end=dt.datetime(2024, 1, 16),
        pandas_data=pandas_data,
        budget=100_000,
        benchmark_asset=None,      # no benchmark fetch -> no network
        analyze_backtest=False,    # no tearsheet / quantstats
        save_tearsheet=False,
        show_tearsheet=False,
        show_plot=False,
        show_indicators=False,
        quiet_logs=True,
        save_logfile=False,
    )
    return normalize(result)


def normalize(result) -> dict:
    """Reduce the run to a stable, comparable shape."""
    if isinstance(result, tuple):
        result = result[0]
    if hasattr(result, "to_dict"):
        try:
            result = result.to_dict()
        except Exception:
            pass
    if isinstance(result, dict):
        out = {}
        for key in sorted(result):
            value = result[key]
            if isinstance(value, (int, float, str, bool)) or value is None:
                out[key] = value
            else:
                out[key] = f"<{type(value).__name__}>"
        return out
    return {"repr": repr(result)[:400]}


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    evidence = {
        "label": label,
        "spike_creds_mode": SPIKE_CREDS,
        "lumibot_lazy_credentials": os.environ.get("LUMIBOT_LAZY_CREDENTIALS"),
        "python": sys.version.split()[0],
        "lumibot_version": lumibot.__version__,
        "lumibot_version_is_exactly_4_5_78": lumibot.__version__ == "4.5.78",
        "pandas_backtesting_class": f"{PandasDataBacktesting.__module__}.{PandasDataBacktesting.__qualname__}",
        "input_bars_digest": input_bars_digest(),
        "input_bar_count": len(BARS),
        "env_reads_at_import": len(ENV_READS_AT_IMPORT),
        "credential_env_reads_at_import": guards.credential_reads(),
        "network_attempts_at_import": NET_AT_IMPORT,
    }
    try:
        evidence["backtest"] = run_once()
        evidence["backtest_ok"] = True
    except guards.NetworkBlocked as exc:
        evidence["backtest_ok"] = False
        evidence["backtest_error"] = f"NetworkBlocked: {exc}"
    except Exception as exc:  # noqa: BLE001 - spike records the real failure
        evidence["backtest_ok"] = False
        evidence["backtest_error"] = f"{type(exc).__name__}: {exc}"
        import traceback

        evidence["backtest_traceback"] = traceback.format_exc()[-2500:]

    evidence["credential_env_reads_total"] = guards.credential_reads()
    evidence["network_attempts_total"] = guards.NETWORK_ATTEMPTS
    evidence["distinct_env_keys_read"] = len(set(guards.ENV_READS))

    with open(f"result_{label}.json", "w") as handle:
        json.dump(evidence, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
