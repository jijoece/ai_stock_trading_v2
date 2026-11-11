# ADR 0010 (Revised): Autonomous Multi-Strategy Live Equity Trading with Mandatory Stop-Loss Protection

**Status:** Proposed. Requires repository-owner acceptance and explicit live activation.

**Date:** 2026-11-11

**Supersedes:** The proposed 2026-11-10 version of ADR 0010, which required an operator to approve each Robinhood MCP order. The original proposal was not accepted, so this revision does not itself authorize real-money trading.

**Companion:** [`../automated-live-trading-implementation-plan.md`](../automated-live-trading-implementation-plan.md). The roadmap lives only there; the copy that was embedded in this ADR was removed on 2026-11-11.

**Review amendments:** Section 4A (added 2026-11-11 after a review against the repository) changes or completes parts of Sections 2–4 and adds owner decisions O11–O16. Where 4A and an earlier section differ, 4A applies. The amendments are proposed with the rest of this ADR and need the same owner acceptance.

## 1. Objective

Turn the existing research and paper-trading application into a **controlled, autonomous, multi-strategy trading system** that can monitor US stocks, generate strategy signals, size positions, buy, manage, and sell them in a **real brokerage account** without manual approval of every transaction.

Every live entry must have a strategy-defined, risk-validated **hard stop-loss attached through a supported broker-managed order mechanism**. Strategies may also define take-profit targets, trailing-stop rules, time-based exits, and signal-driven exits. The application must remain safe when its process, its AI services, or its market-data feed is unavailable. A stop is an order instruction, not a guarantee of an exit price or a maximum loss.

**Required user workflow:** Configure and validate strategies -> complete paper-trading evidence -> arm approved strategies and limits -> application trades automatically within those limits -> operator monitors performance and can pause or disable trading.

## 2. Existing constraints and migration

ADR 0001 currently makes real execution unavailable through three locks:

1. `execution/live_gateway.py` exposes only `DisabledLiveExecutionGateway`.
2. `execution/config.py::load_execution_config` rejects `trading_mode != paper` and `live_trading_enabled != false`.
3. SQLite triggers reject writes to `real_orders`.

`paper_books/config.py` also rejects `allow_live_broker=true`, and `config/tool_policy.yaml` denies Robinhood order tools. These controls **stay in place** until the new isolated live path has passed acceptance gates. They must never be bypassed simply to get an order through. Preserve the current paper path as paper-only and introduce a separately armed live runtime and book.

The prior proposal's **human approval for every ticket**, **Claude Code MCP relay**, and **ban on scheduled trading** are explicitly rejected as a production design because they conflict with autonomous execution. ADR 0003's intent remains: AI can research or propose ideas but cannot hold unrestricted order-placement authority.

## 3. Architecture decision

**Production execution uses a supported, documented, noninteractive brokerage trading API, not an LLM session.** Alpaca is the initial reference implementation because its documented API offers separate paper/live endpoints, bracket and OTO equity orders, client order IDs, and order-status streams. The design remains broker-agnostic behind an `ExecutionBroker` interface. Robinhood may remain a read-only data/research integration and can be evaluated as a live adapter later **only** after written authorization (where required), documented programmatic access, and equivalent automated-stop and reconciliation capabilities are verified. Do not use unofficial Robinhood trading APIs or browser automation to bypass the restriction.

```text
Market Data + Calendar + Account State
                  |
          Strategy Registry
       (strategy A / B / C ...)
                  |
       Versioned Trade Signals
                  |
        Portfolio Allocator
     + Symbol Conflict Manager
                  |
    Deterministic Risk Engine
        (accept / resize / reject)
                  |
       Order Management System
    + Stop-Loss Lifecycle Manager
                  |
       Broker Adapter (Alpaca)
                  |
      Broker-native Entry + Stop
                  |
 Fills / Order Events -> Reconciliation
                  |
 Append-only Audit / Alerts / Dashboard
```

Only the deterministic execution service, using restricted live credentials, can submit, modify, or cancel orders. Claude Code remains a **developer and reviewer**, not a participant in the production order path. An AI research overlay may disable or reduce a proposed trade but may not manufacture, enlarge, or send an order.

## 4. Functional decisions

### D1. Multiple strategies run independently under one risk authority

- Build a strategy registry with stable strategy IDs, pinned code/config versions, modes (`backtest`, `paper`, `shadow`, `live`), schedules, eligible tickers, and capital/risk budgets.
- Each strategy can issue **ENTRY**, **EXIT**, **REDUCE**, or **HOLD** signals with a unique `signal_id`, strategy ID, symbol, bar/as-of time, expiration, rationale/feature references, desired entry parameters, mandatory initial stop, and optional take-profit or trailing-stop policy.
- Strategies may include trend following, momentum, breakout, mean reversion, and other tested approaches. These are **plugin categories**, not proof that any one is profitable.
- The same strategy logic and signal schema must run in backtest, paper, shadow, and live. Only the execution adapter changes. No look-ahead data, future-bar fills, or model-chosen live order fields.
- An EXIT or REDUCE signal routes to position management and cannot accidentally open a short position.

### D2. Autonomous execution after deliberate activation

- The owner approves strategy versions, broker account, symbols, risk limits, and an activation request. This is a **one-time or configuration-change approval**, not per-trade approval.
- After activation, a supervised service/scheduler may generate, evaluate, and execute signals throughout the approved market window without a human present.
- Activation expires or is suspended under configured conditions (new version or policy, broker credential change, reconciliation failure, emergency halt, or other critical safety event). Deployment does not auto-enable live trading.
- `paper` remains the default; live requires `config/live_execution.yaml: enabled: true`, a separate live credential identity, signed/recorded activation, and successful automated readiness checks.

### D3. Mandatory broker-managed stop for every entry

- A live **ENTRY without a valid, broker-supported protective stop is rejected before routing**. Stop method is selected by the strategy, validated and normalized by the risk engine, and placed as an attached broker-native order whenever supported.
- Initial reference implementation: an **OTO entry + stop-loss** for stop-only trades, or a **bracket entry + stop-loss + take-profit** when a take-profit is specified. Use the exact broker capabilities verified at integration time. Do not place an unprotected plain buy followed by a hopeful separate stop submission.
- Prefer **stop-market** over stop-limit for the initial hard-stop policy, subject to tested broker support. Stop-limits can remain unfilled after a gap. Stop-market orders may execute substantially worse than the stop trigger; no order guarantees a loss cap.
- Treat **an entry fill without confirmed protective coverage of the filled quantity** as a critical incident: block additional entries, alert immediately, attempt a deterministic emergency flatten/corrective action under preapproved limits, and reconcile the broker before resuming. If correction fails, keep the book paused and escalate to the operator. Never assert that a linked stop is active merely because the entry request was accepted.
- Broker-managed conditional exits can be inactive until the parent order completely fills. **Partial-fill behavior must be tested explicitly**. Until partial-fill protection is proven for the chosen order type, the live pilot permits only **one whole share per entry**; scaling to multi-share orders is blocked.
- The stop is required to remain broker-resident and valid across application restarts. Validate whether orders are day/GTC, whether they persist across sessions, and how dividends, splits, halts, and extended-hours gaps affect them. Initial scope is regular-session entries only.
- A trailing stop may **tighten but never loosen** the current hard stop on a long position. Updating a stop is an atomic, verified replacement where possible; do not cancel the existing protection first if doing so creates an unprotected interval. If the broker cannot safely replace, reject that trailing feature for live use.
- All manual/strategy exits must account for existing protective legs so they cannot oversell or leave orphan stops; reconcile completion before removing protection.

**Illustrative strategy-specific exit policies (not approved trading rules):**

| Strategy family | Example hard stop | Optional automated exit |
| --- | --- | --- |
| Breakout | Below validated breakout/invalidation level | Profit target, time-based exit, or breakout failure |
| Trend following | Volatility/ATR-based initial stop | Tightening trailing stop when broker mechanism permits it |
| Mean reversion | Below thesis-invalidation support level | Exit near estimated mean or if reversion fails |
| Momentum | Volatility-adjusted initial stop | Loss of momentum or tightened trailing stop |

Each strategy must produce an explicit numerical stop level **before entry**. Stop calculation, trailing logic, and exit parameters are versioned and backtested. The portfolio risk engine may tighten, resize, or reject the proposal; it may not silently omit the hard stop.

### D4. Central risk sizing and capital allocation

The risk engine calculates the **final allowed quantity**; strategies cannot bypass it. For a long entry, at minimum:

`planned_stop_risk = shares * max(entry_limit - stop_trigger, 0)`

The engine considers expected slippage, spread, volatility, fees, stale data, broker buying power, unsettled funds, existing exposure, pending entries, and correlated holdings. It must reject invalid or too-tight stops and entries that exceed **any** global or strategy-specific limit. Actual losses can exceed planned stop risk after gaps or poor fills.

**Illustrative conservative pilot limits, subject to owner confirmation:**

| Limit | Proposed starting value |
| --- | --- |
| New entry quantity | 1 whole share per order |
| Maximum order notional | $250 |
| Target planned risk per trade | At most 0.25% of account equity |
| Maximum planned open risk | At most 1% of account equity across strategies |
| Daily realized + marked-to-market loss halt | 1% of start-of-day equity |
| Concurrent live positions | 3 |
| Per-symbol ownership | Only one active strategy may own a symbol |
| Universe and market hours | Explicit US-listed equity allowlist; regular session |
| Leverage / short sales / options / crypto | Disabled |

Hitting a global loss limit stops **new entries** and discretionary risk increases; existing broker-managed protective exits stay intact. A manual emergency flatten capability is available, and any automatic emergency flatten must have its own explicit authorization and tests. Risk changes require owner approval and a new activation version.

### D5. Central arbitration prevents strategy conflicts

- Strategies cannot submit directly to the broker. A portfolio allocator resolves simultaneous signals, risk budgets, cooldowns, and duplicate trades.
- V1 permits **one strategy-owned position per symbol per account**. If Strategy A holds AAPL, Strategy B cannot independently buy or sell AAPL until A's position is closed and reconciled.
- A global single-writer order coordinator enforces account-level quantity, open-order, and buying-power checks. Strategies cannot race to spend the same cash or sell the same shares.
- Netting and shared-symbol allocations are future work and require an explicit allocation-ledger design and new acceptance tests.

### D6. Persistent order state, idempotency, and reconciliation

- Generate a deterministic `client_order_id` linked to `strategy_id`, `signal_id`, and position intent; persist the intent **before** any outbound request.
- Implement explicit order states such as `CREATED`, `RISK_APPROVED`, `SUBMITTING`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `PROTECTION_PENDING`, `PROTECTED`, `EXITING`, `CLOSED`, `REJECTED`, and `UNKNOWN_REQUIRES_RECONCILIATION`.
- Broker responses, fills, child stop IDs, fees, account cash, position quantity, and broker event timestamps are recorded from verified API payloads, **never from model-written summaries**.
- A network timeout **does not** authorize resubmission. Query broker order ID/client order ID and reconcile open orders and fills before any retry; no blind duplicate buys or sells.
- On startup, reconnect, market open, and at a periodic interval: reconcile broker positions, open orders, protective legs, cash/buying power, and local strategy ownership. Unexplained mismatches block new entries and trigger alerts.
- Reuse the existing lot accounting, cash ledger, daily-loss, and drawdown logic only after validating it against live settlement and broker data. Create an isolated `LIVE_ALPACA` book and append-only live event log. Change `real_orders` triggers only in a separately reviewed migration; keep UPDATE/DELETE prohibited for audit events.

### D7. Credential isolation, kill switch, and observability

- Store keys in the execution runtime's secret store, not in source, notebooks, Claude Code prompts, logs, or the research service. Paper and live credentials and API endpoints must be impossible to confuse silently.
- The execution service must be the **only** component with order permissions; use least-privilege deployment and restricted network/process access where possible.
- An operator kill switch immediately prevents new entries and risk-increasing replacements; **it must not cancel protective stops**. A separate, deliberate command handles emergency liquidation and its consequences.
- Alert on rejected orders, unprotected fills, stale/missing market data, disconnected event streams, position/stop mismatches, repeated retries, risk limits, and suspicious trading activity.
- Provide a dashboard and daily summary by strategy: live/paper mode, enabled state, capital, P/L, positions, broker order IDs, stop values, realized/unrealized risk, slippage, failures, and audit history. Alerting is not a substitute for broker-resident protection.

### D8. Initial scope and out-of-scope items

**V1 scope:** US-listed long-only equities, whole-share entries, regular-session execution, strategy-driven buys/sells, broker-native hard stops, optional take-profit, paper/live modes, deterministic risk management, and audit/reconciliation.

**Not in V1:** Shorting, options, crypto, margin, fractional trades, autonomous AI-generated orders, unofficial brokerage interfaces, unsupported trailing-stop combinations, guaranteed stop execution price, or unsupported unattended Robinhood MCP order routing.

## 4A. Review amendments (2026-11-11)

Statements about Alpaca behaviour in this section are assumptions to be confirmed in Phase 1 of the plan. If Phase 1 contradicts one, the affected amendment is revised before any later phase starts.

### A1. The paper path gains an autonomous mode (amends Section 2)

Phase 7 runs the full strategy -> risk -> order -> broker path against an Alpaca paper account without an operator present. That is not possible today:

- the paper runtime accepts only `LIMIT` orders with `DAY` time in force (`paper_runtime/src/trading_paper_runtime/models.py`, `dispatcher.py`);
- every external paper order needs an operator preview and an operator submit (`paper_books/external_broker.py`);
- the recurring scheduler never submits or cancels an external order (ADR 0007).

Decision: the existing manual external-paper flow and its commands stay exactly as they are. Autonomous submission is added as a **separate entry point** that only the new execution runtime (A2) can serve, in `paper` mode first. "Preserve the current paper path as paper-only" in Section 2 means the existing code path never reaches a live endpoint; it does not mean paper stays manual. ADR 0007's scheduler statement and stage 6 of `docs/paper-testing-plan.md` ("No automation submits") are amended to exclude this new entry point.

### A2. Broker code lives in a new, separate execution runtime (completes Section 3)

ADR 0002 confines broker calls to the isolated `paper_runtime` process, which uses LumiBot and a request/response JSONL protocol.

Decision (proposed default, confirmed or changed in Phase 1):

- A new isolated distribution holds the `ExecutionBroker` Alpaca adapter and uses the official Alpaca SDK directly, so parent and child order IDs and leg states are read without an intermediate framework.
- One deployment of that runtime is pinned to exactly one mode, `paper` or `live`, with its own credential identity and a hard-coded endpoint per mode. A mode/endpoint/credential mismatch refuses to start.
- `paper_runtime` is not extended. It keeps refusing any endpoint other than the Alpaca paper URL (`configuration.py`).
- The main process still holds no broker credential and talks to the runtime over a versioned protocol, as ADR 0002 requires.
- Broker state is read by **polling** (orders, positions, account) as the source of truth for reconciliation. An order-event stream may be added as a latency optimisation; nothing may depend on it for correctness.

### A3. Time in force for entries and stops (completes D3)

Positions are held overnight (strategy holding periods are 10–20 sessions), so the stop must outlive the session. Assumption: an Alpaca bracket or OTO order has one time in force for all legs.

Decision: attached orders are submitted `GTC` so the stop persists. Because the entry is then also `GTC`, the order manager cancels any entry not filled by the end of the signal's validity window (default: the session after the signal), which also cancels its legs. A stop approaching the broker's GTC age limit is replaced before it lapses. If Phase 1 shows a `DAY` entry can carry a `GTC` stop, that is preferred and this amendment is simplified.

### A4. Strategy exits when a stop is resting (replaces the last two bullets of D3 for signal exits)

Assumption: shares covered by a resting stop cannot be sold by a second order. A signal-driven or time-based exit therefore cannot leave the stop in place.

Decision, in order of preference:

1. Replace the resting stop leg in place with the exit order, if the broker supports that as one request.
2. Otherwise: cancel the stop, confirm the cancellation, submit the exit immediately, and confirm the fill. If the exit is rejected or not filled within a short configured window, re-place the stop at its previous level and raise a critical alert.

Option 2 has a brief unprotected interval. It is accepted only for exits (which reduce risk) and never for trailing-stop updates, where D3's "tighten by verified replacement only" rule stands.

### A5. Two distinct sell authorities (reconciles D3, D4, D7 and O8)

- **Corrective exit (automatic).** Triggered only by a filled entry quantity without confirmed stop coverage. It may sell at most the uncovered quantity, only in a symbol the live book owns, only during the regular session, once per incident. It then pauses new entries until reconciliation passes. This is the "deterministic corrective exit" of D3 and O8 and needs no further authorization beyond this ADR.
- **Emergency flatten (operator only).** Closing covered positions or the whole book is always a deliberate operator command. No automated path may invoke it. The sentence in D4 allowing an "automatic emergency flatten" under separate authorization is withdrawn for V1.

### A6. No existing strategy is vetted (amends D1 and O6)

`config/strategies.yaml` is at `STAGE_2_HISTORICAL_BACKTEST`. The real-data backtest run and the go/no-go note have not happened, and forward stages 3–5 have not been operated. `momentum_breakout` and `mean_reversion` are candidates. `event_catalyst` has no live event source and is out of V1.

Decision:

- Engineering phases use these strategies as test subjects only. Nothing in this ADR treats them as validated.
- Numeric go-live gates (O12) are written down before the autonomous paper soak starts and are not changed during it.
- Engineering completion and strategy validation are judged separately; see the plan's definition of completion.

### A7. Reuse the existing strategy contract; exit ownership (amends D1)

`strategies/contracts.py::StrategySignal` already carries strategy ID and version, configuration hash, limit, stop, target, invalidation level and holding period. `paper_books/strategy_bridge.py`, `exit_policy.py` and `lifecycle_state.py` already apply a signal's own stop, target and holding period, and advance a trailing stop. No parallel `TradeSignal`, `StopPolicy` or `ExitPolicy` type is introduced; the existing types are extended.

Decision for V1 (O13): a strategy fixes its exit levels at entry. Exits are the broker stop, an optional broker take-profit, the holding-period limit, and safety exits, all applied by the lifecycle code. Strategy-emitted `EXIT` and `REDUCE` signals are deferred until the A4 sequence has passed broker tests.

For a live entry, `initial_stop_reference` is mandatory; the field is optional in the contract today.

### A8. Signal cadence (completes D2)

The strategies run on daily bars: a signal is produced after a session closes and the entry is placed for the next regular session. V1 keeps that cadence. "Throughout the approved market window" in D2 means order management and reconciliation run during the session; it does not introduce intraday signals.

### A9. Broker-side safeguards, account rules and undefined terms (completes D4 and D7)

- The broker account itself is configured to refuse short sales and to use no margin leverage, so invariant "no accidental short" does not rest on application code alone.
- A stop that fires on the entry day is a day trade. Phase 1 records the day-trade and settlement rules that apply to the chosen account type and size, and whether that account type accepts attached orders.
- **Activation record:** an append-only database event holding the operator name, the ADR version, the strategy versions and configuration hashes, the risk-limit hash and the account fingerprint, following the existing readiness-review -> request -> activation pattern. "Signed" in D2 and O10 means this record; no cryptographic signature is implied.
- **Secret store:** the host operating system's credential store, read only by the execution runtime. Never `.env` in the repository root.
- **Host (O14):** the service needs an always-on machine. The repository's only deployment asset is `deploy/launchd/`, and a laptop that sleeps does not qualify.
- **Pilot limits (O15):** D4's percentages need a stated account size. With one share per order, a 0.25% risk budget is a stop distance in dollars; if that is tighter than the strategies' ATR stops, almost every entry is rejected.

## 5. Migration of existing ADRs and controls

- **ADR 0001:** Preserve paper-path fail-closed checks. Amend Decision 4 only to acknowledge a **separate approved live runtime**, and only when that runtime passes all release gates.
- **ADR 0002:** Preserve credential isolation; apply it to official API keys and distinct live/paper environments.
- **ADR 0003:** Preserve the prohibition on Claude holding execution authority; replace per-order MCP relay with deterministic execution service.
- **ADR 0007:** Replace the blanket statement that live execution is structurally unavailable with a reference to the newly approved live path after activation.
- **Repository docs/config:** Update `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/INDEX.md`, `.env.example`, live configuration, broker inventory, test policy, and runbooks as appropriate. Do not weaken existing paper-only tests until the matching live capability is independently implemented and tested.

## 6. Owner decisions required before activation

| ID | Decision | Proposed default |
| --- | --- | --- |
| O1 | Broker for unattended execution | Alpaca official Trading API; Robinhood not live-enabled pending authorization/verification |
| O2 | Account | Small dedicated funded account, preferably cash if broker supports the intended order workflow |
| O3 | Automated execution | Yes, only for specifically approved strategy versions and limits |
| O4 | Stop-loss | Broker-native stop attached to every entry; reject entries when unavailable |
| O5 | Initial order/portfolio limits | The conservative pilot values in D4 |
| O6 | Strategy go-live evidence | Historical backtest plus out-of-sample tests, paper soak, and supervised live pilot |
| O7 | Stop method | Prefer stop-market; initial regular-session long-only; explicitly accept gap/slippage risk |
| O8 | Handling uncovered fills | Block entries, alert, deterministic corrective exit under preapproved policy, otherwise operator escalation |
| O9 | Shared-symbol behavior | Only one strategy owns a symbol until a more advanced allocator is accepted |
| O10 | Live activation | Explicit human acceptance of this ADR and signed readiness review; no automatic activation from deployment |
| O11 | Runtime placement (A2) | New isolated execution runtime on the official Alpaca SDK; `paper_runtime` unchanged |
| O12 | Go-live gates (A6) | Numeric performance and operational gates fixed in writing before the autonomous paper soak starts |
| O13 | Exit ownership in V1 (A7) | Exit levels fixed at entry and applied by the lifecycle code; strategy `EXIT`/`REDUCE` signals deferred |
| O14 | Host (A9) | An always-on machine, named before Phase 6 |
| O15 | Account size and pilot limits (A9) | Owner states the account size; D4 limits are checked against the strategies' stop distances |
| O16 | Autonomous paper submission (A1) | Allowed only through the new execution runtime; the manual external-paper flow is unchanged |

## 7. Non-negotiable release invariants

1. No live order can be sent from a paper code path, AI research tool, or Claude Code relay.
2. No strategy can create a live entry without a valid stop and passing central risk checks.
3. No multi-share live entry until fill/stop quantity handling, including partial fills, passes broker-integration tests.
4. No duplicate order after an unknown response; reconcile first.
5. A restart, network outage, or failed stream cannot silently rearm a paused system or orphan local stop state.
6. A global kill switch blocks new risk without canceling protection on existing positions.
7. Broker positions, fills, and protective orders are the source of truth for reconciliation; local strategy ownership is consistently attributed.
8. Strategy, broker, config, or risk-policy changes require new tests and owner activation.
9. A broker without documented, verified automated protective exits cannot be used for unattended live entries.
10. Live trading remains **disabled by default** until all gates pass and the owner approves it.

## 8. Consequences and limitations

Automation can place incorrect trades at machine speed and lose real money. Stop orders may fail to trigger or execute at an intended price because of gaps, halts, liquidity, market sessions, order restrictions, outages, or broker errors. Broker-native bracket/OCO groups have timing and partial-fill behavior that must be tested; they are not atomic guarantees against every adverse event. Even with limit checks, realized losses may exceed intended risk. The operator remains responsible for account rules, settlement, day trading restrictions, wash sales, and tax consequences.

## 9. External references checked 2026-11-11

- Alpaca Trading API overview: https://docs.alpaca.markets/us/docs/trading-api
- Alpaca OTO, bracket, stop-loss, and related order semantics: https://docs.alpaca.markets/us/docs/orders-at-alpaca
- Alpaca order placement, client order IDs, and trade updates: https://docs.alpaca.markets/us/docs/working-with-orders
- Robinhood API restrictions: https://robinhood.com/us/en/support/articles/third-party-connections/

## 10. Implementation roadmap

See [`../automated-live-trading-implementation-plan.md`](../automated-live-trading-implementation-plan.md). It lists only the work that remains after what the repository already contains.

