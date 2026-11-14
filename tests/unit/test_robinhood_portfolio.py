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


def _inactive_for(*dead: str):
    def history(symbol):
        if symbol in dead:
            raise oc.McpToolError(
                "the MCP tool call to get_equity_historicals returned an error: "
                f'API error 400: {{"inactive_instruments":["{symbol}"]}}',
                f'API error 400: {{"inactive_instruments":["{symbol}"]}}')
        return _history_for(symbol)
    return history


def test_inactive_instrument_is_reported_unpriced_and_left_out_of_totals(positions):
    live = _client(positions).snapshot()
    victim = live.lines[0]

    snapshot = _client(positions, history=_inactive_for(victim.symbol)).snapshot()

    assert [item.symbol for item in snapshot.unpriced] == [victim.symbol]
    unpriced = snapshot.unpriced[0]
    assert unpriced.quantity == victim.quantity and unpriced.cost_basis == victim.cost_basis
    assert victim.symbol not in {line.symbol for line in snapshot.lines}
    assert snapshot.market_value == live.market_value - victim.market_value
    assert snapshot.cost_basis == live.cost_basis - victim.cost_basis


def test_other_history_tool_errors_still_fail_closed(positions):
    def rate_limited(_symbol):
        raise oc.McpToolError("the MCP tool call to get_equity_historicals returned an error: API error 429",
                              "API error 429")

    with pytest.raises(oc.McpToolError, match="429"):
        _client(positions, history=rate_limited).snapshot()


@pytest.mark.parametrize("detail", [
    'API error 400: {"inactive_instruments":["OTHER"]}',              # a different symbol
    'API error 400: {"inactive_instruments":["{sym}","OTHER"]}',      # extra symbols
    'API error 400: {"inactive_instruments":[]}',                     # empty list
    'API error 400: {"inactive_instruments":"{sym}"}',                # not a list
    'API error 400: {"inactive_instruments":[1]}',                    # wrong item type
    'API error 400: {"inactive_instruments":["{sym}"],"x":1}',        # extra keys
    'API error 400: {"inactive_instruments":["{sym}"',                # truncated JSON
    'API error 400: inactive_instruments for {sym}',                  # only mentions the field
    'API error 500: see inactive_instruments docs',                   # diagnostic mention
    'API error 500: {"inactive_instruments":["{sym}"]}',              # valid object, wrong status
    'API error 400: {"inactive_instruments":["{sym}"]} trailing unrelated failure',  # trailing text
    'API error 400: {"inactive_instruments":["{sym}"]} {"error":"upstream timeout"}',  # compound error
    'upstream: API error 400: {"inactive_instruments":["{sym}"]}',    # unrelated leading text
    '{"inactive_instruments":["{sym}"]}',                             # no status at all
    '',                                                               # no text
])
def test_inactive_error_not_naming_exactly_this_symbol_fails_closed(positions, detail):
    victim = _client(positions).snapshot().lines[0].symbol
    text = detail.replace("{sym}", victim)

    def bad(symbol):
        if symbol == victim:
            raise oc.McpToolError(f"the MCP tool call returned an error: {text}", text)
        return _history_for(symbol)

    with pytest.raises(oc.McpToolError):
        _client(positions, history=bad).snapshot()


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
