"""Executable entry point.

docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 2: every
credential-safety property this distribution provides depends on the three
statements below running, in exactly this order, before anything in this
process imports `lumibot` -- which happens transitively the moment `.cli`
(and the `.strategy` module it imports) is imported. Do not reorder these
statements, and do not move the `.cli` import above them.
"""
from __future__ import annotations

import sys

from .credential_guard import scrub_credential_environment, suppress_dotenv_discovery

scrub_credential_environment()
suppress_dotenv_discovery()

# LumiBot prints an unguarded startup banner and progress bars directly to fd
# 1 the moment it is imported/run. Results travel by file (Decision 3), so
# this cannot corrupt the result contract, but the redirect keeps log output
# on stderr and is required regardless -- see the ADR's "stdout is not a
# channel" section.
sys.stdout = sys.stderr

from .cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
