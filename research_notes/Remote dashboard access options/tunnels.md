# Tunnel and reverse-proxy options for exposing a local Streamlit dashboard (127.0.0.1:8501) with edge auth

Research date: 2026-11-14. All pages were fetched on that date unless noted. Use case: one user, a private Streamlit app with no built-in auth, reached from a phone with no VPN client installed. The SQLite DB and the Robinhood/Alpaca secrets stay on the Mac.

Method note: I ran about 22 search and fetch calls. Several primary pages returned only partial content: the Cloudflare plan pages, the pinggy.io pricing page (404), the zrok docs (moved to netfoundry.io, and the fetched pages had no body text) and the localtunnel repo metadata. Those gaps are flagged inline.

## 1. Free-tier limits (users, bandwidth, requests, session timeouts, tunnel count)

### Takeaway
The free tiers that fit "one user, always on, behind a login" are Cloudflare Tunnel + Access and ngrok Free. Cloudflare's free tier is far more generous: 50 users, no published bandwidth or request caps for Tunnel. ngrok Free caps data at 1 GB/month and requests at 20k/month, which a Streamlit app that streams over WebSockets could use up. Quick tunnels, Pinggy Free and localtunnel are ephemeral or have no auth, so they don't fit.

### Cited Findings
**Cloudflare Tunnel + Access (Zero Trust Free)**
- Zero Trust free plan: up to 50 users, $0, no time limit, 24-hour log retention, community support only. Paid seats are $7/user/month. These figures come from search summaries citing Cloudflare's plan page and aggregators. The fetched cloudflare.com/plans/zero-trust-services and /plans/free pages did not render the limits text — [Search summary / CostBench](https://costbench.com/software/business-vpn/cloudflare-zero-trust/free-plan/); [Cloudflare plans page (no limits rendered)](https://www.cloudflare.com/plans/zero-trust-services/)
- Account limits: 500 Access applications per account, 1,000 `cloudflared` tunnels per account, 50 identity providers per account — [Cloudflare One account limits](https://developers.cloudflare.com/cloudflare-one/account-limits/)

**Cloudflare quick tunnels (trycloudflare.com)**
- Each quick tunnel handles at most 200 in-flight requests; extra requests get HTTP 429. Server-Sent Events are not supported. There is "no uptime guarantee". No account or domain is needed. The hostname changes every time a tunnel is created. Recommended only for testing and development — [Cloudflare docs: Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)

**ngrok Free**
- 3 online endpoints, 3 concurrent agents, 1 user. Data transfer out is 1 GB/month. HTTP/S requests are 20,000/month, with a rate limit of 4,000/min. TCP connections are 5,000/month (100/min) and need card verification. Logs and events are 10,000/month. Monthly quotas reset each billing cycle — [ngrok docs: Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/)
- "Free endpoints have no timeout"; ngrok recommends running the agent as a background service to keep an endpoint up unattended — [ngrok docs: Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/)
- Free-plan credit can't be spent on data transfer out. Extra requests can be bought from credit at $2 per 100k. Traffic Policy units cost $0.10 per 100k from credit — [ngrok pricing](https://ngrok.com/pricing)
- Cheapest paid plan: Hobbyist at $8/month billed annually ($96/year) or $10/month billed monthly — [ngrok pricing](https://ngrok.com/pricing)

**Tailscale Funnel**
- Available on all plans, but still in beta. Bandwidth limits apply and can't be configured; no number is published. Funnel listens only on ports 443, 8443 and 10000 — [Tailscale KB 1223: Funnel](https://tailscale.com/kb/1223/funnel)

**Pinggy**
- Free: "60 minutes tunnel timeout". Restarting after a timeout generates a new URL. Subdomains are random. Data transfer is listed as "Unlimited" on both Free and Pro. Pro adds persistent tunnels, persistent subdomains and custom domains. The hero banner mentions a free persistent URL for 7 days plus custom domain support, which conflicts with the plan table. The Pro price was blank in the fetched page — [pinggy.io](https://pinggy.io/)

**zrok (hosted zrok.io / myzrok.io)**
- The zrok docs now redirect from docs.zrok.io to netfoundry.io/docs/zrok. The hosted service is described as "secure peer-to-peer sharing built on the OpenZiti mesh", with self-host or zrok.io options and "no open ports or firewall rules" — [NetFoundry zrok docs](https://netfoundry.io/docs/zrok/guides/self-hosting/limits/)

**Pangolin (fossorial)**
- Pangolin Cloud Basic (Free) is $0 with no credit card. Limits: 5 users, 5 sites, 5 domains, 1 organization, 15 public resources, 15 private resources, 5 machine clients. Bandwidth isn't listed. Team is $4/user/month and Business is $9/user/month; Enterprise is custom — [pangolin.net/pricing](https://pangolin.net/pricing)
- The self-hosted Community Edition is free. The Enterprise Edition is free for personal or hobbyist use and for businesses under $100K gross annual revenue. Dual-licensed AGPL-3 and Fossorial Commercial License — [GitHub fosrl/pangolin](https://github.com/fosrl/pangolin); [Pangolin LICENSE](https://github.com/fosrl/pangolin/blob/main/LICENSE) (the original license-change post at pangolin.net/news/license-change returned 404 on 2026-11-14)

**localtunnel**
- MIT-licensed. Usage is `npx localtunnel --port 8000` or `lt --port 8000`, with a `--subdomain` option. The repo shows 22.5k stars, 151 open issues and 17 open PRs, with an empty Releases section. The server component lives in a separate repo (localtunnel/server) — [GitHub localtunnel/localtunnel](https://github.com/localtunnel/localtunnel)

**frp / rathole on a free VM**
- frp: Apache-2.0. The newest version mentioned in the README is v0.62.0 — [GitHub fatedier/frp](https://github.com/fatedier/frp)

### Inferences
- A Streamlit session keeps a WebSocket open and pushes deltas on every rerun. A dashboard with charts and dataframes could plausibly use up ngrok's 1 GB/month with heavy daily use, though light single-user use probably fits. Measure before relying on it.
- On Cloudflare, a single user uses 1 of 50 seats, 1 Access app and 1 tunnel, so the free tier has a large margin.

### Gaps
- I could not render Cloudflare's official Zero Trust plan comparison text. The 50-user, 24h-log figures come from secondary sources and are widely repeated, but I didn't see them verbatim on cloudflare.com in this session.
- Cloudflare publishes no explicit bandwidth or request cap for named Tunnels in the pages I fetched. I found no explicit statement either way.
- zrok hosted free-tier quotas (bandwidth/day, shares, reserved names, environments) could not be read. The netfoundry.io pages came back without body text. Unverified.
- Pinggy Pro price: unverified (blank in the fetch). pinggy.io/pricing/ returned 404.
- Tailscale Funnel bandwidth: not published.
- Whether localtunnel's public server (loca.lt) is still actively maintained in 2026: I found no release or commit date. Unverified.
- rathole: not researched; no fetch was done. Free-VM options (e.g., Oracle Cloud Always Free, GCP e2-micro) were not verified in this session.

## 2. Custom domain: required? cost? free subdomain?

### Takeaway
Cloudflare named Tunnel + Access needs a domain on Cloudflare DNS, so expect roughly the cost of one domain per year (price not verified here). Quick tunnels need no domain but get a random hostname that changes each time. ngrok Free gives one stable `*.ngrok-free.app` dev domain at no cost. Tailscale Funnel uses a free stable `*.ts.net` name. Pangolin Cloud Free includes custom domains.

### Cited Findings
- Quick tunnels need no Cloudflare account or domain. The `trycloudflare.com` hostname changes each time a tunnel is created. For a stable hostname, Cloudflare points you to a regular Cloudflare Tunnel — [Cloudflare docs: Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
- ngrok Free: one automatically assigned dev domain (e.g., `your-assigned-name.ngrok-free.app`) that stays the same across agent restarts. It is the only domain on Free; custom, reserved, bring-your-own and wildcard domains need a paid plan — [ngrok docs: Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/); [ngrok pricing](https://ngrok.com/pricing)
- Tailscale Funnel works only with names under the tailnet domain `tailnet-name.ts.net`. Requesting certificates too often can hit Let's Encrypt rate limits, with blocks of up to 34 hours — [Tailscale KB 1223](https://tailscale.com/kb/1223/funnel)
- Pinggy Free uses random subdomains; persistent subdomains and custom domains are Pro — [pinggy.io](https://pinggy.io/)
- Pangolin Cloud Free includes 5 domains; a search summary of the pricing page says custom domains and all resource types are included on Basic — [pangolin.net/pricing](https://pangolin.net/pricing)

### Inferences
- For the Cloudflare path, a cheap domain registered at or moved to Cloudflare is the only recurring cost. Any domain whose nameservers point at Cloudflare works.
- Self-hosted Pangolin, frp or rathole need a VM with a public IP plus a domain or DNS name that points at it.

### Gaps
- The cost of a domain on Cloudflare Registrar (at-cost pricing) was not fetched. Unverified.
- I did not verify whether a free `*.pages.dev` or `*.workers.dev` hostname can be used as an Access-protected Tunnel hostname. My understanding is that it can't, because Tunnel public hostnames need a zone you control, but this is unverified.

## 3. Auth options at the edge and free-plan availability

### Takeaway
Cloudflare Access (free) and ngrok Free (OAuth through Traffic Policy, capped at 3 identities a month) both put a real login in front of the app at the edge. Cloudflare supports email one-time PIN without any IdP, plus Google, GitHub and others. Tailscale Funnel has no identity layer for public visitors, so the app itself would have to authenticate. Pinggy and frp offer only Basic auth or tokens. localtunnel has none. Pangolin offers PIN, passcode and email OTP per resource, but external IdPs aren't on the Cloud free tier.

### Cited Findings
**Cloudflare Access**
- Email one-time PIN: the user enters an email, and if an Access policy allows it, Cloudflare sends a single-use PIN. The PIN "expires 10 minutes after the initial request". OTP is "an alternative to integrating an identity provider" and can run alongside an IdP — [Cloudflare docs: One-time PIN](https://developers.cloudflare.com/cloudflare-one/identity/one-time-pin/)
- Up to 50 identity providers per account, 500 Access apps — [Cloudflare One account limits](https://developers.cloudflare.com/cloudflare-one/account-limits/)

**ngrok**
- The OAuth action supports Amazon, Facebook, GitHub, GitLab, Google, LinkedIn, Microsoft and Twitch. Managed ngrok OAuth apps (no client ID or secret needed) exist for GitHub, GitLab, Google, LinkedIn, Microsoft and Twitch. You restrict to one email with a deny step that uses an exact one-address `in` list. Do not use an `endsWith` suffix rule such as `endsWith('@gmail.com')`: ngrok documents it as a domain restriction, so it admits every address at that domain. Session lifetime is set with `max_session_duration` and `idle_session_timeout` — [ngrok docs: OAuth action](https://ngrok.com/docs/traffic-policy/actions/oauth/)
- Free plan includes "Traffic Identities" (OAuth/OIDC/SAML) for 3 monthly active users — [ngrok pricing](https://ngrok.com/pricing); [ngrok Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/)
- Free plan shows an interstitial warning page before HTML browser traffic, to deter phishing. Clicking "Visit" sets a cookie that suppresses it for 7 days per domain. API clients aren't affected. It can be bypassed with the `ngrok-skip-browser-warning` header, a non-standard User-Agent, or a paid plan — [ngrok Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/)

**Tailscale Funnel**
- Identity headers are sent only for tailnet traffic through Serve; Funnel requests don't get them — [Tailscale KB 1312: Serve](https://tailscale.com/kb/1312/serve) (via search summary)
- tsidp (Tailscale's OIDC IdP) can expose its app-facing endpoints through Funnel, but its /authorize step identifies users with WhoIs, so only tailnet members can complete the login — [Tailscale blog: Building tsidp](https://tailscale.com/blog/building-tsidp)
- Third-party example: OpenClaw's Funnel mode refuses to start unless a shared password is configured, and identity-header auth works only with Serve — [OpenClaw docs: Tailscale](https://docs.openclaw.ai/tailscale)

**Pinggy**
- Supports password (HTTP Basic auth) and bearer token or key-based auth. The page doesn't say which plan includes them, and OAuth isn't mentioned — [pinggy.io](https://pinggy.io/)

**zrok**
- The public-sharing concept page's table of contents covers OAuth through Google, GitHub or generic OIDC providers, restricting to specific email addresses, a password-style option, and HTTPS at the frontend. The body text wasn't readable, so the details are unverified — [NetFoundry zrok: Public sharing](https://netfoundry.io/docs/zrok/concepts/sharing-public/)

**Pangolin**
- Per-resource access with SSO, IdP integration and RBAC. Resources can also be protected by PIN codes, passcodes, email OTP, geoblocking and allow-lists — [GitHub fosrl/pangolin](https://github.com/fosrl/pangolin)
- Cloud Free includes user and role access and 2FA. External IdPs, IdP auto-provisioning and IdP session revalidation are not on Free — [pangolin.net/pricing](https://pangolin.net/pricing)

**localtunnel**
- The README documents no authentication or access-control options — [GitHub localtunnel](https://github.com/localtunnel/localtunnel)

**frp**
- `httpUser` and `httpPassword` on an `http`-type proxy enforce Basic auth. frp's OIDC support covers only client-to-server auth (Client Credentials Grant), not visitor login — [GitHub fatedier/frp](https://github.com/fatedier/frp)

### Inferences
- For "a login sits at the edge, the phone has no client", Cloudflare Access with OTP (or Google) restricted to one email is the strongest free fit. ngrok OAuth with an email deny rule is the runner-up; the 3-MAU cap doesn't matter for one user.
- With Tailscale Funnel, Pinggy, localtunnel, quick tunnels or bare frp, the Streamlit app becomes reachable by anyone who knows the URL, unless Basic auth or an extra in-front proxy (e.g., oauth2-proxy or Caddy with auth on the Mac) is added. Streamlit has no auth of its own.
- frp or rathole on a VM can get real OAuth only by adding something like oauth2-proxy, Authelia or Pangolin itself on the VM. That makes self-hosted Pangolin, with its built-in auth, the self-hosted counterpart to Cloudflare Access.

### Gaps
- Whether Cloudflare Access OTP and the Google/GitHub IdPs are explicitly listed as free-plan features on a Cloudflare primary page wasn't confirmed verbatim. The OTP doc doesn't mention plan tier. Widely understood to be free; unverified here.
- Default Cloudflare Access session duration wasn't captured. (It is configurable per app; the default was not verified.)
- ngrok OAuth docs don't explicitly say the action works on Free. The pricing and limits pages list 3 Traffic Identities on Free, which implies it does. Pricing lists Traffic Policy units as "not included; buy with credit", so it's unclear whether a Free account with $0 credit can run an OAuth policy. Flag this and verify with a test.
- Pangolin self-hosted Community Edition IdP/OIDC support vs. Enterprise-only features: not confirmed.

## 4. TLS termination and privacy (can the provider see plaintext?)

### Takeaway
Cloudflare, ngrok, Pinggy, zrok's hosted frontend and Pangolin Cloud all terminate TLS at their edge, so they can see dashboard content and requests in plaintext. That's inherent to edge auth. Tailscale Funnel is the exception: its relay can't decrypt, and TLS ends on the Mac. Self-hosted frp, rathole or Pangolin on your own VM moves the plaintext point to a box you control.

### Cited Findings
- Tailscale Funnel: only TLS-encrypted connections are allowed. "The relay server cannot decrypt the traffic." The device's Tailscale process terminates TLS and passes the decrypted request to the local service — [Tailscale KB 1223](https://tailscale.com/kb/1223/funnel)
- zrok docs include a section on "HTTPS at the front-facing proxy", i.e. the frontend terminates TLS. The body wasn't readable, so this is unverified — [NetFoundry zrok: Public sharing](https://netfoundry.io/docs/zrok/concepts/sharing-public/)
- frp: TLS between frpc and frps has been on by default since v0.50.0. By default frpc does not verify the server certificate unless a trusted CA or system CAs are configured. frps can require TLS-only connections — [GitHub fatedier/frp](https://github.com/fatedier/frp)

### Inferences
- Edge auth (OAuth or OTP checked at the provider) needs the provider to see HTTP, so it must terminate TLS. With Cloudflare or ngrok, they can technically see rendered portfolio values in the Streamlit WebSocket frames. The Robinhood OAuth tokens and Alpaca keys still never leave the Mac, as long as the dashboard never renders them. The DB stays local either way.
- If you use frp, set the trusted CA (or system CAs) in frpc so it actually verifies the server certificate; otherwise the frpc-to-frps link is open to a MITM.

### Gaps
- I didn't fetch Cloudflare's or ngrok's own statements on TLS termination or data handling. It's general knowledge that both terminate TLS at their edge (both offer HTTP inspection and policy features that require it), but no primary source was captured this session.

## 5. Streamlit (WebSocket) compatibility

### Takeaway
Streamlit needs the `/_stcore/stream` WebSocket path forwarded with an HTTP/1.1 Upgrade. Most failures behind reverse proxies are misconfigured Upgrade handling or path prefixes. I found no primary source confirming or refuting WebSocket support for each tunnel provider in this session. Note that quick tunnels explicitly don't support SSE (this doesn't affect Streamlit, which uses WebSockets).

### Cited Findings
- Reverse-proxy issues with Streamlit usually come from not forwarding `/_stcore/stream` with the right Upgrade/Connection headers. nginx needs HTTP/1.1, `Connection: upgrade` and a longer read timeout — [Streamlit forum: AWS websocket issues](https://discuss.streamlit.io/t/aws-deployment-websocket-issues/75627)
- Older Streamlit used `/stream`; newer versions use `/_stcore/stream`. Health-check paths also moved — [Streamlit forum: v1.14 vs 1.18](https://discuss.streamlit.io/t/any-changes-regarding-websocket-for-streamlit-v1-14-vs-1-18/38110)
- Under a sub-path, `/_stcore/stream` must carry the prefix. A CloudFront multipage setup broke when the page name leaked into the WS path — [Streamlit forum: _stcore 404](https://discuss.streamlit.io/t/getting-404-errors-when-trying-to-reach-stcore-health-and-stcore-allowed-messages-origins-using-nginx-proxy-on-aws-ec2/38003); [Streamlit forum: CloudFront multipage](https://discuss.streamlit.io/t/issues-with-multipage-access-in-cloudfront/59304)
- Quick tunnels: SSE not supported; the docs say nothing about WebSockets — [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
- zrok's public-sharing page lists "support for persistent two-way connections". Body unreadable; unverified — [NetFoundry zrok: Public sharing](https://netfoundry.io/docs/zrok/concepts/sharing-public/)

### Inferences
- Serving the app at the root of a dedicated hostname avoids the sub-path pitfalls. Every option here gives a dedicated hostname, so `baseUrlPath` can stay unset.
- Streamlit has CORS and XSRF-protection settings that can interact with a different public origin. With edge auth on the same hostname, the default `enableXsrfProtection` usually works. Not verified in this session; test it.

### Gaps
- No primary-source confirmation of WebSocket support was captured for Cloudflare Tunnel, ngrok, Tailscale Funnel, Pinggy, Pangolin or localtunnel. From general knowledge, Cloudflare and ngrok proxy WebSockets by default, but that is unverified here. Recommend a quick test before committing.
- WebSocket idle timeouts on each provider (e.g., Cloudflare's 100-second idle timeout on proxied connections) were not verified.

## 6. macOS setup effort, launchd service, reliability

### Takeaway
`cloudflared` has a first-party `service install` for launchd, either per-user at login or system-wide at boot. ngrok recommends running as a background service and has no free timeout. Tailscale Funnel on macOS needs the open-source tailscaled variant, not the App Store app. Pinggy Free (60-minute timeout) and quick tunnels (new URL every time, no uptime guarantee) aren't suited to an always-on dashboard.

### Cited Findings
- `cloudflared service install` creates a launch agent that runs at login using `~/.cloudflared/`. `sudo cloudflared service install` creates a launch daemon that runs at boot using `/etc/cloudflared`. Start it with `sudo launchctl start com.cloudflare.cloudflared`. Logs go to `/Library/Logs/com.cloudflare.cloudflared.out.log` and `.err.log`. Prerequisite: a named tunnel and a `config.yml` with `tunnel` UUID and `credentials-file` — [Cloudflare docs: run as macOS service](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/configure-tunnels/local-management/as-a-service/macos/)
- Funnel works only where the Tailscale CLI runs; on macOS that means an open-source variant. It needs v1.38.3 or later, MagicDNS, HTTPS certs, and a `funnel` node attribute in the policy file. A port can't serve Serve and Funnel at the same time — [Tailscale KB 1223](https://tailscale.com/kb/1223/funnel)
- ngrok: "Free endpoints have no timeout"; run as a background service for unattended operation — [ngrok Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/)
- Pinggy Free: 60-minute timeout, and a new URL on restart — [pinggy.io](https://pinggy.io/)
- Quick tunnels: no uptime guarantee; new hostname per tunnel — [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)

### Inferences
- The Mac must be awake for any of these to work. Consider `caffeinate` or pmset settings, plus a launchd job for Streamlit itself bound to 127.0.0.1.
- Self-hosted Pangolin, frp or rathole adds a VM you have to patch, monitor and keep within free-tier rules. That's more operational load than Cloudflare or ngrok.

### Gaps
- Published uptime or SLA figures for the free tiers of Cloudflare, ngrok, Pinggy and zrok were not captured.
- Whether `ngrok service install` on macOS works on Free: not fetched.

## 7. Security risks and known incidents

### Takeaway
Free anonymous tunnels (TryCloudflare in particular) have been widely abused for malware delivery. That has led to defender blocklisting of whole tunnel domains, and to ngrok's interstitial phishing warning. For a private dashboard, the main risks are exposing an unauthenticated Streamlit app on a guessable or discoverable URL, and trusting the provider with plaintext.

### Cited Findings
- Proofpoint (2024): threat actors abused TryCloudflare (no-account tunnels with random subdomains) to deliver AsyncRAT and Xworm. A May 28, 2024 tax-themed campaign was followed by a July 11, 2024 wave of over 1,500 messages. Volumes ranged from hundreds to tens of thousands. Proofpoint noted that this undermines static blocklists. Cloudflare said it acts against malware use when URLs are reported — [Proofpoint: Threat actor abuses Cloudflare Tunnels to deliver RATs](https://www.proofpoint.com/us/blog/threat-insight/threat-actor-abuses-cloudflare-tunnels-deliver-rats); [SecurityWeek](https://www.securityweek.com/cloudflare-tunnels-abused-for-malware-delivery/); [The Hacker News, Aug 2024](https://thehackernews.com/2024/08/cybercriminals-abusing-cloudflare.html)
- ngrok shows an interstitial on free-tier HTML traffic specifically "to deter phishing" — [ngrok Free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits/)
- frpc doesn't verify the frps certificate by default — [GitHub fatedier/frp](https://github.com/fatedier/frp)
- Tailscale Funnel exposes the service to the public internet with no Tailscale identity headers, so public requests are anonymous — [Tailscale KB 1312](https://tailscale.com/kb/1312/serve) (via search summary)

### Inferences
- Corporate or school networks and some mobile security tools may block `*.trycloudflare.com`, `*.ngrok-free.app` or similar shared tunnel domains because of this abuse history. A custom domain on Cloudflare avoids that.
- This dashboard has pages that can trigger Alpaca or Robinhood actions (paper-order gates). Edge auth is not optional: any option without a real identity check makes those Streamlit pages publicly reachable. A second layer is advisable, e.g. a Streamlit-side check of the `Cf-Access-Jwt-Assertion` header, or keeping order-submission pages disabled in remote mode. That's a design recommendation, not a sourced fact.
- Binding Streamlit to 127.0.0.1 (as now) and letting only the tunnel connector reach it keeps the LAN surface closed.

### Gaps
- No 2025–2026 incident reports were retrieved specifically for ngrok, Pinggy, zrok, localtunnel or Pangolin. I only searched the TryCloudflare abuse case.
- No CVE review was done for cloudflared, ngrok agent, frp, rathole or Pangolin.

## Summary matrix (from cited findings above; "?" = unverified)

| Option | Free stable URL | Edge identity auth on free | TLS seen by provider | Always-on fit | Notes |
|---|---|---|---|---|---|
| Cloudflare Tunnel + Access | Needs own domain (cost ?) | Yes: OTP, IdPs (free-plan listing ?) | Yes (?) | Good (launchd service) | 50 users, 500 apps, 1,000 tunnels |
| Quick tunnel (trycloudflare) | No (changes per run) | No | Yes (?) | Poor | 200 in-flight cap, no SSE, abuse history |
| ngrok Free | Yes (`*.ngrok-free.app`) | OAuth, 3 MAU (TP-credit nuance ?) | Yes (?) | Good | 1 GB/mo, 20k req/mo, interstitial |
| Tailscale Funnel | Yes (`*.ts.net`) | No | No (relay can't decrypt) | OK (open-source tailscaled on macOS) | Beta; app must self-auth |
| Pinggy Free | No (random, 60-min timeout) | Basic or token only | Yes (?) | Poor | Pro price ? |
| zrok hosted | ? | OAuth/OIDC/email (details ?) | Yes (?) | ? | Docs moved to netfoundry.io |
| localtunnel | Requested subdomain (?) | None | Yes (?) | Poor | Maintenance status ? |
| Pangolin Cloud Free / self-host | Custom domains on free | PIN/passcode/email OTP; no external IdP on Cloud Free | Cloud: yes (?); self-host: your VM | Good with VM | 5 users, 15 public resources |
| frp / rathole on VM | Your domain + VM | Basic auth only (frp) | Your VM | Depends on VM | frpc doesn't verify cert by default |
