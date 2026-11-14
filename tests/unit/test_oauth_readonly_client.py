"""Generic read-only OAuth MCP client (`trading_research.mcp.oauth_readonly_client`).

No network and no OAuth. A made-up second server ("acme") with an injected policy shows the
client is not Robinhood-specific; the real `config/tool_policy.yaml` is checked for Robinhood.
"""
from __future__ import annotations

import asyncio
import builtins
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_research.mcp import oauth_readonly_client as oc

ACME = oc.ServerSpec(name="acme", default_url="https://mcp.acme.example/api", url_env="ACME_MCP_URL",
                     token_path_env="ACME_MCP_TOKEN_PATH")
POLICY = {"acme": {"allowlist": ["get_quote", "get_bars", "place_thing"],
                   "denylist_patterns": ["place", "cancel"]}}


def _client(transport=None, tools=("get_quote",), **kw):
    return oc.ReadOnlyOAuthMcpClient(
        ACME, allowed_tools=tools, token_store=oc.FileTokenStore(Path("/nonexistent/t.json")),
        transport=transport, policy=POLICY, min_interval=0, **kw)


# -- read-only policy ----------------------------------------------------------------------


def test_allowlisted_tool_is_callable_through_any_server_spec():
    calls: list = []
    client = _client(lambda name, args: calls.append((name, args)) or {"data": 1})
    assert client.call("get_quote", {"symbols": ["X"]}) == {"data": 1}
    assert calls == [("get_quote", {"symbols": ["X"]})]


def test_denylist_wins_over_allowlist():
    with pytest.raises(oc.McpPolicyError, match="denylist"):
        _client(tools=("place_thing",))


@pytest.mark.parametrize("tool", ["get_secret", "unknown_tool", ""])
def test_tools_off_the_allowlist_cannot_be_enabled(tool):
    with pytest.raises(oc.McpPolicyError, match="allowlist"):
        _client(tools=(tool,))


def test_a_client_only_calls_the_tools_it_was_built_with():
    client = _client(lambda *_: {}, tools=("get_quote",))
    with pytest.raises(oc.McpPolicyError, match="not enabled"):
        client.call("get_bars", {})  # allowlisted for the server, but not for this client


def test_an_empty_tool_set_is_refused():
    with pytest.raises(oc.McpPolicyError, match="at least one"):
        _client(tools=())


def test_unknown_server_has_no_allowlist():
    with pytest.raises(oc.McpPolicyError):
        oc.assert_read_only("nope", "get_quote", POLICY)


@pytest.mark.parametrize("tool", [
    "place_equity_order", "cancel_equity_order", "place_option_order", "review_equity_order",
    "create_watchlist", "exercise_option", "place_advanced_order",
])
def test_robinhood_order_and_mutation_tools_can_never_be_enabled(tool):
    with pytest.raises(oc.McpPolicyError):
        oc.assert_read_only("robinhood", tool)  # real config/tool_policy.yaml


def test_robinhood_read_tools_are_allowlisted_in_the_real_policy():
    for tool in ("get_equity_historicals", "get_equity_quotes"):
        oc.assert_read_only("robinhood", tool)


def test_client_requires_https():
    with pytest.raises(oc.McpError, match="https"):
        _client(url="http://mcp.acme.example")


def test_calls_are_spaced_out(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(oc.time, "sleep", sleeps.append)
    client = oc.ReadOnlyOAuthMcpClient(ACME, allowed_tools=["get_quote"], policy=POLICY, min_interval=60.0,
                                       token_store=oc.FileTokenStore(Path("x")), transport=lambda *_: {})
    client.call("get_quote", {})
    client.call("get_quote", {})
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 60.0


# -- MCP result handling and error scrubbing ------------------------------------------------


def test_payload_prefers_structured_content_then_text():
    payload = {"data": {"results": []}}
    assert oc._payload_from_result(SimpleNamespace(isError=False, structuredContent=payload, content=[])) == payload
    text = SimpleNamespace(text=json.dumps(payload))
    assert oc._payload_from_result(SimpleNamespace(isError=False, structuredContent=None, content=[text])) == payload
    with pytest.raises(oc.McpError, match="returned an error"):
        oc._payload_from_result(SimpleNamespace(isError=True, structuredContent=None, content=[]))
    with pytest.raises(oc.McpError, match="no JSON payload"):
        oc._payload_from_result(SimpleNamespace(isError=False, structuredContent=None,
                                                content=[SimpleNamespace(text="not json")]))


def test_transport_errors_never_forward_provider_text(monkeypatch):
    async def boom(*_a):
        raise RuntimeError("Bearer secret-token-123 rejected")
    monkeypatch.setattr(oc, "_call_tool", boom)
    client = oc.ReadOnlyOAuthMcpClient(ACME, allowed_tools=["get_quote"], policy=POLICY, min_interval=0,
                                       token_store=oc.FileTokenStore(Path("x")))
    with pytest.raises(oc.McpError) as exc:
        client.call("get_quote", {})
    assert "secret-token-123" not in str(exc.value) and "RuntimeError" in str(exc.value)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup needs Python 3.11+")
def test_login_required_surfaces_through_sdk_exception_groups(monkeypatch):
    async def boom(*_a):
        group = getattr(builtins, "ExceptionGroup")  # absent on 3.10, which pyright targets
        raise group("tg", [ValueError("x"), oc.McpLoginRequired("log in")])
    monkeypatch.setattr(oc, "_call_tool", boom)
    client = oc.ReadOnlyOAuthMcpClient(ACME, allowed_tools=["get_quote"], policy=POLICY, min_interval=0,
                                       token_store=oc.FileTokenStore(Path("x")))
    with pytest.raises(oc.McpLoginRequired):
        client.call("get_quote", {})


async def test_unauthenticated_run_asks_for_a_login_instead_of_opening_a_browser():
    with pytest.raises(oc.McpLoginRequired, match="scripts/mcp_login.py login acme"):
        await oc._login_required(ACME)


# -- token storage -------------------------------------------------------------------------------


def _token():
    from mcp.shared.auth import OAuthToken
    return OAuthToken(access_token="at-1", token_type="Bearer", expires_in=3600, refresh_token="rt-1")


async def test_token_store_round_trips_privately(tmp_path):
    store = oc.FileTokenStore(tmp_path / "cfg" / "oauth.json")
    assert not store.has_tokens() and await store.get_tokens() is None
    await store.set_tokens(_token())
    assert store.has_tokens()
    loaded = await store.get_tokens()
    assert loaded is not None and loaded.refresh_token == "rt-1"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    store.clear()
    assert not store.path.exists()


async def test_token_store_refuses_a_file_other_users_can_read(tmp_path):
    store = oc.FileTokenStore(tmp_path / "oauth.json")
    await store.set_tokens(_token())
    os.chmod(store.path, 0o644)
    with pytest.raises(oc.McpError, match="readable by other users"):
        store.has_tokens()


def test_token_store_rejects_corrupt_file(tmp_path):
    path = tmp_path / "oauth.json"
    path.write_text("{not json")
    os.chmod(path, 0o600)
    with pytest.raises(oc.McpError, match="corrupt"):
        oc.FileTokenStore(path).has_tokens()


def test_default_token_path_is_per_server_and_honours_overrides(tmp_path):
    assert oc.default_token_path(ACME, {"ACME_MCP_TOKEN_PATH": str(tmp_path / "t.json")}) == tmp_path / "t.json"
    assert oc.default_token_path(ACME, {"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "trading_research" / "acme_mcp_oauth.json")
    assert oc.default_token_path(oc.ROBINHOOD, {"XDG_CONFIG_HOME": str(tmp_path)}).name == "robinhood_mcp_oauth.json"


def test_client_from_environment_requires_a_prior_login(tmp_path):
    env = {"ROBINHOOD_MCP_TOKEN_PATH": str(tmp_path / "oauth.json")}
    with pytest.raises(oc.McpLoginRequired, match="scripts/mcp_login.py login robinhood"):
        oc.client_from_environment(oc.ROBINHOOD, allowed_tools=["get_equity_historicals"], env=env)
    asyncio.run(oc.FileTokenStore(Path(env["ROBINHOOD_MCP_TOKEN_PATH"])).set_tokens(_token()))
    client = oc.client_from_environment(oc.ROBINHOOD, allowed_tools=["get_equity_historicals"], env=env)
    assert client.allowed_tools == {"get_equity_historicals"}


def test_status_and_logout_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_PATH", str(tmp_path / "oauth.json"))
    assert oc.main(["status", "robinhood"]) == 0 and "not logged in" in capsys.readouterr().out
    assert oc.main(["logout", "robinhood"]) == 0
    with pytest.raises(SystemExit):  # the server name is required
        oc.main(["status"])
