# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/score-alpaca-data`
- Reviewed HEAD: `8cf064f6ce1fd4ada96b418b3353ad70edb9cf2a`
- Subject: Add Alpaca and Robinhood data modes to score.py
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7,0b570bf65a68738db7c48631068de82651bd1ac4,0e6f286d3911366bead442cb36be07607dc68282,f8b8086c6917a48bb9971fb5cb098a0cbfbdee1c,61668730af922794d826f92cc6af09e8f68b9089,113a7da9fb9d8604b8b6ee7794dbc7baee8977e0,4a121cd0dd1847f6668ade0ade0e35e867a3badd,b293223505f98c8ca0a4c3cf610290805930c23f,792f181ad0f31812e6e0cc956cbf6a0baacb53bd,b7543242278c0af0f4c8a84ccbce89f5d09b6cb0,6981f6b497f937ec4da3ecb536774a6ad594c964,547bd9b0cd0fa8ff942e8d9207f6f557e930b369,a4287a5ce0655fb910fe8e5c3997dca804a7f7db,ed65694edef1014e85f0345ae18b87b5582fb6ee,dde61fd3a68178ca70b93540ba07dd1537494594,eab3f7807b3155058d155af19b37e8ff06904e79,8cf064f6ce1fd4ada96b418b3353ad70edb9cf2a
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 2185e28cc21384504e432f93df713ab3584d8438

## Findings

### [P1] Investigate: REST capability mode can submit paper orders without confirmation and leave unexpected exposure

Commit: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7

Location: spikes/alpaca_phase1/capability_spike.py:208-275, 475-486

Concern: Investigate whether `rest` mode violates the repository’s explicit paper-order confirmation boundary and can leave an unexpected position behind.

Evidence: `stage_rest` submits, replaces, and cancels multiple Alpaca paper orders, including a sell intended to test shorting. The `--i-understand-this-trades-paper` gate is enforced only for `fill`; `rest` executes with only an environment file. Its limits are described as unable to fill because they are 20–25% from the quote, but that is not guaranteed during a gap or extreme move. Cleanup calls `settle_to_flat` with `Recorder.owns_symbol == False`, so it cancels open orders but intentionally does not flatten any position produced by an unexpected fill. Existing tests verify stuck-order cleanup but do not simulate a filled REST order or require the confirmation flag for REST mode.

Potential impact if confirmed: An operator could unintentionally submit external paper orders without acknowledging that the command trades, and a fill could leave a long or short SPY position requiring manual reconciliation.

Investigation and conditional remediation: Verify the concern against current broker-call paths and confirmation requirements first. If confirmed, require the explicit paper-trading confirmation for every mutating mode and establish known-flat ownership/reconciliation before REST submissions, or make REST incapable of submitting orders. Add regression coverage for invocation without confirmation and for an order filling before cleanup. If disproved or already fixed elsewhere, document that evidence and leave the code unchanged.

Validation: Exercise the fake broker with a REST order that fills immediately and confirm whether the command exits with non-zero status and restores a known-flat account without touching pre-existing exposure.

### [P2] Investigate: Allocation-based rejections may be permanently persisted from stale book state

Commit: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7

Location: src/trading_research/paper_books/strategy_bridge.py:398-421

Concern: Investigate whether the allocation checks introduced here can persist a final rejection after the book state used to calculate it has changed concurrently.

Evidence: Allocation now reads symbol ownership, pending/open risk, and committed notional from book state. Approved decisions acquire the write lock and compare `_risk_state_fingerprint` before persistence, but rejected decisions are persisted immediately inside a transaction without repeating `already_processed()` or comparing the fingerprint. Once stored, `already_processed()` treats that signal as final, so a rejection based on exposure that closed or was canceled between evaluation and persistence cannot be recomputed. Tests cover rejection persistence and approved registration rollback, but no rejected-decision interleaving.

Potential impact if confirmed: A valid signal can be permanently suppressed by a stale ownership, capital-budget, or open-risk rejection, producing incorrect and non-retryable paper-trading results.

Investigation and conditional remediation: Verify the concern against the current transaction and SQLite concurrency behavior first using two connections and a controlled state change between risk evaluation and rejection persistence. If confirmed, subject rejected decisions to the same locked fingerprint and duplicate checks as approvals, retrying on stale state, and add regression coverage. If disproved or already fixed, record the concurrency evidence and leave the code unchanged.

Validation: Pause execution after `evaluate_paper_risk` returns an allocation rejection, remove the competing exposure through a second connection, resume persistence, and verify that the stale rejection is not committed and the retry evaluates the new state.

Tests or diagnostics run: `nox -s tests -- tests/tools/test_alpaca_capability_spike.py tests/unit/test_strategy_controls.py tests/unit/test_allocation.py tests/unit/test_score_alpaca.py tests/unit/test_robinhood_history.py tests/unit/test_robinhood_holdings.py tests/unit/test_oauth_readonly_client.py tests/dashboard/test_scoring_service.py` was attempted. It could not create `.nox/tests` because the read-only environment exposed no writable temporary directory; no tests executed. Static commit/diff inspection, current-code tracing, blame attribution, and existing regression-test inspection were completed.
