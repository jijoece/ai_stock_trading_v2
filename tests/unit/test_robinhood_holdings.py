"""Robinhood holdings check (`trading_research.mcp.robinhood_holdings`).

No network and no OAuth: parsers run on redacted copies of real `get_accounts` /
`get_equity_positions` responses (tests/fixtures/robinhood_*.json); the client runs over the generic
read-only client with an injected transport.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.mcp import oauth_readonly_client as oc
from trading_research.mcp import robinhood_holdings as rhh

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


@pytest.fixture
def accounts() -> dict:
    return _load("robinhood_accounts.json")


def _client(responses: dict, calls: list | None = None, tools=(rhh.ACCOUNTS_TOOL, rhh.POSITIONS_TOOL)):
    """`responses`: {tool: payload | {account_number: payload | [pages] | Exception}}."""
    pages: dict = {}

    def transport(name, args):
        if calls is not None:
            calls.append((name, args))
        if name == rhh.ACCOUNTS_TOOL:
            return responses[name]
        by_account = responses[name][args["account_number"]]
        if isinstance(by_account, Exception):
            raise by_account
        if isinstance(by_account, list):
            index = pages.setdefault(args["account_number"], 0)
            pages[args["account_number"]] = index + 1
            return by_account[index]
        return by_account

    mcp = oc.ReadOnlyOAuthMcpClient(oc.ROBINHOOD, allowed_tools=tools, min_interval=0,
                                    token_store=oc.FileTokenStore(Path("/nonexistent/x.json")),
                                    transport=transport)
    return rhh.RobinhoodHoldingsClient(mcp)


def _standard(accounts):
    return {rhh.ACCOUNTS_TOOL: accounts,
            rhh.POSITIONS_TOOL: {"ACCT-0001": _load("robinhood_positions_multi.json"),
                                 "ACCT-0002": _load("robinhood_positions_single.json")}}


# -- parsing ---------------------------------------------------------------------------------


def test_active_accounts_are_listed_and_inactive_ones_skipped(accounts):
    accounts["data"]["accounts"].append({"account_number": "ACCT-0003", "state": "deactivated",
                                         "deactivated": True, "permanently_deactivated": False})
    assert rhh.parse_active_accounts(accounts) == ["ACCT-0001", "ACCT-0002"]


@pytest.mark.parametrize("payload, fragment", [
    ({"data": None}, "accounts response shape"),
    ({"data": {"accounts": []}}, "no active"),
    ({"data": {"accounts": [{"account_number": "", "state": "active"}]}}, "no account_number"),
    ({"data": {"accounts": [{"account_number": "X", "state": "active", "deactivated": True,
                             "permanently_deactivated": False}]}}, "no active"),
])
def test_bad_accounts_responses_fail_closed(payload, fragment):
    with pytest.raises(rhh.RobinhoodHoldingsError, match=fragment):
        rhh.parse_active_accounts(payload)


def test_positions_parse_to_symbol_quantities():
    longs, other = rhh.parse_positions(_load("robinhood_positions_multi.json"))
    assert longs == {"AAA": Decimal("39"), "BBB": Decimal("2"), "NVDA": Decimal("5")} and other == set()


@pytest.mark.parametrize("position_type", ["short", "weird", None])
def test_a_non_long_position_is_recorded_not_interpreted(position_type):
    payload = _load("robinhood_positions_multi.json")
    payload["data"]["positions"][0].update(type=position_type, quantity="-3.0")  # quantity is not even read
    longs, other = rhh.parse_positions(payload)
    assert other == {"AAA"} and "AAA" not in longs and longs["NVDA"] == Decimal("5")


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p.update(data=None), "positions response shape"),
    (lambda p: p["data"]["positions"][0].update(quantity="abc"), "not a number"),
    (lambda p: p["data"]["positions"][0].update(quantity="-1"), "non-negative"),
    (lambda p: p["data"]["positions"][0].update(quantity=1.5), "decimal string"),
    (lambda p: p["data"]["positions"][0].update(symbol=""), "no symbol"),
    (lambda p: p["data"]["positions"].append("junk"), "not an object"),
])
def test_bad_positions_responses_fail_closed(mutate, fragment):
    payload = _load("robinhood_positions_multi.json")
    mutate(payload)
    with pytest.raises(rhh.RobinhoodHoldingsError, match=fragment):
        rhh.parse_positions(payload)


# -- client ------------------------------------------------------------------------------------


def test_snapshot_totals_quantities_across_all_active_accounts(accounts):
    calls: list = []
    snap = _client(_standard(accounts), calls).snapshot()
    assert snap.accounts_checked == 2
    assert snap.quantities["NVDA"] == Decimal("7")  # 5 in one account + 2 in the other
    assert snap.holds("nvda") and snap.holds("AAA") and not snap.holds("TSLA")
    assert [c[0] for c in calls] == ["get_accounts", "get_equity_positions", "get_equity_positions"]
    assert {c[1]["account_number"] for c in calls[1:]} == {"ACCT-0001", "ACCT-0002"}


def test_a_zero_quantity_position_is_not_holding(accounts):
    zero = _load("robinhood_positions_single.json")
    zero["data"]["positions"][0]["quantity"] = "0.000000"
    responses = _standard(accounts)
    responses[rhh.POSITIONS_TOOL] = {"ACCT-0001": {"data": {"positions": []}}, "ACCT-0002": zero}
    assert not _client(responses).snapshot().holds("NVDA")


def test_pagination_follows_the_cursor_and_totals_every_page(accounts):
    page1 = _load("robinhood_positions_single.json")
    page1["data"]["next"] = "cursor-2"
    page2 = _load("robinhood_positions_single.json")
    responses = _standard(accounts)
    responses[rhh.POSITIONS_TOOL]["ACCT-0002"] = [page1, page2]
    calls: list = []
    snap = _client(responses, calls).snapshot()
    assert snap.quantities["NVDA"] == Decimal("9")  # 5 + 2 + 2
    assert [c[1].get("cursor") for c in calls if c[1].get("account_number") == "ACCT-0002"] == [None, "cursor-2"]


def test_a_cursor_that_never_advances_is_refused(accounts):
    looping = _load("robinhood_positions_single.json")
    looping["data"]["next"] = "same"
    responses = _standard(accounts)
    responses[rhh.POSITIONS_TOOL]["ACCT-0002"] = [looping] * 5
    with pytest.raises(rhh.RobinhoodHoldingsError, match="did not advance"):
        _client(responses).snapshot()


def test_if_any_account_cannot_be_read_the_whole_check_fails(accounts):
    responses = _standard(accounts)
    responses[rhh.POSITIONS_TOOL]["ACCT-0002"] = oc.McpError("boom")
    with pytest.raises(oc.McpError, match="boom"):  # never a partial "not holding"
        _client(responses).snapshot()


def test_errors_never_contain_account_numbers(accounts):
    responses = _standard(accounts)
    bad = copy.deepcopy(_load("robinhood_positions_multi.json"))
    bad["data"]["positions"][0]["quantity"] = "not-a-number"
    responses[rhh.POSITIONS_TOOL]["ACCT-0001"] = bad
    with pytest.raises(rhh.RobinhoodHoldingsError) as exc:
        _client(responses).snapshot()
    assert "ACCT-" not in str(exc.value)


# -- short / non-long positions block only their own symbol ----------------------------------------


def _with_short(accounts, *, symbol="BBB", account="ACCT-0001"):
    responses = _standard(accounts)
    payload = copy.deepcopy(responses[rhh.POSITIONS_TOOL][account])
    for pos in payload["data"]["positions"]:
        if pos["symbol"] == symbol:
            pos["type"] = "short"
    responses[rhh.POSITIONS_TOOL][account] = payload
    return responses


def test_a_short_in_an_unrelated_symbol_does_not_block_the_check(accounts):
    snap = _client(_with_short(accounts, symbol="BBB")).snapshot()
    assert snap.unsupported == {"BBB"}
    assert snap.holds("NVDA") is True and snap.holds("TSLA") is False  # unaffected


def test_asking_about_the_short_symbol_raises_a_clear_error(accounts):
    snap = _client(_with_short(accounts, symbol="BBB")).snapshot()
    with pytest.raises(rhh.RobinhoodHoldingsError, match="BBB .*not a plain long .*cannot be determined"):
        snap.holds("bbb")


def test_long_in_one_account_and_short_in_another_is_not_guessed(accounts):
    responses = _standard(accounts)
    short_nvda = _load("robinhood_positions_single.json")
    short_nvda["data"]["positions"][0]["type"] = "short"
    responses[rhh.POSITIONS_TOOL]["ACCT-0002"] = short_nvda  # NVDA is long 5 in ACCT-0001
    snap = _client(responses).snapshot()
    with pytest.raises(rhh.RobinhoodHoldingsError, match="NVDA"):
        snap.holds("NVDA")


def test_both_tools_must_be_enabled(accounts):
    with pytest.raises(rhh.RobinhoodHoldingsError, match="does not enable"):
        _client(_standard(accounts), tools=(rhh.ACCOUNTS_TOOL,))


def test_the_holdings_client_cannot_place_orders(accounts):
    client = _client(_standard(accounts))
    for tool in ("place_equity_order", "get_portfolio", "cancel_equity_order"):
        with pytest.raises(oc.McpPolicyError):
            client._mcp.call(tool, {})


def test_without_login_the_factory_raises_the_login_error(tmp_path):
    with pytest.raises(oc.McpLoginRequired, match="mcp_login.py login robinhood"):
        rhh.robinhood_holdings_client_from_environment({"ROBINHOOD_MCP_TOKEN_PATH": str(tmp_path / "none.json")})
