# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/17-remove-approved-commodity-implementations`
- Reviewed HEAD: `7594aca024ed08df11f8dfe75cb9804ddbe410b6`
- Subject: PR 17 fix round 1: restore the one-return below-threshold Sortino ratio
- Review scope: GitHub inline review threads on PR #35
- Reviewed base: `39069350927e8c9663eaeec785423b0c24ad724e` (`origin/main`)
- GitHub PR: #35
- Fix round: 2
- Trigger: [chatgpt-codex-connector review thread r4245830182](https://github.com/jijoece/ai_stock_trading_v2/pull/35#discussion_r4245830182)
  (`commit_id` = `original_commit_id` = `7594aca`, `metrics.py:226`)
- Review status: FIXED
- Highest priority: P2 (confirmed, fixed)
- Finding count: 2 across rounds (round 1 r4245456846, round 2
  r4245830182), both confirmed and fixed
- Fix commits: round 1 `7594aca024ed08df11f8dfe75cb9804ddbe410b6`.
  Round 2 is the commit that adds this revision (subject "PR 17 fix round
  2: tolerate float noise in the promotion drawdown gate"), the branch head
  after `7594aca`.
- GitHub threads: neither thread was replied to or resolved from a fix
  session. Resolving them is left to the operator.

This document previously held the PR #34 (`migration/16-opentelemetry-tracing`)
record, whose header said `FIXES_APPLIED_PENDING_REVIEW` with no fix
commit. PR #34 was merged into `main` as `3906935`, so that status was
stale; its full record is preserved in git history at `987013a`.

## Round 2: false promotion regressions from float noise (r4245830182)

**Confirmed.** PR 17 changed `metrics.max_drawdown` from exact `Decimal`
arithmetic to `empyrical.max_drawdown`, a float cumulative product.
`research/promotion.py` fed both arms' drawdowns into a strict `<`
safety-regression check. Two return sequences with the same exact
drawdown can produce floats that differ in the last bit, so a tied
enhanced arm could be failed as a safety regression. The production path
is `cli.py:1760-1761`, which passes `comparison.{enhanced,baseline}.max_drawdown`
straight into `PromotionMetricInput`.

### Evidence: the reviewer's paired fixture through `evaluate_promotion`

Enhanced returns were `[0.19, -0.20, 0.01, 0.01, 0.01]` and baseline
returns `[-0.20, 0.01, 0.01, 0.01, 0.01]`. Both have an exact drawdown of
`-0.20`. Everything else used the `test_promotion_gates.py` defaults
(excess return 0.05 vs 0.01). `max_drawdown` from `3906935`
(`git show`, loaded by file path) was compared with the current one:

| `max_drawdown` source | enhanced / baseline | `currently_preferred` | Decision |
|---|---|---|---|
| `3906935` (Decimal) | `Decimal('-0.20')` / `Decimal('-0.20')` | False / True | `PREFERRED_FOR_PAPER` / `PREFERRED_FOR_PAPER` |
| `7594aca` (empyrical) | `-0.2000000000000001` / `-0.2` | False | `REJECTED` |
| `7594aca` (empyrical) | `-0.2000000000000001` / `-0.2` | True | `ROLLBACK_REQUIRED` |

The reason recorded was `max_drawdown regressed: enhanced
-0.2000000000000001 worse than baseline -0.2`. This was a behavior
regression introduced by PR 17. D15 ruling 3 had cleared this consumer on
the grounds that "equal inputs still give equal results". That is true
for identical sequences and false for different sequences with equal
drawdowns, so the D15 entry and `STATUS.md` were corrected.

The other promotion comparison, the excess return, is unaffected:
`benchmark_relative_cumulative_return` is still `Decimal` arithmetic at
this HEAD.

### Fix (round 2)

`research/promotion.py` adds `MAX_DRAWDOWN_REGRESSION_TOLERANCE = 1e-12`,
the same value as `metrics._ZERO_DEVIATION_TOLERANCE`. The drawdown check
now flags a regression only when `float(enhanced) < float(baseline) - 1e-12`.
Converting to `float` keeps mixed `float`/`Decimal` inputs legal, which
`PromotionMetricInput.value` allows. Rounding noise from the cumulative
product is on the order of 1e-16 per step, far below the tolerance. A
drawdown difference of 1e-12 is immaterial to a promotion decision.

The tolerance only affects ties within 1e-12. Every other status path and
gate is unchanged. The fix was made at the comparison point, not by
rounding `max_drawdown`, so the metric's values and the PR 17 golden
parity table stay unchanged.

### Regression coverage (`tests/unit/test_promotion_gates.py`)

- `test_equal_drawdowns_with_float_noise_are_not_a_safety_regression` runs
  for `currently_preferred` False and True. It builds both arms through the
  real `metrics.max_drawdown`, asserts the float noise is present, and
  asserts `PREFERRED_FOR_PAPER`. Both cases failed before the fix, with
  `REJECTED` and `ROLLBACK_REQUIRED` (verified red), and pass after it.
- `test_small_real_drawdown_regression_beyond_tolerance_is_still_rejected`
  has `float` and `Decimal` cases. `-0.2000001` vs `-0.2` is still
  `REJECTED`, so the tolerance does not loosen the gate for a real
  regression.
- `test_equal_decimal_drawdowns_are_not_a_safety_regression` covers the
  pre-PR `Decimal` input path.
- The existing `test_drawdown_regression_is_a_safety_failure` (`-0.30` vs
  `-0.05`) still passes.

### Validation (round 2)

- `nox -s tests -- tests/unit/test_promotion_gates.py tests/unit/test_metrics_library_authority.py tests/unit/test_metrics.py tests/unit/test_analytics_import_boundary.py -q`:
  124 passed.
- `nox -s ci` (exit 0): `tests` had 3397 passed and 107 skipped,
  `paper_tests` had 160 passed, `safety_typecheck` reported 0 errors, 0
  warnings and 0 informations, and `migration_smoke` reported "migration
  schema smoke checks OK". The run started after the code and test changes
  were final; only Markdown was edited after it began.
- `scripts/check_links.sh`: 0 errors (185 OK, 2 excluded).

## Round 1: one-sample downside Sortino handling (r4245456846)

**Confirmed.** At `155b0a3`, `sortino_ratio` short-circuited the downside
deviation to `0.0` whenever the series had fewer than two returns, so a
single return below `risk_free_rate` was reported `UNDEFINED`. The removed
implementation computed the downside deviation over that one observation
and returned a valid ratio. That changed the public contract this PR
promises to preserve.

### Evidence (round 1)

The pre-PR `sortino_ratio` was extracted with
`git show 3906935:src/trading_research/evaluation/metrics.py` and run by
file path with `min_sample_size=1`, using the test file's `_series`
fixture builder:

| Return(s) | `risk_free_rate` | `3906935` annualized / raw | `155b0a3` |
|---|---|---|---|
| `-0.05` | `0.0` | `OK -15.874507866387544` / `OK -1.0` | `UNDEFINED` |
| `0.01` | `0.02` | `OK -15.874507866387544` / `OK -1.0` | `UNDEFINED` |
| `0.05` | `0.0` | `UNDEFINED` / `UNDEFINED` | `UNDEFINED` (unchanged) |

Library behavior (`empyrical-reloaded`, `empyrical/stats.py`):

- `downside_risk` guards only `len(returns) < 1`. For `[-0.05]` it returns
  `0.05`, so the deviation is defined for one return.
- `sortino_ratio` returns `NaN` for `len(returns) < 2`, before it computes
  anything.

The PR's golden capture (`docs/library-migration/pr17/*_values.txt`)
covered only a positive single return (`single_1` = `0.05`, which has no
downside), so it missed this gap. D15 ruling 2 also said "fewer than two
returns is `UNDEFINED`, as before" for both ratios. That is true for Sharpe
and false for Sortino.

### Fix (round 1)

The finding asks to "remove the length shortcut". Removing it alone is not
safe: the library's `sortino_ratio` would then return `NaN`, which would be
reported as `status="OK", value=nan`. The minimal correction in
`src/trading_research/evaluation/metrics.py` (`sortino_ratio`) is:

1. Always compute the deviation with `empyrical.downside_risk`, with no
   length shortcut. A deviation at or near zero is still `UNDEFINED`.
2. For one return only, apply empyrical's own Sortino formula: annualized
   mean excess return over `empyrical.downside_risk` at the same
   annualization. For two or more returns, `empyrical.sortino_ratio` is
   still called unchanged, so every existing golden value and
   import-boundary rule holds. `math` is still not imported.

Sharpe is unchanged, because one return has no sample variance (`ddof=1`),
exactly as before.

Docs corrected to match: the `metrics.py` module docstring (boundary 1),
`docs/library-migration/DECISIONS.md` D15 ruling 2, and
`docs/library-migration/STATUS.md`.

### Regression coverage (round 1)

`tests/unit/test_metrics_library_authority.py::test_single_return_below_required_return_sortino_matches_removed_formula`
covers four cases: `[-0.05]` at `rf=0` and `[0.01]` at `rf=0.02`, each
annualized and raw. Each asserts `OK`, `sample_size=1`, and the value
captured from `3906935` above. All four failed before the fix, getting
`UNDEFINED` (verified red), and pass after it. The existing
`test_single_return_with_minimum_one_is_undefined` still pins the
no-downside single return (`0.05`) to `UNDEFINED` for both ratios. Only its
comment was clarified.

### Validation (round 1)

- `nox -s tests -- tests/unit/test_metrics_library_authority.py tests/unit/test_metrics.py tests/unit/test_analytics_import_boundary.py -q`:
  94 passed.
- `nox -s ci`: successful. `tests` had 3392 passed and 107 skipped,
  `paper_tests` had 160 passed, `safety_typecheck` reported 0 errors, and
  `migration_smoke` was OK.
- `scripts/check_links.sh`: 0 errors.

## Findings

### [P2] Investigate false promotion regressions from float noise

Location: `src/trading_research/evaluation/metrics.py:226` (at `7594aca`);
the comparison is in `src/trading_research/research/promotion.py:156-158`

> When two arms have mathematically equal drawdowns, this conversion can
> produce slightly different floats, while `research/promotion.py:156-158`
> treats any strict `<` difference as a safety regression. For example,
> enhanced returns `[0.19, -0.20, 0.01, 0.01, 0.01]` and baseline returns
> `[-0.20, 0.01, 0.01, 0.01, 0.01]` both have an exact Decimal drawdown of
> `-0.20`, but empyrical's cumulative-float calculation can report
> approximately `-0.2000000000000001` and `-0.2`, causing an otherwise tied
> enhanced arm to become `REJECTED` or `ROLLBACK_REQUIRED`. Please verify
> this paired fixture through `evaluate_promotion`; if confirmed, use a
> documented tolerance or normalized comparison and add regression
> coverage, otherwise record the contrary evidence and leave the code
> unchanged.

Resolution: confirmed and fixed in round 2, as described above.

### [P2] Recheck one-sample downside Sortino handling

Location: `src/trading_research/evaluation/metrics.py:188` (at `155b0a3`)

> When a caller sets `min_sample_size=1` and the sole completed return is
> below `risk_free_rate`, this branch forces `downside_deviation` to zero
> solely because the series has fewer than two entries and reports
> `UNDEFINED`. The removed implementation instead calculated downside
> deviation over that one observation and returned a valid ratio (`-1`
> unannualized or `-sqrt(252)` annualized), so this changes the stated
> public contract. Verify with a one-element negative-return fixture; if
> confirmed, remove the length shortcut and add regression coverage;
> otherwise record evidence and leave code unchanged.

Resolution: confirmed and fixed in round 1 (`7594aca`), as described above.
