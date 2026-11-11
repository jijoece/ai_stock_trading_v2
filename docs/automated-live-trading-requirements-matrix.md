# Automated Live Trading — Requirements-to-Tests Matrix and Doc-Edit List

**Status:** Proposed 2026-11-11, with the plan and [ADR 0010](adr/0010-automated-live-trading.md), which is
not accepted. The repository remains paper only. This is the Phase 0 deliverable in
[`automated-live-trading-implementation-plan.md`](automated-live-trading-implementation-plan.md): it maps every
ADR invariant and every Section 4 scenario to the test file that holds it, and lists the doc edits that wait for
the phase that makes each one true. **Nothing here changes behaviour.**

"Exists" means a test in the repository today covers the paper path or the local part. "Planned" names a file
that does not exist yet; the name is a proposal for the phase shown. A planned test must be written before
the code it guards is merged.

## 1. ADR 0010 release invariants

| # | Invariant | Held by | State |
|---|---|---|---|
| 1 | No live order from a paper path, AI tool or Claude Code relay | `tests/unit/test_live_gateway.py`, `tests/unit/test_paper_books_config.py` (`allow_live_broker` rejected), `tests/unit/test_alpaca_runbook_approval_gates.py`; planned `tests/unit/test_execution_runtime_mode_pinning.py` (Phase 4: paper credentials cannot reach the live endpoint, live credentials cannot start a paper runtime) | Exists for the paper path; runtime part planned, Phase 4 |
| 2 | No live entry without a valid stop and passing central risk checks | `tests/unit/test_strategy_contracts.py` (an ELIGIBLE signal needs a stop below entry), `tests/unit/test_allocation.py` (stop validation, always-on checks), `tests/unit/test_strategy_controls.py` (bridge); planned `tests/unit/test_autonomous_entry_requires_stop.py` (Phase 4/5: zero orders sent without a stop) | Local part exists (Phases 2–3); broker part planned |
| 3 | No multi-share live entry until partial-fill coverage is proven | Planned `tests/unit/test_partial_fill_coverage.py` (Phase 5), plus a config test that the live book rejects quantity above 1 | Planned, Phase 5 |
| 4 | No duplicate order after an unknown response; reconcile first | `tests/unit/test_external_paper_broker.py`, `tests/unit/test_external_submit_reservation_crash_atomicity.py`, `tests/unit/test_external_order_lease_fencing.py` (single limit orders); planned `tests/unit/test_autonomous_order_manager_recovery.py` (Phase 4: with legs) | Exists for the manual path; planned for the new path |
| 5 | A restart, outage or failed stream cannot silently rearm a paused system or orphan local stop state | `tests/unit/test_daily_risk_state.py`, `tests/unit/test_recurring_paper_scheduler.py` (pause is append-only, resume is the operator's); planned `tests/unit/test_live_restart_state.py` (Phase 6) | Exists for pauses; planned for stops |
| 6 | The kill switch blocks new risk and never cancels a protective stop | Planned `tests/unit/test_live_kill_switch.py` (Phase 5/6) | Planned |
| 7 | Broker positions, fills and stops are the reconciliation source of truth; local ownership is consistently attributed | `tests/unit/test_account_reconciliation.py`, `tests/unit/test_cross_book_verification.py`; `tests/unit/test_allocation.py` and `tests/unit/test_strategy_controls.py` (ownership from the book's own records); planned `tests/unit/test_live_reconciliation_legs.py` (Phase 6) | Local exists; broker and legs planned |
| 8 | Strategy, broker, config or risk-policy changes need new tests and owner activation | `tests/unit/test_strategy_config_activation.py` (stage ladder); planned `tests/unit/test_activation_expiry.py` (Phase 6: activation expires on any hash change) | Stage ladder exists; activation planned |
| 9 | A broker without documented, verified automated protective exits cannot be used for unattended live entries | Planned `tests/unit/test_broker_capability_gate.py` (Phase 1 matrix enforced at runtime start, Phase 4) | Planned |
| 10 | Live trading stays disabled by default until every gate passes and the owner approves | `tests/unit/test_live_gateway.py`, `tests/unit/test_strategy_controls.py` (`mode: live` is refused by the loader and by `StrategyControls`); planned `tests/unit/test_live_config_shipped_disabled.py` (Phase 6) | Exists for the loader; the rest is planned |

## 2. Section 4 acceptance scenarios (new execution path)

| Scenario | Local-path test today | New-path test (planned) |
|---|---|---|
| Two strategies, same symbol, same bar: one reservation, one entry | `tests/unit/test_strategy_controls.py::test_two_strategies_on_the_same_symbol_and_bar_get_one_entry_for_the_stronger_signal`, `::test_the_ordering_rule_follows_signal_strength_not_strategy_name`, `tests/unit/test_allocation.py` (property test) | Phase 4: `tests/unit/test_autonomous_symbol_reservation.py` |
| Entry without a stop: zero orders sent | `tests/unit/test_strategy_contracts.py`, `tests/unit/test_strategy_execution_boundary.py` | Phase 4: `tests/unit/test_autonomous_entry_requires_stop.py` |
| Stop invalid or rejected: no unprotected entry | `tests/unit/test_allocation.py`, `tests/unit/test_strategy_controls.py::test_a_stop_outside_the_distance_band_creates_no_intent` | Phase 5: `tests/unit/test_attached_stop_rejection.py` |
| One-share entry fills and the stop leg is verified at the broker | None (no broker legs exist) | Phase 5: `tests/unit/test_one_share_entry_stop_verified.py` |
| Multi-share partial fill: uncovered share detected, entries halt, corrective policy runs | None | Phase 5: `tests/unit/test_partial_fill_coverage.py` |
| Entry times out after the broker accepted it: reconciliation finds it, no duplicate | `tests/unit/test_external_paper_broker.py` (single orders) | Phase 4: `tests/unit/test_autonomous_order_manager_recovery.py` |
| Unfilled entry reaches the end of its validity window: it and its legs are cancelled | Local expiry only: `tests/unit/test_strategy_controls.py::test_an_expired_signal_cannot_produce_an_intent`, `tests/unit/test_paper_books_lifecycle.py` (pending expiry) | Phase 4: `tests/unit/test_entry_validity_cancellation.py` |
| A stop fires while the process is down: restart imports the fill, no opposite position | None | Phase 6: `tests/unit/test_live_restart_state.py` |
| An exit races a stop fill: exactly the held quantity is closed | None | Phase 5: `tests/unit/test_exit_stop_race.py` |
| An exit fails after its stop was cancelled: stop re-placed, critical alert | None | Phase 5: `tests/unit/test_exit_failure_replaces_stop.py` |
| Pause or daily-loss halt: no new entries, stops remain | `tests/unit/test_daily_risk_state.py`, `tests/unit/test_allocation.py::test_the_loss_halt_wins_over_any_allocation_headroom` | Phase 6: `tests/unit/test_live_kill_switch.py` |
| Market data, network or broker polling fails: entries stop, state reported | `tests/unit/test_strategy_controls.py::test_a_stale_reference_price_is_still_rejected` (stale price only) | Phase 6: `tests/unit/test_live_data_outage_halts_entries.py` |
| Unexplained position or manually changed stop at the broker: entries stop pending reconciliation | `tests/unit/test_account_reconciliation.py` (positions) | Phase 6: `tests/unit/test_live_reconciliation_legs.py` |
| Paper credentials cannot reach live, live cannot start paper mode, no activation while the ADR is not accepted | `tests/unit/test_strategy_controls.py::test_live_mode_cannot_be_built_even_in_code` | Phase 4: `tests/unit/test_execution_runtime_mode_pinning.py`; Phase 6: `tests/unit/test_live_activation_requires_accepted_adr.py` |

## 3. What Phases 2 and 3 added (local paper path only)

These are the parts of the plan that need no broker and no credentials. They change the local paper cycle and
nothing else.

- `strategies/controls.py`, `config/strategies.yaml: strategy_controls`: per-strategy mode, stage, symbol
  allowlist, capital budget, risk budget and signal validity. The global stage stays the ceiling. `live` is refused.
- `paper_books/strategy_bridge.py`: signals are ordered strongest first (`selector.signal_rank_key`), then gated
  by controls, expiry and chronology before risk.
- `paper_books/allocation.py`, `strategy_exposure.py`, `risk.evaluate_paper_risk(allocation=...)`: symbol
  ownership, stop validation, per-trade, per-strategy and whole-book planned-risk limits, strategy capital budget.
  Limits live in `config/paper_books.yaml: risk.allocation`.
- Tests: `tests/unit/test_allocation.py` (including property tests), `tests/unit/test_strategy_controls.py`.

Deviations from the plan, for the reviewer:

- The signal's validity window is **derived** from the stored `session_date` and the strategy's
  `signal_validity_sessions`, not stored as a new field on `StrategySignal`. That avoids a schema change and a
  change to every signal's content ID, and a replay reproduces it. Revisit if a strategy needs its own expiry.
- `initial_stop_reference` is already mandatory for every ELIGIBLE signal (`StrategySignal.__post_init__`), so
  "mandatory for a live-eligible signal" needed no change. The optional versioned trailing rule (ADR A7) is not
  added: no broker supports it yet (Phase 1) and the local lifecycle already advances trailing stops.
- Production screening (`NOT_EVALUATED` today) is **not** computed. It is an operator-side dependency in plan
  Section 2, not a local code change.
- `candidates` and `shadow` do not read the per-strategy mode yet; only the paper bridge does.
- The stale-quote rule for a live order is the existing `risk.reject_stale_market_price_seconds`. A stricter
  broker-quote age belongs to Phase 4, when a live quote exists to measure.
- Stop-inside-the-spread rejection is implemented and tested, but the paper path has no quote spread, so it is
  never exercised until the execution runtime supplies one.
- The paper default limits in `config/paper_books.yaml` (1% per trade, 10% open risk, a 0.5%–25% stop band) are a
  starting point for the operator and are **not** the ADR's live pilot limits (O15).
- Symbol ownership is derived from open lots and pending intents. "Released after the position is closed and
  reconciled" holds because the cycle refuses entries unless the book reconciles as MATCHED; there is no separate
  reservation table until the live book (Phase 6) needs one.

## 4. Exact doc edits, applied in the phase that makes each true

Nothing below is applied now. Each edit waits for the phase named, so no document claims something the code does
not yet do.

| Document | Edit | Phase |
|---|---|---|
| ADR 0001, Decision 4 ("why live execution remains disabled") | Add an amendment: the three locks stay for the paper path; a separately armed live runtime may exist once ADR 0010 is Accepted and its release gates pass. Do not edit the original text. | 8 |
| ADR 0002 | Add an amendment: credential isolation also covers official Alpaca keys and distinct paper and live runtime deployments; `paper_runtime` is unchanged and still accepts only the paper URL. | 4 |
| ADR 0003 | Add an amendment replacing "per-order MCP relay" with the deterministic execution service. The prohibition on Claude holding order authority stays. | 8 |
| ADR 0007 | Replace "live execution is structurally unavailable" with a reference to the approved live path after activation; exclude the autonomous paper entry point from "the scheduler never submits or cancels" (ADR 0010 A1). | 4 (autonomous paper), 8 (live) |
| `README.md` | Update the badge, the "Research and paper trading only" banner, the safety guarantees list (lines about `trading_mode`, `allow_live_broker`, the scheduler) and the config table. Do not weaken any guarantee that is still true. | 8 |
| `CLAUDE.md` and `AGENTS.md` | Keep "default all broker interaction to read-only" and "never submit without explicit request". Add the live-runtime rule: no credentialed run, no activation, no order from an agent session. | 8 |
| `docs/paper-testing-plan.md` | Stage 6 text "No automation submits" gains the exclusion for the autonomous paper path; record the Phase 2–3 local controls under the strategy stages. | 7 |
| `docs/INDEX.md` | Link this document next to the plan. | Now |

## 5. Owner decisions still open (Phase 0 exit)

Phase 0 is done when ADR 0010 is **Accepted** with every owner decision filled in. These are the owner's to
answer; no code can: O1–O16 in the ADR and amendments A1–A9. Until then the repository stays paper only, no
credentialed run is made and Phases 1 and 4–8 do not start.
