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
* Provider text is not forwarded in transport or authentication errors (it may echo tokens). The one exception
  is a tool-level ``isError`` result, which names the tool and carries a short server message after redaction
  of bearer strings, opaque tokens and sensitive argument values, capped at 200 characters.

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
import re
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .tool_classifier import load_policy

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

MIN_CALL_INTERVAL_SECONDS = 0.5
LOGIN_TIMEOUT_SECONDS = 300.0
_CALLBACK_PATH = "/callback"


class McpError(Exception):
    """A read-only MCP call could not be completed."""


class McpToolError(McpError):
    """The server answered a tool call with an error result.

    ``detail`` is the redacted, truncated server text, for display only. ``full_detail`` is the same text
    redacted but not truncated, so an error can be classified without a truncation cutting off the evidence.
    """

    def __init__(self, message: str, detail: str = "", full_detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail
        self.full_detail = detail if full_detail is None else full_detail


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


TOKEN_EXPIRY_SKEW_SECONDS = 30.0


class FileTokenStore:
    """OAuth tokens and registered client info in one 0600 file (directory 0700).

    Refuses to read a file that is accessible to group/other, and never logs or
    prints its contents. Each write goes to its own exclusively created
    temporary file, and read-modify-write updates are serialized across
    processes with an advisory lock, so concurrent logins or refreshes cannot
    share a temporary file or lose each other's fields.

    Besides the SDK's token and client-info fields, the file keeps the absolute
    access-token expiry and the discovered authorization-server metadata. The
    SDK keeps both only in memory, so without them a new process would send an
    expired token, get a 401, and need another interactive login although a
    refresh token is stored.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        """Hold an exclusive advisory lock for one read-modify-write (POSIX; a no-op elsewhere)."""
        if fcntl is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the lock

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
        # mkstemp creates a new, uniquely named 0600 file with O_EXCL: never a pre-existing or shared one.
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _update(self, **fields: Any) -> None:
        with self._locked():
            data = self._read()
            data.update(fields)
            self._write(data)

    def has_tokens(self) -> bool:
        return bool(self._read().get("tokens"))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        expires_in = getattr(tokens, "expires_in", None)
        self._update(tokens=tokens.model_dump(mode="json", exclude_none=True),
                     tokens_expire_at=None if expires_in is None else time.time() + float(expires_in))

    def token_expires_at(self) -> float | None:
        """When the stored access token expires (epoch seconds, minus a small skew), or None when unknown."""
        value = self._read().get("tokens_expire_at")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) - TOKEN_EXPIRY_SKEW_SECONDS

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        self._update(client_info=client_info.model_dump(mode="json", exclude_none=True))

    def get_oauth_metadata(self) -> Any:
        from mcp.shared.auth import OAuthMetadata
        raw = self._read().get("oauth_metadata")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthMetadata.model_validate(raw)
        except ValueError:
            return None  # stale or foreign metadata: the SDK rediscovers it on the next 401

    def set_oauth_metadata(self, metadata: Any) -> None:
        self._update(oauth_metadata=metadata.model_dump(mode="json", exclude_none=True))


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


_SERVER_ERROR_MAX_CHARS = 200
_SENSITIVE_ARGUMENT_KEY = re.compile(r"account|cursor|token|secret|password|key", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9_\-.+/=]{24,}")


def _redacted_error_text(result: Any, arguments: dict[str, Any] | None) -> str:
    """The redacted, whitespace-collapsed, untruncated text of a tool-level error.

    Redacts bearer strings, long opaque tokens and the values of sensitive arguments (account numbers,
    cursors, tokens). Use it for classification; use :func:`_server_error_text` for anything displayed.
    """
    parts = [text for block in getattr(result, "content", None) or []
             if isinstance(text := getattr(block, "text", None), str)]
    message = " ".join(" ".join(parts).split())
    for key, value in (arguments or {}).items():
        if _SENSITIVE_ARGUMENT_KEY.search(key) and isinstance(value, str) and value:
            message = message.replace(value, "[redacted]")
    return _OPAQUE_TOKEN.sub("[redacted]", _BEARER.sub("Bearer [redacted]", message))


def _server_error_text(result: Any, arguments: dict[str, Any] | None) -> str:
    """A short, redacted rendering of a tool-level error so a failure can be attributed.

    Truncates :func:`_redacted_error_text`. Only used for ``isError`` tool results; transport and
    authentication failures still go through :func:`_scrub`, which never forwards provider text.
    """
    message = _redacted_error_text(result, arguments)
    if len(message) > _SERVER_ERROR_MAX_CHARS:
        message = message[:_SERVER_ERROR_MAX_CHARS].rstrip() + "..."
    return message


def _payload_from_result(result: Any, tool: str | None = None, arguments: dict[str, Any] | None = None) -> Any:
    if getattr(result, "isError", False):
        subject = f"the MCP tool call to {tool}" if tool else "the MCP tool call"
        full_detail = _redacted_error_text(result, arguments)
        detail = _server_error_text(result, arguments)
        raise McpToolError(f"{subject} returned an error" + (f": {detail}" if detail else ""), detail, full_detail)
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


def _make_provider(spec: ServerSpec, url: str, store: TokenStore, redirect_uri: str,
                   redirect_handler, callback_handler):
    """An SDK OAuth provider with the persisted token expiry and token endpoint restored.

    The SDK's lazy initialization loads only the tokens and client info, so a new provider treats any
    stored access token as valid until the server rejects it, and its 401 handling starts an interactive
    login instead of refreshing. Restoring the expiry makes the SDK refresh an expired token before the
    request. Restoring the metadata points that refresh at the discovered token endpoint instead of the
    SDK's guess of `<server origin>/token`.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthMetadata

    provider = OAuthClientProvider(
        server_url=url, client_metadata=_client_metadata(spec, redirect_uri), storage=store,
        redirect_handler=redirect_handler, callback_handler=callback_handler)
    load_expiry = getattr(store, "token_expires_at", None)
    expires_at = load_expiry() if callable(load_expiry) else None
    if isinstance(expires_at, float):
        provider.context.token_expiry_time = expires_at
    load_metadata = getattr(store, "get_oauth_metadata", None)
    metadata = load_metadata() if callable(load_metadata) else None
    if isinstance(metadata, OAuthMetadata):
        provider.context.oauth_metadata = metadata
    return provider


def _remember_metadata(provider: Any, store: TokenStore) -> None:
    """Persist the authorization-server metadata the SDK discovered, for later refreshes."""
    metadata = getattr(provider.context, "oauth_metadata", None)
    save = getattr(store, "set_oauth_metadata", None)
    if metadata is None or not callable(save):
        return
    load = getattr(store, "get_oauth_metadata", None)
    if callable(load) and load() == metadata:
        return
    save(metadata)


def _open_session(spec: ServerSpec, url: str, store: TokenStore, redirect_uri: str,
                  redirect_handler, callback_handler):
    """Async context manager yielding an initialized, OAuth-authenticated MCP session."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    @asynccontextmanager
    async def _cm():
        provider = _make_provider(spec, url, store, redirect_uri, redirect_handler, callback_handler)
        async with httpx.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    _remember_metadata(provider, store)
                    yield session
    return _cm()


async def _login_required(spec: ServerSpec, *_args: Any) -> Any:
    raise McpLoginRequired(f"{spec.name} login required: run `{spec.login_command}`")


async def _call_tool(spec: ServerSpec, url: str, store: TokenStore, name: str, arguments: dict[str, Any]) -> Any:
    async def no_login(*args: Any) -> Any:
        return await _login_required(spec, *args)

    cm = _open_session(spec, url, store, "http://127.0.0.1/callback", no_login, no_login)
    async with cm as session:
        return _payload_from_result(await session.call_tool(name, arguments=arguments), name, arguments)


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
