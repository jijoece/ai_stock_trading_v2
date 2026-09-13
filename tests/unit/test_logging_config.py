"""Parity tests for the structlog-backed `logging_config.py` (library-migration
PR 15). Redaction/secret-registration behavior must match the pre-migration
`RedactingFormatter`/`JsonRedactingFormatter` implementation exactly; the
public API (`get_logger`, `configure_logging`, `register_secret`, `redact`)
is unchanged in name and signature. `structlog` is a base dependency as of
this PR (see `pyproject.toml`, `DECISIONS.md` D13), so this file has no
`importorskip` guard -- unlike the optional-extra pattern in
`test_indicators.py`/`test_analytics_parity.py`, a missing `structlog`
import here is a real failure, not an expected skip.
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from trading_research import logging_config


@pytest.fixture(autouse=True)
def _reset_logging_state():
    root = logging.getLogger("trading_research")
    original_handlers = list(root.handlers)
    original_secrets = list(logging_config._RUNTIME_SECRETS)
    yield
    root.handlers.clear()
    root.handlers.extend(original_handlers)
    logging_config._RUNTIME_SECRETS.clear()
    logging_config._RUNTIME_SECRETS.extend(original_secrets)


def _configure_capturing(json_output: bool) -> io.StringIO:
    buffer = io.StringIO()
    logging_config.configure_logging(level="INFO", json_output=json_output)
    root = logging.getLogger("trading_research")
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.stream = buffer
    return buffer


def test_get_logger_returns_stdlib_logger_in_namespace():
    log = logging_config.get_logger("mcp.reddit_adapter")
    assert isinstance(log, logging.Logger)
    assert log.name == "trading_research.mcp.reddit_adapter"


def test_configure_logging_sets_up_single_stderr_handler_no_propagate():
    logging_config.configure_logging()
    root = logging.getLogger("trading_research")
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)
    assert root.propagate is False


def test_configure_logging_clears_handlers_on_repeat_call():
    logging_config.configure_logging()
    logging_config.configure_logging()
    root = logging.getLogger("trading_research")
    assert len(root.handlers) == 1


def test_plain_output_contains_level_logger_and_message():
    buffer = _configure_capturing(json_output=False)
    log = logging_config.get_logger("unit_test")
    log.info("hello world")
    line = buffer.getvalue().strip()
    assert "INFO" in line
    assert "trading_research.unit_test" in line
    assert line.endswith("hello world")


def test_plain_output_percent_style_positional_args_interpolated():
    buffer = _configure_capturing(json_output=False)
    log = logging_config.get_logger("unit_test")
    log.info("Wrote %s", "/tmp/report.json")
    line = buffer.getvalue().strip()
    assert "Wrote /tmp/report.json" in line
    assert "%s" not in line


def test_json_output_has_expected_keys_and_message():
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.warning("something happened")
    payload = json.loads(buffer.getvalue().strip())
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "trading_research.unit_test"
    assert payload["message"] == "something happened"
    assert "timestamp" in payload


def test_json_output_includes_extra_fields():
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("calling tool", extra={"operation": "list_repos", "duration_ms": 42})
    payload = json.loads(buffer.getvalue().strip())
    assert payload["operation"] == "list_repos"
    assert payload["duration_ms"] == 42


def test_register_secret_redacts_verbatim_value():
    logging_config.register_secret("super-secret-value-123")
    assert logging_config.redact("token=super-secret-value-123") == "token=[REDACTED]"


def test_register_secret_deduplicates():
    logging_config.register_secret("dup-secret")
    logging_config.register_secret("dup-secret")
    assert logging_config._RUNTIME_SECRETS.count("dup-secret") == 1


def test_register_secret_ignores_none_and_empty():
    before = len(logging_config._RUNTIME_SECRETS)
    logging_config.register_secret(None)
    logging_config.register_secret("")
    assert len(logging_config._RUNTIME_SECRETS) == before


@pytest.mark.parametrize(
    "text,expected_substring",
    [
        ("key sk-ant-abcdefghij1234567890", "[REDACTED]"),
        ("Authorization: Bearer abcdefghij1234567890", "[REDACTED]"),
        ('{"api_key": "abcd1234efgh"}', "[REDACTED]"),
        ('{"token": "abcd1234efgh"}', "[REDACTED]"),
        ('{"password": "abcd1234efgh"}', "[REDACTED]"),
    ],
)
def test_redact_pattern_cases(text, expected_substring):
    assert expected_substring in logging_config.redact(text)


def test_redact_empty_string_returns_empty():
    assert logging_config.redact("") == ""


def test_plain_output_redacts_registered_secret_in_message():
    logging_config.register_secret("plain-mode-secret-xyz")
    buffer = _configure_capturing(json_output=False)
    log = logging_config.get_logger("unit_test")
    log.info("leaked value: plain-mode-secret-xyz")
    line = buffer.getvalue()
    assert "plain-mode-secret-xyz" not in line
    assert "[REDACTED]" in line


def test_json_output_redacts_registered_secret_in_extra_field():
    logging_config.register_secret("extra-field-secret")
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"operation": "op-extra-field-secret-done"})
    payload = json.loads(buffer.getvalue().strip())
    assert "extra-field-secret" not in payload["operation"]
    assert "[REDACTED]" in payload["operation"]


def test_json_output_never_leaks_bearer_token_end_to_end():
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.error("auth failed with header Bearer abcdefghij1234567890XYZ")
    raw = buffer.getvalue()
    assert "abcdefghij1234567890XYZ" not in raw
    payload = json.loads(raw.strip())
    assert "[REDACTED]" in payload["message"]
