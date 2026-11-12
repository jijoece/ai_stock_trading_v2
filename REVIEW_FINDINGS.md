# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/automated-live-trading-phases-0-3-spike`
- Reviewed HEAD: `6981f6b497f937ec4da3ecb536774a6ad594c964`
- Subject: Record review fix applied for tick-size drift finding (pending re-review)
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7,0b570bf65a68738db7c48631068de82651bd1ac4,0e6f286d3911366bead442cb36be07607dc68282,f8b8086c6917a48bb9971fb5cb098a0cbfbdee1c,61668730af922794d826f92cc6af09e8f68b9089,113a7da9fb9d8604b8b6ee7794dbc7baee8977e0,4a121cd0dd1847f6668ade0ade0e35e867a3badd,b293223505f98c8ca0a4c3cf610290805930c23f,792f181ad0f31812e6e0cc956cbf6a0baacb53bd,b7543242278c0af0f4c8a84ccbce89f5d09b6cb0,6981f6b497f937ec4da3ecb536774a6ad594c964
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: 547bd9b0cd0fa8ff942e8d9207f6f557e930b369
- Highest priority: P2
- Finding count: 0

## Findings

### [P2] Investigate: legacy exposure remains vulnerable to tick-size re-risking

Commit: b7543242278c0af0f4c8a84ccbce89f5d09b6cb0

Location: src/trading_research/paper_books/strategy_exposure.py:55-66

Concern: Exposure created before `allocation_stop` was introduced may still have its planned risk reduced by a later, smaller tick size.

Evidence: The schema upgrade adds the nullable column but does not backfill existing strategy links. `_risked_stop` handles those legacy `NULL` values by normalizing the raw stop with the current tick size. Therefore, unlike new rows, legacy exposure is not frozen to its approval-time risk. The regression test at `tests/unit/test_strategy_controls.py:494-506` confirms only that a `NULL` row uses the current tick; it does not change that tick afterward. For 1,000 shares at entry 100 with raw stop 99.999, the fallback reports $10 risk with tick 0.01 but only $1 with tick 0.001.

Potential impact if confirmed: After upgrading a database containing open positions or pending entries, a subsequent tick-size reduction could understate book and per-strategy planned risk. Later entries could then exceed the limits under which the existing exposure was approved, and historical risk reconstruction would remain configuration-dependent.

Investigation and conditional remediation: Verify the concern against the current code first using a database containing a pre-upgrade strategy link with `allocation_stop IS NULL`; calculate exposure at tick 0.01 and then at 0.001. Only if planned risk decreases should the fixer introduce a fail-closed or deterministic migration strategy—for example, backfill the approval stop from trusted approval-time configuration, or prohibit allocation-setting changes while unresolved legacy exposure exists—and add regression coverage for the actual upgrade path. If the concern is disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Upgrade a fixture containing legacy pending and filled exposure, then prove that changing the active tick from 0.01 to 0.001 cannot reduce either book-level or strategy-level planned risk.

Resolution (fix commit 547bd9b0): Confirmed. After the upgrade, legacy links have `allocation_stop IS NULL`, and no approval-time tick is persisted anywhere to backfill from, so the fallback re-rounded at the current tick and a smaller tick cut planned risk. Rows without `allocation_stop` are now risked at their whole notional (the same treatment as a lot with no stop on record), so legacy book and strategy risk no longer depends on `allocation.tick_size`; ownership is unchanged and risk frees up only when the exposure closes. `read_book_exposure` no longer takes a tick size. The regression test `test_legacy_exposure_without_an_approval_stop_is_counted_as_fully_at_risk` builds a filled lot and a pending entry under tick 0.01, runs the schema upgrade path, and asserts book and strategy risk equal the full notional (at least the approved risk). `nox -s ci` passed.

Tests or diagnostics run:

- Inspected all 11 specified commit diffs chronologically and traced their changes through HEAD `6981f6b497f937ec4da3ecb536774a6ad594c964`.
- `git diff --check 8c53e26035f5e1228861f9798ff8104e2b322a0d..6981f6b497f937ec4da3ecb536774a6ad594c964` — reported only a blank line at EOF in the proposed ADR; excluded as non-consequential.
- Pure Decimal diagnostic reproduced planned-risk reduction from `$10.00` at tick `0.01` to `$1.000` at tick `0.001`.
- `nox -s tests -- tests/unit/test_allocation.py tests/unit/test_strategy_controls.py -q` — not run because `nox` is unavailable.
- No files or external systems were modified or accessed.
