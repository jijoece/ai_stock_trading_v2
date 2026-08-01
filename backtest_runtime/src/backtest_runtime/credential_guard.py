"""Credential scrub and .env suppression, applied before `import lumibot`.

docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 2: LumiBot
4.5.78 reads 64 credential-named environment variables unconditionally on
import, and separately loads `.env`/`.env.local` by walking upward from both
the script directory and the current working directory to the filesystem
root. Neither discovery walk can be pointed at a path guaranteed to be empty
-- the walk always reaches an ancestor's `.env` first (proved by the pre-step
sentinel evidence, `docs/library-migration/pre-step-06/dotenv_sentinel_output.txt`,
run S5: an empty CWD still loaded the parent's `.env`). The only verified
suppression is: delete every credential-named variable from the process
environment, AND set `LUMIBOT_DISABLE_DOTENV=1` -- both, before any `lumibot`
import. Neither alone is sufficient (S1/S5 show the flag alone leaks an
inherited process credential; P1 shows the scrub alone leaks a `.env`
credential).
"""
from __future__ import annotations

import os

LUMIBOT_DISABLE_DOTENV_VAR = "LUMIBOT_DISABLE_DOTENV"

# The exact marker set docs/library-migration/pre-step-06/guards.py's
# feasibility spike used to identify every credential-named variable LumiBot
# 4.5.78 reads at import.
CREDENTIAL_KEY_MARKERS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "ACCOUNT",
    "CREDENTIAL",
    "ALPACA",
    "TRADIER",
    "POLYGON",
    "SCHWAB",
    "IB_",
    "COINBASE",
    "KRAKEN",
)

# Credential-named variables that resolve to a LumiBot-internal hardcoded
# `.get()` default even in a fully scrubbed process (ADR 0009 Decision 2,
# proved by the pre-step sentinel evidence). None of these is a credential.
# A LumiBot upgrade that resolves a fourth credential-named variable must be
# caught by the CI check asserting this set exactly, not by silently
# widening this tuple.
BENIGN_LUMIBOT_DEFAULT_CREDENTIAL_NAMES = frozenset(
    {"COINBASE_SANDBOX", "IB_USE_PAPER_ACCOUNT", "DATADOWNLOADER_API_KEY_HEADER"}
)


def is_credential_named(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in CREDENTIAL_KEY_MARKERS)


def scrub_credential_environment() -> list[str]:
    """Delete every credential-named variable from `os.environ`.

    Returns the names removed, for diagnostics only -- never their values.
    """
    removed = []
    for key in list(os.environ):
        if is_credential_named(key):
            del os.environ[key]
            removed.append(key)
    return removed


def suppress_dotenv_discovery() -> None:
    """Set `LUMIBOT_DISABLE_DOTENV=1` -- the only mechanism that works.

    `lumibot/credentials.py` reads this flag at module scope, before either
    its script-directory or working-directory `.env` discovery walk runs,
    and skips both entirely when it is set. `chdir` does not help: both
    walks ascend to the filesystem root from directories this process does
    not control (`sys.argv[0]`'s directory and `os.getcwd()`).
    """
    os.environ[LUMIBOT_DISABLE_DOTENV_VAR] = "1"
