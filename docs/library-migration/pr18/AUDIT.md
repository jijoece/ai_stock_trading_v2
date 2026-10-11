# PR 18 — Final Authority and Safety Audit

Branch `migration/18-final-authority-safety-audit`, based on `origin/main`
`bd95d2c` (PR 17 merged). Rulings: [`DECISIONS.md`](../DECISIONS.md) D16.
This PR changes tests and documents, one line in the migration tooling,
`scripts/migration_helper.py` (D16 ruling 3a), and one source file,
`src/trading_research/logging_config.py` (D16 ruling 5): review found that
arbitrary objects in log `extra` fields bypassed redaction, and the fix
redacts their `repr()` before JSON serialization. No other code under
`src/`, and no code under `paper_runtime/src/`, `backtest_runtime/`, or
`config/`, is modified. No trading, limit, authorization, or accounting
behavior changes.

## Merge status (GitHub-authoritative, checked 2026-10-12)

| PR | GitHub PR | Merge commit |
|---|---|---|
| 0 | #2 | `795bb41` |
| 1 | #5 | `294714c` |
| 2 | #6, #7 | `456b45b`, `7d59b51` |
| 3 | #9, #10 (+ fixup #11) | `5f8ba3a`, `b60b521` (`66b25f7`) |
| 4 | #12 | `62dfcd4` |
| 5 | #13, #16 | `c3841c3`, `e07aef5` |
| Pre-step 6 | #15 | `fe14ca7` |
| 6 | #18 | `bbd7a1f` |
| 7 | #19 | `5b9e1e3` |
| 8 | #20 | `573fd3d` |
| 9 | #22 | `22f2cdc` |
| 10 | #27 | `227fbc7` |
| 11 | #28 | `611b3df` |
| 12 | #29 | `641f5da` |
| 13 | #30 | `c32021e` |
| 14 | #32 | `33dce8f` |
| 15 | #33 | `ea1168d` |
| 16 | #34 | `3906935` |
| 17 | #35 | `bd95d2c` |

Row 8a (legacy backtest follow-up) is not started. It sits outside the
numbered sequence and is not a precondition for this audit.

## `plan.md` completion criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Each generic responsibility has one authoritative library | **Met, with documented exceptions** | Calendar → `exchange_calendars`. Indicator formulas → TA-Lib, with the compatibility adapter retained on purpose. Analytics → `empyrical-reloaded`. Log rendering → `structlog`. Deferred evaluations keep the custom code as the only authority, so no dual authority exists: Riskfolio-Lib (D10), SQLAlchemy/Alembic (D11), APScheduler/Tenacity (D12). The backtest engine is preserved (D7). `backtest_runtime/` is a cross-check with no callers in `src/`. |
| 2 | Custom generic implementations deleted | **Met** | `REMOVAL_MANIFEST.md`: the PR 3, PR 4, and PR 15 rows were closed in place, and the PR 11 row was removed in PR 17. |
| 3 | No fallback to a removed engine | **Met** | `market_calendar.py` raises `MarketCalendarError` out of range and never falls back. `metrics.py` has no `math` import (`test_analytics_import_boundary.py`). There is no formatter subclass (new guard). |
| 4 | Parity passes or differences accepted | **Met** | PR 3, 4, 7, 11, 15, and 17 records. Accepted differences: flat-price RSI (PR 4) and `float` metric values (D15). |
| 5 | Safety rules enforced outside framework defaults | **Met under policy B** | D3, plus D16 ruling 4. `test_standing_paper_limits_policy.py`. |
| 6 | Offline workflows need no credentials | **Met** | `nox -s ci` runs offline. No credentialed test ran in this PR. |
| 7 | Paper execution disabled and operator-initiated | **Met** | The tracked config has external submission disabled (`test_paper_books_config.py`). Submission needs a preview, `--operator`, `--reason`, and the operator's approval phrase. |
| 8 | Live trading unavailable | **Met** | `allow_live_broker: true` fails config load. The live phrase is reserved and never implemented. |
| 9 | Documentation matches current code | **Met after this PR** | The approval-phrase row was corrected. Merge-status drift in `MASTER_PLAN.md`/`STATUS.md` was corrected. The Alpaca runbook now uses the override file instead of editing the tracked config. |
| 10 | `REMOVAL_MANIFEST.md` has no unresolved target | **Met** | Since PR 17. |
| 11 | CI prevents reintroduction | **Met after this PR** | Analytics (PR 17 AST guard). Indicators (`test_indicators.py` delegation tests). Calendar and logging formatter: `test_removed_implementation_reintroduction_guards.py`, new and checked by mutation (D16 ruling 1). |

## Findings and resolutions

1. **Stale approval-phrase documentation.** `PRESERVATION_MANIFEST.md`
   claimed a code-level phrase gate and phrase tests. Neither exists. The
   row and runbook were corrected (D16 ruling 2).
2. **No structural guard against reintroducing the manual calendar or the
   old logging formatters.** Added (D16 ruling 1).
3. **Merge-status drift.** `MASTER_PLAN.md` rows 9–17 and `STATUS.md`
   paragraphs were corrected (D16 ruling 3).
4. **The migration helper misread history as a phase declaration.** With
   no header `Next phase:`, it would have reported PR 17 as PR 18's
   successor. Fixed by limiting the parser to the header block
   (D16 ruling 3a).
5. **Hard limits versus configurable limits.** The owner chose policy B.
   The rationale and regression coverage are in D16 ruling 4.
6. **Log `extra` objects bypassed redaction (found in review).**
   `_redact_value()` returned arbitrary objects unchanged, so a registered
   secret in an object's `repr()` reached `JSONRenderer`, which could escape
   it past the line-level redaction. It now redacts every non-scalar
   object's `repr()`, and a `repr()` that raises becomes a type-only
   placeholder so the record is still emitted (D16 ruling 5).

## Residual items (not blockers)

- `paper_runtime`'s own plain `logging.Formatter` belongs to a separate
  distribution and is outside the PR 15 scope (D16, out of scope).
- Row 8a remains open by design.

## Operational confirmation

```text
real model calls: 0
real provider calls: 0
broker calls: 0
paper orders: 0
live orders: 0
scheduler enabled: no
```
