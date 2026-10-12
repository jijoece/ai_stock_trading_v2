# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/17-remove-approved-commodity-implementations`
- Reviewed HEAD: `155b0a3f6326687e272dc6d4355a64254c4b65f6`
- Subject: PR 17: remove the approved analytics formulas; empyrical-reloaded is the sole authority
- Review scope: GitHub inline review thread on PR #35
- Reviewed base: `39069350927e8c9663eaeec785423b0c24ad724e` (`origin/main`)
- GitHub PR: #35
- Fix round: 1
- Trigger: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/35#discussion_r4245456846)
  (`commit_id` = `original_commit_id` = `155b0a3`, `metrics.py:188`)
- Review status: FIXED
- Highest priority: P2 (confirmed, fixed)
- Finding count: 1
- Fix commit: the commit that adds this document (subject "PR 17 fix
  round 1: restore the one-return below-threshold Sortino ratio"); its SHA
  is the branch head that follows `155b0a3`.

This document previously held the PR #34 (`migration/16-opentelemetry-tracing`)
record, whose header said `FIXES_APPLIED_PENDING_REVIEW` with no fix
commit. PR #34 was merged into `main` as `3906935`, so that status was
stale; its full record is preserved in git history at `987013a`.

## Investigation and resolution

**Confirmed.** At `155b0a3`, `sortino_ratio` short-circuited the downside
deviation to `0.0` whenever the series had fewer than two returns, so a
single return below `risk_free_rate` was reported `UNDEFINED`. The removed
implementation computed the downside deviation over that one observation
and returned a valid ratio. That changed the public contract this PR
promises to preserve.

### Evidence

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

### Fix

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

### Regression coverage

`tests/unit/test_metrics_library_authority.py::test_single_return_below_required_return_sortino_matches_removed_formula`
covers four cases: `[-0.05]` at `rf=0` and `[0.01]` at `rf=0.02`, each
annualized and raw. Each asserts `OK`, `sample_size=1`, and the value
captured from `3906935` above. All four failed before the fix, getting
`UNDEFINED` (verified red), and pass after it. The existing
`test_single_return_with_minimum_one_is_undefined` still pins the
no-downside single return (`0.05`) to `UNDEFINED` for both ratios. Only its
comment was clarified.

### Validation

- `nox -s tests -- tests/unit/test_metrics_library_authority.py tests/unit/test_metrics.py tests/unit/test_analytics_import_boundary.py -q`:
  94 passed.
- `nox -s ci`: successful. `tests` had 3392 passed and 107 skipped,
  `paper_tests` had 160 passed, `safety_typecheck` reported 0 errors, and
  `migration_smoke` was OK.
- `scripts/check_links.sh`: 0 errors.

## Findings

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

Resolution: confirmed and fixed as described above.
