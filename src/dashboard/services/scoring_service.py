"""On-demand ticker scoring for the dashboard (opt-in).

Runs ``scripts/score.py`` as a child process with a fixed argument vector (no
shell), so Python computes every indicator and the dashboard only displays the
JSON card. The child process fetches market data read-only (Alpaca market data
or the Robinhood MCP's ``get_equity_historicals``). It never touches the broker
order path, and ``--check-holding`` (account positions) is deliberately not
offered here.

The page is disabled unless ``AI_STOCK_TRADING_ENABLE_SCORING=1`` is set, because
it makes credentialed network calls that the rest of the dashboard never makes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Literal, Mapping, Sequence


ENABLE_ENV = "AI_STOCK_TRADING_ENABLE_SCORING"
MAX_SYMBOLS = 5
TIMEOUT_SECONDS = 180
MAX_ERROR_CHARS = 300

Source = Literal["alpaca", "robinhood"]
Holding = Literal["unknown", "holding", "not_holding"]

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "score.py"
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
_HOLDING_FLAGS: Mapping[Holding, tuple[str, ...]] = {
    "unknown": (),
    "holding": ("--holding",),
    "not_holding": ("--not-holding",),
}

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ScoringError(RuntimeError):
    """A sanitized scoring failure that is safe to show to the user."""


@dataclass(frozen=True)
class ScoringOutcome:
    as_of: str | None
    macro: Mapping[str, Any] | None
    cards: tuple[Mapping[str, Any], ...]
    errors: tuple[tuple[str, str], ...]  # (symbol, message)


def scoring_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip() == "1"


def parse_symbols(text: str) -> tuple[str, ...]:
    """Validate a comma/space separated symbol list; raises ScoringError on bad input."""
    symbols: list[str] = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        symbol = token.upper()
        if not _SYMBOL_RE.match(symbol):
            raise ScoringError(f"{token[:20]!r} is not a valid ticker symbol.")
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise ScoringError("Enter at least one ticker symbol.")
    if len(symbols) > MAX_SYMBOLS:
        raise ScoringError(f"Score at most {MAX_SYMBOLS} symbols at a time.")
    return tuple(symbols)


def build_command(
    symbols: Sequence[str],
    source: Source,
    *,
    holding: Holding = "unknown",
    macro_auto: bool = True,
    python: str | None = None,
) -> list[str]:
    if source not in ("alpaca", "robinhood"):
        raise ScoringError("Unknown data source.")
    if holding not in _HOLDING_FLAGS:
        raise ScoringError("Unknown holding option.")
    command = [python or sys.executable, str(_SCRIPT), f"--{source}", *symbols, "--json"]
    if macro_auto:
        command += ["--macro-score", "auto"]
    command += _HOLDING_FLAGS[holding]
    return command


def run_scoring(
    symbols: Sequence[str],
    source: Source,
    *,
    holding: Holding = "unknown",
    macro_auto: bool = True,
    runner: Runner | None = None,
) -> ScoringOutcome:
    if not scoring_enabled():
        raise ScoringError(f"Scoring is disabled. Set {ENABLE_ENV}=1 to enable it.")
    command = build_command(symbols, source, holding=holding, macro_auto=macro_auto)
    try:
        completed = (runner or subprocess.run)(
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise ScoringError(f"Scoring timed out after {TIMEOUT_SECONDS} seconds.") from None
    except OSError:
        raise ScoringError("The scoring script could not be started.") from None

    payload = _parse_json(completed.stdout)
    if payload is None:
        raise ScoringError(_script_error(completed.stderr, completed.returncode))
    return ScoringOutcome(
        as_of=payload.get("as_of") if isinstance(payload.get("as_of"), str) else None,
        macro=payload.get("macro") if isinstance(payload.get("macro"), dict) else None,
        cards=tuple(card for card in payload.get("results", ()) if isinstance(card, dict)),
        errors=tuple(
            (str(item.get("symbol", "?")), str(item.get("error", "failed"))[:MAX_ERROR_CHARS])
            for item in payload.get("errors", ())
            if isinstance(item, dict)
        ),
    )


def _parse_json(stdout: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _script_error(stderr: str, returncode: int) -> str:
    """Show only the script's own `error: ...` line, never raw output or tracebacks."""
    for line in reversed((stderr or "").strip().splitlines()):
        if line.startswith("error:"):
            return line[:MAX_ERROR_CHARS]
    return f"Scoring failed (exit code {returncode})."
