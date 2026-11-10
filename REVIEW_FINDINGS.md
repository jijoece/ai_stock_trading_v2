# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `4419a2daeafc5f42a54d1cbd15e70e3f261fb7a3`
- Subject: Enforce chronology, strategy invalidation and lifecycle-policy limits in the strategy paper cycle
- Claude commits reviewed: 4419a2daeafc5f42a54d1cbd15e70e3f261fb7a3
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: 669cd4f98554b1917760c153ebbdf28dced3c0b2

## Findings

### [P2] Investigate: unrelated books can incorrectly advance the strategy book’s chronology

Commit: 4419a2daeafc5f42a54d1cbd15e70e3f261fb7a3

Location: src/trading_research/forward_testing/paper_cycle.py:81-84

Concern: The new chronology guard may reject a valid BASELINE strategy session because it treats every lifecycle run in the database as work recorded for the selected book, even when that run did not process BASELINE.

Evidence: `pb_repo.list_lifecycle_runs(conn)` returns global lifecycle runs without a book filter. Each run already records `books_processed`, but `_require_chronological` ignores that field. For example, a later ENHANCED-only lifecycle run followed by a configuration change to BASELINE-only would prevent processing an earlier BASELINE session despite BASELINE having no later state. The added tests cover a later run that did process the strategy book, but not an unrelated-book run.

Potential impact if confirmed: Legitimate historical paper-cycle progression can become permanently blocked by activity belonging solely to another book, requiring database replacement or code changes even though processing the requested BASELINE session would not violate its chronology.

Investigation and conditional remediation: Verify this concern against the current code first by creating a later lifecycle run whose `books_processed` excludes the selected strategy book, then attempt an earlier strategy cycle for a book with no later orders or lifecycle work. If confirmed, derive the lifecycle timestamps only from runs that processed—or otherwise mutated—the selected book, while retaining the selected book’s order timestamps, and add regression coverage for the cross-book/config-transition case. If the concern is disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Confirm that an unrelated later lifecycle run does not block the strategy cycle, while a later run that processed the selected book and a later order belonging to that book both continue to fail closed.

Tests or diagnostics run:

- `git diff 23c8a9dfd0bfabecfbfce8f2826309587a1535f6..4419a2daeafc5f42a54d1cbd15e70e3f261fb7a3 --check` — passed.
- `nox -s tests -- tests/unit/test_strategy_paper_cycle.py tests/unit/test_paper_books_lifecycle_cli.py` — not run; `nox` was unavailable on PATH.
- `.venv/bin/python -m pytest tests/unit/test_strategy_paper_cycle.py tests/unit/test_paper_books_lifecycle_cli.py -q` — could not start because the read-only environment had no writable temporary directory.
