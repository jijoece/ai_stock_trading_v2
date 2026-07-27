"""Fail-closed test guards for the LumiBot 4.5.78 backtest feasibility spike.

Install BEFORE importing lumibot. Two guards:

1. env-read tracing: every read of os.environ is recorded, so we can state
   exactly which (if any) broker-credential variables LumiBot consults.
2. network fail-closed: DNS resolution and TCP connect raise NetworkBlocked
   instead of succeeding. Any attempt is recorded with a stack summary.
"""
from __future__ import annotations

import os
import socket
import traceback

ENV_READS: list[str] = []
NETWORK_ATTEMPTS: list[dict] = []


class NetworkBlocked(RuntimeError):
    pass


def install_env_tracer() -> None:
    real = os.environ

    class TracingEnviron(type(real)):  # type: ignore[misc]
        def __getitem__(self, key):
            ENV_READS.append(str(key))
            return super().__getitem__(key)

        def get(self, key, default=None):
            ENV_READS.append(str(key))
            return super().get(key, default)

        def __contains__(self, key):
            ENV_READS.append(str(key))
            return super().__contains__(key)

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


def credential_reads() -> list[str]:
    seen = []
    for key in ENV_READS:
        upper = key.upper()
        if any(marker in upper for marker in CREDENTIAL_KEY_MARKERS) and key not in seen:
            seen.append(key)
    return seen
