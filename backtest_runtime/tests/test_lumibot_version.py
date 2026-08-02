"""The resolved LumiBot version in this distribution's own environment must
be exactly the pin (docs/adr/0009-lumibot-backtest-distribution-boundary.md
Decision 4, item 4: "asserts the resolved LumiBot version is exactly
4.5.78"). Protected by `conftest.py`'s session-wide credential guard, same as
every other test module here that imports `lumibot`."""
from __future__ import annotations

import lumibot

from backtest_runtime import LUMIBOT_PINNED_VERSION


def test_resolved_lumibot_version_matches_the_pin():
    assert lumibot.__version__ == LUMIBOT_PINNED_VERSION == "4.5.78"
