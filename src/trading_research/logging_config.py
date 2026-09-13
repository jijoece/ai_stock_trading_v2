"""Structured logging with centralized secret redaction (structlog-backed).

No API key, bearer token, OAuth token, account number, or raw credential
header may reach a log line. `redact()` is applied to every rendered field.

`get_logger()` still returns a plain `logging.Logger` — every existing
caller uses stdlib idioms (`%s` positional args, `extra={...}`) and none of
that changes. Structlog is wired in purely as the formatting layer, via
`structlog.stdlib.ProcessorFormatter`: it turns every stdlib `LogRecord`
into a structlog event dict, runs the redaction logic (unchanged from the
pre-migration implementation, just relocated into a processor) over every
top-level string field and the final rendered line, then renders it. No
structlog-native logger
(`structlog.get_logger()`/`structlog.configure()`) is created anywhere in
this module.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any, MutableMapping

import structlog

_SECRET_PATTERNS = [
    re.compile(r"(sk-ant-[A-Za-z0-9\-_]{10,})"),
    re.compile(r"(Bearer\s+[A-Za-z0-9\-_.]{10,})", re.IGNORECASE),
    re.compile(r"(\"?(?:api[_-]?key|token|secret|password|authorization)\"?\s*[:=]\s*\"?)([^\s\"',}]{4,})", re.IGNORECASE),
]

_RUNTIME_SECRETS: list[str] = []


def register_secret(value: str | None) -> None:
    """Register an in-memory secret value so it can be scrubbed from logs verbatim."""
    if value and value not in _RUNTIME_SECRETS:
        _RUNTIME_SECRETS.append(value)


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for secret in _RUNTIME_SECRETS:
        if secret and secret in out:
            out = out.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) + "[REDACTED]") if m.lastindex and m.lastindex >= 2 else "[REDACTED]", out)
    return out


_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S%z"


def _add_timestamp(logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    record = event_dict.get("_record")
    if record is not None:
        event_dict["timestamp"] = logging.Formatter().formatTime(record, _TIMESTAMP_FMT)
    return event_dict


def _uppercase_level(logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    level = event_dict.get("level")
    if isinstance(level, str):
        event_dict["level"] = level.upper()
    return event_dict


def _redact_event_dict(logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact(value)
    return event_dict


def _add_plain_diagnostics(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Preserve stdlib Formatter's exception and stack-info behavior."""
    exc_info = event_dict.get("exc_info")
    if isinstance(exc_info, tuple):
        event_dict["_exception_text"] = logging.Formatter().formatException(exc_info)
    stack_info = event_dict.get("stack_info")
    if isinstance(stack_info, str):
        event_dict["_stack_info"] = stack_info
    return event_dict


def _render_plain(logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> str:
    rendered = "{timestamp} {level} {logger} {message}".format(
        timestamp=event_dict.get("timestamp", ""),
        level=event_dict.get("level", ""),
        logger=event_dict.get("logger", ""),
        message=event_dict.get("message", ""),
    )
    diagnostics = [
        str(event_dict[key])
        for key in ("_exception_text", "_stack_info")
        if event_dict.get(key)
    ]
    if diagnostics:
        rendered = "\n".join((rendered, *diagnostics))
    return redact(rendered)


_JSON_RENDERER = structlog.processors.JSONRenderer()


def _render_json(logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> str:
    # Redact the serialized line as the final boundary too.  ExtraAdder may
    # surface nested mappings, sequences, or objects whose string
    # representation contains a secret; top-level event-dict redaction alone
    # cannot see those values before JSONRenderer serializes them.
    return redact(_JSON_RENDERER(logger, method_name, event_dict))


_FOREIGN_PRE_CHAIN = [
    structlog.stdlib.add_log_level,
    _uppercase_level,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.ExtraAdder(),
    _add_timestamp,
    _redact_event_dict,
    structlog.processors.EventRenamer("message"),
]


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    renderer = _render_json if json_output else _render_plain
    processors = [structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer]
    if not json_output:
        processors.insert(0, _add_plain_diagnostics)
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_FOREIGN_PRE_CHAIN,
        processors=processors,
    )
    root = logging.getLogger("trading_research")
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"trading_research.{name}")
