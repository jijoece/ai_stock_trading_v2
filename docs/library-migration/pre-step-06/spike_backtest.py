"""LumiBot 4.5.78 offline-backtest feasibility spike.

Proves (or disproves) each claim the pre-step's architecture decision depends on:

  1. the installed LumiBot version is exactly 4.5.78
  2. PandasDataBacktesting replays a caller-supplied DataFrame
  3. no broker credential *value* is available to or loaded by LumiBot
     (LumiBot reads credential variable *names* unconditionally; that is not
     the safety property and is not asserted away)
  4. no credentials are loaded from the process environment or from a .env /
     .env.local file in the script directory or the working directory
  5. no network connection is attempted (guards fail closed)
  6. no live broker / live data provider is initialized
  7. results are deterministic across repeated runs
  8. all input bars are caller supplied
  9. stdout contamination measured (protocol-corruption risk for a subprocess)

Run: venv-b/bin/python spike_backtest.py <run_label>
Writes JSON evidence to result_<run_label>.json; keeps stdout free for the
contamination measurement.

Modes (all read before `import lumibot`):

  SPIKE_CREDS=present|absent|inherit
      present -> seed fake sentinel broker credentials into os.environ
      absent  -> run the credential scrub: delete every credential-named
                 variable inherited from the parent process
      inherit -> neither seed nor scrub; the positive control that shows what
                 an inherited process-environment credential would do
  SPIKE_SUPPRESS_DOTENV=1|0
      1 -> set LUMIBOT_DISABLE_DOTENV=1, the documented 4.5.78 opt-out that
           skips .env/.env.local discovery entirely (see EVALUATION.md §2.3)
      0 -> leave discovery on; used as the positive control that proves the
           sentinel .env really would be loaded
  SPIKE_PERTURB=1
      raise the final bar's close by 5.00, proving bars are caller-supplied
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

import guards

# --- guards installed BEFORE lumibot is imported -------------------------
# SPIKE_CREDS=present -> seed sentinel broker credentials in-process (models a
#                        developer machine / CI runner already holding Alpaca
#                        paper credentials).
# SPIKE_CREDS=absent  -> run the credential scrub: delete every
#                        credential-named variable inherited from the parent
#                        process. This is the scrub ADR 0009 Decision 2
#                        requires of backtest_runtime/'s entry point.
# SPIKE_CREDS=inherit -> neither seed nor scrub. The positive control for the
#                        process-environment case: whatever the parent
#                        exported stays, so the harness can show the scrub is
#                        what removes it.
SPIKE_CREDS = os.environ.get("SPIKE_CREDS", "present")

# Captured before the scrub so the evidence can state what this process
# actually inherited, rather than what it was assumed to inherit.
INHERITED_SENTINEL_KEYS = guards.keys_with_token(guards.PROCENV_SENTINEL_TOKEN)

if SPIKE_CREDS == "present":
    for key, value in guards.CREDENTIAL_SENTINELS.items():
        os.environ[key] = value
elif SPIKE_CREDS == "absent":
    for key in list(os.environ):
        upper = key.upper()
        if any(marker in upper for marker in guards.CREDENTIAL_KEY_MARKERS):
            del os.environ[key]
elif SPIKE_CREDS != "inherit":
    raise SystemExit(f"unknown SPIKE_CREDS mode: {SPIKE_CREDS!r}")

# Immediately after the scrub, before lumibot is imported.
SENTINEL_KEYS_AFTER_SCRUB = guards.keys_with_token(guards.PROCENV_SENTINEL_TOKEN)

# --- .env suppression: the exact 4.5.78 mechanism ------------------------
# lumibot/credentials.py reads LUMIBOT_DISABLE_DOTENV at module scope, before
# any discovery runs, and skips BOTH the script-directory walk and the
# working-directory walk when it is set. Setting it here — before the import —
# is what prevents an operator's .env from entering this process.
SPIKE_SUPPRESS_DOTENV = os.environ.get("SPIKE_SUPPRESS_DOTENV", "1") == "1"
if SPIKE_SUPPRESS_DOTENV:
    os.environ["LUMIBOT_DISABLE_DOTENV"] = "1"
else:
    os.environ.pop("LUMIBOT_DISABLE_DOTENV", None)

# Recorded before the import so the evidence states what the process actually
# faced, not what it was configured to face.
CWD_AT_START = os.getcwd()
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
DOTENV_IN_CWD = os.path.isfile(os.path.join(CWD_AT_START, ".env"))
DOTENV_LOCAL_IN_CWD = os.path.isfile(os.path.join(CWD_AT_START, ".env.local"))

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


def dotenv_and_broker_evidence() -> dict:
    """Post-import proof set for the credential-safety requirement.

    Deliberately records *presence*, never a value: a leaked sentinel is
    reported by key name and by the boolean below, so the evidence file can be
    committed without carrying any credential-shaped string.
    """
    from lumibot import credentials as lumibot_credentials

    dotenv_keys = guards.keys_with_token(guards.DOTENV_SENTINEL_TOKEN)
    procenv_keys = guards.keys_with_token(guards.PROCENV_SENTINEL_TOKEN)

    configs_with_sentinel = {}
    for name in dir(lumibot_credentials):
        if not name.endswith("_CONFIG"):
            continue
        hits = guards.sentinel_hits_in(getattr(lumibot_credentials, name, None))
        if hits:
            configs_with_sentinel[name] = hits

    broker = getattr(lumibot_credentials, "broker", None)
    data_source = getattr(lumibot_credentials, "data_source", None)
    return {
        # Credential NAMES LumiBot resolved to some value, including its own
        # hardcoded .get() defaults. Not expected to be empty.
        "credential_env_reads_with_values": guards.credential_reads_with_values(),
        # The strict metric: credential-named values that actually came from
        # the process environment. Must be empty.
        "credential_values_from_environment": guards.credential_values_from_environment(),
        # Leak path A: did a sentinel .env / .env.local reach this process?
        "dotenv_sentinel_keys_after_import": dotenv_keys,
        "dotenv_sentinel_loaded": bool(dotenv_keys),
        # Leak path B: did an inherited process-environment credential survive?
        "procenv_sentinel_keys_inherited": INHERITED_SENTINEL_KEYS,
        "procenv_sentinel_keys_after_scrub": SENTINEL_KEYS_AFTER_SCRUB,
        "procenv_sentinel_keys_after_import": procenv_keys,
        "procenv_sentinel_survived": bool(procenv_keys),
        # Either path reaching a LumiBot config, reported by token.
        "sentinel_in_lumibot_configs": configs_with_sentinel,
        # Back-compat aggregate across both leak paths.
        "sentinel_env_keys_after_import": guards.sentinel_env_keys(),
        "sentinel_values_loaded": bool(dotenv_keys or procenv_keys),
        # No live broker / live data provider initialized.
        "broker_after_import": repr(broker),
        "data_source_after_import": repr(data_source),
        "broker_is_none": broker is None,
        "data_source_is_none": data_source is None,
    }


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    evidence = {
        "label": label,
        "spike_creds_mode": SPIKE_CREDS,
        "dotenv_suppressed": SPIKE_SUPPRESS_DOTENV,
        "lumibot_disable_dotenv": os.environ.get("LUMIBOT_DISABLE_DOTENV"),
        "lumibot_lazy_credentials": os.environ.get("LUMIBOT_LAZY_CREDENTIALS"),
        "cwd": CWD_AT_START,
        "script_dir": SCRIPT_DIR,
        "sentinel_dotenv_present_in_cwd": DOTENV_IN_CWD,
        "sentinel_dotenv_local_present_in_cwd": DOTENV_LOCAL_IN_CWD,
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
    evidence.update(dotenv_and_broker_evidence())
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
    evidence["credential_env_reads_with_values_total"] = guards.credential_reads_with_values()
    evidence["credential_values_from_environment_total"] = (
        guards.credential_values_from_environment()
    )
    evidence["network_attempts_total"] = guards.NETWORK_ATTEMPTS
    evidence["network_attempt_count"] = len(guards.NETWORK_ATTEMPTS)
    evidence["distinct_env_keys_read"] = len(set(guards.ENV_READS))
    evidence["sentinel_env_keys_after_run"] = guards.sentinel_env_keys()
    evidence["dotenv_sentinel_keys_after_run"] = guards.keys_with_token(
        guards.DOTENV_SENTINEL_TOKEN
    )
    evidence["procenv_sentinel_keys_after_run"] = guards.keys_with_token(
        guards.PROCENV_SENTINEL_TOKEN
    )

    with open(f"result_{label}.json", "w") as handle:
        json.dump(evidence, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
