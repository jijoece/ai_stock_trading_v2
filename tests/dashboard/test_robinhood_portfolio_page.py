from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.pages import robinhood_portfolio as page
from dashboard.services.robinhood_portfolio_service import ENABLE_ENV, PortfolioViewError, load_portfolio
from trading_research.mcp.oauth_readonly_client import McpLoginRequired
from trading_research.mcp.robinhood_portfolio import PortfolioLine, PortfolioSnapshot

SNAPSHOT = PortfolioSnapshot(
    lines=(
        PortfolioLine("AAA", Decimal("10"), Decimal("10"), Decimal("15")),
        PortfolioLine("BBB", Decimal("2"), Decimal("100"), Decimal("90")),
    ),
    as_of_session=date(2026, 11, 13),
    accounts_checked=2,
)


class _Client:
    def __init__(self, result=SNAPSHOT, error: Exception | None = None):
        self._result, self._error = result, error

    def snapshot(self):
        if self._error:
            raise self._error
        return self._result


def test_load_portfolio_refuses_when_disabled(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(PortfolioViewError, match="disabled"):
        load_portfolio(lambda: pytest.fail("must not be built"))


def test_load_portfolio_returns_snapshot(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    assert load_portfolio(lambda: _Client()) is SNAPSHOT


def test_load_portfolio_shows_login_hint_but_hides_unexpected_errors(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    with pytest.raises(PortfolioViewError, match="login"):
        load_portfolio(lambda: _Client(error=McpLoginRequired("not logged in; run mcp_login.py login robinhood")))
    with pytest.raises(PortfolioViewError) as info:
        load_portfolio(lambda: _Client(error=ValueError("acct 12345678 secret")))
    assert "12345678" not in str(info.value) and "ValueError" in str(info.value)


def test_page_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    app = AppTest.from_string(
        "from dashboard.pages.robinhood_portfolio import render\nrender()", default_timeout=10
    ).run()
    assert not app.exception
    assert app.title[0].value == "Robinhood Portfolio"
    assert "disabled" in app.info[0].value
    assert len(app.dataframe) == 0


def test_page_renders_totals_and_positions(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setattr(page, "load_portfolio", lambda: SNAPSHOT)
    app = AppTest.from_string(
        "from dashboard.pages.robinhood_portfolio import render\nrender()", default_timeout=10
    ).run()

    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Market value"] == "$330.00"
    assert metrics["Cost basis"] == "$300.00"
    assert metrics["Unrealized P/L"] == "$30.00"
    assert list(app.dataframe[0].value["Symbol"]) == ["BBB", "AAA"]  # sorted by value, largest first
    assert "2026-11-13" in app.caption[1].value


def test_page_shows_sanitized_error(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")

    def boom():
        raise PortfolioViewError("not logged in")

    monkeypatch.setattr(page, "load_portfolio", boom)
    app = AppTest.from_string(
        "from dashboard.pages.robinhood_portfolio import render\nrender()", default_timeout=10
    ).run()
    assert app.error[0].value == "not logged in"
