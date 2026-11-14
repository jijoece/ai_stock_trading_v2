from __future__ import annotations

import json
import subprocess

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.services import scoring_service
from dashboard.services.scoring_service import (
    ENABLE_ENV,
    MAX_SYMBOLS,
    ScoringError,
    build_command,
    parse_symbols,
    run_scoring,
)


CARD = {
    "symbol": "ABC",
    "n_bars": 260,
    "warning": None,
    "pillars": {
        "trend": {"score": 2, "detail": "price>EMA20"},
        "momentum": {"score": 1, "detail": "RSI 60"},
        "macro_sentiment": {"score": 1, "detail": "injected"},
    },
    "pillar_total": 4,
    "decision": {
        "action": "HOLD",
        "rationale": "trend intact",
        "framing": "no exhaustion",
        "flags": {"exhaustion": [], "bearish": [], "rebound": []},
    },
    "data": {
        "provider": "alpaca", "feed": "iex", "adjustment": "split",
        "adjustment_verified": True, "first_session": "2025-01-02", "last_session": "2026-11-13",
    },
}


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["x"], returncode, stdout, stderr)


def test_parse_symbols_normalizes_and_deduplicates():
    assert parse_symbols("nvda, AAPL  nvda") == ("NVDA", "AAPL")


@pytest.mark.parametrize("text", ["", "  ", "AB;CD", "../x", "$(id)", "A" * 11])
def test_parse_symbols_rejects_bad_input(text):
    with pytest.raises(ScoringError):
        parse_symbols(text)


def test_parse_symbols_caps_count():
    with pytest.raises(ScoringError):
        parse_symbols(" ".join(f"S{i}" for i in range(MAX_SYMBOLS + 1)))


def test_build_command_is_fixed_argv_without_account_positions():
    command = build_command(("NVDA",), "robinhood", holding="not_holding", python="py")
    assert command[0] == "py"
    assert command[2:] == ["--robinhood", "NVDA", "--json", "--macro-score", "auto", "--not-holding"]
    assert "--check-holding" not in command


def test_run_scoring_refuses_when_disabled(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(ScoringError, match="disabled"):
        run_scoring(("ABC",), "alpaca", runner=lambda *a, **k: pytest.fail("must not run"))


def test_run_scoring_parses_json_and_uses_no_shell(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    seen = {}

    def runner(command, **kwargs):
        seen.update(kwargs, command=command)
        return _completed(json.dumps({
            "as_of": "2026-11-13", "macro": {"score": 1}, "results": [CARD],
            "errors": [{"symbol": "BAD", "error": "insufficient history"}],
        }), returncode=2)

    outcome = run_scoring(("ABC", "BAD"), "alpaca", runner=runner)

    assert seen["shell"] is False and seen["timeout"] == scoring_service.TIMEOUT_SECONDS
    assert outcome.as_of == "2026-11-13"
    assert outcome.cards[0]["symbol"] == "ABC"
    assert outcome.errors == (("BAD", "insufficient history"),)


def test_run_scoring_surfaces_only_the_script_error_line(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    stderr = "Traceback (most recent call last):\n  secret=abc\nerror: ALPACA keys are not set"
    with pytest.raises(ScoringError) as info:
        run_scoring(("ABC",), "alpaca", runner=lambda *a, **k: _completed("", stderr, 1))
    assert str(info.value) == "error: ALPACA keys are not set"


def test_run_scoring_hides_unstructured_failures(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    with pytest.raises(ScoringError) as info:
        run_scoring(("ABC",), "alpaca", runner=lambda *a, **k: _completed("", "Traceback secret", 1))
    assert str(info.value) == "Scoring failed (exit code 1)."


def test_run_scoring_reports_timeout(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")

    def runner(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    with pytest.raises(ScoringError, match="timed out"):
        run_scoring(("ABC",), "alpaca", runner=runner)


def test_page_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    app = AppTest.from_string(
        "from dashboard.pages.score_ticker import render\nrender()", default_timeout=10
    ).run()
    assert not app.exception
    assert app.title[0].value == "Score a Ticker"
    assert "disabled" in app.info[0].value
    assert len(app.text_input) == 0


def test_page_scores_with_mocked_runner(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setattr(
        scoring_service.subprocess, "run",
        lambda *a, **k: _completed(json.dumps({"as_of": "2026-11-13", "results": [CARD], "errors": []})),
    )
    app = AppTest.from_string(
        "from dashboard.pages.score_ticker import render\nrender()", default_timeout=10
    ).run()
    app.text_input[0].set_value("abc")
    app.button[0].click().run()

    assert not app.exception
    assert app.subheader[0].value == "ABC · HOLD"
    assert {m.label: m.value for m in app.metric}["Total (-6..+6)"] == "+4"
