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


def test_plain_output_preserves_and_redacts_exception_traceback():
    logging_config.register_secret("exception-secret-value")
    buffer = _configure_capturing(json_output=False)
    log = logging_config.get_logger("unit_test")
    try:
        raise ValueError("exception-secret-value")
    except ValueError:
        log.exception("operation failed")
    line = buffer.getvalue()
    assert "Traceback (most recent call last)" in line
    assert "ValueError: [REDACTED]" in line
    assert "exception-secret-value" not in line


def test_plain_output_preserves_stack_info():
    buffer = _configure_capturing(json_output=False)
    log = logging_config.get_logger("unit_test")
    log.info("diagnostic", stack_info=True)
    assert "Stack (most recent call last)" in buffer.getvalue()


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


def test_json_output_redacts_registered_secret_in_nested_extra_field():
    logging_config.register_secret("nested-extra-secret")
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"context": {"token": "nested-extra-secret"}})
    raw = buffer.getvalue()
    assert "nested-extra-secret" not in raw
    payload = json.loads(raw.strip())
    assert payload["context"]["token"] == "[REDACTED]"


def test_json_output_never_leaks_bearer_token_end_to_end():
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.error("auth failed with header Bearer abcdefghij1234567890XYZ")
    raw = buffer.getvalue()
    assert "abcdefghij1234567890XYZ" not in raw
    payload = json.loads(raw.strip())
    assert "[REDACTED]" in payload["message"]


@pytest.mark.parametrize(
    "secret",
    [
        'nested"secret-with-quote',
        "nested\\secret-with-backslash",
        "nested\nsecret-with-newline",
    ],
)
def test_json_output_redacts_registered_secret_in_nested_extra_field_with_json_escaped_characters(secret):
    """PR 15 review round 2 (findings 1, 2, 4): a registered secret
    containing a character JSON must escape (a quote, backslash, or
    control character) no longer survives verbatim as a contiguous
    substring once `JSONRenderer` serializes it, so the old post-render
    `redact()` pass over the fully rendered line could never find it.
    Redaction must now happen on the raw nested value before serialization
    escapes it."""
    logging_config.register_secret(secret)
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"context": {"value": secret}})
    raw = buffer.getvalue()
    payload = json.loads(raw.strip())
    assert payload["context"]["value"] == "[REDACTED]"
    assert secret not in raw


def test_json_output_redacts_account_number_extra_field_by_key():
    """PR 15 review round 2 (finding 3): the pre-migration formatter only
    ever surfaced eight allowlisted `extra` fields, so an account number
    passed via `extra={"account_number": ...}` never reached the JSON
    payload. `ExtraAdder` now surfaces every extra field, and an account
    number matches none of `_SECRET_PATTERNS`' key names, so it must be
    redacted by key alone to preserve this module's documented "no ...
    account number ... may reach a log line" contract."""
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("order filled", extra={"account_number": "123456789"})
    raw = buffer.getvalue()
    assert "123456789" not in raw
    payload = json.loads(raw.strip())
    assert payload["account_number"] == "[REDACTED]"


def test_json_output_redacts_nested_non_string_sensitive_field_by_key():
    """A sensitive key's value must be redacted regardless of its type
    (e.g. an account number logged as an int), and regardless of nesting
    depth."""
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("order filled", extra={"context": {"account_number": 123456789}})
    raw = buffer.getvalue()
    assert "123456789" not in raw
    payload = json.loads(raw.strip())
    assert payload["context"]["account_number"] == "[REDACTED]"


def test_json_output_redacts_sensitive_keys_inside_list_extra_field():
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"items": [{"token": "abcd1234efgh"}, "harmless"]})
    raw = buffer.getvalue()
    assert "abcd1234efgh" not in raw
    payload = json.loads(raw.strip())
    assert payload["items"][0]["token"] == "[REDACTED]"
    assert payload["items"][1] == "harmless"


def test_json_output_does_not_redact_unrelated_extra_fields():
    """Guards against overbroad key-name matching: ordinary operational
    fields must survive unredacted."""
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info(
        "event",
        extra={"operation": "list_repos", "duration_ms": 42, "status": "ok"},
    )
    payload = json.loads(buffer.getvalue().strip())
    assert payload["operation"] == "list_repos"
    assert payload["duration_ms"] == 42
    assert payload["status"] == "ok"


def test_json_output_redacts_registered_secret_inside_set_extra_field():
    """Kimi K3 automated review (PR 15 fix round 3): `_redact_value`
    originally handled only `Mapping`/`list`/`tuple`, so a secret nested
    inside a `set` extra reached `JSONRenderer`'s non-serializable-value
    fallback (which stringifies via `repr()`) unredacted by the
    pre-serialization pass -- relying entirely on the post-render safety
    net, which (as the earlier escaping findings in this file show) is not
    reliable for secrets containing JSON-escaped characters."""
    logging_config.register_secret("set-member-secret")
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"items": {"set-member-secret", "harmless"}})
    raw = buffer.getvalue()
    assert "set-member-secret" not in raw
    assert "[REDACTED]" in raw
    assert "harmless" in raw


def test_json_output_redacts_registered_secret_inside_frozenset_extra_field():
    logging_config.register_secret("frozenset-member-secret")
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"items": frozenset({"frozenset-member-secret"})})
    raw = buffer.getvalue()
    assert "frozenset-member-secret" not in raw
    assert "[REDACTED]" in raw


def test_json_output_redacts_registered_secret_inside_bytes_extra_field():
    logging_config.register_secret("bytes-member-secret")
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"blob": b"bytes-member-secret"})
    raw = buffer.getvalue()
    assert "bytes-member-secret" not in raw
    assert "[REDACTED]" in raw


def test_json_output_redacts_registered_secret_inside_namedtuple_extra_field_without_crashing():
    """Kimi K3 automated review (PR 15 fix round 3): reconstructing a
    namedtuple via `type(value)(generator)` raises, because a namedtuple's
    `__new__` takes one positional argument per field rather than a single
    iterable -- silently dropping the entire log record (Python's logging
    module swallows a formatter exception), which is worse than losing the
    namedtuple's type identity in a JSON line that would discard it
    anyway."""
    import collections

    Point = collections.namedtuple("Point", ["x", "y"])
    logging_config.register_secret("namedtuple-member-secret")
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"point": Point(x="namedtuple-member-secret", y="b")})
    raw = buffer.getvalue()
    assert raw, "log record must not be silently dropped"
    assert "namedtuple-member-secret" not in raw
    payload = json.loads(raw.strip())
    assert payload["point"] == ["[REDACTED]", "b"]


def test_json_output_over_redacts_key_substring_matches_by_design():
    """Documents accepted, deliberate over-redaction (Kimi K3 automated
    review, PR 15 fix round 3): `_SENSITIVE_KEY_SUBSTRINGS` matches `key` as
    a substring, so an unrelated field like `monkey` is redacted too. This
    is the safe direction for this module's no-leak contract and is not a
    bug -- an actual API key must never slip through because its field
    happened to be named something slightly different."""
    buffer = _configure_capturing(json_output=True)
    log = logging_config.get_logger("unit_test")
    log.info("event", extra={"monkey": "not-a-secret-value"})
    payload = json.loads(buffer.getvalue().strip())
    assert payload["monkey"] == "[REDACTED]"
