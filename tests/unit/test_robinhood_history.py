"""Robinhood price-history domain layer (`trading_research.mcp.robinhood_history`).

No network and no OAuth: the response parser runs on a response captured from the real
`get_equity_historicals` tool (tests/fixtures/robinhood_historicals_spy_day.json), and the
client runs over the generic read-only client with an injected transport. OAuth, token
storage and the read-only policy are covered in `test_oauth_readonly_client.py`.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.mcp import oauth_readonly_client as oc
from trading_research.mcp import robinhood_history as rh

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "robinhood_historicals_spy_day.json"
LAST = date(2026, 11, 12)  # the fixture's last real bar; 2026-11-13 is a gap-fill
AS_OF = datetime(2026, 11, 12, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _bars(p: dict) -> list[dict]:
    return p["data"]["results"][0]["bars"]


# -- parsing -------------------------------------------------------------------------


def test_fixture_parses_and_drops_the_gap_fill_after_the_as_of_session(payload):
    bars = rh.parse_historicals(payload, symbol="SPY", last_session=LAST)
    assert [b.session_date for b in bars] == [date(2026, 11, d) for d in (9, 10, 11, 12)]
    assert bars[-1].close == Decimal("757.120000")
    assert all(b.provider == "robinhood-mcp" and b.adjusted and b.symbol == "SPY" for b in bars)


def test_a_placeholder_for_the_as_of_session_says_it_is_not_published_yet(payload):
    # Real behaviour seen on 2026-11-14: the session traded (hourly bars exist) but its daily bar is a placeholder.
    with pytest.raises(rh.RobinhoodHistoryError, match=r"gap-filled .* not published that session's daily bar"):
        rh.parse_historicals(payload, symbol="SPY", last_session=date(2026, 11, 13))


def test_a_gap_fill_in_the_middle_of_the_history_is_refused(payload):
    _bars(payload)[1]["interpolated"] = True  # 2026-11-10, well before the as-of session
    with pytest.raises(rh.RobinhoodHistoryError, match="refusing to score a hole"):
        rh.parse_historicals(payload, symbol="SPY", last_session=LAST)


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p.update(data=None), "unexpected response shape"),
    (lambda p: p["data"]["results"].append(copy.deepcopy(p["data"]["results"][0])), "exactly one result"),
    (lambda p: p["data"]["results"][0].update(symbol="QQQ"), "exactly one result"),
    (lambda p: p["data"]["results"][0].update(interval="week"), "not regular-hours daily"),
    (lambda p: p["data"]["results"][0].update(bounds="extended"), "not regular-hours daily"),
    (lambda p: _bars(p)[0].update(session="pre"), "not a regular session"),
    (lambda p: _bars(p)[0].update(begins_at="2026-11-09T05:00:00Z"), "00:00Z"),
    (lambda p: _bars(p)[0].update(begins_at="2026-11-09T00:00:00-05:00"), "not UTC"),
    (lambda p: _bars(p)[0].update(begins_at="2026-11-07T00:00:00Z"), "not a trading session"),  # Saturday
    (lambda p: _bars(p)[0].update(close_price="abc"), "not a number"),
    (lambda p: _bars(p)[0].update(close_price="-1"), "positive"),
    (lambda p: _bars(p)[0].update(close_price="NaN"), "positive"),
    (lambda p: _bars(p)[0].update(close_price=752.69), "decimal string"),
    (lambda p: _bars(p)[0].update(volume="5"), "non-integer volume"),
    (lambda p: _bars(p)[0].update(high_price="700"), "high"),  # high below low/open/close
    (lambda p: _bars(p).pop(1), "missing inside"),  # 2026-11-10 absent
])
def test_malformed_or_inconsistent_responses_fail_closed(payload, mutate, fragment):
    mutate(payload)
    with pytest.raises(rh.RobinhoodHistoryError, match=fragment):
        rh.parse_historicals(payload, symbol="SPY", last_session=LAST)


# -- client ---------------------------------------------------------------------------------


def _client(payload, calls: list) -> rh.RobinhoodHistoryClient:
    def transport(name, args):
        calls.append((name, args))
        return payload
    mcp = oc.ReadOnlyOAuthMcpClient(oc.ROBINHOOD, allowed_tools=[rh.TOOL_NAME], min_interval=0,
                                    token_store=oc.FileTokenStore(Path("/nonexistent/x.json")),
                                    transport=transport)
    return rh.RobinhoodHistoryClient(mcp)


def test_client_asks_for_split_adjusted_regular_daily_bars_ending_after_the_as_of_session(payload):
    calls: list = []
    bars = _client(payload, calls).get_price_history(
        "SPY", start=date(2026, 11, 9), end=LAST, as_of=AS_OF, adjustment="split")
    ((name, args),) = calls
    assert name == "get_equity_historicals"
    assert args == {
        "symbols": ["SPY"], "interval": "day", "bounds": "regular", "adjustment_type": "split",
        "start_time": "2026-11-09T00:00:00Z", "end_time": "2026-11-13T00:00:00Z",
    }
    assert len(bars) == 4


def test_client_refuses_other_adjustments_and_naive_as_of(payload):
    client = _client(payload, [])
    with pytest.raises(rh.RobinhoodHistoryError, match="split-adjusted only"):
        client.get_price_history("SPY", start=date(2026, 11, 9), end=LAST, as_of=AS_OF, adjustment="all")
    with pytest.raises(rh.RobinhoodHistoryError, match="timezone-aware"):
        client.get_price_history("SPY", start=date(2026, 11, 9), end=LAST, as_of=datetime(2026, 11, 12))


def test_history_client_needs_the_historicals_tool_enabled():
    mcp = oc.ReadOnlyOAuthMcpClient(oc.ROBINHOOD, allowed_tools=["get_equity_quotes"], min_interval=0,
                                    token_store=oc.FileTokenStore(Path("x")), transport=lambda *_: {})
    with pytest.raises(rh.RobinhoodHistoryError, match="does not enable"):
        rh.RobinhoodHistoryClient(mcp)


def test_history_client_cannot_reach_any_other_tool(payload):
    client = _client(payload, [])
    with pytest.raises(oc.McpPolicyError, match="not enabled"):
        client._mcp.call("place_equity_order", {})


def test_login_helper_path_still_works(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_PATH", str(tmp_path / "oauth.json"))
    assert rh.main(["status"]) == 0 and "not logged in" in capsys.readouterr().out


def test_client_from_environment_without_login_raises_the_login_error(tmp_path):
    with pytest.raises(rh.McpLoginRequired, match="login"):
        rh.robinhood_client_from_environment({"ROBINHOOD_MCP_TOKEN_PATH": str(tmp_path / "none.json")})
