# OCI ARM Full Trading Desk: Multi-Broker Trading Application Deployment and Implementation Plan

**Version:** 3.0  
**Prepared:** November 14, 2026  
**Status:** Proposed implementation specification; not authorization to place orders or activate live trading  
**Repository:** https://github.com/jijoece/ai_stock_trading_v2  
**Target:** Oracle Cloud Infrastructure (OCI) Ampere A1, Ubuntu 24.04 ARM64  
**Supersedes:** OCI ARM Deployment Plan v2.0, which deliberately scoped the hosted UI to read-only research and gated paper workflows.  
**Companion architecture:** `docs/adr/0010-automated-live-trading.md` and `docs/automated-live-trading-implementation-plan.md` (both remain proposed / incomplete for live trading).

## 1. Product goal and operating principles

Build and deploy a **single private, phone-accessible, multi-broker Trading Desk** that can:

1. Connect **Alpaca** (paper and, when explicitly activated, live) and **Robinhood** (official Robinhood Trading MCP / Agentic account).
2. Display accounts, buying power, holdings, positions, cost basis, realized/unrealized P&L, orders, fills, stop orders, strategy ownership, fees, and health **per broker** and across brokers.
3. Let the authenticated operator **initiate**, preview, validate, confirm, place, cancel, and manage supported orders from the application, subject to broker capability and risk-policy checks.
4. Generate and execute **multiple independently configured strategies** with central risk limits, mandatory protective stop policy, paper-first testing, and separately approved live activation.
5. Monitor open risk continuously, including exit orders and protective-stop status, and alert on actionable incidents even when the user is not viewing the UI.
6. Recover safely from application restarts, provider disconnections, network outages, partial fills, unknown responses, duplicate signals, and machine loss.
7. Remain reproducible and inexpensive to run on OCI ARM without turning the cloud deployment itself into permission to trade.

**Target user experience:** One browser/PWA-like mobile entry point presents a broker-aware portfolio, an order ticket, an active-orders blotter, strategy controls, alerts, research, performance, and an administrative status page. The application distinguishes simulated paper balances from actual brokerage holdings and labels each position with broker/account/mode.

**Safety principle:** Python and deterministic policy authorize order fields; strategies or AI provide bounded signals; broker adapters perform supported actions; broker order/fill records are authoritative. A browser click, model message, or environment variable alone must never bypass policy or account activation.

**Scope baseline:** US long equities first. Options, short sales, margin, leveraged products, crypto, extended hours, and fractional shares are **excluded from execution in the initial release** even if a broker advertises them. They may appear read-only when legitimately supported. Expand only through explicit product/risks reviews and tests.

## 2. Verified starting point and material changes from v2.0

### Repository state checked on November 14, 2026

- Main package: `pyproject.toml` with `Python >=3.10`, Streamlit, pandas, SQLite storage, research, strategy selection, local paper books, and risk checks.
- Existing dashboard entry: `src/dashboard/streamlit_app.py`. Current seven pages: Overview, Decisions, Research Cycles, Portfolio, Robinhood Portfolio, Score a Ticker, and System Health.
- Existing launcher: `scripts/run_dashboard.sh` **already points to `src/dashboard/streamlit_app.py`**. Do not reintroduce the earlier path fix as unfinished work.
- Dashboard database access: `src/dashboard/services/database.py`, `AI_STOCK_TRADING_DB_PATH`; currently read-only (`mode=ro`, `PRAGMA query_only=ON`). **Preserve this read-only reporting boundary** instead of giving Streamlit direct broker/DB-write access.
- Storage: `src/trading_research/storage/database.py` initializes SQLite in WAL mode and applies schemas/migrations. Main paths: `RESEARCH_DATABASE_PATH` and `RESEARCH_DATA_DIR`.
- Existing segregated runtimes: `paper_runtime/` for Alpaca paper via LumiBot; `backtest_runtime/` for backtesting. Do not install those into the main project's virtual environment.
- Current config explicitly prohibits live execution; `config/strategies.yaml` has stage gates, and `config/paper_books.yaml` contains disabled-by-default activation controls. **Do not weaken old safety locks to add an execution feature.** Introduce the separately governed new broker service described in ADR 0010.
- Robinhood portfolio/scoring pages are optional authenticated **read-only** integrations; they are not equivalent to a full Robinhood order lifecycle.

### Broker capabilities that must shape the design

| Capability | Alpaca | Robinhood |
|---|---|---|
| Authorized access | Official Trading API; distinct paper and live endpoints/credentials | Official Robinhood Trading MCP with OAuth and a **dedicated Agentic/MCP account** |
| Read account/positions/orders | Documented REST and account/order updates | Official MCP tools for accounts, holdings, order history, quotes and P&L |
| Place equity orders | Official REST/SDK | Official MCP `review_equity_order`, `place_equity_order`, `cancel_equity_order` |
| Advanced order API | Bracket, OTO, OCO and stop types documented | Official MCP lists `review_advanced_order`, `place_advanced_order`, `cancel_advanced_order`; **exact supported schemas and linked-stop semantics need discovery and testing** |
| Streaming changes | `trade_updates` WebSocket, plus REST reconciliation | Do not assume a push execution stream; start with bounded MCP polling/reconciliation unless supported streaming is proven |
| Simulation | Alpaca paper account | No equivalent public Robinhood MCP paper environment verified; mock the MCP adapter for tests and use an explicitly capped Agentic live account only after approval |
| Unattended execution | Supported API, subject to account/risk capabilities | Agentic trading can operate without per-order approval, but **OCI-hosted custom-client authentication, session persistence, permission and advanced-stop behavior must be validated first** |
| Account scope | Selected Alpaca paper/live account | MCP may read multiple accounts, but **write orders only to its dedicated Agentic account**, never ordinary Robinhood holdings |

**Important policy distinction:** Robinhood's general third-party API policy restricts unapproved trading API integrations, while it explicitly offers the **official Trading MCP** for agentic trading. Use the officially authorized MCP only; do not use unofficial `robin_stocks` order methods, reverse-engineered endpoints, browser automation, or stored account passwords. If the Robinhood-supported custom-client route requires authorization/assistance, obtain it before enabling order write operations.

**Critical unknowns / Phase 1 spike:** Does `place_advanced_order` support an atomic entry with broker-resident protective stop (and, if desired, profit target) for the intended equity order type? What happens to partial fills, time-in-force, cancellations, authentication refresh, order identifiers, and historical event completeness? Until proven, **Robinhood automatic entries stay disabled**. Robinhood may still offer account monitoring and supported user-approved trade requests; the app must refuse an entry if it cannot meet the mandatory stop policy.

**Protective order warning:** A stop trigger is not a guaranteed execution price. Gap moves, volatility, halts and liquidity can produce worse fills. An attached order acknowledged by a broker is not necessarily an active protective leg after a partial entry fill; monitor actual state.

## 3. OCI infrastructure sizing and topology

### Initial Always Free configuration

- Shape: `VM.Standard.A1.Flex`, **2 OCPU / 12 GB RAM** as the conservative value shown in Oracle's current **Always Free Resources** page; **50 GB** boot volume, Ubuntu 24.04 ARM64 in the tenancy's home region.
- Note Oracle documentation is inconsistent: its separate **Arm-Based Compute** page still advertises 3,000 OCPU-hours / 18,000 GB-hours (often summarized as 4 OCPU / 24 GB), whereas its **Always Free Resources** page currently gives 1,500 / 9,000 (2 / 12). **Confirm the live tenancy Limits, Quotas and Usage and Always Free eligibility before provisioning. Do not budget for the larger numbers without confirmation.**
- Boot/block storage combined Always Free allowance: up to 200 GB, account-dependent; start at 50 GB. Object Storage allowances/pricing must be verified for backup retention.
- Single instance initially to minimize cost/complexity. If broker-monitoring and API workloads exceed budgets, or if live-trading operational requirements justify it, migrate to a paid VM and separately evaluated HA/failover design.
- ARM64 compatibility: validate separately the main venv, TA-Lib/indicators extra, LumiBot-based paper runtime, and backtest runtime. Do not assume availability of wheels merely because `pip install -e .` succeeds.

### Access model

- All application listeners bind to `127.0.0.1` or a Unix-domain socket. No ingress for Streamlit, FastAPI, broker adapters, worker control, or web terminals in the OCI VCN.
- Use **Tailscale Serve**, not Funnel, for HTTPS access from approved phones/laptops. Restrict tailnet grants/ACLs to the owner and specific devices.
- Bootstrap SSH with keys and source-restricted OCI ingress or OCI Bastion. After validating tailnet SSH/bastion, further restrict public SSH as feasible; preserve a documented recovery path.
- Network egress limited as practical to provider APIs, system security updates, OCI Object Storage, and necessary Tailscale connectivity. Never expose `.env`, SQLite, backups, or MCP tokens through the web app.
- No public ttyd or browser shell. OCI Ubuntu platform networking should not be altered by blindly enabling UFW; use OCI security controls and verify host firewall behavior.

### Logical service topology

```text
Phone/Desktop browser (Tailscale authorized + application step-up login)
                           |
                   Tailscale Serve HTTPS
                           |
              127.0.0.1:8501 Trading UI (Streamlit)
                           |
             Local authenticated control API
                   (FastAPI / UDS or loopback)
                           |
             Command + policy + audit boundary
              /                          \
     Read-only portfolio             Order/strategy commands
        query services                     |
              |                    Deterministic Risk Engine
              |                    + Order Management System
              |                     /                   \
              |         Alpaca adapter             Robinhood MCP adapter
              |      paper / separately live      official Agentic account
              |                     \                   /
              |                 Reconciliation workers
              |                         |
              +---------- SQLite append-only audit/state ---------+
                                   |
              Market-data/research + strategy engine (independent)
                                   |
                  Alerts / health / encrypted backup workers
```

**Architecture choice:** Keep existing Streamlit research screens and read-only query services, then add new broker-aware **Trading / Orders / Strategies / Risk / Alerts** pages that **only call a separately authenticated control API**. Do not perform broker write requests inside Streamlit scripts or grant the UI process broker trading credentials. The control API is private; it is not an internet-facing public API. If Streamlit proves inadequate for complex, responsive order UX, a later React/static PWA may replace the UI while preserving the API and trading core, but a frontend rewrite is **not a prerequisite** to building the application.

## 4. Trading product: modules and requirements

### A. Account and broker management

- Broker registry with IDs, adapter versions, capability matrices, environment (paper/live), account fingerprint and permission scopes.
- Connect/disconnect/re-authenticate workflows; credential/refresh-state diagnostics without displaying secrets.
- Per-account status: connected, stale, re-authentication needed, rate-limited, paused, blocked, unauthorized, disconnected.
- Account selector on every order or strategy; default **never** silently routes to a live account or sends the same trade to both brokers.
- Distinguish existing Robinhood personal accounts (possibly visible read-only through MCP) from the **dedicated Agentic account** authorized to trade.
- Account balances, positions, cost basis, buying power, order history, and settled/unsettled cash when supported. Missing fields are explicitly labeled unavailable.

### B. Unified portfolio and trade monitor

- Tabs: **All Accounts**, **Alpaca Paper**, **Alpaca Live**, **Robinhood Agentic**, and read-only traditional Robinhood accounts if accessible.
- Open positions, average cost, current/best-known price and timestamp, broker market value, unrealized/realized P&L, open orders, reserved buying power, strategy ownership, aggregate gross exposure, concentration and planned open risk.
- Trades/Activity blotter: broker order ID, internal client order ID, symbol, account, strategy/manual origin, direction, order type, limit, requested/fill qty, stop/target, status, timestamps and warnings.
- Filters by broker/account, mode, symbol, strategy, date, order state, protected/unprotected and manual/automated.
- Data freshness/provenance badges: streaming live, polled, delayed, market closed, stale, disconnected, unknown. Do not call polling “real-time” without evidence.
- Reconciliation exceptions prominently displayed; never present local estimates as broker truth.

### C. Manual order ticket and initiation

- Order ticket is available from watchlist, screener, portfolio position, and trade detail.
- Inputs: explicit broker **and account**, symbol, side, qty (whole shares initially), order type, limit price, time-in-force, required initial stop, optional target, source tag/manual reason.
- Preview before submit: broker-side preliminary validation where supported, projected max notional, planned entry-to-stop risk, estimated buying-power usage, portfolio exposure, daily-risk impact, latest quote timestamp, order expiration and eligibility.
- **Two distinct actions:** `Create Preview` (zero broker writes) and `Submit Order` (creates immutable intent and transmits only after independent server-side revalidation). Changing any field invalidates the preview.
- Per-trade explicit confirmation plus step-up authentication in manual-live mode. Require a short-lived, single-use, server-issued submission token tied to broker/account/order payload and current policy version.
- Cancel/replace/close actions show effects on existing broker protective stops and cannot accidentally flip a long position short.
- Distinguish **rejected by local policy**, **broker rejected**, **queued**, **submitted**, **unknown outcome**, **partially filled**, **fully filled**, **canceled**.
- If an entry cannot be placed with the required verified broker-native stop policy, **block the in-app buy** and show the concrete unsupported capability. An operator can use the broker's own official app separately, but such action is not misrepresented as approved/protected through Trading Desk.

### D. Automated strategies and watchlists

- Reuse existing `momentum_breakout`, `mean_reversion`, `event_catalyst`, shortlist, signal contracts, local paper books, backtesting, shadow tracking and strategy-to-paper bridge.
- Strategy control fields: stable ID, version/config hash, symbols/watchlist, account binding, mode (`backtest`, `shadow`, `local-paper`, `broker-paper`, `live`), trading session, activation state, max risk/trade, per-strategy capital and planned risk budgets, order type, stop method, hold period, exit rules.
- Support concurrent strategies but one centralized **account + symbol conflict/reservation owner** (prevent duplicate entries and opposite instructions).
- Modes advance only through evidence-backed gates: fixture -> historical backtest -> shadow -> local paper -> Alpaca broker paper -> bounded live. Robinhood requires its own live capability and user-approval pilot gate.
- Schedule using exchange calendars, market timezone, holidays, early closes, DST; enforce signal expiry and market-data age before orders.
- AI may research/score but **cannot generate arbitrary broker tool calls**, rewrite risk limits, select live credentials, or bypass risk/stop policy.
- Engine and order monitor run independently of dashboard/phone state.

### E. Deterministic portfolio risk and exits

- Enforce long-only US equity scope; per-trade risk as planned shares x (entry - stop), maximum total open planned risk, concentration, strategy budget, per-account notional, daily new-order cap, max daily realized/unrealized loss and drawdown, buying-power, stale-data, broker trading halt, and symbol cooldown.
- Every automated and in-app manually initiated **entry** must carry a broker-supported protective-stop plan and must not proceed until that plan satisfies broker capability and risk gates.
- Broker stop coverage must be monitored **against filled quantity**, not only acknowledged parent order. Partial entry fills require verified corrective behavior before multi-share automation is unlocked.
- Avoid cancel-first strategies that leave a position naked. Changing a stop/target or exiting a position must use provider-supported safe sequences and reconciliation.
- When protection becomes absent or uncertain: **pause new entries**, alert, reconcile, and follow a preapproved corrective/flatten policy that cannot create an opposite position. Do not automatically cancel protective stops when executing a kill switch.
- Portfolio-level kill switch blocks **new risk** across both brokers, does not claim to cancel existing brokerage orders or protect fills by itself, and requires a separate deliberate reactivation after review.
- Protect against stop/target race, late fills, unfilled/canceled parents, corporate actions, after-hours gaps, market halts and broker disconnects.

### F. Notifications and operational command center

- Alerts for order submitted/rejected/filled/partially filled, stop absent or rejected, broker reauthentication, stale market data, failed research run, risk limit, engine paused/kill, account-reconciliation mismatch, low disk, backup failure, host unreachable.
- Send actionable alerts to one explicitly configured channel (e.g., email or push provider), with severity, broker/account alias, incident ID, and direct **private** dashboard route. Do not embed credentials or full account numbers.
- Dashboard health: each service's last heartbeat, last broker sync, time since last valid quote and last reconcile, pending approval count, and whether the engine is merely online versus actively authorized for trading.
- Audit every user action and every automated decision with immutable IDs, who/what initiated it, config hash, broker account, request, reply, state transition and outcome.

## 5. Detailed order execution lifecycle

### Manual entry

```text
UI order ticket
 -> explicit account + mode
 -> broker capability validation
 -> local preview + broker preview if supported (non-mutating)
 -> deterministic risk/stop checks
 -> short-lived, immutable preview
 -> owner confirms + step-up authentication
 -> revalidate account, price age, risk, stop and intent hash
 -> durable intent + idempotency key + SUBMITTING state
 -> isolated broker adapter sends authorized request
 -> record broker ID or mark UNKNOWN, never blindly retry
 -> reconcile broker parent + protection legs + position/fills
 -> stream/poll lifecycle into dashboard + alert service
```

### Automated strategy entry

```text
fresh evidence -> versioned signal -> allocator/symbol arbiter
 -> risk + stop + broker-capability checks -> bounded order intent
 -> verified active strategy/account authorization
 -> broker submit -> fill/stop verification -> monitoring/reconciliation
```

### Order state and duplicate protection

- Persist state transitions: `DRAFT -> PREVIEWED -> APPROVED -> SUBMITTING -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED`, plus `REJECTED`, `CANCELED`, `EXPIRED`, `UNKNOWN_REQUIRES_RECONCILIATION` and post-fill `PROTECTION_VERIFIED/PROTECTION_UNCERTAIN` records (exact schema names may follow repository conventions).
- Generate a stable request ID and broker-supported client-order-ID when available; map to broker IDs. An HTTP timeout after send **never** authorizes immediate resubmission. First query authoritative broker records, then decide using documented evidence.
- Prevent duplicate account/symbol/strategy intent across concurrent workers and UI retries using DB uniqueness constraints and leases/fencing. Only one actor may execute an active command.
- Broker-originated/manual-outside-app orders and position changes must be discovered by reconciliation and marked as **external origin**, not silently attributed to a strategy.
- Alpaca streams are hints/updates; on reconnect, fetch authoritative account/order/fill state. Robinhood starts with bounded MCP polling plus read-after-write verification until supported push events are established.

## 6. Credentials, app authorization, and service separation

### Human and application identity

- Require Tailscale authorized device **plus application-level identity/step-up** for trading actions. Tailscale access by itself is not sufficient for live-order permissions.
- Prefer passkey/WebAuthn or an equivalent well-tested MFA mechanism for live order submits, strategy activation, emergency flatten and credential changes. Require fresh auth for high-impact actions; revocable sessions, expiry, rate limiting and CSRF protection.
- Roles: `observer` (read only), `operator` (preview and approved manual trading), `admin` (broker connection/config, live activation); start with a single owner. Authenticate and authorize each control API request server-side.
- UI never sees raw broker credentials/tokens. The trading engine and authorized adapters obtain separately scoped secrets through root-controlled systemd credentials/OCI Vault or comparably secured mechanism. Mask account IDs in logs/UI.

### Broker identity isolation

- Alpaca paper and live credentials use **separate** environment/secret namespaces and endpoint pinning. The paper process cannot reach the live endpoint, and no live credentials are loaded into the dashboard.
- Robinhood MCP OAuth connection must use the **official Trading MCP** (`https://agent.robinhood.com/mcp/trading`) through an approved custom external-agent client, not generic web scraping. Validate headless Linux OAuth callback/setup, token persistence/expiry, refresh, disconnect and scope with the provider before declaring the OCI adapter ready.
- Preserve explicit account pinning: a Robinhood read call may surface all accounts, but order writes are possible only to the chosen dedicated Agentic account. Unexpected account IDs stop execution.
- Production system must operate without Claude Code or another conversational developer agent being in the order-placement path. MCP can be accessed through a dedicated restricted broker adapter, subject to Robinhood's supported client policy; the model never receives unbounded MCP trading tools.
- Discovery of MCP tool schemas or changes to them must be versioned and tested before any runtime order capability is enabled.

### API and UI boundary

- Add a small control API (FastAPI recommended after a minimal architecture review), bound to Unix domain socket or localhost. Streamlit's existing read-only query layer remains intact. Only the API handles authenticated commands, immutable previews, policy checks and durable command queuing.
- Isolated broker adapters consume approved commands; UI process account can never read broker write secrets. The API must not accept arbitrary provider tool names or raw JSON passthrough from browsers.
- Every command is explicit and allowlisted: `preview`, `submit`, `cancel`, `replace` (only if supported), `reduce/close`, `pause_new_entries`, `request_live_activation`, `activate_live` and broker disconnect. No arbitrary shell or SQL execution endpoints.

## 7. Storage model and operational data contracts

Continue with SQLite initially, one authority for ordered writes and durable transactions. Main dashboard uses read-only connections. WAL and `busy_timeout` are already in the repo; measure lock behavior under simultaneous UI, worker, polling and background reconciliations. Move to PostgreSQL only if benchmarked concurrency/HA requirements justify it, with explicit migration tests.

New schema families (adapt naming and migration conventions to the current repository):

- `broker_connections`, `broker_accounts`, `broker_capabilities`, `broker_sessions` (metadata only; no raw secret in DB).
- `account_snapshots`, `broker_positions`, `broker_orders`, `broker_fills`, `protective_legs`, `broker_sync_cursors`.
- `trade_intents`, `trade_previews`, `trade_approvals`, `trade_execution_attempts`, `order_events`, `order_reconciliations`.
- `strategy_deployments`, `strategy_account_allocations`, `strategy_signals`, `strategy_runs`, `strategy_activations`, `symbol_reservations`.
- `risk_state_snapshots`, `risk_incidents`, `pause_kill_events`, `user_sessions`, `audit_events`, `notification_attempts`.

Mandatory attributes: broker ID, environment, **account fingerprint**, owner/strategy origin, UTC event timestamp, source timestamp, correlation/client order ID, server policy/config hash, execution state, broker raw-status mapping and redacted structured reason code.

**Reporting semantics:** Account snapshots are not financial ledgers; open positions and fills from each broker are authoritative when reconciled. Multi-broker consolidated P&L must declare method, included/excluded costs, exchange data freshness, currencies and incomplete broker fields. Do not combine paper with live capital in a misleading headline.

## 8. OCI runtime and service inventory

### Suggested filesystem layout

```text
/opt/trading-desk/releases/<pinned-commit>/  application code, read-only to service users
/opt/trading-desk/current -> releases/<pinned-commit>
/opt/trading-desk/venvs/main/            main/Streamlit/API environment
/opt/trading-desk/venvs/paper/           isolated LumiBot paper environment
/opt/trading-desk/venvs/backtest/        isolated backtest environment
/var/lib/trading-desk/research.sqlite3   persistent database; not in releases
/var/lib/trading-desk/backups-staging/   protected temporary SQLite snapshots
/etc/trading-desk/                       protected configs and non-secret paths
/run/trading-desk/                       sockets/runtime state
```

### Recommended systemd units

| Unit | Role | Starts automatically? |
|---|---|---|
| `trading-desk-ui.service` | Existing Streamlit views plus broker-aware order/strategy pages, listens 127.0.0.1:8501 | Yes, after read-only startup checks |
| `trading-desk-api.service` | Local authorization, previews, command handlers | Yes, disabled from broker writes until policy readiness |
| `trading-desk-reconcile.service` | Loop/poller/consumer for broker account and order state | Only after connection setup; defaults read-only |
| `trading-desk-strategy.service` | Bounded strategy engine and signal evaluation | Paper/shadow only initially; independent from UI |
| `trading-desk-alpaca-worker.service` | Authorized broker adapter process/subprocess | Paper initially, live adapter separately armed |
| `trading-desk-robinhood-worker.service` | Approved Robinhood MCP client, account-restricted | Read-only initially; writes only after provider capability gates |
| `trading-desk-research.service` + `.timer` | Finite market-calendar-aware analysis cycles | Activated after validation |
| `trading-desk-backup.service` + `.timer` | Consistent encrypted off-VM backups | Yes |
| `trading-desk-health.service` + `.timer` | Health evaluations and alert dispatch | Yes |

On 2 OCPU/12 GB, do not run redundant heavyweight idle model processes. Where safe, use small processes or on-demand broker subprocesses while keeping **security and failure isolation** logical boundaries intact. Set memory/CPU ceilings, restart policies, stop timeouts, heartbeat and journald retention; test actual ARM usage.

### Environment configuration example (non-secret)

```ini
RESEARCH_DATA_DIR=/var/lib/trading-desk
RESEARCH_DATABASE_PATH=/var/lib/trading-desk/research.sqlite3
AI_STOCK_TRADING_DB_PATH=/var/lib/trading-desk/research.sqlite3
TRADING_DESK_DEFAULT_MODE=paper
TRADING_DESK_API_BIND=unix:/run/trading-desk/control.sock
TRADING_DESK_MANUAL_LIVE_ENABLED=false
TRADING_DESK_AUTONOMOUS_LIVE_ENABLED=false
TRADING_DESK_ROBINHOOD_WRITE_ENABLED=false
TRADING_DESK_ALPACA_LIVE_WRITE_ENABLED=false
```

These example `TRADING_DESK_*` settings are **new proposed design**, not existing repository configuration. Implement them only behind validated schema and persistent operator activation. Boolean environment variables are **not sufficient authority**: a credential and feature flag cannot create live execution without matching approved account/mode, persisted activation, broker capability proof and current risk health.

## 9. Implementation roadmap (separate reviewed PRs, resumable Claude Code sessions)

### Phase 0: Baseline audit + ADR alignment

- Map existing modules/tests to the full product requirements; revalidate current main and accepted ADRs.
- Write a gap matrix for manual order management, multi-broker monitoring, Robinhood MCP trading, live activation, and attached-stop coverage. Retain current paper-only semantics until a separately approved change.
- Make architecture decisions for app identity, FastAPI control boundary, broker adapter contracts, SQLite concurrency, and UI re-use.
- Record which parts of ADR 0010 must be accepted or revised by the owner before live execution work can move beyond mocks.
- **Exit:** signed-off requirement/capability matrix and test list; zero broker writes.

### Phase 1: Broker capability and authentication spikes, **read-only**

- Alpaca: paper authentication, accounts/positions/orders, quote freshness, `trade_updates` reconnect, bracket/OTO/OCO limitations, partial-fill behavior, rate limits.
- Robinhood: official external-agent MCP authorization for an OCI-hosted custom client, dedicated Agentic account identity, tool schemas, review/advanced-order payloads, OAuth renewal, connection recovery, polling/latency, stop/linked-order behavior, and write-scoped approval gates. Use schema inspection and non-mutating broker tools first.
- Separate **documented** capability from **observed** behavior and reject unknown order types.
- No live or Robinhood trading orders during discovery without a separate operator-approved test plan and disposable/restricted account.
- **Exit:** per-broker capability matrix and explicit `SUPPORTED/UNSUPPORTED/UNVERIFIED` flags, unit-test fixtures and hard denial for unverified stops.

### Phase 2: OCI ARM baseline deployment and secure access

- Provision verified Always Free eligible 2 OCPU/12 GB (or tenancy-confirmed alternate). Ubuntu 24.04 ARM64, 50 GB disk; configure budgets, quotas, outbound networking, key-only bootstrap SSH.
- Install main package, indicators if ARM-supported, paper runtime, and backtest runtime in **separate** venvs; pin a tested commit and dependencies.
- Configure one persistent SQLite path and verify schema initialization or restore (do not accidentally replace/misplace an existing DB).
- Install Tailscale Serve and run current dashboard private on loopback via systemd.
- **Exit:** private dashboard accessible on phone; zero public app ports; full ARM-specific offline smoke suite passes; reliable reboot.

### Phase 3: Broker abstraction, schemas, unified read-only monitoring

- Implement stable `BrokerAdapter` interface and adapter-specific capability declarations.
- Build normalized account/position/order/fill models with broker/account environment tags, freshness, masked identifiers, and persisted reconcile cursors.
- Implement Alpaca read-only and Robinhood MCP read-only adapters; use official auth/session flows. Add snapshot + periodic sync (stream + REST Alpaca; MCP poll Robinhood).
- Enhance Streamlit with **All Accounts**, broker filters, active orders, position detail, account connection/error state and combined portfolio reporting.
- **Exit:** all read flows tested with offline fixtures; authorized sandbox/paper observations; missing data not shown as zero.

### Phase 4: Authenticated control API and manual trading UX

- Add local FastAPI command boundary and durable order-intent/preview/approval models.
- Add app-level owner identity, step-up auth, CSRF/session protection, rate limits and allowlisted commands.
- Build Trading, Orders, Risk and Alerts pages in Streamlit calling the control API, never broker SDK/MCP directly.
- Add fake broker adapter accepting only testing orders; every frontend control has a backing permissioned API and audit test.
- **Exit:** full interactive order-ticket flow succeeds end to end against deterministic fake adapter, fails closed under malicious/replayed requests.

### Phase 5: Manual Alpaca paper execution and order lifecycle

- Integrate the **isolated paper** adapter through supported broker order methods, broker preview/validation, durable idempotency and confirmations.
- Verify attached OTO/bracket stops, stop leg states, partial fills, cancel/replace, rejected stops, stale data, unknown POST response, stream disconnect/recovery.
- Add working position close/reduce UI with stop collision/replace policies.
- **Exit:** user can preview/place/monitor/cancel supported **Alpaca paper orders** from phone; broker truth reconciles; no new live authority.

### Phase 6: Robinhood agentic account read/write integration, gated

- Establish official MCP connection on OCI through a supported agent/client. Require selection of the dedicated Robinhood Agentic account for writes, never ordinary investing accounts.
- Wire supported equity preview/order/cancel APIs, order history and read-after-write verification into the shared order state machine.
- Determine which **advanced order** behaviors actually support atomic, broker-resident protective stops; test with a separately consented, tightly capped Agentic account. Maintain `UNVERIFIED` until observed.
- If atomic protection cannot be demonstrated, keep new buy entries **blocked in this app**, or provide a proposal requiring the user to execute outside the app. Do not implement naked buy -> hope to place stop as an automatic workaround.
- **Exit:** supported Robinhood order lifecycle is integrated with authenticated operator controls and accurate capability warnings. Automated Robinhood entries stay disabled until stricter later gates pass.

### Phase 7: Cross-broker risk, reconciliation and incident handling

- Consolidate account exposures for visibility and global risk policy while keeping orders/account ownership separate.
- Implement `UNKNOWN_REQUIRES_RECONCILIATION`, duplicate avoidance, non-overlapping symbol reservations, stale-sync protection, pre-execution account checks, and evidence-based recovery.
- Implement kill/pause of new risk, notification dispatch, stop coverage audit, protection-loss corrective actions and broker-specific incident runbooks.
- **Exit:** fault-injection test matrix passes across both adapter simulations; UI reflects precise states and all critical failures alert.

### Phase 8: Multi-strategy autonomous **paper** execution

- Connect current deterministic strategies/signals to the shared allocator/order manager via an approved paper-account bridge, keeping existing controls.
- Schedule sessions using real exchange calendars and day boundaries, with rate/budget limits and strategy activation hashes.
- Verify stop, trailing, target, expiration, exits, split/corporate-action and restart consistency in multi-day Alpaca paper soaks.
- **Exit:** two or more strategies run concurrently and automatically trade in Alpaca **paper** with proven broker-stop coverage, no duplicate entries, documented evaluation metrics and reproducible logs.

### Phase 9: Limited live trading capability, separate explicit owner approval

- Requires accepted ADR 0010, completed protective-stop proof, alert test, recovery drill, real-account and risk limits approved, broker permissions validated.
- Enable **Alpaca live first** in an isolated environment with account-pinned secrets; one-share pilot and conservative caps until partial-fill/leg proof permits expansion.
- Robinhood live activation separately requires supported Agentic MCP write integration **and** demonstrated protective order/partial-fill behavior. Never infer live readiness from Alpaca's capability.
- No silent strategy re-activation after deploy/reboot/config change. All live activations have expiry, audit, version pin and explicit operator action.
- **Exit:** independent operational review, documented owner activation, bounded live pilot with reconciled fills and stops. Successful UI or paper tests do not count as this gate.

### Phase 10: Production hardening, backups and alerts

- Store SQLite on persistent volume/path outside code releases. Use SQLite online backup API, integrity checks, encryption and **off-VM** Object Storage or another independently managed destination.
- Protect encryption material, rotate secrets, implement restore drills and tested recovery from VM reclaim, disk exhaustion, data corruption and provider downtime.
- For paper: validate initial 24-hour recovery-point objective as a planning target; for live, set much stricter alert/recovery objectives and document how broker-managed stops persist during host loss.
- Alert independently on stale broker sync, order/stop uncertainty, paused engine, missed strategy runs, app down, backups failing or authentication expiry. Test that an absent UI does not suppress alerts.
- **Exit:** successful clean replacement-VM rebuild, DB restore, credential reauthorization, and position/order reconciliation **before** any new live entry can be rearmed.

### Phase 11: Repeatable deployment, rollback and acceptance

- Build `deploy/oci/` with idempotent `preflight`, `install`, `migrate`, `validate`, `status`, `backup`, `restore-test`, `deploy`, `rollback` and `rebuild` utilities.
- Release immutable Git SHA + pinned deps; perform backup before migrations; reject downgrade across incompatible DB schema. Deploy UI independently of a live worker where possible.
- Add OCI ARM end-to-end smoke workflow, CI security gates, network-open-port tests, broker-fake/fault tests and an operator acceptance checklist.
- **Exit:** another clean ARM VM can reproduce the tested state from code/config/secrets/offsite backup without manually patching the application.

## 10. Critical test scenarios (mandatory before live activation)

| Scenario | Required behavior |
|---|---|
| Two simultaneous strategies buy the same symbol/account | Exactly one authorized reservation/intended entry; second rejected/merged per policy |
| UI user double-taps Submit | Single order attempt and stable client intent; no duplicate brokerage order |
| Broker accepts order but HTTP/MCP response times out | Mark unknown; reconcile authoritative broker state; **no blind retry** |
| Entry has no stop or invalid stop | Block before broker write |
| Broker acknowledges parent but protective leg is missing/rejected | Pause new entries, alert, reconcile/correct under preapproved policy |
| Entry partially fills | Check protective coverage for actual filled quantity; deny multi-share unattended entry until proven |
| Robinhood account read includes a traditional brokerage account | Allow display if permitted; **never** send orders to it through the MCP adapter |
| Robinhood advanced-order schema changes | Fail closed until revalidated; no experimental writes in production |
| Robinhood OAuth/token expires | Mark disconnected, alert, block new orders; preserve and reconcile broker-resident existing orders |
| Alpaca trade-update stream disconnects | Reconnect and REST backfill; no assumed fills during gap |
| Stop executes while the VM is offline | Reconcile fill on restart and avoid selling an already closed position |
| Manual exit races with stop execution | Prevent oversell/reversal via fresh broker order/position check and safe cancel/replace sequence |
| Stop-market slips on a price gap | Record actual fill and breach; never imply loss was bounded by stop price |
| Database file is locked during a UI trade | Serialize writes / retry safe DB operation; no second broker order generated |
| VM restarts after live authorization | Boot in observation/reconciliation state; do not silently rearm entries |
| User activates global kill switch | Block new entries and alerts; **do not remove broker-resident stops** |
| Tailscale unauthorized device or expired app session | No access or no trading action; failed events audited |
| Encrypted backup restored on replacement VM | Restore integrity, recover schema, reauthorize credentials, reconcile all broker orders before rearming |
| Optional Score a Ticker / Robinhood Portfolio UI enabled | Only allowed read operations and capped calls; no implicit trading permission |
| Provider returns stale/missing quote | No order entry; UI shows stale/unknown source instead of fresh value |

## 11. Release gates and definition of done

**A. Hosted app (not yet executing):** Phone/desktop access works privately; existing research pages render; All Accounts and Orders show accurate marked-source snapshots; service health and backup monitoring work.

**B. Full manual trading MVP:** Manual order preview, broker selection, risk checks, step-up confirmation, placement, cancellation, fills, stops and audit work for Alpaca paper and every **verified supported** Robinhood Agentic operation. Missing Robinhood protective-order support causes a visible blocker, not an unsafe fallback.

**C. Autonomous paper trading:** At least two strategies produce independently controlled, automatically executed Alpaca paper orders with verified protective stops, fail-safe restart behavior, multi-day soak and alerts.

**D. Real-money trading:** Only after a separately authorized broker-by-broker activation, pilot evidence and ADR acceptance; live stop coverage and reconciliation demonstrably work. A deployment or config edit never grants trading authority by itself.

**E. Reliable operations:** A clean Ubuntu ARM rebuild restores data and service state safely and reconciles each broker before new entries. Alerts and audit logs are usable during incidents.

## 12. Claude Code execution protocol and handoff

1. Store this plan in `docs/deployment/oci-arm/PLAN.md` as the **deployment/product roadmap**, not as an accepted live-trading ADR. Do not replace ADR 0010 acceptance with a deployment commit.
2. Create/maintain `docs/deployment/oci-arm/IMPLEMENTATION_STATUS.md` recording each phase, Git SHA, exact files touched, focused tests, full test outcome, blockers, unresolved owner decisions, broker capabilities, and **next executable task**.
3. Execute **one independently reviewable slice per PR**; do not spend an entire large Claude Code session reading unrelated source or implementing all phases.
4. Use separate workers only for independent work (docs, narrowly scoped tests, adapter research). Keep write paths and migration ownership single-threaded. Maintain a scratchpad checklist/checkpoint for multi-session continuity.
5. Read current code/tests/accepted ADRs before editing. Reuse repositories, risk primitives and order state machines where possible; avoid parallel systems of record and speculative rewrites.
6. Run offline fixture tests and security checks by default; never call real broker writes, connect real credentials, activate a strategy, or alter OCI infrastructure without an explicit operator-approved step.
7. Every PR must have tests for permissions, mode separation, broker capability rejection, duplicate submission and failure recovery relevant to its phase. Stop after its exit gate and report precise changes.

### Suggested first prompt for Claude Code

> Review the attached OCI ARM Full Trading Desk v3 plan against the latest `ai_stock_trading_v2` main branch and the existing ADRs. Begin **Phase 0 only**: create a precise requirements-to-code/test gap matrix, the target adapter/control-API contracts, a broker capability list with clear `SUPPORTED / UNVERIFIED / UNSUPPORTED` flags, and `docs/deployment/oci-arm/IMPLEMENTATION_STATUS.md`. Preserve the existing read-only dashboard reporting path, paper-only execution boundaries, and all live-trading locks. Do not install OCI services, request credentials, submit orders, or activate live trading. Keep the changes minimal and focused, run targeted offline tests, and prepare one reviewable PR. Stop after Phase 0 and record the exact next task.

## 13. Owner decisions before live activation

1. Which brokerage accounts will be connected? Alpaca paper/live account and a **separate funded Robinhood Agentic MCP account** are distinct entities.
2. Should manual trading support both brokers wherever capability gates pass, with autonomous execution initially Alpaca only? **Recommended yes.**
3. What maximum capital, per-trade planned risk, portfolio gross exposure, per-day loss and maximum daily notional caps should be approved? Treat initial values as configurable suggestions, not automatic policy.
4. Should stop and take-profit be mandatory for all entry types or only stop mandatory? **Recommended mandatory initial broker-native stop for both manual and automated buys.**
5. Which alert channel and backup destination can the operator support outside the OCI VM?
6. Is a paid OCI compute/backup option acceptable before unattended live trading? **Recommended for reliability assessment**, but not necessary for building the first paper/private manual UI stages.
7. Has Robinhood confirmed the chosen custom OCI-hosted MCP client is supported, and have the advanced-order stop/partial-fill semantics been validated?

These decisions can be staged by phase. None should block Phase 0 or an offline OCI-compatible build.

## 14. Primary documentation and source references

- **Current repository:** https://github.com/jijoece/ai_stock_trading_v2
- **Live trading ADR proposal:** https://github.com/jijoece/ai_stock_trading_v2/blob/main/docs/adr/0010-automated-live-trading.md
- **Current existing dashboard:** https://github.com/jijoece/ai_stock_trading_v2/blob/main/src/dashboard/streamlit_app.py
- **Robinhood official external-agent onboarding:** https://robinhood.com/us/en/support/articles/agentic-trading-overview/
- **Robinhood agent tools and permissions:** https://robinhood.com/us/en/support/articles/trading-with-your-agent/
- **Robinhood third-party API policy:** https://robinhood.com/us/en/support/articles/third-party-connections/
- **Alpaca order classes and stops:** https://docs.alpaca.markets/us/docs/orders-at-alpaca
- **Alpaca working with orders:** https://docs.alpaca.markets/us/docs/working-with-orders
- **Alpaca streaming:** https://docs.alpaca.markets/us/docs/websocket-streaming
- **Oracle Always Free Resources:** https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- **Oracle ARM resource overview (verify discrepancies):** https://docs.oracle.com/en-us/iaas/Content/Compute/References/arm.htm
- **Tailscale Serve:** https://tailscale.com/docs/features/tailscale-serve
- **SQLite online backup:** https://www.sqlite.org/backup.html

---

**Scope statement:** This plan defines the **complete intended application** and its staged, verifiable deployment. It is not a claim that the existing repository already implements Robinhood/Alpaca autonomous live trading. Broker-specific capabilities must be proven, and no real-money trading authority is activated by documentation, installation or a successful UI test.
