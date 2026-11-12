"""Generic read-only client for OAuth-protected, hosted MCP servers.

One reusable building block for any code that needs *data* from a hosted MCP
server (price history, quotes for a stale-data check, read-only account state
for reconciliation) without being able to *act* on it. Domain logic (what to
ask for, how to validate the answer) lives with the caller, for example
``robinhood_history.py``.

Read-only is enforced here, not left to callers:

* Each client instance is built with an explicit ``allowed_tools`` set
  (least privilege). Every name in it must also be on the server's read-only
  allowlist in ``config/tool_policy.yaml`` and must not match one of that
  server's ``denylist_patterns`` (the denylist wins, as in ``tool_classifier``).
  Anything else raises :class:`McpPolicyError` before a network call is made.
* There is no order, transfer or account-mutation path in this module. Order
  placement belongs to the isolated, separately gated execution runtime
  described in ``docs/automated-live-trading-implementation-plan.md``; it must
  not be built on this client.
* Provider text is never forwarded in errors (it may echo tokens).

A new server is one :class:`ServerSpec` in ``SERVERS`` plus a section in
``config/tool_policy.yaml``. Tokens live behind the :class:`TokenStore`
protocol; the default is a private ``0600`` file outside the repository, and an
OS-credential-store implementation can be swapped in without touching callers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .tool_classifier import load_policy

MIN_CALL_INTERVAL_SECONDS = 0.5
LOGIN_TIMEOUT_SECONDS = 300.0
_CALLBACK_PATH = "/callback"


class McpError(Exception):
    """A read-only MCP call could not be completed."""


class McpLoginRequired(McpError):
    """No usable OAuth session: a human must run the login command."""


class McpPolicyError(McpError):
    """A tool is not permitted for this client by the read-only policy."""


@dataclass(frozen=True)
class ServerSpec:
    name: str  # key in config/tool_policy.yaml; also names the default token file
    default_url: str
    url_env: str
    token_path_env: str
    client_name: str = "trading_research read-only client"

    @property
    def login_command(self) -> str:
        return f"python3 scripts/mcp_login.py login {self.name}"


ROBINHOOD = ServerSpec(
    name="robinhood",
    default_url="https://agent.robinhood.com/mcp/trading",
    url_env="ROBINHOOD_MCP_URL",
    token_path_env="ROBINHOOD_MCP_TOKEN_PATH",
    client_name="trading_research read-only price history",
)
SERVERS: dict[str, ServerSpec] = {ROBINHOOD.name: ROBINHOOD}


# --------------------------------------------------------------------------
# Read-only policy
# --------------------------------------------------------------------------

def assert_read_only(server: str, tool: str, policy: Mapping[str, Any] | None = None) -> None:
    """Raise unless `tool` is allowlisted for `server` and matches no denylist pattern."""
    section = (policy if policy is not None else load_policy()).get(server, {})
    lowered = tool.lower()
    if any(p.lower() in lowered for p in section.get("denylist_patterns", [])):
        raise McpPolicyError(f"{tool!r} matches a denylist pattern for {server!r}")
    if tool not in set(section.get("allowlist", [])):
        raise McpPolicyError(f"{tool!r} is not on the read-only allowlist for {server!r}")


# --------------------------------------------------------------------------
# Token storage
# --------------------------------------------------------------------------

class TokenStore(Protocol):
    """The MCP SDK's ``TokenStorage`` plus the two helpers this module needs."""

    async def get_tokens(self) -> Any: ...
    async def set_tokens(self, tokens: Any) -> None: ...
    async def get_client_info(self) -> Any: ...
    async def set_client_info(self, client_info: Any) -> None: ...
    def has_tokens(self) -> bool: ...
    def clear(self) -> None: ...


def default_token_path(spec: ServerSpec, env: Mapping[str, str] | None = None) -> Path:
    source: Mapping[str, str] = os.environ if env is None else env
    if source.get(spec.token_path_env):
        return Path(source[spec.token_path_env]).expanduser()
    base = Path(source["XDG_CONFIG_HOME"]) if source.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "trading_research" / f"{spec.name}_mcp_oauth.json"


class FileTokenStore:
    """OAuth tokens and registered client info in one 0600 file (directory 0700).

    Refuses to read a file that is accessible to group/other, and never logs or
    prints its contents.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return {}
        if st.st_mode & 0o077:
            raise McpError(f"{self.path} is readable by other users; run `chmod 600` on it or log in again")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise McpError(f"{self.path} is unreadable or corrupt; log in again") from None
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
        os.replace(tmp, self.path)

    def has_tokens(self) -> bool:
        return bool(self._read().get("tokens"))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


# --------------------------------------------------------------------------
# MCP transport
# --------------------------------------------------------------------------

def _require_https(url: str) -> str:
    if urlparse(url).scheme != "https":
        raise McpError("the MCP server URL must be https")
    return url


def _client_metadata(spec: ServerSpec, redirect_uri: str):
    from mcp.shared.auth import OAuthClientMetadata
    return OAuthClientMetadata(
        redirect_uris=[redirect_uri],  # type: ignore[list-item]
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name=spec.client_name,
    )


def _payload_from_result(result: Any) -> Any:
    if getattr(result, "isError", False):
        raise McpError("the MCP tool call returned an error")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "data" in structured:
        return structured
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except ValueError:
                continue
    raise McpError("the MCP response had no JSON payload")


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    inner = getattr(exc, "exceptions", None)
    if inner:
        return [leaf for child in inner for leaf in _leaf_exceptions(child)]
    return [exc]


def _scrub(exc: BaseException, what: str) -> McpError:
    """Our own errors pass through (also inside SDK exception groups); anything else is reduced to type names."""
    leaves = _leaf_exceptions(exc)
    for leaf in leaves:
        if isinstance(leaf, McpError):
            return leaf
    return McpError(f"{what} failed (" + ", ".join(sorted({type(x).__name__ for x in leaves})) + ")")


def _open_session(spec: ServerSpec, url: str, store: TokenStore, redirect_uri: str,
                  redirect_handler, callback_handler):
    """Async context manager yielding an initialized, OAuth-authenticated MCP session."""
    import httpx
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client

    @asynccontextmanager
    async def _cm():
        provider = OAuthClientProvider(
            server_url=url, client_metadata=_client_metadata(spec, redirect_uri), storage=store,
            redirect_handler=redirect_handler, callback_handler=callback_handler)
        async with httpx.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
    return _cm()


async def _login_required(spec: ServerSpec, *_args: Any) -> Any:
    raise McpLoginRequired(f"{spec.name} login required: run `{spec.login_command}`")


async def _call_tool(spec: ServerSpec, url: str, store: TokenStore, name: str, arguments: dict[str, Any]) -> Any:
    async def no_login(*args: Any) -> Any:
        return await _login_required(spec, *args)

    cm = _open_session(spec, url, store, "http://127.0.0.1/callback", no_login, no_login)
    async with cm as session:
        return _payload_from_result(await session.call_tool(name, arguments=arguments))


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class ReadOnlyOAuthMcpClient:
    """Calls an allowlisted set of read-only tools on one hosted MCP server."""

    def __init__(self, spec: ServerSpec, *, allowed_tools: Iterable[str], token_store: TokenStore,
                 url: str | None = None, min_interval: float = MIN_CALL_INTERVAL_SECONDS,
                 transport: Callable[[str, dict[str, Any]], Any] | None = None,
                 policy: Mapping[str, Any] | None = None) -> None:
        self.spec = spec
        self._allowed = frozenset(allowed_tools)
        if not self._allowed:
            raise McpPolicyError("allowed_tools must name at least one tool")
        for tool in sorted(self._allowed):
            assert_read_only(spec.name, tool, policy)
        self._url = _require_https(url or spec.default_url)
        self._store = token_store
        self._transport = transport or (lambda name, args: self._run(name, args))
        self._min_interval = min_interval
        self._last_call = 0.0

    @property
    def allowed_tools(self) -> frozenset[str]:
        return self._allowed

    def _run(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return asyncio.run(_call_tool(self.spec, self._url, self._store, name, arguments))
        except McpError:
            raise
        except BaseException as exc:  # includes ExceptionGroup from the SDK's task groups
            raise _scrub(exc, f"{self.spec.name} MCP call") from None

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Call one allowed tool and return its decoded JSON payload."""
        if tool not in self._allowed:
            raise McpPolicyError(f"{tool!r} is not enabled for this client")
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            return self._transport(tool, arguments)
        finally:
            self._last_call = time.monotonic()


def client_from_environment(spec: ServerSpec, *, allowed_tools: Iterable[str],
                            env: Mapping[str, str] | None = None) -> ReadOnlyOAuthMcpClient:
    """Build a client; raises :class:`McpLoginRequired` when nobody has logged in yet."""
    source: Mapping[str, str] = os.environ if env is None else env
    store = FileTokenStore(default_token_path(spec, source))
    if not store.has_tokens():
        raise McpLoginRequired(f"no {spec.name} login found: run `{spec.login_command}`")
    return ReadOnlyOAuthMcpClient(spec, allowed_tools=allowed_tools, token_store=store,
                                  url=source.get(spec.url_env, spec.default_url))


# --------------------------------------------------------------------------
# Interactive login (loopback redirect; PKCE and `state` are handled by the SDK)
# --------------------------------------------------------------------------

class _CallbackServer:
    def __init__(self) -> None:
        outcome: dict[str, str | None] = {}
        self.outcome = outcome

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != _CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                outcome["code"] = (query.get("code") or [None])[0]
                outcome["state"] = (query.get("state") or [None])[0]
                outcome["error"] = (query.get("error") or [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Login received. You can close this tab and return to the terminal.\n")

            def log_message(self, *_args: Any) -> None:  # keep the code/state out of logs
                return

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)  # loopback only
        self.port = self.httpd.server_address[1]

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{_CALLBACK_PATH}"

    def wait(self, timeout: float) -> tuple[str, str | None]:
        deadline = time.monotonic() + timeout
        while "code" not in self.outcome and time.monotonic() < deadline:
            self.httpd.timeout = max(0.1, min(1.0, deadline - time.monotonic()))
            self.httpd.handle_request()
        if self.outcome.get("error"):
            raise McpError("the authorization was denied")
        code = self.outcome.get("code")
        if not code:
            raise McpError("timed out waiting for the login to complete")
        return code, self.outcome.get("state")

    def close(self) -> None:
        self.httpd.server_close()


async def _login(spec: ServerSpec, url: str, store: TokenStore, *, open_browser: bool) -> None:
    server = _CallbackServer()

    async def redirect_handler(authorization_url: str) -> None:
        print(f"Open this URL to authorize read-only access to {spec.name}:", file=sys.stderr)
        print(authorization_url, file=sys.stderr)
        if open_browser:
            threading.Thread(target=webbrowser.open, args=(authorization_url,), daemon=True).start()

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.to_thread(server.wait, LOGIN_TIMEOUT_SECONDS)

    try:
        cm = _open_session(spec, url, store, server.redirect_uri, redirect_handler, callback_handler)
        async with cm as session:
            await session.list_tools()  # proves the session works; calls no tool
    finally:
        server.close()


def login(spec: ServerSpec, url: str, store: TokenStore, *, open_browser: bool = True) -> None:
    try:
        asyncio.run(_login(spec, _require_https(url), store, open_browser=open_browser))
    except McpError:
        raise
    except BaseException as exc:
        raise _scrub(exc, f"{spec.name} login") from None
    if not store.has_tokens():
        raise McpError("login finished but no token was stored")


def main(argv: list[str] | None = None, *, default_server: str | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m trading_research.mcp.oauth_readonly_client",
        description="Manage the OAuth login of a hosted read-only MCP server.")
    ap.add_argument("command", choices=("login", "status", "logout"))
    ap.add_argument("server", nargs="?", choices=sorted(SERVERS), default=default_server)
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    args = ap.parse_args(argv)
    if args.server is None:
        ap.error("a server name is required")
    spec = SERVERS[args.server]
    store = FileTokenStore(default_token_path(spec))
    if args.command == "status":
        print(f"token file: {store.path}")
        try:
            print("logged in" if store.has_tokens() else "not logged in")
        except McpError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "logout":
        store.clear()
        print(f"removed {store.path}")
        return 0
    try:
        login(spec, os.environ.get(spec.url_env, spec.default_url), store, open_browser=not args.no_browser)
    except McpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("logged in (tokens stored privately; no tool was called)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
