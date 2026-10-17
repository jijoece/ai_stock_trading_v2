# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/18-final-authority-safety-audit`
- Reviewed HEAD: `8ad687099fd82636e42c7d0bc6517ce44260f917`
- Subject: PR 18 fix round 14: resolve review findings 1-6, 8-10
- Reviewed base: `bd95d2c7ba14a5333f1128ecf7b34a2059a95602`
- Review scope: re-review of fix round 14 (`333efbd..8ad6870`) against
  the 10 findings from the `333efbd` review
- GitHub PR: #36
- Trigger: manual Claude Code re-review
- Review status: RESOLVED in fix round 15
- Highest priority: P3
- Finding count: 1
- Note: line numbers refer to `8ad6870`, the PR head on GitHub.

## Status of the previous round's findings

| # | Finding | Status at `8ad6870` |
|---|---------|---------------------|
| 1 | Refresh CLI returns config-loader text | Resolved by decision. `_load_external_config` sends unexpected exceptions through `_sanitized_cli_error`. `PaperBooksConfigError` text, which includes the absolute path and the OS or YAML error, is returned as a domain error, the same convention as `paper-book-list`. Recorded in D16. |
| 2 | Cyclic containers recurse without bound | Resolved. Ancestor-id cycle detection and a 64-level nesting cap. A self-referencing dict and a hook returning `[self]` both render placeholders. |
| 3 | Class object renders as "hook failed" | Resolved. A class object skips the instance hook. |
| 4 | Set extras change JSON form | Resolved. Sets are sorted when their redacted items are orderable, otherwise kept in iteration order. The format change is accepted in D16. |
| 5 | `parse_status` reads history prose | Partially resolved in round 14; completed in round 15 (finding 1 below). |
| 6 | Extras evaluated in plain-text mode | Resolved. The plain-text pre-chain uses `_redact_event_strings`. |
| 7 | Redactor re-implements structlog's fallback | Deferred, with the reason recorded in D16 Ruling 5. |
| 8 | Duplicated config-load prologue, dead branch | Resolved. `_load_external_config` is shared and the dead branch is gone. |
| 9 | Guards re-parse every file | Resolved. Parsing is cached by `(source, filename)`. No guard mutates a cached tree. |
| 10 | Guards miss dynamic imports and `Handler.format` | Resolved for literal module names. A computed name still gets past the guard, which is the documented limit. The one existing `__import__("time")` in `src/` is not flagged. |

## Findings

### [P3] 1. `parse_status` still returns a stale phase when a recap starts a line

Location: `scripts/migration_helper.py:123`

Concern: Round 14 anchored the declaration patterns to the start of a line
and made two different declarations of the same kind raise `HelperError`.
That catches a recap in the middle of a line. A recap that starts a line,
however, still matches `_NEXT_PHASE`. `**Next phase: none.**` does not match
the pattern at all, since the pattern requires `PR <id>`, so it never takes
part in the conflict check, and the stale value wins.

Failure scenario (reproduced at `8ad6870`):

```text
**Current phase: PR 18**

Recap from the PR 17 era:
Next phase: PR 18 (now done)

**Next phase: none.**
```

`parse_status` returns `('18', '18')` instead of `('18', None)`, which is the
original failure scenario of finding 5. The new regression test passes only
because its recap sits in the middle of a line.

Remediation: Either:

- match `none` as a declared value (`(?:PR\s+{_PHASE_ID}|none)`), so a stale
  `PR 18` and the real `none` conflict and raise; or
- accept only the bold form (`^\*\*Next phase:`).

Add the line-start recap above as a regression case.

## Notes (no action required)

- `_load_external_config` returns a string error for `PaperBooksConfigError`
  and a `{code, message}` dict for unexpected failures. A consumer that
  reads `outcome["error"]["code"]` must handle both shapes. This matches the
  existing paper-books convention.

## Tests or diagnostics run

Run in a temporary detached worktree at `8ad6870`, using the repository's
`.venv` with `PYTHONPATH=src:scripts`. This is not the canonical Nox
interface; `nox -s ci` was not run.

- `pytest tests/unit/test_logging_config.py tests/unit/test_removed_implementation_reintroduction_guards.py tests/unit/test_migration_helper.py tests/unit/test_external_paper_refresh_cli_config_override.py`:
  164 passed.
- Probes:
  - `parse_status` on the live `STATUS.md` returns `('18', None)`.
  - The line-start recap above returns `('18', '18')`.
  - A self-referencing dict and a hook that returns `[self]` both render
    placeholders.
  - `{1, "a"}` falls back to `(1, 'a')`.

## Resolution (fix round 15)

| # | Outcome |
| - | ------- |
| 1 | Fixed. `none` is now a declared value in `_CURRENT_PHASE`/`_NEXT_PHASE`, so a line-start recap such as `Next phase: PR 18` conflicts with `**Next phase: none.**` and raises `HelperError`. `none` alone still means no successor. Regression: `test_line_start_recap_conflicting_with_none_fails_loudly` and `test_next_phase_none_alone_means_no_successor`. |
