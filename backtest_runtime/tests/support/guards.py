"""Fail-closed test guards, adapted for permanent use from the verified
pre-step evidence (docs/library-migration/pre-step-06/guards.py). Install
BEFORE importing lumibot in a probe subprocess. Two guards:

1. env-read tracing: every read of `os.environ` is recorded, together with
   whether the read resolved to a value *from the process environment*, as
   opposed to a hardcoded default LumiBot supplied to `os.environ.get()`
   itself. LumiBot reads credential-named variable names unconditionally, so
   the read count is not the safety property -- the safety property is that
   none of those reads returns a value sourced from the environment.
2. network fail-closed: DNS resolution and TCP connect raise `NetworkBlocked`
   instead of succeeding. Any attempt is recorded.

Not installed by production code -- this module exists only so the tests in
this suite can prove the credential-safety and network-safety properties
docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 2 requires,
by measurement rather than by reading the source.
"""
from __future__ import annotations

import os
import socket

ENV_READS: list[str] = []
ENV_READS_WITH_VALUE: list[str] = []
ENV_VALUES_FROM_ENVIRONMENT: list[str] = []
NETWORK_ATTEMPTS: list[dict] = []


class NetworkBlocked(RuntimeError):
    pass


def _record_read(key, value, *, from_environment: bool) -> None:
    name = str(key)
    ENV_READS.append(name)
    if value in (None, ""):
        return
    if name not in ENV_READS_WITH_VALUE:
        ENV_READS_WITH_VALUE.append(name)
    if from_environment and name not in ENV_VALUES_FROM_ENVIRONMENT:
        ENV_VALUES_FROM_ENVIRONMENT.append(name)


def install_env_tracer() -> None:
    real = os.environ

    class TracingEnviron(type(real)):  # type: ignore[misc]
        def __getitem__(self, key):
            try:
                value = super().__getitem__(key)
            except KeyError:
                _record_read(key, None, from_environment=False)
                raise
            _record_read(key, value, from_environment=True)
            return value

        def get(self, key, default=None):
            present = super().__contains__(key)
            value = super().__getitem__(key) if present else default
            _record_read(key, value, from_environment=present)
            return value

        def __contains__(self, key):
            present = super().__contains__(key)
            _record_read(key, "x" if present else None, from_environment=present)
            return present

    try:
        traced = TracingEnviron(
            real._data, real.encodekey, real.decodekey, real.encodevalue, real.decodevalue
        )
    except Exception:  # pragma: no cover - interpreter-specific fallback
        return
    os.environ = traced  # type: ignore[assignment]


def _record(kind: str, target: object) -> None:
    NETWORK_ATTEMPTS.append({"kind": kind, "target": repr(target)})


def install_network_guard() -> None:
    real_getaddrinfo = socket.getaddrinfo

    def blocked_getaddrinfo(host, port, *a, **k):
        if host in ("localhost", "127.0.0.1", "::1", None):
            return real_getaddrinfo(host, port, *a, **k)
        _record("getaddrinfo", (host, port))
        raise NetworkBlocked(f"DNS resolution blocked by test guard: {host}:{port}")

    def blocked_create_connection(address, *a, **k):
        _record("create_connection", address)
        raise NetworkBlocked(f"outbound connection blocked by test guard: {address}")

    def blocked_connect(self, address, *a, **k):
        _record("socket.connect", address)
        raise NetworkBlocked(f"outbound connection blocked by test guard: {address}")

    def blocked_connect_ex(self, address, *a, **k):
        _record("socket.connect_ex", address)
        raise NetworkBlocked(f"outbound connection blocked by test guard: {address}")

    socket.getaddrinfo = blocked_getaddrinfo
    socket.create_connection = blocked_create_connection
    socket.socket.connect = blocked_connect
    socket.socket.connect_ex = blocked_connect_ex


CREDENTIAL_KEY_MARKERS = (
    "KEY", "SECRET", "TOKEN", "PASSWORD", "ACCOUNT", "CREDENTIAL",
    "ALPACA", "TRADIER", "POLYGON", "SCHWAB", "IB_", "COINBASE", "KRAKEN",
)


def _is_credential_named(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in CREDENTIAL_KEY_MARKERS)


def credential_reads_with_values() -> list[str]:
    """Credential-named variables that resolved to a non-empty value,
    including LumiBot's own hardcoded `.get()` defaults -- NOT expected to
    be empty. See `BENIGN_LUMIBOT_DEFAULT_CREDENTIAL_NAMES` in
    `backtest_runtime.credential_guard`."""
    return [key for key in ENV_READS_WITH_VALUE if _is_credential_named(key)]


def credential_values_from_environment() -> list[str]:
    """The strict safety metric: credential-named variables whose value came
    from the process environment (ambient or a loaded `.env`). Must be
    empty."""
    return [key for key in ENV_VALUES_FROM_ENVIRONMENT if _is_credential_named(key)]


# Unique, obviously-fake markers. Never real credentials.
DOTENV_SENTINEL_TOKEN = "BACKTEST-RUNTIME-SENTINEL-DOTENV-9f1c7a3e"
PROCENV_SENTINEL_TOKEN = "BACKTEST-RUNTIME-SENTINEL-PROCENV-2a7ed915"
SENTINEL_TOKENS = (DOTENV_SENTINEL_TOKEN, PROCENV_SENTINEL_TOKEN)


def keys_with_token(token: str) -> list[str]:
    return sorted(str(key) for key, value in os.environ.items() if token in str(value))


def sentinel_env_keys() -> list[str]:
    return sorted(
        str(key)
        for key, value in os.environ.items()
        if any(token in str(value) for token in SENTINEL_TOKENS)
    )
