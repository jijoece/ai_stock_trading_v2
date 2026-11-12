# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/automated-live-trading-phases-0-3-spike`
- Reviewed HEAD: `792f181ad0f31812e6e0cc956cbf6a0baacb53bd`
- Subject: Use tick-rounded stops when reading existing book exposure
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7,0b570bf65a68738db7c48631068de82651bd1ac4,0e6f286d3911366bead442cb36be07607dc68282,f8b8086c6917a48bb9971fb5cb098a0cbfbdee1c,61668730af922794d826f92cc6af09e8f68b9089,113a7da9fb9d8604b8b6ee7794dbc7baee8977e0,4a121cd0dd1847f6668ade0ade0e35e867a3badd,b293223505f98c8ca0a4c3cf610290805930c23f,792f181ad0f31812e6e0cc956cbf6a0baacb53bd
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: b7543242278c0af0f4c8a84ccbce89f5d09b6cb0
- Highest priority: P2
- Finding count: 0

## Findings

### [P2] Investigate: existing exposure can be re-risked under a newer, smaller tick size

Commit: 792f181ad0f31812e6e0cc956cbf6a0baacb53bd

Location: src/trading_research/paper_books/strategy_exposure.py:55,79,97-100

Concern: Existing positions and pending orders appear to have their raw strategy stops normalized using the current cycle’s tick size, rather than the tick size used when each exposure was approved.

Evidence: `paper_book_strategy_links` persists only `strategy_stop`. `read_book_exposure` accepts the current `cfg.risk.allocation.tick_size` and recalculates the effective stop for all historical exposure. There is no guard preventing `paper_books.yaml` from changing while a book remains open. For 1,000 shares entered at 100 with a raw stop of 99.999, approval under a 0.01 tick records $10 planned risk, but a later cycle using a 0.001 tick recomputes only $1. The new regression test covers a stable tick size, not configuration drift.

Potential impact if confirmed: A tick-size reduction can understate existing planned open risk and strategy risk, allowing subsequent entries to exceed the limits under which the outstanding exposure was approved. This also makes historical risk reconstruction configuration-dependent.

Investigation and conditional remediation: First verify against the current code that an open lot or pending intent approved with one tick size can subsequently be evaluated under a smaller tick size without a fail-closed configuration-drift gate. If confirmed, persist the normalized effective stop or approval tick size with the strategy exposure and use that immutable value for later risk calculations, or reject cycles when outstanding exposure was created under incompatible allocation settings. Add regression coverage that creates exposure under 0.01, reloads under 0.001, and proves planned risk cannot decrease. If configuration drift is already prohibited elsewhere, document that evidence and leave the code unchanged.

Validation: Reproduce with a raw stop not aligned to either tick, such as 99.999 at entry 100, and assert that changing the current tick from 0.01 to 0.001 does not reduce existing planned risk from $10 to $1 for 1,000 shares.

Resolution (fix commit b7543242): Confirmed. Nothing prevented `allocation.tick_size` from changing while exposure was open. The tick-normalized stop is now persisted as `paper_book_strategy_links.allocation_stop` at approval and `read_book_exposure` uses it, so planned risk cannot shrink when the tick does. Older databases get the column through an idempotent `ALTER TABLE`; rows without it fall back to the current tick. The raw `strategy_stop` is unchanged. Regression tests in `tests/unit/test_strategy_controls.py` cover 0.01 -> 0.001 drift, the NULL fallback and the migration. `nox -s ci` passed.

Tests or diagnostics run:

- `nox -s tests -- tests/unit/test_allocation.py tests/unit/test_strategy_controls.py` — not run because `nox` is unavailable.
- `.venv/bin/python -m pytest -q tests/unit/test_allocation.py tests/unit/test_strategy_controls.py` — blocked before collection because the read-only environment provides no writable temporary directory.
- Pure normalization diagnostic — confirmed planned risk changes from `$10.00` at tick `0.01` to `$1.000` at tick `0.001`.
- `git diff --check 8c53e260…792f181a` — reported only a blank line at EOF in the ADR, not a review finding.
