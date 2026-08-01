"""Fail-closed test guards for the LumiBot 4.5.78 backtest feasibility spike.

Install BEFORE importing lumibot. Two guards:

1. env-read tracing: every read of os.environ is recorded, together with
   whether the read *resolved to a value*. LumiBot reads credential variable
   names unconditionally, so the read count is not the safety property; the
   safety property is that none of those reads returns a credential value.
2. network fail-closed: DNS resolution and TCP connect raise NetworkBlocked
   instead of succeeding. Any attempt is recorded with a stack summary.
"""
from __future__ import annotations

import os
import socket
import traceback

ENV_READS: list[str] = []
# Keys whose traced read returned a non-empty value. A credential-named key in
# ENV_READS is expected and harmless; the same key here means a credential
# value was actually available to LumiBot.
ENV_READS_WITH_VALUE: list[str] = []
NETWORK_ATTEMPTS: list[dict] = []


class NetworkBlocked(RuntimeError):
    pass


def _record_read(key, value) -> None:
    name = str(key)
    ENV_READS.append(name)
    if value not in (None, "") and name not in ENV_READS_WITH_VALUE:
        ENV_READS_WITH_VALUE.append(name)


def install_env_tracer() -> None:
    real = os.environ

    class TracingEnviron(type(real)):  # type: ignore[misc]
        def __getitem__(self, key):
            try:
                value = super().__getitem__(key)
            except KeyError:
                _record_read(key, None)
                raise
            _record_read(key, value)
            return value

        def get(self, key, default=None):
            value = super().get(key, default)
            _record_read(key, value)
            return value

        def __contains__(self, key):
            present = super().__contains__(key)
            _record_read(key, "x" if present else None)
            return present

    try:
        traced = TracingEnviron(
            real._data, real.encodekey, real.decodekey, real.encodevalue, real.decodevalue
        )
    except Exception:  # pragma: no cover - interpreter-specific fallback
        return
    os.environ = traced  # type: ignore[assignment]


def _record(kind: str, target: object) -> None:
    NETWORK_ATTEMPTS.append(
        {
            "kind": kind,
            "target": repr(target),
            "stack": [
                f"{f.filename}:{f.lineno} {f.name}" for f in traceback.extract_stack()[-8:-1]
            ],
        }
    )


def install_network_guard() -> None:
    real_getaddrinfo = socket.getaddrinfo

    def blocked_getaddrinfo(host, port, *a, **k):
        if host in ("localhost", "127.0.0.1", "::1", None):
            return real_getaddrinfo(host, port, *a, **k)
        _record("getaddrinfo", (host, port))
        raise NetworkBlocked(f"DNS resolution blocked by spike guard: {host}:{port}")

    def blocked_create_connection(address, *a, **k):
        _record("create_connection", address)
        raise NetworkBlocked(f"outbound connection blocked by spike guard: {address}")

    def blocked_connect(self, address, *a, **k):
        _record("socket.connect", address)
        raise NetworkBlocked(f"outbound connection blocked by spike guard: {address}")

    def blocked_connect_ex(self, address, *a, **k):
        _record("socket.connect_ex", address)
        raise NetworkBlocked(f"outbound connection blocked by spike guard: {address}")

    socket.getaddrinfo = blocked_getaddrinfo
    socket.create_connection = blocked_create_connection
    socket.socket.connect = blocked_connect
    socket.socket.connect_ex = blocked_connect_ex


CREDENTIAL_SENTINELS = {
    "ALPACA_API_KEY": "SPIKE-SENTINEL-ALPACA-KEY",
    "ALPACA_API_SECRET": "SPIKE-SENTINEL-ALPACA-SECRET",
    "ALPACA_SECRET_KEY": "SPIKE-SENTINEL-ALPACA-SECRET-KEY",
    "TRADIER_ACCESS_TOKEN": "SPIKE-SENTINEL-TRADIER",
    "POLYGON_API_KEY": "SPIKE-SENTINEL-POLYGON",
    "INTERACTIVE_BROKERS_ACCOUNT": "SPIKE-SENTINEL-IB",
    "SCHWAB_API_KEY": "SPIKE-SENTINEL-SCHWAB",
}

CREDENTIAL_KEY_MARKERS = (
    "KEY", "SECRET", "TOKEN", "PASSWORD", "ACCOUNT", "CREDENTIAL",
    "ALPACA", "TRADIER", "POLYGON", "SCHWAB", "IB_", "COINBASE", "KRAKEN",
)


def _is_credential_named(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in CREDENTIAL_KEY_MARKERS)


def credential_reads() -> list[str]:
    """Credential-named variables LumiBot *looked for*.

    Expected to be non-empty on any `import lumibot`: LumiBot reads the names
    unconditionally. This is not a safety metric on its own — see
    `credential_reads_with_values()`, which is.
    """
    seen = []
    for key in ENV_READS:
        if _is_credential_named(key) and key not in seen:
            seen.append(key)
    return seen


def credential_reads_with_values() -> list[str]:
    """Credential-named variables that actually resolved to a value.

    This is the safety metric. It must be empty: LumiBot may read the names,
    but no broker credential value may be available to it.
    """
    return [key for key in ENV_READS_WITH_VALUE if _is_credential_named(key)]


# Unique, obviously-fake marker written into the sentinel .env / .env.local
# fixtures. Never a real credential. If this substring appears in os.environ or
# in a LumiBot config after import, a dotenv file leaked into the process.
DOTENV_SENTINEL_TOKEN = "SENTINEL-DOTENV-7f3a9c21e4b8"


def sentinel_env_keys() -> list[str]:
    """Environment keys whose value carries the sentinel token."""
    leaked = []
    for key, value in os.environ.items():
        if DOTENV_SENTINEL_TOKEN in str(value):
            leaked.append(str(key))
    return sorted(leaked)


def sentinel_hits_in(obj) -> bool:
    """True if the sentinel token appears anywhere in obj's repr."""
    try:
        return DOTENV_SENTINEL_TOKEN in repr(obj)
    except Exception:  # pragma: no cover - defensive
        return False
