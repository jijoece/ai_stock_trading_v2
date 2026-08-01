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
# Keys whose traced read returned a non-empty value, from EITHER the process
# environment or a default the caller passed to os.environ.get(). A
# credential-named key in ENV_READS is expected and harmless.
ENV_READS_WITH_VALUE: list[str] = []
# Keys whose non-empty value actually came from the process environment, as
# opposed to a hardcoded default LumiBot supplied itself. This is the strict
# metric: os.environ.get("COINBASE_SANDBOX", "false") returning "false" on an
# empty environment is LumiBot talking to itself, not a credential leak.
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
            # Resolve presence explicitly so a returned value can be attributed
            # to the environment or to the caller-supplied default.
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
    """Credential-named variables that resolved to a non-empty value.

    Includes values LumiBot supplied to itself as `.get()` defaults, so this
    list is NOT expected to be empty — see
    `BENIGN_LUMIBOT_DEFAULT_CREDENTIAL_NAMES`. The strict metric is
    `credential_values_from_environment()`.
    """
    return [key for key in ENV_READS_WITH_VALUE if _is_credential_named(key)]


def credential_values_from_environment() -> list[str]:
    """Credential-named variables whose value came from the process environment.

    This is the strict safety metric. It must be empty: a credential-named key
    here means a real value was present in `os.environ` — whether it arrived
    from the ambient environment or from a loaded `.env` — and was handed to
    LumiBot.
    """
    return [key for key in ENV_VALUES_FROM_ENVIRONMENT if _is_credential_named(key)]


# Credential-NAMED variables that legitimately resolve to a value in a fully
# scrubbed process, because LumiBot passes its own hardcoded default to
# os.environ.get(). None of these is a credential:
#
#   COINBASE_SANDBOX              -> "false"            (a mode flag)
#   IB_USE_PAPER_ACCOUNT          -> "true"             (a mode flag)
#   DATADOWNLOADER_API_KEY_HEADER -> "X-Downloader-Key" (an HTTP header NAME)
#
# The evidence summariser asserts the suppressed runs match this set exactly,
# so a LumiBot upgrade that starts resolving any additional credential-named
# variable fails the check instead of being explained away in prose.
BENIGN_LUMIBOT_DEFAULT_CREDENTIAL_NAMES = frozenset(
    {"COINBASE_SANDBOX", "IB_USE_PAPER_ACCOUNT", "DATADOWNLOADER_API_KEY_HEADER"}
)


# Unique, obviously-fake markers. Never real credentials.
#
# DOTENV  — written into the sentinel .env / .env.local fixtures. Appearing in
#           os.environ or a LumiBot config after import means a dotenv file
#           leaked into the process.
# PROCENV — exported into the child process environment by the harness before
#           the interpreter starts. Appearing after import means the
#           credential scrub failed to remove an inherited credential.
#
# Two distinct tokens so the two leak paths can never be confused for each
# other in the evidence.
DOTENV_SENTINEL_TOKEN = "SENTINEL-DOTENV-7f3a9c21e4b8"
PROCENV_SENTINEL_TOKEN = "SENTINEL-PROCENV-3d5b18ca9027"

SENTINEL_TOKENS = (DOTENV_SENTINEL_TOKEN, PROCENV_SENTINEL_TOKEN)

# Fake Alpaca credentials the harness exports into the child environment for
# the process-environment case. Values, not just names, so a leak is visible.
PROCENV_SENTINELS = {
    "ALPACA_API_KEY": f"{PROCENV_SENTINEL_TOKEN}-ALPACA-KEY",
    "ALPACA_API_SECRET": f"{PROCENV_SENTINEL_TOKEN}-ALPACA-SECRET",
    "ALPACA_IS_PAPER": "true",
}


def keys_with_token(token: str) -> list[str]:
    """Environment keys whose value carries the given sentinel token."""
    return sorted(
        str(key) for key, value in os.environ.items() if token in str(value)
    )


def sentinel_env_keys() -> list[str]:
    """Environment keys carrying EITHER sentinel token."""
    return sorted(
        str(key)
        for key, value in os.environ.items()
        if any(token in str(value) for token in SENTINEL_TOKENS)
    )


def sentinel_hits_in(obj) -> list[str]:
    """Which sentinel tokens appear anywhere in obj's repr."""
    try:
        text = repr(obj)
    except Exception:  # pragma: no cover - defensive
        return []
    return [token for token in SENTINEL_TOKENS if token in text]
