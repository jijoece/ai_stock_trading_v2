# App-level authentication for a single-user, read-only Streamlit dashboard (as of Nov 2026)

Context: the Streamlit app currently has no auth and would be exposed through a tunnel or cloud host. The goal is a defense-in-depth layer, possibly behind an exposure method that adds no auth of its own (Tailscale Funnel, a plain tunnel). The locally installed version, checked by reading its source, is **Streamlit 1.59.2**, in `.venv/lib/python3.14/site-packages/streamlit`. The installed server is Starlette/Uvicorn based (`web/server/server.py` starts `UvicornServer`).

## Q1. Streamlit native auth (st.login / st.user / st.logout): config, version, cookie and session behavior, limitations

### Takeaway
Native OIDC login (`st.login`) is a few lines of code plus an `[auth]` block in `secrets.toml`. 2FA comes from the identity provider. It **only gates the code you put behind `if not st.user.is_logged_in: st.login(); st.stop()`**. The server still accepts WebSocket sessions, and serves `/media`, `/_stcore/health`, static assets and other routes, without checking login. It is application-level gating, not a perimeter.

### Cited Findings
- Native OIDC auth shipped in **Streamlit 1.42** (early 2025). The `st.experimental_user` name was renamed to `st.user`, and the old name became a deprecated alias. From 1.42.0, `st.user` no longer returns the Community Cloud account email, so apps need their own `[auth]` provider. — [st.user docs](https://docs.streamlit.io/1.33.0/develop/api-reference/user/st.user); [Snowflake SiS release note, 1.42.0 GA Apr 14 2025](https://docs.snowflake.com/release-notes/2025/other/2025-04-14-sis). The exact PyPI release date of 1.42.0 was not verified.
- Install with `pip install streamlit[auth]`, which requires **Authlib>=1.3.2**. Only **OIDC** providers are supported, not generic OAuth2. — [st.login API ref (v1.67.0 docs)](https://docs.streamlit.io/develop/api-reference/user/st.login)
- `secrets.toml` layout: `[auth]` must contain `redirect_uri` (absolute, ending in `/oauth2callback`; local default `http://localhost:8501/oauth2callback`) and `cookie_secret` ("a strong, randomly generated secret"). Each provider needs `client_id`, `client_secret` and `server_metadata_url`. Several providers go in named tables such as `[auth.google]` or `[auth.microsoft]` and are started with `st.login("google")`. Provider names cannot contain underscores. Optional settings: `client_kwargs` (defaults `scope="openid profile email"`, `prompt="select_account"`) and `expose_tokens` (`"id"`, `"access"`; none exposed by default). — [st.login API ref](https://docs.streamlit.io/develop/api-reference/user/st.login); [Auth concept page](https://docs.streamlit.io/develop/concepts/connections/authentication)
- Examples cover Google, Microsoft, Okta and Auth0. Auth0 with `client_kwargs = { "prompt" = "login" }` forces a fresh login every time, because some IdPs silently reuse an existing session. — [st.login API ref](https://docs.streamlit.io/develop/api-reference/user/st.login)
- "Configuring authentication in secrets.toml automatically enables CORS and XSRF protection," and this overrides `config.toml`. — [st.login API ref](https://docs.streamlit.io/develop/api-reference/user/st.login)
- Cookie: Streamlit stores the user's identity information in a browser cookie. `st.logout()` deletes it and starts a new session. The cookie **expires after 30 days**, which is **not configurable** and **not tied to the ID token's expiry**. To avoid persistent sessions, check expiry in `st.user` and call `st.logout()` yourself. — [Auth concept page](https://docs.streamlit.io/develop/concepts/connections/authentication)
- In the 1.59.2 source, the cookies are `_streamlit_user` and `_streamlit_user_tokens`, chunked as `_streamlit_user__1` and so on. They are signed with itsdangerous using `cookie_secret`, set `httponly=True` and `samesite="lax"`, and have `AUTH_COOKIE_MAX_AGE_SECONDS = 30*24*60*60`. — local source `streamlit/web/server/starlette/starlette_server_config.py` and `starlette_auth_routes.py`, lines 210-227 (verified by reading 1.59.2). The config docs also say auth cookies always use SameSite=Lax. — [config.toml docs](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
- Logging in carries over to new tabs in the same browser. Logging out in one session does **not** log out other open sessions. — [Auth concept page](https://docs.streamlit.io/develop/concepts/connections/authentication)
- `st.logout()` does not sign the user out of the IdP. Google's metadata has no `end_session_endpoint`, so the user stays signed in to Google. — [Auth concept page](https://docs.streamlit.io/develop/concepts/connections/authentication); [st.login API ref](https://docs.streamlit.io/develop/api-reference/user/st.login)
- Other limitations: OIDC gives **authentication, not authorization**. Auth is **not supported for embedded apps**. Hosted environments such as Codespaces can break the redirect. You must update `redirect_uri` in both the app and the IdP when you deploy. — [Auth concept page](https://docs.streamlit.io/develop/concepts/connections/authentication); [st.login API ref](https://docs.streamlit.io/develop/api-reference/user/st.login)
- Restricting to one email is **not a built-in feature**. Neither docs page describes an allowlist. — [Auth concept page](https://docs.streamlit.io/develop/concepts/connections/authentication)

### Inferences
- Single-user restriction has to be done in app code or at the IdP. Do not authorize on the email claim alone: an email can be mutable or unverified (Microsoft Entra documents it as unsuitable for authorization), so another identity could match `if st.user.email != "me@…"`. In app code, compare the trusted issuer (`st.user.iss`) and the stable subject identifier (`st.user.sub`) against the owner's recorded values, and additionally require `email_verified` if the email is also checked. Provider-specific constraint: for Entra, use the `oid`/`sub` claim, not `email`. A cleaner option is to restrict at the IdP: a Google OAuth app in "Testing" mode with one test user, or an Auth0/Okta app assigned to one user. The check has to run at the top of **every** page of a multipage app, or in the `st.navigation` entrypoint before any page runs.
- 2FA story: whatever the IdP enforces, such as Google account 2-Step Verification or passkeys. Streamlit adds none of its own.
- Setup effort is low: create an OIDC client in Google Cloud Console, write about 6 lines of TOML and 3 lines of Python. It needs a stable public HTTPS URL for `redirect_uri`, which a random-subdomain tunnel breaks.

### Gaps
- The exact minimum version (1.42.0) was confirmed only through secondary sources. I did not fetch the official 1.42.0 GitHub release page.
- The docs build I read is labelled v1.67.0 while the local install is 1.59.2. I did not check whether behavior differs between these versions.

## Q2. Does st.login protect all pages and endpoints, or only code after the check? Is the WebSocket or static content reachable unauthenticated?

### Takeaway
**Only code after the check.** In 1.59.2 the WebSocket endpoint `/_stcore/stream` accepts any connection whose Origin is allowed. It reads the auth cookie only to fill in `st.user`, and starts a script session whether or not the user is logged in. HTTP routes such as `/media/*`, `/_stcore/health`, `/_stcore/host-config`, `/component/*`, `/app/static/*` and the frontend JS/CSS bundle have no login check. An unauthenticated visitor can open a session, which executes your script up to `st.stop()` (any imports and top-level side effects run), fetch media by file ID, and fingerprint the version.

### Cited Findings (local source, Streamlit 1.59.2; read directly)
- `starlette_websocket.py` `_websocket_endpoint`: (1) it rejects with close code 1008 only if `_is_origin_allowed(origin, host)` fails; (2) `await websocket.accept(...)`; (3) if XSRF is enabled and the XSRF token is valid, it decodes the signed `_streamlit_user` cookie into `user_info`; (4) it merges `server.trustedUserHeaders` from request headers, which "override anything coming from the auth cookie"; (5) it calls `runtime.connect_session(client, user_info, ...)` unconditionally. No branch closes the socket when `is_logged_in` is false.
- Route constants in `starlette_routes.py`: `_stcore/health`, `_stcore/host-config`, `_stcore/upload_file/{session_id}/{file_id}`, `media/{file_id:path}`, `component/{path:path}`, `_stcore/bidi-components/{path:path}`, `app/static/{path:path}`. A grep of the route module found no references to the user/auth cookie. The only cookie logic is XSRF.
- An auth-cookie origin check exists: if the cookie's stored origin does not match the WebSocket Origin ("not the same origin of redirect_uri in secrets.toml"), `user_info` is left empty, so the user is treated as logged out.
- A secondary source agrees that these are separate handlers and that the auth check is tied to the WebSocket and XSRF path only. — [DeepWiki: Streamlit web server](https://deepwiki.com/streamlit/streamlit/3.4-web-server-and-communication) (AI-generated wiki, lower trust; consistent with the source above).
- `server.enableXsrfProtection` (default true) "doesn't enable CORS, and the WebSocket connection doesn't require a valid XSRF token". — [config.toml docs](https://docs.streamlit.io/develop/api-reference/configuration/config.toml). A missing token means no `st.user` data, not a rejected connection, which matches the source.

### Inferences
- `st.login` is a UI gate. A proxy-level gate (oauth2-proxy, Pomerium, Authelia/Caddy, Cloudflare Access) is the only way to keep unauthenticated traffic away from the Streamlit process, the WebSocket, `/media` and the version-revealing static bundle. This matters most for a Funnel or plain-tunnel exposure.
- `/media/{file_id}` URLs use opaque generated IDs, so exploitation requires a leaked URL. Unverified: I did not confirm the ID entropy.
- **`server.trustedUserHeaders` pitfall:** it is a hidden, experimental option (`visibility="hidden"`, "experimental API subject to change"; local `config.py` line 1140). If you use it to pass `X-Forwarded-Email` from a proxy, then anyone who can reach Streamlit directly, bypassing the proxy, can spoof identity. Bind Streamlit to `127.0.0.1` (`server.address`) when it sits behind a proxy.

### Gaps
- I did not live-test an unauthenticated WebSocket or a `/media` fetch against a running instance. The conclusions come from reading the source.
- I did not check whether `_stcore/metrics` exists or is exposed in 1.59.2. It was referenced in `config.py` comments ("metrics endpoint").

## Q3. streamlit-authenticator (third-party): maintenance and security posture

### Takeaway
It is still published but slow-moving. The latest release is **0.4.2 (1 Mar 2025)**, with no release in the roughly 20 months since. It is a username/password plus cookie scheme inside the Streamlit app, so it has the same "UI gate only" limitation as `st.login`, with weaker identity guarantees. Its 2FA feature is marked "phased out". It is not a good primary control for internet exposure.

### Cited Findings
- PyPI release history: 0.4.2 (Mar 1 2025), 0.4.1 (Sep 30 2024), 0.3.3 (Jul 27 2024), 0.3.2 (Mar 2024), 0.3.1 (Jan 2024), 0.2.3 (Sep 2023). — [PyPI streamlit-authenticator](https://pypi.org/project/streamlit-authenticator/)
- The repo has about 2.1k stars, 48 open issues and 12 open PRs. Plain-text passwords in config are "hashed automatically", and `Hasher.hash_passwords` is offered. A re-authentication cookie is signed with a configured cookie key, and `expiry_days` defaults to 30 (0 disables it). 2FA (an emailed 4-digit code) exists only for register, forgot-password and forgot-username flows, and is labelled "(phased out)", relying on a phased-out API key. OAuth2 Google/Microsoft is available only through `experimental_guest_login`. `forgot_password` returns a new plain-text password the developer must transmit. — [GitHub mkhorasani/Streamlit-Authenticator](https://github.com/mkhorasani/Streamlit-Authenticator)
- I found no CVE or GHSA for the package. Reported problems are functional: a cookie not read on page reload (issue #292 on 0.4.2) and a KeyError on logout. — [GitHub issues](https://www.github.com/mkhorasani/Streamlit-Authenticator/issues); [Streamlit forum: logout KeyError](https://discuss.streamlit.io/t/streamlit-authenticator-keyerror-on-logout/65568)
- A forum thread reports users being logged in as previous users through cookie mishandling. It concerns an app-level implementation on Cloud Run and is not confirmed as a library flaw. — [Streamlit forum](https://discuss.streamlit.io/t/authentication-mix-up-cookies-causing-unintended-logins/66158)

### Inferences
- Since `st.login` (1.42+) exists, streamlit-authenticator's main remaining use is password login without an IdP. For one user it adds risk (local password store, no real 2FA, irregular releases) without adding perimeter protection.

### Gaps
- I could not confirm the password hash algorithm (historically bcrypt) or the cookie format (historically a PyJWT-signed token) from the current README. I also could not get the last-commit date.

## Q4. Reverse-proxy auth layers: oauth2-proxy, Authelia, Authentik, Caddy forward_auth, Pomerium, plain basic auth

### Takeaway
For one user on macOS, **oauth2-proxy** (one Go binary, Google OIDC, a one-line email allowlist file, WebSockets proxied by default) or **Caddy with basic_auth/forward_auth** gate *everything*, including the WebSocket and `/media`. Authelia adds self-hosted TOTP, WebAuthn and Duo, but needs Caddy or another proxy plus a config and user DB. Pomerium needs `allow_websockets: true` per route. Basic auth is acceptable only over HTTPS, as a coarse outer layer.

### Cited Findings
- **oauth2-proxy** (docs for the 7.15.x line): `--proxy-websockets` defaults to `true`; `--http-address` defaults to `127.0.0.1:4180`; `--cookie-secure` defaults to `true`; `--cookie-expire` defaults to `168h` (0 gives a session cookie); `--provider` defaults to `google` (generic OIDC through `--oidc-issuer-url`). Access control is `--email-domain` (repeatable; `*` allows any email) and/or `--authenticated-emails-file` (one email per line). `--skip-auth-route` (`method=path_regex`) can bypass auth, which is a footgun if over-broad. `--upstream` points to e.g. `http://127.0.0.1:8501`. `--reverse-proxy` (default false) controls trust of `X-Real-IP`-style headers. — [oauth2-proxy configuration overview](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview)
- **Authelia + Caddy**: `forward_auth authelia:9091 { uri /api/authz/forward-auth; copy_headers Remote-User Remote-Groups Remote-Email Remote-Name }`, then `reverse_proxy` to the app. Requires **Caddy v2.5.1+**. Caddy trusts no proxies by default, so `trusted_proxies` must be set deliberately. — [Authelia Caddy integration](https://www.authelia.com/integration/proxies/caddy/)
- Authelia second factors: TOTP, Security Key/Passkeys (WebAuthn), Duo/Mobile Push. The first factor is username and password. — [Authelia authentication intro](https://www.authelia.com/overview/authentication/introduction/)
- **Pomerium**: `allow_websockets` defaults to **false** ("If set, enables proxying of websocket connections"). The docs warn it "could result in abuse via DOS attacks". On WebSocket routes the route and idle timeouts are automatically disabled (0) unless set. — [Pomerium route timeouts docs](https://www.pomerium.com/docs/reference/routes/timeouts)
- **Caddy basic_auth**: syntax `basic_auth [<matcher>] [<hash_algorithm> [<realm>]] { <user> <hash> }`. bcrypt is the default and argon2id is supported. Plaintext passwords are rejected, so use `caddy hash-password`. The directive was renamed from `basicauth` in **v2.8.0**. "Basic auth is not secure over plain HTTP." — [Caddy basic_auth docs](https://caddyserver.com/docs/caddyfile/directives/basic_auth)

### Inferences
- **Coverage**: every proxy option gates *all* paths, including `/_stcore/stream`, `/media` and static assets, because Streamlit sees only proxied traffic. That holds only if Streamlit binds to `127.0.0.1` and the tunnel points at the proxy port, not 8501.
- **oauth2-proxy on macOS**: `brew install oauth2-proxy` (formula exists as far as I know; not verified in this session). Run with `--provider=google --authenticated-emails-file=allow.txt --upstream=http://127.0.0.1:8501 --cookie-secret=$(openssl rand -base64 32 | head -c 32)`, plus a Google OAuth client with the redirect `https://<host>/oauth2/callback`. You get single-email restriction natively, 2FA through the Google account, and WebSockets by default. This is the best effort-to-security ratio for one user.
- **Authelia/Authentik**: heavier (a container or service, a session store, user DB or LDAP, SMTP for notifications). Justified only if you want self-hosted TOTP or WebAuthn without a third-party IdP. Authentik is heavier still (Postgres, plus Redis in older versions).
- **Basic auth**: low effort and gates everything. There is no 2FA, no lockout or rate limit by default, credentials go with every request, logout is awkward, and some clients handle WebSocket upgrades with basic auth inconsistently (unverified). It is reasonable as an outer layer *in addition to* `st.login`, not as the only control on a public URL.
- **2FA summary**: `st.login` and oauth2-proxy rely on IdP 2FA, Authelia and Authentik have built-in TOTP and WebAuthn, Pomerium uses its IdP, and basic auth has none.

### Gaps
- I did not fetch the Authentik docs (proxy outpost, forward-auth with Caddy, WebSocket notes). The Authentik claims above are from general knowledge and unverified.
- I did not verify whether Authelia's forward-auth works cleanly with Streamlit WebSockets through Caddy. Caddy's reverse_proxy handles WebSocket upgrades natively, but the forward_auth subrequest behavior on the upgrade request was not checked.
- I did not confirm Homebrew availability or the current release number for oauth2-proxy.

## Q5. Streamlit CVEs and advisories, 2024–2026

### Takeaway
Advisories in this window are few and mostly moderate, and two of them affect **Windows hosts only**. The most relevant to internet exposure is the unauthenticated SSRF in component endpoints (fixed in 1.54.0). The local 1.59.2 is past all listed fixes.

### Cited Findings
- **GHSA-7p48-42j8-8846 / CVE-2026-33682** (published 24 Mar 2026, Moderate, CVSS 4.7): unauthenticated SSRF on **Windows** hosts in `ComponentRequestHandler` / component endpoints. A UNC path appended to the URI makes outbound SMB on port 445, leaking NTLMv2 credentials. Affects versions before **1.54.0**, fixed in 1.54.0. "unauthenticated and does not require user interaction." — [GitHub advisory](https://github.com/advisories/GHSA-7p48-42j8-8846)
- **CVE-2026-10804 / GHSA-vqwp-45wm-r9r5**: `@st.cache_data` hash collision (fixed sampling seed, PIL P-mode palette omission). Low severity, affects versions before **1.53.1**. — [GitHub advisory](https://github.com/advisories/GHSA-vqwp-45wm-r9r5) (from search result summary; the page was not fetched)
- **GHSA-rxff-vr5r-8cj5** (12 Aug 2024, Moderate): path traversal on Windows. — [Streamlit security advisories](https://github.com/streamlit/streamlit/security/advisories). I did not retrieve the CVE ID or the fixed version. A search result mentions a 2024 Windows "password-hash leak", probably the same issue (NTLM hash), but that is unverified.
- **GHSA-8qw9-gf7w-42x5** (11 Jan 2024, Low): a minor fix to the previous patch for CVE-2022-35918 (component directory traversal). — [Streamlit security advisories](https://github.com/streamlit/streamlit/security/advisories)
- A Cato Networks blog (4 Aug 2025) reports a "new Streamlit vulnerability". — [Cato CTRL blog](https://www.catonetworks.com/blog/cato-ctrl-new-streamlit-vulnerability). The page failed to load, so I could not verify the details. As far as I recall, it concerned client-side-only file-type validation in `st.file_uploader`, but that is unverified, and it does not appear on Streamlit's GHSA list.

### Inferences
- Component endpoints were reachable without auth (CVE-2026-33682), which supports the case for a proxy gate. With `st.login` alone, those HTTP routes stay exposed to unpatched bugs.

### Gaps
- The CVE ID and fixed version for GHSA-rxff-vr5r-8cj5 were not retrieved. Advisories after Mar 2026 may exist beyond those listed on the page I fetched.

## Q6. Best-practice Streamlit config for public exposure (XSRF, CORS, static serving)

### Takeaway
Keep the defaults `enableCORS=true` and `enableXsrfProtection=true`, which configuring `[auth]` enforces anyway. Keep `enableStaticServing=false`. Bind to localhost behind the proxy. Reduce information leakage with `client.showErrorDetails` and `client.toolbarMode`, and turn off telemetry.

### Cited Findings
- `server.enableCORS` defaults to true. When it is off, most HTTP routes send `Access-Control-Allow-Origin: *` and WebSockets accept any origin. `server.corsAllowedOrigins` defaults to `[]` and applies only when CORS is on. — [config.toml docs](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
- `server.enableXsrfProtection` defaults to true. It does not enable CORS, and the WebSocket does not require a valid XSRF token. — [config.toml docs](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
- `server.enableStaticServing` defaults to **false** and serves the app's `static/` dir at `/app/static/*` when enabled. — [config.toml docs](https://docs.streamlit.io/develop/api-reference/configuration/config.toml); local `config.py` line 1114
- `server.cookieSecret` is random by default and signs cookies (share it across replicas). `server.address` binds to a specific address. `server.sslCertFile` exists but is not advised for production. `client.showErrorDetails` defaults to `"full"` (options `stacktrace`, `type`, `none`). `client.toolbarMode` defaults to `"auto"` (options `developer`, `viewer`, `minimal`). `browser.gatherUsageStats` defaults to true. — [config.toml docs](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
- With `[auth]` configured, CORS and XSRF are forced on, overriding config.toml. — [st.login API ref](https://docs.streamlit.io/develop/api-reference/user/st.login)
- The WebSocket rejects disallowed Origins with close code 1008 before accepting, which blocks cross-site WebSocket hijacking. — local source `starlette_websocket.py` (1.59.2)

### Inferences
- Recommended `.streamlit/config.toml` for this use case: `server.address="127.0.0.1"`, `server.headless=true`, `server.enableCORS=true`, `server.enableXsrfProtection=true`, `server.enableStaticServing=false`, `client.showErrorDetails="none"` (no tracebacks to strangers), `client.toolbarMode="viewer"` or `"minimal"`, and `browser.gatherUsageStats=false`. Terminate TLS at the tunnel or proxy, not with `server.sslCertFile`.
- Layered recommendation for Funnel or a plain tunnel: tunnel → oauth2-proxy (Google, a one-email allowlist file) → Streamlit on 127.0.0.1, plus an optional `st.login`/email check inside the app as a second layer. Note that `st.login` and oauth2-proxy are two separate OIDC clients and two logins, unless you instead pass the identity via `trustedUserHeaders` (experimental, hidden).

### Gaps
- I did not verify an official Streamlit "deploying publicly / security hardening" guide. None was found during this session.
