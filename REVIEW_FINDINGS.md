# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/automated-live-trading-phases-0-3-spike`
- Reviewed HEAD: `a4287a5ce0655fb910fe8e5c3997dca804a7f7db`
- Subject: Record review fix applied for legacy exposure tick-size finding (pending re-review)
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7,0b570bf65a68738db7c48631068de82651bd1ac4,0e6f286d3911366bead442cb36be07607dc68282,f8b8086c6917a48bb9971fb5cb098a0cbfbdee1c,61668730af922794d826f92cc6af09e8f68b9089,113a7da9fb9d8604b8b6ee7794dbc7baee8977e0,4a121cd0dd1847f6668ade0ade0e35e867a3badd,b293223505f98c8ca0a4c3cf610290805930c23f,792f181ad0f31812e6e0cc956cbf6a0baacb53bd,b7543242278c0af0f4c8a84ccbce89f5d09b6cb0,6981f6b497f937ec4da3ecb536774a6ad594c964,547bd9b0cd0fa8ff942e8d9207f6f557e930b369,a4287a5ce0655fb910fe8e5c3997dca804a7f7db
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: ed65694edef1014e85f0345ae18b87b5582fb6ee
- Highest priority: P2
- Finding count: 0

## Findings

### [P2] Investigate: unconfirmed fills can leave an unintended long or open a short

Commit: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7

Location: spikes/alpaca_phase1/capability_spike.py:345-357,382-386

Concern: The credentialed paper-broker spike may submit a flattening sell without confirming that the bracket entry filled, while its exception cleanup cancels orders but does not flatten an existing position.

Evidence: The bracket path ignores the status returned by `_wait`, cancels prefixed orders, and then unconditionally submits a one-share market sell. If the entry remains unfilled or times out, cancellation followed by that sell can open a short; the captured capability evidence establishes that this account permits shorting. Conversely, an exception after an entry fill reaches `finally`, which only calls `cancel_prefixed`, potentially removing protective orders while leaving the long position open. This contradicts the script’s stated one-share/cleanup safety boundary.

Potential impact if confirmed: An explicitly authorized `fill` run could finish with unintended paper exposure—either an unprotected long or a short—requiring manual reconciliation and invalidating the capability test’s safety assumptions.

Investigation and conditional remediation: Verify the concern against the current code first using a fake broker that exercises an entry timeout, an exception after entry fill, and an unsuccessful flatten. Only if confirmed should the fixer require authoritative position/fill confirmation before choosing the flatten side and quantity, make cleanup reconcile to a known-flat state or fail prominently with the remaining exposure, and add regression coverage for every interrupted stage. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: With a fake broker, prove that an unfilled or cancelled entry never produces a sell, that an exception after a filled entry invokes position-aware flattening, and that the command cannot report successful cleanup while SPY exposure or related open orders remain.

Resolution (fix commit ed65694e): Confirmed. The bracket stage discarded `_wait`'s status and then sent an unconditional one-share sell, so an unfilled entry could open a short; the `finally` only cancelled orders, so an exception after a fill left the long open. Cleanup is now `settle_to_flat`: cancel this run's open orders, wait until none remain, read the broker's SPY position and flatten exactly that signed quantity (nothing when flat). It flattens only after the `fill` preflight verified the account held no SPY position or order (`Recorder.owns_symbol`), so a refused run cannot touch a pre-existing position. Leftover exposure or open orders raise `ExposureRemaining` mid-run and, at exit, are printed as `EXPOSURE REMAINS` with a non-zero exit code. The mid-run exit sell is also skipped when no long position remains. Regression tests in `tests/tools/test_alpaca_capability_spike.py` use a fake broker (no network or alpaca-py) for an unfilled entry, an exception after a fill, a fill that races the cancel, a flatten that never fills, a refused run, and an uncancellable order; five of the seven fail against the previous code. `nox -s ci` passed (tests, paper_tests, safety_typecheck, migration_smoke).

Tests or diagnostics run:

- Inspected all 13 specified commit diffs chronologically and traced their changes through HEAD `a4287a5ce0655fb910fe8e5c3997dca804a7f7db`.
- Reviewed relevant current code, tests, configuration, persistence schema and migrations, proposed ADR, implementation plan, capability fixtures, and runbook.
- AST parsing passed for all 14 changed Python files.
- JSON parsing passed for all 43 Alpaca capability fixtures.
- `git diff --check 8c53e26035f5e1228861f9798ff8104e2b322a0d..a4287a5ce0655fb910fe8e5c3997dca804a7f7db` reported only a blank line at the end of the proposed ADR; excluded as non-consequential.
- Nox/pytest were not run because the read-only environment cannot create required temporary files.
- No files, brokers, credentials, models, schedulers, orders, network services, GitHub resources, or external systems were modified or accessed.
