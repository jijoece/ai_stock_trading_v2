"""Robinhood portfolio view (`trading_research.mcp.robinhood_portfolio`). No network, no OAuth."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.mcp import oauth_readonly_client as oc
from trading_research.mcp import robinhood_portfolio as rp

FIX = Path(__file__).resolve().parent.parent / "fixtures"
NOW = datetime(2026, 11, 12, 22, 0, tzinfo=timezone.utc)  # after the 16:00 ET close of Thursday 2026-11-12
SESSION = date(2026, 11, 12)
TOOLS = ["get_accounts", "get_equity_positions", "get_equity_historicals"]


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def _history_for(symbol: str) -> dict:
    payload = _load("robinhood_historicals_spy_day.json")
    result = payload["data"]["results"][0]
    result["symbol"] = symbol
    result["bars"] = result["bars"][:4]  # 2026-11-09..2026-11-12; last close 757.12
    return payload


def _client(positions: dict, history=_history_for, calls: list | None = None, tools=TOOLS):
    def transport(name, args):
        if calls is not None:
            calls.append((name, args))
        if name == "get_accounts":
            return _load("robinhood_accounts.json")
        if name == "get_equity_positions":
            return positions[args["account_number"]]
        return history(args["symbols"][0])

    mcp = oc.ReadOnlyOAuthMcpClient(oc.ROBINHOOD, allowed_tools=tools, min_interval=0,
                                    token_store=oc.FileTokenStore(Path("/nonexistent/x.json")),
                                    transport=transport)
    return rp.RobinhoodPortfolioClient(mcp, now=lambda: NOW)


@pytest.fixture
def positions() -> dict:
    return {"ACCT-0001": _load("robinhood_positions_multi.json"),
            "ACCT-0002": _load("robinhood_positions_single.json")}


def test_snapshot_totals_positions_across_accounts_at_last_close(positions):
    snapshot = _client(positions).snapshot()

    assert snapshot.as_of_session == SESSION
    assert snapshot.accounts_checked == 2
    by_symbol = {line.symbol: line for line in snapshot.lines}
    nvda = by_symbol["NVDA"]
    assert nvda.last_close == Decimal("757.12")
    assert nvda.market_value == nvda.quantity * Decimal("757.12")
    assert snapshot.market_value == sum(line.market_value for line in snapshot.lines)
    assert snapshot.unrealized_pnl == snapshot.market_value - snapshot.cost_basis


def test_average_cost_is_quantity_weighted_across_accounts():
    payload = {"data": {"positions": [{"symbol": "AAA", "quantity": "10", "average_buy_price": "10", "type": "long"}]}}
    other = {"data": {"positions": [{"symbol": "AAA", "quantity": "30", "average_buy_price": "20", "type": "long"}]}}
    snapshot = _client({"ACCT-0001": payload, "ACCT-0002": other}).snapshot()

    (line,) = snapshot.lines
    assert line.quantity == 40
    assert line.average_cost == Decimal("17.5")  # (10*10 + 30*20) / 40


def test_only_the_three_read_only_tools_are_called(positions):
    calls: list = []
    _client(positions, calls=calls).snapshot()
    assert {name for name, _ in calls} == set(TOOLS)


def test_zero_quantity_positions_are_ignored():
    payload = {"data": {"positions": [{"symbol": "AAA", "quantity": "0.000000", "average_buy_price": "10", "type": "long"}]}}
    assert _client({"ACCT-0001": payload, "ACCT-0002": payload}).snapshot().lines == ()


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p["data"]["positions"][0].update(type="short"), "not a plain long"),
    (lambda p: p["data"]["positions"][0].update(quantity="abc"), "not a number"),
    (lambda p: p["data"]["positions"][0].update(quantity="-1"), "out of range"),
    (lambda p: p["data"]["positions"][0].update(average_buy_price=None), "decimal string"),
    (lambda p: p["data"].update(positions="nope"), "unexpected positions"),
])
def test_malformed_positions_fail_closed(positions, mutate, fragment):
    bad = copy.deepcopy(positions)
    mutate(bad["ACCT-0001"])
    with pytest.raises(rp.RobinhoodPortfolioError, match=fragment):
        _client(bad).snapshot()


def test_missing_last_close_fails_closed(positions):
    def stale(symbol):
        payload = _history_for(symbol)
        payload["data"]["results"][0]["bars"].pop()  # drop 2026-11-12
        return payload

    with pytest.raises(rp.McpError):
        _client(positions, history=stale).snapshot()


def test_pnl_percent_handles_zero_cost():
    line = rp.PortfolioLine("AAA", Decimal(1), Decimal(0), Decimal(5))
    assert line.unrealized_pnl_pct is None
    assert rp.PortfolioLine("AAA", Decimal(2), Decimal(10), Decimal(15)).unrealized_pnl_pct == Decimal(50)


def test_client_requires_all_three_tools():
    with pytest.raises(rp.RobinhoodPortfolioError, match="does not enable"):
        _client({}, tools=["get_accounts", "get_equity_positions"])


def test_last_completed_session_uses_close_time():
    assert rp.last_completed_session(datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)) == date(2026, 11, 11)
    assert rp.last_completed_session(NOW) == SESSION
    with pytest.raises(rp.RobinhoodPortfolioError):
        rp.last_completed_session(datetime(2026, 11, 12, 22, 0))


def test_client_from_environment_without_login_raises_the_login_error(tmp_path):
    with pytest.raises(rp.McpError, match="login"):
        rp.robinhood_portfolio_client_from_environment({"ROBINHOOD_MCP_TOKEN_PATH": str(tmp_path / "none.json")})
